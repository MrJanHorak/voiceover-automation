import logging
import json
import os
from datetime import datetime
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Path as PathParam, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from filelock import FileLock
from pydantic import BaseModel, Field, StringConstraints
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audio import MAX_REFERENCE_BYTES, AudioError, CudaDirectedEngine, DirectedEngine, TurboEngine, balanced_section, combine_reference_excerpts, export_audio, prepare_reference
from .script_import import MAX_SCRIPT_FILE_BYTES, extract_script
from .dataset import GOALS_MINUTES, MAX_TAKE_BYTES, MAX_TAKE_SECONDS, MIN_TAKE_SECONDS, assess_take, export_dataset, recording_stats, split_prompts, training_text
from .normalize import spoken_paragraphs, spoken_text
from .quality import QualityChecker, expected_text, score as quality_score
from .settings import TurboSettings
from .store import CHUNK_LIMIT, INSTRUCTIONS_LIMIT, MAX_PAUSE_MS, PARAGRAPH_PAUSE_MS, SCRIPT_LIMIT, TRANSCRIPT_LIMIT, Store, StudioError, new_id, split_script_with_pauses, validate_directions


PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).parent
LOG = logging.getLogger(__name__)
Directions = Annotated[str, StringConstraints(strip_whitespace=True, max_length=INSTRUCTIONS_LIMIT)]
PromptId = Annotated[str, PathParam(pattern=r"^[bc][0-9a-f]{15}$")]
# Engine for sections without special instructions: Chatterbox, or VoxCPM2 maximum resemblance.
VoiceMatch = Literal["standard", "maximum"]


class SectionInput(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CHUNK_LIMIT)]
    instructions: Directions | None = None
    turbo_settings: TurboSettings | None = None
    # Used only when preparing a new recording; regeneration keeps the section's saved pause.
    pause_ms: Annotated[int, Field(strict=True, ge=0, le=MAX_PAUSE_MS)] | None = None
    voice_match: VoiceMatch | None = None


class PronunciationInput(BaseModel):
    written: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    spoken: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class LoudnessInput(BaseModel):
    target: Literal[-14.0, -16.0, -23.0, -24.0] | None


class PromptsInput(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SCRIPT_LIMIT)]


class KeptInput(BaseModel):
    kept: bool


class TranscriptInput(BaseModel):
    transcript: Annotated[str, StringConstraints(strip_whitespace=True, max_length=TRANSCRIPT_LIMIT)]


class JobInput(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    script: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SCRIPT_LIMIT)]
    instructions: Directions = ""
    sections: Annotated[list[SectionInput], Field(min_length=1, max_length=SCRIPT_LIMIT)] | None = None
    turbo_settings: TurboSettings | None = None
    voice_match: VoiceMatch = "standard"
    auto_retries: Literal[0, 1, 2] = 1


class PreviewInput(BaseModel):
    script: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SCRIPT_LIMIT)]


class PauseInput(BaseModel):
    pause_ms: Annotated[int, Field(strict=True, ge=0, le=MAX_PAUSE_MS)]


class ReferenceExcerpt(BaseModel):
    voice_id: UUID
    start: Annotated[float, Field(ge=0, le=30, allow_inf_nan=False)]
    end: Annotated[float, Field(gt=0, le=30, allow_inf_nan=False)]


class ReferenceMix(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    excerpts: Annotated[list[ReferenceExcerpt], Field(min_length=1, max_length=20)]


class NoChecks:
    available = False


def install_voice(store, source, label):
    voice_id = new_id()
    destination = store.voices / f"{voice_id}.wav"
    # Keep the untouched recording at its original quality for future fine-tuning datasets.
    original = store.originals / f"{voice_id}.wav"
    try:
        duration = prepare_reference(source, destination)
        shutil.copyfile(source, original)
        return store.add_voice(voice_id, label, duration)
    except Exception:
        destination.unlink(missing_ok=True)
        original.unlink(missing_ok=True)
        raise


def render_attempt(store, engine, directed, job, section, folder, reference, spoken, seed):
    """Generate one take of a section, record it and return its filename."""
    job_id = job["id"]
    filename = f"section-{section['number']}-{new_id()}.wav"
    try:
        arguments = (spoken, reference, seed, folder / filename)
        matched = not section["instructions"] and section.get("voice_match") == "maximum"
        if section["instructions"] or matched:
            if getattr(directed, "needs_exclusive_gpu", False):
                release = getattr(engine, "release", None)
                if release:
                    release()
            if matched:
                directed.render_matched(*arguments, job.get("voice_transcript") or "")
            else:
                directed.render(*arguments, section["instructions"])
            rendered_by = directed
        else:
            settings = json.loads(section["turbo_settings"]) if section["turbo_settings"] else None
            if settings is None:
                engine.render(*arguments)
            else:
                engine.render(*arguments, settings=settings)
            rendered_by = engine
        details = None
        if callable(getattr(rendered_by, "details", None)):
            try:
                details = rendered_by.details()
            except Exception as error:
                LOG.warning("Could not record model details (%s)", type(error).__name__)
        if details and matched and details.get("engine"):
            details = {**details, "engine": f"{details['engine']} · maximum resemblance"}
        store.finish_section(job_id, section["number"], filename, details, seed, spoken)
    except Exception:
        (folder / filename).unlink(missing_ok=True)
        raise
    return filename


def check_take(store, checker, job_id, number, path, expected, reference):
    """Run the automatic check for one take; a checker failure never stops the recording."""
    try:
        result = checker.check(path, expected, reference)
    except Exception as error:
        LOG.warning("Automatic check could not run (%s)", type(error).__name__)
        result = {"status": "error", "reasons": ["The automatic check could not run."]}
    store.record_check(job_id, number, path.name, result)
    return result


def render_job(store, engine, directed, stop, job_id, checker=None):
    job = store.job(job_id)
    job.setdefault("id", job_id)
    folder = store.recordings / job_id
    folder.mkdir(exist_ok=True)
    reference = store.voices / f"{job['voice_id']}.wav"
    pairs = store.pronunciation_pairs() if hasattr(store, "pronunciation_pairs") else []
    checking = checker is not None and checker.available
    retries = (job.get("auto_retries") or 0) if checking else 0
    filenames = {section["number"]: section["audio"] for section in job["sections"]}

    if checking and job.get("check_requested"):
        # The owner asked to check a finished recording: check each section's current take.
        for section in job["sections"]:
            if stop.is_set():
                return
            if section["audio"] and not section["pending"]:
                current = next((take for take in section.get("takes", []) if take["audio"] == section["audio"]), {})
                expected = expected_text(current.get("text") or section["text"], pairs)
                check_take(store, checker, job_id, section["number"], folder / section["audio"], expected, reference)

    pending = [section for section in job["sections"] if section["pending"]]
    if getattr(directed, "needs_exclusive_gpu", False):
        # Render Chatterbox sections first so the shared 4 GB GPU switches engines only once.
        pending.sort(key=lambda section: bool(section["instructions"]) or section.get("voice_match") == "maximum")
    for section in pending:
        # Models receive normalized words; the section keeps the text as written.
        spoken = spoken_text(section["text"], pairs)
        expected = expected_text(section["text"], pairs)
        attempts = []
        for attempt in range(1 + retries):
            if stop.is_set():
                return
            seed = section["seed"] if attempt == 0 else secrets.randbelow(2147483647) + 1
            filename = render_attempt(store, engine, directed, job, section, folder, reference, spoken, seed)
            result = check_take(store, checker, job_id, section["number"], folder / filename, expected, reference) if checking else None
            attempts.append((filename, result))
            if result is None or result.get("status") == "pass":
                break
        best = attempts[-1][0]
        scored = [(filename, result) for filename, result in attempts if result and result.get("status") != "error"]
        if len(attempts) > 1 and scored:
            best = min(scored, key=lambda item: quality_score(item[1]))[0]
            if best != attempts[-1][0]:
                store.select_take(job_id, section["number"], best)
        filenames[section["number"]] = best
    paths = [(folder / filenames[section["number"]], section["pause_ms"]) for section in job["sections"]]

    if stop.is_set():
        return
    export = f"export-{new_id()}"
    wav, mp3 = folder / f"{export}.wav", folder / f"{export}.mp3"
    try:
        loudness = export_audio(paths, wav, mp3, trim=bool(job.get("trim_edges")),
                                rate=job.get("master_rate") or 24000, loudness=job.get("loudness_target"))
        store.finish_job(job_id, export, loudness)
    except Exception:
        wav.unlink(missing_ok=True)
        mp3.unlink(missing_ok=True)
        raise


def worker(store, engine, directed, stop, checker=None):
    while not stop.is_set():
        job_id = store.claim()
        if job_id is None:
            stop.wait(0.35)
            continue
        try:
            render_job(store, engine, directed, stop, job_id, checker)
        except AudioError as error:
            store.fail_job(job_id, str(error))
        except Exception as error:
            LOG.error("Recording %s failed (%s)", job_id, type(error).__name__)
            store.fail_job(job_id, "Generation could not finish. Check the reference and model setup, then retry.")


def file_response(path, media_type, filename=None):
    if not path.is_file():
        raise StudioError("This audio file could not be found.", 404)
    return FileResponse(path, media_type=media_type, filename=filename)


def local_mutation(request: Request):
    if request.headers.get("x-studio-request") != "1":
        raise StudioError("Submit changes from the local studio.", 403)


def create_app(data_dir=None, engine=None, directed_engine=None, quality_checker=None):
    data_dir = Path(data_dir or os.environ.get("VOICEOVER_DATA_DIR", PROJECT / "data")).resolve()
    store = Store(data_dir / "studio")
    device = os.environ.get("VOICEOVER_DEVICE", "cpu")
    directed_device = os.environ.get("VOICEOVER_DIRECTED_DEVICE", "cpu").lower()
    if directed_device not in {"cpu", "cuda"}:
        raise ValueError("VOICEOVER_DIRECTED_DEVICE must be cpu or cuda")
    templates = Jinja2Templates(directory=PACKAGE / "templates")
    if directed_engine is not None:
        directed = directed_engine
    elif directed_device == "cuda":
        executable = "audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli"
        runtime = Path(os.environ.get("VOICEOVER_AUDIOCPP_PATH", PROJECT / ".tools" / "audio-cpp" / executable))
        weights = Path(os.environ.get("VOICEOVER_VOXCPM2_GGUF", PROJECT / "models" / "voxcpm2-gguf" / "voxcpm2-q8_0.gguf"))
        directed = CudaDirectedEngine(runtime, weights)
    else:
        directed = DirectedEngine(PROJECT / "models" / "voxcpm2")
    if quality_checker is not None:
        checker = quality_checker
    elif engine is not None:
        checker = NoChecks()  # Tests that supply their own engine also choose their own checker.
    else:
        checker = QualityChecker(Path(os.environ.get("VOICEOVER_ASR_DIR", PROJECT / "models" / "whisper-small.en")),
                                 PROJECT / "models" / "chatterbox-turbo" / "ve.safetensors")

    @asynccontextmanager
    async def lifespan(app):
        store.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(store.root / ".studio.lock"), timeout=0):
            store.initialize()
            app.state.reference_notice = None
            reference = data_dir / "reference.wav"
            if store.active_voice() is None and reference.is_file():
                try:
                    if reference.stat().st_size > MAX_REFERENCE_BYTES:
                        raise AudioError("The existing reference exceeds 16 MB.")
                    install_voice(store, reference, reference.name)
                except (AudioError, OSError) as error:
                    app.state.reference_notice = str(error) if isinstance(error, AudioError) else "The existing reference could not be read."
            stop = Event()
            model = engine if engine is not None else TurboEngine(PROJECT / "models" / "chatterbox-turbo", device)
            thread = Thread(target=worker, args=(store, model, directed, stop, checker), name="voiceover-worker", daemon=True)
            thread.start()
            try:
                yield
            finally:
                stop.set()
                thread.join()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    mutations = [Depends(local_mutation)]

    @app.middleware("http")
    async def local_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response

    @app.exception_handler(StudioError)
    async def studio_error(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(AudioError)
    async def audio_error(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse({"detail": "Check the required fields and length limits."}, status_code=422)

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={
            "script_limit": SCRIPT_LIMIT, "chunk_limit": CHUNK_LIMIT, "max_pause": MAX_PAUSE_MS,
            "device": device, "directed_device": directed_device,
            "directed_available": directed.available, "instructions_limit": INSTRUCTIONS_LIMIT,
            "transcript_limit": TRANSCRIPT_LIMIT, "checks_available": checker.available,
        })

    @app.get("/assets/{asset}")
    def browser_asset(asset: Literal["studio.css", "studio.js"]):
        media_type = "text/css" if asset == "studio.css" else "text/javascript"
        return FileResponse(PACKAGE / "static" / asset, media_type=media_type)

    @app.get("/api/state")
    def state():
        return {**store.state(), "reference_notice": app.state.reference_notice}

    @app.post("/api/scripts/import", dependencies=mutations)
    def import_script(file: Annotated[UploadFile, File()]):
        contents = file.file.read(MAX_SCRIPT_FILE_BYTES + 1)
        return extract_script(file.filename or "", contents)

    @app.post("/api/voice", dependencies=mutations, status_code=201)
    def enroll(audio: Annotated[UploadFile, File()],
               name: Annotated[str, Form(), StringConstraints(strip_whitespace=True, max_length=100)] = ""):
        contents = audio.file.read(MAX_REFERENCE_BYTES + 1)
        if len(contents) > MAX_REFERENCE_BYTES:
            raise StudioError("Choose a WAV file under 16 MB.", 413)
        label = name or Path((audio.filename or "Reference.wav").replace("\\", "/")).name[:100]
        with TemporaryDirectory(dir=store.root) as temporary:
            source = Path(temporary) / "upload.wav"
            source.write_bytes(contents)
            result = install_voice(store, source, label)
        app.state.reference_notice = None
        return result

    def build_reference(data: ReferenceMix):
        available = {voice["id"] for voice in store.state()["voices"]}
        if any(item.voice_id.hex not in available for item in data.excerpts):
            raise StudioError("A selected reference could not be found.", 404)
        return combine_reference_excerpts([
            (store.voices / f"{item.voice_id.hex}.wav", item.start, item.end)
            for item in data.excerpts
        ])

    @app.post("/api/voice/combine/preview", dependencies=mutations)
    def preview_reference(data: ReferenceMix):
        audio, _ = build_reference(data)
        return Response(audio, media_type="audio/wav")

    @app.post("/api/voice/combine", dependencies=mutations, status_code=201)
    def save_combined_reference(data: ReferenceMix):
        audio, duration = build_reference(data)
        voice_id = new_id()
        destination = store.voices / f"{voice_id}.wav"
        try:
            destination.write_bytes(audio)
            result = store.add_voice(voice_id, data.name or "Combined reference", duration)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        app.state.reference_notice = None
        return result

    @app.get("/api/voice/current/audio")
    def listen_to_reference():
        voice = store.active_voice()
        if not voice:
            raise StudioError("Add a voice reference first.", 404)
        return file_response(store.voices / f"{voice['id']}.wav", "audio/wav")

    @app.get("/api/voice/{voice_id}/audio")
    def saved_reference_audio(voice_id: UUID):
        return file_response(store.voices / f"{voice_id.hex}.wav", "audio/wav")

    @app.post("/api/pronunciations", dependencies=mutations, status_code=201)
    def save_pronunciation(data: PronunciationInput):
        return store.save_pronunciation(data.written, data.spoken)

    @app.delete("/api/pronunciations/{word_id}", dependencies=mutations, status_code=204)
    def delete_pronunciation(word_id: UUID):
        store.delete_pronunciation(word_id.hex)
        return Response(status_code=204)

    @app.post("/api/spoken-preview", dependencies=mutations)
    def spoken_preview(data: PreviewInput):
        pairs = store.pronunciation_pairs()
        return {"spoken": spoken_text(data.script, pairs), "paragraphs": spoken_paragraphs(data.script, pairs)}

    # ---- Training recordings ----

    def dataset_view():
        prompts, kept_seconds, recorded = [], 0.0, 0
        for row in store.dataset_state():
            take = None
            if row["take_id"]:
                recorded += 1
                kept_seconds += row["duration"] if row["kept"] else 0
                take = {"id": row["take_id"], "duration": row["duration"], "kept": bool(row["kept"]),
                        "check": json.loads(row["check_result"]) if row["check_result"] else None}
            spoken = training_text(row["text"])
            prompts.append({"id": row["id"], "text": row["text"], "source": row["source"],
                            "read_as": spoken if spoken != " ".join(row["text"].split()) else None, "take": take})
        return {"prompts": prompts, "kept_minutes": round(kept_seconds / 60, 1), "recorded": recorded,
                "goals": list(GOALS_MINUTES), "checks_available": checker.available}

    @app.get("/api/dataset")
    def dataset():
        return dataset_view()

    @app.post("/api/dataset/prompts", dependencies=mutations, status_code=201)
    def add_prompts(data: PromptsInput):
        texts = split_prompts(data.text)
        if not texts:
            raise StudioError("Enter text with words to read.")
        return {"added": store.add_prompts(texts)}

    @app.delete("/api/dataset/prompts/{prompt_id}", dependencies=mutations, status_code=204)
    def delete_prompt(prompt_id: PromptId):
        audio = store.delete_prompt(prompt_id)
        if audio:
            (store.dataset / "takes" / audio).unlink(missing_ok=True)
        return Response(status_code=204)

    @app.post("/api/dataset/prompts/{prompt_id}/take", dependencies=mutations, status_code=201)
    def record_take(prompt_id: PromptId,
                    audio: Annotated[UploadFile, File()]):
        import soundfile as sf

        prompt = store.prompt(prompt_id)
        contents = audio.file.read(MAX_TAKE_BYTES + 1)
        if len(contents) > MAX_TAKE_BYTES:
            raise StudioError("The recording is too large. Keep each take under 45 seconds.", 413)
        take_id = new_id()
        destination = store.dataset / "takes" / f"{take_id}.wav"
        destination.write_bytes(contents)
        try:
            try:
                with sf.SoundFile(destination) as handle:
                    if handle.format not in {"WAV", "WAVEX"} or not handle.subtype.startswith("PCM_"):
                        raise AudioError("Record a PCM WAV take.")
                    samples, rate = handle.read(dtype="float32", always_2d=True), handle.samplerate
            except sf.LibsndfileError as error:
                raise AudioError("The recording could not be read as a PCM WAV.") from error
            stats = recording_stats(samples, rate)
            if not MIN_TAKE_SECONDS <= stats["duration"] <= MAX_TAKE_SECONDS:
                raise AudioError("Record between 1 and 45 seconds.")
            heard = None
            if checker.available:
                try:
                    heard = checker.transcribe_locked(destination)
                except Exception as error:
                    LOG.warning("Training take could not be transcribed (%s)", type(error).__name__)
            result = assess_take(prompt["text"], heard, stats)
            previous = store.save_take(prompt_id, take_id, destination.name, rate, stats["duration"], result)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if previous:
            (store.dataset / "takes" / previous).unlink(missing_ok=True)
        return {"id": take_id, "duration": stats["duration"], "kept": result["status"] != "review", "check": result}

    @app.get("/api/dataset/takes/{take_id}/audio")
    def dataset_take_audio(take_id: UUID):
        take = store.dataset_take(take_id.hex)
        return file_response(store.dataset / "takes" / take["audio"], "audio/wav")

    @app.put("/api/dataset/takes/{take_id}/kept", dependencies=mutations)
    def keep_take(take_id: UUID, data: KeptInput):
        store.set_take_kept(take_id.hex, data.kept)
        return {"id": take_id.hex, "kept": data.kept}

    @app.post("/api/dataset/export", dependencies=mutations)
    def export_training_set():
        takes = [(store.dataset / "takes" / row["audio"], row["text"]) for row in store.kept_takes()]
        takes = [(path, text) for path, text in takes if path.is_file()]
        if not takes:
            raise StudioError("Keep at least one recording before exporting.", 409)
        folder = store.dataset / "exports" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
        try:
            return export_dataset(takes, folder)
        except Exception:
            LOG.error("Training set export failed")
            raise

    @app.put("/api/voice/{voice_id}/transcript", dependencies=mutations)
    def save_transcript(voice_id: UUID, data: TranscriptInput):
        return store.set_transcript(voice_id.hex, " ".join(data.transcript.split()))

    @app.put("/api/voice/{voice_id}/active", dependencies=mutations)
    def activate_voice(voice_id: UUID):
        return store.activate_voice(voice_id.hex)

    @app.post("/api/jobs", dependencies=mutations, status_code=202)
    def create_job(data: JobInput):
        prepared = None
        if data.sections is not None:
            prepared = [{"text": item.text, "instructions": item.instructions or "",
                         "pause_ms": PARAGRAPH_PAUSE_MS if item.pause_ms is None else item.pause_ms,
                         "voice_match": item.voice_match or data.voice_match}
                        for item in data.sections]
            combined = "\n\n".join(item["text"] for item in prepared)
            if len(combined) > SCRIPT_LIMIT:
                raise StudioError("The combined sections exceed the script length limit.")
            for item in prepared:
                validate_directions(item["text"], item["instructions"], directed.available)
            script = combined
        else:
            validate_directions(data.script, data.instructions, directed.available)
            script = data.script
        settings = data.turbo_settings.model_dump() if data.turbo_settings else None
        return {"id": store.create_job(data.title or "Untitled recording", script, data.instructions, prepared, settings,
                                       data.voice_match, directed.available, data.auto_retries)}

    @app.post("/api/sections/preview", dependencies=mutations)
    def preview_sections(data: PreviewInput):
        sections = split_script_with_pauses(data.script)
        return {"sections": [text for text, _ in sections], "pauses": [pause for _, pause in sections]}

    @app.get("/api/jobs/{job_id}")
    def job_details(job_id: UUID):
        job = store.job(job_id.hex)
        pairs = store.pronunciation_pairs()
        return {
            **{key: job[key] for key in ("id", "title", "script", "instructions", "engine", "created_at", "status", "error", "export")},
            "voice_reference": job["voice_label"],
            "voice_transcript_ready": bool(job["voice_transcript"]),
            "master_rate": job["master_rate"], "loudness_target": job["loudness_target"],
            "export_loudness": json.loads(job["export_loudness"]) if job["export_loudness"] else None,
            "auto_retries": job["auto_retries"], "checks_available": checker.available,
            "sections": [{**{key: section[key] for key in ("number", "text", "instructions", "pending", "audio", "pause_ms", "voice_match")},
                          "spoken": spoken_text(section["text"], pairs),
                          "generation": {key: section[f"rendered_{key}"] for key in
                                         ("engine", "device", "runtime", "model_sha256", "seed", "at")},
                          "turbo_settings": json.loads(section["turbo_settings"]) if section["turbo_settings"] else None,
                          "check": next((json.loads(take["check_result"]) for take in section["takes"]
                                         if take["audio"] == section["audio"] and take["check_result"]), None),
                          "takes": [{**{key: take[key] for key in ("id", "text", "instructions", "engine", "device", "created_at", "voice_match", "spoken")},
                                     "check": json.loads(take["check_result"]) if take["check_result"] else None,
                                     "seed": take["seed"],
                                     "turbo_settings": json.loads(take["turbo_settings"]) if take["turbo_settings"] else None,
                                     "current": take["audio"] == section["audio"],
                                     "can_restore": take["text"] is not None and take["instructions"] is not None}
                                    for take in section["takes"]]}
                         for section in job["sections"]],
        }

    @app.post("/api/jobs/{job_id}/sections/{number}", dependencies=mutations, status_code=202)
    def regenerate(job_id: UUID, number: int, data: SectionInput):
        settings = data.turbo_settings.model_dump() if data.turbo_settings else None
        store.regenerate(job_id.hex, number, data.text, data.instructions, directed.available, settings, data.voice_match)
        return {"id": job_id.hex}

    @app.put("/api/jobs/{job_id}/sections/{number}/pause", dependencies=mutations, status_code=202)
    def update_pause(job_id: UUID, number: int, data: PauseInput):
        store.adjust_pause(job_id.hex, number, data.pause_ms)
        return {"id": job_id.hex}

    @app.post("/api/jobs/{job_id}/retry", dependencies=mutations, status_code=202)
    def retry(job_id: UUID):
        store.retry(job_id.hex)
        return {"id": job_id.hex}

    @app.put("/api/jobs/{job_id}/loudness", dependencies=mutations, status_code=202)
    def update_loudness(job_id: UUID, data: LoudnessInput):
        store.set_loudness(job_id.hex, None if data.target is None else float(data.target))
        return {"id": job_id.hex}

    @app.post("/api/jobs/{job_id}/check", dependencies=mutations, status_code=202)
    def check_recording(job_id: UUID):
        if not checker.available:
            raise StudioError("Set up automatic checks using the README first.", 409)
        store.request_check(job_id.hex)
        return {"id": job_id.hex}

    @app.post("/api/jobs/{job_id}/balance", dependencies=mutations, status_code=202)
    def balance(job_id: UUID):
        store.rebuild(job_id.hex)
        return {"id": job_id.hex}

    @app.get("/api/jobs/{job_id}/sections/{number}/audio")
    def section_audio(job_id: UUID, number: int):
        job = store.job(job_id.hex)
        for section in job["sections"]:
            if section["number"] == number and section["audio"]:
                path = store.recordings / job_id.hex / section["audio"]
                if not path.is_file():
                    raise StudioError("This audio file could not be found.", 404)
                return file_response(balanced_section(path), "audio/wav")
        raise StudioError("This section has no audio yet.", 404)

    @app.get("/api/jobs/{job_id}/sections/{number}/takes/{take_id}/audio")
    def take_audio(job_id: UUID, number: int, take_id: UUID):
        take = store.take(job_id.hex, number, take_id.hex)
        path = store.recordings / job_id.hex / take["audio"]
        if not path.is_file():
            raise StudioError("This take's audio file could not be found.", 404)
        return file_response(balanced_section(path), "audio/wav")

    @app.post("/api/jobs/{job_id}/sections/{number}/takes/{take_id}/restore", dependencies=mutations, status_code=202)
    def restore_take(job_id: UUID, number: int, take_id: UUID):
        store.restore_take(job_id.hex, number, take_id.hex)
        return {"id": job_id.hex}

    @app.get("/api/jobs/{job_id}/export/{kind}")
    def audio_export(job_id: UUID, kind: Literal["wav", "mp3"], download: bool = False):
        job = store.job(job_id.hex)
        if not job["export"]:
            raise StudioError("This recording has no export yet.", 404)
        media_type = "audio/wav" if kind == "wav" else "audio/mpeg"
        safe_title = "".join(character if character.isalnum() or character in " -_" else "_"
                             for character in job["title"]).strip() or "recording"
        return file_response(store.recordings / job_id.hex / f"{job['export']}.{kind}", media_type,
                             f"{safe_title}.{kind}" if download else None)

    @app.delete("/api/jobs/{job_id}", dependencies=mutations, status_code=204)
    def delete_job(job_id: UUID):
        store.delete(job_id.hex)
        folder = store.recordings / job_id.hex
        if folder.is_dir():
            try:
                for path in folder.iterdir():
                    path.unlink()
                folder.rmdir()
            except OSError:
                LOG.warning("Could not remove every audio file for deleted recording %s", job_id.hex)
        return Response(status_code=204)

    return app


app = create_app()

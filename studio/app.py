import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from filelock import FileLock
from pydantic import BaseModel, Field, StringConstraints
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audio import MAX_REFERENCE_BYTES, AudioError, TurboEngine, export_audio, prepare_reference
from .store import CHUNK_LIMIT, MAX_PAUSE_MS, SCRIPT_LIMIT, Store, StudioError, new_id


PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).parent
LOG = logging.getLogger(__name__)


class JobInput(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    script: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SCRIPT_LIMIT)]


class SectionInput(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CHUNK_LIMIT)]


class PauseInput(BaseModel):
    pause_ms: Annotated[int, Field(strict=True, ge=0, le=MAX_PAUSE_MS)]


def install_voice(store, source, label):
    voice_id = new_id()
    destination = store.voices / f"{voice_id}.wav"
    try:
        duration = prepare_reference(source, destination)
        return store.add_voice(voice_id, label, duration)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def render_job(store, engine, stop, job_id):
    job = store.job(job_id)
    folder = store.recordings / job_id
    folder.mkdir(exist_ok=True)
    reference = store.voices / f"{job['voice_id']}.wav"
    paths = []
    for section in job["sections"]:
        if stop.is_set():
            return
        filename = section["audio"]
        if section["pending"]:
            filename = f"section-{section['number']}-{new_id()}.wav"
            try:
                engine.render(section["text"], reference, section["seed"], folder / filename)
                store.finish_section(job_id, section["number"], filename)
            except Exception:
                (folder / filename).unlink(missing_ok=True)
                raise
        paths.append((folder / filename, section["pause_ms"]))

    if stop.is_set():
        return
    export = f"export-{new_id()}"
    wav, mp3 = folder / f"{export}.wav", folder / f"{export}.mp3"
    try:
        export_audio(paths, wav, mp3)
        store.finish_job(job_id, export)
    except Exception:
        wav.unlink(missing_ok=True)
        mp3.unlink(missing_ok=True)
        raise


def worker(store, engine, stop):
    while not stop.is_set():
        job_id = store.claim()
        if job_id is None:
            stop.wait(0.35)
            continue
        try:
            render_job(store, engine, stop, job_id)
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


def create_app(data_dir=None, engine=None):
    data_dir = Path(data_dir or os.environ.get("VOICEOVER_DATA_DIR", PROJECT / "data")).resolve()
    store = Store(data_dir / "studio")
    device = os.environ.get("VOICEOVER_DEVICE", "cpu")
    templates = Jinja2Templates(directory=PACKAGE / "templates")

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
            thread = Thread(target=worker, args=(store, model, stop), name="voiceover-worker", daemon=True)
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
            "script_limit": SCRIPT_LIMIT, "chunk_limit": CHUNK_LIMIT, "max_pause": MAX_PAUSE_MS, "device": device,
        })

    @app.get("/assets/{asset}")
    def browser_asset(asset: Literal["studio.css", "studio.js"]):
        media_type = "text/css" if asset == "studio.css" else "text/javascript"
        return FileResponse(PACKAGE / "static" / asset, media_type=media_type)

    @app.get("/api/state")
    def state():
        return {**store.state(), "reference_notice": app.state.reference_notice}

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

    @app.get("/api/voice/current/audio")
    def listen_to_reference():
        voice = store.active_voice()
        if not voice:
            raise StudioError("Add a voice reference first.", 404)
        return file_response(store.voices / f"{voice['id']}.wav", "audio/wav")

    @app.get("/api/voice/{voice_id}/audio")
    def saved_reference_audio(voice_id: UUID):
        return file_response(store.voices / f"{voice_id.hex}.wav", "audio/wav")

    @app.put("/api/voice/{voice_id}/active", dependencies=mutations)
    def activate_voice(voice_id: UUID):
        return store.activate_voice(voice_id.hex)

    @app.post("/api/jobs", dependencies=mutations, status_code=202)
    def create_job(data: JobInput):
        return {"id": store.create_job(data.title or "Untitled recording", data.script)}

    @app.get("/api/jobs/{job_id}")
    def job_details(job_id: UUID):
        job = store.job(job_id.hex)
        return {
            **{key: job[key] for key in ("id", "title", "script", "engine", "created_at", "status", "error", "export")},
            "sections": [{key: section[key] for key in ("number", "text", "pending", "audio", "pause_ms")}
                         for section in job["sections"]],
        }

    @app.post("/api/jobs/{job_id}/sections/{number}", dependencies=mutations, status_code=202)
    def regenerate(job_id: UUID, number: int, data: SectionInput):
        store.regenerate(job_id.hex, number, data.text)
        return {"id": job_id.hex}

    @app.put("/api/jobs/{job_id}/sections/{number}/pause", dependencies=mutations, status_code=202)
    def update_pause(job_id: UUID, number: int, data: PauseInput):
        store.adjust_pause(job_id.hex, number, data.pause_ms)
        return {"id": job_id.hex}

    @app.post("/api/jobs/{job_id}/retry", dependencies=mutations, status_code=202)
    def retry(job_id: UUID):
        store.retry(job_id.hex)
        return {"id": job_id.hex}

    @app.get("/api/jobs/{job_id}/sections/{number}/audio")
    def section_audio(job_id: UUID, number: int):
        job = store.job(job_id.hex)
        for section in job["sections"]:
            if section["number"] == number and section["audio"]:
                return file_response(store.recordings / job_id.hex / section["audio"], "audio/wav")
        raise StudioError("This section has no audio yet.", 404)

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

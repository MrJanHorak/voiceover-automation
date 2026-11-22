import logging
import json
import os
from datetime import datetime, timedelta, timezone
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
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
from .store import CHUNK_LIMIT, INSTRUCTIONS_LIMIT, MAX_PAUSE_MS, PARAGRAPH_PAUSE_MS, SCRIPT_LIMIT, TRAINED_MATCHES, TRANSCRIPT_LIMIT, Store, StudioError, new_id, split_script_with_pauses, validate_directions
from .accounts import DUMMY_HASH, SESSION_COOKIE, SESSION_DAYS, RateLimiter, check_password, hash_password, new_token, normalize_email, token_hash, verify_password
from .inbox import SCAN_SECONDS, Inbox, unique
from .trained import find_trained


PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).parent
LOG = logging.getLogger(__name__)
Directions = Annotated[str, StringConstraints(strip_whitespace=True, max_length=INSTRUCTIONS_LIMIT)]
PromptId = Annotated[str, PathParam(pattern=r"^[bc][0-9a-f]{15}$")]
# Engine for a section: Chatterbox, VoxCPM2 maximum resemblance, or VoxCPM2 with the trained voice
# alone or together with the reference clip.
VoiceMatch = Literal["standard", "maximum", "trained", "trained_reference"]


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


class SignInInput(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    password: Annotated[str, StringConstraints(max_length=200)]


class SignUpInput(SignInInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    terms: bool = False


class SetupInput(SignInInput):
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]


class AccountInput(BaseModel):
    disabled: bool | None = None
    role: Literal["admin", "customer"] | None = None


class ComparisonInput(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CHUNK_LIMIT)]
    baseline: VoiceMatch = "maximum"
    contender: VoiceMatch = "trained"


class ChoiceInput(BaseModel):
    choice: Literal["A", "B", "same"]


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


def trained_adapter(store, job):
    """Folder of the trained voice a recording uses; it must still be in data/studio/trained."""
    voice = find_trained(store.root / "trained", job.get("trained_voice")) if job.get("trained_voice") else None
    if voice is None:
        raise AudioError(f"The trained voice \"{job.get('trained_voice') or ''}\" is missing from data/studio/trained. "
                         "Copy it back, or choose another voice engine for these sections.")
    return voice["path"]


def render_attempt(store, engine, directed, job, section, folder, reference, spoken, seed):
    """Generate one take of a section, record it and return its filename."""
    job_id = job["id"]
    filename = f"section-{section['number']}-{new_id()}.wav"
    try:
        arguments = (spoken, reference, seed, folder / filename)
        mode = section.get("voice_match")
        trained = mode in TRAINED_MATCHES
        matched = not section["instructions"] and mode == "maximum"
        if section["instructions"] or matched or trained:
            if getattr(directed, "needs_exclusive_gpu", False):
                release = getattr(engine, "release", None)
                if release:
                    release()
            if trained:
                adapter = trained_adapter(store, job)
                voice = reference if mode == "trained_reference" else None
                if section["instructions"]:
                    directed.render(spoken, voice, seed, folder / filename, section["instructions"], adapter=adapter)
                else:
                    directed.render_trained(spoken, voice, seed, folder / filename, adapter)
            elif matched:
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
        elif details and trained and details.get("engine"):
            extra = " + reference" if mode == "trained_reference" else ""
            details = {**details, "engine": f"{details['engine']} · trained voice {job.get('trained_voice')}{extra}"}
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
    # Global pronunciations plus the recording owner's own (customers only have their own list).
    pairs = store.pronunciation_pairs(job.get("user_id")) if hasattr(store, "pronunciation_pairs") else []
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
        pending.sort(key=lambda section: bool(section["instructions"]) or section.get("voice_match", "standard") != "standard")
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


def customer_view(details):
    """A recording as a customer sees it: no engine names, model details, seeds or delivery records."""
    hidden = ("engine", "voice_reference", "voice_transcript_ready", "trained_voice", "delivered", "approved", "source")
    view = {key: value for key, value in details.items() if key not in hidden}
    view["sections"] = [
        {**{key: value for key, value in section.items() if key not in ("generation", "turbo_settings", "voice_match")},
         "generation": {"at": section["generation"]["at"]},
         "takes": [{key: value for key, value in take.items()
                    if key not in ("engine", "device", "seed", "turbo_settings", "voice_match")}
                   for take in section["takes"]]}
        for section in details["sections"]]
    return view


def inbox_worker(inbox, stop):
    while not stop.wait(SCAN_SECONDS):
        try:
            inbox.scan()
        except Exception as error:
            LOG.error("Inbox scan failed (%s)", type(error).__name__)


def safe_name(title):
    return "".join(character if character.isalnum() or character in " -_" else "_" for character in title).strip() or "recording"


def file_response(path, media_type, filename=None):
    if not path.is_file():
        raise StudioError("This audio file could not be found.", 404)
    return FileResponse(path, media_type=media_type, filename=filename)


def local_mutation(request: Request):
    if request.headers.get("x-studio-request") != "1":
        raise StudioError("Submit changes from the local studio.", 403)


# Customers may queue this many recordings at a time.
CUSTOMER_ACTIVE_LIMIT = 3
# Without accounts (VOICEOVER_AUTH=off) everyone who reaches the studio is its owner: loopback only.
LOCAL_OWNER = {"id": None, "email": "", "role": "admin", "name": "Owner", "local": True}


def create_app(data_dir=None, engine=None, directed_engine=None, quality_checker=None, auth=None):
    data_dir = Path(data_dir or os.environ.get("VOICEOVER_DATA_DIR", PROJECT / "data")).resolve()
    store = Store(data_dir / "studio")
    auth_enabled = auth if auth is not None else os.environ.get("VOICEOVER_AUTH", "on").lower() != "off"
    secure_cookies = os.environ.get("VOICEOVER_SECURE_COOKIES", "") == "1"
    allowed_hosts = [host.strip() for host in os.environ.get("VOICEOVER_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
                     if host.strip()]
    limiter = RateLimiter()
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

    inbox_dir = Path(os.environ.get("VOICEOVER_INBOX") or store.root / "inbox")
    outbox_dir = Path(os.environ.get("VOICEOVER_OUTBOX") or store.root / "outbox")

    def house_match():
        """The engine for hands-off and customer recordings: trained voice + reference when available."""
        voice = store.active_voice()
        if current_trained() and directed.available:
            return "trained_reference"
        if directed.available and voice and voice["transcript"]:
            return "maximum"
        return "standard"

    def intake_job(title, script, source="inbox"):
        """Hands-off recording with the owner's preferred setup."""
        trained = current_trained()
        return store.create_job(title, script, "", None, None, house_match(), directed.available,
                                1 if checker.available else 0, trained["name"] if trained else None, source=source)

    inbox = Inbox(inbox_dir, intake_job)

    def current_trained():
        """The trained voice new sections use: VOICEOVER_TRAINED_VOICE, or else the newest one."""
        voice = find_trained(store.root / "trained", os.environ.get("VOICEOVER_TRAINED_VOICE") or None)
        return {"name": voice["name"], "step": voice["step"]} if voice else None

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
            app.state.setup_code = None
            if auth_enabled and store.admin_count() == 0:
                # First run: whoever has the server's terminal creates the admin account with this code.
                app.state.setup_code = secrets.token_hex(4).upper()
                LOG.warning("No admin account yet. Open /login and use setup code %s", app.state.setup_code)
                print(f"\n  Voice studio setup code: {app.state.setup_code}  (open /login to create the admin account)\n", flush=True)
            stop = Event()
            model = engine if engine is not None else TurboEngine(PROJECT / "models" / "chatterbox-turbo", device)
            thread = Thread(target=worker, args=(store, model, directed, stop, checker), name="voiceover-worker", daemon=True)
            thread.start()
            intake = None
            if os.environ.get("VOICEOVER_INBOX", "").lower() != "off":
                inbox.prepare()
                outbox_dir.mkdir(parents=True, exist_ok=True)
                intake = Thread(target=inbox_worker, args=(inbox, stop), name="voiceover-inbox", daemon=True)
                intake.start()
            try:
                yield
            finally:
                stop.set()
                thread.join()
                if intake:
                    intake.join()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    # ---- Access control ----

    def current_user(request: Request):
        if not auth_enabled:
            return LOCAL_OWNER
        token = request.cookies.get(SESSION_COOKIE)
        return store.session_user(token_hash(token)) if token else None

    def require_user(request: Request):
        user = current_user(request)
        if user is None:
            raise StudioError("Sign in to continue.", 401)
        return user

    def require_admin(request: Request):
        user = require_user(request)
        if user["role"] != "admin":
            raise StudioError("Only the studio admin can do this.", 403)
        return user

    def customer_owner(owner_id):
        """The customer account that owns a recording, or None for the studio's own recordings."""
        account = store.user(owner_id) if owner_id else None
        return account if account and account["role"] == "customer" else None

    def owned_job(request: Request, job_id: UUID):
        """Customers reach only their own recordings; admins reach all. Others get 404, not 403.

        Admins review customer recordings read-only: they can listen, download and delete, but not change them.
        """
        user = require_user(request)
        owner = store.job_owner(job_id.hex)
        if user["role"] != "admin":
            if owner != user["id"]:
                raise StudioError("Recording not found.", 404)
        elif request.method not in ("GET", "HEAD", "DELETE") and customer_owner(owner):
            raise StudioError("Customer recordings are read-only for admins. You can listen, download or delete them.", 403)
        return user

    def owner_id(user):
        return None if user.get("local") else user["id"]

    def customer_id(user):
        return user["id"] if user["role"] == "customer" else None

    mutations = [Depends(local_mutation), Depends(require_user)]
    admin_only = [Depends(require_admin)]
    admin_mutations = [Depends(local_mutation), Depends(require_admin)]
    job_access = [Depends(owned_job)]
    job_mutations = [Depends(local_mutation), Depends(owned_job)]

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

    def start_session(response, user):
        token = new_token()
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
        store.create_session(user["id"], token_hash(token), expires.isoformat(timespec="seconds"))
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                            secure=secure_cookies, path="/")

    def account_view(user):
        return {"email": user["email"], "name": user["name"], "role": user["role"], "local": bool(user.get("local"))}

    @app.get("/login", include_in_schema=False)
    @app.get("/signup", include_in_schema=False)
    def sign_in_page(request: Request):
        if not auth_enabled or current_user(request):
            return RedirectResponse("/", status_code=303)
        mode = "setup" if store.admin_count() == 0 else ("signup" if request.url.path == "/signup" else "login")
        return templates.TemplateResponse(request=request, name="auth.html", context={"mode": mode})

    @app.post("/api/auth/login", dependencies=[Depends(local_mutation)])
    def sign_in(data: SignInInput, request: Request):
        client = request.client.host if request.client else "unknown"
        email = data.email.strip().lower()
        if limiter.blocked(f"email:{email}", f"ip:{client}"):
            raise StudioError("Too many attempts. Wait 15 minutes and try again.", 429)
        user = store.user_by_email(email)
        valid = verify_password(data.password, user["password_hash"] if user else DUMMY_HASH)
        if not user or not valid or user["disabled"]:
            limiter.fail(f"email:{email}", f"ip:{client}")
            raise StudioError("The email or password is not correct.", 401)
        limiter.reset(f"email:{email}")
        response = JSONResponse({"account": account_view(user)})
        start_session(response, user)
        return response

    @app.post("/api/auth/signup", dependencies=[Depends(local_mutation)], status_code=201)
    def sign_up(data: SignUpInput, request: Request):
        if store.admin_count() == 0:
            raise StudioError("The studio is not set up yet.", 409)
        client = request.client.host if request.client else "unknown"
        if limiter.blocked(f"signup:{client}"):
            raise StudioError("Too many new accounts from this network. Try again later.", 429)
        if not data.terms:
            raise StudioError("Accept the terms of use to create an account.")
        try:
            email = normalize_email(data.email)
            check_password(data.password)
        except ValueError as error:
            raise StudioError(str(error)) from error
        user = store.create_user(email, hash_password(data.password), "customer", data.name, terms=True)
        limiter.fail(f"signup:{client}")  # Counts sign-ups per network address, not failures.
        response = JSONResponse({"account": account_view(user)}, status_code=201)
        start_session(response, user)
        return response

    @app.post("/api/auth/setup", dependencies=[Depends(local_mutation)], status_code=201)
    def set_up(data: SetupInput, request: Request):
        client = request.client.host if request.client else "unknown"
        if store.admin_count() > 0 or not app.state.setup_code:
            raise StudioError("The studio already has an admin account.", 409)
        if limiter.blocked(f"setup:{client}"):
            raise StudioError("Too many attempts. Wait 15 minutes and try again.", 429)
        if not secrets.compare_digest(data.code.upper(), app.state.setup_code):
            limiter.fail(f"setup:{client}")
            raise StudioError("The setup code is not correct. It is shown in the terminal running the studio.", 401)
        try:
            email = normalize_email(data.email)
            check_password(data.password)
        except ValueError as error:
            raise StudioError(str(error)) from error
        user = store.create_user(email, hash_password(data.password), "admin", "Admin", terms=True)
        app.state.setup_code = None
        response = JSONResponse({"account": account_view(user)}, status_code=201)
        start_session(response, user)
        return response

    @app.post("/api/auth/logout", dependencies=[Depends(local_mutation)], status_code=204)
    def sign_out(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            store.delete_session(token_hash(token))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/auth/me")
    def me(user: Annotated[dict, Depends(require_user)]):
        return {"account": account_view(user)}

    # ---- Admin: accounts and misuse review ----

    @app.get("/api/admin/accounts", dependencies=admin_only)
    def accounts():
        return {"accounts": store.users()}

    @app.put("/api/admin/accounts/{user_id}", dependencies=admin_mutations)
    def update_account(user_id: UUID, data: AccountInput, admin: Annotated[dict, Depends(require_admin)]):
        if user_id.hex == admin.get("id") and (data.disabled or data.role == "customer"):
            raise StudioError("You cannot disable or demote your own account.", 409)
        store.update_user(user_id.hex, data.disabled, data.role)
        return {"accounts": store.users()}

    @app.get("/api/admin/recordings", dependencies=admin_only)
    def review_recordings(user: UUID | None = None):
        return {"recordings": store.customer_recordings(user.hex if user else None)}

    @app.get("/api/admin/accounts/{user_id}/usage", dependencies=admin_only)
    def account_usage(user_id: UUID):
        if store.user(user_id.hex) is None:
            raise StudioError("Account not found.", 404)
        return {"usage": store.usage(user_id.hex)}

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        admin = user["role"] == "admin"
        return templates.TemplateResponse(request=request, name="index.html", context={
            "admin": admin, "account": account_view(user), "auth_enabled": auth_enabled,
            "house_match": house_match(),
            "script_limit": SCRIPT_LIMIT, "chunk_limit": CHUNK_LIMIT, "max_pause": MAX_PAUSE_MS,
            "device": device, "directed_device": directed_device,
            "directed_available": directed.available, "instructions_limit": INSTRUCTIONS_LIMIT,
            "transcript_limit": TRANSCRIPT_LIMIT, "checks_available": checker.available,
            "trained_voice": current_trained() if admin else ({"name": "house", "step": None} if current_trained() else None),
            "inbox": str(inbox_dir) if admin else "", "outbox": str(outbox_dir) if admin else "",
        })

    @app.get("/assets/{asset}")
    def browser_asset(asset: Literal["studio.css", "studio.js", "auth.js"]):
        media_type = "text/css" if asset.endswith(".css") else "text/javascript"
        return FileResponse(PACKAGE / "static" / asset, media_type=media_type)

    @app.get("/api/state")
    def state(user: Annotated[dict, Depends(require_user)]):
        if user["role"] == "customer":
            voice = store.active_voice()
            house = {"id": "house", "label": "Studio voice", "duration": 0, "transcript": "ready"} if voice else None
            return {**store.state(user["id"]), "voice": house, "voices": [], "reference_notice": None,
                    "trained_voice": None, "account": account_view(user)}
        return {**store.state(), "reference_notice": app.state.reference_notice, "trained_voice": current_trained(),
                "account": account_view(user)}

    @app.post("/api/scripts/import", dependencies=mutations)
    def import_script(file: Annotated[UploadFile, File()]):
        contents = file.file.read(MAX_SCRIPT_FILE_BYTES + 1)
        return extract_script(file.filename or "", contents)

    @app.post("/api/voice", dependencies=admin_mutations, status_code=201)
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

    @app.post("/api/voice/combine/preview", dependencies=admin_mutations)
    def preview_reference(data: ReferenceMix):
        audio, _ = build_reference(data)
        return Response(audio, media_type="audio/wav")

    @app.post("/api/voice/combine", dependencies=admin_mutations, status_code=201)
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

    @app.get("/api/voice/current/audio", dependencies=admin_only)
    def listen_to_reference():
        voice = store.active_voice()
        if not voice:
            raise StudioError("Add a voice reference first.", 404)
        return file_response(store.voices / f"{voice['id']}.wav", "audio/wav")

    @app.get("/api/voice/{voice_id}/audio", dependencies=admin_only)
    def saved_reference_audio(voice_id: UUID):
        return file_response(store.voices / f"{voice_id.hex}.wav", "audio/wav")

    @app.post("/api/pronunciations", dependencies=mutations, status_code=201)
    def save_pronunciation(data: PronunciationInput, user: Annotated[dict, Depends(require_user)]):
        return store.save_pronunciation(data.written, data.spoken, customer_id(user))

    @app.delete("/api/pronunciations/{word_id}", dependencies=mutations, status_code=204)
    def delete_pronunciation(word_id: UUID, user: Annotated[dict, Depends(require_user)]):
        store.delete_pronunciation(word_id.hex, customer_id(user))
        return Response(status_code=204)

    @app.post("/api/spoken-preview", dependencies=mutations)
    def spoken_preview(data: PreviewInput, user: Annotated[dict, Depends(require_user)]):
        pairs = store.pronunciation_pairs(customer_id(user))
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

    @app.get("/api/dataset", dependencies=admin_only)
    def dataset():
        return dataset_view()

    @app.post("/api/dataset/prompts", dependencies=admin_mutations, status_code=201)
    def add_prompts(data: PromptsInput):
        texts = split_prompts(data.text)
        if not texts:
            raise StudioError("Enter text with words to read.")
        return {"added": store.add_prompts(texts)}

    @app.delete("/api/dataset/prompts/{prompt_id}", dependencies=admin_mutations, status_code=204)
    def delete_prompt(prompt_id: PromptId):
        audio = store.delete_prompt(prompt_id)
        if audio:
            (store.dataset / "takes" / audio).unlink(missing_ok=True)
        return Response(status_code=204)

    @app.post("/api/dataset/prompts/{prompt_id}/take", dependencies=admin_mutations, status_code=201)
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

    @app.get("/api/dataset/takes/{take_id}/audio", dependencies=admin_only)
    def dataset_take_audio(take_id: UUID):
        take = store.dataset_take(take_id.hex)
        return file_response(store.dataset / "takes" / take["audio"], "audio/wav")

    @app.put("/api/dataset/takes/{take_id}/kept", dependencies=admin_mutations)
    def keep_take(take_id: UUID, data: KeptInput):
        store.set_take_kept(take_id.hex, data.kept)
        return {"id": take_id.hex, "kept": data.kept}

    @app.post("/api/dataset/export", dependencies=admin_mutations)
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

    @app.put("/api/voice/{voice_id}/transcript", dependencies=admin_mutations)
    def save_transcript(voice_id: UUID, data: TranscriptInput):
        return store.set_transcript(voice_id.hex, " ".join(data.transcript.split()))

    @app.put("/api/voice/{voice_id}/active", dependencies=admin_mutations)
    def activate_voice(voice_id: UUID):
        return store.activate_voice(voice_id.hex)

    @app.post("/api/jobs", dependencies=mutations, status_code=202)
    def create_job(data: JobInput, user: Annotated[dict, Depends(require_user)]):
        customer = user["role"] == "customer"
        voice_match = house_match() if customer else data.voice_match
        if customer and store.active_jobs(user["id"]) >= CUSTOMER_ACTIVE_LIMIT:
            raise StudioError(f"You already have {CUSTOMER_ACTIVE_LIMIT} recordings in progress. "
                              "Wait for one to finish, then try again.", 429)
        prepared = None
        if data.sections is not None:
            prepared = [{"text": item.text, "instructions": item.instructions or "",
                         "pause_ms": PARAGRAPH_PAUSE_MS if item.pause_ms is None else item.pause_ms,
                         "voice_match": voice_match if customer else (item.voice_match or data.voice_match)}
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
        # Customers use the studio voice with the admin's settings; engine options are for the admin.
        settings = data.turbo_settings.model_dump() if data.turbo_settings and not customer else None
        trained = current_trained()
        retries = (1 if checker.available else 0) if customer else data.auto_retries
        return {"id": store.create_job(data.title or "Untitled recording", script, data.instructions, prepared, settings,
                                       voice_match, directed.available, retries,
                                       trained["name"] if trained else None, user_id=owner_id(user),
                                       voice_label="Studio voice" if customer else None)}

    @app.get("/api/comparisons", dependencies=admin_only)
    def comparisons():
        return {"comparisons": store.comparisons(), "trained_voice": current_trained()}

    @app.post("/api/comparisons", dependencies=admin_mutations, status_code=202)
    def create_comparison(data: ComparisonInput):
        if data.baseline == data.contender:
            raise StudioError("Choose two different voice engines to compare.")
        # Random order, so the owner cannot tell which clip is which until choosing.
        order = [data.baseline, data.contender]
        if secrets.randbelow(2):
            order.reverse()
        prepared = [{"text": data.text, "instructions": "", "pause_ms": PARAGRAPH_PAUSE_MS, "voice_match": match}
                    for match in order]
        trained = current_trained()
        return {"id": store.create_job("Blind comparison", f"{data.text}\n\n{data.text}", "", prepared, None,
                                       data.baseline, directed.available, 0,
                                       trained["name"] if trained else None, blind=True)}

    @app.put("/api/comparisons/{job_id}", dependencies=admin_mutations)
    def choose_comparison(job_id: UUID, data: ChoiceInput):
        store.choose_blind(job_id.hex, data.choice)
        return {"comparisons": store.comparisons()}

    @app.post("/api/sections/preview", dependencies=mutations)
    def preview_sections(data: PreviewInput):
        sections = split_script_with_pauses(data.script)
        return {"sections": [text for text, _ in sections], "pauses": [pause for _, pause in sections]}

    @app.get("/api/jobs/{job_id}", dependencies=job_access)
    def job_details(job_id: UUID, user: Annotated[dict, Depends(require_user)]):
        job = store.job(job_id.hex)
        pairs = store.pronunciation_pairs(job["user_id"])  # Admins' own words are global, so this is safe for them.
        details = {
            **{key: job[key] for key in ("id", "title", "script", "instructions", "engine", "created_at", "status", "error", "export")},
            "voice_reference": job["voice_label"],
            "voice_transcript_ready": bool(job["voice_transcript"]),
            "trained_voice": job["trained_voice"],
            "source": job["source"],
            "approved": bool(job["export"]) and job["approved_export"] == job["export"],
            "delivered": json.loads(job["delivered"]) if job["delivered"] else None,
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
        if user["role"] == "customer":
            return customer_view(details)
        owner = customer_owner(job["user_id"])
        details["owner"] = ({"id": owner["id"], "email": owner["email"], "name": owner["name"],
                             "disabled": bool(owner["disabled"])} if owner else None)
        details["read_only"] = owner is not None
        return details

    @app.post("/api/jobs/{job_id}/sections/{number}", dependencies=job_mutations, status_code=202)
    def regenerate(job_id: UUID, number: int, data: SectionInput, user: Annotated[dict, Depends(require_user)]):
        customer = user["role"] == "customer"
        settings = data.turbo_settings.model_dump() if data.turbo_settings and not customer else None
        trained = current_trained()
        store.regenerate(job_id.hex, number, data.text, data.instructions, directed.available, settings,
                         house_match() if customer else data.voice_match, trained["name"] if trained else None)
        return {"id": job_id.hex}

    @app.put("/api/jobs/{job_id}/sections/{number}/pause", dependencies=job_mutations, status_code=202)
    def update_pause(job_id: UUID, number: int, data: PauseInput):
        store.adjust_pause(job_id.hex, number, data.pause_ms)
        return {"id": job_id.hex}

    @app.post("/api/jobs/{job_id}/retry", dependencies=job_mutations, status_code=202)
    def retry(job_id: UUID):
        store.retry(job_id.hex)
        return {"id": job_id.hex}

    @app.put("/api/jobs/{job_id}/loudness", dependencies=job_mutations, status_code=202)
    def update_loudness(job_id: UUID, data: LoudnessInput):
        store.set_loudness(job_id.hex, None if data.target is None else float(data.target))
        return {"id": job_id.hex}

    @app.post("/api/jobs/{job_id}/check", dependencies=job_mutations, status_code=202)
    def check_recording(job_id: UUID):
        if not checker.available:
            raise StudioError("Set up automatic checks using the README first.", 409)
        store.request_check(job_id.hex)
        return {"id": job_id.hex}

    @app.post("/api/jobs/{job_id}/balance", dependencies=job_mutations, status_code=202)
    def balance(job_id: UUID):
        store.rebuild(job_id.hex)
        return {"id": job_id.hex}

    @app.get("/api/jobs/{job_id}/sections/{number}/audio", dependencies=job_access)
    def section_audio(job_id: UUID, number: int):
        job = store.job(job_id.hex)
        for section in job["sections"]:
            if section["number"] == number and section["audio"]:
                path = store.recordings / job_id.hex / section["audio"]
                if not path.is_file():
                    raise StudioError("This audio file could not be found.", 404)
                return file_response(balanced_section(path), "audio/wav")
        raise StudioError("This section has no audio yet.", 404)

    @app.get("/api/jobs/{job_id}/sections/{number}/takes/{take_id}/audio", dependencies=job_access)
    def take_audio(job_id: UUID, number: int, take_id: UUID):
        take = store.take(job_id.hex, number, take_id.hex)
        path = store.recordings / job_id.hex / take["audio"]
        if not path.is_file():
            raise StudioError("This take's audio file could not be found.", 404)
        return file_response(balanced_section(path), "audio/wav")

    @app.post("/api/jobs/{job_id}/sections/{number}/takes/{take_id}/restore", dependencies=job_mutations, status_code=202)
    def restore_take(job_id: UUID, number: int, take_id: UUID):
        store.restore_take(job_id.hex, number, take_id.hex)
        return {"id": job_id.hex}

    @app.get("/api/jobs/{job_id}/export/{kind}", dependencies=job_access)
    def audio_export(job_id: UUID, kind: Literal["wav", "mp3"], download: bool = False):
        job = store.job(job_id.hex)
        if not job["export"]:
            raise StudioError("This recording has no export yet.", 404)
        media_type = "audio/wav" if kind == "wav" else "audio/mpeg"
        safe_title = safe_name(job["title"])
        return file_response(store.recordings / job_id.hex / f"{job['export']}.{kind}", media_type,
                             f"{safe_title}.{kind}" if download else None)

    @app.post("/api/jobs/{job_id}/approve", dependencies=admin_mutations)
    def approve(job_id: UUID):
        """Approve the current export and copy its WAV and MP3 to the outbox folder."""
        job = store.job(job_id.hex)
        if customer_owner(job["user_id"]):
            raise StudioError("Customer recordings are delivered to the customer, not to the outbox.", 403)
        if job["status"] != "ready" or not job["export"]:
            raise StudioError("Wait for the recording to finish before approving it.", 409)
        folder = store.recordings / job_id.hex
        sources = [folder / f"{job['export']}.{kind}" for kind in ("wav", "mp3")]
        if not all(path.is_file() for path in sources):
            raise StudioError("This recording's files could not be found.", 404)
        outbox_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{safe_name(job['title'])} {datetime.now().strftime('%Y-%m-%d')}"
        delivered = []
        try:
            for path in sources:
                target = unique(outbox_dir, f"{stem}{path.suffix}")
                shutil.copyfile(path, target)
                delivered.append(target)
            store.approve(job_id.hex, job["export"], [target.name for target in delivered])
        except Exception:
            for target in delivered:
                target.unlink(missing_ok=True)
            raise
        return {"delivered": [target.name for target in delivered], "outbox": str(outbox_dir)}

    @app.post("/api/intake", dependencies=admin_mutations, status_code=202)
    def intake_file(file: Annotated[UploadFile, File()],
                    title: Annotated[str, Form(), StringConstraints(strip_whitespace=True, max_length=100)] = ""):
        """For other apps on this computer: a script file becomes a recording awaiting approval."""
        contents = file.file.read(MAX_SCRIPT_FILE_BYTES + 1)
        script = extract_script(file.filename or "", contents)["text"]
        name = title or Path(file.filename or "").stem[:100] or "API recording"
        return {"id": intake_job(name, script, source="api")}

    @app.delete("/api/jobs/{job_id}", dependencies=job_mutations, status_code=204)
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

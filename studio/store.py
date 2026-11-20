import re
import json
import secrets
import sqlite3
import textwrap
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import uuid4


SCRIPT_LIMIT = 12000
CHUNK_LIMIT = 220
DEFAULT_PAUSE_MS = 160  # Legacy column default for sections created before edge trimming.
CLAUSE_PAUSE_MS = 150  # After a long sentence is wrapped mid-sentence.
SENTENCE_PAUSE_MS = 350
PARAGRAPH_PAUSE_MS = 750
MAX_PAUSE_MS = 5000
INSTRUCTIONS_LIMIT = 240
ENGINE_VERSION = "Chatterbox Turbo 0.1.7 · 749d1c1"
DIRECTED_VERSION = "VoxCPM2 2.0.3 · 7936080"
TURBO_SOUND_CUE = re.compile(
    r"\[(?:advertisement|angry|chuckle|clear throat|cough|crying|dramatic|fear|gasp|groan|"
    r"happy|laugh|narration|sarcastic|shush|sigh|sniff|surprised|whispering)\]", re.IGNORECASE,
)


class StudioError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def new_id():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_directions(text, instructions, available):
    if instructions and not available:
        raise StudioError("Special instructions need the local VoxCPM2 setup described in the README.", 409)
    if instructions and not instructions.replace("(", "").replace(")", "").strip():
        raise StudioError("Enter a description of the delivery you want.")
    if instructions and TURBO_SOUND_CUE.search(text):
        raise StudioError("Remove bracketed sound or delivery cues from the script when using Special instructions.")


# Abbreviations whose trailing period does not end a sentence.
TITLES = {"mr", "mrs", "ms", "dr", "prof", "st", "rev", "gen", "capt", "lt", "sgt", "hon"}
ALWAYS_CONTINUE = {"e.g", "i.e", "cf", "approx", "vs"}
SOFT_ABBREVIATIONS = {
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "no", "nos", "vol", "fig", "dept", "est", "inc", "ltd", "co", "corp", "jr", "sr", "etc", "mt", "ave", "blvd",
}
INITIALISM = re.compile(r"^(?:[a-z]\.)+[a-z]$")


def continues_sentence(previous, following):
    """Whether a period after `previous` is an abbreviation rather than a sentence end."""
    if not previous.endswith(".") or not following:
        return False
    word = previous.rstrip(".").lstrip("(\"'\u201c\u2018").lower()
    if word in TITLES or word in ALWAYS_CONTINUE:
        return True
    if len(word) == 1 and word.isalpha() and previous[-2].isupper():
        return True  # A middle initial, as in "John F. Kennedy".
    if word in SOFT_ABBREVIATIONS or INITIALISM.match(word):
        # "Jan. 5" and "the U.S. office" continue; "the U.S. Next year" starts a new sentence.
        return not following[0].isupper()
    return False


def split_sentences(paragraph):
    sentences = []
    for piece in re.split(r"(?<=[.!?])\s+", paragraph):
        if not piece:
            continue
        if sentences and continues_sentence(sentences[-1].split()[-1], piece):
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    return sentences


def split_script_with_pauses(script):
    """Split at paragraph and sentence ends, returning (text, pause after it in ms) pairs."""
    chunks = []
    for paragraph in re.split(r"\n\s*\n", script):
        current = ""
        start = len(chunks)
        for sentence in split_sentences(" ".join(paragraph.split())):
            if len(current) + len(sentence) + 1 <= CHUNK_LIMIT:
                current = f"{current} {sentence}".strip()
                continue
            if current:
                chunks.append([current, SENTENCE_PAUSE_MS])
            *finished, current = textwrap.wrap(
                sentence, width=CHUNK_LIMIT, break_on_hyphens=False,
            )
            chunks.extend([piece, CLAUSE_PAUSE_MS] for piece in finished)
        if current:
            chunks.append([current, SENTENCE_PAUSE_MS])
        if len(chunks) > start:
            chunks[-1][1] = PARAGRAPH_PAUSE_MS
    return [tuple(chunk) for chunk in chunks]


def split_script(script):
    return [text for text, _pause in split_script_with_pauses(script)]


class Store:
    def __init__(self, root):
        self.root = root
        self.voices = root / "voices"
        self.recordings = root / "recordings"
        self.originals = self.voices / "originals"

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.root / "studio.sqlite3", timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def initialize(self):
        self.voices.mkdir(parents=True, exist_ok=True)
        self.originals.mkdir(exist_ok=True)
        self.recordings.mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS voices (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    duration REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS voice_selection (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    voice_id TEXT NOT NULL REFERENCES voices(id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    script TEXT NOT NULL,
                    voice_id TEXT NOT NULL REFERENCES voices(id),
                    engine TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error TEXT,
                    export TEXT
                );
                CREATE TABLE IF NOT EXISTS sections (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    seed INTEGER NOT NULL DEFAULT 35,
                    pending INTEGER NOT NULL DEFAULT 1,
                    audio TEXT,
                    PRIMARY KEY (job_id, number)
                );
                CREATE TABLE IF NOT EXISTS section_takes (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    section_number INTEGER NOT NULL,
                    audio TEXT NOT NULL,
                    text TEXT,
                    instructions TEXT,
                    seed INTEGER,
                    engine TEXT,
                    device TEXT,
                    runtime TEXT,
                    model_sha256 TEXT,
                    created_at TEXT,
                    UNIQUE(job_id, section_number, audio),
                    FOREIGN KEY (job_id, section_number) REFERENCES sections(job_id, number) ON DELETE CASCADE
                );
            """)
            section_columns = {column["name"] for column in db.execute("PRAGMA table_info(sections)")}
            if "pause_ms" not in section_columns:
                db.execute(f"""
                    ALTER TABLE sections ADD COLUMN pause_ms INTEGER NOT NULL DEFAULT {DEFAULT_PAUSE_MS}
                    CHECK (pause_ms BETWEEN 0 AND {MAX_PAUSE_MS})
                """)
            if "instructions" not in section_columns:
                db.execute("ALTER TABLE sections ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
            if "turbo_settings" not in section_columns:
                db.execute("ALTER TABLE sections ADD COLUMN turbo_settings TEXT")
            if "turbo_settings" not in {column["name"] for column in db.execute("PRAGMA table_info(section_takes)")}:
                db.execute("ALTER TABLE section_takes ADD COLUMN turbo_settings TEXT")
            for name, kind in (("rendered_engine", "TEXT"), ("rendered_device", "TEXT"),
                               ("rendered_runtime", "TEXT"), ("rendered_model_sha256", "TEXT"),
                               ("rendered_seed", "INTEGER"), ("rendered_at", "TEXT")):
                if name not in section_columns:
                    db.execute(f"ALTER TABLE sections ADD COLUMN {name} {kind}")
            job_columns = {column["name"] for column in db.execute("PRAGMA table_info(jobs)")}
            if "instructions" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
            # Existing recordings keep their original joins; new recordings trim section edge silence.
            if "trim_edges" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN trim_edges INTEGER NOT NULL DEFAULT 0")
            # Older databases know only the current audio. Pending edits may no longer match it.
            db.execute("""
                INSERT OR IGNORE INTO section_takes
                    (id, job_id, section_number, audio, text, instructions, seed, engine, device, runtime, model_sha256, created_at)
                SELECT lower(hex(randomblob(16))), job_id, number, audio,
                    CASE WHEN pending = 0 THEN text END,
                    CASE WHEN pending = 0 THEN instructions END,
                    rendered_seed, rendered_engine, rendered_device, rendered_runtime, rendered_model_sha256, rendered_at
                FROM sections WHERE audio IS NOT NULL
            """)
            db.execute("""
                UPDATE jobs SET status = 'failed', error = 'Generation was interrupted. Retry to continue.'
                WHERE status = 'running'
            """)

    def active_voice(self, db=None):
        if db is None:
            with self.connect() as connection:
                return self.active_voice(connection)
        row = db.execute("""
            SELECT * FROM voices WHERE id = COALESCE(
                (SELECT voice_id FROM voice_selection WHERE id = 1),
                (SELECT id FROM voices ORDER BY rowid DESC LIMIT 1)
            )
        """).fetchone()
        return dict(row) if row else None

    def add_voice(self, voice_id, label, duration):
        with self.connect() as db:
            db.execute("INSERT INTO voices VALUES (?, ?, ?)", (voice_id, label, duration))
            self.select_voice(db, voice_id)
        return {"id": voice_id, "label": label, "duration": duration}

    @staticmethod
    def select_voice(db, voice_id):
        db.execute("""
            INSERT INTO voice_selection (id, voice_id) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET voice_id = excluded.voice_id
        """, (voice_id,))

    def activate_voice(self, voice_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            voice = db.execute("SELECT * FROM voices WHERE id = ?", (voice_id,)).fetchone()
            if voice is None:
                raise StudioError("Voice reference not found.", 404)
            self.select_voice(db, voice_id)
            return dict(voice)

    def state(self):
        with self.connect() as db:
            db.execute("BEGIN")
            jobs = db.execute("""
                SELECT id, title, status, created_at FROM jobs ORDER BY rowid DESC
            """).fetchall()
            voices = db.execute("SELECT * FROM voices ORDER BY rowid DESC").fetchall()
            return {"voice": self.active_voice(db), "voices": [dict(voice) for voice in voices],
                    "jobs": [dict(job) for job in jobs]}

    def create_job(self, title, script, instructions="", prepared_sections=None, turbo_settings=None):
        sections = prepared_sections if prepared_sections is not None else [
            {"text": text, "instructions": instructions, "pause_ms": pause}
            for text, pause in split_script_with_pauses(script)
        ]
        if not sections:
            raise StudioError("Enter a script with text to speak.")
        job_id = new_id()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            voice = self.active_voice(db)
            if voice is None:
                raise StudioError("Add a voice reference before generating a recording.", 409)
            db.execute("""
                INSERT INTO jobs (id, title, script, voice_id, engine, created_at, instructions, trim_edges)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (job_id, title, script, voice["id"], DIRECTED_VERSION if any(section["instructions"] for section in sections) else ENGINE_VERSION,
                  now(), instructions))
            settings_json = json.dumps(turbo_settings, sort_keys=True) if turbo_settings is not None else None
            def selected_seed(section):
                if section["instructions"]:
                    return 35
                selected = turbo_settings["seed"] if turbo_settings is not None else 35
                return selected if selected else secrets.randbelow(2147483647) + 1
            db.executemany("""
                INSERT INTO sections (job_id, number, text, instructions, seed, turbo_settings, pause_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [(job_id, index, section["text"], section["instructions"], selected_seed(section), settings_json,
                   section.get("pause_ms", PARAGRAPH_PAUSE_MS))
                  for index, section in enumerate(sections, 1)])
        return job_id

    def job(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN")
            job = dict(self.get_job(db, job_id))
            voice = db.execute("SELECT label FROM voices WHERE id = ?", (job["voice_id"],)).fetchone()
            job["voice_label"] = voice["label"] if voice else None
            job["sections"] = [dict(section) for section in db.execute(
                "SELECT * FROM sections WHERE job_id = ? ORDER BY number", (job_id,),
            )]
            takes = db.execute("SELECT * FROM section_takes WHERE job_id = ? ORDER BY rowid DESC", (job_id,)).fetchall()
            by_section = {section["number"]: section for section in job["sections"]}
            for section in job["sections"]:
                section["takes"] = []
            for take in takes:
                by_section[take["section_number"]]["takes"].append(dict(take))
            return job

    @staticmethod
    def get_job(db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise StudioError("Recording not found.", 404)
        return row

    def claim(self):
        with self.connect() as db:
            row = db.execute("""
                UPDATE jobs SET status = 'running' WHERE id = (
                    SELECT id FROM jobs WHERE status = 'queued' ORDER BY rowid LIMIT 1
                ) RETURNING id
            """).fetchone()
            return row["id"] if row else None

    def finish_section(self, job_id, number, audio, details=None, seed=None):
        details = details or {}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            section = db.execute("SELECT text, instructions, turbo_settings FROM sections WHERE job_id = ? AND number = ?",
                                 (job_id, number)).fetchone()
            if section is None:
                raise StudioError("Section not found.", 404)
            rendered_at = now()
            db.execute("""
                INSERT INTO section_takes
                    (id, job_id, section_number, audio, text, instructions, seed, engine, device, runtime, model_sha256, created_at, turbo_settings)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_id(), job_id, number, audio, section["text"], section["instructions"], seed,
                  details.get("engine"), details.get("device"), details.get("runtime"), details.get("model_sha256"), rendered_at,
                  section["turbo_settings"]))
            db.execute("""
                UPDATE sections SET pending = 0, audio = ?, rendered_engine = ?, rendered_device = ?,
                    rendered_runtime = ?, rendered_model_sha256 = ?, rendered_seed = ?, rendered_at = ?
                WHERE job_id = ? AND number = ?
            """, (audio, details.get("engine"), details.get("device"), details.get("runtime"),
                  details.get("model_sha256"), seed, rendered_at, job_id, number))

    def take(self, job_id, number, take_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM section_takes WHERE id = ? AND job_id = ? AND section_number = ?",
                             (take_id, job_id, number)).fetchone()
            if row is None:
                raise StudioError("Take not found.", 404)
            return dict(row)

    def restore_take(self, job_id, number, take_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] not in {"ready", "failed"}:
                raise StudioError("Wait for the recording to finish before choosing a take.", 409)
            take = db.execute("SELECT * FROM section_takes WHERE id = ? AND job_id = ? AND section_number = ?",
                              (take_id, job_id, number)).fetchone()
            if take is None:
                raise StudioError("Take not found.", 404)
            if take["text"] is None or take["instructions"] is None:
                raise StudioError("The words for this older take are unknown, so it cannot be restored.", 409)
            if not (self.recordings / job_id / take["audio"]).is_file():
                raise StudioError("This take's audio file could not be found.", 404)
            db.execute("""
                UPDATE sections SET text = ?, instructions = ?, seed = COALESCE(?, seed), pending = 0,
                    audio = ?, rendered_engine = ?, rendered_device = ?, rendered_runtime = ?,
                    rendered_model_sha256 = ?, rendered_seed = ?, rendered_at = ?, turbo_settings = ?
                WHERE job_id = ? AND number = ?
            """, (take["text"], take["instructions"], take["seed"], take["audio"], take["engine"],
                  take["device"], take["runtime"], take["model_sha256"], take["seed"], take["created_at"],
                  take["turbo_settings"], job_id, number))
            db.execute("UPDATE jobs SET status = 'queued', error = NULL WHERE id = ?", (job_id,))

    def finish_job(self, job_id, export):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status = 'ready', export = ?, error = NULL WHERE id = ?",
                       (export, job_id))

    def fail_job(self, job_id, error):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status = 'failed', error = ? WHERE id = ?", (error, job_id))

    def retry(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] != "failed":
                raise StudioError("Only a failed recording can be retried.", 409)
            db.execute("UPDATE jobs SET status = 'queued', error = NULL WHERE id = ?", (job_id,))

    def rebuild(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] != "ready":
                raise StudioError("Wait for the recording to finish before updating its volume.", 409)
            db.execute("UPDATE jobs SET status = 'queued', error = NULL WHERE id = ?", (job_id,))

    def regenerate(self, job_id, number, text, instructions=None, directed_available=False, turbo_settings=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] != "ready":
                raise StudioError("Wait for the recording to finish before regenerating.", 409)
            section = db.execute("SELECT instructions, turbo_settings FROM sections WHERE job_id = ? AND number = ?",
                                 (job_id, number)).fetchone()
            if section is None:
                raise StudioError("Section not found.", 404)
            instructions = section["instructions"] if instructions is None else instructions
            validate_directions(text, instructions, directed_available)
            if turbo_settings is None and section["turbo_settings"]:
                turbo_settings = json.loads(section["turbo_settings"])
                turbo_settings["seed"] = 0
            seed = turbo_settings["seed"] if turbo_settings and not instructions else 0
            seed = seed if seed else secrets.randbelow(2147483647) + 1
            settings_json = json.dumps(turbo_settings, sort_keys=True) if turbo_settings is not None else None
            db.execute("""
                UPDATE sections SET text = ?, instructions = ?, seed = ?, turbo_settings = ?, pending = 1 WHERE job_id = ? AND number = ?
            """, (text, instructions, seed, settings_json, job_id, number))
            db.execute("UPDATE jobs SET status = 'queued', error = NULL WHERE id = ?", (job_id,))

    def adjust_pause(self, job_id, number, pause_ms):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] != "ready":
                raise StudioError("Wait for the recording to finish before editing its pauses.", 409)
            section = db.execute("SELECT pause_ms FROM sections WHERE job_id = ? AND number = ?",
                                 (job_id, number)).fetchone()
            if section is None:
                raise StudioError("Section not found.", 404)
            following = db.execute("SELECT 1 FROM sections WHERE job_id = ? AND number > ? LIMIT 1",
                                   (job_id, number)).fetchone()
            if following is None:
                raise StudioError("Pauses go between sections. Choose an earlier section.")
            if section["pause_ms"] == pause_ms:
                return
            db.execute("UPDATE sections SET pause_ms = ? WHERE job_id = ? AND number = ?",
                       (pause_ms, job_id, number))
            db.execute("UPDATE jobs SET status = 'queued', error = NULL WHERE id = ?", (job_id,))

    def delete(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] in {"queued", "running"}:
                raise StudioError("Wait for generation to finish before deleting this recording.", 409)
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

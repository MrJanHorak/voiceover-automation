import re
import secrets
import sqlite3
import textwrap
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import uuid4


SCRIPT_LIMIT = 12000
CHUNK_LIMIT = 220
DEFAULT_PAUSE_MS = 160
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


def split_script(script):
    chunks = []
    for paragraph in re.split(r"\n\s*\n", script):
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(paragraph.split())):
            if not sentence:
                continue
            if len(current) + len(sentence) + 1 <= CHUNK_LIMIT:
                current = f"{current} {sentence}".strip()
                continue
            if current:
                chunks.append(current)
            *finished, current = textwrap.wrap(
                sentence, width=CHUNK_LIMIT, break_on_hyphens=False,
            )
            chunks.extend(finished)
        if current:
            chunks.append(current)
    return chunks


class Store:
    def __init__(self, root):
        self.root = root
        self.voices = root / "voices"
        self.recordings = root / "recordings"

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.root / "studio.sqlite3", timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def initialize(self):
        self.voices.mkdir(parents=True, exist_ok=True)
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
            """)
            section_columns = {column["name"] for column in db.execute("PRAGMA table_info(sections)")}
            if "pause_ms" not in section_columns:
                db.execute(f"""
                    ALTER TABLE sections ADD COLUMN pause_ms INTEGER NOT NULL DEFAULT {DEFAULT_PAUSE_MS}
                    CHECK (pause_ms BETWEEN 0 AND {MAX_PAUSE_MS})
                """)
            if "instructions" not in section_columns:
                db.execute("ALTER TABLE sections ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
            if "instructions" not in {column["name"] for column in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
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

    def create_job(self, title, script, instructions="", prepared_sections=None):
        sections = prepared_sections if prepared_sections is not None else [
            {"text": text, "instructions": instructions} for text in split_script(script)
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
                INSERT INTO jobs (id, title, script, voice_id, engine, created_at, instructions)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (job_id, title, script, voice["id"], DIRECTED_VERSION if any(section["instructions"] for section in sections) else ENGINE_VERSION,
                  now(), instructions))
            db.executemany("INSERT INTO sections (job_id, number, text, instructions) VALUES (?, ?, ?, ?)",
                           [(job_id, index, section["text"], section["instructions"]) for index, section in enumerate(sections, 1)])
        return job_id

    def job(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN")
            job = dict(self.get_job(db, job_id))
            job["sections"] = [dict(section) for section in db.execute(
                "SELECT * FROM sections WHERE job_id = ? ORDER BY number", (job_id,),
            )]
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

    def finish_section(self, job_id, number, audio):
        with self.connect() as db:
            db.execute("UPDATE sections SET pending = 0, audio = ? WHERE job_id = ? AND number = ?",
                       (audio, job_id, number))

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

    def regenerate(self, job_id, number, text, instructions=None, directed_available=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] != "ready":
                raise StudioError("Wait for the recording to finish before regenerating.", 409)
            section = db.execute("SELECT instructions FROM sections WHERE job_id = ? AND number = ?",
                                 (job_id, number)).fetchone()
            if section is None:
                raise StudioError("Section not found.", 404)
            instructions = section["instructions"] if instructions is None else instructions
            validate_directions(text, instructions, directed_available)
            db.execute("""
                UPDATE sections SET text = ?, instructions = ?, seed = ?, pending = 1 WHERE job_id = ? AND number = ?
            """, (text, instructions, secrets.randbits(31), job_id, number))
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

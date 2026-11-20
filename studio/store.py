import re
import json
import hashlib
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
TRANSCRIPT_LIMIT = 1000
WRITTEN_LIMIT = 100
SPOKEN_LIMIT = 200
# standard: Chatterbox. maximum: VoxCPM2 ultimate clone. trained: VoxCPM2 with the trained voice (LoRA)
# alone. trained_reference: the trained voice together with the reference clip.
VOICE_MATCHES = ("standard", "maximum", "trained", "trained_reference")
TRAINED_MATCHES = ("trained", "trained_reference")
MASTER_RATE = 48000
DEFAULT_LOUDNESS = -16.0
LOUDNESS_TARGETS = (-14.0, -16.0, -23.0, -24.0)
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
        raise StudioError("Delivery styles need the local VoxCPM2 setup described in the README.", 409)
    if instructions and not instructions.replace("(", "").replace(")", "").strip():
        raise StudioError("Enter a description of the delivery you want.")
    if instructions and TURBO_SOUND_CUE.search(text):
        raise StudioError("Remove bracketed sound or delivery cues from the script when using a delivery style.")


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


def validate_voice_match(text, instructions, voice_match, available, transcript, trained=None):
    """Check a section that uses VoxCPM2 (maximum resemblance or a trained voice) instead of Chatterbox."""
    if voice_match not in VOICE_MATCHES:
        raise StudioError("Choose a supported voice engine.")
    if voice_match in TRAINED_MATCHES:
        # The trained voice also applies to sections with a delivery style.
        if not available:
            raise StudioError("A trained voice needs the local VoxCPM2 setup described in the README.", 409)
        if not trained:
            raise StudioError("Add a trained voice first. See Voice training in the guide.", 409)
        if TURBO_SOUND_CUE.search(text):
            raise StudioError("Remove bracketed sound cues when using a trained voice; they are read aloud by VoxCPM2.")
        return
    if voice_match != "maximum" or instructions:
        return  # Sections with special instructions use VoxCPM2's directed mode.
    if not available:
        raise StudioError("Maximum resemblance needs the local VoxCPM2 setup described in the README.", 409)
    if not (transcript or "").strip():
        raise StudioError("Add the exact words spoken in the voice reference before using maximum resemblance.", 409)
    if TURBO_SOUND_CUE.search(text):
        raise StudioError("Remove bracketed sound cues when using maximum resemblance; they are read aloud by VoxCPM2.")


CLAUSE_BREAK = re.compile(r"(?<=[,;:\u2014\u2013])\s+|\s+(?=[\u2014\u2013]\s)")


def split_long_sentence(sentence, limit=None):
    """Split an overlong sentence at clause punctuation, keeping the pieces balanced.

    Falls back to the word boundary nearest the balanced point only when a clause has no
    usable punctuation, so a phrase such as "click buy" is never cut in the middle.
    """
    limit = limit or CHUNK_LIMIT
    if len(sentence) <= limit:
        return [sentence]
    pieces = -(-len(sentence) // limit)
    target = len(sentence) / pieces
    breaks = [match.end() for match in CLAUSE_BREAK.finditer(sentence) if match.end() <= limit]
    breaks = [point for point in breaks if len(sentence) - point >= 1]
    if not breaks:
        breaks = [match.end() for match in re.finditer(r"\s+", sentence) if match.end() <= limit]
    if not breaks:
        return textwrap.wrap(sentence, width=limit, break_on_hyphens=False)
    point = min(breaks, key=lambda position: abs(position - target))
    first, rest = sentence[:point].rstrip(), sentence[point:].lstrip()
    return [first, *split_long_sentence(rest, limit)]


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
            *finished, current = split_long_sentence(sentence)
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
        self.dataset = root / "dataset"

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.root / "studio.sqlite3", timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def initialize(self):
        self.voices.mkdir(parents=True, exist_ok=True)
        self.originals.mkdir(exist_ok=True)
        (self.dataset / "takes").mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS dataset_prompts (
                    id TEXT PRIMARY KEY,
                    position INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source IN ('builtin', 'custom')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dataset_takes (
                    id TEXT PRIMARY KEY,
                    prompt_id TEXT NOT NULL UNIQUE REFERENCES dataset_prompts(id) ON DELETE CASCADE,
                    audio TEXT NOT NULL,
                    sample_rate INTEGER NOT NULL,
                    duration REAL NOT NULL,
                    check_result TEXT,
                    kept INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pronunciations (
                    id TEXT PRIMARY KEY,
                    written TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    spoken TEXT NOT NULL
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
            if "voice_match" not in section_columns:
                db.execute("ALTER TABLE sections ADD COLUMN voice_match TEXT NOT NULL DEFAULT 'standard'")
            take_columns = {column["name"] for column in db.execute("PRAGMA table_info(section_takes)")}
            if "turbo_settings" not in take_columns:
                db.execute("ALTER TABLE section_takes ADD COLUMN turbo_settings TEXT")
            if "voice_match" not in take_columns:
                db.execute("ALTER TABLE section_takes ADD COLUMN voice_match TEXT")
            if "spoken" not in take_columns:
                db.execute("ALTER TABLE section_takes ADD COLUMN spoken TEXT")
            if "check_result" not in take_columns:
                db.execute("ALTER TABLE section_takes ADD COLUMN check_result TEXT")
            if "transcript" not in {column["name"] for column in db.execute("PRAGMA table_info(voices)")}:
                db.execute("ALTER TABLE voices ADD COLUMN transcript TEXT NOT NULL DEFAULT ''")
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
            # Existing recordings keep 24 kHz exports and no final loudness pass until the owner chooses one.
            if "master_rate" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN master_rate INTEGER NOT NULL DEFAULT 24000")
            if "loudness_target" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN loudness_target REAL")
            if "export_loudness" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN export_loudness TEXT")
            if "auto_retries" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN auto_retries INTEGER NOT NULL DEFAULT 0")
            if "check_requested" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN check_requested INTEGER NOT NULL DEFAULT 0")
            if "trained_voice" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN trained_voice TEXT")
            if "blind" not in job_columns:
                # Blind comparisons render one sentence with two voice engines in random order.
                db.execute("ALTER TABLE jobs ADD COLUMN blind INTEGER NOT NULL DEFAULT 0")
            if "blind_choice" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN blind_choice TEXT")
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
            self.seed_prompts(db)

    # ---- Training recordings ----

    @staticmethod
    def seed_prompts(db):
        """Add any built-in prompts not yet present; ids come from the text, so this is idempotent."""
        from .dataset_prompts import PROMPTS

        existing = {row["id"] for row in db.execute("SELECT id FROM dataset_prompts WHERE source = 'builtin'")}
        rows = [(f"b{hashlib.sha1(text.encode()).hexdigest()[:15]}", position, text, now())
                for position, text in enumerate(PROMPTS)]
        db.executemany("INSERT INTO dataset_prompts (id, position, text, source, created_at) VALUES (?, ?, ?, 'builtin', ?)",
                       [row for row in rows if row[0] not in existing])

    def dataset_state(self):
        with self.connect() as db:
            db.execute("BEGIN")
            prompts = db.execute("""
                SELECT p.id, p.text, p.source, t.id AS take_id, t.duration, t.check_result, t.kept, t.created_at AS take_at
                FROM dataset_prompts p LEFT JOIN dataset_takes t ON t.prompt_id = p.id
                ORDER BY p.source = 'custom', p.position, p.created_at
            """).fetchall()
            return [dict(row) for row in prompts]

    def add_prompts(self, texts):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            start = db.execute("SELECT COALESCE(MAX(position), 0) FROM dataset_prompts WHERE source = 'custom'").fetchone()[0]
            if db.execute("SELECT COUNT(*) FROM dataset_prompts WHERE source = 'custom'").fetchone()[0] + len(texts) > 2000:
                raise StudioError("You can add up to 2,000 of your own prompts.")
            rows = [(f"c{new_id()[:15]}", start + index + 1, text, now()) for index, text in enumerate(texts)]
            db.executemany("INSERT INTO dataset_prompts (id, position, text, source, created_at) VALUES (?, ?, ?, 'custom', ?)", rows)
            return len(rows)

    def delete_prompt(self, prompt_id):
        """Remove one of the owner's prompts and return its take's audio name, if any."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prompt = db.execute("SELECT source FROM dataset_prompts WHERE id = ?", (prompt_id,)).fetchone()
            if prompt is None:
                raise StudioError("Prompt not found.", 404)
            if prompt["source"] != "custom":
                raise StudioError("Built-in prompts can be skipped but not deleted.", 409)
            take = db.execute("SELECT audio FROM dataset_takes WHERE prompt_id = ?", (prompt_id,)).fetchone()
            db.execute("DELETE FROM dataset_prompts WHERE id = ?", (prompt_id,))
            return take["audio"] if take else None

    def prompt(self, prompt_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM dataset_prompts WHERE id = ?", (prompt_id,)).fetchone()
            if row is None:
                raise StudioError("Prompt not found.", 404)
            return dict(row)

    def save_take(self, prompt_id, take_id, audio, sample_rate, duration, result):
        """Store a new take for a prompt, replacing any earlier one; returns the replaced audio name."""
        kept = 1 if result.get("status") != "review" else 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM dataset_prompts WHERE id = ?", (prompt_id,)).fetchone() is None:
                raise StudioError("Prompt not found.", 404)
            previous = db.execute("SELECT audio FROM dataset_takes WHERE prompt_id = ?", (prompt_id,)).fetchone()
            db.execute("DELETE FROM dataset_takes WHERE prompt_id = ?", (prompt_id,))
            db.execute("""
                INSERT INTO dataset_takes (id, prompt_id, audio, sample_rate, duration, check_result, kept, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (take_id, prompt_id, audio, sample_rate, duration, json.dumps(result), kept, now()))
            return previous["audio"] if previous else None

    def dataset_take(self, take_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM dataset_takes WHERE id = ?", (take_id,)).fetchone()
            if row is None:
                raise StudioError("Recording not found.", 404)
            return dict(row)

    def set_take_kept(self, take_id, kept):
        with self.connect() as db:
            if db.execute("UPDATE dataset_takes SET kept = ? WHERE id = ?", (1 if kept else 0, take_id)).rowcount == 0:
                raise StudioError("Recording not found.", 404)

    def kept_takes(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT t.audio, p.text FROM dataset_takes t JOIN dataset_prompts p ON p.id = t.prompt_id
                WHERE t.kept = 1 ORDER BY p.source = 'custom', p.position, p.created_at
            """)]

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
            db.execute("INSERT INTO voices (id, label, duration) VALUES (?, ?, ?)", (voice_id, label, duration))
            self.select_voice(db, voice_id)
        return {"id": voice_id, "label": label, "duration": duration, "transcript": ""}

    def set_transcript(self, voice_id, transcript):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE voices SET transcript = ? WHERE id = ?", (transcript, voice_id)).rowcount == 0:
                raise StudioError("Voice reference not found.", 404)
            return dict(db.execute("SELECT * FROM voices WHERE id = ?", (voice_id,)).fetchone())

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
                SELECT id, title, status, created_at FROM jobs WHERE blind = 0 ORDER BY rowid DESC
            """).fetchall()
            voices = db.execute("SELECT * FROM voices ORDER BY rowid DESC").fetchall()
            words = db.execute("SELECT * FROM pronunciations ORDER BY written COLLATE NOCASE").fetchall()
            return {"voice": self.active_voice(db), "voices": [dict(voice) for voice in voices],
                    "jobs": [dict(job) for job in jobs], "pronunciations": [dict(word) for word in words]}

    def pronunciation_pairs(self):
        with self.connect() as db:
            return [(row["written"], row["spoken"]) for row in db.execute("SELECT written, spoken FROM pronunciations")]

    def save_pronunciation(self, written, spoken):
        written, spoken = " ".join(written.split()), " ".join(spoken.split())
        if not re.search(r"\w", written) or not spoken:
            raise StudioError("Enter the written word and how to say it.")
        if len(written) > WRITTEN_LIMIT or len(spoken) > SPOKEN_LIMIT:
            raise StudioError("Shorten the word or its pronunciation.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM pronunciations WHERE written = ?", (written,)).fetchone()
            word_id = existing["id"] if existing else new_id()
            db.execute("""
                INSERT INTO pronunciations (id, written, spoken) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET written = excluded.written, spoken = excluded.spoken
            """, (word_id, written, spoken))
            return {"id": word_id, "written": written, "spoken": spoken}

    def delete_pronunciation(self, word_id):
        with self.connect() as db:
            if db.execute("DELETE FROM pronunciations WHERE id = ?", (word_id,)).rowcount == 0:
                raise StudioError("Pronunciation not found.", 404)

    def create_job(self, title, script, instructions="", prepared_sections=None, turbo_settings=None,
                   voice_match="standard", directed_available=False, auto_retries=0, trained_voice=None,
                   blind=False):
        if auto_retries not in (0, 1, 2):
            raise StudioError("Choose 0, 1 or 2 automatic retries.")
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
            for section in sections:
                section.setdefault("voice_match", voice_match)
                validate_voice_match(section["text"], section["instructions"], section["voice_match"],
                                     directed_available, voice["transcript"], trained_voice)
            uses_voxcpm = any(section["instructions"] or section["voice_match"] != "standard" for section in sections)
            uses_trained = any(section["voice_match"] in TRAINED_MATCHES for section in sections)
            db.execute("""
                INSERT INTO jobs (id, title, script, voice_id, engine, created_at, instructions, trim_edges,
                                  master_rate, loudness_target, auto_retries, trained_voice, blind)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """, (job_id, title, script, voice["id"], DIRECTED_VERSION if uses_voxcpm else ENGINE_VERSION,
                  now(), instructions, MASTER_RATE, DEFAULT_LOUDNESS, auto_retries,
                  trained_voice if uses_trained else None, int(blind)))
            settings_json = json.dumps(turbo_settings, sort_keys=True) if turbo_settings is not None else None
            def selected_seed(section):
                if section["instructions"] or section["voice_match"] != "standard":
                    return 35
                selected = turbo_settings["seed"] if turbo_settings is not None else 35
                return selected if selected else secrets.randbelow(2147483647) + 1
            db.executemany("""
                INSERT INTO sections (job_id, number, text, instructions, seed, turbo_settings, pause_ms, voice_match)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [(job_id, index, section["text"], section["instructions"], selected_seed(section), settings_json,
                   section.get("pause_ms", PARAGRAPH_PAUSE_MS), section["voice_match"])
                  for index, section in enumerate(sections, 1)])
        return job_id

    def job(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN")
            job = dict(self.get_job(db, job_id))
            voice = db.execute("SELECT label, transcript FROM voices WHERE id = ?", (job["voice_id"],)).fetchone()
            job["voice_label"] = voice["label"] if voice else None
            job["voice_transcript"] = voice["transcript"] if voice else ""
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

    def comparisons(self):
        """Blind comparisons, newest first. Which clip used which engine stays hidden until a choice is saved."""
        with self.connect() as db:
            db.execute("BEGIN")
            jobs = db.execute("""
                SELECT id, status, error, created_at, blind_choice, trained_voice FROM jobs
                WHERE blind = 1 ORDER BY rowid DESC
            """).fetchall()
            result = []
            for job in jobs:
                sections = db.execute("""
                    SELECT number, text, voice_match, pending, audio FROM sections WHERE job_id = ? ORDER BY number
                """, (job["id"],)).fetchall()
                item = {"id": job["id"], "status": job["status"], "error": job["error"], "created_at": job["created_at"],
                        "text": sections[0]["text"] if sections else "", "choice": job["blind_choice"],
                        "ready": job["status"] == "ready" and all(section["audio"] for section in sections)}
                if job["blind_choice"]:
                    item["engines"] = {"A": sections[0]["voice_match"], "B": sections[1]["voice_match"]}
                    item["trained_voice"] = job["trained_voice"]
                result.append(item)
            return result

    def choose_blind(self, job_id, choice):
        if choice not in ("A", "B", "same"):
            raise StudioError("Choose A, B or no difference.")
        with self.connect() as db:
            job = db.execute("SELECT blind, status, blind_choice FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None or not job["blind"]:
                raise StudioError("Comparison not found.", 404)
            if job["status"] != "ready":
                raise StudioError("Wait for both clips to finish.", 409)
            if job["blind_choice"]:
                raise StudioError("This comparison already has a choice.", 409)
            db.execute("UPDATE jobs SET blind_choice = ? WHERE id = ?", (choice, job_id))

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

    def finish_section(self, job_id, number, audio, details=None, seed=None, spoken=None):
        details = details or {}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            section = db.execute("SELECT text, instructions, turbo_settings, voice_match FROM sections WHERE job_id = ? AND number = ?",
                                 (job_id, number)).fetchone()
            if section is None:
                raise StudioError("Section not found.", 404)
            rendered_at = now()
            db.execute("""
                INSERT INTO section_takes
                    (id, job_id, section_number, audio, text, instructions, seed, engine, device, runtime, model_sha256, created_at,
                     turbo_settings, voice_match, spoken)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_id(), job_id, number, audio, section["text"], section["instructions"], seed,
                  details.get("engine"), details.get("device"), details.get("runtime"), details.get("model_sha256"), rendered_at,
                  section["turbo_settings"], section["voice_match"], spoken))
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
                    rendered_model_sha256 = ?, rendered_seed = ?, rendered_at = ?, turbo_settings = ?,
                    voice_match = COALESCE(?, 'standard')
                WHERE job_id = ? AND number = ?
            """, (take["text"], take["instructions"], take["seed"], take["audio"], take["engine"],
                  take["device"], take["runtime"], take["model_sha256"], take["seed"], take["created_at"],
                  take["turbo_settings"], take["voice_match"], job_id, number))
            db.execute("UPDATE jobs SET status = 'queued', error = NULL WHERE id = ?", (job_id,))

    def finish_job(self, job_id, export, loudness=None):
        with self.connect() as db:
            db.execute("""
                UPDATE jobs SET status = 'ready', export = ?, error = NULL, export_loudness = ?, check_requested = 0
                WHERE id = ?
            """, (export, json.dumps(loudness) if loudness else None, job_id))

    def record_check(self, job_id, number, audio, result):
        with self.connect() as db:
            db.execute("UPDATE section_takes SET check_result = ? WHERE job_id = ? AND section_number = ? AND audio = ?",
                       (json.dumps(result), job_id, number, audio))

    def select_take(self, job_id, number, audio):
        """Make an already generated take current, for example the best of several attempts."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            take = db.execute("SELECT * FROM section_takes WHERE job_id = ? AND section_number = ? AND audio = ?",
                              (job_id, number, audio)).fetchone()
            if take is None:
                raise StudioError("Take not found.", 404)
            db.execute("""
                UPDATE sections SET audio = ?, pending = 0, rendered_engine = ?, rendered_device = ?, rendered_runtime = ?,
                    rendered_model_sha256 = ?, rendered_seed = ?, rendered_at = ?
                WHERE job_id = ? AND number = ?
            """, (take["audio"], take["engine"], take["device"], take["runtime"], take["model_sha256"], take["seed"],
                  take["created_at"], job_id, number))

    def request_check(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get_job(db, job_id)["status"] != "ready":
                raise StudioError("Wait for the recording to finish before checking it.", 409)
            db.execute("UPDATE jobs SET check_requested = 1, status = 'queued', error = NULL WHERE id = ?", (job_id,))

    def set_loudness(self, job_id, target):
        if target is not None and target not in LOUDNESS_TARGETS:
            raise StudioError("Choose a supported loudness target.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self.get_job(db, job_id)
            if job["status"] != "ready":
                raise StudioError("Wait for the recording to finish before changing its loudness.", 409)
            if job["loudness_target"] == target:
                return
            db.execute("UPDATE jobs SET loudness_target = ?, status = 'queued', error = NULL WHERE id = ?", (target, job_id))

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

    def regenerate(self, job_id, number, text, instructions=None, directed_available=False, turbo_settings=None,
                   voice_match=None, trained_voice=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self.get_job(db, job_id)
            if job["status"] != "ready":
                raise StudioError("Wait for the recording to finish before regenerating.", 409)
            section = db.execute("SELECT instructions, turbo_settings, voice_match FROM sections WHERE job_id = ? AND number = ?",
                                 (job_id, number)).fetchone()
            if section is None:
                raise StudioError("Section not found.", 404)
            instructions = section["instructions"] if instructions is None else instructions
            voice_match = section["voice_match"] if voice_match is None else voice_match
            validate_directions(text, instructions, directed_available)
            voice = db.execute("SELECT transcript FROM voices WHERE id = ?", (job["voice_id"],)).fetchone()
            # A recording keeps the trained voice it was made with; the current one fills in when it has none.
            trained = job["trained_voice"] or trained_voice
            validate_voice_match(text, instructions, voice_match, directed_available, voice["transcript"] if voice else "",
                                 trained)
            if voice_match in TRAINED_MATCHES and not job["trained_voice"]:
                db.execute("UPDATE jobs SET trained_voice = ? WHERE id = ?", (trained, job_id))
            if turbo_settings is None and section["turbo_settings"]:
                turbo_settings = json.loads(section["turbo_settings"])
                turbo_settings["seed"] = 0
            seed = turbo_settings["seed"] if turbo_settings and not instructions and voice_match == "standard" else 0
            seed = seed if seed else secrets.randbelow(2147483647) + 1
            settings_json = json.dumps(turbo_settings, sort_keys=True) if turbo_settings is not None else None
            db.execute("""
                UPDATE sections SET text = ?, instructions = ?, seed = ?, turbo_settings = ?, voice_match = ?, pending = 1
                WHERE job_id = ? AND number = ?
            """, (text, instructions, seed, settings_json, voice_match, job_id, number))
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

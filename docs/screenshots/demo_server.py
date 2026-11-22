"""Demo server for the documentation screenshots.

It renders the studio's real templates and browser scripts, but answers API calls with invented data
(example names, example.com addresses, made-up scripts and a synthetic tone), so no real voice, account
or recording appears in the docs. Needs only jinja2. Run it, then run capture.py:

    python docs/screenshots/demo_server.py      # serves http://127.0.0.1:8765
    python docs/screenshots/capture.py          # writes the PNGs next to this file
"""
import io, json, math, re, struct, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[2] / "studio"
env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=True)

def tone(seconds=3.0, rate=24000, freq=180.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        frames = bytearray()
        for i in range(int(seconds * rate)):
            env_ = min(1, i / 2000, (seconds * rate - i) / 2000)
            v = int(6000 * env_ * math.sin(2 * math.pi * freq * i / rate) * (0.6 + 0.4 * math.sin(i / 900)))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))
    return buf.getvalue()

WAV = tone()
T0 = "2026-11-22T15:04:00+00:00"
ADMIN = {"email": "admin@example.com", "name": "Studio admin", "role": "admin", "local": False}
CUSTOMER = {"email": "taylor@example.com", "name": "Taylor Reed", "role": "customer", "local": False}

SCRIPT = ("Welcome to the Brightwave spring update. I'm glad you're here.\n\n"
          "On Mar. 3 at 10:30 a.m., Dr. Patel will open our new studio at 410 Main St. The first 250 guests get a free gift.\n\n"
          "The Pro plan now costs $129.99, about 40% less than last year. Questions? Call (555) 123-4567.\n\n"
          "Short and sweet. That's the goal.")
SECTIONS = [p for p in SCRIPT.split("\n\n")]
SPOKEN = [
    "Welcome to the braitwave spring update. I'm glad you're here.",
    "On March third at ten thirty A M, Doctor Patel will open our new studio at four hundred ten Main Street. The first two hundred fifty guests get a free gift.",
    "The Pro plan now costs one hundred twenty-nine dollars and ninety-nine cents, about forty percent less than last year. Questions? Call five five five, one two three, four five six seven.",
    "Short and sweet. That's the goal.",
]
CHECKS = [
    {"status": "pass", "word_error": 0.0, "similarity": 0.95},
    {"status": "pass", "word_error": 0.03, "similarity": 0.93},
    {"status": "review", "word_error": 0.12, "similarity": 0.91, "reasons": ["The opening words differ from the script."],
     "heard": "The Pro plan costs one hundred twenty nine dollars"},
    {"status": "pass", "word_error": 0.0, "similarity": 0.94},
]

def section(n, text, admin):
    gen = {"engine": "VoxCPM2 · trained voice alex-step150 + reference", "device": "cuda", "runtime": "2.0.3",
           "model_sha256": "f7f964cfa9da23653baec6e6f7750719977ad944ed9f95fe52fe3a620506891d", "seed": 35, "at": T0}
    take = {"id": f"take{n}", "text": text, "instructions": "", "created_at": T0, "spoken": SPOKEN[n - 1],
            "check": CHECKS[n - 1], "current": True, "can_restore": True}
    if admin:
        take.update({"engine": gen["engine"], "device": "cuda", "voice_match": "trained_reference", "seed": 35, "turbo_settings": None})
    s = {"number": n, "text": text, "instructions": "", "pending": False, "audio": f"s{n}.wav", "pause_ms": 750 if n < 4 else 0,
         "spoken": SPOKEN[n - 1], "check": CHECKS[n - 1], "takes": [take],
         "generation": gen if admin else {"at": T0}}
    if admin:
        s.update({"voice_match": "trained_reference", "turbo_settings": None})
    return s

def job(job_id, admin, title="Brightwave spring update", owner=None):
    d = {"id": job_id, "title": title, "script": SCRIPT, "instructions": "", "created_at": T0, "status": "ready", "error": None,
         "export": "export1", "voice_name": "Alex · warm narrator", "voice_available": True, "master_rate": 48000,
         "loudness_target": -16.0, "export_loudness": {"integrated": -16.0, "true_peak": -1.4}, "auto_retries": 1,
         "checks_available": True, "sections": [section(i + 1, t, admin) for i, t in enumerate(SECTIONS)]}
    if admin:
        d.update({"engine": "VoxCPM2 2.0.3 · 7936080", "voice_reference": "alex-reference.wav", "voice_transcript_ready": True,
                  "trained_voice": "alex-step150", "source": "studio", "approved": False, "delivered": None,
                  "owner": owner, "read_only": owner is not None})
    return d

JOBS_ADMIN = [
    {"id": "a" * 32, "title": "Brightwave spring update", "status": "ready", "created_at": T0, "source": "studio", "awaiting_approval": 0},
    {"id": "b" * 32, "title": "Podcast intro, episode 12", "status": "ready", "created_at": T0, "source": "inbox", "awaiting_approval": 1},
    {"id": "c" * 32, "title": "Support line greeting", "status": "running", "created_at": T0, "source": "studio", "awaiting_approval": 0},
]
JOBS_CUSTOMER = [
    {"id": "d" * 32, "title": "Brightwave spring update", "status": "ready", "created_at": T0, "source": "studio", "awaiting_approval": 0},
    {"id": "e" * 32, "title": "Holiday hours announcement", "status": "ready", "created_at": T0, "source": "studio", "awaiting_approval": 0},
]
WORDS = [{"id": "1" * 32, "written": "Brightwave", "spoken": "braitwave"}, {"id": "2" * 32, "written": "Patel", "spoken": "puhtel"}]
CATALOG = [
    {"id": "p1" + "0" * 30, "name": "Alex · warm narrator", "description": "Warm, friendly and clear. Great for explainers and adverts.", "sample": "/api/voices/p1/sample"},
    {"id": "p2" + "0" * 30, "name": "Sam · upbeat promo", "description": "Bright and energetic, for promotions and social video.", "sample": "/api/voices/p2/sample"},
]
PROFILES = [
    {"id": CATALOG[0]["id"], "name": CATALOG[0]["name"], "description": CATALOG[0]["description"], "voice_id": "r1", "voice_label": "alex-reference.wav",
     "trained_voice": "alex-step150", "published": 1, "speaker": "Alex Morgan", "consent_type": "written", "consent_date": "2026-11-10",
     "consent_notes": "", "consent_recorded_at": T0, "consent_revoked_at": None, "sample_status": "ready", "recordings": 14, "created_at": T0,
     "has_consent_file": True, "has_transcript": True, "engine": "trained_reference", "problem": None, "sample": "/api/voices/p1/sample", "trained_missing": False},
    {"id": CATALOG[1]["id"], "name": CATALOG[1]["name"], "description": CATALOG[1]["description"], "voice_id": "r2", "voice_label": "sam-reference.wav",
     "trained_voice": None, "published": 1, "speaker": "", "consent_type": "self", "consent_date": None,
     "consent_notes": "", "consent_recorded_at": T0, "consent_revoked_at": None, "sample_status": "ready", "recordings": 3, "created_at": T0,
     "has_consent_file": False, "has_transcript": True, "engine": "maximum", "problem": None, "sample": "/api/voices/p2/sample", "trained_missing": False},
    {"id": "p3" + "0" * 30, "name": "Riley · calm guide", "description": "Slow and reassuring, for tutorials.", "voice_id": "r3", "voice_label": "riley-reference.wav",
     "trained_voice": "riley-step200", "published": 0, "speaker": "Riley Chen", "consent_type": "written", "consent_date": "2026-11-21",
     "consent_notes": "", "consent_recorded_at": T0, "consent_revoked_at": None, "sample_status": None, "recordings": 0, "created_at": T0,
     "has_consent_file": False, "has_transcript": False, "engine": "trained_reference", "problem": "Attach the speaker's signed consent before publishing.",
     "sample": None, "trained_missing": False},
]
ACCOUNTS = [
    {"id": "u1" + "0" * 30, "email": "taylor@example.com", "role": "customer", "name": "Taylor Reed", "created_at": T0, "last_login_at": T0, "disabled": 0, "recordings": 2, "generations": 5},
    {"id": "u2" + "0" * 30, "email": "jordan@example.com", "role": "customer", "name": "Jordan Lee", "created_at": T0, "last_login_at": T0, "disabled": 0, "recordings": 4, "generations": 9},
    {"id": "u3" + "0" * 30, "email": "admin@example.com", "role": "admin", "name": "Studio admin", "created_at": T0, "last_login_at": T0, "disabled": 0, "recordings": 0, "generations": 0},
]
REVIEW = [
    {"id": "d" * 32, "title": "Brightwave spring update", "status": "ready", "created_at": T0, "script": SCRIPT.split("\n\n")[0] + "\n\n" + SCRIPT.split("\n\n")[1], "email": "taylor@example.com", "user_id": ACCOUNTS[0]["id"], "disabled": 0},
    {"id": "f" * 32, "title": "Café menu radio spot", "status": "ready", "created_at": T0, "script": "Fresh pastries every morning at Corner Café. Open seven days a week, from 7 a.m. to 3 p.m.", "email": "jordan@example.com", "user_id": ACCOUNTS[1]["id"], "disabled": 0},
]
PROMPTS = [{"id": f"b{i:015x}", "text": t, "source": "builtin", "read_as": r, "take": k} for i, (t, r, k) in enumerate([
    ("Thanks for calling. Our team will get back to you within one business day.", None,
     {"id": "t" * 32, "duration": 4.6, "kept": True, "check": {"status": "pass", "peak_db": -4.8, "noise_db": -62}}),
    ("The meeting moved to 3:15 p.m. on Thursday, so bring the updated slides.", "The meeting moved to three fifteen P M on Thursday, so bring the updated slides.", None),
])]
PROMPTS += [{"id": f"c{i:015x}", "text": f"Prompt {i}", "source": "builtin", "read_as": None, "take": {"id": "t" * 32, "duration": 6.0, "kept": True, "check": {"status": "pass"}}} for i in range(2, 146)]

def paragraphs():
    return [
        [{"text": "Welcome to the"}, {"text": "Brightwave", "spoken": "braitwave", "kind": "pronunciation"}, {"text": "spring update. I'm glad you're here."}],
        [{"text": "On"}, {"text": "Mar. 3", "spoken": "March third", "kind": "auto"}, {"text": "at"}, {"text": "10:30 a.m.,", "spoken": "ten thirty A M,", "kind": "auto"},
         {"text": "Dr.", "spoken": "Doctor", "kind": "auto"}, {"text": "Patel", "spoken": "puhtel", "kind": "pronunciation"}, {"text": "will open our new studio at"},
         {"text": "410 Main St.", "spoken": "four hundred ten Main Street.", "kind": "auto"}, {"text": "The first"}, {"text": "250", "spoken": "two hundred fifty", "kind": "auto"}, {"text": "guests get a free gift."}],
        [{"text": "The Pro plan now costs"}, {"text": "$129.99,", "spoken": "one hundred twenty-nine dollars and ninety-nine cents,", "kind": "auto"},
         {"text": "about"}, {"text": "40%", "spoken": "forty percent", "kind": "auto"}, {"text": "less than last year. Questions? Call"},
         {"text": "(555) 123-4567.", "spoken": "five five five, one two three, four five six seven.", "kind": "auto"}],
        [{"text": "Short and sweet. That's the goal."}],
    ]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def role(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["role"].value if "role" in cookie else "admin"

    def send(self, body, type_="application/json", status=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def page(self, admin):
        ctx = dict(script_limit=12000, chunk_limit=220, max_pause=5000, device="cuda", directed_device="cuda", directed_available=True,
                   instructions_limit=240, transcript_limit=1000, checks_available=True, admin=admin, auth_enabled=True,
                   account=ADMIN if admin else CUSTOMER, house_match="trained_reference", chatterbox_available=True,
                   trained_voice={"name": "alex-step150", "step": 150} if admin else {"name": "house", "step": None},
                   inbox="data\\studio\\inbox" if admin else "", outbox="data\\studio\\outbox" if admin else "")
        return env.get_template("index.html").render(**ctx)

    def do_GET(self):
        path = self.path.split("?")[0]
        admin = self.role() == "admin"
        if path == "/":
            return self.send(self.page(admin), "text/html; charset=utf-8")
        if path in ("/login", "/signup"):
            return self.send(env.get_template("auth.html").render(mode=path[1:]), "text/html; charset=utf-8")
        if path.startswith("/assets/"):
            name = path.split("/")[-1]
            return self.send((ROOT / "static" / name).read_bytes(), "text/css" if name.endswith(".css") else "text/javascript")
        if path == "/api/state":
            if admin:
                return self.send({"voice": {"id": "r1", "label": "alex-reference.wav", "duration": 18.4, "transcript": "Hi, this is my normal speaking voice."},
                                  "voices": [{"id": "r1", "label": "alex-reference.wav", "duration": 18.4, "transcript": "x"},
                                             {"id": "r2", "label": "sam-reference.wav", "duration": 12.0, "transcript": "y"}],
                                  "jobs": JOBS_ADMIN, "pronunciations": WORDS, "reference_notice": None,
                                  "trained_voice": {"name": "alex-step150", "step": 150}, "account": ADMIN})
            return self.send({"jobs": JOBS_CUSTOMER, "pronunciations": WORDS, "voice": {"id": "catalog", "label": CATALOG[0]["name"], "duration": 0, "transcript": "ready"},
                              "voices": [], "catalog": CATALOG, "reference_notice": None, "trained_voice": None, "account": CUSTOMER})
        m = re.match(r"^/api/jobs/([0-9a-f]{32})$", path)
        if m:
            jid = m.group(1)
            owner = {"id": ACCOUNTS[0]["id"], "email": "taylor@example.com", "name": "Taylor Reed", "disabled": False} if jid == "d" * 32 and admin else None
            title = next((j["title"] for j in JOBS_ADMIN + JOBS_CUSTOMER if j["id"] == jid), "Brightwave spring update")
            return self.send(job(jid, admin, title, owner))
        if re.match(r"^/api/(jobs/.+/(sections/\d+(/takes/.+)?/audio|export/(wav|mp3))|voices/.+/sample|voice/.+/audio|dataset/takes/.+/audio)$", path):
            return self.send(WAV, "audio/wav")
        if path == "/api/voices":
            if admin:
                return self.send({"voices": PROFILES, "references": [{"id": "r1", "label": "alex-reference.wav", "has_transcript": True},
                                  {"id": "r2", "label": "sam-reference.wav", "has_transcript": True}, {"id": "r3", "label": "riley-reference.wav", "has_transcript": False}],
                                  "trained": [{"name": "alex-step150", "step": 150}, {"name": "riley-step200", "step": 200}], "directed_available": True})
            return self.send({"voices": CATALOG})
        if path == "/api/admin/accounts":
            return self.send({"accounts": ACCOUNTS})
        if path == "/api/admin/recordings":
            return self.send({"recordings": REVIEW})
        if path.startswith("/api/admin/accounts/") and path.endswith("/usage"):
            return self.send({"usage": []})
        if path == "/api/dataset":
            return self.send({"prompts": PROMPTS, "kept_minutes": 19.6, "recorded": 145, "goals": [10, 30, 60], "checks_available": True})
        if path == "/api/comparisons":
            return self.send({"comparisons": [
                {"id": "9" * 32, "status": "ready", "error": None, "created_at": T0, "text": "Thanks for calling. Our team will get back to you within one business day.",
                 "choice": None, "ready": True},
                {"id": "8" * 32, "status": "ready", "error": None, "created_at": T0, "text": "Fresh pastries every morning, seven days a week.",
                 "choice": "A", "ready": True, "engines": {"A": "trained_reference", "B": "maximum"}, "trained_voice": "alex-step150"}],
                "trained_voice": {"name": "alex-step150", "step": 150}})
        return self.send({"detail": "Not found."}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        path = self.path.split("?")[0]
        if path == "/api/spoken-preview":
            return self.send({"spoken": " ".join(SPOKEN), "paragraphs": paragraphs()})
        if path == "/api/sections/preview":
            return self.send({"sections": SECTIONS, "pauses": [750, 750, 750, 0]})
        return self.send({})

    do_PUT = do_POST
    do_DELETE = do_POST

ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()

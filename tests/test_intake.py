"""Hands-off intake: the inbox watcher, approval and outbox delivery. Synthetic scripts only."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from studio.inbox import Inbox, ignored, title_for, unique
from studio.store import Store, StudioError


def fake_extract(filename, contents):
    if not filename.lower().endswith(".txt"):
        raise AssertionError("only .txt is used in these tests")
    text = contents.decode("utf-8").strip()
    if not text:
        raise StudioError("The file does not contain any script text.")
    return {"text": text, "warnings": []}


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "inbox"
        self.submitted = []
        self.inbox = Inbox(self.folder, self.submit)
        self.inbox.prepare()
        patcher = patch("studio.script_import.extract_script", side_effect=fake_extract)
        # The real reader needs python-docx and pypdf; inbox imports it lazily, so patch the module.
        try:
            patcher.start()
            self.addCleanup(patcher.stop)
        except ModuleNotFoundError:
            import sys
            import types
            module = types.ModuleType("studio.script_import")
            module.MAX_SCRIPT_FILE_BYTES = 10 * 1024 * 1024
            module.extract_script = fake_extract
            sys.modules["studio.script_import"] = module
            self.addCleanup(sys.modules.pop, "studio.script_import")

    def submit(self, title, script):
        if script == "fail":
            raise StudioError("Remove bracketed sound cues.")
        self.submitted.append((title, script))
        return f"job{len(self.submitted)}"

    def test_waits_for_a_stable_file_then_records_and_moves_it(self):
        (self.folder / "Spring_promo.txt").write_text("Hello there.", encoding="utf-8")
        self.assertEqual(self.inbox.scan(), [])  # Seen once: it might still be copying.
        self.assertEqual(self.inbox.scan(), ["job1"])
        self.assertEqual(self.submitted, [("Spring promo", "Hello there.")])
        self.assertFalse((self.folder / "Spring_promo.txt").exists())
        [done] = list((self.folder / "done").iterdir())
        self.assertTrue(done.name.endswith(" Spring_promo.txt"))
        self.assertEqual(self.inbox.scan(), [])

    def test_failures_move_with_a_note(self):
        (self.folder / "cues.txt").write_text("fail", encoding="utf-8")
        (self.folder / "empty.txt").write_text("   ", encoding="utf-8")
        (self.folder / "song.mp3").write_bytes(b"ID3")
        self.inbox.scan()
        self.inbox.scan()
        self.assertEqual(self.submitted, [])
        notes = {path.name.split(" ", 1)[1]: path.read_text(encoding="utf-8")
                 for path in (self.folder / "failed").glob("*.why.txt")}
        self.assertIn("sound cues", notes["cues.txt.why.txt"])
        self.assertIn("script text", notes["empty.txt.why.txt"])
        self.assertIn(".txt, .docx and .pdf", notes["song.mp3.why.txt"])
        self.assertEqual(len([path for path in self.folder.iterdir() if path.is_file()]), 0)

    def test_ignores_temporary_and_lock_files(self):
        for name in ("~$draft.docx", ".hidden.txt", "upload.crdownload", "desktop.ini"):
            (self.folder / name).write_text("x", encoding="utf-8")
            self.assertTrue(ignored(self.folder / name))
        self.inbox.scan()
        self.inbox.scan()
        self.assertEqual(self.submitted, [])
        self.assertEqual(list((self.folder / "failed").iterdir()), [])

    def test_helpers(self):
        self.assertEqual(title_for(Path("__.txt")), "Inbox recording")
        (self.folder / "a.wav").write_bytes(b"1")
        self.assertEqual(unique(self.folder, "a.wav").name, "a (2).wav")
        self.assertEqual(unique(self.folder, "b.wav").name, "b.wav")


class ApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.initialize()
        self.store.add_voice("voice", "Test", 7.0)

    def test_inbox_recordings_await_approval_of_each_export(self):
        job_id = self.store.create_job("Promo", "Hello there.", source="inbox")
        studio_job = self.store.create_job("Mine", "Hello there.")
        with self.assertRaises(StudioError) as early:
            self.store.approve(job_id, "export1", [])
        self.assertEqual(early.exception.status, 409)
        self.store.finish_section(job_id, 1, "one.wav", {}, 35)
        self.store.finish_job(job_id, "export1")
        self.store.finish_section(studio_job, 1, "one.wav", {}, 35)
        self.store.finish_job(studio_job, "export1")
        awaiting = {job["id"]: job["awaiting_approval"] for job in self.store.state()["jobs"]}
        self.assertEqual(awaiting, {job_id: 1, studio_job: 0})
        with self.assertRaises(StudioError):
            self.store.approve(job_id, "stale-export", ["Promo.wav"])
        self.store.approve(job_id, "export1", ["Promo.wav", "Promo.mp3"])
        self.assertEqual(self.store.state()["jobs"][1]["awaiting_approval"], 0)
        # A regenerated section makes a new export, which needs approval again.
        self.store.regenerate(job_id, 1, "Hello again.")
        self.store.finish_section(job_id, 1, "two.wav", {}, 1)
        self.store.finish_job(job_id, "export2")
        self.assertEqual(self.store.state()["jobs"][1]["awaiting_approval"], 1)


class IntakeApiTests(unittest.TestCase):
    """Needs the studio environment (FastAPI, ffmpeg)."""

    def test_api_intake_approve_and_deliver(self):
        import os

        from fastapi.testclient import TestClient

        from studio.app import create_app
        try:
            from test_takes import FakeEngine, wait_for, write_wav
        except ImportError:
            from tests.test_takes import FakeEngine, wait_for, write_wav

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            write_wav(root / "reference.wav", seconds=7)
            headers = {"X-Studio-Request": "1"}
            with patch.dict(os.environ, {"VOICEOVER_INBOX": "off"}), \
                 TestClient(create_app(root, FakeEngine(), auth=False), base_url="http://localhost") as client:
                response = client.post("/api/intake", headers=headers,
                                       files={"file": ("Spring promo.txt", b"Hello from another app.", "text/plain")})
                self.assertEqual(response.status_code, 202)
                route = f"/api/jobs/{response.json()['id']}"
                ready = wait_for(client, route, "ready")
                self.assertEqual((ready["source"], ready["approved"]), ("api", False))
                self.assertEqual(client.get("/api/state").json()["jobs"][0]["awaiting_approval"], 1)
                approved = client.post(route + "/approve", headers=headers)
                self.assertEqual(approved.status_code, 200)
                names = approved.json()["delivered"]
                self.assertEqual([Path(name).suffix for name in names], [".wav", ".mp3"])
                outbox = root / "studio" / "outbox"
                self.assertTrue(all((outbox / name).is_file() for name in names))
                self.assertTrue(client.get(route).json()["approved"])
                self.assertEqual(client.get("/api/state").json()["jobs"][0]["awaiting_approval"], 0)
                self.assertEqual(client.post("/api/intake", files={"file": ("a.txt", b"x")}).status_code, 403)


if __name__ == "__main__":
    unittest.main()

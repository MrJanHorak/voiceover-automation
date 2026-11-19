import math
import struct
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.store import Store, StudioError


def write_wav(path, frequency=220, seconds=1):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        frames = (struct.pack("<h", int(5000 * math.sin(2 * math.pi * frequency * i / 24000)))
                  for i in range(24000 * seconds))
        audio.writeframes(b"".join(frames))


class FakeEngine:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def render(self, text, reference, seed, output):
        self.calls += 1
        if self.fail:
            self.fail = False
            output.write_bytes(b"partial")
            raise RuntimeError("simulated failure")
        write_wav(output, 220 + self.calls * 60)


def wait_for(client, url, status):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        data = client.get(url).json()
        if data["status"] == status:
            return data
        sleep(0.1)
    raise AssertionError(data)


class TakeTests(unittest.TestCase):
    def test_regenerate_compare_restore_and_restart(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            write_wav(root / "reference.wav", seconds=7)
            engine = FakeEngine()
            headers = {"X-Studio-Request": "1"}
            with TestClient(create_app(root, engine), base_url="http://localhost") as client:
                response = client.post("/api/jobs", headers=headers, json={"script": "First version."})
                self.assertEqual(response.status_code, 202)
                route = f"/api/jobs/{response.json()['id']}"
                first = wait_for(client, route, "ready")
                first_take = first["sections"][0]["takes"][0]
                original_wav = client.get(route + "/export/wav").content
                response = client.post(route + "/sections/1", headers=headers, json={"text": "Second version."})
                self.assertEqual(response.status_code, 202)
                second = wait_for(client, route, "ready")
                takes = second["sections"][0]["takes"]
                self.assertEqual([take["text"] for take in takes], ["Second version.", "First version."])
                self.assertTrue(takes[0]["current"])
                self.assertNotEqual(client.get(route + "/export/wav").content, original_wav)
                self.assertEqual(client.get(route + f"/sections/1/takes/{first_take['id']}/audio").status_code, 200)
                self.assertEqual(client.get(route + f"/sections/2/takes/{first_take['id']}/audio").status_code, 404)

                engine.fail = True
                client.post(route + "/sections/1", headers=headers, json={"text": "Failed version."})
                failed = wait_for(client, route, "failed")
                self.assertEqual(failed["export"], second["export"])
                self.assertEqual(len(failed["sections"][0]["takes"]), 2)
                restore = client.post(route + f"/sections/1/takes/{first_take['id']}/restore", headers=headers)
                self.assertEqual(restore.status_code, 202)
                restored = wait_for(client, route, "ready")
                self.assertEqual(restored["sections"][0]["text"], "First version.")
                self.assertEqual(client.get(route + "/export/wav").content, original_wav)
                self.assertEqual(engine.calls, 3)
            with TestClient(create_app(root, FakeEngine()), base_url="http://localhost") as client:
                restarted = client.get(route).json()
                self.assertEqual(len(restarted["sections"][0]["takes"]), 2)
                self.assertEqual(restarted["sections"][0]["text"], "First version.")

    def test_older_pending_audio_does_not_invent_words(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            store = Store(root)
            store.initialize()
            store.add_voice("voice", "Reference", 10)
            job = store.create_job("Old", "Original words.")
            with store.connect() as db:
                db.execute("UPDATE sections SET audio = 'old.wav', pending = 1, text = 'Unsuccessful edit.' WHERE job_id = ?", (job,))
            store.initialize()
            section = store.job(job)["sections"][0]
            self.assertIsNone(section["takes"][0]["text"])
            self.assertEqual(section["audio"], "old.wav")

    def test_missing_take_audio_preserves_current_recording(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            store.add_voice("voice", "Reference", 10)
            job = store.create_job("Test", "First.")
            store.finish_section(job, 1, "missing.wav", {"engine": "Test"}, 1)
            store.finish_job(job, "previous-export")
            take = store.job(job)["sections"][0]["takes"][0]
            with self.assertRaises(StudioError) as caught:
                store.restore_take(job, 1, take["id"])
            self.assertEqual(caught.exception.status, 404)
            unchanged = store.job(job)
            self.assertEqual(unchanged["status"], "ready")
            self.assertEqual(unchanged["export"], "previous-export")


if __name__ == "__main__":
    unittest.main()

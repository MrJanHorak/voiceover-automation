"""Voice catalog: profiles, consent, publishing, revocation, samples and trained-voice uploads. Synthetic data only."""

import io
import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from studio.catalog import check_safetensors, consent_extension, install_trained
from studio.store import Store, StudioError

PASSWORD = "correct horse battery"


def safetensors_bytes(key="base_lm.layers.0.self_attn.q_proj.lora_A"):
    header = json.dumps({key: {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    return struct.pack("<Q", len(header)) + header + b"\x00" * 4


CONFIG = json.dumps({"lora_config": {"enable_lm": True, "enable_dit": True, "r": 32, "alpha": 32}}).encode()


class CatalogStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.initialize()
        self.store.add_voice("voice", "Reference", 7.0)

    def make(self, **values):
        return self.store.save_profile({"name": "Jan · warm", "voice_id": "voice", **values})

    def test_existing_voice_becomes_the_first_profile_once(self):
        # A new studio has no voice at first start, so nothing is seeded, then or later.
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            store.add_voice("voice", "Owner", 7.0)
            store.initialize()
            self.assertEqual(store.profiles(), [])
            # An upgraded studio (a voice, but no seeding marker yet) gets one published "Studio voice".
            with store.connect() as db:
                db.execute("DELETE FROM meta WHERE key = 'profiles_seeded'")
            store.initialize()
            [profile] = store.profiles()
            self.assertEqual((profile["name"], profile["published"], profile["consent_type"]), ("Studio voice", 1, "self"))
            self.assertIsNone(store.consent_problem(profile))
            store.initialize()
            self.assertEqual(len(store.profiles()), 1)

    def test_publishing_needs_consent(self):
        profile = self.make()
        with self.assertRaises(StudioError) as missing:
            self.store.publish_profile(profile["id"], True)
        self.assertEqual(missing.exception.status, 409)
        self.store.save_profile({"consent_type": "written", "speaker": "Jan Horak"}, profile["id"])
        with self.assertRaises(StudioError):  # Written consent needs the signed document.
            self.store.publish_profile(profile["id"], True)
        self.store.set_consent_file(profile["id"], "signed.pdf")
        self.assertEqual(self.store.publish_profile(profile["id"], True)["published"], 1)
        self.assertEqual([item["id"] for item in self.store.profiles(published_only=True)], [profile["id"]])
        with self.assertRaises(StudioError):  # A published voice cannot lose its consent record.
            self.store.save_profile({"speaker": ""}, profile["id"])

    def test_revoking_consent_is_final_and_stops_queued_work(self):
        profile = self.store.publish_profile(self.make(consent_type="self")["id"], True)
        job_id = self.store.create_job("T", "Hello.", voice_id="voice", voice_profile_id=profile["id"])
        revoked = self.store.revoke_consent(profile["id"])
        self.assertEqual((revoked["published"], bool(revoked["consent_revoked_at"])), (0, True))
        self.assertEqual(self.store.job(job_id)["status"], "failed")
        self.assertEqual(self.store.profiles(published_only=True), [])
        with self.assertRaises(StudioError):
            self.store.publish_profile(profile["id"], True)
        with self.assertRaises(StudioError):
            self.store.save_profile({"consent_type": "self"}, profile["id"])
        with self.assertRaises(StudioError) as in_use:
            self.store.delete_profile(profile["id"])
        self.assertEqual(in_use.exception.status, 409)

    def test_jobs_use_the_profile_reference_and_samples_stay_hidden(self):
        self.store.add_voice("other", "Other reference", 8.0)  # Now the active voice.
        profile = self.make(consent_type="self")
        job_id = self.store.create_job("T", "Hello.", voice_id="voice", voice_profile_id=profile["id"])
        sample = self.store.create_job("Sample", "Hello.", voice_id="voice", voice_profile_id=profile["id"], blind=2)
        self.store.set_sample(profile["id"], sample)
        job = self.store.job(job_id)
        self.assertEqual((job["voice_id"], job["voice_profile_id"]), ("voice", profile["id"]))
        self.assertEqual([item["id"] for item in self.store.state()["jobs"]], [job_id])
        self.assertEqual(self.store.comparisons(), [])
        self.assertEqual(self.store.profile(profile["id"])["recordings"], 1)
        with self.assertRaises(StudioError):
            self.store.create_job("T", "Hello.", voice_id="missing")

    def test_validation(self):
        with self.assertRaises(StudioError):
            self.store.save_profile({"name": "  ", "voice_id": "voice"})
        with self.assertRaises(StudioError):
            self.store.save_profile({"name": "X", "voice_id": "missing"})
        unused = self.make()
        self.store.delete_profile(unused["id"])
        self.assertIsNone(self.store.profile(unused["id"]))


class CatalogFileTests(unittest.TestCase):
    def test_consent_documents_are_checked_by_content(self):
        self.assertEqual(consent_extension(b"%PDF-1.7 ..."), ".pdf")
        self.assertEqual(consent_extension(b"\x89PNG\r\n\x1a\n...."), ".png")
        self.assertEqual(consent_extension(b"\xff\xd8\xff\xe0...."), ".jpg")
        with self.assertRaises(StudioError):
            consent_extension(b"<html>not a document</html>")

    def test_trained_voice_upload(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary) / "trained"
            target = install_trained(root, "jan-step150", io.BytesIO(CONFIG), io.BytesIO(safetensors_bytes()))
            self.assertEqual(sorted(path.name for path in target.iterdir()), ["lora_config.json", "lora_weights.safetensors"])
            with self.assertRaises(StudioError) as exists:
                install_trained(root, "jan-step150", io.BytesIO(CONFIG), io.BytesIO(safetensors_bytes()))
            self.assertEqual(exists.exception.status, 409)
            for name, config, weights in (("../escape", CONFIG, safetensors_bytes()),
                                          ("bad-config", b"{}", safetensors_bytes()),
                                          ("bad-weights", CONFIG, b"not safetensors"),
                                          ("not-lora", CONFIG, safetensors_bytes("model.layers.0.weight"))):
                with self.assertRaises(StudioError, msg=name):
                    install_trained(root, name, io.BytesIO(config), io.BytesIO(weights))
            # Failed uploads leave nothing behind.
            self.assertEqual([path.name for path in root.iterdir()], ["jan-step150"])
            with self.assertRaises(StudioError):
                check_safetensors(root / "jan-step150" / "lora_config.json")


class CatalogApiTests(unittest.TestCase):
    """Needs the studio environment (FastAPI, ffmpeg)."""

    def test_admin_publishes_and_customer_records_with_the_voice(self):
        from fastapi.testclient import TestClient

        from studio.app import create_app
        try:
            from test_takes import FakeEngine, wait_for, write_wav
        except ImportError:
            from tests.test_takes import FakeEngine, wait_for, write_wav

        headers = {"X-Studio-Request": "1"}
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            app = create_app(root, FakeEngine(), auth=True)
            customer = TestClient(app, base_url="http://localhost")
            with TestClient(app, base_url="http://localhost") as admin:
                admin.post("/api/auth/setup", headers=headers, json={
                    "email": "admin@example.com", "password": PASSWORD, "code": app.state.setup_code})
                customer.post("/api/auth/signup", headers=headers,
                              json={"email": "c@example.com", "password": PASSWORD, "terms": True})
                write_wav(root / "ref.wav", seconds=7)
                with (root / "ref.wav").open("rb") as audio:
                    voice_id = admin.post("/api/voice", headers=headers, files={"audio": ("ref.wav", audio, "audio/wav")},
                                          data={"name": "Speaker"}).json()["id"]
                self.assertEqual(customer.post("/api/jobs", headers=headers, json={"script": "Hello."}).status_code, 409)
                self.assertEqual(customer.get("/api/voices").json(), {"voices": []})
                self.assertEqual(customer.post("/api/admin/voices", headers=headers,
                                               json={"name": "X", "voice_id": voice_id}).status_code, 403)

                created = admin.post("/api/admin/voices", headers=headers, json={
                    "name": "Speaker · warm", "description": "Warm and clear.", "voice_id": voice_id,
                    "consent_type": "written", "speaker": "Test Speaker", "consent_date": "2026-11-21"})
                self.assertEqual(created.status_code, 201)
                profile = created.json()
                self.assertEqual(profile["problem"], "Attach the speaker's signed consent before publishing.")
                route = f"/api/admin/voices/{profile['id']}"
                self.assertEqual(admin.put(route + "/published", headers=headers, json={"published": True}).status_code, 409)
                bad = admin.post(route + "/consent", headers=headers, files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
                self.assertEqual(bad.status_code, 400)
                self.assertEqual(admin.post(route + "/consent", headers=headers,
                                            files={"file": ("c.pdf", b"%PDF-1.4 signed", "application/pdf")}).status_code, 200)
                self.assertEqual(admin.get(route + "/consent").content, b"%PDF-1.4 signed")
                self.assertEqual(customer.get(route + "/consent").status_code, 403)
                self.assertEqual(admin.put(route + "/published", headers=headers, json={"published": True}).status_code, 200)

                # A sample customers can play.
                self.assertEqual(admin.post(route + "/sample", headers=headers, json={}).status_code, 202)
                for _ in range(200):
                    listed = customer.get("/api/voices").json()["voices"]
                    if listed and listed[0]["sample"]:
                        break
                    import time
                    time.sleep(0.1)
                self.assertEqual(listed[0]["name"], "Speaker · warm")
                self.assertNotIn("consent_type", listed[0])
                self.assertEqual(customer.get(listed[0]["sample"]).status_code, 200)

                # The customer records with the chosen voice.
                job = customer.post("/api/jobs", headers=headers, json={"script": "Hello there.", "voice_profile": profile["id"]})
                self.assertEqual(job.status_code, 202)
                details = wait_for(customer, f"/api/jobs/{job.json()['id']}", "ready")
                self.assertEqual((details["voice_name"], details["voice_available"]), ("Speaker · warm", True))
                self.assertEqual(customer.get("/api/state").json()["catalog"][0]["id"], profile["id"])

                # Revoking consent removes the voice and blocks regeneration.
                self.assertEqual(admin.post(route + "/revoke", headers=headers).status_code, 200)
                self.assertEqual(customer.get("/api/voices").json(), {"voices": []})
                again = customer.post(f"/api/jobs/{job.json()['id']}/sections/1", headers=headers, json={"text": "Hi."})
                self.assertEqual(again.status_code, 409)
                self.assertEqual(customer.get(f"/api/jobs/{job.json()['id']}").json()["voice_available"], False)


if __name__ == "__main__":
    unittest.main()

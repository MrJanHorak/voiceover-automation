import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from studio.audio import ModelDetails
from studio.store import Store


class RecordingDetailsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.store.initialize()
        self.store.add_voice("voice-1", "Warm reference", 10.0)

    def test_details_follow_successful_audio_across_retry_and_regeneration(self):
        job_id = self.store.create_job("Example", "Hello.")
        first = {"engine": "Chatterbox Turbo", "device": "cuda", "runtime": "0.1.7", "model_sha256": "a" * 64}
        self.store.finish_section(job_id, 1, "first.wav", first, 35)
        self.store.finish_job(job_id, "export-one")
        job = self.store.job(job_id)
        self.assertEqual(job["voice_label"], "Warm reference")
        self.assertEqual(job["sections"][0]["rendered_engine"], "Chatterbox Turbo")
        self.assertEqual(job["sections"][0]["rendered_seed"], 35)

        self.store.add_voice("voice-2", "Different reference", 10.0)
        self.store.regenerate(job_id, 1, "Hello again.", "Calm", directed_available=True)
        pending = self.store.job(job_id)
        self.assertEqual(pending["voice_label"], "Warm reference")
        self.assertEqual(pending["sections"][0]["audio"], "first.wav")
        self.assertEqual(pending["sections"][0]["rendered_engine"], "Chatterbox Turbo")
        self.store.fail_job(job_id, "Simulated error")
        self.store.retry(job_id)
        self.assertEqual(self.store.job(job_id)["sections"][0]["rendered_seed"], 35)

        next_seed = pending["sections"][0]["seed"]
        second = {"engine": "VoxCPM2 Q8", "device": "cuda", "runtime": "audio.cpp 0.9.4", "model_sha256": "b" * 64}
        self.store.finish_section(job_id, 1, "second.wav", second, next_seed)
        section = self.store.job(job_id)["sections"][0]
        self.assertEqual((section["audio"], section["rendered_engine"], section["rendered_seed"]),
                         ("second.wav", "VoxCPM2 Q8", next_seed))
        self.assertEqual(section["rendered_model_sha256"], "b" * 64)

    def test_old_sections_remain_unknown_after_migration(self):
        job_id = self.store.create_job("Old", "Old recording.")
        self.store.finish_section(job_id, 1, "old.wav")
        with self.store.connect() as db:
            for name in ("rendered_engine", "rendered_device", "rendered_runtime", "rendered_model_sha256", "rendered_seed", "rendered_at"):
                db.execute(f"ALTER TABLE sections DROP COLUMN {name}")
        self.store.initialize()
        section = self.store.job(job_id)["sections"][0]
        self.assertEqual(section["audio"], "old.wav")
        self.assertIsNone(section["rendered_engine"])
        self.assertIsNone(section["rendered_model_sha256"])

    def test_fingerprint_identifies_current_primary_model(self):
        import hashlib
        path = self.root / "model"
        details = ModelDetails()
        path.write_bytes(b"first")
        self.assertEqual(details.model_hash(path), hashlib.sha256(b"first").hexdigest())
        path.write_bytes(b"replacement model")
        self.assertEqual(details.model_hash(path), hashlib.sha256(b"replacement model").hexdigest())
        path.unlink()
        self.assertIsNone(details.model_hash(path))


if __name__ == "__main__":
    unittest.main()

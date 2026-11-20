"""Maximum resemblance: reference transcripts, validation, routing and take history."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from studio.store import DIRECTED_VERSION, Store, StudioError

TRANSCRIPT = "Hi, this is my normal speaking voice for the studio."


class VoiceMatchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.initialize()
        self.store.add_voice("voice", "Test", 7.0)

    def create(self, script="Hello there.", **options):
        options.setdefault("directed_available", True)
        return self.store.create_job("Title", script, voice_match="maximum", **options)

    def test_transcript_is_required_and_saved(self):
        with self.assertRaises(StudioError) as missing:
            self.create()
        self.assertEqual(missing.exception.status, 409)
        saved = self.store.set_transcript("voice", TRANSCRIPT)
        self.assertEqual(saved["transcript"], TRANSCRIPT)
        self.assertEqual(self.store.active_voice()["transcript"], TRANSCRIPT)
        with self.assertRaises(StudioError):
            self.store.set_transcript("missing", TRANSCRIPT)

    def test_validation(self):
        self.store.set_transcript("voice", TRANSCRIPT)
        with self.assertRaises(StudioError) as unavailable:
            self.create(directed_available=False)
        self.assertEqual(unavailable.exception.status, 409)
        with self.assertRaises(StudioError):
            self.create("Hello [laugh] there.")
        with self.assertRaises(StudioError):
            self.store.create_job("Title", "Hello.", voice_match="other", directed_available=True)
        # Chatterbox sections still accept sound cues without a transcript or VoxCPM2.
        self.store.set_transcript("voice", "")
        self.store.create_job("Title", "Hello [laugh] there.")

    def test_sections_record_mode_seed_and_engine(self):
        self.store.set_transcript("voice", TRANSCRIPT)
        job_id = self.create("One sentence.\n\nAnother paragraph.")
        job = self.store.job(job_id)
        self.assertEqual(job["engine"], DIRECTED_VERSION)
        self.assertEqual(job["voice_transcript"], TRANSCRIPT)
        self.assertEqual({section["voice_match"] for section in job["sections"]}, {"maximum"})
        self.assertEqual({section["seed"] for section in job["sections"]}, {35})

    def test_instructions_take_priority_in_prepared_sections(self):
        # No transcript is needed when every maximum section has special instructions.
        job_id = self.store.create_job("Title", "a\n\nb", prepared_sections=[
            {"text": "A calm line.", "instructions": "Calm"},
            {"text": "Plain line.", "instructions": "", "voice_match": "standard"},
        ], voice_match="maximum", directed_available=True)
        self.assertEqual([section["voice_match"] for section in self.store.job(job_id)["sections"]], ["maximum", "standard"])

    def test_regenerate_switch_and_restore_mode(self):
        self.store.set_transcript("voice", TRANSCRIPT)
        job_id = self.store.create_job("Title", "Hello there.")
        self.store.finish_section(job_id, 1, "first.wav", {"engine": "Chatterbox Turbo"}, 35)
        self.store.finish_job(job_id, "export")
        self.store.regenerate(job_id, 1, "Hello there.", "", True, None, "maximum")
        section = self.store.job(job_id)["sections"][0]
        self.assertEqual((section["voice_match"], section["pending"]), ("maximum", 1))
        (self.store.recordings / job_id).mkdir(parents=True)
        for name in ("first.wav", "second.wav", "third.wav"):
            (self.store.recordings / job_id / name).write_bytes(b"audio")
        self.store.finish_section(job_id, 1, "second.wav", {"engine": "VoxCPM2 · maximum resemblance"}, section["seed"])
        self.store.finish_job(job_id, "export2")
        takes = self.store.job(job_id)["sections"][0]["takes"]
        self.assertEqual([take["voice_match"] for take in takes], ["maximum", "standard"])
        # Regeneration without a mode keeps the section's current mode.
        self.store.regenerate(job_id, 1, "Hello again.", None, True)
        self.assertEqual(self.store.job(job_id)["sections"][0]["voice_match"], "maximum")
        self.store.finish_section(job_id, 1, "third.wav", {}, 1)
        self.store.finish_job(job_id, "export3")
        first = next(take for take in self.store.job(job_id)["sections"][0]["takes"] if take["audio"] == "first.wav")
        self.store.restore_take(job_id, 1, first["id"])
        self.assertEqual(self.store.job(job_id)["sections"][0]["voice_match"], "standard")
        # Without a transcript, switching a section to maximum resemblance is refused.
        self.store.finish_job(job_id, "export4")
        self.store.set_transcript("voice", "")
        with self.assertRaises(StudioError) as missing:
            self.store.regenerate(job_id, 1, "Hello.", "", True, None, "maximum")
        self.assertEqual(missing.exception.status, 409)


class VoiceMatchRoutingTests(unittest.TestCase):
    """Needs the studio environment (FastAPI, soundfile)."""

    def test_maximum_sections_use_matched_rendering_and_label(self):
        from studio.app import render_job

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            calls, finished = [], {}

            class FakeStore:
                recordings = root
                voices = root

                def job(self, job_id):
                    return {"voice_id": "voice", "voice_transcript": TRANSCRIPT, "trim_edges": 1, "sections": [
                        {"number": 1, "audio": None, "pending": True, "text": "Plain.", "seed": 35,
                         "instructions": "", "voice_match": "maximum", "turbo_settings": None, "pause_ms": 0},
                        {"number": 2, "audio": None, "pending": True, "text": "Calm.", "seed": 35,
                         "instructions": "Calm", "voice_match": "maximum", "turbo_settings": None, "pause_ms": 0},
                        {"number": 3, "audio": None, "pending": True, "text": "Fast.", "seed": 7,
                         "instructions": "", "voice_match": "standard", "turbo_settings": None, "pause_ms": 0},
                    ]}

                def finish_section(self, job_id, number, filename, details, seed, spoken=None):
                    finished[number] = details

                def finish_job(self, job_id, export, loudness=None):
                    pass

            class Turbo:
                def render(self, text, reference, seed, output):
                    calls.append(("turbo", text))

                def details(self):
                    return {"engine": "Chatterbox Turbo"}

            class Directed:
                def render(self, text, reference, seed, output, instructions):
                    calls.append(("directed", text, instructions))

                def render_matched(self, text, reference, seed, output, transcript):
                    calls.append(("matched", text, transcript))

                def details(self):
                    return {"engine": "VoxCPM2"}

            from unittest.mock import patch
            with patch("studio.app.export_audio"):
                render_job(FakeStore(), Turbo(), Directed(), Event(), "job")
            self.assertEqual(calls, [("matched", "Plain.", TRANSCRIPT), ("directed", "Calm.", "Calm"), ("turbo", "Fast.")])
            self.assertEqual(finished[1]["engine"], "VoxCPM2 · maximum resemblance")
            self.assertEqual(finished[2]["engine"], "VoxCPM2")

    def test_gpu_command_requests_ultimate_clone_without_script_text(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        import numpy as np
        import perth  # noqa: F401  Load before mocking subprocess.
        import soundfile as sf
        from studio.audio import RATE, CudaDirectedEngine

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cli").touch()
            (root / "model.gguf").touch()
            reference = root / "reference.wav"
            sf.write(reference, np.ones(RATE, dtype=np.float32) * 0.1, RATE)
            engine = CudaDirectedEngine(root / "cli", root / "model.gguf")
            engine.watermarker = SimpleNamespace(apply_watermark=lambda audio, sample_rate: audio)
            calls = []

            def fake_cli(command, **kwargs):
                calls.append(command)
                inputs = Path(command[command.index("--batch-text-dir") + 1])
                outputs = Path(command[command.index("--out-dir") + 1])
                self.assertEqual((inputs / "section.txt").read_text(encoding="utf-8"), "Private script.")
                sf.write(outputs / "section.wav", np.ones(4800, dtype=np.float32) * 0.1, 48000)
                return SimpleNamespace(returncode=0)

            def fake_ffmpeg(*args):
                sf.write(args[-1], np.ones(48000, dtype=np.float32) * 0.1, 48000)

            with patch("studio.audio.ffmpeg", side_effect=fake_ffmpeg), \
                 patch("studio.audio.subprocess.run", side_effect=fake_cli):
                engine.render_matched("Private script.", reference, 42, root / "out.wav", f"  {TRANSCRIPT}  ")
            command = calls[0]
            self.assertNotIn("Private script.", " ".join(command))
            self.assertEqual(command[command.index("--reference-text") + 1], TRANSCRIPT)
            self.assertIn("ultimate_clone=true", command)


if __name__ == "__main__":
    unittest.main()

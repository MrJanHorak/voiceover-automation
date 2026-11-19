import unittest
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import perth  # Load optional dependencies before mocking subprocess.
import scipy.signal
import soundfile as sf

from studio.audio import AudioError, CudaDirectedEngine, RATE
from studio.app import render_job


class CudaDirectedEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root / "cli"
        self.model = self.root / "model.gguf"
        self.executable.touch()
        self.model.touch()
        self.reference = self.root / "reference.wav"
        sf.write(self.reference, np.ones(RATE, dtype=np.float32) * 0.1, RATE)
        self.output = self.root / "result.wav"
        self.engine = CudaDirectedEngine(self.executable, self.model)
        self.engine.watermarker = SimpleNamespace(apply_watermark=lambda audio, sample_rate: audio)

    def test_private_input_watermark_and_output(self):
        calls = []

        def fake_cli(command, **kwargs):
            calls.append((command, kwargs))
            inputs = Path(command[command.index("--batch-text-dir") + 1])
            outputs = Path(command[command.index("--out-dir") + 1])
            self.assertEqual((inputs / "section.txt").read_text(encoding="utf-8"),
                             "(Speak calmly)Private script.")
            sf.write(outputs / "section.wav", np.ones(4800, dtype=np.float32) * 0.1, 48000)
            return SimpleNamespace(returncode=0)

        def fake_ffmpeg(*args):
            sf.write(args[-1], np.ones(48000, dtype=np.float32) * 0.1, 48000)

        with patch("studio.audio.ffmpeg", side_effect=fake_ffmpeg), patch("studio.audio.subprocess.run", side_effect=fake_cli):
            self.engine.render("Private script.", self.reference, 42, self.output, "Speak (calmly)")
        command, kwargs = calls[0]
        self.assertNotIn("Private script.", " ".join(command))
        self.assertIn("voxcpm2.audiovae_encoder_sample_capacity=240000", command)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(sf.info(self.output).samplerate, RATE)
        self.assertFalse(list(self.root.glob(".directed-*")))

    def test_switch_releases_gpu_and_failure_keeps_existing_audio(self):
        folder = self.root / "recordings" / "job"
        folder.mkdir(parents=True)
        previous = folder / "previous.wav"
        previous.write_bytes(b"previous audio")
        events = []

        class Store:
            recordings = self.root / "recordings"
            voices = self.root

            def job(self, job_id):
                return {"voice_id": "voice", "sections": [{"number": 1, "audio": "previous.wav",
                        "pending": True, "text": "Hello.", "seed": 1,
                        "instructions": "Calm", "pause_ms": 0}]}

        class Turbo:
            def release(self):
                events.append("release")

        class Directed:
            needs_exclusive_gpu = True

            def render(self, text, reference, seed, output, instructions):
                events.append("render")
                output.write_bytes(b"partial")
                raise AudioError("simulated GPU failure")

        with self.assertRaises(AudioError):
            render_job(Store(), Turbo(), Directed(), Event(), "job")
        self.assertEqual(events, ["release", "render"])
        self.assertEqual(previous.read_bytes(), b"previous audio")
        self.assertEqual(list(folder.iterdir()), [previous])

    def test_failed_gpu_run_removes_temporary_files(self):
        def fake_ffmpeg(*args):
            sf.write(args[-1], np.ones(48000, dtype=np.float32) * 0.1, 48000)

        with patch("studio.audio.ffmpeg", side_effect=fake_ffmpeg), patch("studio.audio.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(AudioError):
                self.engine.render("A private script.", self.reference, 1, self.output, "Calm")
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".directed-*")))


if __name__ == "__main__":
    unittest.main()

"""Delivery quality: internal pause cap, reference join fades, loudness pass and GPU render order."""

import re
import shutil
import subprocess
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

import numpy as np

from studio.audio import RATE, fade_joins, normalize_loudness, tighten_pauses
from studio.store import DEFAULT_LOUDNESS, MASTER_RATE, Store, StudioError


def tone(seconds, rate=RATE, frequency=220, amplitude=6000):
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.int16)


class PauseCapTests(unittest.TestCase):
    def test_long_gap_inside_speech_is_shortened(self):
        audio = np.concatenate([tone(1), np.zeros(2 * RATE, np.int16), tone(1)])
        self.assertAlmostEqual(tighten_pauses(audio, RATE).size / RATE, 2.54, delta=0.02)

    def test_normal_pauses_and_edges_are_untouched(self):
        audio = np.concatenate([np.zeros(RATE, np.int16), tone(1), np.zeros(int(0.4 * RATE), np.int16), tone(1),
                                np.zeros(RATE, np.int16)])
        self.assertEqual(tighten_pauses(audio, RATE).size, audio.size)


class JoinFadeTests(unittest.TestCase):
    def test_only_inner_edges_fade(self):
        pieces = [np.full(RATE, 1000, "<i2").tobytes(), np.full(RATE, -2000, "<i2").tobytes()]
        first, second = (np.frombuffer(piece, "<i2") for piece in fade_joins(pieces))
        self.assertEqual((int(first[0]), int(first[-1]), int(second[0]), int(second[-1])), (1000, 0, 0, -2000))
        self.assertEqual(fade_joins(pieces[:1]), pieces[:1])


class LoudnessStoreTests(unittest.TestCase):
    def test_new_jobs_use_master_and_loudness_and_old_jobs_do_not(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            store.add_voice("voice", "Test", 7.0)
            job_id = store.create_job("Title", "Hello.")
            job = store.job(job_id)
            self.assertEqual((job["master_rate"], job["loudness_target"]), (MASTER_RATE, DEFAULT_LOUDNESS))
            with self.assertRaises(StudioError):
                store.set_loudness(job_id, -23.0)  # Still queued.
            store.finish_job(job_id, "export", {"integrated": -16.1, "true_peak": -1.2})
            self.assertIn("-16.1", store.job(job_id)["export_loudness"])
            store.set_loudness(job_id, -23.0)
            self.assertEqual((store.job(job_id)["loudness_target"], store.job(job_id)["status"]), (-23.0, "queued"))
            store.finish_job(job_id, "export2")
            store.set_loudness(job_id, None)
            self.assertIsNone(store.job(job_id)["loudness_target"])
            store.finish_job(job_id, "export3")
            with self.assertRaises(StudioError):
                store.set_loudness(job_id, -20.0)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class LoudnessPassTests(unittest.TestCase):
    def test_master_reaches_target_with_true_peak_ceiling(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            source, destination = root / "in.wav", root / "out.wav"
            speechlike = np.concatenate([tone(1.5, 48000, 220, 2500), np.zeros(9600, np.int16), tone(1.5, 48000, 330, 9000)])
            with wave.open(str(source), "wb") as audio:
                audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                audio.writeframes(speechlike.astype("<i2").tobytes())
            result = normalize_loudness(source, destination, -16.0, 48000)
            self.assertAlmostEqual(result["integrated"], -16.0, delta=1.0)
            self.assertLessEqual(result["true_peak"], -0.9)
            with wave.open(str(destination), "rb") as audio:
                self.assertEqual(audio.getframerate(), 48000)
            report = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(destination), "-af", "ebur128",
                                     "-f", "null", "-"], capture_output=True, text=True).stderr
            self.assertAlmostEqual(float(re.findall(r"I:\s+(-?[\d.]+) LUFS", report)[-1]), -16.0, delta=1.0)


    def test_peaky_recording_reaches_target(self):
        # Speech often has peaks far above its average level; reaching -16 LUFS then needs a
        # limiter, or loudnorm falls back to dynamic mode and undershoots.
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            source, destination = root / "in.wav", root / "out.wav"
            audio = tone(6, 48000, 220, 1500).astype(np.int32)
            audio[::4800] = 30000
            with wave.open(str(source), "wb") as handle:
                handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                handle.writeframes(audio.astype("<i2").tobytes())
            for target in (-16.0, -14.0):
                destination.unlink(missing_ok=True)
                result = normalize_loudness(source, destination, target, 48000)
                self.assertAlmostEqual(result["integrated"], target, delta=0.5)
                self.assertLessEqual(result["true_peak"], -1.0)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["in.wav", "out.wav"])


class RenderOrderTests(unittest.TestCase):
    """Needs the studio environment (FastAPI)."""

    def test_exclusive_gpu_renders_chatterbox_first_but_exports_in_order(self):
        from unittest.mock import patch
        from studio.app import render_job

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            calls, exported = [], []

            def section(number, instructions="", match="standard"):
                return {"number": number, "audio": None, "pending": True, "text": f"Line {number}.", "seed": 1,
                        "instructions": instructions, "voice_match": match, "turbo_settings": None, "pause_ms": 0}

            class FakeStore:
                recordings = root
                voices = root

                def job(self, job_id):
                    return {"voice_id": "voice", "voice_transcript": "Words.", "trim_edges": 1, "master_rate": 48000,
                            "loudness_target": -16.0,
                            "sections": [section(1, "Calm"), section(2), section(3, match="maximum"), section(4)]}

                def finish_section(self, *args, **kwargs):
                    pass

                def finish_job(self, job_id, export, loudness=None):
                    pass

            class Turbo:
                def render(self, text, reference, seed, output):
                    calls.append(f"turbo {text}")

                def release(self):
                    calls.append("release")

            class Directed:
                needs_exclusive_gpu = True

                def render(self, text, reference, seed, output, instructions):
                    calls.append(f"directed {text}")

                def render_matched(self, text, reference, seed, output, transcript):
                    calls.append(f"matched {text}")

            def capture(paths, *args, **kwargs):
                exported.extend(path.name.split("-")[1] for path, _pause in paths)

            with patch("studio.app.export_audio", side_effect=capture):
                render_job(FakeStore(), Turbo(), Directed(), Event(), "job")
            self.assertEqual(calls, ["turbo Line two.", "turbo Line four.", "release", "directed Line one.",
                                     "release", "matched Line three."])
            self.assertEqual(exported, ["1", "2", "3", "4"])


if __name__ == "__main__":
    unittest.main()

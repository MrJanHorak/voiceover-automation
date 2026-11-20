"""Training recordings: measurements, verdicts, prompts, takes and export."""

import json
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from studio.dataset import assess_take, recording_stats, split_prompts, training_text
from studio.dataset_prompts import PROMPTS
from studio.store import Store, StudioError


def voice(seconds=4.0, rate=48000, level=0.3, noise=0.0005):
    t = np.arange(int(rate * seconds)) / rate
    signal = level * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    signal[: rate // 2] = 0  # room tone before speaking
    return signal + noise * np.random.default_rng(1).standard_normal(signal.size)


class MeasureTests(unittest.TestCase):
    def test_clean_take_passes(self):
        stats = recording_stats(voice(), 48000)
        self.assertLess(stats["noise_db"], -50)
        result = assess_take("The train pulled out of the station.", "The train pulled out of the station.", stats)
        self.assertEqual((result["status"], result["word_error"]), ("pass", 0.0))

    def test_problems_are_explained(self):
        loud = voice(level=1.4).clip(-1, 1)
        self.assertIn("clipped", " ".join(assess_take("Hello there.", "Hello there.", recording_stats(loud, 48000))["reasons"]))
        quiet = recording_stats(voice(level=0.02), 48000)
        self.assertIn("quiet", " ".join(assess_take("Hello there.", "Hello there.", quiet)["reasons"]))
        noisy = recording_stats(voice(noise=0.02), 48000)
        self.assertIn("noise", " ".join(assess_take("Hello there.", "Hello there.", noisy)["reasons"]))
        wrong = assess_take("The train pulled out of the station.", "The rain fell on the nation.", recording_stats(voice(), 48000))
        self.assertIn("Words differ", wrong["reasons"][0])

    def test_without_transcription_only_measurements_count(self):
        result = assess_take("Hello there.", None, recording_stats(voice(), 48000))
        self.assertEqual((result["status"], result["word_error"]), ("unchecked", None))

    def test_numbers_are_read_as_words(self):
        self.assertEqual(training_text("Tickets start at $25."), "Tickets start at twenty-five dollars.")
        stats = recording_stats(voice(), 48000)
        self.assertEqual(assess_take("Tickets start at $25.", "Tickets start at $25.", stats)["status"], "pass")

    def test_prompts(self):
        self.assertGreaterEqual(len(PROMPTS), 140)
        self.assertEqual(len(PROMPTS), len(set(PROMPTS)))
        self.assertTrue(all(len(prompt) <= 260 for prompt in PROMPTS))
        pieces = split_prompts("First sentence here. Second one follows.\n\n" + "A long sentence, " * 30 + "end.")
        self.assertTrue(all(len(piece) <= 220 for piece in pieces))


class DatasetStoreTests(unittest.TestCase):
    def test_seed_add_take_replace_keep_delete(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            store.initialize()  # Seeding twice must not duplicate prompts.
            rows = store.dataset_state()
            self.assertEqual(len(rows), len(PROMPTS))
            builtin = rows[0]["id"]
            with self.assertRaises(StudioError):
                store.delete_prompt(builtin)
            self.assertEqual(store.add_prompts(["My own line one.", "My own line two."]), 2)
            custom = [row for row in store.dataset_state() if row["source"] == "custom"]
            self.assertEqual([row["text"] for row in custom], ["My own line one.", "My own line two."])
            self.assertIsNone(store.save_take(builtin, "t" * 32, "a.wav", 48000, 4.0, {"status": "pass"}))
            self.assertEqual(store.save_take(builtin, "u" * 32, "b.wav", 48000, 5.0, {"status": "review"}), "a.wav")
            take = store.dataset_take("u" * 32)
            self.assertEqual((take["kept"], take["duration"]), (0, 5.0))
            self.assertEqual(store.kept_takes(), [])
            store.set_take_kept("u" * 32, True)
            self.assertEqual(store.kept_takes(), [{"audio": "b.wav", "text": rows[0]["text"]}])
            store.save_take(custom[0]["id"], "v" * 32, "c.wav", 48000, 3.0, {"status": "pass"})
            self.assertEqual(store.delete_prompt(custom[0]["id"]), "c.wav")
            with self.assertRaises(StudioError):
                store.dataset_take("v" * 32)


class ExportTests(unittest.TestCase):
    """Needs soundfile and scipy (the studio environment)."""

    def test_export_writes_16k_clips_and_manifests(self):
        import soundfile as sf
        from studio.dataset import export_dataset

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            takes = []
            for index in range(21):
                path = root / f"take{index}.wav"
                sf.write(path, voice(2.0).astype(np.float32), 48000, subtype="PCM_16")
                takes.append((path, f"Line {index + 1} costs $5."))
            summary = export_dataset(takes, root / "export")
            self.assertEqual((summary["clips"], summary["train"], summary["validation"]), (21, 20, 1))
            lines = [json.loads(line) for line in (root / "export" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(lines[0]["text"], "Line one costs five dollars.")
            self.assertEqual(sf.info(root / "export" / lines[0]["audio"]).samplerate, 16000)
            self.assertLess(lines[0]["duration"], 2.0)  # Leading room tone trimmed.


if __name__ == "__main__":
    unittest.main()

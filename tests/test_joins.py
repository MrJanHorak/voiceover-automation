"""Model-free checks for sentence splitting, section pauses, edge trimming and saved originals."""

import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from studio.audio import RATE, trim_edge_silence
from studio.store import (CLAUSE_PAUSE_MS, PARAGRAPH_PAUSE_MS, SENTENCE_PAUSE_MS, Store,
                          split_script_with_pauses, split_sentences)


class SentenceSplitTests(unittest.TestCase):
    def test_abbreviations_stay_with_their_sentence(self):
        self.assertEqual(split_sentences(
            "Call Dr. Smith on Jan. 5 at 3 p.m. today. The U.S. office opened. John F. Kennedy spoke. "
            "See e.g. Fig. 3 for details."
        ), [
            "Call Dr. Smith on Jan. 5 at 3 p.m. today.",
            "The U.S. office opened.",
            "John F. Kennedy spoke.",
            "See e.g. Fig. 3 for details.",
        ])

    def test_abbreviation_before_a_capital_ends_the_sentence(self):
        self.assertEqual(split_sentences("I moved to the U.S. Next year I start. Meet at 5 p.m. Bring food."),
                         ["I moved to the U.S.", "Next year I start.", "Meet at 5 p.m.", "Bring food."])

    def test_pauses_follow_sentence_and_paragraph_ends(self):
        first = "A sentence that is long enough to fill most of one section on its own, " * 3 + "end."
        sections = split_script_with_pauses(f"{first} Second sentence.\n\nNew paragraph.")
        self.assertEqual([pause for _, pause in sections], [SENTENCE_PAUSE_MS, PARAGRAPH_PAUSE_MS, PARAGRAPH_PAUSE_MS])
        wrapped = split_script_with_pauses("word " * 120)
        self.assertEqual(wrapped[0][1], CLAUSE_PAUSE_MS)
        self.assertEqual(wrapped[-1][1], PARAGRAPH_PAUSE_MS)


class TrimTests(unittest.TestCase):
    def tone(self, seconds):
        t = np.arange(int(RATE * seconds)) / RATE
        return (8000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)

    def test_trims_edges_keeps_padding_and_fades(self):
        audio = np.concatenate([np.zeros(RATE // 2, np.int16), self.tone(1), np.zeros(RATE // 2, np.int16)])
        audio[100] = 20000  # An isolated click must not count as speech.
        trimmed = trim_edge_silence(audio)
        self.assertAlmostEqual(trimmed.size / RATE, 1.11, delta=0.02)
        self.assertEqual((int(trimmed[0]), int(trimmed[-1])), (0, 0))

    def test_silence_and_tiny_input_are_unchanged(self):
        self.assertEqual(trim_edge_silence(np.zeros(1000, np.int16)).size, 1000)
        self.assertEqual(trim_edge_silence(np.zeros(5, np.int16)).size, 5)


class StoreJoinTests(unittest.TestCase):
    def test_new_jobs_trim_and_legacy_jobs_do_not(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            store.add_voice("voice", "Test", 7.0)
            job_id = store.create_job("Title", "One. Two.\n\nThree.")
            job = store.job(job_id)
            self.assertEqual(job["trim_edges"], 1)
            self.assertEqual([section["pause_ms"] for section in job["sections"]][-1], PARAGRAPH_PAUSE_MS)
            with store.connect() as db:
                db.execute("UPDATE jobs SET trim_edges = 0")
            self.assertEqual(store.job(job_id)["trim_edges"], 0)
            prepared = store.create_job("Prepared", "a\n\nb", prepared_sections=[
                {"text": "a", "instructions": "", "pause_ms": 0}, {"text": "b", "instructions": ""}])
            self.assertEqual([section["pause_ms"] for section in store.job(prepared)["sections"]], [0, PARAGRAPH_PAUSE_MS])


class OriginalReferenceTests(unittest.TestCase):
    def test_enrollment_keeps_the_untouched_upload(self):
        from fastapi.testclient import TestClient
        from studio.app import create_app
        from test_takes import FakeEngine

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            source = root / "upload.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                tone = np.repeat(self.tone(7), 2)
                audio.writeframes(tone.astype("<i2").tobytes())
            with TestClient(create_app(root, FakeEngine()), base_url="http://localhost") as client:
                response = client.post("/api/voice", files={"audio": ("me.wav", source.read_bytes(), "audio/wav")},
                                       headers={"X-Studio-Request": "1"})
                self.assertEqual(response.status_code, 201, response.text)
                voice_id = response.json()["id"]
            original = root / "studio" / "voices" / "originals" / f"{voice_id}.wav"
            self.assertEqual(original.read_bytes(), source.read_bytes())
            with wave.open(str(original), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getframerate()), (2, 48000))

    def tone(self, seconds):
        t = np.arange(int(48000 * seconds)) / 48000
        return (8000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


if __name__ == "__main__":
    unittest.main()

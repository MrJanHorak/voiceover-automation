"""Automatic checks: scoring, stored results, best-of-N retries and checking finished recordings."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from studio.quality import assess, score
from studio.store import Store, StudioError

EXPECTED = "The Pro plan costs $1,299.99, about 40% less than last year, and the Basic plan is just $19."


class AssessTests(unittest.TestCase):
    def test_correct_take_passes_even_with_digits_from_transcription(self):
        heard = "The pro plan costs $1,299.99, about 40% less than last year, and the basic plan is just $19."
        result = assess(EXPECTED, heard, 10.7, 0.82)
        self.assertEqual((result["status"], result["word_error"], result["reasons"]), ("pass", 0.0, []))

    def test_garbled_opening_is_flagged_even_when_overall_errors_are_low(self):
        heard = ("The pearl blank cost one thousand two hundred ninety-nine dollars and ninety-nine cents, about forty "
                 "percent less than last year, and the basic plan is just nineteen dollars.")
        result = assess(EXPECTED, heard, 10.7, 0.82)
        self.assertEqual(result["status"], "review")
        self.assertIn("opening words", result["reasons"][0])

    def test_skipped_words_low_voice_match_and_pace(self):
        skipped = assess(EXPECTED, "The Pro plan costs $1,299.99, and the Basic plan is just $19.", 10.7, 0.8)
        self.assertIn("starting near", skipped["reasons"][0])
        self.assertIn("Voice match is low", assess("Hello there friend.", "Hello there friend.", 1.2, 0.4)["reasons"][0])
        self.assertIn("Unusual pace", assess(EXPECTED, EXPECTED, 2.0, 0.8)["reasons"][0])

    def test_sound_cues_and_fillers(self):
        self.assertEqual(assess("Wait [laugh] for it, please now.", "Wait for it, please now.", 2.0)["status"], "pass")
        self.assertEqual(assess(EXPECTED, "Um, " + EXPECTED, 10.7)["status"], "pass")

    def test_score_prefers_passing_then_fewer_errors_then_closer_voice(self):
        results = [{"status": "review", "word_error": 0.1, "similarity": 0.9},
                   {"status": "pass", "word_error": 0.05, "similarity": 0.7},
                   {"status": "pass", "word_error": 0.05, "similarity": 0.8}]
        self.assertEqual(min(results, key=score)["similarity"], 0.8)


class CheckStoreTests(unittest.TestCase):
    def test_record_select_and_request(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            store.add_voice("voice", "Test", 7.0)
            with self.assertRaises(StudioError):
                store.create_job("Title", "Hello.", auto_retries=5)
            job_id = store.create_job("Title", "Hello.", auto_retries=2)
            self.assertEqual(store.job(job_id)["auto_retries"], 2)
            store.finish_section(job_id, 1, "a.wav", {"engine": "A"}, 1, "Hello.")
            store.finish_section(job_id, 1, "b.wav", {"engine": "B"}, 2, "Hello.")
            store.record_check(job_id, 1, "a.wav", {"status": "pass", "word_error": 0})
            store.select_take(job_id, 1, "a.wav")
            section = store.job(job_id)["sections"][0]
            self.assertEqual((section["audio"], section["rendered_seed"]), ("a.wav", 1))
            take = next(take for take in section["takes"] if take["audio"] == "a.wav")
            self.assertEqual(json.loads(take["check_result"])["status"], "pass")
            with self.assertRaises(StudioError):
                store.request_check(job_id)  # Still queued.
            store.finish_job(job_id, "export")
            store.request_check(job_id)
            self.assertEqual((store.job(job_id)["check_requested"], store.job(job_id)["status"]), (1, "queued"))
            store.finish_job(job_id, "export2")
            self.assertEqual(store.job(job_id)["check_requested"], 0)


class RetryTests(unittest.TestCase):
    """Needs the studio environment (FastAPI)."""

    def run_job(self, verdicts, retries=2, check_requested=False):
        from unittest.mock import patch
        from studio.app import render_job

        temporary = TemporaryDirectory(dir=".tools")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        log = {"rendered": [], "checked": [], "selected": [], "recorded": []}
        verdicts = list(verdicts)

        class FakeStore:
            recordings = root
            voices = root

            def job(self, job_id):
                return {"id": job_id, "voice_id": "voice", "auto_retries": retries, "check_requested": check_requested,
                        "trim_edges": 1, "master_rate": 48000, "loudness_target": None,
                        "sections": [{"number": 1, "audio": "old.wav" if check_requested else None,
                                      "pending": not check_requested, "text": "Hello there.", "seed": 35,
                                      "instructions": "", "voice_match": "standard", "turbo_settings": None,
                                      "pause_ms": 0, "takes": [{"audio": "old.wav", "spoken": "Hello there."}]}]}

            def finish_section(self, job_id, number, filename, details, seed, spoken=None):
                log["rendered"].append((filename, seed))

            def record_check(self, job_id, number, audio, result):
                log["recorded"].append((audio, result["status"]))

            def select_take(self, job_id, number, audio):
                log["selected"].append(audio)

            def finish_job(self, job_id, export, loudness=None):
                pass

        class Engine:
            def render(self, text, reference, seed, output):
                pass

        class Checker:
            available = True

            def check(self, path, spoken, reference):
                log["checked"].append(path.name)
                status, error = verdicts.pop(0)
                return {"status": status, "word_error": error, "similarity": 0.8, "reasons": []}

        with patch("studio.app.export_audio"):
            render_job(FakeStore(), Engine(), object(), Event(), "job", Checker())
        return log

    def test_retry_stops_at_first_pass(self):
        log = self.run_job([("review", 0.3), ("pass", 0.0), ("pass", 0.0)])
        self.assertEqual(len(log["rendered"]), 2)
        self.assertEqual(log["rendered"][0][1], 35)
        self.assertNotEqual(log["rendered"][1][1], 35)
        self.assertEqual(log["selected"], [])  # The passing take is already current.

    def test_all_failing_keeps_the_best_take(self):
        log = self.run_job([("review", 0.3), ("review", 0.1), ("review", 0.4)])
        self.assertEqual(len(log["rendered"]), 3)
        self.assertEqual(log["selected"], [log["rendered"][1][0]])

    def test_no_retries_when_disabled_and_checking_finished_recordings(self):
        self.assertEqual(len(self.run_job([("review", 0.3)], retries=0)["rendered"]), 1)
        log = self.run_job([("pass", 0.0)], check_requested=True)
        self.assertEqual((log["rendered"], log["checked"]), ([], ["old.wav"]))


if __name__ == "__main__":
    unittest.main()

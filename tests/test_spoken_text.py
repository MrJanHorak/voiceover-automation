"""Model-free checks for clause splitting, text normalization and the pronunciation list."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from studio.normalize import ordinal, spoken_text, year
from studio.store import Store, StudioError, split_long_sentence

LONG = ("Here is the part that matters most to our customers, so listen closely, because when we rebuilt the "
        "ordering system we didn't just make it faster, we made it simpler to use on a phone, easier to track from "
        "the moment you click buy, and far more reliable during the busy holiday season when traffic triples overnight.")


class ClauseSplitTests(unittest.TestCase):
    def test_long_sentence_splits_at_a_balanced_comma(self):
        pieces = split_long_sentence(LONG)
        self.assertEqual(len(pieces), 2)
        self.assertTrue(pieces[0].endswith("faster,"))
        self.assertTrue(all(len(piece) <= 220 for piece in pieces))
        self.assertTrue(all("you click buy" in piece or "click" not in piece for piece in pieces))

    def test_without_punctuation_pieces_are_balanced(self):
        pieces = split_long_sentence("word " * 120)
        self.assertEqual(len(pieces), 3)
        self.assertLess(max(map(len, pieces)) - min(map(len, pieces)), 15)


class NormalizeTests(unittest.TestCase):
    def check(self, written, spoken):
        self.assertEqual(spoken_text(written), spoken)

    def test_dates_titles_and_times(self):
        self.check("Dr. Smith joined on Jan. 5 at 9 a.m., e.g. on time.",
                   "Doctor Smith joined on January fifth at nine A M, for example, on time.")
        self.check("By 3:30 PM on March 21st, 2027.", "By three thirty P M on March twenty-first, twenty twenty-seven.")
        self.check("Meet at 10:05 or 12:00.", "Meet at ten oh five or twelve o'clock.")

    def test_money_percent_and_numbers(self):
        self.check("It costs $1,299.99, about 40% less.",
                   "It costs one thousand two hundred ninety-nine dollars and ninety-nine cents, about forty percent less.")
        self.check("Up 4.5% to $2.5 million from $1.", "Up four point five percent to two point five million dollars from one dollar.")
        self.check("12000 people, 1,000,000 fans, No. 7.", "twelve thousand people, one million fans, number seven.")

    def test_years_ranges_phones_and_addresses(self):
        self.check("In 1905, 2005 and 2024.", "In nineteen oh five, two thousand five and twenty twenty-four.")
        self.check("From 2020-2024.", "From twenty twenty to twenty twenty-four.")
        self.check("Call 555-0123 or (555) 123-4567.", "Call five five five, oh one two three or five five five, one two three, four five six seven.")
        self.check("Visit www.example.com or jo@studio.io.", "Visit w w w dot example dot com or jo at studio dot io.")

    def test_initialisms_keep_sentence_ends(self):
        self.check("The U.S. office. I moved to the U.S. Next year.", "The U S office. I moved to the U S. Next year.")

    def test_sound_cues_are_untouched(self):
        self.check("Wait [laugh] 5 more.", "Wait [laugh] five more.")

    def test_pronunciations_win_and_match_whole_words(self):
        pairs = [("Kubernetes", "koo-ber-NET-eez"), ("AWS", "A W S")]
        self.assertEqual(spoken_text("kubernetes on AWS, not AWSOME.", pairs), "koo-ber-NET-eez on A W S, not AWSOME.")

    def test_number_words(self):
        self.assertEqual([ordinal(n) for n in (1, 2, 3, 12, 20, 21, 100)],
                         ["first", "second", "third", "twelfth", "twentieth", "twenty-first", "one hundredth"])
        self.assertEqual([year(n) for n in (1999, 2000, 2005, 2010, 1900)],
                         ["nineteen ninety-nine", "two thousand", "two thousand five", "twenty ten", "nineteen hundred"])


class PronunciationStoreTests(unittest.TestCase):
    def test_save_update_list_and_delete(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            first = store.save_pronunciation("  Kubernetes ", "koo-ber-net-eez")
            again = store.save_pronunciation("kubernetes", "koo ber NET eez")
            self.assertEqual(first["id"], again["id"])
            self.assertEqual(store.pronunciation_pairs(), [("kubernetes", "koo ber NET eez")])
            self.assertEqual(len(store.state()["pronunciations"]), 1)
            with self.assertRaises(StudioError):
                store.save_pronunciation("!!", "nothing")
            store.delete_pronunciation(first["id"])
            self.assertEqual(store.pronunciation_pairs(), [])
            with self.assertRaises(StudioError):
                store.delete_pronunciation(first["id"])


if __name__ == "__main__":
    unittest.main()

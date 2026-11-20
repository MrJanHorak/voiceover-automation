"""Automatic checks for generated sections.

Each new take is transcribed with a local Whisper model and compared with the words the
voice was asked to say, scored for resemblance to the voice reference with Chatterbox's
speaker encoder, and checked for an implausible speaking pace. Everything runs on the CPU
so it does not compete with the voice models for the 4 GB GPU, and nothing leaves the computer.
"""

import logging
import math
import os
import re
from pathlib import Path

from .normalize import spoken_text

LOG = logging.getLogger(__name__)
ASR_RATE = 16000
MAX_WORD_ERROR = 0.15  # Allows for ordinary transcription noise in otherwise correct takes.
MIN_SIMILARITY = 0.80  # The owner's good takes scored 0.94-0.98 on 2026-11-20.
MIN_SIMILARITY_SECONDS = 3.0  # Shorter clips score unreliably (a good 2-second take scored 0.59).
WILDCARD = "zzanywordzz"  # Stands in for pronunciation-list words, which transcription cannot spell.
WILDCARD_SPAN = 3
WORDS_PER_SECOND = (1.0, 4.5)


def expected_text(text, pronunciations=()):
    """The words a take should contain, with pronunciation-list words left open.

    A respelling such as "noovexuh" never appears in a transcript, so each listed word may
    match up to three heard words, whatever they are.
    """
    return spoken_text(text, [(written, WILDCARD) for written, _spoken in pronunciations])


def comparable_words(text):
    """Lowercase words for comparison, after writing numbers and abbreviations out in full."""
    text = spoken_text(re.sub(r"\[[^\]]*\]", " ", text or "")).lower()
    text = text.replace("-", " ").replace("\u2019", "'")
    return ["*" if word == WILDCARD else word for word in re.findall(r"[a-z0-9']+", text)]


def word_errors(expected, heard):
    """Word-level edit distance; an open word ("*") matches zero to three heard words for free."""
    rows, cols = len(expected) + 1, len(heard) + 1
    cost = [[0] * cols for _ in range(rows)]
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(cols):
            if expected[i - 1] == "*":
                cost[i][j] = min(cost[i - 1][j - span] for span in range(0, min(WILDCARD_SPAN, j) + 1))
            elif j == 0:
                cost[i][0] = cost[i - 1][0] + 1
            else:
                same = expected[i - 1] == heard[j - 1]
                cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1, cost[i - 1][j - 1] + (0 if same else 1))
    return cost[-1][-1]


def first_difference(expected, heard):
    """The first few expected words that were missed or changed, to explain a failed check."""
    for index, word in enumerate(expected):
        if word == "*":
            return ""  # Alignment after an open word is uncertain; the percentage still reports the gap.
        if index >= len(heard) or heard[index] != word:
            return " ".join(expected[index:index + 6])
    return ""


def opening_errors(expected, heard, size=6):
    """Errors in the first few words; VoxCPM2 sometimes mumbles the start of a section."""
    opening = expected[:size]
    if len(opening) < 3:
        return 0
    return min(word_errors(opening, heard[:length]) for length in range(max(0, len(opening) - 1), len(opening) + 3))


def assess(expected_text, heard_text, duration, similarity=None):
    """Combine transcription, voice match and pace into one result for a take."""
    expected, heard = comparable_words(expected_text), comparable_words(heard_text)
    errors = word_errors(expected, heard)
    rate = errors / max(1, len([word for word in expected if word != "*"]))
    reasons = []
    if rate > MAX_WORD_ERROR:
        missed = first_difference(expected, heard)
        reasons.append(f"Words differ from the script ({rate:.0%})" + (f", starting near “{missed}”" if missed else ""))
    elif opening_errors(expected, heard) >= 2:
        reasons.append(f"The opening words may be unclear (expected \u201c{' '.join(expected[:6]).replace('*', '…')}\u201d)")
    if similarity is not None and duration >= MIN_SIMILARITY_SECONDS and similarity < MIN_SIMILARITY:
        reasons.append(f"Voice match is low ({similarity:.2f})")
    pace = len(expected) / duration if duration > 0 else 0
    if expected and not WORDS_PER_SECOND[0] <= pace <= WORDS_PER_SECOND[1]:
        reasons.append(f"Unusual pace ({pace:.1f} words per second)")
    return {"status": "review" if reasons else "pass", "heard": " ".join(heard_text.split()),
            "word_error": round(rate, 3), "similarity": None if similarity is None else round(float(similarity), 3),
            "pace": round(pace, 2), "reasons": reasons}


def score(result):
    """Lower is better: used to keep the best of several takes."""
    similarity = result.get("similarity")
    return (result["status"] != "pass", result["word_error"], -(similarity if similarity is not None else 0))


class QualityChecker:
    def __init__(self, asr_dir: Path, encoder_path: Path):
        self.asr_dir = asr_dir
        self.encoder_path = encoder_path
        self.processor = None
        self.model = None
        self.encoder = None
        self._reference = (None, None)

    @property
    def available(self):
        weights = any((self.asr_dir / name).is_file() for name in ("model.safetensors", "pytorch_model.bin"))
        return weights and (self.asr_dir / "config.json").is_file() and (self.asr_dir / "preprocessor_config.json").is_file()

    @property
    def description(self):
        return f"Whisper ({self.asr_dir.name}) on CPU" if self.available else None

    def _audio(self, path, rate):
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly

        audio, source_rate = sf.read(path, dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        if source_rate != rate:
            divisor = math.gcd(source_rate, rate)
            audio = resample_poly(audio, rate // divisor, source_rate // divisor).astype(np.float32)
        return audio, len(audio) / rate

    def transcribe(self, path):
        os.environ["HF_HUB_OFFLINE"] = "1"
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        if self.model is None:
            self.processor = WhisperProcessor.from_pretrained(str(self.asr_dir), local_files_only=True)
            self.model = WhisperForConditionalGeneration.from_pretrained(str(self.asr_dir), local_files_only=True).eval()
        audio, duration = self._audio(path, ASR_RATE)
        multilingual = getattr(self.model.generation_config, "is_multilingual", False)
        options = {"language": "en", "task": "transcribe"} if multilingual else {}
        pieces = []
        window = 30 * ASR_RATE  # Whisper hears at most 30 seconds at a time.
        with torch.inference_mode():
            for start in range(0, max(1, len(audio)), window):
                features = self.processor(audio[start:start + window], sampling_rate=ASR_RATE,
                                          return_tensors="pt").input_features
                tokens = self.model.generate(features, max_new_tokens=440, **options)
                pieces.append(self.processor.batch_decode(tokens, skip_special_tokens=True)[0])
        return " ".join(piece.strip() for piece in pieces), duration

    def similarity(self, path, reference):
        import numpy as np

        if not self.encoder_path.is_file():
            return None
        import torch
        from chatterbox.models.voice_encoder import VoiceEncoder
        from safetensors.torch import load_file

        if self.encoder is None:
            encoder = VoiceEncoder()
            encoder.load_state_dict(load_file(str(self.encoder_path)))
            self.encoder = encoder.to("cpu").eval()
        rate = self.encoder.hp.sample_rate
        key = (Path(reference), Path(reference).stat().st_mtime_ns)
        if self._reference[0] != key:
            audio, _ = self._audio(reference, rate)
            self._reference = (key, self.encoder.embeds_from_wavs([audio], rate, as_spk=True))
        audio, _ = self._audio(path, rate)
        with torch.inference_mode():
            take = self.encoder.embeds_from_wavs([audio], rate, as_spk=True)
        return float(np.dot(self._reference[1], take))

    def check(self, path, expected_text, reference):
        heard, duration = self.transcribe(path)
        try:
            similarity = self.similarity(path, reference)
        except Exception as error:  # A voice-match failure should not hide the transcription result.
            LOG.warning("Voice match could not be scored (%s)", type(error).__name__)
            similarity = None
        return assess(expected_text, heard, duration, similarity)

"""Training recordings: measure each take, judge it, and export a fine-tuning dataset.

Takes are the owner's own read of a prompt, recorded in the browser as PCM WAV. They are
kept exactly as recorded. The export trims edge silence, converts to 16 kHz mono (the rate
VoxCPM2's training code expects) and writes JSONL manifests with paths relative to the
export folder, so the folder can be copied to a rented GPU as it is.
"""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from .normalize import spoken_text

MIN_TAKE_SECONDS = 1.0
MAX_TAKE_SECONDS = 45.0
MAX_TAKE_BYTES = 32 * 1024 * 1024
TRAINING_RATE = 16000  # VoxCPM2 config "sample_rate"; its validator rejects other rates.
MAX_WORD_ERROR = 0.10  # Stricter than generated takes: training text must match the audio.
NOISE_LIMIT_DB = -50.0
QUIET_PEAK_DB = -20.0
GOALS_MINUTES = (10, 30, 60)


def training_text(prompt):
    """What the reader says aloud: numbers and abbreviations written out, no respellings."""
    return spoken_text(prompt)


def recording_stats(samples, rate):
    """Level, clipping and background noise of a take, from float samples in -1..1."""
    import numpy as np

    audio = np.asarray(samples, dtype=np.float64)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    duration = audio.size / rate if rate else 0.0
    if not audio.size:
        return {"duration": 0.0, "peak_db": -120.0, "noise_db": -120.0, "clipped": 0}
    peak = float(np.max(np.abs(audio)))
    window = max(1, rate // 20)  # 50 ms
    count = audio.size // window
    if count:
        blocks = audio[:count * window].reshape(count, window)
        rms = np.sqrt(np.mean(blocks * blocks, axis=1))
        noise = float(np.percentile(rms, 10))
    else:
        noise = float(np.sqrt(np.mean(audio * audio)))
    clipped = int(np.count_nonzero(np.abs(audio) >= 0.999))
    to_db = lambda value: round(20 * math.log10(value), 1) if value > 1e-6 else -120.0
    return {"duration": round(duration, 2), "peak_db": to_db(peak), "noise_db": to_db(noise), "clipped": clipped}


def assess_take(prompt, heard, stats):
    """Combine the transcript and the measurements into a verdict for a training take."""
    from .quality import comparable_words, first_difference, opening_errors, word_errors

    reasons = []
    word_error = None
    if heard is not None:
        expected, words = comparable_words(training_text(prompt)), comparable_words(heard)
        word_error = round(word_errors(expected, words) / max(1, len(expected)), 3)
        if word_error > MAX_WORD_ERROR or opening_errors(expected, words) >= 2:
            missed = first_difference(expected, words)
            reasons.append(f"Words differ from the prompt ({word_error:.0%})"
                           + (f", near “{missed}”" if missed else "") + ". Read it again exactly as written.")
    samples = stats["duration"] * 48000
    if stats["clipped"] > max(3, samples * 0.0001):
        reasons.append("The recording clipped (too loud). Lower the microphone gain or move back a little.")
    if stats["peak_db"] < QUIET_PEAK_DB:
        reasons.append(f"The recording is quiet (peak {stats['peak_db']} dB). Move closer or raise the gain.")
    if stats["noise_db"] > NOISE_LIMIT_DB:
        reasons.append(f"Background noise is high ({stats['noise_db']} dB). Record somewhere quieter.")
    if not MIN_TAKE_SECONDS <= stats["duration"] <= MAX_TAKE_SECONDS:
        reasons.append("The recording is too short or too long.")
    status = "review" if reasons else ("pass" if heard is not None else "unchecked")
    return {"status": status, "heard": heard, "word_error": word_error, "reasons": reasons, **stats}


def split_prompts(text, limit=220):
    """Turn pasted script text into prompts of about five to twelve seconds."""
    from .store import split_script

    return [prompt for prompt in split_script(text) if re.search(r"[A-Za-z]", prompt)][:500] if limit else []


def export_dataset(takes, destination, validation_every=20):
    """Write trimmed 16 kHz WAVs and train/validation manifests for the kept takes.

    `takes` is a list of (source WAV path, prompt text). Returns a summary.
    """
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    from .audio import trim_edge_silence

    wavs = destination / "wavs"
    wavs.mkdir(parents=True)
    train, validation, seconds = [], [], 0.0
    for index, (source, prompt) in enumerate(takes, 1):
        audio, rate = sf.read(source, dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        samples = np.clip(np.round(audio * 32767), -32768, 32767).astype(np.int16)
        samples = trim_edge_silence(samples, rate, keep_lead_ms=150, keep_tail_ms=200, fade_in_ms=5, fade_out_ms=20)
        divisor = math.gcd(rate, TRAINING_RATE)
        resampled = resample_poly(samples.astype(np.float64) / 32768, TRAINING_RATE // divisor, rate // divisor)
        name = f"{index:04d}.wav"
        sf.write(wavs / name, np.clip(resampled, -1, 1).astype(np.float32), TRAINING_RATE, subtype="PCM_16")
        duration = round(len(resampled) / TRAINING_RATE, 3)
        seconds += duration
        line = {"audio": f"wavs/{name}", "text": training_text(prompt), "duration": duration}
        (validation if len(takes) >= validation_every and index % validation_every == 0 else train).append(line)
    for name, lines in (("train.jsonl", train), ("val.jsonl", validation)):
        if lines or name == "train.jsonl":
            (destination / name).write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
                                            encoding="utf-8")
    summary = {"clips": len(takes), "train": len(train), "validation": len(validation),
               "minutes": round(seconds / 60, 1), "sample_rate": TRAINING_RATE,
               "created_at": datetime.now(timezone.utc).isoformat(), "folder": str(destination)}
    (destination / "README.txt").write_text(
        "Voice fine-tuning dataset exported by the voice-over studio.\n\n"
        f"{summary['clips']} clips, {summary['minutes']} minutes, {TRAINING_RATE} Hz mono 16-bit WAV.\n"
        "train.jsonl and val.jsonl list one clip per line: audio (path relative to this folder), text and duration.\n"
        "Run VoxCPM2 fine-tuning from this folder so the relative paths resolve.\n"
        "This folder contains the owner's voice. Keep it private and delete it from rented machines after training.\n",
        encoding="utf-8")
    return summary

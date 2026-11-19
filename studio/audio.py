import os
import math
import subprocess
import wave
from importlib.util import find_spec
from math import gcd
from pathlib import Path
from uuid import uuid4


MAX_REFERENCE_BYTES = 16 * 1024 * 1024
RATE = 24000
TARGET_LOUDNESS = -20.0
PEAK_LIMIT = -2.0
MAX_GAIN = 48.0


class AudioError(Exception):
    pass


def ffmpeg(*arguments):
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *arguments],
            check=True,
            capture_output=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as error:
        raise AudioError("Install FFmpeg and make it available on PATH.") from error
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise AudioError("FFmpeg could not finish processing the audio.") from error


def prepare_reference(source, destination):
    import soundfile as sf

    try:
        with sf.SoundFile(source) as audio:
            if audio.format not in {"WAV", "WAVEX"} or not audio.subtype.startswith("PCM_"):
                raise AudioError("Choose a PCM WAV recording.")
            seconds = audio.frames / audio.samplerate
            if not 6 <= seconds <= 30:
                raise AudioError("Use a reference with 6 to 30 seconds of audio.")
            if not audio.read(dtype="float32").any():
                raise AudioError("The recording is silent. Choose a reference with speech.")
    except sf.LibsndfileError as error:
        raise AudioError("The file could not be read as a PCM WAV.") from error

    ffmpeg("-n", "-i", str(source), "-vn", "-ac", "1", "-ar", str(RATE),
           "-c:a", "pcm_s16le", str(destination))
    return round(seconds, 1)


class TurboEngine:
    def __init__(self, model_dir: Path, device: str):
        self.model_dir = model_dir
        self.device = device
        self.model = None
        self.reference = None

    def render(self, text, reference, seed, output):
        os.environ["HF_HUB_OFFLINE"] = "1"
        import soundfile as sf
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        if self.model is None:
            if not (self.model_dir / "t3_turbo_v1.safetensors").is_file():
                raise AudioError("Set up the local Chatterbox Turbo weights using the README.")
            if self.device == "cuda" and not torch.cuda.is_available():
                raise AudioError("CUDA is unavailable. Restart the studio with the CPU setting.")
            self.model = ChatterboxTurboTTS.from_local(self.model_dir, device=self.device)

        with torch.inference_mode():
            if self.reference != reference:
                self.model.prepare_conditionals(str(reference))
                self.reference = reference
            torch.manual_seed(seed)
            audio = self.model.generate(text).squeeze(0)

        if not audio.numel() or not torch.isfinite(audio).all() or not audio.abs().max().item():
            raise AudioError("The model returned unusable audio. Try a shorter section or a new reference.")
        sf.write(output, audio.cpu().numpy(), self.model.sr, subtype="PCM_16")


class DirectedEngine:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model = None
        self.watermarker = None

    @property
    def available(self):
        return find_spec("voxcpm") is not None and all(
            (self.model_dir / filename).is_file()
            for filename in ("model.safetensors", "audiovae.pth", "config.json", "tokenizer.json",
                             "tokenizer_config.json", "tokenization_voxcpm2.py", "special_tokens_map.json")
        )

    def render(self, text, reference, seed, output, instructions):
        if not self.available:
            raise AudioError("Start the studio with the VoxCPM2 setup described in the README.")
        os.environ["HF_HUB_OFFLINE"] = "1"
        import numpy as np
        import perth
        import soundfile as sf
        import torch
        from scipy.signal import resample_poly
        from voxcpm import VoxCPM

        if self.model is None:
            self.watermarker = perth.PerthImplicitWatermarker()
            self.model = VoxCPM.from_pretrained(str(self.model_dir), device="cpu", optimize=False,
                                                local_files_only=True, load_denoiser=False)

        # VoxCPM2 reads the leading parenthetical as control text. Keep its delimiters unambiguous.
        direction = " ".join(instructions.replace("(", " ").replace(")", " ").split())
        with torch.inference_mode():
            torch.manual_seed(seed)
            audio = self.model.generate(text=f"({direction}){text}", reference_wav_path=str(reference),
                                        denoise=False)
            if not audio.size or not np.isfinite(audio).all() or not audio.any():
                raise AudioError("The directed model returned unusable audio. Try a shorter section.")
            source_rate = self.model.tts_model.sample_rate
            divisor = gcd(source_rate, RATE)
            audio = resample_poly(audio, RATE // divisor, source_rate // divisor).astype(np.float32)
            audio = self.watermarker.apply_watermark(audio, sample_rate=RATE)
        sf.write(output, audio, RATE, subtype="PCM_16")


def balanced_section(source):
    """Create a level-matched preview while keeping the generated section intact."""
    import numpy as np
    import pyloudnorm
    import soundfile as sf
    from scipy.signal import resample_poly

    destination = source.with_name(f"balanced-v1-{source.name}")
    if destination.is_file():
        return destination
    audio, rate = sf.read(source, dtype="float64")
    if audio.ndim != 1 or rate != RATE or not audio.size or not np.isfinite(audio).all():
        raise AudioError("A section has an unexpected audio format. Regenerate that section.")
    if not audio.any():
        raise AudioError("A section is silent. Regenerate that section.")
    # R128 measures 400 ms blocks. Pad a short section for measurement only.
    measured = np.pad(audio, (0, max(0, int(rate * 0.4) - audio.size)))
    loudness = pyloudnorm.Meter(rate).integrated_loudness(measured)
    if not math.isfinite(loudness):
        rms = float(np.sqrt(np.mean(audio * audio)))
        loudness = 20 * math.log10(rms)
    # Four-times oversampling estimates inter-sample peaks before applying gain.
    peak = float(np.max(np.abs(resample_poly(audio, 4, 1))))
    peak_db = 20 * math.log10(peak)
    gain_db = min(TARGET_LOUDNESS - loudness, PEAK_LIMIT - peak_db, MAX_GAIN)
    adjusted = audio * (10 ** (gain_db / 20))
    temporary = destination.with_name(f".{destination.stem}-{uuid4().hex}.wav")
    try:
        sf.write(temporary, adjusted, rate, subtype="PCM_16")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def export_audio(chunks, wav_path, mp3_path):
    with wave.open(str(wav_path), "wb") as master:
        master.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for index, (path, pause_ms) in enumerate(chunks):
            with wave.open(str(balanced_section(path)), "rb") as chunk:
                if (chunk.getnchannels(), chunk.getsampwidth(), chunk.getframerate()) != (1, 2, RATE):
                    raise AudioError("A section has an unexpected audio format. Regenerate that section.")
                master.writeframesraw(chunk.readframes(chunk.getnframes()))
            if index + 1 < len(chunks):
                master.writeframesraw(bytes(RATE * 2 * pause_ms // 1000))

    ffmpeg("-n", "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", "96k", str(mp3_path))

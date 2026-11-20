import os
import math
import gc
import hashlib
import subprocess
import wave
from io import BytesIO
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from math import gcd
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


MAX_REFERENCE_BYTES = 16 * 1024 * 1024
RATE = 24000
TARGET_LOUDNESS = -20.0
PEAK_LIMIT = -2.0
MAX_GAIN = 48.0


class AudioError(Exception):
    pass


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class ModelDetails:
    """Identify the primary local weight file without assuming its source revision."""

    def model_hash(self, path):
        try:
            stat = path.stat()
            key = (path, stat.st_size, stat.st_mtime_ns)
            if getattr(self, "_model_hash_key", None) != key:
                with path.open("rb") as weights:
                    digest = hashlib.file_digest(weights, "sha256").hexdigest()
                self._model_hash_key, self._model_hash = key, digest
            return self._model_hash
        except OSError:
            return None


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


def combine_reference_excerpts(excerpts):
    """Join bounded excerpts of normalized saved references without changing them."""
    output = BytesIO()
    total_frames = 0
    pieces = []
    try:
        for path, start, end in excerpts:
            with wave.open(str(path), "rb") as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, RATE, "NONE"):
                    raise AudioError("A saved reference has an unexpected format.")
                first, last = round(start * RATE), round(end * RATE)
                if first < 0 or last <= first or last > source.getnframes():
                    raise AudioError("Check each excerpt's start and end against the recording length.")
                total_frames += last - first
                if total_frames > 30 * RATE:
                    raise AudioError("The combined reference must be 6 to 30 seconds.")
                source.setpos(first)
                piece = source.readframes(last - first)
                if len(piece) != (last - first) * 2:
                    raise AudioError("A saved reference could not be read completely.")
                pieces.append(piece)
        if total_frames < 6 * RATE:
            raise AudioError("The combined reference must be 6 to 30 seconds.")
        if not any(any(piece) for piece in pieces):
            raise AudioError("The selected excerpts are silent.")
        with wave.open(output, "wb") as combined:
            combined.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            for piece in pieces:
                combined.writeframesraw(piece)
    except (OSError, EOFError, wave.Error) as error:
        raise AudioError("A saved reference could not be read.") from error
    return output.getvalue(), round(total_frames / RATE, 1)


class TurboEngine(ModelDetails):
    def __init__(self, model_dir: Path, device: str):
        self.model_dir = model_dir
        self.device = device
        self.model = None
        self.reference = None

    def details(self):
        return {"engine": "Chatterbox Turbo", "device": self.device,
                "runtime": package_version("chatterbox-tts"),
                "model_sha256": self.model_hash(self.model_dir / "t3_turbo_v1.safetensors")}

    def release(self):
        """Free the CUDA model before running another memory-intensive engine."""
        if self.device != "cuda" or self.model is None:
            return
        self.model = None
        self.reference = None
        gc.collect()
        import torch
        torch.cuda.empty_cache()

    def render(self, text, reference, seed, output, settings=None):
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
            normalize = settings.get("normalize_reference", True) if settings else True
            reference_key = (reference, normalize)
            if self.reference != reference_key:
                self.model.prepare_conditionals(str(reference), norm_loudness=normalize)
                self.reference = reference_key
            torch.manual_seed(seed)
            options = {key: settings[key] for key in ("temperature", "top_p", "top_k", "repetition_penalty")} if settings else {}
            audio = self.model.generate(text, **options).squeeze(0)

        if not audio.numel() or not torch.isfinite(audio).all() or not audio.abs().max().item():
            raise AudioError("The model returned unusable audio. Try a shorter section or a new reference.")
        sf.write(output, audio.cpu().numpy(), self.model.sr, subtype="PCM_16")


class DirectedEngine(ModelDetails):
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model = None
        self.watermarker = None

    def details(self):
        return {"engine": "VoxCPM2", "device": "cpu", "runtime": package_version("voxcpm"),
                "model_sha256": self.model_hash(self.model_dir / "model.safetensors")}

    @property
    def available(self):
        return find_spec("voxcpm") is not None and all(
            (self.model_dir / filename).is_file()
            for filename in ("model.safetensors", "audiovae.pth", "config.json", "tokenizer.json",
                             "tokenizer_config.json", "tokenization_voxcpm2.py", "special_tokens_map.json")
        )

    def render(self, text, reference, seed, output, instructions):
        # VoxCPM2 reads the leading parenthetical as control text. Keep its delimiters unambiguous.
        direction = " ".join(instructions.replace("(", " ").replace(")", " ").split())
        self._generate(f"({direction}){text}", reference, seed, output)

    def render_matched(self, text, reference, seed, output, transcript):
        """Maximum resemblance: continue from the reference audio using its exact transcript."""
        self._generate(text, reference, seed, output, transcript=" ".join(transcript.split()))

    def _generate(self, text, reference, seed, output, transcript=None):
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

        prompt = {"prompt_wav_path": str(reference), "prompt_text": transcript} if transcript else {}
        with torch.inference_mode():
            torch.manual_seed(seed)
            audio = self.model.generate(text=text, reference_wav_path=str(reference), denoise=False, **prompt)
            if not audio.size or not np.isfinite(audio).all() or not audio.any():
                raise AudioError("The directed model returned unusable audio. Try a shorter section.")
            source_rate = self.model.tts_model.sample_rate
            divisor = gcd(source_rate, RATE)
            audio = resample_poly(audio, RATE // divisor, source_rate // divisor).astype(np.float32)
            audio = self.watermarker.apply_watermark(audio, sample_rate=RATE)
        sf.write(output, audio, RATE, subtype="PCM_16")


class CudaDirectedEngine(ModelDetails):
    """Optional audio.cpp backend; each invocation releases GPU memory on exit."""

    needs_exclusive_gpu = True

    def __init__(self, executable: Path, model: Path):
        self.executable = executable.resolve()
        self.model = model.resolve()
        self.watermarker = None

    def details(self):
        if not hasattr(self, "_runtime_version"):
            try:
                result = subprocess.run([str(self.executable), "--version"], capture_output=True,
                                        text=True, timeout=10, check=True,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._runtime_version = result.stdout.splitlines()[0][:100] if result.stdout else None
            except (OSError, subprocess.SubprocessError):
                self._runtime_version = None
        return {"engine": "VoxCPM2 Q8", "device": "cuda", "runtime": self._runtime_version,
                "model_sha256": self.model_hash(self.model)}

    @property
    def available(self):
        return self.executable.is_file() and self.model.is_file() and find_spec("perth") is not None

    def render(self, text, reference, seed, output, instructions):
        direction = " ".join(instructions.replace("(", " ").replace(")", " ").split())
        self._generate(f"({direction}){text}", reference, seed, output)

    def render_matched(self, text, reference, seed, output, transcript):
        """Maximum resemblance: audio.cpp's ultimate clone continues from the reference and its transcript."""
        self._generate(text, reference, seed, output, transcript=" ".join(transcript.split()))

    def _generate(self, text, reference, seed, output, transcript=None):
        if not self.available:
            raise AudioError("Set up the optional VoxCPM2 GPU files using the README, or restart with its CPU setting.")
        import numpy as np
        import perth
        import soundfile as sf
        from scipy.signal import resample_poly

        # Keep script and reference audio out of process arguments and temporary system directories.
        with TemporaryDirectory(prefix=".directed-", dir=output.parent) as temporary:
            folder = Path(temporary)
            inputs, outputs = folder / "input", folder / "output"
            inputs.mkdir()
            outputs.mkdir()
            (inputs / "section.txt").write_text(text, encoding="utf-8")
            converted = folder / "reference.wav"
            ffmpeg("-n", "-i", str(reference), "-vn", "-ac", "1", "-ar", "48000",
                   "-c:a", "pcm_s16le", str(converted))
            info = sf.info(converted)
            # AudioVAE requires a capacity in blocks of 240,000 samples (five seconds at 48 kHz).
            capacity = max(240000, math.ceil(info.frames / 240000) * 240000)
            command = [str(self.executable), "--task", "tts", "--family", "voxcpm2",
                       "--model", str(self.model), "--backend", "cuda",
                       "--session-option", "voxcpm2.mem_saver=true",
                       "--session-option", "voxcpm2.audiovae_tile_frames=128",
                       "--session-option", f"voxcpm2.audiovae_encoder_sample_capacity={capacity}",
                       "--request-option", f"seed={seed}",
                       "--batch-text-dir", str(inputs), "--voice-ref", str(converted),
                       "--out-dir", str(outputs)]
            if transcript:
                # The CLI accepts the reference transcript only as an argument. It describes the
                # owner's reference recording, not customer script text.
                command += ["--reference-text", transcript, "--request-option", "ultimate_clone=true"]
            try:
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired) as error:
                raise AudioError("VoxCPM2 GPU could not start or finish. Check its setup, or use the CPU setting.") from error
            generated = outputs / "section.wav"
            if result.returncode != 0 or not generated.is_file():
                raise AudioError("VoxCPM2 GPU could not finish. Free GPU memory or restart with its CPU setting, then retry.")
            try:
                audio, source_rate = sf.read(generated, dtype="float32")
            except (OSError, RuntimeError) as error:
                raise AudioError("VoxCPM2 GPU returned an unreadable recording. Retry the section.") from error
            if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all() or not audio.any():
                raise AudioError("VoxCPM2 GPU returned unusable audio. Try a shorter section.")
            divisor = gcd(source_rate, RATE)
            audio = resample_poly(audio, RATE // divisor, source_rate // divisor).astype(np.float32)
            if self.watermarker is None:
                self.watermarker = perth.PerthImplicitWatermarker()
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


def trim_edge_silence(samples, rate=RATE, threshold_db=-45.0, window_ms=10,
                      keep_lead_ms=30, keep_tail_ms=80, fade_in_ms=8, fade_out_ms=25):
    """Remove leading and trailing silence from 16-bit PCM samples and fade the new edges.

    Silence is judged per short window relative to the loudest window, so a single click
    does not count as speech. A little silence is kept so breaths and decays are not cut.
    """
    import numpy as np

    audio = np.asarray(samples, dtype=np.int16)
    window = max(1, rate * window_ms // 1000)
    count = audio.size // window
    if count == 0:
        return audio
    blocks = audio[:count * window].astype(np.float64).reshape(count, window)
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    loudest = float(rms.max())
    if loudest <= 0:
        return audio
    loud = rms >= loudest * 10 ** (threshold_db / 20)
    # Require three loud windows in a row (30 ms) so an isolated click is not taken for speech.
    runs = np.convolve(loud.astype(np.int8), np.ones(3, dtype=np.int8), mode="valid") == 3
    starts = np.flatnonzero(runs)
    if starts.size == 0:
        return audio
    voiced = np.array([starts[0], starts[-1] + 2])
    first = max(0, voiced[0] * window - rate * keep_lead_ms // 1000)
    last = min(audio.size, (voiced[-1] + 1) * window + rate * keep_tail_ms // 1000)
    trimmed = audio[first:last].astype(np.float64)
    fade_in = min(trimmed.size, rate * fade_in_ms // 1000)
    fade_out = min(trimmed.size, rate * fade_out_ms // 1000)
    if fade_in:
        trimmed[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)
    if fade_out:
        trimmed[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    return np.round(trimmed).astype(np.int16)


def export_audio(chunks, wav_path, mp3_path, trim=False):
    """Join sections in order. With `trim`, each pause is the full gap between sections."""
    import numpy as np

    with wave.open(str(wav_path), "wb") as master:
        master.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for index, (path, pause_ms) in enumerate(chunks):
            with wave.open(str(balanced_section(path)), "rb") as chunk:
                if (chunk.getnchannels(), chunk.getsampwidth(), chunk.getframerate()) != (1, 2, RATE):
                    raise AudioError("A section has an unexpected audio format. Regenerate that section.")
                frames = chunk.readframes(chunk.getnframes())
            if trim:
                frames = trim_edge_silence(np.frombuffer(frames, dtype="<i2")).astype("<i2").tobytes()
            master.writeframesraw(frames)
            if index + 1 < len(chunks):
                master.writeframesraw(bytes(RATE * 2 * pause_ms // 1000))

    ffmpeg("-n", "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3_path))

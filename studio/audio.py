import os
import subprocess
import wave
from pathlib import Path


MAX_REFERENCE_BYTES = 16 * 1024 * 1024
RATE = 24000


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


def export_audio(chunks, wav_path, mp3_path):
    with wave.open(str(wav_path), "wb") as master:
        master.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for index, (path, pause_ms) in enumerate(chunks):
            with wave.open(str(path), "rb") as chunk:
                if (chunk.getnchannels(), chunk.getsampwidth(), chunk.getframerate()) != (1, 2, RATE):
                    raise AudioError("A section has an unexpected audio format. Regenerate that section.")
                master.writeframesraw(chunk.readframes(chunk.getnframes()))
            if index + 1 < len(chunks):
                master.writeframesraw(bytes(RATE * 2 * pause_ms // 1000))

    ffmpeg("-n", "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", "96k", str(mp3_path))

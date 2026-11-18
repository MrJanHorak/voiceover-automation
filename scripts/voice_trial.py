"""Generate a short, offline Chatterbox Turbo voice-over for evaluation."""

import argparse
import io
import os
import wave
from pathlib import Path
from time import perf_counter


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_DIR / "models" / "chatterbox-turbo"
MAX_SCRIPT_CHARS = 250


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="Your 6 to 30 second PCM WAV")
    script = parser.add_mutually_exclusive_group(required=True)
    script.add_argument("--text", help="One short English passage")
    script.add_argument("--script", type=Path, help="UTF-8 .txt file with one short passage")
    parser.add_argument("--output", type=Path, required=True, help="Path for a new WAV file")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()

    if args.script and args.script.suffix.lower() != ".txt":
        parser.error("--script requires a UTF-8 .txt file.")
    try:
        if args.script:
            with args.script.open(encoding="utf-8-sig") as script_file:
                text = script_file.read(MAX_SCRIPT_CHARS + 1)
                if len(text) > MAX_SCRIPT_CHARS:
                    parser.error(f"Use a short script of up to {MAX_SCRIPT_CHARS} characters.")
                text = text.strip()
        else:
            text = args.text.strip()
        with wave.open(str(args.reference), "rb") as reference:
            reference_seconds = reference.getnframes() / reference.getframerate()
    except (OSError, UnicodeError, wave.Error, EOFError) as error:
        parser.error(str(error) or "The reference must be a readable PCM WAV file.")

    if not 0 < len(text) <= MAX_SCRIPT_CHARS:
        parser.error(f"Use a short script of 1 to {MAX_SCRIPT_CHARS} characters.")
    if not 6 <= reference_seconds <= 30:
        parser.error("Use a PCM WAV with 6 to 30 seconds of your voice.")
    if args.output.suffix.lower() != ".wav" or not args.output.parent.is_dir():
        parser.error("Choose a .wav output path in an existing folder.")
    if args.output.exists():
        parser.error("The output file already exists. Choose a new filename.")
    if not (args.model_dir / "t3_turbo_v1.safetensors").is_file():
        parser.error("Download the Chatterbox Turbo model using the README setup steps.")

    os.environ["HF_HUB_OFFLINE"] = "1"
    import soundfile
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    if args.device == "cuda":
        if not torch.cuda.is_available():
            parser.error("CUDA is unavailable. Use --device cpu or install the CUDA wheels.")
        torch.cuda.reset_peak_memory_stats()

    start = perf_counter()
    model = ChatterboxTurboTTS.from_local(args.model_dir.resolve(), device=args.device)
    print(f"Loaded model on {args.device} in {perf_counter() - start:.1f}s.", flush=True)
    start = perf_counter()
    with torch.inference_mode():
        audio = model.generate(text, audio_prompt_path=str(args.reference)).squeeze(0)
    generation_seconds = perf_counter() - start

    if audio.numel() == 0 or not torch.isfinite(audio).all() or audio.abs().max().item() == 0:
        parser.exit(1, "The model returned unusable audio. Try a different reference or shorter text.\n")

    wav = io.BytesIO()
    soundfile.write(wav, audio.cpu().numpy(), model.sr, format="WAV", subtype="PCM_16")
    try:
        output = args.output.open("xb")
    except OSError as error:
        parser.exit(1, f"Could not create the output WAV. {error}\n")
    try:
        with output:
            output.write(wav.getbuffer())
    except OSError as error:
        args.output.unlink(missing_ok=True)
        parser.exit(1, f"Could not write the output WAV. {error}\n")
    duration = audio.numel() / model.sr
    print(f"Wrote {duration:.1f}s to {args.output} in {generation_seconds:.1f}s.")
    print(f"Real-time factor: {generation_seconds / duration:.2f}x.")
    if args.device == "cuda":
        peak_vram = torch.cuda.max_memory_reserved() / (1024 * 1024)
        print(f"Peak PyTorch CUDA memory reserved: {peak_vram:.0f} MiB.")


if __name__ == "__main__":
    main()

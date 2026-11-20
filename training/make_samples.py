"""Generate comparison clips for each saved LoRA checkpoint, to choose the best one by ear.

For every checkpoint it writes two test sentences without a reference (the adapter alone)
and with your reference clip, plus a baseline from the original model with your reference.
"""

import glob
import json
import os
from pathlib import Path

import soundfile as sf
import torch
from voxcpm import VoxCPM
from voxcpm.model.voxcpm import LoRAConfig

PRETRAINED = "/workspace/VoxCPM2"
REFERENCE = "/workspace/kit/reference.wav"
OUT = Path("/workspace/output/samples")
TEXTS = [
    "Thanks for calling. Our team will be with you in just a moment, so please stay on the line.",
    "Introducing a fresher way to start your morning, with breakfast made from scratch every single day.",
]


def lora_config(checkpoint):
    """Read the adapter settings saved with the checkpoint; the library default (r=8) would not match."""
    path = Path(checkpoint) / "lora_config.json"
    data = json.loads(path.read_text()) if path.is_file() else {}
    data = data.get("lora_config", data)
    fields = LoRAConfig.model_fields
    return LoRAConfig(**{key: value for key, value in data.items() if key in fields})


def render(model, name):
    for index, text in enumerate(TEXTS, 1):
        for label, reference in (("alone", None), ("with-reference", REFERENCE)):
            if reference and not os.path.isfile(reference):
                continue
            torch.manual_seed(35)
            options = {"reference_wav_path": reference} if reference else {}
            audio = model.generate(text=text, **options)
            sf.write(OUT / f"{name}_{label}_{index}.wav", audio, model.tts_model.sample_rate)
            print("wrote", f"{name}_{label}_{index}.wav", flush=True)


OUT.mkdir(parents=True, exist_ok=True)
baseline = VoxCPM.from_pretrained(PRETRAINED, load_denoiser=False, optimize=False, local_files_only=True)
for index, text in enumerate(TEXTS, 1):
    if os.path.isfile(REFERENCE):
        torch.manual_seed(35)
        audio = baseline.generate(text=text, reference_wav_path=REFERENCE)
        sf.write(OUT / f"baseline_with-reference_{index}.wav", audio, baseline.tts_model.sample_rate)
del baseline
torch.cuda.empty_cache()

for checkpoint in sorted(glob.glob("/workspace/output/lora/step_*")):
    model = VoxCPM.from_pretrained(PRETRAINED, load_denoiser=False, optimize=False, local_files_only=True,
                                   lora_config=lora_config(checkpoint), lora_weights_path=checkpoint)
    render(model, Path(checkpoint).name)
    del model
    torch.cuda.empty_cache()

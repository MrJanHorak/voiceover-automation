"""Trained voices: LoRA adapters made by the VoxCPM2 fine-tuning kit (see training/README.md).

Each voice is a folder under data/studio/trained/<name>/ holding the trainer's lora_config.json and
lora_weights.safetensors. The base VoxCPM2 model stays unchanged; the adapter is applied on top of it.
"""
import json
import re
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
REQUIRED = ("lora_config.json", "lora_weights.safetensors")


def read_config(folder):
    """Return the adapter settings saved by the trainer, or None when the folder is not a usable adapter."""
    folder = Path(folder)
    if not all((folder / name).is_file() for name in REQUIRED):
        return None
    try:
        data = json.loads((folder / "lora_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    config = data.get("lora_config", data) if isinstance(data, dict) else None
    if not isinstance(config, dict) or not isinstance(config.get("r"), int) or config["r"] <= 0:
        return None
    return config


def training_step(folder):
    try:
        step = json.loads((Path(folder) / "training_state.json").read_text(encoding="utf-8")).get("step")
    except (OSError, ValueError, AttributeError):
        return None
    return step if isinstance(step, int) else None


def list_trained(root):
    """Usable trained voices, newest folder first."""
    root = Path(root)
    if not root.is_dir():
        return []
    voices = []
    for folder in root.iterdir():
        if folder.is_dir() and NAME.match(folder.name) and read_config(folder) is not None:
            voices.append({"name": folder.name, "path": folder, "step": training_step(folder),
                           "modified": (folder / "lora_weights.safetensors").stat().st_mtime})
    voices.sort(key=lambda voice: (-voice["modified"], voice["name"]))
    return voices


def find_trained(root, name=None):
    """The named trained voice, or the newest one when no name is given; None when there is none."""
    voices = list_trained(root)
    if name is None:
        return voices[0] if voices else None
    return next((voice for voice in voices if voice["name"] == name), None)

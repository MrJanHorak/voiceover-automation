"""Voice catalog helpers: validating consent documents and trained-voice uploads."""
import json
import shutil
import struct
from pathlib import Path
from uuid import uuid4

from .store import StudioError
from .trained import NAME, read_config

MAX_CONSENT_BYTES = 10 * 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024
MAX_WEIGHTS_BYTES = 1024 * 1024 * 1024
CONSENT_TYPES = {b"%PDF-": ".pdf", b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg"}
CONSENT_MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}
SAMPLE_TEXT = "Hello, and thanks for listening. This is how I sound when I read your script."


def consent_extension(contents):
    """The file type of a consent document, judged by its content: PDF, PNG or JPEG."""
    if len(contents) > MAX_CONSENT_BYTES:
        raise StudioError("Choose a consent document under 10 MB.", 413)
    for magic, extension in CONSENT_TYPES.items():
        if contents.startswith(magic):
            return extension
    raise StudioError("Upload the signed consent as a PDF, PNG or JPEG file.")


def copy_limited(upload, destination, limit, message):
    """Stream an upload to disk, refusing files above the limit."""
    written = 0
    with destination.open("wb") as handle:
        while chunk := upload.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                raise StudioError(message, 413)
            handle.write(chunk)
    return written


def check_safetensors(path):
    """A safetensors file starts with an 8-byte little-endian header length and a JSON header."""
    try:
        with path.open("rb") as handle:
            (length,) = struct.unpack("<Q", handle.read(8))
            if not 2 <= length <= 100 * 1024 * 1024:
                raise ValueError
            header = json.loads(handle.read(length))
    except (ValueError, struct.error, UnicodeDecodeError):
        raise StudioError("lora_weights.safetensors is not a valid safetensors file.") from None
    if not isinstance(header, dict) or not any("lora" in key.lower() for key in header):
        raise StudioError("The weights file does not contain LoRA weights.")


def install_trained(root, name, config_upload, weights_upload):
    """Save an uploaded adapter as data/studio/trained/<name>/ after validating both files."""
    name = (name or "").strip()
    if not NAME.match(name):
        raise StudioError("Name the trained voice with letters, numbers, spaces, dots, dashes or underscores.")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.exists():
        raise StudioError("A trained voice with this name already exists. Choose another name.", 409)
    staging = root / f".upload-{uuid4().hex}"
    staging.mkdir()
    try:
        copy_limited(config_upload, staging / "lora_config.json", MAX_CONFIG_BYTES, "lora_config.json is too large.")
        copy_limited(weights_upload, staging / "lora_weights.safetensors", MAX_WEIGHTS_BYTES,
                     "The weights file is larger than 1 GB.")
        if read_config(staging) is None:
            raise StudioError("lora_config.json is not a VoxCPM LoRA configuration from the training kit.")
        check_safetensors(staging / "lora_weights.safetensors")
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target

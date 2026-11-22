"""Runpod Serverless worker: renders one studio section with VoxCPM2 on a GPU.

Input (from studio/remote.py):
    {"text": str, "seed": int, "transcript": str | None,
     "reference": base64 WAV | None, "adapter": {"name": str, "sha256": str} | None}
Output: {"audio": base64 48 kHz WAV (Perth-watermarked), "details": {...}} or {"error": message}.

Trained voices are downloaded once from the studio (VOICEOVER_STUDIO_URL, with VOICEOVER_WORKER_TOKEN)
and cached by their hash, so a warm worker reuses them.
"""
import base64
import binascii
import hashlib
import logging
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # The studio package sits beside worker/.

from studio.audio import AudioError, DirectedEngine  # noqa: E402
from studio.trained import NAME, read_config  # noqa: E402

LOG = logging.getLogger("voiceover-worker")
MODEL_DIR = Path(os.environ.get("VOXCPM2_DIR", "/models/voxcpm2"))
CACHE = Path(os.environ.get("ADAPTER_CACHE", "/tmp/voiceover-adapters"))
MAX_TEXT = 2000
MAX_REFERENCE_BYTES = 8 * 1024 * 1024
FILES = ("lora_config.json", "lora_weights.safetensors")
_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = DirectedEngine(MODEL_DIR, device=os.environ.get("VOXCPM2_DEVICE", "cuda"))
    return _engine


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_adapter(name, expected_hash, opener=urllib.request.urlopen):
    """A local folder with the trained voice, downloaded from the studio unless already cached."""
    if not isinstance(name, str) or not NAME.match(name):
        raise AudioError("The trained voice name is not valid.")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash):
        raise AudioError("The trained voice fingerprint is not valid.")
    folder = CACHE / expected_hash
    if all((folder / filename).is_file() for filename in FILES):
        return folder
    base = os.environ.get("VOICEOVER_STUDIO_URL", "").rstrip("/")
    token = os.environ.get("VOICEOVER_WORKER_TOKEN", "")
    if not base or not token:
        raise AudioError("The GPU worker is missing VOICEOVER_STUDIO_URL or VOICEOVER_WORKER_TOKEN.")
    CACHE.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=CACHE, prefix=".download-"))
    try:
        for filename in FILES:
            request = urllib.request.Request(f"{base}/api/worker/adapters/{urllib.request.quote(name)}/{filename}",
                                             headers={"X-Worker-Token": token})
            with opener(request, timeout=300) as response, (staging / filename).open("wb") as handle:
                shutil.copyfileobj(response, handle, 1024 * 1024)
        if sha256(staging / "lora_weights.safetensors") != expected_hash:
            raise AudioError("The downloaded trained voice does not match. Upload it again on the Voices page.")
        if read_config(staging) is None:
            raise AudioError("The downloaded trained voice has an invalid configuration.")
        staging.rename(folder)
    except AudioError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(staging, ignore_errors=True)
        LOG.error("Trained voice download failed (%s)", type(error).__name__)
        raise AudioError("The GPU worker could not download the trained voice from the studio.") from None
    return folder


def handler(event, render_engine=None, opener=urllib.request.urlopen):
    job = (event or {}).get("input") or {}
    text, seed = job.get("text"), job.get("seed", 35)
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        return {"error": "The section text is missing or too long."}
    if not isinstance(seed, int) or not 0 <= seed <= 4294967295:
        return {"error": "The seed is not valid."}
    transcript = job.get("transcript") or None
    if transcript is not None and (not isinstance(transcript, str) or len(transcript) > 1000):
        return {"error": "The reference transcript is not valid."}
    render_engine = render_engine or engine()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            reference = None
            if job.get("reference"):
                try:
                    audio = base64.b64decode(job["reference"], validate=True)
                except (binascii.Error, ValueError, TypeError):
                    return {"error": "The voice reference could not be read."}
                if len(audio) > MAX_REFERENCE_BYTES or audio[:4] != b"RIFF":
                    return {"error": "The voice reference must be a WAV file under 8 MB."}
                reference = folder / "reference.wav"
                reference.write_bytes(audio)
            adapter = None
            if job.get("adapter"):
                details = job["adapter"]
                if not isinstance(details, dict):
                    return {"error": "The trained voice details are not valid."}
                adapter = fetch_adapter(details.get("name"), details.get("sha256"), opener)
            if transcript and reference is None:
                return {"error": "Maximum resemblance needs the voice reference."}
            output = folder / "section.wav"
            render_engine.generate(text, reference, seed, output, transcript=transcript, adapter=adapter)
            result = {"audio": base64.b64encode(output.read_bytes()).decode()}
            try:
                result["details"] = render_engine.details()
            except Exception:
                result["details"] = {"engine": "VoxCPM2"}
            return result
    except AudioError as error:
        return {"error": str(error)}


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})

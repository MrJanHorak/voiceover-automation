"""Render VoxCPM2 sections on a Runpod Serverless GPU endpoint (the split hosting setup).

The website server sends each section as a job: the text, seed, optional transcript (maximum resemblance),
the reference recording as base64, and the trained voice's name and hash. The worker in worker/ downloads
trained voices it has not cached from the website, renders on the GPU, applies the Perth watermark and
returns a 48 kHz WAV as base64. Script text and the reference are sent to Runpod for rendering.
"""
import base64
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from .audio import AudioError
from .trained import read_config

LOG = logging.getLogger(__name__)
API = "https://api.runpod.ai/v2"
# Runpod accepts /run payloads up to 10 MB; base64 adds a third, so keep references well below.
MAX_REFERENCE_BYTES = 6 * 1024 * 1024
FINISHED = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


class RemoteDirectedEngine:
    """VoxCPM2 on a serverless GPU. Same interface as DirectedEngine, so the studio does not change."""

    needs_exclusive_gpu = False

    def __init__(self, endpoint_id, api_key, timeout=1800, poll_seconds=2.0, api=API,
                 opener=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic):
        self.endpoint_id = (endpoint_id or "").strip()
        self.api_key = (api_key or "").strip()
        self.timeout, self.poll_seconds, self.api = timeout, poll_seconds, api.rstrip("/")
        self.opener, self.sleep, self.clock = opener, sleep, clock
        self.last_details = {}
        self._hashes = {}

    @property
    def available(self):
        return bool(self.endpoint_id and self.api_key)

    def details(self):
        details = self.last_details or {}
        return {"engine": details.get("engine", "VoxCPM2"), "device": "remote GPU",
                "runtime": details.get("runtime"), "model_sha256": details.get("model_sha256")}

    def render(self, text, reference, seed, output, instructions, adapter=None):
        direction = " ".join(instructions.replace("(", " ").replace(")", " ").split())
        self._generate(f"({direction}){text}", reference, seed, output, adapter=adapter)

    def render_matched(self, text, reference, seed, output, transcript):
        self._generate(text, reference, seed, output, transcript=" ".join(transcript.split()))

    def render_trained(self, text, reference, seed, output, adapter):
        self._generate(text, reference, seed, output, adapter=adapter)

    # ---- helpers ----

    def file_hash(self, path):
        path = Path(path)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self._hashes:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            self._hashes[key] = digest.hexdigest()
        return self._hashes[key]

    def request(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"{self.api}/{self.endpoint_id}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener(request, timeout=60) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise AudioError("The rendering service rejected the studio's API key. Check VOICEOVER_RUNPOD_API_KEY.") from None
            if error.code == 404:
                raise AudioError("The rendering endpoint was not found. Check VOICEOVER_RUNPOD_ENDPOINT.") from None
            raise ConnectionError(f"HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise ConnectionError(type(error).__name__) from None

    def payload(self, text, reference, seed, transcript, adapter):
        job = {"text": text, "seed": int(seed), "transcript": transcript or None, "reference": None, "adapter": None}
        if reference is not None:
            audio = Path(reference).read_bytes()
            if len(audio) > MAX_REFERENCE_BYTES:
                raise AudioError("The voice reference is too large to send for rendering. Use a clip of 30 seconds or less.")
            job["reference"] = base64.b64encode(audio).decode()
        if adapter is not None:
            if read_config(adapter) is None:
                raise AudioError("The trained voice files are incomplete. Upload them again on the Voices page.")
            job["adapter"] = {"name": Path(adapter).name,
                              "sha256": self.file_hash(Path(adapter) / "lora_weights.safetensors")}
        return {"input": job}

    def _generate(self, text, reference, seed, output, transcript=None, adapter=None):
        if not self.available:
            raise AudioError("Remote rendering is not configured. Set VOICEOVER_RUNPOD_ENDPOINT and VOICEOVER_RUNPOD_API_KEY.")
        if reference is None and adapter is None:
            raise AudioError("Choose a voice reference or a trained voice.")
        payload = self.payload(text, reference, seed, transcript, adapter)
        try:
            job = self.request("POST", "/run", payload)
        except ConnectionError as error:
            raise AudioError("The rendering service could not be reached. Try again in a minute.") from error
        job_id = job.get("id")
        if not job_id:
            raise AudioError("The rendering service did not accept the section. Try again.")
        result = self.wait(job_id)
        output_data = result.get("output") or {}
        if result.get("status") != "COMPLETED" or "error" in output_data or not output_data.get("audio"):
            message = output_data.get("error") if isinstance(output_data, dict) else None
            LOG.warning("Remote render %s ended as %s", job_id, result.get("status"))
            raise AudioError(message or "The GPU could not render this section. Try again, or shorten it.")
        try:
            audio = base64.b64decode(output_data["audio"], validate=True)
        except (ValueError, TypeError):
            raise AudioError("The rendering service returned unreadable audio. Try again.") from None
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise AudioError("The rendering service returned unreadable audio. Try again.")
        Path(output).write_bytes(audio)
        self.last_details = output_data.get("details") or {}

    def wait(self, job_id):
        """Poll until the job finishes. Includes the cold start of a scaled-down endpoint."""
        deadline = self.clock() + self.timeout
        failures = 0
        while True:
            try:
                result = self.request("GET", f"/status/{job_id}")
                failures = 0
            except ConnectionError:
                failures += 1
                if failures >= 10:
                    raise AudioError("Lost contact with the rendering service. Try again.") from None
                result = {}
            if result.get("status") in FINISHED:
                return result
            if self.clock() >= deadline:
                try:
                    self.request("POST", f"/cancel/{job_id}")
                except (ConnectionError, AudioError):
                    pass
                raise AudioError("Rendering took too long and was stopped. Try again, or shorten the section.")
            self.sleep(self.poll_seconds)


class DisabledEngine:
    """Chatterbox switched off (VOICEOVER_DEVICE=off), for servers without a GPU."""

    def render(self, *args, **kwargs):
        raise AudioError("Chatterbox is not available on this server. Choose a voice that uses VoxCPM2.")

    def details(self):
        return {"engine": "Chatterbox Turbo (off)", "device": "off"}

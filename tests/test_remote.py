"""Split hosting: the studio's remote engine against a fake Runpod API, and the GPU worker's handler
against a fake engine. Nothing here contacts Runpod or loads a model. Synthetic audio only."""

import base64
import hashlib
import io
import json
import os
import sys
import unittest
import urllib.error
import wave
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from studio.audio import AudioError
from studio.remote import RemoteDirectedEngine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
import handler as worker  # noqa: E402

CONFIG = json.dumps({"lora_config": {"enable_lm": True, "enable_dit": True, "r": 32, "alpha": 32}}).encode()


def wav_bytes(frames=4800, rate=48000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x10\x00" * frames)
    return buffer.getvalue()


def make_adapter(folder, weights=b"weights"):
    folder.mkdir(parents=True)
    (folder / "lora_config.json").write_bytes(CONFIG)
    (folder / "lora_weights.safetensors").write_bytes(weights)
    return folder


class FakeRunpod:
    """Answers /run, /status and /cancel like the Runpod queue API."""

    def __init__(self, statuses=("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), output=None, http_error=None):
        self.statuses = list(statuses)
        self.output = output if output is not None else {
            "audio": base64.b64encode(wav_bytes()).decode(), "details": {"engine": "VoxCPM2", "runtime": "2.0.3"}}
        self.http_error = http_error
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None,
                              dict(request.header_items())))
        if self.http_error:
            raise urllib.error.HTTPError(request.full_url, self.http_error, "error", {}, None)
        if request.full_url.endswith("/run"):
            body = {"id": "job-1", "status": "IN_QUEUE"}
        elif "/status/" in request.full_url:
            status = self.statuses.pop(0) if self.statuses else "IN_PROGRESS"
            body = {"id": "job-1", "status": status}
            if status == "COMPLETED":
                body["output"] = self.output
        else:
            body = {"id": "job-1", "status": "CANCELLED"}

        @contextmanager
        def response():
            yield io.BytesIO(json.dumps(body).encode())
        return response()


class RemoteEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference.wav"
        self.reference.write_bytes(wav_bytes(24000, 24000))

    def engine(self, api, **options):
        clock = iter(range(0, 100000, 5))
        return RemoteDirectedEngine("endpoint", "key", opener=api, sleep=lambda _: None, clock=lambda: next(clock), **options)

    def test_trained_voice_with_reference(self):
        api = FakeRunpod()
        adapter = make_adapter(self.root / "speaker-step150")
        output = self.root / "out.wav"
        engine = self.engine(api)
        engine.render_trained("Private script.", self.reference, 42, output, adapter)
        self.assertEqual(output.read_bytes()[:4], b"RIFF")
        method, url, body, headers = api.requests[0]
        self.assertEqual((method, url), ("POST", "https://api.runpod.ai/v2/endpoint/run"))
        self.assertEqual(headers["Authorization"], "Bearer key")
        job = body["input"]
        self.assertEqual((job["text"], job["seed"], job["transcript"]), ("Private script.", 42, None))
        self.assertEqual(base64.b64decode(job["reference"]), self.reference.read_bytes())
        self.assertEqual(job["adapter"], {"name": "speaker-step150", "sha256": hashlib.sha256(b"weights").hexdigest()})
        self.assertEqual(engine.details()["device"], "remote GPU")
        self.assertEqual(engine.details()["runtime"], "2.0.3")
        self.assertEqual(len([r for r in api.requests if "/status/" in r[1]]), 3)

    def test_matched_and_directed_payloads(self):
        api = FakeRunpod(statuses=["COMPLETED", "COMPLETED"])
        engine = self.engine(api)
        engine.render_matched("Hello.", self.reference, 7, self.root / "a.wav", "  exact   words  ")
        engine.render("Hello.", self.reference, 7, self.root / "b.wav", "calm (slow)")
        runs = [r[2]["input"] for r in api.requests if r[1].endswith("/run")]
        self.assertEqual(runs[0]["transcript"], "exact words")
        self.assertEqual(runs[1]["text"], "(calm slow)Hello.")
        self.assertIsNone(runs[1]["adapter"])

    def test_failures_become_clear_errors(self):
        cases = [
            (FakeRunpod(statuses=["FAILED"]), "could not render"),
            (FakeRunpod(output={"error": "The section text is missing or too long."}), "too long"),
            (FakeRunpod(output={"audio": base64.b64encode(b"not audio").decode()}), "unreadable"),
            (FakeRunpod(http_error=401), "API key"),
            (FakeRunpod(http_error=404), "endpoint was not found"),
        ]
        for api, message in cases:
            with self.subTest(message=message), self.assertRaises(AudioError) as error:
                self.engine(api).render_matched("Hi.", self.reference, 1, self.root / "x.wav", "words")
            self.assertIn(message, str(error.exception))

    def test_timeout_cancels_the_job(self):
        api = FakeRunpod(statuses=[])
        with self.assertRaises(AudioError) as error:
            self.engine(api, timeout=20).render_matched("Hi.", self.reference, 1, self.root / "x.wav", "words")
        self.assertIn("too long", str(error.exception))
        self.assertTrue(api.requests[-1][1].endswith("/cancel/job-1"))

    def test_configuration_and_limits(self):
        self.assertFalse(RemoteDirectedEngine("", "key").available)
        with self.assertRaises(AudioError):
            RemoteDirectedEngine("", "").render_matched("Hi.", self.reference, 1, self.root / "x.wav", "words")
        big = self.root / "big.wav"
        big.write_bytes(b"RIFF" + b"\x00" * (7 * 1024 * 1024))
        with self.assertRaises(AudioError):
            self.engine(FakeRunpod()).render_matched("Hi.", big, 1, self.root / "x.wav", "words")


class FakeEngine:
    def __init__(self):
        self.calls = []

    def generate(self, text, reference, seed, output, transcript=None, adapter=None):
        self.calls.append((text, reference is not None and reference.read_bytes()[:4], seed, transcript,
                           adapter and sorted(path.name for path in Path(adapter).iterdir())))
        if reference is None and adapter is None:
            raise AudioError("Choose a voice reference or a trained voice.")
        Path(output).write_bytes(wav_bytes())

    def details(self):
        return {"engine": "VoxCPM2", "device": "cuda"}


class WorkerHandlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(worker, "CACHE", self.root / "cache")
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"VOICEOVER_STUDIO_URL": "https://studio.example", "VOICEOVER_WORKER_TOKEN": "t" * 40})
        env.start()
        self.addCleanup(env.stop)
        self.downloads = []

    def studio(self, files):
        def opener(request, timeout=None):
            self.downloads.append((request.full_url, request.get_header("X-worker-token")))
            name = request.full_url.rsplit("/", 1)[1]

            @contextmanager
            def response():
                yield io.BytesIO(files[name])
            return response()
        return opener

    def test_renders_with_reference_and_downloads_the_adapter_once(self):
        engine = FakeEngine()
        weights = b"trained weights"
        files = {"lora_config.json": CONFIG, "lora_weights.safetensors": weights}
        event = {"input": {"text": "Hello.", "seed": 42, "reference": base64.b64encode(wav_bytes()).decode(),
                           "adapter": {"name": "speaker-step150", "sha256": hashlib.sha256(weights).hexdigest()}}}
        for _ in range(2):
            result = worker.handler(event, engine, self.studio(files))
            self.assertNotIn("error", result)
            self.assertEqual(base64.b64decode(result["audio"])[:4], b"RIFF")
        self.assertEqual(len(self.downloads), 2)  # Two files, downloaded once; the second job used the cache.
        self.assertEqual(self.downloads[0], ("https://studio.example/api/worker/adapters/speaker-step150/lora_config.json", "t" * 40))
        self.assertEqual(engine.calls[0][:4], ("Hello.", b"RIFF", 42, None))
        self.assertEqual(engine.calls[0][4], ["lora_config.json", "lora_weights.safetensors"])
        self.assertEqual(result["details"]["device"], "cuda")

    def test_rejects_bad_input_and_tampered_adapters(self):
        engine = FakeEngine()
        good = {"text": "Hi.", "seed": 1}
        bad_inputs = [
            {"text": ""}, {"text": "x" * 3000}, {"text": "Hi.", "seed": -1},
            {**good, "reference": "not base64!"}, {**good, "reference": base64.b64encode(b"MP3 data").decode()},
            {**good, "transcript": "words"},
            {**good, "adapter": {"name": "../etc", "sha256": "0" * 64}},
            {**good, "adapter": {"name": "ok", "sha256": "short"}},
            {**good, "adapter": {"name": "ok", "sha256": "0" * 64}},  # Hash mismatch after download.
            good,  # No reference and no adapter: the engine refuses.
        ]
        files = {"lora_config.json": CONFIG, "lora_weights.safetensors": b"other"}
        for event in bad_inputs:
            with self.subTest(event=str(event)[:60]):
                self.assertIn("error", worker.handler({"input": event}, engine, self.studio(files)))
        self.assertEqual([path.name for path in (self.root / "cache").iterdir()], [])  # Nothing half-downloaded kept.
        with patch.dict(os.environ, {"VOICEOVER_WORKER_TOKEN": ""}):
            result = worker.handler({"input": {**good, "adapter": {"name": "ok", "sha256": "1" * 64}}}, engine,
                                    self.studio(files))
            self.assertIn("VOICEOVER_WORKER_TOKEN", result["error"])


class WorkerRouteTests(unittest.TestCase):
    """Needs the studio environment (FastAPI)."""

    def test_adapter_download_needs_the_worker_token(self):
        from fastapi.testclient import TestClient

        from studio.app import create_app
        try:
            from test_takes import FakeEngine as Turbo
        except ImportError:
            from tests.test_takes import FakeEngine as Turbo

        token = "w" * 40
        with TemporaryDirectory(dir=".tools") as temporary, patch.dict(os.environ, {"VOICEOVER_WORKER_TOKEN": token}):
            root = Path(temporary)
            make_adapter(root / "studio" / "trained" / "speaker-step150", b"weights")
            with TestClient(create_app(root, Turbo(), auth=True), base_url="http://localhost") as client:
                url = "/api/worker/adapters/speaker-step150/lora_weights.safetensors"
                self.assertEqual(client.get(url).status_code, 404)
                self.assertEqual(client.get(url, headers={"X-Worker-Token": "wrong" * 8}).status_code, 404)
                response = client.get(url, headers={"X-Worker-Token": token})
                self.assertEqual((response.status_code, response.content), (200, b"weights"))
                self.assertEqual(client.get("/api/worker/adapters/missing/lora_config.json",
                                            headers={"X-Worker-Token": token}).status_code, 404)
                self.assertEqual(client.get("/api/worker/adapters/speaker-step150/studio.sqlite3",
                                            headers={"X-Worker-Token": token}).status_code, 422)


if __name__ == "__main__":
    unittest.main()

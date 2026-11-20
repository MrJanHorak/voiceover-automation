"""Trained voices (LoRA adapters): discovery, validation, routing, the GPU command and blind comparisons.

Store and discovery tests use only the standard library. Routing, GPU and API tests need the studio
environment (FastAPI, soundfile, perth). No test uses the owner's voice or trained files.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from studio.store import DIRECTED_VERSION, ENGINE_VERSION, Store, StudioError
from studio.trained import find_trained, list_trained, read_config

CONFIG = {"base_model": "/workspace/VoxCPM2/", "lora_config": {"enable_lm": True, "enable_dit": True, "r": 32, "alpha": 32}}


def make_adapter(folder, step=150, config=CONFIG, weights=True):
    folder.mkdir(parents=True)
    (folder / "lora_config.json").write_text(json.dumps(config), encoding="utf-8")
    if weights:
        (folder / "lora_weights.safetensors").write_bytes(b"fake weights")
    (folder / "training_state.json").write_text(json.dumps({"step": step}), encoding="utf-8")
    return folder


class DiscoveryTests(unittest.TestCase):
    def test_only_complete_adapters_are_listed(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            make_adapter(root / "step150")
            make_adapter(root / "no-weights", weights=False)
            make_adapter(root / "bad-config", config={"lora_config": {"r": "32"}})
            make_adapter(root / ".hidden")
            (root / "loose-file.json").write_text("{}", encoding="utf-8")
            voices = list_trained(root)
            self.assertEqual([voice["name"] for voice in voices], ["step150"])
            self.assertEqual(voices[0]["step"], 150)
            self.assertEqual(read_config(root / "step150")["r"], 32)
            self.assertEqual(find_trained(root)["name"], "step150")
            self.assertEqual(find_trained(root, "step150")["name"], "step150")
            self.assertIsNone(find_trained(root, "no-weights"))
            self.assertEqual(list_trained(root / "missing"), [])


class TrainedStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.initialize()
        self.store.add_voice("voice", "Test", 7.0)

    def test_validation(self):
        with self.assertRaises(StudioError) as missing:
            self.store.create_job("T", "Hello.", voice_match="trained", directed_available=True)
        self.assertEqual(missing.exception.status, 409)
        with self.assertRaises(StudioError) as unavailable:
            self.store.create_job("T", "Hello.", voice_match="trained", trained_voice="step150")
        self.assertEqual(unavailable.exception.status, 409)
        with self.assertRaises(StudioError):
            self.store.create_job("T", "Hello [laugh].", voice_match="trained_reference",
                                  directed_available=True, trained_voice="step150")

    def test_job_records_trained_voice(self):
        # No reference transcript is needed, and a delivery style still uses the trained voice.
        job_id = self.store.create_job("T", "a\n\nb", prepared_sections=[
            {"text": "Plain line.", "instructions": "", "voice_match": "trained"},
            {"text": "Calm line.", "instructions": "Calm", "voice_match": "trained_reference"},
        ], directed_available=True, trained_voice="step150")
        job = self.store.job(job_id)
        self.assertEqual((job["trained_voice"], job["engine"]), ("step150", DIRECTED_VERSION))
        self.assertEqual([section["seed"] for section in job["sections"]], [35, 35])
        plain = self.store.job(self.store.create_job("T", "Hello.", trained_voice="step150"))
        self.assertEqual((plain["trained_voice"], plain["engine"]), (None, ENGINE_VERSION))

    def test_regenerate_switches_to_trained_and_keeps_its_voice(self):
        job_id = self.store.create_job("T", "Hello there.")
        self.store.finish_section(job_id, 1, "first.wav", {"engine": "Chatterbox Turbo"}, 35)
        self.store.finish_job(job_id, "export")
        with self.assertRaises(StudioError):
            self.store.regenerate(job_id, 1, "Hello there.", "", True, None, "trained")
        self.store.regenerate(job_id, 1, "Hello there.", "", True, None, "trained", "step150")
        self.assertEqual(self.store.job(job_id)["trained_voice"], "step150")
        self.store.finish_section(job_id, 1, "second.wav", {}, 1)
        self.store.finish_job(job_id, "export2")
        # A recording keeps the trained voice it was made with, even after a newer one is added.
        self.store.regenerate(job_id, 1, "Hello again.", "", True, None, "trained", "step200")
        self.assertEqual(self.store.job(job_id)["trained_voice"], "step150")

    def test_blind_comparisons_hide_engines_until_a_choice(self):
        prepared = [{"text": "Same line.", "instructions": "", "voice_match": match} for match in ("trained", "standard")]
        job_id = self.store.create_job("Blind comparison", "Same line.\n\nSame line.", "", prepared,
                                       directed_available=True, trained_voice="step150", blind=True)
        self.assertEqual(self.store.state()["jobs"], [])
        [item] = self.store.comparisons()
        self.assertEqual((item["text"], item["ready"], item["choice"]), ("Same line.", False, None))
        self.assertNotIn("engines", item)
        with self.assertRaises(StudioError) as early:
            self.store.choose_blind(job_id, "A")
        self.assertEqual(early.exception.status, 409)
        for number in (1, 2):
            self.store.finish_section(job_id, number, f"{number}.wav", {}, 35)
        self.store.finish_job(job_id, "export")
        self.assertTrue(self.store.comparisons()[0]["ready"])
        with self.assertRaises(StudioError):
            self.store.choose_blind(job_id, "C")
        self.store.choose_blind(job_id, "A")
        [item] = self.store.comparisons()
        self.assertEqual((item["choice"], item["engines"]), ("A", {"A": "trained", "B": "standard"}))
        with self.assertRaises(StudioError):
            self.store.choose_blind(job_id, "B")
        normal = self.store.create_job("T", "Hello.")
        with self.assertRaises(StudioError) as not_blind:
            self.store.choose_blind(normal, "A")
        self.assertEqual(not_blind.exception.status, 404)


class TrainedRoutingTests(unittest.TestCase):
    """Needs the studio environment (FastAPI, soundfile)."""

    def run_job(self, sections, trained_voice="step150", with_adapter=True):
        from unittest.mock import patch

        from studio.app import render_job

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            if with_adapter:
                make_adapter(root / "trained" / "step150")
            calls, finished = [], {}

            class FakeStore:
                recordings = root
                voices = root

                def __init__(self):
                    self.root = root

                def job(self, job_id):
                    return {"voice_id": "voice", "voice_transcript": "", "trim_edges": 1,
                            "trained_voice": trained_voice, "sections": [
                                {"number": number, "audio": None, "pending": True, "text": text, "seed": 35,
                                 "instructions": instructions, "voice_match": match, "turbo_settings": None,
                                 "pause_ms": 0}
                                for number, (text, instructions, match) in enumerate(sections, 1)]}

                def finish_section(self, job_id, number, filename, details, seed, spoken=None):
                    finished[number] = details

                def finish_job(self, job_id, export, loudness=None):
                    pass

            class Turbo:
                def render(self, text, reference, seed, output):
                    calls.append(("turbo", text))

            class Directed:
                def render(self, text, reference, seed, output, instructions, adapter=None):
                    calls.append(("directed", text, reference is not None, instructions, Path(adapter).name if adapter else None))

                def render_matched(self, text, reference, seed, output, transcript):
                    calls.append(("matched", text))

                def render_trained(self, text, reference, seed, output, adapter):
                    calls.append(("trained", text, reference is not None, Path(adapter).name))

                def details(self):
                    return {"engine": "VoxCPM2"}

            with patch("studio.app.export_audio"):
                render_job(FakeStore(), Turbo(), Directed(), Event(), "job")
            return calls, finished

    def test_trained_sections_use_the_adapter(self):
        calls, finished = self.run_job([("Alone.", "", "trained"), ("With clip.", "", "trained_reference"),
                                        ("Calm.", "Calm", "trained"), ("Fast.", "", "standard")])
        self.assertEqual(calls, [("turbo", "Fast."), ("trained", "Alone.", False, "step150"),
                                 ("trained", "With clip.", True, "step150"),
                                 ("directed", "Calm.", False, "Calm", "step150")])
        self.assertEqual(finished[1]["engine"], "VoxCPM2 · trained voice step150")
        self.assertEqual(finished[2]["engine"], "VoxCPM2 · trained voice step150 + reference")

    def test_missing_adapter_fails_with_a_clear_message(self):
        from studio.audio import AudioError

        with self.assertRaises(AudioError) as missing:
            self.run_job([("Alone.", "", "trained")], with_adapter=False)
        self.assertIn("step150", str(missing.exception))

    def test_gpu_command_loads_the_adapter(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        import numpy as np
        import perth  # noqa: F401  Load before mocking subprocess.
        import soundfile as sf
        from studio.audio import RATE, CudaDirectedEngine

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cli").touch()
            (root / "model.gguf").touch()
            adapter = make_adapter(root / "step150")
            reference = root / "reference.wav"
            sf.write(reference, np.ones(RATE, dtype=np.float32) * 0.1, RATE)
            engine = CudaDirectedEngine(root / "cli", root / "model.gguf")
            engine.watermarker = SimpleNamespace(apply_watermark=lambda audio, sample_rate: audio)
            calls = []

            def fake_cli(command, **kwargs):
                calls.append(command)
                outputs = Path(command[command.index("--out-dir") + 1])
                sf.write(outputs / "section.wav", np.ones(4800, dtype=np.float32) * 0.1, 48000)
                return SimpleNamespace(returncode=0)

            def fake_ffmpeg(*args):
                sf.write(args[-1], np.ones(48000, dtype=np.float32) * 0.1, 48000)

            with patch("studio.audio.ffmpeg", side_effect=fake_ffmpeg), \
                 patch("studio.audio.subprocess.run", side_effect=fake_cli):
                engine.render_trained("Alone.", None, 42, root / "alone.wav", adapter)
                engine.render_trained("With clip.", reference, 42, root / "clip.wav", adapter)
            alone, clip = calls
            self.assertIn(f"voxcpm2.lora={adapter.resolve()}", alone)
            self.assertNotIn("--voice-ref", alone)
            self.assertIn("--voice-ref", clip)
            self.assertIn(f"voxcpm2.lora={adapter.resolve()}", clip)
            self.assertNotIn("ultimate_clone=true", clip)


class ComparisonApiTests(unittest.TestCase):
    """Needs the studio environment (FastAPI, ffmpeg)."""

    def test_blind_pair_renders_both_engines(self):
        from fastapi.testclient import TestClient

        from studio.app import create_app
        try:
            from test_takes import FakeEngine, wait_for, write_wav
        except ImportError:
            from tests.test_takes import FakeEngine, wait_for, write_wav

        class FakeDirected:
            available = True

            def __init__(self):
                self.calls = []

            def render(self, text, reference, seed, output, instructions, adapter=None):
                write_wav(output, 330)

            def render_matched(self, text, reference, seed, output, transcript):
                write_wav(output, 330)

            def render_trained(self, text, reference, seed, output, adapter):
                self.calls.append(Path(adapter).name)
                write_wav(output, 440)

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            write_wav(root / "reference.wav", seconds=7)
            make_adapter(root / "studio" / "trained" / "step150")
            directed = FakeDirected()
            headers = {"X-Studio-Request": "1"}
            with TestClient(create_app(root, FakeEngine(), directed), base_url="http://localhost") as client:
                same = client.post("/api/comparisons", headers=headers,
                                   json={"text": "Same line.", "baseline": "trained", "contender": "trained"})
                self.assertEqual(same.status_code, 400)
                response = client.post("/api/comparisons", headers=headers,
                                       json={"text": "Same line.", "baseline": "standard", "contender": "trained"})
                self.assertEqual(response.status_code, 202)
                job_id = response.json()["id"]
                wait_for(client, f"/api/jobs/{job_id}", "ready")
                data = client.get("/api/comparisons").json()
                self.assertEqual(data["trained_voice"], {"name": "step150", "step": 150})
                [item] = data["comparisons"]
                self.assertTrue(item["ready"])
                self.assertNotIn("engines", item)
                self.assertEqual(client.get("/api/state").json()["jobs"], [])
                self.assertEqual(directed.calls, ["step150"])
                chosen = client.put(f"/api/comparisons/{job_id}", headers=headers, json={"choice": "B"})
                self.assertEqual(chosen.status_code, 200)
                engines = chosen.json()["comparisons"][0]["engines"]
                self.assertEqual(sorted(engines.values()), ["standard", "trained"])


if __name__ == "__main__":
    unittest.main()

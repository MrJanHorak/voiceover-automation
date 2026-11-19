import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import torch
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.audio import TurboEngine
from studio.settings import TurboSettings
from studio.store import Store
from test_takes import FakeEngine, wait_for, write_wav


class SettingsEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.settings_seen = []

    def render(self, text, reference, seed, output, settings=None):
        self.settings_seen.append((seed, settings))
        super().render(text, reference, seed, output)


class TurboSettingsTests(unittest.TestCase):
    def test_chatterbox_seed_does_not_change_directed_sections(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            store = Store(Path(temporary))
            store.initialize()
            store.add_voice("voice", "Reference", 10)
            settings = TurboSettings(seed=123).model_dump()
            job = store.create_job("Mixed", "Hello.\n\nWorld.", prepared_sections=[
                {"text": "Hello.", "instructions": ""},
                {"text": "World.", "instructions": "Calm"},
            ], turbo_settings=settings)
            sections = store.job(job)["sections"]
            self.assertEqual([section["seed"] for section in sections], [123, 35])
            store.finish_job(job, "export")
            with patch("studio.store.secrets.randbelow", return_value=456):
                store.regenerate(job, 2, "World.", "Calm", directed_available=True, turbo_settings=settings)
            self.assertEqual(store.job(job)["sections"][1]["seed"], 457)

    def test_controls_reach_engine_and_survive_take_restoration(self):
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            write_wav(root / "reference.wav", seconds=7)
            engine = SettingsEngine()
            headers = {"X-Studio-Request": "1"}
            settings = {"seed": 123, "temperature": 1.05, "top_p": 0.7, "top_k": 45,
                        "repetition_penalty": 1.4, "normalize_reference": False}
            with TestClient(create_app(root, engine), base_url="http://localhost") as client:
                response = client.post("/api/jobs", headers=headers,
                                       json={"script": "First version.", "turbo_settings": settings})
                self.assertEqual(response.status_code, 202, response.text)
                route = "/api/jobs/" + response.json()["id"]
                first = wait_for(client, route, "ready")
                self.assertEqual(engine.settings_seen[0], (123, settings))
                first_take = first["sections"][0]["takes"][0]
                self.assertEqual(first_take["turbo_settings"], settings)
                self.assertEqual(first_take["seed"], 123)
                second_settings = {**settings, "seed": 0, "temperature": 0.6}
                result = client.post(route + "/sections/1", headers=headers,
                                     json={"text": "Second version.", "turbo_settings": second_settings})
                self.assertEqual(result.status_code, 202)
                second = wait_for(client, route, "ready")
                self.assertGreater(engine.settings_seen[1][0], 0)
                self.assertEqual(engine.settings_seen[1][1], second_settings)
                self.assertEqual(second["sections"][0]["takes"][0]["turbo_settings"], second_settings)
                client.post(route + f"/sections/1/takes/{first_take['id']}/restore", headers=headers)
                restored = wait_for(client, route, "ready")
                self.assertEqual(restored["sections"][0]["turbo_settings"], settings)
                self.assertEqual(restored["sections"][0]["generation"]["seed"], 123)
                for invalid in ({"top_p": 0}, {"temperature": 2.1}, {"top_k": -1},
                                {"seed": -1}, {"normalize_reference": "yes"}, {"min_p": 0.2}):
                    response = client.post("/api/jobs", headers=headers,
                                           json={"script": "Test.", "turbo_settings": {**settings, **invalid}})
                    self.assertEqual(response.status_code, 422, invalid)

    def test_reference_cache_changes_when_normalization_changes(self):
        class Model:
            sr = 24000

            def __init__(self):
                self.prepared = []
                self.generated = []

            def prepare_conditionals(self, reference, norm_loudness=True):
                self.prepared.append((reference, norm_loudness))

            def generate(self, text, **options):
                self.generated.append(options)
                return torch.ones((1, 2400)) * 0.1

        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            model = Model()
            engine = TurboEngine(root, "cpu")
            engine.model = model
            settings = TurboSettings(normalize_reference=False, top_k=0, temperature=1.1).model_dump()
            reference = root / "reference.wav"
            engine.render("Hello.", reference, 12, root / "one.wav", settings)
            engine.render("Hello.", reference, 12, root / "two.wav", settings)
            engine.render("Hello.", reference, 12, root / "three.wav", {**settings, "normalize_reference": True})
            self.assertEqual(model.prepared, [(str(reference), False), (str(reference), True)])
            self.assertEqual(model.generated[0], {"temperature": 1.1, "top_p": 0.95, "top_k": 0, "repetition_penalty": 1.2})


if __name__ == "__main__":
    unittest.main()

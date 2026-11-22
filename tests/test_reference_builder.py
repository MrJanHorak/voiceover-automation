import io
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.audio import RATE


def reference(path, sample):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * (RATE * 8))


class ReferenceBuilderTests(unittest.TestCase):
    def test_preview_save_and_validate_excerpts(self):
        with TemporaryDirectory(dir=".tools") as folder:
            app = create_app(Path(folder), engine=object(), auth=False)
            with TestClient(app, base_url="http://localhost") as client:
                # Populate the same private store that the application opened.
                from studio.store import Store
                store = Store(Path(folder) / "studio")
                ids = ["1" * 32, "2" * 32]
                for voice_id, sample in zip(ids, [1000, -2000]):
                    reference(store.voices / f"{voice_id}.wav", sample)
                    store.add_voice(voice_id, voice_id, 8)
                originals = [(store.voices / f"{voice_id}.wav").read_bytes() for voice_id in ids]
                data = {"name": "Combined", "excerpts": [
                    {"voice_id": ids[1], "start": 1, "end": 4},
                    {"voice_id": ids[0], "start": 2, "end": 5},
                ]}
                headers = {"X-Studio-Request": "1"}
                preview = client.post("/api/voice/combine/preview", json=data, headers=headers)
                self.assertEqual(preview.status_code, 200, preview.text if preview.status_code != 200 else "")
                with wave.open(io.BytesIO(preview.content), "rb") as audio:
                    self.assertEqual(audio.getnframes(), 6 * RATE)
                    self.assertEqual(audio.readframes(1), (-2000).to_bytes(2, "little", signed=True))
                    # The join at three seconds fades in over 15 ms so it does not click.
                    audio.setpos(3 * RATE)
                    self.assertEqual(audio.readframes(1), (0).to_bytes(2, "little", signed=True))
                    audio.setpos(3 * RATE + RATE // 10)
                    self.assertEqual(audio.readframes(1), (1000).to_bytes(2, "little", signed=True))
                saved = client.post("/api/voice/combine", json=data, headers=headers)
                self.assertEqual(saved.status_code, 201)
                self.assertEqual((store.voices / f"{saved.json()['id']}.wav").read_bytes(), preview.content)
                self.assertEqual(originals, [(store.voices / f"{voice_id}.wav").read_bytes() for voice_id in ids])
                self.assertEqual(store.active_voice()["id"], saved.json()["id"])
                for bad in [
                    [{"voice_id": ids[0], "start": 0, "end": 9}],
                    [{"voice_id": ids[0], "start": 3, "end": 2}],
                    [{"voice_id": ids[0], "start": 0, "end": 2}],
                    [{"voice_id": "3" * 32, "start": 0, "end": 7}],
                ]:
                    self.assertGreaterEqual(client.post("/api/voice/combine", json={"excerpts": bad}, headers=headers).status_code, 400)
                self.assertEqual(len(store.state()["voices"]), 3)
                self.assertEqual(client.post("/api/voice/combine", json=data).status_code, 403)


if __name__ == "__main__":
    unittest.main()

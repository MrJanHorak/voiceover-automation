import io
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.store import PARAGRAPH_PAUSE_MS
from test_takes import FakeEngine, wait_for, write_wav


class LongWorkflowTests(unittest.TestCase):
    def test_multi_section_exports_and_failure_preserves_downloads(self):
        paragraphs = [
            "First: welcome to the annual community showcase. Please find your seat and enjoy the presentation.",
            "Second: our volunteers prepared the exhibits throughout the spring. Their work connects neighbors and celebrates local creativity.",
            "Third: the program continues after a short break. Refreshments are available in the lobby for everyone attending.",
            "Last: thank you for spending the evening with us. We hope to see you again at the next event.",
        ]
        script = "\n\n".join(paragraphs)
        rate = 48000  # New recordings use a 48 kHz master; the fake sections are 24 kHz.
        pause = rate * PARAGRAPH_PAUSE_MS // 1000
        headers = {"X-Studio-Request": "1"}
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            write_wav(root / "reference.wav", seconds=7)
            engine = FakeEngine()
            with TestClient(create_app(root, engine), base_url="http://localhost") as client:
                response = client.post("/api/jobs", json={"script": script}, headers=headers)
                self.assertEqual(response.status_code, 202, response.text)
                route = "/api/jobs/" + response.json()["id"]
                ready = wait_for(client, route, "ready")
                self.assertEqual([section["text"] for section in ready["sections"]], paragraphs)
                self.assertEqual(engine.calls, len(paragraphs))
                downloads = {kind: client.get(route + "/export/" + kind).content for kind in ("wav", "mp3")}
                self.assertGreater(len(downloads["mp3"]), 1000)
                with wave.open(io.BytesIO(downloads["wav"]), "rb") as audio:
                    self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, rate))
                    self.assertAlmostEqual(audio.getnframes(), 4 * rate + 3 * pause, delta=rate // 100)
                    # Each synthetic section has a different frequency; confirm the export keeps their order.
                    for index, frequency in enumerate((280, 340, 400, 460)):
                        audio.setpos(index * (rate + pause))
                        samples = audio.readframes(rate)
                        import struct
                        values = struct.unpack(f"<{rate}h", samples)
                        crossings = sum(a <= 0 < b for a, b in zip(values, values[1:]))
                        self.assertAlmostEqual(crossings, frequency, delta=2)
                audio_before = [section["audio"] for section in ready["sections"]]
                with patch("studio.app.export_audio", side_effect=RuntimeError("simulated export failure")):
                    response = client.post(route + "/sections/2", json={"text": "A revised second section."}, headers=headers)
                    self.assertEqual(response.status_code, 202)
                    failed = wait_for(client, route, "failed")
                self.assertEqual(failed["export"], ready["export"])
                self.assertEqual([failed["sections"][i]["audio"] for i in (0, 2, 3)],
                                 [audio_before[i] for i in (0, 2, 3)])
                for kind in downloads:
                    self.assertEqual(client.get(route + "/export/" + kind).content, downloads[kind])
                self.assertEqual(client.post(route + "/retry", headers=headers).status_code, 202)
                retried = wait_for(client, route, "ready")
                self.assertEqual(engine.calls, 5)
                self.assertEqual(retried["sections"][1]["text"], "A revised second section.")


if __name__ == "__main__":
    unittest.main()

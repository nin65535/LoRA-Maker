import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.project_service import ProjectService


class MovieApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.client = TestClient(create_app(ProjectService(self.base / "settings")))
        self.client.__enter__()
        self.root = self.base / "project"
        self.client.post("/api/projects/create", json={"rootPath": str(self.root), "name": "Test", "datasets": [
            {"key": "face", "name": "顔", "repeats": 10, "triggerTags": [], "removedTags": []}
        ]})
        self.source = self.root / "01_素材画像/face/source.png"
        self.source.write_bytes(b"image")
        self.output = self.base / "comfy-video-only"
        self.output.mkdir()
        settings = self.client.get("/api/projects/settings").json()
        settings["comfyuiMovieOutputPath"] = str(self.output)
        self.client.put("/api/projects/settings", json=settings)

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def wait(self, count: int = 1) -> list[dict]:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            jobs = [item for item in self.client.get("/api/jobs").json() if item["type"] == "movie-generation"]
            if len(jobs) >= count and all(item["status"] not in ("queued", "running") for item in jobs[:count]):
                return jobs
            time.sleep(.02)
        self.fail("movie jobs did not settle")

    def test_lists_sources_and_presets(self) -> None:
        response = self.client.get("/api/movies")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["images"][0]["relativePath"], "source.png")
        self.assertTrue(response.json()["presets"])

    def test_generates_serial_number_and_cleans_temporary_output(self) -> None:
        videos = self.root / "02_動画/face"
        (videos / "walk_004.mp4").write_bytes(b"old")

        def generate(*_) -> None:
            (self.output / "comfy-result.mp4").write_bytes(b"generated-video")

        with patch("backend.app.services.movie_service.ComfyMovieGenerator.generate", side_effect=generate):
            response = self.client.post("/api/movies/queue", json={"datasetKey": "face", "imagePath": "source.png", "presetKey": "walk"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.wait()[0]["status"], "completed")
        self.assertEqual((videos / "walk_005.mp4").read_bytes(), b"generated-video")
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), b"image")

    def test_output_recovery_failure_keeps_generated_files(self) -> None:
        def generate(*_) -> None:
            (self.output / "one.mp4").write_bytes(b"one")
            (self.output / "two.mp4").write_bytes(b"two")

        with patch("backend.app.services.movie_service.ComfyMovieGenerator.generate", side_effect=generate):
            self.client.post("/api/movies/queue", json={"datasetKey": "face", "imagePath": "source.png", "presetKey": "walk"})
            job = self.wait()[0]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(len(list(self.output.iterdir())), 2)

    def test_project_root_cannot_be_temporary_output(self) -> None:
        settings = self.client.get("/api/projects/settings").json()
        settings["comfyuiMovieOutputPath"] = str(self.root)
        self.client.put("/api/projects/settings", json=settings)
        response = self.client.post("/api/movies/queue", json={"datasetKey": "face", "imagePath": "source.png", "presetKey": "walk"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("危険", response.json()["error"]["message"])


if __name__ == "__main__":
    unittest.main()

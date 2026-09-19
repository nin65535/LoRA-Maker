import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.project_service import ProjectService


class ProgressApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.service = ProjectService(self.base / "settings")
        self.client = TestClient(create_app(self.service))
        self.root = self.base / "project"
        self.client.post("/api/projects/create", json={
            "rootPath": str(self.root), "name": "Character", "datasets": [{
                "key": "face", "name": "顔", "repeats": 20,
                "triggerTags": ["__face__"], "removedTags": [],
            }],
        })

    def tearDown(self) -> None:
        self.client.close()
        self.temporary.cleanup()

    def test_scan_counts_files_pairs_and_warnings(self) -> None:
        (self.root / "01_素材画像/face/a.PNG").write_bytes(b"image")
        (self.root / "02_動画/face/movie.mp4").write_bytes(b"video")
        capture = self.root / "03_動画キャプチャ/face/movie"
        capture.mkdir()
        (capture / "frame.webp").write_bytes(b"image")
        (self.root / "03_動画キャプチャ/face/empty").mkdir()
        training = self.root / "06_LoRA学習素材/20_face"
        (training / "paired.jpg").write_bytes(b"image")
        (training / "paired.txt").write_text("tag", encoding="utf-8")
        (training / "missing.txt").write_text("tag", encoding="utf-8")
        (self.root / "07_LoRA/model.safetensors").write_bytes(b"model")

        response = self.client.get("/api/progress")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["totals"]["sourceImages"], 1)
        self.assertEqual(body["totals"]["capturedFrames"], 1)
        self.assertEqual(body["totals"]["matchedPairs"], 1)
        self.assertEqual(body["totals"]["mismatches"], 1)
        self.assertEqual(body["totals"]["trainedLora"], 1)
        self.assertEqual(body["datasets"][0]["captureFolders"], 2)
        self.assertFalse(body["datasets"][0]["canDelete"])
        self.assertIn("01_素材画像", body["datasets"][0]["deleteBlockers"])
        self.assertIn("06_LoRA学習素材", body["datasets"][0]["deleteBlockers"])
        self.assertTrue(any("画像のないキャプション" in item for item in body["warnings"]))
        self.assertTrue(any("空のキャプチャフォルダ" in item for item in body["warnings"]))

    def test_empty_dataset_can_be_deleted(self) -> None:
        response = self.client.get("/api/progress")

        self.assertEqual(response.status_code, 200)
        dataset = response.json()["datasets"][0]
        self.assertTrue(dataset["canDelete"])
        self.assertEqual(dataset["deleteBlockers"], [])

    def test_scan_requires_open_project(self) -> None:
        empty = ProjectService(self.base / "other-settings")
        with TestClient(create_app(empty)) as client:
            response = client.get("/api/progress")
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()

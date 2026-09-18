import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.project_service import ProjectService


class TagApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.service = ProjectService(self.base / "settings")
        self.client = TestClient(create_app(self.service))
        self.client.__enter__()
        self.root = self.base / "project"
        self.client.post("/api/projects/create", json={"rootPath": str(self.root), "name": "Test", "datasets": [{"key": "face", "name": "顔", "repeats": 10, "triggerTags": ["hero", "person"], "removedTags": []}]})
        self.training = self.root / "06_LoRA学習素材" / "10_face"
        (self.training / "a.png").write_bytes(b"png")
        (self.training / "b.jpg").write_bytes(b"jpg")

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_summary_removed_tags_and_placement_are_based_on_raw_captions(self) -> None:
        raw = self.root / "05_タグ付け" / "face"
        (raw / "a.txt").write_text("blue_hair, smile, person", encoding="utf-8")
        (raw / "b.txt").write_text("blue_hair, solo", encoding="utf-8")
        summary = self.client.get("/api/tags/face").json()
        self.assertEqual(summary["tags"][0]["tag"], "blue_hair")
        self.assertEqual(summary["tags"][0]["rate"], 1)
        rejected = self.client.put("/api/tags/face/removed", json={"removedTags": ["hero"]})
        self.assertEqual(rejected.status_code, 400)
        saved = self.client.put("/api/tags/face/removed", json={"removedTags": ["blue_hair"]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["config"]["datasets"][0]["removedTags"], ["blue_hair"])
        self.assertEqual(self.client.post("/api/tags/face/place").status_code, 200)
        self.assertEqual((self.training / "a.txt").read_text(encoding="utf-8").strip(), "hero, person, smile")
        (self.training / "a.txt").write_text("damaged", encoding="utf-8")
        self.client.post("/api/tags/face/place")
        self.assertEqual((self.training / "a.txt").read_text(encoding="utf-8").strip(), "hero, person, smile")
        self.assertEqual((raw / "a.txt").read_text(encoding="utf-8"), "blue_hair, smile, person")

    def test_tagger_runs_as_background_job(self) -> None:
        with patch("backend.app.services.tag_service.ComfyTagger.tag", side_effect=["tag_a, tag_b", "tag_b"]):
            created = self.client.post("/api/tags/face/run").json()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                job = self.client.get(f"/api/jobs/{created['id']}").json()
                if job["status"] == "completed":
                    break
                time.sleep(.02)
            self.assertEqual(job["status"], "completed")
        self.assertEqual((self.root / "05_タグ付け" / "face" / "a.txt").read_text(encoding="utf-8").strip(), "tag_a, tag_b")


if __name__ == "__main__":
    unittest.main()

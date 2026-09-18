import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.project_service import ProjectService


class UpscaleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.service = ProjectService(self.base / "settings")
        self.client = TestClient(create_app(self.service))
        self.client.__enter__()
        self.root = self.base / "project"
        self.client.post("/api/projects/create", json={"rootPath": str(self.root), "name": "Test", "datasets": [{"key": "face", "name": "顔", "repeats": 10, "triggerTags": [], "removedTags": []}]})
        self.capture = self.root / "03_動画キャプチャ" / "face" / "walk_001"
        self.capture.mkdir()
        (self.capture / "frame_000001.png").write_bytes(b"original")
        self.selection = self.base / "selected"
        self.executable = self.base / "BandiView.exe"
        self.executable.write_bytes(b"fake")
        settings = self.client.get("/api/projects/settings").json()
        settings.update({"bandiviewPath": str(self.executable), "bandiviewSelectionPath": str(self.selection)})
        self.client.put("/api/projects/settings", json=settings)

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def wait(self) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            jobs = [job for job in self.client.get("/api/jobs").json() if job["type"] == "image-upscale"]
            if jobs and jobs[0]["status"] not in ("queued", "running"):
                return jobs[0]
            time.sleep(.02)
        self.fail("upscale job did not settle")

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_selection_and_successful_upscale_copy_then_cleanup(self, popen) -> None:
        response = self.client.post("/api/upscale/face/walk_001/select")
        self.assertEqual(response.status_code, 200)
        popen.assert_called_once_with([str(self.executable.resolve()), str(self.capture.resolve())])
        self.selection.mkdir(exist_ok=True)
        selected = self.selection / "frame_000001.png"
        selected.write_bytes(b"selected")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", return_value=b"upscaled"):
            self.assertEqual(self.client.post("/api/upscale/face/walk_001/run").status_code, 200)
            self.assertEqual(self.wait()["status"], "completed")
        self.assertEqual((self.root / "04_拡大/face/walk_001_frame_000001.png").read_bytes(), b"upscaled")
        self.assertEqual((self.root / "06_LoRA学習素材/10_face/sample_000001.png").read_bytes(), b"upscaled")
        self.assertFalse(selected.exists())
        self.assertEqual((self.capture / "frame_000001.png").read_bytes(), b"original")

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_failure_keeps_selected_input_and_rolls_back_outputs(self, _) -> None:
        self.client.post("/api/upscale/face/walk_001/select")
        self.selection.mkdir(exist_ok=True)
        selected = self.selection / "frame_000001.png"
        selected.write_bytes(b"selected")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", side_effect=RuntimeError("broken")):
            self.client.post("/api/upscale/face/walk_001/run")
            self.assertEqual(self.wait()["status"], "failed")
        self.assertTrue(selected.is_file())
        self.assertFalse(any((self.root / "04_拡大/face").iterdir()))

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_other_selection_is_rejected_while_images_remain(self, _) -> None:
        other = self.root / "03_動画キャプチャ/face/walk_002"
        other.mkdir()
        (other / "frame.png").write_bytes(b"frame")
        self.client.post("/api/upscale/face/walk_001/select")
        self.selection.mkdir(exist_ok=True)
        (self.selection / "chosen.png").write_bytes(b"selected")
        response = self.client.post("/api/upscale/face/walk_002/select")
        self.assertEqual(response.status_code, 400)
        self.assertIn("選別画像が残っています", response.json()["error"]["message"])

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_duplicate_training_image_is_reported_and_selection_is_kept(self, _) -> None:
        training = self.root / "06_LoRA学習素材/10_face"
        (training / "sample_000042.png").write_bytes(b"same-upscaled-image")
        self.client.post("/api/upscale/face/walk_001/select")
        selected = self.selection / "frame_000001.png"
        selected.write_bytes(b"selected")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", return_value=b"same-upscaled-image"):
            self.client.post("/api/upscale/face/walk_001/run")
            job = self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertIn("sample_000042.png", job["error"])
        self.assertTrue(selected.is_file())
        self.assertFalse((self.root / "04_拡大/face/walk_001_frame_000001.png").exists())

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_processed_source_is_rejected_before_comfyui(self, _) -> None:
        self.client.post("/api/upscale/face/walk_001/select")
        selected = self.selection / "frame_000001.png"
        selected.write_bytes(b"same-source")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", return_value=b"first-output"):
            self.client.post("/api/upscale/face/walk_001/run")
            self.assertEqual(self.wait()["status"], "completed")
        other = self.root / "03_動画キャプチャ/face/walk_002"
        other.mkdir()
        (other / "frame.png").write_bytes(b"frame")
        self.client.post("/api/upscale/face/walk_002/select")
        (self.selection / "another_name.png").write_bytes(b"same-source")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale") as upscale:
            self.client.post("/api/upscale/face/walk_002/run")
            job = self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertIn("sample_000001.png", job["error"])
        self.assertIn("開始していません", job["error"])
        upscale.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import tempfile
import threading
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
        self.tagger = patch("backend.app.services.tag_service.ComfyTagger.tag", return_value="auto, tag")
        self.tagger.start()
        self.addCleanup(self.tagger.stop)

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

    def select_and_finish(self, popen, capture_folder: str = "walk_001", name: str = "frame_000001.png", data: bytes = b"selected") -> Path:
        release = threading.Event()
        popen.return_value.wait.side_effect = lambda: release.wait(1)
        self.assertEqual(self.client.post(f"/api/upscale/face/{capture_folder}/select").status_code, 200)
        self.selection.mkdir(exist_ok=True)
        (self.selection / name).write_bytes(data)
        release.set()
        deadline = time.monotonic() + 1
        while self.client.app.state.upscale_service.bandiview_process is not None and time.monotonic() < deadline:
            time.sleep(.01)
        return self.root / "04_選別・拡大" / "face" / capture_folder / name

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_selection_and_successful_upscale_copy_then_cleanup(self, popen) -> None:
        selected = self.select_and_finish(popen)
        popen.assert_called_once_with([str(self.executable.resolve()), str(self.capture.resolve())])
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", return_value=b"upscaled"):
            self.assertEqual(self.client.post("/api/upscale/face/walk_001/run", json={"scale": 2}).status_code, 200)
            self.assertEqual(self.wait()["status"], "completed")
        self.assertEqual(selected.read_bytes(), b"selected")
        self.assertEqual((self.root / "06_LoRA学習素材/10_face/sample_000001.png").read_bytes(), b"upscaled")
        self.assertTrue(selected.exists())
        self.assertEqual((self.capture / "frame_000001.png").read_bytes(), b"original")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            tag_jobs = [job for job in self.client.get("/api/jobs").json() if job["type"] == "tagger"]
            if tag_jobs and tag_jobs[0]["status"] == "completed":
                break
            time.sleep(.01)
        self.assertEqual(tag_jobs[0]["status"], "completed")
        self.assertEqual(tag_jobs[0]["payload"]["imageNames"], ["sample_000001.png"])
        self.assertTrue((self.root / "05_タグ付け/face/sample_000001.txt").is_file())

    @patch("backend.app.services.upscale_service.ComfyUpscaler.upscale")
    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_scale_one_copies_without_comfyui(self, popen, upscale) -> None:
        selected = self.select_and_finish(popen, data=b"unchanged-image")

        self.assertEqual(self.client.post("/api/upscale/face/walk_001/run", json={"scale": 1}).status_code, 200)
        self.assertEqual(self.wait()["status"], "completed")

        upscale.assert_not_called()
        self.assertEqual(selected.read_bytes(), b"unchanged-image")
        self.assertEqual((self.root / "06_LoRA学習素材/10_face/sample_000001.png").read_bytes(), b"unchanged-image")

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_same_selection_can_be_added_at_both_scales(self, popen) -> None:
        selected = self.select_and_finish(popen, data=b"same-source")
        self.client.post("/api/upscale/face/walk_001/run", json={"scale": 1})
        self.assertEqual(self.wait()["status"], "completed")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", return_value=b"two-times"):
            self.client.post("/api/upscale/face/walk_001/run", json={"scale": 2})
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                jobs = [job for job in self.client.get("/api/jobs").json() if job["type"] == "image-upscale"]
                if len(jobs) >= 2 and jobs[0]["status"] not in ("queued", "running"):
                    break
                time.sleep(.02)
        self.assertEqual(jobs[0]["status"], "completed")
        training = self.root / "06_LoRA学習素材/10_face"
        self.assertEqual((training / "sample_000001.png").read_bytes(), b"same-source")
        self.assertEqual((training / "sample_000002.png").read_bytes(), b"two-times")
        self.assertTrue(selected.is_file())

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_reports_bandiview_launcher_process_completion(self, popen) -> None:
        with patch.object(self.client.app.state.job_service, "publish") as publish:
            selected = self.select_and_finish(popen, name="chosen.png")
            self.assertTrue(selected.is_file())
            publish.assert_called_with("bandiview-finished")
            completed = self.client.get("/api/upscale").json()

        self.assertFalse(completed["bandiviewRunning"])
        self.assertEqual(completed["folders"][0]["selectedImageCount"], 1)

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_failure_keeps_selected_input_and_rolls_back_outputs(self, _) -> None:
        selected = self.select_and_finish(_, data=b"selected")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", side_effect=RuntimeError("broken")):
            self.client.post("/api/upscale/face/walk_001/run", json={"scale": 2})
            self.assertEqual(self.wait()["status"], "failed")
        self.assertTrue(selected.is_file())
        self.assertTrue(selected.is_file())

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_next_selection_is_allowed_after_previous_is_imported(self, popen) -> None:
        other = self.root / "03_動画キャプチャ/face/walk_002"
        other.mkdir()
        (other / "frame.png").write_bytes(b"frame")
        first = self.select_and_finish(popen, name="chosen.png")
        second = self.select_and_finish(popen, "walk_002", "other.png")
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_duplicate_training_image_is_reported_and_selection_is_kept(self, _) -> None:
        training = self.root / "06_LoRA学習素材/10_face"
        (training / "sample_000042.png").write_bytes(b"same-upscaled-image")
        selected = self.select_and_finish(_, data=b"selected")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", return_value=b"same-upscaled-image"):
            self.client.post("/api/upscale/face/walk_001/run", json={"scale": 2})
            job = self.wait()
        self.assertEqual(job["status"], "failed")
        self.assertIn("sample_000042.png", job["error"])
        self.assertTrue(selected.is_file())
        self.assertTrue(selected.exists())

    @patch("backend.app.services.upscale_service.subprocess.Popen")
    def test_processed_source_is_rejected_before_comfyui(self, _) -> None:
        selected = self.select_and_finish(_, data=b"same-source")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale", return_value=b"first-output"):
            self.client.post("/api/upscale/face/walk_001/run", json={"scale": 2})
            self.assertEqual(self.wait()["status"], "completed")
        other = self.root / "03_動画キャプチャ/face/walk_002"
        other.mkdir()
        (other / "frame.png").write_bytes(b"frame")
        self.select_and_finish(_, "walk_002", "another_name.png", b"same-source")
        with patch("backend.app.services.upscale_service.ComfyUpscaler.upscale") as upscale:
            response = self.client.post("/api/upscale/face/walk_002/run", json={"scale": 2})
        self.assertEqual(response.status_code, 400)
        self.assertIn("配置済み", response.json()["error"]["message"])
        upscale.assert_not_called()


if __name__ == "__main__":
    unittest.main()

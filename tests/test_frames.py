import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.frame_service import FrameServiceError
from backend.app.services.project_service import ProjectService


class FrameApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.service = ProjectService(self.base / "settings")
        self.client = TestClient(create_app(self.service))
        self.client.__enter__()
        self.root = self.base / "project"
        self.client.post("/api/projects/create", json={
            "rootPath": str(self.root), "name": "Test",
            "datasets": [{"key": "face", "name": "顔", "repeats": 10, "triggerTags": [], "removedTags": []}],
        })
        self.videos = self.root / "02_動画" / "face"
        (self.videos / "ok.mp4").write_bytes(b"input remains unchanged")
        (self.videos / "bad.mp4").write_bytes(b"bad input remains unchanged")

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def wait_until_settled(self, count: int = 1) -> list[dict]:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            jobs = [item for item in self.client.get("/api/jobs").json() if item["type"] == "frame-extraction"]
            if len(jobs) >= count and all(item["status"] not in ("queued", "running") for item in jobs[:count]):
                return jobs
            time.sleep(.02)
        self.fail("frame jobs did not settle")

    def test_individual_extraction_is_atomic_and_does_not_overwrite(self) -> None:
        async def fake_run(_, video: Path, output: Path) -> None:
            (output / "frame_000001.png").write_bytes(b"frame")

        with patch.object(type(self.client.app.state.frame_service), "_run_ffmpeg", fake_run):
            created = self.client.post("/api/frames/face/videos/ok.mp4/run")
            self.assertEqual(created.status_code, 200)
            self.assertEqual(self.wait_until_settled()[0]["status"], "completed")
        self.assertEqual((self.videos / "ok.mp4").read_bytes(), b"input remains unchanged")
        self.assertTrue((self.root / "03_動画キャプチャ/face/ok/frame_000001.png").is_file())
        status = self.client.get("/api/frames/face").json()
        ok = next(item for item in status["videos"] if item["name"] == "ok.mp4")
        self.assertEqual((ok["state"], ok["frameCount"]), ("extracted", 1))
        self.assertEqual(self.client.post("/api/frames/face/videos/ok.mp4/run").status_code, 400)

    def test_batch_continues_after_failure_and_skips_existing_frames(self) -> None:
        capture = self.root / "03_動画キャプチャ/face"
        (capture / "empty").mkdir()

        async def fake_run(_, video: Path, output: Path) -> None:
            if video.name == "bad.mp4":
                (output / "partial.png").write_bytes(b"partial")
                raise FrameServiceError("broken video")
            (output / "frame_000001.png").write_bytes(b"frame")

        with patch.object(type(self.client.app.state.frame_service), "_run_ffmpeg", fake_run):
            response = self.client.post("/api/frames/run-unprocessed")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["jobs"]), 2)
            jobs = self.wait_until_settled(2)
        self.assertEqual({item["status"] for item in jobs[:2]}, {"completed", "failed"})
        self.assertTrue((capture / "ok/frame_000001.png").is_file())
        self.assertFalse((capture / "bad").exists())
        self.assertFalse(any(item.name.endswith(".extracting") for item in capture.iterdir()))
        states = {item["name"]: item["state"] for item in self.client.get("/api/frames/face").json()["videos"]}
        self.assertEqual(states, {"bad.mp4": "failed", "ok.mp4": "extracted"})

    @patch("backend.app.services.frame_service.subprocess.Popen")
    def test_opens_extracted_frame_folder(self, popen) -> None:
        output = self.root / "03_動画キャプチャ/face/ok"
        output.mkdir()
        (output / "frame_000001.png").write_bytes(b"frame")

        response = self.client.post("/api/frames/face/videos/ok.mp4/open-output")

        self.assertEqual(response.status_code, 200)
        popen.assert_called_once_with(["explorer.exe", str(output.resolve())])


if __name__ == "__main__":
    unittest.main()

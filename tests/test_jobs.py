import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.job_service import JobService
from backend.app.services.project_service import ProjectService


class JobApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.projects = ProjectService(self.base / "settings")
        self.jobs = JobService(self.base / "settings" / "jobs.sqlite3")
        self.client = TestClient(create_app(self.projects, self.jobs))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def wait_for(self, job_id: str, expected: str, timeout: float = 3) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] == expected:
                return job
            time.sleep(0.02)
        self.fail(f"job {job_id} did not become {expected}")

    def test_serial_queue_completion_failure_and_cancel(self) -> None:
        first = self.client.post("/api/jobs/test", json={"durationSeconds": .15}).json()
        second = self.client.post("/api/jobs/test", json={"durationSeconds": .05, "shouldFail": True}).json()
        third = self.client.post("/api/jobs/test", json={"durationSeconds": .05}).json()
        cancelled = self.client.post(f"/api/jobs/{third['id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        completed = self.wait_for(first["id"], "completed")
        failed = self.wait_for(second["id"], "failed")
        self.assertLessEqual(completed["finishedAt"], failed["startedAt"])
        self.assertIn("テスト用の失敗", failed["error"])

    def test_active_job_blocks_project_switch(self) -> None:
        job = self.client.post("/api/jobs/test", json={"durationSeconds": .3}).json()
        response = self.client.post("/api/projects/create", json={"rootPath": str(self.base / "project"), "name": "Blocked", "datasets": []})
        self.assertEqual(response.status_code, 409)
        self.wait_for(job["id"], "completed")

    def test_running_job_is_marked_failed_after_restart(self) -> None:
        path = self.base / "recovery.sqlite3"
        service = JobService(path)
        job = service.enqueue("test", {"durationSeconds": 1, "shouldFail": False}, None)
        connection = sqlite3.connect(path)
        try:
            connection.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job.id,))
            connection.commit()
        finally:
            connection.close()
        recovered = JobService(path).get(job.id)
        self.assertEqual(recovered.status, "failed")
        self.assertIn("再実行", recovered.error)


if __name__ == "__main__":
    unittest.main()

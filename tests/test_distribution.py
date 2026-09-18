import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.job_service import JobService
from backend.app.services.project_service import ProjectService
from backend.app.services.shutdown_service import ShutdownService


class DistributionTests(unittest.TestCase):
    def test_built_frontend_and_spa_fallback_are_served(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            base = Path(temporary)
            dist = base / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<h1>LoRA Maker</h1>", encoding="utf-8")
            app = create_app(
                ProjectService(base / "settings"),
                JobService(base / "settings/jobs.sqlite3"),
                frontend_dist=dist,
            )
            with TestClient(app) as client:
                self.assertIn("LoRA Maker", client.get("/").text)
                self.assertIn("LoRA Maker", client.get("/client-route").text)
                self.assertEqual(client.get("/api/missing").status_code, 404)


class ShutdownServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.jobs = JobService(Path(self.temporary.name) / "jobs.sqlite3")
        self.calls = 0

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    def shutdown(self) -> None:
        self.calls += 1

    async def test_reconnect_clears_request_and_last_disconnect_triggers_shutdown(self) -> None:
        service = ShutdownService(self.jobs, self.shutdown, grace_seconds=0)
        await service.start()
        try:
            await service.connected()
            await service.disconnected()
            await service.connected()
            await service.evaluate()
            self.assertEqual(self.calls, 0)
            self.assertFalse(service.shutdown_requested)
            await service.disconnected()
            await service.evaluate()
            self.assertEqual(self.calls, 1)
        finally:
            await service.stop()

    async def test_active_job_defers_shutdown_until_queue_finishes(self) -> None:
        self.jobs.enqueue("test", {"durationSeconds": 0, "shouldFail": False}, None)
        service = ShutdownService(self.jobs, self.shutdown, grace_seconds=0)
        await service.start()
        try:
            await service.evaluate()
            self.assertTrue(service.shutdown_requested)
            self.assertEqual(self.calls, 0)
            with self.jobs._connect() as connection:
                connection.execute("UPDATE jobs SET status = 'completed'")
            await service.evaluate()
            self.assertEqual(self.calls, 1)
        finally:
            await service.stop()

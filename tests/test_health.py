import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class HealthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health_returns_service_status(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["service"], "lora-maker-api")
        self.assertIn("timestamp", response.json())
        self.assertIn("X-Request-ID", response.headers)

    def test_missing_route_uses_common_error_shape(self) -> None:
        response = self.client.get("/api/missing")

        self.assertEqual(response.status_code, 404)
        error = response.json()["error"]
        self.assertEqual(error["code"], "http_404")
        self.assertEqual(error["request_id"], response.headers["X-Request-ID"])


if __name__ == "__main__":
    unittest.main()

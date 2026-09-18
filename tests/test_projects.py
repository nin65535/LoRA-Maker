import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.project_service import FOLDERS, ProjectService


class ProjectApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.selected_path: Path | None = None
        self.service = ProjectService(
            self.base / "settings", file_selector=lambda _: self.selected_path
        )
        self.client = TestClient(create_app(self.service))

    def tearDown(self) -> None:
        self.client.close()
        self.temporary.cleanup()

    def test_create_add_save_and_restore_project(self) -> None:
        root = self.base / "project"
        response = self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Tanshun", "datasets": []},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue((root / "lora_maker.json").is_file())
        for _, folder_name in FOLDERS:
            self.assertTrue((root / folder_name).is_dir())

        response = self.client.post(
            "/api/projects/current/datasets",
            json={
                "key": "face",
                "name": "顔",
                "repeats": 20,
                "triggerTags": ["__face__", "__person__"],
                "removedTags": [],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue((root / "01_素材画像" / "face").is_dir())
        self.assertTrue((root / "06_LoRA学習素材" / "20_face").is_dir())

        restored = ProjectService(self.base / "settings")
        state = restored.restore_last_project()
        self.assertIsNotNone(state)
        self.assertEqual(state.config.datasets[0].trigger_tags, ["__face__", "__person__"])

    def test_invalid_load_keeps_current_project_and_setting(self) -> None:
        root = self.base / "valid"
        created = self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Valid", "datasets": []},
        ).json()
        invalid = self.base / "invalid.json"
        invalid.write_text('{"application":"other"}', encoding="utf-8")

        response = self.client.post("/api/projects/load", json={"configPath": str(invalid)})
        self.assertEqual(response.status_code, 400)
        current = self.client.get("/api/projects/current").json()
        self.assertEqual(current["configPath"], created["configPath"])
        settings = json.loads(self.service.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(settings["lastProjectConfigPath"], created["configPath"])

    def test_rejects_invalid_dataset_and_reports_folder_mismatch(self) -> None:
        root = self.base / "project"
        self.client.post("/api/projects/create", json={"rootPath": str(root), "name": "Test", "datasets": []})
        invalid = self.client.post(
            "/api/projects/current/datasets",
            json={"key": "Bad-Key", "name": "Bad", "repeats": 0, "triggerTags": [], "removedTags": []},
        )
        self.assertEqual(invalid.status_code, 422)

        (root / "03_動画キャプチャ").rmdir()
        loaded = self.client.post("/api/projects/load", json={"configPath": str(root / "lora_maker.json")})
        self.assertEqual(loaded.status_code, 200)
        self.assertTrue(any("03_動画キャプチャ" in warning for warning in loaded.json()["warnings"]))

    def test_native_selection_loads_project_and_cancel_keeps_current(self) -> None:
        root = self.base / "selected"
        created = self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Selected", "datasets": []},
        ).json()

        self.selected_path = root / "lora_maker.json"
        selected = self.client.post("/api/projects/select")
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["configPath"], created["configPath"])

        self.selected_path = None
        cancelled = self.client.post("/api/projects/select")
        self.assertEqual(cancelled.status_code, 200)
        self.assertIsNone(cancelled.json())
        current = self.client.get("/api/projects/current").json()
        self.assertEqual(current["configPath"], created["configPath"])


if __name__ == "__main__":
    unittest.main()

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.project_service import ProjectService


class ProjectApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.selected_path: Path | None = None
        self.selected_save_path: Path | None = None
        self.service = ProjectService(
            self.base / "settings",
            file_selector=lambda _: self.selected_path,
            save_path_selector=lambda _: self.selected_save_path,
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
        for _, folder_name in self.service.master_service.folders:
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

    def test_remove_dataset_requires_all_related_folders_to_be_empty(self) -> None:
        root = self.base / "remove-project"
        self.client.post(
            "/api/projects/create",
            json={
                "rootPath": str(root),
                "name": "Test",
                "datasets": [{"key": "face", "name": "顔", "repeats": 20, "triggerTags": [], "removedTags": []}],
            },
        )
        source = root / "01_素材画像" / "face" / "source.png"
        source.write_bytes(b"image")

        response = self.client.delete("/api/projects/current/datasets/face")

        self.assertEqual(response.status_code, 400)
        self.assertIn("01_素材画像", response.json()["error"]["message"])
        self.assertEqual(source.read_bytes(), b"image")
        source.unlink()

        response = self.client.delete("/api/projects/current/datasets/face")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["config"]["datasets"], [])
        self.assertFalse((root / "06_LoRA学習素材" / "20_face").exists())
        saved = json.loads((root / "lora_maker.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["datasets"], [])

    def test_create_uses_expanded_default_datasets_when_omitted(self) -> None:
        root = self.base / "default-project"
        response = self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "key": "white_knight", "name": "White Knight"},
        )

        self.assertEqual(response.status_code, 200)
        config = response.json()["config"]
        self.assertEqual(config["project"], {"key": "white_knight", "name": "White Knight"})
        self.assertEqual([item["key"] for item in config["datasets"]], ["face", "outfit", "body"])
        self.assertEqual(config["datasets"][0]["triggerTags"], ["__white_knight_face__"])
        self.assertEqual(
            config["datasets"][1]["triggerTags"],
            ["__white_knight_face__", "__white_knight_outfit__"],
        )
        self.assertTrue((root / "01_素材画像" / "face").is_dir())
        self.assertTrue((root / "01_素材画像" / "outfit").is_dir())
        self.assertTrue((root / "01_素材画像" / "body").is_dir())

        saved = json.loads((root / "lora_maker.json").read_text(encoding="utf-8"))
        self.assertNotIn("${project}", json.dumps(saved))
        self.assertNotIn("${dataset}", json.dumps(saved))

    def test_create_accepts_empty_character_name(self) -> None:
        root = self.base / "unnamed-project"
        response = self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "key": "unnamed", "name": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["config"]["project"], {"key": "unnamed", "name": ""})
        self.assertTrue((root / "lora_maker.json").is_file())

    def test_select_create_config_path_returns_selection_and_preserves_cancel(self) -> None:
        selected_path = self.base / "custom-project.json"
        self.selected_save_path = selected_path
        selected = self.client.post(
            "/api/projects/create/select-config-path",
            json={"initialPath": str(self.base / "new-project")},
        )
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json(), {"path": str(selected_path.resolve())})

        self.selected_save_path = None
        cancelled = self.client.post(
            "/api/projects/create/select-config-path",
            json={"initialPath": str(self.base)},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertIsNone(cancelled.json())

    def test_create_supports_custom_config_filename(self) -> None:
        config_path = self.base / "custom-name.json"
        response = self.client.post(
            "/api/projects/create",
            json={"configPath": str(config_path), "key": "custom", "name": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["configPath"], str(config_path.resolve()))
        self.assertTrue(config_path.is_file())
        self.assertFalse((self.base / "lora_maker.json").exists())

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

    def test_close_project_clears_current_and_prevents_restore(self) -> None:
        root = self.base / "project"
        self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Test", "datasets": []},
        )

        closed = self.client.post("/api/projects/close")
        self.assertEqual(closed.status_code, 200)
        self.assertIsNone(closed.json())
        self.assertIsNone(self.client.get("/api/projects/current").json())
        self.assertIsNone(self.service.settings().last_project_config_path)
        self.assertIsNone(ProjectService(self.base / "settings").restore_last_project())

    def test_reload_current_reads_external_config_change(self) -> None:
        root = self.base / "reload-project"
        self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Before", "datasets": []},
        )
        path = root / "lora_maker.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["project"]["name"] = "After"
        path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

        response = self.client.post("/api/projects/current/reload")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["config"]["project"]["name"], "After")

    def test_save_rejects_external_config_change(self) -> None:
        root = self.base / "external-change"
        self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Original", "datasets": []},
        )
        path = root / "lora_maker.json"
        external = json.loads(path.read_text(encoding="utf-8"))
        external["project"]["name"] = "External"
        path.write_text(json.dumps(external, ensure_ascii=False), encoding="utf-8")

        response = self.client.put(
            "/api/projects/current",
            json={"project": {"key": None, "name": "Draft"}, "datasets": []},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("外部で変更", response.json()["error"]["message"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["project"]["name"], "External")

    def test_save_renames_training_folder_when_repeats_changes(self) -> None:
        root = self.base / "repeats-project"
        dataset = {"key": "face", "name": "顔", "repeats": 20, "triggerTags": [], "removedTags": []}
        self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Test", "datasets": [dataset]},
        )
        old_folder = root / "06_LoRA学習素材" / "20_face"
        (old_folder / "sample.txt").write_text("caption", encoding="utf-8")
        dataset["repeats"] = 30

        response = self.client.put(
            "/api/projects/current",
            json={"project": {"key": None, "name": "Test"}, "datasets": [dataset]},
        )

        new_folder = root / "06_LoRA学習素材" / "30_face"
        self.assertEqual(response.status_code, 200)
        self.assertFalse(old_folder.exists())
        self.assertEqual((new_folder / "sample.txt").read_text(encoding="utf-8"), "caption")

    def test_save_rejects_training_folder_collision(self) -> None:
        root = self.base / "collision-project"
        dataset = {"key": "face", "name": "顔", "repeats": 20, "triggerTags": [], "removedTags": []}
        self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Test", "datasets": [dataset]},
        )
        old_folder = root / "06_LoRA学習素材" / "20_face"
        new_folder = root / "06_LoRA学習素材" / "30_face"
        (old_folder / "old.txt").write_text("old", encoding="utf-8")
        new_folder.mkdir()
        (new_folder / "new.txt").write_text("new", encoding="utf-8")
        dataset["repeats"] = 30

        response = self.client.put(
            "/api/projects/current",
            json={"project": {"key": None, "name": "Test"}, "datasets": [dataset]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("変更前後", response.json()["error"]["message"])
        saved = json.loads((root / "lora_maker.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["datasets"][0]["repeats"], 20)
        self.assertTrue((old_folder / "old.txt").exists())
        self.assertTrue((new_folder / "new.txt").exists())

    def test_clear_generated_outputs_preserves_sources_and_recreates_structure(self) -> None:
        root = self.base / "clear-project"
        dataset = {"key": "face", "name": "顔", "repeats": 20, "triggerTags": [], "removedTags": []}
        created = self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Test", "datasets": [dataset]},
        ).json()
        source = root / "01_素材画像" / "face" / "source.png"
        source.write_bytes(b"source")
        folders = ["02_動画", "03_動画キャプチャ", "04_選別・拡大", "05_タグ付け", "06_LoRA学習素材"]
        for name in folders:
            target = root / name / ("20_face" if name == "06_LoRA学習素材" else "face")
            target.mkdir(parents=True, exist_ok=True)
            (target / "generated.bin").write_bytes(b"generated")
        (root / "07_LoRA" / "model.safetensors").write_bytes(b"model")
        upscale = self.client.app.state.upscale_service
        upscale._save_history(created["configPath"], "face", 2, [("source", "output", root / "06_LoRA学習素材/10_face/generated.bin")])

        response = self.client.post("/api/projects/current/clear-generated/videos")
        self.assertEqual(response.status_code, 200)
        self.assertTrue((root / "06_LoRA学習素材/20_face/generated.bin").is_file())
        for stage in ("capturedFrames", "upscaledImages", "generatedTags", "trainingDataset", "trainedLora"):
            response = self.client.post(f"/api/projects/current/clear-generated/{stage}")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(source.read_bytes(), b"source")
        for name in folders:
            target = root / name / ("20_face" if name == "06_LoRA学習素材" else "face")
            self.assertTrue(target.is_dir())
            self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(list((root / "07_LoRA").iterdir()), [])
        connection = sqlite3.connect(upscale.history_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM upscale_history").fetchone()[0], 0)
        finally:
            connection.close()

    def test_active_job_blocks_generated_output_clear(self) -> None:
        root = self.base / "active-clear-project"
        self.client.post(
            "/api/projects/create",
            json={"rootPath": str(root), "name": "Test", "datasets": []},
        )
        self.client.post("/api/jobs/test", json={"durationSeconds": 10})

        response = self.client.post("/api/projects/current/clear-generated/videos")

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()

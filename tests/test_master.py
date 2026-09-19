import json
import tempfile
import unittest
from pathlib import Path

from backend.app.services.master_service import MasterService, MasterServiceError


class MasterServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.source = Path("app_master.json").resolve()
        self.path = self.base / "app_master.json"
        self.path.write_text(self.source.read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_once_and_serves_cached_master(self) -> None:
        service = MasterService(self.path)
        original_name = service.folder_map["sourceImages"]
        value = json.loads(self.path.read_text(encoding="utf-8"))
        value["folders"][0]["name"] = "changed-after-startup"
        self.path.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(service.folder_map["sourceImages"], original_name)
        self.assertNotEqual(service.folder_map["sourceImages"], "changed-after-startup")

    def test_invalid_master_stops_startup(self) -> None:
        self.path.write_text('{"application":"wrong"}', encoding="utf-8")
        with self.assertRaises(MasterServiceError):
            MasterService(self.path)

    def test_movie_presets_can_be_added_and_removed_without_fixed_keys(self) -> None:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        value["movieGeneration"]["presets"] = [
            {"key": "custom_motion", "name": "独自動作", "positivePrompt": "move", "negativePrompt": "stop"},
            {"key": "second", "name": "別動作", "positivePrompt": "second", "negativePrompt": ""},
        ]
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

        presets = MasterService(self.path).value.movie_generation.presets

        self.assertEqual([item.key for item in presets], ["custom_motion", "second"])

    def test_movie_workflow_nodes_match_bundled_workflow(self) -> None:
        service = MasterService(self.source)
        config = service.value.movie_generation
        workflow = json.loads(service.resolve_app_path(config.workflow_path).read_text(encoding="utf-8"))

        for name in ("sourceImage", "positivePrompt", "negativePrompt", "output"):
            node = config.nodes[name]
            self.assertIn(node.node_id, workflow, name)
            self.assertIn(node.input_name, workflow[node.node_id]["inputs"], name)

    def test_upscale_workflow_reduces_four_times_output_to_two_times(self) -> None:
        service = MasterService(self.source)
        config = service.value.image_upscale
        workflow = json.loads(service.resolve_app_path(config.workflow_path).read_text(encoding="utf-8"))

        resize = workflow["5"]
        self.assertEqual(resize["class_type"], "ImageScaleBy")
        self.assertEqual(resize["inputs"]["scale_by"], 0.5)
        self.assertEqual(workflow[config.nodes["output"].node_id]["inputs"]["images"], ["5", 0])


if __name__ == "__main__":
    unittest.main()

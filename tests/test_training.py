import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.project_service import ProjectService


class TrainingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temporary.name)
        self.app = create_app(ProjectService(self.base / "settings"))
        self.configs = self.base / "training_configs"; self.configs.mkdir()
        (self.configs / "train.toml").write_text("", encoding="utf-8")
        self.app.state.training_service.config_directory = self.configs
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.root = self.base / "project"
        self.client.post("/api/projects/create", json={"rootPath": str(self.root), "key": "test", "name": "Test", "datasets": [
            {"key": "face", "name": "顔", "repeats": 10, "triggerTags": [], "removedTags": []}
        ]})
        self.training = self.root / "06_LoRA学習素材/10_face"
        (self.training / "a.png").write_bytes(b"image")
        (self.training / "a.txt").write_text("tag", encoding="utf-8")
        self.models = self.base / "models"; self.models.mkdir()
        self.python = self.base / "python.exe"; self.python.write_bytes(b"exe")
        self.script = self.base / "sdxl_train_network.py"; self.script.write_text("", encoding="utf-8")
        settings = self.client.get("/api/projects/settings").json()
        settings.update({"sdScriptsPythonPath": str(self.python), "sdScriptsPath": str(self.script),
                         "sdScriptsWorkingDirectory": str(self.base),
                         "loraModelsPath": str(self.models)})
        self.client.put("/api/projects/settings", json=settings)
        self.output_prefix = f"{datetime.now().strftime('%y%m%d')}_test_"

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None); self.temporary.cleanup()

    def test_status_and_mismatch_validation(self) -> None:
        status = self.client.get("/api/training").json()
        self.assertTrue(status["configured"]); self.assertEqual(status["datasets"][0]["matchedPairs"], 1)
        self.assertEqual(status["nextOutputName"], self.output_prefix + "001")
        (self.training / "extra.txt").write_text("tag", encoding="utf-8")
        response = self.client.post("/api/training/run", json={"configName": "train.toml"})
        self.assertEqual(response.status_code, 400); self.assertIn("一致しない", response.json()["error"]["message"])

    def test_deploy_classification_and_explicit_mismatch_confirmation(self) -> None:
        artifact = self.root / "07_LoRA/test.safetensors"; artifact.write_bytes(b"original")
        status = self.client.get("/api/training").json(); self.assertEqual(status["artifacts"][0]["deployment"], "not_deployed")
        self.assertEqual(self.client.post("/api/training/artifacts/test.safetensors/deploy", json={}).status_code, 200)
        self.assertEqual(artifact.read_bytes(), b"original"); self.assertEqual((self.models / artifact.name).read_bytes(), b"original")
        (self.models / artifact.name).write_bytes(b"different")
        refused = self.client.post("/api/training/artifacts/test.safetensors/deploy", json={})
        self.assertEqual(refused.status_code, 400)
        accepted = self.client.post("/api/training/artifacts/test.safetensors/deploy", json={"confirmMismatch": True})
        self.assertEqual(accepted.status_code, 200); self.assertEqual(artifact.read_bytes(), b"original")

    def test_training_job_records_output(self) -> None:
        class Process:
            returncode = 0
            def __init__(self):
                self.stdout = self
            async def readline(self): return b""
            async def wait(self):
                (self_outer.root / f"07_LoRA/{self_outer.output_prefix}001.safetensors").write_bytes(b"lora")
                return 0
        self_outer = self
        with patch("backend.app.services.training_service.asyncio.create_subprocess_exec", return_value=Process()), patch("backend.app.services.training_service.urlopen"):
            response = self.client.post("/api/training/run", json={"configName": "train.toml"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["job"]["payload"]["outputName"], self.output_prefix + "001")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                jobs = [j for j in self.client.get("/api/jobs").json() if j["type"] == "lora-training"]
                if jobs and jobs[0]["status"] not in ("queued", "running"): break
                time.sleep(.02)
            self.assertEqual(jobs[0]["status"], "completed")

    def test_epoch_calculates_max_train_steps_when_configured_steps_are_zero(self) -> None:
        (self.configs / "train.toml").write_text(
            "epoch = 20\nmax_train_steps = 0\ntrain_batch_size = 3\ngradient_accumulation_steps = 1\n",
            encoding="utf-8",
        )
        commands = []

        class Process:
            returncode = 0
            def __init__(self): self.stdout = self
            async def readline(self): return b""
            async def wait(self):
                (self_outer.root / f"07_LoRA/{self_outer.output_prefix}001.safetensors").write_bytes(b"lora")
                return 0

        def create_process(*command, **_kwargs):
            commands.append(command)
            return Process()

        self_outer = self
        with patch("backend.app.services.training_service.asyncio.create_subprocess_exec", side_effect=create_process), patch("backend.app.services.training_service.urlopen"):
            self.assertEqual(self.client.post("/api/training/run", json={"configName": "train.toml"}).status_code, 200)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not commands:
                time.sleep(.02)

        self.assertTrue(commands)
        index = commands[0].index("--max_train_steps")
        # 1 image * 10 folder repeats / batch 3 * 20 epochs = ceil(66.66...)
        self.assertEqual(commands[0][index + 1], "67")

    def test_output_sequence_uses_existing_artifacts(self) -> None:
        (self.root / f"07_LoRA/{self.output_prefix}009.safetensors").write_bytes(b"old")
        status = self.client.get("/api/training").json()
        self.assertEqual(status["nextOutputName"], self.output_prefix + "010")


if __name__ == "__main__": unittest.main()

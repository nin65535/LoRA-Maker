import asyncio
import hashlib
import math
import re
import shutil
import threading
import tomllib
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from backend.app.schemas.training import ArtifactStatus, TrainingDatasetStatus, TrainingStatus
from backend.app.services.job_service import JobService
from backend.app.services.project_service import ProjectService


class TrainingServiceError(ValueError):
    pass


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class TrainingService:
    def __init__(self, projects: ProjectService, jobs: JobService) -> None:
        self.projects, self.jobs = projects, jobs
        self.process: asyncio.subprocess.Process | None = None
        self._enqueue_lock = threading.Lock()
        self.config_directory = Path(__file__).parents[3] / "external_configs" / "training_configs"

    def _state(self):
        if self.projects.current is None:
            raise TrainingServiceError("プロジェクトが開かれていません")
        return self.projects.current

    def _paths(self):
        settings = self.projects.settings()
        python = Path(settings.sd_scripts_python_path or "")
        configured = Path(settings.sd_scripts_path or "")
        script = configured / "sdxl_train_network.py" if configured.is_dir() else configured
        working = Path(settings.sd_scripts_working_directory or "")
        return settings, python, script, working

    def _configuration_errors(self) -> list[str]:
        _, python, script, working = self._paths()
        checks = [(python.is_file(), "sd-scripts用Pythonが未設定または存在しません"),
                  (script.is_file(), "学習スクリプトが未設定または存在しません"),
                  (working.is_dir(), "sd-scripts作業ディレクトリが未設定または存在しません"),
                  (self.config_directory.is_dir(), "学習設定フォルダが存在しません")]
        errors = [message for valid, message in checks if not valid]
        if not self._state().config.project.key:
            errors.append("プロジェクトキーが未設定です")
        return errors

    def training_configs(self) -> list[str]:
        if not self.config_directory.is_dir():
            return []
        return sorted((item.name for item in self.config_directory.glob("*.toml") if item.is_file()), key=str.lower)

    def _training_config(self, name: str) -> Path:
        if Path(name).name != name or not name.lower().endswith(".toml"):
            raise TrainingServiceError("学習設定ファイル名が不正です")
        path = (self.config_directory / name).resolve()
        if not path.is_relative_to(self.config_directory.resolve()) or not path.is_file():
            raise TrainingServiceError("選択された学習設定TOMLが見つかりません")
        return path

    def _calculated_max_train_steps(self, config: Path, train_dir: Path) -> int | None:
        try:
            values = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise TrainingServiceError(f"学習設定TOMLを読み込めません: {exc}") from exc

        # kohya_ss GUI only derives max_train_steps from epoch when the explicit
        # maximum is zero. Keep the same distinction when invoking sd-scripts directly.
        if values.get("max_train_steps") != 0 or "epoch" not in values:
            return None
        try:
            epoch = int(values["epoch"])
            batch_size = int(values.get("train_batch_size", 1))
            accumulation = int(values.get("gradient_accumulation_steps", 1))
        except (TypeError, ValueError) as exc:
            raise TrainingServiceError("epoch、train_batch_size、gradient_accumulation_stepsは整数で指定してください") from exc
        if epoch < 1 or batch_size < 1 or accumulation < 1:
            raise TrainingServiceError("epoch、train_batch_size、gradient_accumulation_stepsは1以上で指定してください")

        image_ext = set(self.projects.master_service.value.file_extensions.images)
        weighted_images = 0
        for folder in train_dir.iterdir() if train_dir.is_dir() else []:
            if not folder.is_dir():
                continue
            try:
                repeats = int(folder.name.split("_", 1)[0])
            except ValueError:
                continue
            image_count = sum(1 for item in folder.iterdir() if item.is_file() and item.suffix.lower() in image_ext)
            weighted_images += repeats * image_count
        if weighted_images < 1:
            raise TrainingServiceError("epochからステップ数を計算できる学習画像がありません")
        return math.ceil(weighted_images / batch_size / accumulation * epoch)

    def _dataset_statuses(self) -> list[TrainingDatasetStatus]:
        state = self._state()
        root = Path(state.root_path) / self.projects.master_service.folder_map["trainingDataset"]
        image_ext = set(self.projects.master_service.value.file_extensions.images)
        result = []
        for dataset in state.config.datasets:
            folder = root / f"{dataset.repeats}_{dataset.key}"
            images = {p.stem.lower(): p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in image_ext} if folder.is_dir() else {}
            captions = {p.stem.lower(): p.name for p in folder.glob("*.txt")} if folder.is_dir() else {}
            result.append(TrainingDatasetStatus(key=dataset.key, name=dataset.name,
                imageCount=len(images), captionCount=len(captions), matchedPairs=len(images.keys() & captions.keys()),
                imagesWithoutCaptions=[images[k] for k in sorted(images.keys() - captions.keys())],
                captionsWithoutImages=[captions[k] for k in sorted(captions.keys() - images.keys())]))
        return result

    def _artifact_path(self, name: str) -> Path:
        if Path(name).name != name:
            raise TrainingServiceError("成果物名が不正です")
        state = self._state()
        path = Path(state.root_path) / self.projects.master_service.folder_map["trainedLora"] / name
        if not path.is_file() or path.suffix.lower() not in self.projects.master_service.value.file_extensions.lora:
            raise TrainingServiceError("学習成果物が見つかりません")
        return path

    def _models(self) -> Path | None:
        value = self.projects.settings().lora_models_path
        return Path(value).resolve() if value else None

    def status(self) -> TrainingStatus:
        state = self._state()
        output = Path(state.root_path) / self.projects.master_service.folder_map["trainedLora"]
        models = self._models()
        artifacts = []
        for source in sorted(output.iterdir(), key=lambda p: p.name.lower()) if output.is_dir() else []:
            if not source.is_file() or source.suffix.lower() not in self.projects.master_service.value.file_extensions.lora:
                continue
            if models is None:
                deployment = "unconfigured"
            elif not (models / source.name).is_file():
                deployment = "not_deployed"
            else:
                deployment = "identical" if _hash(source) == _hash(models / source.name) else "different"
            artifacts.append(ArtifactStatus(name=source.name, size=source.stat().st_size, deployment=deployment))
        errors = self._configuration_errors()
        configs = self.training_configs()
        if not configs:
            errors.append("利用できる学習設定TOMLがありません")
        return TrainingStatus(datasets=self._dataset_statuses(), artifacts=artifacts,
                              configured=not errors, configurationErrors=errors, trainingConfigs=configs,
                              nextOutputName=self._next_output_name())

    def _next_output_name(self) -> str:
        state = self._state()
        project_key = state.config.project.key
        if not project_key:
            return ""
        prefix = f"{datetime.now().strftime('%y%m%d')}_{project_key}_"
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)(?:$|[-_.])", re.IGNORECASE)
        output = Path(state.root_path) / self.projects.master_service.folder_map["trainedLora"]
        names = [item.stem for item in output.iterdir() if item.is_file()] if output.is_dir() else []
        names.extend(str(job.payload.get("outputName", "")) for job in self.jobs.list(10000)
                     if job.type == "lora-training" and job.project_config_path == state.config_path)
        sequence = max((int(match.group(1)) for name in names if (match := pattern.match(name))), default=0) + 1
        return f"{prefix}{sequence:03d}"

    def enqueue(self, config_name: str):
        state = self._state()
        statuses = self._dataset_statuses()
        if not statuses or sum(item.matched_pairs for item in statuses) == 0:
            raise TrainingServiceError("正常な画像・キャプションのペアがありません")
        invalid = [item.name for item in statuses if item.images_without_captions or item.captions_without_images]
        if invalid:
            raise TrainingServiceError(f"画像とキャプションが一致しないデータセットがあります: {', '.join(invalid)}")
        errors = self._configuration_errors()
        if errors:
            raise TrainingServiceError(" / ".join(errors))
        self._training_config(config_name)
        with self._enqueue_lock:
            output_name = self._next_output_name()
            return self.jobs.enqueue("lora-training", {"outputName": output_name, "configName": config_name}, state.config_path)

    async def run(self, payload: dict, log) -> None:
        state = self._state()
        _, python, script, working = self._paths()
        config = self._training_config(payload["configName"])
        train_dir = Path(state.root_path) / self.projects.master_service.folder_map["trainingDataset"]
        output_dir = Path(state.root_path) / self.projects.master_service.folder_map["trainedLora"]
        output_dir.mkdir(parents=True, exist_ok=True)
        if any(output_dir.glob(payload["outputName"] + "*")):
            raise TrainingServiceError(f"同じ連番の学習成果物が既に存在します: {payload['outputName']}")
        before = {p.resolve() for p in output_dir.iterdir() if p.is_file()}
        try:
            settings = self.projects.settings()
            request = Request(settings.comfyui_api_url.rstrip("/") + "/free",
                              data=b'{"unload_models":true,"free_memory":true}',
                              headers={"Content-Type": "application/json"}, method="POST")
            await asyncio.to_thread(lambda: urlopen(request, timeout=10).read())
            log("ComfyUIのモデルとVRAMを解放しました")
        except Exception as exc:
            log(f"ComfyUIのモデル解放を確認できませんでした（学習は続行）: {exc}")
        command = [str(python), str(script), "--config_file", str(config), "--train_data_dir", str(train_dir),
                   "--output_dir", str(output_dir), "--output_name", payload["outputName"]]
        calculated_steps = self._calculated_max_train_steps(config, train_dir)
        if calculated_steps is not None:
            command.extend(["--max_train_steps", str(calculated_steps)])
            log(f"epochと学習素材からmax_train_stepsを計算しました: {calculated_steps}")
        log(f"学習を開始します: {payload['outputName']}")
        self.process = await asyncio.create_subprocess_exec(*command, cwd=working,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert self.process.stdout is not None
        while line := await self.process.stdout.readline():
            log(line.decode("utf-8", errors="replace").rstrip())
        code = await self.process.wait()
        self.process = None
        if code != 0:
            raise TrainingServiceError(f"sd-scriptsが終了コード {code} で失敗しました")
        created = [p for p in output_dir.iterdir() if p.is_file() and p.resolve() not in before and p.suffix.lower() in self.projects.master_service.value.file_extensions.lora]
        if not created:
            expected = [p for p in output_dir.glob(payload["outputName"] + "*") if p.is_file()]
            if not expected:
                raise TrainingServiceError("学習は終了しましたが成果物を確認できません")
        log("学習成果物を07_LoRAへ保存しました")

    def deploy(self, name: str, confirm: bool) -> None:
        source, models = self._artifact_path(name), self._models()
        if models is None or not models.is_dir():
            raise TrainingServiceError("LoRA modelsフォルダが未設定または存在しません")
        target = models / source.name
        if target.exists() and _hash(source) != _hash(target) and not confirm:
            raise TrainingServiceError("同名で内容が異なります。確認後に上書きを指定してください")
        if not target.exists() or _hash(source) != _hash(target):
            shutil.copy2(source, target)

    def remove(self, name: str, confirm: bool) -> None:
        source, models = self._artifact_path(name), self._models()
        if models is None:
            raise TrainingServiceError("LoRA modelsフォルダが未設定です")
        target = models / source.name
        if not target.is_file():
            raise TrainingServiceError("modelsフォルダに配置済みコピーがありません")
        if _hash(source) != _hash(target) and not confirm:
            raise TrainingServiceError("配置先は原本と内容が異なります。確認後に削除を指定してください")
        target.unlink()

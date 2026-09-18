import json
import os
import tempfile
from pathlib import Path
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from backend.app.schemas.projects import (
    DatasetConfig,
    PersonalSettings,
    ProjectConfig,
    ProjectState,
)
from backend.app.services.native_dialog import select_project_config


FOLDERS = (
    ("sourceImages", "01_素材画像"),
    ("videos", "02_動画"),
    ("capturedFrames", "03_動画キャプチャ"),
    ("upscaledImages", "04_拡大"),
    ("generatedTags", "05_タグ付け"),
    ("trainingDataset", "06_LoRA学習素材"),
    ("trainedLora", "07_LoRA"),
)


class ProjectServiceError(ValueError):
    pass


def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


class ProjectService:
    def __init__(
        self,
        settings_directory: Path | None = None,
        file_selector: Callable[[Path | None], Path | None] = select_project_config,
    ) -> None:
        configured = os.environ.get("LORA_MAKER_SETTINGS_DIR")
        default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LoRAMaker"
        if settings_directory is not None:
            self.settings_directory = settings_directory
        elif configured:
            self.settings_directory = Path(configured)
        else:
            self.settings_directory = default
        self.settings_path = self.settings_directory / "settings.json"
        self.file_selector = file_selector
        self.current: ProjectState | None = None

    def master(self) -> dict[str, Any]:
        return {"folders": [{"key": key, "name": name} for key, name in FOLDERS]}

    def settings(self) -> PersonalSettings:
        if not self.settings_path.exists():
            return PersonalSettings()
        try:
            return PersonalSettings.model_validate_json(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise ProjectServiceError(f"個人設定を読み込めません: {exc}") from exc

    def save_settings(self, settings: PersonalSettings) -> PersonalSettings:
        _atomic_json_write(self.settings_path, settings.model_dump(by_alias=True))
        return settings

    def restore_last_project(self) -> ProjectState | None:
        try:
            path = self.settings().last_project_config_path
        except ProjectServiceError:
            return None
        if not path:
            return None
        try:
            return self.load(Path(path))
        except ProjectServiceError:
            return None

    def create(self, root: Path, config: ProjectConfig) -> ProjectState:
        root = root.expanduser().resolve()
        if root.exists() and not root.is_dir():
            raise ProjectServiceError("プロジェクトルートはフォルダを指定してください")
        config_path = root / "lora_maker.json"
        if config_path.exists():
            raise ProjectServiceError("lora_maker.json は既に存在します")
        root.mkdir(parents=True, exist_ok=True)
        for _, folder_name in FOLDERS:
            (root / folder_name).mkdir(exist_ok=True)
        for dataset in config.datasets:
            self._create_dataset_folders(root, dataset)
        _atomic_json_write(config_path, config.model_dump(by_alias=True))
        return self._activate(config_path, config)

    def load(self, config_path: Path) -> ProjectState:
        config_path = config_path.expanduser().resolve()
        if not config_path.is_file():
            raise ProjectServiceError("プロジェクト設定JSONが見つかりません")
        try:
            config = ProjectConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise ProjectServiceError(f"プロジェクト設定が不正です: {exc}") from exc
        return self._activate(config_path, config)

    def select_and_load(self) -> ProjectState | None:
        initial_path = Path(self.current.root_path) if self.current else None
        try:
            selected = self.file_selector(initial_path)
        except Exception as exc:
            raise ProjectServiceError(f"ファイル選択ダイアログを開けません: {exc}") from exc
        return self.load(selected) if selected else None

    def save(self, config: ProjectConfig) -> ProjectState:
        if self.current is None:
            raise ProjectServiceError("プロジェクトが開かれていません")
        config_path = Path(self.current.config_path)
        _atomic_json_write(config_path, config.model_dump(by_alias=True))
        return self._activate(config_path, config)

    def add_dataset(self, dataset: DatasetConfig) -> ProjectState:
        if self.current is None:
            raise ProjectServiceError("プロジェクトが開かれていません")
        config = self.current.config.model_copy(deep=True)
        if any(item.key == dataset.key for item in config.datasets):
            raise ProjectServiceError("同じデータセットキーが既に存在します")
        self._create_dataset_folders(Path(self.current.root_path), dataset)
        config.datasets.append(dataset)
        return self.save(config)

    def _read_config(self, path: Path) -> ProjectConfig:
        try:
            return ProjectConfig.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise ProjectServiceError(f"プロジェクト設定が不正です: {exc}") from exc

    def _activate(self, config_path: Path, config: ProjectConfig) -> ProjectState:
        state = ProjectState(
            configPath=str(config_path),
            rootPath=str(config_path.parent),
            config=config,
            warnings=self._warnings(config_path.parent, config),
        )
        settings = self.settings()
        settings.last_project_config_path = str(config_path)
        self.save_settings(settings)
        self.current = state
        return state

    def _create_dataset_folders(self, root: Path, dataset: DatasetConfig) -> None:
        for _, folder_name in FOLDERS[:5]:
            (root / folder_name / dataset.key).mkdir(parents=True, exist_ok=True)
        (root / FOLDERS[5][1] / f"{dataset.repeats}_{dataset.key}").mkdir(parents=True, exist_ok=True)

    def _warnings(self, root: Path, config: ProjectConfig) -> list[str]:
        warnings: list[str] = []
        for _, folder_name in FOLDERS:
            if not (root / folder_name).is_dir():
                warnings.append(f"工程フォルダがありません: {folder_name}")
        for dataset in config.datasets:
            for _, folder_name in FOLDERS[:5]:
                if not (root / folder_name / dataset.key).is_dir():
                    warnings.append(f"データセットフォルダがありません: {folder_name}/{dataset.key}")
            expected = root / FOLDERS[5][1] / f"{dataset.repeats}_{dataset.key}"
            if not expected.is_dir():
                warnings.append(f"学習素材フォルダが設定と一致しません: {expected.name}")
        return warnings

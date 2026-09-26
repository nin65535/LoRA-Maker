import json
import os
import shutil
import subprocess
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
from backend.app.services.native_dialog import select_project_config, select_project_save_path
from backend.app.services.master_service import MasterService
from backend.app.services.dataset_storage import dataset_delete_blockers


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
        save_path_selector: Callable[[Path | None], Path | None] = select_project_save_path,
        master_service: MasterService | None = None,
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
        self.save_path_selector = save_path_selector
        self.master_service = master_service or MasterService()
        self.current: ProjectState | None = None

    def master(self) -> dict[str, Any]:
        return self.master_service.value.model_dump(by_alias=True)

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

    def create(self, root: Path, config: ProjectConfig, config_path: Path | None = None) -> ProjectState:
        root = root.expanduser().resolve()
        if root.exists() and not root.is_dir():
            raise ProjectServiceError("プロジェクトルートはフォルダを指定してください")
        config_path = config_path.expanduser().resolve() if config_path else root / "lora_maker.json"
        if config_path.parent != root:
            raise ProjectServiceError("プロジェクト設定JSONはプロジェクトルート直下へ保存してください")
        if config_path.suffix.lower() != ".json":
            raise ProjectServiceError("プロジェクト設定のファイル名は .json で終わる必要があります")
        if config_path.exists():
            raise ProjectServiceError(f"プロジェクト設定JSONは既に存在します: {config_path.name}")
        root.mkdir(parents=True, exist_ok=True)
        for _, folder_name in self.master_service.folders:
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

    def select_create_config_path(self, initial_path: Path | None = None) -> Path | None:
        try:
            selected = self.save_path_selector(initial_path)
        except Exception as exc:
            raise ProjectServiceError(f"ファイル保存ダイアログを開けません: {exc}") from exc
        return selected.expanduser().resolve() if selected else None

    def close(self) -> None:
        settings = self.settings()
        settings.last_project_config_path = None
        self.save_settings(settings)
        self.current = None

    def open_source_folder(self, key: str) -> None:
        if self.current is None:
            raise ProjectServiceError("プロジェクトが開かれていません")
        if not any(dataset.key == key for dataset in self.current.config.datasets):
            raise ProjectServiceError("データセットが見つかりません")
        folder_name = dict(self.master_service.folders)["sourceImages"]
        folder = Path(self.current.root_path) / folder_name / key
        if not folder.is_dir():
            raise ProjectServiceError("素材画像フォルダが見つかりません")
        subprocess.Popen(["explorer.exe", str(folder)])

    def reload_current(self) -> ProjectState:
        if self.current is None:
            raise ProjectServiceError("プロジェクトが開かれていません")
        config_path = Path(self.current.config_path)
        return self._activate(config_path, self._read_config(config_path))

    def clear_generated_outputs(self, stage: str) -> ProjectState:
        if self.current is None:
            raise ProjectServiceError("プロジェクトが開かれていません")
        root = Path(self.current.root_path).resolve()
        folders = self.master_service.folder_map
        allowed = (
            "videos", "capturedFrames", "upscaledImages", "generatedTags",
            "trainingDataset", "trainedLora",
        )
        if stage not in allowed:
            raise ProjectServiceError("クリア対象の工程が不正です")
        target = (root / folders[stage]).resolve()
        if target.parent != root:
            raise ProjectServiceError(f"工程フォルダの場所が不正です: {folders[stage]}")
        target.mkdir(parents=True, exist_ok=True)
        for child in target.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
        if stage != "trainedLora":
            for dataset in self.current.config.datasets:
                name = f"{dataset.repeats}_{dataset.key}" if stage == "trainingDataset" else dataset.key
                (target / name).mkdir(parents=True, exist_ok=True)
        return self._activate(Path(self.current.config_path), self.current.config)

    def save(self, config: ProjectConfig) -> ProjectState:
        if self.current is None:
            raise ProjectServiceError("プロジェクトが開かれていません")
        config_path = Path(self.current.config_path)
        disk_config = self._read_config(config_path)
        if disk_config != self.current.config:
            raise ProjectServiceError(
                "プロジェクト設定が外部で変更されています。素材画像タブを開き直してください"
            )

        root = Path(self.current.root_path)
        old_by_key = {dataset.key: dataset for dataset in self.current.config.datasets}
        new_by_key = {dataset.key: dataset for dataset in config.datasets}
        for key, dataset in old_by_key.items():
            if key not in new_by_key:
                blockers = dataset_delete_blockers(root, self.master_service.folder_map, dataset)
                if blockers:
                    raise ProjectServiceError(
                        "関連データが存在するため削除できません: " + ", ".join(blockers)
                    )

        rollbacks = self._sync_training_folders(root, old_by_key, new_by_key)
        try:
            for key, dataset in new_by_key.items():
                if key not in old_by_key:
                    self._create_dataset_folders(root, dataset)
            _atomic_json_write(config_path, config.model_dump(by_alias=True))
        except Exception:
            self._rollback_training_folders(rollbacks)
            raise
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

    def remove_dataset(self, key: str) -> ProjectState:
        if self.current is None:
            raise ProjectServiceError("プロジェクトが開かれていません")
        config = self.current.config.model_copy(deep=True)
        dataset = next((dataset for dataset in config.datasets if dataset.key == key), None)
        if dataset is None:
            raise ProjectServiceError("データセットが見つかりません")
        blockers = dataset_delete_blockers(
            Path(self.current.root_path), self.master_service.folder_map, dataset
        )
        if blockers:
            raise ProjectServiceError(
                "関連データが存在するため削除できません: " + ", ".join(blockers)
            )
        config.datasets = [item for item in config.datasets if item.key != key]
        return self.save(config)

    def _read_config(self, path: Path) -> ProjectConfig:
        try:
            return ProjectConfig.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise ProjectServiceError(f"プロジェクト設定が不正です: {exc}") from exc

    def _activate(self, config_path: Path, config: ProjectConfig) -> ProjectState:
        root = config_path.parent
        selected_root = root / self.master_service.folder_map["upscaledImages"]
        if (root / "04_拡大").is_dir() and not selected_root.exists():
            selected_root.mkdir(parents=True)
            for dataset in config.datasets:
                (selected_root / dataset.key).mkdir()
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
        folders = self.master_service.folder_map
        for key in ("sourceImages", "videos", "capturedFrames", "upscaledImages", "generatedTags"):
            folder_name = folders[key]
            (root / folder_name / dataset.key).mkdir(parents=True, exist_ok=True)
        (root / folders["trainingDataset"] / f"{dataset.repeats}_{dataset.key}").mkdir(parents=True, exist_ok=True)

    def _sync_training_folders(
        self,
        root: Path,
        old_by_key: dict[str, DatasetConfig],
        new_by_key: dict[str, DatasetConfig],
    ) -> list[tuple[str, Path, Path | None]]:
        training_root = root / self.master_service.folder_map["trainingDataset"]
        training_root.mkdir(parents=True, exist_ok=True)
        changes: list[tuple[Path, Path]] = []
        removed: list[Path] = []
        for key, old_dataset in old_by_key.items():
            new_dataset = new_by_key.get(key)
            if new_dataset is None:
                removed.append(training_root / f"{old_dataset.repeats}_{key}")
                continue
            if old_dataset.repeats == new_dataset.repeats:
                continue
            old_path = training_root / f"{old_dataset.repeats}_{key}"
            new_path = training_root / f"{new_dataset.repeats}_{key}"
            if old_path.exists() and not old_path.is_dir():
                raise ProjectServiceError(f"学習素材フォルダ名と同名のファイルがあります: {old_path.name}")
            if new_path.exists() and not new_path.is_dir():
                raise ProjectServiceError(f"学習素材フォルダ名と同名のファイルがあります: {new_path.name}")
            old_has_data = old_path.is_dir() and any(old_path.iterdir())
            new_has_data = new_path.is_dir() and any(new_path.iterdir())
            if old_has_data and new_has_data:
                raise ProjectServiceError(
                    f"変更前後の学習素材フォルダにデータがあります: {old_path.name}, {new_path.name}"
                )
            changes.append((old_path, new_path))

        rollbacks: list[tuple[str, Path, Path | None]] = []
        try:
            for old_path in removed:
                if old_path.is_dir():
                    old_path.rmdir()
                    rollbacks.append(("mkdir", old_path, None))
            for old_path, new_path in changes:
                if old_path.exists() and new_path.exists():
                    if any(old_path.iterdir()):
                        new_path.rmdir()
                        old_path.rename(new_path)
                        rollbacks.append(("rename", new_path, old_path))
                    else:
                        old_path.rmdir()
                        rollbacks.append(("mkdir", old_path, None))
                elif old_path.exists():
                    try:
                        old_path.rename(new_path)
                        rollbacks.append(("rename", new_path, old_path))
                    except OSError as exc:
                        if any(old_path.iterdir()):
                            raise ProjectServiceError(
                                f"学習素材フォルダを変更できません: {old_path.name} → {new_path.name}: {exc}"
                            ) from exc
                        old_path.rmdir()
                        new_path.mkdir(parents=True, exist_ok=True)
                        rollbacks.append(("swap-empty", new_path, old_path))
                else:
                    new_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._rollback_training_folders(rollbacks)
            raise
        return rollbacks

    def _rollback_training_folders(
        self, rollbacks: list[tuple[str, Path, Path | None]]
    ) -> None:
        for action, current_path, original_path in reversed(rollbacks):
            if action == "rename" and original_path is not None:
                if current_path.exists() and not original_path.exists():
                    current_path.rename(original_path)
            elif action == "mkdir":
                current_path.mkdir(parents=True, exist_ok=True)
            elif action == "swap-empty" and original_path is not None:
                if current_path.is_dir() and not any(current_path.iterdir()):
                    current_path.rmdir()
                original_path.mkdir(parents=True, exist_ok=True)

    def _warnings(self, root: Path, config: ProjectConfig) -> list[str]:
        warnings: list[str] = []
        folders = self.master_service.folder_map
        legacy_upscale = root / "04_拡大"
        if folders.get("upscaledImages") != "04_拡大" and legacy_upscale.is_dir() and any(legacy_upscale.rglob("*")):
            warnings.append("旧04_拡大フォルダは自動移行していません。内容を確認してから保管または削除してください")
        for _, folder_name in self.master_service.folders:
            if not (root / folder_name).is_dir():
                warnings.append(f"工程フォルダがありません: {folder_name}")
        for dataset in config.datasets:
            for key in ("sourceImages", "videos", "capturedFrames", "upscaledImages", "generatedTags"):
                folder_name = folders[key]
                if not (root / folder_name / dataset.key).is_dir():
                    warnings.append(f"データセットフォルダがありません: {folder_name}/{dataset.key}")
            expected = root / folders["trainingDataset"] / f"{dataset.repeats}_{dataset.key}"
            if not expected.is_dir():
                warnings.append(f"学習素材フォルダが設定と一致しません: {expected.name}")
        return warnings

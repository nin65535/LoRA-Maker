from pathlib import Path

from pydantic import ValidationError

from backend.app.schemas.master import AppMaster


class MasterServiceError(ValueError):
    pass


class MasterService:
    """Loads and validates the application master once, then serves the cached model."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or Path(__file__).parents[3] / "app_master.json").resolve()
        try:
            self._cached = AppMaster.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise MasterServiceError(f"アプリ共通マスタを読み込めません: {self.path}: {exc}") from exc

    @property
    def value(self) -> AppMaster:
        return self._cached

    @property
    def folders(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.key, item.name) for item in self._cached.folders)

    @property
    def folder_map(self) -> dict[str, str]:
        return {item.key: item.name for item in self._cached.folders}

    def resolve_app_path(self, relative_path: str) -> Path:
        candidate = (self.path.parent / relative_path).resolve()
        if not candidate.is_relative_to(self.path.parent):
            raise MasterServiceError("アプリ共通マスタの参照先がアプリ配置場所の外です")
        return candidate

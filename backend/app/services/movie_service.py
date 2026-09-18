import asyncio
import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.app.schemas.movies import MoviePresetInfo, MovieStatus, SourceImageInfo
from backend.app.services.job_service import JobService
from backend.app.services.project_service import ProjectService


class MovieServiceError(ValueError):
    pass


class ComfyMovieGenerator:
    def __init__(self, api_url: str, workflow_path: Path, nodes: dict, timeout: int) -> None:
        self.api_url = api_url.rstrip("/")
        self.workflow_path, self.nodes, self.timeout = workflow_path, nodes, timeout

    def generate(self, image: Path, positive: str, negative: str) -> None:
        boundary = "----LoRAMakerUpload"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{image.name}"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode()
        body += image.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        uploaded = self._json("/upload/image", body, f"multipart/form-data; boundary={boundary}")
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
            for key, value in (("sourceImage", uploaded["name"]), ("positivePrompt", positive), ("negativePrompt", negative)):
                node = self.nodes[key]
                workflow[node.node_id]["inputs"][node.input_name] = value
            output_node = self.nodes["output"].node_id
            if output_node not in workflow:
                raise KeyError(output_node)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise MovieServiceError(f"動画生成ワークフローが設定と一致しません: {exc}") from exc
        prompt_id = self._json("/prompt", json.dumps({"prompt": workflow}).encode(), "application/json").get("prompt_id")
        if not prompt_id:
            raise MovieServiceError("ComfyUIからprompt_idが返されませんでした")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            item = self._json(f"/history/{prompt_id}").get(prompt_id)
            if item:
                status = item.get("status", {})
                if status.get("completed") and status.get("status_str") == "success":
                    return
                if status.get("completed"):
                    messages = status.get("messages", [])
                    raise MovieServiceError(f"ComfyUIの動画生成が失敗しました: {messages}")
            time.sleep(.5)
        raise MovieServiceError("ComfyUIの動画生成がタイムアウトしました")

    def _json(self, path: str, body: bytes | None = None, content_type: str | None = None) -> dict:
        try:
            request = Request(self.api_url + path, data=body, headers={"Content-Type": content_type} if content_type else {})
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MovieServiceError(f"ComfyUI APIへ接続できません: {exc}") from exc


class MovieService:
    def __init__(self, projects: ProjectService, jobs: JobService) -> None:
        self.projects, self.jobs = projects, jobs

    @property
    def folders(self) -> dict[str, str]:
        return self.projects.master_service.folder_map

    def _context(self, key: str):
        state = self.projects.current
        if state is None:
            raise MovieServiceError("プロジェクトが開かれていません")
        dataset = next((item for item in state.config.datasets if item.key == key), None)
        if dataset is None:
            raise MovieServiceError("データセットが見つかりません")
        return state, dataset

    def status(self) -> MovieStatus:
        state = self.projects.current
        if state is None:
            raise MovieServiceError("プロジェクトが開かれていません")
        root = Path(state.root_path)
        extensions = set(self.projects.master_service.value.file_extensions.images)
        images = []
        for dataset in state.config.datasets:
            folder = root / self.folders["sourceImages"] / dataset.key
            for path in sorted(folder.rglob("*"), key=lambda item: str(item).lower()):
                if path.is_file() and path.suffix.lower() in extensions:
                    images.append(SourceImageInfo(datasetKey=dataset.key, datasetName=dataset.name,
                                                  name=path.name, relativePath=path.relative_to(folder).as_posix()))
        presets = [MoviePresetInfo(key=item.key, name=item.name)
                   for item in self.projects.master_service.value.movie_generation.presets]
        return MovieStatus(presets=presets, images=images)

    def enqueue(self, key: str, image_path: str, preset_key: str):
        state, _ = self._context(key)
        source_root = (Path(state.root_path) / self.folders["sourceImages"] / key).resolve()
        source = (source_root / image_path).resolve()
        if not source.is_relative_to(source_root) or not source.is_file():
            raise MovieServiceError("素材画像が見つかりません")
        if source.suffix.lower() not in set(self.projects.master_service.value.file_extensions.images):
            raise MovieServiceError("未対応の画像形式です")
        if not any(item.key == preset_key for item in self.projects.master_service.value.movie_generation.presets):
            raise MovieServiceError("動画生成プリセットが見つかりません")
        self._output_folder(state.root_path)  # destructive work starts only after safety validation
        return self.jobs.enqueue("movie-generation", {
            "datasetKey": key, "imagePath": image_path, "presetKey": preset_key,
            "projectConfigPath": state.config_path,
        }, state.config_path)

    def _output_folder(self, project_root: str) -> Path:
        value = self.projects.settings().comfyui_movie_output_path
        if not value:
            raise MovieServiceError("個人設定でComfyUI動画専用出力フォルダを設定してください")
        path = Path(value).expanduser().resolve()
        project = Path(project_root).resolve()
        if not path.is_dir():
            raise MovieServiceError("ComfyUI動画専用出力フォルダが見つかりません")
        if path.parent == path or project == path or project.is_relative_to(path):
            raise MovieServiceError("ComfyUI動画専用出力フォルダに危険な上位フォルダは指定できません")
        return path

    @staticmethod
    def _clear(folder: Path) -> None:
        for child in folder.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    async def run(self, payload: dict, log: Callable[[str], None]) -> None:
        state, _ = self._context(payload["datasetKey"])
        if state.config_path != payload["projectConfigPath"]:
            raise MovieServiceError("ジョブ登録時と異なるプロジェクトが開かれています")
        key, image_path, preset_key = payload["datasetKey"], payload["imagePath"], payload["presetKey"]
        source_root = (Path(state.root_path) / self.folders["sourceImages"] / key).resolve()
        source = (source_root / image_path).resolve()
        if not source.is_relative_to(source_root) or not source.is_file():
            raise MovieServiceError("素材画像が見つかりません")
        preset = next((item for item in self.projects.master_service.value.movie_generation.presets if item.key == preset_key), None)
        if preset is None:
            raise MovieServiceError("動画生成プリセットが見つかりません")
        temporary = self._output_folder(state.root_path)
        existing = [item for item in temporary.rglob("*") if item.is_file()]
        if existing:
            log(f"専用一時出力に残っていた {len(existing)} 件を削除します")
        self._clear(temporary)
        config = self.projects.master_service.value.movie_generation
        generator = ComfyMovieGenerator(self.projects.settings().comfyui_api_url,
            self.projects.master_service.resolve_app_path(config.workflow_path), config.nodes, config.timeout_seconds)
        log(f"{source.name} / {preset.name} をComfyUIへ送信します")
        await asyncio.to_thread(generator.generate, source, preset.positive_prompt, preset.negative_prompt)
        outputs = [item for item in temporary.rglob("*") if item.is_file()]
        videos = [item for item in outputs if item.suffix.lower() in set(self.projects.master_service.value.file_extensions.videos)]
        if len(videos) != 1 or len(outputs) != 1:
            raise MovieServiceError(f"専用一時出力から動画1本を特定できません（全{len(outputs)}件、動画{len(videos)}件）")
        destination_dir = Path(state.root_path) / self.folders["videos"] / key
        destination_dir.mkdir(parents=True, exist_ok=True)
        pattern = re.compile(rf"^{re.escape(preset_key)}_(\d{{3,}})$", re.IGNORECASE)
        numbers = [int(match.group(1)) for item in destination_dir.iterdir()
                   if item.is_file() and (match := pattern.match(item.stem))]
        number = max(numbers, default=0) + 1
        destination = destination_dir / f"{preset_key}_{number:03d}{videos[0].suffix.lower()}"
        if destination.exists():
            raise MovieServiceError(f"保存先が既に存在するため上書きできません: {destination.name}")
        shutil.copy2(videos[0], destination)
        if (not destination.is_file() or destination.stat().st_size == 0
                or destination.stat().st_size != videos[0].stat().st_size):
            destination.unlink(missing_ok=True)
            raise MovieServiceError("移動先の動画を確認できません。一時出力を保全します")
        try:
            self._clear(temporary)
        except OSError as exc:
            raise MovieServiceError(f"動画は保存しましたが、一時出力を空にできません: {exc}") from exc
        log(f"{destination.name} を保存しました")

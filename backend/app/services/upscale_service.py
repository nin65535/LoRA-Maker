import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.app.schemas.jobs import JobStatus
from backend.app.schemas.upscale import CaptureFolderStatus, SelectionTarget, UpscaleStatus
from backend.app.services.job_service import JobService
from backend.app.services.project_service import ProjectService, _atomic_json_write


class UpscaleServiceError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ComfyUpscaler:
    def __init__(self, api_url: str, workflow_path: Path, source_node: str,
                 source_input: str, output_node: str, output_input: str, timeout: int) -> None:
        self.api_url = api_url.rstrip("/")
        self.workflow_path = workflow_path
        self.source_node, self.source_input = source_node, source_input
        self.output_node, self.output_input = output_node, output_input
        self.timeout = timeout

    def upscale(self, image: Path) -> bytes:
        boundary = "----LoRAMakerUpload"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{image.name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode()
        body += image.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        uploaded = self._json("/upload/image", body, f"multipart/form-data; boundary={boundary}")
        workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        workflow[self.source_node]["inputs"][self.source_input] = uploaded["name"]
        workflow[self.output_node]["inputs"][self.output_input] = f"lora_maker_upscale/{image.stem}"
        prompt_id = self._json("/prompt", json.dumps({"prompt": workflow}).encode(), "application/json")["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            item = self._json(f"/history/{prompt_id}").get(prompt_id)
            if item:
                status = item.get("status", {})
                if status.get("completed") and status.get("status_str") == "success":
                    return self._output(item)
                if status.get("completed"):
                    raise UpscaleServiceError("ComfyUIの画像拡大処理が失敗しました")
            time.sleep(.5)
        raise UpscaleServiceError("ComfyUIの画像拡大処理がタイムアウトしました")

    def _output(self, history: dict) -> bytes:
        output = history.get("outputs", {}).get(self.output_node, {})
        for values in output.values():
            if not isinstance(values, list):
                continue
            for file in values:
                if isinstance(file, dict) and file.get("filename"):
                    query = urlencode({key: file.get(key, "") for key in ("filename", "subfolder", "type")})
                    try:
                        with urlopen(f"{self.api_url}/view?{query}", timeout=60) as response:
                            data = response.read()
                    except (HTTPError, URLError, TimeoutError) as exc:
                        raise UpscaleServiceError(f"拡大画像を取得できません: {exc}") from exc
                    if data:
                        return data
        raise UpscaleServiceError("ComfyUIの拡大画像出力を取得できませんでした")

    def _json(self, path: str, body: bytes | None = None, content_type: str | None = None) -> dict:
        try:
            request = Request(self.api_url + path, data=body, headers={"Content-Type": content_type} if content_type else {})
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UpscaleServiceError(f"ComfyUI APIへ接続できません: {exc}") from exc


class UpscaleService:
    def __init__(self, projects: ProjectService, jobs: JobService) -> None:
        self.projects, self.jobs = projects, jobs
        self.state_path = projects.settings_directory / "selection.json"
        self.history_path = projects.settings_directory / "upscale_history.sqlite3"

    def _history_connection(self) -> sqlite3.Connection:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.history_path)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS upscale_history (
                project_config_path TEXT NOT NULL,
                dataset_key TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                output_sha256 TEXT NOT NULL,
                training_path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_config_path, dataset_key, source_sha256)
            )
        """)
        connection.commit()
        return connection

    def _source_history(self, project_path: str, dataset_key: str, digest: str) -> Path | None:
        with closing(self._history_connection()) as connection:
            row = connection.execute(
                "SELECT training_path, output_sha256 FROM upscale_history WHERE project_config_path = ? AND dataset_key = ? AND source_sha256 = ?",
                (project_path, dataset_key, digest),
            ).fetchone()
        if not row:
            return None
        output = Path(row[0])
        if output.is_file() and _sha256(output) == row[1]:
            return output
        return None

    def _save_history(self, project_path: str, dataset_key: str,
                      records: list[tuple[str, str, Path]]) -> None:
        with closing(self._history_connection()) as connection:
            connection.executemany(
                """INSERT INTO upscale_history (project_config_path, dataset_key, source_sha256, output_sha256, training_path)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(project_config_path, dataset_key, source_sha256) DO UPDATE SET
                     output_sha256 = excluded.output_sha256,
                     training_path = excluded.training_path,
                     created_at = CURRENT_TIMESTAMP""",
                [(project_path, dataset_key, source_hash, output_hash, str(path))
                 for source_hash, output_hash, path in records],
            )
            connection.commit()

    @property
    def image_extensions(self) -> set[str]:
        return set(self.projects.master_service.value.file_extensions.images)

    @property
    def folders(self) -> dict[str, str]:
        return self.projects.master_service.folder_map

    def _context(self, key: str):
        state = self.projects.current
        if state is None:
            raise UpscaleServiceError("プロジェクトが開かれていません")
        dataset = next((item for item in state.config.datasets if item.key == key), None)
        if dataset is None:
            raise UpscaleServiceError("データセットが見つかりません")
        return state, dataset

    def _target(self) -> SelectionTarget | None:
        if not self.state_path.is_file():
            return None
        try:
            return SelectionTarget.model_validate_json(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UpscaleServiceError(f"選別状態を読み込めません: {exc}") from exc

    def _selection(self, required: bool = False) -> Path | None:
        value = self.projects.settings().bandiview_selection_path
        if not value:
            if required:
                raise UpscaleServiceError("個人設定でBandiView画像保存フォルダを設定してください")
            return None
        path = Path(value).expanduser().resolve()
        if path.exists() and not path.is_dir():
            raise UpscaleServiceError("BandiView画像保存先はフォルダを指定してください")
        if path.parent == path:
            raise UpscaleServiceError("ドライブのルートはBandiView画像保存先に指定できません")
        state = self.projects.current
        if state and path == Path(state.root_path).resolve():
            raise UpscaleServiceError("プロジェクトルートはBandiView画像保存先に指定できません")
        return path

    def _images(self, folder: Path) -> list[Path]:
        return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in self.image_extensions), key=lambda p: p.name.lower()) if folder.is_dir() else []

    def status(self) -> UpscaleStatus:
        state = self.projects.current
        if state is None:
            raise UpscaleServiceError("プロジェクトが開かれていません")
        target, selection = self._target(), self._selection()
        selected_count = len(self._images(selection)) if selection else 0
        rows = []
        root = Path(state.root_path)
        recent = {}
        for job in self.jobs.list(500):
            if job.type == "image-upscale":
                key = (job.payload.get("datasetKey"), job.payload.get("captureFolder"))
                recent.setdefault(key, job)
        for dataset in state.config.datasets:
            capture_root = root / self.folders["capturedFrames"] / dataset.key
            upscale_root = root / self.folders["upscaledImages"] / dataset.key
            for folder in sorted((p for p in capture_root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
                is_selected = bool(target and target.project_config_path == state.config_path and target.dataset_key == dataset.key and target.capture_folder == folder.name)
                job = recent.get((dataset.key, folder.name))
                row_state = "selected" if is_selected else "idle"
                if job and job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED):
                    row_state = "failed" if job.status == JobStatus.FAILED else job.status.value
                prefix = f"{folder.name}_"
                rows.append(CaptureFolderStatus(
                    datasetKey=dataset.key, datasetName=dataset.name, captureFolder=folder.name,
                    captureCount=len(self._images(folder)),
                    upscaledCount=sum(1 for p in self._images(upscale_root) if p.name.startswith(prefix)),
                    selected=is_selected, selectedImageCount=selected_count if is_selected else 0,
                    state=row_state, jobId=job.id if job else None,
                    error=job.error if job and job.status == JobStatus.FAILED else None,
                ))
        return UpscaleStatus(selectionPath=str(selection) if selection else None, activeTarget=target, folders=rows)

    def start_selection(self, key: str, capture_folder: str) -> SelectionTarget:
        state, _ = self._context(key)
        source = (Path(state.root_path) / self.folders["capturedFrames"] / key / capture_folder).resolve()
        capture_root = (Path(state.root_path) / self.folders["capturedFrames"] / key).resolve()
        if not source.is_relative_to(capture_root) or not source.is_dir():
            raise UpscaleServiceError("キャプチャフォルダが見つかりません")
        selection = self._selection(True)
        existing = self._images(selection)
        current = self._target()
        same = bool(current and current.project_config_path == state.config_path and current.dataset_key == key and current.capture_folder == capture_folder)
        if existing and not same:
            raise UpscaleServiceError("別対象の選別画像が残っています。先に現在の対象を拡大してください")
        executable_value = self.projects.settings().bandiview_path
        if not executable_value:
            raise UpscaleServiceError("個人設定でBandiView実行ファイルを設定してください")
        executable = Path(executable_value).expanduser().resolve()
        if not executable.is_file():
            raise UpscaleServiceError("BandiView実行ファイルが見つかりません")
        selection.mkdir(parents=True, exist_ok=True)
        target = SelectionTarget(projectConfigPath=state.config_path, datasetKey=key, captureFolder=capture_folder)
        _atomic_json_write(self.state_path, target.model_dump(by_alias=True))
        try:
            subprocess.Popen([str(executable), str(source)])
        except OSError as exc:
            self.state_path.unlink(missing_ok=True)
            raise UpscaleServiceError(f"BandiViewを起動できません: {exc}") from exc
        return target

    def enqueue(self, key: str, capture_folder: str):
        state, _ = self._context(key)
        target = self._target()
        if not target or target.project_config_path != state.config_path or target.dataset_key != key or target.capture_folder != capture_folder:
            raise UpscaleServiceError("このフォルダは現在の選別対象ではありません")
        selection = self._selection(True)
        if not self._images(selection):
            raise UpscaleServiceError("BandiView画像保存フォルダに選別画像がありません")
        if any(job.type == "image-upscale" and job.status in (JobStatus.QUEUED, JobStatus.RUNNING) for job in self.jobs.list()):
            raise UpscaleServiceError("画像拡大ジョブが既に待機中または実行中です")
        return self.jobs.enqueue("image-upscale", {
            "datasetKey": key, "captureFolder": capture_folder, "projectConfigPath": state.config_path,
        }, state.config_path)

    async def run(self, payload: dict, log: Callable[[str], None]) -> None:
        key, capture_folder = payload["datasetKey"], payload["captureFolder"]
        state, dataset = self._context(key)
        if state.config_path != payload["projectConfigPath"]:
            raise UpscaleServiceError("ジョブ登録時と異なるプロジェクトが開かれています")
        target = self._target()
        if not target or target.project_config_path != state.config_path or target.dataset_key != key or target.capture_folder != capture_folder:
            raise UpscaleServiceError("選別対象がジョブ登録時から変更されています")
        selection = self._selection(True)
        images = self._images(selection)
        if not images:
            raise UpscaleServiceError("選別画像がありません")
        unexpected = [item.name for item in selection.iterdir() if not item.is_file() or item.suffix.lower() not in self.image_extensions]
        if unexpected:
            raise UpscaleServiceError(f"画像保存フォルダに未対応の項目があります: {', '.join(unexpected)}")
        source_hashes: list[str] = []
        seen_sources: dict[str, str] = {}
        for image in images:
            digest = _sha256(image)
            if digest in seen_sources:
                raise UpscaleServiceError(f"今回の選別画像に同一の元画像が含まれています: {seen_sources[digest]} / {image.name}")
            seen_sources[digest] = image.name
            previous = self._source_history(state.config_path, key, digest)
            if previous:
                raise UpscaleServiceError(f"この元画像は拡大済みです: {image.name} → {previous.name}（ComfyUI処理は開始していません）")
            source_hashes.append(digest)
        root = Path(state.root_path)
        primary = root / self.folders["upscaledImages"] / key
        training = root / self.folders["trainingDataset"] / f"{dataset.repeats}_{key}"
        names = [f"{capture_folder}_{image.stem}.png" for image in images]
        if len(set(name.lower() for name in names)) != len(names):
            raise UpscaleServiceError("拡張子だけが異なる同名画像があり、出力名が衝突します")
        conflicts = [name for name in names if (primary / name).exists()]
        if conflicts:
            raise UpscaleServiceError(f"出力先に同名ファイルがあるため上書きできません: {', '.join(conflicts)}")
        config = self.projects.master_service.value.image_upscale
        source, output = config.nodes["sourceImage"], config.nodes["output"]
        upscaler = ComfyUpscaler(self.projects.settings().comfyui_api_url,
            self.projects.master_service.resolve_app_path(config.workflow_path),
            source.node_id, source.input_name or "image", output.node_id,
            output.input_name or "filename_prefix", config.timeout_seconds)
        primary.mkdir(parents=True, exist_ok=True)
        training.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".upscale-", dir=primary))
        created: list[Path] = []
        try:
            for index, (image, name) in enumerate(zip(images, names, strict=True), 1):
                data = await asyncio.to_thread(upscaler.upscale, image)
                if not data:
                    raise UpscaleServiceError(f"{image.name} の拡大結果が空です")
                (staging / name).write_bytes(data)
                log(f"{index}/{len(images)} {image.name} を拡大しました")
            existing_images = self._images(training)
            by_size: dict[int, list[Path]] = {}
            for existing in existing_images:
                by_size.setdefault(existing.stat().st_size, []).append(existing)
            existing_hashes: dict[Path, str] = {}
            seen_new: dict[tuple[int, str], str] = {}
            for name in names:
                staged = staging / name
                digest = _sha256(staged)
                signature = (staged.stat().st_size, digest)
                duplicate = None
                for item in by_size.get(signature[0], []):
                    if item not in existing_hashes:
                        existing_hashes[item] = _sha256(item)
                    if existing_hashes[item] == digest:
                        duplicate = item
                        break
                if duplicate:
                    raise UpscaleServiceError(f"同一内容の学習画像が既にあります: {duplicate.name}（選別画像は保持しました）")
                if signature in seen_new:
                    raise UpscaleServiceError(f"今回の選別画像に同一内容が含まれています: {seen_new[signature]} / {name}（選別画像は保持しました）")
                seen_new[signature] = name
            sequence = max((int(item.stem[7:]) for item in existing_images
                            if item.suffix.lower() == ".png" and item.stem.startswith("sample_")
                            and len(item.stem) == 13 and item.stem[7:].isdigit()), default=0)
            training_names = [f"sample_{sequence + index:06d}.png" for index in range(1, len(names) + 1)]
            training_conflicts = [name for name in training_names if (training / name).exists()]
            if training_conflicts:
                raise UpscaleServiceError(f"学習素材の連番出力先が既に存在します: {', '.join(training_conflicts)}")
            for name, training_name in zip(names, training_names, strict=True):
                source_file = staging / name
                for destination in (primary / name, training / training_name):
                    shutil.copy2(source_file, destination)
                    created.append(destination)
                    if not destination.is_file() or destination.stat().st_size != source_file.stat().st_size:
                        raise UpscaleServiceError(f"保存結果を確認できません: {destination}")
            records = [(source_hash, _sha256(staging / name), training / training_name)
                       for source_hash, name, training_name in zip(source_hashes, names, training_names, strict=True)]
            self._save_history(state.config_path, key, records)
            for image in images:
                image.unlink()
            self.state_path.unlink(missing_ok=True)
            log(f"{len(images)} 枚を04_拡大と06_LoRA学習素材へ保存しました")
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
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
        self.bandiview_process: subprocess.Popen | None = None
        self.bandiview_error: str | None = None

    def _bandiview_running(self) -> bool:
        return self.bandiview_process is not None

    def _wait_for_bandiview(self, process: subprocess.Popen, target: SelectionTarget,
                            selection: Path) -> None:
        try:
            process.wait()
            self._import_selection(target, selection)
        except Exception as exc:
            self.bandiview_error = str(exc)
            self.jobs.publish(f"bandiview-import-failed:{exc}")
        finally:
            if self.bandiview_process is process:
                self.bandiview_process = None
            self.jobs.publish("bandiview-finished")

    def _selected_folder(self, target: SelectionTarget) -> Path:
        config_path = Path(target.project_config_path).resolve()
        if not config_path.is_file():
            raise UpscaleServiceError("選別開始時のプロジェクト設定が見つかりません")
        return config_path.parent / self.folders["upscaledImages"] / target.dataset_key / target.capture_folder

    def _import_selection(self, target: SelectionTarget, selection: Path) -> None:
        images = self._images(selection)
        destination = self._selected_folder(target)
        destination.mkdir(parents=True, exist_ok=True)
        conflicts = [item.name for item in images if (destination / item.name).exists()]
        if conflicts:
            raise UpscaleServiceError(f"選別済み保存先に同名ファイルがあります: {', '.join(conflicts)}")
        moved: list[tuple[Path, Path]] = []
        try:
            for image in images:
                target_path = destination / image.name
                shutil.move(str(image), target_path)
                moved.append((target_path, image))
        except Exception:
            for target_path, original in reversed(moved):
                if target_path.exists() and not original.exists():
                    shutil.move(str(target_path), original)
            raise
        self.state_path.unlink(missing_ok=True)

    def _history_connection(self) -> sqlite3.Connection:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.history_path)
        columns = connection.execute("PRAGMA table_info(upscale_history)").fetchall()
        if columns and not any(row[1] == "scale" for row in columns):
            connection.execute("ALTER TABLE upscale_history RENAME TO upscale_history_legacy")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS upscale_history (
                project_config_path TEXT NOT NULL,
                dataset_key TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                scale INTEGER NOT NULL,
                output_sha256 TEXT NOT NULL,
                training_path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_config_path, dataset_key, source_sha256, scale)
            )
        """)
        connection.commit()
        return connection

    def clear_project_history(self, project_path: str) -> None:
        with closing(self._history_connection()) as connection:
            connection.execute(
                "DELETE FROM upscale_history WHERE project_config_path = ?",
                (project_path,),
            )
            connection.commit()

    def _source_history(self, project_path: str, dataset_key: str, digest: str, scale: int) -> Path | None:
        with closing(self._history_connection()) as connection:
            row = connection.execute(
                "SELECT training_path, output_sha256 FROM upscale_history WHERE project_config_path = ? AND dataset_key = ? AND source_sha256 = ? AND scale = ?",
                (project_path, dataset_key, digest, scale),
            ).fetchone()
        if not row:
            return None
        output = Path(row[0])
        if output.is_file() and _sha256(output) == row[1]:
            return output
        return None

    def _save_history(self, project_path: str, dataset_key: str,
                      scale: int, records: list[tuple[str, str, Path]]) -> None:
        with closing(self._history_connection()) as connection:
            connection.executemany(
                """INSERT INTO upscale_history (project_config_path, dataset_key, source_sha256, scale, output_sha256, training_path)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_config_path, dataset_key, source_sha256, scale) DO UPDATE SET
                     output_sha256 = excluded.output_sha256,
                     training_path = excluded.training_path,
                     created_at = CURRENT_TIMESTAMP""",
                [(project_path, dataset_key, source_hash, scale, output_hash, str(path))
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
        target = self._target()
        rows = []
        root = Path(state.root_path)
        recent = {}
        for job in self.jobs.list(500):
            if job.type == "image-upscale":
                key = (job.payload.get("datasetKey"), job.payload.get("captureFolder"))
                recent.setdefault(key, job)
        for dataset in state.config.datasets:
            capture_root = root / self.folders["capturedFrames"] / dataset.key
            selected_root = root / self.folders["upscaledImages"] / dataset.key
            for folder in sorted((p for p in capture_root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
                is_selected = bool(target and target.project_config_path == state.config_path and target.dataset_key == dataset.key and target.capture_folder == folder.name)
                selected_images = self._images(selected_root / folder.name)
                scale1_count = sum(self._source_history(state.config_path, dataset.key, _sha256(item), 1) is not None for item in selected_images)
                scale2_count = sum(self._source_history(state.config_path, dataset.key, _sha256(item), 2) is not None for item in selected_images)
                job = recent.get((dataset.key, folder.name))
                row_state = "selected" if selected_images or is_selected else "idle"
                if job and job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED):
                    row_state = "failed" if job.status == JobStatus.FAILED else job.status.value
                prefix = f"{folder.name}_"
                rows.append(CaptureFolderStatus(
                    datasetKey=dataset.key, datasetName=dataset.name, captureFolder=folder.name,
                    captureCount=len(self._images(folder)),
                    selected=is_selected, selectedImageCount=len(selected_images),
                    scale1ProcessedCount=scale1_count, scale2ProcessedCount=scale2_count,
                    state=row_state, jobId=job.id if job else None,
                    error=(job.error if job and job.status == JobStatus.FAILED else
                           self.bandiview_error if is_selected else None),
                ))
        return UpscaleStatus(
            selectionPath=str(self._selection()) if self._selection() else None,
            activeTarget=target,
            bandiviewRunning=self._bandiview_running(),
            folders=rows,
        )

    def start_selection(self, key: str, capture_folder: str) -> SelectionTarget:
        state, _ = self._context(key)
        if self._bandiview_running():
            raise UpscaleServiceError("BandiViewの起動用プロセスが実行中です")
        self.bandiview_error = None
        source = (Path(state.root_path) / self.folders["capturedFrames"] / key / capture_folder).resolve()
        capture_root = (Path(state.root_path) / self.folders["capturedFrames"] / key).resolve()
        if not source.is_relative_to(capture_root) or not source.is_dir():
            raise UpscaleServiceError("キャプチャフォルダが見つかりません")
        selection = self._selection(True)
        existing = self._images(selection)
        if existing:
            raise UpscaleServiceError("BandiView画像保存フォルダに未取り込みの選別画像が残っています")
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
            process = subprocess.Popen([str(executable), str(source)])
            self.bandiview_process = process
            threading.Thread(
                target=self._wait_for_bandiview,
                args=(process, target, selection),
                name="bandiview-waiter",
                daemon=True,
            ).start()
        except OSError as exc:
            self.state_path.unlink(missing_ok=True)
            raise UpscaleServiceError(f"BandiViewを起動できません: {exc}") from exc
        return target

    def enqueue(self, key: str, capture_folder: str, scale: int):
        state, _ = self._context(key)
        capture_root = (Path(state.root_path) / self.folders["capturedFrames"] / key).resolve()
        capture = (capture_root / capture_folder).resolve()
        if not capture.is_relative_to(capture_root) or not capture.is_dir():
            raise UpscaleServiceError("キャプチャフォルダが見つかりません")
        selected_root = (Path(state.root_path) / self.folders["upscaledImages"] / key).resolve()
        selected = (selected_root / capture_folder).resolve()
        if not selected.is_relative_to(selected_root):
            raise UpscaleServiceError("選別済みフォルダの場所が不正です")
        images = self._images(selected)
        if not images:
            raise UpscaleServiceError("プロジェクト内に選別済み画像がありません")
        active = [job for job in self.jobs.list() if job.type == "image-upscale"
                  and job.status in (JobStatus.QUEUED, JobStatus.RUNNING)]
        if any(job.payload.get("datasetKey") == key and job.payload.get("captureFolder") == capture_folder
               and int(job.payload.get("scale", 0)) == scale for job in active):
            raise UpscaleServiceError(f"この対象の拡大×{scale}は既に待機中または実行中です")
        pending = []
        for image in images:
            digest = _sha256(image)
            if self._source_history(state.config_path, key, digest, scale) is None:
                pending.append({"name": image.name, "sha256": digest})
        if not pending:
            raise UpscaleServiceError(f"選別画像はすべて拡大×{scale}で学習素材へ配置済みです")
        return self.jobs.enqueue("image-upscale", {
            "datasetKey": key, "captureFolder": capture_folder, "scale": scale,
            "projectConfigPath": state.config_path, "images": pending,
        }, state.config_path)

    async def run(self, payload: dict, log: Callable[[str], None]) -> None:
        key, capture_folder = payload["datasetKey"], payload["captureFolder"]
        scale = int(payload["scale"])
        if scale not in (1, 2):
            raise UpscaleServiceError("拡大倍率は1または2を指定してください")
        state, dataset = self._context(key)
        if state.config_path != payload["projectConfigPath"]:
            raise UpscaleServiceError("ジョブ登録時と異なるプロジェクトが開かれています")
        selection = Path(state.root_path) / self.folders["upscaledImages"] / key / capture_folder
        requested = payload.get("images", [])
        images = [selection / item["name"] for item in requested]
        if not images:
            raise UpscaleServiceError("選別画像がありません")
        for image, expected in zip(images, requested, strict=True):
            if not image.is_file() or _sha256(image) != expected["sha256"]:
                raise UpscaleServiceError(f"ジョブ登録後に選別画像が変更されました: {image.name}")
        source_hashes: list[str] = []
        seen_sources: dict[str, str] = {}
        for image in images:
            digest = _sha256(image)
            if digest in seen_sources:
                raise UpscaleServiceError(f"今回の選別画像に同一の元画像が含まれています: {seen_sources[digest]} / {image.name}")
            seen_sources[digest] = image.name
            previous = self._source_history(state.config_path, key, digest, scale)
            if previous:
                raise UpscaleServiceError(f"この元画像は拡大済みです: {image.name} → {previous.name}（ComfyUI処理は開始していません）")
            source_hashes.append(digest)
        root = Path(state.root_path)
        training = root / self.folders["trainingDataset"] / f"{dataset.repeats}_{key}"
        names = [f"{capture_folder}_{image.stem}{'.png' if scale == 2 else image.suffix.lower()}" for image in images]
        if len(set(name.lower() for name in names)) != len(names):
            raise UpscaleServiceError("拡張子だけが異なる同名画像があり、出力名が衝突します")
        upscaler = None
        if scale == 2:
            config = self.projects.master_service.value.image_upscale
            source, output = config.nodes["sourceImage"], config.nodes["output"]
            upscaler = ComfyUpscaler(self.projects.settings().comfyui_api_url,
                self.projects.master_service.resolve_app_path(config.workflow_path),
                source.node_id, source.input_name or "image", output.node_id,
                output.input_name or "filename_prefix", config.timeout_seconds)
        training.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".upscale-", dir=training))
        created: list[Path] = []
        try:
            for index, (image, name) in enumerate(zip(images, names, strict=True), 1):
                staged = staging / name
                if scale == 2:
                    data = await asyncio.to_thread(upscaler.upscale, image)
                    if not data:
                        raise UpscaleServiceError(f"{image.name} の拡大結果が空です")
                    staged.write_bytes(data)
                    log(f"{index}/{len(images)} {image.name} を2倍スケールへ拡大しました")
                else:
                    shutil.copy2(image, staged)
                    log(f"{index}/{len(images)} {image.name} を等倍で配置準備しました")
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
                            if item.stem.startswith("sample_")
                            and len(item.stem) == 13 and item.stem[7:].isdigit()), default=0)
            training_names = [f"sample_{sequence + index:06d}{Path(name).suffix.lower()}"
                              for index, name in enumerate(names, 1)]
            training_conflicts = [name for name in training_names if (training / name).exists()]
            if training_conflicts:
                raise UpscaleServiceError(f"学習素材の連番出力先が既に存在します: {', '.join(training_conflicts)}")
            for name, training_name in zip(names, training_names, strict=True):
                source_file = staging / name
                destination = training / training_name
                shutil.copy2(source_file, destination)
                created.append(destination)
                if not destination.is_file() or destination.stat().st_size != source_file.stat().st_size:
                    raise UpscaleServiceError(f"保存結果を確認できません: {destination}")
            records = [(source_hash, _sha256(staging / name), training / training_name)
                       for source_hash, name, training_name in zip(source_hashes, names, training_names, strict=True)]
            self._save_history(state.config_path, key, scale, records)
            log(f"{len(images)} 枚を{scale}倍スケールで06_LoRA学習素材へ保存しました")
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self.jobs.enqueue("tagger", {
            "datasetKey": key,
            "projectConfigPath": state.config_path,
            "imageNames": training_names,
        }, state.config_path)
        log(f"追加した {len(training_names)} 枚の自動タグ付けを登録しました")

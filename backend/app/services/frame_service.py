import asyncio
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from backend.app.schemas.frames import FrameDatasetStatus, VideoFrameStatus
from backend.app.schemas.jobs import JobStatus
from backend.app.services.job_service import JobService
from backend.app.services.project_service import ProjectService


class FrameServiceError(ValueError):
    pass


class FrameService:
    def __init__(self, projects: ProjectService, jobs: JobService) -> None:
        self.projects = projects
        self.jobs = jobs

    @property
    def folders(self) -> dict[str, str]:
        return self.projects.master_service.folder_map

    @property
    def video_extensions(self) -> set[str]:
        return set(self.projects.master_service.value.file_extensions.videos)

    @property
    def image_extensions(self) -> set[str]:
        return set(self.projects.master_service.value.file_extensions.images)

    def _context(self, key: str):
        state = self.projects.current
        if state is None:
            raise FrameServiceError("プロジェクトが開かれていません")
        dataset = next((item for item in state.config.datasets if item.key == key), None)
        if dataset is None:
            raise FrameServiceError("データセットが見つかりません")
        return state, dataset

    def _videos(self, key: str) -> tuple[Path, Path, list[Path]]:
        state, _ = self._context(key)
        root = Path(state.root_path)
        source = root / self.folders["videos"] / key
        target = root / self.folders["capturedFrames"] / key
        videos = sorted(
            (item for item in source.iterdir() if item.is_file() and item.suffix.lower() in self.video_extensions),
            key=lambda item: item.name.lower(),
        )
        return source, target, videos

    def status(self, key: str) -> FrameDatasetStatus:
        source, target, videos = self._videos(key)
        stem_counts: dict[str, int] = {}
        for video in videos:
            stem_counts[video.stem.lower()] = stem_counts.get(video.stem.lower(), 0) + 1
        active_or_failed = {}
        for job in self.jobs.list(500):
            if job.type != "frame-extraction" or job.payload.get("datasetKey") != key:
                continue
            name = str(job.payload.get("videoName", ""))
            if name and name not in active_or_failed:
                active_or_failed[name] = job
        rows: list[VideoFrameStatus] = []
        for video in videos:
            output = target / video.stem
            frames = sum(1 for item in output.iterdir() if item.is_file() and item.suffix.lower() in self.image_extensions) if output.is_dir() else 0
            job = active_or_failed.get(video.name)
            state = "extracted" if frames else "unprocessed"
            if not frames and job and job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED):
                state = "failed" if job.status == JobStatus.FAILED else job.status.value
            conflict = "同名の動画が複数あるため出力先を特定できません" if stem_counts[video.stem.lower()] > 1 else None
            rows.append(VideoFrameStatus(
                name=video.name,
                relativePath=video.relative_to(source).as_posix(),
                frameCount=frames,
                state=state,
                jobId=job.id if job else None,
                error=job.error if job and job.status == JobStatus.FAILED else None,
                conflict=conflict,
            ))
        return FrameDatasetStatus(datasetKey=key, videos=rows)

    def enqueue(self, key: str, video_name: str):
        state, _ = self._context(key)
        _, _, videos = self._videos(key)
        matches = [item for item in videos if item.name == video_name]
        if not matches:
            raise FrameServiceError("動画が見つかりません")
        if sum(item.stem.lower() == matches[0].stem.lower() for item in videos) > 1:
            raise FrameServiceError("同名の動画が複数あり、出力先が衝突します")
        current = next((item for item in self.status(key).videos if item.name == video_name), None)
        if current and current.frame_count:
            raise FrameServiceError("抽出済みフレームがあるため上書きできません")
        if current and current.state in ("queued", "running"):
            raise FrameServiceError("この動画は既に処理待ちまたは実行中です")
        return self.jobs.enqueue("frame-extraction", {
            "datasetKey": key, "videoName": video_name, "projectConfigPath": state.config_path,
        }, state.config_path)

    def enqueue_unprocessed(self, key: str):
        status = self.status(key)
        jobs, skipped = [], []
        for video in status.videos:
            if video.state == "unprocessed" and not video.conflict:
                jobs.append(self.enqueue(key, video.name))
            else:
                skipped.append(video.name)
        return jobs, skipped

    def enqueue_all_unprocessed(self):
        state = self.projects.current
        if state is None:
            raise FrameServiceError("プロジェクトが開かれていません")
        jobs, skipped = [], []
        for dataset in state.config.datasets:
            dataset_jobs, dataset_skipped = self.enqueue_unprocessed(dataset.key)
            jobs.extend(dataset_jobs)
            skipped.extend(f"{dataset.key}/{name}" for name in dataset_skipped)
        return jobs, skipped

    def open_output(self, key: str, video_name: str) -> None:
        _, target, videos = self._videos(key)
        video = next((item for item in videos if item.name == video_name), None)
        if video is None:
            raise FrameServiceError("動画が見つかりません")
        output = (target / video.stem).resolve()
        if not output.is_dir():
            raise FrameServiceError("抽出結果フォルダが見つかりません")
        try:
            subprocess.Popen(["explorer.exe", str(output)])
        except OSError as exc:
            raise FrameServiceError(f"抽出結果フォルダを開けません: {exc}") from exc

    async def run(self, payload: dict, log: Callable[[str], None]) -> None:
        key, video_name = payload["datasetKey"], payload["videoName"]
        state, _ = self._context(key)
        if state.config_path != payload["projectConfigPath"]:
            raise FrameServiceError("ジョブ登録時と異なるプロジェクトが開かれています")
        source, target_root, videos = self._videos(key)
        video = next((item for item in videos if item.name == video_name), None)
        if video is None:
            raise FrameServiceError("入力動画が見つかりません")
        output = target_root / video.stem
        if output.exists() and any(output.iterdir()):
            raise FrameServiceError("出力フォルダに既存ファイルがあるため上書きできません")
        target_root.mkdir(parents=True, exist_ok=True)
        staging = target_root / f".{video.stem}.{uuid.uuid4().hex}.extracting"
        staging.mkdir()
        try:
            log(f"{video.name} の全フレーム抽出を開始します")
            await self._run_ffmpeg(video, staging)
            frames = [item for item in staging.iterdir() if item.is_file() and item.suffix.lower() == ".png"]
            if not frames:
                raise FrameServiceError("ffmpegは成功しましたがフレームが出力されませんでした")
            if output.exists():
                output.rmdir()
            staging.replace(output)
            log(f"{len(frames)} フレームを {output.name} へ保存しました")
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    async def _run_ffmpeg(self, video: Path, output: Path) -> None:
        executable = os.environ.get("LORA_MAKER_FFMPEG", "ffmpeg")
        try:
            process = await asyncio.create_subprocess_exec(
                executable, "-hide_banner", "-nostdin", "-i", str(video),
                "-fps_mode", "passthrough", str(output / "frame_%06d.png"),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise FrameServiceError("ffmpegが見つかりません。PATHまたはLORA_MAKER_FFMPEGを確認してください") from exc
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip().splitlines()
            raise FrameServiceError(f"ffmpegが終了コード {process.returncode} で失敗しました: {detail[-1] if detail else '詳細なし'}")

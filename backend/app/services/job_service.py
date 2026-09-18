import asyncio
import json
import sqlite3
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.schemas.jobs import Job, JobStatus


class JobServiceError(ValueError):
    pass


class JobService:
    """Persistent, single-worker queue shared by all long-running operations."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        self._wake = asyncio.Event()
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._worker: asyncio.Task[None] | None = None
        self._stopping = False
        self._initialized = False

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        if self._initialized:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL,
                    project_config_path TEXT, payload TEXT NOT NULL, logs TEXT NOT NULL,
                    error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
                )"""
            )
            now = self._now()
            rows = connection.execute(
                "SELECT id, logs FROM jobs WHERE status = ?", (JobStatus.RUNNING,)
            ).fetchall()
            for row in rows:
                logs = json.loads(row["logs"])
                logs.append(f"{now} アプリの異常終了により処理状態を復元できませんでした")
                connection.execute(
                    "UPDATE jobs SET status = ?, logs = ?, error = ?, finished_at = ? WHERE id = ?",
                    (JobStatus.FAILED, json.dumps(logs, ensure_ascii=False),
                     "アプリ終了時に実行中だったジョブです。再実行してください", now, row["id"]),
                )
        self._initialized = True

    def _ensure_initialized(self) -> None:
        with self._lock:
            self._initialize()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    async def start(self) -> None:
        self._ensure_initialized()
        self._stopping = False
        self._worker = asyncio.create_task(self._run(), name="job-worker")
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    def create_test_job(self, duration: float, should_fail: bool, project_path: str | None) -> Job:
        return self.enqueue("test", {"durationSeconds": duration, "shouldFail": should_fail}, project_path)

    def enqueue(self, job_type: str, payload: dict[str, Any], project_path: str | None) -> Job:
        self._ensure_initialized()
        job_id, now = str(uuid.uuid4()), self._now()
        logs = [f"{now} ジョブを登録しました"]
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL)",
                (job_id, job_type, JobStatus.QUEUED, project_path,
                 json.dumps(payload, ensure_ascii=False), json.dumps(logs, ensure_ascii=False), now),
            )
        self._wake.set()
        self._publish("jobs-changed")
        return self.get(job_id)

    def list(self, limit: int = 100) -> list[Job]:
        self._ensure_initialized()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._to_job(row) for row in rows]

    def get(self, job_id: str) -> Job:
        self._ensure_initialized()
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobServiceError("ジョブが見つかりません")
        return self._to_job(row)

    def cancel(self, job_id: str) -> Job:
        self._ensure_initialized()
        now = self._now()
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT status, logs FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobServiceError("ジョブが見つかりません")
            if row["status"] != JobStatus.QUEUED:
                raise JobServiceError("キャンセルできるのは待機中のジョブだけです")
            logs = json.loads(row["logs"])
            logs.append(f"{now} キャンセルしました")
            connection.execute(
                "UPDATE jobs SET status = ?, logs = ?, finished_at = ? WHERE id = ?",
                (JobStatus.CANCELLED, json.dumps(logs, ensure_ascii=False), now, job_id),
            )
        self._publish("jobs-changed")
        return self.get(job_id)

    def has_active(self) -> bool:
        self._ensure_initialized()
        with self._lock, self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN (?, ?)",
                (JobStatus.QUEUED, JobStatus.RUNNING),
            ).fetchone()[0]
        return count > 0

    async def events(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        try:
            yield "connected"
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield "keep-alive"
        finally:
            self._subscribers.discard(queue)

    def _publish(self, event: str) -> None:
        for queue in tuple(self._subscribers):
            if not queue.full():
                queue.put_nowait(event)

    async def _run(self) -> None:
        while not self._stopping:
            job = self._next_queued()
            if job is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            await self._execute(job)

    def _next_queued(self) -> Job | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1", (JobStatus.QUEUED,)
            ).fetchone()
            if row is None:
                return None
            now = self._now()
            logs = json.loads(row["logs"])
            logs.append(f"{now} 実行を開始しました")
            connection.execute(
                "UPDATE jobs SET status = ?, started_at = ?, logs = ? WHERE id = ? AND status = ?",
                (JobStatus.RUNNING, now, json.dumps(logs, ensure_ascii=False), row["id"], JobStatus.QUEUED),
            )
        self._publish("jobs-changed")
        return self.get(row["id"])

    async def _execute(self, job: Job) -> None:
        try:
            if job.type != "test":
                raise RuntimeError(f"未対応のジョブ種別です: {job.type}")
            await asyncio.sleep(float(job.payload["durationSeconds"]))
            if job.payload.get("shouldFail"):
                raise RuntimeError("テスト用の失敗です")
            self._finish(job.id, JobStatus.COMPLETED, "正常に完了しました", None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._finish(job.id, JobStatus.FAILED, "失敗しました", str(exc))

    def _finish(self, job_id: str, status: JobStatus, message: str, error: str | None) -> None:
        now = self._now()
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT logs FROM jobs WHERE id = ?", (job_id,)).fetchone()
            logs = json.loads(row["logs"])
            logs.append(f"{now} {message}")
            connection.execute(
                "UPDATE jobs SET status = ?, logs = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, json.dumps(logs, ensure_ascii=False), error, now, job_id),
            )
        self._publish("jobs-changed")

    @staticmethod
    def _to_job(row: sqlite3.Row) -> Job:
        return Job.model_validate({
            "id": row["id"], "type": row["type"], "status": row["status"],
            "projectConfigPath": row["project_config_path"], "payload": json.loads(row["payload"]),
            "logs": json.loads(row["logs"]), "error": row["error"], "createdAt": row["created_at"],
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
        })

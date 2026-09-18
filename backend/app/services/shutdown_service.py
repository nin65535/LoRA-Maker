import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable

from backend.app.services.job_service import JobService


ShutdownCallback = Callable[[], None | Awaitable[None]]


class ShutdownService:
    """Tracks browser SSE connections and requests a graceful server shutdown."""

    def __init__(
        self,
        job_service: JobService,
        request_shutdown: ShutdownCallback,
        grace_seconds: float = 10.0,
        poll_seconds: float = 0.25,
    ) -> None:
        self.job_service = job_service
        self.request_shutdown = request_shutdown
        self.grace_seconds = grace_seconds
        self.poll_seconds = poll_seconds
        self.connection_count = 0
        self.shutdown_requested = False
        self.shutdown_triggered = False
        self._started_at = 0.0
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._monitor(), name="shutdown-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def connected(self) -> None:
        async with self._lock:
            self.connection_count += 1
            self.shutdown_requested = False

    async def disconnected(self) -> None:
        async with self._lock:
            self.connection_count = max(0, self.connection_count - 1)

    async def evaluate(self) -> None:
        async with self._lock:
            if self.shutdown_triggered:
                return
            if self.connection_count > 0:
                self.shutdown_requested = False
                return
            if time.monotonic() - self._started_at < self.grace_seconds:
                return
            self.shutdown_requested = True
            if self.job_service.has_active():
                return
            self.shutdown_triggered = True

        result = self.request_shutdown()
        if inspect.isawaitable(result):
            await result

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(self.poll_seconds)
            await self.evaluate()

from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.health import router as health_router
from backend.app.api.jobs import router as jobs_router
from backend.app.api.projects import router as projects_router
from backend.app.api.progress import router as progress_router
from backend.app.api.tags import router as tags_router
from backend.app.api.frames import router as frames_router
from backend.app.api.upscale import router as upscale_router
from backend.app.api.movies import router as movies_router
from backend.app.core.errors import register_exception_handlers
from backend.app.core.logging import configure_logging, register_request_logging
from backend.app.services.project_service import ProjectService
from backend.app.services.job_service import JobService
from backend.app.services.tag_service import TagService
from backend.app.services.frame_service import FrameService
from backend.app.services.upscale_service import UpscaleService
from backend.app.services.movie_service import MovieService
from backend.app.services.training_service import TrainingService
from backend.app.api.training import router as training_router
from backend.app.services.shutdown_service import ShutdownService


def create_app(
    project_service: ProjectService | None = None,
    job_service: JobService | None = None,
    request_shutdown: Callable[[], None | Awaitable[None]] | None = None,
    shutdown_grace_seconds: float = 10.0,
    frontend_dist: Path | None = None,
) -> FastAPI:
    active_project_service = project_service or ProjectService()
    active_job_service = job_service or JobService(active_project_service.settings_directory / "jobs.sqlite3")
    active_tag_service = TagService(active_project_service)
    active_frame_service = FrameService(active_project_service, active_job_service)
    active_upscale_service = UpscaleService(active_project_service, active_job_service)
    active_movie_service = MovieService(active_project_service, active_job_service)
    active_training_service = TrainingService(active_project_service, active_job_service)
    active_job_service.register_handler("tagger", active_tag_service.run_tagger)
    active_job_service.register_handler("frame-extraction", active_frame_service.run)
    active_job_service.register_handler("image-upscale", active_upscale_service.run)
    active_job_service.register_handler("movie-generation", active_movie_service.run)
    active_job_service.register_handler("lora-training", active_training_service.run)
    shutdown_service = (
        ShutdownService(active_job_service, request_shutdown, shutdown_grace_seconds)
        if request_shutdown is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        active_project_service.restore_last_project()
        await active_job_service.start()
        if shutdown_service is not None:
            await shutdown_service.start()
        try:
            yield
        finally:
            if shutdown_service is not None:
                await shutdown_service.stop()
            await active_job_service.stop()

    app = FastAPI(title="LoRA Maker API", version="0.9.0", lifespan=lifespan)
    logger = configure_logging()
    app.state.logger = logger
    app.state.project_service = active_project_service
    app.state.job_service = active_job_service
    app.state.tag_service = active_tag_service
    app.state.frame_service = active_frame_service
    app.state.upscale_service = active_upscale_service
    app.state.movie_service = active_movie_service
    app.state.training_service = active_training_service
    app.state.shutdown_service = shutdown_service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_request_logging(app, logger)
    register_exception_handlers(app)
    app.include_router(health_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(projects_router, prefix="/api")
    app.include_router(progress_router, prefix="/api")
    app.include_router(tags_router, prefix="/api")
    app.include_router(frames_router, prefix="/api")
    app.include_router(upscale_router, prefix="/api")
    app.include_router(movies_router, prefix="/api")
    app.include_router(training_router, prefix="/api")

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def api_not_found(path: str) -> None:
        raise HTTPException(status_code=404, detail=f"API route not found: /api/{path}")

    resolved_dist = frontend_dist or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if resolved_dist.is_dir() and (resolved_dist / "index.html").is_file():
        assets = resolved_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            candidate = (resolved_dist / path).resolve()
            if candidate.is_relative_to(resolved_dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(resolved_dist / "index.html")

    return app


app = create_app()

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.health import router as health_router
from backend.app.api.projects import router as projects_router
from backend.app.api.progress import router as progress_router
from backend.app.core.errors import register_exception_handlers
from backend.app.core.logging import configure_logging, register_request_logging
from backend.app.services.project_service import ProjectService


def create_app(project_service: ProjectService | None = None) -> FastAPI:
    active_project_service = project_service or ProjectService()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        active_project_service.restore_last_project()
        yield

    app = FastAPI(title="LoRA Maker API", version="0.2.0", lifespan=lifespan)
    logger = configure_logging()
    app.state.logger = logger
    app.state.project_service = active_project_service

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
    app.include_router(projects_router, prefix="/api")
    app.include_router(progress_router, prefix="/api")

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def api_not_found(path: str) -> None:
        raise HTTPException(status_code=404, detail=f"API route not found: /api/{path}")

    return app


app = create_app()

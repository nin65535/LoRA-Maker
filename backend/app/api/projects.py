from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.projects import (
    DatasetConfig,
    PersonalSettings,
    ProjectConfig,
    ProjectCreateRequest,
    ProjectLoadRequest,
    ProjectState,
)
from backend.app.services.project_service import ProjectService, ProjectServiceError


router = APIRouter(prefix="/projects", tags=["projects"])


def service(request: Request) -> ProjectService:
    return request.app.state.project_service


def run(action):
    try:
        return action()
    except (ProjectServiceError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def ensure_switch_allowed(request: Request) -> None:
    if request.app.state.job_service.has_active():
        raise HTTPException(status_code=409, detail="待機中または実行中のジョブがあるためプロジェクトを切り替えられません")


@router.get("/current", response_model=ProjectState | None)
def current(request: Request) -> ProjectState | None:
    return service(request).current


@router.post("/create", response_model=ProjectState)
def create(payload: ProjectCreateRequest, request: Request) -> ProjectState:
    ensure_switch_allowed(request)
    config = ProjectConfig(project={"name": payload.name}, datasets=payload.datasets)
    return run(lambda: service(request).create(Path(payload.root_path), config))


@router.post("/load", response_model=ProjectState)
def load(payload: ProjectLoadRequest, request: Request) -> ProjectState:
    ensure_switch_allowed(request)
    return run(lambda: service(request).load(Path(payload.config_path)))


@router.post("/select", response_model=ProjectState | None)
def select_project(request: Request) -> ProjectState | None:
    ensure_switch_allowed(request)
    return run(lambda: service(request).select_and_load())


@router.put("/current", response_model=ProjectState)
def save(payload: ProjectConfig, request: Request) -> ProjectState:
    return run(lambda: service(request).save(payload))


@router.post("/current/datasets", response_model=ProjectState)
def add_dataset(payload: DatasetConfig, request: Request) -> ProjectState:
    return run(lambda: service(request).add_dataset(payload))


@router.get("/master")
def master(request: Request):
    return service(request).master()


@router.get("/settings", response_model=PersonalSettings)
def settings(request: Request) -> PersonalSettings:
    return run(lambda: service(request).settings())


@router.put("/settings", response_model=PersonalSettings)
def save_settings(payload: PersonalSettings, request: Request) -> PersonalSettings:
    return run(lambda: service(request).save_settings(payload))

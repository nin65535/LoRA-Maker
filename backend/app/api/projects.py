from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.projects import (
    DatasetConfig,
    SavePathSelectRequest,
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


@router.post("/current/reload", response_model=ProjectState)
def reload_current(request: Request) -> ProjectState:
    return run(lambda: service(request).reload_current())


@router.post("/current/clear-generated/{stage}", response_model=ProjectState)
def clear_generated(stage: str, request: Request) -> ProjectState:
    ensure_switch_allowed(request)
    current = service(request).current
    if current is None:
        raise HTTPException(status_code=409, detail="プロジェクトが開かれていません")
    state = run(lambda: service(request).clear_generated_outputs(stage))
    if stage == "trainingDataset":
        request.app.state.upscale_service.clear_project_history(current.config_path)
    return state


@router.post("/create", response_model=ProjectState)
def create(payload: ProjectCreateRequest, request: Request) -> ProjectState:
    ensure_switch_allowed(request)
    datasets = payload.datasets
    if datasets is None:
        if payload.key is None:
            raise HTTPException(status_code=422, detail="既定データセットの作成にはプロジェクトキーが必要です")
        datasets = []
        for template in service(request).master_service.value.default_datasets:
            dataset = template.model_copy(deep=True)
            dataset.trigger_tags = [
                tag.replace("${project}", payload.key).replace("${dataset}", dataset.key)
                for tag in dataset.trigger_tags
            ]
            datasets.append(dataset)
    config = ProjectConfig(project={"key": payload.key, "name": payload.name}, datasets=datasets)
    config_path = Path(payload.config_path) if payload.config_path else None
    root = config_path.parent if config_path else Path(payload.root_path or "")
    return run(lambda: service(request).create(root, config, config_path))


@router.post("/load", response_model=ProjectState)
def load(payload: ProjectLoadRequest, request: Request) -> ProjectState:
    ensure_switch_allowed(request)
    return run(lambda: service(request).load(Path(payload.config_path)))


@router.post("/select", response_model=ProjectState | None)
def select_project(request: Request) -> ProjectState | None:
    ensure_switch_allowed(request)
    return run(lambda: service(request).select_and_load())


@router.post("/create/select-config-path", response_model=dict[str, str] | None)
def select_create_config_path(payload: SavePathSelectRequest, request: Request) -> dict[str, str] | None:
    initial = Path(payload.initial_path).expanduser() if payload.initial_path else None
    selected = run(lambda: service(request).select_create_config_path(initial))
    return {"path": str(selected)} if selected else None


@router.post("/close", response_model=None)
def close_project(request: Request) -> None:
    ensure_switch_allowed(request)
    return run(lambda: service(request).close())


@router.post("/current/datasets/{key}/open-source-folder")
def open_source_folder(key: str, request: Request) -> dict[str, bool]:
    run(lambda: service(request).open_source_folder(key))
    return {"opened": True}


@router.put("/current", response_model=ProjectState)
def save(payload: ProjectConfig, request: Request) -> ProjectState:
    ensure_switch_allowed(request)
    return run(lambda: service(request).save(payload))


@router.post("/current/datasets", response_model=ProjectState)
def add_dataset(payload: DatasetConfig, request: Request) -> ProjectState:
    return run(lambda: service(request).add_dataset(payload))


@router.delete("/current/datasets/{key}", response_model=ProjectState)
def remove_dataset(key: str, request: Request) -> ProjectState:
    ensure_switch_allowed(request)
    return run(lambda: service(request).remove_dataset(key))


@router.get("/master")
def master(request: Request):
    return service(request).master()


@router.get("/settings", response_model=PersonalSettings)
def settings(request: Request) -> PersonalSettings:
    return run(lambda: service(request).settings())


@router.put("/settings", response_model=PersonalSettings)
def save_settings(payload: PersonalSettings, request: Request) -> PersonalSettings:
    ensure_switch_allowed(request)
    return run(lambda: service(request).save_settings(payload))

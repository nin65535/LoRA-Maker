from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.jobs import Job
from backend.app.schemas.projects import ProjectState
from backend.app.schemas.tags import RemovedTagsRequest, TagSummary
from backend.app.services.tag_service import TagService, TagServiceError


router = APIRouter(prefix="/tags", tags=["tags"])


def service(request: Request) -> TagService:
    return request.app.state.tag_service


def run(action):
    try:
        return action()
    except (TagServiceError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{dataset_key}", response_model=TagSummary)
def summary(dataset_key: str, request: Request) -> TagSummary:
    return run(lambda: service(request).summary(dataset_key))


@router.put("/{dataset_key}/removed", response_model=ProjectState)
def save_removed(dataset_key: str, payload: RemovedTagsRequest, request: Request) -> ProjectState:
    return run(lambda: service(request).save_removed(dataset_key, payload.removed_tags))


@router.post("/{dataset_key}/run", response_model=Job)
def run_tagger(dataset_key: str, request: Request) -> Job:
    project = request.app.state.project_service.current
    if project is None:
        raise HTTPException(status_code=400, detail="プロジェクトが開かれていません")
    run(lambda: service(request)._context(dataset_key))
    return request.app.state.job_service.enqueue(
        "tagger", {"datasetKey": dataset_key, "projectConfigPath": project.config_path}, project.config_path
    )


@router.post("/{dataset_key}/place", response_model=TagSummary)
def place(dataset_key: str, request: Request) -> TagSummary:
    return run(lambda: service(request).place(dataset_key))

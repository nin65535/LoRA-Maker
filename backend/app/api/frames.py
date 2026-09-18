from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.frames import FrameBatchResult, FrameDatasetStatus
from backend.app.schemas.jobs import Job
from backend.app.services.frame_service import FrameService, FrameServiceError


router = APIRouter(prefix="/frames", tags=["frames"])


def service(request: Request) -> FrameService:
    return request.app.state.frame_service


def run(action):
    try:
        return action()
    except (FrameServiceError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{dataset_key}", response_model=FrameDatasetStatus)
def status(dataset_key: str, request: Request) -> FrameDatasetStatus:
    return run(lambda: service(request).status(dataset_key))


@router.post("/{dataset_key}/videos/{video_name}/run", response_model=Job)
def extract(dataset_key: str, video_name: str, request: Request) -> Job:
    return run(lambda: service(request).enqueue(dataset_key, video_name))


@router.post("/{dataset_key}/run-unprocessed", response_model=FrameBatchResult)
def extract_unprocessed(dataset_key: str, request: Request) -> FrameBatchResult:
    jobs, skipped = run(lambda: service(request).enqueue_unprocessed(dataset_key))
    return FrameBatchResult(jobs=jobs, skipped=skipped)


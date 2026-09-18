from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.jobs import Job
from backend.app.schemas.upscale import SelectionStartResult, UpscaleRunResult, UpscaleStatus
from backend.app.services.upscale_service import UpscaleService, UpscaleServiceError


router = APIRouter(prefix="/upscale", tags=["upscale"])


def service(request: Request) -> UpscaleService:
    return request.app.state.upscale_service


def run(action):
    try:
        return action()
    except (UpscaleServiceError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=UpscaleStatus)
def status(request: Request) -> UpscaleStatus:
    return run(lambda: service(request).status())


@router.post("/{dataset_key}/{capture_folder}/select", response_model=SelectionStartResult)
def select(dataset_key: str, capture_folder: str, request: Request) -> SelectionStartResult:
    target = run(lambda: service(request).start_selection(dataset_key, capture_folder))
    selection = service(request)._selection(True)
    return SelectionStartResult(target=target, selectionPath=str(selection))


@router.post("/{dataset_key}/{capture_folder}/run", response_model=UpscaleRunResult)
def upscale(dataset_key: str, capture_folder: str, request: Request) -> UpscaleRunResult:
    job: Job = run(lambda: service(request).enqueue(dataset_key, capture_folder))
    return UpscaleRunResult(job=job)

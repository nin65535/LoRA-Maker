from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.training import ArtifactActionRequest, TrainingRunRequest, TrainingRunResult, TrainingStatus
from backend.app.services.training_service import TrainingService, TrainingServiceError

router = APIRouter(prefix="/training", tags=["training"])


def service(request: Request) -> TrainingService:
    return request.app.state.training_service


def call(action):
    try:
        return action()
    except (TrainingServiceError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=TrainingStatus)
def status(request: Request) -> TrainingStatus:
    return call(lambda: service(request).status())


@router.post("/run", response_model=TrainingRunResult)
def run(payload: TrainingRunRequest, request: Request) -> TrainingRunResult:
    return TrainingRunResult(job=call(lambda: service(request).enqueue(payload.config_name)))


@router.post("/artifacts/{name}/deploy", response_model=TrainingStatus)
def deploy(name: str, payload: ArtifactActionRequest, request: Request) -> TrainingStatus:
    call(lambda: service(request).deploy(name, payload.confirm_mismatch))
    return service(request).status()


@router.post("/artifacts/{name}/remove", response_model=TrainingStatus)
def remove(name: str, payload: ArtifactActionRequest, request: Request) -> TrainingStatus:
    call(lambda: service(request).remove(name, payload.confirm_mismatch))
    return service(request).status()

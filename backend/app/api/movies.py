from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.movies import MovieQueueRequest, MovieQueueResult, MovieStatus
from backend.app.services.movie_service import MovieService, MovieServiceError


router = APIRouter(prefix="/movies", tags=["movies"])


def service(request: Request) -> MovieService:
    return request.app.state.movie_service


def run(action):
    try:
        return action()
    except (MovieServiceError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=MovieStatus)
def status(request: Request) -> MovieStatus:
    return run(lambda: service(request).status())


@router.post("/queue", response_model=MovieQueueResult)
def queue(payload: MovieQueueRequest, request: Request) -> MovieQueueResult:
    return MovieQueueResult(job=run(lambda: service(request).enqueue(
        payload.dataset_key, payload.image_path, payload.preset_key
    )))

import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from backend.app.schemas.jobs import Job, TestJobRequest
from backend.app.services.job_service import JobService, JobServiceError


router = APIRouter(prefix="/jobs", tags=["jobs"])


def service(request: Request) -> JobService:
    return request.app.state.job_service


@router.get("", response_model=list[Job])
def list_jobs(request: Request, limit: int = Query(100, ge=1, le=500)) -> list[Job]:
    return service(request).list(limit)


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str, request: Request) -> Job:
    try:
        return service(request).get(job_id)
    except JobServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/test", response_model=Job)
def create_test_job(payload: TestJobRequest, request: Request) -> Job:
    project = request.app.state.project_service.current
    project_path = project.config_path if project else None
    return service(request).create_test_job(payload.duration_seconds, payload.should_fail, project_path)


@router.post("/{job_id}/cancel", response_model=Job)
def cancel_job(job_id: str, request: Request) -> Job:
    try:
        return service(request).cancel(job_id)
    except JobServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/events/stream")
async def job_events(request: Request) -> StreamingResponse:
    async def stream():
        shutdown_service = getattr(request.app.state, "shutdown_service", None)
        if shutdown_service is not None:
            await shutdown_service.connected()
        try:
            async for event in service(request).events():
                if await request.is_disconnected():
                    break
                yield f"event: {event}\ndata: {json.dumps({'event': event})}\n\n"
        finally:
            if shutdown_service is not None:
                await shutdown_service.disconnected()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.jobs import Job


FrameState = Literal["unprocessed", "queued", "running", "extracted", "failed"]


class VideoFrameStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    relative_path: str = Field(alias="relativePath")
    frame_count: int = Field(alias="frameCount")
    state: FrameState
    job_id: str | None = Field(None, alias="jobId")
    error: str | None = None
    conflict: str | None = None


class FrameDatasetStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_key: str = Field(alias="datasetKey")
    videos: list[VideoFrameStatus]


class FrameBatchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    jobs: list[Job]
    skipped: list[str]


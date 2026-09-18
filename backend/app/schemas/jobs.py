from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    status: JobStatus
    project_config_path: str | None = Field(None, alias="projectConfigPath")
    payload: dict[str, Any] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")


class TestJobRequest(BaseModel):
    duration_seconds: float = Field(2, ge=0.05, le=60, alias="durationSeconds")
    should_fail: bool = Field(False, alias="shouldFail")


from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.jobs import Job


UpscaleState = Literal["idle", "selected", "queued", "running", "failed"]


class SelectionTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_config_path: str = Field(alias="projectConfigPath")
    dataset_key: str = Field(alias="datasetKey")
    capture_folder: str = Field(alias="captureFolder")


class CaptureFolderStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_key: str = Field(alias="datasetKey")
    dataset_name: str = Field(alias="datasetName")
    capture_folder: str = Field(alias="captureFolder")
    capture_count: int = Field(alias="captureCount")
    selected_image_count: int = Field(alias="selectedImageCount")
    scale1_processed_count: int = Field(alias="scale1ProcessedCount")
    scale2_processed_count: int = Field(alias="scale2ProcessedCount")
    selected: bool
    state: UpscaleState
    job_id: str | None = Field(None, alias="jobId")
    error: str | None = None


class UpscaleStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    selection_path: str | None = Field(alias="selectionPath")
    active_target: SelectionTarget | None = Field(None, alias="activeTarget")
    bandiview_running: bool = Field(alias="bandiviewRunning")
    folders: list[CaptureFolderStatus]


class SelectionStartResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target: SelectionTarget
    selection_path: str = Field(alias="selectionPath")


class UpscaleRunResult(BaseModel):
    job: Job


class UpscaleRunRequest(BaseModel):
    scale: Literal[1, 2]

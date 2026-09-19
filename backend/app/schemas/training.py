from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.jobs import Job

class TrainingDatasetStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    key: str
    name: str
    image_count: int = Field(alias="imageCount")
    caption_count: int = Field(alias="captionCount")
    matched_pairs: int = Field(alias="matchedPairs")
    images_without_captions: list[str] = Field(alias="imagesWithoutCaptions")
    captions_without_images: list[str] = Field(alias="captionsWithoutImages")


class ArtifactStatus(BaseModel):
    name: str
    size: int
    deployment: Literal["unconfigured", "not_deployed", "identical", "different"]


class TrainingStatus(BaseModel):
    datasets: list[TrainingDatasetStatus]
    artifacts: list[ArtifactStatus]
    configured: bool
    configuration_errors: list[str] = Field(alias="configurationErrors")
    training_configs: list[str] = Field(alias="trainingConfigs")
    next_output_name: str = Field(alias="nextOutputName")


class TrainingRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    config_name: str = Field(alias="configName", min_length=1)


class TrainingRunResult(BaseModel):
    job: Job


class ArtifactActionRequest(BaseModel):
    confirm_mismatch: bool = Field(default=False, alias="confirmMismatch")

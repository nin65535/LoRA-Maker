from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.jobs import Job


class MoviePresetInfo(BaseModel):
    key: str
    name: str


class SourceImageInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_key: str = Field(alias="datasetKey")
    dataset_name: str = Field(alias="datasetName")
    name: str
    relative_path: str = Field(alias="relativePath")


class MovieQueueRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_key: str = Field(alias="datasetKey")
    image_path: str = Field(alias="imagePath")
    preset_key: str = Field(alias="presetKey")


class MovieStatus(BaseModel):
    presets: list[MoviePresetInfo]
    images: list[SourceImageInfo]


class MovieQueueResult(BaseModel):
    job: Job

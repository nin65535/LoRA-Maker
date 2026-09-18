from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from backend.app.schemas.projects import DatasetConfig


Key = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9]*$")]
PresetKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]


class FolderDefinition(BaseModel):
    key: Key
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)


class FileExtensions(BaseModel):
    images: list[str] = Field(min_length=1)
    videos: list[str] = Field(min_length=1)
    lora: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_extensions(self) -> "FileExtensions":
        for values in (self.images, self.videos, self.lora):
            if any(not value.startswith(".") or value != value.lower() for value in values):
                raise ValueError("file extensions must be lowercase and start with a dot")
        return self


class WorkflowNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(alias="nodeId", min_length=1)
    input_name: str | None = Field(default=None, alias="inputName")


class TaggingMaster(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_path: str = Field(alias="workflowPath", min_length=1)
    nodes: dict[str, WorkflowNode]
    timeout_seconds: int = Field(alias="timeoutSeconds", ge=1)


class ImageUpscaleMaster(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_path: str = Field(alias="workflowPath", min_length=1)
    nodes: dict[str, WorkflowNode]
    timeout_seconds: int = Field(alias="timeoutSeconds", ge=1)


class MoviePreset(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: PresetKey
    name: str = Field(min_length=1)
    positive_prompt: str = Field(alias="positivePrompt")
    negative_prompt: str = Field(alias="negativePrompt")


class MovieGenerationMaster(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_path: str = Field(alias="workflowPath", min_length=1)
    nodes: dict[str, WorkflowNode]
    presets: list[MoviePreset]


class AppMaster(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    application: Literal["lora-maker"]
    schema_version: Literal[1] = Field(alias="schemaVersion")
    folders: list[FolderDefinition] = Field(min_length=7)
    default_datasets: list[DatasetConfig] = Field(alias="defaultDatasets")
    file_extensions: FileExtensions = Field(alias="fileExtensions")
    tagging: TaggingMaster
    image_upscale: ImageUpscaleMaster = Field(alias="imageUpscale")
    movie_generation: MovieGenerationMaster = Field(alias="movieGeneration")

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "AppMaster":
        required = {"sourceImages", "videos", "capturedFrames", "upscaledImages", "generatedTags", "trainingDataset", "trainedLora"}
        folder_keys = [item.key for item in self.folders]
        if len(folder_keys) != len(set(folder_keys)) or set(folder_keys) != required:
            raise ValueError("folders must contain each standard folder key exactly once")
        preset_keys = [item.key for item in self.movie_generation.presets]
        if len(preset_keys) != len(set(preset_keys)):
            raise ValueError("movie preset keys must be unique")
        return self

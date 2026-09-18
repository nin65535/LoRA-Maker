from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
DatasetKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]


class DatasetConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: DatasetKey
    name: NonEmptyText
    repeats: int = Field(ge=1)
    trigger_tags: list[NonEmptyText] = Field(alias="triggerTags")
    removed_tags: list[NonEmptyText] = Field(default_factory=list, alias="removedTags")

    @model_validator(mode="after")
    def validate_tags(self) -> "DatasetConfig":
        if len(set(self.trigger_tags)) != len(self.trigger_tags):
            raise ValueError("triggerTags must not contain duplicates")
        if len(set(self.removed_tags)) != len(self.removed_tags):
            raise ValueError("removedTags must not contain duplicates")
        overlap = set(self.trigger_tags) & set(self.removed_tags)
        if overlap:
            raise ValueError("triggerTags cannot also be removedTags")
        return self


class ProjectDetails(BaseModel):
    name: NonEmptyText


class ProjectConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    application: Literal["lora-maker"] = "lora-maker"
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    project: ProjectDetails
    datasets: list[DatasetConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dataset_keys(self) -> "ProjectConfig":
        keys = [dataset.key for dataset in self.datasets]
        if len(set(keys)) != len(keys):
            raise ValueError("dataset keys must be unique")
        return self


class ProjectCreateRequest(BaseModel):
    root_path: NonEmptyText = Field(alias="rootPath")
    name: NonEmptyText
    datasets: list[DatasetConfig] = Field(default_factory=list)


class ProjectLoadRequest(BaseModel):
    config_path: NonEmptyText = Field(alias="configPath")


class ProjectState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    config_path: str = Field(alias="configPath")
    root_path: str = Field(alias="rootPath")
    config: ProjectConfig
    warnings: list[str] = Field(default_factory=list)


class PersonalSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    last_project_config_path: str | None = Field(default=None, alias="lastProjectConfigPath")
    comfyui_api_url: str = Field(default="http://127.0.0.1:8188", alias="comfyuiApiUrl")
    comfyui_movie_output_path: str | None = Field(default=None, alias="comfyuiMovieOutputPath")
    sd_scripts_python_path: str | None = Field(default=None, alias="sdScriptsPythonPath")
    sd_scripts_path: str | None = Field(default=None, alias="sdScriptsPath")
    sd_scripts_working_directory: str | None = Field(default=None, alias="sdScriptsWorkingDirectory")
    training_output_path: str | None = Field(default=None, alias="trainingOutputPath")

from datetime import datetime

from pydantic import BaseModel, Field


class FileGroup(BaseModel):
    count: int = 0
    files: list[str] = Field(default_factory=list)
    truncated: bool = False


class DatasetProgress(BaseModel):
    key: str
    name: str
    source_images: FileGroup = Field(alias="sourceImages")
    videos: FileGroup
    captured_frames: FileGroup = Field(alias="capturedFrames")
    capture_folders: int = Field(alias="captureFolders")
    upscaled_images: FileGroup = Field(alias="upscaledImages")
    raw_captions: FileGroup = Field(alias="rawCaptions")
    training_images: FileGroup = Field(alias="trainingImages")
    training_captions: FileGroup = Field(alias="trainingCaptions")
    matched_pairs: int = Field(alias="matchedPairs")
    images_without_captions: int = Field(alias="imagesWithoutCaptions")
    captions_without_images: int = Field(alias="captionsWithoutImages")
    warnings: list[str] = Field(default_factory=list)


class ProgressTotals(BaseModel):
    datasets: int
    source_images: int = Field(alias="sourceImages")
    videos: int
    captured_frames: int = Field(alias="capturedFrames")
    capture_folders: int = Field(alias="captureFolders")
    upscaled_images: int = Field(alias="upscaledImages")
    raw_captions: int = Field(alias="rawCaptions")
    training_images: int = Field(alias="trainingImages")
    training_captions: int = Field(alias="trainingCaptions")
    matched_pairs: int = Field(alias="matchedPairs")
    mismatches: int
    trained_lora: int = Field(alias="trainedLora")


class ProjectProgress(BaseModel):
    scanned_at: datetime = Field(alias="scannedAt")
    totals: ProgressTotals
    datasets: list[DatasetProgress]
    trained_lora: FileGroup = Field(alias="trainedLora")
    warnings: list[str] = Field(default_factory=list)


class ToolStatus(BaseModel):
    comfyui: str
    comfyui_message: str = Field(alias="comfyuiMessage")
    sd_scripts: str = Field(alias="sdScripts")
    sd_scripts_message: str = Field(alias="sdScriptsMessage")

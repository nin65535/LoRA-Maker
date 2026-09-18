from pydantic import BaseModel, ConfigDict, Field


class TagCount(BaseModel):
    tag: str
    count: int
    rate: float
    removed: bool
    protected: bool


class CaptionMismatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    images_without_captions: list[str] = Field(alias="imagesWithoutCaptions")
    captions_without_images: list[str] = Field(alias="captionsWithoutImages")


class TagSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_key: str = Field(alias="datasetKey")
    image_count: int = Field(alias="imageCount")
    caption_count: int = Field(alias="captionCount")
    tags: list[TagCount]
    mismatch: CaptionMismatch


class RemovedTagsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    removed_tags: list[str] = Field(alias="removedTags")

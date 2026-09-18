from datetime import UTC, datetime
from pathlib import Path

from backend.app.schemas.progress import (
    DatasetProgress,
    FileGroup,
    ProgressTotals,
    ProjectProgress,
)
from backend.app.schemas.projects import ProjectState
from backend.app.schemas.master import AppMaster
LIST_LIMIT = 200


def _files(path: Path, extensions: set[str], recursive: bool = True) -> list[Path]:
    if not path.is_dir():
        return []
    iterator = path.rglob("*") if recursive else path.iterdir()
    return sorted(
        (item for item in iterator if item.is_file() and item.suffix.lower() in extensions),
        key=lambda item: str(item).lower(),
    )


def _group(path: Path, extensions: set[str], recursive: bool = True) -> tuple[FileGroup, list[Path]]:
    found = _files(path, extensions, recursive)
    names = [item.relative_to(path).as_posix() for item in found[:LIST_LIMIT]]
    return FileGroup(count=len(found), files=names, truncated=len(found) > LIST_LIMIT), found


def scan_project(state: ProjectState, master: AppMaster) -> ProjectProgress:
    root = Path(state.root_path)
    folder = {item.key: root / item.name for item in master.folders}
    image_extensions = set(master.file_extensions.images)
    video_extensions = set(master.file_extensions.videos)
    lora_extensions = set(master.file_extensions.lora)
    datasets: list[DatasetProgress] = []
    warnings = list(state.warnings)

    for config in state.config.datasets:
        source_group, _ = _group(folder["sourceImages"] / config.key, image_extensions)
        video_group, _ = _group(folder["videos"] / config.key, video_extensions)
        capture_path = folder["capturedFrames"] / config.key
        capture_group, _ = _group(capture_path, image_extensions)
        capture_directories = [item for item in capture_path.iterdir() if item.is_dir()] if capture_path.is_dir() else []
        capture_folders = len(capture_directories)
        empty_capture_folders = sum(1 for item in capture_directories if not _files(item, image_extensions))
        upscale_group, _ = _group(folder["upscaledImages"] / config.key, image_extensions)
        raw_group, _ = _group(folder["generatedTags"] / config.key, {".txt"})
        training_path = folder["trainingDataset"] / f"{config.repeats}_{config.key}"
        training_images, image_files = _group(training_path, image_extensions)
        training_captions, caption_files = _group(training_path, {".txt"})
        image_stems = {item.relative_to(training_path).with_suffix("").as_posix().lower() for item in image_files}
        caption_stems = {item.relative_to(training_path).with_suffix("").as_posix().lower() for item in caption_files}
        missing_captions = len(image_stems - caption_stems)
        missing_images = len(caption_stems - image_stems)
        item_warnings: list[str] = []
        if source_group.count == 0:
            item_warnings.append("素材画像フォルダが空です")
        if video_group.count and capture_group.count == 0:
            item_warnings.append("動画がありますがキャプチャ画像がありません")
        if empty_capture_folders:
            item_warnings.append(f"空のキャプチャフォルダが {empty_capture_folders} 件あります")
        if missing_captions:
            item_warnings.append(f"キャプションのない学習画像が {missing_captions} 件あります")
        if missing_images:
            item_warnings.append(f"画像のないキャプションが {missing_images} 件あります")
        datasets.append(DatasetProgress(
            key=config.key, name=config.name, sourceImages=source_group, videos=video_group,
            capturedFrames=capture_group, captureFolders=capture_folders,
            upscaledImages=upscale_group, rawCaptions=raw_group,
            trainingImages=training_images, trainingCaptions=training_captions,
            matchedPairs=len(image_stems & caption_stems), imagesWithoutCaptions=missing_captions,
            captionsWithoutImages=missing_images, warnings=item_warnings,
        ))
        warnings.extend(f"{config.name}: {message}" for message in item_warnings)

    lora_group, _ = _group(folder["trainedLora"], lora_extensions)
    def total(attribute: str) -> int:
        return sum(getattr(item, attribute).count for item in datasets)
    totals = ProgressTotals(
        datasets=len(datasets), sourceImages=total("source_images"), videos=total("videos"),
        capturedFrames=total("captured_frames"), captureFolders=sum(item.capture_folders for item in datasets),
        upscaledImages=total("upscaled_images"), rawCaptions=total("raw_captions"),
        trainingImages=total("training_images"), trainingCaptions=total("training_captions"),
        matchedPairs=sum(item.matched_pairs for item in datasets),
        mismatches=sum(item.images_without_captions + item.captions_without_images for item in datasets),
        trainedLora=lora_group.count,
    )
    return ProjectProgress(scannedAt=datetime.now(UTC), totals=totals, datasets=datasets, trainedLora=lora_group, warnings=warnings)

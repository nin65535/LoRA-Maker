from pathlib import Path

from backend.app.schemas.projects import DatasetConfig


def dataset_data_locations(
    root: Path, folders: dict[str, str], dataset: DatasetConfig
) -> list[tuple[str, Path]]:
    locations = [
        (folders[key], root / folders[key] / dataset.key)
        for key in ("sourceImages", "videos", "capturedFrames", "upscaledImages", "generatedTags")
    ]
    training_root = root / folders["trainingDataset"]
    if training_root.is_dir():
        locations.extend(
            (folders["trainingDataset"], path)
            for path in training_root.iterdir()
            if path.is_dir() and path.name.endswith(f"_{dataset.key}")
        )
    return locations


def dataset_delete_blockers(
    root: Path, folders: dict[str, str], dataset: DatasetConfig
) -> list[str]:
    blockers: list[str] = []
    for label, path in dataset_data_locations(root, folders, dataset):
        if path.is_dir() and any(item.is_file() for item in path.rglob("*")):
            if label not in blockers:
                blockers.append(label)
    return blockers

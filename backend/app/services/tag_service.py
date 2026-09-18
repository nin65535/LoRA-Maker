import asyncio
import json
import os
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.app.schemas.tags import CaptionMismatch, TagCount, TagSummary
from backend.app.services.project_service import ProjectService, ProjectServiceError


class TagServiceError(ValueError):
    pass


def _tags(text: str) -> list[str]:
    return [tag.strip() for tag in text.replace("\n", ",").split(",") if tag.strip()]


def _atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text.rstrip() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ComfyTagger:
    def __init__(self, api_url: str, workflow_path: Path, source_node: str, source_input: str, output_node: str, output_input: str, timeout: int) -> None:
        self.api_url = api_url.rstrip("/")
        self.workflow_path = workflow_path
        self.source_node = source_node
        self.source_input = source_input
        self.output_node = output_node
        self.output_input = output_input
        self.timeout = timeout

    def tag(self, image: Path) -> str:
        boundary = "----LoRAMakerUpload"
        data = image.read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{image.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        uploaded = self._json("/upload/image", body, f"multipart/form-data; boundary={boundary}")
        workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        workflow[self.source_node]["inputs"][self.source_input] = uploaded["name"]
        workflow[self.output_node]["inputs"][self.output_input] = f"lora_maker_tags/{image.stem}"
        queued = self._json("/prompt", json.dumps({"prompt": workflow}).encode(), "application/json")
        prompt_id = queued["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            history = self._json(f"/history/{prompt_id}")
            item = history.get(prompt_id)
            if item:
                status = item.get("status", {})
                if status.get("completed") and status.get("status_str") == "success":
                    return self._extract_text(item)
                if status.get("completed"):
                    raise TagServiceError("ComfyUIのTagger処理が失敗しました")
            time.sleep(.5)
        raise TagServiceError("ComfyUIのTagger処理がタイムアウトしました")

    def _extract_text(self, history: dict) -> str:
        output = history.get("outputs", {}).get(self.output_node, {})
        for key in ("text", "string"):
            values = output.get(key)
            if values:
                return values[0] if isinstance(values, list) else str(values)
        for value in output.values():
            if not isinstance(value, list):
                continue
            for file in value:
                if isinstance(file, dict) and file.get("filename"):
                    query = urlencode({k: file.get(k, "") for k in ("filename", "subfolder", "type")})
                    with urlopen(f"{self.api_url}/view?{query}", timeout=30) as response:
                        return response.read().decode("utf-8-sig")
        raise TagServiceError("Taggerのテキスト出力を取得できませんでした")

    def _json(self, path: str, body: bytes | None = None, content_type: str | None = None) -> dict:
        headers = {"Content-Type": content_type} if content_type else {}
        try:
            with urlopen(Request(self.api_url + path, data=body, headers=headers), timeout=30) as response:
                return json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TagServiceError(f"ComfyUI APIへ接続できません: {exc}") from exc


class TagService:
    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

    @property
    def folders(self) -> dict[str, str]:
        return self.projects.master_service.folder_map

    @property
    def image_extensions(self) -> set[str]:
        return set(self.projects.master_service.value.file_extensions.images)

    def _context(self, key: str):
        state = self.projects.current
        if state is None:
            raise TagServiceError("プロジェクトが開かれていません")
        dataset = next((item for item in state.config.datasets if item.key == key), None)
        if dataset is None:
            raise TagServiceError("データセットが見つかりません")
        return state, dataset

    def summary(self, key: str) -> TagSummary:
        state, dataset = self._context(key)
        root = Path(state.root_path)
        images = {p.stem: p.name for p in (root / self.folders["trainingDataset"] / f"{dataset.repeats}_{key}").iterdir() if p.is_file() and p.suffix.lower() in self.image_extensions}
        caption_dir = root / self.folders["generatedTags"] / key
        captions = {p.stem: p for p in caption_dir.glob("*.txt") if p.is_file()}
        counts: Counter[str] = Counter()
        for path in captions.values():
            counts.update(set(_tags(path.read_text(encoding="utf-8-sig"))))
        total = len(captions)
        protected = set(dataset.trigger_tags)
        removed = set(dataset.removed_tags)
        return TagSummary(
            datasetKey=key, imageCount=len(images), captionCount=total,
            tags=[TagCount(tag=tag, count=count, rate=count / total if total else 0, removed=tag in removed, protected=tag in protected) for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))],
            mismatch=CaptionMismatch(imagesWithoutCaptions=sorted(set(images) - set(captions)), captionsWithoutImages=sorted(set(captions) - set(images))),
        )

    def save_removed(self, key: str, removed: list[str]):
        state, dataset = self._context(key)
        clean = list(dict.fromkeys(tag.strip() for tag in removed if tag.strip()))
        overlap = set(clean) & set(dataset.trigger_tags)
        if overlap:
            raise TagServiceError(f"識別タグは削除対象にできません: {', '.join(sorted(overlap))}")
        config = state.config.model_copy(deep=True)
        next_dataset = next(item for item in config.datasets if item.key == key)
        next_dataset.removed_tags = clean
        return self.projects.save(config)

    async def run_tagger(self, payload: dict, log: Callable[[str], None]) -> None:
        key = payload["datasetKey"]
        state, dataset = self._context(key)
        if state.config_path != payload["projectConfigPath"]:
            raise TagServiceError("ジョブ登録時と異なるプロジェクトが開かれています")
        folder = Path(state.root_path) / self.folders["trainingDataset"] / f"{dataset.repeats}_{key}"
        images = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in self.image_extensions)
        if not images:
            raise TagServiceError("タグ付け対象の画像がありません")
        config = self.projects.master_service.value.tagging
        source, output_node = config.nodes["sourceImage"], config.nodes["output"]
        tagger = ComfyTagger(
            self.projects.settings().comfyui_api_url,
            self.projects.master_service.resolve_app_path(config.workflow_path),
            source.node_id, source.input_name or "image",
            output_node.node_id, output_node.input_name or "filename_prefix",
            config.timeout_seconds,
        )
        output = Path(state.root_path) / self.folders["generatedTags"] / key
        for index, image in enumerate(images, 1):
            text = await asyncio.to_thread(tagger.tag, image)
            _atomic_text_write(output / f"{image.stem}.txt", text)
            log(f"{index}/{len(images)} {image.name} のタグを保存しました")

    def place(self, key: str) -> TagSummary:
        state, dataset = self._context(key)
        summary = self.summary(key)
        if summary.mismatch.images_without_captions:
            raise TagServiceError("未加工キャプションがない画像があります")
        root = Path(state.root_path)
        raw = root / self.folders["generatedTags"] / key
        target = root / self.folders["trainingDataset"] / f"{dataset.repeats}_{key}"
        removed = set(dataset.removed_tags)
        for image in (p for p in target.iterdir() if p.is_file() and p.suffix.lower() in self.image_extensions):
            tags = _tags((raw / f"{image.stem}.txt").read_text(encoding="utf-8-sig"))
            final = list(dict.fromkeys([*dataset.trigger_tags, *(tag for tag in tags if tag not in removed and tag not in dataset.trigger_tags)]))
            _atomic_text_write(target / f"{image.stem}.txt", ", ".join(final))
        return self.summary(key)

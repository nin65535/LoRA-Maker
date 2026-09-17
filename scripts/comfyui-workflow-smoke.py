"""Submit one repository ComfyUI workflow and report its observable result."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def gpu_memory_mib() -> int | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
        return max(int(line.strip()) for line in result.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True, type=Path)
    parser.add_argument(
        "--from-head",
        action="store_true",
        help="read the workflow's committed version for before/after comparisons",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8188")
    parser.add_argument("--input-image", required=True)
    parser.add_argument("--tagger-directory")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    if args.from_head:
        workflow_text = subprocess.run(
            ["git", "show", f"HEAD:{args.workflow.as_posix()}"],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        ).stdout
    else:
        workflow_text = args.workflow.read_text(encoding="utf-8-sig")
    workflow = json.loads(workflow_text)
    for node in workflow.values():
        class_type = node.get("class_type")
        inputs = node.get("inputs", {})
        if class_type == "LoadImage":
            inputs["image"] = args.input_image
        elif class_type == "VHS_LoadImagesPath":
            if not args.tagger_directory:
                parser.error("--tagger-directory is required by this workflow")
            inputs["directory"] = args.tagger_directory
            inputs["image_load_cap"] = 1
        elif class_type in {"SaveImage", "SaveVideo", "SaveText"}:
            inputs["filename_prefix"] = args.prefix

    started = time.monotonic()
    submitted = request_json(f"{args.api_url}/prompt", {"prompt": workflow})
    prompt_id = submitted["prompt_id"]
    peak_vram = gpu_memory_mib()
    history_entry = None
    while time.monotonic() - started < args.timeout:
        history = request_json(f"{args.api_url}/history/{prompt_id}")
        if prompt_id in history:
            history_entry = history[prompt_id]
            status = history_entry.get("status", {})
            if status.get("completed"):
                break
        current_vram = gpu_memory_mib()
        if current_vram is not None:
            peak_vram = max(peak_vram or 0, current_vram)
        time.sleep(0.5)
    else:
        raise TimeoutError(f"workflow did not finish within {args.timeout:g}s")

    outputs = []
    for node_id, node_output in history_entry.get("outputs", {}).items():
        for kind in ("images", "gifs", "audio"):
            for item in node_output.get(kind, []):
                outputs.append({"node_id": node_id, "kind": kind, **item})

    report = {
        "workflow": str(args.workflow),
        "prompt_id": prompt_id,
        "status": history_entry.get("status"),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "peak_vram_mib": peak_vram,
        "outputs": outputs,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"].get("status_str") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

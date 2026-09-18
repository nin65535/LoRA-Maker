import asyncio
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Request

from backend.app.schemas.progress import ProjectProgress, ToolStatus
from backend.app.services.progress_service import scan_project


router = APIRouter(tags=["progress"])


@router.get("/progress", response_model=ProjectProgress)
async def progress(request: Request) -> ProjectProgress:
    current = request.app.state.project_service.current
    if current is None:
        raise HTTPException(status_code=409, detail="プロジェクトが開かれていません")
    return await asyncio.to_thread(scan_project, current)


def _comfyui_status(url: str) -> tuple[str, str]:
    try:
        with urlopen(f"{url.rstrip('/')}/system_stats", timeout=1.5) as response:
            if 200 <= response.status < 300:
                return "connected", "接続済み"
        return "disconnected", "応答が不正です"
    except (OSError, URLError, ValueError):
        return "disconnected", "停止中（Stability Matrixから起動してください）"


@router.get("/tools/status", response_model=ToolStatus)
async def tool_status(request: Request) -> ToolStatus:
    settings = request.app.state.project_service.settings()
    comfyui, message = await asyncio.to_thread(_comfyui_status, settings.comfyui_api_url)
    return ToolStatus(
        comfyui=comfyui, comfyuiMessage=message,
        sdScripts="idle", sdScriptsMessage="LoRA Makerが開始した学習プロセスはありません",
    )

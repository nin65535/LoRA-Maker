import asyncio
import logging
import os
import subprocess
from pathlib import Path

import uvicorn

from backend.app.main import create_app


HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}/"


def find_chrome() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def open_chrome(logger: logging.Logger) -> None:
    chrome = find_chrome()
    if chrome is None:
        logger.error("Google Chromeが見つかりません。ブラウザ接続を10秒待って終了します")
        return
    try:
        subprocess.Popen([str(chrome), "--profile-directory=Default", URL])
    except OSError:
        logger.exception("Google Chromeの起動に失敗しました")


async def run() -> None:
    server_holder: dict[str, uvicorn.Server] = {}

    def request_shutdown() -> None:
        server_holder["server"].should_exit = True

    app = create_app(request_shutdown=request_shutdown)
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="info")
    server = uvicorn.Server(config)
    server_holder["server"] = server

    async def launch_when_ready() -> None:
        while not server.started and not server.should_exit:
            await asyncio.sleep(0.05)
        if server.started:
            open_chrome(logging.getLogger("lora-maker"))

    launcher = asyncio.create_task(launch_when_ready())
    try:
        await server.serve()
    finally:
        await launcher


if __name__ == "__main__":
    asyncio.run(run())

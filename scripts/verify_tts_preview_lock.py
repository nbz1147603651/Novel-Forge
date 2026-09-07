"""End-to-end verification of the audition lock-timeout structured rejection.

Prerequisites:
- The uvicorn engine (port 8000) must have been restarted after the
  engine.py change, otherwise the old code still returns HTTP 500.
- The 梦侦探 project must have a voice team (it does).

How it works:
- A child process acquires the project TTS lock for ~25s.
- The main process calls the real preview-character-voice API.
- New code: HTTP 200 with status=rejected + error_code=tts_preview_lock_busy.
- Old code: HTTP 500 (TimeoutError) — restart the engine first.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import sys
import time
import urllib.request
from pathlib import Path

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.tts_ops.lock_manager import tts_project_lock

PROJECT_ID = "梦侦探"
CHARACTER_ID = "char_c9f5a31ce181"
PORT = 8000
HOLD_SECONDS = 25.0


def _hold_lock(duration: float) -> None:
    async def _hold() -> None:
        layout = ProjectLayout(Path(f"data/{PROJECT_ID}"))
        async with tts_project_lock(PROJECT_ID, layout):
            await asyncio.sleep(duration)

    asyncio.run(_hold())


def _call_preview() -> dict:
    body = json.dumps(
        {
            "kind": "preview_character_voice",
            "project_id": PROJECT_ID,
            "character_id": CHARACTER_ID,
            "sample_text": "",
            "provider": "",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/api/v1/engine/commands/preview-character-voice",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    holder = multiprocessing.Process(target=_hold_lock, args=(HOLD_SECONDS,))
    holder.start()
    time.sleep(2)  # let the child acquire the project TTS lock
    try:
        result = _call_preview()
    except Exception as exc:  # noqa: BLE001 - transport/5xx surfaces here
        print("HTTP 异常：", exc)
        print(
            "引擎可能仍在运行旧代码（未重启）；重启 uvicorn 后再运行本脚本。"
        )
        return 2
    finally:
        holder.terminate()
        holder.join()

    print(json.dumps(result, ensure_ascii=False))
    # The API envelope serializes error_code as camelCase (errorCode).
    if result.get("status") == "rejected" and result.get("errorCode") == "tts_preview_lock_busy":
        print("VERIFY OK: 锁超时已结构化返回 rejected + tts_preview_lock_busy")
        return 0
    print("VERIFY FAIL: 预期 rejected + tts_preview_lock_busy，实际如上")
    return 1


if __name__ == "__main__":
    sys.exit(main())

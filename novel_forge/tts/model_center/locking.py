"""Small cross-process lock files for model and runtime mutations."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ModelCenterLockedError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def active_model_center_locks(
    root: Path,
    *,
    exclude: set[str] | None = None,
    stale_after_s: int = 86_400,
) -> list[Path]:
    """Return live mutation locks without treating abandoned files as active."""

    lock_root = root / "locks"
    ignored = exclude or set()
    if not lock_root.is_dir():
        return []
    active: list[Path] = []
    now = time.time()
    for path in sorted(lock_root.glob("*.lock")):
        if path.name in ignored:
            continue
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            owner = int(current.get("pid") or 0)
            created = float(current.get("created_at") or 0)
        except (OSError, ValueError, TypeError):
            continue
        if _pid_alive(owner) and now - created < stale_after_s:
            active.append(path)
    return active


@contextmanager
def model_center_lock(
    path: Path,
    *,
    stale_after_s: int = 86_400,
    wait_timeout_s: float = 0.0,
    poll_interval_s: float = 0.05,
) -> Iterator[None]:
    """Acquire an atomic lock and reclaim it only when its owner is gone.

    ``wait_timeout_s`` is intentionally opt-in.  Download/install callers
    retain the historical fail-fast behaviour, while a sidecar lifecycle
    caller can wait for another UI process to finish starting the same
    runtime and then adopt it instead of racing to bind its port.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "created_at": time.time()}
    deadline = time.monotonic() + max(0.0, wait_timeout_s)
    while True:
        acquired = False
        for _attempt in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                    owner = int(current.get("pid") or 0)
                    created = float(current.get("created_at") or 0)
                except (OSError, ValueError, TypeError):
                    owner, created = 0, 0
                if _pid_alive(owner) and time.time() - created < stale_after_s:
                    if time.monotonic() >= deadline:
                        raise ModelCenterLockedError(f"模型中心正由进程 {owner} 修改。") from None
                    time.sleep(max(0.01, poll_interval_s))
                    break
                path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            acquired = True
            break
        else:  # pragma: no cover - defensive guard
            raise ModelCenterLockedError("无法获取模型中心锁。")
        if acquired:
            break
    try:
        yield
    finally:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if int(current.get("pid") or 0) == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass

"""Local Engine revision and ownership facts used by the desktop boundary.

The running FastAPI process is deliberately the authority for its own source
revision.  A launcher can compare this opaque value without learning a user's
storage layout, configuration, or API credentials.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

LOCAL_ENGINE_INSTANCE_ID_ENV = "NOVEL_FORGE_LOCAL_ENGINE_INSTANCE_ID"
LOCAL_ENGINE_MANAGER_ENV = "NOVEL_FORGE_LOCAL_ENGINE_MANAGER"
_FINGERPRINT_CACHE_TTL_S = 1.0
_SOURCE_SUFFIXES = frozenset({".py", ".j2"})


class EngineRestartRequiredError(RuntimeError):
    """Raised before a new job uses a backend whose source changed on disk."""

    error_code = "engine_restart_required"

    def __init__(self, *, boot_revision: str, current_revision: str) -> None:
        self.boot_revision = boot_revision
        self.current_revision = current_revision
        super().__init__("本地 Engine 源码已更新，请安全重启后端后再提交新任务。")


@dataclass(frozen=True)
class EngineRevisionStatus:
    """Safe-to-expose runtime identity used by the Tauri client."""

    boot_revision: str
    current_revision: str
    instance_id: str
    managed_by: str

    @property
    def restart_required(self) -> bool:
        return self.boot_revision != self.current_revision


class _SourceFingerprint:
    """Small metadata cache so status polling does not re-hash unchanged files."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._last_check = 0.0
        self._snapshot: tuple[tuple[str, int, int], ...] | None = None
        self._value = ""

    def get(self) -> str:
        root = _project_root()
        if getattr(sys, "frozen", False) or root is None:
            return os.environ.get("NOVEL_FORGE_ENGINE_BUILD_ID", "packaged-unknown")
        paths = _revision_paths(root)
        snapshot = tuple(
            (str(path.relative_to(root)), path.stat().st_mtime_ns, path.stat().st_size)
            for path in paths
        )
        now = monotonic()
        with self._lock:
            if (
                self._snapshot == snapshot
                and self._value
                and now - self._last_check <= _FINGERPRINT_CACHE_TTL_S
            ):
                return self._value
            if self._snapshot == snapshot and self._value:
                self._last_check = now
                return self._value
            digest = hashlib.sha256()
            for path in paths:
                relative = path.relative_to(root).as_posix().encode("utf-8")
                digest.update(relative)
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
            self._snapshot = snapshot
            self._value = f"src-{digest.hexdigest()[:20]}"
            self._last_check = now
            return self._value


def _project_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if (root / "novel_forge").is_dir() else None


def _revision_paths(root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    source = root / "novel_forge"
    if source.is_dir():
        candidates.extend(
            path for path in source.rglob("*") if path.is_file() and path.suffix in _SOURCE_SUFFIXES
        )
    for relative in (Path("pyproject.toml"), Path("packages/engine-contracts/src/index.ts")):
        path = root / relative
        if path.is_file():
            candidates.append(path)
    return tuple(sorted(candidates, key=lambda path: path.relative_to(root).as_posix()))


_fingerprint = _SourceFingerprint()
_boot_revision = _fingerprint.get()
_instance_id = os.environ.get(LOCAL_ENGINE_INSTANCE_ID_ENV, "").strip() or f"external-{uuid4().hex}"
_managed_by = os.environ.get(LOCAL_ENGINE_MANAGER_ENV, "").strip() or "external"


def current_engine_revision() -> str:
    """Return the current on-disk backend revision without exposing paths."""

    return _fingerprint.get()


def engine_revision_status() -> EngineRevisionStatus:
    """Return the immutable boot revision and current revision comparison."""

    return EngineRevisionStatus(
        boot_revision=_boot_revision,
        current_revision=current_engine_revision(),
        instance_id=_instance_id,
        managed_by=_managed_by,
    )


def require_engine_revision_current() -> None:
    """Reject only new jobs when source changes beneath a local backend."""

    status = engine_revision_status()
    if status.restart_required:
        raise EngineRestartRequiredError(
            boot_revision=status.boot_revision,
            current_revision=status.current_revision,
        )


__all__ = [
    "EngineRestartRequiredError",
    "EngineRevisionStatus",
    "LOCAL_ENGINE_INSTANCE_ID_ENV",
    "LOCAL_ENGINE_MANAGER_ENV",
    "current_engine_revision",
    "engine_revision_status",
    "require_engine_revision_current",
]

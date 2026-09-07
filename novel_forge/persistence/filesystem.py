"""FileSystemStorage — stores project artifacts as files on disk."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from novel_forge.persistence.base import StorageBackend

_logger = logging.getLogger(__name__)
_PROJECT_FILE_LOCK_GUARD = threading.RLock()
_PROJECT_FILE_LOCK_DEPTHS: dict[tuple[str, str], int] = {}
_PROJECT_FILE_LOCK_OWNER: ContextVar[str | None] = ContextVar(
    "project_file_lock_owner",
    default=None,
)

_fcntl: Any
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - only relevant on non-POSIX platforms
    _fcntl = None

fcntl: Any = _fcntl
if fcntl is None:
    _logger.warning(
        "fcntl not available on this platform; project_lock() is no-op. "
        "Concurrent writes to the same project from multiple processes may cause data loss."
    )

_msvcrt: Any
try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - only relevant on non-Windows platforms
    _msvcrt = None

msvcrt: Any = _msvcrt
if msvcrt is None and sys.platform == "win32":
    _logger.warning("msvcrt not available on this Windows system; project_lock() is no-op.")


def normalize_project_id(project_id: str) -> str:
    """Return a safe single-directory project id.

    Project ids are directory names under the storage root.  Keep existing
    human-readable ids such as Chinese titles intact, but reject path syntax.
    """
    normalized = str(project_id or "").strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "\x00" in normalized
        or "/" in normalized
        or "\\" in normalized
    ):
        raise ValueError(f"Invalid project_id: {project_id!r}")
    return normalized


def resolve_project_path(root: Path, project_id: str) -> Path:
    """Resolve a project path and ensure it stays inside ``root``."""
    normalized = normalize_project_id(project_id)
    root_resolved = root.resolve(strict=False)
    path = root / normalized
    path_resolved = path.resolve(strict=False)
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Project path escapes storage root: {project_id!r}") from exc
    if path_resolved == root_resolved:
        raise ValueError(f"Invalid project_id: {project_id!r}")
    return path


def _lock_file(handle: Any, exclusive: bool = True) -> None:
    """Platform-specific file locking.

    Uses fcntl.flock on POSIX, msvcrt.locking on Windows.
    """
    if sys.platform == "win32" and msvcrt is not None:
        # LK_LOCK = exclusive lock, LK_RLCK = shared lock
        mode = msvcrt.LK_LOCK if exclusive else msvcrt.LK_RLCK
        handle.seek(0)
        msvcrt.locking(handle.fileno(), mode, 1)
    elif fcntl is not None:
        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle.fileno(), lock_type)


def _unlock_file(handle: Any) -> None:
    """Platform-specific file unlocking.

    Uses fcntl.flock on POSIX, msvcrt.locking on Windows.
    """
    if sys.platform == "win32" and msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    elif fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _reentrant_project_file_lock(lock_key: str, *, exclusive: bool) -> Iterator[bool]:
    """Track nested project locks for one logical owner without bypassing others."""
    del exclusive
    logical_owner = _PROJECT_FILE_LOCK_OWNER.get() or f"thread:{threading.get_ident()}"
    owner_key = (logical_owner, lock_key)
    with _PROJECT_FILE_LOCK_GUARD:
        depth = _PROJECT_FILE_LOCK_DEPTHS.get(owner_key, 0)
        _PROJECT_FILE_LOCK_DEPTHS[owner_key] = depth + 1
    should_lock = depth == 0
    try:
        yield should_lock
    finally:
        with _PROJECT_FILE_LOCK_GUARD:
            current_depth = _PROJECT_FILE_LOCK_DEPTHS.get(owner_key, 0)
            if current_depth <= 1:
                _PROJECT_FILE_LOCK_DEPTHS.pop(owner_key, None)
            else:
                _PROJECT_FILE_LOCK_DEPTHS[owner_key] = current_depth - 1


@contextmanager
def project_file_lock_owner(owner_id: str) -> Iterator[None]:
    """Bind nested filesystem locks to one logical async/task owner."""
    clean_owner = str(owner_id or "").strip()
    if not clean_owner:
        yield
        return
    token = _PROJECT_FILE_LOCK_OWNER.set(clean_owner)
    try:
        yield
    finally:
        _PROJECT_FILE_LOCK_OWNER.reset(token)


@dataclass(frozen=True)
class _JSONCacheEntry:
    mtime_ns: int
    size: int
    payload: dict[str, Any]
    fetched_at: float = 0.0


@dataclass(frozen=True)
class _TextCacheEntry:
    mtime_ns: int
    size: int
    payload: str
    fetched_at: float = 0.0


@dataclass(frozen=True)
class _DirMtimeCacheEntry:
    mtime: float
    fetched_at: float


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a text file by writing to a sibling temp file first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass  # On Windows, fsync on directory handle may fail
            finally:
                os.close(dir_fd)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def atomic_write_text_fast(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a text file, skipping directory fsync.

    Suitable for best-effort checkpoint files where directory-level
    durability is not required.  The file itself is still fsync'd before
    ``os.replace``, so the data is durable once the call returns; only
    the directory entry may lag on power loss.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


async def async_atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Async wrapper for atomic_write_text.

    Offloads the blocking file I/O (including fsync) to a thread pool,
    preventing event loop blocking during large file writes.
    """
    import asyncio

    await asyncio.to_thread(atomic_write_text, path, text, encoding=encoding)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace a binary file and fsync it before publication."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically save JSON content with stable formatting."""
    atomic_write_text(
        path,
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
    )


def atomic_append_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Append text with file-lock protection and fsync durability.

    Uses true O(1) append instead of the former read-all + replace strategy.
    Crash safety: a partial write leaves at most one incomplete trailing line,
    which callers (e.g. ``polish_history._load_entries``) already tolerate via
    ``json.JSONDecodeError`` handling.
    """
    if not text:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.append.lock")
    with lock_path.open("a+", encoding=encoding) as lock_handle:
        _lock_file(lock_handle, exclusive=True)
        try:
            needs_leading_newline = (
                path.exists() and path.stat().st_size > 0 and not text.startswith("\n")
            )
            with path.open("a", encoding=encoding) as f:
                if needs_leading_newline:
                    f.write("\n")
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        finally:
            _unlock_file(lock_handle)


class FileSystemStorage(StorageBackend):
    """Concrete storage backend using the local filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def project_path(self, project_id: str) -> Path:
        return resolve_project_path(self._root, project_id)

    def ensure_project_dir(self, project_id: str) -> Path:
        d = self.project_path(project_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def existing_project_dir(self, project_id: str) -> Path:
        d = self.project_path(project_id)
        if not d.is_dir():
            raise FileNotFoundError(f"Project directory not found: {d}")
        return d

    def project_dir(self, project_id: str) -> Path:
        """Backward-compatible alias for write paths that need to create the directory."""
        return self.ensure_project_dir(project_id)

    def save_json(self, path: Path, data: dict[str, Any]) -> None:
        atomic_write_json(path, data)

    def load_json(self, path: Path) -> dict[str, Any]:
        raw = path.read_text(encoding="utf-8")
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            from novel_forge.core.exceptions import StorageError  # local import avoids circular dep

            raise StorageError(
                f"Invalid JSON in {path}: {exc}",
                operation="load_json",
            ) from exc
        if not isinstance(result, dict):
            from novel_forge.core.exceptions import StorageError  # local import avoids circular dep

            raise StorageError(
                f"Expected JSON object in {path}, got {type(result).__name__}",
                operation="load_json",
            )
        return result

    def save_text(self, path: Path, text: str) -> None:
        atomic_write_text(path, text)

    def save_text_fast(self, path: Path, text: str) -> None:
        """Save text with atomic write but skip directory fsync.

        Suitable for best-effort checkpoint files where directory-level
        durability is not critical.
        """
        atomic_write_text_fast(path, text)

    def load_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return path.exists()

    def list_dir(self, path: Path) -> list[Path]:
        if not path.is_dir():
            return []
        return sorted(path.iterdir())

    @staticmethod
    def _lock_file_name(project_id: str) -> str:
        return normalize_project_id(project_id).replace(os.sep, "_") + ".lock"

    @contextmanager
    def project_lock(self, project_id: str) -> Iterator[None]:
        """Acquire a best-effort cross-process lock for one project."""
        lock_dir = self._root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / self._lock_file_name(project_id)
        lock_key = str(lock_path.resolve(strict=False))
        with _reentrant_project_file_lock(lock_key, exclusive=True) as should_lock:
            if not should_lock:
                yield
                return
            with lock_path.open("a+", encoding="utf-8") as handle:
                _lock_file(handle, exclusive=True)
                try:
                    yield
                finally:
                    _unlock_file(handle)

    @contextmanager
    def project_shared_lock(self, project_id: str) -> Iterator[None]:
        """Acquire a shared (read-compatible) lock for one project.

        Multiple holders of project_shared_lock may coexist; they are only
        blocked by an active project_lock (LOCK_EX) holder.  Use this for
        operations that write only chapter-local files (state_packet, bridge,
        plan, checkpoint) and never touch global project files such as
        outline.json or story_bible.json, so they can safely run in parallel
        with init_long.
        """
        lock_dir = self._root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / self._lock_file_name(project_id)
        lock_key = str(lock_path.resolve(strict=False))
        with _reentrant_project_file_lock(lock_key, exclusive=False) as should_lock:
            if not should_lock:
                yield
                return
            with lock_path.open("a+", encoding="utf-8") as handle:
                _lock_file(handle, exclusive=False)
                try:
                    yield
                finally:
                    _unlock_file(handle)


class CachedFileSystemStorage(FileSystemStorage):
    """Filesystem storage with read-through caches for hot JSON/text files.

    Parameters
    ----------
    max_entries:
        Maximum number of entries per cache (LRU eviction).
    cache_ttl:
        Time-to-live in seconds for cache entries.  Within the TTL window,
        ``load_json`` / ``load_text`` skip the ``path.stat()`` system call
        and return the cached payload directly.  Set to 0 to disable TTL
        (always stat).  Default 2.0 s matches the typical hot-read burst
        during a single pipeline stage.
    """

    def __init__(self, root: Path, *, max_entries: int = 256, cache_ttl: float = 2.0) -> None:
        super().__init__(root)
        self._max_entries = max_entries
        self._cache_ttl = cache_ttl
        self._cache_lock = threading.RLock()
        self._json_cache: OrderedDict[str, _JSONCacheEntry] = OrderedDict()
        self._text_cache: OrderedDict[str, _TextCacheEntry] = OrderedDict()
        self._dir_mtime_cache: OrderedDict[str, _DirMtimeCacheEntry] = OrderedDict()
        self._dir_mtime_ttl = 30.0

    @staticmethod
    def _cache_key(path: Path) -> str:
        return str(path.resolve(strict=False))

    def _remember_json(self, key: str, entry: _JSONCacheEntry) -> None:
        self._json_cache[key] = entry
        self._json_cache.move_to_end(key)
        while len(self._json_cache) > self._max_entries:
            self._json_cache.popitem(last=False)

    def _remember_text(self, key: str, entry: _TextCacheEntry) -> None:
        self._text_cache[key] = entry
        self._text_cache.move_to_end(key)
        while len(self._text_cache) > self._max_entries:
            self._text_cache.popitem(last=False)

    def load_json(self, path: Path) -> dict[str, Any]:
        key = self._cache_key(path)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._json_cache.get(key)
            if cached is not None:
                # Fast path: within TTL, skip stat() entirely.
                if self._cache_ttl > 0 and (now - cached.fetched_at) < self._cache_ttl:
                    self._json_cache.move_to_end(key)
                    return deepcopy(cached.payload)
                # TTL expired: verify via stat().
                stat = path.stat()
                if cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
                    self._json_cache.move_to_end(key)
                    # Refresh fetched_at to extend the TTL window.
                    self._json_cache[key] = _JSONCacheEntry(
                        mtime_ns=cached.mtime_ns,
                        size=cached.size,
                        payload=cached.payload,
                        fetched_at=now,
                    )
                    return deepcopy(cached.payload)
            else:
                stat = path.stat()

        result = super().load_json(path)
        with self._cache_lock:
            self._remember_json(
                key,
                _JSONCacheEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    payload=deepcopy(result),
                    fetched_at=now,
                ),
            )
        return result

    def save_json(self, path: Path, data: dict[str, Any]) -> None:
        super().save_json(path, data)
        stat = path.stat()
        key = self._cache_key(path)
        with self._cache_lock:
            self._text_cache.pop(key, None)
            self._remember_json(
                key,
                _JSONCacheEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    payload=deepcopy(data),
                    fetched_at=time.monotonic(),
                ),
            )

    def load_text(self, path: Path) -> str:
        key = self._cache_key(path)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._text_cache.get(key)
            if cached is not None:
                # Fast path: within TTL, skip stat() entirely.
                if self._cache_ttl > 0 and (now - cached.fetched_at) < self._cache_ttl:
                    self._text_cache.move_to_end(key)
                    return cached.payload
                # TTL expired: verify via stat().
                stat = path.stat()
                if cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
                    self._text_cache.move_to_end(key)
                    self._text_cache[key] = _TextCacheEntry(
                        mtime_ns=cached.mtime_ns,
                        size=cached.size,
                        payload=cached.payload,
                        fetched_at=now,
                    )
                    return cached.payload
            else:
                stat = path.stat()

        result = super().load_text(path)
        with self._cache_lock:
            self._remember_text(
                key,
                _TextCacheEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    payload=result,
                    fetched_at=now,
                ),
            )
        return result

    def save_text(self, path: Path, text: str) -> None:
        super().save_text(path, text)
        stat = path.stat()
        key = self._cache_key(path)
        with self._cache_lock:
            self._json_cache.pop(key, None)
            self._remember_text(
                key,
                _TextCacheEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    payload=text,
                    fetched_at=time.monotonic(),
                ),
            )

    def save_text_fast(self, path: Path, text: str) -> None:
        """Save text with atomic write (no dir fsync) and update cache."""
        atomic_write_text_fast(path, text)
        stat = path.stat()
        key = self._cache_key(path)
        with self._cache_lock:
            self._json_cache.pop(key, None)
            self._remember_text(
                key,
                _TextCacheEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    payload=text,
                    fetched_at=time.monotonic(),
                ),
            )

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._json_cache.clear()
            self._text_cache.clear()
            self._dir_mtime_cache.clear()

    def cache_stats(self) -> dict[str, int]:
        with self._cache_lock:
            return {
                "json_entries": len(self._json_cache),
                "text_entries": len(self._text_cache),
                "dir_mtime_entries": len(self._dir_mtime_cache),
                "max_entries": self._max_entries,
            }

    def directory_latest_mtime(self, path: Path) -> float | None:
        """Return the directory's own ``st_mtime`` (no recursion), cached 30 s."""
        key = str(path.resolve(strict=False))
        now = time.monotonic()
        with self._cache_lock:
            cached = self._dir_mtime_cache.get(key)
            if cached is not None and (now - cached.fetched_at) < self._dir_mtime_ttl:
                self._dir_mtime_cache.move_to_end(key)
                return cached.mtime
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        with self._cache_lock:
            self._dir_mtime_cache[key] = _DirMtimeCacheEntry(mtime=mtime, fetched_at=now)
            self._dir_mtime_cache.move_to_end(key)
            while len(self._dir_mtime_cache) > self._max_entries:
                self._dir_mtime_cache.popitem(last=False)
        return mtime

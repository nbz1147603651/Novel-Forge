"""Persistent HTTP download tasks with resume and integrity gates."""

from __future__ import annotations

import hashlib
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.tts.model_center.locking import model_center_lock
from novel_forge.tts.model_center.schemas import DownloadTaskState, ModelDownloadTask

ProgressCallback = Callable[[str, int], None]
PauseCallback = Callable[[], bool]
SignatureVerifier = Callable[[Path, str, str], bool]

# Save progress state at most every _SAVE_INTERVAL_BYTES or _SAVE_INTERVAL_SECONDS,
# whichever comes first. Reduces atomic_write_json calls from ~2000 (per 1MB)
# to ~40 for a 2GB model download.
_SAVE_INTERVAL_BYTES = 50 * 1024 * 1024  # 50 MB
_SAVE_INTERVAL_SECONDS = 5.0  # 5 seconds


class DownloadIntegrityError(RuntimeError):
    pass


class PersistentDownloadManager:
    """Store enough state to resume a partial transfer after app restart."""

    def __init__(
        self,
        root: Path,
        *,
        signature_verifier: SignatureVerifier | None = None,
    ) -> None:
        self.root = root
        self.tasks_dir = root / "tasks"
        self.signature_verifier = signature_verifier

    def create_task(
        self,
        *,
        task_id: str,
        plugin_id: str,
        url: str,
        target_path: Path,
        expected_sha256: str = "",
        signature: str = "",
        signature_key_id: str = "",
    ) -> ModelDownloadTask:
        existing = self.load(task_id)
        if existing is not None:
            return existing
        task = ModelDownloadTask(
            task_id=task_id,
            plugin_id=plugin_id,
            url=url,
            target_path=str(target_path),
            expected_sha256=expected_sha256.lower(),
            signature=signature,
            signature_key_id=signature_key_id,
        )
        self._save(task)
        return task

    def load(self, task_id: str) -> ModelDownloadTask | None:
        path = self._task_path(task_id)
        if not path.is_file():
            return None
        try:
            return ModelDownloadTask.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def list_tasks(self) -> list[ModelDownloadTask]:
        if not self.tasks_dir.is_dir():
            return []
        tasks = [task for path in self.tasks_dir.glob("*.json") if (task := self.load(path.stem))]
        return sorted(tasks, key=lambda item: item.updated_at, reverse=True)

    def run(
        self,
        task_id: str,
        *,
        progress: ProgressCallback | None = None,
        should_pause: PauseCallback | None = None,
    ) -> ModelDownloadTask:
        task = self.load(task_id)
        if task is None:
            raise KeyError(f"下载任务不存在：{task_id}")
        target = Path(task.target_path)
        partial = target.with_name(f"{target.name}.part")
        target.parent.mkdir(parents=True, exist_ok=True)
        emit = progress or (lambda _message, _percent: None)
        with model_center_lock(self.root / "locks" / f"download-{task_id}.lock"):
            if task.state == DownloadTaskState.COMPLETED and target.is_file():
                try:
                    self._verify(target, task)
                except Exception:
                    target.unlink(missing_ok=True)
                else:
                    task.bytes_downloaded = target.stat().st_size
                    task.total_bytes = task.bytes_downloaded
                    self._save(task)
                    emit("下载文件已完成并通过校验，直接复用。", 100)
                    return task
            offset = partial.stat().st_size if partial.is_file() else 0
            headers = {"User-Agent": "NovelForge/AudioModelCenter"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            task.state = DownloadTaskState.RUNNING
            task.bytes_downloaded = offset
            task.error = ""
            self._save(task)
            try:
                request = urllib.request.Request(task.url, headers=headers)
                with urllib.request.urlopen(request, timeout=60) as response:
                    resumed = offset > 0 and getattr(response, "status", 200) == 206
                    if offset and not resumed:
                        offset = 0
                        task.bytes_downloaded = 0
                    mode = "ab" if resumed else "wb"
                    remaining = int(response.headers.get("Content-Length") or 0)
                    task.total_bytes = offset + remaining if remaining else 0
                    with partial.open(mode) as output:
                        last_save_bytes = task.bytes_downloaded
                        last_save_time = time.monotonic()
                        while chunk := response.read(1024 * 1024):
                            output.write(chunk)
                            task.bytes_downloaded += len(chunk)
                            if should_pause is not None and should_pause():
                                task.state = DownloadTaskState.PAUSED
                                self._save(task)
                                emit("下载已暂停，可在重启应用后继续。", self._percent(task))
                                return task
                            # Throttle progress persistence: save at most every
                            # 50MB or 5s to reduce I/O on large downloads.
                            now = time.monotonic()
                            bytes_since_save = task.bytes_downloaded - last_save_bytes
                            time_since_save = now - last_save_time
                            if (
                                bytes_since_save >= _SAVE_INTERVAL_BYTES
                                or time_since_save >= _SAVE_INTERVAL_SECONDS
                            ):
                                self._save(task)
                                last_save_bytes = task.bytes_downloaded
                                last_save_time = now
                            emit(
                                f"已下载 {task.bytes_downloaded / 1024 / 1024:.1f} MB",
                                self._percent(task),
                            )
                self._verify(partial, task)
                partial.replace(target)
                task.state = DownloadTaskState.COMPLETED
                task.bytes_downloaded = target.stat().st_size
                task.total_bytes = task.bytes_downloaded
                self._save(task)
                emit("下载完成并通过完整性校验。", 100)
                return task
            except Exception as exc:
                task.state = DownloadTaskState.FAILED
                task.error = str(exc)
                self._save(task)
                raise

    def pause(self, task_id: str) -> ModelDownloadTask:
        task = self.load(task_id)
        if task is None:
            raise KeyError(task_id)
        task.state = DownloadTaskState.PAUSED
        self._save(task)
        return task

    def _verify(self, path: Path, task: ModelDownloadTask) -> None:
        if task.expected_sha256:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest().lower() != task.expected_sha256.lower():
                raise DownloadIntegrityError("SHA256 校验失败，已保留分片供诊断。")
        if task.signature:
            if self.signature_verifier is None:
                raise DownloadIntegrityError("模型带有签名，但未配置可信签名验证器。")
            if not self.signature_verifier(path, task.signature, task.signature_key_id):
                raise DownloadIntegrityError("模型签名验证失败。")

    def _save(self, task: ModelDownloadTask) -> None:
        task.updated_at = datetime.now(timezone.utc)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._task_path(task.task_id), task.model_dump(mode="json"))

    def _task_path(self, task_id: str) -> Path:
        safe = "".join(char for char in task_id if char.isalnum() or char in "-_")
        if not safe or safe != task_id:
            raise ValueError("下载任务 ID 不合法。")
        return self.tasks_dir / f"{safe}.json"

    @staticmethod
    def _percent(task: ModelDownloadTask) -> int:
        return (
            min(99, int(task.bytes_downloaded * 100 / task.total_bytes)) if task.total_bytes else -1
        )

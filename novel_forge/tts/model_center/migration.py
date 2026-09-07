"""Verified model-repository migration with an explicit rollback anchor."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.tts.model_center.locking import (
    active_model_center_locks,
    model_center_lock,
)
from novel_forge.tts.model_center.schemas import ModelRepositoryMigration


class ModelRepositoryMigrationError(RuntimeError):
    pass


class ModelRepositoryMigrator:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.history_dir = state_root / "migrations"

    def migrate(
        self,
        source_root: Path,
        target_root: Path,
        *,
        resume_runtime_ids: Sequence[str] = (),
    ) -> ModelRepositoryMigration:
        source = source_root.expanduser().resolve()
        target = target_root.expanduser().resolve()
        if not source.is_dir():
            raise ModelRepositoryMigrationError("源模型库不存在。")
        if source == target:
            raise ModelRepositoryMigrationError("目标目录与当前模型库相同。")
        if source in target.parents:
            raise ModelRepositoryMigrationError("目标目录不能位于源模型库内。")
        if target.exists() and any(target.iterdir()):
            raise ModelRepositoryMigrationError("目标目录必须为空。")
        active_locks = active_model_center_locks(source)
        if active_locks:
            names = "、".join(path.stem for path in active_locks[:4])
            raise ModelRepositoryMigrationError(f"模型中心仍有任务运行：{names}。请稍后重试。")
        source_size = self._tree_size(source)
        disk_probe = target if target.exists() else self._nearest_existing_parent(target)
        available = shutil.disk_usage(disk_probe).free
        reserve = max(256 * 1024 * 1024, source_size // 20)
        required = source_size + reserve
        if available < required:
            raise ModelRepositoryMigrationError(
                "目标磁盘空间不足："
                f"需要至少 {self._format_bytes(required)}，"
                f"当前可用 {self._format_bytes(available)}。"
            )
        migration = ModelRepositoryMigration(
            migration_id=uuid.uuid4().hex,
            source_root=str(source),
            target_root=str(target),
            backup_root=str(source),
            state="copying",
            source_size_bytes=source_size,
            required_free_bytes=required,
            available_free_bytes=available,
            resume_runtime_ids=list(resume_runtime_ids),
        )
        self._save(migration)
        staging = target.with_name(f".{target.name}.{migration.migration_id}.migrating")
        with model_center_lock(self.state_root / "locks" / "repository-migration.lock"):
            shutil.rmtree(staging, ignore_errors=True)
            try:
                shutil.copytree(source, staging)
                source_manifest = self._manifest(source)
                target_manifest = self._manifest(staging)
                if source_manifest != target_manifest:
                    raise ModelRepositoryMigrationError("迁移后文件清单或 SHA256 不一致。")
                migration.manifest_sha256 = self._manifest_digest(source_manifest)
                if target.exists():
                    target.rmdir()
                staging.replace(target)
                # The migration lock lives below the source repository's
                # lifecycle directory and is therefore copied with the tree.
                # Never leave that live-owner lock behind in the activated copy.
                (target / "lifecycle" / "locks" / "repository-migration.lock").unlink(
                    missing_ok=True
                )
                migration.state = "committed"
                migration.updated_at = datetime.now(timezone.utc)
                self._save(migration)
                target_history = target / "lifecycle" / "migrations"
                target_history.mkdir(parents=True, exist_ok=True)
                atomic_write_json(
                    target_history / f"{migration.migration_id}.json",
                    migration.model_dump(mode="json"),
                )
                return migration
            except Exception as exc:
                shutil.rmtree(staging, ignore_errors=True)
                migration.state = "failed"
                migration.error = str(exc)
                self._save(migration)
                raise

    def rollback(self, migration_id: str) -> ModelRepositoryMigration:
        with model_center_lock(self.state_root / "locks" / "repository-migration.lock"):
            migration = self.load(migration_id)
            if migration is None or migration.state != "committed":
                raise ModelRepositoryMigrationError("迁移记录不存在或不可回滚。")
            source = Path(migration.source_root)
            target = Path(migration.target_root)
            if source.resolve() == target.resolve():
                raise ModelRepositoryMigrationError("迁移记录中的源目录与目标目录相同。")
            if target.resolve() in {Path(target.anchor).resolve(), Path.home().resolve()}:
                raise ModelRepositoryMigrationError("迁移记录指向受保护目录，拒绝自动删除。")
            if not source.is_dir():
                raise ModelRepositoryMigrationError("原模型库回滚锚点已不存在。")
            active_locks = active_model_center_locks(target)
            if active_locks:
                names = "、".join(path.stem for path in active_locks[:4])
                raise ModelRepositoryMigrationError(f"模型中心仍有任务运行：{names}。请稍后重试。")
            if target.is_dir():
                current = self._manifest_digest(self._manifest(target))
                if current != migration.manifest_sha256:
                    raise ModelRepositoryMigrationError(
                        "新模型库的模型或运行时已发生变化，拒绝自动删除。"
                    )
                shutil.rmtree(target)
            migration.state = "rolled_back"
            migration.updated_at = datetime.now(timezone.utc)
            source_history = source / "lifecycle" / "migrations"
            source_history.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                source_history / f"{migration.migration_id}.json",
                migration.model_dump(mode="json"),
            )
            try:
                self.state_root.resolve().relative_to(target.resolve())
            except ValueError:
                self._save(migration)
            return migration

    def load(self, migration_id: str) -> ModelRepositoryMigration | None:
        path = self.history_dir / f"{migration_id}.json"
        try:
            return ModelRepositoryMigration.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def latest_committed(self) -> ModelRepositoryMigration | None:
        if not self.history_dir.is_dir():
            return None
        records = [
            record
            for path in self.history_dir.glob("*.json")
            if (record := self.load(path.stem)) is not None and record.state == "committed"
        ]
        return max(records, key=lambda item: item.updated_at) if records else None

    @staticmethod
    def _manifest(root: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root)
            if ModelRepositoryMigrator._is_volatile(relative):
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            result[relative.as_posix()] = digest.hexdigest()
        return result

    @staticmethod
    def _is_volatile(relative: Path) -> bool:
        parts = relative.parts
        if not parts:
            return False
        if parts[0] in {"logs", "locks"}:
            return True
        if parts[:2] in {
            ("lifecycle", "locks"),
            ("lifecycle", "migrations"),
            ("downloads", "tasks"),
        }:
            return True
        return len(parts) >= 3 and parts[0] == "runtimes" and parts[-1] == "state.json"

    @staticmethod
    def _tree_size(root: Path) -> int:
        total = 0
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _nearest_existing_parent(path: Path) -> Path:
        candidate = path
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate

    @staticmethod
    def _format_bytes(value: int) -> str:
        size = float(max(0, value))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return "0 B"

    @staticmethod
    def _manifest_digest(manifest: dict[str, str]) -> str:
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _save(self, migration: ModelRepositoryMigration) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self.history_dir / f"{migration.migration_id}.json",
            migration.model_dump(mode="json"),
        )

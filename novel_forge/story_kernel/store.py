"""StoryKernelStore — FieldPool storage layer with full CRUD operations.

Provides async CRUD for the unified field pool (StoryKernel) backed by
SQLAlchemy 2.0 async sessions over SQLite with WAL journal mode.
"""

from __future__ import annotations

import json as _json
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from sqlalchemy import Integer, String, Text, event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from novel_forge.persistence.filesystem import atomic_write_text as _atomic_write_text
from novel_forge.story_kernel.orm import (
    AccessLedger as ORMAccessLedger,
)
from novel_forge.story_kernel.orm import (
    Base as ORMBase,
)
from novel_forge.story_kernel.orm import (
    BusinessDependency as ORMBusinessDependency,
)
from novel_forge.story_kernel.orm import (
    Entity as ORMEntity,
)
from novel_forge.story_kernel.orm import (
    KnowledgeLedger as ORMKnowledgeLedger,
)
from novel_forge.story_kernel.orm import (
    MotifProtocol as ORMMotifProtocol,
)
from novel_forge.story_kernel.orm import (
    ObjectLedger as ORMObjectLedger,
)
from novel_forge.story_kernel.orm import (
    PromiseLedger as ORMPromiseLedger,
)
from novel_forge.story_kernel.orm import (
    Relationship as ORMRelationship,
)
from novel_forge.story_kernel.orm import (
    TimelineAnchor as ORMTimelineAnchor,
)
from novel_forge.story_kernel.schemas import (
    Entity,
    KnowledgeLedger,
    ObjectLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
)

_log = logging.getLogger("novel_forge.story_kernel.store")


# ---------------------------------------------------------------------------
# Kernel metadata table (not in orm.py — store-internal)
# ---------------------------------------------------------------------------


class _KernelMetaBase(DeclarativeBase):
    """Separate base for store-internal tables."""

    pass


class KernelMeta(_KernelMetaBase):
    """Stores StoryKernel metadata as JSON alongside indexed columns."""

    __tablename__ = "kernel_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    current_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kernel_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


# ---------------------------------------------------------------------------
# StoryKernelStore
# ---------------------------------------------------------------------------


class StoryKernelStore:
    """Async CRUD store for the unified field pool (StoryKernel).

    Uses SQLAlchemy 2.0 async sessions over SQLite with WAL journal mode
    and foreign key enforcement.

    Parameters
    ----------
    db_path:
        SQLite database file path. Use :meth:`in_memory` for ephemeral tests.
    echo:
        If ``True``, emit all SQL statements to stderr.
    wal_mode:
        If ``True``, enable SQLite WAL journal mode for file-backed databases.
    """

    _db_path: str
    _db_url: str
    _engine: AsyncEngine
    _session_factory: async_sessionmaker[AsyncSession]

    def __init__(
        self,
        db_path: str | Path,
        *,
        echo: bool = False,
        wal_mode: bool = True,
    ) -> None:
        self._db_path = str(Path(str(db_path)).expanduser())
        self._db_url = _sqlite_url_from_path(self._db_path)
        _ensure_sqlite_parent_dir(self._db_path)
        self._configure_engine(echo=echo, wal_mode=wal_mode)

    @classmethod
    def in_memory(cls, *, echo: bool = False) -> "StoryKernelStore":
        """Create an ephemeral in-memory StoryKernel store for tests."""
        store = cls.__new__(cls)
        store._db_path = ":memory:"
        store._db_url = "sqlite+aiosqlite://"
        store._configure_engine(echo=echo, wal_mode=False)
        return store

    def _configure_engine(self, *, echo: bool, wal_mode: bool) -> None:
        self._engine: AsyncEngine = create_async_engine(self._db_url, echo=echo)

        # Register WAL + foreign_keys pragmas on every new connection.
        @event.listens_for(self._engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            if wal_mode and self._db_path != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """Yield an auto-committing / auto-rolling-back session."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def init_db(self) -> None:
        """Create all tables if they do not exist."""
        async with self._engine.begin() as conn:
            await conn.run_sync(ORMBase.metadata.create_all)
            await conn.run_sync(_KernelMetaBase.metadata.create_all)
            await conn.run_sync(_ensure_story_kernel_schema)

    async def close(self) -> None:
        """Dispose of the engine and release connection pool resources."""
        await self._engine.dispose()

    # ------------------------------------------------------------------
    # Kernel CRUD
    # ------------------------------------------------------------------

    async def create_kernel(self, project_id: str) -> StoryKernel:
        """Create a new empty kernel for *project_id*.

        Raises ``ValueError`` if a kernel already exists for this project.
        """
        async with self._session() as session:
            existing = await session.execute(
                select(KernelMeta).where(KernelMeta.project_id == project_id)
            )
            if existing.scalar_one_or_none() is not None:
                raise ValueError(f"Kernel for project '{project_id}' already exists.")

            kernel = StoryKernel(project_id=project_id)
            meta = KernelMeta(
                project_id=project_id,
                current_chapter=0,
                kernel_json=kernel.model_dump_json(),
            )
            session.add(meta)
            _log.info("create_kernel | project_id=%s", project_id)
            return kernel

    async def load_kernel(self, project_id: str) -> StoryKernel:
        """Load the StoryKernel for *project_id*.

        Raises ``ValueError`` if the project does not exist.
        """
        async with self._session() as session:
            result = await session.execute(
                select(KernelMeta).where(KernelMeta.project_id == project_id)
            )
            meta = result.scalar_one_or_none()
            if meta is None:
                raise ValueError(f"Kernel for project '{project_id}' not found.")
            return StoryKernel.model_validate_json(meta.kernel_json)

    async def save_kernel(self, kernel: StoryKernel) -> None:
        """Persist a StoryKernel (upsert by project_id)."""
        async with self._session() as session:
            result = await session.execute(
                select(KernelMeta).where(KernelMeta.project_id == kernel.project_id)
            )
            meta = result.scalar_one_or_none()
            if meta is None:
                meta = KernelMeta(project_id=kernel.project_id)
                session.add(meta)

            meta.current_chapter = kernel.current_chapter
            meta.kernel_json = kernel.model_dump_json()
            await _replace_index_rows(session, kernel)
            _log.info("save_kernel | project_id=%s", kernel.project_id)

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    async def add_entity(self, entity: Entity) -> None:
        """Insert a new entity row."""
        async with self._session() as session:
            row = ORMEntity(
                name=entity.name,
                entity_type=entity.entity_type,
                aliases=entity.aliases,
                source=f"eid:{entity.entity_id}",
                notes=entity.model_dump_json(),
            )
            session.add(row)
            await session.flush()
            await _upsert_kernel_model(session, "entities", "entity_id", entity)
            _log.debug("add_entity | entity_id=%s orm_id=%d", entity.entity_id, row.id)

    async def get_entity(self, entity_id: str) -> Entity | None:
        """Retrieve an entity by its Pydantic entity_id."""
        async with self._session() as session:
            result = await session.execute(
                select(ORMEntity).where(ORMEntity.source == f"eid:{entity_id}")
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _orm_entity_to_pydantic(row)

    async def update_entity(self, entity_id: str, updates: dict[str, Any]) -> None:
        """Update fields on an existing entity.

        Raises ``ValueError`` if the entity is not found.
        """
        async with self._session() as session:
            result = await session.execute(
                select(ORMEntity).where(ORMEntity.source == f"eid:{entity_id}")
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise ValueError(f"Entity '{entity_id}' not found.")

            orm_field_map = {
                "name": "name",
                "entity_type": "entity_type",
                "aliases": "aliases",
            }
            for key, value in updates.items():
                orm_key = orm_field_map.get(key)
                if orm_key is not None:
                    setattr(row, orm_key, value)

            existing = _orm_entity_to_pydantic(row)
            merged = existing.model_copy(update=updates)
            row.notes = merged.model_dump_json()
            await _upsert_kernel_model(session, "entities", "entity_id", merged)
            _log.debug("update_entity | entity_id=%s keys=%s", entity_id, list(updates))

    # ------------------------------------------------------------------
    # Relationship CRUD
    # ------------------------------------------------------------------

    async def add_relationship(self, rel: Relationship) -> None:
        """Insert a new relationship row."""
        async with self._session() as session:
            # Resolve entity_id → ORM id for FK columns
            source_orm_id = await _resolve_entity_orm_id(session, rel.source_entity_id)
            target_orm_id = await _resolve_entity_orm_id(session, rel.target_entity_id)
            if source_orm_id is None or target_orm_id is None:
                raise ValueError(
                    "Relationship endpoints must exist before adding a relationship: "
                    f"{rel.source_entity_id!r} -> {rel.target_entity_id!r}."
                )
            row = ORMRelationship(
                source_id=source_orm_id,
                target_id=target_orm_id,
                relation_type=rel.relation_type,
                description=rel.model_dump_json(),
                is_public=rel.status == "active",
                confidence=rel.trust,
                source=f"rid:{rel.relationship_id}",
            )
            session.add(row)
            await _upsert_kernel_model(session, "relationships", "relationship_id", rel)
            _log.debug("add_relationship | relationship_id=%s", rel.relationship_id)

    async def get_relationships(self, entity_id: str) -> list[Relationship]:
        """Get all relationships where *entity_id* is source or target."""
        async with self._session() as session:
            orm_id = await _resolve_entity_orm_id(session, entity_id)
            if orm_id is None:
                return []

            result = await session.execute(
                select(ORMRelationship).where(
                    (ORMRelationship.source_id == orm_id) | (ORMRelationship.target_id == orm_id)
                )
            )
            rows = result.scalars().all()
            return [_orm_relationship_to_pydantic(row) for row in rows]

    # ------------------------------------------------------------------
    # Timeline anchors
    # ------------------------------------------------------------------

    async def add_timeline_anchor(self, anchor: TimelineAnchor) -> None:
        """Insert a new timeline anchor."""
        async with self._session() as session:
            # Resolve first character entity as the primary entity FK when available.
            entity_orm_id: int | None = None
            if anchor.characters_involved:
                entity_orm_id = await _resolve_entity_orm_id(session, anchor.characters_involved[0])

            row = ORMTimelineAnchor(
                entity_id=entity_orm_id,
                event_text=anchor.event,
                anchor_date=anchor.in_story_time,
                anchor_type=anchor.significance,
                source=f"aid:{anchor.anchor_id}",
                notes=anchor.model_dump_json(),
            )
            session.add(row)
            await _upsert_kernel_model(session, "timeline", "anchor_id", anchor)
            _log.debug("add_timeline_anchor | anchor_id=%s", anchor.anchor_id)

    # ------------------------------------------------------------------
    # Object ledger
    # ------------------------------------------------------------------

    async def add_object_ledger_entry(self, entry: ObjectLedger) -> None:
        """Insert a new object ledger entry."""
        async with self._session() as session:
            row = ORMObjectLedger(
                object_name=entry.item_name,
                origin=entry.location,
                current_owner=entry.owner_entity_id,
                visibility=entry.visibility,
                chapter_acquired=entry.introduced_chapter,
                source=f"oid:{entry.entry_id}",
                notes=entry.model_dump_json(),
            )
            session.add(row)
            await _upsert_kernel_model(session, "object_ledger", "entry_id", entry)
            _log.debug("add_object_ledger_entry | entry_id=%s", entry.entry_id)

    # ------------------------------------------------------------------
    # Knowledge ledger
    # ------------------------------------------------------------------

    async def add_knowledge_entry(self, entry: KnowledgeLedger) -> None:
        """Insert a new knowledge ledger entry."""
        async with self._session() as session:
            entity_orm_id = await _resolve_entity_orm_id(session, entry.entity_id)
            row = ORMKnowledgeLedger(
                entity_id=entity_orm_id or 0,
                fact_text=entry.fact,
                learned_chapter=entry.source_chapter,
                evidence="",
                is_secret=entry.knowledge_type in ("secret_kept", "secret"),
                source=f"kid:{entry.entry_id}",
                notes=entry.model_dump_json(),
            )
            session.add(row)
            await _upsert_kernel_model(session, "knowledge_ledger", "entry_id", entry)
            _log.debug("add_knowledge_entry | entry_id=%s", entry.entry_id)

    async def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        entity_ids: list[str] | None = None,
    ) -> list[KnowledgeLedger]:
        """Return knowledge entries, optionally filtered by entity_ids.

        Parameters
        ----------
        query:
            Query text matched against local knowledge fields. This is a
            deterministic text-search fallback until vector retrieval is wired in.
        top_k:
            Maximum number of entries to return.
        entity_ids:
            If provided, only return entries whose ``entity_id`` is in this list.
            ``None`` returns all entries (backward-compatible).
        """
        async with self._session() as session:
            meta = await _first_kernel_meta(session)
            if meta is None:
                return []
            kernel = StoryKernel.model_validate_json(meta.kernel_json)
            entries = list(kernel.knowledge_ledger)
            if entity_ids is not None:
                entity_set = set(entity_ids)
                entries = [e for e in entries if e.entity_id in entity_set]
            query_text = str(query or "").strip().lower()
            if query_text:
                scored = [
                    (score, index, entry)
                    for index, entry in enumerate(entries)
                    if (score := _score_knowledge_entry(entry, query_text)) > 0
                ]
                scored.sort(key=lambda item: (-item[0], item[1]))
                entries = [entry for _, _, entry in scored]
            return entries[:top_k]

    # ------------------------------------------------------------------
    # Promise ledger
    # ------------------------------------------------------------------

    async def add_promise(self, entry: PromiseLedger) -> None:
        """Insert a new promise ledger entry."""
        async with self._session() as session:
            row = ORMPromiseLedger(
                promise_text=entry.description,
                planted_chapter=entry.planted_chapter,
                expected_window=entry.promise_type,
                fulfilled_chapter=entry.payoff_chapter or None,
                source=f"pid:{entry.entry_id}",
                notes=entry.model_dump_json(),
            )
            session.add(row)
            await _upsert_kernel_model(session, "promise_ledger", "entry_id", entry)
            _log.debug("add_promise | entry_id=%s", entry.entry_id)

    # ------------------------------------------------------------------
    # Volume archive — timeline / summaries / exit_states / promises
    # ------------------------------------------------------------------

    async def archive_volume_data(
        self,
        project_id: str,
        *,
        current_volume: int,
        volumes_to_keep: int = 2,
        archive_before_chapter: int | None = None,
        protected_chapters: set[int] | None = None,
    ) -> dict[str, int]:
        """Archive old volume data to reduce kernel JSON size.

        Moves timeline anchors older than *volumes_to_keep* volumes to
        ``archived_timeline``, cleans old chapter summaries/exit states,
        and archives paid/broken promises.

        Returns a dict with counts of archived items per category.
        """
        protected = set(protected_chapters or set())
        async with self._session() as session:
            result = await session.execute(
                select(KernelMeta).where(KernelMeta.project_id == project_id)
            )
            meta = result.scalar_one_or_none()
            if meta is None:
                return {}
            kernel = StoryKernel.model_validate_json(meta.kernel_json)

            stats: dict[str, int] = {}

            # --- 1. Timeline: archive anchors from volumes older than cutoff ---
            # Prefer caller-provided outline boundaries. Fall back to the legacy
            # estimate only when the caller cannot provide a real volume start.
            if archive_before_chapter is not None:
                cutoff_chapter = max(1, int(archive_before_chapter))
            else:
                cutoff_chapter = max(1, (current_volume - volumes_to_keep) * 20 + 1)
            active_timeline: list[TimelineAnchor] = []
            archived_timeline: list[TimelineAnchor] = list(kernel.archived_timeline)
            archived_count = 0
            for anchor in kernel.timeline:
                if anchor.chapter < cutoff_chapter:
                    archived_timeline.append(anchor)
                    archived_count += 1
                else:
                    active_timeline.append(anchor)
            stats["timeline_archived"] = archived_count

            # --- 2. Chapter summaries: keep recent volumes + protected ---
            summaries_cutoff = cutoff_chapter
            active_summaries: dict[int, str] = {}
            for ch, summary in kernel.chapter_summaries.items():
                if ch >= summaries_cutoff or ch in protected:
                    active_summaries[ch] = summary
            stats["summaries_removed"] = len(kernel.chapter_summaries) - len(active_summaries)

            # --- 3. Chapter exit states: same strategy ---
            active_exit_states: dict[int, Any] = {}
            for ch, state in kernel.chapter_exit_states.items():
                if ch >= summaries_cutoff or ch in protected:
                    active_exit_states[ch] = state
            stats["exit_states_removed"] = len(kernel.chapter_exit_states) - len(active_exit_states)

            # --- 4. Promise ledger: archive paid/broken ---
            active_promises: list[PromiseLedger] = []
            archived_promises: list[PromiseLedger] = list(kernel.archived_promises)
            promise_archived = 0
            for promise in kernel.promise_ledger:
                if promise.status in ("paid", "broken"):
                    archived_promises.append(promise)
                    promise_archived += 1
                else:
                    active_promises.append(promise)
            stats["promises_archived"] = promise_archived

            # Apply changes
            updated = kernel.model_copy(
                update={
                    "timeline": active_timeline,
                    "archived_timeline": archived_timeline,
                    "chapter_summaries": active_summaries,
                    "chapter_exit_states": active_exit_states,
                    "promise_ledger": active_promises,
                    "archived_promises": archived_promises,
                }
            )
            meta.current_chapter = updated.current_chapter
            meta.kernel_json = updated.model_dump_json()
            await _replace_index_rows(session, updated)

            _log.info(
                "archive_volume_data | project=%s volume=%d | %s",
                project_id,
                current_volume,
                stats,
            )
            return stats

    # ------------------------------------------------------------------
    # query_field_slice
    # ------------------------------------------------------------------

    async def query_field_slice(
        self, field_names: list[str], filters: dict[str, Any]
    ) -> dict[str, Any]:
        """Query specific field groups with optional filters.

        Parameters
        ----------
        field_names:
            List of StoryKernel field names to include in the result.
            Supported: ``entities``, ``relationships``, ``timeline``,
            ``world_rules``, ``object_ledger``, ``knowledge_ledger``,
            ``access_ledger``, ``promise_ledger``, ``motif_protocols``,
            ``business_dependencies``, ``title``, ``current_chapter``,
            ``project_id``, ``active_volume``, ``premise``, ``notes``,
            ``chapter_summaries``, ``banned_phrases``.
        filters:
            Filter predicates. Supported keys depend on the field:
            - ``entity_type`` — filter entities by type
            - ``entity_id`` — filter relationships/knowledge by entity
            - ``relation_type`` — filter relationships by type

        Returns
        -------
        dict[str, Any]
            Keys are the requested field names; values are the queried data.
        """
        if not field_names:
            return {}

        async with self._session() as session:
            meta = await _first_kernel_meta(session)
            if meta is None:
                return {}
            kernel = StoryKernel.model_validate_json(meta.kernel_json)
            return _slice_kernel(kernel, field_names, filters)

    # ------------------------------------------------------------------
    # Snapshot / Rollback / Prune
    # ------------------------------------------------------------------

    @property
    def _snapshot_dir(self) -> Path:
        """Return the snapshot directory adjacent to the DB file."""
        return Path(self._db_path).parent / "snapshots"

    def snapshot_path(self, chapter: int) -> Path:
        """Return the filesystem path for a given chapter snapshot."""
        return self._snapshot_dir / f"kernel_v{chapter}.db"

    async def save_snapshot(self, chapter: int) -> None:
        """Copy the current kernel DB file as a snapshot for *chapter*.

        Raises ``ValueError`` if the store is in-memory (no file to copy).
        """
        if self._db_path == ":memory:":
            raise ValueError("Cannot snapshot an in-memory database.")
        src = Path(self._db_path)
        if not src.exists():
            raise ValueError(f"Database file does not exist: {src}")
        async with self._session() as session:
            await session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        dest = self.snapshot_path(chapter)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
        _log.info("save_snapshot | chapter=%d path=%s", chapter, dest)

    async def load_snapshot(self, chapter: int) -> StoryKernel:
        """Load the kernel JSON stored in the snapshot for *chapter*.

        Raises ``ValueError`` if the snapshot file does not exist.
        """
        path = self.snapshot_path(chapter)
        if not path.exists():
            available = self.list_snapshots()
            raise ValueError(
                f"Snapshot for chapter {chapter} not found at {path}.\n"
                f"Available snapshots: {available}"
            )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session as SyncSession

        url = f"sqlite:///{path}"
        sync_engine = create_engine(url)
        try:
            with SyncSession(sync_engine) as session:
                result = session.execute(text("SELECT kernel_json FROM kernel_meta LIMIT 1"))
                row = result.first()
                if row is None:
                    raise ValueError(f"Snapshot DB for chapter {chapter} has no kernel_meta row.")
                return StoryKernel.model_validate_json(row[0])
        finally:
            sync_engine.dispose()

    def list_snapshots(self) -> list[int]:
        """Return a sorted list of chapter numbers that have snapshots."""
        snap_dir = self._snapshot_dir
        if not snap_dir.exists():
            return []
        nums: list[int] = []
        for f in snap_dir.glob("kernel_v*.db"):
            try:
                num = int(f.stem.split("_v")[1])
                nums.append(num)
            except (IndexError, ValueError):
                continue
        return sorted(nums)

    def prune_snapshots(
        self,
        *,
        keep_recent: int = 5,
        keep_chapters: list[int] | None = None,
        max_disk_mb: int = 0,
    ) -> list[int]:
        """Delete old chapter snapshots while preserving rollback anchors.

        Parameters
        ----------
        keep_recent:
            Number of most-recent snapshots to retain.
        keep_chapters:
            Explicit chapter snapshots that must never be deleted.
        max_disk_mb:
            If > 0, enforce a total disk budget for snapshots.  After
            protecting *keep_recent* + *keep_chapters*, remaining snapshots
            are deleted oldest-first until total size is within budget.

        Returns
        -------
        list[int]
            Sorted list of deleted snapshot chapter numbers.
        """
        keep_recent = max(0, int(keep_recent))
        protected = set(keep_chapters or [])
        snapshots = self.list_snapshots()
        if keep_recent:
            protected.update(snapshots[-keep_recent:])

        deleted: list[int] = []

        # Phase 1: delete unprotected snapshots (always)
        for chapter in snapshots:
            if chapter in protected:
                continue
            path = self.snapshot_path(chapter)
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                _log.warning(
                    "story_kernel_snapshot_prune_failed | chapter=%d | error=%s",
                    chapter,
                    exc,
                )
                continue
            deleted.append(chapter)

        # Phase 2: enforce disk budget on remaining (protected) snapshots
        if max_disk_mb > 0:
            max_bytes = max_disk_mb * 1024 * 1024
            remaining = [c for c in snapshots if c not in set(deleted)]
            total_size = sum(
                self.snapshot_path(c).stat().st_size
                for c in remaining
                if self.snapshot_path(c).exists()
            )
            if total_size > max_bytes:
                # Delete oldest protected snapshots (but keep the very last one)
                deletable = [c for c in remaining if c != remaining[-1]]
                for chapter in deletable:
                    if total_size <= max_bytes:
                        break
                    path = self.snapshot_path(chapter)
                    if not path.exists():
                        continue
                    try:
                        fsize = path.stat().st_size
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        _log.warning(
                            "story_kernel_snapshot_disk_prune_failed | chapter=%d | error=%s",
                            chapter,
                            exc,
                        )
                        continue
                    total_size -= fsize
                    if chapter not in deleted:
                        deleted.append(chapter)

        deleted.sort()
        return deleted

    async def rollback_to(self, chapter: int) -> StoryKernel:
        """Rollback the kernel to a specific chapter snapshot.

        Loads the snapshot, replaces the current kernel DB with it, and
        returns the restored ``StoryKernel``.

        Raises ``ValueError`` if the snapshot does not exist.
        """
        import os

        kernel = await self.load_snapshot(chapter)

        await self._engine.dispose()
        src = self.snapshot_path(chapter)
        dest = Path(self._db_path)
        # Atomic replace: copy to temp file first, then os.replace()
        # to avoid data loss if process crashes mid-operation.
        tmp_dest = dest.with_suffix(".db.rollback_tmp")
        shutil.copy2(str(src), str(tmp_dest))
        os.replace(str(tmp_dest), str(dest))
        self._configure_engine(echo=False, wal_mode=True)
        _log.info("rollback_to | chapter=%d", chapter)
        return kernel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sqlite_url_from_path(database_path: str | Path) -> str:
    """Build the internal SQLAlchemy async URL from a plain SQLite file path."""
    raw = str(database_path or "").strip()
    if not raw:
        raise ValueError("StoryKernel database path must not be empty.")
    return f"sqlite+aiosqlite:///{Path(raw).expanduser()}"


def _ensure_sqlite_parent_dir(database_path: str | Path) -> None:
    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


async def _first_kernel_meta(session: AsyncSession) -> KernelMeta | None:
    result = await session.execute(select(KernelMeta).limit(1))
    return result.scalar_one_or_none()


def _ensure_story_kernel_schema(connection: Any) -> None:
    """Backfill lightweight columns added after initial StoryKernel DB creation."""
    required: dict[str, dict[str, str]] = {
        "relationships": {"source": "VARCHAR(256) NOT NULL DEFAULT ''"},
        "timeline_anchors": {
            "source": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        },
        "object_ledger": {
            "source": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        },
        "knowledge_ledger": {
            "source": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        },
        "access_ledger": {
            "source": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        },
        "promise_ledger": {
            "source": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        },
        "motif_protocols": {
            "source": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        },
        "business_dependencies": {
            "source": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        },
    }
    for table_name, columns in required.items():
        existing = {
            str(row[1])
            for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
        }
        for column_name, ddl in columns.items():
            if column_name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"
                )


async def _upsert_kernel_model(
    session: AsyncSession,
    field_name: str,
    id_attr: str,
    item: Any,
) -> None:
    """Mirror a CRUD insert/update into the canonical kernel JSON."""
    meta = await _first_kernel_meta(session)
    if meta is None:
        return

    kernel = StoryKernel.model_validate_json(meta.kernel_json)
    current_items = list(getattr(kernel, field_name))
    item_id = getattr(item, id_attr)
    for index, existing in enumerate(current_items):
        if getattr(existing, id_attr) == item_id:
            current_items[index] = item
            break
    else:
        current_items.append(item)

    updated = kernel.model_copy(update={field_name: current_items})
    meta.current_chapter = updated.current_chapter
    meta.kernel_json = updated.model_dump_json()


async def _replace_index_rows(session: AsyncSession, kernel: StoryKernel) -> None:
    """Incrementally upsert relational index tables from the canonical kernel JSON.

    Instead of DELETE ALL + INSERT ALL (O(N) write amplification), this uses
    a source-key-based upsert strategy:
    - Load existing rows keyed by ``source`` column
    - UPDATE existing rows or INSERT new ones
    - DELETE rows whose source key is no longer present

    Each ORM row stores the full Pydantic model JSON in ``notes`` (or
    ``description`` / ``evidence`` for tables that already used those columns)
    for complete round-trip fidelity.
    """
    # -- Helper closures per ORM table --

    async def _upsert_by_source(
        orm_cls: type,
        source_key: str,
        attrs: dict[str, Any],
        existing: dict[str, Any],
    ) -> None:
        """Update or insert a single ORM row identified by *source_key*."""
        if source_key in existing:
            row = existing.pop(source_key)
            for k, v in attrs.items():
                setattr(row, k, v)
        else:
            session.add(orm_cls(**attrs, source=source_key))

    # --- 1. Entities (must come first — FK target) ---
    result = await session.execute(select(ORMEntity))
    existing_entities: dict[str, ORMEntity] = {
        row.source: row for row in result.scalars() if row.source
    }
    entity_id_to_orm_id: dict[str, int] = {}

    for entity in kernel.entities:
        sk = f"eid:{entity.entity_id}"
        attrs = dict(
            name=entity.name,
            entity_type=entity.entity_type,
            aliases=entity.aliases,
            notes=entity.model_dump_json(),
        )
        await _upsert_by_source(ORMEntity, sk, attrs, existing_entities)

    # Flush to populate PKs for newly inserted entities
    await session.flush()
    result = await session.execute(select(ORMEntity))
    for row in result.scalars():
        if row.source and row.source.startswith("eid:"):
            entity_id_to_orm_id[row.source[4:]] = row.id

    # Delete orphaned entity rows
    for row in existing_entities.values():
        await session.delete(row)

    # --- 2. Relationships ---
    result = await session.execute(select(ORMRelationship))
    existing_rels: dict[str, ORMRelationship] = {
        row.source: row for row in result.scalars() if row.source
    }
    for rel in kernel.relationships:
        sk = f"rid:{rel.relationship_id}"
        source_id = entity_id_to_orm_id.get(rel.source_entity_id)
        target_id = entity_id_to_orm_id.get(rel.target_entity_id)
        if source_id is None or target_id is None:
            continue
        attrs = dict(
            source_id=source_id,
            target_id=target_id,
            relation_type=rel.relation_type,
            description=rel.model_dump_json(),
            is_public=rel.status == "active",
            confidence=rel.trust,
        )
        await _upsert_by_source(ORMRelationship, sk, attrs, existing_rels)
    for row in existing_rels.values():
        await session.delete(row)

    # --- 3. Timeline anchors ---
    result = await session.execute(select(ORMTimelineAnchor))
    existing_anchors: dict[str, ORMTimelineAnchor] = {
        row.source: row for row in result.scalars() if row.source
    }
    for anchor in kernel.timeline:
        sk = f"aid:{anchor.anchor_id}"
        entity_id: int | None = None
        if anchor.characters_involved:
            entity_id = entity_id_to_orm_id.get(anchor.characters_involved[0])
        attrs = dict(
            entity_id=entity_id,
            event_text=anchor.event,
            anchor_date=anchor.in_story_time,
            anchor_type=anchor.significance,
            notes=anchor.model_dump_json(),
        )
        await _upsert_by_source(ORMTimelineAnchor, sk, attrs, existing_anchors)
    for row in existing_anchors.values():
        await session.delete(row)

    # --- 4. Object ledger ---
    result = await session.execute(select(ORMObjectLedger))
    existing_objects: dict[str, ORMObjectLedger] = {
        row.source: row for row in result.scalars() if row.source
    }
    for object_entry in kernel.object_ledger:
        sk = f"oid:{object_entry.entry_id}"
        attrs = dict(
            object_name=object_entry.item_name,
            origin=object_entry.location,
            current_owner=object_entry.owner_entity_id,
            visibility=object_entry.visibility,
            chapter_acquired=object_entry.introduced_chapter,
            notes=object_entry.model_dump_json(),
        )
        await _upsert_by_source(ORMObjectLedger, sk, attrs, existing_objects)
    for row in existing_objects.values():
        await session.delete(row)

    # --- 5. Knowledge ledger ---
    result = await session.execute(select(ORMKnowledgeLedger))
    existing_knowledge: dict[str, ORMKnowledgeLedger] = {
        row.source: row for row in result.scalars() if row.source
    }
    for knowledge_entry in kernel.knowledge_ledger:
        sk = f"kid:{knowledge_entry.entry_id}"
        knowledge_entity_id = entity_id_to_orm_id.get(knowledge_entry.entity_id)
        if knowledge_entity_id is None:
            continue
        attrs = dict(
            entity_id=knowledge_entity_id,
            fact_text=knowledge_entry.fact,
            learned_chapter=knowledge_entry.source_chapter,
            evidence=knowledge_entry.model_dump_json(),
            is_secret=knowledge_entry.knowledge_type in {"secret_kept", "secret"},
            notes=knowledge_entry.model_dump_json(),
        )
        await _upsert_by_source(ORMKnowledgeLedger, sk, attrs, existing_knowledge)
    for row in existing_knowledge.values():
        await session.delete(row)

    # --- 6. Access ledger ---
    result = await session.execute(select(ORMAccessLedger))
    existing_access: dict[str, ORMAccessLedger] = {
        row.source: row for row in result.scalars() if row.source
    }
    for access_entry in kernel.access_ledger:
        sk = f"acid:{access_entry.entry_id}"
        access_entity_id = entity_id_to_orm_id.get(access_entry.entity_id)
        if access_entity_id is None:
            continue
        attrs = dict(
            entity_id=access_entity_id,
            location=access_entry.target,
            reason=access_entry.condition,
            chapter_granted=access_entry.granted_chapter,
            notes=access_entry.model_dump_json(),
        )
        await _upsert_by_source(ORMAccessLedger, sk, attrs, existing_access)
    for row in existing_access.values():
        await session.delete(row)

    # --- 7. Promise ledger ---
    result = await session.execute(select(ORMPromiseLedger))
    existing_promises: dict[str, ORMPromiseLedger] = {
        row.source: row for row in result.scalars() if row.source
    }
    for promise_entry in kernel.promise_ledger:
        sk = f"pid:{promise_entry.entry_id}"
        attrs = dict(
            promise_text=promise_entry.description,
            planted_chapter=promise_entry.planted_chapter,
            expected_window=promise_entry.promise_type,
            fulfilled_chapter=promise_entry.payoff_chapter or None,
            notes=promise_entry.model_dump_json(),
        )
        await _upsert_by_source(ORMPromiseLedger, sk, attrs, existing_promises)
    for row in existing_promises.values():
        await session.delete(row)

    # --- 8. Motif protocols ---
    result = await session.execute(select(ORMMotifProtocol))
    existing_motifs: dict[str, ORMMotifProtocol] = {
        row.source: row for row in result.scalars() if row.source
    }
    for motif_entry in kernel.motif_protocols:
        sk = f"mid:{motif_entry.entry_id}"
        attrs = dict(
            motif_name=motif_entry.motif_name,
            trigger_pattern=motif_entry.description,
            callback_rules=motif_entry.model_dump(mode="json"),
            notes=motif_entry.model_dump_json(),
        )
        await _upsert_by_source(ORMMotifProtocol, sk, attrs, existing_motifs)
    for row in existing_motifs.values():
        await session.delete(row)

    # --- 9. Business dependencies ---
    result = await session.execute(select(ORMBusinessDependency))
    existing_deps: dict[str, ORMBusinessDependency] = {
        row.source: row for row in result.scalars() if row.source
    }
    for dependency_entry in kernel.business_dependencies:
        sk = f"did:{dependency_entry.dependency_id}"
        attrs = dict(
            entity_a=dependency_entry.source_id,
            entity_b=dependency_entry.target_id,
            dependency_type=dependency_entry.dependency_type,
            description=dependency_entry.description,
            notes=dependency_entry.model_dump_json(),
        )
        await _upsert_by_source(ORMBusinessDependency, sk, attrs, existing_deps)
    for row in existing_deps.values():
        await session.delete(row)


def _slice_kernel(
    kernel: StoryKernel,
    field_names: list[str],
    filters: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field_name in field_names:
        if not hasattr(kernel, field_name):
            continue
        value = getattr(kernel, field_name)
        if isinstance(value, list):
            value = _filter_model_list(field_name, value, filters)
            result[field_name] = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in value
            ]
        else:
            result[field_name] = value
    return result


def _filter_model_list(
    field_name: str,
    items: list[Any],
    filters: dict[str, Any],
) -> list[Any]:
    entity_type = filters.get("entity_type")
    entity_id = filters.get("entity_id")
    relation_type = filters.get("relation_type")

    if field_name == "entities" and entity_type:
        return [item for item in items if getattr(item, "entity_type", "") == entity_type]
    if field_name == "relationships":
        filtered = items
        if entity_id:
            filtered = [
                item
                for item in filtered
                if getattr(item, "source_entity_id", "") == entity_id
                or getattr(item, "target_entity_id", "") == entity_id
            ]
        if relation_type:
            filtered = [
                item for item in filtered if getattr(item, "relation_type", "") == relation_type
            ]
        return filtered
    if field_name == "knowledge_ledger" and entity_id:
        return [item for item in items if getattr(item, "entity_id", "") == entity_id]
    return items


def _score_knowledge_entry(entry: KnowledgeLedger, normalized_query: str) -> int:
    haystack = " ".join(
        str(part or "")
        for part in (
            entry.fact,
            entry.knowledge_type,
            entry.entity_id,
            entry.notes,
            entry.visibility,
            entry.source_chapter,
            entry.revealed_in_chapter,
        )
    ).lower()
    if not haystack:
        return 0
    score = 0
    if normalized_query in haystack:
        score += 10
    for term in normalized_query.split():
        if term and term in haystack:
            score += 1
    return score


async def _resolve_entity_orm_id(session: AsyncSession, pydantic_entity_id: str) -> int | None:
    """Map a Pydantic entity_id to the ORM row's integer primary key."""
    result = await session.execute(
        select(ORMEntity.id).where(ORMEntity.source == f"eid:{pydantic_entity_id}")
    )
    return result.scalar_one_or_none()


def _orm_entity_to_pydantic(row: ORMEntity) -> Entity:
    """Convert an ORM Entity row to a Pydantic Entity schema."""
    try:
        return Entity.model_validate_json(row.notes or "{}")
    except Exception:
        source = row.source or ""
        entity_id = source[4:] if source.startswith("eid:") else ""
        return Entity(
            entity_id=entity_id,
            name=row.name,
            entity_type=row.entity_type,
            aliases=row.aliases or [],
            notes=row.notes,
        )


def _orm_entity_to_dict(row: ORMEntity) -> dict[str, Any]:
    """Convert an ORM Entity row to a plain dict."""
    try:
        return Entity.model_validate_json(row.notes or "{}").model_dump(mode="json")
    except Exception:
        source = row.source or ""
        entity_id = source[4:] if source.startswith("eid:") else ""
        return {
            "entity_id": entity_id,
            "name": row.name,
            "entity_type": row.entity_type,
            "aliases": row.aliases or [],
            "notes": row.notes,
        }


def _orm_relationship_to_pydantic(row: ORMRelationship) -> Relationship:
    """Convert an ORM Relationship row to a Pydantic Relationship schema."""
    desc = row.description or ""
    try:
        return Relationship.model_validate_json(desc)
    except Exception:
        rel_id = ""
        label = desc
        parts = desc.split("|", 1)
        if desc.startswith("rid:"):
            rel_id = parts[0][4:]
            label = parts[1] if len(parts) > 1 else ""

        return Relationship(
            relationship_id=rel_id,
            source_entity_id=str(row.source_id),
            target_entity_id=str(row.target_id),
            relation_type=row.relation_type,
            label=label,
            trust=row.confidence,
        )


def _orm_relationship_to_dict(row: ORMRelationship) -> dict[str, Any]:
    """Convert an ORM Relationship row to a plain dict."""
    desc = row.description or ""
    try:
        return Relationship.model_validate_json(desc).model_dump(mode="json")
    except Exception:
        pass
    rel_id = ""
    label = desc
    if desc.startswith("rid:"):
        parts = desc.split("|", 1)
        rel_id = parts[0][4:]
        label = parts[1] if len(parts) > 1 else ""
    return {
        "relationship_id": rel_id,
        "source_id": str(row.source_id),
        "target_id": str(row.target_id),
        "relation_type": row.relation_type,
        "label": label,
        "confidence": row.confidence,
    }


# ---------------------------------------------------------------------------
# CanonStore — JSON-file persistence for StoryKernel (migrated from canon/)
# ---------------------------------------------------------------------------
_SUPPORTED_SCHEMA_VERSION = "2.0"
_canon_log = logging.getLogger("novel_forge.story_kernel.store")


class CanonStore:
    """Persists StoryKernel as JSON files with per-chapter snapshots.

    Migrated from ``novel_forge.canon.store`` so that consumers can import
    from ``novel_forge.story_kernel`` instead of ``novel_forge.canon``.
    """

    def __init__(self, project_dir: Path) -> None:
        self._dir = project_dir / "canon"
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def current_path(self) -> Path:
        return self._dir / "canon_current.json"

    def snapshot_path(self, chapter: int) -> Path:
        return self._dir / f"canon_v{chapter}.json"

    def exists(self) -> bool:
        return self.current_path.exists()

    def load(self) -> StoryKernel:
        raw = self.current_path.read_text(encoding="utf-8")
        self._assert_supported_schema(raw)
        return StoryKernel.model_validate_json(raw)

    def load_snapshot(self, chapter: int) -> StoryKernel:
        path = self.snapshot_path(chapter)
        raw = path.read_text(encoding="utf-8")
        self._assert_supported_schema(raw)
        return StoryKernel.model_validate_json(raw)

    @staticmethod
    def _assert_supported_schema(raw: str) -> None:
        try:
            payload = _json.loads(raw)
        except _json.JSONDecodeError:
            _canon_log.warning("canon_json_malformed | schema check skipped, deferring to Pydantic")
            return
        version = str(payload.get("schema_version", "")).strip()
        if version != _SUPPORTED_SCHEMA_VERSION:
            raise ValueError("当前版本不兼容旧项目，请重新 init-long 或等待迁移工具")

    def save(self, state: StoryKernel, *, snapshot: bool = True) -> None:
        data = state.model_dump_json(indent=2, ensure_ascii=False)
        _atomic_write_text(self.current_path, data)
        if snapshot and state.current_chapter > 0:
            snap = self.snapshot_path(state.current_chapter)
            _atomic_write_text(snap, data)

    def init(self, project_id: str) -> StoryKernel:
        state = StoryKernel(project_id=project_id, current_chapter=0)
        self.save(state, snapshot=False)
        return state

    def list_snapshots(self) -> list[int]:
        nums: list[int] = []
        for f in self._dir.glob("canon_v*.json"):
            try:
                num = int(f.stem.split("_v")[1])
                nums.append(num)
            except (IndexError, ValueError):
                continue
        return sorted(nums)

    def prune_snapshots(
        self,
        *,
        keep_recent: int,
        keep_chapters: set[int] | None = None,
    ) -> list[int]:
        keep_recent = max(0, int(keep_recent))
        keep_chapters = set(keep_chapters or set())
        snapshots = self.list_snapshots()
        if keep_recent:
            keep_chapters.update(snapshots[-keep_recent:])

        deleted: list[int] = []
        for chapter in snapshots:
            if chapter in keep_chapters:
                continue
            try:
                self.snapshot_path(chapter).unlink(missing_ok=True)
            except OSError as exc:
                _canon_log.warning(
                    "canon_snapshot_prune_failed | chapter=%d | error=%s",
                    chapter,
                    exc,
                )
                continue
            deleted.append(chapter)
        return deleted

    def rollback_to(self, target_chapter: int) -> StoryKernel:
        if target_chapter == 0:
            if not self.exists():
                raise ValueError("Canon not initialized")
            current = self.load()
            state = StoryKernel(project_id=current.project_id, current_chapter=0)
            self.save(state, snapshot=False)
            return state

        snap_path = self.snapshot_path(target_chapter)
        if not snap_path.exists():
            available = self.list_snapshots()
            raise ValueError(
                f"Cannot rollback to chapter {target_chapter}: snapshot not found.\n"
                f"Available snapshots: {available}"
            )

        state = self.load_snapshot(target_chapter)
        self.save(state, snapshot=False)
        return state

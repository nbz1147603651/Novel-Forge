"""Character relationship tracker — query and visualize relationship evolution.

Migrated from ``novel_forge.canon.relationship_tracker`` to use
:class:`~novel_forge.story_kernel.store.StoryKernelStore` and
:class:`~novel_forge.story_kernel.schemas.StoryKernel` as the backing state.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.core.domain.character_relationships import iter_character_relationship_projections
from novel_forge.core.domain.guardrails import is_system_artifact_name
from novel_forge.persistence.filesystem import atomic_write_text

_MALFORMED_RELATIONSHIP_ENTITY_KEYS = {
    "character_id",
    "characterid",
    "gender",
    "name",
    "id",
    "性别",
    "角色id",
    "角色_id",
}

# These labels are extraction artifacts rather than stable characters in
# relationship views. A real character named exactly "男" or "女" should be
# disambiguated in the character bible to avoid being hidden here.
_GENERIC_RELATIONSHIP_NAMES = {
    "众人",
    "镇民",
    "村民",
    "百姓",
    "官兵",
    "士兵",
    "侍卫",
    "下人",
    "仆从",
    "黑衣人",
    "群众",
    "路人",
    "男",
    "女",
    "男性",
    "女性",
    "male",
    "female",
}


@dataclass
class RelationshipSnapshot:
    """Snapshot of a relationship at a specific chapter."""

    chapter_number: int
    public_status: str
    trust: float
    tension: float
    dependency: float
    shift_event: str


@dataclass
class RelationshipTimeline:
    """Evolution of a relationship across chapters."""

    pair_id: str
    character_a: str
    character_b: str
    snapshots: list[RelationshipSnapshot] = field(default_factory=list)
    current_status: str = ""
    current_trust: float = 0.5
    current_tension: float = 0.5


@dataclass
class CharacterRelationshipMap:
    """Full relationship map for a character."""

    character_name: str
    relationships: list[RelationshipTimeline] = field(default_factory=list)


@dataclass
class RelationshipOverview:
    """Overview of all tracked relationships in a project."""

    total_relationships: int = 0
    timelines: list[RelationshipTimeline] = field(default_factory=list)
    high_tension_pairs: list[str] = field(default_factory=list)
    recent_shifts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_relationships": self.total_relationships,
            "high_tension_pairs": self.high_tension_pairs,
            "recent_shifts": self.recent_shifts,
            "timelines": [
                {
                    "pair_id": t.pair_id,
                    "character_a": t.character_a,
                    "character_b": t.character_b,
                    "current_status": t.current_status,
                    "current_trust": t.current_trust,
                    "current_tension": t.current_tension,
                    "snapshots": [
                        {
                            "chapter": s.chapter_number,
                            "status": s.public_status,
                            "trust": s.trust,
                            "tension": s.tension,
                            "shift": s.shift_event,
                        }
                        for s in t.snapshots
                    ],
                }
                for t in self.timelines
            ],
        }


def _resolve_entity_name(
    entity_id: str,
    entity_map: dict[str, str],
) -> str:
    """Look up a human-readable entity name from an entity ID."""
    return entity_map.get(entity_id, entity_id)


def _is_dirty_relationship_endpoint(value: Any) -> bool:
    """Return True when an endpoint is an extracted schema key or crowd label."""
    text = str(value or "").strip().strip("“”\"'「」《》")
    if is_system_artifact_name(text):
        return True

    normalized = text.replace("：", ":").strip()
    lowered = normalized.lower()
    if lowered in _GENERIC_RELATIONSHIP_NAMES or normalized in _GENERIC_RELATIONSHIP_NAMES:
        return True

    candidate = lowered
    for prefix in ("char_", "char-", "entity_", "entity-"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break

    if candidate in _GENERIC_RELATIONSHIP_NAMES:
        return True

    if ":" in candidate:
        key = candidate.split(":", maxsplit=1)[0].strip()
        if key in _MALFORMED_RELATIONSHIP_ENTITY_KEYS:
            return True

    return False


def _is_dirty_relationship(rel: Any, char_a: str, char_b: str) -> bool:
    """Return True for malformed persisted relationships that should not render."""
    src_id = getattr(rel, "source_entity_id", "")
    tgt_id = getattr(rel, "target_entity_id", "")
    return any(_is_dirty_relationship_endpoint(value) for value in (src_id, tgt_id, char_a, char_b))


def _build_entity_map(kernel: Any) -> dict[str, str]:
    """Build entity_id → name mapping from a StoryKernel."""
    entity_map: dict[str, str] = {}
    for entity in getattr(kernel, "entities", []) or []:
        eid = getattr(entity, "entity_id", "")
        name = getattr(entity, "name", "")
        if eid:
            entity_map[eid] = name or eid
    return entity_map


def _extract_timelines_from_kernel(
    kernel: Any,
    chapter: int,
    timelines: dict[str, RelationshipTimeline],
    entity_map: dict[str, str],
) -> None:
    """Extract relationship data from a StoryKernel into *timelines*."""
    for rel in getattr(kernel, "relationships", []) or []:
        pair_id = getattr(rel, "relationship_id", "")
        if not pair_id:
            continue
        src_id = getattr(rel, "source_entity_id", "")
        tgt_id = getattr(rel, "target_entity_id", "")
        char_a = _resolve_entity_name(src_id, entity_map)
        char_b = _resolve_entity_name(tgt_id, entity_map)
        if _is_dirty_relationship(rel, char_a, char_b):
            continue

        if pair_id not in timelines:
            timelines[pair_id] = RelationshipTimeline(
                pair_id=pair_id,
                character_a=char_a,
                character_b=char_b,
            )
        timelines[pair_id].snapshots.append(
            RelationshipSnapshot(
                chapter_number=chapter,
                public_status=getattr(rel, "label", "") or getattr(rel, "status", ""),
                trust=getattr(rel, "trust", 0.5),
                tension=getattr(rel, "tension", 0.5),
                dependency=getattr(rel, "dependency", 0.0),
                shift_event=getattr(rel, "shift_summary", ""),
            )
        )


def _fill_current_state(
    kernel: Any,
    timelines: dict[str, RelationshipTimeline],
    entity_map: dict[str, str],
) -> None:
    """Fill current relationship state from the latest kernel into *timelines*."""
    current_rels = getattr(kernel, "relationships", []) or []
    current_by_id: dict[str, Any] = {}
    for rel in current_rels:
        rid = getattr(rel, "relationship_id", "")
        src_id = getattr(rel, "source_entity_id", "")
        tgt_id = getattr(rel, "target_entity_id", "")
        char_a = _resolve_entity_name(src_id, entity_map)
        char_b = _resolve_entity_name(tgt_id, entity_map)
        if rid and not _is_dirty_relationship(rel, char_a, char_b):
            current_by_id[rid] = rel

    for pair_id, rel in current_by_id.items():
        if pair_id not in timelines:
            src_id = getattr(rel, "source_entity_id", "")
            tgt_id = getattr(rel, "target_entity_id", "")
            timelines[pair_id] = RelationshipTimeline(
                pair_id=pair_id,
                character_a=_resolve_entity_name(src_id, entity_map),
                character_b=_resolve_entity_name(tgt_id, entity_map),
            )

    for pair_id, timeline in timelines.items():
        if pair_id in current_by_id:
            cur = current_by_id[pair_id]
            timeline.current_status = getattr(cur, "label", "") or getattr(cur, "status", "")
            timeline.current_trust = getattr(cur, "trust", 0.5)
            timeline.current_tension = getattr(cur, "tension", 0.5)


async def build_relationship_overview(
    project_dir: Path,
    *,
    use_cache: bool = True,
) -> RelationshipOverview:
    """Build a full relationship overview from story-kernel snapshots.

    Scans all chapter-level kernel snapshots to build evolution timelines
    for each tracked relationship pair.
    """
    from novel_forge.story_kernel.store import StoryKernelStore

    db_path = project_dir / "story_kernel.db"
    store = StoryKernelStore(db_path)
    try:
        fingerprint = _relationship_overview_fingerprint(project_dir, store)
        cache_path = _relationship_overview_cache_path(project_dir)
        if use_cache:
            cached = _load_cached_relationship_overview(cache_path, fingerprint)
            if cached is not None:
                return cached

        # Load current kernel
        kernel = await store.load_kernel(project_dir.name)
        entity_map = _build_entity_map(kernel)

        # Build timelines from snapshots
        snapshot_chapters = sorted(store.list_snapshots())
        timelines: dict[str, RelationshipTimeline] = {}

        for ch_num in snapshot_chapters:
            try:
                snapshot = await store.load_snapshot(ch_num)
            except (OSError, ValueError):
                continue
            snap_entity_map = _build_entity_map(snapshot)
            _extract_timelines_from_kernel(snapshot, ch_num, timelines, snap_entity_map)

        # Ensure current relationships are visible even when snapshots are missing.
        _fill_current_state(kernel, timelines, entity_map)

        # Fallback: seed baseline relationships from character_bible when kernel
        # snapshots and current kernel have no relationship entries yet.
        if not timelines:
            _seed_timelines_from_character_bible(project_dir, timelines)

        # Detect high tension pairs
        high_tension = [t.pair_id for t in timelines.values() if t.current_tension >= 0.7]

        # Collect recent shifts
        recent: list[dict[str, Any]] = []
        for t in timelines.values():
            if t.snapshots:
                last = t.snapshots[-1]
                if last.shift_event:
                    recent.append(
                        {
                            "pair_id": t.pair_id,
                            "characters": [t.character_a, t.character_b],
                            "chapter": last.chapter_number,
                            "event": last.shift_event,
                        }
                    )

        timeline_list = sorted(timelines.values(), key=lambda t: t.pair_id)

        overview = RelationshipOverview(
            total_relationships=len(timelines),
            timelines=timeline_list,
            high_tension_pairs=high_tension,
            recent_shifts=recent[-10:],
        )
        if use_cache:
            _save_cached_relationship_overview(cache_path, fingerprint, overview)
        return overview
    finally:
        await store.close()


def _seed_timelines_from_character_bible(
    project_dir: Path,
    timelines: dict[str, RelationshipTimeline],
) -> None:
    """Seed baseline relationship timelines from character_bible.json."""
    bible_path = project_dir / "character_bible.json"
    if not bible_path.exists():
        return
    try:
        payload = json.loads(bible_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    raw_chars = payload.get("characters", [])
    profiles: list[dict[str, Any]] = []
    if isinstance(raw_chars, list):
        profiles = [item for item in raw_chars if isinstance(item, dict)]
    elif isinstance(raw_chars, dict):
        for name, item in raw_chars.items():
            if not isinstance(item, dict):
                continue
            if not item.get("name"):
                item = dict(item)
                item["name"] = str(name)
            profiles.append(item)
    else:
        return

    pair_descriptions: dict[tuple[str, str], list[str]] = {}
    for projection in iter_character_relationship_projections(profiles):
        raw_pair = tuple(projection.pair_key)
        if len(raw_pair) != 2:
            continue
        pair = (str(raw_pair[0]), str(raw_pair[1]))
        if pair not in pair_descriptions:
            pair_descriptions[pair] = []
        if projection.description and projection.description not in pair_descriptions[pair]:
            pair_descriptions[pair].append(projection.description)

    for (char_a, char_b), descriptions in pair_descriptions.items():
        pair_id = f"{char_a}__{char_b}"
        current_status = " / ".join(descriptions[:2]) if descriptions else ""
        timelines[pair_id] = RelationshipTimeline(
            pair_id=pair_id,
            character_a=char_a,
            character_b=char_b,
            current_status=current_status,
            current_trust=0.5,
            current_tension=0.5,
        )


async def get_character_relationships(
    project_dir: Path,
    character_name: str,
) -> CharacterRelationshipMap:
    """Get all relationships for a specific character."""
    overview = await build_relationship_overview(project_dir)
    name_lower = character_name.lower()
    matching = [
        t
        for t in overview.timelines
        if t.character_a.lower() == name_lower or t.character_b.lower() == name_lower
    ]
    return CharacterRelationshipMap(
        character_name=character_name,
        relationships=matching,
    )


def _relationship_overview_cache_path(project_dir: Path) -> Path:
    return project_dir / "story_kernel" / "relationship_overview_cache.json"


def _file_signature(path: Path) -> dict[str, int | str]:
    if not path.exists():
        return {"path": str(path.name), "missing": 1}
    stat = path.stat()
    return {
        "path": str(path.name),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _relationship_overview_fingerprint(
    project_dir: Path,
    store: Any,
) -> dict[str, Any]:
    snapshots = [
        _file_signature(store.snapshot_path(chapter)) for chapter in store.list_snapshots()
    ]
    db_path = (
        Path(store._db_path) if hasattr(store, "_db_path") else project_dir / "story_kernel.db"
    )
    return {
        "schema": 3,
        "current": _file_signature(db_path),
        "current_wal": _file_signature(db_path.with_name(f"{db_path.name}-wal")),
        "snapshots": snapshots,
        "character_bible": _file_signature(project_dir / "character_bible.json"),
    }


def _load_cached_relationship_overview(
    cache_path: Path,
    fingerprint: dict[str, Any],
) -> RelationshipOverview | None:
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("fingerprint") != fingerprint:
        return None
    overview_data = data.get("overview")
    if not isinstance(overview_data, dict):
        return None
    return _relationship_overview_from_dict(overview_data)


def _save_cached_relationship_overview(
    cache_path: Path,
    fingerprint: dict[str, Any],
    overview: RelationshipOverview,
) -> None:
    payload = {
        "fingerprint": fingerprint,
        "overview": overview.to_dict(),
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            cache_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
    except OSError:
        return


def _relationship_overview_from_dict(data: dict[str, Any]) -> RelationshipOverview:
    timelines: list[RelationshipTimeline] = []
    for item in data.get("timelines", []) or []:
        if not isinstance(item, dict):
            continue
        timeline = RelationshipTimeline(
            pair_id=str(item.get("pair_id", "") or ""),
            character_a=str(item.get("character_a", "") or ""),
            character_b=str(item.get("character_b", "") or ""),
            current_status=str(item.get("current_status", "") or ""),
            current_trust=float(item.get("current_trust", 0.5) or 0.5),
            current_tension=float(item.get("current_tension", 0.5) or 0.5),
        )
        for snapshot in item.get("snapshots", []) or []:
            if not isinstance(snapshot, dict):
                continue
            timeline.snapshots.append(
                RelationshipSnapshot(
                    chapter_number=int(snapshot.get("chapter", 0) or 0),
                    public_status=str(snapshot.get("status", "") or ""),
                    trust=float(snapshot.get("trust", 0.5) or 0.5),
                    tension=float(snapshot.get("tension", 0.5) or 0.5),
                    dependency=float(snapshot.get("dependency", 0.0) or 0.0),
                    shift_event=str(snapshot.get("shift", "") or ""),
                )
            )
        timelines.append(timeline)
    return RelationshipOverview(
        total_relationships=int(data.get("total_relationships", len(timelines)) or 0),
        timelines=timelines,
        high_tension_pairs=[
            str(item) for item in data.get("high_tension_pairs", []) or [] if str(item).strip()
        ],
        recent_shifts=[
            item for item in data.get("recent_shifts", []) or [] if isinstance(item, dict)
        ],
    )


# ---------------------------------------------------------------------------
# Sync variant — uses read-only SQLite instead of an async StoryKernelStore
# ---------------------------------------------------------------------------


def build_relationship_overview_sync(
    project_dir: Path,
    *,
    use_cache: bool = True,
) -> RelationshipOverview:
    """Build a relationship overview without creating an async SQLite engine.

    Desktop refreshes call this function from the Qt thread. Reading the
    SQLite-backed StoryKernel with ``aiosqlite`` there creates a worker thread
    for every refresh and can leave callbacks targeting a loop that has been
    torn down after a suspend/resume cycle. Prefer SQLite's read-only
    synchronous driver for this UI read path; retain the JSON CanonStore
    fallback for projects created by older versions.
    """
    db_path = project_dir / "story_kernel.db"
    if db_path.is_file():
        store: Any = _ReadOnlyStoryKernelStore(db_path)
    else:
        store = _LegacyCanonStoreAdapter(project_dir)

    fingerprint = _relationship_overview_fingerprint(project_dir, store)
    cache_path = _relationship_overview_cache_path(project_dir)
    if use_cache:
        cached = _load_cached_relationship_overview(cache_path, fingerprint)
        if cached is not None:
            return cached

    current_state = store.load_kernel(project_dir.name)
    entity_map = _build_entity_map(current_state)
    timelines: dict[str, RelationshipTimeline] = {}

    for ch_num in store.list_snapshots():
        try:
            snapshot = store.load_snapshot(ch_num)
        except (OSError, ValueError, sqlite3.Error):
            continue
        snap_entity_map = _build_entity_map(snapshot)
        _extract_timelines_from_kernel(snapshot, ch_num, timelines, snap_entity_map)

    _fill_current_state(current_state, timelines, entity_map)

    if not timelines:
        _seed_timelines_from_character_bible(project_dir, timelines)

    overview = _relationship_overview_from_timelines(timelines)
    if use_cache:
        _save_cached_relationship_overview(cache_path, fingerprint, overview)
    return overview


class _ReadOnlyStoryKernelStore:
    """Minimal synchronous reader for the SQLite StoryKernel artifact.

    Every connection uses SQLite ``mode=ro`` so relationship cards do not
    create WAL/journal writes that would retrigger the workspace file watcher.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = str(db_path)

    @property
    def _snapshot_dir(self) -> Path:
        return Path(self._db_path).parent / "snapshots"

    def snapshot_path(self, chapter: int) -> Path:
        return self._snapshot_dir / f"kernel_v{chapter}.db"

    def list_snapshots(self) -> list[int]:
        if not self._snapshot_dir.exists():
            return []
        numbers: list[int] = []
        for path in self._snapshot_dir.glob("kernel_v*.db"):
            try:
                numbers.append(int(path.stem.split("_v", 1)[1]))
            except (IndexError, ValueError):
                continue
        return sorted(numbers)

    def load_kernel(self, project_id: str) -> Any:
        return self._load_kernel_json(Path(self._db_path), project_id=project_id)

    def load_snapshot(self, chapter: int) -> Any:
        return self._load_kernel_json(self.snapshot_path(chapter))

    @staticmethod
    def _load_kernel_json(path: Path, *, project_id: str | None = None) -> Any:
        from novel_forge.story_kernel.schemas import StoryKernel

        if not path.is_file():
            raise ValueError(f"StoryKernel database not found: {path}")
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.5) as connection:
            connection.execute("PRAGMA query_only=ON")
            if project_id is None:
                row = connection.execute("SELECT kernel_json FROM kernel_meta LIMIT 1").fetchone()
            else:
                row = connection.execute(
                    "SELECT kernel_json FROM kernel_meta WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
        if row is None:
            target = project_id or path.name
            raise ValueError(f"Kernel for project '{target}' not found.")
        return StoryKernel.model_validate_json(row[0])


class _LegacyCanonStoreAdapter:
    """Expose the old JSON CanonStore through the SQLite reader's small API."""

    def __init__(self, project_dir: Path) -> None:
        from novel_forge.story_kernel.store import CanonStore

        self._store = CanonStore(project_dir)
        self._db_path = str(project_dir / "canon" / "canon_current.json")

    def snapshot_path(self, chapter: int) -> Path:
        return self._store.snapshot_path(chapter)

    def list_snapshots(self) -> list[int]:
        return self._store.list_snapshots()

    def load_kernel(self, _project_id: str) -> Any:
        return self._store.load()

    def load_snapshot(self, chapter: int) -> Any:
        return self._store.load_snapshot(chapter)


def _relationship_overview_from_timelines(
    timelines: dict[str, RelationshipTimeline],
) -> RelationshipOverview:
    """Build the serializable view model shared by synchronous readers."""
    high_tension = [
        timeline.pair_id for timeline in timelines.values() if timeline.current_tension >= 0.7
    ]
    recent: list[dict[str, Any]] = []
    for timeline in timelines.values():
        if not timeline.snapshots:
            continue
        latest = timeline.snapshots[-1]
        if latest.shift_event:
            recent.append(
                {
                    "pair_id": timeline.pair_id,
                    "characters": [timeline.character_a, timeline.character_b],
                    "chapter": latest.chapter_number,
                    "event": latest.shift_event,
                }
            )
    return RelationshipOverview(
        total_relationships=len(timelines),
        timelines=sorted(timelines.values(), key=lambda timeline: timeline.pair_id),
        high_tension_pairs=high_tension,
        recent_shifts=recent[-10:],
    )

"""Humanize pattern library — SQLite + FTS5 + optional Zvec vector store.

Provides persistent CRUD, cross-platform process locking, schema migration,
embedding-signature tracking, and resumable vector rebuilds for the humanize
pattern library.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterator,
    Literal,
    Protocol,
    Sequence,
    runtime_checkable,
)

from novel_forge.core.schemas.humanize_library import (
    LIBRARY_BUILTIN_ENTRIES,
    LIBRARY_SCHEMA_VERSION,
    HumanizeLibraryEntry,
)
from novel_forge.memory.retrieval import bm25_scores
from novel_forge.obs.project_logger import get_project_logger

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safe event emission helper
# ---------------------------------------------------------------------------


def _safe_log_event(event_name: str, data: dict[str, object] | None = None) -> None:
    """Emit a structured event to the project logger, if available.

    Never raises — on failure, writes a warning to stderr.
    """
    try:
        logger_ = get_project_logger()
        if logger_ is not None:
            logger_.log_event(event_name, data, level="INFO")
    except Exception:
        import sys

        print(f"WARNING: Failed to emit event {event_name!r}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LibraryError(Exception):
    """Base exception for humanize library operations."""


class LibraryUnavailableError(LibraryError):
    """Raised when the library database cannot be opened."""


class LibraryDegradedError(LibraryError):
    """Raised when a subsystem (e.g. Zvec) has failed but the library is usable."""


class LibraryReadOnlyError(LibraryError):
    """Raised when attempting to mutate a builtin entry."""


class LibraryDuplicateError(LibraryError):
    """Raised when adding an entry whose pattern_id already exists."""


class LibraryLockTimeoutError(LibraryError):
    """Raised when the library file lock cannot be acquired in time."""


class LibrarySchemaVersionMismatchError(LibraryError):
    """Raised when the on-disk schema version is newer than the code supports."""


# ---------------------------------------------------------------------------
# EmbeddingSignature
# ---------------------------------------------------------------------------


class EmbeddingSignature:
    """Compute and compare embedding fingerprints.

    The signature is ``sha256(f"{provider}:{model}:{dimension}")`` which
    uniquely identifies an embedding model + dimensionality combination.
    """

    @staticmethod
    def compute(provider: str, model: str, dimension: int) -> str:
        """Return a stable hex-digest fingerprint."""
        raw = f"{provider}:{model}:{dimension}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def parse(sig: str) -> tuple[str, str, str]:
        """Parse is not reversible (hash), but we can validate format."""
        if len(sig) != 64:
            raise ValueError(f"Invalid embedding signature length: {len(sig)}")
        try:
            int(sig, 16)
        except ValueError:
            raise ValueError(f"Invalid hex in embedding signature: {sig!r}") from None
        return (sig, "", "")

    @staticmethod
    def equals(a: str | None, b: str | None) -> bool:
        """Compare two signatures for equality (None-safe)."""
        if a is None or b is None:
            return a is None and b is None
        return a == b


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryStats:
    """Snapshot statistics of the humanize library."""

    total: int = 0
    enabled: int = 0
    regex_count: int = 0
    llm_only_count: int = 0
    user_count: int = 0
    imported_count: int = 0
    stale_vector_count: int = 0
    last_updated_at: str | None = None


@dataclass
class ImportReport:
    """Result of importing an archive."""

    imported: int = 0
    skipped: int = 0
    overwritten: int = 0
    merged: int = 0
    vector_stale_count: int = 0
    schema_upgraded_to: str = ""


@dataclass
class MigrationReport:
    """Result of running schema migrations."""

    from_version: str = ""
    to_version: str = ""
    steps_applied: list[str] = field(default_factory=list)
    success: bool = True
    error: str = ""


MergeStrategy = Literal["skip", "overwrite", "merge"]

# ---------------------------------------------------------------------------
# Embedder protocol for rebuild_vectors
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Minimal protocol for the embedder used by ``rebuild_vectors``."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_CREATE_ENTRIES_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    pattern_id TEXT PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'medium',
    example_phrases TEXT NOT NULL DEFAULT '[]',
    example_template TEXT NOT NULL DEFAULT '',
    detection_method TEXT NOT NULL DEFAULT 'regex',
    detection_config TEXT NOT NULL DEFAULT '{}',
    keywords TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'user',
    project_id TEXT,
    notes TEXT NOT NULL DEFAULT '',
    hit_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_hit_chapter INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    embedding_signature TEXT,
    vector_stale INTEGER NOT NULL DEFAULT 0,
    schema_version TEXT NOT NULL DEFAULT '2.0',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

_CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    pattern_id,
    pattern_name,
    category,
    keywords,
    notes,
    content='',
    tokenize='unicode61'
);
"""

_CREATE_META_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_ENTRY_COLUMNS: list[str] = [
    "pattern_id",
    "pattern_name",
    "category",
    "severity",
    "example_phrases",
    "example_template",
    "detection_method",
    "detection_config",
    "keywords",
    "source",
    "project_id",
    "notes",
    "hit_count",
    "first_seen_at",
    "last_seen_at",
    "last_hit_chapter",
    "enabled",
    "embedding_signature",
    "vector_stale",
    "schema_version",
    "created_at",
]


def _entry_to_row(entry: HumanizeLibraryEntry) -> tuple[Any, ...]:
    """Convert a HumanizeLibraryEntry to a SQLite row tuple."""
    return (
        entry.pattern_id,
        entry.pattern_name,
        entry.category,
        entry.severity,
        json.dumps(entry.example_phrases, ensure_ascii=False),
        entry.example_template,
        entry.detection_method,
        json.dumps(entry.detection_config, ensure_ascii=False),
        json.dumps(entry.keywords, ensure_ascii=False),
        entry.source,
        entry.project_id,
        entry.notes,
        entry.hit_count,
        entry.first_seen_at.isoformat() if entry.first_seen_at else None,
        entry.last_seen_at.isoformat() if entry.last_seen_at else None,
        entry.last_hit_chapter,
        1 if entry.enabled else 0,
        entry.embedding_signature,
        1 if entry.vector_stale else 0,
        entry.schema_version,
        entry.created_at.isoformat() if entry.created_at else "",
    )


def _row_to_entry(row: sqlite3.Row) -> HumanizeLibraryEntry:
    """Convert a SQLite row to a HumanizeLibraryEntry."""
    d = dict(row)
    first_seen = d.get("first_seen_at")
    last_seen = d.get("last_seen_at")
    created_at = d.get("created_at") or ""
    return HumanizeLibraryEntry(
        pattern_id=d["pattern_id"],
        pattern_name=d["pattern_name"],
        category=d.get("category", ""),
        severity=d.get("severity", "medium"),
        example_phrases=json.loads(d.get("example_phrases") or "[]"),
        example_template=d.get("example_template", ""),
        detection_method=d.get("detection_method", "regex"),
        detection_config=json.loads(d.get("detection_config") or "{}"),
        keywords=json.loads(d.get("keywords") or "[]"),
        source=d.get("source", "user"),
        project_id=d.get("project_id"),
        notes=d.get("notes", ""),
        hit_count=d.get("hit_count", 0),
        first_seen_at=datetime.fromisoformat(first_seen) if first_seen else None,
        last_seen_at=datetime.fromisoformat(last_seen) if last_seen else None,
        last_hit_chapter=d.get("last_hit_chapter"),
        enabled=bool(d.get("enabled", 1)),
        embedding_signature=d.get("embedding_signature"),
        vector_stale=bool(d.get("vector_stale", 0)),
        schema_version=d.get("schema_version", LIBRARY_SCHEMA_VERSION),
        created_at=datetime.fromisoformat(created_at) if created_at else datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------

_MIGRATIONS: dict[str, Callable[[sqlite3.Connection], None]] = {}
# Currently empty since v2.0 is the initial version.
# Future migrations would be registered as:
# _MIGRATIONS["2.1"] = _migrate_2_0_to_2_1


# ---------------------------------------------------------------------------
# Cross-platform file-lock helpers
# ---------------------------------------------------------------------------


def _lock_file_fd(fd: int, exclusive: bool = True, timeout: float = 30.0) -> None:
    """Acquire an advisory file lock with a bounded wait."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if sys.platform == "win32":
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK
                msvcrt.locking(fd, mode, 1)
            else:
                lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(fd, lock_type | fcntl.LOCK_NB)
            return
        except (OSError, BlockingIOError):
            if time.monotonic() >= deadline:
                raise LibraryLockTimeoutError(
                    f"Could not acquire library lock within {timeout}s"
                ) from None
            time.sleep(0.05)


def _unlock_file_fd(fd: int) -> None:
    """Release a lock acquired by :func:`_lock_file_fd`."""
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# HumanizeLibrary
# ---------------------------------------------------------------------------


class HumanizeLibrary:
    """SQLite-backed humanize pattern library with FTS5 and optional Zvec."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        in_memory: bool = False,
        lock_timeout: float = 30.0,
    ) -> None:
        self._in_memory = in_memory
        self._lock_timeout = lock_timeout
        self._closed = False

        if in_memory:
            self._db_path = None
            self._lock_path = None
            self._conn = sqlite3.connect(":memory:")
        else:
            assert db_path is not None, "db_path required for on-disk library"
            self._db_path = Path(db_path)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_path = self._db_path.parent / "humanize_library.lock"
            self._conn = sqlite3.connect(str(self._db_path))

        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=10000")
        for _attempt in range(3):
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError:
                time.sleep(0.1)
        self._init_schema()
        self.run_migrations()

        # Zvec (optional) — gracefully degrade
        self._zvec_store: Any = None
        self._zvec_available = False

    # -- Factories -----------------------------------------------------------

    @classmethod
    def from_default_path(cls) -> HumanizeLibrary:
        """Create a library from the configured default path."""
        from novel_forge.core.config import Settings

        settings = Settings()
        path = settings.humanize_library_resolved_path / "library.db"
        return cls.from_path(path)

    @classmethod
    def from_language(cls, language: str = "zh") -> HumanizeLibrary:
        """Create a library from the language-appropriate database.

        When *language* indicates English (en, en-us, en-gb, english), loads
        ``humanize_library_en.db`` from the same directory as the default
        library. Falls back to the default ``library.db`` if the English DB
        does not exist.
        """
        from novel_forge.core.config import Settings

        settings = Settings()
        base_dir = settings.humanize_library_resolved_path
        key = str(language or "zh").strip().lower().replace("_", "-")
        is_en = key in {"en", "en-us", "en-gb", "english"} or key.startswith("en")

        if is_en:
            en_path = base_dir / "humanize_library_en.db"
            if en_path.exists():
                return cls.from_path(en_path)
            # Fall back to default if English DB is missing
            _safe_log_event(
                "humanize_library.en_fallback",
                {"language": language, "reason": "en_db_missing", "fallback_path": str(base_dir / "library.db")},
            )

        return cls.from_default_path()

    @classmethod
    def from_path(cls, path: Path | str) -> HumanizeLibrary:
        """Create a library from an explicit database file path."""
        p = Path(path)
        if p.is_dir():
            p = p / "library.db"
        try:
            lib = cls(db_path=p)
        except Exception as exc:
            _safe_log_event(
                "humanize_library.unavailable",
                {
                    "path": str(p),
                    "error_type": type(exc).__name__,
                    "error_msg": str(exc),
                },
            )
            raise
        stats = lib.stats()
        _safe_log_event(
            "humanize_library.initialized",
            {
                "path": str(p),
                "total_entries": stats.total,
                "regex_count": stats.regex_count,
                "llm_only_count": stats.llm_only_count,
                "schema_version": LIBRARY_SCHEMA_VERSION,
            },
        )
        return lib

    @classmethod
    def in_memory(cls) -> HumanizeLibrary:
        """Create an in-memory library (no persistence, no Zvec)."""
        lib = cls(in_memory=True)
        _safe_log_event(
            "humanize_library.initialized",
            {
                "path": ":memory:",
                "total_entries": 0,
                "regex_count": 0,
                "llm_only_count": 0,
                "schema_version": LIBRARY_SCHEMA_VERSION,
            },
        )
        return lib

    # -- Context manager -----------------------------------------------------

    def __enter__(self) -> HumanizeLibrary:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        if not self._closed:
            self._conn.close()
            self._closed = True

    # -- Schema init ---------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        cur = self._conn.cursor()
        cur.execute(_CREATE_ENTRIES_SQL)
        cur.execute(_CREATE_FTS_SQL)
        cur.execute(_CREATE_META_SQL)
        # Set schema version if not present
        cur.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", LIBRARY_SCHEMA_VERSION),
        )
        cur.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("created_at", datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    # -- Migration -----------------------------------------------------------

    def run_migrations(self) -> MigrationReport:
        """Run pending schema migrations."""
        report = MigrationReport(to_version=LIBRARY_SCHEMA_VERSION)
        cur = self._conn.cursor()
        cur.execute("SELECT value FROM meta WHERE key = ?", ("schema_version",))
        row = cur.fetchone()
        if row is None:
            # Fresh database — already initialized with current version
            report.from_version = LIBRARY_SCHEMA_VERSION
            return report

        current_version = row[0]
        report.from_version = current_version

        if current_version == LIBRARY_SCHEMA_VERSION:
            return report

        # Compare versions (simple string comparison for semver-like strings)
        if _version_gt(current_version, LIBRARY_SCHEMA_VERSION):
            raise LibrarySchemaVersionMismatchError(
                f"Library schema version {current_version} is newer than "
                f"code version {LIBRARY_SCHEMA_VERSION}. Upgrade the code."
            )

        # Chain upgrades
        version_chain = _build_version_chain(current_version, LIBRARY_SCHEMA_VERSION)
        for target_version in version_chain:
            migrator = _MIGRATIONS.get(target_version)
            if migrator is not None:
                migrator(self._conn)
                report.steps_applied.append(target_version)
            # Update version
            cur.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", target_version),
            )
            self._conn.commit()

        # Ensure version is current even if no intermediate migrations exist
        cur.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", LIBRARY_SCHEMA_VERSION),
        )
        self._conn.commit()

        report.to_version = LIBRARY_SCHEMA_VERSION
        if report.steps_applied:
            entries = self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            _safe_log_event(
                "humanize_library.migration_applied",
                {
                    "from_version": report.from_version,
                    "to_version": report.to_version,
                    "entries_migrated": entries,
                },
            )
        return report

    # -- Lock ----------------------------------------------------------------

    @contextmanager
    def library_lock(self, operation: str = "write") -> Iterator[None]:
        """Acquire the cross-platform file lock for write operations."""
        if self._in_memory or self._lock_path is None:
            yield
            return

        lock_path = self._lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle: Any = None
        try:
            t0 = time.monotonic()
            handle = open(lock_path, "a+b")  # noqa: SIM115
            _lock_file_fd(handle.fileno(), exclusive=True, timeout=self._lock_timeout)
            duration_ms = (time.monotonic() - t0) * 1000
            _safe_log_event(
                "humanize_library.lock_acquired",
                {
                    "operation": operation,
                    "duration_ms": round(duration_ms, 1),
                },
            )
            yield
        except LibraryLockTimeoutError:
            _safe_log_event(
                "humanize_library.lock_timeout",
                {
                    "operation": operation,
                    "waited_ms": round(self._lock_timeout * 1000),
                },
            )
            raise
        finally:
            if handle is not None:
                try:
                    _unlock_file_fd(handle.fileno())
                except Exception:
                    pass
                handle.close()

    # -- CRUD ----------------------------------------------------------------

    def list_all(self) -> list[HumanizeLibraryEntry]:
        """Return all entries."""
        cur = self._conn.execute(
            f"SELECT {', '.join(_ENTRY_COLUMNS)} FROM entries ORDER BY pattern_id"
        )
        return [_row_to_entry(row) for row in cur.fetchall()]

    def get(self, pattern_id: str) -> HumanizeLibraryEntry | None:
        """Get a single entry by pattern_id, or None."""
        cur = self._conn.execute(
            f"SELECT {', '.join(_ENTRY_COLUMNS)} FROM entries WHERE pattern_id = ?",
            (pattern_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    def add(self, entry: HumanizeLibraryEntry) -> None:
        """Add a new entry. Raises LibraryDuplicateError if pattern_id exists."""
        with self.library_lock("add"):
            existing = self.get(entry.pattern_id)
            if existing is not None:
                raise LibraryDuplicateError(
                    f"Entry with pattern_id={entry.pattern_id!r} already exists"
                )
            self._insert_entry(entry)
            self._sync_fts_insert(entry)
            self._conn.commit()
        _safe_log_event(
            "humanize_library.entry_added",
            {
                "pattern_id": entry.pattern_id,
                "source": entry.source,
                "project_id": entry.project_id,
            },
        )

    def update(self, pattern_id: str, **fields: Any) -> HumanizeLibraryEntry:
        """Update fields on an entry. Returns the updated entry.

        Raises LibraryReadOnlyError for builtin entries.
        """
        with self.library_lock("update"):
            existing = self.get(pattern_id)
            if existing is None:
                raise LibraryError(f"Entry {pattern_id!r} not found")
            if existing.source == "builtin":
                raise LibraryReadOnlyError(f"Cannot update builtin entry {pattern_id!r}")
            vector_source_fields = {
                "pattern_name",
                "category",
                "example_phrases",
                "example_template",
                "detection_method",
                "detection_config",
                "keywords",
                "notes",
            }
            updates = dict(fields)
            if vector_source_fields.intersection(updates):
                updates["embedding_signature"] = None
                updates["vector_stale"] = True
            updated = existing.model_copy(update=updates)
            self._update_entry(updated)
            self._sync_fts_update(updated)
            self._conn.commit()
            return updated

    def remove(self, pattern_id: str) -> bool:
        """Remove an entry. Returns True if removed.

        Raises LibraryReadOnlyError for builtin entries.
        """
        with self.library_lock("remove"):
            existing = self.get(pattern_id)
            if existing is None:
                return False
            if existing.source == "builtin":
                raise LibraryReadOnlyError(f"Cannot remove builtin entry {pattern_id!r}")
            self._conn.execute("DELETE FROM entries WHERE pattern_id = ?", (pattern_id,))
            self._conn.execute("DELETE FROM entries_fts WHERE pattern_id = ?", (pattern_id,))
            self._conn.commit()
            # Also remove from Zvec if available
            if self._zvec_available and self._zvec_store is not None:
                try:
                    self._zvec_store.remove(pattern_id)
                except Exception:
                    logger.debug("zvec_remove_failed | id=%s", pattern_id)
            return True

    def enable(self, pattern_id: str) -> None:
        """Enable an entry."""
        self._set_enabled(pattern_id, True)

    def disable(self, pattern_id: str) -> None:
        """Disable an entry."""
        self._set_enabled(pattern_id, False)

    def _set_enabled(self, pattern_id: str, enabled: bool) -> None:
        with self.library_lock("enable/disable"):
            existing = self.get(pattern_id)
            if existing is None:
                raise LibraryError(f"Entry {pattern_id!r} not found")
            updated = existing.model_copy(update={"enabled": enabled})
            self._update_entry(updated)
            self._conn.commit()

    # -- Stats / hit tracking ------------------------------------------------

    def bump_hit(
        self,
        pattern_id: str,
        chapter: int,
        score: float,
        source: str = "",
    ) -> None:
        """Increment hit_count and update last-seen metadata."""
        with self.library_lock("bump_hit"):
            existing = self.get(pattern_id)
            if existing is None:
                return
            now = datetime.now(timezone.utc)
            updates: dict[str, Any] = {
                "hit_count": existing.hit_count + 1,
                "last_hit_chapter": chapter,
                "last_seen_at": now,
            }
            if existing.first_seen_at is None:
                updates["first_seen_at"] = now
            updated = existing.model_copy(update=updates)
            self._update_entry(updated)
            self._conn.commit()

    def bump_hits_from_report(self, report: Any, chapter: int) -> int:
        """Bulk bump hits from a detection report.

        Expects *report* to have a ``hits`` attribute that is a list of dicts
        or objects with ``pattern_id`` and optionally ``score`` keys/attrs.
        Returns the count of entries bumped.
        """
        hits: Sequence[Any] = getattr(report, "hits", [])
        count = 0
        for hit in hits:
            if isinstance(hit, dict):
                pid = hit.get("pattern_id")
                score = hit.get("score", 0.0)
            else:
                pid = getattr(hit, "pattern_id", None)
                score = getattr(hit, "score", 0.0)
            if pid:
                self.bump_hit(pid, chapter, score)
                count += 1
        if count > 0:
            _safe_log_event(
                "humanize_library.entry_bumped",
                {
                    "chapter": chapter,
                    "count": count,
                    "pattern_ids": count,
                },
            )
        return count

    def stats(self) -> LibraryStats:
        """Return library statistics."""
        all_entries = self.list_all()
        total = len(all_entries)
        enabled = sum(1 for e in all_entries if e.enabled)
        regex_count = sum(1 for e in all_entries if e.detection_method == "regex")
        llm_only_count = sum(1 for e in all_entries if e.detection_method == "llm_only")
        user_count = sum(1 for e in all_entries if e.source == "user")
        imported_count = sum(1 for e in all_entries if e.source == "imported")
        stale_vector_count = sum(1 for e in all_entries if e.vector_stale)
        last_updated: str | None = None
        for e in all_entries:
            ts = e.last_seen_at or e.created_at
            if ts:
                iso = ts.isoformat()
                if last_updated is None or iso > last_updated:
                    last_updated = iso
        return LibraryStats(
            total=total,
            enabled=enabled,
            regex_count=regex_count,
            llm_only_count=llm_only_count,
            user_count=user_count,
            imported_count=imported_count,
            stale_vector_count=stale_vector_count,
            last_updated_at=last_updated,
        )

    def is_healthy(self) -> bool:
        """Check if the library is in a healthy state."""
        try:
            self._conn.execute("SELECT COUNT(*) FROM entries")
            return True
        except Exception:
            return False

    # -- Duplicate detection -------------------------------------------------

    def find_duplicates(
        self, threshold: float = 0.85
    ) -> list[tuple[HumanizeLibraryEntry, HumanizeLibraryEntry, float]]:
        """Find pairs of entries with high similarity.

        Uses BM25 on concatenated text + cosine on embeddings (if available).
        Returns list of (entry_a, entry_b, similarity_score).
        """
        all_entries = self.list_all()
        if len(all_entries) < 2:
            return []

        # Build text documents for BM25
        docs = [_entry_text(e) for e in all_entries]
        results: list[tuple[HumanizeLibraryEntry, HumanizeLibraryEntry, float]] = []
        seen: set[tuple[str, str]] = set()

        for i, entry_a in enumerate(all_entries):
            text_a = docs[i]
            for j in range(i + 1, len(all_entries)):
                entry_b = all_entries[j]
                pair_key = (entry_a.pattern_id, entry_b.pattern_id)
                if pair_key in seen:
                    continue

                # BM25-based similarity (approximate via cross-scoring)
                score_a = bm25_scores(text_a, [docs[j]])[0] if text_a else 0.0
                score_b = bm25_scores(docs[j], [text_a])[0] if docs[j] else 0.0
                bm25_sim = min((score_a + score_b) / 2.0, 10.0) / 10.0

                if bm25_sim >= threshold:
                    seen.add(pair_key)
                    results.append((entry_a, entry_b, bm25_sim))

        results.sort(key=lambda x: x[2], reverse=True)
        return results

    # -- Export / Import -----------------------------------------------------

    def export(self) -> bytes:
        """Export the library as a tar.gz archive."""
        with self.library_lock("export"):
            all_entries = self.list_all()
            data = json.dumps(
                {
                    "schema_version": LIBRARY_SCHEMA_VERSION,
                    "entries": [e.model_dump(mode="json") for e in all_entries],
                },
                ensure_ascii=False,
                indent=2,
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                json_path = Path(tmpdir) / "library.json"
                json_path.write_text(data, encoding="utf-8")
                tar_path = Path(tmpdir) / "library.tar.gz"
                with tarfile.open(str(tar_path), "w:gz") as tar:
                    tar.add(str(json_path), arcname="library.json")
                return tar_path.read_bytes()

    def import_archive(
        self,
        blob: bytes,
        merge_strategy: MergeStrategy = "skip",
    ) -> ImportReport:
        """Import entries from a tar.gz archive."""
        report = ImportReport(schema_upgraded_to=LIBRARY_SCHEMA_VERSION)
        with self.library_lock("import_archive"):
            with tempfile.TemporaryDirectory() as tmpdir:
                tar_path = Path(tmpdir) / "import.tar.gz"
                tar_path.write_bytes(blob)
                with tarfile.open(str(tar_path), "r:gz") as tar:
                    try:
                        tar.extractall(tmpdir, filter="data")
                    except TypeError:  # pragma: no cover - Python <3.12 compatibility.
                        tar.extractall(tmpdir)
                json_path = Path(tmpdir) / "library.json"
                if not json_path.exists():
                    raise LibraryError("Archive does not contain library.json")
                data = json.loads(json_path.read_text(encoding="utf-8"))

            entries_data = data.get("entries", [])
            for ed in entries_data:
                try:
                    entry = HumanizeLibraryEntry.model_validate(ed)
                except Exception:
                    report.skipped += 1
                    continue

                existing = self.get(entry.pattern_id)
                if existing is not None:
                    if merge_strategy == "skip":
                        report.skipped += 1
                    elif merge_strategy == "overwrite":
                        if existing.source == "builtin":
                            report.skipped += 1
                            continue
                        entry = entry.model_copy(
                            update={"hit_count": max(existing.hit_count, entry.hit_count)}
                        )
                        self._update_entry(entry)
                        self._sync_fts_update(entry)
                        report.overwritten += 1
                    elif merge_strategy == "merge":
                        merged_hit = existing.hit_count + entry.hit_count
                        entry = entry.model_copy(update={"hit_count": merged_hit})
                        self._update_entry(entry)
                        self._sync_fts_update(entry)
                        report.merged += 1
                else:
                    self._insert_entry(entry)
                    self._sync_fts_insert(entry)
                    report.imported += 1

                if entry.vector_stale:
                    report.vector_stale_count += 1

            self._conn.commit()
        return report

    # -- Vector management ---------------------------------------------------

    def vec_search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        query_text: str = "",
    ) -> list[tuple[str, float]]:
        """Search by vector similarity. Returns [(pattern_id, score)]."""
        if not self._zvec_available or self._zvec_store is None:
            return []
        try:
            hybrid_search = getattr(self._zvec_store, "hybrid_search", None)
            if query_text.strip() and callable(hybrid_search):
                results = hybrid_search(query_vec, query_text, top_k=top_k)
            else:
                results = self._zvec_store.search(query_vec, top_k=top_k)
            return [(r[0], r[1]) for r in results]
        except Exception:
            _safe_log_event(
                "humanize_library.degraded",
                {
                    "path": str(self._db_path) if self._db_path else ":memory:",
                    "reason": "zvec_search_failed",
                    "fallback_mode": "fts5_only",
                },
            )
            logger.debug("vec_search_failed", exc_info=True)
            return []

    def rebuild_vectors(
        self,
        embedder: EmbedderProtocol,
        *,
        resumable: bool = True,
        batch_size: int = 32,
        embedding_signature: str | None = None,
    ) -> dict[str, int]:
        """Rebuild embedding vectors for all entries.

        If *resumable* is True, reads checkpoint from ``rebuild_progress.json``
        and skips already-processed entries.

        Returns a dict with counts: ``embedded``, ``skipped``, ``failed``.
        """
        result: dict[str, int] = {"embedded": 0, "skipped": 0, "failed": 0}

        checkpoint_path: Path | None = None
        completed_ids: set[str] = set()
        if resumable and not self._in_memory and self._db_path is not None:
            checkpoint_path = self._db_path.parent / "rebuild_progress.json"
            if checkpoint_path.exists():
                try:
                    cp = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    completed_ids = set(cp.get("completed_ids", []))
                except Exception:
                    completed_ids = set()

        all_entries = self.list_all()
        to_embed: list[HumanizeLibraryEntry] = []
        for entry in all_entries:
            if entry.pattern_id in completed_ids:
                result["skipped"] += 1
                continue
            to_embed.append(entry)

        if not to_embed:
            return result

        with self.library_lock("rebuild_vectors"):
            for i in range(0, len(to_embed), batch_size):
                batch = to_embed[i : i + batch_size]
                texts = [_entry_text(e) for e in batch]
                try:
                    vecs = embedder.embed(texts)
                except Exception:
                    logger.warning("embed_batch_failed | batch_start=%d", i, exc_info=True)
                    for entry in batch:
                        result["failed"] += 1
                        self._mark_stale(entry.pattern_id)
                    self._conn.commit()
                    continue

                if len(vecs) != len(batch) or any(not vec for vec in vecs):
                    logger.warning(
                        "embed_batch_incomplete | batch_start=%d | expected=%d | actual=%d",
                        i,
                        len(batch),
                        len(vecs),
                    )
                    for entry in batch:
                        result["failed"] += 1
                        self._mark_stale(entry.pattern_id)
                    self._conn.commit()
                    continue

                for entry, vec in zip(batch, vecs, strict=True):
                    try:
                        sig = embedding_signature or EmbeddingSignature.compute(
                            "custom", "rebuild", len(vec)
                        )
                        updated = entry.model_copy(
                            update={"embedding_signature": sig, "vector_stale": False}
                        )
                        self._update_entry(updated)
                        # Add to Zvec if available
                        if self._zvec_available and self._zvec_store is not None:
                            try:
                                self._zvec_store.add(
                                    entry.pattern_id,
                                    vec,
                                    {
                                        "content": _entry_text(entry),
                                        "pattern_id": entry.pattern_id,
                                        "category": entry.category,
                                    },
                                )
                            except Exception:
                                logger.debug("zvec_add_failed | id=%s", entry.pattern_id)
                        result["embedded"] += 1
                        completed_ids.add(entry.pattern_id)
                    except Exception:
                        result["failed"] += 1
                        self._mark_stale(entry.pattern_id)

                self._conn.commit()

                # Checkpoint
                if checkpoint_path is not None:
                    try:
                        checkpoint_path.write_text(
                            json.dumps(
                                {
                                    "completed_ids": sorted(completed_ids),
                                    "total": len(all_entries),
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                    except Exception:
                        logger.debug("checkpoint_write_failed", exc_info=True)
                _safe_log_event(
                    "humanize_library.rebuild_progress",
                    {
                        "processed": len(completed_ids),
                        "total": len(all_entries),
                        "signature_mismatched": 0,
                        "checkpoint_path": str(checkpoint_path) if checkpoint_path else "",
                    },
                )

            # Clean up checkpoint on full completion
            if checkpoint_path is not None and result["failed"] == 0:
                try:
                    checkpoint_path.unlink(missing_ok=True)
                except Exception:
                    pass

        return result

    def _mark_stale(self, pattern_id: str) -> None:
        """Mark an entry's vector as stale."""
        existing = self.get(pattern_id)
        if existing is not None:
            updated = existing.model_copy(update={"vector_stale": True})
            self._update_entry(updated)

    # -- Zvec setup (optional) -----------------------------------------------

    def setup_zvec(self, zvec_store: Any) -> None:
        """Attach a Zvec vector store for vector search."""
        self._zvec_store = zvec_store
        self._zvec_available = True

    @property
    def vector_available(self) -> bool:
        """Whether a usable vector index is attached to this library instance."""

        return bool(self._zvec_available and self._zvec_store is not None)

    def ensure_vector_index(
        self,
        embedder: EmbedderProtocol,
        *,
        provider: str,
        model: str,
        index_type: str = "hnsw",
        memory_limit_mb: int = 512,
    ) -> bool:
        """Attach and, when necessary, rebuild the small humanize Zvec index.

        The persisted metadata makes this cheap on later chapters.  A provider
        or model change, a new/stale library entry, or a missing collection
        triggers a complete rebuild; the library is intentionally small enough
        that a full rebuild is safer than a partially mixed embedding space.
        """

        if self._in_memory or self._db_path is None:
            return False
        entries = self.list_all()
        searchable_entries = [entry for entry in entries if _entry_text(entry).strip()]
        if not searchable_entries:
            return False

        index_path = self._db_path.parent / "zvec_patterns"
        meta_path = self._db_path.parent / "zvec_meta.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            except Exception:
                meta = {}

        normalized_provider = str(provider or "unknown").strip().lower()
        normalized_model = str(model or "unknown").strip().lower()
        dimension = int(meta.get("dimension", 0) or 0)
        same_embedding_space = (
            dimension > 0
            and str(meta.get("provider", "")).strip().lower() == normalized_provider
            and str(meta.get("model", "")).strip().lower() == normalized_model
        )
        index_existed = index_path.exists()
        if not same_embedding_space:
            try:
                probe = embedder.embed([_entry_text(searchable_entries[0])])
            except Exception:
                probe = []
            if not probe or not probe[0]:
                return False
            dimension = len(probe[0])

        signature = EmbeddingSignature.compute(
            normalized_provider,
            normalized_model,
            dimension,
        )
        needs_rebuild = (
            not index_existed
            or not same_embedding_space
            or any(
                entry.vector_stale or entry.embedding_signature != signature
                for entry in searchable_entries
            )
        )
        try:
            from novel_forge.memory.zvec_store import ZvecVectorStore

            zvec_store = ZvecVectorStore(
                str(index_path),
                dimension,
                index_type=index_type,
                memory_limit_mb=memory_limit_mb,
                reset_existing=not same_embedding_space,
            )
            self.setup_zvec(zvec_store)
        except Exception:
            logger.debug("humanize_zvec_setup_failed", exc_info=True)
            return False

        if needs_rebuild:
            rebuild = self.rebuild_vectors(
                embedder,
                resumable=False,
                embedding_signature=signature,
            )
            if rebuild["failed"] > 0 or rebuild["embedded"] <= 0:
                self._zvec_store = None
                self._zvec_available = False
                return False
            meta = {
                "provider": normalized_provider,
                "model": normalized_model,
                "dimension": dimension,
                "embedding_signature": signature,
                "entry_count": len(searchable_entries),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            temp_path = meta_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(meta_path)
        return self.vector_available

    # -- Internal SQL helpers ------------------------------------------------

    def _insert_entry(self, entry: HumanizeLibraryEntry) -> None:
        cols = ", ".join(_ENTRY_COLUMNS)
        placeholders = ", ".join(["?"] * len(_ENTRY_COLUMNS))
        self._conn.execute(
            f"INSERT INTO entries ({cols}) VALUES ({placeholders})",
            _entry_to_row(entry),
        )

    def _update_entry(self, entry: HumanizeLibraryEntry) -> None:
        sets = ", ".join(f"{c} = ?" for c in _ENTRY_COLUMNS if c != "pattern_id")
        values = [
            _entry_to_row(entry)[i] for i, c in enumerate(_ENTRY_COLUMNS) if c != "pattern_id"
        ]
        values.append(entry.pattern_id)
        self._conn.execute(f"UPDATE entries SET {sets} WHERE pattern_id = ?", values)

    def _sync_fts_insert(self, entry: HumanizeLibraryEntry) -> None:
        """Insert into FTS5 table."""
        try:
            self._conn.execute(
                "INSERT INTO entries_fts (rowid, pattern_id, pattern_name, category, keywords, notes) "
                "VALUES ((SELECT rowid FROM entries WHERE pattern_id = ?), ?, ?, ?, ?, ?)",
                (
                    entry.pattern_id,
                    entry.pattern_id,
                    entry.pattern_name,
                    entry.category,
                    " ".join(entry.keywords),
                    entry.notes,
                ),
            )
        except Exception:
            logger.debug("fts_insert_failed | id=%s", entry.pattern_id, exc_info=True)

    def _sync_fts_update(self, entry: HumanizeLibraryEntry) -> None:
        """Update FTS5 table (delete + re-insert)."""
        try:
            self._conn.execute("DELETE FROM entries_fts WHERE pattern_id = ?", (entry.pattern_id,))
            self._conn.execute(
                "INSERT INTO entries_fts (rowid, pattern_id, pattern_name, category, keywords, notes) "
                "VALUES ((SELECT rowid FROM entries WHERE pattern_id = ?), ?, ?, ?, ?, ?)",
                (
                    entry.pattern_id,
                    entry.pattern_id,
                    entry.pattern_name,
                    entry.category,
                    " ".join(entry.keywords),
                    entry.notes,
                ),
            )
        except Exception:
            logger.debug("fts_update_failed | id=%s", entry.pattern_id, exc_info=True)

    def fts_search(self, query: str, limit: int = 20) -> list[str]:
        """Full-text search. Returns list of pattern_ids."""
        try:
            cur = self._conn.execute(
                "SELECT pattern_id FROM entries_fts WHERE entries_fts MATCH ? LIMIT ?",
                (query, limit),
            )
            return [row[0] for row in cur.fetchall()]
        except Exception:
            logger.debug("fts_search_failed", exc_info=True)
            return []


# ---------------------------------------------------------------------------
# seed_builtin_patterns
# ---------------------------------------------------------------------------


def seed_builtin_patterns(lib: HumanizeLibrary) -> int:
    """Seed the library with builtin entries. Idempotent.

    Returns the number of entries added.
    """
    added = 0
    synchronized = 0
    now = datetime.now(timezone.utc)
    for entry_dict in LIBRARY_BUILTIN_ENTRIES:
        pid = entry_dict["pattern_id"]
        existing = lib.get(pid)
        if existing is not None:
            # Builtins are code-owned.  Synchronize executable metadata while
            # preserving user-controlled enablement and accumulated hit stats.
            desired = existing.model_copy(
                update={
                    "pattern_name": entry_dict["pattern_name"],
                    "category": entry_dict.get("category", ""),
                    "severity": entry_dict.get("severity", "medium"),
                    "detection_method": entry_dict.get("detection_method", "regex"),
                    "detection_config": entry_dict.get("detection_config", {}),
                    "example_phrases": entry_dict.get("example_phrases", []),
                    "example_template": entry_dict.get("example_template", ""),
                    "keywords": entry_dict.get("keywords", []),
                    "notes": entry_dict.get("notes", ""),
                }
            )
            if desired != existing:
                desired = desired.model_copy(update={"vector_stale": True})
                lib._update_entry(desired)
                lib._sync_fts_update(desired)
                synchronized += 1
            continue
        entry = HumanizeLibraryEntry(
            pattern_id=pid,
            pattern_name=entry_dict["pattern_name"],
            category=entry_dict.get("category", ""),
            severity=entry_dict.get("severity", "medium"),
            detection_method=entry_dict.get("detection_method", "regex"),
            source=entry_dict.get("source", "builtin"),
            enabled=entry_dict.get("enabled", True),
            example_phrases=entry_dict.get("example_phrases", []),
            example_template=entry_dict.get("example_template", ""),
            detection_config=entry_dict.get("detection_config", {}),
            keywords=entry_dict.get("keywords", []),
            notes=entry_dict.get("notes", ""),
            created_at=now,
        )
        # Direct insert (bypass lock for seed — it's an initialization op)
        lib._insert_entry(entry)
        lib._sync_fts_insert(entry)
        added += 1
    lib._conn.commit()
    total = len(LIBRARY_BUILTIN_ENTRIES)
    _safe_log_event(
        "humanize_library.seed_completed",
        {
            "added": added,
            "synchronized": synchronized,
            "skipped": total - added,
            "total": total,
        },
    )
    return added


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_text(entry: HumanizeLibraryEntry) -> str:
    """Build a searchable text representation of an entry."""
    parts = [entry.pattern_name, entry.category]
    parts.extend(entry.keywords)
    parts.extend(entry.example_phrases)
    if entry.notes:
        parts.append(entry.notes)
    return " ".join(parts)


def _version_gt(a: str, b: str) -> bool:
    """Simple semver-like version comparison."""
    try:
        a_parts = [int(x) for x in a.split(".")]
        b_parts = [int(x) for x in b.split(".")]
    except ValueError:
        return a > b
    return a_parts > b_parts


def _build_version_chain(from_version: str, to_version: str) -> list[str]:
    """Build the chain of versions to migrate through.

    Currently returns an empty list since v2.0 is the initial version
    and there are no migrations registered.
    """
    # When migrations are added, this function should return the ordered
    # list of target versions between from_version and to_version.
    chain: list[str] = []
    for ver in sorted(_MIGRATIONS.keys()):
        if _version_gt(ver, from_version) and not _version_gt(ver, to_version):
            chain.append(ver)
    return chain

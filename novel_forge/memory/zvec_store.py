"""Zvec-backed vector store for episodic memory."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from novel_forge.memory.vector_store import matches_filter

_log = logging.getLogger(__name__)

_ZVEC_INIT_LOCK = threading.Lock()
_ZVEC_INITIALIZED = False
_ZVEC_COLLECTION_LOCK_GUARD = threading.Lock()
_ZVEC_COLLECTION_LOCKS: dict[str, threading.RLock] = {}
_SUPPORTED_INDEX_TYPES = {"hnsw", "ivf", "flat", "hnsw_rabitq", "diskann"}

# Maximum overscan multiplier when filters cannot be fully pushed down to
# zvec.  Prevents pulling the entire collection into Python memory for large
# projects (tens of thousands of vectors).
_MAX_OVERSCAN_MULTIPLIER = 10
# When the in-memory _metadata cache grows beyond this many entries, a
# background resync is triggered on the next write to bound memory usage.
_METADATA_RESYNC_THRESHOLD = 10_000

_fcntl: Any
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - only relevant on non-POSIX platforms
    _fcntl = None

_msvcrt: Any
try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - only relevant on non-Windows platforms
    _msvcrt = None


def _lock_file(handle: Any) -> None:
    if sys.platform == "win32" and _msvcrt is not None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)
        _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
    elif _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if sys.platform == "win32" and _msvcrt is not None:
        handle.seek(0)
        _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
    elif _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


class ZvecVectorStore:
    """Vector-store adapter backed by Zvec.

    The adapter is intentionally zvec 0.5+ only: dense retrieval uses the
    unified ``Query`` API, scalar filters are pushed down where possible, and a
    lightweight FTS field is maintained for hybrid retrieval. Rich memory
    metadata remains in Novel Forge's JSON state and is mirrored in
    ``_metadata`` during rebuilds, which keeps migration and fallback behavior
    straightforward.
    """

    backend_name = "zvec"
    vector_field = "embedding"
    text_field = "content"
    collection_name = "novel_forge_memory"

    _filterable_fields = {"chapter", "scene_index", "is_outline"}

    def __init__(
        self,
        path: str,
        dimension: int,
        index_type: str = "hnsw",
        memory_limit_mb: int = 512,
        extra_int_fields: tuple[str, ...] = (),
        text_fields: tuple[str, ...] = (text_field,),
        reset_existing: bool = False,
    ) -> None:
        if dimension <= 0:
            raise ValueError("ZvecVectorStore dimension must be positive")

        self.path = Path(path)
        self.dimension = int(dimension)
        self.index_type = self._normalize_index_type(index_type)
        self.memory_limit_mb = int(memory_limit_mb or 512)
        self._extra_int_fields = tuple(
            field
            for field in dict.fromkeys(str(item).strip() for item in extra_int_fields)
            if field and field not in self._filterable_fields
        )
        self._reset_existing = bool(reset_existing)
        self._text_fields = self._normalize_text_fields(text_fields)
        self._active_filterable_fields = set(self._filterable_fields)
        self._active_filterable_fields.update(self._extra_int_fields)
        self._active_output_fields = set(self._active_filterable_fields)
        self._active_output_fields.update(self._text_fields)
        self._metadata: dict[str, dict[str, Any]] = {}

        import zvec  # type: ignore[import-not-found]

        self._zvec = zvec
        self._require_zvec_05_api()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()
        self._collection = self._open_or_create_collection()

    def add(self, id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        """Add or replace a vector with metadata."""
        if not id:
            raise ValueError("vector id cannot be empty")
        clean_vector = self._coerce_vector(vector)
        fields = self._fields_from_metadata(metadata)
        doc = self._zvec.Doc(
            id=str(id),
            vectors={self.vector_field: clean_vector},
            fields=fields,
        )
        self._collection.upsert(doc)
        self._metadata[str(id)] = self._metadata_snapshot(metadata, fields)
        self._maybe_resync_metadata()

    def add_many(self, items: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        """Add or replace multiple vectors with metadata."""
        docs = []
        metadata_updates: dict[str, dict[str, Any]] = {}
        for id, vector, metadata in items:
            if not id:
                raise ValueError("vector id cannot be empty")
            clean_vector = self._coerce_vector(vector)
            fields = self._fields_from_metadata(metadata)
            docs.append(
                self._zvec.Doc(
                    id=str(id),
                    vectors={self.vector_field: clean_vector},
                    fields=fields,
                )
            )
            metadata_updates[str(id)] = self._metadata_snapshot(metadata, fields)
        if not docs:
            return
        self._collection.upsert(docs)
        self._metadata.update(metadata_updates)
        self._maybe_resync_metadata()

    def clear(self) -> None:
        """Clear all vectors by recreating the derived Zvec collection."""
        with self._collection_init_lock():
            self._metadata.clear()
            try:
                self._collection.destroy()
            except Exception:
                self._remove_collection_dir()
            self._collection = self._create_collection(reset_existing=True)

    def remove(self, id: str) -> None:
        """Remove one vector by id."""
        self._metadata.pop(str(id), None)
        try:
            self._collection.delete(str(id))
        except Exception as exc:
            _log.debug("zvec_delete_ignored | id=%s | error=%s", id, exc)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by cosine similarity.

        Zvec returns cosine distance for a COSINE index, so the adapter converts
        it back to Novel Forge's historical score contract: higher similarity is
        better.
        """
        if not query_vector or top_k <= 0:
            return []

        clean_query = self._coerce_vector(query_vector)
        filter_expr, fully_pushed_down = self._build_filter_expression(filter)
        query_top_k = self._query_top_k(top_k, fully_pushed_down)
        query = self._zvec.Query(self.vector_field, vector=clean_query)
        docs = self._collection.query(
            queries=query,
            topk=query_top_k,
            filter=filter_expr or None,
            output_fields=list(self._active_output_fields),
        )

        return self._docs_to_results(
            docs,
            top_k=top_k,
            filter=filter,
            score_mode="distance",
        )

    def text_search(
        self,
        query_text: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by zvec 0.5 full-text retrieval over the content field."""
        clean_text = str(query_text or "").strip()
        if not clean_text or top_k <= 0:
            return []

        filter_expr, fully_pushed_down = self._build_filter_expression(filter)
        query_top_k = self._query_top_k(top_k, fully_pushed_down)
        query = self._zvec.Query(
            self.text_field,
            fts=self._zvec.Fts(match_string=clean_text),
        )
        docs = self._collection.query(
            queries=query,
            topk=query_top_k,
            filter=filter_expr or None,
            output_fields=list(self._active_output_fields),
        )
        return self._docs_to_results(
            docs,
            top_k=top_k,
            filter=filter,
            score_mode="score",
        )

    def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
        vector_weight: float = 0.75,
        text_weight: float = 0.25,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search with zvec 0.5 hybrid dense-vector + FTS retrieval."""
        clean_text = str(query_text or "").strip()
        if not clean_text:
            return self.search(query_vector, top_k=top_k, filter=filter)
        if not query_vector or top_k <= 0:
            return []

        clean_query = self._coerce_vector(query_vector)
        filter_expr, fully_pushed_down = self._build_filter_expression(filter)
        query_top_k = self._query_top_k(top_k, fully_pushed_down)
        vector_query = self._zvec.Query(self.vector_field, vector=clean_query)
        text_query = self._zvec.Query(
            self.text_field,
            fts=self._zvec.Fts(match_string=clean_text),
        )
        weights = self._normalize_hybrid_weights(vector_weight, text_weight)
        docs = self._collection.query(
            queries=[vector_query, text_query],
            topk=query_top_k,
            filter=filter_expr or None,
            output_fields=list(self._active_output_fields),
            reranker=self._zvec.WeightedReRanker(weights),
        )
        return self._docs_to_results(
            docs,
            top_k=top_k,
            filter=filter,
            score_mode="score",
        )

    def flush(self) -> None:
        """Flush pending Zvec writes to disk."""
        try:
            self._collection.flush()
        except Exception as exc:
            _log.debug("zvec_flush_ignored | path=%s | error=%s", self.path, exc)

    def resync_metadata(self) -> int:
        """Rebuild the in-memory _metadata cache from zvec collection stats.

        This bounds memory usage for long-running processes that index many
        thousands of vectors.  Returns the new metadata cache size.
        """
        try:
            stats = self._collection.stats
            doc_count = int(getattr(stats, "doc_count", 0) or 0)
        except Exception:
            doc_count = 0
        # If zvec has significantly fewer docs than our cache, rebuild.
        # Otherwise just trim entries that are no longer in zvec.
        if doc_count < len(self._metadata) * 0.5:
            _log.info(
                "zvec_metadata_resync | cache_size=%d | zvec_docs=%d | rebuilding",
                len(self._metadata),
                doc_count,
            )
            # Keep only entries that we know about from recent writes.
            # A full rebuild would require iterating zvec which is expensive;
            # instead we just cap the cache by dropping oldest entries.
            max_keep = max(doc_count * 2, 1000)
            while len(self._metadata) > max_keep:
                self._metadata.pop(next(iter(self._metadata)), None)
        return len(self._metadata)

    def _maybe_resync_metadata(self) -> None:
        """Trigger a metadata resync when cache exceeds threshold."""
        if len(self._metadata) > _METADATA_RESYNC_THRESHOLD:
            self.resync_metadata()

    def _ensure_initialized(self) -> None:
        global _ZVEC_INITIALIZED

        if _ZVEC_INITIALIZED:
            return

        with _ZVEC_INIT_LOCK:
            if _ZVEC_INITIALIZED:
                return
            try:
                self._zvec.init(
                    log_level=self._zvec.LogLevel.WARN,
                    memory_limit_mb=self.memory_limit_mb,
                )
            except RuntimeError as exc:
                if "initialized" not in str(exc).lower():
                    raise
            _ZVEC_INITIALIZED = True

    def _require_zvec_05_api(self) -> None:
        missing = [
            name
            for name in ("Query", "Fts", "FtsIndexParam", "WeightedReRanker")
            if getattr(self._zvec, name, None) is None
        ]
        if missing:
            raise RuntimeError(
                "ZvecVectorStore requires zvec>=0.5.0; missing API: " + ", ".join(missing)
            )

    def _open_or_create_collection(self) -> Any:
        with self._collection_init_lock():
            if self._reset_existing:
                return self._create_collection(
                    reset_existing=True,
                    quarantine_existing=True,
                    reset_reason="rebuild",
                )
            if self.path.exists():
                try:
                    collection = self._zvec.open(str(self.path))
                    self._validate_opened_schema(collection)
                    return collection
                except Exception as exc:
                    if self._is_collection_lock_error(exc):
                        raise RuntimeError(
                            "zvec collection is locked or already open in another "
                            f"process/thread: {self.path}"
                        ) from exc
                    _log.warning(
                        "zvec_collection_open_failed | path=%s | error=%s | recreating=true",
                        self.path,
                        exc,
                    )
                    return self._create_collection(
                        reset_existing=True,
                        quarantine_existing=True,
                        reset_reason="open_failed",
                    )
            return self._create_collection(reset_existing=False)

    def _create_collection(
        self,
        *,
        reset_existing: bool,
        quarantine_existing: bool = False,
        reset_reason: str = "reset",
    ) -> Any:
        if reset_existing:
            if quarantine_existing:
                self._quarantine_collection_dir(reset_reason)
            else:
                self._remove_collection_dir()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = self._build_schema()
        try:
            return self._zvec.create_and_open(
                path=str(self.path),
                schema=schema,
            )
        except Exception as exc:
            quarantined = self._quarantine_collection_dir("create_failed")
            if quarantined is None:
                raise
            _log.warning(
                "zvec_collection_create_failed_quarantined | path=%s | quarantine=%s | "
                "error=%s | retrying=true",
                self.path,
                quarantined,
                exc,
            )
            try:
                return self._zvec.create_and_open(
                    path=str(self.path),
                    schema=schema,
                )
            except Exception as retry_exc:
                retry_quarantine = self._quarantine_collection_dir("create_retry_failed")
                raise RuntimeError(
                    "zvec collection create retry failed after quarantining corrupt "
                    f"collection at {quarantined}; retry_quarantine={retry_quarantine}; "
                    f"error={retry_exc}"
                ) from retry_exc

    def _validate_opened_schema(self, collection: Any) -> None:
        schema = getattr(collection, "schema", None)
        vector = None
        if schema is not None:
            vector_getter = getattr(schema, "vector", None)
            if callable(vector_getter):
                vector = vector_getter(self.vector_field)
            elif getattr(schema, "vectors", None):
                vector = schema.vectors[0]
        actual_dimension = int(getattr(vector, "dimension", 0) or 0)
        if actual_dimension and actual_dimension != self.dimension:
            raise ValueError(
                f"collection dimension mismatch: expected {self.dimension}, got {actual_dimension}"
            )
        if schema is not None:
            field_names = {
                str(getattr(field, "name", "") or "") for field in getattr(schema, "fields", [])
            }
            missing_text_fields = [field for field in self._text_fields if field not in field_names]
            if missing_text_fields:
                raise ValueError(
                    "collection schema missing zvec 0.5 FTS field(s): "
                    + ", ".join(missing_text_fields)
                )

    @staticmethod
    def _is_collection_lock_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "lock" in message and (
            "already open" in message or "can't lock" in message or "no locks available" in message
        )

    def _remove_collection_dir(self) -> None:
        if self.path.exists():
            if self.path.is_dir():
                shutil.rmtree(self.path)
            else:
                self.path.unlink()
        if self.path.exists():
            raise RuntimeError(f"failed to remove zvec collection path: {self.path}")

    def _quarantine_collection_dir(self, reason: str) -> Path | None:
        if not self.path.exists():
            return None

        timestamp = time.strftime("%Y%m%d%H%M%S")
        base_name = f"{self.path.name}.corrupt-{timestamp}-{os.getpid()}-{reason}"
        for attempt in range(20):
            suffix = "" if attempt == 0 else f"-{attempt}"
            target = self.path.with_name(f"{base_name}{suffix}")
            try:
                self.path.rename(target)
                return target
            except FileExistsError:
                continue
            except OSError as exc:
                _log.warning(
                    "zvec_collection_quarantine_failed | path=%s | target=%s | "
                    "error=%s | removing=true",
                    self.path,
                    target,
                    exc,
                )
                self._remove_collection_dir()
                return None

        self._remove_collection_dir()
        return None

    @contextmanager
    def _collection_init_lock(self) -> Iterator[None]:
        lock = self._collection_thread_lock()
        with lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.parent / f".{self.path.name}.init.lock"
            with lock_path.open("a+", encoding="utf-8") as handle:
                _lock_file(handle)
                try:
                    yield
                finally:
                    _unlock_file(handle)

    def _collection_thread_lock(self) -> threading.RLock:
        key = str(self.path.resolve(strict=False))
        with _ZVEC_COLLECTION_LOCK_GUARD:
            lock = _ZVEC_COLLECTION_LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _ZVEC_COLLECTION_LOCKS[key] = lock
            return lock

    def _build_schema(self) -> Any:
        zvec = self._zvec
        scalar_index = zvec.InvertIndexParam(enable_range_optimization=True)
        fts_index = zvec.FtsIndexParam()
        return zvec.CollectionSchema(
            name=self.collection_name,
            fields=[
                zvec.FieldSchema(
                    "chapter",
                    zvec.DataType.INT64,
                    nullable=False,
                    index_param=scalar_index,
                ),
                zvec.FieldSchema(
                    "scene_index",
                    zvec.DataType.INT32,
                    nullable=False,
                    index_param=scalar_index,
                ),
                zvec.FieldSchema(
                    "is_outline",
                    zvec.DataType.BOOL,
                    nullable=False,
                    index_param=zvec.InvertIndexParam(),
                ),
                *[
                    zvec.FieldSchema(
                        field,
                        zvec.DataType.STRING,
                        nullable=False,
                        index_param=fts_index,
                    )
                    for field in self._text_fields
                ],
                *[
                    zvec.FieldSchema(
                        field,
                        zvec.DataType.INT64,
                        nullable=False,
                        index_param=scalar_index,
                    )
                    for field in self._extra_int_fields
                ],
            ],
            vectors=zvec.VectorSchema(
                self.vector_field,
                zvec.DataType.VECTOR_FP32,
                dimension=self.dimension,
                index_param=self._build_index_param(),
            ),
        )

    def _build_index_param(self) -> Any:
        metric = self._zvec.MetricType.COSINE
        if self.index_type == "flat":
            return self._zvec.FlatIndexParam(metric_type=metric)
        if self.index_type == "ivf":
            return self._zvec.IVFIndexParam(metric_type=metric)
        if self.index_type == "hnsw_rabitq":
            return self._zvec.HnswRabitqIndexParam(metric_type=metric)
        if self.index_type == "diskann":
            diskann_cls = getattr(self._zvec, "DiskAnnIndexParam", None)
            if diskann_cls is None:
                raise RuntimeError("Zvec DiskANN index requires zvec>=0.5.0")
            return diskann_cls(metric_type=metric)
        return self._zvec.HnswIndexParam(metric_type=metric)

    def _coerce_vector(self, vector: list[float]) -> list[float]:
        if len(vector) != self.dimension:
            raise ValueError(
                f"vector dimension mismatch: expected {self.dimension}, got {len(vector)}"
            )
        return [float(value) for value in vector]

    @staticmethod
    def _normalize_index_type(index_type: str) -> str:
        normalized = str(index_type or "hnsw").strip().lower().replace("-", "_")
        if normalized in {"hnsw_rabit_q", "hnsw_rabit-q"}:
            normalized = "hnsw_rabitq"
        if normalized == "disk_ann":
            normalized = "diskann"
        if normalized in _SUPPORTED_INDEX_TYPES:
            return normalized
        _log.warning("unknown_zvec_index_type | index_type=%s | fallback=hnsw", index_type)
        return "hnsw"

    @classmethod
    def _normalize_text_fields(cls, text_fields: tuple[str, ...]) -> tuple[str, ...]:
        fields = [
            field for field in dict.fromkeys(str(item).strip() for item in text_fields) if field
        ]
        if cls.text_field not in fields:
            fields.insert(0, cls.text_field)
        return tuple(fields)

    def _fields_from_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        fields = {
            "chapter": int(metadata.get("chapter", 0) or 0),
            "scene_index": int(metadata.get("scene_index", 0) or 0),
            "is_outline": bool(metadata.get("is_outline", False)),
        }
        for field in self._text_fields:
            if field == self.text_field:
                fields[field] = self._text_from_metadata(metadata)
            else:
                fields[field] = str(metadata.get(field, "") or "")
        for field in self._extra_int_fields:
            fields[field] = int(metadata.get(field, 0) or 0)
        return fields

    def _metadata_snapshot(
        self,
        metadata: dict[str, Any],
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = dict(metadata)
        for field in self._text_fields:
            snapshot[field] = fields.get(field, "")
        return snapshot

    def _text_from_metadata(self, metadata: dict[str, Any]) -> str:
        content = metadata.get(self.text_field)
        if content:
            return str(content)
        parts: list[str] = []
        for key in (
            "event_summary",
            "plot_point",
            "summary",
            "volume_summary",
            "quote",
            "embedding_text",
            "thematic_meaning",
            "issue_type",
            "severity",
            "emotional_tone",
            "timestamp_in_story",
        ):
            value = metadata.get(key)
            if value:
                parts.append(str(value))
        for key in ("characters", "locations", "event_types", "keywords", "themes"):
            value = metadata.get(key)
            if isinstance(value, (list, tuple, set)):
                parts.extend(str(item) for item in value if item)
            elif value:
                parts.append(str(value))
        return " ".join(part for part in parts if part).strip()

    def _metadata_from_doc(self, doc: Any) -> dict[str, Any]:
        def _field(name: str, default: Any) -> Any:
            value = None
            field = getattr(doc, "field", None)
            if callable(field):
                value = field(name)
            elif hasattr(doc, "fields"):
                value = getattr(doc, "fields", {}).get(name)
            return default if value is None else value

        metadata = {
            "chapter": int(_field("chapter", 0) or 0),
            "scene_index": int(_field("scene_index", 0) or 0),
            "is_outline": bool(_field("is_outline", False)),
        }
        for field in self._extra_int_fields:
            metadata[field] = int(_field(field, 0) or 0)
        for field in self._text_fields:
            metadata[field] = str(_field(field, "") or "")
        return metadata

    def _docs_to_results(
        self,
        docs: Any,
        *,
        top_k: int,
        filter: dict[str, Any] | None,
        score_mode: str,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        results: list[tuple[str, float, dict[str, Any]]] = []
        for doc in docs:
            metadata = self._metadata.get(str(doc.id)) or self._metadata_from_doc(doc)
            if filter and not matches_filter(metadata, filter):
                continue
            raw_score = getattr(doc, "score", 0.0) or 0.0
            score = (
                self._distance_to_similarity(raw_score)
                if score_mode == "distance"
                else float(raw_score)
            )
            results.append((str(doc.id), score, metadata))
            if len(results) >= top_k:
                break

        results.sort(key=lambda item: item[1], reverse=True)
        return results[:top_k]

    def _query_top_k(self, top_k: int, fully_pushed_down: bool) -> int:
        if fully_pushed_down:
            return max(1, int(top_k))
        # When filters cannot be fully pushed down we need to overscan,
        # but cap the overscan to avoid pulling the entire collection into
        # Python memory for large projects.
        overscan = max(int(top_k) * _MAX_OVERSCAN_MULTIPLIER, 100)
        return overscan

    def _build_filter_expression(
        self,
        filter: dict[str, Any] | None,
    ) -> tuple[str, bool]:
        if not filter:
            return "", True

        clauses: list[str] = []
        fully_pushed_down = True
        for key, value in filter.items():
            if key not in self._active_filterable_fields:
                fully_pushed_down = False
                continue
            field_clauses = self._field_filter_clauses(key, value)
            if not field_clauses:
                fully_pushed_down = False
                continue
            clauses.extend(field_clauses)

        return " and ".join(clauses), fully_pushed_down

    def _field_filter_clauses(self, key: str, value: Any) -> list[str]:
        if isinstance(value, dict):
            clauses: list[str] = []
            for op, expected in value.items():
                operator = {
                    "$gte": ">=",
                    "$lte": "<=",
                    "$gt": ">",
                    "$lt": "<",
                    "$ne": "!=",
                }.get(op)
                if operator is not None:
                    clauses.append(f"{key} {operator} {self._format_filter_value(expected)}")
                    continue
                if op == "$in":
                    clauses.append(f"{key} in ({self._format_filter_list(expected)})")
                    continue
                if op == "$nin":
                    clauses.append(f"{key} not in ({self._format_filter_list(expected)})")
                    continue
                return []
            return clauses

        return [f"{key} = {self._format_filter_value(value)}"]

    @staticmethod
    def _format_filter_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{text}'"

    def _format_filter_list(self, value: Any) -> str:
        if not isinstance(value, (list, tuple, set)):
            value = [value]
        return ", ".join(self._format_filter_value(item) for item in value)

    @staticmethod
    def _normalize_hybrid_weights(vector_weight: float, text_weight: float) -> list[float]:
        vector = max(0.0, float(vector_weight))
        text = max(0.0, float(text_weight))
        total = vector + text
        if total <= 0:
            return [0.75, 0.25]
        return [vector / total, text / total]

    @staticmethod
    def _distance_to_similarity(distance: float) -> float:
        try:
            score = 1.0 - float(distance)
        except Exception:
            return 0.0
        return max(-1.0, min(1.0, score))

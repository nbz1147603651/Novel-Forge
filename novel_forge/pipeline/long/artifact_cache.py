"""Request-scoped typed artifact loading cache."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)
_FRESHNESS_METADATA_KEYS = {
    "source_text_hash",
    "canon_state_hash",
    "report_context_hash",
    "report_freshness",
    "context_hash",
    "source_context_hash",
    # Report writers retain this provenance outside individual Pydantic
    # contracts so the UI can explain which review pass produced a file.
    # It must not prevent a typed report from being reopened on a later pass.
    "pipeline_stage",
}


@dataclass(frozen=True)
class _CacheEntry:
    mtime_ns: int
    size: int
    value: Any


@dataclass(frozen=True)
class ArtifactLoadContext:
    """Typed load context for request-scoped artifact reuse."""

    source: str = "chapter_request"
    project_id: str = ""
    chapter_number: int = 0


class ChapterArtifactCache:
    """Small request-scoped cache for repeated chapter artifact loads."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage
        self._entries: dict[tuple[str, str], _CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    def load_json(self, path: Path) -> dict[str, Any]:
        entry = self._get(("json", self._key(path)), path)
        if entry is None:
            payload = self._storage.load_json(path)
            entry = self._remember(("json", self._key(path)), path, payload)
        return deepcopy(entry.value) if isinstance(entry.value, dict) else {}

    def load_model(self, path: Path, model: type[ModelT]) -> ModelT:
        model_key = f"model:{model.__module__}.{model.__qualname__}"
        cache_key = (model_key, self._key(path))
        entry = self._get(cache_key, path)
        if entry is None:
            payload = self._storage.load_json(path)
            value = model.model_validate(self._model_payload(payload, model))
            entry = self._remember(cache_key, path, value)
        value = entry.value
        if hasattr(value, "model_copy"):
            return cast(ModelT, value.model_copy(deep=True))
        return model.model_validate(self._model_payload(deepcopy(value), model))

    def clear(self) -> None:
        self._entries.clear()

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
        }

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.resolve(strict=False))

    def _get(self, cache_key: tuple[str, str], path: Path) -> _CacheEntry | None:
        try:
            stat = path.stat()
        except OSError:
            self._entries.pop(cache_key, None)
            self._misses += 1
            return None
        entry = self._entries.get(cache_key)
        if entry is None:
            self._misses += 1
            return None
        if entry.mtime_ns != stat.st_mtime_ns or entry.size != stat.st_size:
            self._entries.pop(cache_key, None)
            self._misses += 1
            return None
        self._hits += 1
        return entry

    def _remember(self, cache_key: tuple[str, str], path: Path, value: Any) -> _CacheEntry:
        stat = path.stat()
        entry = _CacheEntry(mtime_ns=stat.st_mtime_ns, size=stat.st_size, value=deepcopy(value))
        self._entries[cache_key] = entry
        return entry

    @staticmethod
    def _model_payload(payload: Any, model: type[ModelT]) -> Any:
        if not isinstance(payload, dict):
            return payload
        allowed_fields = set(getattr(model, "model_fields", {}) or {})
        if not allowed_fields:
            return payload
        return {
            key: value
            for key, value in payload.items()
            if key in allowed_fields or key not in _FRESHNESS_METADATA_KEYS
        }


class ChapterArtifactBundleLoader:
    """Typed request loader built on top of :class:`ChapterArtifactCache`."""

    def __init__(
        self,
        storage: Any,
        *,
        context: ArtifactLoadContext | None = None,
        cache: ChapterArtifactCache | None = None,
    ) -> None:
        self.context = context or ArtifactLoadContext()
        self.cache = cache or ChapterArtifactCache(storage)

    def load_json(self, path: Path) -> dict[str, Any]:
        return self.cache.load_json(path)

    def load_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return self.cache.load_json(path)

    def load_model(self, path: Path, model: type[ModelT]) -> ModelT:
        return self.cache.load_model(path, model)

    def load_optional_model(self, path: Path, model: type[ModelT]) -> ModelT | None:
        if not path.exists():
            return None
        return self.cache.load_model(path, model)

    def stats(self) -> dict[str, int]:
        return self.cache.stats()

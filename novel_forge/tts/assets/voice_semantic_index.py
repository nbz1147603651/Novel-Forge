"""Low-cost semantic retrieval for reusable and provider voice catalogs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from novel_forge.core.config import Settings
from novel_forge.gateway.embedding import EmbeddingService
from novel_forge.memory.embedding_profiles import get_embedding_config_from_profiles
from novel_forge.memory.vector_store import VectorStore, create_vector_store
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.tts.assets.voice_library import VoiceLibrary, VoiceLibraryEntry
from novel_forge.tts.schemas import TTSProvider

_log = get_logger("tts.voice_semantic_index")
_MOCK_DIMENSIONS = 256


@dataclass(frozen=True)
class VoiceSemanticScores:
    """Separated semantic results plus the current supplier snapshot identity."""

    library: dict[str, dict[str, float]]
    catalog: dict[str, dict[str, float]]
    catalog_fingerprint: str = ""


@dataclass(frozen=True)
class _SemanticDocument:
    document_id: str
    voice_id: str
    namespace: str
    content: str


async def build_voice_semantic_scores(
    *,
    settings: Settings,
    library: VoiceLibrary,
    characters: list[dict[str, Any]],
    provider: TTSProvider,
    provider_voices: list[dict[str, Any]] | None = None,
) -> VoiceSemanticScores:
    """Build an incrementally synchronized semantic view of all voice sources.

    The provider response is authoritative. Its latest rows and the reusable
    voice library are projected into a provider/model-specific Zvec collection.
    Changed rows are re-embedded, new rows are inserted, and absent rows are
    deleted. The vector collection is therefore a rebuildable cache, never the
    source of truth.
    """
    empty = VoiceSemanticScores(library={}, catalog={})
    if not settings.tts_voice_semantic_matching_enabled or not characters:
        return empty
    library_entries = [
        entry for entry in library.entries if entry.provider == provider and not entry.is_expired
    ]
    catalog_voices = [
        voice
        for voice in provider_voices or []
        if str(voice.get("voice_id") or voice.get("id") or "").strip()
    ]
    documents = _semantic_documents(library_entries, catalog_voices)
    if not documents:
        return empty

    service: EmbeddingService | None = None
    try:
        service, dimension, model_signature, mock_mode = _embedding_runtime(settings)
        backend = "in_memory" if mock_mode else settings.memory_vector_store_backend
        collection_path, manifest_path = _index_paths(
            settings.storage_root,
            model_signature=model_signature,
            dimension=dimension,
            provider=provider,
        )
        collection_existed = collection_path.exists()
        store = create_vector_store(
            backend=backend,
            path=collection_path,
            dimension=dimension,
            index_type=settings.memory_zvec_index_type,
            memory_limit_mb=settings.memory_zvec_memory_limit_mb,
        )
        await _sync_documents(
            service=service,
            store=store,
            documents=documents,
            dimension=dimension,
            manifest_path=manifest_path,
            persist_manifest=backend != "in_memory",
            mock_mode=mock_mode,
            force_reindex=backend != "in_memory" and not collection_existed,
        )
        query_vectors = await _embed_texts(
            service,
            [_character_semantic_text(character) for character in characters],
            dimension=dimension,
            mock_mode=mock_mode,
        )
        valid_document_ids = {document.document_id for document in documents}
        top_k = min(max(2, settings.tts_voice_semantic_top_k), len(documents))
        library_scores: dict[str, dict[str, float]] = {}
        catalog_scores: dict[str, dict[str, float]] = {}
        for character, vector in zip(characters, query_vectors, strict=True):
            character_id = str(character.get("character_id") or character.get("name") or "")
            if not character_id:
                continue
            library_scores[character_id] = {}
            catalog_scores[character_id] = {}
            for document_id, score, _metadata in store.search(vector, top_k=top_k):
                if document_id not in valid_document_ids or "--" not in document_id:
                    continue
                namespace, voice_id = document_id.split("--", 1)
                target = library_scores if namespace == "library" else catalog_scores
                target[character_id][voice_id] = round(max(0.0, min(1.0, score)), 4)
        flush = getattr(store, "flush", None)
        if callable(flush):
            flush()
        return VoiceSemanticScores(
            library=library_scores,
            catalog=catalog_scores,
            catalog_fingerprint=voice_catalog_fingerprint(catalog_voices),
        )
    except Exception as exc:
        _log.warning("voice_semantic_index_unavailable | provider=%s | error=%s", provider, exc)
        return empty
    finally:
        if service is not None:
            await service.aclose()


def _semantic_documents(
    library_entries: list[VoiceLibraryEntry],
    catalog_voices: list[dict[str, Any]],
) -> list[_SemanticDocument]:
    documents = [
        _SemanticDocument(
            document_id=f"library--{entry.voice_id}",
            voice_id=entry.voice_id,
            namespace="library",
            content=_voice_semantic_text(entry),
        )
        for entry in library_entries
    ]
    documents.extend(
        _SemanticDocument(
            document_id=f"catalog--{_catalog_voice_id(voice)}",
            voice_id=_catalog_voice_id(voice),
            namespace="catalog",
            content=_catalog_voice_semantic_text(voice),
        )
        for voice in catalog_voices
    )
    return documents


def _embedding_runtime(settings: Settings) -> tuple[EmbeddingService, int, str, bool]:
    if settings.memory_use_mock_embeddings:
        return EmbeddingService(), _MOCK_DIMENSIONS, "mock", True
    config = get_embedding_config_from_profiles(settings.memory_embedding_profile_id or None)
    if not config:
        raise RuntimeError("未配置可用的 embedding 模型")
    service = EmbeddingService.create_from_config(config)
    model = str(config.get("model") or "embedding")
    dimension = int(
        config.get("dimensions") or EmbeddingService.default_dimensions_for_model(model)
    )
    return service, dimension, f"{config.get('provider', '')}:{model}", False


async def _sync_documents(
    *,
    service: EmbeddingService,
    store: VectorStore,
    documents: list[_SemanticDocument],
    dimension: int,
    manifest_path: Path,
    persist_manifest: bool,
    mock_mode: bool,
    force_reindex: bool,
) -> None:
    manifest = _read_manifest(manifest_path) if persist_manifest and not force_reindex else {}
    texts = {document.document_id: document.content for document in documents}
    hashes = {document_id: _text_hash(text) for document_id, text in texts.items()}
    changed = [
        document
        for document in documents
        if manifest.get(document.document_id) != hashes[document.document_id]
    ]
    for stale_document_id in set(manifest).difference(hashes):
        store.remove(stale_document_id)
    if changed:
        vectors = await _embed_texts(
            service,
            [texts[document.document_id] for document in changed],
            dimension=dimension,
            mock_mode=mock_mode,
        )
        store.add_many(
            [
                (
                    document.document_id,
                    vector,
                    {
                        "content": texts[document.document_id],
                        "voice_id": document.voice_id,
                        "namespace": document.namespace,
                    },
                )
                for document, vector in zip(changed, vectors, strict=True)
            ]
        )
    if persist_manifest:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(manifest_path, {"entries": hashes})


async def _embed_texts(
    service: EmbeddingService,
    texts: list[str],
    *,
    dimension: int,
    mock_mode: bool,
) -> list[list[float]]:
    if mock_mode:
        return [service.generate_mock_embedding(text, dimension) for text in texts]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), service.max_batch_size):
        batch = texts[start : start + service.max_batch_size]
        results = await service.generate_batch(batch)
        vectors.extend(result.embedding for result in results)
    return vectors


def _read_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("entries", {}) if isinstance(raw, dict) else {}
        return {str(key): str(value) for key, value in entries.items()}
    except (OSError, TypeError, ValueError):
        return {}


def _index_paths(
    storage_root: Path,
    *,
    model_signature: str,
    dimension: int,
    provider: TTSProvider,
) -> tuple[Path, Path]:
    digest = hashlib.sha256(model_signature.encode("utf-8")).hexdigest()[:12]
    provider_key = re.sub(r"[^a-z0-9_-]+", "_", provider.value.lower())
    root = storage_root / "_global" / "tts_voice_vectors" / f"{digest}-{dimension}"
    return root / provider_key, root / f"{provider_key}.manifest.json"


def _character_semantic_text(character: Mapping[str, Any]) -> str:
    parts = [
        f"性别 {character.get('gender', '')}",
        f"年龄感 {character.get('age', '')}",
        f"角色定位 {character.get('role', '')}",
        f"性格 {character.get('personality', '')}",
        f"声线 {character.get('voice', '')} {character.get('voice_description', '')}",
    ]
    hints = character.get("tts_voice_hints")
    if isinstance(hints, Mapping):
        parts.append("表达习惯 " + " ".join(str(value or "") for value in hints.values()))
    return " ".join(part for part in parts if part.strip())


def _voice_semantic_text(entry: VoiceLibraryEntry) -> str:
    return " ".join(
        (
            f"性别 {entry.gender}",
            f"年龄感 {entry.age_hint}",
            f"适用角色 {entry.role}",
            f"性格 {entry.personality}",
            f"声线 {entry.voice_description}",
            f"设计简报 {entry.voice_design_prompt}",
        )
    )


def _catalog_voice_id(voice: Mapping[str, Any]) -> str:
    return str(voice.get("voice_id") or voice.get("id") or "").strip()


def _catalog_voice_semantic_text(voice: Mapping[str, Any]) -> str:
    tags = voice.get("tags")
    tag_text = (
        " ".join(str(tag) for tag in tags) if isinstance(tags, (list, tuple)) else str(tags or "")
    )
    return " ".join(
        (
            f"名称 {voice.get('name', '')}",
            f"性别 {voice.get('gender', '')}",
            f"年龄感 {voice.get('age', '') or voice.get('age_hint', '')}",
            f"适用角色 {voice.get('role', '')}",
            f"描述 {voice.get('description', '') or voice.get('voice_description', '')}",
            f"标签 {tag_text}",
        )
    )


def voice_catalog_fingerprint(voices: list[dict[str, Any]]) -> str:
    """Return a stable identity for one authoritative provider response."""
    canonical = json.dumps(voices, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

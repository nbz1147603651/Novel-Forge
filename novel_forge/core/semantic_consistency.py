"""Deterministic identities shared by semantic compilation and projections."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_VOLATILE_SEMANTIC_HASH_KEYS = frozenset({"created_at", "schema_version"})


def canonical_semantic_payload(payload: Any) -> Any:
    """Remove generated schema metadata without interpreting story meaning."""

    if isinstance(payload, dict):
        return {
            str(key): canonical_semantic_payload(value)
            for key, value in payload.items()
            if str(key) not in _VOLATILE_SEMANTIC_HASH_KEYS
        }
    if isinstance(payload, list):
        return [canonical_semantic_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [canonical_semantic_payload(item) for item in payload]
    return payload


def semantic_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        canonical_semantic_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["canonical_semantic_payload", "semantic_payload_hash"]

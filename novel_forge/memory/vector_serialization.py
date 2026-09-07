"""Compact binary serialization for memory embedding vectors."""

from __future__ import annotations

import base64
import struct


def pack_embedding(vec: list[float]) -> str:
    """Pack a float list into a base64-encoded little-endian float32 string."""
    return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode("ascii")


def unpack_embedding(b64: str, dims: int) -> list[float]:
    """Unpack a base64-encoded little-endian float32 string."""
    raw = base64.b64decode(b64)
    return list(struct.unpack(f"<{dims}f", raw))


# Backward-compatible private aliases used by older tests/imports.
_pack_embedding = pack_embedding
_unpack_embedding = unpack_embedding

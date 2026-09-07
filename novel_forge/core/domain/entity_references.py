"""Exact, type-aware resolution of references to an authoritative entity catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class EntityReferenceIndex:
    """Resolve IDs, canonical names and unambiguous registered aliases, never substrings."""

    def __init__(self, catalog: Sequence[Mapping[str, Any]]) -> None:
        self.by_id = {
            str(item["entity_id"]): dict(item) for item in catalog if item.get("entity_id")
        }
        self._names: dict[str, set[str]] = {}
        self._aliases: dict[str, set[str]] = {}
        for entity_id, item in self.by_id.items():
            name = str(item.get("name") or "").strip()
            if name:
                self._names.setdefault(name, set()).add(entity_id)
            for alias in item.get("aliases") or []:
                text = str(alias or "").strip()
                if text:
                    self._aliases.setdefault(text, set()).add(entity_id)

    def resolve(self, value: str, *, entity_type: str | None = None) -> str | None:
        """An existing ID of the wrong type must not fall back to a same-named entity."""
        value = value.strip()
        if value in self.by_id:
            item = self.by_id[value]
            return value if entity_type is None or item.get("entity_type") == entity_type else None
        for index in (self._names, self._aliases):
            matches = {
                entity_id
                for entity_id in index.get(value, set())
                if entity_type is None or self.by_id[entity_id].get("entity_type") == entity_type
            }
            if matches:
                return next(iter(matches)) if len(matches) == 1 else None
        return None

    def contains(self, value: str) -> bool:
        """Whether a spelling is registered, including ambiguous or wrong-type names."""
        return value in self.by_id or value in self._names or value in self._aliases

    def display_name(self, entity_id: str, *, entity_type: str | None = None) -> str:
        if entity_id not in self.by_id or self.resolve(entity_id, entity_type=entity_type) is None:
            return ""
        return str(self.by_id[entity_id].get("name") or "")

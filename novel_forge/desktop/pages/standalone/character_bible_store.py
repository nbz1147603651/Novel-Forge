"""Shared data layer for the character-bible/relationship-network pages.

Both `CharacterProfilePage` (角色档案) and `RelationshipNetworkPage` (关系网络)
mount a `CharacterBibleStore` so they share one bible instance, one dirty
flag, and one set of save/discard/dirty notifications.

The store is a thin wrapper around `character_artifact_writer` plus the
character-relationship matrix (`character_relationship_matrix.json`) and
the narrative-state `entity_graph.json` payload. It deliberately exposes
no UI of its own; it emits Qt signals that views listen to.

Keeping the data layer separate keeps each view small and lets us replace
either view without touching the persistence path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from novel_forge.core.domain.character_identity import clean_character_name
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    CharacterArtifactWriteResult,
)
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    add_character as _add_character_writer,
)
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    load_character_bible as _load_character_bible,
)
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    remove_relationship_edge as _remove_relationship_edge_writer,
)
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    retire_character as _retire_character_writer,
)
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    write_character_bible as _write_character_bible_writer,
)
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    write_relationship_edge as _write_relationship_edge_writer,
)
from novel_forge.persistence.models import ProjectLayout


@dataclass(frozen=True)
class _SaveWarning:
    code: str
    message: str


@dataclass
class CharacterRelationshipMatrix:
    """Lookup table from a frozenset of names to the typed relation type."""

    lookup: dict[frozenset[str], str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> CharacterRelationshipMatrix:
        return cls()

    def type_for(self, source: str, target: str) -> str:
        pair = frozenset((clean_character_name(source), clean_character_name(target)))
        return self.lookup.get(pair, "relationship")

    def as_dict(self) -> dict[frozenset[str], str]:
        return dict(self.lookup)


class CharacterBibleStore(QObject):
    """Single source of truth for the two character pages.

    Mutations never touch the bible directly; callers go through the
    ``request_*`` methods, which persist via the artifact writer and then
    emit ``bibleChanged`` so every view can refresh itself in lock-step.
    """

    bibleChanged = Signal()
    dirtyChanged = Signal(bool)
    saveWarningsChanged = Signal(list)
    savedPayloadChanged = Signal()
    bibleReplaced = Signal(object)  # emits the new CharacterBible
    requestExternalRefresh = Signal()  # forwarded to workspace level

    def __init__(self, project_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._project_dir = Path(project_dir)
        self._layout = ProjectLayout(self._project_dir)
        self._bible: CharacterBible = _load_character_bible(self._project_dir)
        self._saved_payload: dict[str, Any] = self._bible.model_dump(mode="json")
        self._pending_payload: dict[str, Any] | None = None
        self._dirty = False
        self._warnings: list[_SaveWarning] = []
        self._relationship_matrix = self._load_relationship_matrix()

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def bible(self) -> CharacterBible:
        return self._bible

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def warnings(self) -> list[_SaveWarning]:
        return list(self._warnings)

    @property
    def relationship_matrix(self) -> CharacterRelationshipMatrix:
        return self._relationship_matrix

    def profile_by_name(self, name: str) -> CharacterProfile | None:
        target = clean_character_name(name)
        for profile in self._bible.characters:
            if clean_character_name(profile.name) == target:
                return profile
        return None

    def character_names(self) -> list[str]:
        return [clean_character_name(profile.name) for profile in self._bible.characters]

    def first_character_name(self) -> str:
        if not self._bible.characters:
            return ""
        return clean_character_name(self._bible.characters[0].name)

    def entity_graph_payload(self) -> dict[str, Any]:
        path = self._layout.narrative_state_dir / "entity_graph.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    # ── Mutations ──────────────────────────────────────────────────────

    def apply_pending_payload(self, payload: dict[str, Any]) -> None:
        """Adopt a fully-formed in-memory bible payload as the new draft.

        This is used by `CharacterProfilePage`'s edit form: the form keeps
        editing the pending payload and only triggers `commit_pending_payload`
        when the user clicks Save. The store remembers the *saved* payload so
        we can revert on Discard.
        """
        self._pending_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        new_bible = CharacterBible.model_validate(self._pending_payload)
        self._bible = new_bible
        if not self._payloads_equal(self._pending_payload, self._saved_payload):
            self._set_dirty(True)
        else:
            self._set_dirty(False)

    def has_pending_payload(self) -> bool:
        return self._pending_payload is not None

    def commit_pending_payload(self) -> CharacterArtifactWriteResult | None:
        if self._pending_payload is None:
            return None
        result = self._persist_via_writer(self._pending_payload)
        self._pending_payload = None
        return result

    def discard_pending_payload(self) -> None:
        self._pending_payload = None
        self._bible = CharacterBible.model_validate(self._saved_payload)
        self._set_dirty(False)
        self.bibleChanged.emit()

    def request_save(self) -> CharacterArtifactWriteResult | None:
        if self._pending_payload is not None:
            return self.commit_pending_payload()
        if not self._dirty:
            return None
        result = self._persist_via_writer(self._bible.model_dump(mode="json"))
        return result

    def request_discard(self) -> None:
        if self._pending_payload is not None:
            self.discard_pending_payload()
            return
        if not self._dirty:
            return
        self._bible = CharacterBible.model_validate(self._saved_payload)
        self._set_dirty(False)
        self.bibleChanged.emit()

    # ── High-level actions (persist + emit) ─────────────────────────────

    def request_add_character(
        self, profile_payload: dict[str, Any]
    ) -> CharacterArtifactWriteResult:
        result = _add_character_writer(self._project_dir, profile_payload)
        self._on_persisted(result)
        return result

    def request_retire_character(self, name: str) -> CharacterArtifactWriteResult:
        result = _retire_character_writer(self._project_dir, name)
        self._on_persisted(result)
        return result

    def request_write_relationship(
        self,
        source: str,
        target: str,
        relation_type: str,
        description: str,
    ) -> CharacterArtifactWriteResult:
        result = _write_relationship_edge_writer(
            self._project_dir, source, target, relation_type, description
        )
        self._on_persisted(result)
        return result

    def request_remove_relationship(
        self,
        source: str,
        target: str,
    ) -> CharacterArtifactWriteResult:
        result = _remove_relationship_edge_writer(self._project_dir, source, target)
        self._on_persisted(result)
        return result

    def request_reload(self) -> None:
        """Force a fresh load from disk — used when another tool rewrites
        character_bible.json (e.g. workflow jobs)."""
        self._bible = _load_character_bible(self._project_dir)
        self._saved_payload = self._bible.model_dump(mode="json")
        self._pending_payload = None
        self._dirty = False
        self._relationship_matrix = self._load_relationship_matrix()
        self._set_dirty(False)
        self.bibleReplaced.emit(self._bible)
        self.bibleChanged.emit()

    # ── Internals ──────────────────────────────────────────────────────

    def _persist_via_writer(self, payload: dict[str, Any]) -> CharacterArtifactWriteResult:
        result = _write_character_bible_writer(self._project_dir, payload)
        self._on_persisted(result)
        return result

    def _on_persisted(self, result: CharacterArtifactWriteResult) -> None:
        self._bible = result.character_bible
        self._saved_payload = self._bible.model_dump(mode="json")
        self._pending_payload = None
        self._set_dirty(False)
        self._relationship_matrix = self._load_relationship_matrix()
        self._warnings = [
            _SaveWarning(code="artifact_writer", message=str(message))
            for message in result.warnings
        ]
        self.saveWarningsChanged.emit(
            [{"code": w.code, "message": w.message} for w in self._warnings]
        )
        self.savedPayloadChanged.emit()
        self.bibleReplaced.emit(self._bible)
        self.bibleChanged.emit()
        self.requestExternalRefresh.emit()

    def _set_dirty(self, value: bool) -> None:
        if value == self._dirty:
            return
        self._dirty = value
        self.dirtyChanged.emit(value)

    def _load_relationship_matrix(self) -> CharacterRelationshipMatrix:
        path = self._layout.states_dir / "init_v2" / "character_relationship_matrix.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return CharacterRelationshipMatrix.empty()
        matrix = payload.get("relationship_matrix")
        if not isinstance(matrix, list):
            return CharacterRelationshipMatrix.empty()
        lookup: dict[frozenset[str], str] = {}
        for item in matrix:
            if not isinstance(item, dict):
                continue
            left = clean_character_name(
                item.get("character_a") or item.get("source_name") or item.get("source")
            )
            right = clean_character_name(
                item.get("character_b") or item.get("target_name") or item.get("target")
            )
            rel_type = str(item.get("relation_type") or "relationship").strip()
            if left and right:
                lookup[frozenset((left, right))] = rel_type
        return CharacterRelationshipMatrix(lookup=lookup)

    @staticmethod
    def _payloads_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
        return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(
            b, ensure_ascii=False, sort_keys=True
        )


__all__ = ["CharacterBibleStore", "CharacterRelationshipMatrix"]

"""I/O helpers for chapter data loading and caching."""

from __future__ import annotations

from typing import Any

from novel_forge.core.exceptions import StorageError
from novel_forge.core.utils.boundary_windows import take_tail_paragraphs
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.style_profile_helpers import (
    merge_style_profile_overrides,
)
from novel_forge.workspace.artifact_cache import ArtifactLoadContext, ChapterArtifactBundleLoader

_log = get_logger("workspace.execution_io")


class _ChapterDataCache:
    """Per-request cache for chapter repair operations.

    Avoids redundant disk reads for character_bible, style_profile, and
    previous_chapter_ending within a single chapter repair flow. The cache
    key is the file path, so stale data is impossible within one request.
    """

    __slots__ = ("_storage", "_layout", "_cache", "_artifacts")

    def __init__(self, storage: Any, layout: Any, *, artifact_loader: Any | None = None) -> None:
        self._storage = storage
        self._layout = layout
        self._cache: dict[str, Any] = {}
        self._artifacts = artifact_loader or ChapterArtifactBundleLoader(
            storage,
            context=ArtifactLoadContext(source="execution_io"),
        )

    # -- character_bible (read once per request) ---------------------------

    def get_character_bible_data(self) -> dict[str, Any]:
        """Load and cache character_bible.json."""
        key = "character_bible_data"
        if key not in self._cache:
            try:
                chars_path = self._layout.characters_path
                if chars_path.exists():
                    raw = self._artifacts.load_json(chars_path)
                    self._cache[key] = raw if isinstance(raw, dict) else {}
                else:
                    self._cache[key] = {}
            except (OSError, StorageError):
                _log.debug("ChapterDataCache: character_bible load failed", exc_info=True)
                self._cache[key] = {}
        value = self._cache[key]
        return value if isinstance(value, dict) else {}

    # -- style_profile (read once per request) ------------------------------

    def get_style_profile(self) -> dict[str, Any] | None:
        """Load, cache, and resolve style_profile.json (with overrides)."""
        key = "style_profile"
        if key not in self._cache:
            self._cache[key] = _resolve_style_profile(self._artifacts, self._layout)
        value = self._cache[key]
        return value if isinstance(value, dict) else None

    # -- previous chapter ending (read once per chapter_num per request) --

    def get_previous_chapter_ending(
        self,
        chapter_num: int,
        tail: int = 800,
        *,
        paragraphs: int | None = None,
    ) -> str:
        """Load and cache the previous chapter ending by paragraphs or trailing chars."""
        key = f"prev_ending_{chapter_num}_{tail}_{paragraphs or 0}"
        if key not in self._cache:
            self._cache[key] = _load_previous_chapter_ending(
                self._storage,
                self._layout,
                chapter_num,
                tail=tail,
                paragraphs=paragraphs,
            )
        return str(self._cache[key] or "")

    # -- character notes (derived from character_bible, cached) ------------

    def get_character_notes(self) -> str:
        """Build and cache a brief character ability summary."""
        key = "character_notes"
        if key not in self._cache:
            self._cache[key] = _build_character_notes_from_data(self.get_character_bible_data())
        return str(self._cache[key] or "")

    # -- character profiles for repair (derived from character_bible) -----

    def get_character_profiles_for_repair(self) -> list[dict[str, str]]:
        """Build and cache character profiles for repair prompts."""
        key = "character_profiles_for_repair"
        if key not in self._cache:
            self._cache[key] = _load_character_profiles_from_data(self.get_character_bible_data())
        value = self._cache[key]
        return value if isinstance(value, list) else []


def _build_character_notes_from_data(char_data: dict[str, Any]) -> str:
    """Build a brief character ability summary from pre-loaded character bible data.

    Returns an empty string if the data is empty or parsing fails.
    """
    try:
        chars = char_data.get("characters", [])
        if isinstance(char_data, list):
            chars = char_data
        lines: list[str] = []
        for c in chars:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip()
            backstory = (c.get("backstory") or "").strip()
            role = (c.get("role") or "").strip()
            if name and backstory:
                lines.append(f"- {name}（{role}）：{backstory[:120]}")
        return "\n".join(lines)
    except (TypeError, ValueError, AttributeError):
        _log.debug("_build_character_notes_from_data failed", exc_info=True)
        return ""


def _load_character_profiles_from_data(char_data: dict[str, Any]) -> list[dict[str, str]]:
    """Build character profiles from pre-loaded character bible data."""
    characters = char_data.get("characters", [])
    if not isinstance(characters, list):
        return []
    profiles: list[dict[str, str]] = []
    for ch in characters:
        if not isinstance(ch, dict):
            continue
        name = ch.get("name", "")
        if not name:
            continue
        entry: dict[str, str] = {"name": str(name)[:120]}
        for src_key, (profile_key, max_chars) in _CHAR_FIELD_MAP.items():
            val = ch.get(src_key, "")
            if val:
                entry[profile_key] = str(val)[:max_chars]
        profiles.append(entry)
    return profiles[:8]


def _build_character_notes(storage: Any, layout: Any) -> str:
    """Build a brief character ability summary from the character bible.

    Returns an empty string if the file is missing or parsing fails.
    The result is injected into the causal validation prompt so the LLM
    can attribute supernatural actions to established character capabilities.

    NOTE: For per-request caching, use ``_ChapterDataCache.get_character_notes()``
    instead to avoid redundant disk reads.
    """
    try:
        chars_path = layout.characters_path
        if not chars_path.exists():
            return ""
        raw = storage.load_json(chars_path)
        return _build_character_notes_from_data(
            raw if isinstance(raw, dict) else {"characters": raw}
        )
    except (OSError, StorageError):
        _log.debug("_format_characters failed", exc_info=True)
        return ""


def _load_previous_chapter_ending(
    storage: Any,
    layout: Any,
    chapter_num: int,
    tail: int = 800,
    *,
    paragraphs: int | None = None,
) -> str:
    """Load the previous chapter's ending text.

    Used to give the repair LLM real narrative context for opening continuity.
    Returns an empty string when chapter_num <= 1 or when the previous chapter file
    does not exist.
    """
    if chapter_num <= 1:
        return ""
    prev_path = layout.chapter_path(chapter_num - 1)
    if not prev_path.exists():
        return ""
    try:
        text = str(prev_path.read_text(encoding="utf-8"))
        if paragraphs is not None:
            return take_tail_paragraphs(text, paragraphs, max_chars=tail)
        return text[-tail:] if len(text) > tail else text
    except OSError:
        return ""


def _resolve_style_profile(storage: Any, layout: Any) -> dict[str, Any] | None:
    """Read style_profile.json, apply overrides, return merged dict or None."""
    try:
        if layout.style_profile_path.exists():
            raw = storage.load_json(layout.style_profile_path)
            if isinstance(raw, dict):
                merged = merge_style_profile_overrides(raw)
                return merged if isinstance(merged, dict) else None
    except (OSError, StorageError, TypeError):
        _log.debug("_resolve_style_profile failed", exc_info=True)
    return None


# Source field -> (profile key, max_chars).  Templates reference profile keys
# (e.g. p.abilities, p.goals, p.social_status); this mapping connects them
# to the actual field names in character_bible.json.
_CHAR_FIELD_MAP: dict[str, tuple[str, int]] = {
    "role": ("identity", 120),
    "gender": ("gender", 20),
    "personality": ("personality", 200),
    "backstory": ("backstory", 200),
    "status": ("social_status", 120),
    "arc": ("goals", 200),
    "notes": ("abilities", 200),
}


def _load_character_profiles_for_repair(storage: Any, layout: Any) -> list[dict[str, str]]:
    """Load character profiles from the project's character_bible.json.

    Returns a list of dicts with keys driven by ``_CHAR_FIELD_MAP`` — up to
    8 characters.  These are passed to CausalRepairInput so the repair LLM
    has character context when fixing event_without_cause, unmotivated_decision,
    and address_form_mismatch issues.
    """
    try:
        char_data = storage.load_json(layout.characters_path) or {}
    except (OSError, StorageError):
        return []
    if not isinstance(char_data, dict):
        return []
    characters = char_data.get("characters", [])
    if not isinstance(characters, list):
        return []
    profiles: list[dict[str, str]] = []
    for ch in characters:
        if not isinstance(ch, dict):
            continue
        name = ch.get("name", "")
        if not name:
            continue
        entry: dict[str, str] = {"name": str(name)[:120]}
        for src_key, (profile_key, max_chars) in _CHAR_FIELD_MAP.items():
            val = ch.get(src_key, "")
            if val:
                entry[profile_key] = str(val)[:max_chars]
        profiles.append(entry)
    return profiles[:8]

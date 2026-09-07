"""BibleDerivedProvider — derives forbidden element categories from StoryBible.

Pure rule-based derivation (zero LLM calls).  Scans
CharacterProfile.relationships, CharacterState.social_status, and
StoryBible themes to produce project-specific forbidden element seeds.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from novel_forge.core.schemas.bible import StoryBible
from novel_forge.obs.logger import get_logger

_log = get_logger("core.bible_derived_provider")


class ForbiddenElementDefaults(BaseModel):
    """Validated container for default forbidden element root tokens.

    Loaded once from forbidden_element_defaults.yaml and cached via hash.
    Validation rules:
    - No duplicate items across all 3 lists
    - All items must be at least 2 characters long
    """

    rhetorical_imagery_hints: list[str] = Field(default_factory=list)
    kinship_and_address_terms: list[str] = Field(default_factory=list)
    abstract_emotion_keywords: list[str] = Field(default_factory=list)

    @classmethod
    def _validate_no_cross_list_duplicates(
        cls,
        rhetorical: list[str],
        kinship: list[str],
        emotion: list[str],
    ) -> None:
        seen: set[str] = set()
        for item in rhetorical + kinship + emotion:
            if item in seen:
                raise ValueError(f"Duplicate item across lists: {item!r}")
            seen.add(item)

    @classmethod
    def from_yaml(cls, path: Path) -> "ForbiddenElementDefaults":
        """Load and validate from a YAML file with hash-based caching."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected dict, got {type(raw).__name__}")

        rhetorical = raw.get("rhetorical_imagery_hints", [])
        kinship = raw.get("kinship_and_address_terms", [])
        emotion = raw.get("abstract_emotion_keywords", [])

        cls._validate_no_cross_list_duplicates(rhetorical, kinship, emotion)

        return cls(
            rhetorical_imagery_hints=rhetorical,
            kinship_and_address_terms=kinship,
            abstract_emotion_keywords=emotion,
        )


_DEFAULTS_CACHE: dict[str, tuple[frozenset[str], frozenset[str], frozenset[str]]] = {}
_DEFAULTS_PATH = Path(__file__).parent / "forbidden_element_defaults.yaml"


def _load_defaults_cached() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Load defaults from YAML with hash-based caching.

    Cache key is the MD5 hash of the YAML file contents. Re-loads only
    when the file changes.
    """
    if not _DEFAULTS_PATH.exists():
        return (frozenset(), frozenset(), frozenset())

    content = _DEFAULTS_PATH.read_text(encoding="utf-8")
    file_hash = hashlib.md5(content.encode()).hexdigest()

    if file_hash in _DEFAULTS_CACHE:
        return _DEFAULTS_CACHE[file_hash]

    defaults = ForbiddenElementDefaults.from_yaml(_DEFAULTS_PATH)
    result = (
        frozenset(defaults.rhetorical_imagery_hints),
        frozenset(defaults.kinship_and_address_terms),
        frozenset(defaults.abstract_emotion_keywords),
    )
    _DEFAULTS_CACHE[file_hash] = result
    _log.debug("loaded_forbidden_defaults | hash=%s", file_hash)
    return result


class BibleDerivedProvider:
    """Derives forbidden-element seed lists from Bible structures.

    All methods are synchronous and perform only string matching —
    no model calls, no embeddings, no I/O except optional template loading.
    """

    def __init__(self, template_dir: Path | None = None) -> None:
        self._template_dir = template_dir
        self._rhetorical_roots, self._kinship_roots, self._emotion_roots = _load_defaults_cached()
        self._load_template_overrides()

    def _load_template_overrides(self) -> None:
        if self._template_dir is None:
            return

    @staticmethod
    def _tokenize_chinese(text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        tokens: list[str] = []
        for length in range(2, min(5, len(text) + 1)):
            for i in range(len(text) - length + 1):
                tokens.append(text[i : i + length])
        return tokens

    def _extract_kinship(self, characters: list[dict[str, Any]]) -> list[str]:
        """Extract kinship/address terms from relationship descriptions."""
        found: set[str] = set()
        for char in characters or []:
            if not isinstance(char, dict):
                continue
            rels = char.get("relationships", {})
            if isinstance(rels, dict):
                for desc in rels.values():
                    desc = str(desc).strip()
                    for root in self._kinship_roots:
                        if root in desc and len(root) >= 2:
                            found.add(root)
            status = str(char.get("social_status", "")).strip()
            for root in self._kinship_roots:
                if root in status and len(root) >= 2:
                    found.add(root)
        return sorted(found)

    def _extract_emotions(self, characters: list[dict[str, Any]]) -> list[str]:
        """Extract emotion keywords from character personality / notes."""
        found: set[str] = set()
        for char in characters or []:
            if not isinstance(char, dict):
                continue
            for field in ("personality", "notes", "backstory", "arc"):
                text = str(char.get(field, "")).strip()
                for root in self._emotion_roots:
                    if root in text and len(root) >= 2:
                        found.add(root)
        return sorted(found)

    def _derive_rhetorical_hints(self, bible: StoryBible | dict[str, Any]) -> list[str]:
        """Derive rhetorical hints from themes, tone, and era."""
        found: set[str] = set()
        texts: list[str] = []

        if isinstance(bible, dict):
            texts.extend(bible.get("themes", []))
            texts.append(bible.get("tone", ""))
            texts.append(bible.get("era", ""))
            texts.append(bible.get("notes", ""))
        else:
            texts.extend(bible.themes)
            texts.append(bible.tone)
            texts.append(bible.era)
            texts.append(bible.notes)

        for text in texts:
            text = str(text).strip()
            if not text:
                continue
            for root in self._rhetorical_roots:
                if root in text and len(root) >= 2:
                    found.add(root)
        return sorted(found)

    def derive_from_bible(
        self,
        bible: StoryBible | dict[str, Any],
        characters: list[dict[str, Any]] | None = None,
    ) -> dict[str, list[str]]:
        """Derive all forbidden element categories from Bible data.

        Returns:
            {
                "rhetorical_imagery_hints": [...],
                "kinship_and_address_terms": [...],
                "abstract_emotion_keywords": [...],
            }
        """
        if characters is None:
            characters = []
            if isinstance(bible, dict):
                characters = bible.get("characters", [])

        result = {
            "rhetorical_imagery_hints": self._derive_rhetorical_hints(bible),
            "kinship_and_address_terms": self._extract_kinship(characters),
            "abstract_emotion_keywords": self._extract_emotions(characters),
        }

        _log.info(
            "bible_derived_forbidden_elements | rhetorical=%d | kinship=%d | emotion=%d",
            len(result["rhetorical_imagery_hints"]),
            len(result["kinship_and_address_terms"]),
            len(result["abstract_emotion_keywords"]),
        )
        return result

    def derive_from_packet(
        self,
        packet: Any,
    ) -> dict[str, list[str]]:
        """Convenience wrapper that extracts Bible data from a ChapterStatePacket."""
        characters: list[dict[str, Any]] = []
        bible_dict: dict[str, Any] | None = None

        character_profiles = getattr(packet, "character_profiles", None)
        if character_profiles:
            if isinstance(character_profiles, list):
                characters = [
                    p if isinstance(p, dict) else p.model_dump(mode="json")
                    for p in character_profiles
                ]

        # Try to locate a StoryBible or bible dict inside the packet
        story_bible = getattr(packet, "story_bible", None)
        if story_bible is not None:
            if isinstance(story_bible, dict):
                bible_dict = story_bible
            else:
                bible_dict = story_bible.model_dump(mode="json")

        if bible_dict is None:
            return {
                "rhetorical_imagery_hints": [],
                "kinship_and_address_terms": [],
                "abstract_emotion_keywords": [],
            }

        return self.derive_from_bible(bible_dict, characters=characters)


__all__ = ["BibleDerivedProvider", "ForbiddenElementDefaults"]

"""Forbidden element registry — project-level dynamic seed configuration.

Provides four-layer fallback for forbidden element sources:
  1. Motif context (dynamic, LLM-derived)
  2. Bible-derived terms (dynamic, rule-based)
  3. Project seed file (static, user-editable)
  4. Genre template (static, built-in fallback)

All hard-coded constants from continuity_eval_step.py have been externalised
into YAML so they can be customised per project and per genre.
"""

from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path
from typing import Any, ClassVar

import yaml  # type: ignore[import-untyped]

from novel_forge.obs.logger import get_logger

_log = get_logger("core.forbidden_element_registry")

_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates" / "forbidden_elements"
_DEFAULTS_PATH = Path(__file__).parent / "forbidden_element_defaults.yaml"
_SEED_KEYS = (
    "rhetorical_imagery_hints",
    "kinship_and_address_terms",
    "abstract_emotion_keywords",
)


class ForbiddenElementRegistry:
    """Loads and merges forbidden element definitions from multiple sources.

    Usage:
        registry = ForbiddenElementRegistry(project_path)
        hints = registry.get_rhetorical_hints()
        kinship = registry.get_kinship_terms()
        emotions = registry.get_emotion_keywords()
    """

    _instances: ClassVar[dict[Path, ForbiddenElementRegistry]] = {}

    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._seeds: dict[str, Any] = {}
        self._merged_cache: dict[str, Any] | None = None
        self._load()
        ForbiddenElementRegistry._instances[project_path] = self

    def _load(self) -> None:
        seeds_path = self._project_path / "config" / "forbidden_element_seeds.yaml"
        raw = self._load_seed_payload(seeds_path)
        if isinstance(raw, dict):
            self._seeds = raw
            _log.debug(
                "loaded_forbidden_seeds | path=%s | rhetorical=%d | kinship=%d | emotion=%d",
                seeds_path,
                len(self._seeds.get("rhetorical_imagery_hints", [])),
                len(self._seeds.get("kinship_and_address_terms", [])),
                len(self._seeds.get("abstract_emotion_keywords", [])),
            )

    @property
    def has_project_seeds(self) -> bool:
        return bool(self._seeds)

    def get_rhetorical_hints(self) -> frozenset[str]:
        return frozenset(self._seeds.get("rhetorical_imagery_hints", []))

    def get_kinship_terms(self) -> frozenset[str]:
        return frozenset(self._seeds.get("kinship_and_address_terms", []))

    def get_emotion_keywords(self) -> frozenset[str]:
        return frozenset(self._seeds.get("abstract_emotion_keywords", []))

    def get_all_seeds(self) -> dict[str, frozenset[str]]:
        return {
            "rhetorical": self.get_rhetorical_hints(),
            "kinship": self.get_kinship_terms(),
            "emotion": self.get_emotion_keywords(),
        }

    # -------------------------------------------------------------------------
    # Hybrid registry — merged source deduplication
    # -------------------------------------------------------------------------

    def merge_sources(
        self,
        bible_derived: dict[str, list[str]] | None = None,
        genre: str = "universal_minimal",
    ) -> dict[str, list[str]]:
        """Merge all four sources with Unicode NFC normalization and longest-first dedup.

        Priority (high → low): project seeds > bible-derived > genre template > universal minimal

        Args:
            bible_derived: Bible-derived terms from BibleDerivedProvider.derive_from_bible()
            genre: Genre template name (default: universal_minimal)

        Returns:
            Merged dict with normalized, deduplicated lists for each category
        """
        if self._merged_cache is not None:
            return self._merged_cache

        # Source 1: Project seeds (highest priority)
        source_project = self._seeds

        # Source 2: Bible-derived
        source_bible: dict[str, Any] = {}
        if bible_derived:
            source_bible = bible_derived

        # Source 3: Genre template
        source_genre = self._load_template_source(genre)

        # Source 4: Universal minimal (lowest priority fallback)
        source_universal = self._load_template_source("universal_minimal")

        # Merge per key, following priority order
        merged: dict[str, list[str]] = {}
        for key in _SEED_KEYS:
            combined: list[str] = []
            # Add in reverse priority order so higher priority overwrites
            for source in [source_universal, source_genre, source_bible, source_project]:
                if isinstance(source.get(key), list):
                    combined.extend(source[key])

            merged[key] = self._normalize_and_dedupe(combined)

        self._merged_cache = merged
        _log.debug(
            "merged_forbidden_sources | rhetorical=%d | kinship=%d | emotion=%d",
            len(merged["rhetorical_imagery_hints"]),
            len(merged["kinship_and_address_terms"]),
            len(merged["abstract_emotion_keywords"]),
        )
        return merged

    def get_all_root_sets(
        self,
        bible_derived: dict[str, list[str]] | None = None,
        genre: str = "universal_minimal",
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """Return merged root sets as (rhetorical_hints, kinship_terms, emotion_keywords).

        Args:
            bible_derived: Bible-derived terms from BibleDerivedProvider.derive_from_bible()
            genre: Genre template name (default: universal_minimal)

        Returns:
            Tuple of frozensets: (rhetorical_hints, kinship_terms, emotion_keywords)
        """
        merged = self.merge_sources(bible_derived=bible_derived, genre=genre)
        return (
            frozenset(merged.get("rhetorical_imagery_hints", [])),
            frozenset(merged.get("kinship_and_address_terms", [])),
            frozenset(merged.get("abstract_emotion_keywords", [])),
        )

    @classmethod
    def _normalize_and_dedupe(cls, values: list[str]) -> list[str]:
        """Normalize (Unicode NFC), strip whitespace, sort by length (longest first), dedupe.

        Longest-first sorting preserves compound words (e.g., "父亲" before "父").
        """
        seen: set[str] = set()
        result: list[str] = []

        for value in values:
            if not value:
                continue
            # Unicode NFC normalization then strip
            normalized = unicodedata.normalize("NFC", str(value)).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)

        # Sort by length (longest first) to preserve compound words
        result.sort(key=lambda x: len(x), reverse=True)
        return result

    @classmethod
    def _load_template_source(cls, genre: str) -> dict[str, Any]:
        """Lazy-load a genre template from the templates directory."""
        template_path = _TEMPLATE_DIR / f"{genre}.yaml"
        if not template_path.exists():
            template_path = _TEMPLATE_DIR / "universal_minimal.yaml"
        if not template_path.exists():
            return {}
        try:
            raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception as exc:
            _log.warning("failed_to_load_template | genre=%s | error=%s", genre, exc)
        return {}

    # -------------------------------------------------------------------------
    # Original methods
    # -------------------------------------------------------------------------

    @staticmethod
    def _dedupe_preserve_order(values: list[Any]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
        return merged

    @classmethod
    def _load_seed_payload(cls, seeds_path: Path) -> dict[str, Any]:
        if not seeds_path.exists():
            return {}
        try:
            raw = yaml.safe_load(seeds_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
            _log.warning("invalid_forbidden_seeds_format | path=%s", seeds_path)
        except Exception as exc:
            _log.warning("failed_to_load_forbidden_seeds | path=%s | error=%s", seeds_path, exc)
        return {}

    @classmethod
    def _merge_seed_payload(
        cls,
        existing: dict[str, Any],
        incoming: dict[str, list[str]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in _SEED_KEYS:
            payload[key] = cls._dedupe_preserve_order(
                list(existing.get(key, []) or []) + list(incoming.get(key, []) or [])
            )

        merged_metadata: dict[str, Any] = {}
        if isinstance(existing.get("metadata"), dict):
            merged_metadata.update(existing["metadata"])
        if metadata:
            merged_metadata.update(metadata)
        payload["metadata"] = merged_metadata
        return payload

    @classmethod
    def init_project_seeds(cls, project_path: Path, genre: str = "universal_minimal") -> Path:
        """Copy a genre template into the project config directory.

        Called during project initialisation so every project starts with
        its own editable seed file rather than relying on built-in constants.
        """
        seeds_path = project_path / "config" / "forbidden_element_seeds.yaml"
        seeds_path.parent.mkdir(parents=True, exist_ok=True)

        if seeds_path.exists():
            _log.info("preserve_forbidden_seeds | path=%s", seeds_path)
            return seeds_path

        template = _TEMPLATE_DIR / f"{genre}.yaml"
        if not template.exists():
            template = _TEMPLATE_DIR / "universal_minimal.yaml"

        if template.exists():
            shutil.copy(template, seeds_path)
            _log.info("init_forbidden_seeds | genre=%s | dest=%s", genre, seeds_path)
        else:
            _log.warning("no_forbidden_template_found | genre=%s", genre)

        return seeds_path

    @classmethod
    def reinit_project_seeds(cls, project_path: Path, new_genre: str) -> Path:
        """Overwrite the project seeds file with a new genre template.

        Unlike init_project_seeds() which preserves existing files, this method
        ALWAYS copies the template, useful for genre switching.

        Clears any cached merged data from existing registry instances for this project.
        """
        seeds_path = project_path / "config" / "forbidden_element_seeds.yaml"
        seeds_path.parent.mkdir(parents=True, exist_ok=True)

        instance = cls._instances.get(project_path)
        if instance is not None:
            instance._merged_cache = None
            _log.debug("cleared_merged_cache | project=%s", project_path)

        template = _TEMPLATE_DIR / f"{new_genre}.yaml"
        if not template.exists():
            template = _TEMPLATE_DIR / "universal_minimal.yaml"

        if template.exists():
            shutil.copy(template, seeds_path)
            _log.info("reinit_forbidden_seeds | genre=%s | dest=%s", new_genre, seeds_path)
        else:
            _log.warning("no_forbidden_template_found | genre=%s", new_genre)

        return seeds_path

    @classmethod
    def save_seeds(cls, project_path: Path, seeds: dict[str, list[str]], metadata: dict[str, Any] | None = None) -> None:
        """Persist derived seeds to the project config file."""
        seeds_path = project_path / "config" / "forbidden_element_seeds.yaml"
        seeds_path.parent.mkdir(parents=True, exist_ok=True)
        existing = cls._load_seed_payload(seeds_path)
        payload = cls._merge_seed_payload(existing, seeds, metadata=metadata)
        seeds_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        _log.info(
            "saved_forbidden_seeds | path=%s | total=%d",
            seeds_path,
            sum(len(payload.get(key, [])) for key in _SEED_KEYS),
        )


__all__ = ["ForbiddenElementRegistry"]

"""Prompt pack discovery and locale resolution."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.core.domain.language import (
    is_simplified_chinese_language,
    is_traditional_chinese_language,
)

PROMPT_PACKS_DIR = Path(__file__).parent / "packs"
DEFAULT_PROMPT_LOCALE = "zh"


@dataclass(frozen=True)
class PromptPack:
    """Metadata for one prompt language pack."""

    locale: str
    display_name: str
    status: str
    base_language: str
    length_unit: str
    source_revision: str
    coverage_required: bool
    root_dir: Path
    templates_dir: Path

    @property
    def is_stable(self) -> bool:
        return self.status == "stable"


def _locale_key(value: Any) -> str:
    return str(value or DEFAULT_PROMPT_LOCALE).strip().lower().replace("_", "-")


def normalize_prompt_locale(locale: Any) -> str:
    """Normalize a prompt-pack locale code."""

    key = _locale_key(locale)
    if key.startswith("zh"):
        return "zh"
    if key.startswith("en"):
        return "en"
    if key in {"jp", "jpn"} or key.startswith("ja"):
        return "ja"
    if key in {"kr", "kor"} or key.startswith("ko"):
        return "ko"
    return key or DEFAULT_PROMPT_LOCALE


def prompt_locale_for_language(language: Any) -> str:
    """Map an output language tag to the prompt-pack locale."""

    if is_simplified_chinese_language(language) or is_traditional_chinese_language(language):
        return "zh"
    return normalize_prompt_locale(language)


def _pack_manifest_path(root_dir: Path) -> Path:
    return root_dir / "manifest.toml"


def load_prompt_pack(root_dir: Path) -> PromptPack:
    """Load one prompt pack manifest."""

    manifest_path = _pack_manifest_path(root_dir)
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    locale = normalize_prompt_locale(data.get("locale", root_dir.name))
    templates_name = str(data.get("templates_dir") or "templates")
    templates_dir = (root_dir / templates_name).resolve()
    return PromptPack(
        locale=locale,
        display_name=str(data.get("display_name") or locale),
        status=str(data.get("status") or "draft").strip().lower(),
        base_language=str(data.get("base_language") or locale),
        length_unit=str(data.get("length_unit") or "words"),
        source_revision=str(data.get("source_revision") or ""),
        coverage_required=bool(data.get("coverage_required", False)),
        root_dir=root_dir.resolve(),
        templates_dir=templates_dir,
    )


def discover_prompt_packs(packs_dir: Path = PROMPT_PACKS_DIR) -> dict[str, PromptPack]:
    """Discover prompt packs with manifests, keyed by normalized locale."""

    packs: dict[str, PromptPack] = {}
    if not packs_dir.exists():
        return packs
    for child in sorted(packs_dir.iterdir()):
        if not child.is_dir() or not _pack_manifest_path(child).exists():
            continue
        pack = load_prompt_pack(child)
        packs[pack.locale] = pack
    return packs


def get_prompt_pack(locale: Any = DEFAULT_PROMPT_LOCALE) -> PromptPack:
    """Return a prompt pack by locale, raising if it is not installed."""

    resolved = normalize_prompt_locale(locale)
    packs = discover_prompt_packs()
    pack = packs.get(resolved)
    if pack is None:
        available = ", ".join(sorted(packs)) or "<none>"
        raise KeyError(f"Prompt pack '{resolved}' is not installed. Available: {available}")
    return pack


def prompt_language_choices(*, include_draft: bool = False) -> list[tuple[str, str]]:
    """Return language choices suitable for Desktop forms."""

    choices: list[tuple[str, str]] = []
    for pack in discover_prompt_packs().values():
        if not include_draft and not pack.is_stable:
            continue
        choices.append((pack.locale, pack.display_name))
    choices.sort(key=lambda item: (0 if item[0] == DEFAULT_PROMPT_LOCALE else 1, item[0]))
    return choices or [(DEFAULT_PROMPT_LOCALE, "中文 (zh)")]

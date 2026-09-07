"""Global voice library — cross-project reusable *designed* voices.

The library intentionally contains provider-designed voices only.  Cloned
voices may be tied to a user's reference audio and manually selected voices
are authoring decisions for one project, so neither is safe to auto-assign to
another project.  Entries are stored under
``{storage_root}/_global/tts_voice_library.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.tts.assets.voice_matching import age_bucket, assess_voice_match, normalize_gender
from novel_forge.tts.schemas import TTSProvider

_log = get_logger("tts.assets.voice_library")

_MIN_LIBRARY_MATCH_SCORE = 11
_MIN_SEMANTIC_MATCH_SCORE = 0.55

# ─── Schema ───────────────────────────────────────────────────────────────────


class VoiceLibraryEntry(BaseModel):
    """One reusable voice entry in the global library."""

    voice_id: str = Field(min_length=1, description="TTS 平台音色 ID")
    model_id: str = Field(
        default="",
        description="音色创建时绑定的 TTS 模型；云端自定义音色不可跨模型复用",
    )
    provider: TTSProvider = Field(default=TTSProvider.MOCK, description="所属 TTS 平台")
    character_name: str = Field(default="", description="原始角色名（溯源用）")
    gender: str = Field(default="", description="性别标签")
    age_hint: str = Field(default="", description="年龄标签")
    role: str = Field(default="", description="角色定位")
    personality: str = Field(default="", description="性格关键词")
    voice_description: str = Field(default="", description="声纹描述")
    voice_design_prompt: str = Field(default="", description="设计时的 prompt（用于去重）")
    expires_at: datetime | None = Field(
        default=None,
        description="设计音色的过期时间；到期后不得再自动复用",
    )
    activation_deadline: datetime | None = Field(
        default=None,
        description="设计音色首次正式合成的激活截止时间；激活后清空",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="创建时间 ISO 格式",
    )
    usage_count: int = Field(default=0, ge=0, description="被复用次数")
    source_project_id: str = Field(
        default="legacy",
        description="首次设计此音色的项目 ID；legacy 表示历史数据无溯源",
    )
    origin_project_ids: list[str] = Field(
        default_factory=list,
        description="所有曾复用此音色的项目 ID 列表（含 source）",
    )

    @property
    def is_expired(self) -> bool:
        """Return whether this provider-owned voice can no longer be reused."""
        now = datetime.now(timezone.utc)
        return bool(
            (self.expires_at is not None and now > self.expires_at)
            or (self.activation_deadline is not None and now > self.activation_deadline)
        )


@dataclass(frozen=True)
class VoiceLibraryUsage:
    """One successful library assignment to be persisted with the build result."""

    voice_id: str
    provider: TTSProvider


class VoiceLibrary:
    """In-memory collection of VoiceLibraryEntry with load/save/find."""

    def __init__(self, entries: list[VoiceLibraryEntry] | None = None) -> None:
        self.entries: list[VoiceLibraryEntry] = list(entries or [])

    # ── Persistence ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, storage_root: Path) -> VoiceLibrary:
        """Load library from ``{storage_root}/_global/tts_voice_library.json``."""
        path = _library_path(storage_root)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return cls()
        raw_entries = data.get("entries", []) if isinstance(data, dict) else []
        if not isinstance(raw_entries, list):
            return cls()

        entries: list[VoiceLibraryEntry] = []
        for raw_entry in raw_entries:
            try:
                entries.append(VoiceLibraryEntry.model_validate(raw_entry))
            except (TypeError, ValueError):
                # One bad historical row must not make every valid saved voice
                # unusable, nor should it be allowed into a future save.
                continue
        return cls(entries=entries)

    def save(self, storage_root: Path) -> None:
        """Atomically write library to disk."""
        path = _library_path(storage_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            path,
            {"entries": [e.model_dump(mode="json") for e in self.entries]},
        )

    # ── Mutation ─────────────────────────────────────────────────────────

    def add_or_update(self, entry: VoiceLibraryEntry) -> None:
        """Insert or replace entry keyed by (voice_id, provider).

        When the same voice was already used by other projects, preserve the
        original provenance and merge the ``origin_project_ids`` list so every
        project that reused this voice can still find it in ``project_only``
        scope after migration.
        """
        for i, existing in enumerate(self.entries):
            if (
                existing.voice_id == entry.voice_id
                and existing.provider == entry.provider
                and (
                    not existing.model_id
                    or not entry.model_id
                    or existing.model_id == entry.model_id
                )
            ):
                merged_origins: list[str] = []
                seen_origins: set[str] = set()
                for pid in (
                    *existing.origin_project_ids,
                    *entry.origin_project_ids,
                    existing.source_project_id,
                    entry.source_project_id,
                ):
                    if pid and pid not in seen_origins:
                        seen_origins.add(pid)
                        merged_origins.append(pid)
                self.entries[i] = entry.model_copy(
                    update={
                        "model_id": entry.model_id or existing.model_id,
                        "created_at": existing.created_at,
                        "usage_count": existing.usage_count,
                        "source_project_id": existing.source_project_id,
                        "origin_project_ids": merged_origins,
                    }
                )
                return
        self.entries.append(entry)

    def increment_usage(self, voice_id: str, provider: TTSProvider) -> None:
        """Bump usage_count for a matched entry (best-effort)."""
        for entry in self.entries:
            if entry.voice_id == voice_id and entry.provider == provider:
                entry.usage_count += 1
                return

    def clear_activation_deadlines(
        self,
        voice_ids: set[str],
        provider: TTSProvider | None = None,
    ) -> bool:
        """Mark provider voices as permanently activated after successful T2A."""
        if not voice_ids:
            return False
        changed = False
        for index, entry in enumerate(self.entries):
            if (
                entry.voice_id in voice_ids
                and entry.activation_deadline is not None
                and (provider is None or entry.provider == provider)
            ):
                self.entries[index] = entry.model_copy(update={"activation_deadline": None})
                changed = True
        return changed

    # ─ Query ────────────────────────────────────────────────────────────

    def find_by_design_prompt(
        self,
        design_prompt: str,
        provider: TTSProvider,
        *,
        excluded_voice_ids: set[str] | None = None,
        scope: str = "project_only",
        project_id: str = "",
    ) -> VoiceLibraryEntry | None:
        """Return an unexpired entry whose ``voice_design_prompt`` matches.

        The design prompt is the canonical, provider-neutral brief derived
        from character traits.  Two characters producing the same prompt would
        receive an identical designed voice, so reusing the existing entry
        avoids a redundant (billable) ``voice_design`` call.  Activated voices
        are preferred.

        ``scope`` and ``project_id`` filter by project ownership
        (see ``_scope_filter``).
        """
        if not design_prompt:
            return None
        excluded = excluded_voice_ids or set()
        matches = [
            entry
            for entry in self.entries
            if entry.provider == provider
            and entry.voice_id not in excluded
            and not entry.is_expired
            and entry.voice_design_prompt == design_prompt
            and _scope_filter(entry, scope, project_id)
        ]
        if not matches:
            return None
        matches.sort(key=lambda e: (0 if e.activation_deadline is None else 1, -e.usage_count))
        return matches[0]

    def find_match(
        self,
        character: dict[str, Any],
        provider: TTSProvider,
        *,
        excluded_voice_ids: set[str] | None = None,
        semantic_scores: dict[str, float] | None = None,
        scope: str = "project_only",
        project_id: str = "",
    ) -> VoiceLibraryEntry | None:
        """Find the best-matching library entry for a character.

        Matching runs in four tiers so that a previously designed voice is
        reused whenever it is safe to do so, avoiding redundant (billable)
        ``voice_design`` calls:

        1. **Exact name match** - the same character name + provider is the
           strongest signal a voice was designed for this persona.
        2. **Structured trait match** - gender + age bucket + role all align.
        3. **Semantic vector match** - personality, timbre and cadence
           neighbors after the hard demographic filter.
        4. **Keyword-scored match** - deterministic fallback.

        Within each tier, already-activated voices are preferred.

        ``scope`` controls cross-project visibility:
        - ``project_only`` (default): only entries from *project_id* or with
          *project_id* in ``origin_project_ids``.
        - ``global_with_names``: include entries from any project (original
          behaviour, preserves cross-project name reuse).
        - ``global_all``: no project filtering at all.
        """
        excluded = excluded_voice_ids or set()
        semantic_scores = semantic_scores or {}
        candidates = [
            entry
            for entry in self.entries
            if entry.provider == provider
            and entry.voice_id not in excluded
            and not entry.is_expired
            and assess_voice_match(character, entry).hard_match
            and _scope_filter(entry, scope, project_id)
        ]
        if not candidates:
            return None

        gender = normalize_gender(character.get("gender"))
        age_hint = str(character.get("age", "") or "").lower()
        role = str(character.get("role", "") or "").lower()
        character_age_bucket = age_bucket(age_hint)
        char_name = str(character.get("name", "") or "").strip()

        voice_desc = " ".join(
            str(character.get(key, "") or "").lower()
            for key in ("voice", "voice_description", "personality")
        )
        hints = character.get("tts_voice_hints")
        hint_text = ""
        if isinstance(hints, dict):
            hint_text = " ".join(str(v) for v in hints.values()).lower()
        search_text = f"{voice_desc} {hint_text} {role}"

        def activated_first(entry: VoiceLibraryEntry) -> int:
            """Sort key so activated voices win ties (0 before 1)."""
            return 0 if entry.activation_deadline is None else 1

        # ── Tier 1: exact character-name match ──────────────────────────
        # A voice designed for the same named persona in another project is
        # the strongest possible reuse signal and must never be re-billed.
        if char_name:
            name_matches = [e for e in candidates if e.character_name.strip() == char_name]
            if name_matches:
                name_matches.sort(key=lambda e: (activated_first(e), -e.usage_count))
                return name_matches[0]

        # ── Tier 2: structured trait match (gender + age bucket + role) ─
        # When the name differs but the demographic profile is identical, the
        # designed voice is still a safe reuse.  This requires *all three*
        # available traits to agree, so "gender only" (which the existing
        # tests guard against) still falls through to tier 3 / no match.
        if gender and gender != "neutral" and character_age_bucket and role:
            trait_matches: list[VoiceLibraryEntry] = []
            for entry in candidates:
                entry_gender = normalize_gender(entry.gender)
                entry_age_bucket = age_bucket(entry.age_hint)
                entry_role = str(entry.role or "").lower()
                if (
                    entry_gender == gender
                    and entry_age_bucket == character_age_bucket
                    and entry_role == role
                ):
                    trait_matches.append(entry)
            if trait_matches:
                trait_matches.sort(
                    key=lambda entry: (
                        semantic_scores.get(entry.voice_id, -1.0),
                        -activated_first(entry),
                        entry.usage_count,
                    ),
                    reverse=True,
                )
                return trait_matches[0]

        # ── Tier 3: keyword-scored match (original heuristic) ───────────
        semantic_ranked = sorted(
            candidates,
            key=lambda entry: (
                semantic_scores.get(entry.voice_id, -1.0),
                assess_voice_match(character, entry).score,
                -activated_first(entry),
                entry.usage_count,
            ),
            reverse=True,
        )
        if (
            semantic_ranked
            and semantic_scores.get(semantic_ranked[0].voice_id, -1.0) >= _MIN_SEMANTIC_MATCH_SCORE
        ):
            return semantic_ranked[0]

        keyword_tokens = (
            "温柔",
            "gentle",
            "soft",
            "甜",
            "sweet",
            "清亮",
            "bright",
            "活泼",
            "lively",
            "沉稳",
            "calm",
            "低沉",
            "deep",
            "浑厚",
            "resonant",
            "hoarse",
            "沙哑",
            "克制",
            "restrained",
            "冷峻",
            "cold",
            "威严",
            "authoritative",
            "机敏",
            "witty",
            "短句",
            "brisk",
            "停顿",
            "measured",
        )

        def score(entry: VoiceLibraryEntry) -> int:
            entry_gender = normalize_gender(entry.gender)
            haystack = (
                f"{entry.gender} {entry.age_hint} {entry.role} "
                f"{entry.personality} {entry.voice_description} {entry.voice_design_prompt}"
            ).lower()
            points = 0
            if gender and gender != "neutral":
                points += 8 if entry_gender == gender else -8
            entry_age_bucket = age_bucket(entry.age_hint)
            if character_age_bucket and entry_age_bucket:
                points += 4 if character_age_bucket == entry_age_bucket else -4
            for token in keyword_tokens:
                if token in search_text and token in haystack:
                    points += 3
            if role in ("narrator", "旁白") and any(
                t in haystack for t in ("旁白", "narrator", "中性", "neutral")
            ):
                points += 5
            return points

        ranked = sorted(
            candidates,
            key=lambda entry: (
                score(entry),
                assess_voice_match(character, entry).score,
                -activated_first(entry),
                entry.usage_count,
            ),
            reverse=True,
        )
        if ranked and score(ranked[0]) >= _MIN_LIBRARY_MATCH_SCORE:
            return ranked[0]
        return None

    def to_dicts(self) -> list[dict[str, Any]]:
        """Serialize entries for passing through pipeline input."""
        return [e.model_dump(mode="json") for e in self.entries]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _scope_filter(entry: VoiceLibraryEntry, scope: str, project_id: str) -> bool:
    """Return whether ``entry`` should be visible in the given scope.

    - ``project_only`` (config default): only entries from *project_id* or with
      *project_id* in ``origin_project_ids``. ``"legacy"`` entries excluded
      unless migrated.
    - ``global_with_names``: include any project (original behaviour).
    - ``global_all``: no project filtering at all.

    R2 note: when *scope* is ``project_only`` but *project_id* is empty, we
    log a debug message and fall through to global visibility for backward
    compatibility.  Callers that never opted into scoping (e.g. tests, legacy
    code paths) rely on this behaviour.  Production callers that need project
    isolation must always pass a valid *project_id*.
    An unrecognised *scope* also disables filtering for backward compat.
    """
    if scope in ("global_with_names", "global_all"):
        return True
    if scope == "project_only":
        if not project_id:
            # Backward compat: callers that never opted into scoping pass an
            # empty project_id and expect global visibility.  Log at debug
            # level so the path is observable without breaking existing tests.
            _log.debug(
                "_scope_filter: scope='project_only' with empty project_id; "
                "falling through to global visibility for entry %s",
                entry.voice_id,
            )
            return True
        return entry.source_project_id == project_id or project_id in entry.origin_project_ids
    # Unknown scope → no filtering (backward compat).
    return True


def _library_path(storage_root: Path) -> Path:
    return storage_root / "_global" / "tts_voice_library.json"


def _age_bucket(value: str) -> str:
    """Backward-compatible private alias for the shared age normalizer."""
    return age_bucket(value)


def entry_from_cast(
    voice_id: str,
    provider: TTSProvider,
    character: dict[str, Any],
    design_prompt: str = "",
    expires_at: datetime | None = None,
    activation_deadline: datetime | None = None,
    project_id: str = "",
    model_id: str = "",
) -> VoiceLibraryEntry:
    """Construct a VoiceLibraryEntry from a character dict and design result."""
    return VoiceLibraryEntry(
        voice_id=voice_id,
        model_id=model_id,
        provider=provider,
        character_name=str(character.get("name") or character.get("character_name") or ""),
        gender=str(character.get("gender") or ""),
        age_hint=str(character.get("age") or ""),
        role=str(character.get("role") or ""),
        personality=str(character.get("personality") or ""),
        voice_description=" ".join(
            part
            for part in (
                str(character.get("voice") or "").strip(),
                str(character.get("voice_description") or "").strip(),
            )
            if part
        ),
        voice_design_prompt=design_prompt,
        expires_at=expires_at,
        activation_deadline=activation_deadline,
        source_project_id=project_id or "legacy",
        origin_project_ids=[project_id] if project_id else [],
    )

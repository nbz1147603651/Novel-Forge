"""Voice-team readiness preflight for automatic post-archive dubbing.

归档后的自动配音（``run_post_archive_tts``）以 ``allow_voice_team_rebuild=False``
运行，只允许复用作者已确认的配音团队。当团队未就绪时，``execute_full_tts_pipeline``
已经会返回结构化 ``diagnoses`` + ``error_code``，但那时已经起了一个注定失败的
后台 job。

本模块提供 :func:`diagnose_voice_team_readiness` 作为预检纯函数，复用
``execution.py`` 中已有的 ``_diagnose_voice_team_reuse`` / ``_diagnose_narrator_reuse``
判定逻辑，让 ``run_post_archive_tts`` 在写 ``status=running`` 之前就能短路返回，
避免无谓的 job 启动开销。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    NarratorVoiceProfile,
    TTSProvider,
    VoiceTeamContract,
)

__all__ = [
    "VoiceTeamReadinessDiagnosis",
    "diagnose_voice_team_readiness",
]


@dataclass(frozen=True)
class VoiceTeamReadinessDiagnosis:
    """Structured readiness diagnosis for post-archive automatic dubbing."""

    ready: bool
    missing_artifact: str
    """``""`` / ``"narrator_voice_profile"`` / ``"voice_team"``."""

    error_code: str
    """Mirrors execute_full_tts_pipeline's error_code field."""

    error: str
    """Human-readable summary; matches the message the pipeline would return."""

    diagnoses: list[dict[str, str]] = field(default_factory=list)
    """Per-character reasons; same shape as execute_full_tts_pipeline's diagnoses."""

    confirmable_character_ids: list[str] = field(default_factory=list)
    """Character IDs whose reason is REASON_PENDING_APPROVAL (actionable by the author)."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "missing_artifact": self.missing_artifact,
            "error_code": self.error_code,
            "error": self.error,
            "diagnoses": list(self.diagnoses),
            "confirmable_character_ids": list(self.confirmable_character_ids),
        }


def _extract_expected_character_ids(enriched_characters: list[dict[str, Any]]) -> set[str]:
    """Return the set of character IDs the team must cover.

    Unifies the inconsistent ID extraction between the MANUAL and reusable-team
    branches in ``execute_full_tts_pipeline`` (one checked ``character_id`` /
    ``id`` / ``name``, the other only ``character_id`` / ``name``). The
    preflight checks all three so a bible using any key shape is covered.
    """
    ids: set[str] = set()
    for raw in enriched_characters:
        if not isinstance(raw, dict):
            continue
        character_id = str(
            raw.get("character_id") or raw.get("id") or raw.get("name") or ""
        ).strip()
        if character_id:
            ids.add(character_id)
    return ids


def diagnose_voice_team_readiness(
    *,
    layout: ProjectLayout,
    settings: Settings,
    provider: str,
    enriched_characters: list[dict[str, Any]],
) -> VoiceTeamReadinessDiagnosis:
    """Diagnose whether the author-confirmed voice team can be reused.

    Mirrors the ``needs_narrator_build`` + ``reusable_team`` logic in
    ``execute_full_tts_pipeline`` (with ``allow_voice_team_rebuild=False``)
    so the preflight and the pipeline agree on what counts as ready. The
    function is pure: it only reads on-disk artifacts and never mutates
    state, making it safe to call before queueing a background TTS job.

    Returns a structured diagnosis with the same ``error_code`` / ``diagnoses``
    / ``confirmable_character_ids`` shape the pipeline would produce, so the
    caller can short-circuit to the same failure record without launching
    the doomed job.
    """
    # Imported lazily to avoid a circular import at module load time.
    from novel_forge.workspace.tts_ops.execution import (
        REASON_PENDING_APPROVAL,
        _diagnose_narrator_reuse,
        _diagnose_voice_team_reuse,
        _load_json,
        _resolve_provider,
    )

    resolved_provider: TTSProvider = _resolve_provider(settings, provider)

    # ── Step 0: narrator profile ───────────────────────────────────────────
    narrator_profile: NarratorVoiceProfile | None = None
    if layout.tts_narrator_profile_path.exists():
        try:
            narrator_profile = NarratorVoiceProfile.model_validate(
                _load_json(layout.tts_narrator_profile_path)
            )
        except Exception:
            narrator_profile = None
    needs_narrator_build = True
    if narrator_profile is not None:
        needs_narrator_build = (
            not narrator_profile.voice_id
            or narrator_profile.is_expired
            or narrator_profile.provider != resolved_provider
        )
    # NOTE: deliberately no ``settings.tts_narrator_voice_id`` fallback here.
    # The pipeline gate this mirrors (``execute_full_tts_pipeline`` with
    # ``allow_voice_team_rebuild=False``) fails with
    # ``missing_artifact="narrator_voice_profile"`` whenever the profile
    # artifact is missing/expired/wrong-provider, regardless of the settings
    # value.  A fallback here would green-light exactly the job that gate
    # then rejects.

    if needs_narrator_build:
        narrator_diagnoses = _diagnose_narrator_reuse(narrator_profile, resolved_provider)
        return VoiceTeamReadinessDiagnosis(
            ready=False,
            missing_artifact="narrator_voice_profile",
            error_code="tts_voice_team_manual_rebuild_required",
            error=(
                "自动配音只复用已确认的旁白与配音团队；当前旁白音色不可用，"
                "请先在声腔工作室手动构建或确认旁白音色。"
            ),
            diagnoses=narrator_diagnoses,
            confirmable_character_ids=[],
        )

    # ── Step 1: voice team ─────────────────────────────────────────────────
    existing_team: VoiceTeamContract | None = None
    if layout.tts_voice_team_path.exists():
        try:
            existing_team = VoiceTeamContract.model_validate(
                _load_json(layout.tts_voice_team_path)
            )
        except Exception:
            existing_team = None

    expected_character_ids = _extract_expected_character_ids(enriched_characters)

    if existing_team is not None:
        provider_ok = existing_team.default_provider == resolved_provider
        # Mirror the WP2 short-circuit: a confirmed team with matching
        # provider is trusted as a unit (the contract validator resets
        # ``confirmed`` to False on load if any entry expired) — but only
        # when it also covers every character expected in this chapter; a
        # character introduced after the confirmation has no entry and must
        # surface as ``missing`` instead of reaching synthesis without a voice.
        if existing_team.confirmed and provider_ok:
            covered_ids = {entry.character_id for entry in existing_team.entries}
            if expected_character_ids <= covered_ids:
                return VoiceTeamReadinessDiagnosis(
                    ready=True,
                    missing_artifact="",
                    error_code="",
                    error="",
                    diagnoses=[],
                    confirmable_character_ids=[],
                )
        team_diagnoses = _diagnose_voice_team_reuse(
            existing_team, expected_character_ids, resolved_provider
        )
        reusable = provider_ok and not team_diagnoses
        if reusable:
            return VoiceTeamReadinessDiagnosis(
                ready=True,
                missing_artifact="",
                error_code="",
                error="",
                diagnoses=[],
                confirmable_character_ids=[],
            )
    else:
        # Team file missing: synthesise the same diagnoses the pipeline would.
        team_diagnoses = [
            {
                "character_id": cid,
                "character_name": "",
                "reason": "missing",
                "detail": "配音团队不存在，请先在声腔工作室构建",
            }
            for cid in sorted(expected_character_ids)
        ]

    confirmable_ids = [
        d["character_id"]
        for d in team_diagnoses
        if d.get("reason") == REASON_PENDING_APPROVAL and d.get("character_id")
    ]
    return VoiceTeamReadinessDiagnosis(
        ready=False,
        missing_artifact="voice_team",
        error_code="tts_voice_team_manual_rebuild_required",
        error=(
            "自动配音只复用已确认的稳定配音团队；检测到角色音色缺失、过期或平台不一致，"
            "请先在声腔工作室手动构建或确认配音团队。"
        ),
        diagnoses=team_diagnoses,
        confirmable_character_ids=confirmable_ids,
    )

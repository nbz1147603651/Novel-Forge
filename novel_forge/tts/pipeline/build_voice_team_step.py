"""Build Voice Team pipeline step.

Takes character information from EditorialContract and creates
a VoiceTeamContract with character-to-voice mappings.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep, StepEventCallback, TracedStep
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.assets.voice_library import (
    VoiceLibrary,
    VoiceLibraryEntry,
    VoiceLibraryUsage,
    entry_from_cast,
)
from novel_forge.tts.assets.voice_matching import (
    VoiceMatchAssessment,
    assess_voice_match,
    designed_voice_assessment,
    normalize_gender,
    with_semantic_evidence,
)
from novel_forge.tts.assets.voice_semantic_index import (
    build_voice_semantic_scores,
    voice_catalog_fingerprint,
)
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.gateway.factory import TTSAdapterRegistry, resolve_tts_model
from novel_forge.tts.platform.matching_profiles import load_matching_profile
from novel_forge.tts.runtime.performance_policy import (
    derive_voice_performance_profile,
    refresh_voice_performance_profile,
)
from novel_forge.tts.schemas import (
    TTSProvider,
    VoiceCastEntry,
    VoiceCloneRequest,
    VoiceCloneStatus,
    VoiceDesignRequest,
    VoiceTeamContract,
)

_log = get_logger("tts.pipeline.build_voice_team")


def _catalog_cache_path(settings: Settings, provider: TTSProvider, language: str) -> Path:
    """Return the disk cache path for one provider+language voice catalog."""
    root = Path(settings.storage_root) / "tts" / "voice_catalog_cache"
    lang_part = (language or "auto").strip().lower() or "auto"
    return root / f"{provider.value}_{lang_part}.json"


def _load_catalog_cache(
    cache_path: Path,
    *,
    ttl_hours: int,
) -> list[dict[str, Any]] | None:
    """Load a fresh-enough cached voice catalog, or None when stale/missing."""
    try:
        if not cache_path.is_file():
            return None
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(str(payload.get("saved_at") or ""))
        age_hours = (datetime.now(timezone.utc) - saved_at).total_seconds() / 3600.0
        if age_hours > ttl_hours:
            return None
        voices = payload.get("voices")
        if not isinstance(voices, list):
            return None
        return [item for item in voices if isinstance(item, dict)]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_catalog_cache(
    cache_path: Path,
    voices: list[dict[str, Any]],
) -> None:
    """Persist a successfully fetched voice catalog for offline reuse."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "voices": voices,
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        _log.warning("Unable to persist voice catalog cache: %s", exc)


class BuildVoiceTeamInput:
    """Input for voice team building."""

    def __init__(
        self,
        *,
        characters: list[dict[str, Any]],
        narrator_voice_id: str = "",
        default_provider: TTSProvider = TTSProvider.MOCK,
        existing_team: VoiceTeamContract | None = None,
        parallel_clone: bool = True,
        voice_design_enabled: bool = True,
        voice_library: VoiceLibrary | list[dict[str, Any]] | None = None,
        rebuild_character_ids: list[str] | None = None,
        new_library_entries: list[VoiceLibraryEntry] | None = None,
        library_usage: list[VoiceLibraryUsage] | None = None,
        semantic_scores: dict[str, dict[str, float]] | None = None,
        project_id: str = "",
        voice_library_scope: str = "project_only",
        project_language: str = "",
    ) -> None:
        self.characters = characters
        self.narrator_voice_id = narrator_voice_id
        self.default_provider = default_provider
        self.existing_team = existing_team
        self.parallel_clone = parallel_clone
        self.voice_design_enabled = voice_design_enabled
        self.voice_library: VoiceLibrary | None
        if isinstance(voice_library, VoiceLibrary) or voice_library is None:
            self.voice_library = voice_library
        else:
            entries: list[VoiceLibraryEntry] = []
            for raw_entry in voice_library:
                try:
                    entries.append(VoiceLibraryEntry.model_validate(raw_entry))
                except (TypeError, ValueError):
                    continue
            # Keep the former list-of-dicts input usable for external callers
            # while the workspace now passes the typed in-memory library.
            self.voice_library = VoiceLibrary(entries)
        # None = normal build/reuse; [] = retain existing entries; ["id1", ...]
        # = force assignment only for the selected existing characters.
        self.rebuild_character_ids = rebuild_character_ids
        # Mutable result collectors owned by the caller.  The execution layer
        # persists them under a global-library lock after the step succeeds.
        self.new_library_entries: list[VoiceLibraryEntry] = (
            new_library_entries if new_library_entries is not None else []
        )
        self.library_usage: list[VoiceLibraryUsage] = (
            library_usage if library_usage is not None else []
        )
        self.semantic_scores = semantic_scores or {}
        self.project_id = project_id
        self.voice_library_scope = voice_library_scope
        # Primary language of the novel project (e.g. "zh", "en").
        # Used to filter system voice catalogs so that only voices matching
        # the project language are considered during team construction.
        self.project_language = project_language


class BuildVoiceTeamStep(PipelineStep[BuildVoiceTeamInput, VoiceTeamContract]):
    """Build voice team from character list.

    For each character (cost-aware priority):
    1. Check if voice already assigned in existing team
    2. If character has explicit voice_id, use it (authoring decision)
    3. If character has reference_audio_path, clone voice
    4. Reuse a compatible global designed voice from library (zero cost)
    5. Match from provider system voice catalog (zero cost)
    6. AI voice design as last resort (billable, requires explicit opt-in)
    """

    def __init__(
        self,
        registry: TTSAdapterRegistry,
        *,
        settings: Settings,
        on_step: StepEventCallback | None = None,
        router: ModelRouter | None = None,
        builder: PromptBuilder | None = None,
    ) -> None:
        self._registry = registry
        if router is not None and builder is not None:
            self._llm_adjudication_available = True
            super().__init__(router, builder, settings=settings, on_step=on_step)
        else:
            self._llm_adjudication_available = False
            # Voice-team construction is ordinarily deterministic and should
            # not construct an LLM router unless the optional review is on.
            # Initialize the TracedStep base directly (no router/builder).
            TracedStep.__init__(self, settings=settings, on_step=on_step)

    @property
    def step_name(self) -> str:
        return "tts_build_voice_team"

    async def _execute(self, input_data: BuildVoiceTeamInput) -> VoiceTeamContract:
        """Build or update voice team."""
        self._project_id = input_data.project_id
        self._scope = input_data.voice_library_scope
        total = len(input_data.characters)
        _log.info(
            "Building voice team for %d characters",
            total,
        )
        self._emit("build_voice_team_start", {"total": total})

        # Get or create voice team
        team = input_data.existing_team or VoiceTeamContract(
            narrator_voice_id=input_data.narrator_voice_id,
            narrator_provider=input_data.default_provider,
            default_provider=input_data.default_provider,
            default_tts_model=resolve_tts_model(self._settings, input_data.default_provider),
        )
        previous_narrator_provider = team.narrator_provider
        if input_data.narrator_voice_id:
            team.narrator_voice_id = input_data.narrator_voice_id
        elif previous_narrator_provider != input_data.default_provider:
            # Voice IDs are provider-scoped; do not carry one across providers.
            team.narrator_voice_id = ""
        team.narrator_provider = input_data.default_provider
        team.default_provider = input_data.default_provider
        team.default_tts_model = resolve_tts_model(self._settings, input_data.default_provider)
        team.updated_at = datetime.now(timezone.utc)

        # Get adapter for voice operations
        adapter = self._registry.get_adapter(input_data.default_provider)

        # Prefer the provider's actual catalog.  The previous implementation
        # always returned MiniMax voice IDs, which made Tencent/DashScope/local
        # projects silently synthesize with the narrator fallback.
        available_voices: list[dict[str, Any]] = []
        catalog_sync_ok = False
        self._emit(
            "voice_catalog_sync_start",
            {
                "provider": input_data.default_provider.value,
                "limit": 500,
                "total": len(input_data.characters),
                "language": input_data.project_language,
            },
        )
        try:
            available_voices = await adapter.list_system_voices(
                limit=500,
                language=input_data.project_language or None,
            )
            catalog_sync_ok = True
            _save_catalog_cache(
                _catalog_cache_path(
                    self._settings,
                    input_data.default_provider,
                    input_data.project_language,
                ),
                available_voices,
            )
        except Exception as exc:
            _log.warning("Unable to load %s voice catalog: %s", adapter.provider_name, exc)
            # Disk-cache fallback (P1-6): an offline build still gets the most
            # recently synchronized catalog instead of degrading to no matches.
            cached = _load_catalog_cache(
                _catalog_cache_path(
                    self._settings,
                    input_data.default_provider,
                    input_data.project_language,
                ),
                ttl_hours=self._settings.tts_voice_catalog_cache_ttl_hours,
            )
            if cached:
                available_voices = cached
                catalog_sync_ok = True
                _log.info(
                    "Voice catalog fallback: %d cached voices for %s",
                    len(cached),
                    input_data.default_provider.value,
                )
            else:
                available_voices = []
        self._emit(
            "voice_catalog_sync_done",
            {
                "provider": input_data.default_provider.value,
                "voices": len(available_voices),
                "available": catalog_sync_ok,
            },
        )

        rebuild_ids = {str(item) for item in (input_data.rebuild_character_ids or [])}
        is_partial_rebuild = input_data.rebuild_character_ids is not None
        requested_character_ids = {
            str(character.get("character_id") or character.get("name") or "").strip()
            for character in input_data.characters
        }
        if is_partial_rebuild:
            adjudication_character_ids = {
                character_id
                for character_id in requested_character_ids
                if character_id in rebuild_ids or team.get_entry(character_id) is None
            }
        else:
            adjudication_character_ids = requested_character_ids
        assignment_characters: list[dict[str, Any]] = []
        for character in input_data.characters:
            character_id = character.get("character_id", character.get("name", ""))
            existing_entry = team.get_entry(character_id)
            if existing_entry is not None:
                self._refresh_entry_performance_profile(existing_entry, character)
            force_rebuild = character_id in rebuild_ids
            if is_partial_rebuild and not force_rebuild and existing_entry:
                continue
            if (
                existing_entry
                and not force_rebuild
                and self._can_reuse_existing_entry(
                    existing_entry,
                    character,
                    input_data.default_provider,
                    available_voices=available_voices,
                    catalog_sync_ok=catalog_sync_ok,
                )
            ):
                continue
            assignment_characters.append(character)

        semantic_characters = list(assignment_characters)
        semantic_character_ids = {
            str(character.get("character_id") or character.get("name") or "").strip()
            for character in semantic_characters
        }
        if self._settings.tts_voice_llm_adjudication_enabled and self._llm_adjudication_available:
            reusable_review_entries = [
                entry
                for entry in team.entries
                if entry.character_id in adjudication_character_ids
                and entry.character_id not in semantic_character_ids
                and self._needs_llm_voice_adjudication(entry)
            ]
            reusable_review_entries.sort(
                key=lambda entry: (entry.match_score or 0.0, entry.character_id)
            )
            reusable_review_ids = {
                entry.character_id
                for entry in reusable_review_entries[
                    : self._settings.tts_voice_llm_adjudication_max_candidates
                ]
            }
            semantic_characters.extend(
                character
                for character in input_data.characters
                if str(character.get("character_id") or character.get("name") or "").strip()
                in reusable_review_ids
            )

        self._emit(
            "voice_semantic_retrieval_start",
            {
                "characters": len(semantic_characters),
                "total_characters": total,
                "catalog_voices": len(available_voices),
                "provider": input_data.default_provider.value,
            },
        )
        semantic_result = await build_voice_semantic_scores(
            settings=self._settings,
            library=input_data.voice_library or VoiceLibrary(),
            characters=semantic_characters,
            provider=input_data.default_provider,
            provider_voices=available_voices,
        )
        library_semantic_scores = dict(semantic_result.library)
        for character_id, scores in input_data.semantic_scores.items():
            library_semantic_scores.setdefault(character_id, {}).update(scores)
        catalog_semantic_scores = semantic_result.catalog
        team.voice_catalog_fingerprint = (
            semantic_result.catalog_fingerprint or voice_catalog_fingerprint(available_voices)
        )
        team.voice_catalog_synced_at = datetime.now(timezone.utc) if catalog_sync_ok else None
        team.voice_catalog_sync_status = "live" if catalog_sync_ok else "unavailable"
        self._emit(
            "voice_semantic_retrieval_done",
            {
                "characters": len(semantic_characters),
                "total_characters": total,
                "catalog_voices": len(available_voices),
                "provider": input_data.default_provider.value,
                "enabled": bool(semantic_result.library or semantic_result.catalog),
            },
        )

        # Process each character
        tasks: list[
            asyncio.Task[tuple[dict[str, Any], VoiceCastEntry | None, Exception | None]]
        ] = []
        completed = 0
        claimed_library_voice_ids = {entry.voice_id for entry in team.entries if entry.voice_id}
        self._emit(
            "voice_assignment_batch_start",
            {
                "completed": 0,
                "total": total,
                "pending": total,
                "parallel": input_data.parallel_clone,
            },
        )

        async def assign_voice(
            character: dict[str, Any],
        ) -> tuple[dict[str, Any], VoiceCastEntry | None, Exception | None]:
            """Keep character context attached while parallel assignments finish."""
            try:
                entry = await self._assign_voice_for_character(
                    character,
                    adapter,
                    input_data.default_provider,
                    available_voices,
                    input_data.voice_design_enabled,
                    input_data.voice_library,
                    input_data.new_library_entries,
                    input_data.library_usage,
                    claimed_library_voice_ids,
                    library_semantic_scores.get(
                        str(character.get("character_id") or character.get("name") or ""),
                        {},
                    ),
                    catalog_semantic_scores.get(
                        str(character.get("character_id") or character.get("name") or ""),
                        {},
                    ),
                )
            except Exception as exc:
                return character, None, exc
            return character, entry, None

        def emit_progress(character: dict[str, Any], status: str) -> None:
            self._emit(
                "build_voice_team_progress",
                {
                    "completed": completed,
                    "total": total,
                    "character_id": str(
                        character.get("character_id") or character.get("name") or ""
                    ),
                    "character_name": str(character.get("name") or ""),
                    "status": status,
                },
            )

        for char in input_data.characters:
            # Check if already in team
            char_id = char.get("character_id", char.get("name", ""))
            existing_entry = team.get_entry(char_id)
            if existing_entry is not None:
                self._refresh_entry_performance_profile(existing_entry, char)

            # A partial rebuild keeps selected entries replaceable and leaves
            # the rest untouched.  Newly introduced characters are still
            # assigned, so the persisted team remains complete.
            force_rebuild = char_id in rebuild_ids
            if is_partial_rebuild and not force_rebuild and existing_entry:
                _log.debug("Character %s skipped (not in rebuild list)", char_id)
                completed += 1
                emit_progress(char, "reused")
                continue

            if (
                existing_entry
                and not force_rebuild
                and self._can_reuse_existing_entry(
                    existing_entry,
                    char,
                    input_data.default_provider,
                    available_voices=available_voices,
                    catalog_sync_ok=catalog_sync_ok,
                )
            ):
                _log.debug("Character %s already has valid voice", char_id)
                completed += 1
                emit_progress(char, "reused")
                continue

            # Create task for voice assignment
            if input_data.parallel_clone:
                tasks.append(asyncio.create_task(assign_voice(char)))
            else:
                self._emit(
                    "character_voice_assignment_started",
                    {
                        "completed": completed,
                        "total": total,
                        "character_id": str(char_id),
                        "character_name": str(char.get("name") or ""),
                        "parallel": False,
                    },
                )
                entry = await self._assign_voice_for_character(
                    char,
                    adapter,
                    input_data.default_provider,
                    available_voices,
                    input_data.voice_design_enabled,
                    input_data.voice_library,
                    input_data.new_library_entries,
                    input_data.library_usage,
                    claimed_library_voice_ids,
                    library_semantic_scores.get(str(char_id), {}),
                    catalog_semantic_scores.get(str(char_id), {}),
                )
                self._update_team_entry(team, entry)
                completed += 1
                emit_progress(char, "assigned")

        # Process parallel assignments as each one finishes so the desktop can
        # show real progress instead of receiving a burst after every model
        # call has already completed.
        if tasks:
            for pending in asyncio.as_completed(tasks):
                character, assigned_entry, error = await pending
                completed += 1
                if error is not None:
                    _log.warning("Voice assignment failed: %s", error)
                    emit_progress(character, "failed")
                elif assigned_entry is not None:
                    self._update_team_entry(team, assigned_entry)
                    emit_progress(character, "assigned")

        await self._apply_llm_voice_adjudications(
            team,
            input_data.characters,
            character_ids=adjudication_character_ids,
            available_voices=available_voices,
            voice_library=input_data.voice_library,
            library_semantic_scores=library_semantic_scores,
            catalog_semantic_scores=catalog_semantic_scores,
            library_usage=input_data.library_usage,
            provider=input_data.default_provider,
        )

        _log.info(
            "Voice team built: %d entries, %d ready",
            len(team.entries),
            len(team.get_ready_entries()),
        )
        self._emit(
            "build_voice_team_done",
            {
                "entries": len(team.entries),
                "ready": len(team.get_ready_entries()),
                "completed": completed,
                "total": total,
            },
        )

        return team

    async def _apply_llm_voice_adjudications(
        self,
        team: VoiceTeamContract,
        characters: list[dict[str, Any]],
        *,
        character_ids: set[str] | None = None,
        available_voices: list[dict[str, Any]] | None = None,
        voice_library: VoiceLibrary | None = None,
        library_semantic_scores: dict[str, dict[str, float]] | None = None,
        catalog_semantic_scores: dict[str, dict[str, float]] | None = None,
        library_usage: list[VoiceLibraryUsage] | None = None,
        provider: TTSProvider = TTSProvider.MINIMAX,
    ) -> None:
        """Let the LLM choose only among candidates that passed hard constraints."""
        if not self._settings.tts_voice_llm_adjudication_enabled:
            return
        if not self._llm_adjudication_available:
            self._emit(
                "tts_voice_llm_adjudication_skipped",
                {"reason": "router_unavailable"},
            )
            return

        character_by_id = {
            str(character.get("character_id") or character.get("name") or "").strip(): character
            for character in characters
        }
        reviewable = [
            entry
            for entry in team.entries
            if (character_ids is None or entry.character_id in character_ids)
            and self._needs_llm_voice_adjudication(entry)
        ]
        reviewable.sort(key=lambda entry: (entry.match_score or 0.0, entry.character_id))
        reviewable = reviewable[: self._settings.tts_voice_llm_adjudication_max_candidates]
        if not reviewable:
            self._emit(
                "tts_voice_llm_adjudication_skipped",
                {"reason": "no_low_confidence_match"},
            )
            return

        library_semantic_scores = library_semantic_scores or {}
        catalog_semantic_scores = catalog_semantic_scores or {}
        matching_profile = load_matching_profile(provider.value)
        candidate_entries: dict[str, dict[str, VoiceCastEntry]] = {}
        candidates: list[dict[str, Any]] = []
        other_cast = [
            {
                "character_id": entry.character_id,
                "character_name": entry.character_name,
                "voice_id": entry.voice_id,
                "voice_source": entry.voice_source,
            }
            for entry in team.entries
            if entry.voice_id
        ]
        for entry in reviewable:
            character = character_by_id.get(entry.character_id, {})
            entries_by_voice_id, voice_options = self._build_llm_voice_candidates(
                character=character,
                current_entry=entry,
                team=team,
                available_voices=available_voices or [],
                voice_library=voice_library,
                library_semantic_scores=library_semantic_scores.get(entry.character_id, {}),
                catalog_semantic_scores=catalog_semantic_scores.get(entry.character_id, {}),
                matching_profile=matching_profile,
            )
            if not entries_by_voice_id:
                continue
            candidate_entries[entry.character_id] = entries_by_voice_id
            candidates.append(
                {
                    "character_id": entry.character_id,
                    "character_name": entry.character_name,
                    "role": str(character.get("role") or ""),
                    "gender": str(character.get("gender") or ""),
                    "age": str(character.get("age") or ""),
                    "personality": str(character.get("personality") or ""),
                    "voice_description": str(
                        character.get("voice_description") or character.get("voice") or ""
                    ),
                    "current_voice_id": entry.voice_id,
                    "provider": entry.provider.value,
                    "identity_locked": entry.identity_locked,
                    "candidate_voices": voice_options,
                    "other_cast": [
                        item for item in other_cast if item["character_id"] != entry.character_id
                    ],
                }
            )

        if not candidates:
            self._emit(
                "tts_voice_llm_adjudication_skipped",
                {"reason": "no_hard_constraint_candidates"},
            )
            return

        self._emit(
            "tts_voice_llm_adjudication_start",
            {
                "candidate_count": len(candidates),
                "voice_choice_count": sum(
                    len(item.get("candidate_voices", [])) for item in candidates
                ),
            },
        )
        try:
            response = await self._call_with_retry(
                TaskType.TTS_ADJUDICATE_VOICE_MATCH,
                {"stage_cards": {"candidates": candidates, "matching_profile": matching_profile}},
                max_tokens=self._settings.tts_voice_llm_adjudication_max_output_tokens,
                temperature=float(
                    getattr(self._settings, "tts_review_adjudication_temperature", 0.0)
                ),
                required_keys=("decisions", "summary"),
                max_retries=1,
            )
        except Exception as exc:
            _log.warning("Voice match LLM adjudication failed: %s", exc)
            self._emit(
                "tts_voice_llm_adjudication_skipped",
                {"reason": "llm_failure"},
            )
            return

        if not isinstance(response, dict):
            self._emit(
                "tts_voice_llm_adjudication_skipped",
                {"reason": "invalid_response"},
            )
            return

        entries_by_id = {entry.character_id: entry for entry in reviewable}
        claimed_voice_ids = {
            entry.voice_id: entry.character_id for entry in team.entries if entry.voice_id
        }
        auto_select_score = self._settings.tts_voice_llm_adjudication_auto_select_score
        applied = 0
        recast_count = 0
        audition_count = 0
        rejected_count = 0
        invalid_output_count = 0
        for raw_decision in response.get("decisions", []):
            if not isinstance(raw_decision, dict):
                continue
            character_id = str(raw_decision.get("character_id") or "").strip()
            review_entry = entries_by_id.get(character_id)
            verdict = str(raw_decision.get("verdict") or "").strip().lower()
            if review_entry is None or verdict not in {"approve", "review", "reject"}:
                continue

            reason = str(raw_decision.get("reason") or "").strip()[:240]
            try:
                confidence = float(raw_decision.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            confidence_text = f"{confidence:.0%}"
            allowed_entries = candidate_entries.get(character_id, {})
            allowed_ids = list(allowed_entries)
            selected_voice_id = str(raw_decision.get("selected_voice_id") or "").strip()
            raw_audition_ids = raw_decision.get("audition_voice_ids")
            audition_ids = _allowed_voice_ids(raw_audition_ids, allowed_entries, limit=3)

            selected_entry = allowed_entries.get(selected_voice_id)
            decision_invalid_output = bool(verdict == "approve" and not selected_voice_id)
            selected_claimed_by = claimed_voice_ids.get(selected_voice_id, "")
            selected_is_available = bool(
                selected_entry is not None
                and not selected_entry.is_expired
                and selected_entry.provider == review_entry.provider
                and (not selected_claimed_by or selected_claimed_by == character_id)
            )
            if selected_voice_id and not selected_is_available:
                decision_invalid_output = True
                invalid_output_count += 1
                selected_voice_id = ""
                selected_entry = None
            elif decision_invalid_output:
                invalid_output_count += 1

            can_auto_select = bool(
                verdict == "approve"
                and selected_entry is not None
                and confidence >= auto_select_score
                and not review_entry.identity_locked
            )
            if can_auto_select and selected_entry is not None:
                evidence = f"LLM 受约束选角通过（{confidence_text}）"
                if reason:
                    evidence = f"{evidence}：{reason}"
                changed_voice = selected_entry.voice_id != review_entry.voice_id
                updated_entry = selected_entry.model_copy(
                    update={
                        "match_reasons": _append_unique(
                            selected_entry.match_reasons,
                            evidence,
                        ),
                        "llm_adjudication_status": "recast" if changed_voice else "approved",
                        "llm_adjudication_confidence": confidence,
                        "llm_adjudication_reason": reason,
                        "audition_candidate_voice_ids": [],
                        "preview_audio_path": (
                            "" if changed_voice else review_entry.preview_audio_path
                        ),
                        "preview_text": "" if changed_voice else review_entry.preview_text,
                        "preview_error": "" if changed_voice else review_entry.preview_error,
                        "preview_variants": (
                            {} if changed_voice else dict(review_entry.preview_variants)
                        ),
                    }
                )
                if changed_voice:
                    claimed_voice_ids.pop(review_entry.voice_id, None)
                    claimed_voice_ids[updated_entry.voice_id] = character_id
                    self._update_team_entry(team, updated_entry)
                    entries_by_id[character_id] = updated_entry
                    recast_count += 1
                    if updated_entry.voice_source == "library" and library_usage is not None:
                        library_usage.append(
                            VoiceLibraryUsage(
                                voice_id=updated_entry.voice_id,
                                provider=updated_entry.provider,
                            )
                        )
                    self._emit(
                        "character_voice_recast",
                        {
                            "character_id": character_id,
                            "character_name": updated_entry.character_name,
                            "from_voice_id": review_entry.voice_id,
                            "to_voice_id": updated_entry.voice_id,
                            "confidence": confidence,
                        },
                    )
                else:
                    review_entry.match_reasons = updated_entry.match_reasons
                    review_entry.llm_adjudication_status = "approved"
                    review_entry.llm_adjudication_confidence = confidence
                    review_entry.llm_adjudication_reason = reason
                    review_entry.audition_candidate_voice_ids = []
            elif verdict == "reject":
                warning = f"LLM 否决全部候选（{confidence_text}），请重新设计或手动指定"
                if reason:
                    warning = f"{warning}：{reason}"
                review_entry.match_warnings = _append_unique(review_entry.match_warnings, warning)
                review_entry.llm_adjudication_status = "rejected"
                review_entry.llm_adjudication_confidence = confidence
                review_entry.llm_adjudication_reason = reason
                review_entry.audition_candidate_voice_ids = []
                rejected_count += 1
            else:
                if selected_voice_id:
                    audition_ids = _unique_voice_ids(
                        [selected_voice_id, review_entry.voice_id, *audition_ids],
                        limit=3,
                    )
                if len(audition_ids) < 2:
                    audition_ids = _unique_voice_ids(
                        [*audition_ids, review_entry.voice_id, *allowed_ids],
                        limit=3,
                    )
                if decision_invalid_output:
                    warning = f"LLM 返回了无效选角（{confidence_text}），已拒绝应用"
                elif review_entry.identity_locked and selected_voice_id != review_entry.voice_id:
                    warning = f"角色声纹已锁定，改配候选仅供试听（{confidence_text}）"
                elif verdict == "review":
                    warning = f"LLM 建议对比试听（{confidence_text}）"
                else:
                    warning = f"LLM 置信度未达自动改配阈值（{confidence_text}）"
                if reason:
                    warning = f"{warning}：{reason}"
                review_entry.match_warnings = _append_unique(
                    review_entry.match_warnings,
                    warning,
                )
                review_entry.llm_adjudication_status = "needs_audition"
                review_entry.llm_adjudication_confidence = confidence
                review_entry.llm_adjudication_reason = reason
                review_entry.audition_candidate_voice_ids = audition_ids
                audition_count += 1
            applied += 1

        self._emit(
            "tts_voice_llm_adjudication_done",
            {
                "candidate_count": len(candidates),
                "decision_count": applied,
                "recast_count": recast_count,
                "audition_count": audition_count,
                "rejected_count": rejected_count,
                "invalid_output_count": invalid_output_count,
                "summary": str(response.get("summary") or "")[:240],
            },
        )

    def _needs_llm_voice_adjudication(self, entry: VoiceCastEntry) -> bool:
        """Return whether a reusable or newly assigned entry needs semantic casting review."""
        return bool(
            entry.voice_source in {"library", "system", "designed"}
            and entry.match_score is not None
            and (
                entry.match_score < self._settings.tts_voice_llm_adjudication_min_match_score
                or entry.match_warnings
            )
        )

    def _build_llm_voice_candidates(
        self,
        *,
        character: dict[str, Any],
        current_entry: VoiceCastEntry,
        team: VoiceTeamContract,
        available_voices: list[dict[str, Any]],
        voice_library: VoiceLibrary | None,
        library_semantic_scores: dict[str, float],
        catalog_semantic_scores: dict[str, float],
        matching_profile: dict[str, Any] | None = None,
    ) -> tuple[dict[str, VoiceCastEntry], list[dict[str, Any]]]:
        """Build a provider-scoped, hard-filtered whitelist for one character."""
        other_claimed_ids = {
            entry.voice_id
            for entry in team.entries
            if entry.character_id != current_entry.character_id and entry.voice_id
        }
        limit = self._settings.tts_voice_llm_adjudication_choices_per_character
        entries_by_id: dict[str, VoiceCastEntry] = {current_entry.voice_id: current_entry}
        metadata_by_id: dict[str, dict[str, Any]] = {}
        ranked_alternatives: list[tuple[float, VoiceCastEntry, dict[str, Any], float | None]] = []
        speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)

        for library_entry in voice_library.entries if voice_library is not None else []:
            if (
                library_entry.provider != current_entry.provider
                or library_entry.is_expired
                or library_entry.voice_id in other_claimed_ids
                or library_entry.voice_id == current_entry.voice_id
            ):
                continue
            semantic_score = library_semantic_scores.get(library_entry.voice_id)
            assessment = with_semantic_evidence(
                assess_voice_match(character, library_entry, matching_profile=matching_profile),
                semantic_score,
            )
            if not assessment.hard_match:
                continue
            candidate_entry = VoiceCastEntry(
                character_id=current_entry.character_id,
                character_name=current_entry.character_name,
                voice_id=library_entry.voice_id,
                model_id=library_entry.model_id or current_entry.model_id,
                provider=library_entry.provider,
                clone_status=VoiceCloneStatus.READY,
                voice_source="library",
                voice_design_prompt=library_entry.voice_design_prompt,
                voice_library_match_key=self._build_voice_design_prompt(character),
                expires_at=library_entry.expires_at,
                activation_deadline=library_entry.activation_deadline,
                speed_offset=speed_offset,
                pitch_offset=pitch_offset,
                vol_offset=vol_offset,
                performance_profile=derive_voice_performance_profile(character),
                **character_identity_fields(character),
                **_match_evidence_fields(assessment),
            )
            ranked_alternatives.append(
                (
                    assessment.score,
                    candidate_entry,
                    library_entry.model_dump(mode="json"),
                    semantic_score,
                )
            )

        for voice in available_voices:
            voice_id = str(voice.get("voice_id") or voice.get("id") or "").strip()
            if (
                not voice_id
                or voice_id in other_claimed_ids
                or voice_id == current_entry.voice_id
                or any(item[1].voice_id == voice_id for item in ranked_alternatives)
            ):
                continue
            semantic_score = catalog_semantic_scores.get(voice_id)
            assessment = with_semantic_evidence(
                assess_voice_match(character, voice, matching_profile=matching_profile),
                semantic_score,
            )
            if not assessment.hard_match:
                continue
            candidate_entry = VoiceCastEntry(
                character_id=current_entry.character_id,
                character_name=current_entry.character_name,
                voice_id=voice_id,
                model_id=str(voice.get("model_id") or current_entry.model_id),
                provider=current_entry.provider,
                clone_status=VoiceCloneStatus.READY,
                voice_source="system",
                speed_offset=speed_offset,
                pitch_offset=pitch_offset,
                vol_offset=vol_offset,
                performance_profile=derive_voice_performance_profile(character),
                **character_identity_fields(character),
                **_match_evidence_fields(assessment),
            )
            ranked_alternatives.append(
                (assessment.score, candidate_entry, dict(voice), semantic_score)
            )

        ranked_alternatives.sort(key=lambda item: item[0], reverse=True)
        selected_alternatives = ranked_alternatives[: max(0, limit - 1)]
        for _score, candidate_entry, metadata, _semantic_score in selected_alternatives:
            entries_by_id[candidate_entry.voice_id] = candidate_entry
            metadata_by_id[candidate_entry.voice_id] = metadata

        current_metadata: dict[str, Any] = {}
        if voice_library is not None:
            current_library = next(
                (
                    item
                    for item in voice_library.entries
                    if item.provider == current_entry.provider
                    and item.voice_id == current_entry.voice_id
                ),
                None,
            )
            if current_library is not None:
                current_metadata = current_library.model_dump(mode="json")
        if not current_metadata:
            current_metadata = next(
                (
                    dict(voice)
                    for voice in available_voices
                    if str(voice.get("voice_id") or voice.get("id") or "").strip()
                    == current_entry.voice_id
                ),
                {},
            )
        metadata_by_id[current_entry.voice_id] = current_metadata

        voice_options = [
            _llm_voice_candidate_payload(
                entry,
                metadata_by_id.get(voice_id, {}),
                current_voice_id=current_entry.voice_id,
                semantic_score=(
                    library_semantic_scores.get(voice_id)
                    if entry.voice_source == "library"
                    else catalog_semantic_scores.get(voice_id)
                ),
            )
            for voice_id, entry in entries_by_id.items()
        ]
        voice_options.sort(
            key=lambda item: (float(item.get("match_score") or 0.0), bool(item.get("current"))),
            reverse=True,
        )
        return entries_by_id, voice_options

    def _can_reuse_existing_entry(
        self,
        entry: VoiceCastEntry,
        character: dict[str, Any],
        provider: TTSProvider,
        *,
        available_voices: list[dict[str, Any]] | None = None,
        catalog_sync_ok: bool = False,
    ) -> bool:
        """Return whether a ready cast entry still matches its authoritative inputs."""
        if provider in {TTSProvider.BAILIAN, TTSProvider.DASHSCOPE} and not entry.model_id:
            if entry.identity_locked:
                raise RuntimeError(
                    f"角色 {entry.character_name} 的旧版百炼音色未记录绑定模型；"
                    "请解锁后显式重建并试听，系统不会猜测模型。"
                )
            return False
        if entry.identity_locked:
            if not entry.is_ready or entry.is_expired:
                raise RuntimeError(
                    f"角色 {entry.character_name} 的锁定音色已不可用；"
                    "请显式重建并审核替换音色，系统不会静默换人。"
                )
            if entry.provider != provider:
                raise RuntimeError(
                    f"角色 {entry.character_name} 已锁定在 {entry.provider.value}:"
                    f"{entry.voice_id}；切换到 {provider.value} 前必须显式重建或配置备用音色。"
                )
            return True
        if not entry.is_ready or entry.is_expired or entry.provider != provider:
            return False
        # An un-auditioned designed/cloned voice (approval_status="pending")
        # must not be silently reused - the author has not confirmed the voice
        # actually matches the character (e.g. gender). Force a fresh design or
        # an explicit approval before reuse.
        if entry.approval_status != "approved":
            return False
        if entry.voice_source == "system" and catalog_sync_ok and available_voices:
            live_voice = next(
                (
                    voice
                    for voice in available_voices
                    if str(voice.get("voice_id") or voice.get("id") or "").strip() == entry.voice_id
                ),
                None,
            )
            if live_voice is None:
                return False
            live_model_id = str(live_voice.get("model_id") or "")
            if live_model_id and entry.model_id and live_model_id != entry.model_id:
                return False

        character_name = str(character.get("name") or entry.character_name).strip()
        if character_name and character_name != entry.character_name:
            return False

        explicit_voice_id = str(
            character.get("voice_id") or character.get("tts_voice_id") or ""
        ).strip()
        if explicit_voice_id:
            return entry.voice_id == explicit_voice_id and entry.voice_source == "manual"

        voice_description = str(character.get("voice_description") or "").strip()
        if entry.voice_source == "designed":
            return entry.voice_design_prompt == self._build_voice_design_prompt(character)
        if entry.voice_source == "library":
            return bool(entry.voice_library_match_key) and (
                entry.voice_library_match_key == self._build_voice_design_prompt(character)
            )
        if entry.voice_source in {"system", "cloned"} and voice_description:
            return entry.clone_prompt == voice_description
        return True

    async def _assign_voice_for_character(
        self,
        character: dict[str, Any],
        adapter: TTSProviderAdapter,
        provider: TTSProvider,
        available_voices: list[dict[str, Any]] | None = None,
        voice_design_enabled: bool = True,
        voice_library: VoiceLibrary | None = None,
        new_library_entries: list[VoiceLibraryEntry] | None = None,
        library_usage: list[VoiceLibraryUsage] | None = None,
        claimed_library_voice_ids: set[str] | None = None,
        semantic_scores: dict[str, float] | None = None,
        catalog_semantic_scores: dict[str, float] | None = None,
    ) -> VoiceCastEntry:
        """Assign a voice to a single character."""
        char_id = character.get("character_id", character.get("name", ""))
        char_name = character.get("name", char_id)
        reference_audio = character.get("reference_audio_path", "")
        reference_transcript = str(character.get("reference_transcript") or "")
        reference_audio_authorized = bool(character.get("reference_audio_authorized", False))
        voice_description = character.get("voice_description", "")
        default_model_id = resolve_tts_model(self._settings, provider)
        clone_model_id = resolve_tts_model(self._settings, provider, purpose="clone")
        design_model_id = resolve_tts_model(self._settings, provider, purpose="design")
        matching_profile = load_matching_profile(provider.value)

        # An explicit voice ID is an authoring decision and must win over all
        # heuristics.  This also makes re-running the build idempotent when a
        # project stores a manually selected system voice in its character data.
        explicit_voice_id = str(
            character.get("voice_id") or character.get("tts_voice_id") or ""
        ).strip()
        if explicit_voice_id:
            speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)
            return VoiceCastEntry(
                character_id=char_id,
                character_name=char_name,
                voice_id=explicit_voice_id,
                model_id=default_model_id,
                provider=provider,
                clone_status=VoiceCloneStatus.READY,
                voice_source="manual",
                match_reasons=["使用作者明确指定的音色，不参与自动改配"],
                match_warnings=["请通过试听人工确认角色特质与指定音色是否一致"],
                reference_audio_path=reference_audio,
                reference_transcript=reference_transcript,
                clone_prompt=voice_description,
                speed_offset=speed_offset,
                pitch_offset=pitch_offset,
                vol_offset=vol_offset,
                performance_profile=derive_voice_performance_profile(character),
                **character_identity_fields(character),
            )

        # If reference audio provided, try to clone
        if reference_audio and provider != TTSProvider.MOCK:
            try:
                clone_source = await adapter.prepare_clone_source(str(reference_audio))
                clone_request = VoiceCloneRequest(
                    voice_id=f"nf-{char_id}",
                    file_id=clone_source,
                    model_id=clone_model_id,
                    clone_prompt=voice_description,
                    reference_transcript=reference_transcript,
                    authorized=reference_audio_authorized,
                    provider=provider,
                )
                clone_response = await adapter.clone_voice(clone_request)

                if clone_response.status == VoiceCloneStatus.READY:
                    _log.info("Cloned voice for %s: %s", char_name, clone_response.voice_id)
                    # Compute offsets even for cloned voices
                    speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)
                    return VoiceCastEntry(
                        character_id=char_id,
                        character_name=char_name,
                        voice_id=clone_response.voice_id,
                        model_id=clone_response.model_id or clone_model_id,
                        provider=provider,
                        clone_status=VoiceCloneStatus.READY,
                        voice_source="cloned",
                        match_reasons=["使用已授权参考音频建立角色声纹"],
                        match_warnings=["克隆还原度需要通过试听人工确认"],
                        expires_at=clone_response.expires_at,
                        activation_deadline=clone_response.activation_deadline,
                        reference_audio_path=reference_audio,
                        reference_transcript=reference_transcript,
                        clone_prompt=voice_description,
                        speed_offset=speed_offset,
                        pitch_offset=pitch_offset,
                        vol_offset=vol_offset,
                        performance_profile=derive_voice_performance_profile(character),
                        **character_identity_fields(character),
                    )
            except Exception as exc:
                _log.warning("Voice clone failed for %s: %s", char_name, exc)

        design_prompt = self._build_voice_design_prompt(character)
        unclaimed_system_voices = [
            voice
            for voice in (available_voices or [])
            if str(voice.get("voice_id") or voice.get("id") or "")
            not in (claimed_library_voice_ids or set())
        ]

        # Query global voice library before AI design
        if voice_library is not None:
            library_match = self._match_voice_library(
                character,
                provider,
                voice_library,
                excluded_voice_ids=claimed_library_voice_ids,
                semantic_scores=semantic_scores,
            )
            if library_match:
                if claimed_library_voice_ids is not None:
                    claimed_library_voice_ids.add(library_match.voice_id)
                _log.info("Voice library hit for %s: %s", char_name, library_match.voice_id)
                self._emit("voice_library_hit", {"character_name": char_name})
                if library_usage is not None:
                    library_usage.append(
                        VoiceLibraryUsage(
                            voice_id=library_match.voice_id,
                            provider=library_match.provider,
                        )
                    )
                speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)
                assessment = with_semantic_evidence(
                    assess_voice_match(character, library_match, matching_profile=matching_profile),
                    (semantic_scores or {}).get(library_match.voice_id),
                )
                return VoiceCastEntry(
                    character_id=char_id,
                    character_name=char_name,
                    voice_id=library_match.voice_id,
                    model_id=library_match.model_id or default_model_id,
                    provider=provider,
                    clone_status=VoiceCloneStatus.READY,
                    voice_source="library",
                    clone_prompt=voice_description,
                    voice_design_prompt=library_match.voice_design_prompt,
                    voice_library_match_key=design_prompt,
                    **_match_evidence_fields(assessment),
                    expires_at=library_match.expires_at,
                    activation_deadline=library_match.activation_deadline,
                    speed_offset=speed_offset,
                    pitch_offset=pitch_offset,
                    vol_offset=vol_offset,
                    performance_profile=derive_voice_performance_profile(character),
                    **character_identity_fields(character),
                )

        # ── System catalog matching (zero cost) ─────────────────────────
        # Provider preset voices are free and immediately available.
        # Try catalog BEFORE AI design to avoid unnecessary billing.
        # Minor characters (few-chapter side roles) prefer system voices:
        # even without a hard match they accept the provider default instead
        # of paying for an AI-designed voice (P3-4 voice strategy).
        minor_character = is_minor_character(character)
        if unclaimed_system_voices or minor_character:
            voice_id, assessment = match_system_voice_with_evidence(
                character,
                available_voices=unclaimed_system_voices,
                provider=adapter.provider_type,
                semantic_scores=catalog_semantic_scores,
                matching_profile=matching_profile,
            )
            if voice_id and (assessment.hard_match or minor_character):
                if claimed_library_voice_ids is not None:
                    claimed_library_voice_ids.add(voice_id)
                speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)
                self._emit(
                    "voice_catalog_hit",
                    {"character_name": char_name, "voice_id": voice_id},
                )
                return VoiceCastEntry(
                    character_id=char_id,
                    character_name=char_name,
                    voice_id=voice_id,
                    model_id=str(
                        next(
                            (
                                voice.get("model_id")
                                for voice in unclaimed_system_voices
                                if str(voice.get("voice_id") or voice.get("id") or "") == voice_id
                            ),
                            default_model_id,
                        )
                        or default_model_id
                    ),
                    provider=provider,
                    clone_status=VoiceCloneStatus.READY,
                    voice_source="system",
                    **_match_evidence_fields(assessment),
                    reference_audio_path=reference_audio,
                    reference_transcript=reference_transcript,
                    clone_prompt=voice_description,
                    speed_offset=speed_offset,
                    pitch_offset=pitch_offset,
                    vol_offset=vol_offset,
                    performance_profile=derive_voice_performance_profile(character),
                    **character_identity_fields(character),
                )

        # ── AI voice design (last resort, billable) ─────────────────────
        # Only reached when library AND catalog both failed to provide a
        # hard-match voice.  Requires explicit opt-in via tts_voice_design_enabled.
        if voice_design_enabled and design_prompt:
            # Last-chance dedup: if the library already holds a voice designed
            # from the exact same brief, reuse it instead of paying for another
            # design_voice call.  This catches characters that differ in name
            # but share identical traits (and thus an identical design prompt).
            if voice_library is not None:
                prompt_match = voice_library.find_by_design_prompt(
                    design_prompt,
                    provider,
                    excluded_voice_ids=claimed_library_voice_ids,
                    scope=getattr(self, "_scope", "project_only"),
                    project_id=getattr(self, "_project_id", ""),
                )
                if prompt_match:
                    if claimed_library_voice_ids is not None:
                        claimed_library_voice_ids.add(prompt_match.voice_id)
                    _log.info(
                        "Voice library prompt-dedup hit for %s: %s",
                        char_name,
                        prompt_match.voice_id,
                    )
                    self._emit("voice_library_hit", {"character_name": char_name})
                    if library_usage is not None:
                        library_usage.append(
                            VoiceLibraryUsage(
                                voice_id=prompt_match.voice_id,
                                provider=prompt_match.provider,
                            )
                        )
                    speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)
                    assessment = assess_voice_match(
                        character, prompt_match, matching_profile=matching_profile
                    )
                    return VoiceCastEntry(
                        character_id=char_id,
                        character_name=char_name,
                        voice_id=prompt_match.voice_id,
                        model_id=prompt_match.model_id or design_model_id,
                        provider=provider,
                        clone_status=VoiceCloneStatus.READY,
                        voice_source="library",
                        clone_prompt=voice_description,
                        voice_design_prompt=prompt_match.voice_design_prompt,
                        voice_library_match_key=design_prompt,
                        **_match_evidence_fields(assessment),
                        expires_at=prompt_match.expires_at,
                        activation_deadline=prompt_match.activation_deadline,
                        speed_offset=speed_offset,
                        pitch_offset=pitch_offset,
                        vol_offset=vol_offset,
                        performance_profile=derive_voice_performance_profile(character),
                        **character_identity_fields(character),
                    )
            try:
                design_response = await adapter.design_voice(
                    VoiceDesignRequest(
                        description=design_prompt,
                        preview_text=build_voice_preview_text(character),
                        model_id=design_model_id,
                        provider=provider,
                    )
                )
                if design_response.status == VoiceCloneStatus.READY and design_response.voice_id:
                    speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)
                    assessment = designed_voice_assessment(character)
                    _log.info(
                        "Designed character voice for %s: %s",
                        char_name,
                        design_response.voice_id,
                    )
                    # Add to library for future reuse
                    library_entry = entry_from_cast(
                        design_response.voice_id,
                        provider,
                        character,
                        design_prompt,
                        design_response.expires_at,
                        design_response.activation_deadline,
                        project_id=getattr(self, "_project_id", ""),
                        model_id=design_response.model_id or design_model_id,
                    )
                    if new_library_entries is not None:
                        new_library_entries.append(library_entry)
                    return VoiceCastEntry(
                        character_id=char_id,
                        character_name=char_name,
                        voice_id=design_response.voice_id,
                        model_id=design_response.model_id or design_model_id,
                        provider=provider,
                        clone_status=VoiceCloneStatus.READY,
                        expires_at=design_response.expires_at,
                        activation_deadline=design_response.activation_deadline,
                        clone_prompt=design_prompt,
                        voice_design_prompt=design_prompt,
                        voice_source="designed",
                        # MiniMax voice_design is a black box that may return a
                        # wrong-gender voice despite a correct prompt. Mark the
                        # entry pending until the author auditions the activation
                        # audio (filled by _activate_minimax_team_voices) and
                        # confirms it matches the character.
                        approval_status="pending",
                        **_match_evidence_fields(assessment),
                        speed_offset=speed_offset,
                        pitch_offset=pitch_offset,
                        vol_offset=vol_offset,
                        performance_profile=derive_voice_performance_profile(character),
                        **character_identity_fields(character),
                    )
            except NotImplementedError as exc:
                _log.info("Voice design unavailable for %s: %s", char_name, exc)
                self._emit(
                    "voice_design_unavailable",
                    {"character_name": char_name, "detail": str(exc)},
                )
            except Exception as exc:
                _log.warning(
                    "Voice design failed for %s, using catalog fallback: %s",
                    char_name,
                    exc,
                )
                self._emit(
                    "voice_design_fallback",
                    {"character_name": char_name, "detail": str(exc)},
                )
            else:
                self._emit(
                    "voice_design_fallback",
                    {
                        "character_name": char_name,
                        "detail": design_response.message or "Provider returned no ready voice",
                    },
                )

        # ── Final fallback: system catalog best-effort ──────────────────
        # Reached when AI design is disabled, failed, or catalog had no
        # hard-match earlier.  Always returns a usable voice_id.
        voice_id, assessment = match_system_voice_with_evidence(
            character,
            available_voices=unclaimed_system_voices,
            provider=adapter.provider_type,
            semantic_scores=catalog_semantic_scores,
            matching_profile=matching_profile,
        )

        speed_offset, pitch_offset, vol_offset = self._compute_voice_offsets(character)
        if claimed_library_voice_ids is not None and voice_id:
            claimed_library_voice_ids.add(voice_id)

        return VoiceCastEntry(
            character_id=char_id,
            character_name=char_name,
            voice_id=voice_id,
            model_id=default_model_id,
            provider=provider,
            clone_status=VoiceCloneStatus.READY,  # System voices are always ready
            voice_source="system",
            **_match_evidence_fields(assessment),
            reference_audio_path=reference_audio,
            reference_transcript=reference_transcript,
            clone_prompt=voice_description,
            speed_offset=speed_offset,
            pitch_offset=pitch_offset,
            vol_offset=vol_offset,
            performance_profile=derive_voice_performance_profile(character),
            **character_identity_fields(character),
        )

    def _match_voice_library(
        self,
        character: dict[str, Any],
        provider: TTSProvider,
        voice_library: VoiceLibrary,
        *,
        excluded_voice_ids: set[str] | None = None,
        semantic_scores: dict[str, float] | None = None,
    ) -> VoiceLibraryEntry | None:
        """Query the global voice library for a matching entry."""
        return voice_library.find_match(
            character,
            provider,
            excluded_voice_ids=excluded_voice_ids,
            semantic_scores=semantic_scores,
            scope=getattr(self, "_scope", "project_only"),
            project_id=getattr(self, "_project_id", ""),
        )

    def _match_system_voice(
        self,
        character: dict[str, Any],
        adapter: TTSProviderAdapter,
        available_voices: list[dict[str, Any]] | None = None,
    ) -> str:
        """Match a system voice to character traits (delegates to standalone helper)."""
        return match_system_voice(
            character,
            available_voices=available_voices,
            provider=adapter.provider_type,
        )

    def _compute_voice_offsets(
        self,
        character: dict[str, Any],
    ) -> tuple[float, int, float]:
        """Return the provider-neutral automatic baseline for compatibility callers."""

        offsets = derive_voice_performance_profile(character).automatic_baseline
        return offsets.speed_offset, offsets.pitch_offset, offsets.vol_offset

    def _refresh_entry_performance_profile(
        self,
        entry: VoiceCastEntry,
        character: dict[str, Any],
    ) -> None:
        """Refresh automatic policy data without changing voice identity or manual fields."""

        updated = refresh_voice_performance_profile(entry, character)
        entry.performance_profile = updated.performance_profile
        entry.speed_offset = updated.speed_offset
        entry.pitch_offset = updated.pitch_offset
        entry.vol_offset = updated.vol_offset
        entry.performance_offsets_manually_set = updated.performance_offsets_manually_set

    def _build_voice_design_prompt(self, character: dict[str, Any]) -> str:
        """Create a stable, provider-neutral voice brief from character traits."""
        return build_voice_design_prompt(character)

    def _update_team_entry(
        self,
        team: VoiceTeamContract,
        entry: VoiceCastEntry,
    ) -> None:
        """Add or update entry in voice team."""
        # Remove existing entry for this character
        team.entries = [e for e in team.entries if e.character_id != entry.character_id]
        team.entries.append(entry)
        self._emit(
            "character_voice_assigned",
            {
                "character_name": entry.character_name,
                "character_id": entry.character_id,
                "voice_source": entry.voice_source,
                "voice_id": entry.voice_id,
            },
        )


# ─── Standalone helpers (reusable without instantiating the step) ────────────


def character_identity_fields(character: dict[str, Any]) -> dict[str, str]:
    """提取角色身份硬约束字段，供 VoiceCastEntry 持久化。

    这些字段在 build_voice_team 阶段写入，让下游脚本生成 / 说话人裁决 /
    专业审校等 LLM 步骤无需回头查 character_bible 即可读到性别与年龄，
    避免类似"女角色被描述成男声口吻"的身份错配。
    """
    return {
        "character_gender": str(character.get("gender") or "").strip(),
        "character_age": str(character.get("age") or "").strip(),
        "character_role": str(character.get("role") or "").strip(),
    }


def build_voice_design_prompt(character: dict[str, Any]) -> str:
    """Build the canonical voice-design brief from upstream character traits."""
    hints = character.get("tts_voice_hints")
    hint_text = ""
    if isinstance(hints, dict):
        hint_text = "; ".join(
            f"{key}={value}" for key, value in sorted(hints.items()) if value not in (None, "", [])
        )
    fields = [
        ("角色定位", character.get("role", "")),
        ("性别", character.get("gender", "")),
        ("年龄", character.get("age", "")),
        ("性格", character.get("personality", "")),
        ("说话习惯", character.get("voice", "")),
        ("编辑声纹", character.get("voice_description", "")),
        ("结构化声音提示", hint_text),
    ]
    parts = [f"{label}：{value}" for label, value in fields if str(value or "").strip()]
    if len(parts) < 2:
        return ""
    return (
        "请只为有声书角色设计稳定、可复用的声纹身份，不要把某一场景的短期情绪固化进音色。"
        "性别与年龄感是不可违反的硬约束；在此基础上依次匹配角色的性格底色、声音质地、"
        "共鸣位置、音高中心、口音、吐字方式、节奏与停顿习惯。"
        "整部作品必须保持这些身份特征一致；短期情绪与场景语气由后续逐句表演指导控制。"
        "避免夸张卡通腔，不模仿现实人物。" + "；".join(parts)
    )[:1600]


def build_voice_preview_text(character: dict[str, Any]) -> str:
    """Choose a stable audition line that exposes identity without extreme acting."""
    for key in ("voice_sample", "sample_dialogue", "signature_line", "sample_quote"):
        candidate = str(character.get(key) or "").strip()
        if candidate:
            return candidate[:500]
    name = str(character.get("name") or "这个角色").strip()
    return (
        f"我是{name}。事情还没有结束，我们先把已经知道的线索重新理一遍。"
        "别急，我会把该说的话说清楚。"
    )[:500]


def match_system_voice(
    character: dict[str, Any],
    *,
    available_voices: list[dict[str, Any]] | None = None,
    provider: TTSProvider = TTSProvider.MINIMAX,
) -> str:
    """Return the best system voice while preserving the public string API."""
    voice_id, _ = match_system_voice_with_evidence(
        character,
        available_voices=available_voices,
        provider=provider,
    )
    return voice_id


def match_system_voice_with_evidence(
    character: dict[str, Any],
    *,
    available_voices: list[dict[str, Any]] | None = None,
    provider: TTSProvider = TTSProvider.MINIMAX,
    semantic_scores: dict[str, float] | None = None,
    matching_profile: dict[str, Any] | None = None,
) -> tuple[str, VoiceMatchAssessment]:
    """Match a system voice with hard demographic gates and explain the choice."""
    candidates: list[tuple[dict[str, Any], VoiceMatchAssessment]] = []
    for voice in available_voices or []:
        if not str(voice.get("voice_id") or voice.get("id") or "").strip():
            continue
        assessment = assess_voice_match(character, voice, matching_profile=matching_profile)
        if assessment.hard_match:
            candidates.append((voice, assessment))
    if candidates:
        semantic_scores = semantic_scores or {}
        # System voices receive a naturalness bonus: they are professionally
        # recorded and tuned, producing more natural dialogue than AI-designed
        # voices.  This ensures they rank higher when demographic constraints
        # are otherwise equal.
        _SYSTEM_VOICE_NATURALNESS_BONUS = 0.08
        candidates.sort(
            key=lambda item: (
                0.65 * item[1].score
                + 0.35
                * semantic_scores.get(
                    str(item[0].get("voice_id") or item[0].get("id") or ""),
                    0.0,
                )
                + (
                    _SYSTEM_VOICE_NATURALNESS_BONUS
                    if str(item[0].get("voice_source") or "") == "system"
                    or not str(item[0].get("voice_id") or "").startswith("ttv-")
                    else 0.0
                )
            ),
            reverse=True,
        )
        voice, assessment = candidates[0]
        voice_id = str(voice.get("voice_id") or voice.get("id") or "")
        return voice_id, with_semantic_evidence(
            assessment,
            semantic_scores.get(voice_id),
        )

    provider_defaults = {
        TTSProvider.MINIMAX: {
            "male": "Chinese (Mandarin)_Reliable_Executive",
            "female": "Chinese (Mandarin)_News_Anchor",
            "neutral": "Chinese (Mandarin)_Reliable_Executive",
        },
        TTSProvider.BAILIAN: {
            "male": "longanlufeng",
            "female": "longanlingxin",
            "neutral": "longanlingxin",
        },
        TTSProvider.DASHSCOPE: {
            "male": "longanlufeng",
            "female": "longanlingxin",
            "neutral": "longanlingxin",
        },
        TTSProvider.TENCENT: {"male": "1004", "female": "1001", "neutral": "1004"},
        TTSProvider.VOLCENGINE_ARK: {
            "male": "zh_male_m191_uranus_bigtts",
            "female": "zh_female_vv_uranus_bigtts",
            "neutral": "zh_female_vv_uranus_bigtts",
        },
        TTSProvider.MIMO: {"male": "苏打", "female": "冰糖", "neutral": "mimo_default"},
        TTSProvider.LOCAL: {"male": "default", "female": "default", "neutral": "default"},
        TTSProvider.COSYVOICE: {"male": "中文男", "female": "中文女", "neutral": "中文女"},
        TTSProvider.OPENVOICE: {
            "male": "openvoice-base",
            "female": "openvoice-base",
            "neutral": "openvoice-base",
        },
        TTSProvider.MOCK: {
            "male": "mock-male-1",
            "female": "mock-female-1",
            "neutral": "mock-neutral-1",
        },
    }
    defaults = provider_defaults.get(provider, provider_defaults[TTSProvider.MINIMAX])
    gender = normalize_gender(character.get("gender"))
    voice_id = defaults.get(gender, defaults["neutral"])
    fallback_profile = {
        "voice_id": voice_id,
        "gender": gender if gender in {"male", "female"} else "neutral",
        "tags": ["系统默认音色"],
    }
    assessment = assess_voice_match(character, fallback_profile, matching_profile=matching_profile)
    warnings = list(assessment.warnings)
    warnings.append("平台音色目录缺少合格候选，已回退到同性交付默认音色")
    return voice_id, VoiceMatchAssessment(
        score=assessment.score,
        hard_match=assessment.hard_match,
        reasons=assessment.reasons,
        warnings=tuple(dict.fromkeys(warnings)),
        expressive_matches=assessment.expressive_matches,
    )


def _match_evidence_fields(assessment: VoiceMatchAssessment) -> dict[str, Any]:
    """Convert immutable matching evidence into schema-ready values."""
    return {
        "match_score": assessment.score,
        "match_reasons": list(assessment.reasons),
        "match_warnings": list(assessment.warnings),
    }


# 次要角色定位关键词（P3-4 音色策略）：出场少、戏份轻的角色优先系统音色。
_MINOR_ROLE_KEYWORDS = (
    "龙套",
    "配角",
    "客串",
    "路人",
    "次要",
    "npc",
    "minor",
    "supporting",
    "extra",
    "cameo",
)


def is_minor_character(character: dict[str, Any]) -> bool:
    """Return whether *character* is a minor role (few-chapter appearances).

    Uses the role/定位 field as the project currently does not track
    per-chapter appearance counts.  Protagonists and explicitly "important"
    roles are never treated as minor, so the policy only affects side
    characters — they receive free, stable system voices instead of
    billable AI-designed or cloned voices.
    """
    role = str(character.get("role") or character.get("role_label") or "").lower()
    if not role:
        return False
    if any(keyword in role for keyword in ("主角", "重要", "protagonist", "main", "lead")):
        return False
    return any(keyword in role for keyword in _MINOR_ROLE_KEYWORDS)


def _llm_voice_candidate_payload(
    entry: VoiceCastEntry,
    metadata: dict[str, Any],
    *,
    current_voice_id: str,
    semantic_score: float | None,
) -> dict[str, Any]:
    """Project one validated candidate into the LLM whitelist."""
    tags = metadata.get("tags")
    if not isinstance(tags, list):
        tags = [str(tags)] if tags else []
    return {
        "voice_id": entry.voice_id,
        "current": entry.voice_id == current_voice_id,
        "voice_source": entry.voice_source,
        "name": str(metadata.get("name") or metadata.get("character_name") or ""),
        "gender": str(metadata.get("gender") or ""),
        "age_hint": str(metadata.get("age_hint") or metadata.get("age") or ""),
        "role": str(metadata.get("role") or ""),
        "personality": str(metadata.get("personality") or ""),
        "voice_description": str(
            metadata.get("voice_description") or metadata.get("description") or ""
        ),
        "tags": [str(tag) for tag in tags[:12]],
        "match_score": entry.match_score,
        "semantic_score": semantic_score,
        "match_reasons": entry.match_reasons,
        "match_warnings": entry.match_warnings,
        "hard_constraints_passed": True,
    }


def _allowed_voice_ids(
    raw_values: Any,
    allowed_entries: dict[str, VoiceCastEntry],
    *,
    limit: int,
) -> list[str]:
    """Accept only unique IDs from the server-built candidate whitelist."""
    if not isinstance(raw_values, list):
        return []
    return _unique_voice_ids(
        [str(value).strip() for value in raw_values if str(value).strip() in allowed_entries],
        limit=limit,
    )


def _unique_voice_ids(values: list[str], *, limit: int) -> list[str]:
    """Keep ordered, non-empty voice IDs without duplicates."""
    result: list[str] = []
    for value in values:
        voice_id = str(value or "").strip()
        if voice_id and voice_id not in result:
            result.append(voice_id)
        if len(result) >= limit:
            break
    return result


def _append_unique(values: list[str], addition: str) -> list[str]:
    """Append one non-empty audit message without duplicating prior evidence."""
    item = addition.strip()
    if not item or item in values:
        return list(values)
    return [*values, item]

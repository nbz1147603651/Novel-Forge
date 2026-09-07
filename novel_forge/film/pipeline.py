"""Recoverable film workflow orchestration for the 映界 workbench."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelRequest
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.registry import PromptRegistry

from .compliance import COMPLIANCE_REPORT_RELATIVE_PATH, ComplianceAuditor
from .jobs import FilmJobManager
from .lineage import reconcile_film_sources
from .media import FilmMediaVault
from .platform_adapter import (
    apply_ark_face_compliance,
    estimate_generation_cost,
    project_prompt_for_platform,
    select_platform_for_shot,
)
from .providers._client import normalize_task_status, poll_until_terminal
from .providers.base import (
    FilmGenerationMode,
    FilmGenerationRequest,
    FilmProviderTask,
    FilmProviderTaskState,
    FilmReferenceMedia,
)
from .providers.catalog import FILM_PROVIDER_CATALOG
from .providers.factory import create_film_provider
from .qc import FilmQcEngine
from .rendering import FilmRenderer
from .schemas import (
    FILM_STAGE_ORDER,
    FilmJob,
    FilmJobKind,
    FilmJobState,
    FilmShot,
    FilmStage,
    FilmStageState,
    FilmStageStatus,
    FilmStudioState,
    FilmVisualAsset,
    MediaArtifact,
    MediaArtifactKind,
    ProductionMode,
    QcCheckStatus,
    RunPlanNode,
    Screenplay,
    ScreenplayScene,
    TimelineClip,
    utc_now_iso,
)
from .screenplay_projection import (
    ChapterScreenplayProjector,
    validate_screenplay_adaptation,
)
from .source_projection import FilmSourceProjector
from .store import FilmProjectStore
from .storyboard import SHOTS_PER_SCENE_RANGE, audit_storyboard, plan_storyboard_shots
from .vision_qc import FilmVisionQcEngine
from .workflow import completion_ratio, invalidate_downstream, ready_nodes, topological_order
from .workflow_graph import build_run, estimate_run, validate_graph
from .workflow_models import (
    FilmGraphDefinition,
    FilmGraphEvent,
    FilmGraphRun,
    FilmGraphRunScope,
    FilmGraphRunStatus,
    FilmGraphView,
    FilmRunEstimate,
)
from .workflow_repository import FilmWorkflowRepository

StepCallback = Callable[[str, dict[str, Any]], None]


def _noop_step(_step: str, _payload: dict[str, Any]) -> None:
    return


def _source_identity(source: Any) -> str:
    payload = {
        "revision": source.revision,
        "input_signature": source.input_signature,
        "quality_status": source.quality_status,
        "derivation_status": source.derivation_status,
        "output_version": source.output_version,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


# Generation mode → structured-prompt alignment mode (H3 guide §2.1).
# Reference-driven modes (Ref2VA) carry no keyframe alignment header.
_ALIGNMENT_MODES: dict[FilmGenerationMode, str] = {
    FilmGenerationMode.TEXT_TO_VIDEO: "t2va",
    FilmGenerationMode.IMAGE_TO_VIDEO: "i2va",
    FilmGenerationMode.FIRST_LAST_FRAME: "fl2va",
    FilmGenerationMode.VIDEO_CONTINUATION: "i2va",
    FilmGenerationMode.REFERENCE_TO_VIDEO: "ref2va",
    FilmGenerationMode.SUBJECT_REFERENCE_VIDEO: "ref2va",
}

_SPEAKER_IN_TITLE = re.compile(r"（([^（）]+)）")
_OFFSCREEN_TITLE_MARKERS = ("旁白", "画外音")


def _shot_dialogue_entries(shot: FilmShot) -> list[dict[str, Any]]:
    """Parse a shot's dialogue into speaker-tagged entries for projection.

    The storyboard records the speaker in the shot title (「对白镜头（某人）」)
    and narration shots as 「旁白空镜」; both are surfaced deterministically so
    structured platforms get stable speaker IDs and voiceover semantics.
    """
    text = (shot.dialogue or "").strip()
    if not text:
        return []
    speaker_match = _SPEAKER_IN_TITLE.search(shot.title or "")
    speaker = speaker_match.group(1).strip() if speaker_match else ""
    offscreen = any(marker in (shot.title or "") for marker in _OFFSCREEN_TITLE_MARKERS)
    return [
        {"speaker": speaker, "text": line.strip(), "offscreen": offscreen}
        for line in text.split("；")
        if line.strip()
    ]


class FilmProductionPipeline:
    """Coordinates story projection, creative stages and paid media jobs."""

    def __init__(
        self,
        *,
        project_id: str,
        layout: ProjectLayout,
        settings: Any | None = None,
        router: Any | None = None,
        on_step: StepCallback | None = None,
    ) -> None:
        self.project_id = project_id
        self.layout = layout
        self.settings = settings
        self.router = router
        self.on_step = on_step or _noop_step
        self.store = FilmProjectStore(layout)
        self.vault = FilmMediaVault(layout)
        self.renderer = FilmRenderer(layout)
        self.qc = FilmQcEngine(layout, vault=self.vault)
        self.jobs = FilmJobManager()
        self.workflow_repository = FilmWorkflowRepository(
            self.store.film_dir,
            project_id,
        )

    def get_or_bootstrap(
        self,
        *,
        mode: ProductionMode | None = None,
        refresh_sources: bool = False,
    ) -> FilmStudioState:
        state = self.store.load()
        projected = FilmSourceProjector(self.layout, self.project_id).build()
        if state is not None and not refresh_sources and (
            state.source_signature == projected.source_signature
            and state.source_signature != "legacy_unknown"
        ):
            if mode is not None and state.mode != mode:
                state = self.store.save(state.model_copy(update={"mode": mode}))
            if any(job.state == FilmJobState.RUNNING for job in state.jobs):
                recovered, resumable = self.jobs.recover(state)
                self.on_step("film_jobs_recovered", {"count": len(resumable)})
                state = self.store.save(recovered)
            self._bind_graph_sources(state)
            return self.store.save(self.sync_run_plan(state))
        self.on_step("film_project_source_projection", {"project_id": self.project_id})
        projected = projected.model_copy(
            update={
                "mode": mode or ProductionMode.COLLABORATIVE,
                "notices": [self.store.reset_notice] if self.store.reset_notice else [],
            }
        )
        if state is not None:
            projected = reconcile_film_sources(projected, state)
        saved = self.store.save(projected)
        self._bind_graph_sources(saved)
        self.on_step(
            "film_project_ready",
            {
                "characters": len(saved.production_bible.characters),
                "locations": len(saved.production_bible.locations),
                "scenes": len(saved.screenplay.scenes),
                "shots": len(saved.shots),
            },
        )
        return self.store.save(self.sync_run_plan(saved))

    def _bind_graph_sources(self, state: FilmStudioState) -> None:
        self.workflow_repository.bind_source_signatures(
            {
                source.artifact_type: _source_identity(source)
                for source in state.production_bible.sources
            }
        )

    async def advance(
        self,
        *,
        mode: ProductionMode | None = None,
        use_ai: bool = True,
        run_until: FilmStage | None = None,
    ) -> FilmStudioState:
        state = self.get_or_bootstrap(mode=mode)
        if mode is not None:
            state = state.model_copy(update={"mode": mode})
        stop_stage = run_until or self._next_stage(state.current_stage)
        while state.current_stage != FilmStage.DELIVERY:
            state = await self._advance_once(state, use_ai=use_ai)
            if state.current_stage == stop_stage:
                break
            if state.mode == ProductionMode.COLLABORATIVE:
                break
            if state.current_stage == FilmStage.SHOT_PRODUCTION and not self._shots_ready(state):
                state = self._block_media_stage(state)
                break
        return self.store.save(state)

    async def _advance_once(self, state: FilmStudioState, *, use_ai: bool) -> FilmStudioState:
        current = state.current_stage
        next_stage = self._next_stage(current)
        self.on_step("film_stage_complete", {"stage": current.value})
        stages = [
            item.model_copy(
                update={
                    "status": FilmStageStatus.COMPLETED,
                    "progress": 1.0,
                    "updated_at": utc_now_iso(),
                }
            )
            if item.stage == current
            else item.model_copy(
                update={
                    "status": FilmStageStatus.ACTIVE,
                    "progress": max(item.progress, 0.1),
                    "updated_at": utc_now_iso(),
                }
            )
            if item.stage == next_stage
            else item
            for item in state.stages
        ]
        state = state.model_copy(update={"current_stage": next_stage, "stages": stages})
        state = await self._materialize_stage(state, next_stage, use_ai=use_ai)
        self.on_step(
            "film_stage_ready",
            {"stage": next_stage.value, "mode": state.mode.value},
        )
        return state

    async def _materialize_stage(
        self,
        state: FilmStudioState,
        stage: FilmStage,
        *,
        use_ai: bool,
    ) -> FilmStudioState:
        if stage == FilmStage.SCREENPLAY and use_ai:
            screenplay = await self._refine_screenplay(state)
            state = state.model_copy(update={"screenplay": screenplay})
        elif stage == FilmStage.STORYBOARD:
            shots = await self._refine_storyboard(state, use_ai=use_ai)
            state = state.model_copy(update={"shots": shots})
        elif stage == FilmStage.SOUND_PICTURE:
            state = self._inherit_audio_timeline(state)
        elif stage == FilmStage.EDIT:
            state = self._build_picture_timeline(state)
        elif stage == FilmStage.COMPLIANCE:
            auditor = ComplianceAuditor(
                self.layout, self.project_id, router=self.router, on_step=self.on_step
            )
            report = await auditor.audit(state, use_ai=use_ai)
            state = state.model_copy(update={"compliance_report": report})
        elif stage == FilmStage.DELIVERY:
            self.store.export_otio(state)

        counts = {
            FilmStage.PLANNING: len(state.production_bible.sources),
            FilmStage.SCREENPLAY: len(state.screenplay.scenes),
            FilmStage.VISUAL_DEVELOPMENT: len(state.visual_assets),
            FilmStage.STORYBOARD: len(state.shots),
            FilmStage.SHOT_PRODUCTION: sum(bool(shot.selected_asset_url) for shot in state.shots),
            FilmStage.SOUND_PICTURE: sum(
                len(track.clips) for track in state.timeline.tracks if track.kind != "video"
            ),
            FilmStage.EDIT: sum(len(track.clips) for track in state.timeline.tracks),
            FilmStage.COMPLIANCE: len(state.compliance_report.findings)
            if state.compliance_report
            else 0,
            FilmStage.DELIVERY: int(self.store.otio_path.exists()),
        }
        stages = [
            item.model_copy(update={"artifact_count": counts[stage], "updated_at": utc_now_iso()})
            if item.stage == stage
            else item
            for item in state.stages
        ]
        return state.model_copy(update={"stages": stages})

    async def _refine_screenplay(self, state: FilmStudioState) -> Screenplay:
        """Two-level finished-text backflow for the SCREENPLAY stage.

        Level 1 deterministically projects finished chapters into scenes
        (dialogue/action with ``source_refs`` lineage) and merges them with the
        outline projection.  Level 2 asks the router for a semantic adaptation
        (``ADAPT_SCREENPLAY``); every adapted scene must pass
        ``validate_screenplay_adaptation`` or the deterministic scene wins.
        """
        projector = ChapterScreenplayProjector(self.layout, self.project_id)
        chapter_projection = projector.build(
            bible=state.production_bible,
            title=state.production_bible.title,
        )
        base = ChapterScreenplayProjector.merge_with_outline(chapter_projection, state.screenplay)
        self.on_step(
            "film_screenplay_projection",
            {
                "extracted_chapters": sorted(
                    {
                        scene.source_chapter
                        for scene in chapter_projection.scenes
                        if scene.source_chapter is not None
                    }
                ),
                "scenes": len(base.scenes),
            },
        )
        if self.router is None or not base.scenes:
            return base
        cast_names = tuple(
            character.name for character in state.production_bible.characters if character.name
        )
        try:
            prompt = PromptRegistry().render(
                TaskType.ADAPT_SCREENPLAY,
                title=state.production_bible.title,
                language=state.production_bible.language,
                direction=self._adaptation_direction(),
                cast_names=list(cast_names),
                scenes=[scene.model_dump(mode="json") for scene in base.scenes],
            )
        except Exception:
            self.on_step("film_screenplay_ai_fallback", {"reason": "prompt_unavailable"})
            return base
        request = ModelRequest(
            task_type=TaskType.ADAPT_SCREENPLAY,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            temperature=0.55,
            response_json_schema=Screenplay.model_json_schema(),
            response_schema_name="film_screenplay_adaptation",
            output_language=state.production_bible.language,
        )
        try:
            response = await self.router.route(request)
            adapted = Screenplay.model_validate(_json_object(response.content))
        except (RuntimeError, ValueError, ValidationError, TypeError):
            self.on_step("film_screenplay_ai_fallback", {"reason": "invalid_or_unavailable"})
            return base
        accepted = 0
        rejected = 0
        scenes: list[ScreenplayScene] = []
        adapted_by_sequence = {scene.sequence_number: scene for scene in adapted.scenes}
        for deterministic_scene in base.scenes:
            candidate = adapted_by_sequence.get(deterministic_scene.sequence_number)
            if candidate is None:
                scenes.append(deterministic_scene)
                continue
            passed, reason = validate_screenplay_adaptation(
                deterministic_scene, candidate, cast_names=cast_names
            )
            if not passed:
                rejected += 1
                self.on_step(
                    "film_screenplay_scene_rejected",
                    {
                        "scene_id": deterministic_scene.scene_id,
                        "reason": reason,
                    },
                )
                scenes.append(deterministic_scene)
                continue
            accepted += 1
            # Lineage stays owned by the deterministic projection.
            scenes.append(
                candidate.model_copy(
                    update={
                        "scene_id": deterministic_scene.scene_id,
                        "sequence_number": deterministic_scene.sequence_number,
                        "source_chapter": deterministic_scene.source_chapter,
                        "source_scene_ref": deterministic_scene.source_scene_ref,
                        "source_refs": deterministic_scene.source_refs,
                    }
                )
            )
        self.on_step(
            "film_screenplay_adapted",
            {"accepted": accepted, "rejected": rejected},
        )
        return Screenplay(
            title=base.title,
            synopsis=base.synopsis,
            acts=base.acts,
            scenes=scenes,
            estimated_duration_s=sum(scene.duration_s for scene in scenes),
        )

    async def _refine_storyboard(self, state: FilmStudioState, *, use_ai: bool) -> list[FilmShot]:
        """Beat-driven storyboard re-plan for the STORYBOARD stage.

        Mirrors the comic layout layer (``ComicPanel`` 镜像 ``FilmShot``)：
        beats are packed into shots within the hard per-scene budget via the
        shared feasible-region allocator.  ``FILM_SHOT_LAYOUT`` only advises
        pacing (same division of labour as ``COMIC_PANEL_LAYOUT``); the
        deterministic planner clamps, pads and keeps lineage.  Locked shots
        survive the re-plan by ``shot_id``.
        """
        pacing = await self._run_shot_layout_task(state.screenplay) if use_ai else {}
        planned = plan_storyboard_shots(
            state.screenplay, state.production_bible, pacing_targets=pacing
        )
        locked = {shot.shot_id: shot for shot in state.shots if shot.locked}
        shots = [locked.get(shot.shot_id, shot) for shot in planned]
        issues = audit_storyboard(shots, state.screenplay)
        self.on_step(
            "film_storyboard_planned",
            {
                "scenes": len(state.screenplay.scenes),
                "shots": len(shots),
                "llm_paced_scenes": sorted(pacing.keys()),
                "issues": issues,
            },
        )
        return shots

    async def _run_shot_layout_task(self, screenplay: Screenplay) -> dict[str, tuple[int, str]]:
        """LLM pacing hint (scene_id → (target shot count, rhythm note));
        ``{}`` keeps the deterministic fallback."""
        if self.router is None or not screenplay.scenes:
            return {}
        low, high = SHOTS_PER_SCENE_RANGE
        scenes = [
            {
                "scene_id": scene.scene_id,
                "heading": scene.heading,
                "duration_s": scene.duration_s,
                "dialogue_count": sum(1 for line in scene.lines if (line.kind or "") == "dialogue"),
                "action_count": sum(1 for line in scene.lines if (line.kind or "") != "dialogue"),
            }
            for scene in screenplay.scenes
        ]
        try:
            prompt = PromptRegistry().render(
                TaskType.FILM_SHOT_LAYOUT,
                title=screenplay.title,
                shot_budget_low=low,
                shot_budget_high=high,
                scenes=scenes,
            )
        except Exception:
            self.on_step("film_storyboard_fallback", {"reason": "prompt_unavailable"})
            return {}
        request = ModelRequest(
            task_type=TaskType.FILM_SHOT_LAYOUT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.4,
            response_schema_name="film_shot_layout",
        )
        try:
            response = await self.router.route(request)
            payload = _json_object(response.content)
        except RuntimeError:
            self.on_step("film_storyboard_fallback", {"reason": "invalid_or_unavailable"})
            return {}
        raw_scenes = payload.get("scenes")
        if not isinstance(raw_scenes, list):
            return {}
        known = {scene.scene_id for scene in screenplay.scenes}
        targets: dict[str, tuple[int, str]] = {}
        for item in raw_scenes:
            if not isinstance(item, dict):
                continue
            scene_id = str(item.get("scene_id") or "").strip()
            raw_count = item.get("shot_count")
            if not scene_id or scene_id not in known or raw_count is None:
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            count = min(max(count, low), high)
            note = str(item.get("rhythm_note") or "").strip()
            targets[scene_id] = (
                count,
                f"LLM 节奏：{note}" if note else f"LLM 节奏：目标 {count} 镜",
            )
        return targets

    def _adaptation_direction(self) -> dict[str, Any]:
        """Directional metadata only (progressive disclosure, never full text)."""

        arcs: list[dict[str, Any]] = []
        for chapter_number in ChapterScreenplayProjector(
            self.layout, self.project_id
        ).finished_chapters()[:6]:
            try:
                plan = json.loads(
                    self.layout.chapter_plan_path(chapter_number).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError, UnicodeError):
                continue
            if not isinstance(plan, dict):
                continue
            arc = plan.get("emotional_arc")
            arcs.append(
                {
                    "chapter": chapter_number,
                    "emotional_arc": arc if isinstance(arc, (str, list)) else "",
                    "chapter_type": str(plan.get("chapter_type") or ""),
                }
            )
        return {"chapter_arcs": arcs, "note": "仅供把握情绪走向与节奏，不作为正文来源"}

    async def generate_visual_asset(
        self,
        asset_id: str,
        *,
        provider_id: str = "",
        model_id: str = "",
        image_count: int = 4,
    ) -> FilmStudioState:
        state = self.get_or_bootstrap()
        index = next(
            (i for i, item in enumerate(state.visual_assets) if item.asset_id == asset_id), -1
        )
        if index < 0:
            raise ValueError(f"Unknown visual asset: {asset_id}")
        asset = state.visual_assets[index]
        selected_provider = provider_id or asset.provider_id
        selected_model = model_id or asset.model_id
        state, job, created = self.jobs.acquire(
            state,
            kind=FilmJobKind.GENERATE_ASSET,
            target_id=asset_id,
            provider_id=selected_provider,
            model_id=selected_model,
            estimated_cost_usd=estimate_generation_cost(
                selected_provider, media_kind="image", image_count=image_count
            ),
        )
        if not created:
            return self.store.save(state)
        state = self.jobs.mark_running(state, job)
        provider = create_film_provider(selected_provider, self._require_settings())
        references = [
            FilmReferenceMedia(kind="reference_image", url=url) for url in asset.reference_urls
        ]
        mode = (
            FilmGenerationMode.IMAGE_EDIT
            if references
            else FilmGenerationMode.IMAGE_SET
            if selected_provider in {"bailian", "volcengine_ark"}
            else FilmGenerationMode.TEXT_TO_IMAGE
        )
        try:
            task = await provider.submit(
                FilmGenerationRequest(
                    mode=mode,
                    prompt=asset.prompt,
                    negative_prompt=asset.negative_prompt,
                    model_id=model_id or asset.model_id,
                    image_count=image_count,
                    references=references,
                    aspect_ratio="16:9" if asset.asset_type == "location" else "1:1",
                )
            )
        except (RuntimeError, ValueError, OSError) as exc:
            self.store.save(self.jobs.mark_failed(state, job, error=str(exc)))
            raise
        candidates = list(dict.fromkeys([*asset.candidates, *task.asset_urls]))
        updated = asset.model_copy(
            update={
                "provider_id": selected_provider,
                "model_id": task.model_id,
                "provider_task": task,
                "candidates": candidates,
                "selected_url": asset.selected_url or (candidates[0] if candidates else ""),
                "qc_status": "review" if candidates else "running",
                "source_signature": state.source_signature,
                "derivation_status": "fresh",
                "stale_reasons": [],
                "stale_decision": "",
            }
        )
        assets = list(state.visual_assets)
        assets[index] = updated
        state = self.jobs.mark_succeeded(state, job)
        return self.store.save(state.model_copy(update={"visual_assets": assets}))

    async def generate_shot(
        self,
        shot_id: str,
        *,
        provider_id: str = "",
        model_id: str = "",
    ) -> FilmStudioState:
        state = self.get_or_bootstrap()
        index = next((i for i, item in enumerate(state.shots) if item.shot_id == shot_id), -1)
        if index < 0:
            raise ValueError(f"Unknown shot: {shot_id}")
        shot = state.shots[index]
        selected_provider = provider_id or self._route_shot_provider(shot)
        selected_model = model_id or shot.model_id
        state = self._apply_face_compliance(state, shot, selected_provider)
        mode, references = self._shot_generation_contract(shot, state, selected_provider)
        resolution, duration_s = self._resolve_generation_specs(
            selected_provider, selected_model, shot.duration_s
        )
        params = dict(shot.generation_params or {})
        aspect_ratio = str(params.get("aspect_ratio") or state.production_bible.style.aspect_ratio)
        raw_seed = params.get("seed")
        seed = raw_seed if isinstance(raw_seed, int) and raw_seed >= 0 else None
        watermark = bool(params.get("watermark", False))
        requested_prompt_optimizer = params.get("prompt_optimizer")
        prompt_optimizer = (
            requested_prompt_optimizer
            if isinstance(requested_prompt_optimizer, bool)
            else True
        )
        requested_resolution = str(params.get("resolution") or "")
        selected_model_entry = next(
            (
                item
                for item in FILM_PROVIDER_CATALOG.get(selected_provider, {}).get(
                    "video_models", []
                )
                if str(item.get("id") or "") == selected_model
            ),
            {},
        )
        supported_resolutions = {
            str(item) for item in selected_model_entry.get("resolutions", [])
        }
        if requested_resolution in supported_resolutions:
            # Provider-native UI controls may override the catalog default, but
            # unsupported values are ignored at the service boundary.
            resolution = requested_resolution
        if selected_model.startswith("MiniMax-H3"):
            seed = None
        state, job, created = self.jobs.acquire(
            state,
            kind=FilmJobKind.GENERATE_SHOT,
            target_id=shot_id,
            provider_id=selected_provider,
            model_id=selected_model,
            estimated_cost_usd=estimate_generation_cost(
                selected_provider,
                media_kind="video",
                duration_s=duration_s,
                resolution=resolution,
                model_id=selected_model,
                reference_image_count=sum(
                    1 for item in references if item.kind in {"subject", "reference_image"}
                ),
            ),
        )
        if not created:
            return self.store.save(state)
        state = self.jobs.mark_running(state, job)
        provider = create_film_provider(selected_provider, self._require_settings())
        projection = project_prompt_for_platform(
            {
                "prompt": shot.prompt,
                "negative_prompt": shot.negative_prompt,
                "camera_motion": shot.language.camera_motion,
                "dialogues": _shot_dialogue_entries(shot),
                "sound_design": shot.sound_design,
                "music": "",
                "alignment_mode": _ALIGNMENT_MODES.get(mode, "t2va"),
                "duration_s": duration_s,
            },
            selected_provider,
        )
        if projection.notes:
            self.on_step(
                "film_prompt_projection",
                {"shot_id": shot.shot_id, "provider": selected_provider, "notes": projection.notes},
            )
        try:
            driving_audio_url = str(params.get("driving_audio_url") or "").strip()
            if selected_provider == "volcengine_ark" and driving_audio_url:
                references = [
                    *references,
                    FilmReferenceMedia(kind="driving_audio", url=driving_audio_url),
                ]
            # H3 Ref2VA reference-domain validation runs inside the guarded
            # region so a rejected reference also lands on the job ledger.
            await self._validate_h3_references(shot, selected_provider, selected_model)
            task = await provider.submit(
                FilmGenerationRequest(
                    mode=mode,
                    prompt=projection.prompt,
                    negative_prompt=projection.negative_prompt,
                    model_id=selected_model,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    duration_s=duration_s,
                    seed=seed,
                    watermark=watermark,
                    references=references,
                    # Structured prompts (e.g. H3 timeline) must pass through
                    # the provider's optimizer untouched.
                    prompt_optimizer=(
                        prompt_optimizer
                        and not projection.flags.get("disable_prompt_optimizer")
                    ),
                    metadata={
                        "shot_id": shot.shot_id,
                        "audio_url": str(params.get("driving_audio_url") or "").strip(),
                        "generate_audio": bool(params.get("generate_audio", True)),
                        "return_last_frame": bool(params.get("return_last_frame", True)),
                    },
                )
            )
        except (RuntimeError, ValueError, OSError) as exc:
            failed_state = self.store.save(self.jobs.mark_failed(state, job, error=str(exc)))
            self.store.save(self.mark_run_plan_failed(failed_state, f"generate:{shot_id}"))
            raise
        candidates = list(dict.fromkeys([*shot.candidates, *task.asset_urls]))
        updated = shot.model_copy(
            update={
                "provider_id": selected_provider,
                "model_id": task.model_id,
                "generation_mode": mode,
                "provider_task": task,
                "candidates": candidates,
                "selected_asset_url": shot.selected_asset_url
                or (candidates[0] if candidates else ""),
                "qc_status": "review"
                if task.state == FilmProviderTaskState.SUCCEEDED
                else "running",
                "source_signature": state.source_signature,
                "derivation_status": "fresh",
                "stale_reasons": [],
                "stale_decision": "",
            }
        )
        shots = list(state.shots)
        shots[index] = updated
        if task.state == FilmProviderTaskState.SUCCEEDED:
            state = self.jobs.mark_succeeded(state, job)
        elif task.state == FilmProviderTaskState.FAILED:
            state = self.jobs.mark_failed(state, job, error=task.error_message)
        return self.store.save(state.model_copy(update={"shots": shots}))

    async def generate_shots_batch(self, shot_ids: list[str] | None = None) -> FilmStudioState:
        """Generate many shots with per-shot job governance; failures are
        recorded on the job ledger instead of aborting the whole batch."""

        state = self.get_or_bootstrap()
        known = {shot.shot_id for shot in state.shots}
        if shot_ids:
            unknown = [item for item in shot_ids if item not in known]
            if unknown:
                raise ValueError(f"Unknown shots: {', '.join(unknown)}")
            targets = list(shot_ids)
        else:
            targets = [shot.shot_id for shot in state.shots if not shot.selected_asset_url]
        self.on_step("film_batch_start", {"count": len(targets)})
        for position, shot_id in enumerate(targets):
            try:
                await self.generate_shot(shot_id)
            except (RuntimeError, ValueError, OSError) as exc:
                self.on_step("film_batch_shot_failed", {"shot_id": shot_id, "error": str(exc)})
            self.on_step("film_batch_progress", {"done": position + 1, "total": len(targets)})
        return self.get_or_bootstrap()

    async def materialize_media(self, target_id: str) -> FilmStudioState:
        """Download the selected shot/asset media into the local vault.

        Primary URL failures fall back to the provider's alternate URLs
        (MiniMax ``backup_download_url``) before giving up.
        """

        state = self.get_or_bootstrap()
        url, kind, provider_id, task_id = self._resolve_media_target(state, target_id)
        backup_urls = self._task_backup_urls(state, target_id)
        artifact = await self._materialize_with_backup(
            url,
            backup_urls,
            kind=kind,
            subject_ref=target_id,
            provider_id=provider_id,
            task_id=task_id,
        )
        artifacts = [
            item
            for item in state.media_artifacts
            if not (item.subject_ref == target_id and item.kind == kind)
        ]
        artifacts.append(artifact)
        self.on_step(
            "film_media_materialized",
            {"target_id": target_id, "path": artifact.local_path},
        )
        return self.store.save(state.model_copy(update={"media_artifacts": artifacts}))

    @staticmethod
    def _task_backup_urls(state: FilmStudioState, target_id: str) -> list[str]:
        """Alternate download URLs recorded on the owning provider task."""

        for collection_name in ("shots", "visual_assets"):
            item = next(
                (
                    item
                    for item in getattr(state, collection_name)
                    if getattr(item, "shot_id", "") == target_id
                    or getattr(item, "asset_id", "") == target_id
                ),
                None,
            )
            task = getattr(item, "provider_task", None) if item is not None else None
            if task is not None:
                return list(task.backup_urls)
        return []

    async def _materialize_with_backup(
        self,
        url: str,
        backup_urls: list[str],
        *,
        kind: MediaArtifactKind,
        subject_ref: str,
        provider_id: str,
        task_id: str,
    ) -> MediaArtifact:
        attempts = [url, *backup_urls]
        last_error = ""
        for candidate in attempts:
            try:
                return await self.vault.materialize(
                    candidate,
                    kind=kind,
                    subject_ref=subject_ref,
                    provider_id=provider_id,
                    task_id=task_id,
                )
            except (ValueError, OSError, RuntimeError) as exc:
                last_error = str(exc)
        raise ValueError(f"媒体落盘失败（含 {len(backup_urls)} 个备用地址）：{last_error}")

    async def run_shot_qc(self, shot_id: str) -> FilmStudioState:
        """Run automated QC for one shot and record the verdict."""

        state = self.get_or_bootstrap()
        report = await self.qc.check_shot(state, shot_id)
        vision_engine = FilmVisionQcEngine(router=self.router, on_step=self.on_step)
        vision_report = await vision_engine.evaluate(state, shot_id, signal_report=report)
        reports = [item for item in state.qc_reports if item.target_id != shot_id]
        reports.append(report)
        vision_reports = [item for item in state.vision_qc_reports if item.target_id != shot_id]
        vision_reports.append(vision_report)
        status = "passed" if report.passed and vision_report.passed else "failed"
        notes = [
            f"{item.name}: {item.detail}"
            for item in report.checks
            if item.status != QcCheckStatus.PASS and item.detail
        ]
        if report.passed and not vision_report.passed:
            notes.append(
                f"视觉质检 {vision_report.overall_score:.1f}/10 未达可交付线："
                + vision_report.summary
            )
        shots = [
            shot.model_copy(update={"qc_status": status, "qc_notes": notes})
            if shot.shot_id == shot_id
            else shot
            for shot in state.shots
        ]
        return self.store.save(
            state.model_copy(
                update={
                    "qc_reports": reports,
                    "vision_qc_reports": vision_reports,
                    "shots": shots,
                }
            )
        )

    async def render_master(
        self,
        *,
        burn_subtitles: bool = True,
        width: int | None = None,
        height: int | None = None,
        frame_rate: float | None = None,
    ) -> FilmStudioState:
        """Render the master cut, export OTIO, run master QC and persist the
        delivery manifest.  Optional overrides come from the studio settings
        panel; defaults stay 1080p @ style frame rate."""

        renderer = self.renderer
        if width is not None or height is not None or frame_rate is not None:
            renderer = FilmRenderer(
                self.layout,
                width=width or renderer.width,
                height=height or renderer.height,
                frame_rate=frame_rate or renderer.frame_rate,
            )
        state = self.get_or_bootstrap()
        lineage_blockers = self._current_lineage_blockers(state)
        if lineage_blockers:
            raise ValueError(
                "Film delivery is blocked by unresolved upstream lineage: "
                + ", ".join(lineage_blockers)
            )
        state = self._build_picture_timeline(state)
        self.store.export_otio(state)
        manifest = await renderer.render_master(state, burn_subtitles=burn_subtitles)
        report = await self.qc.check_master(manifest)
        notes = [
            f"{item.name}: {item.detail}"
            for item in report.checks
            if item.status != QcCheckStatus.PASS and item.detail
        ]
        compliance = state.compliance_report
        compliance_notes: list[str] = []
        if compliance is not None:
            if compliance.blocked:
                compliance_notes.append(f"合规红线拦截：{compliance.summary}")
            elif not compliance.passed:
                compliance_notes.append(f"合规待整改：{compliance.summary}")
        manifest = manifest.model_copy(
            update={
                "qc_passed": report.passed,
                "compliance_passed": bool(compliance and compliance.passed),
                "compliance_report_path": COMPLIANCE_REPORT_RELATIVE_PATH
                if compliance is not None
                else "",
                "notes": notes + compliance_notes,
                "source_signature": state.source_signature,
                "derivation_status": "fresh",
                "blocking_reasons": [],
            }
        )
        reports = [item for item in state.qc_reports if item.target_id != "master"]
        reports.append(report)
        self.on_step(
            "film_master_rendered",
            {"path": manifest.master_video_path, "qc_passed": report.passed},
        )
        return self.store.save(
            state.model_copy(
                update={
                    "delivery": manifest,
                    "qc_reports": reports,
                    "delivery_blocking_reasons": [],
                    "changed_source_artifacts": [],
                }
            )
        )

    def list_jobs(self, *, limit: int = 50) -> list[FilmJob]:
        return self.jobs.list_jobs(self.get_or_bootstrap(), limit=limit)

    async def cancel_job(self, job_id: str) -> FilmStudioState:
        state = self.get_or_bootstrap()
        job = next((item for item in state.jobs if item.job_id == job_id), None)
        if job is None:
            raise ValueError(f"Unknown film job: {job_id}")
        for collection_name in ("shots", "visual_assets"):
            collection = list(getattr(state, collection_name))
            index = next(
                (
                    i
                    for i, item in enumerate(collection)
                    if getattr(item, "shot_id", "") == job.target_id
                    or getattr(item, "asset_id", "") == job.target_id
                ),
                -1,
            )
            if index < 0:
                continue
            task = collection[index].provider_task
            if task is None:
                break
            if task.state == FilmProviderTaskState.RUNNING:
                raise ValueError("MiniMax 运行中的任务不支持取消，只能等待完成")
            if task.state == FilmProviderTaskState.PENDING:
                provider = create_film_provider(task.provider_id, self._require_settings())
                cancel = getattr(provider, "cancel", None)
                if cancel is not None:
                    refreshed = await cancel(task)
                    collection[index] = collection[index].model_copy(
                        update={"provider_task": refreshed, "qc_status": "cancelled"}
                    )
                    state = state.model_copy(update={collection_name: collection})
            break
        state, cancelled = self.jobs.cancel(state, job_id)
        if not cancelled:
            raise ValueError(f"Job is not cancellable: {job_id}")
        return self.store.save(state)

    def _resolve_media_target(
        self, state: FilmStudioState, target_id: str
    ) -> tuple[str, MediaArtifactKind, str, str]:
        for shot in state.shots:
            if shot.shot_id == target_id:
                if not shot.selected_asset_url:
                    raise ValueError(f"镜头尚未选定素材，无法落盘: {target_id}")
                task_id = shot.provider_task.task_id if shot.provider_task else ""
                return shot.selected_asset_url, MediaArtifactKind.VIDEO, shot.provider_id, task_id
        for asset in state.visual_assets:
            if asset.asset_id == target_id:
                if not asset.selected_url:
                    raise ValueError(f"资产尚未选定素材，无法落盘: {target_id}")
                task_id = asset.provider_task.task_id if asset.provider_task else ""
                return asset.selected_url, MediaArtifactKind.IMAGE, asset.provider_id, task_id
        raise ValueError(f"Unknown media target: {target_id}")

    async def query_media_task(self, target_id: str) -> FilmStudioState:
        state = self.get_or_bootstrap()
        for collection_name in ("visual_assets", "shots"):
            collection = list(getattr(state, collection_name))
            index = next(
                (
                    i
                    for i, item in enumerate(collection)
                    if getattr(item, "asset_id", "") == target_id
                    or getattr(item, "shot_id", "") == target_id
                ),
                -1,
            )
            if index < 0:
                continue
            item = collection[index]
            task = item.provider_task
            if task is None:
                raise ValueError(f"Media target has no provider task: {target_id}")
            provider = create_film_provider(task.provider_id, self._require_settings())
            refreshed = await provider.query(task)
            candidates = list(dict.fromkeys([*item.candidates, *refreshed.asset_urls]))
            update: dict[str, Any] = {
                "provider_task": refreshed,
                "candidates": candidates,
                "qc_status": "review"
                if refreshed.state == FilmProviderTaskState.SUCCEEDED
                else "failed"
                if refreshed.state == FilmProviderTaskState.FAILED
                else "running",
            }
            selected_field = (
                "selected_url" if isinstance(item, FilmVisualAsset) else "selected_asset_url"
            )
            if not getattr(item, selected_field) and candidates:
                update[selected_field] = candidates[0]
            collection[index] = item.model_copy(update=update)
            state = state.model_copy(update={collection_name: collection})
            state = self._sync_provider_job(state, target_id, refreshed)
            return self.store.save(state)
        raise ValueError(f"Unknown media target: {target_id}")

    async def poll_shot_task(self, shot_id: str, *, max_polls: int = 480) -> FilmStudioState:
        """Poll one shot's provider task until it reaches a terminal state.

        The ComfyUI-style polling loop reports queued/processing phases and
        elapsed time through ``film_task_progress`` events and stops early
        once the shot's job is cancelled on the ledger, so a cancelled
        generation never keeps hitting the provider.
        """

        state = self.get_or_bootstrap()
        shot = next((item for item in state.shots if item.shot_id == shot_id), None)
        if shot is None:
            raise ValueError(f"Unknown shot: {shot_id}")
        task = shot.provider_task
        if task is None:
            raise ValueError(f"镜头无进行中任务: {shot_id}")
        if task.state in {
            FilmProviderTaskState.SUCCEEDED,
            FilmProviderTaskState.FAILED,
            FilmProviderTaskState.CANCELLED,
        }:
            return state

        def is_cancelled() -> bool:
            latest = self.store.load()
            if latest is None:
                return False
            return any(
                job.target_id == shot_id
                and job.kind == FilmJobKind.GENERATE_SHOT
                and job.state == FilmJobState.CANCELLED
                for job in latest.jobs
            )

        provider = create_film_provider(task.provider_id, self._require_settings())
        refreshed = await poll_until_terminal(
            provider.query,
            task,
            is_cancelled=is_cancelled,
            on_progress=lambda payload: self.on_step("film_task_progress", payload),
            estimated_duration_s={"minimax": 180, "bailian": 120, "volcengine_ark": 120}.get(
                task.provider_id
            ),
            target_id=shot_id,
            poll_interval_s=5.0,
            max_polls=max_polls,
        )
        state = self.get_or_bootstrap()
        index = next((i for i, item in enumerate(state.shots) if item.shot_id == shot_id), -1)
        if index < 0:
            return state
        item = state.shots[index]
        candidates = list(dict.fromkeys([*item.candidates, *refreshed.asset_urls]))
        update: dict[str, Any] = {
            "provider_task": refreshed,
            "candidates": candidates,
            "qc_status": "review"
            if refreshed.state == FilmProviderTaskState.SUCCEEDED
            else "failed"
            if refreshed.state == FilmProviderTaskState.FAILED
            else "running",
        }
        if not item.selected_asset_url and candidates:
            update["selected_asset_url"] = candidates[0]
        shots = list(state.shots)
        shots[index] = item.model_copy(update=update)
        self.on_step(
            "film_task_polled",
            {"shot_id": shot_id, "state": refreshed.state.value},
        )
        updated = state.model_copy(update={"shots": shots})
        updated = self._sync_provider_job(updated, shot_id, refreshed)
        # A terminal task may have materialized the selected asset; refresh the
        # node graph so the run-plan view marks the shot node completed at once.
        return self.store.save(self.sync_run_plan(updated))

    def _sync_provider_job(
        self,
        state: FilmStudioState,
        target_id: str,
        task: FilmProviderTask,
    ) -> FilmStudioState:
        job = next(
            (
                item
                for item in reversed(state.jobs)
                if item.target_id == target_id
                and item.state in {FilmJobState.QUEUED, FilmJobState.RUNNING}
            ),
            None,
        )
        if job is None:
            return state
        if task.state == FilmProviderTaskState.SUCCEEDED:
            return self.jobs.mark_succeeded(state, job)
        if task.state == FilmProviderTaskState.FAILED:
            return self.jobs.mark_failed(state, job, error=task.error_message)
        if task.state == FilmProviderTaskState.CANCELLED:
            return self.jobs.cancel(state, job.job_id)[0]
        return state

    def apply_minimax_callback(self, payload: dict[str, Any]) -> bool:
        """Apply one authoritative H3 callback to the owning shot or asset."""

        raw_task = payload.get("task")
        task_data = raw_task if isinstance(raw_task, dict) else payload
        task_id = str(task_data.get("id") or task_data.get("task_id") or "")
        if not task_id:
            raise ValueError("MiniMax callback is missing task_id")
        state = self.get_or_bootstrap()
        provider_state = normalize_task_status(str(task_data.get("status") or ""))
        raw_content = task_data.get("content")
        content = raw_content if isinstance(raw_content, dict) else {}
        asset_url = str(content.get("url") or "")
        for collection_name in ("shots", "visual_assets"):
            collection = list(getattr(state, collection_name))
            index = next(
                (
                    i
                    for i, item in enumerate(collection)
                    if item.provider_task is not None and item.provider_task.task_id == task_id
                ),
                -1,
            )
            if index < 0:
                continue
            item = collection[index]
            task = item.provider_task
            assert task is not None
            refreshed = task.model_copy(
                update={
                    "state": provider_state,
                    "asset_urls": [*task.asset_urls, asset_url] if asset_url else task.asset_urls,
                    "error_message": str(task_data.get("error_message") or ""),
                    "raw": payload,
                }
            )
            candidates = list(dict.fromkeys([*item.candidates, *refreshed.asset_urls]))
            update: dict[str, Any] = {
                "provider_task": refreshed,
                "candidates": candidates,
                "qc_status": "review"
                if provider_state == FilmProviderTaskState.SUCCEEDED
                else "failed"
                if provider_state == FilmProviderTaskState.FAILED
                else "cancelled"
                if provider_state == FilmProviderTaskState.CANCELLED
                else "running",
            }
            selected_field = (
                "selected_url" if isinstance(item, FilmVisualAsset) else "selected_asset_url"
            )
            if not getattr(item, selected_field) and candidates:
                update[selected_field] = candidates[0]
            collection[index] = item.model_copy(update=update)
            state = state.model_copy(update={collection_name: collection})
            state = self._sync_provider_job(
                state,
                item.asset_id if isinstance(item, FilmVisualAsset) else item.shot_id,
                refreshed,
            )
            self.store.save(self.sync_run_plan(state))
            return True
        return False

    def update_shot(self, shot_id: str, patch: dict[str, Any]) -> FilmStudioState:
        state = self.get_or_bootstrap()
        allowed = {
            "title",
            "duration_s",
            "language",
            "action",
            "dialogue",
            "sound_design",
            "transition",
            "prompt",
            "negative_prompt",
            "provider_id",
            "model_id",
            "selected_asset_url",
            "locked",
            "qc_status",
            "qc_notes",
            "identity_reference_urls",
            "style_reference_urls",
            "reference_video_urls",
            "reference_audio_urls",
            "first_frame_url",
            "last_frame_url",
            "generation_params",
        }
        safe_patch = {key: value for key, value in patch.items() if key in allowed}
        shots = list(state.shots)
        index = next((i for i, item in enumerate(shots) if item.shot_id == shot_id), -1)
        if index < 0:
            raise ValueError(f"Unknown shot: {shot_id}")
        shots[index] = FilmShot.model_validate({**shots[index].model_dump(), **safe_patch})
        return self.store.save(state.model_copy(update={"shots": shots}))

    def select_asset_candidate(self, asset_id: str, url: str, *, lock: bool) -> FilmStudioState:
        state = self.get_or_bootstrap()
        assets = list(state.visual_assets)
        index = next((i for i, item in enumerate(assets) if item.asset_id == asset_id), -1)
        if index < 0:
            raise ValueError(f"Unknown visual asset: {asset_id}")
        if url not in assets[index].candidates:
            raise ValueError("Selected URL is not a generated candidate")
        assets[index] = assets[index].model_copy(
            update={
                "selected_url": url,
                "locked": lock,
                "qc_status": "approved" if lock else "review",
                "source_signature": state.source_signature,
                "derivation_status": "fresh",
                "stale_reasons": [],
                "stale_decision": "",
            }
        )
        return self.store.save(state.model_copy(update={"visual_assets": assets}))

    def resolve_lineage_decision(self, subject_id: str, action: str) -> FilmStudioState:
        """Resolve one locked/stale source conflict without deleting old media."""

        if action not in {"preserve_old_version", "regenerate", "rebind"}:
            raise ValueError(f"Unknown lineage action: {action}")
        state = self.get_or_bootstrap()
        found = False
        assets: list[FilmVisualAsset] = []
        for asset_item in state.visual_assets:
            if asset_item.asset_id != subject_id:
                assets.append(asset_item)
                continue
            found = True
            updates: dict[str, Any] = {"stale_decision": action}
            if action == "rebind":
                updates.update(
                    {
                        "source_signature": state.source_signature,
                        "derivation_status": "fresh",
                        "stale_reasons": [],
                    }
                )
            assets.append(asset_item.model_copy(update=updates))
        shots: list[FilmShot] = []
        for shot_item in state.shots:
            if shot_item.shot_id != subject_id:
                shots.append(shot_item)
                continue
            found = True
            shot_updates: dict[str, Any] = {"stale_decision": action}
            if action == "rebind":
                shot_updates.update(
                    {
                        "source_signature": state.source_signature,
                        "derivation_status": "fresh",
                        "stale_reasons": [],
                    }
                )
            shots.append(shot_item.model_copy(update=shot_updates))
        if not found:
            raise ValueError(f"Unknown stale film asset or shot: {subject_id}")
        decisions = [
            decision.model_copy(update={"selected": action, "status": "resolved"})
            if decision.decision_id.endswith(f":{subject_id}")
            else decision
            for decision in state.decisions
        ]
        updated = state.model_copy(
            update={"visual_assets": assets, "shots": shots, "decisions": decisions}
        )
        updated = updated.model_copy(
            update={"delivery_blocking_reasons": self._current_lineage_blockers(updated)}
        )
        return self.store.save(self.sync_run_plan(updated))

    @staticmethod
    def _current_lineage_blockers(state: FilmStudioState) -> list[str]:
        blockers = [
            f"novel_source_not_deliverable:{source.artifact_type}"
            for source in state.production_bible.sources
            if source.derivation_status in {"stale", "conflict", "blocked"}
            or source.quality_status == "blocked"
        ]
        blockers.extend(
            f"stale_asset_decision:{item.asset_id}"
            for item in state.visual_assets
            if item.stale_decision == "pending"
        )
        blockers.extend(
            f"stale_shot_decision:{item.shot_id}"
            for item in state.shots
            if item.stale_decision == "pending"
        )
        return list(dict.fromkeys(blockers))

    def _shot_generation_contract(
        self,
        shot: FilmShot,
        state: FilmStudioState,
        provider_id: str,
    ) -> tuple[FilmGenerationMode, list[FilmReferenceMedia]]:
        if shot.first_frame_url and shot.last_frame_url:
            return (
                FilmGenerationMode.FIRST_LAST_FRAME,
                [
                    FilmReferenceMedia(kind="first_frame", url=shot.first_frame_url),
                    FilmReferenceMedia(kind="last_frame", url=shot.last_frame_url),
                ],
            )
        if shot.first_frame_url:
            return (
                FilmGenerationMode.IMAGE_TO_VIDEO,
                [FilmReferenceMedia(kind="first_frame", url=shot.first_frame_url)],
            )
        identity_urls = list(shot.identity_reference_urls)
        for asset in state.visual_assets:
            if asset.subject_id in {*shot.character_ids, shot.location_id} and asset.selected_url:
                identity_urls.append(asset.selected_url)
        identity_urls = list(dict.fromkeys(identity_urls))
        if provider_id != "volcengine_ark":
            # ``asset://`` is an Ark-private trusted-actor URI and must never
            # leak into MiniMax or Bailian requests after a per-shot route switch.
            identity_urls = [url for url in identity_urls if not url.startswith("asset://")]
        if provider_id == "minimax" and (
            identity_urls or shot.reference_video_urls or shot.reference_audio_urls
        ):
            # H3 Ref2VA accepts up to 9 reference images plus up to 3
            # reference videos and 3 reference audios (catalog
            # ``reference_limits``); the multimodal conditioning stream
            # mirrors the ComfyUI Hailuo03 Reference node inputs.
            references = [FilmReferenceMedia(kind="subject", url=url) for url in identity_urls[:9]]
            references.extend(
                FilmReferenceMedia(kind="reference_video", url=url)
                for url in shot.reference_video_urls[:3]
            )
            references.extend(
                FilmReferenceMedia(kind="reference_audio", url=url)
                for url in shot.reference_audio_urls[:3]
            )
            mode = (
                FilmGenerationMode.SUBJECT_REFERENCE_VIDEO
                if identity_urls
                else FilmGenerationMode.REFERENCE_TO_VIDEO
            )
            return mode, references
        if identity_urls and provider_id == "bailian":
            return (
                FilmGenerationMode.REFERENCE_TO_VIDEO,
                [FilmReferenceMedia(kind="reference_image", url=url) for url in identity_urls[:8]],
            )
        if identity_urls and provider_id == "volcengine_ark":
            return (
                FilmGenerationMode.REFERENCE_TO_VIDEO,
                [FilmReferenceMedia(kind="reference_image", url=url) for url in identity_urls[:9]],
            )
        return FilmGenerationMode.TEXT_TO_VIDEO, []

    async def _validate_h3_references(
        self, shot: FilmShot, provider_id: str, model_id: str
    ) -> None:
        """H3 Ref2VA reference-domain checks before submission.

        Mirrors the ComfyUI Hailuo03 Reference node validation: each
        reference video/audio must run 2–15 s, videos need 23.976–60 FPS and
        the combined total must stay ≤15 s.  Only local files can be probed;
        remote URLs are skipped (the platform rejects them if invalid).
        """

        if provider_id != "minimax" or not str(model_id).startswith("MiniMax-H3"):
            return
        videos = [url for url in shot.reference_video_urls if url][:3]
        audios = [url for url in shot.reference_audio_urls if url][:3]
        if not videos and not audios:
            return
        total = 0.0
        for kind, urls in (("视频", videos), ("音频", audios)):
            for url in urls:
                if url.startswith(("http://", "https://")):
                    continue
                path = Path(url)
                if not path.is_file():
                    continue
                metrics = await self.vault.probe_path(path)
                duration = float(metrics.get("duration_s") or 0)
                if duration and not (1.8 <= duration <= 15.1):
                    raise ValueError(f"H3 参考{kind}时长需在 2–15s：{url[:80]}（{duration:.1f}s）")
                total += duration
                if kind == "视频":
                    fps = float(metrics.get("fps") or 0)
                    if fps and not (23.9 <= fps <= 60.5):
                        raise ValueError(f"H3 参考视频 FPS 需在 23.976–60：{url[:80]}（{fps:.2f}）")
        if total and total > 15.1:
            raise ValueError(f"H3 参考视频/音频总时长需 ≤15s：{total:.1f}s")

    def _route_shot_provider(self, shot: FilmShot) -> str:
        """Capability-driven routing: catalog features pick the platform.

        The shot's recorded provider acts as a loyalty preference; the route
        only switches when another platform's feature match dominates.
        """

        requirements = {
            "requires_multi_shot": any(
                marker in shot.prompt for marker in ("第1个镜头", "第2个镜头", "镜头1", "镜头2")
            ),
            "reference_image_count": len(shot.identity_reference_urls)
            + len(shot.style_reference_urls),
            "requires_face_identity": bool(shot.identity_reference_urls),
        }
        selected, scores = select_platform_for_shot(
            requirements, preferred_provider=shot.provider_id
        )
        self.on_step(
            "film_platform_routed",
            {"shot_id": shot.shot_id, "provider": selected, "scores": scores},
        )
        return selected

    def _apply_face_compliance(
        self, state: FilmStudioState, shot: FilmShot, provider_id: str
    ) -> FilmStudioState:
        """Real-face dependency gate: route to trusted-asset or virtual-human
        path and record a human-checkpoint run-plan decision item."""

        decision = apply_ark_face_compliance(
            shot.prompt,
            platform_id=provider_id,
            reference_urls=[*shot.identity_reference_urls, *shot.style_reference_urls],
        )
        if not decision.required:
            return state
        self.on_step(
            "film_face_compliance",
            {
                "shot_id": shot.shot_id,
                "path": decision.path,
                "reason": decision.reason,
                "provider": provider_id,
            },
        )
        node_id = f"face_compliance:{shot.shot_id}"
        if any(node.node_id == node_id for node in state.run_plan):
            return state
        node = RunPlanNode(
            node_id=node_id,
            label=f"真人脸合规确认（{shot.shot_id}）",
            stage=FilmStage.SHOT_PRODUCTION,
            provider_id=provider_id,
            human_checkpoint=True,
            notes=decision.run_plan_note,
        )
        return state.model_copy(update={"run_plan": [*state.run_plan, node]})

    def _inherit_audio_timeline(self, state: FilmStudioState) -> FilmStudioState:
        timeline = state.timeline.model_copy(deep=True)
        dialogue_track = next(
            (track for track in timeline.tracks if track.kind == "dialogue"), None
        )
        if dialogue_track is None:
            return state
        clips = list(dialogue_track.clips)
        known = {clip.source_url for clip in clips}
        for path in sorted((self.layout.tts_dir / "results").glob("chapter_*_audio.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            source = str(
                payload.get("assembled_audio_path")
                or payload.get("master_audio_path")
                or payload.get("audio_path")
                or ""
            )
            if not source or source in known:
                continue
            duration_ms = float(payload.get("total_duration_ms") or 0)
            clips.append(
                TimelineClip(
                    clip_id=f"audio-{path.stem}",
                    name=path.stem.replace("_audio", ""),
                    media_kind="dialogue",
                    source_url=source,
                    duration_s=max(1, duration_ms / 1000),
                    metadata={"source": str(path.relative_to(self.layout.root))},
                )
            )
            known.add(source)
        dialogue_track.clips = clips
        return state.model_copy(update={"timeline": timeline})

    @staticmethod
    def _build_picture_timeline(state: FilmStudioState) -> FilmStudioState:
        timeline = state.timeline.model_copy(deep=True)
        video_track = next((track for track in timeline.tracks if track.kind == "video"), None)
        if video_track is None:
            return state
        clips: list[TimelineClip] = []
        cursor = 0.0
        for shot in state.shots:
            if not shot.selected_asset_url:
                continue
            clips.append(
                TimelineClip(
                    clip_id=f"clip-{shot.shot_id}",
                    name=f"{shot.shot_id} {shot.title}",
                    media_kind="video",
                    source_url=shot.selected_asset_url,
                    start_s=cursor,
                    duration_s=shot.duration_s,
                    metadata={"shot_id": shot.shot_id, "transition": shot.transition},
                )
            )
            cursor += shot.duration_s
        video_track.clips = clips
        return state.model_copy(update={"timeline": timeline})

    @staticmethod
    def _resolve_generation_specs(
        provider_id: str, model_id: str, duration_s: float
    ) -> tuple[str, int]:
        """Derive resolution and duration from the provider catalog model domain.

        The catalog is the single source of truth (providers/catalog.py): a
        resolution outside the model's domain (e.g. 1080P for MiniMax H3 which
        only supports 768P/2K) used to silently downgrade every H3 shot to
        768P.  Now the highest supported resolution is picked unless the
        caller already supplies a supported one, and duration is clamped into
        the model's [min, max] window instead of the generic 2–15s range.
        """

        entry = FILM_PROVIDER_CATALOG.get(provider_id, {})
        model: dict[str, Any] = next(
            (
                item
                for item in entry.get("video_models", [])
                if str(item.get("id") or "") == model_id
            ),
            {},
        )
        resolutions = [str(item) for item in model.get("resolutions", [])]
        default_resolution = str(model.get("default_resolution") or "")
        if default_resolution in resolutions:
            resolution = default_resolution
        elif provider_id == "minimax" and model_id.startswith("MiniMax-H3") and resolutions:
            resolution = resolutions[0]
        elif resolutions:
            order = {"720P": 0, "768P": 0, "1080P": 1, "2K": 2, "4K": 3}
            resolution = max(resolutions, key=lambda item: order.get(item, 1))
        else:
            resolution = "1080P"
        durations = model.get("durations")
        low, high = 2, 15
        if isinstance(durations, dict):
            low = int(durations.get("min", low))
            high = int(durations.get("max", high))
        elif isinstance(durations, list) and durations:
            low = int(min(durations))
            high = int(max(durations))
        return resolution, max(low, min(high, round(duration_s)))

    def sync_run_plan(self, state: FilmStudioState) -> FilmStudioState:
        """Reconcile the executable node graph with the current production state.

        Idempotently appends per-asset and per-shot generation nodes (plus
        their identity-asset dependencies) to the existing run plan; manual
        decision nodes already on the plan are preserved untouched.
        """

        desired: dict[str, RunPlanNode] = {}
        for asset in state.visual_assets:
            node_id = f"generate_asset:{asset.asset_id}"
            desired[node_id] = RunPlanNode(
                node_id=node_id,
                label=f"资产生成 · {asset.name}",
                stage=FilmStage.VISUAL_DEVELOPMENT,
                artifact_outputs=[f"asset:{asset.asset_id}"],
                provider_id=asset.provider_id,
                model_id=asset.model_id,
                status=self._provider_task_stage_status(
                    selected=bool(asset.selected_url),
                    task_state=asset.provider_task.state if asset.provider_task else None,
                    derivation_status=asset.derivation_status,
                    locked=asset.locked,
                    stale_decision=asset.stale_decision,
                ),
                estimated_cost_usd=estimate_generation_cost(
                    asset.provider_id, media_kind="image", image_count=4
                ),
            )
        for shot in state.shots:
            node_id = f"generate:{shot.shot_id}"
            depends = [
                f"generate_asset:{asset.asset_id}"
                for asset in state.visual_assets
                if asset.subject_id in {*shot.character_ids, shot.location_id}
            ]
            desired[node_id] = RunPlanNode(
                node_id=node_id,
                label=f"镜头生成 · {shot.title or shot.shot_id}",
                stage=FilmStage.SHOT_PRODUCTION,
                depends_on=depends,
                artifact_inputs=list(depends),
                artifact_outputs=[f"shot:{shot.shot_id}"],
                provider_id=shot.provider_id,
                model_id=shot.model_id,
                status=self._provider_task_stage_status(
                    selected=bool(shot.selected_asset_url),
                    task_state=shot.provider_task.state if shot.provider_task else None,
                    derivation_status=shot.derivation_status,
                    locked=shot.locked,
                    stale_decision=shot.stale_decision,
                ),
                estimated_cost_usd=estimate_generation_cost(
                    shot.provider_id, media_kind="video", duration_s=shot.duration_s
                ),
            )
        reconciled: list[RunPlanNode] = []
        seen: set[str] = set()
        for node in state.run_plan:
            replacement = desired.get(node.node_id)
            reconciled.append(replacement or node)
            seen.add(node.node_id)
        reconciled.extend(node for node_id, node in desired.items() if node_id not in seen)

        completed = {
            node.node_id for node in reconciled if node.status == FilmStageStatus.COMPLETED
        }
        reconciled = [
            node.model_copy(update={"status": FilmStageStatus.READY})
            if node.node_id in desired
            and node.status == FilmStageStatus.PENDING
            and all(dependency in completed for dependency in node.depends_on)
            else node
            for node in reconciled
        ]
        return state.model_copy(update={"run_plan": reconciled})

    @staticmethod
    def _provider_task_stage_status(
        *,
        selected: bool,
        task_state: FilmProviderTaskState | None,
        derivation_status: str = "fresh",
        locked: bool = False,
        stale_decision: str = "",
    ) -> FilmStageStatus:
        if stale_decision == "preserve_old_version" and selected:
            return FilmStageStatus.COMPLETED
        if derivation_status in {"stale", "conflict", "blocked"}:
            return FilmStageStatus.BLOCKED if locked else FilmStageStatus.PENDING
        if selected or task_state == FilmProviderTaskState.SUCCEEDED:
            return FilmStageStatus.COMPLETED
        if task_state in {FilmProviderTaskState.PENDING, FilmProviderTaskState.RUNNING}:
            return FilmStageStatus.ACTIVE
        return FilmStageStatus.PENDING

    def mark_run_plan_failed(self, state: FilmStudioState, node_id: str) -> FilmStudioState:
        """Record one failed execution node and revert its subgraph to pending."""

        if not any(node.node_id == node_id for node in state.run_plan):
            return state
        updated = invalidate_downstream(state.run_plan, {node_id})
        return state.model_copy(update={"run_plan": updated})

    def rerun_node(self, node_id: str) -> FilmStudioState:
        """Revert one node (and its dependents) to pending for re-execution."""

        state = self.get_or_bootstrap()
        if not any(node.node_id == node_id for node in state.run_plan):
            raise ValueError(f"Unknown run-plan node: {node_id}")
        return self.store.save(self.mark_run_plan_failed(state, node_id))

    def workflow_view(self, state: FilmStudioState | None = None) -> dict[str, Any]:
        """ComfyUI-style execution snapshot for the frontend: topological
        order, currently-ready nodes and overall completion ratio."""

        state = state or self.get_or_bootstrap()
        ordered = topological_order(state.run_plan)
        return {
            "nodes": [node.model_dump() for node in ordered],
            "ready": [node.node_id for node in ready_nodes(state.run_plan)],
            "completion_ratio": completion_ratio(state.run_plan),
        }

    def graph_view(self) -> FilmGraphView:
        self.get_or_bootstrap()
        graph = self.workflow_repository.get_graph()
        runs = self.workflow_repository.list_runs(limit=1)
        return FilmGraphView(
            definition=graph,
            validation_issues=validate_graph(graph),
            latest_run=runs[0] if runs else None,
        )

    def save_graph(
        self,
        graph: FilmGraphDefinition,
        *,
        expected_revision: int,
    ) -> FilmGraphView:
        saved = self.workflow_repository.save_graph(
            graph,
            expected_revision=expected_revision,
        )
        runs = self.workflow_repository.list_runs(limit=1)
        return FilmGraphView(
            definition=saved,
            validation_issues=validate_graph(saved),
            latest_run=runs[0] if runs else None,
        )

    def validate_graph(self, graph: FilmGraphDefinition) -> FilmGraphView:
        return FilmGraphView(
            definition=graph,
            validation_issues=validate_graph(graph),
            latest_run=None,
        )

    def estimate_graph_run(
        self,
        *,
        scope: FilmGraphRunScope,
        target_node_ids: list[str],
    ) -> FilmRunEstimate:
        self.get_or_bootstrap()
        graph = self.workflow_repository.get_graph()
        return estimate_run(
            graph,
            scope=scope,
            target_node_ids=target_node_ids,
            prior_runs=self.workflow_repository.list_runs(limit=100),
            budget_usd=float(getattr(self.settings, "film_project_budget_usd", 50.0)),
        )

    def create_graph_run(
        self,
        *,
        scope: FilmGraphRunScope,
        target_node_ids: list[str],
        confirmed_cost: bool,
        high_priority: bool = False,
    ) -> FilmGraphRun:
        self.get_or_bootstrap()
        graph = self.workflow_repository.get_graph()
        estimate = estimate_run(
            graph,
            scope=scope,
            target_node_ids=target_node_ids,
            prior_runs=self.workflow_repository.list_runs(limit=100),
            budget_usd=float(getattr(self.settings, "film_project_budget_usd", 50.0)),
        )
        run = self.workflow_repository.save_run(
            build_run(
                graph,
                estimate,
                confirmed_cost=confirmed_cost,
                high_priority=high_priority,
            )
        )
        self.workflow_repository.append_event(
            FilmGraphEvent(
                run_id=run.run_id,
                sequence=1,
                kind="run_created",
                message={
                    FilmGraphRunStatus.BLOCKED: "工作流校验未通过",
                    FilmGraphRunStatus.WAITING_CONFIRMATION: "等待费用确认",
                }.get(run.status, "工作流已进入队列"),
                data={
                    "graph_revision": run.graph_revision,
                    "estimated_cost_usd": run.estimate.estimated_cost_usd,
                },
            )
        )
        return run

    def cancel_graph_run(self, run_id: str) -> FilmGraphRun:
        run = self.workflow_repository.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown workflow run: {run_id}")
        if run.status in {
            FilmGraphRunStatus.SUCCEEDED,
            FilmGraphRunStatus.FAILED,
            FilmGraphRunStatus.CANCELLED,
        }:
            return run
        attempts = [
            attempt.model_copy(update={"status": FilmGraphRunStatus.CANCELLED})
            if attempt.status
            in {
                FilmGraphRunStatus.QUEUED,
                FilmGraphRunStatus.WAITING_CONFIRMATION,
            }
            else attempt
            for attempt in run.attempts
        ]
        cancelled = self.workflow_repository.save_run(
            run.model_copy(update={"status": FilmGraphRunStatus.CANCELLED, "attempts": attempts})
        )
        events = self.workflow_repository.list_events(run_id)
        self.workflow_repository.append_event(
            FilmGraphEvent(
                run_id=run_id,
                sequence=len(events) + 1,
                kind="run_cancelled",
                message="工作流运行已取消",
            )
        )
        return cancelled

    def _next_stage(self, stage: FilmStage) -> FilmStage:
        index = FILM_STAGE_ORDER.index(stage)
        return FILM_STAGE_ORDER[min(index + 1, len(FILM_STAGE_ORDER) - 1)]

    @staticmethod
    def _shots_ready(state: FilmStudioState) -> bool:
        return bool(state.shots) and all(shot.selected_asset_url for shot in state.shots)

    @staticmethod
    def _block_media_stage(state: FilmStudioState) -> FilmStudioState:
        stages: list[FilmStageState] = []
        for item in state.stages:
            if item.stage == FilmStage.SHOT_PRODUCTION:
                stages.append(
                    item.model_copy(
                        update={
                            "status": FilmStageStatus.BLOCKED,
                            "warnings": [
                                "自主流程已完成策划、剧本、资产规划和分镜；付费镜头生成需明确授权后继续。"
                            ],
                        }
                    )
                )
            else:
                stages.append(item)
        return state.model_copy(update={"stages": stages})

    def _require_settings(self) -> Any:
        if self.settings is None:
            raise RuntimeError("Film media generation requires runtime settings")
        return self.settings

    @staticmethod
    def _merge_locked_work(
        projected: FilmStudioState, previous: FilmStudioState
    ) -> FilmStudioState:
        old_assets = {item.asset_id: item for item in previous.visual_assets if item.locked}
        assets = [old_assets.get(item.asset_id, item) for item in projected.visual_assets]
        old_shots = {item.shot_id: item for item in previous.shots if item.locked}
        shots = [old_shots.get(item.shot_id, item) for item in projected.shots]
        return projected.model_copy(
            update={
                "mode": previous.mode,
                "visual_assets": assets,
                "shots": shots,
                "timeline": previous.timeline,
            }
        )

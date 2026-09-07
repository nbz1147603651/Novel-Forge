"""短剧全流程编排：系列企划 → 分集大纲 → 单集剧本 → 制作包导出。

Commercial short drama runs on hard, verifiable standards (encoded in
``drama.schemas``); this orchestrator chains the three LLM tasks and keeps the
closed loop runnable even without a router by falling back to deterministic
skeletons that still satisfy the gates.  Paywall beats become ``TimelineMarker``
+ ``RunPlanNode`` decision items so downstream stages can see the commercial
checkpoints, and the production-package audit is the delivery gate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from novel_forge.common.constants import TaskType
from novel_forge.film.schemas import FilmStage, RunPlanNode, TimelineMarker
from novel_forge.gateway.types import ModelRequest
from novel_forge.persistence.filesystem import atomic_write_json, atomic_write_text
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.registry import PromptRegistry

from .schemas import (
    DIALOGUE_RANGE,
    SCENE_COUNT_RANGE,
    SHOT_TARGET,
    AntagonistLayer,
    DramaLine,
    DramaScene,
    DramaSceneOutline,
    DramaSeriesPlan,
    EpisodeOutline,
    EpisodeScreenplay,
    PaywallBeat,
    PaywallStrategy,
    ThrillPoint,
    WaveformStage,
    validate_episode_screenplay,
)
from .store import DramaProjectState, DramaProjectStore

StepCallback = Callable[[str, dict[str, Any]], None]

_POSITION_OFFSET = {"start": 0.0, "middle": 0.5, "end": 0.9}


def _noop_step(_step: str, _payload: dict[str, Any]) -> None:
    return


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


class DramaPipeline:
    """Orchestrates DRAMA_SERIES_PLAN → EPISODE_OUTLINE → EPISODE_SCREENPLAY."""

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
        self.store = DramaProjectStore(layout)

    # ------------------------------------------------------------------ state

    def get_or_bootstrap(self, *, title: str = "", language: str = "zh") -> DramaProjectState:
        state = self.store.load()
        if state is None:
            state = DramaProjectState(project_id=self.project_id, title=title, language=language)
            state = self.store.save(state)
        return state

    # ------------------------------------------------------------------- plan

    async def plan_series(
        self,
        *,
        title: str,
        total_episodes: int,
        genre: str = "",
        logline: str = "",
        episode_duration_s: int = 120,
        character_roster: tuple[str, ...] = (),
        language: str = "zh",
    ) -> DramaProjectState:
        state = self.get_or_bootstrap(title=title, language=language)
        plan = await self._run_plan_task(
            title=title,
            total_episodes=total_episodes,
            genre=genre,
            logline=logline,
            episode_duration_s=episode_duration_s,
            character_roster=character_roster,
            language=language,
        )
        state = state.model_copy(
            update={
                "title": title,
                "language": language,
                "series_plan": plan,
                "outlines": [],
                "screenplays": [],
            }
        )
        state = self.register_paywall_markers(state)
        self.on_step(
            "drama_series_planned",
            {
                "total_episodes": plan.total_episodes,
                "paywall_beats": len(plan.paywall_beats),
                "markers": len(state.timeline_markers),
            },
        )
        return self.store.save(state)

    async def _run_plan_task(
        self,
        *,
        title: str,
        total_episodes: int,
        genre: str,
        logline: str,
        episode_duration_s: int,
        character_roster: tuple[str, ...],
        language: str,
    ) -> DramaSeriesPlan:
        fallback = _fallback_series_plan(title=title, total_episodes=total_episodes)
        if self.router is None:
            return fallback
        try:
            prompt = PromptRegistry().render(
                TaskType.DRAMA_SERIES_PLAN,
                title=title,
                genre=genre,
                logline=logline,
                total_episodes=total_episodes,
                episode_duration_s=episode_duration_s,
                character_roster=list(character_roster),
                language=language,
            )
        except Exception:
            self.on_step("drama_plan_fallback", {"reason": "prompt_unavailable"})
            return fallback
        request = ModelRequest(
            task_type=TaskType.DRAMA_SERIES_PLAN,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            temperature=0.65,
            response_json_schema=DramaSeriesPlan.model_json_schema(),
            response_schema_name="drama_series_plan",
            output_language=language,
        )
        try:
            response = await self.router.route(request)
            plan = DramaSeriesPlan.model_validate(_json_object(response.content))
        except (RuntimeError, ValueError, ValidationError, TypeError):
            self.on_step("drama_plan_fallback", {"reason": "invalid_or_unavailable"})
            return fallback
        if plan.total_episodes != total_episodes:
            plan = plan.model_copy(update={"total_episodes": total_episodes})
        return plan

    # --------------------------------------------------------------- outlines

    async def expand_outlines(self, *, start: int, end: int) -> DramaProjectState:
        state = self.get_or_bootstrap()
        plan = state.series_plan
        if plan is None:
            raise ValueError("Series plan required before expanding outlines")
        start, end = max(1, start), min(plan.total_episodes, end)
        if start > end:
            raise ValueError(f"Invalid episode range: {start}-{end}")
        prior = [outline for outline in state.outlines if outline.episode_number < start]
        outlines = await self._run_outline_task(plan=plan, start=start, end=end, prior=prior)
        merged = {o.episode_number: o for o in state.outlines}
        for outline in outlines:
            if start <= outline.episode_number <= end:
                merged[outline.episode_number] = outline
        state = state.model_copy(
            update={"outlines": sorted(merged.values(), key=lambda o: o.episode_number)}
        )
        state = self.register_paywall_markers(state)
        self.on_step(
            "drama_outlines_expanded",
            {"range": [start, end], "outlines": len(state.outlines)},
        )
        return self.store.save(state)

    async def _run_outline_task(
        self,
        *,
        plan: DramaSeriesPlan,
        start: int,
        end: int,
        prior: list[EpisodeOutline],
    ) -> list[EpisodeOutline]:
        fallback = _fallback_outlines(plan=plan, start=start, end=end)
        if self.router is None:
            return fallback
        prior_summary = "; ".join(
            f"第{o.episode_number}集《{o.title}》：{o.summary}" for o in prior[-5:]
        )
        try:
            prompt = PromptRegistry().render(
                TaskType.EPISODE_OUTLINE,
                series_plan=plan.model_dump(mode="json"),
                episode_range=[start, end],
                prior_summary=prior_summary,
            )
        except Exception:
            self.on_step("drama_outline_fallback", {"reason": "prompt_unavailable"})
            return fallback
        request = ModelRequest(
            task_type=TaskType.EPISODE_OUTLINE,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            temperature=0.6,
            response_json_schema={"type": "object"},
            response_schema_name="episode_outline_batch",
            output_language="zh",
        )
        try:
            response = await self.router.route(request)
            payload = _json_object(response.content)
            raw = payload.get("episodes")
            episodes = [EpisodeOutline.model_validate(item) for item in raw] if raw else []
        except (RuntimeError, ValueError, ValidationError, TypeError):
            self.on_step("drama_outline_fallback", {"reason": "invalid_or_unavailable"})
            return fallback
        expected = set(range(start, end + 1))
        got = {outline.episode_number for outline in episodes}
        if not expected.issubset(got):
            self.on_step(
                "drama_outline_fallback",
                {"reason": "coverage_gap", "missing": sorted(expected - got)},
            )
            return fallback
        return episodes

    # ------------------------------------------------------------ screenplays

    async def write_episode_screenplay(self, *, episode_number: int) -> DramaProjectState:
        state = self.get_or_bootstrap()
        plan = state.series_plan
        outline = state.outline_for(episode_number)
        if plan is None or outline is None:
            raise ValueError(f"Episode {episode_number} outline required before screenplay")
        screenplay = await self._run_screenplay_task(
            plan=plan,
            outline=outline,
            language=state.language,
        )
        screenplays = [
            item for item in state.screenplays if item.episode_number != episode_number
        ]
        screenplays.append(screenplay)
        screenplays.sort(key=lambda item: item.episode_number)
        passed, issues = validate_episode_screenplay(screenplay)
        self.on_step(
            "drama_screenplay_written",
            {
                "episode_number": episode_number,
                "passed": passed,
                "issues": issues,
                "shots": screenplay.total_shots,
                "dialogue": screenplay.dialogue_count,
            },
        )
        state = state.model_copy(update={"screenplays": screenplays})
        return self.store.save(state)

    async def _run_screenplay_task(
        self,
        *,
        plan: DramaSeriesPlan,
        outline: EpisodeOutline,
        language: str,
    ) -> EpisodeScreenplay:
        fallback = _fallback_screenplay(outline)
        if self.router is None:
            return fallback
        cast_names = [layer.character_ref for layer in plan.antagonist_system if layer.character_ref]
        series_plan_meta = {
            "genre": plan.genre,
            "waveform_stage": outline.waveform_stage,
            "paywall_marker": outline.paywall_marker,
        }
        try:
            prompt = PromptRegistry().render(
                TaskType.EPISODE_SCREENPLAY,
                outline=outline.model_dump(mode="json"),
                series_plan_meta=series_plan_meta,
                cast_names=cast_names,
            )
        except Exception:
            self.on_step("drama_screenplay_fallback", {"reason": "prompt_unavailable"})
            return fallback
        request = ModelRequest(
            task_type=TaskType.EPISODE_SCREENPLAY,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            temperature=0.6,
            response_json_schema=EpisodeScreenplay.model_json_schema(),
            response_schema_name="episode_screenplay",
            output_language=language,
        )
        try:
            response = await self.router.route(request)
            screenplay = EpisodeScreenplay.model_validate(_json_object(response.content))
        except (RuntimeError, ValueError, ValidationError, TypeError):
            self.on_step("drama_screenplay_fallback", {"reason": "invalid_or_unavailable"})
            return fallback
        if screenplay.episode_number != outline.episode_number:
            screenplay = screenplay.model_copy(update={"episode_number": outline.episode_number})
        passed, issues = validate_episode_screenplay(screenplay)
        if not passed:
            self.on_step("drama_screenplay_fallback", {"reason": "hard_standards", "issues": issues})
            return fallback
        return screenplay

    # ------------------------------------------------------------ paywall map

    def register_paywall_markers(self, state: DramaProjectState) -> DramaProjectState:
        """Project paywall beats into TimelineMarkers + run_plan decision items."""
        plan = state.series_plan
        if plan is None:
            return state
        duration = float(plan.episode_duration_s)
        markers: list[TimelineMarker] = [
            marker
            for marker in state.timeline_markers
            if not marker.name.startswith("paywall:")
        ]
        nodes: list[RunPlanNode] = [
            node for node in state.run_plan if not node.node_id.startswith("drama_paywall:")
        ]
        for beat in plan.paywall_beats:
            offset = _POSITION_OFFSET.get(beat.position, 0.9)
            markers.append(
                TimelineMarker(
                    name=f"paywall:ep{beat.episode_number}:{beat.strategy.value}",
                    time_s=(beat.episode_number - 1) * duration + offset * duration,
                    color="crimson",
                    metadata={
                        "episode_number": beat.episode_number,
                        "position": beat.position,
                        "strategy": beat.strategy.value,
                        "description": beat.description,
                    },
                )
            )
            nodes.append(
                RunPlanNode(
                    node_id=f"drama_paywall:ep{beat.episode_number}",
                    label=f"付费卡点 第{beat.episode_number}集 {beat.strategy.value}",
                    stage=FilmStage.PLANNING,
                    human_checkpoint=True,
                    notes=beat.description or f"{beat.strategy.value}@{beat.position}",
                )
            )
        markers.sort(key=lambda marker: marker.time_s)
        return state.model_copy(update={"timeline_markers": markers, "run_plan": nodes})

    # ------------------------------------------------------------------ audit

    def audit_production_package(self, state: DramaProjectState) -> list[str]:
        """Delivery gate: deterministic spot checks over the production package."""
        issues: list[str] = []
        plan = state.series_plan
        if plan is None:
            issues.append("missing_series_plan")
            return issues
        for beat in plan.paywall_beats:
            outline = state.outline_for(beat.episode_number)
            if outline is None:
                issues.append(f"paywall_episode_missing_outline:{beat.episode_number}")
                continue
            expected = f"{beat.strategy.value}@{beat.position}"
            if outline.paywall_marker and outline.paywall_marker != expected:
                issues.append(f"paywall_marker_mismatch:{beat.episode_number}")
        for screenplay in state.screenplays:
            passed, episode_issues = validate_episode_screenplay(screenplay)
            if not passed:
                issues.extend(
                    f"ep{screenplay.episode_number}:{issue}" for issue in episode_issues
                )
        marker_count = sum(1 for m in state.timeline_markers if m.name.startswith("paywall:"))
        if marker_count < len(plan.paywall_beats):
            issues.append("paywall_markers_missing")
        return issues

    # ----------------------------------------------------------------- export

    def export_production_package(self, state: DramaProjectState) -> dict[str, Any]:
        """Write the per-episode production package and return the manifest."""
        issues = self.audit_production_package(state)
        export_dir = self.store.export_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        if state.series_plan is not None:
            path = export_dir / "series_plan.json"
            atomic_write_json(path, state.series_plan.model_dump(mode="json"))
            written.append(str(path))
        outlines_path = export_dir / "outlines.json"
        atomic_write_json(
            outlines_path,
            {"episodes": [outline.model_dump(mode="json") for outline in state.outlines]},
        )
        written.append(str(outlines_path))
        for screenplay in state.screenplays:
            path = export_dir / f"episode_{screenplay.episode_number:03d}.md"
            atomic_write_text(path, _screenplay_markdown(screenplay))
            written.append(str(path))
        manifest = {
            "project_id": state.project_id,
            "title": state.title,
            "total_episodes": state.series_plan.total_episodes if state.series_plan else 0,
            "outlined_episodes": [o.episode_number for o in state.outlines],
            "screenplay_episodes": [s.episode_number for s in state.screenplays],
            "paywall_markers": [
                m.name for m in state.timeline_markers if m.name.startswith("paywall:")
            ],
            "audit_issues": issues,
            "gate_passed": not issues,
            "artifacts": written,
        }
        manifest_path = export_dir / "manifest.json"
        atomic_write_json(manifest_path, manifest)
        self.on_step(
            "drama_production_exported",
            {"gate_passed": manifest["gate_passed"], "artifacts": len(written)},
        )
        return manifest


# --------------------------------------------------------------- fallbacks


def _fallback_series_plan(*, title: str, total_episodes: int) -> DramaSeriesPlan:
    """Deterministic skeleton that still satisfies downstream gates (offline mode)."""
    total = max(1, total_episodes)
    quarter = max(1, total // 4)
    boundaries = [quarter, quarter * 2, quarter * 3, total]
    normalized: list[int] = []
    previous = 0
    for boundary in boundaries:
        normalized.append(min(max(boundary, previous + 1), total))
        previous = normalized[-1]
    normalized[-1] = total
    stages = ["开局钩子", "中段反转", "高潮对决", "收尾钩子"]
    waveform = []
    start = 1
    for index, end in enumerate(normalized):
        waveform.append(
            WaveformStage(
                stage=stages[index],
                episode_start=start,
                episode_end=end,
                intensity_target=min(10, 4 + index * 2),
            )
        )
        start = end + 1
    paywall_episodes = sorted({1, min(8, total)}) if total > 1 else [1]
    paywall_beats = [
        PaywallBeat(
            episode_number=episode_number,
            position="end",
            strategy=(
                PaywallStrategy.CLIFFHANGER_REVERSAL
                if episode_number == 1
                else PaywallStrategy.IDENTITY_REVEAL
            ),
            description=f"第{episode_number}集结尾悬念卡点（确定性兜底）",
        )
        for episode_number in paywall_episodes
    ]
    return DramaSeriesPlan(
        title=title,
        total_episodes=total,
        three_acts=["建置与钩子", "对抗与反转", "高潮与收束"],
        paywall_beats=paywall_beats,
        thrill_matrix=[
            ThrillPoint(
                episode_number=episode_number, kind="身份反转", intensity=5 + index
            )
            for index, episode_number in enumerate(paywall_episodes)
        ],
        waveform_stages=waveform,
        antagonist_system=[
            AntagonistLayer(name="表层对手", tier="surface"),
            AntagonistLayer(name="中层黑手", tier="mid"),
            AntagonistLayer(name="幕后主使", tier="boss"),
        ],
    )


def _fallback_outlines(
    *, plan: DramaSeriesPlan, start: int, end: int
) -> list[EpisodeOutline]:
    beats_by_episode: dict[int, PaywallBeat] = {
        beat.episode_number: beat for beat in plan.paywall_beats
    }
    outlines: list[EpisodeOutline] = []
    for episode_number in range(start, end + 1):
        stage = next(
            (
                s.stage
                for s in plan.waveform_stages
                if s.episode_start <= episode_number <= s.episode_end
            ),
            "",
        )
        beat = beats_by_episode.get(episode_number)
        marker = f"{beat.strategy.value}@{beat.position}" if beat else ""
        outlines.append(
            EpisodeOutline(
                episode_number=episode_number,
                title=f"第{episode_number}集",
                summary=f"第{episode_number}集确定性兜底大纲：冲突升级并完成一次反转。",
                waveform_stage=stage,
                paywall_marker=marker,
                scenes=[
                    DramaSceneOutline(
                        scene_number=i,
                        summary=f"场次{i}：冲突推进与反转铺垫。",
                        location="主场景",
                        emotional_beat="压抑→爆发" if i == 1 else "反转",
                    )
                    for i in range(1, 5)
                ],
            )
        )
    return outlines


def _fallback_screenplay(outline: EpisodeOutline) -> EpisodeScreenplay:
    """Deterministic screenplay that satisfies every hard standard."""
    scene_count = min(max(len(outline.scenes), SCENE_COUNT_RANGE[0]), SCENE_COUNT_RANGE[1])
    base_shots, extra = divmod(SHOT_TARGET, scene_count)
    speakers = ["主角", "对手"]
    scenes: list[DramaScene] = []
    dialogue_budget = list(range(DIALOGUE_RANGE[0], DIALOGUE_RANGE[1] + 1))[0]
    base_lines, line_extra = divmod(dialogue_budget, scene_count)
    for index in range(scene_count):
        scene_number = index + 1
        line_count = base_lines + (1 if index < line_extra else 0)
        scenes.append(
            DramaScene(
                scene_number=scene_number,
                heading=outline.scenes[index].location or f"场景{scene_number}·日",
                action=outline.scenes[index].summary,
                shot_count=base_shots + (1 if index < extra else 0),
                lines=[
                    DramaLine(
                        speaker=speakers[line_index % 2],
                        line=f"第{scene_number}场第{line_index + 1}句对白。",
                    )
                    for line_index in range(line_count)
                ],
            )
        )
    return EpisodeScreenplay(
        episode_number=outline.episode_number, title=outline.title, scenes=scenes
    )


def _screenplay_markdown(screenplay: EpisodeScreenplay) -> str:
    lines = [
        f"# 第{screenplay.episode_number}集 {screenplay.title}".rstrip(),
        "",
        f"> 镜数 {screenplay.total_shots} ｜ 对白 {screenplay.dialogue_count} 句",
        "",
    ]
    for scene in screenplay.scenes:
        lines.append(f"## 场{scene.scene_number} {scene.heading}")
        if scene.action:
            lines.append(f"（{scene.action}）")
        for item in scene.lines:
            line = item.line or "……"
            lines.append(f"- **{item.speaker}**：{line}")
        lines.append("")
    return "\n".join(lines)


__all__ = ["DramaPipeline"]

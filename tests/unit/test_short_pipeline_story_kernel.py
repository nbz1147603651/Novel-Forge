"""Tests for short-story StoryKernel projection and persistence."""

from __future__ import annotations

from pathlib import Path

from novel_forge.core.schemas.beats import Beat, StoryBeats
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.short_blueprint import (
    AnchorCharacter,
    ShortBlueprint,
    ShortNarrativePhase,
    StoryAnchor,
)
from novel_forge.core.schemas.short_creative import (
    CharacterAnalysis,
    NarrativeAnalysis,
    ShortCreativeSummary,
)
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.story_kernel.entity_projection import short_context_projection_from_kernel
from novel_forge.story_kernel.state_writer import StoryKernelStateWriter
from novel_forge.story_kernel.store import StoryKernelStore


async def test_initialize_short_project_writes_lightweight_kernel(tmp_path: Path) -> None:
    store = StoryKernelStore(tmp_path / "story_kernel.db", wal_mode=False)
    try:
        writer = StoryKernelStateWriter(store)
        kernel = await writer.initialize_short_project(
            project_id="short-demo",
            spec=_spec(),
            blueprint=_blueprint(),
            beats=_beats(),
            execution_plan={"segments": [{"index": 1}]},
            artifact_refs={"short_spec": {"path": "spec.json"}},
        )
    finally:
        await store.close()

    assert (tmp_path / "story_kernel.db").exists()
    assert kernel.project_mode == "short"
    assert kernel.title == "短篇"
    assert {entity.name for entity in kernel.entities} >= {"林晚", "旧车站", "悬疑", "冷峻"}
    assert len(kernel.timeline) == 2
    assert any(promise.entry_id == "short_central_event" for promise in kernel.promise_ledger)


async def test_merge_short_outcome_records_summary_and_artifact_ref(tmp_path: Path) -> None:
    final_path = tmp_path / "chapters" / "short_story.md"
    final_path.parent.mkdir(parents=True)
    final_text = "林晚在旧车站完成了选择。"
    final_path.write_text(final_text, encoding="utf-8")

    store = StoryKernelStore(tmp_path / "story_kernel.db", wal_mode=False)
    try:
        writer = StoryKernelStateWriter(store)
        await writer.initialize_short_project(
            project_id="short-demo",
            spec=_spec(),
            blueprint=_blueprint(),
            beats=_beats(),
        )
        kernel = await writer.merge_short_outcome(
            project_id="short-demo",
            final_text=final_text,
            eval_report=EvalReport(overall_score=8.0, passed=True, summary="完整收束。"),
            creative_summary=ShortCreativeSummary(
                characters=[
                    CharacterAnalysis(
                        name="林晚",
                        role="主角",
                        arc_summary="从犹疑到决断。",
                        key_traits=["克制"],
                    )
                ],
                narrative_analysis=NarrativeAnalysis(
                    pacing_assessment="节奏紧凑。",
                    ending_impact="结尾回收核心选择。",
                ),
            ),
            final_story_path=final_path,
        )
    finally:
        await store.close()

    assert kernel.current_chapter == 1
    assert kernel.chapter_summaries[1] == "节奏紧凑。；结尾回收核心选择。"
    assert kernel.artifact_refs["short_story_final"]["path"] == "chapters/short_story.md"
    lin_wan = next(entity for entity in kernel.entities if entity.name == "林晚")
    assert lin_wan.attributes["arc_summary"] == "从犹疑到决断。"

    projection = short_context_projection_from_kernel(kernel)
    assert projection["chapter_summaries"][1]
    assert projection["timeline"]


def _spec() -> StorySpec:
    return StorySpec(
        title="短篇",
        genre="悬疑",
        theme="一次迟到的告别",
        tone="冷峻",
        characters_hint="林晚",
        conflict_hint="林晚必须决定是否公开旧案真相",
    )


def _blueprint() -> ShortBlueprint:
    return ShortBlueprint(
        synopsis="林晚回到旧车站，面对被隐藏的真相。",
        anchor_elements=StoryAnchor(
            primary_locations=["旧车站"],
            core_characters=[AnchorCharacter(name="林晚", role="主角")],
            central_event="林晚发现旧案证物",
        ),
        narrative_phases=[
            ShortNarrativePhase(
                phase_name="开端",
                description="林晚抵达旧车站。",
                location="旧车站",
                characters_present=["林晚"],
                key_event="证物出现",
            )
        ],
        ending_strategy="真相公开，人物完成告别",
    )


def _beats() -> StoryBeats:
    return StoryBeats(
        beats=[
            Beat(
                sequence=1,
                summary="林晚抵达旧车站。",
                characters_involved=["林晚"],
                setting="旧车站",
            ),
            Beat(
                sequence=2,
                summary="林晚选择公开真相。",
                characters_involved=["林晚"],
                setting="旧车站",
            ),
        ],
    )

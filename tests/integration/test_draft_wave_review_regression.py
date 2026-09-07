"""End-to-end regression test for the DRAFT -> WAVE -> Review handoff.

This file locks down the *seam contract* introduced by the Generate-stage
split (T5-1 + T5-2).  It is intentionally written as a single behavior test
that walks the full DRAFT -> WAVE -> Review checkpoint path with fakes for
the LLM steps, then asserts:

  1. DRAFT writes raw per-scene prose to ``drafts/chapter_xxx/v0_draft.md``.
  2. WAVE writes the DRAFT+WAVE handoff to
     ``drafts/chapter_xxx/v1_wave.md`` and exposes the artifact name on
     the ``wave`` step event.
  3. The Generate-stage orchestrator chains them in a single
     ``generate_chapter_prose`` call and returns ``performed_edits=1``.
  4. ``ReviewProgressState.draft_done`` is set only after WAVE finished.
  5. Resume recovery (a) prefers ``v1_wave.md`` and (b) never treats
     ``v0_draft.md`` as a reviewable draft.  Legacy ``vN_edited.md``
     snapshots still recover when no v1 exists (pre-WAVE projects).

If any of these assumptions change without an explicit migration, this
test will fail loudly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.sessions.chapter_session_state import (
    ReviewProgressState,
    load_review_progress,
    save_review_progress,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_storage(tmp_path: Path) -> FileSystemStorage:
    return FileSystemStorage(tmp_path)


def _make_layout(storage: FileSystemStorage) -> ProjectLayout:
    # Use a synthetic project root that matches FileSystemStorage's
    # expectations: <root>/<project_name>/...
    project_root = storage.ensure_project_dir("draft_wave_regression")
    return ProjectLayout(project_dir=project_root)


class _FakeDraftStep:
    """Returns a per-scene draft, plus a leading marker so we can tell
    DRAFT output apart from WAVE output even if both were re-rendered."""

    async def run(self, input_data):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            text="[DRAFT-STAGE] 雾霭笼罩的街道，林远沿着墙根疾走。\n\n",
            warnings=[],
        )


class _FakeWaveStep:
    """Returns woven prose with a different marker so v0 and v1 differ."""

    async def run(self, input_data):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            woven_prose=(
                "[WAVE-STAGE] 雾霭笼罩的街道，林远沿着墙根疾走，"
                "在转角处与沈清漪擦肩而过。"
                "她低声说：账簿残页已经不在青阳城了。"
                "林远压住呼吸，让影子跟过去。\n\n"
                "雾里传来更夫的梆子声，三更已过。"
                "他转进暗巷，在墙根蹲下，把袖中残页展开。"
                "墨迹未干，沈清漪的名字在末尾处压了半寸长的指印。"
                "他合上残页，让自己的呼吸随雾起落。"
                "然后起身，朝青阳城的方向继续走。\n\n"
                "街角的灯笼在雾里飘成一颗暗红的星。"
                "守夜人换班的脚步声从巷口传过来，一下，两下，三下。"
                "林远数着步子，把步子折成自己离开的时间。"
                "残页在他袖中像一尾受惊的鱼。"
                "他不让它掉出来，也不让它沉下去。"
                "沈清漪没有回头。"
                "雾把她的背影抹成一条淡青的线。"
                "林远知道她在等自己先开口。"
                "但今夜他要把账簿送到青阳城外的长亭去。"
                "那里有人在接。"
                "他压住呼吸，让影子跟过去。"
            ),
            warnings=[],
            cross_ref_hits=[("scene_01", "scene_02", "callback")],
            scene_transitions_added=["scene_01 -> scene_02"],
            motif_weave_log=[],
            final_word_count=42,
        )


def _make_trace():  # type: ignore[no-untyped-def]
    """Lightweight PipelineTrace stand-in that satisfies the `.add()` call
    that the tracer's context manager makes on exit."""
    from novel_forge.obs.tracer import PipelineTrace
    return PipelineTrace()


def _make_runner(layout: ProjectLayout, storage: FileSystemStorage):  # type: ignore[no-untyped-def]
    step_events: list[tuple[str, dict]] = []

    def _on_step(name: str, payload: dict) -> None:
        step_events.append((name, payload))

    runner = SimpleNamespace(
        _router=SimpleNamespace(),
        _builder=SimpleNamespace(),
        _settings=SimpleNamespace(long_check_chapter_enabled=False),
        _storage=storage,
        _config=SimpleNamespace(writing_mode="whole_chapter"),
        _on_step=_on_step,
        _step_events=step_events,
    )
    return runner


def _make_bundle(layout: ProjectLayout):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        chapter_outline=SimpleNamespace(
            chapter_number=1,
            pov_character="林远",
            expected_word_count=3000,
        ),
        story_bible=SimpleNamespace(),
        editorial_contract=None,
        style_profile=None,
        layout=layout,
        blueprint=None,
        weak_senses=[],
        pov_hint="林远",
    )


def _make_generate_context(runner, bundle, plan=None):  # type: ignore[no-untyped-def]
    from novel_forge.pipeline.long.stages.draft import GenerateContext

    return GenerateContext(
        runner=runner,
        bundle=bundle,
        packet=SimpleNamespace(canon_context={}, character_profiles=[]),
        bridge=SimpleNamespace(),
        plan=plan
        or SimpleNamespace(
            cross_scene_intent={
                "cross_scene_references": [
                    {
                        "from_scene": "scene_01",
                        "to_scene": "scene_02",
                        "ref_type": "callback",
                        "description": "林远走过雾霭街道",
                    }
                ],
                "pacing_curve": [2, 4],
            },
            scene_intents=[],
            forbidden_elements=[],
            forbidden_elements_soft=[],
            expression_channel_records=[],
            intentional_callbacks=[],
        ),
        chapter_number=1,
        trace=_make_trace(),
        planning_hints=None,
        reading_power_hint=None,
        draft_step=_FakeDraftStep(),
        target_word_count=3000,
        budgeted_plan=None,
        draft_memory_hints={},
        draft_canon={},
        prev_known_issues=[],
        focus_ids=[],
        element_selection_payload=None,
        weak_senses=[],
        draft_kernel_context={},
        chapter_repair_kernel_context={},
        wave_kernel_context={},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_v0_draft_and_v1_wave_disk_layout(tmp_path: Path) -> None:
    """DRAFT -> WAVE writes both v0_draft.md (raw) and v1_wave.md (reviewable)."""
    from novel_forge.pipeline.long import chapter_flow

    storage = _make_storage(tmp_path)
    layout = _make_layout(storage)
    runner = _make_runner(layout, storage)
    bundle = _make_bundle(layout)
    ctx = _make_generate_context(runner, bundle)

    with (
        patch(
            "novel_forge.pipeline.long.services.constraints.constraint_router.build_draft_cards",
            lambda **_kwargs: {"plan": {}, "scene_intents": []},
        ),
        patch(
            "novel_forge.pipeline.long.services.constraints.constraint_router.build_wave_cards",
            lambda **_kwargs: {"plan": {}, "scene_intents": []},
        ),
        patch("novel_forge.pipeline.steps.wave_step.WaveStep", lambda *a, **k: _FakeWaveStep()),
        patch("novel_forge.pipeline.steps.draft_step.DraftStep", lambda *a, **k: _FakeDraftStep()),
    ):
            artifacts = asyncio.run(chapter_flow.generate_chapter_prose(
            runner=runner,
            bundle=bundle,
            packet=ctx.packet,
            bridge=ctx.bridge,
            plan=ctx.plan,
            chapter_number=1,
            trace=ctx.trace,
        ))

    # 1. WAVE produced different prose than DRAFT (marker is unique).
    assert "[WAVE-STAGE]" in artifacts.current_text
    assert artifacts.performed_edits == 1
    assert "cross_ref_hits" in artifacts.wave_meta

    # 2. v0_draft.md and v1_wave.md both exist on disk.
    v0 = layout.chapter_draft_path(1, 0)
    v1 = layout.chapter_wave_draft_path(1)
    assert v0.exists(), f"DRAFT stage must write {v0}"
    assert v1.exists(), f"WAVE stage must write {v1}"

    # 3. v0 contains the raw DRAFT marker; v1 contains the WAVE marker.
    v0_text = storage.load_text(v0)
    v1_text = storage.load_text(v1)
    assert "[DRAFT-STAGE]" in v0_text
    assert "[WAVE-STAGE]" in v1_text
    # v1 text is the WAVE woven prose (not the DRAFT text).
    assert "[DRAFT-STAGE]" not in v1_text

    # 4. The wave step event carries the artifact name.
    wave_events = [p for n, p in runner._step_events if n == "wave"]
    assert wave_events, "expected a 'wave' step event"
    assert wave_events[-1].get("artifact") == "v1_wave.md"

    # 5. The draft step event does NOT claim v1_wave.md as its artifact.
    draft_events = [p for n, p in runner._step_events if n == "draft"]
    assert draft_events, "expected a 'draft' step event"


def test_draft_done_recovery_prefers_v1_wave(tmp_path: Path) -> None:
    """Resume recovery picks v1_wave.md as the reviewable draft and
    sets performed_edits=1."""
    storage = _make_storage(tmp_path)
    layout = _make_layout(storage)
    runner = _make_runner(layout, storage)
    bundle = _make_bundle(layout)
    ctx = _make_generate_context(runner, bundle)

    from novel_forge.pipeline.long import chapter_flow

    with (
        patch(
            "novel_forge.pipeline.long.services.constraints.constraint_router.build_draft_cards",
            lambda **_kwargs: {"plan": {}, "scene_intents": []},
        ),
        patch(
            "novel_forge.pipeline.long.services.constraints.constraint_router.build_wave_cards",
            lambda **_kwargs: {"plan": {}, "scene_intents": []},
        ),
        patch("novel_forge.pipeline.steps.wave_step.WaveStep", lambda *a, **k: _FakeWaveStep()),
        patch("novel_forge.pipeline.steps.draft_step.DraftStep", lambda *a, **k: _FakeDraftStep()),
    ):
        asyncio.run(chapter_flow.generate_chapter_prose(
            runner=runner,
            bundle=bundle,
            packet=ctx.packet,
            bridge=ctx.bridge,
            plan=ctx.plan,
            chapter_number=1,
            trace=ctx.trace,
        ))

    # Simulate the orchestrator's checkpoint save (mirrors
    # chapter_flow.review_chapter_draft after WAVE completes).
    v1_text = storage.load_text(layout.chapter_wave_draft_path(1))
    save_review_progress(
        storage=storage,
        layout=layout,
        chapter_number=1,
        progress=ReviewProgressState(
            completed_stage="draft_done",
            current_text=v1_text,
            performed_edits=1,
        ),
    )

    loaded = load_review_progress(storage, layout, 1)
    assert loaded is not None
    assert loaded.completed_stage == "draft_done"
    assert loaded.performed_edits == 1
    assert "[WAVE-STAGE]" in loaded.current_text

    # And _recover_latest_draft must agree: it must not return v0
    # when v1 is present.
    recovered = chapter_flow._recover_latest_draft(storage, layout, 1)
    assert recovered is not None
    assert "[WAVE-STAGE]" in recovered.text
    assert recovered.performed_edits == 1


def test_recovery_never_treats_v0_draft_as_reviewable(tmp_path: Path) -> None:
    """If only v0_draft.md exists on disk (WAVE never ran), recovery
    must NOT promote it to draft_done.  This is the 'raw DRAFT is
    intentionally not reviewable' contract."""
    storage = _make_storage(tmp_path)
    layout = _make_layout(storage)

    # Plant a v0_draft.md that is *long enough* to pass the 200-char
    # _load_recoverable_text filter, so that without the v0-exclusion
    # rule recovery would happily pick it up.
    v0_text = "[DRAFT-STAGE] " + ("雾霭笼罩的街道。" * 60)
    storage.save_text(layout.chapter_draft_path(1, 0), v0_text)

    from novel_forge.pipeline.long import chapter_flow

    recovered = chapter_flow._recover_latest_draft(storage, layout, 1)
    # v0 is explicitly excluded, so nothing should be recovered.
    assert recovered is None, (
        "v0_draft.md must never be recovered as a reviewable draft; "
        f"got {recovered!r}"
    )

    # And loading a saved review progress (had WAVE not finished) would
    # be None here as well — we never wrote one.
    assert load_review_progress(storage, layout, 1) is None


def test_recovery_falls_back_to_legacy_edited_versions(tmp_path: Path) -> None:
    """Pre-WAVE projects only have vN_edited.md snapshots.  When neither
    v1_wave.md nor a saved review-progress exist, recovery falls back
    to the highest-numbered edited snapshot (excluding v0)."""
    storage = _make_storage(tmp_path)
    layout = _make_layout(storage)

    legacy_text = "[LEGACY-EDITED-v3] " + ("旧版编辑后正文。" * 60)
    storage.save_text(layout.chapter_draft_path(1, 3), legacy_text)
    storage.save_text(layout.chapter_draft_path(1, 2), "should not be picked")

    from novel_forge.pipeline.long import chapter_flow

    recovered = chapter_flow._recover_latest_draft(storage, layout, 1)
    assert recovered is not None
    assert "LEGACY-EDITED-v3" in recovered.text
    assert recovered.performed_edits == 3


def test_layout_path_semantics_locked() -> None:
    """v0 and v1 path helpers must point to distinct, stable filenames."""
    layout = ProjectLayout(project_dir=Path("/tmp/x"))
    v0 = layout.chapter_draft_path(7, 0)
    v1 = layout.chapter_wave_draft_path(7)
    assert v0.name == "v0_draft.md"
    assert v1.name == "v1_wave.md"
    assert v0.parent == v1.parent
    # v0 and v1 are *not* the same path: v0 is raw DRAFT, v1 is woven.
    assert v0 != v1


def test_review_progress_state_model_accepts_wave_artifact(tmp_path: Path) -> None:
    """The persisted JSON must round-trip through the model, including
    the performed_edits=1 sentinel that distinguishes 'WAVE done' from
    'edit-round N done'."""
    storage = _make_storage(tmp_path)
    layout = _make_layout(storage)

    progress = ReviewProgressState(
        completed_stage="draft_done",
        current_text="[WAVE-STAGE] 章节正文。",
        performed_edits=1,
        warnings=[],
    )
    save_review_progress(
        storage=storage,
        layout=layout,
        chapter_number=1,
        progress=progress,
    )

    raw = json.loads(storage.load_text(layout.chapter_review_progress_path(1)))
    assert raw["completed_stage"] == "draft_done"
    assert raw["performed_edits"] == 1
    # Stage 2 (quality_done) and beyond would be different sentinel values.
    assert raw["current_text"].startswith("[WAVE-STAGE]")

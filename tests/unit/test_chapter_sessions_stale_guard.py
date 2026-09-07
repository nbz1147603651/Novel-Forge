"""Unit tests for stale-chain guards in chapter session resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import novel_forge.workspace.sessions.chapter_sessions as chapter_sessions
from novel_forge.core.exceptions import ChapterSessionStaleError
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.contracts import (
    ChapterSessionResult,
    DecisionCheckpoint,
    DecisionOption,
    ResolveChapterCheckpointRequest,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_state import PlanCheckpointSessionState


def _build_runtime(*, runtime_settings, router, builder, tmp_storage) -> RuntimeServices:
    return RuntimeServices(
        settings=runtime_settings,
        router=router,
        builder=builder,
        storage=tmp_storage,
    )


def _seed_stale_project(tmp_storage, *, project_id: str) -> ProjectLayout:
    layout = ProjectLayout(tmp_storage.project_dir(project_id))
    layout.ensure_dirs()
    tmp_storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 1})
    # finalized_chapter_numbers() relies on chapter markdown files
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文")
    tmp_storage.save_text(layout.chapter_path(2), "旧版第二章正文")
    return layout


async def test_resolve_rejects_stale_cutoff_checkpoint_without_chain_watermark(
    tmp_storage,
    runtime_settings,
    router,
    builder,
) -> None:
    project_id = "unit_stale_guard_reject"
    layout = _seed_stale_project(tmp_storage, project_id=project_id)
    tmp_storage.save_json(
        layout.chapter_session_path(2),
        {
            "stage": "plan_checkpoint",
            "checkpoint_id": "plan-002-stale",
            "project_id": project_id,
            "chapter_number": 2,
            "trace_summary": {},
            # 缺少 canon_watermark：应被视为旧链路 checkpoint
        },
    )

    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )

    with pytest.raises(ChapterSessionStaleError, match="已失效的上游结果"):
        await chapter_sessions.resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=2,
                checkpoint_id="plan-002-stale",
                option_id="write_now",
                notes="",
            ),
        )


async def test_resolve_allows_stale_cutoff_checkpoint_with_matching_chain_watermark(
    tmp_storage,
    runtime_settings,
    router,
    builder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "unit_stale_guard_allow"
    layout = _seed_stale_project(tmp_storage, project_id=project_id)
    tmp_storage.save_json(
        layout.chapter_session_path(2),
        {
            "stage": "plan_checkpoint",
            "checkpoint_id": "plan-002-fresh",
            "project_id": project_id,
            "chapter_number": 2,
            "canon_watermark": 1,
            "trace_summary": {},
        },
    )

    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )

    bundle = SimpleNamespace(layout=layout)

    async def _fake_prepare_long_project(**kwargs):
        return bundle

    monkeypatch.setattr(
        chapter_sessions,
        "prepare_long_project",
        _fake_prepare_long_project,
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda _bundle, _chapter_number: DecisionCheckpoint(
            checkpoint_id="plan-002-fresh",
            checkpoint_type="plan_checkpoint",
            summary="fresh checkpoint",
            prompt="",
            options=[DecisionOption(option_id="write_now", label="写作", is_recommended=True)],
            related_artifacts=[],
        ),
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda _bundle, _chapter_number: PlanCheckpointSessionState(
            checkpoint_id="plan-002-fresh",
            project_id=project_id,
            chapter_number=2,
            canon_watermark=1,
            trace_summary={},
        ),
    )

    async def _fake_resolve_plan_checkpoint(*args, **kwargs) -> ChapterSessionResult:
        request = args[1]
        return ChapterSessionResult(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            status="completed",
            applied_option_id=request.option_id,
        )

    monkeypatch.setattr(chapter_sessions, "resolve_plan_checkpoint", _fake_resolve_plan_checkpoint)

    result = await chapter_sessions.resolve_chapter_session(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id=project_id,
            chapter_number=2,
            checkpoint_id="plan-002-fresh",
            option_id="write_now",
            notes="",
        ),
    )

    assert result.status == "completed"
    assert result.chapter_number == 2


async def test_resolve_rejects_checkpoint_when_canon_watermark_changed(
    tmp_storage,
    runtime_settings,
    router,
    builder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "unit_session_watermark_changed"
    layout = ProjectLayout(tmp_storage.project_dir(project_id))
    layout.ensure_dirs()

    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )
    bundle = SimpleNamespace(layout=layout, canon_state=SimpleNamespace(current_chapter=2))

    async def _fake_prepare_long_project_watermark(**kwargs):
        return bundle

    monkeypatch.setattr(chapter_sessions, "prepare_long_project", _fake_prepare_long_project_watermark)
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda _bundle, _chapter_number: DecisionCheckpoint(
            checkpoint_id="plan-003-old",
            checkpoint_type="plan_checkpoint",
            summary="old checkpoint",
            prompt="",
            options=[DecisionOption(option_id="write_now", label="写作", is_recommended=True)],
            related_artifacts=[],
        ),
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda _bundle, _chapter_number: PlanCheckpointSessionState(
            checkpoint_id="plan-003-old",
            project_id=project_id,
            chapter_number=3,
            canon_watermark=1,
            trace_summary={},
        ),
    )

    with pytest.raises(ChapterSessionStaleError, match="checkpoint 已失效"):
        await chapter_sessions.resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=3,
                checkpoint_id="plan-003-old",
                option_id="write_now",
                notes="",
            ),
        )


async def test_resolve_rejects_checkpoint_without_canon_watermark(
    tmp_storage,
    runtime_settings,
    router,
    builder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "unit_session_watermark_missing"
    layout = ProjectLayout(tmp_storage.project_dir(project_id))
    layout.ensure_dirs()

    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )
    bundle = SimpleNamespace(layout=layout, canon_state=SimpleNamespace(current_chapter=2))

    async def _fake_prepare_long_project_missing(**kwargs):
        return bundle

    monkeypatch.setattr(chapter_sessions, "prepare_long_project", _fake_prepare_long_project_missing)
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda _bundle, _chapter_number: DecisionCheckpoint(
            checkpoint_id="plan-003-missing",
            checkpoint_type="plan_checkpoint",
            summary="old checkpoint",
            prompt="",
            options=[DecisionOption(option_id="write_now", label="写作", is_recommended=True)],
            related_artifacts=[],
        ),
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda _bundle, _chapter_number: PlanCheckpointSessionState(
            checkpoint_id="plan-003-missing",
            project_id=project_id,
            chapter_number=3,
            trace_summary={},
        ),
    )

    with pytest.raises(ChapterSessionStaleError, match="缺少上游水位"):
        await chapter_sessions.resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=3,
                checkpoint_id="plan-003-missing",
                option_id="write_now",
                notes="",
            ),
        )


# ═══════════════════════════════════════════════════════════════════════════
# P0-C: _backfill_reading_power_score — recover score from report file
# ═══════════════════════════════════════════════════════════════════════════


class TestBackfillReadingPowerScore:
    """P0-C regression: when checkpoint-resume skips the quality stage
    (so ``result.reading_power_report`` is None), the reading_power
    report file from a prior evaluate run is still on disk. This
    backfill reads it and returns ``(overall_score, summary)`` so the
    ChapterSessionResult surfaces a real score instead of ``None``.
    """

    def _seed_report(
        self, tmp_storage, *, project_id: str, chapter: int,
        overall_score: float, hook_description: str = "",
        source_text_hash: str | None = None,
    ) -> None:
        from novel_forge.persistence.models import ProjectLayout

        layout = ProjectLayout(tmp_storage.project_dir(project_id))
        layout.ensure_dirs()
        payload: dict = {
            "overall_score": overall_score,
            "is_fallback": False,
            "evaluation_status": "ok",
            "hook_description": hook_description,
        }
        if source_text_hash is not None:
            payload["source_text_hash"] = source_text_hash
        tmp_storage.save_json(
            layout.reading_power_report_path(chapter), payload
        )

    def test_no_report_file_returns_none(
        self, tmp_storage, runtime_settings,
    ) -> None:
        # Project exists but no report file yet
        from novel_forge.persistence.models import ProjectLayout
        from novel_forge.workspace.sessions.chapter_session_handlers import (
            _backfill_reading_power_score,
        )
        ProjectLayout(tmp_storage.project_dir("p1")).ensure_dirs()

        result = _backfill_reading_power_score(
            storage=tmp_storage,
            layout=ProjectLayout(tmp_storage.project_dir("p1")),
            chapter_number=1,
            current_text="any text",
        )
        assert result == (None, "")

    def test_matching_hash_returns_score_and_summary(
        self, tmp_storage, runtime_settings,
    ) -> None:
        from novel_forge.core.utils.text_hash import source_text_hash
        from novel_forge.workspace.sessions.chapter_session_handlers import (
            _backfill_reading_power_score,
        )

        text = "合卺酒送至面前,金杯在烛光下泛着暖色。"
        self._seed_report(
            tmp_storage,
            project_id="p1",
            chapter=1,
            overall_score=9.5,
            hook_description="mystery / strong",
            source_text_hash=source_text_hash(text),
        )
        from novel_forge.persistence.models import ProjectLayout
        result = _backfill_reading_power_score(
            storage=tmp_storage,
            layout=ProjectLayout(tmp_storage.project_dir("p1")),
            chapter_number=1,
            current_text=text,
        )
        assert result == (9.5, "mystery / strong")

    def test_stale_hash_returns_none(
        self, tmp_storage, runtime_settings,
    ) -> None:
        """A report from a prior cancelled run whose text hash does not
        match the current chapter must be rejected (stale guard)."""
        from novel_forge.core.utils.text_hash import source_text_hash
        from novel_forge.workspace.sessions.chapter_session_handlers import (
            _backfill_reading_power_score,
        )

        stale_text = "残玉背面「昱」字刻痕"
        self._seed_report(
            tmp_storage,
            project_id="p1",
            chapter=1,
            overall_score=10.0,
            source_text_hash=source_text_hash(stale_text),
        )
        from novel_forge.persistence.models import ProjectLayout
        result = _backfill_reading_power_score(
            storage=tmp_storage,
            layout=ProjectLayout(tmp_storage.project_dir("p1")),
            chapter_number=1,
            current_text="current text differs from stale",
        )
        assert result == (None, "")

    def test_missing_source_text_hash_treated_as_fresh(
        self, tmp_storage, runtime_settings,
    ) -> None:
        """Reports written before the hash field existed (legacy) lack
        source_text_hash. We treat them as fresh — the consumer can
        decide whether to trust a non-hashed report."""
        from novel_forge.workspace.sessions.chapter_session_handlers import (
            _backfill_reading_power_score,
        )

        self._seed_report(
            tmp_storage,
            project_id="p1",
            chapter=1,
            overall_score=8.0,
            source_text_hash=None,
        )
        from novel_forge.persistence.models import ProjectLayout
        result = _backfill_reading_power_score(
            storage=tmp_storage,
            layout=ProjectLayout(tmp_storage.project_dir("p1")),
            chapter_number=1,
            current_text="any text",
        )
        assert result == (8.0, "")

    def test_non_dict_report_payload_returns_none(
        self, tmp_storage, runtime_settings,
    ) -> None:
        from novel_forge.persistence.models import ProjectLayout
        from novel_forge.workspace.sessions.chapter_session_handlers import (
            _backfill_reading_power_score,
        )

        layout = ProjectLayout(tmp_storage.project_dir("p1"))
        layout.ensure_dirs()
        tmp_storage.save_json(layout.reading_power_report_path(1), ["not a dict"])

        result = _backfill_reading_power_score(
            storage=tmp_storage,
            layout=layout,
            chapter_number=1,
            current_text="any",
        )
        assert result == (None, "")

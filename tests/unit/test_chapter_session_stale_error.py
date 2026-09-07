"""Unit tests for ChapterSessionStaleError and error_code propagation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import novel_forge.workspace.sessions.chapter_sessions as chapter_sessions
from novel_forge.core.exceptions import (
    BLOCK_KIND_CHAPTER_SESSION_STALE,
    ChapterSessionStaleError,
    FinalReportFreshnessError,
)
from novel_forge.desktop.errors import DesktopErrorCategory, summarize_desktop_error
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.contracts import (
    DecisionCheckpoint,
    DecisionOption,
    ResolveChapterCheckpointRequest,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_state import (
    GuardCheckpointSessionState,
)

# ── ChapterSessionStaleError 基础语义 ────────────────────────────────────────


def test_chapter_session_stale_error_carries_structured_error_code() -> None:
    err = ChapterSessionStaleError(
        message="章节工作台 checkpoint 已变化，请刷新后重试。",
        kind="checkpoint_id_drift",
        context={
            "request_checkpoint_id": "plan-001-old",
            "current_checkpoint_id": "guard-001-new",
        },
    )
    assert err.error_code == "chapter_session_stale"
    assert err.kind == "checkpoint_id_drift"
    assert err.context["block_kind"] == BLOCK_KIND_CHAPTER_SESSION_STALE
    assert err.context["stale_kind"] == "checkpoint_id_drift"
    assert err.context["retryable"] is True
    assert any(
        action.get("action") == "refresh_chapter_studio"
        for action in err.context.get("recovery_actions", [])
    )
    # Extra context merged in.
    assert err.context["request_checkpoint_id"] == "plan-001-old"
    assert err.context["current_checkpoint_id"] == "guard-001-new"


def test_chapter_session_stale_error_is_not_value_error() -> None:
    """Front-end relies on error_code, not exception type; ensure the new
    class is NOT a ValueError subclass so old ``pytest.raises(ValueError)``
    callers must be updated explicitly."""
    err = ChapterSessionStaleError(message="x", kind="test")
    assert not isinstance(err, ValueError)


# ── summarize_desktop_error 透传 error_code ─────────────────────────────────


def test_summarize_desktop_error_propagates_chapter_session_stale_error_code() -> None:
    err = ChapterSessionStaleError(
        message="章节工作台 checkpoint 已变化，请刷新后重试。",
        kind="checkpoint_id_drift",
    )
    summary = summarize_desktop_error(err)
    assert summary.error_code == "chapter_session_stale"
    assert summary.category == DesktopErrorCategory.VALIDATION
    assert summary.retryable is True
    assert summary.title == "章节工作台状态已变化"
    assert any(
        action.get("action") == "refresh_chapter_studio"
        for action in summary.recovery_actions
    )
    # End-to-end: as_payload carries error_code to the desktop job layer.
    payload = summary.as_payload()
    assert payload["error_code"] == "chapter_session_stale"


def test_summarize_desktop_error_propagates_final_report_freshness_error_code() -> None:
    """Regression: FinalReportFreshnessError must now also surface its
    error_code (previously dropped by the desktop layer)."""
    err = FinalReportFreshnessError(
        chapter_number=3,
        expected_text_hash="abc",
        dimensions=["alignment"],
        detail="report missing",
    )
    summary = summarize_desktop_error(err)
    assert summary.error_code == "final_report_freshness"


def test_summarize_desktop_error_payload_round_trips_error_code() -> None:
    """The payload dict that lands in DesktopJobRecord.error_summary must
    contain the error_code key so chapter-studio auto-refresh can read it."""
    err = ChapterSessionStaleError(message="漂移", kind="canon_watermark_drift")
    payload = summarize_desktop_error(err).as_payload()
    assert "error_code" in payload
    assert payload["error_code"] == "chapter_session_stale"


# ── resolve_chapter_session 抛出新异常 ──────────────────────────────────────


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
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文")
    tmp_storage.save_text(layout.chapter_path(2), "旧版第二章正文")
    return layout


async def test_resolve_soft_redirects_guard_to_guard_with_same_option(
    tmp_storage,
    runtime_settings,
    router,
    builder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当 request.checkpoint_id 与磁盘 checkpoint_id 不一致，但两者都是
    guard_checkpoint 且请求的 option_id 在当前 checkpoint 选项中时，
    应透明软重定向到当前 checkpoint，而非抛错。"""
    project_id = "unit_stale_drift_kind"
    layout = ProjectLayout(tmp_storage.project_dir(project_id))
    layout.ensure_dirs()

    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )
    bundle = SimpleNamespace(layout=layout, canon_state=SimpleNamespace(current_chapter=2))

    async def _fake_prepare(**kwargs):
        return bundle

    captured: dict = {}

    async def _fake_resolve_guard(_runtime, redirected_request, **_kwargs):
        captured["redirected_checkpoint_id"] = redirected_request.checkpoint_id
        captured["redirected_option_id"] = redirected_request.option_id
        return SimpleNamespace(project_id=project_id, status="needs_decision")

    monkeypatch.setattr(chapter_sessions, "prepare_long_project", _fake_prepare)
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda _bundle, _chapter_number: DecisionCheckpoint(
            checkpoint_id="guard-002-actual",
            checkpoint_type="guard_checkpoint",
            summary="actual checkpoint",
            prompt="",
            options=[
                DecisionOption(option_id="apply_repairs_and_finalize", label="归档前修复", is_recommended=True)
            ],
            related_artifacts=[],
        ),
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda _bundle, _chapter_number: GuardCheckpointSessionState(
            checkpoint_id="guard-002-actual",
            project_id=project_id,
            chapter_number=2,
            canon_watermark=2,
            pending_result={},
        ),
    )
    monkeypatch.setattr(chapter_sessions, "resolve_guard_checkpoint", _fake_resolve_guard)

    result = await chapter_sessions.resolve_chapter_session(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id=project_id,
            chapter_number=2,
            checkpoint_id="guard-002-stale-ui",
            option_id="apply_repairs_and_finalize",
            notes="",
        ),
    )
    # Soft-redirect uses the CURRENT checkpoint_id, not the stale one.
    assert captured["redirected_checkpoint_id"] == "guard-002-actual"
    assert captured["redirected_option_id"] == "apply_repairs_and_finalize"
    assert result.status == "needs_decision"


async def test_resolve_raises_stale_error_when_option_not_in_current_checkpoint(
    tmp_storage,
    runtime_settings,
    router,
    builder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当 request.checkpoint_id 与磁盘 checkpoint_id 不一致，且请求的
    option_id 不在当前 checkpoint 选项中时，应抛出 ChapterSessionStaleError。"""
    project_id = "unit_stale_drift_no_option"
    layout = ProjectLayout(tmp_storage.project_dir(project_id))
    layout.ensure_dirs()

    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )
    bundle = SimpleNamespace(layout=layout, canon_state=SimpleNamespace(current_chapter=2))

    async def _fake_prepare(**kwargs):
        return bundle

    monkeypatch.setattr(chapter_sessions, "prepare_long_project", _fake_prepare)
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda _bundle, _chapter_number: DecisionCheckpoint(
            checkpoint_id="guard-002-actual",
            checkpoint_type="guard_checkpoint",
            summary="actual checkpoint",
            prompt="",
            options=[
                DecisionOption(option_id="pause_for_human", label="暂停人工处理", is_recommended=True)
            ],
            related_artifacts=[],
        ),
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda _bundle, _chapter_number: GuardCheckpointSessionState(
            checkpoint_id="guard-002-actual",
            project_id=project_id,
            chapter_number=2,
            canon_watermark=2,
            pending_result={},
        ),
    )

    with pytest.raises(ChapterSessionStaleError) as exc_info:
        await chapter_sessions.resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=2,
                checkpoint_id="guard-002-stale-ui",
                # accept_and_finalize 不在当前 checkpoint 的选项中，无法软重定向
                option_id="accept_and_finalize",
                notes="",
            ),
        )
    assert exc_info.value.kind == "checkpoint_id_drift"
    assert exc_info.value.context["request_checkpoint_id"] == "guard-002-stale-ui"
    assert exc_info.value.context["current_checkpoint_id"] == "guard-002-actual"
    assert exc_info.value.context["current_checkpoint_type"] == "guard_checkpoint"


async def test_resolve_raises_stale_error_with_session_type_mismatch_kind(
    tmp_storage,
    runtime_settings,
    router,
    builder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """checkpoint 类型与 session_state 类型不匹配时抛出 session_type_mismatch。"""
    project_id = "unit_stale_type_mismatch"
    layout = ProjectLayout(tmp_storage.project_dir(project_id))
    layout.ensure_dirs()

    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )
    bundle = SimpleNamespace(layout=layout, canon_state=SimpleNamespace(current_chapter=2))

    async def _fake_prepare(**kwargs):
        return bundle

    monkeypatch.setattr(chapter_sessions, "prepare_long_project", _fake_prepare)
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda _bundle, _chapter_number: DecisionCheckpoint(
            checkpoint_id="plan-002-x",
            checkpoint_type="plan_checkpoint",
            summary="plan checkpoint",
            prompt="",
            options=[DecisionOption(option_id="write_now", label="写作", is_recommended=True)],
            related_artifacts=[],
        ),
    )
    # session_state 是 GuardCheckpointSessionState，但 checkpoint 是 plan_checkpoint
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda _bundle, _chapter_number: GuardCheckpointSessionState(
            checkpoint_id="plan-002-x",
            project_id=project_id,
            chapter_number=2,
            canon_watermark=2,
            pending_result={},
        ),
    )

    with pytest.raises(ChapterSessionStaleError) as exc_info:
        await chapter_sessions.resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=2,
                checkpoint_id="plan-002-x",
                option_id="write_now",
                notes="",
            ),
        )
    assert exc_info.value.kind == "session_type_mismatch"
    assert exc_info.value.context["checkpoint_type"] == "plan_checkpoint"
    assert exc_info.value.context["session_state_type"] == "GuardCheckpointSessionState"


async def test_resolve_stale_upstream_carries_stale_kind(
    tmp_storage,
    runtime_settings,
    router,
    builder,
) -> None:
    """stale_upstream 分支也应携带 kind 和 context。"""
    project_id = "unit_stale_upstream_kind"
    _seed_stale_project(tmp_storage, project_id=project_id)
    tmp_storage.save_json(
        ProjectLayout(tmp_storage.project_dir(project_id)).chapter_session_path(2),
        {
            "stage": "plan_checkpoint",
            "checkpoint_id": "plan-002-stale",
            "project_id": project_id,
            "chapter_number": 2,
            "trace_summary": {},
        },
    )
    runtime = _build_runtime(
        runtime_settings=runtime_settings,
        router=router,
        builder=builder,
        tmp_storage=tmp_storage,
    )

    with pytest.raises(ChapterSessionStaleError) as exc_info:
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
    assert exc_info.value.kind == "stale_upstream"
    assert exc_info.value.context["stale_cutoff"] == 2
    assert exc_info.value.context["chapter_number"] == 2

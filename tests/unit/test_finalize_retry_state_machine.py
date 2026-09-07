"""Tests for finalize_chapter_result retry state machine dispatch logic.

Verifies the branch classification and routing documented in the
``finalize_chapter_result`` docstring:
  1st persist -> Success | ConsistencyViolationError
    -> carry_forward / contract_audit / post-Humanize repair -> 2nd persist
      -> Success | ConsistencyViolationError (contract/post-Humanize) -> 3rd persist
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.exceptions import (
    BLOCK_KIND_CARRY_FORWARD,
    BLOCK_KIND_CONTRACT_AUDIT,
    ConsistencyViolationError,
)
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.pipeline.long.execution_models import (
    ChapterReviewArtifacts,
    PreparedChapterArtifacts,
)


def _review_fixture() -> ChapterReviewArtifacts:
    return ChapterReviewArtifacts(
        prepared=PreparedChapterArtifacts(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(
                    chapter_number=1,
                    title="状态机验收",
                    expected_word_count=0,
                ),
                editorial_contract=None,
                layout=SimpleNamespace(),
            ),
            packet=SimpleNamespace(),
            bridge=None,
            plan=None,
            memory_hints=None,
            window_manager=None,
        ),
        current_text="验收正文" * 20,
        performed_edits=1,
        outcome=ChapterOutcome(source_chapter=1),
        alignment_report=None,
        chapter_repair_report=None,
        continuity_report=None,
        repair_plan=None,
    )


def _persisted_fixture(review: ChapterReviewArtifacts) -> SimpleNamespace:
    return SimpleNamespace(
        eval_report=review.eval_report,
        current_text=review.current_text,
        outcome=review.outcome,
        alignment_report=review.alignment_report,
        chapter_repair_report=review.chapter_repair_report,
        continuity_report=review.continuity_report,
        causal_report=review.causal_report,
        reading_power_report=None,
        guard_compliance_report=None,
        review_findings=[],
        repair_tickets=[],
    )


def _context_fixture() -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(long_total_repair_rounds_cap=5),
        storage=SimpleNamespace(),
        on_step=lambda *_args, **_kwargs: None,
    )


def _trace_fixture() -> SimpleNamespace:
    return SimpleNamespace(total_tokens=0, total_cost=0.0, summary=lambda: {})


def _patch_post_persist_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    from novel_forge.pipeline.long import chapter_flow_finalize, chapter_flow_review

    monkeypatch.setattr(
        chapter_flow_review,
        "_build_quality_gate",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "_build_normalized_review_contracts",
        lambda *_args, **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        chapter_flow_finalize,
        "_persist_quality_gate_report",
        lambda *_args, **_kwargs: {"verdict": "pass", "summary": ""},
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.finalize_tts_metadata.extract_tts_metadata",
        lambda **_kwargs: None,
    )


def test_is_carry_forward_block_exception_by_block_kind() -> None:
    """Exception with block_kind='carry_forward' is classified correctly."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_carry_forward_block_exception,
    )

    exc = ConsistencyViolationError(["必须承接的开放项未落地"])
    exc.block_kind = "carry_forward"
    assert is_carry_forward_block_exception(exc) is True


def test_is_carry_forward_block_exception_by_message_text() -> None:
    """Exception with '必须承接的开放项' in message is classified correctly."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_carry_forward_block_exception,
    )

    exc = ConsistencyViolationError(["章节有 3 个必须承接的开放项在本章正文无痕迹"])
    assert is_carry_forward_block_exception(exc) is True


def test_is_carry_forward_block_exception_negative() -> None:
    """Generic ConsistencyViolationError is NOT classified as carry_forward."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_carry_forward_block_exception,
    )

    exc = ConsistencyViolationError(["generic quality failure"])
    assert is_carry_forward_block_exception(exc) is False


def test_is_contract_audit_block_exception() -> None:
    """Exception from contract execution audit is classified correctly."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_contract_audit_block_exception,
    )

    exc = ConsistencyViolationError(["章节契约执行审计阻断归档"])
    exc.block_kind = "contract_audit"
    assert is_contract_audit_block_exception(exc) is True


def test_is_post_humanize_semantic_block_exception() -> None:
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_post_humanize_semantic_block_exception,
    )

    exc = ConsistencyViolationError(
        ["Humanize 后知识边界复检阻断"],
        violation_kind="post_humanize_semantic_verification",
    )
    assert is_post_humanize_semantic_block_exception(exc) is True


@pytest.mark.asyncio
async def test_single_final_refinement_rolls_back_humanize_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novel_forge.pipeline.long import chapter_flow_finalize, chapter_flow_orchestrate
    from novel_forge.pipeline.long.stages.finalize_persist import ArchivePreflightResult

    review = _review_fixture()
    calls: list[str] = []
    events: list[tuple[str, object]] = []

    async def _humanize(_context, candidate, _trace):
        return candidate.__class__(
            **{
                **candidate.__dict__,
                "current_text": f"{candidate.current_text}（越界改写）",
                "performed_edits": candidate.performed_edits + 1,
            }
        )

    async def _preflight(**kwargs):
        terminal = kwargs.get("terminal_humanize")
        if terminal is not None:
            calls.append("humanize_attempt")
            await terminal(kwargs["current_text"])
            raise ConsistencyViolationError(
                ["Humanize 改变了人物知识边界"],
                violation_kind="post_humanize_semantic_verification",
                failed_stage="post_humanize_semantic_verification",
            )
        calls.append("rollback_verify")
        return ArchivePreflightResult(
            current_text=kwargs["current_text"],
            outcome=kwargs["outcome"],
            eval_report=kwargs["eval_report"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            state_adjudication_report=None,
            knowledge_boundary_findings=[],
            terminal_humanize_metadata={"applied": False, "rolled_back": True},
            refresh_decision=kwargs["decision"],
        )

    monkeypatch.setattr(chapter_flow_orchestrate, "_apply_terminal_humanize", _humanize)
    monkeypatch.setattr(chapter_flow_finalize, "run_archive_preflight_repairs", _preflight)
    context = _context_fixture()
    context.on_step = lambda step, payload: events.append((step, payload))

    refined, result = await chapter_flow_finalize._run_single_final_refinement(  # noqa: SLF001
        context,
        review=review,
        trace=_trace_fixture(),
    )

    assert calls == ["humanize_attempt", "rollback_verify"]
    assert refined.current_text == review.current_text
    assert result.current_text == review.current_text
    assert refined.refinement_done is True
    assert any(step == "terminal_humanize_rolled_back" for step, _ in events)


@pytest.mark.asyncio
async def test_single_final_verify_allows_only_one_targeted_reentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novel_forge.pipeline.long import chapter_flow_finalize
    from novel_forge.pipeline.long.stages.finalize_persist import (
        ArchivePreflightResult,
        ReportRefreshDecision,
    )

    review = _review_fixture()
    preflight = ArchivePreflightResult(
        current_text=review.current_text,
        outcome=review.outcome,
        eval_report=None,
        chapter_repair_report=None,
        state_adjudication_report=None,
        knowledge_boundary_findings=[],
        terminal_humanize_metadata={"applied": False},
        refresh_decision=ReportRefreshDecision.empty(),
    )
    persist_calls: list[int] = []
    repair_calls: list[int] = []

    async def _refine(_context, *, review, trace):
        return review.__class__(**{**review.__dict__, "refinement_done": True}), preflight

    async def _persist(*_args, **_kwargs):
        persist_calls.append(len(persist_calls) + 1)
        if len(persist_calls) == 1:
            raise ConsistencyViolationError(
                ["契约审计阻断"],
                block_kind=BLOCK_KIND_CONTRACT_AUDIT,
            )
        return _persisted_fixture(review)

    async def _repair(_context, *, review, trace):
        repair_calls.append(1)
        return chapter_flow_finalize._ArchiveRepairAttempt(status="repaired", review=review)

    monkeypatch.setattr(chapter_flow_finalize, "_run_single_final_refinement", _refine)
    monkeypatch.setattr(chapter_flow_finalize, "persist_results", _persist)
    monkeypatch.setattr(chapter_flow_finalize, "_repair_contract_execution_audit_block", _repair)
    monkeypatch.setattr(
        chapter_flow_finalize, "_save_finalization_review_progress", lambda *a, **k: None
    )
    monkeypatch.setattr(
        chapter_flow_finalize, "_clear_finalization_review_progress", lambda *a, **k: None
    )

    persisted, final_review = await chapter_flow_finalize._persist_single_final_verify(  # noqa: SLF001
        _context_fixture(),
        review=review,
        trace=_trace_fixture(),
        eval_report=None,
        emit_evaluate_step=False,
        allow_contract_audit_auto_repair=True,
        allow_carry_forward_auto_repair=True,
    )

    assert persisted.current_text == review.current_text
    assert final_review.final_verify_done is True
    assert persist_calls == [1, 2]
    assert repair_calls == [1]


def test_retry_dispatch_routes_carry_forward_to_correct_repair() -> None:
    """Verify the dispatch logic: carry_forward -> _repair_unclosed_carry_forward."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_carry_forward_block_exception,
        is_contract_audit_block_exception,
    )

    cf_error = ConsistencyViolationError(["必须承接的开放项未落地"])
    cf_error.block_kind = "carry_forward"

    # Verify dispatch classification
    assert is_carry_forward_block_exception(cf_error) is True
    assert is_contract_audit_block_exception(cf_error) is False

    # Verify the combined can_auto_repair logic
    can_auto_repair = is_contract_audit_block_exception(cf_error) or (
        is_carry_forward_block_exception(cf_error)
    )
    assert can_auto_repair is True


def test_retry_dispatch_routes_generic_to_unrecoverable() -> None:
    """Generic ConsistencyViolationError -> neither repair path -> re-raise."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_carry_forward_block_exception,
        is_contract_audit_block_exception,
    )

    generic_error = ConsistencyViolationError(["generic failure"])

    assert is_carry_forward_block_exception(generic_error) is False
    assert is_contract_audit_block_exception(generic_error) is False

    can_auto_repair = is_contract_audit_block_exception(
        generic_error
    ) or is_carry_forward_block_exception(generic_error)
    assert can_auto_repair is False


def test_retry_dispatch_contract_block_routes_to_contract_repair() -> None:
    """Verify the dispatch logic: contract_audit -> _repair_contract_execution_audit_block."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_carry_forward_block_exception,
        is_contract_audit_block_exception,
    )

    contract_error = ConsistencyViolationError(["章节契约执行审计阻断归档"])
    contract_error.block_kind = "contract_audit"

    assert is_carry_forward_block_exception(contract_error) is False
    assert is_contract_audit_block_exception(contract_error) is True

    can_auto_repair = (is_contract_audit_block_exception(contract_error)) or (
        is_carry_forward_block_exception(contract_error)
    )
    assert can_auto_repair is True


def test_retry_double_block_carry_forward_then_contract() -> None:
    """Simulate the double-block path: 1st carry_forward, 2nd contract audit.

    The state machine allows: 1st attempt carry_forward repair -> 2nd attempt
    -> if 2nd raises contract audit -> 2nd contract repair -> 3rd attempt.
    This test verifies both classifiers work independently so the routing
    can handle the sequential scenario.
    """
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        is_carry_forward_block_exception,
        is_contract_audit_block_exception,
    )

    # First exception: carry_forward
    first_exc = ConsistencyViolationError(["必须承接的开放项未落地"])
    first_exc.block_kind = "carry_forward"
    assert is_carry_forward_block_exception(first_exc) is True

    # Second exception: contract audit (after carry_forward repair)
    second_exc = ConsistencyViolationError(["章节契约执行审计阻断归档"])
    second_exc.block_kind = "contract_audit"
    assert is_contract_audit_block_exception(second_exc) is True
    # The 2nd except block only handles contract audit blocks
    assert is_carry_forward_block_exception(second_exc) is False


@pytest.mark.asyncio
async def test_finalize_retries_two_contract_blocks_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second contract-audit block gets one final repair-and-persist attempt."""
    from novel_forge.pipeline.long import chapter_flow_finalize

    review = _review_fixture()
    persisted = _persisted_fixture(review)
    attempts: list[object] = [
        ConsistencyViolationError(["契约审计阻断"], block_kind=BLOCK_KIND_CONTRACT_AUDIT),
        ConsistencyViolationError(["契约审计再次阻断"], block_kind=BLOCK_KIND_CONTRACT_AUDIT),
        persisted,
    ]
    persist_calls: list[int] = []
    repair_calls: list[int] = []

    async def _persist(*_args, **_kwargs):
        persist_calls.append(1)
        result = attempts.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def _repair_contract(*_args, **_kwargs):
        repair_calls.append(1)
        return chapter_flow_finalize._ArchiveRepairAttempt(status="repaired", review=review)

    monkeypatch.setattr(chapter_flow_finalize, "persist_results", _persist)
    monkeypatch.setattr(
        chapter_flow_finalize,
        "_repair_contract_execution_audit_block",
        _repair_contract,
    )
    _patch_post_persist_steps(monkeypatch)

    result = await chapter_flow_finalize.finalize_chapter_result(
        _context_fixture(),
        review=review,
        trace=_trace_fixture(),
    )

    assert result.text == review.current_text
    assert len(persist_calls) == 3
    assert len(repair_calls) == 2


@pytest.mark.asyncio
async def test_finalize_does_not_retry_a_second_carry_forward_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carry-forward repair is one-shot; a second block must be re-raised."""
    from novel_forge.pipeline.long import chapter_flow_finalize

    review = _review_fixture()
    second_block = ConsistencyViolationError(
        ["必须承接的开放项仍未落地"],
        block_kind=BLOCK_KIND_CARRY_FORWARD,
    )
    attempts: list[object] = [
        ConsistencyViolationError(
            ["必须承接的开放项未落地"],
            block_kind=BLOCK_KIND_CARRY_FORWARD,
        ),
        second_block,
    ]
    persist_calls: list[int] = []
    repair_calls: list[int] = []

    async def _persist(*_args, **_kwargs):
        persist_calls.append(1)
        result = attempts.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def _repair_carry_forward(*_args, **_kwargs):
        repair_calls.append(1)
        return review

    monkeypatch.setattr(chapter_flow_finalize, "persist_results", _persist)
    monkeypatch.setattr(
        chapter_flow_finalize,
        "_repair_unclosed_carry_forward",
        _repair_carry_forward,
    )
    _patch_post_persist_steps(monkeypatch)

    with pytest.raises(ConsistencyViolationError) as exc_info:
        await chapter_flow_finalize.finalize_chapter_result(
            _context_fixture(),
            review=review,
            trace=_trace_fixture(),
        )

    assert exc_info.value is second_block
    assert len(persist_calls) == 2
    assert len(repair_calls) == 1


@pytest.mark.asyncio
async def test_finalize_respects_disabled_contract_repair_after_carry_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled contract-repair flag applies to the final retry branch too."""
    from novel_forge.pipeline.long import chapter_flow_finalize

    review = _review_fixture()
    second_block = ConsistencyViolationError(
        ["契约审计阻断"],
        block_kind=BLOCK_KIND_CONTRACT_AUDIT,
    )
    attempts: list[object] = [
        ConsistencyViolationError(
            ["必须承接的开放项未落地"],
            block_kind=BLOCK_KIND_CARRY_FORWARD,
        ),
        second_block,
    ]
    persist_calls: list[int] = []
    carry_forward_repairs: list[int] = []
    contract_repairs: list[int] = []

    async def _persist(*_args, **_kwargs):
        persist_calls.append(1)
        result = attempts.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def _repair_carry_forward(*_args, **_kwargs):
        carry_forward_repairs.append(1)
        return review

    async def _repair_contract(*_args, **_kwargs):
        contract_repairs.append(1)
        return chapter_flow_finalize._ArchiveRepairAttempt(status="repaired", review=review)

    monkeypatch.setattr(chapter_flow_finalize, "persist_results", _persist)
    monkeypatch.setattr(
        chapter_flow_finalize,
        "_repair_unclosed_carry_forward",
        _repair_carry_forward,
    )
    monkeypatch.setattr(
        chapter_flow_finalize,
        "_repair_contract_execution_audit_block",
        _repair_contract,
    )
    _patch_post_persist_steps(monkeypatch)

    with pytest.raises(ConsistencyViolationError) as exc_info:
        await chapter_flow_finalize.finalize_chapter_result(
            _context_fixture(),
            review=review,
            trace=_trace_fixture(),
            allow_contract_audit_auto_repair=False,
        )

    assert exc_info.value is second_block
    assert len(persist_calls) == 2
    assert len(carry_forward_repairs) == 1
    assert not contract_repairs


@pytest.mark.asyncio
async def test_finalize_post_humanize_repair_reruns_terminal_humanize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-Humanize repair must be followed by another Humanize pass."""
    from dataclasses import replace

    from novel_forge.pipeline.long import (
        chapter_flow_finalize,
        chapter_flow_orchestrate,
        chapter_flow_review,
    )

    review = _review_fixture()
    humanize_inputs: list[str] = []
    persist_inputs: list[str] = []
    repair_inputs: list[str] = []
    attempt = 0

    async def _humanize(_context, candidate, _trace):
        humanize_inputs.append(candidate.current_text)
        return replace(candidate, current_text=candidate.current_text + "|H")

    monkeypatch.setattr(chapter_flow_orchestrate, "_apply_terminal_humanize", _humanize)
    monkeypatch.setattr(
        chapter_flow_review,
        "_build_quality_gate",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "_build_normalized_review_contracts",
        lambda *_args, **_kwargs: ([], []),
    )

    async def _persist(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        persisted_text = await kwargs["terminal_humanize"](args[6])
        persist_inputs.append(persisted_text)
        if attempt == 1:
            raise ConsistencyViolationError(
                ["Humanize 后知识边界复检阻断"],
                violation_kind="post_humanize_semantic_verification",
            )
        candidate = replace(review, current_text=persisted_text)
        return _persisted_fixture(candidate)

    async def _repair(_context, *, review, trace):
        del trace
        repair_inputs.append(review.current_text)
        return replace(
            review,
            current_text=review.current_text + "|R",
            quality_reports_stale_after_text_change=True,
            quality_reports_stale_reason="post_humanize_semantic_repair_before_rehumanize",
        )

    monkeypatch.setattr(chapter_flow_finalize, "persist_results", _persist)
    monkeypatch.setattr(
        chapter_flow_finalize,
        "_repair_post_humanize_semantic_block",
        _repair,
    )
    monkeypatch.setattr(
        chapter_flow_finalize,
        "_persist_quality_gate_report",
        lambda *_args, **_kwargs: {"verdict": "pass", "summary": ""},
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.finalize_tts_metadata.extract_tts_metadata",
        lambda **_kwargs: None,
    )

    result = await chapter_flow_finalize.finalize_chapter_result(
        _context_fixture(),
        review=review,
        trace=_trace_fixture(),
    )

    assert humanize_inputs == [review.current_text, review.current_text + "|H|R"]
    assert repair_inputs == [review.current_text + "|H"]
    assert persist_inputs == [review.current_text + "|H", review.current_text + "|H|R|H"]
    assert result.text == review.current_text + "|H|R|H"

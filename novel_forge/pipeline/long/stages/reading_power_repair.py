"""Reading power repair loop and helpers.

Extracted from quality_checks.py to keep that module manageable.
Contains:
- _execute_reading_power_repair_loop: multi-round reading power repair
- ReadingPowerRepairLoopResult: result dataclass
- All reading-power-specific helper functions
- Shared utilities: source_text_hash, _text_change_ratio, _infer_chapter_type
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, is_dataclass
from types import SimpleNamespace
from typing import Any, Callable, cast

from novel_forge.common.constants import severity_at_least
from novel_forge.core.repair_attempt_guidance import build_repair_attempt_guidance
from novel_forge.core.review.review_contracts import build_ticket_verification_results
from novel_forge.core.review.review_precision import prepare_findings_for_repair
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.core.utils.audit_issue import ensure_issue_id
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.decisions import (
    RepairContext,
    decide_repair_strategy,
)
from novel_forge.pipeline.long.repair_safety import (
    RepairDimension,
    RepairFailurePolicy,
    RepairRoundSnapshot,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    RepairLoopResult,
    RepairRoundContext,
    _RepairRoundKernel,
    resolve_max_issues_per_round,
)
from novel_forge.pipeline.style_profile_helpers import (
    get_profile_section,
    get_reading_power_eval_config,
    get_section_value,
)

_logger = get_logger("pipeline.reading_power_repair")

# ── Constants ────────────────────────────────────────────────────────────────

_READING_POWER_HOOK_ALIASES = {
    "suspense": "mystery",
    "revelation": "mystery",
    "悬念": "mystery",
    "谜团": "mystery",
    "揭示": "mystery",
    "危机": "crisis",
    "情绪": "emotion",
    "选择": "choice",
    "期待": "desire",
}
_READING_POWER_HOOK_TYPES = {"crisis", "mystery", "emotion", "choice", "desire", "none"}
_READING_POWER_HOOK_STRENGTHS = {"strong", "medium", "weak"}

_READING_POWER_MEMORY_TYPE_MAP: dict[str, str] = {
    "hook_missing": "pacing_issue",
    "hook_too_weak": "pacing_issue",
    "overall_score_low": "pacing_issue",
    "prev_hook_unfulfilled": "unresolved_thread",
    "payoff_missing": "forgotten_setup",
    "outline_mismatch": "alignment_weak",
}


# ── Shared utilities ─────────────────────────────────────────────────────────


def _text_change_ratio(before: str, after: str) -> float:
    """Estimate text change ratio: 0.0 = identical, 1.0 = total rewrite.

    Delegates to the canonical implementation in ``core.utils.text_validation``.
    """
    from novel_forge.core.utils.text_validation import text_change_ratio

    return text_change_ratio(before, after)


def _infer_chapter_type(chapter_text: str, bundle: Any) -> str:
    """Infer the chapter type based on content analysis."""
    if not chapter_text:
        return "mixed"

    text_len = len(chapter_text)
    dialogue_markers = ['"', '"', '"', '"', '"', '"', "说", "道", "问", "答"]
    dialogue_count = sum(chapter_text.count(marker) for marker in dialogue_markers)

    introspection_markers = ["想", "思考", "回忆", "记得", "感受", "内心", "心中", "思索"]
    introspection_count = sum(chapter_text.count(marker) for marker in introspection_markers)

    action_markers = ["跑", "跳", "打", "冲", "走", "进", "出", "战斗", "攻击", "飞"]
    action_count = sum(chapter_text.count(marker) for marker in action_markers)

    dialogue_ratio = dialogue_count / max(text_len / 100, 1)
    introspection_ratio = introspection_count / max(text_len / 500, 1)
    action_ratio = action_count / max(text_len / 200, 1)

    if dialogue_ratio > 15 and action_ratio < 5:
        return "dialogue"
    elif introspection_ratio > 10:
        return "reflection"
    elif action_ratio > 20 and dialogue_ratio < 5:
        return "action"
    elif chapter_text.count("\n\n") < 3:
        return "transition"
    else:
        return "mixed"


# ── Reading power helpers ────────────────────────────────────────────────────


def _build_reading_power_excerpt(text: str, *, limit: int = 8000) -> tuple[str, str]:
    """Build an evaluation excerpt that preserves the ending hook and chapter context."""
    text = text or ""
    text_len = len(text)
    if text_len <= limit:
        return text, "full_text"

    if limit <= 1200:
        return text[-limit:], f"tail_only_last_{limit}_chars"

    if text_len <= int(limit * 1.25):
        separator = "\n\n[... 跳至章尾，以下保留章尾钩子 ...]\n\n"
        available = max(1, limit - len(separator))
        head_budget = max(800, min(available // 3, int(available * 0.22)))
        tail_budget = max(1, available - head_budget)
        return (
            text[:head_budget] + separator + text[-tail_budget:],
            f"head_tail_excerpt_{limit}_chars",
        )

    middle_separator = "\n\n[... 章节中段省略，以下为中部采样 ...]\n\n"
    tail_separator = "\n\n[... 跳至章尾，以下保留章尾钩子 ...]\n\n"
    available = max(1, limit - len(middle_separator) - len(tail_separator))
    head_budget = max(1000, int(available * 0.22))
    middle_budget = max(1000, int(available * 0.22))
    if head_budget + middle_budget >= available:
        head_budget = available // 4
        middle_budget = available // 4
    tail_budget = max(1, available - head_budget - middle_budget)
    middle_start = max(head_budget, (text_len - middle_budget) // 2)
    middle_end = min(text_len - tail_budget, middle_start + middle_budget)
    middle_start = max(head_budget, middle_end - middle_budget)

    return (
        text[:head_budget]
        + middle_separator
        + text[middle_start:middle_end]
        + tail_separator
        + text[-tail_budget:],
        f"head_middle_tail_excerpt_{limit}_chars",
    )


def _is_reading_power_fallback(report: Any) -> bool:
    if report is None:
        return False
    if isinstance(report, dict):
        return bool(
            report.get("is_fallback")
            or str(report.get("evaluation_status", "")).lower() == "fallback"
        )
    return bool(
        getattr(report, "is_fallback", False)
        or getattr(report, "evaluation_status", "") == "fallback"
    )


def _read_field(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(cast(Any, value))
    return value


def _serialise_expected_hook(hook: Any) -> dict[str, Any] | None:
    if hook is None:
        return None
    if hasattr(hook, "model_dump"):
        dumped = hook.model_dump(mode="json")
        data = dict(dumped) if isinstance(dumped, dict) else {}
    elif isinstance(hook, dict):
        data = dict(hook)
    else:
        data = {
            "hook_type": _read_field(hook, "hook_type", ""),
            "hook_strength": _read_field(hook, "hook_strength", ""),
            "hook_description": _read_field(hook, "hook_description", ""),
        }
    if not any(str(data.get(k, "") or "").strip() for k in ("hook_type", "hook_description")):
        return None
    hook_type = str(data.get("hook_type", "") or "").strip().lower()
    hook_type = _READING_POWER_HOOK_ALIASES.get(hook_type, hook_type)
    if hook_type and hook_type not in _READING_POWER_HOOK_TYPES:
        hook_type = "mystery"
    if hook_type:
        data["hook_type"] = hook_type

    strength = str(data.get("hook_strength", "") or "").strip().lower()
    if strength and strength not in _READING_POWER_HOOK_STRENGTHS:
        strength = "medium"
    if strength:
        data["hook_strength"] = strength
    return data


def _serialise_expected_payoffs(payoffs: Any) -> list[dict[str, Any]]:
    serialised: list[dict[str, Any]] = []
    for payoff in list(payoffs or []):
        if payoff is None:
            continue
        if hasattr(payoff, "model_dump"):
            data = payoff.model_dump(mode="json")
        elif isinstance(payoff, dict):
            data = dict(payoff)
        else:
            data = {
                "payoff_type": _read_field(payoff, "payoff_type", ""),
                "description": _read_field(payoff, "description", ""),
                "strength": _read_field(payoff, "strength", "medium"),
            }
        if (
            str(data.get("payoff_type", "") or "").strip()
            or str(data.get("description", "") or "").strip()
        ):
            serialised.append(data)
    return serialised


def _extract_reading_power_expectations(
    *,
    bundle: Any,
    chapter_number: int,
    window_manager: Any | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Collect outline expectations, letting the window manager backfill gaps."""
    chapter_outline = getattr(bundle, "chapter_outline", None)
    expected_hook = _serialise_expected_hook(_read_field(chapter_outline, "expected_hook", None))
    expected_payoffs = _serialise_expected_payoffs(
        _read_field(chapter_outline, "expected_payoffs", []) or []
    )

    rp_hint: dict[str, Any] | None = None
    if window_manager is not None:
        try:
            rp_hint = window_manager.build_reading_power_hint(
                current_chapter=chapter_number,
                chapter_outline=chapter_outline,
            )
            if isinstance(rp_hint, dict):
                if expected_hook is None:
                    expected_hook = _serialise_expected_hook(rp_hint.get("outline_expected_hook"))
                if not expected_payoffs:
                    expected_payoffs = _serialise_expected_payoffs(
                        rp_hint.get("outline_expected_payoffs", []) or []
                    )
        except Exception as exc:
            _logger.debug(
                "reading_power_expectation_hint_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

    return expected_hook, expected_payoffs, rp_hint


def _build_eval_suspense_entries(
    window_manager: Any | None,
    chapter_number: int,
) -> list[dict[str, Any]]:
    if window_manager is None:
        return []
    try:
        builder = getattr(window_manager, "build_eval_suspense_entries", None)
        if callable(builder):
            entries = builder(chapter_number)
            if isinstance(entries, (list, tuple, set)):
                return [dict(item) for item in entries if isinstance(item, dict)]
    except Exception as exc:
        _logger.debug(
            "reading_power_suspense_entries_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
    return []


def _to_json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if is_dataclass(value) and not isinstance(value, type):
        dumped = asdict(cast(Any, value))
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _to_json_list(value: Any) -> list[dict[str, Any]]:
    items = list(value or []) if isinstance(value, (list, tuple)) else []
    return [item for item in (_to_json_dict(entry) for entry in items) if item]


def _narrative_phase_context_for_chapter(
    blueprint: Any, chapter_number: int
) -> dict[str, Any] | None:
    phases = _read_field(blueprint, "narrative_phases", []) or []
    for phase in list(phases or []):
        data = _to_json_dict(phase)
        if not data:
            continue
        try:
            start = int(data.get("chapter_start", 1) or 1)
            end = int(data.get("chapter_end", 999999) or 999999)
        except (TypeError, ValueError):
            continue
        if start <= chapter_number <= end:
            return data
    return None


def _build_reading_power_input(
    *,
    runner: Any,
    bundle: Any,
    packet: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    current_text: str,
    chapter_number: int,
    window_manager: Any | None = None,
    strict_review: bool = False,
    kernel_context: dict[str, Any] | None = None,
) -> tuple[Any, Any]:
    from novel_forge.pipeline.steps.reading_power_eval_step import ReadingPowerInput

    prev_rp_hook = ""
    if chapter_number > 1:
        prev_rp_path = bundle.layout.reading_power_report_path(chapter_number - 1)
        if runner._storage.exists(prev_rp_path):
            prev_rp_data = runner._storage.load_json(prev_rp_path)
            if prev_rp_data and not _is_reading_power_fallback(prev_rp_data):
                prev_rp_hook = str(prev_rp_data.get("hook_description", "") or "")

    min_payoffs = 1
    hook_score_config = None
    preferred_payoff_types: list[str] = []
    pacing_dialogue_ratio = "medium"
    style_profile = getattr(bundle, "style_profile", None)
    if style_profile is not None:
        min_payoffs, hook_score_config = get_reading_power_eval_config(style_profile)
        micro_payoff_config = get_profile_section(style_profile, "micro_payoff_config")
        raw_preferred_payoffs = get_section_value(
            micro_payoff_config,
            "preferred_types",
            [],
        )
        if isinstance(raw_preferred_payoffs, list):
            preferred_payoff_types = [
                str(item).strip() for item in raw_preferred_payoffs if str(item or "").strip()
            ]
        global_style = get_profile_section(style_profile, "global_style")
        pacing_dialogue_ratio = str(
            get_section_value(global_style, "dialogue_ratio", "medium") or "medium"
        )

    expected_hook, expected_payoffs, rp_hint = _extract_reading_power_expectations(
        bundle=bundle,
        chapter_number=chapter_number,
        window_manager=window_manager,
    )

    rp_excerpt, excerpt_strategy = _build_reading_power_excerpt(current_text)
    blueprint = getattr(bundle, "blueprint", None)
    main_plot_points = [
        str(item).strip()
        for item in list(getattr(bundle.chapter_outline, "main_plot_points", []) or [])
        if str(item).strip()
    ]

    rp_input = ReadingPowerInput(
        chapter_number=chapter_number,
        chapter_text=rp_excerpt,
        chapter_type=_infer_chapter_type(current_text, bundle),
        previous_hook_description=prev_rp_hook,
        genre=getattr(bundle.story_bible, "genre", "") or "",
        min_payoffs=min_payoffs,
        expected_hook=expected_hook,
        expected_payoffs=expected_payoffs,
        suspense_timeline_entries=_build_eval_suspense_entries(window_manager, chapter_number),
        hook_score_config=hook_score_config,
        excerpt_strategy=excerpt_strategy,
        narrative_phase_context=_narrative_phase_context_for_chapter(blueprint, chapter_number),
        suspense_schedule=_to_json_list(_read_field(blueprint, "suspense_schedule", []) or []),
        preferred_payoff_types=preferred_payoff_types,
        pacing_dialogue_ratio=pacing_dialogue_ratio,
        main_plot_points=main_plot_points,
        strict_review=strict_review,
        kernel_context=kernel_context,
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
    )
    return rp_input, rp_hint


def _persist_reading_power_report(
    *,
    runner: Any,
    bundle: Any,
    chapter_number: int,
    current_text: str,
    rp_report: Any,
    pipeline_stage: str,
) -> dict[str, Any]:
    payload = (
        rp_report.model_dump(mode="json") if hasattr(rp_report, "model_dump") else dict(rp_report)
    )
    payload["source_text_hash"] = source_text_hash(current_text)
    payload["pipeline_stage"] = pipeline_stage
    runner._storage.save_json(
        bundle.layout.reading_power_report_path(chapter_number),
        payload,
    )
    return payload


def _update_reading_power_window(
    *,
    runner: Any,
    bundle: Any,
    plan: Any,
    bridge: Any,
    chapter_number: int,
    rp_report: Any,
    window_manager: Any | None,
) -> None:
    if window_manager is None:
        return
    try:
        if _is_reading_power_fallback(rp_report):
            _logger.info(
                "reading_power_window_update skipped for fallback report | chapter=%d",
                chapter_number,
            )
            runner._on_step(
                "reading_power_eval_fallback",
                {
                    "chapter": chapter_number,
                    "reason": getattr(rp_report, "fallback_reason", "unknown"),
                },
            )
            return

        window_report = window_manager.update_window(
            current_chapter=chapter_number,
            chapter_outline=getattr(bundle, "chapter_outline", None),
            reading_power_report=rp_report,
        )
        window_manager.mark_suspense_events(
            chapter_number=chapter_number,
            plan=plan,
            bridge=bridge,
        )
        try:
            pydantic_report = getattr(window_manager, "_last_pydantic_report", None)
            report_data = (
                pydantic_report.model_dump(mode="json")
                if pydantic_report is not None
                else _jsonable(window_report)
            )
            report_path = (
                bundle.layout.root / "states" / f"reading_power_window_ch{chapter_number}.json"
            )
            runner._storage.save_json(report_path, report_data)
        except Exception as save_exc:
            _logger.warning(
                "reading_power_window_save_failed | chapter=%d | error=%s",
                chapter_number,
                save_exc,
            )
        alerts = getattr(window_report, "alerts", []) or getattr(
            window_report,
            "deviation_alerts",
            [],
        )
        if alerts:
            runner._on_step(
                "reading_power_window_alert",
                {"chapter": chapter_number, "alerts": [str(a) for a in alerts]},
            )
    except Exception as exc:
        _logger.warning(
            "reading_power_window_update_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


async def evaluate_and_record_reading_power(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    window_manager: Any | None = None,
    pipeline_stage: str = "quality_stage",
    step_name: str = "reading_power_eval",
    update_window: bool = True,
    precomputed_report: Any | None = None,
    precomputed_text_hash: str | None = None,
    strict_review: bool = False,
) -> Any:
    """Evaluate, persist, and optionally feed the reading-power window."""
    rp_report = None
    current_hash = source_text_hash(current_text)
    if precomputed_report is not None and precomputed_text_hash == current_hash:
        rp_report = precomputed_report

    if rp_report is None:
        try:
            from novel_forge.pipeline.steps.reading_power_eval_step import (
                ReadingPowerEvalStep,
            )

            kernel_composer = await load_story_kernel_composer(runner, bundle)
            reading_power_kernel_context = (
                kernel_composer.compose_reading_power_eval_input(chapter_number)
                if kernel_composer is not None
                else {}
            )
            rp_input, _rp_hint = _build_reading_power_input(
                runner=runner,
                bundle=bundle,
                packet=packet,
                bridge=bridge,
                plan=plan,
                current_text=current_text,
                chapter_number=chapter_number,
                window_manager=window_manager,
                strict_review=strict_review,
                kernel_context=reading_power_kernel_context,
            )
            rp_step = ReadingPowerEvalStep(
                router=runner._router,
                builder=runner._builder,
                settings=runner._settings,
                trace=trace,
            )
            rp_report = await rp_step.run(rp_input)
        except Exception as exc:
            _logger.warning(
                "reading_power_eval skipped | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
            rp_report = None

    if rp_report is None:
        from novel_forge.pipeline.steps.reading_power_eval_step import (
            ReadingPowerEvalStep as _RPES,
        )

        rp_report = _RPES._default_report(chapter_number, reason="quality_stage_error")
        _logger.info("reading_power_eval using default report | chapter=%d", chapter_number)

    _persist_reading_power_report(
        runner=runner,
        bundle=bundle,
        chapter_number=chapter_number,
        current_text=current_text,
        rp_report=rp_report,
        pipeline_stage=pipeline_stage,
    )
    runner._on_step(step_name, rp_report)

    if update_window:
        _update_reading_power_window(
            runner=runner,
            bundle=bundle,
            plan=plan,
            bridge=bridge,
            chapter_number=chapter_number,
            rp_report=rp_report,
            window_manager=window_manager,
        )

    return rp_report


def _reading_power_repair_focus(
    *,
    report: Any,
    expected_hook: dict[str, Any] | None,
    expected_payoffs: list[dict[str, Any]],
    min_payoffs: int,
    repair_threshold: float,
    chapter_number: int = 0,
) -> list[str]:
    if report is None or _is_reading_power_fallback(report):
        return []

    focus: list[str] = []
    score = float(getattr(report, "overall_score", 0.0) or 0.0)
    hook_type = str(getattr(report, "hook_type", "none") or "none").lower()
    hook_strength = str(getattr(report, "hook_strength", "weak") or "weak").lower()
    micro_payoffs = list(getattr(report, "micro_payoffs", []) or [])

    if score < repair_threshold:
        focus.append(
            f"综合追读力 {score:.1f} 低于修复线 {repair_threshold:.1f}，需提升本章点击下一章的理由。"
        )
    if hook_type == "none":
        focus.append("章尾缺少明确钩子：补一个具体可感的危机、悬念、情绪冲击或选择压力。")
    elif hook_strength == "weak":
        focus.append("章尾钩子力度偏弱：让结尾出现角色必须应对的动作后果、证据发现或选择代价。")
    if chapter_number > 1 and not bool(getattr(report, "prev_hook_fulfilled", True)):
        focus.append("上一章钩子没有被回应：在正文前中段补出承接、答案、反转或阶段性兑现。")
    if len(micro_payoffs) < int(min_payoffs):
        focus.append(f"章内微兑现不足：至少补足 {int(min_payoffs)} 个信息、关系、能力或线索兑现。")

    outline_hook_match = getattr(report, "outline_hook_match", None)
    if isinstance(outline_hook_match, dict):
        match_type = str(outline_hook_match.get("match_type", "") or "")
        if match_type == "different":
            reason = str(outline_hook_match.get("reason", "") or "").strip()
            suffix = f"原因：{reason}" if reason else "请按大纲预期重设章尾钩子。"
            focus.append(f"大纲钩子未达成：{suffix}")

    payoff_coverage = getattr(report, "outline_payoff_coverage", None)
    if isinstance(payoff_coverage, dict):
        coverage = float(payoff_coverage.get("coverage_ratio", 0.0) or 0.0)
        missing = payoff_coverage.get("missing", []) or []
        if expected_payoffs and coverage < 0.5:
            missing_text = "；".join(str(item) for item in missing if item)
            focus.append(f"大纲微兑现覆盖不足：优先补齐 {missing_text or '未兑现项目'}。")

    if expected_hook:
        hook_desc = str(expected_hook.get("hook_description", "") or "").strip()
        hook_type_plan = str(expected_hook.get("hook_type", "") or "").strip()
        if hook_desc or hook_type_plan:
            focus.append(f"本章章尾应靠近大纲规划：{hook_type_plan} / {hook_desc}")

    return focus


def _coerce_repair_ticket(item: Any) -> RepairTicket | None:
    if isinstance(item, RepairTicket):
        return item
    if isinstance(item, dict):
        try:
            return RepairTicket.model_validate(item)
        except Exception:
            return None
    return None


def _filter_reading_power_tickets(
    tickets: list[Any] | tuple[Any, ...] | None,
    *,
    chapter_number: int,
) -> list[RepairTicket]:
    """Keep repair tickets that explicitly target this chapter's reading-power lane."""
    result: list[RepairTicket] = []
    for raw in list(tickets or []):
        ticket = _coerce_repair_ticket(raw)
        if ticket is None:
            continue
        dimension = str(ticket.dimension or "").strip().lower()
        if dimension != "reading_power":
            continue
        ticket_chapter = int(ticket.chapter_number or 0)
        if ticket_chapter not in {0, chapter_number}:
            continue
        result.append(ticket)
    return result


def _reading_power_issue_from_ticket(ticket: RepairTicket) -> Any:
    """Project a normalized repair ticket into ReadingPowerRepairInput's issue shape."""
    from novel_forge.core.review.review_contracts import (
        repair_ticket_to_reading_power_issue_payload,
    )
    from novel_forge.core.schemas.reading_power_repair import ReadingPowerIssue

    payload = repair_ticket_to_reading_power_issue_payload(ticket)
    postconditions = [
        dict(item)
        for item in list(payload.get("postconditions", []) or [])
        if isinstance(item, dict)
    ]
    if not postconditions:
        postconditions = [
            {
                "validator_id": "reading_power_ticket_acceptance",
                "description": str(item or "").strip(),
                "evidence_hint": "repair_ticket.acceptance_criteria",
                "required": True,
            }
            for item in list(ticket.acceptance_criteria or [])
            if str(item or "").strip()
        ]
    return ReadingPowerIssue(
        issue_type=str(payload.get("issue_type") or "reading_power_issue"),
        severity=str(payload.get("severity") or "medium"),
        summary=str(payload.get("summary") or ticket.target_summary or ticket.repair_goal),
        evidence=str(payload.get("evidence") or ""),
        fix_suggestion=str(payload.get("fix_suggestion") or ticket.repair_goal),
        location=str(payload.get("location") or "全文"),
        issue_id=str(payload.get("issue_id") or ""),
        repair_surface="chapter_text",
        status="open",
        blocking=bool(ticket.blocking),
        postconditions=postconditions,
    )


def _dedupe_reading_power_issues(issues: list[Any]) -> list[Any]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[Any] = []
    for issue in issues:
        issue_id = str(getattr(issue, "issue_id", "") or "").strip()
        key = (
            issue_id,
            str(getattr(issue, "issue_type", "") or "").strip().lower(),
            str(getattr(issue, "summary", "") or "").strip(),
            str(getattr(issue, "location", "") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _reading_power_issues_from_tickets(tickets: list[RepairTicket]) -> list[Any]:
    return _dedupe_reading_power_issues(
        [_reading_power_issue_from_ticket(ticket) for ticket in tickets]
    )


def _reading_power_ledger_payload(issue: Any, *, chapter_number: int) -> dict[str, Any]:
    """Keep reading-power ledger identity stable across repair rounds."""
    return {
        "issue_id": ensure_issue_id(
            issue,
            "reading_power",
            chapter_number=chapter_number,
            repair_surface="chapter_text",
        ),
        "issue_type": (getattr(issue, "issue_type", "") or "").lower(),
        "severity": (getattr(issue, "severity", "") or "medium").lower(),
        "summary": getattr(issue, "summary", "") or "",
        "evidence": getattr(issue, "evidence", "") or "",
        "location": getattr(issue, "location", "") or "",
    }


def _reading_power_repair_tickets_from_report(
    report: Any,
    *,
    chapter_number: int,
    current_text: str,
    min_payoffs: int,
    repair_threshold: float,
) -> list[RepairTicket]:
    """Compile a reading-power report into the shared repair-ticket contract."""
    if report is None or _is_reading_power_fallback(report):
        return []

    from novel_forge.core.review.review_contracts import (
        compile_repair_tickets_from_findings,
        normalize_issue_to_finding,
        reading_power_report_to_findings,
    )

    text_hash = source_text_hash(current_text)
    findings = reading_power_report_to_findings(
        report,
        chapter_number=chapter_number,
        current_text_hash=text_hash,
        min_payoffs=min_payoffs,
    )
    score = float(getattr(report, "overall_score", 0.0) or 0.0)
    if score < repair_threshold and not any(
        str(getattr(finding, "issue_type", "") or "") == "overall_score_low" for finding in findings
    ):
        findings.append(
            normalize_issue_to_finding(
                {
                    "issue_type": "overall_score_low",
                    "severity": "medium",
                    "summary": f"综合追读力 {score:.1f} 低于修复线 {repair_threshold:.1f}",
                    "evidence": f"overall_score={score:.1f}",
                    "fix_suggestion": "在不推翻剧情的前提下，补强读者点击下一章的具体理由。",
                    "location": "章尾",
                },
                source_module="reading_power_eval",
                chapter_number=chapter_number,
                dimension="reading_power",
                current_text_hash=text_hash,
            )
        )

    findings, _readiness = prepare_findings_for_repair(
        findings,
        current_text=current_text,
        completed_chapters=[chapter_number],
    )
    return compile_repair_tickets_from_findings(
        findings,
        require_auto_repair_eligible=True,
    )


def _reading_power_repair_issues_from_report(
    report: Any,
    *,
    chapter_number: int,
    current_text: str,
    min_payoffs: int,
    repair_threshold: float,
) -> list[Any]:
    return _reading_power_issues_from_tickets(
        _reading_power_repair_tickets_from_report(
            report,
            chapter_number=chapter_number,
            current_text=current_text,
            min_payoffs=min_payoffs,
            repair_threshold=repair_threshold,
        )
    )


def _reading_power_memory_issue_type(issue_type: Any) -> str:
    normalized = str(issue_type or "").strip().lower()
    return _READING_POWER_MEMORY_TYPE_MAP.get(normalized, "pacing_issue")


def _reading_power_memory_query_issue(issue: Any) -> SimpleNamespace:
    """Project reading-power issues into CriticAgent's memory issue taxonomy."""
    issue_type = str(getattr(issue, "issue_type", "") or "").strip().lower()
    mapped_type = _reading_power_memory_issue_type(issue_type)
    summary = str(getattr(issue, "summary", "") or "").strip()
    location = str(getattr(issue, "location", "") or "").strip()
    evidence = str(getattr(issue, "evidence", "") or "").strip()
    parts = [summary]
    if location:
        parts.append(f"位置：{location}")
    if evidence:
        parts.append(f"证据：{evidence}")
    return SimpleNamespace(
        issue_type=mapped_type,
        summary="；".join(part for part in parts if part),
        original_issue_type=issue_type,
    )


def _reading_power_compact_issue_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def _reading_power_bigram_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_bigrams = {left[i : i + 2] for i in range(max(0, len(left) - 1))} or {left}
    right_bigrams = {right[i : i + 2] for i in range(max(0, len(right) - 1))} or {right}
    union = left_bigrams | right_bigrams
    return len(left_bigrams & right_bigrams) / len(union) if union else 0.0


def _find_current_reading_power_critique_signature(
    episodic_memory: Any,
    issue: Any,
    *,
    current_chapter: int,
) -> str | None:
    """Find the persisted reading-power memory entry matching a current issue."""
    critique_index = getattr(episodic_memory, "_critique_index", {}) or {}
    issue_type = str(getattr(issue, "issue_type", "") or "").strip().lower()
    mapped_type = _reading_power_memory_issue_type(issue_type)
    summary = _reading_power_compact_issue_text(getattr(issue, "summary", ""))
    if not summary:
        return None

    best_signature: str | None = None
    best_score = 0.0
    for sig, entry in critique_index.items():
        if getattr(entry, "chapter_number", None) != current_chapter:
            continue
        metadata = getattr(entry, "metadata", {}) or {}
        entry_type = str(getattr(entry, "issue_type", "") or "").strip().lower()
        original_type = str(metadata.get("reading_power_issue_type") or "").strip().lower()
        if entry_type != mapped_type and original_type != issue_type:
            continue
        entry_summary = _reading_power_compact_issue_text(getattr(entry, "summary", ""))
        if not entry_summary:
            continue
        if entry_summary == summary:
            return str(sig)
        if entry_summary in summary or summary in entry_summary:
            score = 0.9
        else:
            score = _reading_power_bigram_similarity(entry_summary, summary)
        if score > best_score:
            best_score = score
            best_signature = str(sig)

    return best_signature if best_score >= 0.45 else None


async def _index_reading_power_memory_issues(
    *,
    episodic_memory: Any,
    chapter_number: int,
    issues: list[Any],
) -> int:
    """Index actionable reading-power issues into episodic critique memory."""
    add_entry = getattr(episodic_memory, "_add_critique_entry", None)
    if not callable(add_entry):
        return 0

    try:
        from novel_forge.memory.episodic import CritiqueIndex
    except Exception:
        return 0

    indexed = 0
    for issue in issues:
        summary = str(getattr(issue, "summary", "") or "").strip()
        if not summary:
            continue
        original_type = str(getattr(issue, "issue_type", "") or "").strip().lower()
        mapped_type = _reading_power_memory_issue_type(original_type)
        entry = CritiqueIndex(
            chapter_number=chapter_number,
            issue_type=mapped_type,
            severity=str(getattr(issue, "severity", "medium") or "medium").strip().lower(),
            summary=summary,
            evidence=str(getattr(issue, "evidence", "") or "").strip(),
            suggested_fix=str(getattr(issue, "fix_suggestion", "") or "").strip(),
            affected_chapters=[chapter_number],
            metadata={
                "source": "reading_power_repair",
                "reading_power_issue_type": original_type,
                "location": str(getattr(issue, "location", "") or "").strip(),
                "issue_id": str(getattr(issue, "issue_id", "") or "").strip(),
            },
        )
        try:
            result = add_entry(entry)
            if inspect.isawaitable(result):
                await result
            indexed += 1
        except Exception as exc:
            _logger.debug(
                "reading_power_memory_index_failed | chapter=%d | issue_type=%s | error=%s",
                chapter_number,
                original_type,
                exc,
            )
    return indexed


def _reading_power_repair_strategy_label(issues: list[Any]) -> str:
    issue_types = {str(getattr(issue, "issue_type", "") or "").strip().lower() for issue in issues}
    if not issue_types:
        return "reading_power_repair"
    if issue_types <= {"hook_missing", "hook_too_weak", "outline_mismatch"}:
        return "ending_hook"
    if issue_types <= {"payoff_missing", "prev_hook_unfulfilled"}:
        return "targeted_payoff"
    return "fulltext"


def _record_reading_power_repair_memory_result(
    *,
    episodic_memory: Any,
    chapter_number: int,
    round_num: int,
    pre_issues: list[Any],
    ledger: Any,
    score_before: float,
    score_after: float,
    strategy: str,
) -> int:
    """Persist reading-power repair outcomes back onto matching memory entries."""
    record_result = getattr(episodic_memory, "record_repair_result", None)
    if not callable(record_result):
        return 0

    if getattr(ledger, "has_regression", False):
        result = "regression"
    elif getattr(ledger, "resolved", None) or getattr(ledger, "downgraded", None):
        result = "success"
    else:
        result = "no_op"

    new_issue_types = [
        str(getattr(issue, "issue_type", "") or "")
        for issue in getattr(ledger, "new_high_critical", []) or []
        if getattr(issue, "issue_type", "")
    ]

    recorded = 0
    seen_signatures: set[str] = set()
    for issue in pre_issues:
        signature = _find_current_reading_power_critique_signature(
            episodic_memory,
            issue,
            current_chapter=chapter_number,
        )
        if not signature or signature in seen_signatures:
            continue
        if record_result(
            signature,
            chapter=chapter_number,
            round_num=round_num,
            strategy=strategy,
            result=result,
            new_issues=new_issue_types,
            score_before=score_before,
            score_after=score_after,
        ):
            seen_signatures.add(signature)
            recorded += 1
    return recorded


# ── Result dataclass ─────────────────────────────────────────────────────────


@dataclass
class ReadingPowerRepairLoopResult:
    """Result returned by _execute_reading_power_repair_loop."""

    current_text: str
    report: Any | None
    text_hash: str | None
    repair_exhausted: bool = False
    rounds_used: int = 0
    applied: bool = False
    rolled_back: bool = False
    best_effort_accepted: bool = False
    best_effort_reason: str = ""
    needs_human_review: bool = False
    verification_evidence: dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[Any]:
        """Allow unpacking as (current_text, report, text_hash) for backward compat."""
        yield self.current_text
        yield self.report
        yield self.text_hash


# ── Promoted nested helper ───────────────────────────────────────────────────


def _record_final_reading_power(
    *,
    runner: Any,
    bundle: Any,
    chapter_number: int,
    plan: Any,
    bridge: Any,
    window_manager: Any | None,
    current_text_value: str,
    report_value: Any,
) -> str:
    _persist_reading_power_report(
        runner=runner,
        bundle=bundle,
        chapter_number=chapter_number,
        current_text=current_text_value,
        rp_report=report_value,
        pipeline_stage="final_review_text",
    )
    runner._on_step("reading_power_final_eval", report_value)
    _update_reading_power_window(
        runner=runner,
        bundle=bundle,
        plan=plan,
        bridge=bridge,
        chapter_number=chapter_number,
        rp_report=report_value,
        window_manager=window_manager,
    )
    return source_text_hash(current_text_value)


# ── ReadingPowerRepairRunner ──────────────────────────────────────────────────


class ReadingPowerRepairRunner(_RepairRoundKernel[ReadingPowerReport]):
    """Reading-power-specific repair loop runner.

    Encapsulates the reading power repair loop with domain-specific hooks:
    - Reading power evaluation with window manager integration
    - Hook/payoff tracking from chapter outline
    - Custom issue building from ReadingPowerReport
    - Post-repair causal/continuity validation
    """

    def __init__(
        self,
        runner: Any,
        bundle: Any,
        packet: Any,
        bridge: Any,
        plan: Any,
        trace: Any,
        config: RepairLoopConfig,
        on_step: Callable[[str, Any], None],
        chapter_number: int,
        window_manager: Any | None = None,
        window_config: Any | None = None,
    ) -> None:
        super().__init__(config, on_step, chapter_number, trace=trace)
        self._runner = runner
        self._bundle = bundle
        self._packet = packet
        self._bridge = bridge
        self._plan = plan
        self._trace = trace
        self._window_manager = window_manager
        self._window_config = window_config
        self._loop_start_text: str = ""
        self._rolled_back: bool = False
        self._rp_warnings: list[str] = []
        self._original_on_step = on_step
        self._on_step = self._on_step_with_warning_capture
        self._previous_hook_description: str = ""
        self._repair_trigger_threshold: float = 0.0
        self._min_payoffs: int = 1
        self._expected_hook: str = ""
        self._expected_payoffs: list[Any] = []
        self._rp_hint: str = ""
        self._kernel_context: dict[str, Any] = {}
        self._episodic_memory: Any = None
        self._active_rp_tickets: list[Any] = []
        self._known_chars: list[str] = []
        self._forbidden_elements: list[str] = []
        self._forbidden_elements_soft: list[str] = []
        self._intentional_callbacks: list[str] = []
        self._pre_built_issues: list[Any] = []
        self._pre_built_issues_used: bool = False

    def _on_step_with_warning_capture(self, event: str, payload: Any) -> None:
        if isinstance(payload, dict) and "error" in payload and "_error" in event:
            self._rp_warnings.append(str(payload["error"]))
        self._original_on_step(event, payload)

    async def on_round_start(self, ctx: RepairRoundContext[ReadingPowerReport]) -> None:
        """Pre-round: memory guidance."""
        from novel_forge.pipeline.long.stages.chapter_repair_support import (
            _build_memory_guidance,
            _get_runner_episodic_memory,
        )

        if self._episodic_memory is None:
            self._episodic_memory = _get_runner_episodic_memory(self._runner)

        memory_guidance = await _build_memory_guidance(
            episodic_memory=self._episodic_memory,
            current_issues=[
                _reading_power_memory_query_issue(issue) for issue in ctx.must_fix_issues
            ],
            current_chapter=self._chapter_number,
        )
        if memory_guidance:
            strategy = decide_repair_strategy(
                RepairContext(
                    current_round=ctx.round_number,
                    max_rounds=self._config.max_rounds,
                    score=ctx.score,
                    score_threshold=self._config.score_threshold,
                    must_fix_issues=tuple(ctx.must_fix_issues),
                    previous_score=ctx.previous_score,
                    memory_guidance=memory_guidance,
                )
            )
            memory_guidance["strategy_recommendation"] = {
                "preferred_strategy": strategy.preferred_strategy,
                "reason": strategy.reason,
                "confidence": strategy.confidence,
                "warning": strategy.warning,
            }
            self._on_step(
                "reading_power_memory_guidance_added",
                {
                    "chapter": self._chapter_number,
                    "round": ctx.round_number + 1,
                    "matched_issues": len(memory_guidance.get("matched_issues", [])),
                    "success_rate": memory_guidance.get("success_rate"),
                    "preferred_strategy": strategy.preferred_strategy,
                },
            )
        ctx.extra["reading_power_memory_guidance"] = memory_guidance

    async def execute_repair(self, ctx: RepairRoundContext[ReadingPowerReport]) -> str:
        from novel_forge.core.schemas.reading_power_repair import ReadingPowerRepairInput
        from novel_forge.pipeline.steps.reading_power_repair_step import (
            ReadingPowerRepairStep,
        )

        rp_step = ReadingPowerRepairStep(
            self._runner._router,
            self._runner._builder,
            settings=self._runner._settings,
            trace=self._trace,
            on_step=getattr(self._runner, "on_step", None),
        )
        rp_input = ReadingPowerRepairInput(
            chapter_number=self._chapter_number,
            chapter_text=ctx.current_text,
            issues=list(ctx.must_fix_issues),
            expected_hook=self._expected_hook,
            expected_payoffs=self._expected_payoffs,
            previous_hook_description=self._previous_hook_description,
            reading_power_hint=self._rp_hint,
            memory_guidance=ctx.extra.get("reading_power_memory_guidance"),
            repair_attempt_guidance=ctx.extra.get("repair_attempt_guidance")
            or build_repair_attempt_guidance(
                domain="reading_power",
                round_number=ctx.round_number + 1,
                max_rounds=self._config.max_rounds,
                issues=list(ctx.must_fix_issues),
                previous_issues=ctx.pre_issues,
                current_score=ctx.score,
                score_threshold=self._config.score_threshold,
                previous_score=ctx.previous_score,
            ),
            style_profile=getattr(self._bundle, "style_profile", None),
            editorial_contract=getattr(self._bundle, "editorial_contract", None),
            forbidden_elements=self._forbidden_elements,
            forbidden_elements_soft=self._forbidden_elements_soft,
            intentional_callbacks=self._intentional_callbacks,
            iteration=ctx.round_number + 1,
            character_profiles=self._known_chars,
            kernel_context=self._kernel_context,
            chapter_state_packet=self._packet,
            chapter_outline=getattr(self._bundle, "chapter_outline", None),
            chapter_plan=self._plan,
            chapter_bridge=self._bridge,
            chapter_source_slice=getattr(self._bundle, "chapter_source_slice", None),
        )
        rp_result = await rp_step.run(rp_input)
        if not rp_result.applied:
            self._on_step(
                "reading_power_repair_no_op",
                {
                    "chapter": self._chapter_number,
                    "round": ctx.round_number + 1,
                    "reason": rp_result.failure_reason or "no_change",
                },
            )
        return rp_result.revised_text.strip() or ctx.current_text

    async def evaluate(self, text: str) -> Any:
        return await evaluate_and_record_reading_power(
            runner=self._runner,
            bundle=self._bundle,
            packet=self._packet,
            bridge=self._bridge,
            plan=self._plan,
            current_text=text,
            chapter_number=self._chapter_number,
            trace=self._trace,
            window_manager=self._window_manager,
            pipeline_stage="post_repair",
            step_name="reading_power_eval_after_repair",
            update_window=False,
            strict_review=True,
        )

    def extract_issues(self, report: Any) -> list[Any]:
        if self._pre_built_issues and not self._pre_built_issues_used:
            self._pre_built_issues_used = True
            return list(self._pre_built_issues)
        return _reading_power_repair_issues_from_report(
            report,
            chapter_number=self._chapter_number,
            current_text="",
            min_payoffs=self._min_payoffs,
            repair_threshold=float(self._config.score_threshold or 0.0),
        )

    def compute_score(self, report: Any) -> float:
        return float(getattr(report, "overall_score", 0.0) or 0.0)

    def filter_issues_for_round(
        self,
        issues: list[Any],
        current_round: int,
        ctx: RepairRoundContext[ReadingPowerReport],
    ) -> list[Any]:
        return list(issues)

    async def on_repair_success(
        self, ctx: RepairRoundContext[ReadingPowerReport], revised_text: str
    ) -> str:
        """Keep the unverified text in memory until the original evaluator passes."""
        return revised_text

    async def on_recheck_success(
        self,
        ctx: RepairRoundContext[ReadingPowerReport],
        new_report: ReadingPowerReport,
        ledger: Any,
    ) -> None:
        """Post-recheck: ticket verification + memory result recording."""
        if self._active_rp_tickets:
            post_issues_raw = self.extract_issues(new_report)
            verification_results = build_ticket_verification_results(
                self._active_rp_tickets,
                remaining_issues=list(post_issues_raw),
                current_text=ctx.current_text,
                applied=True,
                metadata={
                    "chapter_number": self._chapter_number,
                    "repair_dimension": "reading_power",
                    "round": ctx.round_number + 1,
                },
            )
            ctx.extra["verification_results"] = verification_results

        if self._episodic_memory is not None:
            _memory_records = _record_reading_power_repair_memory_result(
                episodic_memory=self._episodic_memory,
                chapter_number=self._chapter_number,
                round_num=ctx.round_number + 1,
                pre_issues=ctx.pre_issues,
                ledger=ledger,
                score_before=ctx.previous_score or 0.0,
                score_after=self.compute_score(new_report),
                strategy=_reading_power_repair_strategy_label(ctx.pre_issues),
            )
            if _memory_records:
                self._on_step(
                    "reading_power_repair_memory_results_recorded",
                    {
                        "chapter": self._chapter_number,
                        "round": ctx.round_number + 1,
                        "recorded": _memory_records,
                    },
                )

    async def on_loop_exit(self, ctx: RepairRoundContext[ReadingPowerReport]) -> None:
        """Post-loop: warning for remaining issues + cross-dimension checks."""
        # Warning if must-fix issues remain
        if ctx.must_fix_issues:
            message = (
                f"追读力修复发现仍有 {len(ctx.must_fix_issues)} 个中优先级以上问题未解决，"
                f"建议人工复核。"
            )
            self._rp_warnings.append(message)
            self._on_step(
                "reading_power_repair_warning",
                {
                    "chapter": self._chapter_number,
                    "message": message,
                    "score": self.compute_score(ctx.report) if ctx.report else 0.0,
                    "remaining_issues": len(ctx.must_fix_issues),
                },
            )

        # Post-repair causal/continuity checks
        rounds_used = ctx.round_number + 1 if ctx.any_applied else 0
        if rounds_used > 0 and ctx.current_text != self._loop_start_text:
            self._on_step(
                "reading_power_post_repair_checks_start",
                {
                    "chapter": self._chapter_number,
                    "repair_rounds": rounds_used,
                    "change_ratio": round(
                        _text_change_ratio(self._loop_start_text, ctx.current_text), 4
                    ),
                },
            )
            try:
                from novel_forge.pipeline.long.stages.chapter_repair_support import (
                    run_post_repair_checks,
                )

                alignment_report = None
                continuity_report = None
                chapter_repair_report = None
                try:
                    align_path = self._bundle.layout.alignment_report_path(
                        self._chapter_number
                    )
                    if self._runner._storage.exists(align_path):
                        align_data = self._runner._storage.load_json(align_path)
                        if align_data:
                            from novel_forge.core.schemas.chapter import AlignmentReport

                            alignment_report = AlignmentReport.model_validate(align_data)
                except Exception:
                    pass

                if alignment_report is not None:
                    (
                        alignment_report,
                        continuity_report,
                        chapter_repair_report,
                    ) = await run_post_repair_checks(
                        self._runner,
                        self._bundle,
                        self._packet,
                        self._bridge,
                        self._plan,
                        self._loop_start_text,
                        ctx.current_text,
                        self._chapter_number,
                        alignment_report,
                        continuity_report
                        or type(
                            "_DummyCont", (), {"continuity_score": 8.5, "issues": []}
                        )(),
                        chapter_repair_report,
                        self._trace,
                        skip_chapter_repair=True,
                    )
                    self._on_step(
                        "reading_power_post_repair_checks_done",
                        {
                            "chapter": self._chapter_number,
                            "alignment_score": round(
                                float(
                                    getattr(alignment_report, "alignment_score", 0.0)
                                ),
                                2,
                            ),
                            "continuity_score": round(
                                float(
                                    getattr(continuity_report, "continuity_score", 0.0)
                                ),
                                2,
                            ),
                        },
                    )
            except Exception as exc:
                failure_policy = RepairFailurePolicy(
                    self._original_on_step, logger=_logger
                )
                failure_policy.post_repair_check_failed(
                    snapshot=RepairRoundSnapshot(
                        stage=RepairDimension.READING_POWER,
                        chapter_number=self._chapter_number,
                        round_number=rounds_used,
                        text=ctx.current_text,
                        report=ctx.report,
                    ),
                    exc=exc,
                    event_name="reading_power_post_repair_checks_failed",
                    action="skip_cross_dimension_checks_keep_current_text",
                    repair_exhausted=False,
                )

            try:
                from novel_forge.pipeline.long.stages.causal_repair import (
                    run_causal_validation,
                )

                await run_causal_validation(
                    self._runner,
                    self._bundle,
                    self._bridge,
                    ctx.current_text,
                    self._chapter_number,
                    self._trace,
                    previous_chapter_ending=getattr(
                        self._packet, "previous_chapter_ending", ""
                    )
                    or "",
                    recheck_mode=True,
                    strict_review=True,
                )
                self._on_step(
                    "reading_power_post_repair_causal_done",
                    {"chapter": self._chapter_number},
                )
            except Exception as exc:
                failure_policy = RepairFailurePolicy(
                    self._original_on_step, logger=_logger
                )
                failure_policy.post_repair_check_failed(
                    snapshot=RepairRoundSnapshot(
                        stage=RepairDimension.READING_POWER,
                        chapter_number=self._chapter_number,
                        round_number=rounds_used,
                        text=ctx.current_text,
                        report=ctx.report,
                    ),
                    exc=exc,
                    event_name="reading_power_post_repair_causal_failed",
                    action="skip_causal_recheck_keep_current_text",
                    repair_exhausted=False,
                )

    def build_extra_result(
        self,
        ctx: RepairRoundContext[ReadingPowerReport],
        result: RepairLoopResult[ReadingPowerReport],
    ) -> ReadingPowerRepairLoopResult:
        """Convert RepairLoopResult to ReadingPowerRepairLoopResult."""
        loop_start = self._loop_start_text or result.current_text
        rounds = result.rounds_used
        text_hash = source_text_hash(result.current_text)
        return ReadingPowerRepairLoopResult(
            current_text=result.current_text,
            report=result.report,
            text_hash=text_hash,
            repair_exhausted=result.repair_exhausted,
            rounds_used=rounds,
            applied=bool(rounds > 0 and result.current_text != loop_start),
            rolled_back=self._rolled_back,
            best_effort_accepted=result.best_effort_accepted,
            best_effort_reason=result.best_effort_reason,
            needs_human_review=result.needs_human_review,
            verification_evidence=dict(result.extra.get("candidate_verification") or {}),
        )

    async def run(
        self,
        current_text: str,
        initial_report: ReadingPowerReport,
    ) -> ReadingPowerRepairLoopResult:
        """Override run to ensure all paths return ReadingPowerRepairLoopResult."""
        self._loop_start_text = current_text
        result = await super().run(current_text=current_text, initial_report=initial_report)
        if isinstance(result, ReadingPowerRepairLoopResult):
            return result
        return self.build_extra_result(
            RepairRoundContext[ReadingPowerReport](
                current_text=result.current_text,
                report=result.report,
            ),
            result,
        )

    # Helper overrides for ABC _detect_drift (reading-power uses involved_characters)
    def _get_pov_character(self) -> str:
        return getattr(self._bundle.chapter_outline, "pov_character", "") or ""

    def _get_known_characters(self) -> list[str]:
        outline_chars = (
            getattr(self._bundle.chapter_outline, "involved_characters", None)
            or getattr(self._bundle.chapter_outline, "required_characters", None)
            or []
        )
        pov = self._get_pov_character()
        return list(dict.fromkeys([*outline_chars, pov] if pov else outline_chars))

    def _get_chapter_outline(self) -> Any:
        return self._bundle.chapter_outline


# ── Main loop function ───────────────────────────────────────────────────────


async def _execute_reading_power_repair_loop(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    *,
    window_manager: Any | None = None,
    window_config: Any | None = None,
    repair_tickets: list[RepairTicket] | tuple[Any, ...] | None = None,
    precomputed_reading_power_report: Any | None = None,
    precomputed_reading_power_text_hash: str | None = None,
    max_reading_power_rounds: int | None = None,
) -> ReadingPowerRepairLoopResult:
    """Run multi-round reading power repair loop.

    Delegates to ReadingPowerRepairRunner which encapsulates the loop logic.
    """
    if not bool(getattr(runner._settings, "long_reading_power_repair_enabled", True)):
        return ReadingPowerRepairLoopResult(
            current_text=current_text,
            report=None,
            text_hash=None,
        )

    min_payoffs = 1
    style_profile = getattr(bundle, "style_profile", None)
    if style_profile is not None:
        min_payoffs, _ = get_reading_power_eval_config(style_profile)

    expected_hook, expected_payoffs, rp_hint = _extract_reading_power_expectations(
        bundle=bundle,
        chapter_number=chapter_number,
        window_manager=window_manager,
    )
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    kernel_context = (
        kernel_composer.compose_reading_power_repair_input(chapter_number)
        if kernel_composer is not None
        else {}
    )

    _settings_rp_threshold = getattr(
        runner._settings,
        "long_reading_power_repair_threshold",
        getattr(runner._settings, "long_reading_power_score_threshold", 6.0),
    )
    _rp_threshold = float(_settings_rp_threshold if _settings_rp_threshold is not None else 6.0)
    _window_warning_threshold = float(
        getattr(window_config, "warning_score_threshold", _rp_threshold)
        if window_config is not None
        else _rp_threshold
    )
    _repair_trigger_threshold = max(_rp_threshold, _window_warning_threshold)

    report = await evaluate_and_record_reading_power(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        current_text=current_text,
        chapter_number=chapter_number,
        trace=trace,
        window_manager=window_manager,
        pipeline_stage="pre_repair",
        step_name="reading_power_prerepair_eval",
        update_window=False,
        precomputed_report=precomputed_reading_power_report,
        precomputed_text_hash=precomputed_reading_power_text_hash,
    )

    _external_rp_tickets = _filter_reading_power_tickets(
        repair_tickets,
        chapter_number=chapter_number,
    )
    _initial_report_tickets = list(getattr(report, "repair_tickets", []) or [])
    _active_rp_ticket_by_id = {
        str(getattr(ticket, "ticket_id", "") or id(ticket)): ticket
        for ticket in [*_external_rp_tickets, *_initial_report_tickets]
    }
    _active_rp_tickets = list(_active_rp_ticket_by_id.values())

    focus = _reading_power_repair_focus(
        report=report,
        expected_hook=expected_hook,
        expected_payoffs=expected_payoffs,
        min_payoffs=min_payoffs,
        repair_threshold=_repair_trigger_threshold,
        chapter_number=chapter_number,
    )
    _initial_issues = _dedupe_reading_power_issues(
        [
            *_reading_power_issues_from_tickets(_external_rp_tickets),
            *_reading_power_repair_issues_from_report(
                report,
                chapter_number=chapter_number,
                current_text=current_text,
                min_payoffs=min_payoffs,
                repair_threshold=_repair_trigger_threshold,
            ),
        ]
    )
    if _external_rp_tickets:
        runner._on_step(
            "reading_power_repair_tickets_loaded",
            {
                "chapter": chapter_number,
                "external_ticket_count": len(_external_rp_tickets),
                "issue_count": len(_initial_issues),
            },
        )

    if not focus and not _initial_issues:
        text_hash = _record_final_reading_power(
            runner=runner,
            bundle=bundle,
            chapter_number=chapter_number,
            plan=plan,
            bridge=bridge,
            window_manager=window_manager,
            current_text_value=current_text,
            report_value=report,
        )
        runner._on_step(
            "reading_power_repair_skipped",
            {"chapter": chapter_number, "reason": "score_and_expectations_ok"},
        )
        return ReadingPowerRepairLoopResult(
            current_text=current_text,
            report=report,
            text_hash=text_hash,
        )

    _raw_max_rounds = (
        max_reading_power_rounds
        if max_reading_power_rounds is not None
        else getattr(runner._settings, "long_reading_power_max_repair_rounds", 2)
    )
    _max_rounds = max(0, int(2 if _raw_max_rounds is None else _raw_max_rounds))
    if _max_rounds == 0:
        text_hash = _record_final_reading_power(
            runner=runner,
            bundle=bundle,
            chapter_number=chapter_number,
            plan=plan,
            bridge=bridge,
            window_manager=window_manager,
            current_text_value=current_text,
            report_value=report,
        )
        runner._on_step(
            "reading_power_repair_skipped",
            {"chapter": chapter_number, "reason": "max_rounds_zero"},
        )
        return ReadingPowerRepairLoopResult(
            current_text=current_text,
            report=report,
            text_hash=text_hash,
        )

    _max_change_ratio = float(
        getattr(runner._settings, "long_reading_power_repair_max_change_ratio", 0.35) or 0.35
    )
    _hard_floor = max(
        float(getattr(runner._settings, "long_reading_power_hard_block_threshold", 3.0) or 0.0),
        float(getattr(runner._settings, "long_best_effort_accept_floor", 6.0) or 0.0),
    )

    config = RepairLoopConfig(
        max_rounds=_max_rounds,
        must_fix_severity="medium",
        score_threshold=_repair_trigger_threshold,
        change_budget=_max_change_ratio,
        stagnation_delta=0.3,
        hard_floor=_hard_floor,
        max_issues_per_round=resolve_max_issues_per_round(
            runner._settings,
            "long_reading_power_repair_max_issues_per_round",
        ),
    )

    pov_char = getattr(bundle.chapter_outline, "pov_character", "") or ""
    outline_chars = (
        getattr(bundle.chapter_outline, "involved_characters", None)
        or getattr(bundle.chapter_outline, "required_characters", None)
        or []
    )
    known_chars = list(dict.fromkeys([*outline_chars, pov_char] if pov_char else outline_chars))

    intentional_callbacks = [
        str(item).strip()
        for item in (getattr(plan, "intentional_callbacks", []) or [])
        if str(item).strip()
    ]
    intentional_set = set(intentional_callbacks)
    forbidden_elements = [
        str(item).strip()
        for item in (getattr(plan, "forbidden_elements", []) or [])
        if str(item).strip() and str(item).strip() not in intentional_set
    ]
    hard_set = set(forbidden_elements)
    forbidden_elements_soft = [
        str(item).strip()
        for item in (getattr(plan, "forbidden_elements_soft", []) or [])
        if str(item).strip()
        and str(item).strip() not in intentional_set
        and str(item).strip() not in hard_set
    ]

    previous_hook_description = ""
    if chapter_number > 1:
        try:
            _prev_rp_path = bundle.layout.reading_power_report_path(chapter_number - 1)
            if runner._storage.exists(_prev_rp_path):
                _prev_rp_data = runner._storage.load_json(_prev_rp_path)
                if _prev_rp_data and not _is_reading_power_fallback(_prev_rp_data):
                    previous_hook_description = str(
                        _prev_rp_data.get("hook_description", "") or ""
                    )
        except Exception:
            previous_hook_description = ""

    from novel_forge.pipeline.long.stages.chapter_repair_support import (
        _get_runner_episodic_memory,
    )

    _episodic_memory = _get_runner_episodic_memory(runner)
    _must_fix_for_index = [
        iss
        for iss in _initial_issues
        if severity_at_least((getattr(iss, "severity", "") or "medium").lower(), "medium")
    ]
    if _episodic_memory is not None and _must_fix_for_index:
        _indexed = await _index_reading_power_memory_issues(
            episodic_memory=_episodic_memory,
            chapter_number=chapter_number,
            issues=_must_fix_for_index,
        )
        if _indexed:
            runner._on_step(
                "reading_power_memory_issues_indexed",
                {"chapter": chapter_number, "issue_count": _indexed},
            )

    rp_runner = ReadingPowerRepairRunner(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        trace=trace,
        config=config,
        on_step=runner._on_step,
        chapter_number=chapter_number,
        window_manager=window_manager,
        window_config=window_config,
    )
    rp_runner._min_payoffs = min_payoffs
    rp_runner._expected_hook = expected_hook
    rp_runner._expected_payoffs = expected_payoffs
    rp_runner._rp_hint = rp_hint
    rp_runner._kernel_context = kernel_context
    rp_runner._previous_hook_description = previous_hook_description
    rp_runner._repair_trigger_threshold = _repair_trigger_threshold
    rp_runner._known_chars = known_chars
    rp_runner._forbidden_elements = forbidden_elements
    rp_runner._forbidden_elements_soft = forbidden_elements_soft
    rp_runner._intentional_callbacks = intentional_callbacks
    rp_runner._active_rp_tickets = _active_rp_tickets
    rp_runner._pre_built_issues = list(_initial_issues)

    result = await rp_runner.run(current_text=current_text, initial_report=report)
    final_report = result.report or report
    result.text_hash = _record_final_reading_power(
        runner=runner,
        bundle=bundle,
        chapter_number=chapter_number,
        plan=plan,
        bridge=bridge,
        window_manager=window_manager,
        current_text_value=result.current_text,
        report_value=final_report,
    )
    result.report = final_report
    return result

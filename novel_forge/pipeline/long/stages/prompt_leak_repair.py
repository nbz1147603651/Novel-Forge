"""Prompt-leak repair helpers for chapter finalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.domain.guardrails import (
    IN_WORLD_TEXT_VERDICT,
    PROMPT_LEAK_VERDICT,
    build_prompt_leak_patch_issues,
    classify_reported_prompt_leaks,
    repair_confirmed_prompt_leaks,
)
from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput


@dataclass(frozen=True)
class PromptLeakRepairResult:
    """Result of a localized prompt-leak repair attempt."""

    text: str
    chapter_repair_report: Any | None
    repaired_leaks: tuple[str, ...] = ()
    remaining_leaks: tuple[str, ...] = ()
    applied: bool = False
    used_deterministic_fallback: bool = False
    ignored_leaks: tuple[str, ...] = ()
    report_updated: bool = False
    failure_reason: str = ""


def _update_chapter_repair_report(
    chapter_repair_report: Any | None,
    *,
    repaired_leaks: tuple[str, ...],
    ignored_leaks: tuple[str, ...] = (),
    method: str,
) -> Any | None:
    if chapter_repair_report is None or not repaired_leaks and not ignored_leaks:
        return chapter_repair_report
    consumed = set(repaired_leaks) | set(ignored_leaks)
    remaining = [
        item
        for item in (getattr(chapter_repair_report, "prompt_leaks", []) or [])
        if str(item or "").strip() not in consumed
    ]
    if not hasattr(chapter_repair_report, "model_copy"):
        return chapter_repair_report
    details: list[str] = []
    if repaired_leaks:
        details.append(f"已{method}修复：{'；'.join(repaired_leaks[:3])}")
    if ignored_leaks:
        details.append(f"已判定为正文内合理标记并忽略：{'；'.join(ignored_leaks[:3])}")
    return chapter_repair_report.model_copy(
        update={
            "prompt_leaks": remaining,
            "summary": (
                f"{getattr(chapter_repair_report, 'summary', '')} "
                f"{'；'.join(details)}"
            ).strip(),
        }
    )


async def repair_confirmed_prompt_leaks_with_patch(
    *,
    router: Any,
    builder: Any,
    settings: Any,
    trace: Any,
    chapter_number: int,
    current_text: str,
    chapter_repair_report: Any | None,
    style_profile: dict[str, Any] | None = None,
    on_step: Any | None = None,
    allow_deterministic_fallback: bool = True,
) -> PromptLeakRepairResult:
    """Repair confirmed prompt leaks through PATCH_CHAPTER first.

    The repair report may contain broad or paraphrased descriptions, so this
    helper only acts on snippets still present in the chapter text and still
    matched by local leak detection. The primary repair path is model-generated
    surgical patches; deterministic cleanup is only used when patch repair is
    unavailable or fails to remove the confirmed snippets.
    """
    verdicts = classify_reported_prompt_leaks(
        current_text,
        getattr(chapter_repair_report, "prompt_leaks", []) if chapter_repair_report else (),
    )
    confirmed = tuple(verdicts[PROMPT_LEAK_VERDICT])
    ignored = tuple(verdicts[IN_WORLD_TEXT_VERDICT])
    report_after_ignored = _update_chapter_repair_report(
        chapter_repair_report,
        repaired_leaks=(),
        ignored_leaks=ignored,
        method="",
    )
    if not confirmed:
        return PromptLeakRepairResult(
            text=current_text,
            chapter_repair_report=report_after_ignored,
            ignored_leaks=ignored,
            report_updated=report_after_ignored is not chapter_repair_report,
        )

    issues = build_prompt_leak_patch_issues(current_text, confirmed)
    if not issues:
        return PromptLeakRepairResult(
            text=current_text,
            chapter_repair_report=report_after_ignored,
            remaining_leaks=confirmed,
            ignored_leaks=ignored,
            report_updated=report_after_ignored is not chapter_repair_report,
            failure_reason="没有可定位的泄露片段",
        )

    if callable(on_step):
        on_step(
            "prompt_leak_patch_repair_start",
            {
                "chapter": chapter_number,
                "leak_count": len(confirmed),
                "samples": list(confirmed[:5]),
            },
        )

    failure_reason = ""
    patch_text = current_text
    patch_applied = False
    try:
        patch_step = ChapterPatchStep(router, builder, settings=settings, trace=trace)
        patch_result = await patch_step.run(
            PatchInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                issues=issues,
                context_size=1,
                must_fix_summaries=[getattr(issue, "summary", "") for issue in issues],
                style_profile=style_profile,
            )
        )
        patch_text = patch_result.revised_text
        patch_applied = (
            patch_text != current_text
            and not patch_result.fallback
            and patch_result.patches_applied > 0
        )
        if not patch_applied:
            failure_reason = (
                f"PATCH_CHAPTER 未应用有效补丁"
                f"（attempted={patch_result.patches_attempted}, applied={patch_result.patches_applied}）"
            )
    except Exception as exc:
        failure_reason = f"PATCH_CHAPTER 调用失败：{exc}"

    if patch_applied:
        remaining_after_patch = tuple(
            classify_reported_prompt_leaks(patch_text, confirmed)[PROMPT_LEAK_VERDICT]
        )
        if not remaining_after_patch:
            repaired = tuple(leak for leak in confirmed if leak not in remaining_after_patch)
            updated_report = _update_chapter_repair_report(
                chapter_repair_report,
                repaired_leaks=repaired,
                ignored_leaks=ignored,
                method="补丁式",
            )
            if callable(on_step):
                on_step(
                    "prompt_leak_patch_repair_complete",
                    {
                        "chapter": chapter_number,
                        "repaired_count": len(repaired),
                        "samples": list(repaired[:5]),
                    },
                )
            return PromptLeakRepairResult(
                text=patch_text,
                chapter_repair_report=updated_report,
                repaired_leaks=repaired,
                ignored_leaks=ignored,
                applied=True,
                report_updated=updated_report is not chapter_repair_report,
            )
        failure_reason = "PATCH_CHAPTER 后仍检测到提示词/规划语句泄露"

    if not allow_deterministic_fallback:
        return PromptLeakRepairResult(
            text=current_text,
            chapter_repair_report=report_after_ignored,
            remaining_leaks=confirmed,
            ignored_leaks=ignored,
            report_updated=report_after_ignored is not chapter_repair_report,
            failure_reason=failure_reason,
        )

    fallback_text, fallback_repaired = repair_confirmed_prompt_leaks(current_text, confirmed)
    fallback_repaired_tuple = tuple(fallback_repaired)
    remaining_after_fallback = tuple(
        classify_reported_prompt_leaks(fallback_text, confirmed)[PROMPT_LEAK_VERDICT]
    )
    if fallback_repaired_tuple and fallback_text != current_text and not remaining_after_fallback:
        updated_report = _update_chapter_repair_report(
            chapter_repair_report,
            repaired_leaks=fallback_repaired_tuple,
            ignored_leaks=ignored,
            method="确定性兜底",
        )
        if callable(on_step):
            on_step(
                "prompt_leak_deterministic_fallback",
                {
                    "chapter": chapter_number,
                    "repaired_count": len(fallback_repaired_tuple),
                    "samples": list(fallback_repaired_tuple[:5]),
                    "patch_failure_reason": failure_reason,
                },
            )
        return PromptLeakRepairResult(
            text=fallback_text,
            chapter_repair_report=updated_report,
            repaired_leaks=fallback_repaired_tuple,
            ignored_leaks=ignored,
            applied=True,
            used_deterministic_fallback=True,
            report_updated=updated_report is not chapter_repair_report,
            failure_reason=failure_reason,
        )

    return PromptLeakRepairResult(
        text=current_text,
        chapter_repair_report=report_after_ignored,
        remaining_leaks=confirmed,
        ignored_leaks=ignored,
        report_updated=report_after_ignored is not chapter_repair_report,
        failure_reason=failure_reason or "补丁修复与兜底清理均未能消除泄露",
    )

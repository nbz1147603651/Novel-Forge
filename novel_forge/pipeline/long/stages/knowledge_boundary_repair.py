"""Knowledge boundary repair loop — repair-first before finalize blocks.

Modeled on ``_execute_continuity_repair_loop`` and ``_execute_reading_power_repair_loop``.
Uses window-mode rewriting: only the violating paragraphs ± 2 are sent to the
LLM for repair, then the full text is re-assembled and re-verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType, severity_at_least
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.repair_safety import (
    RepairDimension,
    RepairFailurePolicy,
    RepairRoundSnapshot,
)
from novel_forge.pipeline.steps.base import PipelineStep

_logger = get_logger(__name__)


@dataclass
class KnowledgeBoundaryRepairResult:
    """Outcome of the knowledge boundary repair loop."""

    current_text: str
    repair_exhausted: bool = False
    rounds_used: int = 0
    findings_before: list[ReviewFinding] = field(default_factory=list)
    findings_after: list[ReviewFinding] = field(default_factory=list)
    repair_attempted: bool = False


# ---------------------------------------------------------------------------
# Window extraction helpers
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on double-newlines, preserving order."""
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not parts:
        parts = [line.strip() for line in text.splitlines() if line.strip()]
    return parts


def _extract_window(
    paragraphs: list[str],
    para_start: int,
    para_end: int,
    context_radius: int = 2,
) -> tuple[str, int, int]:
    """Extract a window of paragraphs around the violation ± *context_radius*.

    Returns (window_text, window_start_idx, window_end_idx) where indices are
    1-based paragraph numbers for the LLM prompt.
    """
    n = len(paragraphs)
    # paragraph_start/end from ReviewFinding are 0-based indices
    start = max(0, para_start - context_radius)
    end = min(n, para_end + context_radius + 1)
    window_paras = paragraphs[start:end]
    window_text = "\n\n".join(window_paras)
    return window_text, start + 1, end  # 1-based for prompt


def _reassemble_text(
    original_paragraphs: list[str],
    window_start_idx: int,
    window_end_idx: int,
    repaired_window: str,
) -> str:
    """Replace the window region in the original paragraph list with repaired text.

    *window_start_idx* and *window_end_idx* are 1-based (matching the prompt).
    """
    repaired_paras = [p.strip() for p in repaired_window.split("\n\n") if p.strip()]
    start_0 = window_start_idx - 1
    end_0 = window_end_idx  # exclusive in 0-based
    new_paras = (
        original_paragraphs[:start_0]
        + repaired_paras
        + original_paragraphs[end_0:]
    )
    return "\n\n".join(new_paras)


# ---------------------------------------------------------------------------
# Blocking-filter helper
# ---------------------------------------------------------------------------


def _blocking_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Return findings that should trigger repair (blocks_finalize or high+)."""
    return [
        f
        for f in findings
        if f.blocks_finalize or severity_at_least(f.severity, "high")
    ]


# ---------------------------------------------------------------------------
# Main repair loop
# ---------------------------------------------------------------------------


async def run_knowledge_boundary_repair_loop(
    *,
    runner: Any,
    storage: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    findings: list[ReviewFinding],
    trace: Any | None = None,
    max_rounds: int = 2,
) -> KnowledgeBoundaryRepairResult:
    """Run a repair loop for knowledge boundary violations.

    Uses window-mode: only the violating paragraphs ± 2 are rewritten by the
    LLM.  After each round the full text is re-verified via
    ``run_knowledge_boundary_audit``.  If all blocking findings are resolved
    the loop exits early; otherwise it continues up to *max_rounds*.

    Returns a :class:`KnowledgeBoundaryRepairResult`.
    """
    from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
        cognitive_constraints_from_contract,
    )
    from novel_forge.pipeline.long.services.knowledge_boundary_audit import (
        run_knowledge_boundary_audit,
    )

    blocking = _blocking_findings(findings)
    result = KnowledgeBoundaryRepairResult(
        current_text=current_text,
        findings_before=list(findings),
    )

    if not blocking:
        result.findings_after = list(findings)
        return result

    result.repair_attempted = True
    failure_policy = RepairFailurePolicy(runner._on_step, logger=_logger)
    paragraphs = _split_paragraphs(current_text)
    cognitive_constraints = cognitive_constraints_from_contract(
        getattr(packet, "chapter_contract", {})
    )

    for round_idx in range(max_rounds):
        round_num = round_idx + 1
        pre_repair_text = current_text

        # Pick the first unresolved blocking finding to repair in this round.
        # Subsequent rounds re-audit, so the finding list may shrink.
        target = blocking[0] if blocking else None
        if target is None:
            break

        runner._on_step(
            "knowledge_boundary_repair_attempt",
            {
                "chapter": chapter_number,
                "round": round_num,
                "max_rounds": max_rounds,
                "blocking_count": len(blocking),
                "target_finding_id": target.finding_id,
                "severity": target.severity,
            },
        )

        # ── Build window ──
        para_start = max(0, target.paragraph_start)
        para_end = max(para_start, target.paragraph_end)
        window_text, win_start, win_end = _extract_window(
            paragraphs, para_start, para_end, context_radius=2
        )

        pov_character = str(
            getattr(getattr(bundle, "chapter_outline", None), "pov_character", "") or ""
        )

        # ── Call LLM repair step ──
        try:
            repaired_window = await _call_repair_llm(
                runner=runner,
                chapter_number=chapter_number,
                pov_character=pov_character,
                violations=[
                    {
                        "evidence_quote": target.evidence_quote,
                        "reason": target.summary,
                        "severity": target.severity,
                        "paragraph_start": target.paragraph_start,
                        "paragraph_end": target.paragraph_end,
                        "repair_goal": target.repair_goal,
                    }
                ],
                window_text=window_text,
                cognitive_constraints=cognitive_constraints,
                trace=trace,
            )
        except Exception as exc:
            outcome = failure_policy.repair_failed(
                snapshot=RepairRoundSnapshot(
                    stage=RepairDimension.KNOWLEDGE_BOUNDARY,
                    chapter_number=chapter_number,
                    round_number=round_num,
                    text=current_text,
                ),
                exc=exc,
            )
            current_text = outcome.current_text
            result.repair_exhausted = outcome.repair_exhausted
            runner._on_step(
                "knowledge_boundary_repair_failed",
                {
                    "chapter": chapter_number,
                    "round": round_num,
                    "error": outcome.failure_reason[:500],
                },
            )
            break

        if not repaired_window or not repaired_window.strip():
            runner._on_step(
                "knowledge_boundary_repair_empty",
                {"chapter": chapter_number, "round": round_num},
            )
            continue

        # ── Reassemble full text ──
        current_text = _reassemble_text(paragraphs, win_start, win_end, repaired_window)
        paragraphs = _split_paragraphs(current_text)

        # ── Re-verify ──
        try:
            post_findings = await run_knowledge_boundary_audit(
                runner=runner,
                storage=storage,
                bundle=bundle,
                packet=packet,
                current_text=current_text,
                chapter_number=chapter_number,
                stage=f"repair_round_{round_num}",
                block_high_confidence=True,
            )
        except Exception as exc:
            outcome = failure_policy.recheck_failed(
                snapshot=RepairRoundSnapshot(
                    stage=RepairDimension.KNOWLEDGE_BOUNDARY,
                    chapter_number=chapter_number,
                    round_number=round_num,
                    text=pre_repair_text,
                ),
                exc=exc,
                warning=f"知识边界修复后复查失败，回滚本轮：{type(exc).__name__}: {exc}",
            )
            current_text = outcome.current_text
            paragraphs = _split_paragraphs(current_text)
            result.repair_exhausted = outcome.repair_exhausted
            break

        post_blocking = _blocking_findings(post_findings)
        runner._on_step(
            "knowledge_boundary_repair_recheck",
            {
                "chapter": chapter_number,
                "round": round_num,
                "blocking_before": len(blocking),
                "blocking_after": len(post_blocking),
                "total_findings_after": len(post_findings),
            },
        )

        if not post_blocking:
            # All blocking findings resolved
            result.current_text = current_text
            result.rounds_used = round_num
            result.findings_after = list(post_findings)
            return result

        # Update for next round
        blocking = post_blocking
        result.current_text = current_text
        result.rounds_used = round_num

    # ── Loop exhausted ──
    result.repair_exhausted = True
    result.current_text = current_text
    # Final re-audit to populate findings_after
    try:
        final_findings = await run_knowledge_boundary_audit(
            runner=runner,
            storage=storage,
            bundle=bundle,
            packet=packet,
            current_text=current_text,
            chapter_number=chapter_number,
            stage="repair_exhausted",
            block_high_confidence=True,
        )
        result.findings_after = list(final_findings)
    except Exception:
        result.findings_after = list(blocking)

    return result


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------


async def _call_repair_llm(
    *,
    runner: Any,
    chapter_number: int,
    pov_character: str,
    violations: list[dict[str, Any]],
    window_text: str,
    cognitive_constraints: list[dict[str, Any]] | None = None,
    trace: Any | None = None,
) -> str:
    """Invoke the REPAIR_KNOWLEDGE_BOUNDARY task and return repaired window text."""
    # Build a minimal step to call the LLM via the standard retry path.
    step = _KnowledgeBoundaryRepairStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
    )
    data = await step.run(
        _KnowledgeBoundaryRepairInput(
            chapter_number=chapter_number,
            pov_character=pov_character,
            violations=violations,
            window_text=window_text,
            cognitive_constraints=list(cognitive_constraints or []),
        )
    )
    return data.repaired_text


# ---------------------------------------------------------------------------
# Internal step + input/output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _KnowledgeBoundaryRepairInput:
    """Input payload for the knowledge boundary repair LLM call."""

    chapter_number: int
    pov_character: str
    violations: list[dict[str, Any]]
    window_text: str
    cognitive_constraints: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _KnowledgeBoundaryRepairOutput:
    """Parsed output from the knowledge boundary repair LLM call."""

    repaired_text: str
    changes: list[dict[str, Any]]


class _KnowledgeBoundaryRepairStep(
    PipelineStep[_KnowledgeBoundaryRepairInput, _KnowledgeBoundaryRepairOutput],
):
    """Thin PipelineStep wrapper for the REPAIR_KNOWLEDGE_BOUNDARY task."""

    @property
    def step_name(self) -> str:
        return "repair_knowledge_boundary"

    async def _execute(
        self,
        input_data: _KnowledgeBoundaryRepairInput,
    ) -> _KnowledgeBoundaryRepairOutput:
        context: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "pov_character": input_data.pov_character,
            "violations": input_data.violations,
            "window_text": input_data.window_text,
            "cognitive_constraints": input_data.cognitive_constraints,
            "repair_goal": (
                input_data.violations[0].get("repair_goal", "")
                if input_data.violations
                else ""
            ),
        }
        data = await self._call_with_retry(
            TaskType.REPAIR_KNOWLEDGE_BOUNDARY,
            context,
            max_tokens=self._dynamic_max_tokens(
                TaskType.REPAIR_KNOWLEDGE_BOUNDARY,
                max(1200, len(input_data.window_text)),
                prompt_overhead=2400,
                min_tokens=2048,
                max_cap=8192,
            ),
            temperature=float(
                getattr(self.settings, "temp_repair_knowledge_boundary", 0.2) or 0.2
            ),
            required_keys=("repaired_text", "changes"),
            max_retries=2,
        )
        repaired_text = str(data.get("repaired_text", "") or "").strip()
        changes_raw = data.get("changes", [])
        changes = [dict(c) for c in (changes_raw or []) if isinstance(c, dict)]
        return _KnowledgeBoundaryRepairOutput(
            repaired_text=repaired_text,
            changes=changes,
        )

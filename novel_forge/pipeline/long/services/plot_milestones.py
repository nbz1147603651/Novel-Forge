"""Plot milestone index, runtime windows, and progression-ledger utilities."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from novel_forge.narrative_state.schemas import (
    MilestoneWindow,
    PlotMilestone,
    PlotMilestoneIndex,
    ProgressionLedger,
    ProgressionLedgerEntry,
    stable_id,
)


def build_plot_milestone_index(
    *,
    outline: Any,
    chapter_contracts: dict[str, Any] | None = None,
    narrative_contract: dict[str, Any] | None = None,
    project_id: str = "",
) -> PlotMilestoneIndex:
    """Compile a compact, runtime-safe milestone index from init artifacts."""

    contracts = {
        int(item.get("chapter_number", 0) or 0): item
        for item in list((chapter_contracts or {}).get("chapter_contracts", []) or [])
        if isinstance(item, dict) and int(item.get("chapter_number", 0) or 0) > 0
    }
    milestones: list[PlotMilestone] = []
    for chapter in list(getattr(outline, "chapters", []) or []):
        chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
        if chapter_number <= 0:
            continue
        title = _clean(getattr(chapter, "title", ""))
        contract = contracts.get(chapter_number, {})
        summary_items = [
            _clean(getattr(chapter, "goal", "")),
            *_clean_list(getattr(chapter, "main_plot_points", []))[:2],
            *_clean_list(contract.get("required_progressions", []))[:2],
            *_clean_list(contract.get("required_events", []))[:1],
        ]
        summary = "；".join(_dedupe([item for item in summary_items if item]))[:280]
        if not summary:
            continue
        milestones.append(
            PlotMilestone(
                milestone_id=stable_id("pm", project_id, chapter_number, title, summary),
                chapter_number=chapter_number,
                title=title,
                summary=summary,
                kind=_infer_progression_kind(summary),
                status="planned",
                depends_on=[],
                allow_before_chapter=max(1, chapter_number - 1),
                block_before_chapter=chapter_number,
                payoff_only=bool(_clean_list(contract.get("future_leak_risks", []))),
                source="chapter_contract" if contract else "outline",
            )
        )

    for idx, milestone in enumerate(milestones):
        if idx == 0:
            continue
        previous = milestones[idx - 1]
        milestone.depends_on.append(previous.milestone_id)

    source_hash = hashlib.sha256(
        json.dumps(
            {
                "outline": _safe_model_dump(outline),
                "chapter_contracts": chapter_contracts or {},
                "narrative_contract": narrative_contract or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return PlotMilestoneIndex(
        project_id=project_id,
        total_chapters=int(getattr(outline, "total_chapters", 0) or len(milestones)),
        milestones=milestones,
        source_hash=source_hash,
        notes="Runtime stages must retrieve bounded MilestoneWindow slices instead of full outline.",
    )


def select_milestone_window(
    index_payload: Any,
    *,
    chapter_number: int,
    previous: int = 2,
    future: int = 2,
) -> MilestoneWindow:
    """Return a bounded milestone slice for one chapter."""

    index = (
        index_payload
        if isinstance(index_payload, PlotMilestoneIndex)
        else PlotMilestoneIndex.model_validate(index_payload or {})
    )
    current: list[PlotMilestone] = []
    previous_context: list[PlotMilestone] = []
    future_guardrails: list[PlotMilestone] = []
    withheld = 0
    for milestone in index.milestones:
        offset = milestone.chapter_number - chapter_number
        if offset == 0:
            current.append(milestone)
        elif -max(0, previous) <= offset < 0:
            previous_context.append(milestone)
        elif 0 < offset <= max(0, future):
            future_guardrails.append(_redact_future_milestone(milestone))
        elif offset > max(0, future):
            withheld += 1
    return MilestoneWindow(
        chapter_number=chapter_number,
        current=current,
        previous_context=previous_context,
        future_guardrails=future_guardrails,
        withheld_future_count=withheld,
        policy="current detailed + previous context + redacted future guardrails",
    )


def load_progression_ledger(payload: Any) -> ProgressionLedger:
    """Validate a progression ledger payload."""

    if isinstance(payload, ProgressionLedger):
        return payload
    if not isinstance(payload, dict):
        return ProgressionLedger()
    return ProgressionLedger.model_validate(payload)


def append_progression_entries(
    ledger_payload: Any,
    *,
    chapter_number: int,
    progressions: list[str],
    source: str = "contract_execution_audit",
) -> ProgressionLedger:
    """Append deduped progression entries for a chapter."""

    ledger = load_progression_ledger(ledger_payload)
    existing = {
        (entry.chapter_number, entry.progression)
        for entry in ledger.entries
        if entry.progression
    }
    entries = list(ledger.entries)
    for text in _dedupe(progressions):
        key = (chapter_number, text)
        if key in existing:
            continue
        entries.append(
            ProgressionLedgerEntry(
                entry_id=stable_id("pg", chapter_number, text),
                chapter_number=chapter_number,
                progression=text,
                kind=_infer_progression_kind(text),
                status="partial",
                evidence_quotes=[],
                source=source,
            )
        )
    return ProgressionLedger(entries=entries, last_chapter=max(ledger.last_chapter, chapter_number))


def stage_visibility_summary(
    *,
    stage: str,
    milestone_window: dict[str, Any] | None,
    draft_withholds_future: bool = True,
) -> dict[str, Any]:
    """Summarize context visibility for diagnostics/UI."""

    stage_name = str(stage or "").strip().lower()
    window = milestone_window or {}
    current = list(window.get("current", []) or [])
    future = list(window.get("future_guardrails", []) or [])
    withheld = int(window.get("withheld_future_count", 0) or 0)
    if stage_name == "draft" and draft_withholds_future:
        visible_future = 0
        withheld += len(future)
    else:
        visible_future = len(future)
    return {
        "stage": stage_name,
        "visible_current_milestones": len(current),
        "visible_future_guardrails": visible_future,
        "withheld_future_count": withheld,
        "policy": (
            "Draft only receives current execution cards; future guardrails are withheld."
            if stage_name == "draft"
            else "Stage receives bounded milestone window, not full outline."
        ),
    }


def _redact_future_milestone(milestone: PlotMilestone) -> PlotMilestone:
    """Keep future guardrails functional while removing concrete payoff detail."""

    summary = milestone.summary
    if len(summary) > 80:
        summary = summary[:80].rstrip() + "..."
    return milestone.model_copy(
        update={
            "summary": f"未来节点功能边界：{summary}",
            "status": "foreshadow",
            "payoff_only": True,
        }
    )


def _infer_progression_kind(text: str) -> str:
    value = _clean(text)
    if any(token in value for token in ("知道", "发现", "揭示", "真相", "记忆", "证据")):
        return "knowledge"
    if any(token in value for token in ("关系", "信任", "误会", "告白", "并肩")):
        return "relationship"
    if any(token in value for token in ("信物", "怀表", "戒指", "钥匙", "道具", "账簿")):
        return "item"
    if any(token in value for token in ("承诺", "约定", "誓言")):
        return "promise"
    if any(token in value for token in ("悬念", "疑问", "未决", "线索")):
        return "suspense"
    return "event"


def _safe_model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple | set):
        raw = list(value)
    elif isinstance(value, str):
        raw = [value]
    else:
        raw = []
    return [_clean(item) for item in raw if _clean(item)]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _clean(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


__all__ = [
    "append_progression_entries",
    "build_plot_milestone_index",
    "load_progression_ledger",
    "select_milestone_window",
    "stage_visibility_summary",
]

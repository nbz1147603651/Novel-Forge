"""Lightweight subplot/character arc liveness audit."""

from __future__ import annotations

from typing import Any

from novel_forge.narrative_state.schemas import ArcLivenessReport, DormantArc, stable_id


def build_arc_liveness_report(
    *,
    chapter_number: int,
    narrative_contract: dict[str, Any] | None,
    progression_ledger: dict[str, Any] | None,
    window: int = 6,
) -> ArcLivenessReport:
    """Find arcs that have not been touched recently enough for planning hints."""

    contract = narrative_contract or {}
    ledger_entries = list((progression_ledger or {}).get("entries", []) or [])
    dormant: list[DormantArc] = []
    for raw in _contract_arc_items(contract):
        if not isinstance(raw, dict):
            continue
        name = _arc_name(raw)
        if not name:
            continue
        last = _last_progress_chapter(name, ledger_entries)
        expected_next = max(1, (last or 1) + max(1, window))
        if chapter_number < expected_next:
            continue
        risk = "high" if chapter_number - (last or 0) >= window * 2 else "medium"
        dormant.append(
            DormantArc(
                arc_id=stable_id("arc", name),
                name=name,
                arc_type=str(raw.get("arc_type") or raw.get("type") or "subplot"),
                last_progress_chapter=max(0, last),
                expected_next_touch=expected_next,
                risk_level=risk,
                planning_hint=(
                    f"轻触「{name}」：用对话、道具、消息或旁线后果推进一小步，"
                    "不要强插完整支线戏。"
                ),
            )
        )

    top_risk = "low"
    if any(item.risk_level == "high" for item in dormant):
        top_risk = "high"
    elif dormant:
        top_risk = "medium"
    return ArcLivenessReport(
        chapter_number=chapter_number,
        dormant_arcs=dormant[:8],
        last_progress_chapter=max((item.last_progress_chapter for item in dormant), default=0),
        expected_next_touch=min((item.expected_next_touch for item in dormant), default=0),
        risk_level=top_risk,
        planning_hint="；".join(item.planning_hint for item in dormant[:3]),
    )


def _arc_name(raw: dict[str, Any]) -> str:
    for key in (
        "name",
        "character",
        "char_name",
        "thread",
        "thread_name",
        "thread_id",
        "promise",
        "title",
        "arc_summary",
    ):
        value = str(raw.get(key) or "").strip()
        if value:
            return value[:80]
    return ""


def _contract_arc_items(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return arc-like records from deterministic and nested LLM contracts."""

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    sources = [contract]
    nested = contract.get("llm_contract")
    if isinstance(nested, dict):
        sources.append(nested)
    for source in sources:
        for field in ("character_arcs", "plot_threads", "promise_plan"):
            for raw in list(source.get(field, []) or []):
                if not isinstance(raw, dict):
                    continue
                name = _arc_name(raw)
                key = f"{field}:{name}".lower() if name else repr(sorted(raw.items()))[:160]
                if key in seen:
                    continue
                seen.add(key)
                items.append(raw)
    return items


def _last_progress_chapter(name: str, ledger_entries: list[Any]) -> int:
    last = 0
    needle = name.lower()
    for item in ledger_entries:
        if not isinstance(item, dict):
            continue
        text = " ".join(
            str(item.get(key) or "") for key in ("progression", "milestone_id", "source")
        ).lower()
        if needle and needle in text:
            try:
                last = max(last, int(item.get("chapter_number", 0) or 0))
            except (TypeError, ValueError):
                continue
    return last


__all__ = ["build_arc_liveness_report"]

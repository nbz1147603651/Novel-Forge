"""Scene-level chapter planning, validation, and stitching helpers."""

from __future__ import annotations

import copy
import re
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan, SceneIntent
from novel_forge.core.utils.field_extractor import field
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h
from novel_forge.pipeline.long.services.plan_obligations import assess_plan_literal_coverage
from novel_forge.pipeline.token_budget import route_max_output_budget


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _scene_id(scene: Any) -> str:
    return _text(field(scene, "scene_id", ""))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[；;\n]+", value)
    elif isinstance(value, list | tuple | set):
        items = list(value)
    else:
        items = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _scene_order(scene: Any, fallback: int) -> int:
    try:
        order = int(field(scene, "draft_order", 0) or 0)
    except (TypeError, ValueError):
        order = 0
    return order if order > 0 else fallback


# Chinese dialogue delimiters: 「」, “”, ""
_DIALOGUE_RE = re.compile(
    r"[「“\"]([^」”\"]*)[」”\"]"
)


def _compute_dialogue_ratio(text: str) -> float:
    """Compute the percentage of characters inside dialogue delimiters."""
    if not text:
        return 0.0
    total_chars = len(re.sub(r"\s+", "", text))
    if total_chars == 0:
        return 0.0
    dialogue_chars = sum(len(m.group(1)) for m in _DIALOGUE_RE.finditer(text))
    return (dialogue_chars / total_chars) * 100.0


def _style_scene_intent(scene: Any) -> dict[str, Any]:
    return {
        "emotional_tone": field(scene, "emotional_beat", "") or field(scene, "emotional_tone", ""),
        "scene_action": field(scene, "purpose", "") or field(scene, "scene_action", ""),
        "setting": field(scene, "location", "") or field(scene, "setting", ""),
        "pov_keywords": field(scene, "pov_character", "") or field(scene, "pov_keywords", ""),
        "topic": field(scene, "summary", "") or field(scene, "topic", ""),
        "characters_involved": _string_list(field(scene, "required_characters", [])),
    }


def scene_plan_report_path(layout: Any, chapter_number: int) -> Any:
    return layout.reports_dir / f"chapter_{chapter_number:03d}_scene_plan_validation.json"


def plan_structure_audit_path(layout: Any, chapter_number: int) -> Any:
    """Path for the non-blocking whole-chapter plan structure audit."""

    return layout.reports_dir / f"chapter_{chapter_number:03d}_plan_structure_audit.json"


def scene_stitch_report_path(layout: Any, chapter_number: int) -> Any:
    return layout.reports_dir / f"chapter_{chapter_number:03d}_scene_stitch_report.json"


def scene_plan_path(layout: Any, chapter_number: int) -> Any:
    return layout.plans_dir / f"chapter_{chapter_number:03d}_scene_plan.json"


def scene_draft_path(layout: Any, chapter_number: int, scene_id: str) -> Any:
    safe_id = re.sub(r"[^0-9A-Za-z_\-]+", "_", scene_id or "scene")
    return layout.chapter_draft_dir(chapter_number) / "scenes" / f"{safe_id}.md"


def scene_stitched_draft_path(layout: Any, chapter_number: int) -> Any:
    return layout.drafts_dir / f"chapter_{chapter_number:03d}_scene_stitched.md"


def scene_plan_summary(plan: ChapterPlan) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, scene in enumerate(plan.scene_intents, start=1):
        items.append(
            {
                "scene_id": scene.scene_id,
                "summary": scene.summary,
                "scene_goal": scene.scene_goal or scene.purpose,
                "owned_events": list(scene.owned_events or []),
                "owned_revelations": list(scene.owned_revelations or []),
                "owned_state_changes": list(scene.owned_state_changes or []),
                "dependency_scene_ids": list(scene.dependency_scene_ids or []),
                "draft_order": scene.draft_order or index,
                "parallel_group": scene.parallel_group,
            }
        )
    return items


def build_scene_draft_context(
    *,
    base_context: dict[str, Any],
    plan: ChapterPlan,
    scene: SceneIntent,
    target_word_count: int,
    completed_scene_handoffs: list[dict[str, str]] | None = None,
    retry_feedback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the scoped prompt context for one scene draft.

    The caller already computed the full DRAFT stage cards.  This function keeps
    the chapter-level P0 cards, but trims scene-irrelevant character / voice /
    knowledge material and removes full-plan scene bodies so DRAFT_SCENE sees a
    focused local contract.
    """

    context = dict(base_context)
    cards = copy.deepcopy(context.get("stage_cards") or {})
    context["stage_cards"] = _scope_scene_stage_cards(cards, plan=plan, scene=scene)
    context["target_word_count"] = int(target_word_count or 0)
    context["current_scene"] = _model_dump(scene)
    context["scene_intent"] = _style_scene_intent(scene)
    context["completed_scene_handoffs"] = list(completed_scene_handoffs or [])
    context["adjacent_scene_handoffs"] = _adjacent_scene_handoffs(plan, scene)
    # A retry must not repeat the same prompt verbatim: the local validator's
    # concrete findings are safe, bounded input for the next DRAFT_SCENE call.
    # They are prompt-only diagnostics, never prose requirements themselves.
    context["scene_retry_feedback"] = _normalize_retry_feedback(retry_feedback)
    return context


def assess_draft_plan_coverage(
    *,
    plan: Any,
    text: str,
    target_word_count: int,
) -> dict[str, Any]:
    """Return a cheap, non-blocking coverage diagnostic for any raw chapter draft.

    Both writing modes share the same approved ``ChapterPlan`` but previously
    only scene-level runs carried local diagnostics into WAVE.  This report
    gives whole-chapter runs the same evidence-based handoff without trying to
    infer scene boundaries from prose.  A token match is deliberately treated
    as *weak coverage*, not a hard proof of fulfilment; Review remains the
    authoritative semantic gate.
    """
    ordered = sorted(
        list(field(plan, "scene_intents", []) or []),
        key=lambda item: (_scene_order(item, 10_000), _scene_id(item)),
    )
    prose = _clean_scene_text(text)
    word_count = count_chapter_words(prose)
    weak_anchors: list[dict[str, str]] = []
    checked_by_scene: dict[str, int] = {}
    weak_by_scene: dict[str, int] = {}

    def record(scene_id: str, kind: str, anchor: str, message: str) -> None:
        if not anchor:
            return
        checked_by_scene[scene_id] = checked_by_scene.get(scene_id, 0) + 1
        if _shares_token(anchor, prose):
            return
        weak_by_scene[scene_id] = weak_by_scene.get(scene_id, 0) + 1
        weak_anchors.append(
            {
                "scene_id": scene_id,
                "kind": kind,
                "anchor": anchor,
                "message": message,
            }
        )

    for scene in ordered:
        scene_id = _scene_id(scene)
        for field_name, label in (
            ("owned_events", "独占事件"),
            ("owned_revelations", "独占揭示"),
            ("owned_state_changes", "独占状态变化"),
        ):
            # Outline-beat recovery may append a required anchor after the
            # model-provided events. Keep the diagnostic window wide enough
            # to cover those additions without turning this into an unbounded
            # lexical scan.
            for anchor in _string_list(field(scene, field_name, []))[:8]:
                record(
                    scene_id,
                    field_name,
                    anchor,
                    f"{scene_id} 的{label}在草稿中覆盖较弱：{anchor}",
                )

    if ordered:
        first = ordered[0]
        opening_bridge = field(plan, "opening_bridge", {}) or {}
        bridge_anchor = _text(
            field(opening_bridge, "action_handoff", "")
            or field(opening_bridge, "bridge_summary", "")
        )
        if bridge_anchor:
            record(
                _scene_id(first),
                "opening_bridge",
                bridge_anchor,
                f"首场对桥接动作的承接在草稿中较弱：{bridge_anchor}",
            )

        last = ordered[-1]
        exit_anchor = _text(field(last, "exit_state", "") or field(last, "exit_target_state", ""))
        if exit_anchor:
            record(
                _scene_id(last),
                "closing_exit",
                exit_anchor,
                f"末场出口状态在草稿中覆盖较弱：{exit_anchor}",
            )

    word_count_status = "not_checked"
    if target_word_count > 0:
        ratio = word_count / target_word_count
        word_count_status = "pass" if 0.70 <= ratio <= 1.30 else "warning"

    literal_coverage = assess_plan_literal_coverage(plan, prose)
    return {
        "source": "local",
        "scene_count": len(ordered),
        "word_count": word_count,
        "target_word_count": max(0, int(target_word_count or 0)),
        "word_count_status": word_count_status,
        "weak_anchor_count": len(weak_anchors),
        # Keep the prompt payload bounded without starving later scenes when
        # an early scene emits many weak anchors.  Round-robin selection gives
        # every planned scene a chance to reach WAVE.
        "weak_anchors": _select_weak_anchors(weak_anchors, limit=24),
        **literal_coverage,
        "scene_status": [
            {
                "scene_id": _scene_id(scene),
                "checked_anchor_count": checked_by_scene.get(_scene_id(scene), 0),
                "weak_anchor_count": weak_by_scene.get(_scene_id(scene), 0),
            }
            for scene in ordered
        ],
    }


def _select_weak_anchors(
    weak_anchors: list[dict[str, str]],
    *,
    limit: int,
) -> list[dict[str, str]]:
    """Select bounded diagnostics fairly across scene ids."""

    if len(weak_anchors) <= limit:
        return list(weak_anchors)
    queues: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for item in weak_anchors:
        scene_id = item.get("scene_id", "")
        if scene_id not in queues:
            queues[scene_id] = []
            order.append(scene_id)
        queues[scene_id].append(item)
    selected: list[dict[str, str]] = []
    while len(selected) < limit and any(queues.values()):
        for scene_id in order:
            if queues[scene_id] and len(selected) < limit:
                selected.append(queues[scene_id].pop(0))
    return selected


def validate_scene_draft_locally(
    scene: SceneIntent,
    text: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run cheap deterministic checks on a single scene draft.

    Hard failures are objective generation failures that justify one local
    DRAFT_SCENE retry.  Semantic misses stay as soft warnings and are passed to
    WAVE / Review instead of creating a per-scene LLM audit loop.
    """

    context = context or {}
    sid = _scene_id(scene)
    raw_text = str(text or "")
    cleaned = _clean_scene_text(raw_text)
    target = _safe_int(context.get("target_word_count") or field(scene, "target_words", 0))
    word_count = count_chapter_words(cleaned)
    hard_failures: list[dict[str, Any]] = []
    soft_warnings: list[dict[str, Any]] = []

    def hard(code: str, message: str, **extra: Any) -> None:
        hard_failures.append(
            _drop_empty(
                {
                    "code": code,
                    "severity": "high",
                    "scene_ids": [sid] if sid else [],
                    "message": message,
                    **extra,
                }
            )
        )

    def soft(code: str, message: str, **extra: Any) -> None:
        soft_warnings.append(
            _drop_empty(
                {
                    "code": code,
                    "severity": "medium",
                    "scene_ids": [sid] if sid else [],
                    "message": message,
                    **extra,
                }
            )
        )

    if not raw_text.strip():
        hard("empty_scene_text", "场景草稿为空。")
    if not cleaned:
        hard("no_body_after_clean", "移除标题/场景编号后没有可拼接正文。")

    title_lines = [
        line.strip()
        for line in raw_text.splitlines()[:4]
        if re.fullmatch(r"#{1,6}\s*scene[_\-\s]?\d+.*", line.strip(), flags=re.IGNORECASE)
        or re.fullmatch(r"(场景|scene)[_\-\s]?\d+[:：]?.*", line.strip(), flags=re.IGNORECASE)
    ]
    if title_lines:
        hard("scene_title_leak", "场景草稿包含标题或场景编号行。", samples=title_lines[:2])

    if sid and re.search(rf"\b{re.escape(sid)}\b", raw_text):
        hard("scene_id_leak", f"正文中出现 scene_id：{sid}。")

    leaked_markers = [
        marker
        for marker in (
            "scene_id",
            "owned_events",
            "owned_revelations",
            "owned_state_changes",
            "required_outcome",
            "handoff_to_next",
            "forbidden_overlap",
            "current_scene",
            "P0/P1/P2",
            "交付前核验",
            "当前场景契约",
        )
        if marker in raw_text
    ]
    if leaked_markers:
        hard(
            "planning_token_leak",
            "场景草稿泄露规划/提示词术语。",
            markers=leaked_markers[:5],
        )

    if target > 0 and word_count < max(120, int(target * 0.20)):
        hard(
            "severe_word_shortfall",
            f"场景正文过短：{word_count}，目标 {target}。",
            word_count=word_count,
            target_word_count=target,
        )

    if cleaned:
        if target > 0 and (word_count < int(target * 0.70) or word_count > int(target * 1.30)):
            soft(
                "word_count_drift",
                f"场景字数偏离目标：{word_count}，目标 {target}。",
                word_count=word_count,
                target_word_count=target,
            )

        # Dialogue ratio check (P1)
        dialogue_ratio_pct = _compute_dialogue_ratio(cleaned)
        style_profile = context.get("style_profile") or {}
        global_style = (
            style_profile.get("global_style", {})
            if isinstance(style_profile, dict)
            else {}
        ) or {}
        ratio_level = str(global_style.get("dialogue_ratio", "medium") or "medium").strip()
        dlg_min = {"high": 50, "low": 20}.get(ratio_level, 30)
        if dialogue_ratio_pct < dlg_min and word_count > 300:
            soft(
                "dialogue_ratio_low",
                f"对话占比 {dialogue_ratio_pct:.1f}% 低于目标下限 {dlg_min}%。",
                dialogue_ratio_pct=round(dialogue_ratio_pct, 1),
                target_min=dlg_min,
            )

        for field_name in ("owned_events", "owned_revelations", "owned_state_changes"):
            for item in _string_list(field(scene, field_name, []))[:8]:
                if item and not _shares_token(item, cleaned):
                    soft(
                        f"{field_name}_anchor_weak",
                        f"{field_name} 锚点在正文中较弱：{item}",
                        anchor=item,
                    )

        handoff = _text(field(scene, "handoff_to_next", ""))
        if handoff and not _shares_token(handoff, cleaned):
            soft(
                "handoff_anchor_weak",
                f"场景结尾交接锚点在正文中较弱：{handoff}",
                anchor=handoff,
            )

        for item in _string_list(field(scene, "forbidden_overlap", []))[:6]:
            if item and _shares_token(item, cleaned):
                soft(
                    "forbidden_overlap_possible_hit",
                    f"正文疑似触碰禁止重叠项：{item}",
                    anchor=item,
                )

        for handoff_item in list(context.get("completed_scene_handoffs", []) or [])[:3]:
            if not isinstance(handoff_item, dict):
                continue
            anchor = _text(handoff_item.get("handoff_to_next"))
            if anchor and not _shares_token(anchor, cleaned):
                soft(
                    "dependency_handoff_weak",
                    f"依赖场景交接承接较弱：{anchor}",
                    source_scene_id=_text(handoff_item.get("scene_id")),
                    anchor=anchor,
                )

    return {
        "scene_id": sid,
        "valid": not hard_failures,
        "hard_failures": hard_failures,
        "soft_warnings": soft_warnings,
        "text_chars": len(cleaned),
        "word_count": word_count,
        "target_word_count": target,
    }


def validate_scene_plan_locally(
    *,
    plan: ChapterPlan,
    bridge: ChapterBridge,
    target_word_count: int,
) -> dict[str, Any]:
    """Run deterministic checks that guard against forced or incoherent splitting."""
    scenes = list(plan.scene_intents or [])
    issues: list[dict[str, Any]] = []
    ids = [_scene_id(scene) for scene in scenes]
    nonempty_ids = [sid for sid in ids if sid]

    if not scenes:
        issues.append(
            {
                "code": "missing_scenes",
                "severity": "critical",
                "scene_ids": [],
                "message": "场景计划为空，不能进入场景级写作。",
            }
        )
    if len(nonempty_ids) != len(set(nonempty_ids)):
        issues.append(
            {
                "code": "duplicate_scene_id",
                "severity": "critical",
                "scene_ids": nonempty_ids,
                "message": "scene_id 存在重复。",
            }
        )

    id_set = set(nonempty_ids)
    for scene in scenes:
        sid = _scene_id(scene)
        for dep in _string_list(field(scene, "dependency_scene_ids", [])):
            if dep not in id_set:
                issues.append(
                    {
                        "code": "invalid_dependency",
                        "severity": "high",
                        "scene_ids": [sid],
                        "message": f"{sid} 依赖不存在的场景 {dep}。",
                    }
                )

    for field_name in ("owned_events", "owned_revelations", "owned_state_changes"):
        owners: dict[str, list[str]] = {}
        for scene in scenes:
            sid = _scene_id(scene)
            for item in _string_list(field(scene, field_name, [])):
                owners.setdefault(item, []).append(sid)
        for item, owner_ids in owners.items():
            if len(owner_ids) > 1:
                issues.append(
                    {
                        "code": f"duplicate_{field_name}",
                        "severity": "high",
                        "scene_ids": owner_ids,
                        "message": f"{field_name} 重复：{item}",
                    }
                )

    # ``required_outcome`` is a scene-level summary.  Every semicolon-delimited
    # hard outcome must also have its own owned_events entry, otherwise DRAFT
    # receives an ambiguous bundled instruction and can satisfy only a subset.
    for scene in scenes:
        sid = _scene_id(scene)
        outcome_items = _string_list(field(scene, "required_outcome", ""))
        raw_owned_events = list(field(scene, "owned_events", []) or [])
        owned_events = _string_list(raw_owned_events)
        if outcome_items and not owned_events:
            issues.append(
                {
                    "code": "required_outcome_without_owned_events",
                    "severity": "high",
                    "scene_ids": [sid],
                    "message": "场景有 required_outcome，但未提供逐项 owned_events 验收清单。",
                }
            )
            continue
        if len(owned_events) < len(outcome_items):
            issues.append(
                {
                    "code": "required_outcome_not_atomically_owned",
                    "severity": "high",
                    "scene_ids": [sid],
                    "message": (
                        "required_outcome 含多个硬结果，但 owned_events 未逐项认领；"
                        "请拆成一项一个可观察事件。"
                    ),
                }
            )
        if any(len(_string_list(item)) > 1 for item in raw_owned_events):
            issues.append(
                {
                    "code": "non_atomic_owned_event",
                    "severity": "high",
                    "scene_ids": [sid],
                    "message": "owned_events 含分号并列项；每项只能承担一个可观察事件。",
                }
            )

    edges = _collect_edges(plan, {})
    cycle = _first_cycle(nonempty_ids, edges)
    if cycle:
        issues.append(
            {
                "code": "dependency_cycle",
                "severity": "critical",
                "scene_ids": cycle,
                "message": "场景依赖存在环，无法拓扑排序。",
            }
        )

    if scenes:
        first = scenes[0]
        first_text = " ".join(
            [
                _text(field(first, "summary", "")),
                _text(field(first, "entry_state", "")),
                " ".join(_string_list(field(first, "entry_state_refs", []))),
                _text(field(first, "required_outcome", "")),
            ]
        )
        bridge_anchor = _text(bridge.action_handoff or bridge.bridge_summary)
        if bridge_anchor and not _shares_token(first_text, bridge_anchor):
            issues.append(
                {
                    "code": "first_scene_bridge_weak",
                    "severity": "medium",
                    "scene_ids": [_scene_id(first)],
                    "message": "首场与桥接动作/摘要关联较弱，请确认开头承接。",
                }
            )
        last = scenes[-1]
        if not _text(field(last, "exit_state", "")) and not _text(
            field(last, "exit_target_state", "")
        ):
            issues.append(
                {
                    "code": "last_scene_exit_missing",
                    "severity": "high",
                    "scene_ids": [_scene_id(last)],
                    "message": "最后一场缺少出口状态。",
                }
            )

    total_words = sum(max(0, int(field(scene, "target_words", 0) or 0)) for scene in scenes)
    target = max(0, int(target_word_count or 0))
    if target > 0 and total_words > 0:
        ratio = abs(total_words - target) / target
        if ratio > 0.35:
            issues.append(
                {
                    "code": "word_budget_drift",
                    "severity": "medium",
                    "scene_ids": [],
                    "message": f"场景字数预算合计 {total_words} 与目标 {target} 偏差过大。",
                }
            )

    blocking = {
        "critical",
        "high",
    }
    valid = not any(str(issue.get("severity", "")).lower() in blocking for issue in issues)
    return {
        "valid": valid,
        "issues": issues,
        "parallel_groups": _serial_groups(plan),
        "serial_edges": [
            {"before": before, "after": after, "reason": "dependency_scene_ids"}
            for before, after in edges
        ],
        "summary": "本地验证通过。" if valid else "本地验证发现阻断问题。",
        "source": "local",
    }


async def validate_scene_plan_with_llm(
    *,
    runner: Any,
    bundle: Any,
    bridge: ChapterBridge,
    plan: ChapterPlan,
    chapter_number: int,
) -> dict[str, Any]:
    """Combine deterministic validation with an LLM pass for semantic overlap."""
    local_report = validate_scene_plan_locally(
        plan=plan,
        bridge=bridge,
        target_word_count=int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0),
    )
    context = {
        "chapter_number": chapter_number,
        "target_word_count": int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0),
        "chapter_card": {
            "chapter_number": chapter_number,
            "title": getattr(bundle.chapter_outline, "title", ""),
            "goal": getattr(bundle.chapter_outline, "goal", ""),
            "target_word_count": int(
                getattr(bundle.chapter_outline, "expected_word_count", 0) or 0
            ),
        },
        "bridge_card": bridge.model_dump(mode="json"),
        "scene_plan": plan.model_dump(mode="json"),
        "local_issues": local_report.get("issues", []),
    }
    request = runner._builder.build(
        TaskType.VALIDATE_SCENE_PLAN,
        context,
        max_tokens=route_max_output_budget(
            runner._router,
            TaskType.VALIDATE_SCENE_PLAN,
            min_tokens=2400,
        ),
        temperature=0.0,
    )
    try:
        llm_report = await llm_h.route_json_object_with_retry(
            runner._router,
            request,
            task_type=TaskType.VALIDATE_SCENE_PLAN,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        llm_report = {
            "valid": False,
            "issues": [
                {
                    "code": "llm_validation_failed",
                    "severity": "high",
                    "scene_ids": [],
                    "message": str(exc),
                }
            ],
            "parallel_groups": local_report.get("parallel_groups", []),
            "serial_edges": local_report.get("serial_edges", []),
            "summary": "LLM 场景验证失败。",
        }

    issues = [
        *list(local_report.get("issues", []) or []),
        *list(llm_report.get("issues", []) or []),
    ]
    valid = bool(local_report.get("valid")) and bool(llm_report.get("valid"))
    serial_edges = _merge_serial_edges(
        list(local_report.get("serial_edges", []) or []),
        list(llm_report.get("serial_edges", []) or []),
    )
    return {
        "valid": valid,
        "issues": issues,
        "parallel_groups": _normalize_parallel_groups(
            llm_report.get("parallel_groups") or local_report.get("parallel_groups") or [],
            plan=plan,
        ),
        "serial_edges": serial_edges,
        "summary": _text(llm_report.get("summary") or local_report.get("summary")),
        "local": local_report,
        "llm": llm_report,
    }


def validation_issues_for_replan(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in list(report.get("issues", []) or [])
        if isinstance(item, dict)
        and str(item.get("severity", "") or "").lower() in {"critical", "high", "medium"}
    ][:8]


def scene_execution_groups(
    plan: ChapterPlan, validation_report: dict[str, Any]
) -> list[list[SceneIntent]]:
    """Return topological batches, preserving prose continuity by default.

    A validator-suggested parallel batch is only honoured when every scene
    explicitly opts into the same non-empty ``parallel_group``.  This prevents
    an optimistic grouping from causing a later scene to draft without the
    prior scene's actual prose handoff.
    """
    scenes = sorted(
        list(plan.scene_intents or []),
        key=lambda item: (_scene_order(item, 10_000), _scene_id(item)),
    )
    by_id = {_scene_id(scene): scene for scene in scenes if _scene_id(scene)}
    edges = _collect_edges(plan, validation_report)
    remaining = set(by_id)
    batches: list[list[SceneIntent]] = []
    while remaining:
        ready = sorted(
            [
                sid
                for sid in remaining
                if all(after != sid or before not in remaining for before, after in edges)
            ],
            key=lambda sid: (_scene_order(by_id[sid], 10_000), sid),
        )
        if not ready:
            ready = [min(remaining, key=lambda sid: (_scene_order(by_id[sid], 10_000), sid))]
        preferred = _preferred_group_order(validation_report, ready)
        approved_by_scene: dict[str, list[SceneIntent]] = {}
        for group_ids in preferred:
            group = sorted(
                [by_id[sid] for sid in group_ids if sid in remaining],
                key=lambda item: (_scene_order(item, 10_000), _scene_id(item)),
            )
            if not _parallel_batch_is_explicit(group):
                continue
            for scene in group:
                approved_by_scene.setdefault(_scene_id(scene), group)

        # Always emit the earliest ready scene first.  An approved parallel
        # group may run together only when its earliest member becomes the
        # next chronological scene; otherwise it would bypass a needed prose
        # handoff even though the dependency graph happens to be incomplete.
        emitted: set[str] = set()
        ready_set = set(ready)
        ready_positions = {sid: index for index, sid in enumerate(ready)}
        for sid in ready:
            if sid in emitted:
                continue
            group = approved_by_scene.get(sid, [])
            batch_scene_ids = {_scene_id(scene) for scene in group}
            start = ready_positions[sid]
            next_ids = set(ready[start : start + len(batch_scene_ids)])
            if (
                group
                and batch_scene_ids <= ready_set
                and batch_scene_ids == next_ids
                and not (batch_scene_ids & emitted)
            ):
                batches.append(group)
                emitted.update(batch_scene_ids)
            else:
                batches.append([by_id[sid]])
                emitted.add(sid)
        remaining.difference_update(emitted)
    return batches


def stitch_scene_texts(
    *,
    plan: ChapterPlan,
    scene_texts: dict[str, str],
    scene_diagnostics: dict[str, dict[str, Any]] | None = None,
    hard_failures_resolved: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    scene_diagnostics = scene_diagnostics or {}
    hard_failures_resolved = hard_failures_resolved or []
    ordered = sorted(
        list(plan.scene_intents or []),
        key=lambda item: (_scene_order(item, 10_000), _scene_id(item)),
    )
    missing = [
        _scene_id(scene) for scene in ordered if not _text(scene_texts.get(_scene_id(scene)))
    ]
    soft_warning_count = sum(
        len(list((diag or {}).get("soft_warnings", []) or []))
        for diag in scene_diagnostics.values()
        if isinstance(diag, dict)
    )
    if missing:
        return "", {
            "scene_count": len(ordered),
            "word_count": 0,
            "missing_scenes": missing,
            "dependency_order": [_scene_id(scene) for scene in ordered],
            "possible_transition_gaps": ["缺少场景，未执行拼接。"],
            "scene_diagnostics": scene_diagnostics,
            "hard_failures_resolved": hard_failures_resolved,
            "soft_warning_count": soft_warning_count,
        }
    parts = [_clean_scene_text(scene_texts[_scene_id(scene)]) for scene in ordered]
    text = "\n\n".join(part for part in parts if part).strip()
    gaps: list[str] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        handoff = _text(
            previous.handoff_to_next or previous.exit_state or previous.exit_target_state
        )
        entry = _text(current.entry_state or "；".join(current.entry_state_refs))
        if handoff and entry and not _shares_token(handoff, entry):
            gaps.append(f"{previous.scene_id} -> {current.scene_id} 交接可能偏弱")
    report = {
        "scene_count": len(ordered),
        "word_count": len(text),
        "missing_scenes": [],
        "dependency_order": [_scene_id(scene) for scene in ordered],
        "possible_transition_gaps": gaps,
        "scene_diagnostics": scene_diagnostics,
        "hard_failures_resolved": hard_failures_resolved,
        "soft_warning_count": soft_warning_count,
    }
    return text, report


def _clean_scene_text(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if re.fullmatch(r"#{1,6}\s*scene[_\-\s]?\d+.*", stripped, flags=re.IGNORECASE):
            continue
        if re.fullmatch(r"(场景|scene)[_\-\s]?\d+[:：]?.*", stripped, flags=re.IGNORECASE):
            continue
        lines.append(line.rstrip())
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _collect_edges(plan: ChapterPlan, validation_report: dict[str, Any]) -> set[tuple[str, str]]:
    ids = {_scene_id(scene) for scene in plan.scene_intents if _scene_id(scene)}
    edges: set[tuple[str, str]] = set()
    for scene in plan.scene_intents:
        sid = _scene_id(scene)
        if not sid:
            continue
        for dep in _string_list(scene.dependency_scene_ids):
            if dep in ids and dep != sid:
                edges.add((dep, sid))
    for edge in list(validation_report.get("serial_edges", []) or []):
        if not isinstance(edge, dict):
            continue
        before = _text(edge.get("before"))
        after = _text(edge.get("after"))
        if before in ids and after in ids and before != after:
            edges.add((before, after))
    return edges


def _merge_serial_edges(*edge_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []
    for edges in edge_lists:
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            before = _text(edge.get("before"))
            after = _text(edge.get("after"))
            if not before or not after or (before, after) in seen:
                continue
            seen.add((before, after))
            merged.append(
                {
                    "before": before,
                    "after": after,
                    "reason": _text(edge.get("reason")) or "scene dependency",
                }
            )
    return merged


def _normalize_parallel_groups(value: Any, *, plan: ChapterPlan) -> list[list[str]]:
    ids = {_scene_id(scene) for scene in plan.scene_intents if _scene_id(scene)}
    groups: list[list[str]] = []
    if isinstance(value, list):
        for raw_group in value:
            group_items = raw_group if isinstance(raw_group, list | tuple | set) else [raw_group]
            group: list[str] = []
            for item in group_items:
                sid = _text(item)
                if sid in ids and sid not in group:
                    group.append(sid)
            if group:
                groups.append(group)
    covered = {sid for group in groups for sid in group}
    for sid in sorted(ids):
        if sid not in covered:
            groups.append([sid])
    return groups


def _serial_groups(plan: ChapterPlan) -> list[list[str]]:
    return [[_scene_id(scene)] for scene in plan.scene_intents if _scene_id(scene)]


def _preferred_group_order(report: dict[str, Any], ready: list[str]) -> list[list[str]]:
    ready_set = set(ready)
    groups: list[list[str]] = []
    for group in list(report.get("parallel_groups", []) or []):
        if not isinstance(group, list | tuple | set):
            continue
        selected = [sid for sid in (_text(item) for item in group) if sid in ready_set]
        if selected:
            groups.append(selected)
    covered = {sid for group in groups for sid in group}
    remaining = [sid for sid in ready if sid not in covered]
    if remaining:
        groups.append(remaining)
    return groups


def _parallel_batch_is_explicit(group: list[SceneIntent]) -> bool:
    """Whether a validator-approved batch is also explicitly safe in the plan."""
    if len(group) < 2:
        return False
    labels = {_text(field(scene, "parallel_group", "")) for scene in group}
    return len(labels) == 1 and bool(next(iter(labels)))


def _first_cycle(ids: list[str], edges: set[tuple[str, str]]) -> list[str]:
    nodes = [sid for sid in ids if sid]
    remaining = set(nodes)
    while remaining:
        ready = {
            sid
            for sid in remaining
            if all(after != sid or before not in remaining for before, after in edges)
        }
        if not ready:
            return sorted(remaining)
        remaining.difference_update(ready)
    return []


def _shares_token(left: str, right: str) -> bool:
    l_compact = re.sub(r"\s+", "", left or "")
    r_compact = re.sub(r"\s+", "", right or "")
    if len(l_compact) >= 2 and l_compact in r_compact:
        return True
    if len(r_compact) >= 2 and r_compact in l_compact:
        return True
    l_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", left or ""))
    r_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", right or ""))
    if not l_tokens or not r_tokens:
        return False
    return bool(l_tokens & r_tokens)


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
        return dict(payload) if isinstance(payload, dict) else {}
    return {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _normalize_retry_feedback(value: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Keep local retry guidance short, textual, and safe for a prose prompt."""
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in list(value or [])[:6]:
        if not isinstance(item, dict):
            continue
        code = _text(item.get("code"))[:80]
        message = _text(item.get("message"))[:240]
        key = (code, message)
        if not message or key in seen:
            continue
        seen.add(key)
        result.append({"code": code or "local_validation", "message": message})
    return result


def _drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v not in (None, "", [], {})}


def _scope_scene_stage_cards(
    cards: dict[str, Any],
    *,
    plan: ChapterPlan,
    scene: SceneIntent,
) -> dict[str, Any]:
    names = _scene_focus_names(scene)
    cards.pop("bridge", None)

    plan_card = cards.get("plan")
    if isinstance(plan_card, dict):
        plan_card.pop("cross_scene_intent", None)
        plan_card.pop("scene_intents", None)
        if isinstance(plan_card.get("expression_channel_records"), list):
            plan_card["expression_channel_records"] = plan_card["expression_channel_records"][:6]
        cards["plan"] = plan_card

    characters = cards.get("characters")
    if isinstance(characters, list):
        filtered = _filter_named_records(characters, names, max_items=6)
        cards["characters"] = filtered or characters[:4]

    knowledge = cards.get("knowledge")
    if isinstance(knowledge, dict):
        character_cards = knowledge.get("character_cards")
        if isinstance(character_cards, list):
            filtered = _filter_named_records(
                character_cards,
                names,
                name_keys=("character", "name"),
                max_items=6,
            )
            knowledge["character_cards"] = filtered or character_cards[:4]
        if isinstance(knowledge.get("chapter_ops"), list):
            knowledge["chapter_ops"] = knowledge["chapter_ops"][:6]
        if isinstance(knowledge.get("global_ops"), list):
            knowledge["global_ops"] = knowledge["global_ops"][:3]
        cards["knowledge"] = knowledge

    editorial = cards.get("editorial")
    if isinstance(editorial, dict):
        voices = editorial.get("character_voices")
        if isinstance(voices, list):
            filtered = _filter_named_records(
                voices,
                names,
                name_keys=("character", "name"),
                max_items=6,
            )
            editorial["character_voices"] = filtered or voices[:4]
        # DRAFT_SCENE does not consume full editorial structure/style/element
        # directives (those are WAVE-only fields).  Remove them to reduce
        # prompt token count by ~500-800 tokens per scene call.
        editorial.pop("structure", None)
        editorial.pop("style_constraints", None)
        editorial.pop("element_directives", None)
        cards["editorial"] = editorial

    # Filter canon_context entities to scene-relevant focus characters only.
    canon = cards.get("canon_context")
    if isinstance(canon, dict) and names:
        entities = canon.get("relevant_entities")
        if isinstance(entities, list) and len(entities) > 6:
            filtered_entities = _filter_named_records(
                entities,
                names,
                name_keys=("name", "entity_name", "character"),
                max_items=8,
            )
            if filtered_entities:
                canon["relevant_entities"] = filtered_entities
        cards["canon_context"] = canon

    cards["scene_scope"] = {
        "scene_id": _scene_id(scene),
        "scene_count": len(list(plan.scene_intents or [])),
        "focus_characters": sorted(names),
    }
    return cards


def _scene_focus_names(scene: SceneIntent) -> set[str]:
    names = set(_string_list(field(scene, "required_characters", [])))
    for item in list(field(scene, "character_motivations", []) or []):
        name = _text(field(item, "character", ""))
        if name:
            names.add(name)
    pov_name = _text(field(scene, "pov_character", ""))
    if pov_name:
        names.add(pov_name)
    voice_targets = field(scene, "dialogue_voice_targets", {}) or {}
    if isinstance(voice_targets, dict):
        for name in voice_targets:
            text = _text(name)
            if text:
                names.add(text)
    return names


def _filter_named_records(
    records: list[Any],
    names: set[str],
    *,
    name_keys: tuple[str, ...] = ("name", "character"),
    max_items: int,
) -> list[Any]:
    if not names:
        return list(records[:max_items])
    selected: list[Any] = []
    for record in records:
        for key in name_keys:
            value = _text(field(record, key, ""))
            if value and value in names:
                selected.append(record)
                break
        if len(selected) >= max_items:
            break
    return selected


def _adjacent_scene_handoffs(plan: ChapterPlan, scene: SceneIntent) -> list[dict[str, str]]:
    ordered = sorted(
        list(plan.scene_intents or []),
        key=lambda item: (_scene_order(item, 10_000), _scene_id(item)),
    )
    sid = _scene_id(scene)
    index = next((idx for idx, item in enumerate(ordered) if _scene_id(item) == sid), -1)
    if index < 0:
        return []
    items: list[dict[str, str]] = []
    for role, offset in (("previous", -1), ("next", 1)):
        pos = index + offset
        if pos < 0 or pos >= len(ordered):
            continue
        other = ordered[pos]
        items.append(
            {
                "role": role,
                "scene_id": _scene_id(other),
                "entry_state": _text(field(other, "entry_state", "")),
                "exit_state": _text(
                    field(other, "exit_state", "") or field(other, "exit_target_state", "")
                ),
                "handoff_to_next": _text(field(other, "handoff_to_next", "")),
            }
        )
    return items

"""Semantic enrichment for continuity issues.

This module keeps story-specific content in data and routing logic in code.  The
evaluator can still rely on LLM judgment for literary continuity, while local
code provides stable repair surfaces, postconditions, and issue identities.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.utils.audit_issue import stable_issue_id
from novel_forge.core.utils.boundary_windows import (
    DEFAULT_OPENING_PARAGRAPHS,
    DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
    coerce_paragraph_count,
)
from novel_forge.core.utils.field_extractor import field


class _IssueSemantics:
    """Derive repair ownership and verification contracts for continuity issues."""

    _VALID_SURFACES = frozenset(
        {
            "chapter_text",
            "bridge_artifact",
            "chapter_plan",
            "state_packet",
            "local_rule",
            "replan",
            "manual",
        }
    )
    _VALID_SOURCES = frozenset({"llm", "local", "merged", "postcondition", "manual"})
    _VALID_STATUSES = frozenset(
        {"open", "repairing", "resolved", "suppressed", "artifact_fixed", "deferred"}
    )
    _TEXT_REPAIR_TYPES = frozenset(
        {
            "opening_gap",
            "closing_gap",
            "closing_contract_mismatch",
            "carry_forward_missing",
            "custody_break",
            "handoff_missing",
            "information_consistency",
            "location_jump",
            "relationship_change_support",
            "relationship_development",
            "state_carryover_gap",
            "continuity_gap",
            "continuity_error",
        }
    )
    _BRIDGE_FIELD_MARKERS = frozenset(
        {
            "bridge.",
            "bridge_",
            "from_chapter",
            "to_chapter",
            "opening_pov",
            "opening_location",
            "opening_time",
            "transition_mode",
            "action_handoff 为空",
            "桥接字段",
            "bridge 缺少",
            "bridge from_chapter",
        }
    )

    @classmethod
    def _coerce_repair_surface(cls, value: Any) -> str:
        surface = str(value or "").strip().lower()
        return surface if surface in cls._VALID_SURFACES else ""

    @classmethod
    def _coerce_issue_source(cls, issue: dict[str, Any]) -> str:
        raw = str(issue.get("source") or "").strip().lower()
        if raw in cls._VALID_SOURCES:
            return raw
        if issue.get("local_detection"):
            return "local"
        return "llm"

    @classmethod
    def _coerce_issue_status(cls, value: Any) -> str:
        status = str(value or "").strip().lower()
        return status if status in cls._VALID_STATUSES else "open"

    @staticmethod
    def _clean(value: Any, *, limit: int = 180) -> str:
        # Structured carry-forward items project their text under ``text``.
        text_attr = getattr(value, "text", None)
        if text_attr is not None and not isinstance(value, (str, bytes)):
            value = text_attr
        elif isinstance(value, dict) and ("text" in value or "item" in value):
            value = value.get("text") or value.get("item")
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip("，。、；： \n") + "…"

    @classmethod
    def _issue_blob(cls, issue: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("issue_type", "summary", "evidence", "evidence_quote", "validator_id"):
            parts.append(str(issue.get(key) or ""))
        actions = issue.get("fix_actions")
        if isinstance(actions, list):
            parts.extend(str(item or "") for item in actions)
        return " ".join(parts).lower()

    @classmethod
    def _declared_pov_switch(cls, input_data: Any) -> bool:
        bridge = getattr(input_data, "chapter_bridge", None)
        packet = getattr(input_data, "chapter_state_packet", None)
        previous_exit = getattr(packet, "previous_exit_state", None) if packet else None
        transition_mode = str(field(bridge, "transition_mode", "") or "").strip().lower()
        if transition_mode == "pov_switch" or bool(getattr(input_data, "pov_switch", False)):
            return True
        previous_pov = cls._clean(field(previous_exit, "pov", ""), limit=80)
        opening_pov = cls._clean(field(bridge, "opening_pov", ""), limit=80)
        return bool(previous_pov and opening_pov and previous_pov != opening_pov)

    @classmethod
    def _repair_surface_for_issue(cls, issue: dict[str, Any], input_data: Any) -> str:
        explicit = cls._coerce_repair_surface(issue.get("repair_surface"))
        if explicit:
            return explicit

        issue_type = str(issue.get("issue_type") or "").strip().lower()
        blob = cls._issue_blob(issue)
        bridge = getattr(input_data, "chapter_bridge", None)

        if issue_type == "bridge_contract_not_followed":
            if any(marker in blob for marker in cls._BRIDGE_FIELD_MARKERS):
                return "bridge_artifact"
            return "chapter_text"

        if issue_type == "pov_jump":
            if not field(bridge, "transition_mode", "") or "transition_mode" in blob:
                return "bridge_artifact"
            return "chapter_text"

        if issue_type == "location_jump" and cls._declared_pov_switch(input_data):
            action_handoff = cls._clean(field(bridge, "action_handoff", ""), limit=160)
            try:
                location_confidence = float(issue.get("location_confidence") or 0.0)
            except (TypeError, ValueError):
                location_confidence = 0.0
            if action_handoff and location_confidence < 0.8:
                return "local_rule"

        if issue_type in {"state_continuity_error"}:
            return "state_packet"
        if issue_type in {"chapter_plan_mismatch", "plan_contract_mismatch"}:
            return "chapter_plan"
        if issue_type in cls._TEXT_REPAIR_TYPES:
            return "chapter_text"
        return "chapter_text"

    @classmethod
    def _expected_state(cls, input_data: Any) -> dict[str, Any]:
        bridge = getattr(input_data, "chapter_bridge", None)
        packet = getattr(input_data, "chapter_state_packet", None)
        previous_exit = getattr(packet, "previous_exit_state", None) if packet else None
        return {
            "previous_exit": {
                "chapter_number": field(previous_exit, "chapter_number", 0),
                "time_marker": cls._clean(field(previous_exit, "time_marker", ""), limit=80),
                "location": cls._clean(field(previous_exit, "location", ""), limit=80),
                "pov": cls._clean(field(previous_exit, "pov", ""), limit=80),
            },
            "bridge": {
                "from_chapter": field(bridge, "from_chapter", 0),
                "to_chapter": field(bridge, "to_chapter", 0),
                "opening_time": cls._clean(field(bridge, "opening_time", ""), limit=80),
                "opening_location": cls._clean(
                    field(bridge, "opening_location", ""),
                    limit=80,
                ),
                "opening_pov": cls._clean(field(bridge, "opening_pov", ""), limit=80),
                "transition_mode": cls._clean(
                    field(bridge, "transition_mode", ""),
                    limit=80,
                ),
                "action_handoff": cls._clean(
                    field(bridge, "action_handoff", ""),
                    limit=180,
                ),
            },
            "narrative_state_projection": field(
                packet,
                "narrative_state_projection",
                {},
            )
            if packet
            else {},
            "chapter_contract": field(packet, "chapter_contract", {}) if packet else {},
        }

    @classmethod
    def _normalize_anchor(cls, raw: Any) -> dict[str, str] | None:
        if isinstance(raw, str):
            text = cls._clean(raw)
            if not text:
                return None
            return {"source": "llm", "text": text, "role": "", "importance": "required"}
        if not isinstance(raw, dict):
            return None
        text = cls._clean(raw.get("text") or raw.get("anchor") or raw.get("value"))
        if not text:
            return None
        return {
            "source": cls._clean(raw.get("source"), limit=100),
            "text": text,
            "role": cls._clean(raw.get("role"), limit=80),
            "importance": cls._clean(raw.get("importance") or "required", limit=40),
        }

    @classmethod
    def _missing_anchors(cls, issue: dict[str, Any], input_data: Any) -> list[dict[str, str]]:
        anchors: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(source: str, text: Any, role: str, importance: str = "required") -> None:
            cleaned = cls._clean(text)
            if not cleaned:
                return
            key = (source, cleaned)
            if key in seen:
                return
            seen.add(key)
            anchors.append(
                {
                    "source": source,
                    "text": cleaned,
                    "role": role,
                    "importance": importance,
                }
            )

        for raw in issue.get("missing_anchors") or []:
            normalized = cls._normalize_anchor(raw)
            if normalized:
                add(
                    normalized["source"],
                    normalized["text"],
                    normalized["role"],
                    normalized["importance"],
                )

        issue_type = str(issue.get("issue_type") or "").strip().lower()
        bridge = getattr(input_data, "chapter_bridge", None)
        packet = getattr(input_data, "chapter_state_packet", None)
        if issue_type in {"opening_gap", "carry_forward_missing", "location_jump"}:
            add(
                "chapter_bridge.action_handoff",
                field(bridge, "action_handoff", ""),
                "action_handoff",
            )
            add(
                "chapter_bridge.emotional_carryover",
                field(bridge, "emotional_carryover", ""),
                "emotional_handoff",
                "supporting",
            )
            for idx, item in enumerate(list(field(packet, "must_carry_forward", []) or [])):
                add(f"chapter_state_packet.must_carry_forward[{idx}]", item, "carry_forward")
        return anchors

    @classmethod
    def _postconditions(
        cls,
        issue: dict[str, Any],
        surface: str,
        input_data: Any,
    ) -> list[dict[str, Any]]:
        raw_items = issue.get("postconditions")
        if isinstance(raw_items, list) and raw_items:
            normalized: list[dict[str, Any]] = []
            for raw in raw_items:
                if isinstance(raw, str):
                    desc = cls._clean(raw, limit=260)
                    if desc:
                        normalized.append(
                            {
                                "validator_id": "llm_semantic_postcondition",
                                "description": desc,
                                "evidence_hint": "",
                                "required": True,
                            }
                        )
                elif isinstance(raw, dict):
                    desc = cls._clean(raw.get("description") or raw.get("condition"), limit=260)
                    if desc:
                        normalized.append(
                            {
                                "validator_id": cls._clean(
                                    raw.get("validator_id") or "llm_semantic_postcondition",
                                    limit=100,
                                ),
                                "description": desc,
                                "evidence_hint": cls._clean(raw.get("evidence_hint"), limit=160),
                                "required": bool(raw.get("required", True)),
                            }
                        )
            if normalized:
                return normalized

        issue_type = str(issue.get("issue_type") or "").strip().lower()
        if surface == "bridge_artifact":
            return [
                {
                    "validator_id": "bridge_contract_validator",
                    "description": "桥接 JSON 字段与上一章出口、本章大纲和过渡模式保持一致。",
                    "evidence_hint": "chapter_bridge",
                    "required": True,
                }
            ]
        if surface == "local_rule":
            return [
                {
                    "validator_id": "local_rule_suppression_validator",
                    "description": "确认该问题属于合法视角/镜头切换或本地规则误判，不进入正文修复。",
                    "evidence_hint": "previous_exit + chapter_bridge",
                    "required": True,
                }
            ]

        conditions: list[dict[str, Any]] = []
        if issue_type in {"opening_gap", "location_jump", "carry_forward_missing"}:
            conditions.append(
                {
                    "validator_id": "opening_transition_validator",
                    "description": "开头窗口自然落地缺失锚点，且不改变 bridge.opening_pov。",
                    "evidence_hint": "opening paragraphs + missing_anchors",
                    "required": True,
                }
            )
        if issue_type in {"closing_gap", "closing_contract_mismatch"}:
            conditions.append(
                {
                    "validator_id": "closing_handoff_validator",
                    "description": "结尾保留可供下一章直接承接的行动、状态或悬念出口。",
                    "evidence_hint": "closing paragraphs + chapter_plan.closing_contract",
                    "required": True,
                }
            )
        if not conditions:
            conditions.append(
                {
                    "validator_id": "continuity_semantic_validator",
                    "description": "修复后该 issue 的 summary/evidence 所述矛盾不再成立。",
                    "evidence_hint": "target paragraphs",
                    "required": True,
                }
            )
        return conditions

    @classmethod
    def _normalize_repair_directive(cls, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        directive: dict[str, Any] = {}
        for key in (
            "target_window",
            "repair_strategy",
            "recommended_rewrite",
            "conflict_policy",
            "repair_order",
        ):
            value = cls._clean(raw.get(key), limit=800 if key == "recommended_rewrite" else 220)
            if value:
                directive[key] = value
        for key in ("required_context", "required_anchors", "validation_focus"):
            values = raw.get(key)
            if isinstance(values, list):
                cleaned = [cls._clean(item, limit=220) for item in values]
                directive[key] = [item for item in cleaned if item]
        return directive

    @classmethod
    def _repair_directive(
        cls,
        issue: dict[str, Any],
        *,
        surface: str,
        missing_anchors: list[dict[str, str]],
        postconditions: list[dict[str, Any]],
        input_data: Any,
    ) -> dict[str, Any] | None:
        raw_directive = cls._normalize_repair_directive(issue.get("repair_directive"))
        issue_type = str(issue.get("issue_type") or "").strip().lower()
        if issue_type not in {"opening_gap", "bridge_contract_not_followed"}:
            return raw_directive or None
        if surface != "chapter_text":
            return raw_directive or None

        bridge = getattr(input_data, "chapter_bridge", None)
        packet = getattr(input_data, "chapter_state_packet", None)
        plan = getattr(input_data, "chapter_plan", None)
        prev_tail_paragraphs = coerce_paragraph_count(
            getattr(
                input_data,
                "boundary_prev_tail_paragraphs",
                DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
            ),
            default=DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
        )
        opening_paragraphs = coerce_paragraph_count(
            getattr(input_data, "boundary_opening_paragraphs", DEFAULT_OPENING_PARAGRAPHS),
            default=DEFAULT_OPENING_PARAGRAPHS,
            maximum=8,
        )
        start = int(issue.get("paragraph_start") or 1)
        end = int(issue.get("paragraph_end") or max(start, opening_paragraphs))
        target_window = f"本章开头第 {start}-{end} 段"

        raw_required_context = list(raw_directive.get("required_context") or [])
        required_context: list[str] = []
        defaults = [
            f"上一章结尾 {prev_tail_paragraphs} 段（只读，用于判断动作、时空和情绪落点）",
            f"本章开头 {opening_paragraphs} 段（目标窗口，只修改必要段落，后文用于防止接缝）",
            "chapter_bridge 的 opening_time/opening_location/opening_pov/action_handoff",
            "chapter_plan.opening_contract 与第一场景 entry_state_refs",
        ]
        for item in defaults:
            if item not in required_context:
                required_context.append(item)
        for item in raw_required_context:
            if ("上一章结尾" in item and "段" in item) or ("本章开头" in item and "段" in item):
                continue
            if item not in required_context:
                required_context.append(item)

        required_anchors = list(raw_directive.get("required_anchors") or [])
        for anchor in missing_anchors:
            text = cls._clean(anchor.get("text"), limit=220)
            role = cls._clean(anchor.get("role"), limit=80)
            if text:
                label = f"{role}：{text}" if role else text
                if label not in required_anchors:
                    required_anchors.append(label)
        for label, value in (
            ("开场时间", field(bridge, "opening_time", "")),
            ("开场地点", field(bridge, "opening_location", "")),
            ("开场视角", field(bridge, "opening_pov", "")),
            ("动作接力", field(bridge, "action_handoff", "")),
            ("情绪余波", field(bridge, "emotional_carryover", "")),
        ):
            text = cls._clean(value, limit=220)
            if text:
                item = f"{label}：{text}"
                if item not in required_anchors:
                    required_anchors.append(item)

        validation_focus = list(raw_directive.get("validation_focus") or [])
        for condition in postconditions:
            desc = cls._clean(condition.get("description"), limit=220)
            if desc and desc not in validation_focus:
                validation_focus.append(desc)

        rewrite = raw_directive.get("recommended_rewrite", "")
        if not rewrite:
            previous = cls._clean(field(packet, "previous_chapter_ending", ""), limit=180)
            opening_contract = cls._clean(field(plan, "opening_contract", ""), limit=220)
            handoff = cls._clean(field(bridge, "action_handoff", ""), limit=220)
            rewrite_parts = [
                "建议将开头目标窗口改写为连续正文：",
                f"先承接上一章结尾余波（{previous or '以上一章末段实际行动为准'}），",
                "再落到本章开场时间、地点和 POV，",
                f"最后把动作接力自然写成角色正在进行的动作（{handoff or '以 bridge.action_handoff 为准'}）。",
            ]
            if opening_contract:
                rewrite_parts.append(f"同时满足开头契约：{opening_contract}")
            rewrite = "".join(rewrite_parts)

        return {
            "target_window": target_window,
            "repair_strategy": raw_directive.get("repair_strategy")
            or "窗口级改写开头：重排时空落点、情绪余波和动作接力，避免只补关键词。",
            "recommended_rewrite": rewrite,
            "required_context": required_context,
            "required_anchors": required_anchors,
            "validation_focus": validation_focus,
            "conflict_policy": raw_directive.get("conflict_policy")
            or "若与同一开头窗口内的其他修复重叠，先合并其他事实/因果修复，再最后统一改写开场衔接。",
            "repair_order": raw_directive.get("repair_order") or "late_if_conflict",
        }

    @classmethod
    def _issue_id(cls, issue: dict[str, Any], chapter_number: int, surface: str) -> str:
        explicit = cls._clean(issue.get("issue_id"), limit=120)
        if explicit:
            return explicit
        return stable_issue_id(
            "cont",
            chapter_number=chapter_number,
            issue_type=issue.get("issue_type", ""),
            summary=issue.get("summary", ""),
            evidence=issue.get("evidence", "") or issue.get("evidence_quote", ""),
            location=issue.get("location", ""),
            repair_surface=surface,
        )

    @classmethod
    def _severity_blocks(cls, severity: str) -> bool:
        return str(severity or "").strip().lower() in {"critical", "high"}

    @classmethod
    def _enrich_issue_semantics(cls, issue: dict[str, Any], input_data: Any) -> dict[str, Any]:
        enriched = dict(issue)
        surface = cls._repair_surface_for_issue(enriched, input_data)
        source = cls._coerce_issue_source(enriched)
        status = cls._coerce_issue_status(enriched.get("status"))
        diagnostic_note = cls._clean(enriched.get("diagnostic_note"), limit=240)
        if surface == "local_rule" and status == "open":
            status = "suppressed"
            diagnostic_note = (
                diagnostic_note
                or "Declared POV switch with bridge handoff; treating as rule-level false positive."
            )

        severity = str(enriched.get("severity") or "medium").strip().lower()
        confidence = enriched.get("confidence")
        try:
            confidence_value = float(confidence or 0.0)
        except (TypeError, ValueError):
            confidence_value = 0.0

        expected_state = enriched.get("expected_state")
        if not isinstance(expected_state, dict) or not expected_state:
            expected_state = cls._expected_state(input_data)
        observed_state = enriched.get("observed_state")
        if not isinstance(observed_state, dict):
            observed_state = {}

        postconditions = cls._postconditions(enriched, surface, input_data)
        missing_anchors = cls._missing_anchors(enriched, input_data)
        repair_directive = cls._repair_directive(
            enriched,
            surface=surface,
            missing_anchors=missing_anchors,
            postconditions=postconditions,
            input_data=input_data,
        )

        enriched.update(
            {
                "issue_id": cls._issue_id(
                    enriched,
                    int(getattr(input_data, "chapter_number", 0) or 0),
                    surface,
                ),
                "source": source,
                "repair_surface": surface,
                "status": status,
                "blocking": bool(
                    enriched.get("blocking", False)
                    or (status == "open" and cls._severity_blocks(severity))
                ),
                "confidence": max(0.0, min(1.0, confidence_value)),
                "expected_state": expected_state,
                "observed_state": observed_state,
                "missing_anchors": missing_anchors,
                "postconditions": postconditions,
                "repair_directive": repair_directive,
                "validator_id": cls._clean(
                    enriched.get("validator_id") or (postconditions[0]["validator_id"]),
                    limit=100,
                ),
                "diagnostic_note": diagnostic_note,
            }
        )
        return enriched

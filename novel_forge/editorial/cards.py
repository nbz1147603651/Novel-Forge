"""Stage-card projection for the project editorial contract."""

from __future__ import annotations

from typing import Any

from novel_forge.editorial.schemas import EditorialContract


def build_editorial_card(
    contract: EditorialContract | dict[str, Any] | None,
    *,
    chapter_number: int = 0,
    stage: str = "",
    involved_characters: list[str] | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact prompt card from the editorial contract."""

    if contract is None:
        return {}
    if isinstance(contract, EditorialContract):
        editorial = contract
    else:
        editorial = EditorialContract.model_validate(contract)

    main = editorial.main_climax()
    distance = int(chapter_number or 0) - main.chapter_number if chapter_number else 0
    names = {str(name).strip() for name in involved_characters or [] if str(name).strip()}
    voices = [
        voice.model_dump(mode="json")
        for voice in editorial.character_voices
        if not names or voice.character in names
    ]
    if not voices:
        voices = [voice.model_dump(mode="json") for voice in editorial.character_voices[:6]]

    # DRAFT stage must NOT see character_voices (full voice is a WAVE
    # responsibility).  WAVE and EDIT keep them; plan stage also keeps
    # them because the plan template uses them for character-voice aware
    # scene design.
    if stage == "draft":
        voices = []

    risk_guidance = (
        build_editorial_risk_guidance(
            editorial,
            readiness=readiness,
            chapter_number=chapter_number,
            stage=stage,
        )
        if _stage_accepts_editorial_risk(stage)
        else _empty_risk_guidance(stage=stage)
    )

    return {
        "stage": stage,
        "chapter_number": chapter_number,
        "main_climax": main.model_dump(mode="json"),
        "climax_distance": distance,
        "over_denouement_budget": bool(
            distance > main.expected_aftermath_chapters and chapter_number > main.chapter_number
        ),
        "denouement_budget": editorial.denouement_budget.model_dump(mode="json"),
        "character_voices": voices[:8],
        "theme_policies": list(editorial.theme_policies),
        "symbol_policies": [item.model_dump(mode="json") for item in editorial.symbol_policies],
        "scene_resistance_rules": [
            item.model_dump(mode="json") for item in editorial.scene_resistance_rules
        ],
        "expression_channel_budget": dict(editorial.expression_channel_budget),
        "expression_channel_profiles": [
            {
                "channel_id": item.channel_id,
                "channel": item.channel,
                "label": item.label,
                "replacement_axes": list(item.replacement_axes[:4]),
                "allowed_when": item.allowed_when,
                "cooldown_chapters": item.cooldown_chapters,
            }
            for item in editorial.expression_channel_profiles[:8]
        ],
        "body_signal_budget_per_high_emotion_scene": (
            editorial.body_signal_budget_per_high_emotion_scene
        ),
        "forbidden_confirmation_phrases": list(editorial.forbidden_confirmation_phrases),
        "revision_priorities": list(editorial.revision_priorities),
        "risk_guidance": risk_guidance,
        "revelation_ladder": [
            item.model_dump(mode="json") for item in editorial.revelation_ladder
        ],
        "editorial_element_directives": [
            item.model_dump(mode="json")
            for item in editorial.editorial_element_directives
            if (
                not names
                or not item.linked_characters
                or names.intersection(set(item.linked_characters))
            )
        ],
        "time_bridge_policies": list(editorial.time_bridge_policies),
        "title_policy": editorial.title_policy.model_dump(mode="json"),
    }


def build_editorial_risk_guidance(
    contract: EditorialContract | dict[str, Any] | None,
    *,
    readiness: dict[str, Any] | None = None,
    chapter_number: int = 0,
    stage: str = "",
) -> dict[str, Any]:
    """Project init editorial-readiness findings into downstream prompt guidance."""

    if contract is None:
        return _empty_risk_guidance(stage=stage)
    editorial = (
        contract
        if isinstance(contract, EditorialContract)
        else EditorialContract.model_validate(contract)
    )
    readiness_payload = readiness if isinstance(readiness, dict) else {}
    findings = _normalize_readiness_findings(readiness_payload.get("findings"))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        severity = _clean_str(finding.get("severity") or "medium").lower()
        if severity == "info":
            continue
        issue_type = _clean_str(finding.get("issue_type") or "editorial_risk")
        summary = _clean_str(finding.get("summary"))
        if not summary:
            continue
        key = (issue_type, summary[:80])
        if key in seen:
            continue
        seen.add(key)
        recommendation = _clean_str(finding.get("recommendation"))
        items.append(
            {
                "issue_type": issue_type,
                "severity": severity,
                "summary": _trim(summary, 140),
                "action": _trim(
                    _risk_action(issue_type, recommendation),
                    180,
                ),
                "scope": _risk_scope(issue_type),
            }
        )

    max_confirmation = int(editorial.denouement_budget.max_confirmation_scenes or 0)
    has_confirmation_risk = any(
        item.get("issue_type") == "editorial_contract_confirmation_budget_high"
        for item in items
    )
    if max_confirmation > 2 and not has_confirmation_risk:
        items.append(
            {
                "issue_type": "editorial_contract_confirmation_budget_high",
                "severity": "medium",
                "summary": "确认性场景预算偏高，可能形成多次“再结尾”。",
                "action": (
                    "规划、起草和修订时将确认性场景控制在 1-2 次，"
                    "其余尾声节点必须承担新信息、新行动或新后果。"
                ),
                "scope": "denouement",
            }
        )

    main = editorial.main_climax()
    after_climax = bool(chapter_number and chapter_number > main.chapter_number)
    over_aftermath_budget = bool(
        after_climax
        and (int(chapter_number or 0) - main.chapter_number) > main.expected_aftermath_chapters
    )
    has_denouement_risk = any(item.get("scope") == "denouement" for item in items)
    denouement_guidance = ""
    if has_denouement_risk or over_aftermath_budget or max_confirmation > 2:
        denouement_guidance = (
            "高潮后章节不得连续写圆满确认；每个尾声/余波场景必须有独立新功能，"
            "优先承载后果、制度化动作、关系新边界或终场意象。"
        )

    revision_actions = [
        _trim(str(item), 160)
        for item in readiness_payload.get("revision_plan", []) or []
        if _clean_str(item)
    ]
    for item in items:
        action = _clean_str(item.get("action"))
        if action and action not in revision_actions:
            revision_actions.append(action)

    items.sort(
        key=lambda item: (
            _severity_order(str(item.get("severity"))),
            item.get("issue_type", ""),
        )
    )
    return {
        "status": _clean_str(
            readiness_payload.get("status")
            or _as_mapping(readiness_payload.get("metrics")).get("status")
            or ("warning" if items else "passed")
        ),
        "summary": _trim(_clean_str(readiness_payload.get("summary")), 180),
        "items": items[:8],
        "revision_actions": revision_actions[:8],
        "denouement": {
            "current_confirmation_budget": max_confirmation,
            "recommended_confirmation_limit": 2,
            "after_main_climax": after_climax,
            "over_aftermath_budget": over_aftermath_budget,
            "requires_new_function": bool(denouement_guidance),
            "guidance": denouement_guidance,
        },
        "source": "init_editorial_readiness",
        "stage": stage,
    }


def _empty_risk_guidance(*, stage: str = "") -> dict[str, Any]:
    return {
        "status": "passed",
        "summary": "",
        "items": [],
        "revision_actions": [],
        "denouement": {
            "current_confirmation_budget": 0,
            "recommended_confirmation_limit": 2,
            "after_main_climax": False,
            "over_aftermath_budget": False,
            "requires_new_function": False,
            "guidance": "",
        },
        "source": "init_editorial_readiness",
        "stage": stage,
    }


def _stage_accepts_editorial_risk(stage: str) -> bool:
    return _clean_str(stage).lower() in {"plan", "draft", "wave", "edit", "check"}


def _normalize_readiness_findings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    findings: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            findings.append(dict(item))
        elif hasattr(item, "model_dump"):
            dumped = item.model_dump(mode="json")
            if isinstance(dumped, dict):
                findings.append(dumped)
    return findings


def _risk_action(issue_type: str, recommendation: str) -> str:
    if issue_type == "editorial_contract_confirmation_budget_high":
        return recommendation or (
            "将确认性场景控制在 1-2 次，其余尾声节点必须承担新信息、新行动或新后果。"
        )
    if issue_type in {"editorial_contract_denouement_budget_high", "denouement_overrun"}:
        return recommendation or "压缩高潮后重复确认，保留余波后果、制度化节点和终场意象。"
    if issue_type == "time_bridge_policy_missing":
        return recommendation or "规划跨月、跨季或几周后转场时必须给出明确时间桥标记。"
    return recommendation or "在计划、起草和修订时主动规避该编辑契约风险。"


def _risk_scope(issue_type: str) -> str:
    if "denouement" in issue_type or "confirmation" in issue_type:
        return "denouement"
    if "revelation" in issue_type:
        return "revelation"
    if "title" in issue_type:
        return "title"
    if "time_bridge" in issue_type:
        return "time"
    if "element" in issue_type:
        return "element"
    return "editorial"


def _severity_order(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(value.lower(), 4)


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _trim(value: str, limit: int) -> str:
    text = _clean_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"

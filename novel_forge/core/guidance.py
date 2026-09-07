"""Shared meaning of guidance; occurrence and typography never imply obligation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class RequirementSemantics(BaseModel):
    """Provenance and fulfillment policy attached to existing contract artifacts."""

    model_config = ConfigDict(extra="forbid")

    source: str = "planning"
    scope: str = "chapter"
    satisfaction: Literal["invariant", "narrative", "literal", "optional"] = "optional"
    status: Literal["pending", "fulfilled", "not_applicable"] = "pending"
    evidence: str = ""
    source_text_hash: str = ""


class GuidanceRequirement(RequirementSemantics):
    requirement_id: str
    text: str


class LiteralRequirement(RequirementSemantics):
    satisfaction: Literal["literal"] = "literal"
    scope: str = "scene"


def required_cross_scene_ref(ref: Any) -> bool:
    """Legacy rhetorical references are optional, never inferred from keywords."""
    if not isinstance(ref, dict) or not str(ref.get("description") or "").strip():
        return False
    raw = ref.get("requirement") or {}
    if isinstance(raw, RequirementSemantics):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return False
    # Fulfillment belongs to the accepted prose, not a plan model's prediction.
    return raw.get("satisfaction") in {"narrative", "literal"}


def project_guidance_requirements(
    contract: dict[str, Any], *, world_rules: list[str], chapter_number: int
) -> list[dict[str, Any]]:
    """Annotate authoritative fields without promoting optional expressions.

    The source fields remain authoritative; this is a projection, not another
    canon store. No quote/number extraction or frequency-based promotion occurs.
    """
    groups = [
        ("story_foundation.rules", "invariant", world_rules),
        ("chapter_contract.required_events", "narrative", contract.get("required_events", [])),
        (
            "chapter_contract.required_progressions",
            "narrative",
            contract.get("required_progressions", []),
        ),
        ("chapter_contract.theme_duties", "narrative", contract.get("theme_duties", [])),
        ("chapter_contract.symbols", "optional", contract.get("symbols", [])),
    ]
    result = []
    for source, satisfaction, values in groups:
        for index, value in enumerate(values or []):
            if not isinstance(value, str) or not value.strip():
                continue
            result.append(
                GuidanceRequirement(
                    requirement_id=f"{source}:{index}",
                    text=value,
                    source=source,
                    scope=f"chapter:{chapter_number}",
                    satisfaction=satisfaction,
                ).model_dump(mode="json")
            )
    return result


GUIDANCE_RULE_ZH = """## 指导的满足方式
- invariant（事实边界）：未涉及不等于违约；涉及时不得矛盾，不要求复述数字、规则或原话。
- narrative（叙事义务）：由行动、选择和后果语义兑现；已兑现不再分配给后续场景。
- literal（字面义务）：仅明确列入 required_literals 的密码、线索原文等在指定场景逐字保留；不得从引号、数字或意象自动推导。
- optional（可选表达）：可以省略、替换或留白。优先级仅决定参考排序，缺席不得扣分、判违约或生成补写任务。
全书主题是长期理解背景；仅本章明确的 theme_duties 需要推进。感官、意象、措辞不必逐项展示。
保留用户明确要求和已确认事实；AI 补充的意象、数字示例与场景设想属于可修订方案，不因被多份初始化材料引用而成为字面义务。
重复审查须给出正文证据，并比较功能：是否新增信息、改变行动或关系、改变读者理解；换同义词仍重复同一功能也要检查。有意使用不代表没有疲劳。
记忆中的频次与 forbidden_repetition 仅提示重复风险，不是禁词表；承担新作用的再次出现可以保留。
保护密码、必要实体名称、用户明确要求的叠句与仪式性重复。修复优先局部删除或压缩无新增作用的复述，保留已成立的创意、人物声音和因果；不得补回缺席的可选意象。
fulfilled 状态只有与最终接受正文的 source_text_hash 和 evidence 一致才有效；规划预测不能替代正文验收。
"""

GUIDANCE_RULE_EN = """## Guidance fulfillment semantics
- invariant: omission is not a violation; preserve the fact when relevant, without repeating its number or wording.
- narrative: realize the obligation through actions, choices and consequences; do not assign a fulfilled obligation again.
- literal: preserve only explicitly declared required_literals in their owning scenes; quotes and numbers alone never create literal obligations.
- optional: may be omitted or replaced. Priority ranks references only; absence must not lower scores or create repair tasks.
Book themes provide long-term context; only explicit chapter theme_duties require progression. Do not illustrate every sensory or imagery field.
Preserve explicit user requirements and established facts. AI-proposed imagery, illustrative numbers and scene ideas remain revisable; being repeated across initialization artifacts does not create a literal obligation.
Repetition findings need prose evidence and a functional comparison: new information, changed actions or relationships, or changed reader understanding. Synonym swaps can still repeat a function; intentionality alone does not prevent fatigue.
Memory frequency and forbidden_repetition are risk signals, not word bans; recurrence with a new function may remain.
Protect passwords, necessary entity names and user-requested refrains or ritual repetition. Prefer local compression/removal, preserving successful creativity, character voices and causality. Never restore omitted optional imagery.
Fulfilled status is valid only with evidence and source_text_hash matching the accepted final prose, never a planning prediction.
"""

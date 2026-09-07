"""ChapterRepairStep — checks single-chapter correctness issues."""

from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import (
    detect_invalid_time_markers,
    detect_pov_intrusion,
    detect_prompt_leaks,
)
from novel_forge.core.review.review_contracts import (
    chapter_repair_report_to_findings,
    compile_repair_tickets_from_findings,
    normalize_review_mode,
    source_text_hash,
)
from novel_forge.core.review.review_precision import prepare_findings_for_repair
from novel_forge.core.schemas.chapter import ChapterIssue, ChapterRepairReport
from novel_forge.core.utils.field_extractor import field as extract_field
from novel_forge.core.utils.string import clean_str
from novel_forge.editorial.signals import (
    detect_expression_channel_hits,
)
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.continuity_eval.validators import _detect_forbidden_elements


@dataclass
class ChapterRepairInput:
    """Input payload for single-chapter correctness check."""

    chapter_number: int
    chapter_text: str
    canon_context: Any
    character_profiles: list[dict[str, Any]] = field(default_factory=list)
    previous_chapter_ending: str = ""
    known_prompt_markers: list[str] = field(default_factory=list)
    check_mode: str = "full"
    previous_report: ChapterRepairReport | None = None
    changed_sections: list[dict[str, Any]] = field(default_factory=list)
    change_ratio: float | None = None
    time_convention: str = ""
    """Time convention from story_bible for template context."""
    address_rules: str = ""
    """Address form rules from story_bible for template context."""
    world_context_rules: str = ""
    world_rule_card: dict[str, Any] | None = None
    """World-context rules from story_bible for template context."""
    forbidden_elements: list[str] = field(default_factory=list)
    """Hard-forbidden repeated elements from chapter plan."""
    forbidden_elements_soft: list[str] = field(default_factory=list)
    """Soft-forbidden repeated elements from chapter plan."""
    intentional_callbacks: list[str] = field(default_factory=list)
    """Allowed motif callbacks that should not be treated as forbidden-element hits."""
    expression_channel_records: list[dict[str, Any]] = field(default_factory=list)
    """Typed expression channels that should cool down this chapter."""
    expression_channel_detection_enabled: bool = True
    """Whether local semantic expression-channel detection is enabled."""
    pov_character: str = ""
    """Current chapter POV character for local POV intrusion prescreen."""
    scene_intents: list[dict[str, Any]] = field(default_factory=list)
    """Planned scene POV metadata for marker/ambiguity checks."""
    known_characters: list[str] = field(default_factory=list)
    """Known character names for local POV intrusion prescreen."""
    kernel_context: dict[str, Any] | None = None
    """Optional StoryKernel field slice from ContextComposer.

    When provided, the fields are merged into the LLM context as
    ``kernel_context``, giving the model access to entities, relationships,
    timeline, world_rules, knowledge_ledger, object_ledger, promise_ledger,
    and banned_phrases from the unified field pool.
    """


class ChapterRepairStep(PipelineStep[ChapterRepairInput, ChapterRepairReport]):
    """LLM-based single-chapter correctness check."""

    _RISK_ALIAS = {
        "low": "low",
        "minor": "low",
        "safe": "low",
        "medium": "medium",
        "mid": "medium",
        "moderate": "medium",
        "high": "high",
        "severe": "high",
        "critical": "high",
    }

    @property
    def step_name(self) -> str:
        return "check_chapter"

    @staticmethod
    def _detect_resurrection_risks(
        chapter_text: str,
        canon_context: Any,
    ) -> list[str]:
        risks: list[str] = []
        characters = getattr(canon_context, "characters", None)
        if not isinstance(characters, dict):
            characters = (canon_context or {}).get("characters", {})
        if not characters:
            return risks
        for name, state in characters.items():
            if not state:
                continue
            alive = getattr(state, "alive", True)
            if isinstance(state, dict):
                alive = state.get("alive", True)
            if not alive and name and len(name) >= 2:
                if name in chapter_text:
                    risks.append(name)
        return risks

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        source = str(text or "").strip()
        if not source:
            return []
        return [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]

    @staticmethod
    def _clip_excerpt(text: str, max_chars: int = 420) -> str:
        cleaned = str(text or "").strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip("，。、；： \n") + "…"

    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = clean_str(value)
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    @classmethod
    def _clean_list(cls, values: Any) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            return []
        return cls._dedupe([str(value or "").strip() for value in values])

    @staticmethod
    def _context_excerpt(text: str, needle: str, *, radius: int = 48) -> str:
        if not text or not needle:
            return ""
        pos = text.find(needle)
        if pos < 0:
            compact = re.sub(r"\s+", "", text)
            pos = compact.find(needle)
            if pos < 0:
                return text[: max(radius * 2, 80)].strip()
            start = max(0, pos - radius)
            end = min(len(compact), pos + len(needle) + radius)
            return compact[start:end]
        start = max(0, pos - radius)
        end = min(len(text), pos + len(needle) + radius)
        return text[start:end].strip()

    @classmethod
    def _detect_pov_marker_issues(
        cls,
        text: str,
        input_data: ChapterRepairInput,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        scenes = [item for item in list(input_data.scene_intents or []) if isinstance(item, dict)]
        povs = {
            clean_str(item.get("pov_character"))
            for item in scenes
            if clean_str(item.get("pov_character"))
        }
        marker_required = any(
            bool(item.get("pov_switch_marker_required")) or bool(item.get("pov_switch_allowed"))
            for item in scenes
        )
        has_marker = bool(re.search(r"\n\s*---+\s*\n", text))
        if (len(povs) > 1 or marker_required) and not has_marker:
            issues.append(
                {
                    "issue_type": "pov_switch_marker_missing",
                    "summary": "计划中存在多 POV 或要求视角切换标记，但正文未使用分隔符。",
                    "evidence": "缺少独立行分隔符 ---",
                    "confidence": 0.78,
                }
            )
        segments = re.split(r"\n\s*---+\s*\n", text)
        for index, segment in enumerate(segments[1:], start=2):
            first_line = next((line.strip() for line in segment.splitlines() if line.strip()), "")
            if re.match(r"^[他她它](?:[，。；：、\s]|$)", first_line):
                issues.append(
                    {
                        "issue_type": "pov_pronoun_opening_ambiguity",
                        "summary": "POV 切换后首句用代词开场，当前视角角色不明确。",
                        "evidence": f"第{index}段切换后首句：{first_line[:80]}",
                        "confidence": 0.72,
                    }
                )
        return issues

    @classmethod
    def _build_forbidden_candidates(
        cls,
        text: str,
        hits: list[tuple[str, str]],
        *,
        level: str,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for forbidden, matched in hits:
            forbidden_text = clean_str(forbidden)
            matched_text = clean_str(matched)
            if not forbidden_text or not matched_text:
                continue
            key = (level, forbidden_text, matched_text)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "level": level,
                    "forbidden": forbidden_text,
                    "matched": matched_text,
                    "match_type": "exact" if forbidden_text == matched_text else "variant",
                    "context_excerpt": cls._context_excerpt(text, matched_text),
                }
            )
        return candidates

    @classmethod
    def _scope_canon_context(cls, canon_context: Any) -> dict[str, Any]:
        raw_characters = extract_field(canon_context, "characters", {}) or {}
        characters: dict[str, dict[str, Any]] = {}
        if not isinstance(raw_characters, dict):
            return {"characters": characters}
        for raw_name, state in raw_characters.items():
            name = clean_str(raw_name)
            if not name:
                continue
            characters[name] = {
                "alive": bool(extract_field(state, "alive", True)),
                "location": cls._clip_excerpt(clean_str(extract_field(state, "location")), 80),
                "emotional_state": cls._clip_excerpt(
                    clean_str(extract_field(state, "emotional_state")),
                    80,
                ),
            }
        return {"characters": characters}

    @classmethod
    def _scope_character_profiles(
        cls,
        character_profiles: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        scoped: list[dict[str, str]] = []
        for profile in character_profiles:
            name = clean_str(extract_field(profile, "name"))
            if not name:
                continue
            scoped.append(
                {
                    "name": name,
                    "role": clean_str(extract_field(profile, "role")),
                    "personality": clean_str(extract_field(profile, "personality")),
                    "backstory": clean_str(extract_field(profile, "backstory")),
                }
            )
        return scoped

    @classmethod
    def _scope_creative_contract(cls, scene_intents: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Return a compact scene-level creative contract for CHECK_CHAPTER.

        CHECK_CHAPTER should be able to verify whether DRAFT's scene craft
        targets landed in prose, but it must not receive the full raw plan.
        Keep only the three downstream-verifiable creative targets plus a
        small scene locator.
        """
        scoped: list[dict[str, str]] = []
        for index, scene in enumerate(list(scene_intents or []), start=1):
            emotional_beat = clean_str(extract_field(scene, "emotional_beat"))
            sensory_focus = clean_str(extract_field(scene, "sensory_focus"))
            dialogue_subtext = clean_str(extract_field(scene, "dialogue_subtext"))
            if not (emotional_beat or sensory_focus or dialogue_subtext):
                continue
            scene_id = clean_str(extract_field(scene, "scene_id")) or f"scene_{index:02d}"
            scoped.append(
                {
                    "scene_id": scene_id,
                    "summary": clean_str(extract_field(scene, "summary")),
                    "emotional_beat": emotional_beat,
                    "sensory_focus": sensory_focus,
                    "dialogue_subtext": dialogue_subtext,
                }
            )
        return scoped

    @classmethod
    def _known_character_names(cls, input_data: ChapterRepairInput) -> list[str]:
        names = list(input_data.known_characters or [])
        for profile in input_data.character_profiles:
            name = clean_str(extract_field(profile, "name"))
            if name:
                names.append(name)
        raw_characters = extract_field(input_data.canon_context, "characters", {}) or {}
        if isinstance(raw_characters, dict):
            names.extend(str(name or "").strip() for name in raw_characters)
        if input_data.pov_character:
            names.append(input_data.pov_character)
        return cls._dedupe(names)

    @classmethod
    def _pov_scope(cls, input_data: ChapterRepairInput) -> str:
        pov = clean_str(input_data.pov_character)
        first_scope = ""
        for scene in list(input_data.scene_intents or []):
            scope = clean_str(extract_field(scene, "pov_scope"))
            if scope and not first_scope:
                first_scope = scope
            if pov and clean_str(extract_field(scene, "pov_character")) == pov and scope:
                return scope
        return first_scope or "limited"

    @classmethod
    def _detect_exact_sentence_repetition(
        cls,
        text: str,
        *,
        min_chars: int = 14,
    ) -> list[dict[str, Any]]:
        source = clean_str(text)
        if not source:
            return []
        seen: dict[str, int] = {}
        for sentence in re.split(r"[。！？!?；;]\s*", source):
            normalized = "".join(sentence.split())
            if len(normalized) < min_chars:
                continue
            seen[normalized] = seen.get(normalized, 0) + 1
        findings = [
            {"sentence": sentence[:80], "count": count}
            for sentence, count in seen.items()
            if count >= 2
        ]
        findings.sort(key=lambda item: int(str(item.get("count", 0) or 0)), reverse=True)
        return findings[:3]

    @classmethod
    def _build_local_quality_payload(cls, input_data: ChapterRepairInput) -> dict[str, Any]:
        text = input_data.chapter_text
        factual_errors: list[str] = []
        expression_errors: list[str] = []
        repair_actions: list[str] = []
        advisory_issues: list[dict[str, Any]] = []
        confirmed_issues: list[dict[str, Any]] = []

        invalid_markers = detect_invalid_time_markers(text)
        if invalid_markers:
            evidence = "；".join(
                f"行{item['line_number']}:{item['marker']}（{item['reason']}）"
                for item in invalid_markers[:4]
            )
            message = f"正文存在非法时辰刻度表达：{evidence}"
            factual_errors.append(message)
            repair_actions.append("将非法时辰/刻度改为项目允许的时间表达，或改为更笼统的时间锚点。")
            confirmed_issues.append({"issue_type": "time_marker_invalid", "summary": message})

        repeated = cls._detect_exact_sentence_repetition(text)
        if repeated:
            evidence = "；".join(f"重复{item['count']}次：{item['sentence']}" for item in repeated)
            message = f"正文出现整句级重复：{evidence}"
            expression_errors.append(message)
            repair_actions.append("删除或改写重复句，保留一次信息表达并补充新的动作/细节。")
            confirmed_issues.append({"issue_type": "text_repetition", "summary": message})

        intentional = set(cls._clean_list(input_data.intentional_callbacks))
        hard = [
            item
            for item in cls._clean_list(input_data.forbidden_elements)
            if item not in intentional
        ]
        soft = [
            item
            for item in cls._clean_list(input_data.forbidden_elements_soft)
            if item not in intentional and item not in hard
        ]
        hard_hits = _detect_forbidden_elements(text, hard) if hard else []
        soft_hits = _detect_forbidden_elements(text, soft) if soft else []
        forbidden_candidates = [
            *cls._build_forbidden_candidates(text, hard_hits, level="hard"),
            *cls._build_forbidden_candidates(text, soft_hits, level="soft"),
        ]
        expression_hits = []
        if input_data.expression_channel_detection_enabled:
            expression_hits = [
                *detect_expression_channel_hits(text, input_data.expression_channel_records),
            ]
        if expression_hits:
            for hit in expression_hits[:6]:
                label = clean_str(hit.get("reason")) or clean_str(hit.get("text"))
                matched = "、".join(cls._to_string_list(hit.get("matched", []))[:4])
                count = int(hit.get("count", 0) or 0)
                message = f"表达通道候选：{label}" + (
                    f"；命中{count}次：{matched}" if matched else f"；命中{count}次"
                )
                advisory_issues.append(
                    {
                        "issue_type": "expression_channel_candidate",
                        "summary": message,
                        "channel": hit.get("channel"),
                        "channel_id": hit.get("channel_id"),
                        "evidence": matched,
                        "replacement_axes": list(hit.get("replacement_axes", []) or [])[:4],
                        "allowed_when": hit.get("allowed_when"),
                        "confidence": 0.62,
                        "llm_review_required": True,
                    }
                )

        known_characters = cls._known_character_names(input_data)
        if input_data.pov_character and known_characters:
            intrusions = detect_pov_intrusion(
                text,
                input_data.pov_character,
                known_characters,
                pov_scope=cls._pov_scope(input_data),
            )
            if intrusions:
                intrusions_sorted = sorted(
                    intrusions,
                    key=lambda item: float(item.get("confidence", 0.0)),
                    reverse=True,
                )
                evidence = "；".join(
                    f"{item['character']}（行{item['line_number']}）：{item['evidence']}"
                    for item in intrusions_sorted[:5]
                )
                advisory_issues.append(
                    {
                        "issue_type": "pov_intrusion",
                        "summary": "疑似限制视角下出现非 POV 角色内心活动直写。",
                        "evidence": evidence,
                        "confidence": max(
                            float(item.get("confidence", 0.0)) for item in intrusions_sorted
                        ),
                    }
                )

        for issue in cls._detect_pov_marker_issues(text, input_data):
            advisory_issues.append(issue)

        return {
            "confirmed_issues": confirmed_issues,
            "advisory_issues": advisory_issues,
            "factual_errors": cls._dedupe(factual_errors),
            "expression_errors": cls._dedupe(expression_errors),
            "repair_actions": cls._dedupe(repair_actions),
            "forbidden_element_candidates": forbidden_candidates,
            "expression_channel_hits": expression_hits,
        }

    @classmethod
    def _build_llm_context(
        cls,
        input_data: ChapterRepairInput,
        *,
        check_mode: str,
        local_prompt_leaks: list[str] | None = None,
        local_resurrection_risks: list[str] | None = None,
        local_quality_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "chapter_text": input_data.chapter_text,
            "canon_context": cls._scope_canon_context(input_data.canon_context),
            "character_profiles": cls._scope_character_profiles(input_data.character_profiles),
            "known_prompt_markers": input_data.known_prompt_markers,
            "check_mode": check_mode,
            "previous_report": input_data.previous_report,
            "changed_sections": input_data.changed_sections,
            "change_ratio": input_data.change_ratio,
            "address_rules": input_data.address_rules,
            "world_context_rules": input_data.world_context_rules,
            "world_rule_card": input_data.world_rule_card or {},
        }
        if check_mode == "full":
            creative_contract = cls._scope_creative_contract(input_data.scene_intents)
            if creative_contract:
                context["creative_contract"] = creative_contract
        kernel_context = getattr(input_data, "kernel_context", None)
        if kernel_context:
            context["kernel_context"] = kernel_context
        if local_prompt_leaks:
            context["local_detected_prompt_leaks"] = local_prompt_leaks
            context["local_check_note"] = (
                "以上是本地正则检测到的 prompt leak 标记（置信度 95%），"
                "请结合正文验证并指出具体位置。"
            )
        if local_resurrection_risks:
            context["local_detected_resurrection_risks"] = local_resurrection_risks
            context["local_resurrection_note"] = (
                "以下角色在正史中已死亡，但在正文中出现了名字。"
                "请判断是正常提及（回忆、证据、他人转述）还是错误复活。"
                "若属错误复活，记入 factual_errors。"
            )
        if local_quality_payload:
            detected_quality = [
                *list(local_quality_payload.get("confirmed_issues", []) or []),
                *list(local_quality_payload.get("advisory_issues", []) or []),
            ]
            if detected_quality:
                context["local_detected_quality_issues"] = detected_quality
                context["local_quality_note"] = (
                    "以下是本地质量预筛选结果。time_marker / 文本级重复可作为较高置信度证据；"
                    "POV 与表达通道候选必须结合正文语义复核后再写入对应错误字段。"
                )
            forbidden_candidates = list(
                local_quality_payload.get("forbidden_element_candidates", []) or []
            )
            if forbidden_candidates:
                context["local_forbidden_element_candidates"] = forbidden_candidates
                context["local_forbidden_note"] = (
                    "以下只是本地字符串召回的禁用元素候选，不等于违规。请根据语义判断它是"
                    "机械复用、可接受回环、剧情锚点还是无害出现；只有确认损害表达质量时"
                    "才写入 expression_errors。"
                )
            expression_hits = list(local_quality_payload.get("expression_channel_hits", []) or [])
            if expression_hits:
                context["local_expression_channel_hits"] = expression_hits
                context["local_expression_channel_note"] = (
                    "以下只是本地表达通道候选，不等于违规。请结合角色、POV、"
                    "章节语义和表达必要性独立判断；若确认重复，再要求改换动作、"
                    "对白、观察角度或决策过程，避免同义词替换。"
                )
        return context

    @classmethod
    def build_delta_recheck_payload(
        cls,
        previous_text: str,
        revised_text: str,
        *,
        max_sections: int = 5,
        max_total_chars: int = 2600,
    ) -> tuple[str, list[dict[str, Any]], float]:
        """Choose between delta/full recheck and extract changed paragraph sections.

        Delta recheck is only safe when the continuity repair touched a limited
        portion of the chapter. For broad rewrites we fall back to a full scan.
        """
        previous_paragraphs = cls._split_paragraphs(previous_text)
        revised_paragraphs = cls._split_paragraphs(revised_text)
        if not previous_paragraphs or not revised_paragraphs:
            return "full", [], 0.0

        text_ratio = round(
            float(difflib.SequenceMatcher(None, previous_text, revised_text).ratio()), 4
        )
        if text_ratio < 0.75:
            return "full", [], text_ratio

        matcher = difflib.SequenceMatcher(None, previous_paragraphs, revised_paragraphs)

        changed_sections: list[dict[str, Any]] = []
        changed_paragraphs = 0
        total_chars = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            before_text = "\n\n".join(previous_paragraphs[i1:i2]).strip()
            after_text = "\n\n".join(revised_paragraphs[j1:j2]).strip()
            if not before_text and not after_text:
                continue

            changed_paragraphs += max(i2 - i1, j2 - j1)
            if len(changed_sections) >= max_sections:
                return "full", [], text_ratio

            before_excerpt = cls._clip_excerpt(before_text)
            after_excerpt = cls._clip_excerpt(after_text)
            section = {
                "change_type": tag,
                "start_paragraph": j1 + 1 if j2 > j1 else max(1, i1 + 1),
                "end_paragraph": max(j2, j1 + 1) if j2 > j1 else max(i2, i1 + 1),
                "before": before_excerpt,
                "after": after_excerpt,
            }
            total_chars += len(before_excerpt) + len(after_excerpt)
            if total_chars > max_total_chars:
                return "full", [], text_ratio
            changed_sections.append(section)

        if not changed_sections:
            return "full", [], text_ratio

        paragraph_limit = max(8, int(len(revised_paragraphs) * 0.35))
        if changed_paragraphs > paragraph_limit:
            return "full", [], text_ratio

        return "delta", changed_sections, text_ratio

    @classmethod
    def _coerce_repr_dict_to_string(cls, item: Any) -> str | None:
        """Recover structured data from a Python repr-of-dict string.

        Some LLMs serialize their list items as ``"{'text': ..., 'issue': ...}"``
        instead of returning the list element as an actual dict. Without
        normalization the string flows into QualityGate's substring filters as
        a single opaque blob, which can either false-positive or false-negative
        depending on which keys happen to contain the filter tokens. We parse
        the repr back into a dict and prefer the most descriptive field
        (``issue`` > ``text`` > ``description`` > ``summary`` > ``action`` >
        first string value) so downstream substring matching operates on the
        actual issue text.
        """
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not (text.startswith("{") and text.endswith("}")):
            return None
        if len(text) > 4096:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (MemoryError, RecursionError, SyntaxError, TypeError, ValueError):
                return None
        except (MemoryError, RecursionError):
            return None
        if not isinstance(parsed, dict):
            return None
        for key in ("issue", "text", "description", "summary", "action"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in parsed.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _to_string_list(cls, value: Any) -> list[str]:
        items: list[str] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for raw_key, raw_val in item.items():
                        key = clean_str(raw_key)
                        val = clean_str(raw_val)
                        if key and val:
                            items.append(f"{key}: {val}")
                        elif key:
                            items.append(key)
                        elif val:
                            items.append(val)
                    continue
                text = clean_str(item)
                if not text:
                    continue
                recovered = cls._coerce_repr_dict_to_string(text)
                items.append(recovered or text)
        elif isinstance(value, dict):
            for raw_key, raw_val in value.items():
                key = clean_str(raw_key)
                val = clean_str(raw_val)
                if key and val:
                    items.append(f"{key}: {val}")
                elif key:
                    items.append(key)
                elif val:
                    items.append(val)
        else:
            text = clean_str(value)
            if text:
                recovered = cls._coerce_repr_dict_to_string(text)
                items.append(recovered or text)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @classmethod
    def _to_issue_list(
        cls,
        value: Any,
        *,
        default_issue_type: str,
        default_severity: str,
        default_fix_suggestion: str,
    ) -> list[ChapterIssue]:
        """Normalize one checker finding into exactly one typed issue.

        ``CHECK_CHAPTER`` models sometimes return a rich object containing
        ``type``, ``description``, ``evidence`` and ``severity``.  Treating its
        keys as independent strings used to turn that single diagnosis into five
        high-priority repair tickets.  The boundary is now atomic: one list item
        stays one issue throughout review and repair.
        """

        raw_items = value if isinstance(value, list) else [value]
        issues: list[ChapterIssue] = []
        seen: set[tuple[str, str, str]] = set()
        allowed_severities = {"critical", "high", "medium", "low"}
        for item in raw_items:
            if isinstance(item, ChapterIssue):
                issue = item
            elif isinstance(item, dict):
                issue_type = clean_str(
                    item.get("issue_type") or item.get("type") or default_issue_type
                )
                summary = clean_str(
                    item.get("summary")
                    or item.get("description")
                    or item.get("issue")
                    or item.get("message")
                )
                evidence = clean_str(item.get("evidence") or item.get("quote") or summary)
                severity = clean_str(item.get("severity")).lower() or default_severity
                issue = ChapterIssue(
                    issue_type=issue_type or default_issue_type,
                    severity=severity if severity in allowed_severities else default_severity,
                    summary=summary or evidence,
                    evidence=evidence,
                    fix_suggestion=clean_str(
                        item.get("fix_suggestion")
                        or item.get("repair_suggestion")
                        or item.get("suggestion")
                        or default_fix_suggestion
                    ),
                    location=clean_str(item.get("location") or item.get("paragraph_hint")),
                    paragraph_start=cls._coerce_nonnegative_int(item.get("paragraph_start")),
                    paragraph_end=cls._coerce_nonnegative_int(item.get("paragraph_end")),
                )
            else:
                text = clean_str(item)
                if not text:
                    continue
                recovered = cls._coerce_repr_dict_to_string(text)
                issue = ChapterIssue(
                    issue_type=default_issue_type,
                    severity=default_severity,
                    summary=recovered or text,
                    evidence=recovered or text,
                    fix_suggestion=default_fix_suggestion,
                )
            if not issue.summary:
                continue
            key = (issue.issue_type, issue.summary, issue.evidence)
            if key not in seen:
                seen.add(key)
                issues.append(issue)
        return issues

    @staticmethod
    def _coerce_nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _normalize_risk_level(cls, value: Any, issue_count: int) -> str:
        lowered = clean_str(value).lower()
        risk = cls._RISK_ALIAS.get(lowered)
        derived = "low"
        if issue_count >= 5:
            derived = "high"
        elif issue_count >= 2:
            derived = "medium"
        if not risk:
            return derived
        rank = {"low": 0, "medium": 1, "high": 2}
        return risk if rank[risk] >= rank[derived] else derived

    @classmethod
    def _normalize_forbidden_candidates(cls, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        candidates: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            candidate = {
                "level": clean_str(item.get("level")) or "hard",
                "forbidden": clean_str(item.get("forbidden")),
                "matched": clean_str(item.get("matched")),
                "match_type": clean_str(item.get("match_type")) or "exact",
                "context_excerpt": clean_str(item.get("context_excerpt")),
            }
            if candidate["forbidden"] and candidate["matched"]:
                candidates.append(candidate)
        return candidates

    @classmethod
    def _forbidden_evidence_verified(cls, finding: dict[str, Any], chapter_text: str) -> bool:
        text = str(chapter_text or "")
        if not text:
            return False
        for key in ("matched", "context_excerpt", "forbidden"):
            needle = clean_str(finding.get(key))
            if needle and needle in text:
                return True
        return False

    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _normalize_forbidden_findings(
        cls,
        raw: Any,
        *,
        chapter_text: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        findings: list[dict[str, Any]] = []
        valid_verdicts = {
            "violation",
            "mechanical_reuse",
            "acceptable_callback",
            "story_anchor",
            "benign",
            "uncertain",
        }
        valid_severities = {"none", "low", "medium", "high", "critical"}
        for item in raw:
            if not isinstance(item, dict):
                continue
            verdict = clean_str(item.get("verdict")).lower()
            severity = clean_str(item.get("severity")).lower()
            normalized_verdict = verdict if verdict in valid_verdicts else "uncertain"
            normalized_severity = severity if severity in valid_severities else "low"
            finding = {
                "forbidden": clean_str(item.get("forbidden")),
                "matched": clean_str(item.get("matched")),
                "context_excerpt": clean_str(item.get("context_excerpt")),
                "verdict": normalized_verdict,
                "severity": normalized_severity,
                "blocking": bool(item.get("blocking", False)),
                "confidence": cls._normalize_confidence(item.get("confidence")),
                "reason": clean_str(item.get("reason")),
                "replacement_advice": clean_str(item.get("replacement_advice")),
            }
            if finding["forbidden"] or finding["matched"]:
                evidence_verified = cls._forbidden_evidence_verified(finding, chapter_text)
                finding["evidence_verified"] = evidence_verified
                if not evidence_verified and finding["verdict"] in {
                    "violation",
                    "mechanical_reuse",
                }:
                    finding["original_verdict"] = finding["verdict"]
                    finding["original_severity"] = finding["severity"]
                    finding["verdict"] = "uncertain"
                    finding["severity"] = "low"
                    finding["blocking"] = False
                    reason = clean_str(finding.get("reason"))
                    suffix = "未在正文中核验到该禁用元素证据，暂不计入质量惩罚。"
                    finding["reason"] = f"{reason}；{suffix}" if reason else suffix
                findings.append(finding)
        return findings

    @classmethod
    def _forbidden_findings_to_expression_errors(
        cls,
        findings: list[dict[str, Any]],
    ) -> list[str]:
        errors: list[str] = []
        for finding in findings:
            verdict = clean_str(finding.get("verdict")).lower()
            severity = clean_str(finding.get("severity")).lower()
            if verdict not in {"violation", "mechanical_reuse"}:
                continue
            if not bool(finding.get("evidence_verified", False)):
                continue
            if severity in {"none", "low"} and not bool(finding.get("blocking")):
                continue
            matched = clean_str(finding.get("matched"))
            forbidden = clean_str(finding.get("forbidden"))
            reason = clean_str(finding.get("reason"))
            label = "禁用元素语义违规" if verdict == "violation" else "禁用元素机械复用"
            detail = f"{label}：'{forbidden or matched}'"
            if matched and matched != forbidden:
                detail += f"→正文出现'{matched}'"
            if reason:
                detail += f"；{reason}"
            errors.append(detail)
        return cls._dedupe(errors)

    @classmethod
    def _normalize_payload(
        cls,
        raw: Any,
        local_prompt_leaks: list[str] | None = None,
        local_factual_errors: list[str] | None = None,
        local_expression_errors: list[str] | None = None,
        local_repair_actions: list[str] | None = None,
        local_forbidden_candidates: list[dict[str, Any]] | None = None,
        chapter_text: str = "",
        chapter_number: int = 0,
        check_mode: str = "full",
        confidence_threshold: float = 0.7,
    ) -> ChapterRepairReport:
        payload = raw if isinstance(raw, dict) else {}
        # Start with locally verified prompt leaks — these are ground truth.
        prompt_leaks: list[str] = list(local_prompt_leaks or [])
        # Only keep LLM-reported prompt leaks that can be verified in the text.
        # The LLM frequently hallucinates leaks that don't actually exist.
        for item in cls._to_string_list(payload.get("prompt_leaks", [])):
            if item not in prompt_leaks and item in chapter_text:
                prompt_leaks.append(item)
        llm_factual_errors = payload.get("factual_errors", [])
        factual_errors = cls._to_issue_list(
            [
                *(local_factual_errors or []),
                *(
                    llm_factual_errors
                    if isinstance(llm_factual_errors, list)
                    else [llm_factual_errors]
                ),
            ],
            default_issue_type="factual_error",
            default_severity="medium",
            default_fix_suggestion="修正章内事实、时间或状态错误，并保持既有剧情结果不变。",
        )
        continuity_errors = cls._to_issue_list(
            payload.get("continuity_errors", []),
            default_issue_type="chapter_continuity_error",
            default_severity="medium",
            default_fix_suggestion="修正文内前后不一致处，不扩大到跨章重写。",
        )
        llm_expression_errors = payload.get("expression_errors", [])
        expression_errors = cls._to_issue_list(
            [
                *(local_expression_errors or []),
                *(
                    llm_expression_errors
                    if isinstance(llm_expression_errors, list)
                    else [llm_expression_errors]
                ),
            ],
            default_issue_type="expression_clarity",
            default_severity="medium",
            default_fix_suggestion="用局部改写修复章内表达问题，避免改动已通过的主线结果。",
        )
        forbidden_candidates = cls._normalize_forbidden_candidates(
            payload.get("forbidden_element_candidates", local_forbidden_candidates or [])
        )
        forbidden_findings = cls._normalize_forbidden_findings(
            payload.get("forbidden_element_findings", []),
            chapter_text=chapter_text,
        )
        expression_errors = cls._to_issue_list(
            [
                *expression_errors,
                *cls._forbidden_findings_to_expression_errors(forbidden_findings),
            ],
            default_issue_type="expression_clarity",
            default_severity="medium",
            default_fix_suggestion="用局部改写修复章内表达问题，避免改动已通过的主线结果。",
        )
        repair_actions = cls._dedupe(
            [*(local_repair_actions or []), *cls._to_string_list(payload.get("repair_actions", []))]
        )
        for finding in forbidden_findings:
            advice = clean_str(finding.get("replacement_advice"))
            if advice:
                repair_actions.append(advice)
        repair_actions = cls._dedupe(repair_actions)
        summary = clean_str(payload.get("summary"))

        issue_count = (
            len(prompt_leaks)
            + len(factual_errors)
            + len(continuity_errors)
            + len(expression_errors)
        )
        risk_level = cls._normalize_risk_level(payload.get("risk_level"), issue_count)
        if not summary:
            if issue_count == 0:
                summary = "本章未发现明显错误或提示泄露。"
            else:
                summary = f"本章发现 {issue_count} 处需要修复的风险点。"

        review_mode = normalize_review_mode(check_mode)
        text_hash = source_text_hash(chapter_text)
        report = ChapterRepairReport(
            review_mode=review_mode,
            risk_level=risk_level,
            summary=summary,
            prompt_leaks=prompt_leaks,
            factual_errors=factual_errors,
            continuity_errors=continuity_errors,
            expression_errors=expression_errors,
            repair_actions=repair_actions,
            forbidden_element_candidates=forbidden_candidates,
            forbidden_element_findings=forbidden_findings,
            source_text_hash=text_hash,
        )
        findings = chapter_repair_report_to_findings(
            report,
            chapter_number=chapter_number,
            current_text_hash=text_hash,
            review_mode=review_mode,
            review_round=1 if review_mode == "full_review" else 2,
        )
        findings, readiness = prepare_findings_for_repair(
            findings,
            current_text=chapter_text,
            completed_chapters=[chapter_number],
        )
        return report.model_copy(
            update={
                "review_findings": findings,
                "repair_tickets": compile_repair_tickets_from_findings(
                    findings,
                    require_auto_repair_eligible=True,
                ),
                "repair_readiness": readiness,
            }
        )

    async def _execute(self, input_data: ChapterRepairInput) -> ChapterRepairReport:
        settings = self.settings
        use_local_as_prescreen = getattr(settings, "local_check_as_prescreen", True)
        confidence_threshold = getattr(settings, "local_check_confidence_threshold", 0.7)

        check_mode = input_data.check_mode if input_data.check_mode in {"full", "delta"} else "full"
        max_tokens = self._dynamic_max_tokens(
            TaskType.CHECK_CHAPTER,
            max(1800, len(input_data.chapter_text) // (3 if check_mode == "delta" else 2)),
            prompt_overhead=2600 if check_mode == "delta" else 3400,
            min_tokens=2048 if check_mode == "delta" else 4096,
        )

        local_prompt_leaks: list[str] = []
        local_resurrection_risks: list[str] = []
        local_quality_payload: dict[str, Any] = {}
        if use_local_as_prescreen:
            local_prompt_leaks = detect_prompt_leaks(
                input_data.chapter_text,
                extra_markers=input_data.known_prompt_markers,
            )
            local_resurrection_risks = self._detect_resurrection_risks(
                input_data.chapter_text,
                input_data.canon_context,
            )
            local_quality_payload = self._build_local_quality_payload(input_data)

        llm_context = self._build_llm_context(
            input_data,
            check_mode=check_mode,
            local_prompt_leaks=local_prompt_leaks if use_local_as_prescreen else None,
            local_resurrection_risks=local_resurrection_risks if use_local_as_prescreen else None,
            local_quality_payload=local_quality_payload if use_local_as_prescreen else None,
        )

        data = await self._call_with_retry(
            TaskType.CHECK_CHAPTER,
            llm_context,
            max_tokens=max_tokens,
            temperature=self.settings.temp_check_chapter,
        )

        if not use_local_as_prescreen:
            local_prompt_leaks = []

        return self._normalize_payload(
            data,
            local_prompt_leaks,
            local_factual_errors=local_quality_payload.get("factual_errors", []),
            local_expression_errors=local_quality_payload.get("expression_errors", []),
            local_repair_actions=local_quality_payload.get("repair_actions", []),
            local_forbidden_candidates=local_quality_payload.get(
                "forbidden_element_candidates", []
            ),
            chapter_text=input_data.chapter_text,
            chapter_number=input_data.chapter_number,
            check_mode=check_mode,
            confidence_threshold=confidence_threshold,
        )

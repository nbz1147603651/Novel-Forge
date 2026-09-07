"""Local heuristic checks."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.domain.guardrails import (
    text_has_custody_signal,
    text_has_release_signal,
    text_has_transition_signal,
)
from novel_forge.core.utils.boundary_windows import (
    DEFAULT_OPENING_PARAGRAPHS,
    coerce_paragraph_count,
    paragraph_range_label,
    take_head_paragraphs,
)
from novel_forge.core.utils.string import carry_forward_status, carry_forward_text, clean_str
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.continuity_eval.normalizer import _IssueNormalizer

_log = get_logger(__name__)


class _LocalChecks(_IssueNormalizer):
    _META_INSTRUCTION_RE = re.compile(
        r"(?:必须在[下后]一章|需在[下后]一章|需在后续|应在[下后]一章|应在后续"
        r"|下一章[中里]?(?:得到|进行|解释|揭示|展开|交代|明确)"
        r"|后续(?:章节)?(?:中|里)?(?:展开|揭示|解释|解决|交代|明确)"
        r"|尚未(?:揭露|解决|明确|交代|说明|回应|出现|展开|提及|解释)"
        r"|(?:未|没有|无法|不得|不会)(?:揭露|解决|明确|交代|说明|被压制|被解决|被揭露)"
        r"|伏笔|暗示他|动机不明|原因不明|持续关注|仍在调查|仍未解决"
        r")"
    )
    _CARRY_FORWARD_SPLIT_RE = re.compile(
        r"""[，。、；：（）【】"'《》〈〉<>·\s]|"""
        r"(?:已经|仍然|正在|以及|并且|同时|然后|之后|如果|由于|因为|为了|通过|有关|相关"
        r"|确认|发现|显示|提到|出现|收到|获取|获得|完成|保留|存入|设下|切断|进入|展开"
        r"|推进|开始|继续|选择|认为|决定|来自|标记为|与|和|并|且|在|于|向|将|把|被"
        r"|由|从|对|为|了|的)"
    )
    _SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;]\s*")
    _OPENING_HARD_HOOK_CHARS = 1200
    _CARRY_FORWARD_ATOM_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,}")
    _CARRY_FORWARD_ATOM_STOPWORDS = {
        "一个",
        "一种",
        "一些",
        "这个",
        "那个",
        "这些",
        "那些",
        "之间",
        "已经",
        "仍然",
        "正在",
        "形成",
        "成为",
        "继续",
        "必须",
        "需要",
        "尚未",
        "没有",
        "无法",
        "不是",
        "完整",
        "明确",
        "显式",
        "回应",
        "承接",
        "落地",
        "状态",
        "开放",
        "事项",
        "隐性",
        "约定",
    }

    @classmethod
    def _is_meta_instruction(cls, item: str) -> bool:
        return bool(cls._META_INSTRUCTION_RE.search(item))

    @classmethod
    def _extract_carry_forward_cues(cls, item: str) -> list[str]:
        source = clean_str(item)
        if not source:
            return []
        cues: list[str] = []
        for quoted in re.findall(r"[\"'《〈](.*?)[\"'》〉]", source):
            text = quoted.strip()
            if 2 <= len(text) <= 24:
                cues.append(text)
        normalized = cls._META_INSTRUCTION_RE.sub(" ", source)
        for part in cls._CARRY_FORWARD_SPLIT_RE.split(normalized):
            text = part.strip()
            if not text or len(text) < 2 or len(text) > 24:
                continue
            if text.isdigit():
                continue
            cues.append(text)
        deduped: list[str] = []
        seen: set[str] = set()
        for cue in sorted(cues, key=len, reverse=True):
            if cue in seen:
                continue
            seen.add(cue)
            deduped.append(cue)
        return deduped

    @staticmethod
    def _cue_present_in_text(cue: str, text: str) -> bool:
        if not cue or not text:
            return False
        if cue in text:
            return True
        if len(cue) >= 8:
            hits = 0
            for i in range(len(cue) - 3):
                window = cue[i : i + 4]
                if window in text:
                    hits += 1
                    if hits >= 2:
                        return True
        return False

    @classmethod
    def _extract_carry_forward_atoms(cls, item: str) -> list[str]:
        source = clean_str(item)
        if not source:
            return []
        sources = [source]
        sources.extend(text.strip() for text in re.findall(r"[\"'《〈](.*?)[\"'》〉]", source))
        atoms: list[str] = []
        for raw_source in sources:
            normalized = cls._META_INSTRUCTION_RE.sub(" ", raw_source)
            for part in cls._CARRY_FORWARD_SPLIT_RE.split(normalized):
                text = part.strip()
                if not text:
                    continue
                for token in cls._CARRY_FORWARD_ATOM_RE.findall(text):
                    if len(token) <= 4:
                        atoms.append(token)
                        continue
                    atoms.append(token)
                    # Chinese carry-forward items often encode several semantic
                    # anchors without separators. Add short windows so
                    # paraphrased prose can still prove the thread landed.
                    for size in (4, 2):
                        for idx in range(len(token) - size + 1):
                            atoms.append(token[idx : idx + size])
        deduped: list[str] = []
        seen: set[str] = set()
        for atom in sorted(atoms, key=len, reverse=True):
            if atom in seen:
                continue
            if atom in cls._CARRY_FORWARD_ATOM_STOPWORDS:
                continue
            if atom.isdigit():
                continue
            seen.add(atom)
            deduped.append(atom)
        return deduped

    @classmethod
    def _carry_forward_atom_match(cls, item: str, text: str) -> tuple[int, int, list[str]]:
        atoms = cls._extract_carry_forward_atoms(item)
        if not atoms:
            return 0, 1, []
        raw_hits = [atom for atom in atoms if atom in text]
        hits: list[str] = []
        for atom in sorted(raw_hits, key=len, reverse=True):
            if any(atom != existing and atom in existing for existing in hits):
                continue
            hits.append(atom)
        item_len = len(clean_str(item))
        required = 2 if item_len <= 16 or len(atoms) <= 8 else 3
        if item_len >= 28 or len(atoms) >= 24:
            required = 4
        return len(hits), required, hits

    @classmethod
    def _carry_forward_item_present_in_text(cls, item: str, text: str) -> bool:
        if cls._item_present_in_text(item, text):
            return True
        matched, required, _hits = cls._carry_forward_atom_match(item, text)
        return matched >= required

    @staticmethod
    def _item_present_in_text(item: str, text: str) -> bool:
        if item in text:
            return True
        parts = re.split(r"[，。、；：（）【】——→↔\s]", item)
        if any(len(p) >= 4 and p in text for p in parts):
            return True
        cues = _LocalChecks._extract_carry_forward_cues(item)
        if cues:
            matched = sum(1 for cue in cues if _LocalChecks._cue_present_in_text(cue, text))
            required_matches = 1 if len(cues) <= 2 else 2
            if matched >= required_matches:
                return True
        if len(item) >= 8:
            hits = 0
            for i in range(len(item) - 3):
                window = item[i : i + 4]
                if window in text:
                    hits += 1
                    if hits >= 2:
                        return True
        return False

    @classmethod
    def _count_cue_hits(cls, cue_source: str, text: str) -> tuple[int, int]:
        cues = cls._extract_carry_forward_cues(cue_source)
        if not cues:
            return 0, 1
        matched = sum(1 for cue in cues if cls._cue_present_in_text(cue, text))
        required = 1 if len(cues) <= 2 else 2
        return matched, required

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
        findings: list[dict[str, Any]] = []
        for sentence in cls._SENTENCE_SPLIT_RE.split(source):
            normalized = "".join(sentence.split())
            if len(normalized) < min_chars:
                continue
            count = seen.get(normalized, 0) + 1
            seen[normalized] = count
        for sentence, count in seen.items():
            if count < 2:
                continue
            findings.append({"sentence": sentence[:80], "count": count})
        findings.sort(key=lambda item: int(item.get("count", 0)), reverse=True)
        return findings[:3]

    @classmethod
    def _build_local_issues(cls, input_data: Any) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        previous_exit = input_data.chapter_state_packet.previous_exit_state
        bridge = input_data.chapter_bridge
        opening_paragraphs = coerce_paragraph_count(
            getattr(input_data, "boundary_opening_paragraphs", DEFAULT_OPENING_PARAGRAPHS),
            default=DEFAULT_OPENING_PARAGRAPHS,
            maximum=8,
        )
        opening_label = paragraph_range_label(1, opening_paragraphs)
        opening_text = take_head_paragraphs(
            input_data.chapter_text,
            opening_paragraphs,
            max_chars=cls._OPENING_HARD_HOOK_CHARS,
        )
        previous_ending = input_data.chapter_state_packet.previous_chapter_ending

        if previous_exit is not None:
            location_changed = (
                previous_exit.location
                and bridge.opening_location
                and previous_exit.location != bridge.opening_location
            )
            if location_changed:
                handoff_has_transition = text_has_transition_signal(bridge.action_handoff)
                opening_has_transition = text_has_transition_signal(opening_text)
                transition_mode = str(getattr(bridge, "transition_mode", "") or "").lower()
                pov_changed = bool(
                    previous_exit.pov
                    and bridge.opening_pov
                    and previous_exit.pov != bridge.opening_pov
                )
                declared_pov_switch = (
                    transition_mode == "pov_switch"
                    or bool(getattr(input_data, "pov_switch", False))
                    or pov_changed
                )
                if declared_pov_switch and str(bridge.action_handoff or "").strip():
                    handoff_has_transition = True
                if not handoff_has_transition and not opening_has_transition:
                    issues.append(
                        {
                            "issue_type": "location_jump",
                            "severity": "high",
                            "confidence": 0.88,
                            "source": "local",
                            "repair_surface": "chapter_text",
                            "summary": "开场地点与上一章结尾地点不同，但缺少动作交接说明。",
                            "evidence": f"{previous_exit.location} -> {bridge.opening_location}；handoff_transition={handoff_has_transition}；opening_transition={opening_has_transition}",
                            "local_detection": True,
                            "affected_characters": [],
                            "rewrite_scope": "opening",
                            "fix_actions": [
                                "补一段角色如何从上一章场景过渡到本章场景的动作/心理桥接。"
                            ],
                        }
                    )
                has_custody = text_has_custody_signal(previous_ending)
                has_release = text_has_release_signal(bridge.action_handoff)
                opening_has_release = text_has_release_signal(opening_text)
                opening_has_custody_transition = text_has_transition_signal(opening_text)
                if (
                    has_custody
                    and not has_release
                    and not opening_has_release
                    and not opening_has_custody_transition
                ):
                    issues.append(
                        {
                            "issue_type": "custody_break",
                            "severity": "high",
                            "confidence": 0.9,
                            "source": "local",
                            "repair_surface": "chapter_text",
                            "summary": "上一章仍处于被押或受控状态，本章却直接换场，缺少获释、押解或传唤说明。",
                            "evidence": f"{previous_exit.location} -> {bridge.opening_location}",
                            "local_detection": True,
                            "affected_characters": [bridge.opening_pov]
                            if bridge.opening_pov
                            else [],
                            "rewrite_scope": "opening",
                            "fix_actions": ["先交代角色如何离开受控场景，再进入本章主行动。"],
                        }
                    )
            if (
                previous_exit.pov
                and bridge.opening_pov
                and previous_exit.pov != bridge.opening_pov
                and not bridge.transition_mode
                and not input_data.pov_switch
            ):
                issues.append(
                    {
                        "issue_type": "pov_jump",
                        "severity": "medium",
                        "confidence": 0.75,
                        "source": "local",
                        "repair_surface": "bridge_artifact",
                        "summary": "POV 切换缺少过渡模式说明。",
                        "evidence": f"{previous_exit.pov} -> {bridge.opening_pov}",
                        "local_detection": True,
                        "affected_characters": [previous_exit.pov, bridge.opening_pov],
                        "rewrite_scope": "opening",
                        "fix_actions": ["补 POV 切换提示，避免开头像硬切镜头。"],
                    }
                )

        missing_carry_forward = [
            carry_forward_text(item)
            for item in input_data.chapter_state_packet.must_carry_forward
            if carry_forward_text(item)
            and carry_forward_status(item) == "open"
            and not cls._is_meta_instruction(carry_forward_text(item))
            and not cls._carry_forward_item_present_in_text(
                carry_forward_text(item),
                opening_text,
            )
            and not cls._carry_forward_item_present_in_text(
                carry_forward_text(item),
                input_data.chapter_text,
            )
        ]
        if missing_carry_forward:
            issues.append(
                {
                    "issue_type": "carry_forward_missing",
                    "severity": "critical",
                    "confidence": 0.9,
                    "source": "local",
                    "repair_surface": "chapter_text",
                    "local_detection": True,
                    "summary": "上一章必须承接的开放项在本章正文与开场窗口中均未落地，跨章状态可能丢失。",
                    "evidence": "；".join(missing_carry_forward),
                    "location": f"{opening_label}（承接上一章遗留信息处）",
                    "paragraph_start": 1,
                    "paragraph_end": opening_paragraphs,
                    "fix_mode": "insert",
                    "insert_after_para": 1,
                    "affected_characters": [],
                    "rewrite_scope": "opening",
                    "fix_actions": ["在开头或关键转折处显式回应上一章遗留状态。"],
                }
            )

        bridge_handoff = clean_str(getattr(bridge, "action_handoff", "") if bridge else "")
        opening_window = opening_text
        if bridge_handoff and opening_window:
            matched, required = cls._count_cue_hits(bridge_handoff, opening_window)
            bridge_handoff_present = cls._carry_forward_item_present_in_text(
                bridge_handoff,
                opening_window,
            )
            opening_has_transition = text_has_transition_signal(opening_window)
            if matched < required and not bridge_handoff_present and not opening_has_transition:
                issues.append(
                    {
                        "issue_type": "bridge_contract_not_followed",
                        "severity": "high",
                        "confidence": 0.88,
                        "source": "local",
                        "repair_surface": "chapter_text",
                        "summary": "桥接动作接力未在开场显式落地，存在硬切跳场风险。",
                        "evidence": (
                            f"action_handoff=\u201c{bridge_handoff[:80]}\u201d\uff1b"
                            f"opening_hit={matched}/{required}\uff1b"
                            f"opening_transition={opening_has_transition}"
                        ),
                        "local_detection": True,
                        "affected_characters": [bridge.opening_pov]
                        if bridge and bridge.opening_pov
                        else [],
                        "rewrite_scope": "opening",
                        "fix_actions": [
                            f"在开场前 {opening_paragraphs} 段补上离场/转场/接力动作链，再进入本章主冲突。"
                        ],
                    }
                )

        if previous_exit is not None and bridge is not None:
            if bridge.from_chapter != previous_exit.chapter_number:
                issues.append(
                    {
                        "issue_type": "bridge_contract_not_followed",
                        "severity": "critical",
                        "confidence": 0.98,
                        "source": "local",
                        "repair_surface": "bridge_artifact",
                        "summary": f"Bridge from_chapter ({bridge.from_chapter}) 与上一章退场状态 ({previous_exit.chapter_number}) 不匹配，结构性数据断裂。",
                        "evidence": f"bridge.from_chapter={bridge.from_chapter}, previous_exit.chapter_number={previous_exit.chapter_number}",
                        "local_detection": True,
                        "affected_characters": [],
                        "rewrite_scope": "chapter",
                        "fix_actions": ["修正 bridge 数据使 from_chapter 与上一章退场状态一致。"],
                    }
                )
            if not bridge.opening_pov:
                issues.append(
                    {
                        "issue_type": "bridge_contract_not_followed",
                        "severity": "high",
                        "confidence": 0.95,
                        "source": "local",
                        "repair_surface": "bridge_artifact",
                        "summary": "Bridge 缺少 opening_pov，无法验证视角一致性。",
                        "evidence": "bridge.opening_pov 为空",
                        "local_detection": True,
                        "affected_characters": [],
                        "rewrite_scope": "opening",
                        "fix_actions": ["补充 bridge 的 opening_pov 字段。"],
                    }
                )

        return [
            cls._enrich_issue_anchor(
                issue,
                input_data.chapter_text,
                opening_window_paragraphs=opening_paragraphs,
            )
            for issue in issues
        ]

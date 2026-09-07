"""BridgeStep — generates a structured bridge from previous exit to current chapter opening."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import (
    sanitize_story_text,
    text_has_custody_signal,
    text_has_release_signal,
    text_has_transition_signal,
)
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterStatePacket
from novel_forge.core.utils.string import carry_forward_text, clean_str
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.motif_prompt_format import (
    format_motif_continuity_for_prompt,
)
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.forbidden_sources import normalize_forbidden_source_list
from novel_forge.pipeline.steps.prompt_diagnostics import log_prompt_diagnostics
from novel_forge.pipeline.steps.step_registry import register_step

_logger = get_logger("pipeline.bridge")


@dataclass
class BridgeInput:
    """Input for chapter-bridge generation."""

    chapter_state_packet: ChapterStatePacket
    story_kernel_context: dict[str, Any]
    """StoryKernel field slices from ContextComposer.compose_bridge_input()."""
    genre: str = ""
    tone: str = ""
    memory_hints: dict[str, Any] | None = None
    style: str = "literary"
    style_profile: dict[str, Any] | None = None
    narrative_contract: dict[str, Any] | None = None
    pov_hint: str = ""
    reading_power_hint: dict[str, Any] | None = None
    """追读力窗口系统注入的提示（悬念设置/兑现约束、钩子类型建议等）。"""
    story_bible: dict[str, Any] | None = None
    chapter_source_slice: Any | None = None


@register_step("bridge_chapter")
class BridgeStep(PipelineStep[BridgeInput, ChapterBridge]):
    """Build a chapter bridge contract from previous exit state and outline."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.coherence_issues: list[str] = []

    @property
    def step_name(self) -> str:
        return "bridge_chapter"

    _TRANSITION_MODE_ALIASES = {
        "direct": "direct_continue",
        "directcontinue": "direct_continue",
        "direct_continue": "direct_continue",
        "actionhandoff": "action_handoff",
        "action_handoff": "action_handoff",
        "synthetic": "synthetic",
        "timeskip": "time_skip",
        "time_skip": "time_skip",
        "povswitch": "pov_switch",
        "pov_switch": "pov_switch",
    }

    @classmethod
    def _normalize_transition_mode(cls, raw: Any) -> str:
        """Preserve known bridge transition enum values while sanitizing prose."""
        value = clean_str(raw)
        if not value:
            return ""
        key = value.lower().replace("-", "_").replace(" ", "_")
        compact_key = "".join(ch for ch in key if ch.isalnum() or ch == "_")
        if compact_key in cls._TRANSITION_MODE_ALIASES:
            return cls._TRANSITION_MODE_ALIASES[compact_key]
        compact_no_underscore = compact_key.replace("_", "")
        if compact_no_underscore in cls._TRANSITION_MODE_ALIASES:
            return cls._TRANSITION_MODE_ALIASES[compact_no_underscore]
        if "视角" in value and ("切" in value or "换" in value):
            return "pov_switch"
        if "时间" in value and ("跳" in value or "略" in value):
            return "time_skip"
        if "直接" in value and ("延续" in value or "续写" in value):
            return "direct_continue"
        if "动作" in value and ("交接" in value or "接力" in value):
            return "action_handoff"
        sanitized = sanitize_story_text(value).strip()
        return sanitized if sanitized == value else ""

    @staticmethod
    def _first_carry_forward(exit_state: Any) -> str:
        """Return the text of the first open carry-forward item, or "".

        Structured ``CarryForwardItem`` entries replace the legacy plain-string
        form; this helper projects the first *open* item's text for use as a
        bridge fallback anchor.
        """
        if not exit_state:
            return ""
        items = list(getattr(exit_state, "must_carry_forward", []) or [])
        from novel_forge.core.utils.string import carry_forward_status

        for item in items:
            if carry_forward_status(item) != "open":
                continue
            text = carry_forward_text(item)
            if text:
                return text
        return ""

    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        items: list[str] = []
        if isinstance(value, list):
            for item in value:
                # Carry-forward items arrive structured; project to text first.
                text = sanitize_story_text(clean_str(carry_forward_text(item) or item))
                if text:
                    items.append(text)
        elif isinstance(value, str):
            text = sanitize_story_text(clean_str(value))
            if text:
                items.append(text)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @classmethod
    def _prepare_prompt_memory_hints(cls, memory_hints: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(memory_hints, dict) or not memory_hints:
            return {}

        prepared = dict(memory_hints)
        formatted = format_motif_continuity_for_prompt(memory_hints.get("motif_continuity"))
        if formatted:
            prepared["motif_continuity"] = formatted
        else:
            prepared.pop("motif_continuity", None)
        return prepared

    @staticmethod
    def _kernel_to_anchor_data(
        kernel_ctx: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Transform StoryKernel field slices for anchor-term extraction.

        Returns ``(bible_data, canon_data)`` for ``_extract_anchor_terms_from_bible``.
        """
        entities = kernel_ctx.get("entities", [])
        relationships = kernel_ctx.get("relationships", [])

        char_rels: dict[str, dict[str, str]] = {}
        for rel in relationships:
            src_id = rel.get("source_entity_id", "")
            label = rel.get("label", "")
            if src_id and label:
                char_rels.setdefault(src_id, {})[rel.get("target_entity_id", "")] = label

        bible_chars: list[dict[str, Any]] = []
        for entity in entities:
            if entity.get("entity_type") == "character":
                bible_chars.append({
                    "name": entity.get("name", ""),
                    "relationships": char_rels.get(entity.get("entity_id", ""), {}),
                })

        canon_chars: dict[str, dict[str, str]] = {}
        for entity in entities:
            if entity.get("entity_type") == "character":
                canon_chars[entity.get("name", "")] = {
                    "social_status": entity.get("attributes", {}).get("social_status", ""),
                }

        canon_rels: dict[str, dict[str, str]] = {}
        for rel in relationships:
            canon_rels[rel.get("relationship_id", "")] = {
                "public_status": rel.get("label", ""),
            }

        return (
            {"characters": bible_chars},
            {"characters": canon_chars, "relationships": canon_rels},
        )

    @classmethod
    def _check_narrative_coherence(
        cls, normalized_data: dict[str, Any], packet: ChapterStatePacket
    ) -> list[str]:
        """Check the normalized bridge payload for narrative coherence risks."""
        issues = []
        prev_exit = packet.previous_exit_state

        if prev_exit:
            # 1. Check if unresolved questions are completely dropped
            prev_open = prev_exit.open_questions
            current_pending = normalized_data.get("pending_questions", [])
            causal_link = normalized_data.get("causal_link", {})
            unresolved_q = causal_link.get("unresolved_question", "")

            if prev_open:
                has_overlap = False
                for q in prev_open:
                    if q and (any(q in p for p in current_pending) or q in unresolved_q):
                        has_overlap = True
                        break
                if not has_overlap:
                    issues.append("上一章的未决悬念在当前桥接中被完全丢弃")

            # 2. Check for unexplained location jumps
            prev_loc = prev_exit.location
            curr_loc = normalized_data.get("opening_location")
            handoff = normalized_data.get("action_handoff", "")
            prev_pov = prev_exit.pov
            curr_pov = normalized_data.get("opening_pov", "")
            transition_mode = normalized_data.get("transition_mode", "")
            pov_changed = bool(prev_pov and curr_pov and prev_pov != curr_pov)
            if prev_loc and curr_loc and prev_loc != curr_loc and not (
                pov_changed and transition_mode in {"pov_switch", "synthetic"}
            ):
                move_keywords = [
                    "离开",
                    "前往",
                    "逃",
                    "走",
                    "回",
                    "到",
                    "转移",
                    "抵达",
                    "之后",
                    "脱身",
                    "摆脱",
                ]
                if not any(kw in handoff for kw in move_keywords):
                    issues.append(
                        f"地点从 {prev_loc} 跳跃到 {curr_loc}，但 action_handoff 中缺乏移动或过渡的交代"
                    )

        return issues

    @classmethod
    def _normalize_bridge_payload(
        cls,
        payload: Any,
        packet: ChapterStatePacket,
        story_kernel_context: dict[str, Any],
        memory_hints: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        data = payload if isinstance(payload, dict) else {}
        previous_exit = packet.previous_exit_state
        outline = packet.chapter_outline
        synthetic_summary = (
            f"以{outline.pov_character or '当前主视角'}在{outline.setting}开场，"
            f"承接本章目标“{outline.goal}”，优先回应上一章未完成动作。"
        )
        proposed_time = clean_str(
            data.get("opening_time", previous_exit.time_marker if previous_exit else "")
        )
        proposed_location = clean_str(
            data.get(
                "opening_location",
                outline.setting or (previous_exit.location if previous_exit else ""),
            )
        )
        proposed_pov = clean_str(
            data.get(
                "opening_pov", outline.pov_character or (previous_exit.pov if previous_exit else "")
            )
        )
        raw_transition_mode = cls._normalize_transition_mode(data.get("transition_mode"))
        transition_mode = raw_transition_mode or (
            "synthetic" if previous_exit is None else "action_handoff"
        )
        emotional_carryover = clean_str(
            data.get(
                "emotional_carryover",
                cls._first_carry_forward(previous_exit),
            )
        )
        action_handoff = clean_str(
            data.get(
                "action_handoff",
                cls._first_carry_forward(previous_exit) or outline.goal,
            )
        )

        previous_ending = packet.previous_chapter_ending
        location_changed = bool(
            previous_exit
            and previous_exit.location
            and proposed_location
            and previous_exit.location != proposed_location
        )
        if location_changed and previous_exit is not None:
            # Only override the location when the character is clearly still confined:
            # custody signal in the previous ending AND the action_handoff explains
            # neither a release/escort nor an explicit movement/transition.
            # Both checks must fail to trigger override — a single transition word
            # like "前往" is enough to indicate intentional relocation by the LLM.
            if text_has_custody_signal(previous_ending) and (
                not text_has_release_signal(action_handoff)
                and not text_has_transition_signal(action_handoff)
            ):
                proposed_location = previous_exit.location
                transition_mode = "direct_continue"

        if (
            previous_exit is not None
            and previous_exit.pov
            and proposed_pov
            and previous_exit.pov != proposed_pov
            and not raw_transition_mode
        ):
            proposed_pov = previous_exit.pov

        raw_causal = data.get("causal_link", {})
        if not isinstance(raw_causal, dict):
            raw_causal = {}
        if previous_exit is not None and previous_exit.open_questions:
            fallback_threads = previous_exit.open_questions
        else:
            fallback_threads = [
                carry_forward_text(item)
                for item in packet.must_carry_forward
                if carry_forward_text(item)
            ]
        causal_link = {
            "previous_event": sanitize_story_text(clean_str(raw_causal.get("previous_event"))),
            "causal_mechanism": sanitize_story_text(
                clean_str(
                    raw_causal.get(
                        "causal_mechanism",
                        cls._first_carry_forward(previous_exit)
                        or packet.chapter_outline.goal,
                    )
                )
            ),
            "unresolved_question": sanitize_story_text(
                clean_str(
                    raw_causal.get(
                        "unresolved_question",
                        previous_exit.open_questions[0]
                        if previous_exit and previous_exit.open_questions
                        else packet.chapter_outline.goal,
                    )
                )
            ),
            "open_threads": cls._normalize_string_list(
                raw_causal.get("open_threads", fallback_threads)
            ),
        }
        raw_criteria = cls._normalize_string_list(data.get("opening_acceptance_criteria", []))
        opening_acceptance_criteria = raw_criteria
        if not opening_acceptance_criteria:
            if action_handoff:
                opening_acceptance_criteria.append(
                    f"开头必须把动作接力写成正在发生的正文：{action_handoff}"
                )
            if proposed_time or proposed_location or proposed_pov:
                opening_acceptance_criteria.append(
                    f"开头必须自然落到时间/地点/视角：{proposed_time or '沿用上一章'} / "
                    f"{proposed_location or '沿用上一章'} / {proposed_pov or '沿用本章POV'}"
                )
            if emotional_carryover:
                opening_acceptance_criteria.append(f"开头必须承接情绪余波：{emotional_carryover}")

        normalized: dict[str, Any] = {
            "from_chapter": previous_exit.chapter_number
            if previous_exit
            else max(0, packet.chapter_number - 1),
            "to_chapter": packet.chapter_number,
            "opening_time": proposed_time,
            "opening_location": proposed_location,
            "opening_pov": proposed_pov,
            "transition_mode": transition_mode,
            "emotional_carryover": emotional_carryover,
            "action_handoff": action_handoff,
            "causal_link": causal_link,
            "pending_questions": cls._normalize_string_list(
                data.get(
                    "pending_questions",
                    previous_exit.open_questions if previous_exit else packet.must_carry_forward,
                )
            )
            or [carry_forward_text(item) for item in packet.must_carry_forward if carry_forward_text(item)],
            "forbidden_repetition": normalize_forbidden_source_list(
                data.get("forbidden_repetition", []),
                source="bridge",
            ),
            "bridge_summary": clean_str(data.get("bridge_summary")) or synthetic_summary,
            "sensory_anchors": cls._normalize_string_list(data.get("sensory_anchors", [])),
            "opening_acceptance_criteria": opening_acceptance_criteria,
            "relationship_beat": data.get("relationship_beat")
            if isinstance(data.get("relationship_beat"), dict)
            else None,
        }

        from novel_forge.pipeline.long.services.anchor_terms import (
            _extract_anchor_terms_from_bible,
        )
        from novel_forge.pipeline.steps.continuity_eval_step import _filter_forbidden_elements

        bible_anchor_data, canon_anchor_data = cls._kernel_to_anchor_data(story_kernel_context)

        bible_anchor_terms = _extract_anchor_terms_from_bible(
            bible_anchor_data,
            canon_anchor_data,
        )
        open_threads = causal_link.get("open_threads", [])
        normalized["forbidden_repetition"] = _filter_forbidden_elements(
            cast(list[str], normalized["forbidden_repetition"]),
            packet=packet,
            extra_anchor_texts=[
                action_handoff,
                emotional_carryover,
                str(normalized["bridge_summary"]),
                *cast(list[str], normalized["pending_questions"]),
                str(causal_link.get("previous_event", "") or ""),
                str(causal_link.get("causal_mechanism", "") or ""),
                str(causal_link.get("unresolved_question", "") or ""),
                *([str(item) for item in open_threads] if isinstance(open_threads, list) else []),
            ],
            extra_known_terms=[proposed_pov, proposed_location, *bible_anchor_terms],
            motif_context=(memory_hints or {}).get("motif_continuity"),
        )

        # ── direct_continue 模式下，剔除与 action_handoff 冲突的 forbidden 条目 ──
        # 理由：direct_continue 表示场景直接延续，角色的物理状态（如"背靠门板"）
        # 是真实持续的，不是需要回避的重复意象。
        if transition_mode == "direct_continue" and action_handoff:
            _handoff = action_handoff
            _emo = emotional_carryover
            _safe_forbidden: list[str] = []
            for item in normalized["forbidden_repetition"]:
                # 如果这个禁止词出现在 action_handoff 或 emotional_carryover 中，
                # 说明它描述的是当前延续状态而非过度使用的意象 → 剔除
                if item in _handoff or item in _emo:
                    continue
                _safe_forbidden.append(item)
            normalized["forbidden_repetition"] = _safe_forbidden

        # 启用连贯性检查
        issues = cls._check_narrative_coherence(normalized, packet)
        if issues:
            _logger.warning("桥接叙事连贯性风险: " + " | ".join(issues))

        return normalized, issues

    async def _execute(self, input_data: BridgeInput) -> ChapterBridge:
        packet = input_data.chapter_state_packet
        kernel_ctx = input_data.story_kernel_context
        if not kernel_ctx:
            raise ValueError("BridgeStep requires StoryKernel context.")
        from novel_forge.pipeline.long.services.constraints.constraint_router import (
            build_bridge_cards,
        )

        prompt_memory_hints = self._prepare_prompt_memory_hints(input_data.memory_hints)
        stage_cards = build_bridge_cards(
            packet=packet,
            memory_hints=prompt_memory_hints,
            reading_power_hint=input_data.reading_power_hint,
            style_profile=input_data.style_profile,
            narrative_contract=input_data.narrative_contract,
            story_bible=input_data.story_bible or {},
            canon_context=kernel_ctx,
            chapter_source_slice=input_data.chapter_source_slice,
            settings=self.settings,
        )
        ctx = {
            "stage_cards": stage_cards,
        }
        max_tokens = self._dynamic_max_tokens(
            TaskType.BRIDGE_CHAPTER,
            3200,
            prompt_overhead=3200,
            min_tokens=4096,
        )
        request = self._builder.build(
            TaskType.BRIDGE_CHAPTER,
            ctx,
            max_tokens=max_tokens,
            temperature=self.settings.temp_bridge_chapter,
        )
        log_prompt_diagnostics(
            _logger,
            event="bridge_prompt_diagnostics",
            request=request,
            context=ctx,
            settings=self.settings,
            enabled_attr="long_prompt_diagnostics_enabled",
            warn_attr="long_prompt_warn_tokens",
            chapter=getattr(packet, "chapter_number", "?"),
            on_event=self._on_step_event,
        )
        data = await self._call_with_retry(
            TaskType.BRIDGE_CHAPTER,
            ctx,
            max_tokens=max_tokens,
            temperature=self.settings.temp_bridge_chapter,
        )
        normalized, coherence_issues = self._normalize_bridge_payload(
            data,
            packet,
            kernel_ctx,
            memory_hints=prompt_memory_hints,
        )
        self.coherence_issues = coherence_issues
        return ChapterBridge.model_validate(normalized)

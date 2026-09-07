"""Canonical prompt render snapshot cases used by scripts and tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards


@dataclass(frozen=True)
class PromptSnapshotCase:
    """One render snapshot scenario."""

    case_id: str
    task_type: TaskType
    context: dict[str, Any]


def _spec(mode: str = "long") -> dict[str, Any]:
    return {
        "title": "Signal Archive",
        "genre": "mystery",
        "theme": "truth and trust",
        "tone": "tense",
        "length_target": 120000 if mode == "long" else 8000,
        "language": "zh",
        "characters_hint": "Lin Wan and Zhou Lin investigate a hidden terminal.",
        "world_hint": "An underground hangar stores forbidden signal records.",
        "conflict_hint": "The archive proves one ally has hidden evidence.",
        "pov_hint": "limited third-person",
        "opening_style": "start with a concrete action",
        "ending_style": "close on a new decision",
        "extra_instructions": "Keep cause and effect visible.",
    }


def _story_bible() -> dict[str, Any]:
    return {
        "premise": "A signal archive exposes buried choices in an underground city.",
        "era": "near future",
        "geography": "underground hangar district",
        "culture": "technical guilds trade access for silence",
        "magic_or_tech": "signal forensics",
        "rules": ["Signal records cannot be forged without a checksum trace."],
        "themes": ["trust", "cost of truth"],
    }


def _character_bible() -> dict[str, Any]:
    return {
        "characters": [
            {
                "name": "Lin Wan",
                "role": "protagonist",
                "personality": "alert and precise",
                "arc": "learns to trust evidence without abandoning empathy",
                "relationships": {"Zhou Lin": "uneasy ally"},
            },
            {
                "name": "Zhou Lin",
                "role": "ally",
                "personality": "calm and evasive",
                "arc": "admits the cost of earlier silence",
                "relationships": {"Lin Wan": "protects her from partial truth"},
            },
        ]
    }


def _plan_outline_base() -> dict[str, Any]:
    return {
        "spec": _spec("long"),
        "story_bible": _story_bible(),
        "character_bible": _character_bible(),
        "total_chapters": 24,
        "words_per_chapter": 4500,
        "use_volume_mode": True,
        "narrative_complexity": "standard",
        "blueprint_element_selection": {
            "required_elements": [
                {
                    "name": "Causal clarity",
                    "element_id": "causal_clarity",
                    "category": "core",
                    "description": "Keep major reveals causally actionable.",
                    "prompt_hint": "Every major reveal must change a later choice.",
                }
            ],
            "extension_elements": [
                {
                    "name": "Archive motif",
                    "element_id": "archive_motif",
                    "category": "motif",
                    "prompt_hint": "Use signal fragments as recurring pressure.",
                    "recommended_genres": ["mystery"],
                    "selection_reason": "Fits the premise.",
                    "selection_score": 0.9,
                    "selection_source": "snapshot",
                }
            ],
            "focus_constraints": ["Keep motif evidence tied to decisions."],
        },
        "style_profile": {
            "summary": "Tense, concrete, low exposition.",
            "global_style": {
                "dialogue_ratio": "balanced",
                "pace_mode": "moderate",
                "emotional_style": "restrained",
            },
            "hook_config": {"preferred_types": ["mystery"], "strength_baseline": "medium"},
            "cool_point_config": {"preferred_patterns": ["evidence reversal"]},
            "strand_config": {
                "quest_max_consecutive": 3,
                "fire_max_absent": 2,
                "constellation_max_absent": 4,
            },
        },
        "creative_director_packet": {
            "emotional_engine": ["trust tested by incomplete evidence"],
            "thematic_promises": ["truth has a personal cost"],
            "signature_motifs": ["signal checksum", "sealed door"],
            "relationship_tensions": ["Lin Wan doubts Zhou Lin"],
            "anti_cliche_rules": ["Do not solve distrust with one confession."],
            "scene_potential": ["silent terminal room"],
        },
        "relationship_overview": [
            {
                "source": "Lin Wan",
                "target": "Zhou Lin",
                "relation": "uneasy ally",
                "tension": "withheld evidence",
            }
        ],
    }


def _plan_outline_fragment() -> dict[str, Any]:
    return {
        **_plan_outline_base(),
        "blueprint_generation_mode": "quality_first_expansion",
        "blueprint_fragment_request": {
            "block_key": "subplot_plan",
            "title": "Subplot weave",
            "required_keys": ["subplot_plan"],
            "instructions": ["Bind every subplot to a main-turning-point consequence."],
        },
        "blueprint_fragments_so_far": {
            "snapshot_mode": "causal_anchors",
            "anchors": {
                "key_turning_points": [
                    {"chapter": 8, "event": "Lin Wan proves the archive was edited."}
                ]
            },
        },
    }


def _stage_card_context(*, focused: bool = False) -> dict[str, Any]:
    outline = {
        "chapter_number": 3,
        "title": "Checksum",
        "goal": "Lin Wan verifies the signal anomaly.",
        "pov_character": "Lin Wan",
        "setting": "hangar archive",
        "target_word_count": 3200,
    }
    packet = {
        "chapter_number": 3,
        "chapter_contract": {
            "required_events": ["Lin Wan opens the archive terminal."],
            "forbidden_changes": ["Zhou Lin must not confess the full truth yet."],
            "guard_constraints": ["Do not reveal the final sender identity."],
        },
        "character_profiles": [
            {
                "name": "Lin Wan",
                "role": "protagonist",
                "identity": "engineer",
                "motivation": "prove the archive checksum is real",
                "boundary": "will not accuse without evidence",
            }
        ],
        "must_carry_forward": ["The previous chapter ended with a locked terminal."],
        "guard_constraints": ["Do not reveal the final sender identity."],
    }
    plan = {
        "opening_contract": "Start at the locked terminal.",
        "closing_contract": "End with Lin Wan choosing to confront Zhou Lin.",
        "scene_intents": [
            {
                "scene_id": "scene_01",
                "summary": "Lin Wan restores power to the terminal.",
                "purpose": "prove the anomaly is active",
                "conflict": "the checksum keeps changing",
                "required_characters": ["Lin Wan"],
                "character_motivations": [
                    {"character": "Lin Wan", "motivation": "confirm evidence", "stake": "trust"}
                ],
                "entry_state_refs": ["locked terminal"],
                "required_outcome": "checksum accepted",
                "exit_target_state": "archive opens",
                "time_marker": "night",
                "location": "hangar archive",
                "pov_character": "Lin Wan",
            }
        ],
        "key_revelations": ["the checksum was generated inside the hangar"],
    }
    return {
        "chapter_number": 3,
        "target_word_count": 3200,
        "stage_cards": build_stage_cards(
            stage="draft",
            packet=packet,
            chapter_outline=outline,
            bridge={
                "opening_time": "night",
                "opening_location": "hangar archive",
                "opening_pov": "Lin Wan",
                "action_handoff": "She presses the restart key.",
                "emotional_carryover": "distrust from the previous chapter",
                "opening_acceptance_criteria": ["terminal state is shown in action"],
                "pending_questions": ["who changed the checksum"],
            },
            plan=plan,
            canon_context={
                "characters": {"Lin Wan": {"alive": True, "location": "hangar archive"}},
                "recent_events": [{"chapter": 2, "event": "terminal locked"}],
                "immutable_facts": ["The archive terminal is below the hangar."],
            },
            memory_hints={
                "previous_chapter_events": [{"event": "Zhou Lin avoided a direct answer."}],
                "relevant_history": [{"event": "The archive checksum appeared before."}],
            },
            style_profile={
                "summary": "Concrete suspense.",
                "global_style": {"dialogue_ratio": "low", "pace_mode": "tight"},
                "hook_config": {"preferred_types": ["mystery"], "chapter_end_required": True},
                "micro_payoff_config": {"preferred_types": ["clue"], "min_per_chapter": 1},
            },
            element_selection={
                "extension_elements": [
                    {
                        "name": "Focused clue",
                        "prompt_hint": "Make one clue alter the next choice.",
                        "element_id": "focused_clue",
                        "category": "optional",
                    },
                    {
                        "name": "Inactive clue",
                        "prompt_hint": "This should not render when unfocused.",
                        "element_id": "inactive_clue",
                        "category": "optional",
                    },
                ],
                "focus_constraints": ["Focused clue must change a decision."],
            }
            if focused
            else None,
            element_focus=["focused_clue"] if focused else None,
            reading_power_hint={
                "chapter_hook": "checksum contradiction",
                "tension_target": "rising",
            },
            weak_senses=["smell"] if focused else [],
            pov_hint="limited third-person",
        ),
    }


def _book_consistency_base(*, mode: str = "summary") -> dict[str, Any]:
    return {
        "chapter_summaries": [
            {
                "chapter_number": 1,
                "title": "Terminal",
                "summary": "Lin Wan finds a locked archive terminal.",
                "key_events": ["terminal locked", "Zhou Lin withholds context"],
            },
            {
                "chapter_number": 2,
                "title": "Checksum",
                "summary": "The checksum changes after the archive powers on.",
                "key_events": ["checksum shifts", "Lin Wan records evidence"],
            },
        ],
        "chapter_texts": [
            {
                "chapter_number": 1,
                "title": "Terminal",
                "paragraph_count": 1,
                "source_chars": 58,
                "truncated": False,
                "numbered_text": "[P1] Lin Wan touched the cold terminal. The screen stayed dark.",
            },
            {
                "chapter_number": 2,
                "title": "Checksum",
                "paragraph_count": 1,
                "source_chars": 50,
                "truncated": False,
                "numbered_text": "[P1] The checksum changed again. Zhou Lin looked away.",
            },
        ]
        if mode == "full_text"
        else [],
        "chapter_issue_pool": [
            {
                "chapter_number": 2,
                "lane": "continuity",
                "index": 1,
                "issue_type": "checksum_ownership",
                "severity": "warning",
                "location": "chapter 2",
                "summary": "checksum ownership may be unclear",
            }
        ],
        "max_issues_per_chunk": 8,
        "canon_characters": {"Lin Wan": {"alive": True}, "Zhou Lin": {"alive": True}},
        "canon_relationships": [
            {
                "character_a": "Lin Wan",
                "character_b": "Zhou Lin",
                "relationship_type": "uneasy ally",
                "description": "trust is pressured by hidden evidence",
            }
        ],
        "character_bible_names": ["Lin Wan", "Zhou Lin"],
        "character_profiles_compact": [{"name": "Lin Wan", "role": "protagonist"}],
        "world_rules": ["Signal records leave checksum traces."],
        "world_setting": "Underground hangar archive.",
        "world_hint": "Signal forensics society.",
        "story_theme": "trust under evidence pressure",
        "conflict_hint": "evidence versus loyalty",
        "arc_summary": "Lin Wan moves from suspicion to targeted confrontation.",
        "outline_summary": "Archive evidence drives each chapter choice.",
        "analysis_mode": mode,
        "location_strictness": "balanced",
        "prompt_hint": "Prioritize contradictions with exact chapter references.",
        "audit_chunk_notice": "",
        "audit_slices": [
            {
                "slice_id": "phase_1",
                "slice_kind": "phase",
                "chapters": [1, 2],
                "boundary_chapters": [1, 2],
                "focus_dimensions": ["timeline", "character_state"],
                "summary": "Opening archive phase.",
            }
        ],
        "semantic_evidence_context": [{"claim": "checksum cannot be forged silently"}],
        "memory_enhancement_context": "Recent motif: checksum as trust pressure.",
        "active_audit_dimension": "",
        "dimension_focus": {},
        "dimension_ledger_context": [],
        "shared_evidence_anchor": {"anchors": ["checksum", "terminal"]},
    }


def _book_consistency_dimension() -> dict[str, Any]:
    ctx = _book_consistency_base(mode="summary")
    ctx.update(
        {
            "active_audit_dimension": "timeline",
            "dimension_focus": {"name": "timeline", "prompt_hint": "Check ordering only."},
            "dimension_ledger_context": [{"dimension": "timeline", "status": "pending"}],
        }
    )
    return ctx


def _generate_config_context(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "mode_label": "short story" if mode == "short" else "long novel",
        "operation": "generate",
        "generation_mode": "creative",
        "generation_mode_label": "creative",
        "generation_mode_prompt": "Generate a complete creative configuration.",
        "creative_profile": {"tone": "tense"},
        "creative_profile_text": "tone: tense",
        "allow_partial_config_output": False,
        "hard_constraints": {},
        "hard_constraints_text": "",
        "story_synopsis_impact_text": "Respect the premise and avoid drift.",
        "output_fields_text": "Return all required fields.",
        "anchor_constraints_text": "",
        "user_hint": "A hidden archive changes an alliance.",
        "current_config_json": "{}",
    }


def _polish_config_context() -> dict[str, Any]:
    return {
        "mode": "long",
        "mode_label": "long novel",
        "operation": "polish",
        "user_hint": "Strengthen the central conflict.",
        "selected_suggestions": ["make the antagonist pressure clearer"],
        "focus_fields": ["conflict_hint", "opening_style"],
        "editable_config_fields": ["conflict_hint", "opening_style"],
        "allow_partial_config_output": True,
        "story_synopsis_impact_text": "Only update selected fields.",
        "polish_focus_coverage_text": "Cover every focus field.",
        "output_fields_text": "Return conflict_hint and opening_style only.",
        "anchor_constraints_text": "Preserve title and core premise.",
        "current_config_json": '{"conflict_hint": "old", "opening_style": "old"}',
    }


def _guard_check_context() -> dict[str, Any]:
    return {
        "constraint": "Do not reveal the final sender identity.",
        "chapter_number": 3,
        "chapter_text": "Lin Wan saw the checksum flash, but the sender field stayed sealed.",
    }


def _guard_repair_context() -> dict[str, Any]:
    return {
        "_retry_warning": "",
        "chapter_number": 3,
        "chapter_text": "Lin Wan saw the sender name and understood everything.",
        "guardrail_report": {
            "violations": [
                {
                    "type": "future_leak",
                    "severity": "high",
                    "location": "paragraph 1",
                    "description": "final sender identity is revealed too early",
                    "suggestion": "hide the sender field",
                }
            ]
        },
        "chapter_plan": {"main_plot_points": ["Lin Wan verifies the checksum anomaly."]},
        "alignment_report": {
            "missing_main_points": [],
            "supportive_subplot_points": ["checksum clue supports the main plot"],
            "weak_subplot_points": [],
            "summary": "Main plot point is present.",
        },
        "alignment_score_current": 8.0,
        "style_profile": {"summary": "Concrete suspense.", "banned_phrases": []},
        "repair_plan": {"strategy": "minimal edit"},
        "word_count_min": 2000,
        "word_count_max": 3600,
    }


def iter_snapshot_cases() -> tuple[PromptSnapshotCase, ...]:
    """Return all canonical snapshot cases in stable order."""
    return (
        PromptSnapshotCase("plan_outline_full", TaskType.PLAN_OUTLINE, _plan_outline_base()),
        PromptSnapshotCase(
            "plan_outline_fragment", TaskType.PLAN_OUTLINE, _plan_outline_fragment()
        ),
        PromptSnapshotCase("draft_chapter_regular", TaskType.DRAFT_CHAPTER, _stage_card_context()),
        PromptSnapshotCase(
            "draft_chapter_stage_cards",
            TaskType.DRAFT_CHAPTER,
            _stage_card_context(focused=True),
        ),
        PromptSnapshotCase(
            "book_consistency_summary",
            TaskType.BOOK_CONSISTENCY,
            _book_consistency_base(mode="summary"),
        ),
        PromptSnapshotCase(
            "book_consistency_full_text",
            TaskType.BOOK_CONSISTENCY,
            _book_consistency_base(mode="full_text"),
        ),
        PromptSnapshotCase(
            "book_consistency_active_dimension",
            TaskType.BOOK_CONSISTENCY_TIMELINE,
            _book_consistency_dimension(),
        ),
        PromptSnapshotCase(
            "generate_config_short",
            TaskType.GENERATE_CONFIG,
            _generate_config_context("short"),
        ),
        PromptSnapshotCase(
            "generate_config_long",
            TaskType.GENERATE_CONFIG,
            _generate_config_context("long"),
        ),
        PromptSnapshotCase("polish_config_partial", TaskType.POLISH_CONFIG, _polish_config_context()),
        PromptSnapshotCase(
            "guard_constraint_check",
            TaskType.GUARD_CONSTRAINT_CHECK,
            _guard_check_context(),
        ),
        PromptSnapshotCase("repair_guardrail", TaskType.REPAIR_GUARDRAIL, _guard_repair_context()),
    )


def snapshot_case_by_id(case_id: str) -> PromptSnapshotCase:
    """Return a snapshot case by stable identifier."""
    for case in iter_snapshot_cases():
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)

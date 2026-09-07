"""Implementation slice extracted from finalize.py (finalize_report.py)."""
# ruff: noqa: F403,F405,I001

from __future__ import annotations

import hashlib

from novel_forge.core.exceptions import (
    BLOCK_KIND_CONTRACT_AUDIT,
    BLOCK_KIND_EVAL_QUALITY,
    BLOCK_KIND_STATE_ADJUDICATION,
)
from novel_forge.core.utils.string import carry_forward_text
from novel_forge.pipeline.long.stages.finalize_checks import (
    _normalize_outcome_chapter_number,
    _persist_extracted_outcome_artifacts,
    _prior_character_snapshots,
)
from novel_forge.pipeline.long.stages.finalize_common import *
from novel_forge.pipeline.long.stages.finalize_common import _logger

def _clean_and_validate_chapter_text(
    runner: Any,
    bundle: Any,
    chapter_number: int,
    current_text: str,
    *,
    chapter_repair_report: Any | None = None,
    allow_word_count_archive_bypass: bool = False,
) -> str:
    """Clean and validate chapter prose BEFORE canon is updated.

    Raises RuntimeError if the text fails hard guards (prompt leaks, too short),
    ensuring canon is never updated for a rejected chapter.
    """
    # ── Guard: extract prose if LLM returned JSON instead of plain text ───
    _extracted = extract_text_content(current_text)
    if _extracted != current_text:
        _logger.warning(
            "json_guard_extracted | chapter=%d | original_len=%d | extracted_len=%d | "
            "model returned JSON instead of plain text, auto-extracting prose",
            chapter_number,
            len(current_text),
            len(_extracted),
        )
        current_text = _extracted

    # ── Strip leading markdown headings the LLM may have prepended ────────
    _stripped = strip_leading_markdown_headings(current_text)
    if _stripped != current_text:
        _logger.warning(
            "stripped_leading_heading | chapter=%d | removed=%r",
            chapter_number,
            current_text[: len(current_text) - len(_stripped)].strip(),
        )
        current_text = _stripped

    # ── Guard: scrub planning/prompt leakage artifacts before final save ──
    scrubbed_text, removed_artifacts = scrub_prompt_artifacts(current_text)
    if scrubbed_text and scrubbed_text != current_text:
        _logger.warning(
            "prompt_artifact_cleanup | chapter=%d | removed=%d",
            chapter_number,
            len(removed_artifacts),
        )
        runner._on_step(
            "prompt_artifact_cleanup",
            {
                "chapter": chapter_number,
                "removed_count": len(removed_artifacts),
                "samples": removed_artifacts[:5],
            },
        )
        current_text = scrubbed_text

    confirmed_reported_leaks = confirmed_reported_prompt_leaks(
        current_text,
        getattr(chapter_repair_report, "prompt_leaks", [])
        if chapter_repair_report is not None
        else (),
    )
    if confirmed_reported_leaks:
        repaired_text, repaired_leaks = repair_confirmed_prompt_leaks(
            current_text,
            confirmed_reported_leaks,
        )
        if repaired_leaks and repaired_text != current_text:
            _logger.warning(
                "prompt_leak_deterministic_fallback | chapter=%d | repaired=%s",
                chapter_number,
                "；".join(repaired_leaks[:5]),
            )
            runner._on_step(
                "prompt_leak_deterministic_fallback",
                {
                    "chapter": chapter_number,
                    "repaired_count": len(repaired_leaks),
                    "samples": repaired_leaks[:5],
                },
            )
            current_text = repaired_text

    remaining_prompt_leaks = [
        item
        for item in detect_prompt_leaks(current_text, max_hits=8)
        if classify_prompt_leak_candidate(current_text, item) != IN_WORLD_TEXT_VERDICT
    ]
    if remaining_prompt_leaks:
        hard_tokens = (
            "【",
            "输出格式", "output format",
            "opening_contract",
            "closing_contract",
            "required_outcome",
            "exit_target_state",
            "scene_intent",
            "bridge_summary",
            "系统响应", "system response",
            "预期扰动路径", "expected perturbation path",
            "落地场景", "landing scene",
            "伪装压力等级", "disguised pressure level",
            "暴露风险信号", "exposed risk signal",
        )
        hard_leaks = [
            item for item in remaining_prompt_leaks if any(token in item for token in hard_tokens)
        ]
        if hard_leaks:
            _logger.error(
                "persist_guard_prompt_leak_rejected | chapter=%d | leaks=%s",
                chapter_number,
                "；".join(hard_leaks[:5]),
            )
            raise RuntimeError("检测到提示词/规划语句泄露，已拒绝归档。请先执行修复后再保存。")
        runner._on_step(
            "prompt_artifact_warning",
            {
                "chapter": chapter_number,
                "leak_count": len(remaining_prompt_leaks),
                "samples": remaining_prompt_leaks[:5],
            },
        )

    # ── Guard: refuse to save empty or critically short chapters ──────────
    target_words = int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0)
    min_acceptable = max(500, target_words * 0.3) if target_words > 0 else 500
    text_char_count = count_chapter_words(current_text)
    if text_char_count < min_acceptable:
        _logger.error(
            "persist_guard_rejected | chapter=%d | chars=%d | min=%d | "
            "refusing to overwrite chapter with critically short text",
            chapter_number,
            text_char_count,
            int(min_acceptable),
        )
        raise RuntimeError(
            f"Chapter {chapter_number} text too short ({text_char_count} chars, "
            f"minimum {int(min_acceptable)}). Refusing to save to prevent data loss."
        )

    assessment = assess_word_count(current_text, target_words)
    if word_count_archive_gate_enabled(runner._settings) and assessment.band in {
        "structural",
        "hard_reject",
    }:
        if allow_word_count_archive_bypass:
            runner._on_step(
                "word_count_archive_gate_bypassed",
                {
                    "chapter": chapter_number,
                    "reason": "rejection_limit_reached",
                    "assessment": assessment.as_dict(),
                },
            )
            return current_text
        _logger.error(
            "persist_guard_word_count_rejected | chapter=%d | actual=%d | target=%d | "
            "band=%s | hard_range=%d-%d",
            chapter_number,
            assessment.actual,
            assessment.target,
            assessment.band,
            assessment.hard_min,
            assessment.hard_max,
        )
        raise RuntimeError(
            f"Chapter {chapter_number} word count outside archive range "
            f"({assessment.actual}/{assessment.target}, band={assessment.band}). "
            "Refusing to save before structural word-count restructuring succeeds."
        )

    return current_text

def _build_chapter_contract_payload(bundle: Any, packet: Any, plan: Any) -> dict[str, Any]:
    """Build a scoped contract payload without local semantic judgement."""
    outline = getattr(bundle, "chapter_outline", None)
    chapter_number = int(getattr(outline, "chapter_number", 0) or 0)
    layout = getattr(bundle, "layout", None)
    if layout is None:
        return {}
    contract_path = layout.plans_dir / "chapter_contracts.json"
    if contract_path.exists():
        try:
            raw = contract_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            for item in data.get("chapter_contracts", []) or []:
                if int(item.get("chapter_number", 0) or 0) == chapter_number:
                    return dict(item)
        except Exception:
            pass
    return {
        "chapter_number": chapter_number,
        "title": getattr(outline, "title", "") or "",
        "entry_state_requirements": [
            carry_forward_text(item)
            for item in list(getattr(packet, "must_carry_forward", []) or [])
            if carry_forward_text(item)
        ],
        "required_events": list(getattr(outline, "main_plot_points", []) or [])
        + list(getattr(outline, "beats_summary", []) or []),
        "allowed_changes": list(getattr(plan, "required_state_transitions", []) or []),
        "forbidden_changes": list(getattr(packet, "guard_constraints", []) or []),
        "promise_ops": [],
        "relationship_ops": list(getattr(plan, "relationship_evolution", []) or []),
        "item_ops": [],
        "knowledge_ops": [],
        "exit_state_targets": [str(getattr(plan, "closing_contract", "") or "")],
        "required_progressions": list(getattr(outline, "main_plot_points", []) or []),
        "allowed_progressions": list(getattr(plan, "required_state_transitions", []) or []),
        "forbidden_progressions": list(getattr(packet, "guard_constraints", []) or []),
        "completion_criteria": [str(getattr(plan, "closing_contract", "") or "")],
        "future_leak_risks": [],
        "source": "chapter_runtime_scope",
    }

def _has_persisted_chapter_contract(bundle: Any, chapter_number: int) -> bool:
    """Return True when init produced a scoped contract for this chapter."""
    layout = getattr(bundle, "layout", None)
    if layout is None:
        return False
    contract_path = layout.plans_dir / "chapter_contracts.json"
    if not contract_path.exists():
        return False
    try:
        data = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    for item in data.get("chapter_contracts", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if number == chapter_number:
            return True
    return False

def _build_current_state_payload(
    bundle: Any,
    packet: Any,
    chapter_number: int,
    *,
    plan: Any | None = None,
    chapter_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact state context for LLM adjudication without interpreting it."""
    from novel_forge.core.config import get_settings
    from novel_forge.narrative_state.store import NarrativeStateStore

    store = NarrativeStateStore(bundle.layout.root)
    settings = get_settings()
    pending_tail = int(getattr(settings, "narrative_state_pending_tail_items", 6) or 6)
    max_state_chapter = max(0, chapter_number - 1)
    previous_exit = getattr(packet, "previous_exit_state", None)
    previous_exit_payload = (
        previous_exit.model_dump(mode="json")
        if previous_exit is not None and hasattr(previous_exit, "model_dump")
        else previous_exit
    )
    return {
        "chapter_number": chapter_number,
        "character_roster": _build_state_character_roster(bundle, packet),
        "state_update_slots": _build_state_update_slots(
            bundle,
            packet,
            plan,
            chapter_number=chapter_number,
            chapter_contract=chapter_contract,
        ),
        "previous_exit_state": previous_exit_payload,
        "active_relationships": [
            rel.model_dump(mode="json") if hasattr(rel, "model_dump") else rel
            for rel in list(getattr(packet, "active_relationships", []) or [])
        ],
        "active_plot_threads": [
            thread.model_dump(mode="json") if hasattr(thread, "model_dump") else thread
            for thread in list(getattr(packet, "active_plot_threads", []) or [])
        ],
        # Accepted history is already materialized into StoryKernel/current state and
        # searchable as dynamic evidence.  Only unresolved adjudication belongs here.
        "authoritative_projection": store.projection_for_prompt(
            max_entries=0,
            max_pending=pending_tail,
            max_chapter=max_state_chapter,
        ),
    }

def _state_text(value: Any) -> str:
    return str(value or "").strip()

def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    return dict(value) if isinstance(value, dict) else {}

def _build_state_character_roster(bundle: Any, packet: Any) -> list[dict[str, Any]]:
    """Read current characters from CharacterBible/Canon for fixed-slot state extraction."""
    by_name: dict[str, dict[str, Any]] = {}

    def upsert(raw: Any, *, source: str) -> None:
        data = _model_dict(raw)
        entity_type = _state_text(data.get("entity_type"))
        if source == "canon_state" and entity_type and entity_type != "character":
            return
        name = _state_text(data.get("name"))
        if not name or is_system_artifact_name(name):
            return
        current = by_name.setdefault(name, {"name": name})
        current.setdefault("source", source)
        for key in (
            "character_id",
            "role",
            "gender",
            "status",
            "time_layer",
            "social_status",
            "voice",
        ):
            value = _state_text(data.get(key))
            if value:
                current[key] = value
        last_seen = data.get("last_seen_chapter")
        if last_seen not in (None, "", 0):
            current["last_seen_chapter"] = last_seen
        physical_raw = data.get("physical")
        physical: dict[str, Any] = physical_raw if isinstance(physical_raw, dict) else {}
        location = _state_text(physical.get("location"))
        if location:
            current["location"] = location

    character_bible = getattr(bundle, "character_bible", None)
    for profile in list(getattr(character_bible, "characters", []) or []):
        upsert(profile, source="character_bible")
    for profile in list(getattr(packet, "character_profiles", []) or []):
        upsert(profile, source="packet_profile")

    canon_state = getattr(bundle, "canon_state", None)
    if canon_state is not None:
        for state in getattr(canon_state, "entities", []):
            upsert(state, source="canon_state")

    for raw_name in list(getattr(packet, "known_characters", []) or []):
        name = _state_text(raw_name)
        if name and name in by_name and not is_system_artifact_name(name):
            by_name.setdefault(name, {"name": name, "source": "known_characters"})

    active = [
        item for item in by_name.values() if _state_text(item.get("status")) != "retired"
    ]
    active.sort(key=lambda item: (str(item.get("role", "")), str(item.get("name", ""))))
    return active

def _build_state_update_slots(
    bundle: Any,
    packet: Any,
    plan: Any | None,
    *,
    chapter_number: int,
    chapter_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    outline = getattr(bundle, "chapter_outline", None)
    roster_names = [
        item["name"]
        for item in _build_state_character_roster(bundle, packet)
        if item.get("name")
    ]
    main_targets = _state_text_list(
        [
            *list(getattr(outline, "main_plot_points", []) or []),
            *list(getattr(outline, "beats_summary", []) or []),
            *list(getattr(plan, "required_state_transitions", []) or []),
        ],
    )
    subplot_targets = _state_text_list(
        [
            *list(getattr(outline, "subplot_points", []) or []),
            *(
                [getattr(outline, "subplot_focus", "")]
                if getattr(outline, "subplot_focus", "")
                else []
            ),
        ],
    )
    relationship_targets = _relationship_slot_targets(packet, plan, chapter_number=chapter_number)
    contract_targets = _contract_slot_targets(chapter_contract or {}, chapter_number=chapter_number)
    return {
        "chapter_presence": {
            "state_path": f"chapter.{chapter_number}.present_characters",
            "allowed_character_names": roster_names,
            "write_fields": ["present_characters", "summary"],
        },
        "main_plot": [
            {
                "state_path": f"plot.main.chapter_{chapter_number}.{idx:02d}",
                "target": target,
                "write_fields": ["summary", "value", "next_impact"],
            }
            for idx, target in enumerate(main_targets, start=1)
        ],
        "subplot": [
            {
                "state_path": f"subplot.chapter_{chapter_number}.{idx:02d}",
                "target": target,
                "write_fields": ["subplot_id", "summary", "value", "next_impact"],
            }
            for idx, target in enumerate(subplot_targets, start=1)
        ],
        "relationship_carry_forward": relationship_targets,
        "contract_progression": contract_targets,
    }

def _state_text_list(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _state_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result

def _relationship_slot_targets(
    packet: Any, plan: Any | None, *, chapter_number: int
) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rel in list(getattr(packet, "active_relationships", []) or []):
        data = _model_dict(rel)
        pair_id = _state_text(data.get("pair_id"))
        characters_raw = data.get("characters")
        characters: list[Any] = characters_raw if isinstance(characters_raw, list) else []
        key = pair_id or "|".join(str(item) for item in characters)
        if not key or key in seen:
            continue
        seen.add(key)
        slots.append(
            {
                "state_path": f"relationship.{key}.chapter_{chapter_number}",
                "relationship_pair": characters[:2],
                "current_status": _state_text(data.get("public_status")),
                "write_fields": ["relationship_pair", "summary", "value", "next_impact"],
            }
        )
    for idx, target in enumerate(
        _state_text_list(list(getattr(plan, "relationship_evolution", []) or [])), start=1
    ):
        key = f"planned_{idx}"
        if key in seen:
            continue
        slots.append(
            {
                "state_path": f"relationship.{key}.chapter_{chapter_number}",
                "target": target,
                "write_fields": ["relationship_pair", "summary", "value", "next_impact"],
            }
        )
    return slots

def _contract_slot_targets(
    chapter_contract: dict[str, Any],
    *,
    chapter_number: int,
) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for target in compile_contract_targets(
        chapter_contract,
        chapter_number=chapter_number,
        include_entry_state=False,
    ):
        # Cognitive constraints are context-only for archive gating, but an
        # accepted evidence-backed cognition change still needs a legal state
        # slot.  Writability and archive-required status are separate concerns.
        if not target.required_for_archive and target.field_name != "cognitive_constraints":
            continue
        slots.append(
            {
                "state_path": target.state_path,
                "target_id": target.target_id,
                "contract_field": target.field_name,
                "target": target.target,
                "required_for_archive": target.required_for_archive,
                "write_fields": (
                    ["summary", "value", "entity_ids", "knowledge_type", "next_impact"]
                    if target.field_name == "knowledge_ops"
                    else [
                        "summary",
                        "value",
                        "cognitive_subjects",
                        "cognitive_object",
                        "cognitive_level",
                        "action_level",
                        "character_knowledge_coverage",
                        "next_impact",
                    ]
                    if target.field_name == "cognitive_constraints"
                    else ["summary", "value", "next_impact"]
                ),
            }
        )
    return slots

def _llm_requested_state_repair(final_adjudication: Any) -> bool:
    """Return whether the LLM final verdict selected the repair branch."""
    return (
        getattr(final_adjudication, "verdict", "") == "needs_repair"
        or bool(getattr(final_adjudication, "repair_candidate_ids", []) or [])
        or bool(getattr(final_adjudication, "repair_issues", []) or [])
    )

def _progression_texts_for_ledger(
    *,
    contract: dict[str, Any],
    audit_report: Any,
    state_adjudication_report: Any,
) -> list[str]:
    """Collect observed progression summaries after contract audit has passed."""

    final_adjudication = getattr(state_adjudication_report, "final_adjudication", None)
    accepted_ids = set(getattr(final_adjudication, "accepted_candidate_ids", []) or [])
    candidates = list(getattr(state_adjudication_report, "candidates", []) or [])
    progressions: list[str] = []
    for candidate in candidates:
        if final_adjudication is None:
            continue
        candidate_id = str(getattr(candidate, "candidate_id", "") or "").strip()
        if candidate_id not in accepted_ids:
            continue
        summary = str(getattr(candidate, "summary", "") or "").strip()
        if summary:
            progressions.append(summary)
    missing = set(
        str(item) for item in getattr(audit_report, "missing_required_progressions", []) or []
    )
    for item in list(contract.get("required_progressions", []) or []):
        text = str(item or "").strip()
        if text and text not in missing:
            progressions.append(text)
    for item in list(getattr(audit_report, "unexpected_progressions", []) or []):
        text = str(item or "").strip()
        if text:
            progressions.append(text)
    seen: set[str] = set()
    result: list[str] = []
    for item in progressions:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result

async def _extract_candidates_for_contract_audit(
    runner: Any,
    bundle: Any,
    packet: Any,
    *,
    chapter_number: int,
    current_text: str,
    chapter_contract_payload: dict[str, Any],
    current_state_payload: dict[str, Any],
    trace: Any,
) -> list[Any]:
    """Use the LLM candidate extractor as contract-audit evidence without final state writes."""

    from novel_forge.narrative_state.store import NarrativeStateStore

    store = NarrativeStateStore(bundle.layout.root)
    registry = store.ensure_character_entities(list(getattr(packet, "known_characters", []) or []))
    step = CandidateStateDeltaExtractionStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
    )
    runner._on_step(
        "candidate_state_deltas",
        {"chapter": chapter_number, "status": "starting", "source": "contract_execution_audit"},
    )
    candidates = await step.run(
        CandidateStateDeltaExtractionInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_contract=chapter_contract_payload,
            current_state=current_state_payload,
            entity_registry=registry.model_dump(mode="json"),
        )
    )
    max_candidates = int(getattr(runner._settings, "narrative_state_candidate_max_count", 0) or 0)
    omitted_count = 0
    if max_candidates > 0 and len(candidates) > max_candidates:
        omitted_count = len(candidates) - max_candidates
        candidates = candidates[:max_candidates]
    runner._on_step(
        "candidate_state_deltas",
        {
            "chapter": chapter_number,
            "count": len(candidates),
            "omitted_count": omitted_count,
            "status": "done",
            "source": "contract_execution_audit",
        },
    )
    return list(candidates)

async def _run_contract_execution_audit(
    runner: Any,
    bundle: Any,
    packet: Any,
    *,
    chapter_number: int,
    current_text: str,
    chapter_contract_payload: dict[str, Any],
    current_state_payload: dict[str, Any],
    state_adjudication_report: Any,
    trace: Any,
) -> Any | None:
    """Audit executable chapter progressions before archive/persistence."""

    settings = runner._settings
    if not bool(getattr(settings, "long_contract_audit_enabled", True)):
        runner._on_step("contract_execution_audit_skipped", {"chapter": chapter_number})
        return None
    milestone_window = dict(getattr(packet, "milestone_window", {}) or {})
    if not should_run_contract_execution_audit(
        contract=chapter_contract_payload,
        milestone_window=milestone_window,
    ):
        return None

    strictness = str(
        getattr(
            settings,
            "long_contract_audit_strictness",
            getattr(settings, "plot_progression_strictness", "block"),
        )
        or "block"
    )
    audit_step = ContractExecutionAuditStep(
        runner._router,
        runner._builder,
        settings=settings,
        trace=trace,
    )
    report = await audit_step.run(
        ContractExecutionAuditInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_contract=chapter_contract_payload,
            current_state=current_state_payload,
            milestone_window=milestone_window,
            progression_ledger_tail=list(getattr(packet, "progression_ledger_tail", []) or []),
            candidates=list(getattr(state_adjudication_report, "candidates", []) or []),
            strictness=strictness,
            future_leak_guard_enabled=bool(
                getattr(settings, "long_future_leak_guard_enabled", True)
            ),
        )
    )
    payload = report.model_dump(mode="json")
    runner._storage.save_json(bundle.layout.contract_execution_report_path(chapter_number), payload)
    runner._on_step(
        "contract_execution_audit",
        {
            "chapter": chapter_number,
            "verdict": report.verdict,
            "severity": report.severity,
            "score": report.contract_completion_score,
            "missing_required": len(report.missing_required_progressions),
            "missing_knowledge_ops": len(getattr(report, "missing_knowledge_ops", []) or []),
            "forbidden_hits": len(report.forbidden_progression_hits),
            "future_leak_hits": len(report.future_leak_hits),
            "cognitive_constraint_hits": len(
                getattr(report, "cognitive_constraint_hits", []) or []
            ),
            "decision": report.repair_or_replan_decision,
            "blocked": report.should_block_archive,
        },
    )
    if report.should_block_archive:
        reasons = [
            *report.forbidden_progression_hits,
            *report.future_leak_hits,
            *getattr(report, "cognitive_constraint_hits", []),
            *report.missing_required_progressions,
            *getattr(report, "missing_knowledge_ops", []),
        ]
        rationale_excerpt = str(getattr(report, "rationale", "") or "").strip()
        if len(rationale_excerpt) > 240:
            rationale_excerpt = rationale_excerpt[:240].rstrip("。.；;,，") + "…"
        raise ConsistencyViolationError(
            [
                "章节契约执行审计要求阻断归档："
                f"{report.repair_or_replan_decision} "
                f"(verdict={report.verdict}, severity={report.severity}, "
                f"score={report.contract_completion_score:.1f})",
                *(["LLM 裁判理由：" + rationale_excerpt] if rationale_excerpt else []),
                *[str(item) for item in reasons[:8]],
            ],
            block_kind=BLOCK_KIND_CONTRACT_AUDIT,
        )

    progressions = _progression_texts_for_ledger(
        contract=chapter_contract_payload,
        audit_report=report,
        state_adjudication_report=state_adjudication_report,
    )
    if progressions:
        try:
            raw_ledger = runner._storage.load_json(bundle.layout.progression_ledger_path)
        except Exception:
            raw_ledger = {"entries": [], "last_chapter": 0}
        ledger = append_progression_entries(
            raw_ledger,
            chapter_number=chapter_number,
            progressions=progressions,
            source="contract_execution_audit",
        )
        runner._storage.save_json(
            bundle.layout.progression_ledger_path,
            ledger.model_dump(mode="json"),
        )
        runner._on_step(
            "progression_ledger_updated",
            {"chapter": chapter_number, "entries_added": len(progressions)},
        )
    return report

async def _run_contract_execution_audit_without_state_adjudication(
    runner: Any,
    bundle: Any,
    packet: Any,
    plan: Any,
    *,
    chapter_number: int,
    current_text: str,
    trace: Any,
) -> Any | None:
    """Run contract audit when narrative-state adjudication is disabled or unavailable."""

    settings = runner._settings
    if not bool(getattr(settings, "long_contract_audit_enabled", True)):
        return None
    chapter_contract_payload = _build_chapter_contract_payload(bundle, packet, plan)
    current_state_payload = _build_current_state_payload(
        bundle,
        packet,
        chapter_number,
        plan=plan,
        chapter_contract=chapter_contract_payload,
    )
    milestone_window = dict(getattr(packet, "milestone_window", {}) or {})
    if not should_run_contract_execution_audit(
        contract=chapter_contract_payload,
        milestone_window=milestone_window,
    ):
        return None

    candidates = await _extract_candidates_for_contract_audit(
        runner,
        bundle,
        packet,
        chapter_number=chapter_number,
        current_text=current_text,
        chapter_contract_payload=chapter_contract_payload,
        current_state_payload=current_state_payload,
        trace=trace,
    )
    candidate_report = SimpleNamespace(candidates=candidates, final_adjudication=None)
    return await _run_contract_execution_audit(
        runner,
        bundle,
        packet,
        chapter_number=chapter_number,
        current_text=current_text,
        chapter_contract_payload=chapter_contract_payload,
        current_state_payload=current_state_payload,
        state_adjudication_report=candidate_report,
        trace=trace,
    )

async def extract_and_validate(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    continuity_report: Any,
    repair_exhausted: bool = False,
) -> Any:
    """Extract canon delta from chapter text and validate against continuity rules.

    Retries extraction once with violation context if initial validation fails.
    When *repair_exhausted* is True, residual high-severity continuity issues
    are downgraded to warnings so the pipeline does not enter an infinite
    replan loop.

    Returns:
        Validated ChapterOutcome.

    Raises:
        ConsistencyViolationError: If violations persist after one retry.
    """
    extract_step = ExtractCanonDeltaStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
    )
    outline_summary = f"{bundle.chapter_outline.title} - {bundle.chapter_outline.goal}"
    settings = runner._settings
    max_existing_thread_ids = max(
        0,
        int(getattr(settings, "extract_canon_max_existing_thread_ids", 30) or 0),
    )
    max_prior_relationships = max(
        0,
        int(getattr(settings, "extract_canon_max_prior_relationships", 12) or 0),
    )
    recent_character_window = max(
        0,
        int(getattr(settings, "extract_canon_recent_character_window_chapters", 5) or 0),
    )
    max_prior_characters = max(
        0,
        int(getattr(settings, "extract_canon_max_prior_characters", 12) or 0),
    )
    max_prior_plot_threads = max(
        0,
        int(getattr(settings, "extract_canon_max_prior_plot_threads", 10) or 0),
    )
    prior_plot_thread_summary_chars = max(
        0,
        int(getattr(settings, "extract_canon_prior_plot_thread_summary_chars", 60) or 0),
    )

    sorted_threads = sorted(
        bundle.canon_state.plot_threads,
        key=lambda t: t.last_touched_chapter,
        reverse=True,
    )
    existing_thread_ids = [t.thread_id for t in sorted_threads]
    if max_existing_thread_ids == 0:
        existing_thread_ids = []
    else:
        existing_thread_ids = existing_thread_ids[:max_existing_thread_ids]

    # Build compact prior-state snapshots so the LLM has a baseline for delta judgement.
    relationship_states = sorted(
        packet.active_relationships or [],
        key=lambda rel: getattr(rel, "last_updated_chapter", 0),
        reverse=True,
    )
    if max_prior_relationships == 0:
        relationship_states = []
    else:
        relationship_states = relationship_states[:max_prior_relationships]
    prior_relationships = [
        {
            "pair_id": rel.pair_id,
            "characters": rel.characters,
            "public_status": rel.public_status,
            "trust": rel.trust,
            "tension": rel.tension,
        }
        for rel in relationship_states
    ] or None
    character_states = sorted(
        [
            entity
            for entity in getattr(bundle.canon_state, "entities", [])
            if str(
                getattr(
                    getattr(entity, "entity_type", ""), "value", getattr(entity, "entity_type", "")
                )
            )
            == "character"
        ],
        key=lambda cs: getattr(cs, "last_seen_chapter", 0),
        reverse=True,
    )
    if recent_character_window > 0:
        min_last_seen_chapter = max(1, chapter_number - recent_character_window)
        character_states = [
            cs
            for cs in character_states
            if getattr(cs, "last_seen_chapter", 0) >= min_last_seen_chapter
        ]
    if max_prior_characters == 0:
        character_states = []
    else:
        character_states = character_states[:max_prior_characters]
    prior_character_snapshots = _prior_character_snapshots(character_states) or None
    plot_threads = sorted(
        (
            pt
            for pt in bundle.canon_state.plot_threads
            if pt.status in ("active", "advancing", "escalated")
        ),
        key=lambda pt: pt.last_touched_chapter,
        reverse=True,
    )
    if max_prior_plot_threads == 0:
        plot_threads = []
    else:
        plot_threads = plot_threads[:max_prior_plot_threads]
    prior_plot_threads = [
        {
            "thread_id": pt.thread_id,
            "title": pt.title,
            "status": pt.status,
            "summary": (
                pt.summary[:prior_plot_thread_summary_chars]
                if pt.summary and prior_plot_thread_summary_chars > 0
                else ""
            ),
        }
        for pt in plot_threads
    ] or None
    authoritative_character_genders = {
        str(profile.get("name", "")).strip(): normalize_gender_value(profile.get("gender", ""))
        for profile in packet.character_profiles
        if isinstance(profile, dict)
        and str(profile.get("name", "")).strip()
        and normalize_gender_value(profile.get("gender", ""))
    } or None

    runner._on_step(
        "extract_canon_start",
        {"chapter": chapter_number, "status": "starting"},
    )
    outcome = await extract_step.run(
        ExtractInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            known_characters=packet.known_characters,
            chapter_outline_summary=outline_summary,
            existing_thread_ids=existing_thread_ids,
            prior_relationships=prior_relationships,
            prior_character_snapshots=prior_character_snapshots,
            prior_plot_threads=prior_plot_threads,
            authoritative_character_genders=authoritative_character_genders,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        )
    )

    outcome = _normalize_outcome_chapter_number(runner, outcome, chapter_number)

    # Validate
    validation_result = runner._rules.validate(bundle.canon_state, outcome)
    continuity_rules = ContinuityRules()
    continuity_validation = continuity_rules.validate(
        previous_exit_state=packet.previous_exit_state,
        bridge=bridge,
        continuity_report=continuity_report,
        outcome=outcome,
        repair_exhausted=repair_exhausted,
    )
    if validation_result.warnings or continuity_validation.warnings:
        runner._on_step(
            "consistency_warnings",
            {
                "warnings": validation_result.warnings + continuity_validation.warnings,
                "chapter": chapter_number,
            },
        )
    violations = validation_result.violations + continuity_validation.violations
    narrative_state_authority_ready = getattr(
        runner._settings, "narrative_state_enabled", True
    ) and _has_persisted_chapter_contract(bundle, chapter_number)
    if violations and narrative_state_authority_ready:
        runner._on_step(
            "legacy_canon_validation_advisory",
            {
                "chapter": chapter_number,
                "violations": violations[:10],
                "authority": "llm_narrative_state",
            },
        )
        violations = []
    if violations:
        # Canon extraction retry: re-extract once with violation context
        runner._on_step(
            "canon_extract_retry",
            {"chapter": chapter_number, "violations": violations[:5]},
        )
        retry_outcome = await extract_step.run(
            ExtractInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                known_characters=packet.known_characters,
                chapter_outline_summary=outline_summary,
                prior_violations=violations[:10],
                existing_thread_ids=existing_thread_ids,
                prior_relationships=prior_relationships,
                prior_character_snapshots=prior_character_snapshots,
                prior_plot_threads=prior_plot_threads,
                authoritative_character_genders=authoritative_character_genders,
                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            )
        )
        retry_outcome = _normalize_outcome_chapter_number(
            runner,
            retry_outcome,
            chapter_number,
        )
        retry_val = runner._rules.validate(bundle.canon_state, retry_outcome)
        retry_cont = continuity_rules.validate(
            previous_exit_state=packet.previous_exit_state,
            bridge=bridge,
            continuity_report=continuity_report,
            outcome=retry_outcome,
            repair_exhausted=repair_exhausted,
        )
        retry_violations = retry_val.violations + retry_cont.violations
        if retry_violations:
            raise ConsistencyViolationError(retry_violations)
        runner._on_step("canon_extract_retry_ok", {"chapter": chapter_number})
        outcome = retry_outcome

    _persist_extracted_outcome_artifacts(runner, bundle, outcome, chapter_number)
    return outcome


def _eval_field(source: Any, name: str, default: Any = "") -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _eval_dump(source: Any) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, dict):
        return dict(source)
    if hasattr(source, "model_dump"):
        payload = source.model_dump(mode="json")
        return dict(payload) if isinstance(payload, dict) else {}
    return {}


def _short_text(value: Any, _limit: int = 240) -> str:
    return str(value or "").strip()


def _bounded_long_eval_context(
    *,
    bundle: Any,
    plan: Any | None,
    chapter_number: int,
) -> dict[str, Any]:
    """Build bounded long-form chapter context for the generic eval prompt."""

    if bundle is None:
        return {}
    outline = getattr(bundle, "chapter_outline", None)
    source_slice = _eval_dump(getattr(bundle, "chapter_source_slice", None))
    story_foundation = _eval_dump(source_slice.get("story_foundation", {}))
    creative_direction = _eval_dump(source_slice.get("creative_direction", {}))
    style_voice = _eval_dump(source_slice.get("style_voice", {}))

    def _first(*values: Any) -> str:
        for value in values:
            text = _short_text(value)
            if text:
                return text
        return ""

    genre = _first(
        story_foundation.get("genre"),
        _eval_field(getattr(bundle, "story_bible", None), "genre"),
        _eval_field(getattr(bundle, "project_spec", None), "genre"),
    )
    theme = _first(
        story_foundation.get("theme"),
        story_foundation.get("themes"),
        _eval_field(getattr(bundle, "story_bible", None), "theme"),
        _eval_field(getattr(bundle, "project_spec", None), "theme"),
    )
    opening_contract = _first(
        _eval_field(plan, "opening_contract"),
        _eval_field(_eval_field(plan, "opening_bridge", {}), "opening_contract"),
    )
    closing_contract = _first(
        _eval_field(plan, "closing_contract"),
        _eval_field(outline, "hook"),
        _eval_field(outline, "ending_hook"),
    )
    raw_scenes = list(_eval_field(plan, "scene_intents", []) or [])
    scene_intents: list[dict[str, Any]] = []
    for idx, scene in enumerate(raw_scenes, 1):
        payload = _eval_dump(scene)
        scene_intents.append(
            {
                "sequence": idx,
                "beat_type": "scene",
                "summary": _short_text(payload.get("summary"), 180),
                "purpose": _short_text(payload.get("purpose"), 140),
                "required_outcome": _short_text(payload.get("required_outcome"), 180),
                "exit_target_state": _short_text(payload.get("exit_target_state"), 180),
            }
        )
    raw_cross_scene_intent = _eval_dump(_eval_field(plan, "cross_scene_intent", {}))
    cross_scene_intent = {
        key: raw_cross_scene_intent[key]
        for key in ("cross_scene_references", "pacing_curve")
        if raw_cross_scene_intent.get(key) not in (None, "", [], {})
    }
    involved_characters = list(_eval_field(outline, "involved_characters", []) or [])
    locations = [
        _short_text(_eval_field(scene, "location"), 60)
        for scene in raw_scenes
    ]
    locations = [item for item in dict.fromkeys(locations) if item]
    chapter_goal = _first(_eval_field(outline, "goal"), _eval_field(outline, "summary"))

    context: dict[str, Any] = {}
    if genre:
        context["genre"] = genre
    if theme:
        context["theme"] = theme
    if opening_contract:
        context["opening_style"] = opening_contract
    if closing_contract:
        context["ending_style"] = closing_contract
    synopsis = _first(
        _eval_field(outline, "summary"),
        chapter_goal,
        story_foundation.get("premise"),
        creative_direction.get("chapter_intent"),
    )
    if synopsis or scene_intents:
        context["blueprint"] = {
            "synopsis": synopsis or f"第 {chapter_number} 章本章局部投影",
            "emotional_arc": _short_text(_eval_field(plan, "emotional_arc"), 180),
            "ending_strategy": closing_contract,
            "anchor_elements": {
                "time_frame": _short_text(_eval_field(outline, "time_marker"), 80),
                "primary_locations": locations,
                "core_characters": [{"name": str(name)} for name in involved_characters],
                "central_event": chapter_goal,
            },
        }
    if scene_intents:
        # beats must be a flat list (EvaluateDraftPromptContext.beats: list[Any]);
        # wrapping it in {"beats": [...]} causes a Pydantic list_type validation error.
        context["beats"] = scene_intents
        context["execution_plan"] = {
            "opening_contract": opening_contract,
            "ending_contract": closing_contract,
            "beat_execution_plan": scene_intents,
            "cross_scene_intent": cross_scene_intent,
        }
    if style_voice:
        context.setdefault("rewrite_notes", _short_text(style_voice.get("summary"), 300))
    return {key: value for key, value in context.items() if value not in ("", [], {}, None)}


def _normalize_eval_hash_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return _normalize_eval_hash_value(value.model_dump(mode="json"))
        except TypeError:
            return _normalize_eval_hash_value(value.model_dump())
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {
            str(key): _normalize_eval_hash_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_eval_hash_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_eval_hash_value(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _eval_context_hash(
    *,
    eval_context: dict[str, Any],
    kernel_context: dict[str, Any],
) -> str:
    payload = {
        "eval_context": _normalize_eval_hash_value(eval_context),
        "kernel_context": _normalize_eval_hash_value(kernel_context),
    }
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _stamp_eval_report_hashes(
    eval_report: EvalReport,
    *,
    source_hash: str,
    eval_context_hash: str,
) -> EvalReport:
    diagnostics = dict(getattr(eval_report, "score_diagnostics", {}) or {})
    diagnostics["eval_context_hash"] = eval_context_hash
    return eval_report.model_copy(
        update={
            "source_text_hash": source_hash,
            "score_diagnostics": diagnostics,
        }
    )


def _report_eval_context_hash(eval_report: EvalReport | None) -> str:
    if eval_report is None:
        return ""
    diagnostics = getattr(eval_report, "score_diagnostics", {}) or {}
    if not isinstance(diagnostics, dict):
        return ""
    return str(diagnostics.get("eval_context_hash", "") or "").strip()


def _eval_report_reusable(
    settings: Any,
    eval_report: EvalReport | None,
    *,
    source_hash: str,
    eval_context_hash: str,
) -> bool:
    if not bool(getattr(settings, "long_eval_reuse_enabled", True)):
        return False
    if eval_report is None:
        return False
    if bool(getattr(eval_report, "is_fallback", False)) or str(
        getattr(eval_report, "evaluation_status", "") or ""
    ).strip().lower() in {"fallback", "timeout", "degraded"}:
        return False
    report_hash = str(getattr(eval_report, "source_text_hash", "") or "").strip()
    if not report_hash or report_hash != source_hash:
        return False
    return _report_eval_context_hash(eval_report) == eval_context_hash


def _persist_eval_report(
    context: ChapterExecutionContext,
    *,
    bundle: Any,
    chapter_number: int,
    eval_report: EvalReport,
) -> None:
    eval_payload = eval_report.model_dump(mode="json")
    eval_payload["source_text_hash"] = getattr(eval_report, "source_text_hash", "")
    context.storage.save_json(
        bundle.layout.eval_report_path(chapter_number),
        eval_payload,
    )


async def evaluate_chapter_text(
    context: "ChapterExecutionContext",
    *,
    bundle: Any,
    chapter_number: int,
    current_text: str,
    trace: "PipelineTrace",
    emit_step: bool,
    persist: bool = True,
    extra_context: dict[str, Any] | None = None,
    plan: Any | None = None,
    reuse_report: EvalReport | None = None,
    reuse_label: str = "",
) -> EvalReport:
    """Run chapter evaluation and optionally emit the evaluate progress event.

    Args:
        persist: When False, skip saving the eval report to disk.  Use for
            pre-polish evaluation passes where the result is temporary and the
            authoritative report will be written after polish completes.

    Returns:
        EvalReport with quality metrics.
    """
    # Build base context from available bundle metadata
    eval_ctx: dict[str, Any] = {}
    try:
        if bundle is not None:
            tone = getattr(getattr(bundle, "story_bible", None), "tone", "") or ""
            if tone:
                eval_ctx["tone"] = tone
            goal = getattr(getattr(bundle, "chapter_outline", None), "goal", "") or ""
            if goal:
                eval_ctx["chapter_goal"] = goal
            target_word_count = int(
                getattr(getattr(bundle, "chapter_outline", None), "expected_word_count", 0) or 0
            )
            word_count_gate_enabled = word_count_archive_gate_enabled(context.settings)
            eval_ctx["word_count_gate_enabled"] = word_count_gate_enabled
            if target_word_count > 0 and word_count_gate_enabled:
                eval_ctx["target_word_count"] = target_word_count
            # 注入风格参数，使评估器能根据风格类型动态调整评分标准
            eval_ctx["style_profile"] = getattr(bundle, "style_profile", None)
            eval_ctx.update(
                _bounded_long_eval_context(
                    bundle=bundle,
                    plan=plan,
                    chapter_number=chapter_number,
                )
            )
    except Exception as exc:
        _logger.debug("Failed to extract eval context from bundle: %s", exc)
    if extra_context:
        eval_ctx.update(extra_context)
    kernel_composer = await load_story_kernel_composer(
        SimpleNamespace(_settings=context.settings),
        bundle,
    )
    evaluate_kernel_context = (
        kernel_composer.compose_evaluate_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    current_hash = source_text_hash(current_text)
    eval_hash = _eval_context_hash(
        eval_context=eval_ctx,
        kernel_context=evaluate_kernel_context,
    )
    if _eval_report_reusable(
        context.settings,
        reuse_report,
        source_hash=current_hash,
        eval_context_hash=eval_hash,
    ):
        eval_report = _stamp_eval_report_hashes(
            reuse_report,
            source_hash=current_hash,
            eval_context_hash=eval_hash,
        )
        if persist:
            _persist_eval_report(
                context,
                bundle=bundle,
                chapter_number=chapter_number,
                eval_report=eval_report,
            )
        on_step = getattr(context, "on_step", None)
        if callable(on_step):
            on_step(
                "evaluate_reused",
                {
                    "chapter": chapter_number,
                    "source_text_hash": current_hash,
                    "eval_context_hash": eval_hash,
                    "reuse_label": str(reuse_label or ""),
                    "persisted": persist,
                    "emitted_evaluate": emit_step,
                },
            )
        if emit_step:
            context.on_step("evaluate", eval_report.model_dump(mode="json"))
        return eval_report

    eval_step = EvaluateStep(
        context.router,
        context.builder,
        settings=context.settings,
        trace=trace,
        extra_context=eval_ctx if eval_ctx else None,
        kernel_context=evaluate_kernel_context,
    )
    eval_report = await eval_step.run(current_text)
    eval_report = _stamp_eval_report_hashes(
        eval_report,
        source_hash=current_hash,
        eval_context_hash=eval_hash,
    )
    if persist:
        _persist_eval_report(
            context,
            bundle=bundle,
            chapter_number=chapter_number,
            eval_report=eval_report,
        )
    if emit_step:
        context.on_step("evaluate", eval_report.model_dump(mode="json"))
    return eval_report


def _state_repair_eval_score(eval_report: EvalReport | None) -> float | None:
    if eval_report is None:
        return None
    try:
        return float(getattr(eval_report, "overall_score", None))
    except (TypeError, ValueError):
        return None


def _state_repair_min_accept_score(settings: Any) -> float:
    try:
        return max(0.0, float(getattr(settings, "long_min_accept_score", 5.0) or 5.0))
    except (TypeError, ValueError):
        return 5.0


def _context_from_runner(runner: Any) -> ChapterExecutionContext:
    return ChapterExecutionContext(
        storage=runner._storage,
        router=runner._router,
        builder=runner._builder,
        settings=runner._settings,
        config=runner._config,
        merger=runner._merger,
        rules=runner._rules,
        on_step=runner._on_step,
        select_character_profiles=runner._select_character_profiles,
        compact_previous_creative_report=runner._compact_previous_creative_report,
        compress_prompt_context=runner._compress_prompt_context,
        remove_opening_echo_from_previous=runner._remove_opening_echo_from_previous,
        apply_chapter_compaction=runner._apply_chapter_compaction,
        finalize_volume_if_needed=runner._finalize_volume_if_needed,
        is_outline_option_enabled_for_task=runner._is_outline_option_enabled_for_task,
        render_prompt=runner._builder.render,
    )


async def _evaluate_state_repair_candidate(
    runner: Any,
    bundle: Any,
    bridge: Any,
    chapter_number: int,
    current_text: str,
    trace: Any,
    *,
    memory_hints: dict[str, Any] | None,
) -> EvalReport:
    memory_context = await build_finalize_eval_memory_context(
        runner,
        bundle,
        chapter_number,
        memory_hints=memory_hints,
    )
    extra_context: dict[str, Any] = {"causal_link": getattr(bridge, "causal_link", None)}
    extra_context.update(memory_context)
    return await evaluate_chapter_text(
        _context_from_runner(runner),
        bundle=bundle,
        chapter_number=chapter_number,
        current_text=current_text,
        trace=trace,
        emit_step=False,
        extra_context=extra_context,
    )


async def _adjudicate_state_before_archive(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    outcome: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    continuity_report: Any,
    eval_report: EvalReport | None,
    memory_hints: dict[str, Any] | None = None,
    allow_repair: bool = True,
) -> tuple[str, Any, EvalReport | None, Any]:
    """Run LLM state adjudication before chapter text is archived."""
    chapter_contract_payload = _build_chapter_contract_payload(bundle, packet, plan)
    current_state_payload = _build_current_state_payload(
        bundle,
        packet,
        chapter_number,
        plan=plan,
        chapter_contract=chapter_contract_payload,
    )
    state_adjudication_report = await run_narrative_state_adjudication(
        router=runner._router,
        builder=runner._builder,
        settings=runner._settings,
        trace=trace,
        project_root=bundle.layout.root,
        chapter_number=chapter_number,
        chapter_text=current_text,
        chapter_contract=chapter_contract_payload,
        current_state=current_state_payload,
        known_characters=list(getattr(packet, "known_characters", []) or []),
        on_step=runner._on_step,
        report_path=bundle.layout.state_adjudication_report_path(chapter_number),
        persist_ledger=False,
    )
    max_repair_rounds = (
        int(getattr(runner._settings, "narrative_state_max_repair_rounds", 1) or 0)
        if allow_repair
        else 0
    )
    repair_round = 0
    while (
        _llm_requested_state_repair(state_adjudication_report.final_adjudication)
        and repair_round < max_repair_rounds
    ):
        repair_round += 1
        pre_repair_text = current_text
        pre_repair_outcome = outcome
        pre_repair_eval_report = eval_report
        repair_step = RepairAdjudicatedIssueStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        runner._on_step(
            "repair_adjudicated_issue",
            {
                "chapter": chapter_number,
                "round": repair_round,
                "status": "starting",
                "repair_candidate_ids": (
                    state_adjudication_report.final_adjudication.repair_candidate_ids
                ),
            },
        )
        current_text = await repair_step.run(
            RepairAdjudicatedIssueInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                final_adjudication=state_adjudication_report.final_adjudication,
                decisions=state_adjudication_report.decisions,
                candidates=state_adjudication_report.candidates,
                chapter_contract=chapter_contract_payload,
                current_state=current_state_payload,
                memory_repair_hints=memory_hints,
            )
        )
        current_text = _clean_and_validate_chapter_text(
            runner,
            bundle,
            chapter_number,
            current_text,
        )
        if current_text != pre_repair_text and pre_repair_eval_report is not None:
            repair_eval_report = await _evaluate_state_repair_candidate(
                runner,
                bundle,
                bridge,
                chapter_number,
                current_text,
                trace,
                memory_hints=memory_hints,
            )
            before_score = _state_repair_eval_score(pre_repair_eval_report)
            after_score = _state_repair_eval_score(repair_eval_report)
            min_accept_score = _state_repair_min_accept_score(runner._settings)
            if (
                after_score is not None
                and min_accept_score > 0.0
                and after_score < min_accept_score
                and (before_score is None or after_score < before_score)
            ):
                runner._on_step(
                    "state_adjudication_repair_quality_regressed",
                    {
                        "chapter": chapter_number,
                        "round": repair_round,
                        "before_score": before_score,
                        "after_score": after_score,
                        "min_accept_score": min_accept_score,
                    },
                )
                current_text = pre_repair_text
                outcome = pre_repair_outcome
                eval_report = pre_repair_eval_report
                raise ConsistencyViolationError(
                    [
                        "LLM 状态裁判要求阻断归档："
                        "状态裁判自动修复后触发质量回归，"
                        f"评估分 {after_score:.1f} 低于最低可接受线 {min_accept_score:.1f}"
                        + (
                            f"，且低于修复前 {before_score:.1f}"
                            if before_score is not None
                            else ""
                        )
                        + "；已拒绝采用该自动修复文本。"
                    ],
                    block_kind=BLOCK_KIND_EVAL_QUALITY,
                )
            eval_report = repair_eval_report
        outcome = await extract_and_validate(
            runner,
            bundle,
            packet,
            bridge,
            plan,
            current_text,
            chapter_number,
            trace,
            continuity_report,
            repair_exhausted=True,
        )
        current_state_payload = _build_current_state_payload(
            bundle,
            packet,
            chapter_number,
            plan=plan,
            chapter_contract=chapter_contract_payload,
        )
        state_adjudication_report = await run_narrative_state_adjudication(
            router=runner._router,
            builder=runner._builder,
            settings=runner._settings,
            trace=trace,
            project_root=bundle.layout.root,
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_contract=chapter_contract_payload,
            current_state=current_state_payload,
            known_characters=list(getattr(packet, "known_characters", []) or []),
            on_step=runner._on_step,
            report_path=bundle.layout.state_adjudication_report_path(chapter_number),
            persist_ledger=False,
        )
        if current_text == pre_repair_text:
            eval_report = pre_repair_eval_report
        elif pre_repair_eval_report is None:
            eval_report = None
        runner._on_step(
            "repair_adjudicated_issue",
            {
                "chapter": chapter_number,
                "round": repair_round,
                "status": "done",
                "still_blocked": (
                    state_adjudication_report.final_adjudication.should_block_archive
                    or _llm_requested_state_repair(state_adjudication_report.final_adjudication)
                ),
            },
        )
    if state_adjudication_report.final_adjudication.should_block_archive:
        final = state_adjudication_report.final_adjudication
        _repair_exhausted = (
            max_repair_rounds > 0 and repair_round >= max_repair_rounds
        )
        if _repair_exhausted:
            _logger.warning(
                "state_adjudication_block_downgraded_after_repair_exhausted | "
                "chapter=%d | rounds=%d/%d | verdict=%s | severity=%s | "
                "confidence=%.2f | summary=%s",
                chapter_number,
                repair_round,
                max_repair_rounds,
                final.verdict,
                final.severity,
                final.confidence,
                final.summary,
            )
            runner._on_step(
                "state_adjudication_downgraded_to_warning",
                {
                    "chapter": chapter_number,
                    "reason": "repair_rounds_exhausted",
                    "rounds_used": repair_round,
                    "max_rounds": max_repair_rounds,
                    "verdict": final.verdict,
                    "severity": final.severity,
                    "confidence": final.confidence,
                    "summary": final.summary,
                },
            )
        else:
            raise ConsistencyViolationError(
                [
                    "LLM 状态裁判要求阻断归档："
                    f"{final.summary or final.verdict} "
                    f"(severity={final.severity}, confidence={final.confidence:.2f})"
                ],
                block_kind=BLOCK_KIND_STATE_ADJUDICATION,
            )
    if _llm_requested_state_repair(state_adjudication_report.final_adjudication):
        final = state_adjudication_report.final_adjudication
        _logger.warning(
            "state_adjudication_non_blocking_repair_remaining | chapter=%d | "
            "verdict=%s | severity=%s | confidence=%.2f | summary=%s",
            chapter_number,
            final.verdict,
            final.severity,
            final.confidence,
            final.summary,
        )
        runner._on_step(
            "state_adjudication_non_blocking_repair_remaining",
            {
                "chapter": chapter_number,
                "verdict": final.verdict,
                "severity": final.severity,
                "confidence": final.confidence,
                "repair_candidate_ids": list(final.repair_candidate_ids or []),
                "summary": final.summary,
            },
        )
    await _run_contract_execution_audit(
        runner,
        bundle,
        packet,
        chapter_number=chapter_number,
        current_text=current_text,
        chapter_contract_payload=chapter_contract_payload,
        current_state_payload=current_state_payload,
        state_adjudication_report=state_adjudication_report,
        trace=trace,
    )
    return current_text, outcome, eval_report, state_adjudication_report

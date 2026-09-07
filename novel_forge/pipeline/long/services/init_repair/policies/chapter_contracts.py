"""Chapter-contract repair policy for long initialization."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.narrative_state.schemas import ChapterContract
from novel_forge.pipeline.long.services.context.source_artifacts import project_init_entity_catalog
from novel_forge.pipeline.long.services.init_repair.models import (
    InitArtifact,
    InitRepairContext,
    InitRepairIssue,
    InitRepairIssueKind,
    InitRepairReport,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

_log = logging.getLogger(__name__)

_CHAPTER_CONTRACT_LIST_FIELDS = (
    "entry_state_requirements",
    "required_events",
    "allowed_changes",
    "forbidden_changes",
    "promise_ops",
    "relationship_ops",
    "item_ops",
    "knowledge_ops",
    "cognitive_constraints",
    "new_character_candidates",
    "new_entity_candidates",
    "new_group_candidates",
    "new_collective_candidates",
    "new_organization_candidates",
    "new_location_candidates",
    "new_item_candidates",
    "new_concept_candidates",
    "exit_state_targets",
    "required_progressions",
    "allowed_progressions",
    "forbidden_progressions",
    "completion_criteria",
    "future_leak_risks",
    "involved_character_ids",
    "required_character_ids",
    "support_character_ids",
    "scene_design_goals",
)

_CHAPTER_CONTRACT_STRING_FIELDS = (
    "entry_state_requirements",
    "required_events",
    "allowed_changes",
    "forbidden_changes",
    "exit_state_targets",
    "required_progressions",
    "allowed_progressions",
    "forbidden_progressions",
    "completion_criteria",
    "future_leak_risks",
)

_CONTRACT_BUDGET_DEFAULTS = {
    "dynamic_enabled": True,
    "hard_words_per_item": 1000,
    "hard_min_items": 3,
    "hard_max_items": 6,
    "soft_words_per_item": 900,
    "soft_max_items": 5,
    "state_words_per_item": 1200,
    "state_max_items": 4,
}


class ChapterContractsRepairPolicy:
    """Repair strategy for executable per-chapter narrative contracts."""

    artifact = InitArtifact.CHAPTER_CONTRACTS

    def normalize(self, payload: Any, ctx: InitRepairContext) -> dict[str, Any]:
        outline = _outline_from_context(ctx)
        raw_payload = payload if isinstance(payload, dict) else {"chapter_contracts": []}
        normalized, _coverage = ensure_chapter_contract_coverage(
            raw_payload,
            outline,
            backfill_missing=False,
            settings=getattr(ctx.service_ctx, "settings", None) if ctx.service_ctx else None,
        )
        return normalized

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport:
        outline = _outline_from_context(ctx)
        raw_payload = payload if isinstance(payload, dict) else {"chapter_contracts": []}
        normalized, coverage = ensure_chapter_contract_coverage(
            raw_payload,
            outline,
            backfill_missing=False,
            settings=getattr(ctx.service_ctx, "settings", None) if ctx.service_ctx else None,
        )
        errors: list[str] = []
        warnings: list[str] = []
        issues: list[InitRepairIssue] = []

        raw_items = raw_payload.get("chapter_contracts")
        if not isinstance(raw_items, list):
            errors.append("chapter_contracts 必须是列表。")
            issues.append(
                InitRepairIssue(
                    kind=InitRepairIssueKind.SCHEMA_SHAPE,
                    message=errors[-1],
                    field="chapter_contracts",
                )
            )

        missing = list(coverage.get("missing_chapters") or [])
        backfilled = list(coverage.get("backfilled_chapters") or [])
        local_accepted = bool(coverage.get("local_fallback_accepted"))
        if missing:
            errors.append(f"章节契约缺失章节：{missing}")
            issues.append(
                InitRepairIssue(
                    kind=InitRepairIssueKind.MISSING_EXECUTION_GRAPH,
                    message=errors[-1],
                    field="chapter_contracts",
                    metadata={"chapters": missing},
                )
            )
        if backfilled and not local_accepted:
            errors.append(f"章节契约包含未确认的本地回填章节：{backfilled}")
            issues.append(
                InitRepairIssue(
                    kind=InitRepairIssueKind.MISSING_EXECUTION_GRAPH,
                    message=errors[-1],
                    field="chapter_contracts",
                    metadata={"chapters": backfilled},
                )
            )
        if int(coverage.get("discarded_count") or 0) > 0:
            warnings.append(f"章节契约丢弃了 {coverage['discarded_count']} 个越界或非法条目。")
            issues.append(
                InitRepairIssue(
                    kind=InitRepairIssueKind.RANGE_OR_COVERAGE,
                    message=warnings[-1],
                    field="chapter_contracts",
                    severity="warning",
                    metadata={"discarded_count": coverage.get("discarded_count")},
                )
            )
        if int(coverage.get("duplicate_count") or 0) > 0:
            warnings.append(f"章节契约丢弃了 {coverage['duplicate_count']} 个重复章节条目。")
            issues.append(
                InitRepairIssue(
                    kind=InitRepairIssueKind.RANGE_OR_COVERAGE,
                    message=warnings[-1],
                    field="chapter_contracts",
                    severity="warning",
                    metadata={"duplicate_count": coverage.get("duplicate_count")},
                )
            )

        for item in normalized.get("chapter_contracts", []) or []:
            try:
                ChapterContract.model_validate(item)
            except ValidationError as exc:
                chapter_number = item.get("chapter_number") if isinstance(item, dict) else None
                message = f"章节契约 schema 非法：chapter={chapter_number} error={exc}"
                errors.append(message)
                issues.append(
                    InitRepairIssue(
                        kind=InitRepairIssueKind.SCHEMA_SHAPE,
                        message=message,
                        field="chapter_contracts",
                        metadata={"chapter_number": chapter_number},
                    )
                )

        return InitRepairReport(errors=errors, warnings=warnings, issues=issues, raw=coverage)

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> dict[str, Any]:
        service_ctx = ctx.service_ctx
        if service_ctx is None:
            raise RuntimeError("chapter contract LLM repair requires service context")
        outline = _outline_from_context(ctx)
        repair_numbers = _repair_chapter_numbers(report, outline=outline)
        if not repair_numbers:
            raise RuntimeError("chapter contract LLM repair has no focused chapters")

        chapters = [
            chapter
            for chapter in sorted(outline.chapters, key=lambda item: int(item.chapter_number))
            if int(chapter.chapter_number) in repair_numbers
        ]
        if not chapters:
            raise RuntimeError("chapter contract LLM repair chapters are outside outline")

        repair_payload = _chapter_contract_repair_outline_payload(
            outline,
            chapters,
            settings=service_ctx.settings,
            entity_catalog=ctx.artifacts.get("entity_catalog"),
        )
        max_tokens = calculate_route_aware_max_tokens(
            service_ctx.router,
            TaskType.PLAN_CHAPTER_CONTRACTS,
            max(2400, len(chapters) * 850),
            prompt_overhead=3200,
            min_tokens=4096,
        )
        repaired = await service_ctx.call_with_retry(
            TaskType.PLAN_CHAPTER_CONTRACTS,
            {
                "narrative_contract": ctx.artifacts.get("narrative_contract", {}),
                "outline": repair_payload,
            },
            max_tokens=max_tokens,
            temperature=getattr(service_ctx.settings, "temp_plan_chapter_contracts", 0.25),
            required_keys=("chapter_contracts",),
            max_retries=3,
        )
        return _merge_repaired_chapter_contracts(payload, repaired, repair_numbers)

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> dict[str, Any] | None:
        outline = _outline_from_context(ctx)
        raw_payload = payload if isinstance(payload, dict) else {"chapter_contracts": []}
        repaired, _coverage = ensure_chapter_contract_coverage(
            raw_payload,
            outline,
            backfill_missing=True,
            accept_local_fallback=True,
            settings=getattr(ctx.service_ctx, "settings", None) if ctx.service_ctx else None,
        )
        return repaired


def _outline_from_context(ctx: InitRepairContext) -> StoryOutline:
    outline = ctx.artifacts.get("outline")
    if isinstance(outline, StoryOutline):
        return outline
    if isinstance(outline, dict):
        return StoryOutline.model_validate(outline)
    raise ValueError("chapter contract repair requires outline artifact")


def _repair_chapter_numbers(report: InitRepairReport, *, outline: StoryOutline) -> list[int]:
    numbers: list[int] = []
    for issue in report.issues:
        for chapter in issue.metadata.get("chapters", []) or []:
            try:
                number = int(chapter)
            except (TypeError, ValueError):
                continue
            if number not in numbers:
                numbers.append(number)
    if numbers:
        return sorted(numbers)
    return sorted(int(chapter.chapter_number) for chapter in outline.chapters)


def _chapter_contract_repair_outline_payload(
    outline: StoryOutline,
    chapters: list[Any],
    *,
    settings: Any | None = None,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    numbers = [int(chapter.chapter_number) for chapter in chapters]
    payload = {
        "total_chapters": outline.total_chapters,
        "volume_mode": outline.volume_mode,
        "synopsis": outline.synopsis,
        "chapters": [
            chapter.model_dump(mode="json") if hasattr(chapter, "model_dump") else dict(chapter)
            for chapter in chapters
        ],
        "contract_batch": {
            "batch_start": min(numbers) if numbers else 0,
            "batch_end": max(numbers) if numbers else 0,
            "chapter_numbers": numbers,
        },
        "contract_scaffold": [
            _chapter_contract_scaffold_for_llm(chapter, settings=settings) for chapter in chapters
        ],
        "contract_scaffold_policy": (
            "遵守每章 contract_budget：只保留最关键硬约束；超出预算的细节应合并或省略，"
            "不要转写进 allowed_* 造成软性降级。输出 source 必须为 plan_chapter_contracts。"
        ),
    }
    catalog_limit = int(getattr(settings, "chapter_contract_entity_catalog_max_entities", 96) or 96)
    payload["entity_catalog"] = project_init_entity_catalog(
        entity_catalog,
        payload,
        max_entities=catalog_limit,
    )
    return payload


def _merge_repaired_chapter_contracts(
    original: Any,
    repaired: Any,
    repair_numbers: list[int],
) -> dict[str, Any]:
    original_items = []
    if isinstance(original, dict) and isinstance(original.get("chapter_contracts"), list):
        for item in original.get("chapter_contracts", []):
            if not isinstance(item, dict):
                continue
            try:
                chapter_number = int(item.get("chapter_number", 0) or 0)
            except (TypeError, ValueError):
                continue
            if chapter_number not in repair_numbers:
                original_items.append(item)
    repaired_items = []
    if isinstance(repaired, dict) and isinstance(repaired.get("chapter_contracts"), list):
        repaired_items = [
            item for item in repaired.get("chapter_contracts", []) if isinstance(item, dict)
        ]
    return {"chapter_contracts": [*original_items, *repaired_items]}


def _contract_list(value: Any) -> list[Any]:
    """Normalize contract fields to compact lists."""
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    else:
        return [value]
    return [
        item for item in items if item is not None and (not isinstance(item, str) or item.strip())
    ]


def _settings_bool(settings: Any | None, attr: str, default: bool) -> bool:
    value = getattr(settings, attr, default) if settings is not None else default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _settings_int(
    settings: Any | None,
    attr: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = getattr(settings, attr, default) if settings is not None else default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _ceil_div(value: int, divisor: int) -> int:
    return max(1, (max(1, value) + max(1, divisor) - 1) // max(1, divisor))


def _chapter_expected_word_count(chapter: Any) -> int:
    try:
        expected = int(getattr(chapter, "expected_word_count", 0) or 0)
    except (TypeError, ValueError):
        expected = 0
    return max(1, expected)


def _chapter_contract_budget(chapter: Any, *, settings: Any | None = None) -> dict[str, Any]:
    enabled = _settings_bool(
        settings,
        "chapter_contract_dynamic_budget_enabled",
        bool(_CONTRACT_BUDGET_DEFAULTS["dynamic_enabled"]),
    )
    hard_min = _settings_int(
        settings,
        "chapter_contract_hard_min_items",
        int(_CONTRACT_BUDGET_DEFAULTS["hard_min_items"]),
        minimum=1,
        maximum=12,
    )
    hard_max = _settings_int(
        settings,
        "chapter_contract_hard_max_items",
        int(_CONTRACT_BUDGET_DEFAULTS["hard_max_items"]),
        minimum=1,
        maximum=12,
    )
    if hard_min > hard_max:
        hard_min, hard_max = hard_max, hard_min
    soft_max = _settings_int(
        settings,
        "chapter_contract_soft_max_items",
        int(_CONTRACT_BUDGET_DEFAULTS["soft_max_items"]),
        minimum=1,
        maximum=12,
    )
    state_max = _settings_int(
        settings,
        "chapter_contract_state_max_items",
        int(_CONTRACT_BUDGET_DEFAULTS["state_max_items"]),
        minimum=1,
        maximum=12,
    )
    hard_words = _settings_int(
        settings,
        "chapter_contract_hard_words_per_item",
        int(_CONTRACT_BUDGET_DEFAULTS["hard_words_per_item"]),
        minimum=200,
        maximum=5000,
    )
    soft_words = _settings_int(
        settings,
        "chapter_contract_soft_words_per_item",
        int(_CONTRACT_BUDGET_DEFAULTS["soft_words_per_item"]),
        minimum=200,
        maximum=5000,
    )
    state_words = _settings_int(
        settings,
        "chapter_contract_state_words_per_item",
        int(_CONTRACT_BUDGET_DEFAULTS["state_words_per_item"]),
        minimum=200,
        maximum=5000,
    )
    expected_words = _chapter_expected_word_count(chapter)
    if not enabled:
        return {
            "enabled": False,
            "expected_word_count": expected_words,
            "hard_required_limit": 30,
            "soft_allowed_limit": 30,
            "state_target_limit": 30,
            "hard_words_per_item": hard_words,
            "soft_words_per_item": soft_words,
            "state_words_per_item": state_words,
        }

    hard_limit = max(hard_min, min(hard_max, _ceil_div(expected_words, hard_words)))
    soft_limit = max(1, min(soft_max, _ceil_div(expected_words, soft_words)))
    state_limit = max(1, min(state_max, _ceil_div(expected_words, state_words)))
    return {
        "enabled": True,
        "expected_word_count": expected_words,
        "hard_required_limit": hard_limit,
        "soft_allowed_limit": soft_limit,
        "state_target_limit": state_limit,
        "hard_words_per_item": hard_words,
        "soft_words_per_item": soft_words,
        "state_words_per_item": state_words,
    }


def _dedupe_contract_items(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = str(item).strip() if isinstance(item, str) else repr(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _trim_list(value: Any, *, limit: int) -> list[Any]:
    return _dedupe_contract_items(_contract_list(value))[: max(0, limit)]


def _budget_chapter_contract(
    contract: dict[str, Any],
    chapter: Any,
    *,
    settings: Any | None = None,
    merge_hard_fields: bool = True,
) -> dict[str, Any]:
    budget = _chapter_contract_budget(chapter, settings=settings)
    if not budget.get("enabled"):
        return contract
    hard_limit = int(budget["hard_required_limit"])
    soft_limit = int(budget["soft_allowed_limit"])
    state_limit = int(budget["state_target_limit"])

    required_events_limit = max(1, min(hard_limit, 3))
    if merge_hard_fields:
        hard_items = _dedupe_contract_items(
            _contract_list(contract.get("required_progressions"))
            + _contract_list(contract.get("required_events"))
        )[:hard_limit]
        if not hard_items:
            hard_items = _trim_list(getattr(chapter, "main_plot_points", []), limit=hard_limit)
        if not hard_items:
            hard_items = _trim_list(getattr(chapter, "goal", ""), limit=hard_limit)
        contract["required_progressions"] = hard_items
        contract["required_events"] = hard_items[:required_events_limit]
    else:
        contract["required_progressions"] = _trim_list(
            contract.get("required_progressions"),
            limit=hard_limit,
        )
        contract["required_events"] = _trim_list(
            contract.get("required_events"),
            limit=required_events_limit,
        )
        hard_items = _dedupe_contract_items(
            _contract_list(contract.get("required_progressions"))
            + _contract_list(contract.get("required_events"))
        )[:hard_limit]
    contract["allowed_changes"] = _trim_list(contract.get("allowed_changes"), limit=soft_limit)
    contract["allowed_progressions"] = _trim_list(
        contract.get("allowed_progressions"),
        limit=soft_limit,
    )
    contract["exit_state_targets"] = _trim_list(
        contract.get("exit_state_targets"), limit=state_limit
    )
    criteria = _trim_list(contract.get("completion_criteria"), limit=state_limit)
    if not criteria:
        criteria = hard_items[:state_limit]
    contract["completion_criteria"] = criteria
    return contract


def _raw_outline_backfill_chapter_contract(chapter: Any) -> dict[str, Any]:
    """Build the full deterministic contract before attention budgeting."""
    chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
    title = str(getattr(chapter, "title", "") or "")
    goal = str(getattr(chapter, "goal", "") or "").strip()
    required_events = _contract_list(getattr(chapter, "main_plot_points", []))
    for beat in _contract_list(getattr(chapter, "beats_summary", [])):
        if beat not in required_events:
            required_events.append(beat)
    if goal and goal not in required_events:
        required_events.insert(0, goal)

    exit_state_targets: list[Any] = []
    hook = getattr(chapter, "expected_hook", None)
    hook_description = str(getattr(hook, "hook_description", "") or "").strip()
    if hook_description:
        exit_state_targets.append(hook_description)

    allowed_changes = _contract_list(getattr(chapter, "subplot_points", []))

    contract = {
        "chapter_number": chapter_number,
        "title": title,
        "entry_state_requirements": [],
        "required_events": required_events,
        "allowed_changes": allowed_changes,
        "forbidden_changes": [],
        "promise_ops": [],
        "relationship_ops": [],
        "item_ops": [],
        "knowledge_ops": [],
        "cognitive_constraints": [],
        "new_character_candidates": [],
        "new_entity_candidates": [],
        "new_group_candidates": [],
        "new_collective_candidates": [],
        "new_organization_candidates": [],
        "new_location_candidates": [],
        "new_item_candidates": [],
        "new_concept_candidates": [],
        "exit_state_targets": exit_state_targets,
        "required_progressions": list(required_events),
        "allowed_progressions": list(allowed_changes),
        "forbidden_progressions": [],
        "completion_criteria": list(exit_state_targets) or list(required_events[:2]),
        "future_leak_risks": [],
        "pov_character_id": str(getattr(chapter, "pov_character_id", "") or ""),
        "involved_character_ids": _contract_list(getattr(chapter, "involved_character_ids", [])),
        "required_character_ids": _contract_list(getattr(chapter, "required_character_ids", [])),
        "support_character_ids": _contract_list(getattr(chapter, "support_character_ids", [])),
        "cast_plan": (
            getattr(chapter, "cast_plan", {}).model_dump(mode="json")
            if hasattr(getattr(chapter, "cast_plan", None), "model_dump")
            else getattr(chapter, "cast_plan", {})
        ),
        "emotional_plan": (
            getattr(chapter, "emotional_plan", {}).model_dump(mode="json")
            if hasattr(getattr(chapter, "emotional_plan", None), "model_dump")
            else getattr(chapter, "emotional_plan", {})
        ),
        "scene_design_goals": _contract_list(getattr(chapter, "scene_design_goals", [])),
        "source": "outline_backfill",
    }
    return contract


def _outline_backfill_chapter_contract(
    chapter: Any,
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Build a deterministic per-chapter contract from the accepted outline."""
    return _budget_chapter_contract(
        _raw_outline_backfill_chapter_contract(chapter),
        chapter,
        settings=settings,
    )


def _chapter_contract_scaffold_for_llm(
    chapter: Any,
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Build an LLM-facing scaffold without local-fallback source markers."""
    scaffold = _outline_backfill_chapter_contract(chapter, settings=settings)
    scaffold.pop("source", None)
    scaffold["contract_budget"] = _chapter_contract_budget(chapter, settings=settings)
    return scaffold


def _is_outline_backfill_contract(
    item: dict[str, Any],
    chapter: Any,
    *,
    settings: Any | None = None,
) -> bool:
    if str(item.get("source") or "") != "outline_backfill":
        return False
    for defaults in (
        _outline_backfill_chapter_contract(chapter, settings=settings),
        _raw_outline_backfill_chapter_contract(chapter),
    ):
        if str(item.get("title") or defaults["title"]) != str(defaults["title"]):
            continue
        if all(
            _contract_list(item.get(field)) == _contract_list(defaults.get(field))
            for field in _CHAPTER_CONTRACT_STRING_FIELDS
        ):
            return True
    return False


def _normalize_chapter_contract_item(
    item: dict[str, Any],
    chapter: Any,
    *,
    default_source: str,
    settings: Any | None = None,
) -> dict[str, Any]:
    defaults = _outline_backfill_chapter_contract(chapter, settings=settings)
    normalized = dict(defaults)
    normalized.update(item)
    normalized["chapter_number"] = defaults["chapter_number"]
    if not str(normalized.get("title", "") or "").strip():
        normalized["title"] = defaults["title"]
    for field in _CHAPTER_CONTRACT_LIST_FIELDS:
        normalized[field] = _contract_list(normalized.get(field))
    source = str(item.get("source") or default_source)
    if source == "outline_backfill" and default_source != "outline_backfill":
        source = default_source
    normalized["source"] = source
    return _budget_chapter_contract(
        normalized,
        chapter,
        settings=settings,
        merge_hard_fields=False,
    )


def ensure_chapter_contract_coverage(
    chapter_contracts: dict[str, Any],
    outline: StoryOutline,
    *,
    backfill_missing: bool = True,
    accept_local_fallback: bool = False,
    settings: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ensure or report whether every accepted outline chapter has a contract."""
    raw_items = chapter_contracts.get("chapter_contracts", [])
    if not isinstance(raw_items, list):
        raw_items = []

    outline_by_number = {
        int(chapter.chapter_number): chapter
        for chapter in outline.chapters
        if int(chapter.chapter_number) > 0
    }
    expected_numbers = sorted(outline_by_number)
    by_number: dict[int, dict[str, Any]] = {}
    discarded_count = 0
    duplicate_count = 0
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            discarded_count += 1
            continue
        try:
            number = int(raw_item.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            discarded_count += 1
            continue
        if number not in outline_by_number:
            discarded_count += 1
            continue
        if number in by_number:
            duplicate_count += 1
            continue
        by_number[number] = raw_item

    backfilled_numbers: list[int] = []
    missing_numbers: list[int] = []
    normalized_items: list[dict[str, Any]] = []
    for number in expected_numbers:
        chapter = outline_by_number[number]
        item = by_number.get(number)
        if item is None:
            missing_numbers.append(number)
            if not backfill_missing:
                continue
            item = _outline_backfill_chapter_contract(chapter, settings=settings)
            backfilled_numbers.append(number)
            default_source = "outline_backfill"
        else:
            default_source = "plan_chapter_contracts"
            if _is_outline_backfill_contract(item, chapter, settings=settings):
                backfilled_numbers.append(number)
                default_source = "outline_backfill"
        normalized_items.append(
            _normalize_chapter_contract_item(
                item,
                chapter,
                default_source=default_source,
                settings=settings,
            )
        )

    payload = dict(chapter_contracts)
    coverage = {
        "expected_chapters": len(expected_numbers),
        "contract_count": len(normalized_items),
        "missing_chapters": missing_numbers if not backfill_missing else [],
        "backfilled_chapters": backfilled_numbers,
        "discarded_count": discarded_count,
        "duplicate_count": duplicate_count,
        "complete": len(normalized_items) == len(expected_numbers),
    }
    previous_coverage = chapter_contracts.get("coverage")
    previous_local_fallback = (
        bool(previous_coverage.get("local_fallback_accepted"))
        if isinstance(previous_coverage, dict)
        else False
    )
    if accept_local_fallback or previous_local_fallback:
        coverage["local_fallback_accepted"] = True
    payload["chapter_contracts"] = normalized_items
    payload["coverage"] = coverage
    return payload, coverage

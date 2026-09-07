"""Implementation slice extracted from init_service.py (init_chapter_contracts.py)."""

from __future__ import annotations

from novel_forge.common.severity import SEVERITY_RANK, normalize_severity
from novel_forge.core.exceptions import ContextLengthError, ModelGatewayError
from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
    project_cognitive_constraints,
)
from novel_forge.pipeline.long.services.init.init_common import (
    CHAPTER_CONTRACTS_ARTIFACT,
    CLAIM_LEDGER_JSON,
    STATUS_SUCCEEDED,
    Any,
    ChapterContract,
    CharacterBible,
    CoherenceClaim,
    EntityRegistry,
    InitArtifact,
    InitCoherenceError,
    InitLongServiceContext,
    InitRepairContext,
    InitRepairOrchestrator,
    InitRepairOutcome,
    Iterator,
    PipelineConstants,
    Sequence,
    StoryBible,
    StoryOutline,
    StorySpec,
    TaskType,
    _chapter_contract_scaffold_for_llm,
    _contract_list,
    _ensure_chapter_contract_coverage,
    _log,
    asyncio,
    build_plot_milestone_index,
    calculate_route_aware_max_tokens,
    cast,
    dataclass,
    dump_story_bible_for_prompt,
    effective_init_batch_size,
    get_init_repair_policy,
    hash_payload,
    json,
    manifest_for_context,
    normalize_artifact_key,
    normalize_chapter_contract_entity_references,
    normalize_llm_narrative_contract,
    outline_h,
    project_init_entity_catalog,
    re,
    readiness_payload_allows,
    record_effective_init_batch_size,
    route_max_output_budget,
)
from novel_forge.pipeline.long.services.init.init_repair_manifest import (
    _persist_init_repair_outcome,
)
from novel_forge.pipeline.long.services.init.init_value_helpers import (
    _canonicalize_character_knowledge_coverage,
    _canonicalize_entity_name_list,
    _entity_catalog_lookup,
)


@dataclass(frozen=True)
class ChapterContractResumeResult:
    """Reusable chapter-contract cache plus optional focused rebuild work."""

    payload: dict[str, Any]
    coverage: dict[str, Any]
    reusable_chapters: list[int]
    rebuild_chapters: list[int]
    full_rebuild_reason: str = ""

    def __iter__(self) -> Iterator[Any]:
        yield self.payload
        yield self.coverage


def _chapter_contract_batch_error_allows_split(exc: BaseException) -> bool:
    """Return whether reducing the batch can address this failure.

    Content/shape failures can improve with a smaller output surface. Provider
    transport, authentication, capacity, and DNS failures cannot; recursively
    splitting those only burns routes and hides the durable resume checkpoint.
    """

    if isinstance(exc, ContextLengthError):
        return True
    if not isinstance(exc, ModelGatewayError):
        return True
    context = getattr(exc, "context", None)
    raw_categories = context.get("failure_categories", []) if isinstance(context, dict) else []
    categories = {
        str(item or "").strip()
        for item in raw_categories or []
        if str(item or "").strip()
    }
    split_categories = {"context_length", "stream_inflation"}
    return bool(categories) and categories.issubset(split_categories)


async def _generate_llm_narrative_contract(
    ctx: InitLongServiceContext,
    *,
    spec: StorySpec,
    story_bible: StoryBible,
    character_bible: CharacterBible,
    entity_registry: EntityRegistry,
) -> dict[str, Any]:
    """Generate the adjudication contract with the dedicated LLM step."""

    response = await ctx.call_with_retry(
        TaskType.INIT_NARRATIVE_CONTRACT,
        {
            "spec": spec.model_dump(mode="json"),
            "story_bible": dump_story_bible_for_prompt(story_bible, mode="json"),
            "character_bible": character_bible.model_dump(mode="json"),
            "entity_registry": entity_registry.model_dump(mode="json"),
        },
        max_tokens=calculate_route_aware_max_tokens(
            ctx.router,
            TaskType.INIT_NARRATIVE_CONTRACT,
            9000,
            prompt_overhead=7000,
            min_tokens=4096,
        ),
        temperature=getattr(ctx.settings, "temp_init_narrative_contract", 0.25),
        required_keys=(
            "world_rules",
            "character_arcs",
            "plot_threads",
            "promise_plan",
            "notes",
        ),
        max_retries=3,
    )
    llm_contract = normalize_llm_narrative_contract(response)
    if not any(
        llm_contract.get(key)
        for key in ("world_rules", "character_arcs", "plot_threads", "promise_plan")
    ):
        raise ValueError("INIT_NARRATIVE_CONTRACT returned an empty adjudication contract")
    return llm_contract


def _load_reusable_chapter_contracts(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
    narrative_contract: dict[str, Any] | None = None,
    entity_catalog: dict[str, Any] | None = None,
    outline_research_grounding: dict[str, Any] | None = None,
    strict_noise: bool = True,
    force_rebuild_chapters: Sequence[int] | None = None,
) -> ChapterContractResumeResult | None:
    """Load cached chapter contracts if they still cover the current outline."""

    path = ctx.layout.plans_dir / "chapter_contracts.json"
    if not ctx.storage.exists(path):
        return None
    forced_rebuild = {
        number
        for number in (_safe_int(chapter) for chapter in (force_rebuild_chapters or []))
        if number > 0
    }
    try:
        raw_payload = ctx.storage.load_json(path)
        chapter_contracts, coverage = _ensure_chapter_contract_coverage(
            raw_payload,
            outline,
            settings=getattr(ctx, "settings", None),
        )
        # Cached payloads may predate the cognitive_constraints backfill or
        # may have been produced by an older LLM run that dropped entries.
        # Re-run the deterministic backfill against the current claim ledger
        # so the audit has a fair chance of passing on resume.
        backfill_map = _init_claim_constraints_by_chapter(
            ctx,
            entity_catalog=entity_catalog,
        )
        chapter_contracts = _backfill_cognitive_constraints(
            chapter_contracts,
            backfill_map,
            settings=getattr(ctx, "settings", None),
            entity_catalog=entity_catalog,
        )
        schema_invalid_chapters: list[int] = []
        for item in chapter_contracts.get("chapter_contracts", []) or []:
            try:
                ChapterContract.model_validate(item)
            except Exception:
                if strict_noise:
                    raise
                try:
                    chapter_number = int(item.get("chapter_number", 0) or 0)
                except Exception:
                    chapter_number = 0
                if chapter_number > 0:
                    schema_invalid_chapters.append(chapter_number)
        expected_hashes = _chapter_contract_input_hashes(
            outline=outline,
            narrative_contract=narrative_contract,
            outline_research_grounding=outline_research_grounding,
        )
        if expected_hashes:
            manifest = manifest_for_context(ctx)
            record = manifest.matching_record(
                "chapter_contracts",
                input_hashes=expected_hashes,
                output_hashes={"chapter_contracts": hash_payload(chapter_contracts)},
                statuses={STATUS_SUCCEEDED},
                allow_reusable_failure=False,
            )
            if record is None:
                input_record = (
                    manifest.matching_record(
                        "chapter_contracts",
                        input_hashes=expected_hashes,
                        statuses={STATUS_SUCCEEDED},
                        allow_reusable_failure=False,
                    )
                    if forced_rebuild
                    else None
                )
                if input_record is None:
                    _log.info("chapter_contracts_resume_skipped | reason=input_manifest_mismatch")
                    return None
                _log.info(
                    "chapter_contracts_resume_partial | reason=source_artifact_rebuild_scope | "
                    "rebuild=%s",
                    sorted(forced_rebuild),
                )
    except Exception as exc:
        _log.warning("chapter_contracts_resume_invalid | path=%s | error=%s", path, exc)
        return None
    quality_issues = _chapter_contract_resume_quality_issues(
        chapter_contracts,
        coverage,
        strict_noise=strict_noise,
    )
    partition = partition_chapter_contracts_for_resume(
        chapter_contracts,
        coverage,
        outline=outline,
        schema_invalid_chapters=schema_invalid_chapters if not strict_noise else None,
        force_rebuild_chapters=forced_rebuild,
    )
    if quality_issues:
        if not strict_noise and not partition.full_rebuild_reason:
            _log.info(
                "chapter_contracts_resume_partial | reusable=%s | rebuild=%s | coverage=%s",
                partition.reusable_chapters,
                partition.rebuild_chapters,
                coverage,
            )
            return partition
        _log.info(
            "chapter_contracts_resume_skipped | reason=quality_issues | issues=%s | coverage=%s",
            quality_issues,
            coverage,
        )
        return None
    if forced_rebuild:
        _log.info(
            "chapter_contracts_resume_partial | reusable=%s | rebuild=%s | source=source_artifacts",
            partition.reusable_chapters,
            partition.rebuild_chapters,
        )
    return partition


def _source_artifact_resume_rebuild_chapters(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
) -> list[int]:
    """Return chapter numbers that source-artifact readiness says must be rebuilt."""

    readiness_path = ctx.layout.reports_dir / "init_readiness.json"
    if not ctx.storage.exists(readiness_path):
        return []
    try:
        readiness = ctx.storage.load_json(readiness_path)
    except Exception as exc:
        _log.debug(
            "source_artifact_resume_scope_load_failed | path=%s | error=%s", readiness_path, exc
        )
        return []
    if not isinstance(readiness, dict) or readiness_payload_allows(readiness):
        return []
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return []
    source_stage = stages.get("source_artifacts")
    if not isinstance(source_stage, dict) or not bool(source_stage.get("blocked", False)):
        return []
    for stage_name, stage in stages.items():
        if stage_name == "source_artifacts" or not isinstance(stage, dict):
            continue
        if bool(stage.get("blocked", False)):
            return []

    expected = {_safe_int(chapter.chapter_number) for chapter in outline.chapters}
    expected.discard(0)
    chapters: set[int] = set()

    def _collect_from_issue(issue: Any) -> None:
        if not isinstance(issue, dict):
            return
        scopes = issue.get("repair_scope")
        if isinstance(scopes, list):
            for scope in scopes:
                if not isinstance(scope, dict):
                    continue
                artifact = normalize_artifact_key(scope.get("artifact"))
                if artifact and artifact != CHAPTER_CONTRACTS_ARTIFACT:
                    continue
                for chapter in scope.get("chapters") or []:
                    number = _safe_int(chapter)
                    if number in expected:
                        chapters.add(number)
        text = " ".join(
            str(issue.get(key) or "") for key in ("description", "message", "summary", "path")
        )
        for match in re.finditer(r"(?:第\s*)?(\d+)\s*章|by_chapter\.(\d+)", text):
            number = _safe_int(match.group(1) or match.group(2))
            if number in expected:
                chapters.add(number)

    for issue in source_stage.get("issues") or []:
        _collect_from_issue(issue)
    for issue in readiness.get("remaining_issues") or []:
        if isinstance(issue, dict) and issue.get("stage") == "source_artifacts":
            _collect_from_issue(issue)
    return sorted(chapters)


def _chapter_contract_input_hashes(
    *,
    outline: StoryOutline,
    narrative_contract: dict[str, Any] | None,
    outline_research_grounding: dict[str, Any] | None = None,
) -> dict[str, str]:
    if narrative_contract is None:
        return {}
    hashes = {
        "outline": hash_payload(outline),
        "narrative_contract": hash_payload(narrative_contract),
    }
    if outline_research_grounding:
        hashes["outline_research_grounding"] = hash_payload(outline_research_grounding)
    return hashes


def _chapter_contract_quality_issues(
    chapter_contracts: dict[str, Any],
    coverage: dict[str, Any],
    *,
    strict_noise: bool,
) -> list[str]:
    """Return reasons a chapter-contract payload should not be treated as clean."""

    issues: list[str] = []
    if not coverage.get("complete"):
        issues.append("incomplete_coverage")
    if coverage.get("local_fallback_accepted"):
        issues.append("local_fallback_accepted")
    backfilled = list(coverage.get("backfilled_chapters") or [])
    backfilled.extend(coverage.get("batch_backfilled_chapters") or [])
    if backfilled:
        issues.append("backfilled_chapters")
    if strict_noise:
        if int(coverage.get("discarded_count") or 0) > 0:
            issues.append("discarded_items")
        if int(coverage.get("duplicate_count") or 0) > 0:
            issues.append("duplicate_items")
    return sorted(set(issues))


def _chapter_contract_resume_quality_issues(
    chapter_contracts: dict[str, Any],
    coverage: dict[str, Any],
    *,
    strict_noise: bool,
) -> list[str]:
    """Return quality issues that should block reuse of persisted contracts."""
    quality_issues = _chapter_contract_quality_issues(
        chapter_contracts,
        coverage,
        strict_noise=strict_noise,
    )
    if coverage.get("local_fallback_accepted"):
        quality_issues = [
            issue
            for issue in quality_issues
            if issue not in {"backfilled_chapters", "local_fallback_accepted"}
        ]
    return quality_issues


def partition_chapter_contracts_for_resume(
    chapter_contracts: dict[str, Any],
    coverage: dict[str, Any],
    *,
    outline: StoryOutline,
    schema_invalid_chapters: Sequence[int] | None = None,
    force_rebuild_chapters: Sequence[int] | set[int] | None = None,
) -> ChapterContractResumeResult:
    """Split reusable cached chapter contracts from chapters needing focused rebuild."""

    expected = sorted(int(chapter.chapter_number) for chapter in outline.chapters)
    expected_set = set(expected)
    backfilled_for_rebuild = (
        []
        if coverage.get("local_fallback_accepted")
        else [
            *(coverage.get("backfilled_chapters") or []),
            *(coverage.get("batch_backfilled_chapters") or []),
        ]
    )
    rebuild = {
        _safe_int(chapter)
        for chapter in [
            *(coverage.get("missing_chapters") or []),
            *backfilled_for_rebuild,
            *(schema_invalid_chapters or []),
            *(force_rebuild_chapters or []),
        ]
    }
    rebuild = {chapter for chapter in rebuild if chapter in expected_set}
    items_by_chapter: dict[int, dict[str, Any]] = {}
    for item in list(chapter_contracts.get("chapter_contracts") or []):
        if not isinstance(item, dict):
            continue
        chapter_number = _safe_int(item.get("chapter_number"))
        if chapter_number in expected_set and chapter_number not in rebuild:
            items_by_chapter.setdefault(chapter_number, item)
    reusable = [chapter for chapter in expected if chapter in items_by_chapter]
    full_rebuild_reason = ""
    if rebuild and not reusable:
        full_rebuild_reason = "no_reusable_chapter_contracts"
    elif int(coverage.get("discarded_count") or 0) > 0 and not items_by_chapter:
        full_rebuild_reason = "discarded_all_cached_contracts"
    payload = {
        **{key: value for key, value in chapter_contracts.items() if key != "chapter_contracts"},
        "chapter_contracts": [items_by_chapter[chapter] for chapter in reusable],
    }
    return ChapterContractResumeResult(
        payload=payload,
        coverage=dict(coverage),
        reusable_chapters=reusable,
        rebuild_chapters=sorted(rebuild),
        full_rebuild_reason=full_rebuild_reason,
    )


def merge_reusable_and_rebuilt_chapter_contracts(
    reusable_payload: dict[str, Any],
    rebuilt_payload: dict[str, Any],
    *,
    outline: StoryOutline,
    settings: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge clean cached contracts with focused regenerated contracts."""

    by_chapter: dict[int, dict[str, Any]] = {}
    for source in (reusable_payload, rebuilt_payload):
        for item in list(source.get("chapter_contracts") or []):
            if not isinstance(item, dict):
                continue
            chapter_number = _safe_int(item.get("chapter_number"))
            if chapter_number > 0:
                by_chapter[chapter_number] = item
    expected = sorted(int(chapter.chapter_number) for chapter in outline.chapters)
    merged = {
        **{key: value for key, value in reusable_payload.items() if key != "chapter_contracts"},
        "chapter_contracts": [
            by_chapter[chapter_number]
            for chapter_number in expected
            if chapter_number in by_chapter
        ],
    }
    return _ensure_chapter_contract_coverage(merged, outline, settings=settings)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _assert_chapter_contracts_ready_to_persist(
    chapter_contracts: dict[str, Any],
    coverage: dict[str, Any],
    *,
    allow_local_fallback: bool = False,
) -> None:
    quality_issues = _chapter_contract_quality_issues(
        chapter_contracts,
        coverage,
        strict_noise=False,
    )
    if allow_local_fallback and coverage.get("local_fallback_accepted"):
        quality_issues = [
            issue
            for issue in quality_issues
            if issue not in {"backfilled_chapters", "local_fallback_accepted"}
        ]
    if quality_issues:
        raise InitCoherenceError(
            "章节契约生成不完整，已停止写入下游产物："
            f"{'、'.join(quality_issues)}。请重试该步骤以避免复用截断/降级结果。"
        )


def _normalize_chapter_contracts_cognitive_subjects(
    chapter_contracts: dict[str, Any],
    *,
    entity_catalog: dict[str, Any] | None = None,
) -> None:
    """Normalize high-confidence entity references before contract persistence.

    Mutates chapter_contracts in-place before persistence, ensuring corrupted
    or unresolvable optional cognitive references do not poison source-artifact
    readiness while real unknown entities remain blockable.
    """
    normalize_chapter_contract_entity_references(
        chapter_contracts,
        entity_catalog=entity_catalog,
    )


def _synchronize_chapter_contract_cast_plans(
    chapter_contracts: dict[str, Any],
    outline: StoryOutline,
) -> list[int]:
    """Project authoritative outline identity fields onto chapter contracts.

    ``cast_plan`` originates in the deterministic chapter design matrix.  LLM
    contract generation and late resume must not preserve an older copy after
    the entity graph changes, otherwise ids can remain syntactically valid while
    referring to a different entity.
    """

    by_chapter = {int(chapter.chapter_number): chapter for chapter in outline.chapters}
    changed: list[int] = []
    raw_contracts = chapter_contracts.get("chapter_contracts")
    if not isinstance(raw_contracts, list):
        return changed
    for contract in raw_contracts:
        if not isinstance(contract, dict):
            continue
        try:
            chapter_number = int(contract.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        chapter = by_chapter.get(chapter_number)
        if chapter is None:
            continue
        authoritative = {
            "pov_character_id": str(chapter.pov_character_id or ""),
            "involved_character_ids": list(chapter.involved_character_ids or []),
            "required_character_ids": list(chapter.required_character_ids or []),
            "support_character_ids": list(chapter.support_character_ids or []),
            "cast_plan": chapter.cast_plan.model_dump(mode="json"),
        }
        if all(contract.get(key) == value for key, value in authoritative.items()):
            continue
        contract.update(authoritative)
        changed.append(chapter_number)
    return changed


def _normalize_unregistered_sequential_item_ids(
    chapter_contracts: dict[str, Any],
    *,
    entity_catalog: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Remove false identity from LLM-invented sequential item ids.

    Hash-derived ids and declared ``new_*_candidates`` carry creation lineage and
    remain untouched.  A value such as ``item_19`` that is absent from the
    authoritative catalog has no such lineage.  If its operation description
    names exactly one canonical item we repair the id; otherwise we clear only
    the false id and preserve the descriptive operation for downstream prose.
    """

    if not entity_catalog:
        return []
    allowed_entities = [
        item for item in entity_catalog.get("allowed_entities", []) or [] if isinstance(item, dict)
    ]
    allowed_ids = {
        str(item.get("entity_id") or "").strip()
        for item in allowed_entities
        if str(item.get("entity_id") or "").strip()
    }
    item_aliases: list[tuple[str, tuple[str, ...]]] = []
    for item in allowed_entities:
        if str(item.get("entity_type") or "").strip() != "item":
            continue
        entity_id = str(item.get("entity_id") or "").strip()
        names = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in [item.get("canonical_name"), *(item.get("aliases") or [])]
                if str(value or "").strip()
            )
        )
        if entity_id and names:
            item_aliases.append((entity_id, names))

    changes: list[dict[str, Any]] = []
    contracts = chapter_contracts.get("chapter_contracts")
    if not isinstance(contracts, list):
        return changes
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        candidate_ids = {
            str(candidate.get("entity_id") or candidate.get("id") or "").strip()
            for field in ("new_item_candidates", "new_entity_candidates")
            for candidate in (contract.get(field) or [])
            if isinstance(candidate, dict)
        }
        chapter_number = _safe_int(contract.get("chapter_number"))
        for index, operation in enumerate(contract.get("item_ops") or []):
            if not isinstance(operation, dict):
                continue
            entity_id = str(operation.get("entity_id") or "").strip()
            prefix, separator, suffix = entity_id.partition("_")
            if (
                not entity_id
                or entity_id in allowed_ids
                or entity_id in candidate_ids
                or separator != "_"
                or prefix not in {"item", "ent"}
                or not suffix.isdigit()
            ):
                continue
            description = str(operation.get("description") or "").strip()
            matches = {
                canonical_id
                for canonical_id, names in item_aliases
                if any(name in description for name in names)
            }
            replacement = next(iter(matches)) if len(matches) == 1 else ""
            operation["entity_id"] = replacement
            changes.append(
                {
                    "chapter_number": chapter_number,
                    "item_op_index": index,
                    "old_entity_id": entity_id,
                    "new_entity_id": replacement,
                    "action": "map_exact_description" if replacement else "clear_false_identity",
                }
            )
    return changes


def _persist_chapter_contract_runtime_artifacts(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
    chapter_contracts: dict[str, Any],
    llm_contract: dict[str, Any],
    project_id: str,
    entity_catalog: dict[str, Any] | None = None,
    outline_research_grounding: dict[str, Any] | None = None,
) -> Any:
    cast_plan_changes = _synchronize_chapter_contract_cast_plans(
        chapter_contracts,
        outline,
    )
    on_step = getattr(ctx, "on_step", None)
    if cast_plan_changes and callable(on_step):
        on_step(
            "chapter_contract_cast_plan_synced",
            {
                "chapters": cast_plan_changes,
                "count": len(cast_plan_changes),
                "source": "chapter_design_matrix",
            },
        )
    item_id_changes = _normalize_unregistered_sequential_item_ids(
        chapter_contracts,
        entity_catalog=entity_catalog,
    )
    if item_id_changes and callable(on_step):
        on_step(
            "chapter_contract_item_ids_normalized",
            {
                "changes": item_id_changes,
                "count": len(item_id_changes),
                "source": "authoritative_entity_catalog",
            },
        )
    _normalize_chapter_contracts_cognitive_subjects(
        chapter_contracts, entity_catalog=entity_catalog
    )
    ctx.storage.save_json(ctx.layout.plans_dir / "chapter_contracts.json", chapter_contracts)
    partial_path = _chapter_contract_partial_path(ctx)
    if partial_path is not None:
        partial_path.unlink(missing_ok=True)
    manifest_for_context(ctx).record_success(
        artifact="chapter_contracts",
        workflow="init_long",
        step="plan_chapter_contracts",
        input_hashes=_chapter_contract_input_hashes(
            outline=outline,
            narrative_contract=llm_contract,
            outline_research_grounding=outline_research_grounding,
        ),
        output_hashes={"chapter_contracts": hash_payload(chapter_contracts)},
        paths={"chapter_contracts": str(ctx.layout.plans_dir / "chapter_contracts.json")},
        metadata={"project_id": project_id},
    )
    milestone_index = build_plot_milestone_index(
        outline=outline,
        chapter_contracts=chapter_contracts,
        narrative_contract=llm_contract,
        project_id=project_id,
    )
    ctx.storage.save_json(
        ctx.layout.plot_milestone_index_path,
        milestone_index.model_dump(mode="json"),
    )
    if not ctx.storage.exists(ctx.layout.progression_ledger_path):
        ctx.storage.save_json(
            ctx.layout.progression_ledger_path,
            {"entries": [], "last_chapter": 0},
        )
    return milestone_index


async def _validate_and_persist_repaired_chapter_contracts(
    ctx: InitLongServiceContext,
    *,
    payload: dict[str, Any],
    outline: StoryOutline,
    outline_ctx: dict[str, Any],
    total_chapters: int,
    narrative_complexity: Any,
    llm_contract: dict[str, Any],
    project_id: str,
    entity_catalog: dict[str, Any] | None,
    failure_prefix: str,
    outline_research_grounding: dict[str, Any] | None = None,
    allow_local_fallback: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], Any, InitRepairOutcome]:
    repair_outcome = await InitRepairOrchestrator(
        get_init_repair_policy(InitArtifact.CHAPTER_CONTRACTS)
    ).repair(
        payload,
        InitRepairContext(
            service_ctx=ctx,
            outline_ctx=outline_ctx,
            total_chapters=total_chapters,
            narrative_complexity=narrative_complexity,
            artifacts={
                "outline": outline,
                "narrative_contract": llm_contract,
                "project_id": project_id,
                "entity_catalog": entity_catalog or {},
            },
        ),
    )
    _persist_init_repair_outcome(
        ctx,
        artifact="chapter_contracts",
        outcome=repair_outcome,
    )
    if not repair_outcome.report.is_valid:
        raise InitCoherenceError(
            f"{failure_prefix}后章节契约仍未通过：{'; '.join(repair_outcome.report.errors)}"
        )
    chapter_contracts = cast(dict[str, Any], repair_outcome.payload)
    chapter_contracts, coverage = _ensure_chapter_contract_coverage(
        chapter_contracts,
        outline,
        settings=ctx.settings,
    )
    chapter_contracts["coverage"] = coverage
    _assert_chapter_contracts_ready_to_persist(
        chapter_contracts,
        coverage,
        allow_local_fallback=allow_local_fallback,
    )
    milestone_index = _persist_chapter_contract_runtime_artifacts(
        ctx,
        outline=outline,
        chapter_contracts=chapter_contracts,
        llm_contract=llm_contract,
        project_id=project_id,
        entity_catalog=entity_catalog,
        outline_research_grounding=outline_research_grounding,
    )
    return chapter_contracts, coverage, milestone_index, repair_outcome


def _effective_chapter_contract_batch_size(
    ctx: InitLongServiceContext,
    total_chapters: int,
) -> int:
    """Return the v3 effective chapter-contract batch size."""
    batch_size = effective_init_batch_size(
        ctx,
        task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
        total_chapters=total_chapters,
    )
    record_effective_init_batch_size(
        ctx,
        artifact="chapter_contracts",
        task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
        batch_size=batch_size,
        total_chapters=total_chapters,
    )
    return batch_size


def _chapter_contract_batch_outline_payload(
    outline: StoryOutline,
    batch_chapters: list[Any],
    *,
    previous_contracts: list[dict[str, Any]] | None = None,
    context_window: int = 2,
    settings: Any | None = None,
    init_claim_constraints_by_chapter: dict[int, list[dict[str, Any]]] | None = None,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact outline payload scoped to one chapter-contract batch."""
    numbers = [int(chapter.chapter_number) for chapter in batch_chapters]
    if not numbers:
        return outline.model_dump(mode="json")
    start = min(numbers)
    end = max(numbers)
    scoped_volumes: list[dict[str, Any]] = []
    for volume in outline.volumes:
        volume_start = int(getattr(volume, "start_chapter", 0) or 0)
        volume_end = int(getattr(volume, "end_chapter", 0) or 0)
        if volume_start <= end and volume_end >= start:
            scoped_volumes.append(volume.model_dump(mode="json"))
    outline_by_number = {
        int(chapter.chapter_number): chapter
        for chapter in outline.chapters
        if int(chapter.chapter_number) > 0
    }
    window = max(0, int(context_window or 0))
    previous_contract_limit = window * 2
    previous_numbers = [
        number for number in range(start - window, start) if number in outline_by_number
    ]
    next_numbers = [
        number for number in range(end + 1, end + window + 1) if number in outline_by_number
    ]
    payload = {
        "total_chapters": outline.total_chapters,
        "volume_mode": outline.volume_mode,
        "synopsis": outline.synopsis,
        "volumes": scoped_volumes,
        "chapters": [
            chapter.model_dump(mode="json") if hasattr(chapter, "model_dump") else dict(chapter)
            for chapter in batch_chapters
        ],
        "contract_batch": {
            "batch_start": start,
            "batch_end": end,
            "chapter_numbers": numbers,
        },
        "contract_scaffold": [
            _chapter_contract_scaffold_for_llm(chapter, settings=settings)
            for chapter in batch_chapters
        ],
        "contract_scaffold_policy": (
            "遵守每章 contract_budget：只保留最关键硬约束；超出预算的细节应合并或省略，"
            "不要转写进 allowed_* 造成软性降级。输出 source 必须为 plan_chapter_contracts。"
        ),
        "context_chapters": {
            "previous": [
                _compact_chapter_outline_for_contracts(outline_by_number[number])
                for number in previous_numbers
            ],
            "next": [
                _functional_future_chapter_outline_for_contracts(outline_by_number[number])
                for number in next_numbers
            ],
        },
        "previous_chapter_contracts": [
            _compact_chapter_contract_for_context(item)
            for item in (
                (previous_contracts or [])[-previous_contract_limit:]
                if previous_contract_limit
                else []
            )
            if isinstance(item, dict)
        ],
        "init_claim_constraints": _select_init_claim_constraints_for_batch(
            init_claim_constraints_by_chapter,
            numbers,
        ),
    }
    catalog_limit = int(getattr(settings, "chapter_contract_entity_catalog_max_entities", 96) or 96)
    payload["entity_catalog"] = project_init_entity_catalog(
        entity_catalog,
        payload,
        max_entities=catalog_limit,
    )
    return payload


def _backfill_cognitive_constraints(
    chapter_contracts: dict[str, Any],
    constraints_by_chapter: Any,
    *,
    settings: Any | None = None,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure every init_claim_constraint is reflected in the contract.

    The LLM is told to copy init_claim_constraints into
    ``chapter_contracts[*].cognitive_constraints`` verbatim, but in practice
    it sometimes drops entries (or writes them into a type-specific field
    like ``required_events``). The audit then flags the missing claim_ids
    and the whole init run fails.

    This helper performs a deterministic pass that:

    1. Collects every ``claim_id`` already present in each contract's
       ``cognitive_constraints``.
    2. Walks ``constraints_by_chapter`` for the same chapter_number and
       adds any missing constraint as a faithful copy.

    When ``init_claim_constraints_backfill_enabled`` is False, the function
    is a no-op so callers can opt into LLM-only output.
    """
    if not bool(getattr(settings, "init_claim_constraints_backfill_enabled", True)):
        return chapter_contracts
    if not constraints_by_chapter:
        return chapter_contracts
    items = chapter_contracts.get("chapter_contracts")
    if not isinstance(items, list):
        return chapter_contracts

    added_total = 0
    repaired_total = 0
    chapters_touched: list[int] = []
    # Some callers (notably the LLM payload builder) hand us a map keyed
    # by stringified chapter numbers to match the JSON contract. Accept
    # both shapes so the backfill works for every entry point.
    lookup_keys: dict[int, list[dict[str, Any]]] = {}
    for key, value in (constraints_by_chapter or {}).items():
        if isinstance(value, list):
            lookup_keys[_positive_int(key)] = value
            if isinstance(key, str) and key.isdigit():
                lookup_keys[int(key)] = value
    for raw in items:
        if not isinstance(raw, dict):
            continue
        chapter_number = _positive_int(raw.get("chapter_number"))
        if not chapter_number:
            continue
        constraints_for_chapter = lookup_keys.get(chapter_number) or []
        if not constraints_for_chapter:
            continue
        existing_constraints = raw.get("cognitive_constraints")
        if not isinstance(existing_constraints, list):
            existing_constraints = []
            raw["cognitive_constraints"] = existing_constraints
        # Build a lookup from claim_id -> ledger constraint so same-id
        # rows are canonicalized back to the authoritative cognitive
        # anchors if the LLM rewrote or blanked any semantic field.
        ledger_by_id: dict[str, dict[str, Any]] = {}
        for constraint in constraints_for_chapter:
            if not isinstance(constraint, dict):
                continue
            cid = str(constraint.get("claim_id") or "")
            if cid and cid not in ledger_by_id:
                ledger_by_id[cid] = constraint
        canonicalized = 0
        appended = 0
        for index, item in enumerate(existing_constraints):
            if not isinstance(item, dict):
                continue
            cid = str(item.get("claim_id") or "")
            if not cid or cid not in ledger_by_id:
                continue
            merged = _merge_cognitive_constraint_from_ledger(ledger_by_id[cid], item)
            if merged != item:
                existing_constraints[index] = merged
                canonicalized += 1
        existing_ids = {
            str(item.get("claim_id") or "")
            for item in existing_constraints
            if isinstance(item, dict) and str(item.get("claim_id") or "")
        }
        for constraint in constraints_for_chapter:
            if not isinstance(constraint, dict):
                continue
            claim_id = str(constraint.get("claim_id") or "")
            if not claim_id or claim_id in existing_ids:
                continue
            existing_constraints.append(dict(constraint))
            existing_ids.add(claim_id)
            appended += 1
        if appended or canonicalized:
            chapters_touched.append(chapter_number)
            added_total += appended
            repaired_total += canonicalized
    if added_total or repaired_total:
        chapter_contracts["coverage"] = {
            **(chapter_contracts.get("coverage") or {}),
            "claim_constraint_backfill": {
                "added": added_total,
                "canonicalized": repaired_total,
                "chapters": chapters_touched,
            },
        }
    _normalize_chapter_contracts_cognitive_subjects(
        chapter_contracts, entity_catalog=entity_catalog
    )
    return chapter_contracts


def _merge_cognitive_constraint_from_ledger(
    ledger_constraint: dict[str, Any],
    existing_constraint: dict[str, Any],
) -> dict[str, Any]:
    """Return a ledger-authoritative cognitive constraint row.

    The LLM is not allowed to rewrite strong cognitive anchors. Preserve
    any unknown extension keys from the existing row, then overlay the
    canonical ledger projection so same-``claim_id`` rows cannot drift in
    subject/object/level/time semantics.
    """
    merged = dict(existing_constraint)
    merged.update(ledger_constraint)
    return merged


def _positive_int(value: Any) -> int:
    try:
        return int(value) if int(value) > 0 else 0
    except (TypeError, ValueError):
        return 0


def _select_init_claim_constraints_for_batch(
    constraints_by_chapter: dict[int, list[dict[str, Any]]] | None,
    numbers: list[int],
) -> dict[str, list[dict[str, Any]]]:
    """Project every chapter-scoped claim into a compact executable card.

    Lightness comes from dropping provenance-only fields, not from taking a
    local prefix.  If a provider cannot carry a multi-chapter batch, the
    caller's existing batch-split path reduces chapter count while retaining
    complete constraints for every remaining chapter.
    """
    selected: dict[str, list[dict[str, Any]]] = {}
    for number in numbers:
        constraints = (constraints_by_chapter or {}).get(number) or []
        if not constraints:
            continue
        selected[str(number)] = _cognitive_constraint_prompt_projection(constraints)
    return selected


def _select_init_claim_constraints_for_backfill(
    constraints_by_chapter: dict[int, list[dict[str, Any]]] | None,
    numbers: list[int],
) -> dict[str, list[dict[str, Any]]]:
    """Return the complete authoritative projection used for backfill."""

    return {
        str(number): [dict(item) for item in (constraints_by_chapter or {}).get(number, [])]
        for number in numbers
        if (constraints_by_chapter or {}).get(number)
    }


def _cognitive_constraint_prompt_projection(value: Any) -> list[dict[str, Any]]:
    """Keep every semantic field while omitting provenance-only prompt weight."""

    fields = (
        "claim_id",
        "claim_text",
        "cognitive_subjects",
        "cognitive_object",
        "cognitive_level",
        "action_level",
        "reader_awareness",
        "character_knowledge_coverage",
        "cognitive_chapter",
        "public_reveal_chapter",
        "foreshadow_chapters",
    )
    return [
        {field: item.get(field) for field in fields}
        for item in project_cognitive_constraints(value)
    ]


def _truncate_contract_text(value: Any, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _compact_list_for_contract_context(value: Any, *, limit: int = 3) -> list[str]:
    items = _contract_list(value)
    return [_truncate_contract_text(item, limit=90) for item in items[: max(0, limit)]]


def _project_cognitive_constraints_for_audit(
    value: Any,
) -> list[dict[str, Any]]:
    return _cognitive_constraint_prompt_projection(value)


def _compact_chapter_outline_for_contracts(chapter: Any) -> dict[str, Any]:
    hook = getattr(chapter, "expected_hook", None)
    hook_description = _truncate_contract_text(getattr(hook, "hook_description", ""), limit=100)
    payoffs = []
    for payoff in _contract_list(getattr(chapter, "expected_payoffs", []))[:2]:
        payoffs.append(_truncate_contract_text(getattr(payoff, "description", payoff), limit=90))
    cast_plan = getattr(chapter, "cast_plan", {})
    if hasattr(cast_plan, "model_dump"):
        cast_plan = cast_plan.model_dump(mode="json")
    emotional_plan = getattr(chapter, "emotional_plan", {})
    if hasattr(emotional_plan, "model_dump"):
        emotional_plan = emotional_plan.model_dump(mode="json")
    return {
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "title": _truncate_contract_text(getattr(chapter, "title", ""), limit=60),
        "goal": _truncate_contract_text(getattr(chapter, "goal", ""), limit=140),
        "pov_character_id": _truncate_contract_text(
            getattr(chapter, "pov_character_id", ""),
            limit=80,
        ),
        "pov_character_name": _truncate_contract_text(
            getattr(chapter, "pov_character_name", "") or getattr(chapter, "pov_character", ""),
            limit=40,
        ),
        "pov_character": _truncate_contract_text(getattr(chapter, "pov_character", ""), limit=40),
        "involved_character_ids": list(getattr(chapter, "involved_character_ids", []) or []),
        "required_character_ids": list(getattr(chapter, "required_character_ids", []) or []),
        "support_character_ids": list(getattr(chapter, "support_character_ids", []) or []),
        "cast_plan": cast_plan if isinstance(cast_plan, dict) else {},
        "emotional_plan": emotional_plan if isinstance(emotional_plan, dict) else {},
        "scene_design_goals": _compact_list_for_contract_context(
            getattr(chapter, "scene_design_goals", []),
            limit=5,
        ),
        "setting": _truncate_contract_text(getattr(chapter, "setting", ""), limit=80),
        "time_anchor": _truncate_contract_text(getattr(chapter, "time_anchor", ""), limit=60),
        "main_plot_points": _compact_list_for_contract_context(
            getattr(chapter, "main_plot_points", []),
            limit=3,
        ),
        "subplot_points": _compact_list_for_contract_context(
            getattr(chapter, "subplot_points", []),
            limit=2,
        ),
        "expected_hook": hook_description,
        "expected_payoffs": payoffs,
    }


def _functional_future_chapter_outline_for_contracts(chapter: Any) -> dict[str, Any]:
    """Future context for contract planning: functional only, no concrete payoff detail."""
    compact = _compact_chapter_outline_for_contracts(chapter)
    return {
        "chapter_number": compact.get("chapter_number", 0),
        "title": compact.get("title", ""),
        "goal_function": _truncate_contract_text(compact.get("goal", ""), limit=90),
        "pov_character_id": compact.get("pov_character_id", ""),
        "pov_character": compact.get("pov_character", ""),
        "cast_plan": compact.get("cast_plan", {}),
        "emotional_plan": compact.get("emotional_plan", {}),
        "setting_type": compact.get("setting", ""),
        "future_boundary": "仅用于判断本批契约不要提前完成该章功能；不得复制具体触发方式。",
        "payoff_policy": "future_payoff_withheld",
    }


def _compact_chapter_contract_for_context(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_number": int(item.get("chapter_number", 0) or 0),
        "title": _truncate_contract_text(item.get("title", ""), limit=60),
        "required_events": _compact_list_for_contract_context(
            item.get("required_events", []),
            limit=3,
        ),
        "required_progressions": _compact_list_for_contract_context(
            item.get("required_progressions", []),
            limit=2,
        ),
        "forbidden_changes": _compact_list_for_contract_context(
            item.get("forbidden_changes", []),
            limit=3,
        ),
        "forbidden_progressions": _compact_list_for_contract_context(
            item.get("forbidden_progressions", []),
            limit=2,
        ),
        "future_leak_risks": _compact_list_for_contract_context(
            item.get("future_leak_risks", []),
            limit=2,
        ),
        "exit_state_targets": _compact_list_for_contract_context(
            item.get("exit_state_targets", []),
            limit=2,
        ),
    }


def _summarize_chapter_contract_response(raw_response: str) -> str:
    """Summarize a chapter-contract batch for multi-turn continuity."""
    try:
        payload = json.loads(raw_response)
    except Exception:
        return _truncate_contract_text(raw_response, limit=1600)
    items = payload.get("chapter_contracts")
    if not isinstance(items, list):
        return _truncate_contract_text(raw_response, limit=1600)
    lines = ["[上一批章节契约摘要]"]
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        number = item.get("chapter_number", "?")
        title = _truncate_contract_text(item.get("title", ""), limit=40)
        events = "；".join(
            _compact_list_for_contract_context(item.get("required_events", []), limit=2)
        )
        exits = "；".join(
            _compact_list_for_contract_context(item.get("exit_state_targets", []), limit=1)
        )
        lines.append(f"第{number}章《{title}》：必须={events or '略'}；出口={exits or '略'}")
    return "\n".join(lines)


def _append_chapter_contract_history(
    conversation_history: list[dict[str, str]],
    *,
    batch_start: int,
    batch_end: int,
    raw_response: str,
) -> None:
    conversation_history.append(
        {
            "role": "user",
            "content": (
                f"[章节契约批次] 已完成第{batch_start}-{batch_end}章。"
                "后续批次需延续这些约束，但只输出当前请求章节。"
            ),
        }
    )
    conversation_history.append(
        {
            "role": "assistant",
            "content": _summarize_chapter_contract_response(raw_response),
        }
    )


def _compact_chapter_contract_for_adjudication(item: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_chapter_contract_for_context(item)
    for field in (
        "entry_state_requirements",
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
        "required_progressions",
        "allowed_progressions",
        "forbidden_progressions",
        "completion_criteria",
        "future_leak_risks",
    ):
        compact[field] = _compact_list_for_contract_context(item.get(field, []), limit=3)
    compact["cognitive_constraints"] = _project_cognitive_constraints_for_audit(
        item.get("cognitive_constraints", []),
    )
    return compact


def _compact_chapter_contract_index_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_number": int(item.get("chapter_number", 0) or 0),
        "title": _truncate_contract_text(item.get("title", ""), limit=60),
        "required_events": _compact_list_for_contract_context(
            item.get("required_events", []),
            limit=1,
        ),
        "exit_state_targets": _compact_list_for_contract_context(
            item.get("exit_state_targets", []),
            limit=1,
        ),
    }


def _init_claim_constraints_by_chapter(
    ctx: InitLongServiceContext,
    *,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    storage = getattr(ctx, "storage", None)
    layout = getattr(ctx, "layout", None)
    memory_dir = getattr(layout, "memory_dir", None)
    if storage is None or memory_dir is None:
        return {}
    ledger_path = memory_dir / CLAIM_LEDGER_JSON
    if not storage.exists(ledger_path):
        return {}
    try:
        ledger = storage.load_json(ledger_path)
    except Exception as exc:
        _log.debug("init_claim_constraints_ledger_load_failed | error=%s", exc)
        return {}
    if not isinstance(ledger, dict):
        return {}
    claims_by_id = ledger.get("claims_by_id")
    if not isinstance(claims_by_id, dict):
        return {}
    active_ids = [
        str(claim_id)
        for claim_id in (ledger.get("active_claim_ids") or [])
        if isinstance(claim_id, str)
    ]
    if not active_ids:
        active_ids = [
            str(claim_id)
            for claim_id, entry in claims_by_id.items()
            if isinstance(claim_id, str)
            and isinstance(entry, dict)
            and entry.get("status") == "active"
        ]

    by_chapter: dict[int, list[dict[str, Any]]] = {}
    for claim_id in active_ids:
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict) or entry.get("status") != "active":
            continue
        try:
            claim = CoherenceClaim.model_validate(entry)
        except Exception as exc:
            _log.debug(
                "init_claim_constraints_claim_invalid | claim_id=%s | error=%s",
                claim_id,
                exc,
            )
            continue
        if not _claim_has_cognitive_constraint_payload(claim):
            continue
        constraint = _cognitive_constraint_from_claim(
            claim,
            entity_catalog=entity_catalog,
        )
        for chapter_number in sorted(_chapters_for_cognitive_constraint(claim)):
            by_chapter.setdefault(chapter_number, []).append(constraint)
    for chapter_number, constraints in by_chapter.items():
        deduped: dict[str, dict[str, Any]] = {}
        for constraint in constraints:
            key = str(constraint.get("claim_id") or constraint.get("constraint_id") or "")
            if key and key not in deduped:
                deduped[key] = constraint
        by_chapter[chapter_number] = list(deduped.values())
    return by_chapter


def _claim_has_cognitive_constraint_payload(claim: CoherenceClaim) -> bool:
    return bool(
        claim.cognitive_subjects
        or claim.cognitive_object
        or claim.cognitive_level != "unaware"
        or claim.action_level != "none"
        or claim.reader_awareness != "unknown"
        or claim.character_knowledge_coverage
        or claim.cognitive_chapter
        or claim.public_reveal_chapter
        or claim.foreshadow_chapters
    )


def _cognitive_constraint_from_claim(
    claim: CoherenceClaim,
    *,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lookup = _entity_catalog_lookup(entity_catalog)
    cognitive_subjects = list(claim.cognitive_subjects)
    if lookup:
        cognitive_subjects = _canonicalize_entity_name_list(
            [*claim.subject_ids, *claim.cognitive_subjects],
            lookup,
            entity_type="character",
        )
    character_knowledge_coverage = dict(claim.character_knowledge_coverage)
    if lookup:
        character_knowledge_coverage = _canonicalize_character_knowledge_coverage(
            character_knowledge_coverage, lookup
        )
    return {
        "constraint_id": f"claim_{claim.claim_id}",
        "claim_id": claim.claim_id,
        "claim_text": claim.claim_text,
        "cognitive_subjects": cognitive_subjects,
        "cognitive_object": claim.cognitive_object,
        "cognitive_level": claim.cognitive_level,
        "action_level": claim.action_level,
        "reader_awareness": claim.reader_awareness,
        "character_knowledge_coverage": character_knowledge_coverage,
        "cognitive_chapter": claim.cognitive_chapter,
        "public_reveal_chapter": claim.public_reveal_chapter,
        "foreshadow_chapters": list(claim.foreshadow_chapters),
        "source_artifact": claim.artifact,
        "source_path": claim.source_path,
        "evidence": claim.evidence,
    }


def _chapters_for_cognitive_constraint(claim: CoherenceClaim) -> set[int]:
    chapters = {number for number in claim.chapter_numbers if number >= 1}
    if claim.chapter_range is not None and claim.chapter_range.start and claim.chapter_range.end:
        start = min(claim.chapter_range.start, claim.chapter_range.end)
        end = max(claim.chapter_range.start, claim.chapter_range.end)
        if end - start <= 12:
            chapters.update(range(start, end + 1))
    for number in (
        claim.cognitive_chapter,
        claim.public_reveal_chapter,
        *claim.foreshadow_chapters,
    ):
        if isinstance(number, int) and number >= 1:
            chapters.add(number)
    return chapters


def _contract_coherence_batch_size(settings: Any) -> int:
    raw_size = getattr(settings, "contract_coherence_batch_size", 12)
    try:
        size = int(raw_size or 12)
    except (TypeError, ValueError):
        size = 12
    return max(1, min(30, size))


def _contract_coherence_context_window(settings: Any) -> int:
    raw_window = getattr(settings, "contract_coherence_context_window", 2)
    try:
        window = int(raw_window or 0)
    except (TypeError, ValueError):
        window = 2
    return max(0, min(12, window))


def _contract_coherence_max_parallel(settings: Any) -> int:
    raw_parallel = getattr(settings, "contract_coherence_max_parallel", 2)
    try:
        parallel = int(raw_parallel or 1)
    except (TypeError, ValueError):
        parallel = 1
    return max(1, min(8, parallel))


def _contract_coherence_input_payload(
    *,
    narrative_contract: dict[str, Any],
    all_contracts: list[dict[str, Any]],
    batch_contracts: list[dict[str, Any]],
    batch_start_index: int,
    context_window: int,
) -> dict[str, Any]:
    """Build a compact, globally aware contract-coherence adjudication payload."""
    batch_numbers = [int(item.get("chapter_number", 0) or 0) for item in batch_contracts]
    start = min(batch_numbers) if batch_numbers else 0
    end = max(batch_numbers) if batch_numbers else 0
    previous_items = all_contracts[max(0, batch_start_index - context_window) : batch_start_index]
    next_start = batch_start_index + len(batch_contracts)
    next_items = all_contracts[next_start : next_start + context_window]
    return {
        "global_contract": narrative_contract,
        "all_chapter_contract_index": [
            _compact_chapter_contract_index_item(item) for item in all_contracts
        ],
        "chapter_contracts": {
            "batch_start": start,
            "batch_end": end,
            "chapter_numbers": batch_numbers,
            "current": [
                _compact_chapter_contract_for_adjudication(item) for item in batch_contracts
            ],
            "previous_context": [
                _compact_chapter_contract_for_adjudication(item) for item in previous_items
            ],
            "next_context": [
                _compact_chapter_contract_for_adjudication(item) for item in next_items
            ],
        },
        "adjudication_scope": (
            "只裁判 current 中的章节契约，同时用 global_contract、all_chapter_contract_index "
            "和前后邻近上下文判断是否存在硬冲突。"
        ),
    }


_CONTRACT_VERDICT_RANK: dict[str, int] = {
    "accept": 0,
    "defer": 1,
    "ambiguous": 2,
    "needs_repair": 3,
    "reject": 4,
}


def _merge_contract_coherence_reports(
    batch_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge per-batch contract-coherence adjudications into one report."""
    if not batch_reports:
        return {"verdict": "accept", "issues": [], "summary": "未发现硬冲突。"}

    verdict = "accept"
    for report in batch_reports:
        candidate = str(report.get("verdict") or "ambiguous")
        if _CONTRACT_VERDICT_RANK.get(candidate, 2) > _CONTRACT_VERDICT_RANK.get(verdict, 0):
            verdict = candidate

    issues: list[dict[str, Any]] = []
    source_refs: list[Any] = []
    repair_scope: list[Any] = []
    preserve: list[Any] = []
    blocked = False
    change_intents: list[str] = []
    for report in batch_reports:
        blocked = blocked or bool(report.get("blocked", False))
        source_refs.extend(report.get("source_refs", []) or [])
        repair_scope.extend(report.get("repair_scope", []) or [])
        preserve.extend(report.get("preserve", []) or [])
        if report.get("change_intent"):
            change_intents.append(str(report.get("change_intent")))
        for issue in report.get("issues", []) or []:
            if isinstance(issue, dict):
                issues.append(issue)
            else:
                issues.append({"severity": "medium", "description": str(issue), "resolution": ""})

    def _issue_sort_key(issue: dict[str, Any]) -> tuple[int, str]:
        severity_rank = SEVERITY_RANK[normalize_severity(issue.get("severity"))]
        return (-severity_rank, str(issue.get("description") or ""))

    issues = sorted(issues, key=_issue_sort_key)[:8]
    risk_text = "未发现硬冲突" if not issues else f"保留最高风险问题 {len(issues)} 个"
    return {
        "verdict": verdict,
        "issues": issues,
        "source_refs": source_refs[:16],
        "repair_scope": repair_scope[:16],
        "preserve": preserve[:16],
        "change_intent": "；".join(change_intents[:4]),
        "blocked": blocked,
        "summary": f"分批契约裁判完成：{len(batch_reports)} 个批次，最终判定 {verdict}；{risk_text}。",
        "batch_reports": batch_reports,
    }


def _contract_coherence_blocks(report: dict[str, Any]) -> bool:
    """Development-mode coherence gate: reject hard contract conflicts."""

    verdict = str(report.get("verdict") or "accept").strip().lower()
    if verdict in {"reject", "needs_repair"}:
        return True
    for issue in list(report.get("issues", []) or []):
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity") or "").strip().lower()
        if severity in {"high", "critical"}:
            return True
    return False


def _chapter_contract_batch_max_tokens(
    ctx: InitLongServiceContext,
    chapter_count: int,
) -> int:
    """Reserve output proportional to the batch instead of the route maximum."""
    route_limit = route_max_output_budget(
        ctx.router,
        TaskType.PLAN_CHAPTER_CONTRACTS,
        min_tokens=4096,
    )
    proportional_budget = 8192 + 8192 * max(1, int(chapter_count or 1))
    return min(route_limit, proportional_budget)


def _chapter_contract_partial_path(ctx: InitLongServiceContext) -> Any | None:
    layout = getattr(ctx, "layout", None)
    plans_dir = getattr(layout, "plans_dir", None)
    return plans_dir / "chapter_contracts.partial.json" if plans_dir is not None else None


def _load_partial_chapter_contracts(
    ctx: InitLongServiceContext,
    *,
    fingerprint: str,
    expected_chapters: set[int],
) -> list[dict[str, Any]]:
    storage = getattr(ctx, "storage", None)
    path = _chapter_contract_partial_path(ctx)
    if storage is None or path is None or not storage.exists(path):
        return []
    try:
        payload = storage.load_json(path)
        if payload.get("fingerprint") != fingerprint:
            return []
        by_chapter: dict[int, dict[str, Any]] = {}
        for item in payload.get("chapter_contracts", []) or []:
            if not isinstance(item, dict):
                continue
            number = _safe_int(item.get("chapter_number"))
            if number in expected_chapters:
                ChapterContract.model_validate(item)
                by_chapter[number] = item
        return [by_chapter[number] for number in sorted(by_chapter)]
    except Exception as exc:
        _log.warning("chapter_contract_partial_load_failed | path=%s | error=%s", path, exc)
        return []


def _save_partial_chapter_contracts(
    ctx: InitLongServiceContext,
    *,
    fingerprint: str,
    existing: list[dict[str, Any]],
    batch_contracts: dict[str, Any],
    batch_coverage: dict[str, Any],
) -> None:
    storage = getattr(ctx, "storage", None)
    path = _chapter_contract_partial_path(ctx)
    if storage is None or path is None:
        return
    rejected = {_safe_int(number) for number in batch_coverage.get("backfilled_chapters", []) or []}
    by_chapter = {
        _safe_int(item.get("chapter_number")): item
        for item in existing
        if isinstance(item, dict) and _safe_int(item.get("chapter_number")) > 0
    }
    for item in batch_contracts.get("chapter_contracts", []) or []:
        if not isinstance(item, dict):
            continue
        number = _safe_int(item.get("chapter_number"))
        if number <= 0 or number in rejected:
            continue
        try:
            ChapterContract.model_validate(item)
        except Exception:
            continue
        by_chapter[number] = item
    storage.save_json(
        path,
        {
            "fingerprint": fingerprint,
            "chapter_contracts": [by_chapter[number] for number in sorted(by_chapter)],
        },
    )


async def _batched_generate_chapter_contracts(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
    narrative_contract: dict[str, Any],
    project_id: str,
    entity_catalog: dict[str, Any] | None = None,
    focus_chapters: Sequence[int] | None = None,
    outline_research_grounding: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate chapter contracts in small batches and merge them into full coverage."""
    focus_set = {
        number
        for number in (_safe_int(chapter) for chapter in (focus_chapters or []))
        if number > 0
    }
    all_chapters = sorted(outline.chapters, key=lambda chapter: int(chapter.chapter_number))
    requested_chapters = [
        chapter
        for chapter in all_chapters
        if not focus_set or int(chapter.chapter_number) in focus_set
    ]
    coverage_outline = (
        outline if not focus_set else outline.model_copy(update={"chapters": requested_chapters})
    )
    if not requested_chapters:
        return {"chapter_contracts": [], "coverage": {}}, {}
    partial_fingerprint = hash_payload(
        {
            "outline": coverage_outline,
            "narrative_contract": narrative_contract,
            "entity_catalog": entity_catalog or {},
            "outline_research_grounding": outline_research_grounding or {},
        }
    )
    expected_chapters = {int(chapter.chapter_number) for chapter in requested_chapters}
    accumulated = _load_partial_chapter_contracts(
        ctx,
        fingerprint=partial_fingerprint,
        expected_chapters=expected_chapters,
    )
    resumed_numbers = {
        _safe_int(item.get("chapter_number")) for item in accumulated if isinstance(item, dict)
    }
    chapters = [
        chapter
        for chapter in requested_chapters
        if int(chapter.chapter_number) not in resumed_numbers
    ]
    if resumed_numbers:
        ctx.on_step(
            "plan_chapter_contracts_partial_resumed",
            {
                "resumed_chapters": sorted(resumed_numbers),
                "remaining_chapters": [int(chapter.chapter_number) for chapter in chapters],
            },
        )
    batch_size = _effective_chapter_contract_batch_size(ctx, len(requested_chapters))
    batch_reports: list[dict[str, Any]] = []
    conversation_history: list[dict[str, str]] = []
    history_window_rounds = PipelineConstants.CONVERSATION_HISTORY_WINDOW
    context_window = int(getattr(ctx.settings, "chapter_contract_context_window", 2) or 0)
    init_claim_constraints_by_chapter = _init_claim_constraints_by_chapter(
        ctx,
        entity_catalog=entity_catalog,
    )
    option_checker = getattr(ctx, "is_outline_option_enabled", None)
    chapter_contract_multi_turn = bool(
        option_checker(
            capability="multi_turn",
            enabled=getattr(ctx.settings, "chapter_contract_multi_turn", True),
            allowed_providers_raw=getattr(
                ctx.settings,
                "chapter_contract_multi_turn_providers",
                "tongyi,deepseek,minimax",
            ),
            allowed_models_raw=getattr(ctx.settings, "chapter_contract_multi_turn_models", ""),
            task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
        )
        if callable(option_checker)
        else getattr(ctx.settings, "chapter_contract_multi_turn", True)
    )

    async def _generate_batch(
        batch_chapters: list[Any],
        *,
        local_previous_contracts: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        batch_start = int(batch_chapters[0].chapter_number)
        batch_end = int(batch_chapters[-1].chapter_number)
        batch_outline = outline.model_copy(update={"chapters": batch_chapters})
        context_contracts = [
            *accumulated,
            *(local_previous_contracts or []),
        ]
        batch_numbers = [int(chapter.chapter_number) for chapter in batch_chapters]
        batch_payload = _chapter_contract_batch_outline_payload(
            outline,
            batch_chapters,
            previous_contracts=context_contracts,
            context_window=context_window,
            settings=ctx.settings,
            init_claim_constraints_by_chapter=init_claim_constraints_by_chapter,
            entity_catalog=entity_catalog,
        )
        prior = (
            outline_h.history_prior_messages(
                conversation_history,
                history_window_rounds=history_window_rounds,
            )
            if chapter_contract_multi_turn and conversation_history
            else None
        )
        raw_sink: list[str] = []

        try:
            with ctx.trace.step(f"plan_chapter_contracts_batch_{batch_start}_{batch_end}"):
                batch_contracts = await ctx.call_with_retry(
                    TaskType.PLAN_CHAPTER_CONTRACTS,
                    {
                        "narrative_contract": narrative_contract,
                        "outline": batch_payload,
                        "outline_research_grounding": outline_research_grounding or {},
                    },
                    max_tokens=_chapter_contract_batch_max_tokens(ctx, len(batch_chapters)),
                    temperature=getattr(ctx.settings, "temp_plan_chapter_contracts", 0.25),
                    required_keys=("chapter_contracts",),
                    max_retries=4,
                    prior_messages=prior,
                    multi_turn=chapter_contract_multi_turn,
                    _capture_raw=raw_sink,
                )
                # Deterministic backfill: copy any init_claim_constraints the
                # LLM dropped directly into cognitive_constraints so the
                # Claim-to-Contract coverage audit has a fair chance to pass.
                batch_contracts = _backfill_cognitive_constraints(
                    batch_contracts,
                    _select_init_claim_constraints_for_backfill(
                        init_claim_constraints_by_chapter,
                        batch_numbers,
                    ),
                    settings=ctx.settings,
                    entity_catalog=entity_catalog,
                )
        except Exception as exc:
            if not _chapter_contract_batch_error_allows_split(exc):
                ctx.on_step(
                    "plan_chapter_contracts_transport_deferred",
                    {
                        "batch_start": batch_start,
                        "batch_end": batch_end,
                        "error_type": type(exc).__name__,
                        "retryable": bool(
                            isinstance(exc, ModelGatewayError)
                            and getattr(exc, "is_transient_error", False)
                        ),
                        "checkpoint_chapters": sorted(
                            {
                                _safe_int(item.get("chapter_number"))
                                for item in [*accumulated, *(local_previous_contracts or [])]
                                if isinstance(item, dict)
                                and _safe_int(item.get("chapter_number")) > 0
                            }
                        ),
                    },
                )
                raise
            if len(batch_chapters) <= 1:
                raise
            midpoint = len(batch_chapters) // 2
            ctx.on_step(
                "plan_chapter_contracts_split",
                {
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                    "left": [int(chapter.chapter_number) for chapter in batch_chapters[:midpoint]],
                    "right": [int(chapter.chapter_number) for chapter in batch_chapters[midpoint:]],
                },
            )
            left_contracts, _left_coverage = await _generate_batch(
                batch_chapters[:midpoint],
                local_previous_contracts=local_previous_contracts,
            )
            left_items = left_contracts.get("chapter_contracts", []) or []
            right_contracts, _right_coverage = await _generate_batch(
                batch_chapters[midpoint:],
                local_previous_contracts=[
                    *(local_previous_contracts or []),
                    *left_items,
                ],
            )
            merged_raw = {
                "chapter_contracts": (
                    (left_contracts.get("chapter_contracts", []) or [])
                    + (right_contracts.get("chapter_contracts", []) or [])
                )
            }
            merged = _backfill_cognitive_constraints(
                merged_raw,
                _select_init_claim_constraints_for_backfill(
                    init_claim_constraints_by_chapter,
                    batch_numbers,
                ),
                settings=ctx.settings,
                entity_catalog=entity_catalog,
            )
            merged, merged_coverage = _ensure_chapter_contract_coverage(
                merged,
                batch_outline,
                settings=ctx.settings,
            )
            _save_partial_chapter_contracts(
                ctx,
                fingerprint=partial_fingerprint,
                existing=[*accumulated, *(local_previous_contracts or [])],
                batch_contracts=merged,
                batch_coverage=merged_coverage,
            )
            return merged, merged_coverage

        batch_contracts, batch_coverage = _ensure_chapter_contract_coverage(
            batch_contracts,
            batch_outline,
            settings=ctx.settings,
        )
        _save_partial_chapter_contracts(
            ctx,
            fingerprint=partial_fingerprint,
            existing=[*accumulated, *(local_previous_contracts or [])],
            batch_contracts=batch_contracts,
            batch_coverage=batch_coverage,
        )
        if raw_sink:
            _append_chapter_contract_history(
                conversation_history,
                batch_start=batch_start,
                batch_end=batch_end,
                raw_response=raw_sink[0],
            )
        return batch_contracts, batch_coverage

    for offset in range(0, len(chapters), batch_size):
        batch_chapters = chapters[offset : offset + batch_size]
        if not batch_chapters:
            continue
        batch_start = int(batch_chapters[0].chapter_number)
        batch_end = int(batch_chapters[-1].chapter_number)
        batch_contracts, batch_coverage = await _generate_batch(batch_chapters)
        if batch_coverage["backfilled_chapters"]:
            _log.warning(
                "chapter_contracts_batch_backfilled | project=%s | batch=%s-%s | "
                "chapters=%s | discarded=%s | duplicates=%s",
                project_id,
                batch_start,
                batch_end,
                batch_coverage["backfilled_chapters"],
                batch_coverage["discarded_count"],
                batch_coverage["duplicate_count"],
            )
        accumulated.extend(batch_contracts.get("chapter_contracts", []) or [])
        batch_report = {
            "batch_start": batch_start,
            "batch_end": batch_end,
            "coverage": batch_coverage,
        }
        batch_reports.append(batch_report)
        ctx.on_step(
            f"plan_chapter_contracts_batch_{batch_start}_{batch_end}",
            {
                **batch_report,
                "chapters_done": len(resumed_numbers)
                + min(len(chapters), offset + len(batch_chapters)),
                "chapters_total": len(requested_chapters),
                "focus_chapters": sorted(focus_set) if focus_set else [],
            },
        )

    # Final backfill pass: by this point every batch has been merged
    # into `accumulated` and a fresh look at the claim ledger is enough
    # to catch anything the per-batch backfill skipped (e.g., the LLM
    # emitted claim_ids it later dropped).
    merged_input = _backfill_cognitive_constraints(
        {"chapter_contracts": accumulated},
        _select_init_claim_constraints_for_backfill(
            init_claim_constraints_by_chapter,
            sorted({int(chapter.chapter_number) for chapter in chapters}),
        ),
        settings=ctx.settings,
        entity_catalog=entity_catalog,
    )
    merged_contracts, final_coverage = _ensure_chapter_contract_coverage(
        merged_input,
        coverage_outline,
        settings=ctx.settings,
    )
    final_coverage["batch_size"] = batch_size
    final_coverage["batch_count"] = len(batch_reports)
    final_coverage["batch_backfilled_chapters"] = [
        chapter
        for report in batch_reports
        for chapter in report["coverage"].get("backfilled_chapters", [])
    ]
    if any(report["coverage"].get("local_fallback_accepted") for report in batch_reports):
        final_coverage["local_fallback_accepted"] = True
        final_coverage["partial_format_repair_accepted"] = True
    merged_contracts["coverage"] = final_coverage
    merged_contracts["batch_reports"] = batch_reports
    return merged_contracts, final_coverage


async def _batched_adjudicate_contract_coherence(
    ctx: InitLongServiceContext,
    *,
    narrative_contract: dict[str, Any],
    chapter_contracts: dict[str, Any],
    project_id: str,
) -> dict[str, Any]:
    """Adjudicate contract coherence with compact, chapter-scoped batches."""
    raw_items = chapter_contracts.get("chapter_contracts", [])
    if not isinstance(raw_items, list):
        raw_items = []
    all_contracts = sorted(
        [item for item in raw_items if isinstance(item, dict)],
        key=lambda item: int(item.get("chapter_number", 0) or 0),
    )
    if not all_contracts:
        return {
            "verdict": "ambiguous",
            "issues": [
                {
                    "severity": "high",
                    "description": "缺少章节契约，无法完成契约自洽裁判。",
                    "resolution": "先补齐 chapter_contracts.json 后再执行裁判。",
                }
            ],
            "summary": "缺少章节契约，契约裁判无法确认自洽。",
        }

    batch_size = _contract_coherence_batch_size(ctx.settings)
    context_window = _contract_coherence_context_window(ctx.settings)
    max_parallel = _contract_coherence_max_parallel(ctx.settings)
    batch_reports: list[dict[str, Any]] = []

    async def _judge_batch(
        batch_items: list[dict[str, Any]],
        *,
        batch_start_index: int,
    ) -> list[dict[str, Any]]:
        numbers = [int(item.get("chapter_number", 0) or 0) for item in batch_items]
        batch_start = min(numbers) if numbers else 0
        batch_end = max(numbers) if numbers else 0
        try:
            with ctx.trace.step(f"adjudicate_contract_coherence_batch_{batch_start}_{batch_end}"):
                report = await ctx.call_with_retry(
                    TaskType.ADJUDICATE_CONTRACT_COHERENCE,
                    {
                        "narrative_contract": _contract_coherence_input_payload(
                            narrative_contract=narrative_contract,
                            all_contracts=all_contracts,
                            batch_contracts=batch_items,
                            batch_start_index=batch_start_index,
                            context_window=context_window,
                        )
                    },
                    max_tokens=calculate_route_aware_max_tokens(
                        ctx.router,
                        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
                        3072,
                        prompt_overhead=4200,
                        min_tokens=2048,
                    ),
                    temperature=getattr(ctx.settings, "temp_adjudicate_contract_coherence", 0.1),
                    required_keys=("verdict", "issues", "summary"),
                    max_retries=3,
                )
        except Exception:
            if len(batch_items) <= 1:
                raise
            midpoint = len(batch_items) // 2
            ctx.on_step(
                "adjudicate_contract_coherence_split",
                {
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                    "left": [
                        int(item.get("chapter_number", 0) or 0) for item in batch_items[:midpoint]
                    ],
                    "right": [
                        int(item.get("chapter_number", 0) or 0) for item in batch_items[midpoint:]
                    ],
                },
            )
            left_reports = await _judge_batch(
                batch_items[:midpoint],
                batch_start_index=batch_start_index,
            )
            right_reports = await _judge_batch(
                batch_items[midpoint:],
                batch_start_index=batch_start_index + midpoint,
            )
            return [*left_reports, *right_reports]

        normalized_report = {
            "batch_start": batch_start,
            "batch_end": batch_end,
            "verdict": str(report.get("verdict") or "ambiguous"),
            "issues": report.get("issues") if isinstance(report.get("issues"), list) else [],
            "source_refs": report.get("source_refs")
            if isinstance(report.get("source_refs"), list)
            else [],
            "repair_scope": report.get("repair_scope")
            if isinstance(report.get("repair_scope"), list)
            else [],
            "preserve": report.get("preserve") if isinstance(report.get("preserve"), list) else [],
            "change_intent": str(report.get("change_intent") or ""),
            "blocked": bool(report.get("blocked", False)),
            "summary": str(report.get("summary") or ""),
        }
        ctx.on_step(
            f"adjudicate_contract_coherence_batch_{batch_start}_{batch_end}",
            {
                **normalized_report,
                "chapters_done": batch_start_index + len(batch_items),
                "chapters_total": len(all_contracts),
            },
        )
        return [normalized_report]

    batch_specs = [
        (offset, all_contracts[offset : offset + batch_size])
        for offset in range(0, len(all_contracts), batch_size)
    ]
    if max_parallel <= 1 or len(batch_specs) <= 1:
        for offset, batch_items in batch_specs:
            batch_reports.extend(
                await _judge_batch(
                    batch_items,
                    batch_start_index=offset,
                )
            )
    else:
        semaphore = asyncio.Semaphore(min(max_parallel, len(batch_specs)))

        async def _run_limited_batch(
            offset: int,
            batch_items: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            async with semaphore:
                return await _judge_batch(
                    batch_items,
                    batch_start_index=offset,
                )

        for reports in await asyncio.gather(
            *[_run_limited_batch(offset, batch_items) for offset, batch_items in batch_specs]
        ):
            batch_reports.extend(reports)

    merged = _merge_contract_coherence_reports(batch_reports)
    merged["batch_size"] = batch_size
    merged["batch_count"] = len(batch_reports)
    merged["max_parallel"] = max_parallel
    _log.info(
        "contract_coherence_batched | project=%s | batches=%s | parallel=%s | verdict=%s | issues=%s",
        project_id,
        len(batch_reports),
        max_parallel,
        merged.get("verdict"),
        len(merged.get("issues", []) or []),
    )
    return merged

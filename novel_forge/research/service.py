"""Research stage orchestration for long-form initialization."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.pipeline.context_governance import json_char_size, partition_complete
from novel_forge.pipeline.long.services.init.init_v2 import hash_payload
from novel_forge.research.contracts import (
    ModelPriorNotes,
    OutlineResearchChapterNote,
    OutlineResearchGrounding,
    ResearchBrief,
    ResearchDossier,
    ResearchQuery,
    ResearchReport,
    ResearchResult,
    ResearchSource,
    ResearchStatus,
)
from novel_forge.research.providers import NoopResearchProvider, provider_from_settings

_PLANNED_QUERY_FIELDS = frozenset(
    {
        "query",
        "rationale",
        "intent",
        "priority",
        "locale",
        "source_preferences",
        "recency_required",
        "risk_if_missing",
    }
)
_MCP_STDIO_DISPATCHER_VERSION = 2


async def run_init_web_research(
    *,
    spec: StorySpec,
    settings: Any,
    enabled: bool,
    provider_name: str,
    query_hint: str = "",
    ctx: Any | None = None,
    use_llm_planning: bool = False,
    model_prior_enabled: bool = False,
) -> ResearchReport:
    """Run non-blocking web research and return a persisted-report payload."""
    config = build_research_runtime_config(settings, provider_name)
    config_fingerprint = research_runtime_fingerprint(settings, provider_name)
    max_queries = _int_config(config, "max_queries", 3)
    created_at = datetime.now(UTC).isoformat()
    spec_fingerprint = hash_payload(spec.model_dump(mode="json"))

    # --- Query planning: LLM or rule-based ---
    fallback_used = ""
    query_plan_warnings: list[str] = []
    llm_planned = False
    knowledge_gaps: list[str] = []

    should_plan_with_llm = bool(enabled and use_llm_planning and ctx is not None)
    if should_plan_with_llm:
        try:
            queries, knowledge_gaps, plan_warnings = await plan_research_queries_with_llm(
                ctx=ctx,
                spec=spec,
                max_queries=max_queries,
                query_hint=query_hint,
            )
            llm_planned = True
            query_plan_warnings = plan_warnings
        except Exception as exc:  # noqa: BLE001 - fall back to rules
            fallback_used = "rule_based"
            query_plan_warnings = [f"LLM query planning failed: {type(exc).__name__}: {exc}"]
            queries = build_research_queries(spec, query_hint=query_hint, max_queries=max_queries)
    else:
        fallback_used = "rule_based" if enabled and use_llm_planning else ""
        queries = build_research_queries(spec, query_hint=query_hint, max_queries=max_queries)
    if not enabled:
        return ResearchReport(
            enabled=False,
            provider="noop",
            status="skipped",
            config=config,
            config_fingerprint=config_fingerprint,
            queries=queries,
            created_at=created_at,
            spec_fingerprint=spec_fingerprint,
            warnings=[
                "research disabled for this init run; enable research in the long-init request "
                "or set NOVEL_FORGE_RESEARCH_ENABLED=true as the desktop default"
            ],
            llm_planned=llm_planned,
            knowledge_gaps=knowledge_gaps,
            query_plan_warnings=query_plan_warnings,
            fallback_used=fallback_used,
        )

    provider = provider_from_settings(settings, provider_name)
    if isinstance(provider, NoopResearchProvider):
        model_prior = await _maybe_synthesize_model_prior(
            ctx=ctx,
            spec=spec,
            queries=queries,
            existing_sources_count=0,
            enabled=model_prior_enabled,
        )
        return ResearchReport(
            enabled=True,
            provider=provider.name,
            status="skipped",
            config=config,
            config_fingerprint=config_fingerprint,
            queries=queries,
            created_at=created_at,
            spec_fingerprint=spec_fingerprint,
            warnings=["no research provider configured"],
            llm_planned=llm_planned,
            knowledge_gaps=knowledge_gaps,
            query_plan_warnings=query_plan_warnings,
            fallback_used=fallback_used,
            model_prior=model_prior,
        )

    result = await provider.search(queries)
    model_prior = await _maybe_synthesize_model_prior(
        ctx=ctx,
        spec=spec,
        queries=queries,
        existing_sources_count=len(result.sources),
        enabled=model_prior_enabled,
    )
    brief = build_research_brief(result)
    warnings = list(result.warnings)
    status: ResearchStatus
    if result.sources:
        status = "succeeded"
    elif any("endpoint is not configured" in warning for warning in warnings):
        status = "skipped"
    elif warnings:
        status = "failed"
    else:
        status = "empty"
    if not result.sources:
        warnings.append("research provider returned no usable sources")
    return ResearchReport(
        enabled=True,
        provider=result.provider,
        status=status,
        config=config,
        config_fingerprint=config_fingerprint,
        queries=queries,
        sources=result.sources,
        brief=brief,
        warnings=warnings,
        created_at=created_at,
        spec_fingerprint=spec_fingerprint,
        llm_planned=llm_planned,
        knowledge_gaps=knowledge_gaps,
        query_plan_warnings=query_plan_warnings,
        fallback_used=fallback_used,
        model_prior=model_prior,
    )


def failed_research_report(
    *,
    spec: StorySpec,
    settings: Any | None = None,
    enabled: bool,
    provider_name: str,
    query_hint: str,
    error: Exception,
) -> ResearchReport:
    """Build a non-blocking failure report for unexpected research errors."""
    config = build_research_runtime_config(settings=settings, provider_name=provider_name)
    return ResearchReport(
        enabled=enabled,
        provider=(provider_name or "auto").strip() or "auto",
        status="failed",
        config=config,
        config_fingerprint=research_runtime_fingerprint(settings, provider_name),
        queries=build_research_queries(spec, query_hint=query_hint),
        created_at=datetime.now(UTC).isoformat(),
        spec_fingerprint=hash_payload(spec.model_dump(mode="json")),
        warnings=[f"{type(error).__name__}: {error}"],
    )


async def _maybe_synthesize_model_prior(
    *,
    ctx: Any | None,
    spec: StorySpec,
    queries: list[ResearchQuery],
    existing_sources_count: int,
    enabled: bool,
) -> ModelPriorNotes:
    """Run the optional model-prior layer without affecting external source status."""
    if not enabled or ctx is None:
        return ModelPriorNotes(enabled=False, status="skipped")
    return await synthesize_model_prior_notes(
        ctx=ctx,
        spec=spec,
        queries=queries,
        existing_sources_count=existing_sources_count,
        enabled=True,
    )


async def synthesize_init_research_dossier(
    *,
    ctx: Any,
    spec: StorySpec,
    research_report: ResearchReport,
    enabled: bool,
) -> ResearchDossier:
    """Build an LLM-compressed research dossier, or a non-blocking skip report."""
    settings = getattr(ctx, "settings", None)
    created_at = datetime.now(UTC).isoformat()
    spec_fingerprint = hash_payload(spec.model_dump(mode="json"))
    report_payload = research_report.model_dump(mode="json")
    report_fingerprint = hash_payload(report_payload)
    config_fingerprint = research_runtime_fingerprint(settings, research_report.provider)
    if not enabled or not bool(getattr(settings, "research_dossier_enabled", True)):
        return ResearchDossier(
            enabled=False,
            provider=research_report.provider,
            status="skipped",
            created_at=created_at,
            spec_fingerprint=spec_fingerprint,
            research_report_fingerprint=report_fingerprint,
            config_fingerprint=config_fingerprint,
            warnings=["research dossier disabled for this init run"],
        )
    model_prior = research_report.model_prior
    has_external_sources = research_report.status == "succeeded" and bool(research_report.sources)
    has_model_prior = model_prior.status == "succeeded" and bool(
        model_prior.notes or model_prior.terminology or model_prior.uncertainty_notes
    )
    if not has_external_sources and not has_model_prior:
        warnings = list(research_report.warnings) or [
            f"research report status is {research_report.status}"
        ]
        if model_prior.status == "failed":
            warnings.extend(f"model_prior: {warning}" for warning in model_prior.warnings)
        return ResearchDossier(
            enabled=True,
            provider=research_report.provider,
            status="empty" if research_report.status == "empty" else "skipped",
            created_at=created_at,
            spec_fingerprint=spec_fingerprint,
            research_report_fingerprint=report_fingerprint,
            config_fingerprint=config_fingerprint,
            warnings=warnings,
        )

    max_sources = _int_config(
        build_research_runtime_config(settings, research_report.provider),
        "dossier_max_sources",
        8,
    )
    prompt_report = dict(report_payload)
    prompt_report["sources"] = prompt_report.get("sources", [])[: max(1, max_sources)]
    prompt_report["model_prior"] = model_prior.prompt_context()
    data = await ctx.call_with_retry(
        TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER,
        {
            "spec": spec.model_dump(mode="json"),
            "research_report": prompt_report,
        },
        max_tokens=4096,
        temperature=getattr(settings, "temp_synthesize_init_research_dossier", 0.2),
        required_keys=(
            "summary",
            "real_world_constraints",
            "terminology",
            "inspiration_notes",
            "uncertainty_notes",
            "source_refs",
        ),
        max_retries=2,
    )
    dossier_warnings = _clean_list(
        [
            *research_report.warnings,
            *model_prior.warnings,
            *list(data.get("warnings") if isinstance(data.get("warnings"), list) else []),
        ],
        limit=12,
    )
    return ResearchDossier(
        enabled=True,
        provider=research_report.provider,
        status="succeeded",
        summary=_clip(_clean(data.get("summary")), 1600),
        real_world_constraints=_clean_list(data.get("real_world_constraints"), limit=12),
        terminology=_clean_list(data.get("terminology"), limit=24),
        inspiration_notes=_clean_list(data.get("inspiration_notes"), limit=16),
        uncertainty_notes=_clean_list(data.get("uncertainty_notes"), limit=12),
        source_refs=_clean_list(data.get("source_refs"), limit=16),
        warnings=dossier_warnings,
        created_at=created_at,
        spec_fingerprint=spec_fingerprint,
        research_report_fingerprint=report_fingerprint,
        config_fingerprint=config_fingerprint,
        model_prior_notes=list(model_prior.notes) if has_model_prior else [],
        model_prior_terminology=list(model_prior.terminology) if has_model_prior else [],
        model_prior_uncertainty_notes=(
            list(model_prior.uncertainty_notes) if has_model_prior else []
        ),
    )


def failed_research_dossier(
    *,
    spec: StorySpec,
    research_report: ResearchReport,
    settings: Any | None,
    error: Exception,
) -> ResearchDossier:
    """Build a non-blocking dossier failure report."""
    return ResearchDossier(
        enabled=True,
        provider=research_report.provider,
        status="failed",
        created_at=datetime.now(UTC).isoformat(),
        spec_fingerprint=hash_payload(spec.model_dump(mode="json")),
        research_report_fingerprint=hash_payload(research_report.model_dump(mode="json")),
        config_fingerprint=research_runtime_fingerprint(settings, research_report.provider),
        warnings=[f"{type(error).__name__}: {error}"],
    )


async def ground_outline_research(
    *,
    ctx: Any,
    spec: StorySpec,
    story_bible: Any,
    blueprint: Any,
    outline: Any,
    dossier: ResearchDossier,
    enabled: bool,
) -> OutlineResearchGrounding:
    """Align a research dossier with the final outline for chapter contracts."""
    settings = getattr(ctx, "settings", None)
    created_at = datetime.now(UTC).isoformat()
    dossier_fingerprint = hash_payload(dossier.model_dump(mode="json"))
    outline_payload = _model_payload(outline)
    outline_fingerprint = hash_payload(outline_payload)
    config_fingerprint = research_runtime_fingerprint(settings, dossier.provider)
    if (
        not enabled
        or not bool(getattr(settings, "outline_research_grounding_enabled", True))
        or dossier.status != "succeeded"
        or not dossier.summary
    ):
        return OutlineResearchGrounding(
            enabled=False,
            status="skipped",
            created_at=created_at,
            dossier_fingerprint=dossier_fingerprint,
            outline_fingerprint=outline_fingerprint,
            config_fingerprint=config_fingerprint,
            warnings=["outline research grounding skipped"],
        )

    notes_per_chapter = max(
        0,
        int(getattr(settings, "outline_research_grounding_notes_per_chapter", 3) or 0),
    )
    base_context = {
        "spec": spec.model_dump(mode="json"),
        "story_bible": _model_payload(story_bible),
        "blueprint": _model_payload(blueprint),
        "research_dossier": dossier.prompt_context(),
        "limits": {"notes_per_chapter": notes_per_chapter},
    }
    chapters = [
        item for item in list(outline_payload.get("chapters", []) or []) if isinstance(item, dict)
    ]
    outline_base = {key: value for key, value in outline_payload.items() if key != "chapters"}
    batch_size = max(
        1,
        int(getattr(settings, "outline_research_grounding_batch_size", 6) or 6),
    )
    prompt_char_budget = max(
        20000,
        int(getattr(settings, "outline_research_grounding_prompt_char_budget", 140000) or 140000),
    )

    def _batch_context(batch_chapters: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            **base_context,
            "outline": {**outline_base, "chapters": batch_chapters},
            "grounding_batch": {
                "chapter_numbers": _chapter_numbers(batch_chapters),
                "require_complete_chapter_coverage": True,
            },
        }

    partitions = partition_complete(
        chapters,
        build_payload=_batch_context,
        measure_chars=lambda payload: _rendered_context_chars(
            ctx,
            TaskType.GROUND_OUTLINE_RESEARCH,
            payload,
        ),
        char_budget=prompt_char_budget,
        max_items=batch_size,
        item_label="outline chapter",
    )
    batch_results: list[dict[str, Any]] = []
    total_batches = len(partitions)
    for batch_index, partition in enumerate(partitions, start=1):
        batch_chapters = list(partition.items)
        batch_context = _batch_context(batch_chapters)
        batch_context["grounding_batch"].update(
            {
                "batch_index": batch_index,
                "batch_total": total_batches,
                "estimated_prompt_chars": partition.estimated_chars,
            }
        )
        batch_results.append(
            await _ground_outline_research_batch(
                ctx,
                context=batch_context,
                expected_chapters=_chapter_numbers(batch_chapters),
                coverage_retries=max(
                    0,
                    int(getattr(settings, "outline_research_grounding_coverage_retries", 1) or 0),
                ),
            )
        )

    data = _merge_outline_grounding_batches(
        batch_results,
        expected_chapters=_chapter_numbers(chapters),
        notes_per_chapter=notes_per_chapter,
    )
    return OutlineResearchGrounding(
        enabled=True,
        status="succeeded",
        summary=_clip(_clean(data.get("summary")), 1600),
        global_notes=_clean_list(data.get("global_notes"), limit=20),
        chapter_notes=_normalize_chapter_notes(
            data.get("chapter_notes"),
            notes_per_chapter=notes_per_chapter,
        ),
        fact_risks=_clean_list(data.get("fact_risks"), limit=20),
        terminology=_clean_list(data.get("terminology"), limit=30),
        source_refs=_clean_list(data.get("source_refs"), limit=20),
        warnings=_clean_list(data.get("warnings"), limit=8),
        created_at=created_at,
        dossier_fingerprint=dossier_fingerprint,
        outline_fingerprint=outline_fingerprint,
        config_fingerprint=config_fingerprint,
    )


async def _ground_outline_research_batch(
    ctx: Any,
    *,
    context: dict[str, Any],
    expected_chapters: list[int],
    coverage_retries: int,
) -> dict[str, Any]:
    """Run one complete outline batch and retry only missing chapter coverage."""

    settings = getattr(ctx, "settings", None)
    accumulated: dict[str, Any] = {}
    missing = list(expected_chapters)
    for attempt in range(coverage_retries + 1):
        request_context = dict(context)
        request_context["grounding_batch"] = {
            **dict(context.get("grounding_batch", {})),
            "coverage_attempt": attempt + 1,
            "missing_chapter_numbers": missing if attempt else [],
        }
        if attempt:
            request_context["outline"] = {
                **dict(context.get("outline", {})),
                "chapters": [
                    item
                    for item in list(dict(context.get("outline", {})).get("chapters", []) or [])
                    if int(item.get("chapter_number") or 0) in set(missing)
                ],
            }
        data = await ctx.call_with_retry(
            TaskType.GROUND_OUTLINE_RESEARCH,
            request_context,
            max_tokens=6144,
            temperature=getattr(settings, "temp_ground_outline_research", 0.15),
            required_keys=(
                "summary",
                "global_notes",
                "chapter_notes",
                "fact_risks",
                "terminology",
                "source_refs",
            ),
            max_retries=2,
        )
        if not isinstance(data, dict):
            raise ValueError("GROUND_OUTLINE_RESEARCH must return a JSON object")
        accumulated = _merge_grounding_payloads([accumulated, data])
        covered = {
            note.chapter_number
            for note in _normalize_chapter_notes(
                accumulated.get("chapter_notes"),
                notes_per_chapter=int(
                    dict(context.get("limits", {})).get("notes_per_chapter", 3) or 0
                ),
            )
        }
        missing = [chapter for chapter in expected_chapters if chapter not in covered]
        if not missing:
            return accumulated
    raise ValueError(
        "outline research grounding omitted required chapters after coverage retry: "
        + ",".join(str(chapter) for chapter in missing)
    )


def failed_outline_research_grounding(
    *,
    dossier: ResearchDossier,
    outline: Any,
    settings: Any | None,
    error: Exception,
) -> OutlineResearchGrounding:
    """Build a non-blocking outline-grounding failure report."""
    return OutlineResearchGrounding(
        enabled=True,
        status="failed",
        created_at=datetime.now(UTC).isoformat(),
        dossier_fingerprint=hash_payload(dossier.model_dump(mode="json")),
        outline_fingerprint=hash_payload(_model_payload(outline)),
        config_fingerprint=research_runtime_fingerprint(settings, dossier.provider),
        warnings=[f"{type(error).__name__}: {error}"],
    )


def build_research_queries(
    spec: StorySpec,
    *,
    query_hint: str = "",
    max_queries: int = 3,
) -> list[ResearchQuery]:
    """Create a small deterministic query set from the confirmed spec."""
    genre = _clean(getattr(spec, "genre", ""))
    theme = _clean(getattr(spec, "theme", ""))
    world_hint = _clean(getattr(spec, "world_hint", ""))
    conflict_hint = _clean(getattr(spec, "conflict_hint", ""))
    hint = _clean(query_hint)
    raw_queries: list[tuple[str, str]] = []
    if hint:
        raw_queries.append((hint, "用户补充检索方向"))
    if world_hint:
        raw_queries.append((f"{world_hint} 背景 资料", "世界观/时代背景"))
    if genre and conflict_hint:
        raw_queries.append((f"{genre} {conflict_hint} 真实背景 资料", "题材与冲突约束"))
    elif genre and theme:
        raw_queries.append((f"{genre} {theme} 背景 资料", "题材与核心前提"))
    elif theme:
        raw_queries.append((f"{theme} 背景 资料", "核心前提"))

    queries: list[ResearchQuery] = []
    seen: set[str] = set()
    for query, rationale in raw_queries:
        normalized = _clean(query)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        queries.append(ResearchQuery(query=_clip(normalized, 180), rationale=rationale))
        if len(queries) >= max(1, int(max_queries or 1)):
            break
    if not queries:
        queries.append(ResearchQuery(query="小说世界观 背景资料", rationale="默认兜底"))
    return queries


async def plan_research_queries_with_llm(
    *,
    ctx: Any,
    spec: StorySpec,
    max_queries: int = 3,
    query_hint: str = "",
) -> tuple[list[ResearchQuery], list[str], list[str]]:
    """Use LLM to plan research queries from the full spec.

    Returns (queries, knowledge_gaps, warnings).
    Raises on failure so the caller can fall back to rule-based queries.
    """
    settings = getattr(ctx, "settings", None)
    data = await ctx.call_with_retry(
        TaskType.PLAN_INIT_RESEARCH_QUERIES,
        {
            "spec": spec.model_dump(mode="json"),
            "max_queries": max_queries,
            "query_hint": query_hint,
        },
        max_tokens=4096,
        temperature=getattr(settings, "temp_plan_init_research_queries", 0.3),
        required_keys=("queries", "knowledge_gaps"),
        max_retries=2,
    )
    raw_queries = data.get("queries", [])
    if not isinstance(raw_queries, list):
        raw_queries = []
    queries: list[ResearchQuery] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_queries):
        if not isinstance(item, dict):
            raise ValueError(f"queries[{index}] must be an object")
        item_fields = set(item)
        missing_fields = sorted(_PLANNED_QUERY_FIELDS - item_fields)
        extra_fields = sorted(item_fields - _PLANNED_QUERY_FIELDS)
        if missing_fields or extra_fields:
            raise ValueError(
                f"queries[{index}] content contract mismatch: "
                f"missing={missing_fields}, extra={extra_fields}"
            )
        query_text = _clean(item.get("query", ""))
        key = query_text.casefold()
        if not query_text:
            raise ValueError(f"queries[{index}].query must not be empty")
        if key in seen:
            continue
        seen.add(key)
        priority = str(item.get("priority", "should")).strip()
        if priority not in {"must", "should", "nice"}:
            raise ValueError(f"queries[{index}].priority is invalid: {priority!r}")
        queries.append(
            ResearchQuery(
                query=_clip(query_text, 180),
                rationale=_clean(item.get("rationale", "")),
                intent=_clean(item.get("intent", "")),
                priority=priority,
                locale=_clean(item.get("locale", "")),
                source_preferences=_clean_list(
                    item.get("source_preferences"), limit=6, text_limit=60
                ),
                recency_required=bool(item.get("recency_required", False)),
                risk_if_missing=_clean(item.get("risk_if_missing", "")),
            )
        )
        if len(queries) >= max(1, int(max_queries or 1)):
            break
    if not queries:
        raise ValueError("LLM returned no usable queries")
    knowledge_gaps = _clean_list(data.get("knowledge_gaps"), limit=12, text_limit=200)
    warnings = _clean_list(data.get("warnings"), limit=8, text_limit=200)
    return queries, knowledge_gaps, warnings


async def synthesize_model_prior_notes(
    *,
    ctx: Any,
    spec: StorySpec,
    queries: list[ResearchQuery],
    existing_sources_count: int,
    enabled: bool,
) -> ModelPriorNotes:
    """Synthesize model prior-knowledge supplement when enabled.

    Returns ModelPriorNotes with notes/terminology/uncertainty_notes.
    On failure or when disabled, returns a skipped status with warnings.
    """
    if not enabled or ctx is None:
        return ModelPriorNotes(enabled=False, status="skipped")
    settings = getattr(ctx, "settings", None)
    try:
        data = await ctx.call_with_retry(
            TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH,
            {
                "spec": spec.model_dump(mode="json"),
                "queries": [q.model_dump(mode="json") for q in queries],
                "existing_sources_count": existing_sources_count,
            },
            max_tokens=4096,
            temperature=getattr(settings, "temp_synthesize_model_prior_research", 0.2),
            required_keys=("notes", "terminology", "uncertainty_notes"),
            max_retries=2,
        )
        return ModelPriorNotes(
            enabled=True,
            status="succeeded",
            notes=_clean_list(data.get("notes"), limit=16, text_limit=500),
            terminology=_clean_list(data.get("terminology"), limit=24, text_limit=120),
            uncertainty_notes=_clean_list(data.get("uncertainty_notes"), limit=12, text_limit=300),
            warnings=_clean_list(data.get("warnings"), limit=8, text_limit=200),
        )
    except Exception as exc:  # noqa: BLE001 - model prior must not block
        return ModelPriorNotes(
            enabled=True,
            status="failed",
            warnings=[f"{type(exc).__name__}: {exc}"],
        )


def build_research_runtime_config(
    settings: Any,
    provider_name: str,
) -> dict[str, object]:
    """Return a non-secret runtime config snapshot for reports and diagnostics."""
    requested = (provider_name or "auto").strip().lower() or "auto"
    default_provider = str(
        getattr(settings, "research_default_provider", "auto") if settings is not None else "auto"
    )
    default_provider = (default_provider or "auto").strip().lower() or "auto"
    resolved_provider = default_provider if requested == "auto" else requested
    endpoint = str(
        getattr(settings, "research_http_endpoint", "") if settings is not None else ""
    ).strip()
    effective_endpoint = endpoint
    if resolved_provider == "tavily" and not effective_endpoint:
        effective_endpoint = "https://api.tavily.com/search"
    elif resolved_provider == "brave" and not effective_endpoint:
        effective_endpoint = "https://api.search.brave.com/res/v1/web/search"
    mcp_command = str(
        getattr(settings, "research_mcp_command", "") if settings is not None else ""
    ).strip()
    mcp_args_raw = str(
        getattr(settings, "research_mcp_args", "") if settings is not None else ""
    ).strip()
    mcp_args_json_raw = str(
        getattr(settings, "research_mcp_args_json", "") if settings is not None else ""
    ).strip()
    mcp_env_raw = str(
        getattr(settings, "research_mcp_env", "") if settings is not None else ""
    ).strip()
    mcp_env_json_raw = str(
        getattr(settings, "research_mcp_env_json", "") if settings is not None else ""
    ).strip()
    mcp_tool_arguments_raw = str(
        getattr(settings, "research_mcp_tool_arguments_json", "") if settings is not None else ""
    ).strip()
    if resolved_provider == "mcp_search":
        provider_configured = bool(mcp_command)
    elif resolved_provider in {"", "auto", "noop", "none"}:
        provider_configured = False
    else:
        provider_configured = bool(effective_endpoint)
    return {
        "requested_provider": requested,
        "resolved_provider": resolved_provider,
        "provider_configured": provider_configured,
        "research_enabled_default": bool(
            getattr(settings, "research_enabled", False) if settings is not None else False
        ),
        "endpoint_configured": bool(effective_endpoint),
        "timeout_s": float(
            getattr(settings, "research_timeout_s", 10.0) if settings is not None else 10.0
        ),
        "max_queries": int(
            getattr(settings, "research_max_queries", 3) if settings is not None else 3
        ),
        "results_per_query": int(
            getattr(settings, "research_results_per_query", 5) if settings is not None else 5
        ),
        "max_results": int(
            getattr(settings, "research_max_results", 5) if settings is not None else 5
        ),
        "retry_attempts": int(
            getattr(settings, "research_retry_attempts", 1) if settings is not None else 1
        ),
        "include_domains": _split_csv(
            getattr(settings, "research_include_domains", "") if settings is not None else ""
        ),
        "exclude_domains": _split_csv(
            getattr(settings, "research_exclude_domains", "") if settings is not None else ""
        ),
        "locale": str(
            getattr(settings, "research_locale", "") if settings is not None else ""
        ).strip(),
        "search_depth": str(
            getattr(settings, "research_search_depth", "basic") if settings is not None else "basic"
        ).strip()
        or "basic",
        "api_key_configured": bool(
            str(getattr(settings, "research_api_key", "") if settings is not None else "").strip()
        ),
        "dossier_enabled": bool(
            getattr(settings, "research_dossier_enabled", True) if settings is not None else True
        ),
        "dossier_max_sources": int(
            getattr(settings, "research_dossier_max_sources", 8) if settings is not None else 8
        ),
        "outline_grounding_enabled": bool(
            getattr(settings, "outline_research_grounding_enabled", True)
            if settings is not None
            else True
        ),
        "outline_grounding_notes_per_chapter": int(
            getattr(settings, "outline_research_grounding_notes_per_chapter", 3)
            if settings is not None
            else 3
        ),
        "use_llm_planning": bool(
            getattr(settings, "research_use_llm_planning", True) if settings is not None else True
        ),
        "model_prior_enabled": bool(
            getattr(settings, "research_model_prior_enabled", False)
            if settings is not None
            else False
        ),
        "mcp_configured": bool(mcp_command),
        "mcp_args_configured": bool(mcp_args_raw or mcp_args_json_raw),
        "mcp_command": mcp_command,
        "mcp_args": mcp_args_raw,
        "mcp_args_json": mcp_args_json_raw,
        "mcp_env_configured": bool(mcp_env_raw or mcp_env_json_raw),
        "mcp_env_hash": (
            hash_payload({"legacy": mcp_env_raw, "json": mcp_env_json_raw})
            if (mcp_env_raw or mcp_env_json_raw)
            else ""
        ),
        "mcp_api_key_env": str(
            getattr(settings, "research_mcp_api_key_env", "MINIMAX_API_KEY")
            if settings is not None
            else "MINIMAX_API_KEY"
        ).strip(),
        "mcp_protocol_version": str(
            getattr(settings, "research_mcp_protocol_version", "2024-11-05")
            if settings is not None
            else "2024-11-05"
        ).strip()
        or "2024-11-05",
        "mcp_stdio_framing": str(
            getattr(settings, "research_mcp_stdio_framing", "newline")
            if settings is not None
            else "newline"
        ).strip()
        or "newline",
        **(
            {"mcp_stdio_dispatcher_version": _MCP_STDIO_DISPATCHER_VERSION}
            if resolved_provider == "mcp_search"
            else {}
        ),
        "mcp_inherit_environment": bool(
            getattr(settings, "research_mcp_inherit_environment", False)
            if settings is not None
            else False
        ),
        "mcp_tool_name": str(
            getattr(settings, "research_mcp_tool_name", "") if settings is not None else ""
        ).strip(),
        "mcp_query_argument": str(
            getattr(settings, "research_mcp_query_argument", "query")
            if settings is not None
            else "query"
        ).strip()
        or "query",
        "mcp_tool_arguments_hash": (
            hash_payload(mcp_tool_arguments_raw) if mcp_tool_arguments_raw else ""
        ),
    }


def research_runtime_fingerprint(settings: Any, provider_name: str) -> str:
    """Return a cache key for research routing/settings without exposing secrets."""
    config = dict(build_research_runtime_config(settings, provider_name))
    endpoint = str(
        getattr(settings, "research_http_endpoint", "") if settings is not None else ""
    ).strip()
    api_key = str(getattr(settings, "research_api_key", "") if settings is not None else "").strip()
    config["endpoint_hash"] = hash_payload(endpoint) if endpoint else ""
    config["api_key_hash"] = hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else ""
    return hash_payload(config)


def build_research_brief(result: ResearchResult) -> ResearchBrief:
    """Compress normalized sources into a small prompt-facing brief."""
    notes: list[str] = []
    for index, source in enumerate(result.sources, start=1):
        notes.append(_source_note(index, source))
    summary = "；".join(note for note in notes[:5] if note)
    if not summary:
        summary = ""
    return ResearchBrief(summary=_clip(summary, 1200), source_notes=notes[:8])


def _source_note(index: int, source: ResearchSource) -> str:
    title = _clean(source.title) or "未命名来源"
    snippet = _clean(source.snippet)
    url = _clean(source.url)
    parts = [f"[{index}] {title}"]
    if snippet:
        parts.append(snippet)
    if url:
        parts.append(url)
    return _clip(" - ".join(parts), 700)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _clean_list(value: object, *, limit: int = 20, text_limit: int = 500) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clip(_clean(item), text_limit)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= max(0, int(limit or 0)):
            break
    return result


def _normalize_chapter_notes(
    value: object,
    *,
    notes_per_chapter: int,
) -> list[OutlineResearchChapterNote]:
    if not isinstance(value, list):
        return []
    result: list[OutlineResearchChapterNote] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            chapter_number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            chapter_number = 0
        if chapter_number <= 0:
            continue
        result.append(
            OutlineResearchChapterNote(
                chapter_number=chapter_number,
                reminders=_clean_list(
                    item.get("reminders"),
                    limit=notes_per_chapter,
                    text_limit=220,
                ),
                fact_risks=_clean_list(
                    item.get("fact_risks"),
                    limit=notes_per_chapter,
                    text_limit=220,
                ),
                source_refs=_clean_list(item.get("source_refs"), limit=6, text_limit=180),
            )
        )
    return sorted(result, key=lambda note: note.chapter_number)


def _chapter_numbers(chapters: list[dict[str, Any]]) -> list[int]:
    numbers: list[int] = []
    for item in chapters:
        try:
            number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            number = 0
        if number > 0 and number not in numbers:
            numbers.append(number)
    return numbers


def _rendered_context_chars(ctx: Any, task_type: TaskType, payload: object) -> int:
    builder = getattr(ctx, "builder", None) or getattr(ctx, "_builder", None)
    render = getattr(builder, "render", None)
    if callable(render) and isinstance(payload, dict):
        try:
            return len(str(render(task_type, payload)))
        except Exception:
            pass
    return json_char_size(payload)


def _merge_grounding_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: list[str] = []
    chapter_notes: list[dict[str, Any]] = []
    merged_lists: dict[str, list[Any]] = {
        "global_notes": [],
        "fact_risks": [],
        "terminology": [],
        "source_refs": [],
        "warnings": [],
    }
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        summary = _clean(payload.get("summary"))
        if summary and summary not in summaries:
            summaries.append(summary)
        raw_notes = payload.get("chapter_notes")
        if isinstance(raw_notes, list):
            chapter_notes.extend(item for item in raw_notes if isinstance(item, dict))
        for key in merged_lists:
            values = payload.get(key)
            if isinstance(values, list):
                merged_lists[key].extend(values)
    return {
        "summary": _clip("；".join(summaries), 1600),
        "chapter_notes": chapter_notes,
        **merged_lists,
    }


def _merge_outline_grounding_batches(
    payloads: list[dict[str, Any]],
    *,
    expected_chapters: list[int],
    notes_per_chapter: int,
) -> dict[str, Any]:
    merged = _merge_grounding_payloads(payloads)
    normalized = _normalize_chapter_notes(
        merged.get("chapter_notes"),
        notes_per_chapter=notes_per_chapter,
    )
    by_chapter: dict[int, dict[str, list[str]]] = {}
    for note in normalized:
        bucket = by_chapter.setdefault(
            note.chapter_number,
            {"reminders": [], "fact_risks": [], "source_refs": []},
        )
        bucket["reminders"].extend(note.reminders)
        bucket["fact_risks"].extend(note.fact_risks)
        bucket["source_refs"].extend(note.source_refs)

    missing = [chapter for chapter in expected_chapters if chapter not in by_chapter]
    if missing:
        raise ValueError(
            "outline research grounding merge lost chapter coverage: "
            + ",".join(str(chapter) for chapter in missing)
        )
    merged["chapter_notes"] = [
        {
            "chapter_number": chapter,
            "reminders": _clean_list(
                by_chapter[chapter]["reminders"],
                limit=notes_per_chapter,
                text_limit=220,
            ),
            "fact_risks": _clean_list(
                by_chapter[chapter]["fact_risks"],
                limit=notes_per_chapter,
                text_limit=220,
            ),
            "source_refs": _clean_list(
                by_chapter[chapter]["source_refs"],
                limit=6,
                text_limit=180,
            ),
        }
        for chapter in expected_chapters
    ]
    return merged


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    return value


def _split_csv(value: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").replace(";", ",").split(","):
        item = _clean(raw)
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _int_config(config: dict[str, object], key: str, default: int) -> int:
    raw = config.get(key, default)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return int(raw)
    if not isinstance(raw, (int, float, str)):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _clip(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

"""Profile orchestration for initialization coherence v2."""

from __future__ import annotations

import json
import logging
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.init_coherence import InitCoherenceProfile
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

PROFILE_REPORT = "init_coherence_profile.json"

_log = logging.getLogger(__name__)


def _emit_step(ctx: Any, step: str, payload: dict[str, Any]) -> None:
    callback = getattr(ctx, "on_step", None)
    if callable(callback):
        callback(step, payload)


def _init_efficiency_metrics(ctx: Any) -> dict[str, Any]:
    metrics = getattr(ctx, "_init_efficiency_metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
        try:
            ctx._init_efficiency_metrics = metrics
        except Exception:
            return metrics
    metrics.setdefault("claim_cache_hits", 0)
    metrics.setdefault("claim_cache_misses", 0)
    metrics.setdefault("claim_ledger_hits", 0)
    metrics.setdefault("claim_ledger_misses", 0)
    return metrics


def _increment_init_metric(ctx: Any, name: str, amount: int = 1) -> None:
    metrics = _init_efficiency_metrics(ctx)
    try:
        metrics[name] = int(metrics.get(name, 0) or 0) + amount
    except Exception:
        metrics[name] = amount


def load_reusable_init_coherence_profile(
    ctx: Any,
    *,
    require_refined: bool = False,
) -> dict[str, Any] | None:
    """Load a structurally valid project-local coherence profile for resume."""
    path = ctx.layout.reports_dir / PROFILE_REPORT
    if not ctx.storage.exists(path):
        return None
    try:
        payload = ctx.storage.load_json(path)
        if not isinstance(payload, dict):
            return None
        if _profile_contains_serialized_rule_objects(payload):
            _log.warning(
                "init_coherence_profile_resume_rejected_serialized_rules | path=%s",
                path,
            )
            return None
        profile_payload = _coerce_profile_payload(payload)
        profile = InitCoherenceProfile.model_validate(profile_payload)
    except Exception as exc:
        _log.warning("init_coherence_profile_resume_invalid | path=%s | error=%s", path, exc)
        return None
    if require_refined and not bool(profile_payload.get("refined_from_blueprint")):
        return None
    result = profile.model_dump(mode="json")
    if bool(profile_payload.get("refined_from_blueprint")):
        result["refined_from_blueprint"] = True
    _emit_step(
        ctx,
        "init_coherence_profile_resumed",
        {
            "refined": bool(result.get("refined_from_blueprint")),
            "genre_tags": profile.genre_tags,
            "state_axes": len(profile.project_ontology.state_axes),
            "payoff_types": len(profile.project_ontology.payoff_types),
            "summary": profile.summary,
        },
    )
    return result


async def refine_init_coherence_profile(
    ctx: Any,
    *,
    current_profile: dict[str, Any],
    spec: dict[str, Any],
    story_bible: dict[str, Any],
    character_bible: dict[str, Any],
    creative_director_packet: dict[str, Any] | None,
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    """Refine the project-local coherence profile after blueprint generation."""
    _emit_step(
        ctx,
        "refine_init_coherence_profile_start",
        {"status": "running", "source": "resume_or_generate"},
    )
    _increment_init_metric(ctx, "profile_call_count")
    from novel_forge.pipeline.long.services.init.init_split_artifacts import (
        refine_coherence_profile_split,
        split_tasks_enabled,
    )

    if split_tasks_enabled(ctx.settings):
        profile_payload = await refine_coherence_profile_split(
            ctx,
            current_profile=current_profile,
            spec=spec,
            story_bible=story_bible,
            character_bible=character_bible,
            creative_director_packet=creative_director_packet,
            blueprint=blueprint,
        )
        profile = InitCoherenceProfile.model_validate(_coerce_profile_payload(profile_payload))
        profile_payload = profile.model_dump(mode="json")
        profile_payload["refined_from_blueprint"] = True
        ctx.storage.save_json(ctx.layout.reports_dir / PROFILE_REPORT, profile_payload)
        _emit_step(
            ctx,
            "refine_init_coherence_profile",
            {
                "genre_tags": profile.genre_tags,
                "state_axes": len(profile.project_ontology.state_axes),
                "payoff_types": len(profile.project_ontology.payoff_types),
                "summary": profile.summary,
                "mode": "split",
            },
        )
        return profile_payload

    payload = await ctx.call_with_retry(
        TaskType.REFINE_INIT_COHERENCE_PROFILE,
        {
            "current_profile": current_profile,
            "spec": spec,
            "story_bible": story_bible,
            "character_bible": character_bible,
            "creative_director_packet": creative_director_packet or {},
            "blueprint": blueprint,
        },
        max_tokens=calculate_route_aware_max_tokens(
            ctx.router,
            TaskType.REFINE_INIT_COHERENCE_PROFILE,
            5200,
            prompt_overhead=6800,
            min_tokens=4096,
        ),
        temperature=getattr(ctx.settings, "temp_refine_init_coherence_profile", 0.1),
        required_keys=(
            "genre_tags",
            "narrative_modes",
            "project_ontology",
            "conflict_lens",
            "extraction_guidance",
            "summary",
        ),
        max_retries=3,
    )
    profile = InitCoherenceProfile.model_validate(_coerce_profile_payload(payload))
    profile_payload = profile.model_dump(mode="json")
    profile_payload["refined_from_blueprint"] = True
    ctx.storage.save_json(ctx.layout.reports_dir / PROFILE_REPORT, profile_payload)
    _emit_step(
        ctx,
        "refine_init_coherence_profile",
        {
            "genre_tags": profile.genre_tags,
            "state_axes": len(profile.project_ontology.state_axes),
            "payoff_types": len(profile.project_ontology.payoff_types),
            "summary": profile.summary,
            "mode": "single",
        },
    )
    return profile_payload


def claim_cache_stats(ctx: Any) -> dict[str, int]:
    """Return init claim-cache counters recorded on the context."""
    metrics = _init_efficiency_metrics(ctx)
    return {
        "claim_cache_hits": int(metrics.get("claim_cache_hits", 0) or 0),
        "claim_cache_misses": int(metrics.get("claim_cache_misses", 0) or 0),
    }


def set_init_profile_mode_metric(ctx: Any, mode: str) -> None:
    """Record the profile mode used by init for readiness/trace metadata."""
    metrics = _init_efficiency_metrics(ctx)
    metrics["profile_mode"] = str(mode or "").strip() or "unknown"


def _coerce_profile_string_list(
    value: Any,
    *,
    preferred_keys: tuple[str, ...] = (),
) -> list[str]:
    """Return a clean string list from permissive LLM/list payload shapes."""
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = ""
        if isinstance(item, dict):
            for key in preferred_keys + ("name", "label", "value", "id", "type", "marker", "term"):
                candidate = item.get(key)
                if candidate is not None and str(candidate).strip():
                    text = str(candidate).strip()
                    break
            if not text:
                text = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        else:
            text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _profile_contains_serialized_rule_objects(payload: dict[str, Any]) -> bool:
    """Detect legacy profile lists that silently JSON-stringified model objects."""

    values: list[Any] = []
    for key in ("conflict_lens", "extraction_guidance"):
        raw = payload.get(key)
        values.extend(raw if isinstance(raw, list) else [raw])
    ontology = payload.get("project_ontology")
    if isinstance(ontology, dict):
        for key in (
            "payoff_types",
            "irreversible_event_markers",
            "temporal_markers",
        ):
            raw = ontology.get(key)
            values.extend(raw if isinstance(raw, list) else [raw])
    for item in values:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not (text.startswith("{") and text.endswith("}")):
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return True
    return False


def _coerce_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    ontology = result.get("project_ontology")
    if not isinstance(ontology, dict):
        ontology = {}
    ontology = dict(ontology)
    list_key_hints: dict[str, tuple[str, ...]] = {
        "domains": ("domain",),
        "entity_types": ("entity_type",),
        "state_axes": ("axis",),
        "relationship_axes": ("axis",),
        "payoff_types": ("payoff_type", "type"),
        "irreversible_event_markers": ("marker", "event_type"),
        "temporal_markers": ("marker", "temporal_marker"),
    }
    for key, preferred in list_key_hints.items():
        ontology[key] = _coerce_profile_string_list(ontology.get(key), preferred_keys=preferred)
    terminology = ontology.get("terminology")
    if isinstance(terminology, dict):
        ontology["terminology"] = {
            str(key): str(value or "") for key, value in terminology.items() if str(key).strip()
        }
    else:
        ontology["terminology"] = {}
    result["project_ontology"] = ontology
    for key in ("genre_tags", "narrative_modes", "conflict_lens", "extraction_guidance"):
        value = result.get(key)
        result[key] = _coerce_profile_string_list(value)
    result["summary"] = str(result.get("summary") or "")
    return result

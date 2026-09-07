"""Init service cache and rollback helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    apply_local_blueprint_structural_fallback,
    pre_normalize_blueprint_payload,
)
from novel_forge.prompts.version import get_version_manager
from novel_forge.story_kernel.store import CanonStore

_log = logging.getLogger(__name__)

_LONG_INIT_TEXT_KEYS = (
    "premise",
    "genre",
    "tone",
    "title",
    "language",
    "characters_hint",
    "world_hint",
    "conflict_hint",
    "pov_hint",
    "opening_style",
    "ending_style",
    "extra_instructions",
)


@dataclass(frozen=True)
class LongInitRequestFreshness:
    drift: tuple[str, ...] = ()
    requires_confirmation: bool = False
    reset_performed: bool = False


if TYPE_CHECKING:
    from novel_forge.pipeline.long.services.init.init_context import InitLongServiceContext


def _pre_normalize_blueprint_payload(payload: Any, *, total_chapters: int) -> Any:
    return pre_normalize_blueprint_payload(payload, total_chapters=total_chapters)


def _apply_local_blueprint_structural_fallback(
    payload: Any,
    *,
    total_chapters: int,
    narrative_complexity: str = "standard",
    validation_errors: list[str] | None = None,
) -> dict[str, Any] | None:
    return apply_local_blueprint_structural_fallback(
        payload,
        total_chapters=total_chapters,
        narrative_complexity=narrative_complexity,
        validation_errors=validation_errors,
    )


def _get_embedding_config(profile_id: str | None = None) -> dict[str, Any] | None:
    """Get embedding model configuration from configured model profiles."""
    from novel_forge.memory.integration import get_embedding_config_from_profiles

    return get_embedding_config_from_profiles(profile_id)


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _remove_path_if_exists(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return True
        if path.is_dir():
            shutil.rmtree(path)
            return True
        if path.exists():
            path.unlink()
            return True
    except FileNotFoundError:
        return False
    return False


def _clean_request_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _canonicalize_request_value(value: Any) -> Any:
    if isinstance(value, str):
        return _clean_request_text(value)
    if isinstance(value, dict):
        return {str(key): _canonicalize_request_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize_request_value(item) for item in value]
    return value


def _normalize_long_init_request_payload_defaults(payload: Any) -> Any:
    """Normalize optional request-fingerprint fields before hashing."""
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    generation_options = normalized.get("generation_options")
    if isinstance(generation_options, dict):
        options = dict(generation_options)
        options.setdefault("polish_hint", "")
        options.setdefault("research_enabled", False)
        options.setdefault("research_provider", "auto")
        options.setdefault("research_query_hint", "")
        options.setdefault("research_config_fingerprint", "")
        options.setdefault("research_use_llm_planning", True)
        options.setdefault("research_model_prior_enabled", False)
        options.setdefault("creative_exploration", "adaptive")
        options.setdefault("planning_commitment", "progressive")
        normalized["generation_options"] = options
    return normalized


def _build_long_init_request_payload(
    *,
    premise: str,
    genre: str,
    tone: str,
    title: str,
    language: str,
    characters_hint: str,
    world_hint: str,
    conflict_hint: str,
    pov_hint: str,
    opening_style: str,
    ending_style: str,
    extra_instructions: str,
    total_chapters: int,
    words_per_chapter: int,
    volume_mode: str,
    chapters_per_volume: int,
    effective_volume_mode: bool,
    effective_chapters_per_volume: int,
    blueprint_element_preferences: dict[str, Any] | None,
    polish_hint: str = "",
    research_enabled: bool = False,
    research_provider: str = "auto",
    research_query_hint: str = "",
    research_config_fingerprint: str = "",
    research_use_llm_planning: bool = True,
    research_model_prior_enabled: bool = False,
    creative_exploration: str = "adaptive",
    planning_commitment: str = "full",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "init_input": {
            "premise": premise,
            "genre": genre,
            "tone": tone,
            "title": title,
            "language": language,
            "characters_hint": characters_hint,
            "world_hint": world_hint,
            "conflict_hint": conflict_hint,
            "pov_hint": pov_hint,
            "opening_style": opening_style,
            "ending_style": ending_style,
            "extra_instructions": extra_instructions,
        },
        "generation_options": {
            "total_chapters": total_chapters,
            "words_per_chapter": words_per_chapter,
            "volume_mode_setting": volume_mode,
            "chapters_per_volume_setting": chapters_per_volume,
            "effective_volume_mode": effective_volume_mode,
            "effective_chapters_per_volume": effective_chapters_per_volume,
            "blueprint_element_preferences": blueprint_element_preferences or {},
            "polish_hint": polish_hint,
            "research_enabled": bool(research_enabled),
            "research_provider": (research_provider or "auto").strip() or "auto",
            "research_query_hint": research_query_hint,
            "research_config_fingerprint": research_config_fingerprint if research_enabled else "",
            "research_use_llm_planning": bool(research_use_llm_planning),
            "research_model_prior_enabled": bool(research_model_prior_enabled),
            "creative_exploration": creative_exploration,
            "planning_commitment": planning_commitment,
        },
    }


def _long_init_request_fingerprint(payload: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 fingerprint for a long-init request.

    Injects current prompt-template version metadata so that template
    changes automatically invalidate stale cache entries.
    """
    enriched = dict(payload)
    enriched["_template_version"] = _get_template_fingerprint_info()
    raw = json.dumps(
        _canonicalize_request_value(_normalize_long_init_request_payload_defaults(enriched)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_template_fingerprint_info() -> dict[str, str]:
    """Build a canonical dict of current template versions for fingerprinting."""
    mgr = get_version_manager()
    info: dict[str, str] = {"system": mgr.get_current_version()}
    for cat in ("initialization", "writing", "planning", "checking"):
        v = mgr.get_category_version(cat)
        if v:
            info[f"cat_{cat}"] = v.version
    return info


def _request_value_changed(previous: Any, current: Any) -> bool:
    return bool(_canonicalize_request_value(previous) != _canonicalize_request_value(current))


def _summarize_long_request_drift(
    previous_payload: dict[str, Any] | None,
    current_payload: dict[str, Any],
) -> list[str]:
    previous_input = (
        previous_payload.get("init_input", {}) if isinstance(previous_payload, dict) else {}
    )
    current_input = current_payload.get("init_input", {})
    previous_options = (
        previous_payload.get("generation_options", {}) if isinstance(previous_payload, dict) else {}
    )
    current_options = current_payload.get("generation_options", {})

    drift: list[str] = []
    for key in _LONG_INIT_TEXT_KEYS:
        if _request_value_changed(previous_input.get(key), current_input.get(key)):
            drift.append(f"{key}: checkpoint does not match current request")
    for key in (
        "total_chapters",
        "words_per_chapter",
        "effective_volume_mode",
        "effective_chapters_per_volume",
        "polish_hint",
        "research_enabled",
        "research_provider",
        "research_query_hint",
        "research_config_fingerprint",
        "research_use_llm_planning",
        "research_model_prior_enabled",
        "creative_exploration",
        "planning_commitment",
    ):
        default_value: Any
        if key in {"polish_hint", "research_query_hint", "research_config_fingerprint"}:
            default_value = ""
        elif key == "research_enabled":
            default_value = False
        elif key == "research_use_llm_planning":
            default_value = True
        elif key == "research_model_prior_enabled":
            default_value = False
        elif key == "research_provider":
            default_value = "auto"
        elif key == "creative_exploration":
            default_value = "adaptive"
        elif key == "planning_commitment":
            default_value = "progressive"
        else:
            default_value = None
        if _request_value_changed(
            previous_options.get(key, default_value),
            current_options.get(key, default_value),
        ):
            drift.append(f"{key}: checkpoint does not match current request")
    if _request_value_changed(
        previous_options.get("blueprint_element_preferences", {}),
        current_options.get("blueprint_element_preferences", {}),
    ):
        drift.append("blueprint_element_preferences: checkpoint does not match current request")
    return drift


def _ensure_long_init_request_fresh(
    ctx: InitLongServiceContext,
    *,
    project_id: str,
    request_payload: dict[str, Any],
    allow_confirmed_reset: bool = False,
) -> LongInitRequestFreshness:
    meta_path = ctx.layout.init_request_meta_path
    request_fingerprint = _long_init_request_fingerprint(request_payload)

    previous_payload: dict[str, Any] | None = None
    previous_fingerprint = ""
    drift: list[str] = []

    if meta_path.exists():
        try:
            meta = ctx.storage.load_json(meta_path)
            previous_payload = (
                meta.get("request") if isinstance(meta.get("request"), dict) else None
            )
            previous_fingerprint = str(meta.get("request_fingerprint") or "")
        except Exception:
            drift.append("init_request_metadata_unreadable")
        else:
            if previous_fingerprint and previous_fingerprint != request_fingerprint:
                normalized_previous_fingerprint = _long_init_request_fingerprint(
                    previous_payload or {}
                )
                if normalized_previous_fingerprint != request_fingerprint:
                    drift.extend(_summarize_long_request_drift(previous_payload, request_payload))
                    if not drift:
                        drift.append("request_fingerprint_changed")
            elif not previous_fingerprint:
                drift.append("init_request_metadata_unreadable")
    elif ctx.layout.spec_path.exists():
        drift.append("init_request_metadata_missing")

    if drift:
        confirmed_project = bool(
            _has_generated_chapters(ctx.layout)
            or ctx.storage.exists(ctx.layout.reports_dir / "init_readiness.json")
            or ctx.storage.exists(ctx.layout.init_readiness_artifact_path)
        )
        if confirmed_project and not allow_confirmed_reset:
            ctx.on_step(
                "init_param_drift_requires_confirmation",
                {
                    "drift": drift,
                    "action": "preserve_confirmed_project_and_request_human_decision",
                },
            )
            return LongInitRequestFreshness(
                drift=tuple(drift),
                requires_confirmation=True,
                reset_performed=False,
            )
        _log.warning(
            "init_param_drift_detected | cached init request does not match current request, "
            "persisted artifacts will be cleared | drift=%s",
            "; ".join(drift),
        )
        ctx.on_step("init_param_drift", {"drift": drift})
        _assert_canon_project_id_matches(ctx.layout, project_id)
        removed_artifacts = _reset_project_for_reinit(ctx.layout)
        ctx.on_step(
            "init_project_reset",
            {"drift": drift, "removed_artifacts": removed_artifacts},
        )

    meta_payload: dict[str, Any] = {
        "schema_version": 1,
        "request_fingerprint": request_fingerprint,
        "request": request_payload,
    }
    ctx.storage.save_json(meta_path, meta_payload)
    return LongInitRequestFreshness(
        drift=tuple(drift),
        requires_confirmation=False,
        reset_performed=bool(drift),
    )


def reset_project_for_reinit(
    layout: ProjectLayout,
    *,
    preserve_chapters: bool = False,
    preserve_logs: bool = True,
) -> list[str]:
    """Remove prior project contents for a fresh init.

    ``preserve_chapters`` is only for the desktop confirmation path where the
    user explicitly keeps historical chapter markdown. ``preserve_logs`` stays
    true for automatic drift resets so failed-run diagnostics survive; the
    desktop "重新立项" path sets it false so old init logs cannot seed recovery
    helpers in the new run.
    """
    removed: list[str] = []
    preserved_names: set[str] = set()
    if preserve_logs:
        preserved_names.add(layout.logs_dir.name)
    if preserve_chapters:
        preserved_names.add(layout.chapters_dir.name)
    if layout.root.exists():
        for path in layout.root.iterdir():
            if path.name in preserved_names:
                continue
            if _remove_path_if_exists(path):
                removed.append(str(path))
    layout.ensure_dirs()
    return removed


def _reset_project_for_reinit(layout: ProjectLayout) -> list[str]:
    return reset_project_for_reinit(layout)


def _has_generated_chapters(layout: ProjectLayout) -> bool:
    """Return True once global init rollback could invalidate real chapter text."""
    return layout.chapters_dir.exists() and any(layout.chapters_dir.glob("chapter_*.md"))


def _init_artifact_paths_from_step(layout: ProjectLayout, step: str) -> list[Path]:
    """Artifacts to quarantine when a cached init step cannot be reused."""
    outline_memory_path = layout.memory_dir / "outline_episodic.json"
    groups: dict[str, list[Path]] = {
        "spec": [
            layout.spec_path,
            layout.reports_dir / "init_web_research.json",
            layout.reports_dir / "init_research_dossier.json",
            layout.reports_dir / "outline_research_grounding.json",
            layout.reports_dir / "init_upstream_health.json",
            layout.bible_path,
            layout.blueprint_elements_path,
            layout.characters_path,
            layout.style_profile_path,
            layout.blueprint_path,
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
        "story_bible": [
            layout.reports_dir / "outline_research_grounding.json",
            layout.reports_dir / "init_upstream_health.json",
            layout.bible_path,
            layout.characters_path,
            layout.style_profile_path,
            layout.blueprint_path,
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
        "init_web_research": [
            layout.reports_dir / "init_web_research.json",
            layout.reports_dir / "init_research_dossier.json",
            layout.reports_dir / "outline_research_grounding.json",
            layout.reports_dir / "init_upstream_health.json",
            layout.bible_path,
            layout.characters_path,
            layout.style_profile_path,
            layout.blueprint_path,
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
        "blueprint_elements": [
            layout.blueprint_elements_path,
            layout.style_profile_path,
            layout.blueprint_path,
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
        "character_bible": [
            layout.characters_path,
            layout.states_dir / "init_v2",
            layout.root / "initialization" / "fragments" / "character_bible",
            layout.style_profile_path,
            layout.blueprint_path,
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
        "style_profile": [
            layout.style_profile_path,
            layout.blueprint_path,
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
        "blueprint": [
            layout.blueprint_path,
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
        "outline": [
            layout.outline_path,
            layout.outline_session_path,
            layout.outline_tracker_path,
            layout.reports_dir / "outline_research_grounding.json",
            outline_memory_path,
            layout.narrative_contract_path,
            layout.canon_dir,
        ],
    }
    return groups.get(step, [])


def _quarantine_init_artifacts(layout: ProjectLayout, step: str) -> list[str]:
    """Move a failed init cache chain aside instead of deleting it."""
    if _has_generated_chapters(layout):
        raise RuntimeError(
            f"Cannot auto-rollback cached init artifact '{step}' because finalized chapters "
            "already exist. Please back up the project and repair the init artifacts manually."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_root = layout.states_dir / "init_rollbacks" / f"{stamp}_{step}"
    moved: list[str] = []
    for path in _init_artifact_paths_from_step(layout, step):
        if not path.exists():
            continue
        rel_path = path.relative_to(layout.root)
        dest = quarantine_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(dest))
            moved.append(str(rel_path))
        except FileNotFoundError:
            continue
    layout.ensure_dirs()
    return moved


def _rollback_cached_init_step(
    ctx: InitLongServiceContext,
    step: str,
    exc: Exception,
) -> list[str]:
    """Quarantine an invalid cached init step and report the resume rewind."""
    moved = _quarantine_init_artifacts(ctx.layout, step)
    _record_init_resume_rollback_decision(ctx, step=step, exc=exc, moved=moved)
    ctx.on_step(
        "init_resume_rollback",
        {
            "step": step,
            "reason": str(exc),
            "moved_artifacts": moved,
        },
    )
    _log.warning(
        "init_resume_rollback | step=%s | moved=%s | error=%s",
        step,
        moved,
        exc,
    )
    return moved


def _record_init_resume_rollback_decision(
    ctx: InitLongServiceContext,
    *,
    step: str,
    exc: Exception,
    moved: list[str],
) -> None:
    path = ctx.layout.reports_dir / "init_resume_decisions.json"
    record = {
        "stage": step,
        "artifact": step,
        "action": "rollback",
        "reason": str(exc),
        "path": "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata": {"moved_artifacts": list(moved), "error_type": type(exc).__name__},
    }
    try:
        payload = ctx.storage.load_json(path) if ctx.storage.exists(path) else {}
    except Exception:
        payload = {}
    decisions = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(decisions, list):
        decisions = []
    decisions.append(record)
    try:
        ctx.storage.save_json(path, {"schema_version": 1, "decisions": decisions})
    except Exception as persist_exc:
        _log.debug("init_resume_rollback_decision_persist_failed | error=%s", persist_exc)


def _load_cached_json_or_rollback(
    ctx: InitLongServiceContext,
    step: str,
    path: Path,
) -> dict[str, Any] | None:
    """Load cached JSON for resume, rewinding if it is unreadable."""
    if not ctx.storage.exists(path):
        return None
    try:
        return ctx.storage.load_json(path)
    except Exception as exc:
        _rollback_cached_init_step(ctx, step, exc)
        return None


def _load_cached_model_or_rollback(
    ctx: InitLongServiceContext,
    step: str,
    path: Path,
    validator: Any,
) -> Any | None:
    """Load and validate a cached init artifact, rewinding on schema drift."""
    payload = _load_cached_json_or_rollback(ctx, step, path)
    if payload is None:
        return None
    try:
        return validator(payload)
    except Exception as exc:
        _rollback_cached_init_step(ctx, step, exc)
        return None


def _assert_canon_project_id_matches(layout: ProjectLayout, project_id: str) -> Any | None:
    """Return existing canon or raise before any cleanup can hide a mismatch."""
    canon_store = CanonStore(layout.root)
    if not canon_store.exists():
        return None
    existing_canon = canon_store.load()
    if existing_canon.project_id != project_id:
        raise ValueError(
            f"Cannot initialize project '{project_id}': "
            f"Canon files already exist for a different project '{existing_canon.project_id}'.\n"
            f"This will cause character/timeline mismatches in future chapters.\n"
            f"To fix this, delete the old canon:\n"
            f"  rm -rf {layout.root / 'canon'}\n"
            f"Then re-run init-long."
        )
    return existing_canon


def _percent_to_chapter(percent: Any, *, total_chapters: int, is_start: bool) -> int:
    try:
        p = float(percent)
    except Exception:
        p = 0.0 if is_start else 100.0
    p = max(0.0, min(100.0, p))
    if is_start:
        raw = int(math.floor(total_chapters * (p / 100.0))) + 1
    else:
        raw = int(math.ceil(total_chapters * (p / 100.0)))
    return _clamp_int(raw, 1, max(1, total_chapters))


def _build_base_ctx(
    spec: StorySpec, expected_total_words: int, *, premise: str | None = None
) -> dict[str, Any]:
    return {
        "premise": premise or spec.theme,
        "genre": spec.genre,
        "tone": spec.tone,
        "title": spec.title,
        "language": spec.language,
        "characters_hint": spec.characters_hint,
        "world_hint": spec.world_hint,
        "conflict_hint": spec.conflict_hint,
        "pov_hint": spec.pov_hint,
        "opening_style": spec.opening_style,
        "ending_style": spec.ending_style,
        "extra_instructions": spec.extra_instructions,
        "expected_total_words": expected_total_words,
    }

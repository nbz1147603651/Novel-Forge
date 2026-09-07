"""Implementation slice extracted from init_service.py (init_creative_refinement.py)."""

from __future__ import annotations

from novel_forge.pipeline.long.services.init.init_common import (
    BLUEPRINT_ARTIFACT,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    Any,
    InitCreativeRefinementReport,
    InitLongServiceContext,
    NarrativeBlueprint,
    StoryBible,
    StorySpec,
    TaskType,
    _log,
    apply_scoped_init_patch,
    backup_init_artifact,
    blocking_issues,
    calculate_route_aware_max_tokens,
    collect_repair_scopes,
    dump_story_bible_for_prompt,
    hash_payload,
    init_coherence_issue_id,
    manifest_for_context,
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.init.init_repair_manifest import (
    _record_init_repair_manifest_round,
)
from novel_forge.pipeline.long.services.init.init_source_resume import _load_optional_json
from novel_forge.pipeline.long.services.init.init_value_helpers import (
    _init_coherence_list,
    _init_coherence_min_severity,
    _safe_init_int,
)


def _init_creative_refinement_input_hashes(
    *,
    spec: StorySpec,
    story_bible: StoryBible,
    character_bible: Any,
    creative_packet: Any,
    init_coherence_profile: dict[str, Any],
    blueprint: NarrativeBlueprint,
    auto_apply_low_risk: bool,
) -> dict[str, str]:
    return {
        "spec": hash_payload(spec),
        "story_bible": hash_payload(story_bible),
        "character_bible": hash_payload(character_bible),
        "creative_director_packet": hash_payload(creative_packet),
        "coherence_profile": hash_payload(init_coherence_profile),
        "blueprint": hash_payload(blueprint),
        "auto_apply_low_risk": hash_payload(bool(auto_apply_low_risk)),
    }

def _save_init_creative_refinement_report(
    ctx: InitLongServiceContext,
    report_path: Any,
    report_payload: dict[str, Any],
    *,
    input_hashes: dict[str, str],
    input_blueprint_hash: str,
    output_blueprint: NarrativeBlueprint,
    applied: bool,
    applied_patches: int = 0,
    skipped_patches: list[dict[str, Any]] | None = None,
) -> None:
    ctx.storage.save_json(
        report_path,
        {
            **report_payload,
            "input_hashes": input_hashes,
            "input_blueprint_hash": input_blueprint_hash,
            "output_blueprint_hash": hash_payload(output_blueprint),
            "applied": applied,
            "applied_patches": applied_patches,
            "skipped_patches": skipped_patches or [],
        },
    )
    manifest_for_context(ctx).record_success(
        artifact="init_creative_refinement",
        workflow="init_long",
        step="init_creative_refinement",
        status=STATUS_SUCCEEDED if applied else STATUS_SKIPPED,
        input_hashes=input_hashes,
        output_hashes={
            "blueprint": hash_payload(output_blueprint),
            "report": hash_payload(report_payload),
        },
        paths={"report": str(report_path), "blueprint": str(ctx.layout.blueprint_path)},
        metadata={
            "applied": applied,
            "applied_patches": applied_patches,
            "skipped_patch_count": len(skipped_patches or []),
        },
    )

def _load_reusable_init_creative_refinement(
    ctx: InitLongServiceContext,
    *,
    report_path: Any,
    input_hashes: dict[str, str],
    current_blueprint: NarrativeBlueprint,
) -> dict[str, Any] | None:
    payload = _load_optional_json(ctx, report_path)
    if not payload:
        return None
    recorded_hashes = payload.get("input_hashes")
    if not isinstance(recorded_hashes, dict) or recorded_hashes != input_hashes:
        return None
    if str(payload.get("output_blueprint_hash") or "") != hash_payload(current_blueprint):
        return None
    try:
        report = InitCreativeRefinementReport.model_validate(payload)
    except Exception:
        return None
    ctx.on_step(
        "init_creative_refinement_resumed",
        {
            "suggestions": len(report.suggestions),
            "patches": len(report.patches),
            "risks": len(report.risks),
            "auto_apply": bool(payload.get("auto_apply", False)),
            "summary": report.summary,
            "path": str(report_path),
            "source": "cache",
            "applied": bool(payload.get("applied", False)),
        },
    )
    manifest_for_context(ctx).record_success(
        artifact="init_creative_refinement",
        workflow="init_long",
        step="init_creative_refinement",
        status=STATUS_SUCCEEDED if bool(payload.get("applied", False)) else STATUS_SKIPPED,
        input_hashes=input_hashes,
        output_hashes={
            "blueprint": hash_payload(current_blueprint),
            "report": hash_payload(payload),
        },
        paths={"report": str(report_path), "blueprint": str(ctx.layout.blueprint_path)},
        metadata={"source": "cache", "applied": bool(payload.get("applied", False))},
    )
    return payload

async def _maybe_refine_blueprint_creatively(
    ctx: InitLongServiceContext,
    *,
    spec: StorySpec,
    story_bible: StoryBible,
    character_bible: Any,
    creative_packet: Any,
    init_coherence_profile: dict[str, Any],
    blueprint: NarrativeBlueprint,
    total_chapters: int,
) -> NarrativeBlueprint:
    if not bool(getattr(ctx.settings, "init_creative_refinement_enabled", False)):
        return blueprint

    report_path = ctx.layout.reports_dir / "init_creative_refinement.json"
    auto_apply = bool(getattr(ctx.settings, "init_creative_refinement_auto_apply_low_risk", True))
    input_hashes = _init_creative_refinement_input_hashes(
        spec=spec,
        story_bible=story_bible,
        character_bible=character_bible,
        creative_packet=creative_packet,
        init_coherence_profile=init_coherence_profile,
        blueprint=blueprint,
        auto_apply_low_risk=auto_apply,
    )
    input_blueprint_hash = hash_payload(blueprint)
    if (
        _load_reusable_init_creative_refinement(
            ctx,
            report_path=report_path,
            input_hashes=input_hashes,
            current_blueprint=blueprint,
        )
        is not None
    ):
        return blueprint

    try:
        response = await ctx.call_with_retry(
            TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
            {
                "spec": spec.model_dump(mode="json"),
                "story_bible": dump_story_bible_for_prompt(story_bible, mode="json"),
                "character_bible": character_bible.model_dump(mode="json"),
                "creative_director_packet": creative_packet.model_dump(mode="json"),
                "coherence_profile": init_coherence_profile,
                "blueprint": blueprint.model_dump(mode="json"),
                "auto_apply_low_risk": auto_apply,
            },
            max_tokens=calculate_route_aware_max_tokens(
                ctx.router,
                TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
                4096,
                prompt_overhead=7600,
                min_tokens=2048,
            ),
            temperature=getattr(ctx.settings, "temp_refine_init_artifacts_from_synopsis", 0.15),
            required_keys=(
                "suggestions",
                "repair_scope",
                "patches",
                "preserve",
                "risks",
                "summary",
            ),
            max_retries=3,
        )
        report = InitCreativeRefinementReport.model_validate(response)
    except Exception as exc:
        _log.warning("init_creative_refinement_skipped | error=%s", exc)
        ctx.on_step(
            "init_creative_refinement_skipped",
            {"error_type": type(exc).__name__, "error": str(exc), "fallback": "keep_blueprint"},
        )
        return blueprint

    report_payload = report.model_dump(mode="json")
    report_payload["auto_apply"] = auto_apply
    low_risk_ids = {
        item.suggestion_id
        for item in report.suggestions
        if item.risk_level == "low" and bool(item.auto_apply)
    }
    patches = [
        patch
        for patch in report.patches
        if isinstance(patch, dict)
        and str(patch.get("risk_level") or "medium").lower() == "low"
        and (
            not low_risk_ids
            or bool(
                {str(item) for item in _init_coherence_list(patch.get("issue_ids"))} & low_risk_ids
            )
        )
    ]
    event_payload = {
        "suggestions": len(report.suggestions),
        "patches": len(patches),
        "risks": len(report.risks),
        "auto_apply": auto_apply,
        "summary": report.summary,
        "path": str(report_path),
    }
    if not auto_apply or not patches:
        _save_init_creative_refinement_report(
            ctx,
            report_path,
            report_payload,
            input_hashes=input_hashes,
            input_blueprint_hash=input_blueprint_hash,
            output_blueprint=blueprint,
            applied=False,
        )
        ctx.on_step("init_creative_refinement", event_payload)
        return blueprint

    patch_report = {
        "repair_scope": report_payload.get("repair_scope", []),
        "preserve": report_payload.get("preserve", []),
        "change_intent": report.summary,
        "issues": [
            {
                "id": item.suggestion_id,
                "severity": "low",
                "description": item.rationale,
                "artifact": item.artifact,
            }
            for item in report.suggestions
            if item.suggestion_id in low_risk_ids
        ],
    }
    scopes = collect_repair_scopes(patch_report, default_artifact=BLUEPRINT_ARTIFACT)
    if not scopes:
        _save_init_creative_refinement_report(
            ctx,
            report_path,
            report_payload,
            input_hashes=input_hashes,
            input_blueprint_hash=input_blueprint_hash,
            output_blueprint=blueprint,
            applied=False,
        )
        ctx.on_step(
            "init_creative_refinement",
            {**event_payload, "applied": False, "reason": "missing_repair_scope"},
        )
        return blueprint

    payload = blueprint.model_dump(mode="json")
    backup_init_artifact(
        storage=ctx.storage,
        layout=ctx.layout,
        artifact=BLUEPRINT_ARTIFACT,
        payload=payload,
        round_index=0,
    )
    skipped_patches: list[dict[str, Any]] = []
    try:
        repaired_payload, applied = apply_scoped_init_patch(
            artifact=BLUEPRINT_ARTIFACT,
            payload=payload,
            patch_payload={"patches": patches, "summary": report.summary},
            scopes=scopes,
            max_ops=int(getattr(ctx.settings, "init_coherence_patch_max_ops", 40) or 40),
            strict=False,
            skipped=skipped_patches,
        )
        if not applied:
            _save_init_creative_refinement_report(
                ctx,
                report_path,
                report_payload,
                input_hashes=input_hashes,
                input_blueprint_hash=input_blueprint_hash,
                output_blueprint=blueprint,
                applied=False,
                skipped_patches=skipped_patches,
            )
            ctx.on_step(
                "init_creative_refinement",
                {
                    **event_payload,
                    "applied": False,
                    "skipped_patches": skipped_patches,
                    "reason": "no_effective_patch",
                },
            )
            return blueprint
        normalized_payload = pre_normalize_blueprint_payload(
            repaired_payload,
            total_chapters=total_chapters,
        )
        refined_blueprint = NarrativeBlueprint.model_validate(normalized_payload)
    except Exception as exc:
        _log.warning("init_creative_refinement_apply_failed | error=%s", exc)
        _save_init_creative_refinement_report(
            ctx,
            report_path,
            report_payload,
            input_hashes=input_hashes,
            input_blueprint_hash=input_blueprint_hash,
            output_blueprint=blueprint,
            applied=False,
            skipped_patches=skipped_patches,
        )
        ctx.on_step(
            "init_creative_refinement",
            {
                **event_payload,
                "applied": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "skipped_patches": skipped_patches,
            },
        )
        return blueprint

    ctx.storage.save_json(ctx.layout.blueprint_path, refined_blueprint.model_dump(mode="json"))
    _save_init_creative_refinement_report(
        ctx,
        report_path,
        report_payload,
        input_hashes=input_hashes,
        input_blueprint_hash=input_blueprint_hash,
        output_blueprint=refined_blueprint,
        applied=True,
        applied_patches=len(applied),
        skipped_patches=skipped_patches,
    )
    ctx.on_step(
        "init_creative_refinement",
        {
            **event_payload,
            "applied": True,
            "applied_patches": len(applied),
            "skipped_patches": skipped_patches,
        },
    )
    return refined_blueprint

def _try_local_blueprint_coherence_fallback(
    ctx: InitLongServiceContext,
    *,
    blueprint: NarrativeBlueprint,
    report: dict[str, Any],
    round_index: int,
    repairs: list[dict[str, Any]],
) -> NarrativeBlueprint | None:
    """Locally soften duplicate-final-state turning points after LLM repair is exhausted."""
    min_severity = _init_coherence_min_severity(ctx.settings)
    issues = blocking_issues(report, min_severity=min_severity)
    if not issues:
        return None
    scopes = collect_repair_scopes(report, default_artifact=BLUEPRINT_ARTIFACT)
    key_turning_scope_chapters: set[int] = set()
    for scope in scopes:
        if scope.artifact != BLUEPRINT_ARTIFACT:
            continue
        if "key_turning_points" not in scope.fields:
            continue
        key_turning_scope_chapters.update(scope.chapters)
    if not key_turning_scope_chapters:
        return None

    issue_text = " ".join(
        str(issue.get(key) or "")
        for issue in issues
        for key in ("description", "resolution")
        if isinstance(issue, dict)
    )
    duplicate_markers = ("重复", "不可逆", "首次", "payoff", "重复计数")
    if not any(marker in issue_text for marker in duplicate_markers):
        return None

    payload = blueprint.model_dump(mode="json")
    turning_points = payload.get("key_turning_points")
    if not isinstance(turning_points, list):
        return None

    note = (
        "本节点作为终局回收与总结呈现，承接前文已发生的关系、隐患与信任进展，"
        "不标记为新的首次揭示、首次达成或重复 payoff。"
    )
    patches: list[dict[str, Any]] = []
    for index, item in enumerate(turning_points):
        if not isinstance(item, dict):
            continue
        chapter_number = _safe_init_int(item.get("chapter_number"))
        if chapter_number not in key_turning_scope_chapters:
            continue
        description = str(item.get("description") or "").strip()
        if not description or "不标记为新的首次" in description:
            continue
        patches.append(
            {
                "op": "replace",
                "path": f"/key_turning_points/{index}/description",
                "value": f"{description}。{note}",
                "issue_ids": [
                    str(issue.get("id"))
                    for issue in issues
                    if isinstance(issue, dict) and issue.get("id")
                ],
                "rationale": "本地兜底：将重复不可逆/首次完成表述降为终局总结，避免重复计数。",
            }
        )
    if not patches:
        return None

    skipped_patches: list[dict[str, Any]] = []
    repaired_payload, applied = apply_scoped_init_patch(
        artifact=BLUEPRINT_ARTIFACT,
        payload=payload,
        patch_payload={
            "patches": patches,
            "summary": "本地兜底修复蓝图关键转折点的重复不可逆标记。",
        },
        scopes=scopes,
        max_ops=max(1, len(patches)),
        strict=False,
        skipped=skipped_patches,
    )
    if not applied:
        return None
    total_chapters = max(
        [
            _safe_init_int(volume.get("end_chapter"))
            for volume in payload.get("volumes", [])
            if isinstance(volume, dict)
        ]
        + [
            _safe_init_int(item.get("chapter_number"))
            for item in payload.get("key_turning_points", [])
            if isinstance(item, dict)
        ]
        + [0]
    )
    repaired_blueprint = NarrativeBlueprint.model_validate(
        pre_normalize_blueprint_payload(
            repaired_payload,
            total_chapters=total_chapters,
        )
    )
    repair_record = {
        "artifact": BLUEPRINT_ARTIFACT,
        "round": round_index,
        "status": "applied",
        "local_fallback": True,
        "source_issue_ids": sorted(
            {init_coherence_issue_id(issue) for issue in issues if isinstance(issue, dict)}
        ),
        "source_blocking_issue_count": len(issues),
        "patch_count": len(applied),
        "skipped_patch_count": len(skipped_patches),
        "patches": applied,
        "skipped_patches": skipped_patches,
        "summary": "本地兜底：将重复不可逆/首次完成转折点改为终局总结表述。",
    }
    repairs.append(repair_record)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_artifact_repair.json", {"repairs": repairs}
    )
    _record_init_repair_manifest_round(ctx, repair_record)
    ctx.on_step("repair_init_artifact_patch", repair_record)
    return repaired_blueprint

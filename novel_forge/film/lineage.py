"""Pure upstream-source reconciliation for non-destructive film invalidation."""

from __future__ import annotations

import re

from .schemas import (
    ArtifactSource,
    FilmDecision,
    FilmShot,
    FilmStage,
    FilmStudioState,
    FilmVisualAsset,
)

_BROAD_VISUAL_SOURCES = {
    "story_spec",
    "story_bible",
    "style_profile",
    "narrative_blueprint",
}
_CHARACTER_SOURCES = {"character_bible"}
_SHOT_SOURCES = {
    *_BROAD_VISUAL_SOURCES,
    *_CHARACTER_SOURCES,
    "outline",
    "voice_team",
    "audio_creative_bible",
}


def changed_source_artifacts(
    previous: list[ArtifactSource],
    current: list[ArtifactSource],
) -> list[str]:
    old = {item.artifact_type: item for item in previous}
    new = {item.artifact_type: item for item in current}
    changed: list[str] = []
    for artifact_type in sorted(set(old) | set(new)):
        before = old.get(artifact_type)
        after = new.get(artifact_type)
        before_identity = (
            before.revision,
            before.input_signature,
            before.quality_status,
            before.derivation_status,
        ) if before is not None else None
        after_identity = (
            after.revision,
            after.input_signature,
            after.quality_status,
            after.derivation_status,
        ) if after is not None else None
        if before_identity != after_identity:
            changed.append(artifact_type)
    return changed


def _changed_chapters(changed: list[str]) -> set[int]:
    result: set[int] = set()
    for artifact_type in changed:
        match = re.fullmatch(r"novel_chapter:(\d+)", artifact_type)
        if match is not None:
            result.add(int(match.group(1)))
    return result


def _asset_is_impacted(asset: FilmVisualAsset, changed: set[str]) -> bool:
    if changed & _BROAD_VISUAL_SOURCES:
        return True
    if changed & _CHARACTER_SOURCES:
        return asset.asset_type in {"character", "expression_sheet", "pose_sheet"}
    if "outline" in changed or any(item.startswith("novel_chapter:") for item in changed):
        return asset.asset_type in {"location", "scene_variants", "storyboard_contact_sheet"}
    return False


def _shot_is_impacted(
    shot: FilmShot,
    state: FilmStudioState,
    changed: set[str],
    changed_chapters: set[int],
) -> bool:
    if changed & _SHOT_SOURCES:
        return True
    if not changed_chapters:
        return False
    scene = next((item for item in state.screenplay.scenes if item.scene_id == shot.scene_id), None)
    return bool(scene is not None and scene.source_chapter in changed_chapters)


def _stale_fields(*, locked: bool, changed: list[str]) -> dict[str, object]:
    return {
        "derivation_status": "conflict" if locked else "stale",
        "stale_reasons": [f"upstream_changed:{item}" for item in changed],
        "stale_decision": "pending",
    }


def reconcile_film_sources(
    projected: FilmStudioState,
    previous: FilmStudioState,
) -> FilmStudioState:
    """Merge new source projections while preserving generated media by ID."""

    changed = changed_source_artifacts(
        previous.production_bible.sources,
        projected.production_bible.sources,
    )
    if not changed:
        return previous.model_copy(
            update={
                "production_bible": projected.production_bible,
                "source_signature": projected.source_signature,
                "changed_source_artifacts": [],
            }
        )

    changed_set = set(changed)
    changed_chapters = _changed_chapters(changed)
    old_assets = {item.asset_id: item for item in previous.visual_assets}
    assets: list[FilmVisualAsset] = []
    decisions = [
        item for item in previous.decisions if not item.decision_id.startswith("lineage:")
    ]
    for projected_asset in projected.visual_assets:
        old = old_assets.get(projected_asset.asset_id)
        if old is None:
            assets.append(projected_asset)
            continue
        generated = bool(old.selected_url or old.candidates or old.provider_task)
        impacted = generated and _asset_is_impacted(projected_asset, changed_set)
        base = old if old.locked else projected_asset
        asset_update: dict[str, object] = {
            "candidates": old.candidates,
            "selected_url": old.selected_url,
            "provider_task": old.provider_task,
            "locked": old.locked,
            "provider_id": old.provider_id,
            "model_id": old.model_id,
            "qc_status": old.qc_status,
        }
        if impacted:
            asset_update.update(_stale_fields(locked=old.locked, changed=changed))
        else:
            asset_update.update(
                {
                    "source_signature": projected.source_signature,
                    "derivation_status": "fresh",
                    "stale_reasons": [],
                    "stale_decision": "",
                }
            )
        merged_asset = base.model_copy(update=asset_update)
        assets.append(merged_asset)
        if impacted and old.locked:
            decisions.append(
                _lineage_decision(
                    "asset",
                    merged_asset.asset_id,
                    merged_asset.name,
                    changed,
                )
            )

    old_shots = {item.shot_id: item for item in previous.shots}
    shots: list[FilmShot] = []
    for projected_shot in projected.shots:
        old_shot = old_shots.get(projected_shot.shot_id)
        if old_shot is None:
            shots.append(projected_shot)
            continue
        generated = bool(
            old_shot.selected_asset_url or old_shot.candidates or old_shot.provider_task
        )
        impacted = generated and _shot_is_impacted(
            old_shot, previous, changed_set, changed_chapters
        )
        shot_base = old_shot if old_shot.locked else projected_shot
        shot_update: dict[str, object] = {
            "candidates": old_shot.candidates,
            "selected_asset_url": old_shot.selected_asset_url,
            "provider_task": old_shot.provider_task,
            "locked": old_shot.locked,
            "provider_id": old_shot.provider_id,
            "model_id": old_shot.model_id,
            "qc_status": old_shot.qc_status,
            "qc_notes": old_shot.qc_notes,
        }
        if impacted:
            shot_update.update(_stale_fields(locked=old_shot.locked, changed=changed))
        else:
            shot_update.update(
                {
                    "source_signature": projected.source_signature,
                    "derivation_status": "fresh",
                    "stale_reasons": [],
                    "stale_decision": "",
                }
            )
        merged_shot = shot_base.model_copy(update=shot_update)
        shots.append(merged_shot)
        if impacted and old_shot.locked:
            decisions.append(
                _lineage_decision(
                    "shot",
                    merged_shot.shot_id,
                    merged_shot.title,
                    changed,
                )
            )

    source_blockers = [
        f"novel_source_not_deliverable:{item.artifact_type}"
        for item in projected.production_bible.sources
        if item.derivation_status in {"stale", "conflict", "blocked"}
        or item.quality_status == "blocked"
    ]
    pending = [
        f"stale_asset_decision:{item.asset_id}"
        for item in assets
        if item.stale_decision == "pending"
    ] + [
        f"stale_shot_decision:{item.shot_id}"
        for item in shots
        if item.stale_decision == "pending"
    ]
    delivery_blockers = list(dict.fromkeys([*source_blockers, *pending]))
    delivery = previous.delivery
    if delivery is not None:
        delivery_blockers.append("delivery_source_signature_mismatch")
        delivery = delivery.model_copy(
            update={
                "derivation_status": "stale",
                "blocking_reasons": list(dict.fromkeys(delivery_blockers)),
            }
        )
    return projected.model_copy(
        update={
            "mode": previous.mode,
            "visual_assets": assets,
            "shots": shots,
            "timeline": previous.timeline,
            "media_artifacts": previous.media_artifacts,
            "jobs": previous.jobs,
            "qc_reports": previous.qc_reports,
            "vision_qc_reports": previous.vision_qc_reports,
            "compliance_report": previous.compliance_report,
            "delivery": delivery,
            "decisions": decisions,
            "source_signature": projected.source_signature,
            "changed_source_artifacts": changed,
            "delivery_blocking_reasons": list(dict.fromkeys(delivery_blockers)),
            "notices": [
                *previous.notices,
                f"检测到 {len(changed)} 项上游版本变化，已保留旧媒体并标记受影响依赖。",
            ],
        }
    )


def _lineage_decision(kind: str, subject_id: str, label: str, changed: list[str]) -> FilmDecision:
    return FilmDecision(
        decision_id=f"lineage:{kind}:{subject_id}",
        stage=FilmStage.VISUAL_DEVELOPMENT if kind == "asset" else FilmStage.SHOT_PRODUCTION,
        title=f"锁定但已过期：{label or subject_id}",
        description="来源变化：" + "、".join(changed),
        choices=["preserve_old_version", "regenerate", "rebind"],
        status="pending",
    )


__all__ = ["changed_source_artifacts", "reconcile_film_sources"]

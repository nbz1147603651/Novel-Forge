"""Project sound-library loading and deterministic cue-to-asset resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from novel_forge.core.config import get_application_assets_dir
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    ChapterSoundResolutionReport,
    DubbingScript,
    SoundAsset,
    SoundCueResolution,
    SoundLibraryManifest,
)

_SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


def application_sound_library_root() -> Path:
    """Return the app-owned, cross-project reusable sound-library directory."""

    return get_application_assets_dir() / "audio"


def application_sound_assets_dir() -> Path:
    return application_sound_library_root() / "sounds"


def application_sound_library_path() -> Path:
    return application_sound_library_root() / "sound_library.json"


def load_sound_library(layout: ProjectLayout) -> SoundLibraryManifest:
    """Load the project manifest, treating missing or invalid files as empty."""
    path = layout.tts_sound_library_path
    if not path.exists():
        return SoundLibraryManifest()
    try:
        return SoundLibraryManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return SoundLibraryManifest()


def save_sound_library(layout: ProjectLayout, library: SoundLibraryManifest) -> None:
    """Persist the manifest atomically.

    Callers that mutate a project-owned sound library must hold the project
    lock.  Keeping the actual write here avoids divergent JSON serialization
    rules between the Desktop importer and generated-asset services.
    """
    layout.tts_sound_library_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(layout.tts_sound_library_path, library.model_dump(mode="json"))


def load_application_sound_library() -> SoundLibraryManifest:
    """Load the reusable application library without coupling it to a project."""

    path = application_sound_library_path()
    if not path.exists():
        return SoundLibraryManifest()
    try:
        manifest = SoundLibraryManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return SoundLibraryManifest()
    return manifest.model_copy(
        update={
            "assets": [
                asset.model_copy(update={"scope": "application"}) for asset in manifest.assets
            ]
        }
    )


def save_application_sound_library(library: SoundLibraryManifest) -> None:
    """Persist reusable assets atomically under the application data directory."""

    path = application_sound_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = library.model_copy(
        update={
            "assets": [
                asset.model_copy(update={"scope": "application"}) for asset in library.assets
            ]
        }
    )
    atomic_write_json(path, normalized.model_dump(mode="json"))


def sound_asset_path(layout: ProjectLayout, asset: SoundAsset) -> Path:
    """Return an asset path from its declared storage scope."""

    root = (
        application_sound_assets_dir()
        if asset.scope == "application"
        else layout.tts_sound_assets_dir
    )
    return root / asset.relative_path


def set_sound_asset_approval(
    layout: ProjectLayout,
    asset_id: str,
    *,
    status: Literal["approved", "pending", "rejected"],
) -> SoundAsset:
    """Update a generated candidate's review decision and persist it atomically.

    Workspace/API callers must hold the project lock around this read-modify-
    write operation.  Returning the updated asset makes a future Desktop review
    panel independent from manifest internals.
    """
    library = load_sound_library(layout)
    for index, asset in enumerate(library.assets):
        if asset.asset_id != asset_id:
            continue
        updated = asset.model_copy(update={"approval_status": status})
        library.assets[index] = updated
        save_sound_library(layout, library)
        return updated
    raise KeyError(f"Sound asset not found: {asset_id}")


def resolve_sound_cues(
    *,
    layout: ProjectLayout,
    script: DubbingScript,
    library: SoundLibraryManifest | None = None,
    application_library: SoundLibraryManifest | None = None,
    fallback_cue_keys: set[tuple[str, int]] | None = None,
) -> ChapterSoundResolutionReport:
    """Resolve every generated environmental cue against the local asset manifest.

    Resolution is deterministic and never guesses a filesystem path from LLM
    output.  A cue either maps to one declared, safe asset or remains visible
    as missing for the author to address.

    ``fallback_cue_keys``: (cue_kind, cue_index) pairs whose generation already
    failed.  For those cues a missing resolution is degraded to any approved
    same-kind asset (with an explicit review hint) instead of blocking the
    whole chapter delivery.
    """
    project_library = library if library is not None else load_sound_library(layout)
    application_library = (
        application_library if application_library is not None else load_application_sound_library()
    )
    assets = [asset.model_copy(update={"scope": "project"}) for asset in project_library.assets] + [
        asset.model_copy(update={"scope": "application"}) for asset in application_library.assets
    ]
    resolutions: list[SoundCueResolution] = []
    for index, soundscape in enumerate(script.soundscapes):
        resolutions.append(
            _resolve_cue(
                layout=layout,
                assets=assets,
                cue_kind="soundscape",
                cue_index=index,
                cue_label=soundscape.name,
                terms=[soundscape.asset_hint, soundscape.name, soundscape.description],
            )
        )
    used_bgm_asset_ids: set[str] = set()
    bgm_asset_by_label: dict[str, str] = {}
    for index, bgm in enumerate(script.bgm_suggestions):
        # Direct reuse_hint reference takes priority over text matching.
        if bgm.reuse_hint:
            hint_resolution = _resolve_bgm_by_reuse_hint(
                layout=layout,
                assets=assets,
                cue_index=index,
                cue_label=bgm.track_name or bgm.mood,
                reuse_hint=bgm.reuse_hint,
            )
            if hint_resolution is not None:
                resolutions.append(hint_resolution)
                if hint_resolution.status == "matched" and hint_resolution.asset_id:
                    used_bgm_asset_ids.add(hint_resolution.asset_id)
                continue
        cue_label = bgm.track_name or bgm.mood
        normalized_label = cue_label.strip().casefold()
        repeated_asset_id = bgm_asset_by_label.get(normalized_label, "")
        excluded_asset_ids = used_bgm_asset_ids - (
            {repeated_asset_id} if repeated_asset_id else set()
        )
        # Include mood_tags in search terms for semantic matching.
        bgm_terms = [bgm.track_name, bgm.mood, *bgm.mood_tags, bgm.narrative_role]
        resolution = _resolve_cue(
            layout=layout,
            assets=assets,
            cue_kind="bgm",
            cue_index=index,
            cue_label=cue_label,
            terms=bgm_terms,
            excluded_asset_ids=excluded_asset_ids,
        )
        resolutions.append(resolution)
        if resolution.status == "matched" and resolution.asset_id:
            used_bgm_asset_ids.add(resolution.asset_id)
            bgm_asset_by_label.setdefault(normalized_label, resolution.asset_id)
    for index, sfx in enumerate(script.sfx_cues):
        resolutions.append(
            _resolve_cue(
                layout=layout,
                assets=assets,
                cue_kind="sfx",
                cue_index=index,
                cue_label=sfx.effect_name,
                terms=[sfx.effect_name, sfx.description],
            )
        )
    report = ChapterSoundResolutionReport(
        chapter_number=script.chapter_number,
        resolutions=resolutions,
    )
    if fallback_cue_keys:
        degraded: list[SoundCueResolution] = []
        for item in report.resolutions:
            if (
                item.status == "missing"
                and (item.cue_kind, item.cue_index) in fallback_cue_keys
            ):
                alternative = _degrade_missing_cue(layout, assets, item)
                if alternative is not None:
                    degraded.append(alternative)
                    continue
            degraded.append(item)
        report = report.model_copy(update={"resolutions": degraded})
    return report


def _degrade_missing_cue(
    layout: ProjectLayout,
    assets: list[SoundAsset],
    resolution: SoundCueResolution,
) -> SoundCueResolution | None:
    """Fall back to an approved same-kind asset after generation failed.

    Used only for cues whose asset generation already failed (recorded in
    ``fallback_cue_keys``): deterministic matching found no label overlap, so
    we reuse any file-valid approved asset of the same kind and surface an
    explicit review hint instead of blocking the whole chapter.
    """
    candidates = [
        asset
        for asset in assets
        if asset.kind == resolution.cue_kind and asset.approval_status == "approved"
    ]
    ranked = sorted(
        candidates,
        key=lambda asset: _asset_score(asset, [resolution.cue_label]),
        reverse=True,
    )
    for asset in ranked:
        assets_root = (
            application_sound_assets_dir()
            if asset.scope == "application"
            else layout.tts_sound_assets_dir
        ).resolve()
        candidate = (assets_root / asset.relative_path).resolve()
        if (
            not candidate.is_relative_to(assets_root)
            or not candidate.is_file()
            or candidate.suffix.casefold() not in _SUPPORTED_AUDIO_SUFFIXES
        ):
            continue
        return SoundCueResolution(
            cue_kind=resolution.cue_kind,
            cue_index=resolution.cue_index,
            cue_label=resolution.cue_label,
            status="matched",
            asset_id=asset.asset_id,
            relative_path=asset.relative_path,
            asset_scope=asset.scope,
            reason="生成失败后自动降级复用库内同类资产（建议人工复核听感）",
        )
    return None


def resolved_asset_paths(
    layout: ProjectLayout,
    report: ChapterSoundResolutionReport,
) -> dict[tuple[str, int], Path]:
    """Return only safe, existing files keyed by cue kind and index."""
    result: dict[tuple[str, int], Path] = {}
    for item in report.resolutions:
        if item.status != "matched" or not item.relative_path:
            continue
        root = (
            application_sound_assets_dir()
            if item.asset_scope == "application"
            else layout.tts_sound_assets_dir
        ).resolve()
        candidate = (root / item.relative_path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            result[(item.cue_kind, item.cue_index)] = candidate
    return result


def commercial_rights_issues(
    layout: ProjectLayout,
    report: ChapterSoundResolutionReport,
    *,
    library: SoundLibraryManifest | None = None,
    application_library: SoundLibraryManifest | None = None,
) -> list[str]:
    """Return matched assets that are not explicitly cleared for commercial use.

    Content approval and usage rights are intentionally independent.  This
    check reads the current library authority so an author can clear an asset
    and reassemble without regenerating the script or sound-resolution report.
    """

    project_assets = (library or load_sound_library(layout)).assets
    shared_assets = (application_library or load_application_sound_library()).assets
    assets_by_key = {
        ("project", asset.asset_id): asset for asset in project_assets
    } | {
        ("application", asset.asset_id): asset for asset in shared_assets
    }
    issues: list[str] = []
    for resolution in report.resolutions:
        if resolution.status != "matched" or not resolution.asset_id:
            continue
        asset = assets_by_key.get((resolution.asset_scope, resolution.asset_id))
        if asset is None:
            issues.append(f"{resolution.asset_id}:review_required")
            continue
        if asset.commercial_use_status != "cleared":
            issues.append(f"{asset.asset_id}:{asset.commercial_use_status}")
    return list(dict.fromkeys(issues))


def _resolve_cue(
    *,
    layout: ProjectLayout,
    assets: list[SoundAsset],
    cue_kind: str,
    cue_index: int,
    cue_label: str,
    terms: list[str],
    excluded_asset_ids: set[str] | None = None,
) -> SoundCueResolution:
    normalized_terms = [term.strip().casefold() for term in terms if term and term.strip()]
    excluded = excluded_asset_ids or set()
    candidates = [
        asset for asset in assets if asset.kind == cue_kind and asset.asset_id not in excluded
    ]
    approved_candidates = [asset for asset in candidates if asset.approval_status == "approved"]
    ranked = sorted(
        (
            (_asset_score(asset, normalized_terms), asset.scope == "project", asset)
            for asset in approved_candidates
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    if not ranked or ranked[0][0] <= 0:
        pending = sorted(
            (
                (_asset_score(asset, normalized_terms), asset.scope == "project", asset)
                for asset in candidates
            ),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        if pending and pending[0][0] > 0 and pending[0][2].approval_status == "pending":
            return SoundCueResolution(
                cue_kind=cue_kind,
                cue_index=cue_index,
                cue_label=cue_label,
                status="missing",
                asset_id=pending[0][2].asset_id,
                relative_path=pending[0][2].relative_path,
                asset_scope=pending[0][2].scope,
                reason="找到生成资产，但仍待作者审核，未进入正式混音",
            )
        return SoundCueResolution(
            cue_kind=cue_kind,
            cue_index=cue_index,
            cue_label=cue_label,
            status="missing",
            reason="未在项目声音资产库中找到匹配标签或名称",
        )
    asset = ranked[0][2]
    assets_root = (
        application_sound_assets_dir()
        if asset.scope == "application"
        else layout.tts_sound_assets_dir
    ).resolve()
    candidate = (assets_root / asset.relative_path).resolve()
    if not candidate.is_relative_to(assets_root) or not candidate.is_file():
        return SoundCueResolution(
            cue_kind=cue_kind,
            cue_index=cue_index,
            cue_label=cue_label,
            status="invalid",
            asset_id=asset.asset_id,
            relative_path=asset.relative_path,
            asset_scope=asset.scope,
            reason="资产清单指向的文件不存在或不在项目声音资产目录内",
        )
    if candidate.suffix.casefold() not in _SUPPORTED_AUDIO_SUFFIXES:
        return SoundCueResolution(
            cue_kind=cue_kind,
            cue_index=cue_index,
            cue_label=cue_label,
            status="invalid",
            asset_id=asset.asset_id,
            relative_path=asset.relative_path,
            reason="资产格式不受混音器支持",
        )
    return SoundCueResolution(
        cue_kind=cue_kind,
        cue_index=cue_index,
        cue_label=cue_label,
        status="matched",
        asset_id=asset.asset_id,
        relative_path=asset.relative_path,
        asset_scope=asset.scope,
        reason=f"依据{'项目' if asset.scope == 'project' else '应用'}资产 ID、名称或标签匹配",
    )


def _resolve_bgm_by_reuse_hint(
    *,
    layout: ProjectLayout,
    assets: list[SoundAsset],
    cue_index: int,
    cue_label: str,
    reuse_hint: str,
) -> SoundCueResolution | None:
    """Resolve a BGM cue directly by its reuse_hint asset ID."""
    for asset in assets:
        if asset.asset_id != reuse_hint:
            continue
        if asset.approval_status != "approved":
            return SoundCueResolution(
                cue_kind="bgm",
                cue_index=cue_index,
                cue_label=cue_label,
                status="missing",
                asset_id=asset.asset_id,
                relative_path=asset.relative_path,
                asset_scope=asset.scope,
                reason="reuse_hint 指向的资产尚未审核通过",
            )
        assets_root = (
            application_sound_assets_dir()
            if asset.scope == "application"
            else layout.tts_sound_assets_dir
        ).resolve()
        candidate = (assets_root / asset.relative_path).resolve()
        if candidate.is_relative_to(assets_root) and candidate.is_file():
            return SoundCueResolution(
                cue_kind="bgm",
                cue_index=cue_index,
                cue_label=cue_label,
                status="matched",
                asset_id=asset.asset_id,
                relative_path=asset.relative_path,
                asset_scope=asset.scope,
                reason="通过 reuse_hint 直接引用已批准资产",
            )
        return SoundCueResolution(
            cue_kind="bgm",
            cue_index=cue_index,
            cue_label=cue_label,
            status="invalid",
            asset_id=asset.asset_id,
            relative_path=asset.relative_path,
            asset_scope=asset.scope,
            reason="reuse_hint 指向的资产文件不存在",
        )
    return None  # Hint not found; fall through to normal matching.


def _asset_score(asset: SoundAsset, terms: list[str]) -> int:
    haystack = " ".join([asset.asset_id, asset.display_name, *asset.tags]).casefold()
    return sum(20 if term == asset.asset_id.casefold() else 5 for term in terms if term in haystack)


__all__ = [
    "load_sound_library",
    "save_sound_library",
    "load_application_sound_library",
    "save_application_sound_library",
    "application_sound_assets_dir",
    "application_sound_library_path",
    "sound_asset_path",
    "set_sound_asset_approval",
    "resolve_sound_cues",
    "resolved_asset_paths",
    "commercial_rights_issues",
]

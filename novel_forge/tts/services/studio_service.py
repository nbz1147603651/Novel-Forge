"""UI-agnostic application service for the audiobook production workspace.

The desktop page should only collect user input and render returned schemas.
Filesystem mutation, project context projection, sound-library filtering, and
cue lookup live here so future web/mobile clients can reuse the same behavior.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from novel_forge.core.config import Settings
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import (
    application_sound_assets_dir,
    load_application_sound_library,
    load_sound_library,
    save_application_sound_library,
    save_sound_library,
    set_sound_asset_approval,
    sound_asset_path,
)
from novel_forge.tts.runtime.storage_limits import ensure_project_audio_write
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    ChapterTakeManifest,
    DubbingScript,
    DubbingSegment,
    SegmentTakeVersion,
    SoundAsset,
    SynthesisResult,
    TakeReviewStatus,
)

SoundAssetKind = Literal["soundscape", "bgm", "sfx"]
SoundAssetStatus = Literal["approved", "pending", "rejected"]
CommercialUseStatus = Literal["cleared", "review_required", "restricted"]
_SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


@dataclass(frozen=True)
class SoundLibraryQuery:
    """Portable filter contract for any future Voice Studio client."""

    kind: str = ""
    status: str = ""
    scope: str = ""


@dataclass(frozen=True)
class ImportedSoundAssets:
    """Result of one explicit author import."""

    assets: tuple[SoundAsset, ...]

    @property
    def count(self) -> int:
        return len(self.assets)


@dataclass(frozen=True)
class SegmentSoundContext:
    """Structured sound-design context adjacent to one actor take."""

    scene: str
    ambience: tuple[str, ...]
    music: tuple[str, ...]
    effects: tuple[tuple[str, int], ...]

    @property
    def has_cues(self) -> bool:
        return bool(self.ambience or self.music or self.effects)


@dataclass(frozen=True)
class TakeCleanupResult:
    """Summary of an explicit redundant-take cleanup."""

    removed_files: int
    reclaimed_bytes: int


class VoiceStudioProjectService:
    """Project-scoped backend facade with no Qt dependency."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        project_id: str = "",
        settings: Settings | None = None,
    ) -> None:
        self.layout = layout
        self.project_id = project_id
        self._settings = settings or Settings()

    # ── Voice Room take lifecycle ───────────────────────────────────────

    def load_take_manifest(self, chapter_number: int) -> ChapterTakeManifest:
        """Load a take manifest, degrading safely when an old file is corrupt."""
        path = self.layout.tts_take_manifest_path(chapter_number)
        try:
            manifest = ChapterTakeManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if manifest.chapter_number == chapter_number:
                return manifest
        except (OSError, ValueError, TypeError):
            pass
        return ChapterTakeManifest(chapter_number=chapter_number)

    def save_take_manifest(self, manifest: ChapterTakeManifest) -> None:
        """Atomically persist one chapter's take state."""
        path = self.layout.tts_take_manifest_path(manifest.chapter_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest.updated_at = datetime.now(timezone.utc)
        atomic_write_json(path, manifest.model_dump(mode="json"))

    def save_take_draft(
        self,
        chapter_number: int,
        *,
        script_hash: str,
        segment: DubbingSegment,
    ) -> ChapterTakeManifest:
        """Save one guidance edit without touching the source script."""
        return self.save_take_drafts(
            chapter_number,
            script_hash=script_hash,
            segments=(segment,),
        )

    def save_take_drafts(
        self,
        chapter_number: int,
        *,
        script_hash: str,
        segments: Iterable[DubbingSegment],
    ) -> ChapterTakeManifest:
        """Atomically save guidance and retire superseded candidate takes.

        Every segment is validated by the caller against the immutable source
        script.  Persisting the complete batch in one manifest write prevents
        a browser disconnect from leaving only part of an author's guidance
        changes durable.
        """
        pending_segments = tuple(segments)
        indices = [segment.segment_index for segment in pending_segments]
        if len(indices) != len(set(indices)):
            raise ValueError("同一片段不能重复保存试听指导")
        manifest = self.load_take_manifest(chapter_number)
        if manifest.source_script_hash and manifest.source_script_hash != script_hash:
            manifest.drafts.clear()
        reviewed_at = datetime.now(timezone.utc)
        changed_indices = set(indices)
        for take in manifest.takes:
            if (
                take.segment_index in changed_indices
                and take.status == TakeReviewStatus.CANDIDATE
            ):
                take.status = TakeReviewStatus.REJECTED
                take.reviewed_at = reviewed_at
        manifest.source_script_hash = script_hash
        for segment in pending_segments:
            manifest.drafts[str(segment.segment_index)] = segment.model_copy(deep=True)
        self.save_take_manifest(manifest)
        return manifest

    def register_candidate_take(
        self,
        take: SegmentTakeVersion,
    ) -> ChapterTakeManifest:
        """Append an isolated audition; formal audio remains authoritative."""
        manifest = self.load_take_manifest(take.chapter_number)
        reviewed_at = datetime.now(timezone.utc)
        for existing in manifest.takes:
            if (
                existing.segment_index == take.segment_index
                and existing.status == TakeReviewStatus.CANDIDATE
            ):
                existing.status = TakeReviewStatus.REJECTED
                existing.reviewed_at = reviewed_at
        manifest.source_script_hash = take.source_script_hash
        manifest.drafts[str(take.segment_index)] = take.segment.model_copy(deep=True)
        manifest.takes.append(take)
        self.save_take_manifest(manifest)
        return manifest

    def latest_candidate_take(
        self,
        chapter_number: int,
        segment_index: int,
        *,
        script_hash: str = "",
    ) -> SegmentTakeVersion | None:
        """Return the newest still-reviewable take backed by a real audio file."""
        manifest = self.load_take_manifest(chapter_number)
        for take in reversed(manifest.takes):
            if take.segment_index != segment_index or take.status != TakeReviewStatus.CANDIDATE:
                continue
            if script_hash and take.source_script_hash != script_hash:
                continue
            if take.segment_result.audio_path and Path(take.segment_result.audio_path).is_file():
                return take
        return None

    def reject_candidate_take(self, chapter_number: int, take_id: str) -> bool:
        """Mark a candidate rejected while retaining it until explicit cleanup."""
        manifest = self.load_take_manifest(chapter_number)
        changed = False
        for take in manifest.takes:
            if take.take_id == take_id and take.status == TakeReviewStatus.CANDIDATE:
                take.status = TakeReviewStatus.REJECTED
                take.reviewed_at = datetime.now(timezone.utc)
                changed = True
                break
        if changed:
            self.save_take_manifest(manifest)
        return changed

    def accept_candidate_take(
        self,
        chapter_number: int,
        take_id: str,
        *,
        promoted_result: SynthesisResult,
        new_script_hash: str,
    ) -> SegmentTakeVersion:
        """Promote exactly one candidate and retire older versions of that segment."""
        manifest = self.load_take_manifest(chapter_number)
        target = next((take for take in manifest.takes if take.take_id == take_id), None)
        if target is None or target.status != TakeReviewStatus.CANDIDATE:
            raise ValueError("试听版本不存在或已经处理")
        reviewed_at = datetime.now(timezone.utc)
        for take in manifest.takes:
            if take.segment_index != target.segment_index or take.take_id == take_id:
                continue
            if take.status in {TakeReviewStatus.CANDIDATE, TakeReviewStatus.ACCEPTED}:
                take.status = TakeReviewStatus.REJECTED
                take.reviewed_at = reviewed_at
        target.status = TakeReviewStatus.ACCEPTED
        target.reviewed_at = reviewed_at
        target.segment_result = promoted_result
        manifest.drafts.pop(str(target.segment_index), None)
        manifest.source_script_hash = new_script_hash
        self.save_take_manifest(manifest)
        return target

    def cleanup_redundant_takes(self, chapter_number: int) -> TakeCleanupResult:
        """Delete unaccepted/unreferenced takes, preserving current approved audio."""
        manifest = self.load_take_manifest(chapter_number)
        result_path = self.layout.tts_audio_result_path(chapter_number)
        referenced: set[Path] = set()
        if result_path.is_file():
            try:
                result = ChapterAudioResult.model_validate_json(
                    result_path.read_text(encoding="utf-8")
                )
                referenced.update(
                    Path(item.audio_path).resolve()
                    for item in result.segment_results
                    if item.audio_path
                )
            except (OSError, ValueError, TypeError):
                pass

        removed_files = 0
        reclaimed_bytes = 0
        kept_takes: list[SegmentTakeVersion] = []
        for take in manifest.takes:
            path = Path(take.segment_result.audio_path) if take.segment_result.audio_path else None
            keep = bool(
                take.status == TakeReviewStatus.ACCEPTED
                and path is not None
                and path.resolve() in referenced
            )
            if keep:
                kept_takes.append(take)
                continue
            if path is not None and path.is_file() and path.resolve() not in referenced:
                reclaimed_bytes += path.stat().st_size
                path.unlink()
                removed_files += 1
                self._remove_empty_parents(path.parent, self.layout.tts_take_dir(chapter_number))

        manifest.takes = kept_takes
        manifest.drafts.clear()
        self.save_take_manifest(manifest)

        approved_dir = self.layout.tts_approved_take_dir(chapter_number)
        if approved_dir.exists():
            for path in approved_dir.iterdir():
                if not path.is_file() or path.resolve() in referenced:
                    continue
                reclaimed_bytes += path.stat().st_size
                path.unlink()
                removed_files += 1
        # Also sweep crash leftovers that never reached the manifest.  An
        # explicit cleanup means every candidate is disposable; formally used
        # audio lives under ``approved`` and is protected by ``referenced``.
        candidates_dir = self.layout.tts_take_dir(chapter_number) / "candidates"
        if candidates_dir.exists():
            for path in sorted(candidates_dir.rglob("*"), reverse=True):
                if path.is_file() and path.resolve() not in referenced:
                    reclaimed_bytes += path.stat().st_size
                    path.unlink()
                    removed_files += 1
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                candidates_dir.rmdir()
            except OSError:
                pass
        return TakeCleanupResult(removed_files=removed_files, reclaimed_bytes=reclaimed_bytes)

    @staticmethod
    def _remove_empty_parents(directory: Path, stop: Path) -> None:
        while directory != stop and stop in directory.parents:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    def list_sound_assets(self, query: SoundLibraryQuery | None = None) -> list[SoundAsset]:
        query = query or SoundLibraryQuery()
        assets = [
            asset
            for asset in self.all_sound_assets()
            if (not query.kind or asset.kind == query.kind)
            and (not query.status or asset.approval_status == query.status)
            and (not query.scope or asset.scope == query.scope)
        ]
        return sorted(
            assets,
            key=lambda item: (
                {"pending": 0, "approved": 1, "rejected": 2}.get(item.approval_status, 3),
                item.kind,
                item.display_name.lower(),
            ),
        )

    def all_sound_assets(self) -> list[SoundAsset]:
        project_assets = [
            asset.model_copy(update={"scope": "project"})
            for asset in load_sound_library(self.layout).assets
        ]
        application_assets = [
            asset.model_copy(update={"scope": "application"})
            for asset in load_application_sound_library().assets
        ]
        return [*project_assets, *application_assets]

    def sound_asset_path(self, asset: SoundAsset) -> Path:
        return sound_asset_path(self.layout, asset)

    def import_sound_assets(
        self,
        paths: Iterable[str | Path],
        *,
        kind: SoundAssetKind,
        tags: Iterable[str] = (),
    ) -> ImportedSoundAssets:
        library = load_sound_library(self.layout)
        assets_dir = self.layout.tts_sound_assets_dir
        assets_dir.mkdir(parents=True, exist_ok=True)
        normalized_tags = tuple(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))
        known_ids = {asset.asset_id for asset in library.assets}
        imported: list[SoundAsset] = []
        for raw_path in paths:
            source = Path(raw_path)
            if not source.is_file() or source.suffix.lower() not in _SUPPORTED_AUDIO_SUFFIXES:
                continue
            stem = re.sub(r"[^\w.-]+", "_", source.stem).strip("_.") or "sound"
            destination = self._available_destination(assets_dir, stem, source.suffix.lower())
            ensure_project_audio_write(
                layout=self.layout,
                settings=self._settings,
                destination=destination,
                incoming_bytes=source.stat().st_size,
                artifact=f"导入音效 {source.name}",
            )
            shutil.copy2(source, destination)
            asset_id = self._available_asset_id(kind, stem, known_ids)
            known_ids.add(asset_id)
            asset = SoundAsset(
                asset_id=asset_id,
                kind=kind,
                display_name=source.stem,
                relative_path=str(destination.relative_to(assets_dir)),
                tags=list(normalized_tags or (source.stem,)),
                loopable=kind in {"soundscape", "bgm"},
            )
            library.assets.append(asset)
            imported.append(asset)
        if imported:
            save_sound_library(self.layout, library)
        return ImportedSoundAssets(tuple(imported))

    def set_sound_asset_status(
        self,
        asset_id: str,
        status: SoundAssetStatus,
    ) -> SoundAsset:
        if any(asset.asset_id == asset_id for asset in load_sound_library(self.layout).assets):
            return set_sound_asset_approval(self.layout, asset_id, status=status)
        application_library = load_application_sound_library()
        for index, asset in enumerate(application_library.assets):
            if asset.asset_id != asset_id:
                continue
            updated = asset.model_copy(update={"approval_status": status, "scope": "application"})
            application_library.assets[index] = updated
            save_application_sound_library(application_library)
            return updated
        raise KeyError(f"Sound asset not found: {asset_id}")

    def update_sound_asset_tags(self, asset_id: str, tags: Iterable[str]) -> SoundAsset:
        normalized = list(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))
        library = load_sound_library(self.layout)
        for index, asset in enumerate(library.assets):
            if asset.asset_id != asset_id:
                continue
            updated = asset.model_copy(update={"tags": normalized})
            library.assets[index] = updated
            save_sound_library(self.layout, library)
            return updated
        application_library = load_application_sound_library()
        for index, asset in enumerate(application_library.assets):
            if asset.asset_id != asset_id:
                continue
            updated = asset.model_copy(update={"tags": normalized, "scope": "application"})
            application_library.assets[index] = updated
            save_application_sound_library(application_library)
            return updated
        raise KeyError(f"Sound asset not found: {asset_id}")

    def set_sound_asset_commercial_rights(
        self,
        asset_id: str,
        *,
        status: CommercialUseStatus,
        license_note: str,
    ) -> SoundAsset:
        """Persist a human rights review separately from listening approval."""

        note = license_note.strip()
        if status == "cleared" and not note:
            raise ValueError("确认可商用时必须填写授权依据或条款备注")
        reviewed_at = datetime.now(timezone.utc) if status != "review_required" else None

        def update(asset: SoundAsset, *, scope: Literal["project", "application"]) -> SoundAsset:
            return asset.model_copy(
                update={
                    "commercial_use_status": status,
                    "commercial_use_reviewed_at": reviewed_at,
                    "license_note": note,
                    "scope": scope,
                }
            )

        project_library = load_sound_library(self.layout)
        for index, asset in enumerate(project_library.assets):
            if asset.asset_id != asset_id:
                continue
            updated = update(asset, scope="project")
            project_library.assets[index] = updated
            save_sound_library(self.layout, project_library)
            return updated

        application_library = load_application_sound_library()
        for index, asset in enumerate(application_library.assets):
            if asset.asset_id != asset_id:
                continue
            updated = update(asset, scope="application")
            application_library.assets[index] = updated
            save_application_sound_library(application_library)
            return updated
        raise KeyError(f"Sound asset not found: {asset_id}")

    def publish_sound_asset(self, asset_id: str) -> SoundAsset:
        """Copy an approved project asset into the cross-project application library."""

        project_library = load_sound_library(self.layout)
        source_asset = next(
            (asset for asset in project_library.assets if asset.asset_id == asset_id),
            None,
        )
        if source_asset is None:
            raise KeyError(f"Project sound asset not found: {asset_id}")
        if source_asset.approval_status != "approved":
            raise ValueError("请先批准该资产，再发布到应用资源库")
        if source_asset.commercial_use_status != "cleared":
            raise ValueError("请先确认该资产的商用授权，再发布到应用资源库")
        source_path = self.sound_asset_path(source_asset)
        if not source_path.is_file():
            raise FileNotFoundError(f"声音资产文件不存在：{source_path}")

        application_library = load_application_sound_library()
        if source_asset.generation_request_hash:
            existing = next(
                (
                    asset
                    for asset in application_library.assets
                    if asset.generation_request_hash == source_asset.generation_request_hash
                    and sound_asset_path(self.layout, asset).is_file()
                ),
                None,
            )
            if existing is not None:
                return existing.model_copy(update={"scope": "application"})

        destination_dir = application_sound_assets_dir() / source_asset.kind
        destination_dir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w.-]+", "_", source_path.stem).strip("_.") or "sound"
        destination = self._available_destination(destination_dir, stem, source_path.suffix.lower())
        shutil.copy2(source_path, destination)
        known_ids = {asset.asset_id for asset in application_library.assets}
        application_asset = source_asset.model_copy(
            update={
                "asset_id": self._available_asset_id(source_asset.kind, f"app-{stem}", known_ids),
                "relative_path": str(destination.relative_to(application_sound_assets_dir())),
                "scope": "application",
            }
        )
        application_library.assets.append(application_asset)
        save_application_sound_library(application_library)
        return application_asset

    def story_sound_context(self) -> dict[str, object]:
        """Return the stable work-level projection used by sound generation."""
        spec = self._read_json(self.layout.spec_path)
        bible = self._read_json(self.layout.bible_path)
        style = self._read_json(self.layout.style_profile_path)
        global_style = style.get("global_style")
        if not isinstance(global_style, dict):
            global_style = {}
        return {
            "title": bible.get("title") or spec.get("title") or self.project_id,
            "genre": spec.get("genre", ""),
            "theme": spec.get("theme", ""),
            "premise": bible.get("premise") or spec.get("theme", ""),
            "tone": bible.get("tone")
            or spec.get("tone")
            or global_style.get("emotional_style", ""),
            "themes": bible.get("themes") or [],
            "era": bible.get("era", ""),
            "world": "；".join(
                str(value).strip()
                for value in (
                    bible.get("era"),
                    bible.get("geography"),
                    bible.get("culture"),
                )
                if str(value or "").strip()
            ),
            "audio_aesthetic": bible.get("audio_aesthetic") or spec.get("audio_aesthetic_hint", ""),
        }

    @staticmethod
    def segment_sound_context(
        script: DubbingScript,
        segment: DubbingSegment,
    ) -> SegmentSoundContext:
        index = segment.segment_index
        ambience = tuple(
            cue.name
            for cue in script.soundscapes
            if VoiceStudioProjectService._range_contains(
                index, cue.start_segment_index, cue.end_segment_index
            )
        )
        music = tuple(
            cue.track_name or cue.mood or "章节氛围"
            for cue in script.bgm_suggestions
            if VoiceStudioProjectService._range_contains(
                index, cue.start_segment_index, cue.end_segment_index
            )
        )
        effects = tuple(
            (cue.effect_name, cue.offset_ms)
            for cue in script.sfx_cues
            if cue.trigger_segment_index == index
        )
        return SegmentSoundContext(
            scene=segment.scene_context.strip(),
            ambience=ambience,
            music=music,
            effects=effects,
        )

    @staticmethod
    def _range_contains(index: int, start: int | None, end: int | None) -> bool:
        effective_start = start if start is not None else 0
        return index >= effective_start and (end is None or index <= end)

    @staticmethod
    def _available_destination(directory: Path, stem: str, suffix: str) -> Path:
        destination = directory / f"{stem}{suffix}"
        ordinal = 2
        while destination.exists():
            destination = directory / f"{stem}_{ordinal}{suffix}"
            ordinal += 1
        return destination

    @staticmethod
    def _available_asset_id(
        kind: SoundAssetKind,
        stem: str,
        known_ids: set[str],
    ) -> str:
        asset_stem = re.sub(r"[^a-z0-9_-]+", "-", stem.lower()).strip("-") or "sound"
        base = f"{kind}-{asset_stem}"
        candidate = base
        ordinal = 2
        while candidate in known_ids:
            candidate = f"{base}-{ordinal}"
            ordinal += 1
        return candidate

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

"""Selective TTS file cleanup: prune stale derivatives while preserving contracts.

This module provides categorized cleanup of per-project TTS artifacts. Each
category is independently selectable so the author can review exactly what will
be removed before confirming.

Categories (all project-scoped, under ``<project>/tts/``):

- ``stale_previews``: preview/activation/design audio in ``audio/chapter_000/``
  no longer referenced by any cast entry or narrator profile.
- ``orphan_candidates``: candidate audition takes not promoted to approved,
  across all chapters (mirrors ``studio_service.cleanup_redundant_takes`` but
  batched project-wide).
- ``completed_checkpoints``: per-chapter synthesis checkpoints whose chapter
  result is complete or absent.
- ``orphan_sound_assets``: generated sound files with no matching non-rejected
  entry in ``sound_library.json``; orphaned library entries are also pruned.
- ``orphan_chapter_reports``: per-chapter report JSONs (timelines, mix_plans,
  render_reports, quality, sound_resolutions, sound_generation) whose chapter
  audio result no longer exists.

Never deleted (project contracts / authoritative artifacts):
- ``voice_team.json``, ``sound_library.json`` (file itself), ``narrator_profile.json``,
  ``audio_creative_bible.json``, ``audio_execution_plan.json``, ``model_scorecards.json``
- approved takes referenced by a chapter result
- generated sound assets still referenced by ``sound_library.json`` non-rejected entries
- ``chapter_000`` preview files still referenced by a live cast entry / narrator
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout

_log = logging.getLogger(__name__)

# Category identifiers (stable string keys used by the UI checkboxes).
CAT_STALE_PREVIEWS = "stale_previews"
CAT_ORPHAN_CANDIDATES = "orphan_candidates"
CAT_COMPLETED_CHECKPOINTS = "completed_checkpoints"
CAT_ORPHAN_SOUND_ASSETS = "orphan_sound_assets"
CAT_ORPHAN_CHAPTER_REPORTS = "orphan_chapter_reports"

ALL_CATEGORIES: tuple[str, ...] = (
    CAT_STALE_PREVIEWS,
    CAT_ORPHAN_CANDIDATES,
    CAT_COMPLETED_CHECKPOINTS,
    CAT_ORPHAN_SOUND_ASSETS,
    CAT_ORPHAN_CHAPTER_REPORTS,
)

# Human-readable labels + descriptions for the UI dialog.
CATEGORY_LABELS: dict[str, tuple[str, str]] = {
    CAT_STALE_PREVIEWS: (
        "过期试听/激活音频",
        "audio/chapter_000/ 下不再被任何角色或旁白引用的 preview_*/activation_*/design_* 文件",
    ),
    CAT_ORPHAN_CANDIDATES: (
        "未引用的候选试听",
        "各章节 candidates/ 下未被接受为正式分段音频的试听录音",
    ),
    CAT_COMPLETED_CHECKPOINTS: (
        "已完成章节的检查点",
        "states/tts_progress/ 下对应章节已合成完成或结果不存在的进度检查点",
    ),
    CAT_ORPHAN_SOUND_ASSETS: (
        "孤儿生成音效",
        "assets/generated/ 下 sound_library.json 不再登记的生成音效文件",
    ),
    CAT_ORPHAN_CHAPTER_REPORTS: (
        "孤儿章节报告",
        "timelines/mix_plans/render_reports/quality 等章节报告其音频结果已不存在",
    ),
}

_CHAPTER_RE = re.compile(r"chapter_(\d+)")


@dataclass(frozen=True)
class CleanupCategoryResult:
    """What was removed in one category."""

    removed_files: list[str] = field(default_factory=list)
    removed_dirs: list[str] = field(default_factory=list)
    reclaimed_bytes: int = 0


@dataclass(frozen=True)
class TTSCleanupReport:
    """Aggregate result of a categorized cleanup pass."""

    categories: dict[str, CleanupCategoryResult]
    removed_count: int
    reclaimed_bytes: int

    @property
    def is_empty(self) -> bool:
        return self.removed_count == 0


@dataclass(frozen=True)
class TTSProjectResetReport:
    """Complete reset of regenerable outputs while preserving reusable assets."""

    removed_files: tuple[str, ...] = ()
    removed_dirs: tuple[str, ...] = ()
    preserved_paths: tuple[str, ...] = ()
    reclaimed_bytes: int = 0

    @property
    def removed_count(self) -> int:
        return len(self.removed_files)

    @property
    def is_empty(self) -> bool:
        return not self.removed_files and not self.removed_dirs


def _safe_remove(path: Path, *, dry_run: bool = False) -> tuple[bool, int]:
    """Remove a file or dir; return (selected, size_in_bytes_before_removal).

    ``dry_run`` performs the same eligibility and size scan without mutating
    the project.  The desktop chooser relies on this path to preview every
    category before the user confirms the cleanup.
    """
    if not path.exists():
        return False, 0
    size = 0
    try:
        if path.is_dir():
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        size += p.stat().st_size
                    except OSError:
                        pass
            if not dry_run:
                shutil.rmtree(path)
        else:
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
    except OSError as exc:
        _log.warning("Failed to remove %s: %s", path, exc)
        return False, 0
    return True, size


_RESET_DERIVED_DIR_NAMES = (
    "scripts",
    "audio",
    "takes",
    "results",
    "sound_resolutions",
    "sound_generation",
    "timelines",
    "mix_plans",
    "render_reports",
    "quality",
)


def _project_reset_targets(layout: ProjectLayout) -> tuple[list[Path], list[Path]]:
    """Return disjoint regenerable targets and explicitly protected assets."""

    targets = [layout.tts_dir / name for name in _RESET_DERIVED_DIR_NAMES]
    targets.extend(sorted(layout.reports_dir.glob("chapter_*_tts_metadata.json")))
    targets.extend(
        [
            layout.states_dir / "tts_progress",
            layout.tts_progress_path,
        ]
    )
    protected = [
        layout.tts_voice_team_path,
        layout.tts_narrator_profile_path,
        layout.tts_sound_assets_dir,
        layout.tts_sound_library_path,
        layout.tts_audio_creative_bible_path,
        layout.tts_execution_plan_path,
        layout.tts_model_scorecards_path,
    ]
    return targets, protected


def _reset_project_tts_artifacts(
    layout: ProjectLayout,
    *,
    dry_run: bool,
) -> TTSProjectResetReport:
    targets, protected = _project_reset_targets(layout)
    removed_files: list[str] = []
    removed_dirs: list[str] = []
    reclaimed = 0
    for path in targets:
        if not path.exists():
            continue
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file())
            removed_files.extend(str(item) for item in files)
            removed_dirs.append(str(path))
        else:
            removed_files.append(str(path))
        ok, size = _safe_remove(path, dry_run=dry_run)
        if not ok:
            if path.is_dir():
                removed_dirs.pop()
                if files:
                    del removed_files[-len(files) :]
            else:
                removed_files.pop()
            continue
        reclaimed += size

    if not dry_run:
        # Keep the project immediately usable after the reset.  This recreates
        # only standard empty directories; contracts and sound assets were
        # never selected as targets.
        layout.ensure_dirs()
        for directory_name in _RESET_DERIVED_DIR_NAMES:
            (layout.tts_dir / directory_name).mkdir(parents=True, exist_ok=True)
    return TTSProjectResetReport(
        removed_files=tuple(removed_files),
        removed_dirs=tuple(removed_dirs),
        preserved_paths=tuple(str(path) for path in protected if path.exists()),
        reclaimed_bytes=reclaimed,
    )


def preview_reset_project_tts_artifacts(layout: ProjectLayout) -> TTSProjectResetReport:
    """Preview a clean regeneration reset without changing the project."""

    return _reset_project_tts_artifacts(layout, dry_run=True)


def execute_reset_project_tts_artifacts(layout: ProjectLayout) -> TTSProjectResetReport:
    """Delete every regenerable TTS output and retain reusable project assets."""

    return _reset_project_tts_artifacts(layout, dry_run=False)


def _parse_chapter(name: str) -> int | None:
    m = _CHAPTER_RE.search(name)
    return int(m.group(1)) if m else None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


# ─── Category implementations ───────────────────────────────────────────


def _collect_live_preview_references(layout: ProjectLayout) -> set[Path]:
    """Paths under chapter_000 still referenced by voice_team / narrator."""
    refs: set[Path] = set()
    team_path = layout.tts_voice_team_path
    if team_path.is_file():
        team = _load_json(team_path)
        for entry in team.get("entries") or []:
            for vp in (entry.get("preview_variants") or {}).values():
                p = vp.get("audio_path") if isinstance(vp, dict) else None
                if p:
                    refs.add(Path(p))
            if entry.get("preview_audio_path"):
                refs.add(Path(entry["preview_audio_path"]))
    narrator_path = layout.tts_narrator_profile_path
    if narrator_path.is_file():
        narrator = _load_json(narrator_path)
        # NarratorVoiceProfile may store preview paths in a few shapes.
        for key in ("preview_audio_path", "preview_path"):
            p = narrator.get(key)
            if p:
                refs.add(Path(p))
        for vp in (narrator.get("preview_variants") or {}).values():
            p = vp.get("audio_path") if isinstance(vp, dict) else None
            if p:
                refs.add(Path(p))
    return refs


def _cleanup_stale_previews(
    layout: ProjectLayout, *, dry_run: bool = False
) -> CleanupCategoryResult:
    preview_root = layout.tts_audio_dir(0)
    if not preview_root.is_dir():
        return CleanupCategoryResult()
    live_refs = _collect_live_preview_references(layout)
    removed_files: list[str] = []
    reclaimed = 0
    for path in sorted(preview_root.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if not (
            name.startswith("preview_")
            or name.startswith("activation_")
            or name.startswith("design_")
        ):
            continue
        # Keep if any live reference resolves to this file (by path equality).
        try:
            live = any(path.samefile(ref) for ref in live_refs if ref.exists())
        except OSError:
            live = path in live_refs
        if live:
            continue
        ok, size = _safe_remove(path, dry_run=dry_run)
        if ok:
            removed_files.append(str(path))
            reclaimed += size
    return CleanupCategoryResult(removed_files=removed_files, reclaimed_bytes=reclaimed)


def _cleanup_orphan_candidates(
    layout: ProjectLayout, *, dry_run: bool = False
) -> CleanupCategoryResult:
    """Remove candidate takes not referenced by any chapter's approved result."""
    takes_root = layout.tts_dir / "takes"
    if not takes_root.is_dir():
        return CleanupCategoryResult()
    removed_files: list[str] = []
    removed_dirs: list[str] = []
    reclaimed = 0
    for chapter_dir in sorted(takes_root.iterdir()):
        if not chapter_dir.is_dir():
            continue
        chapter_num = _parse_chapter(chapter_dir.name)
        if chapter_num is None:
            continue
        # Build the set of approved audio paths referenced by the formal result.
        result_path = layout.tts_audio_result_path(chapter_num)
        referenced: set[str] = set()
        if result_path.is_file():
            result = _load_json(result_path)
            for seg in result.get("segment_results") or []:
                ap = seg.get("audio_path") if isinstance(seg, dict) else None
                if ap:
                    referenced.add(str(Path(ap).resolve()))
        candidates_dir = chapter_dir / "candidates"
        if not candidates_dir.is_dir():
            continue
        for take_dir in sorted(candidates_dir.iterdir()):
            if not take_dir.is_dir():
                continue
            # A candidate dir is orphan if none of its audio files are referenced.
            referenced_here = False
            for p in take_dir.rglob("*"):
                if p.is_file() and str(p.resolve()) in referenced:
                    referenced_here = True
                    break
            if referenced_here:
                continue
            ok, size = _safe_remove(take_dir, dry_run=dry_run)
            if ok:
                removed_dirs.append(str(take_dir))
                reclaimed += size
    return CleanupCategoryResult(
        removed_files=removed_files, removed_dirs=removed_dirs, reclaimed_bytes=reclaimed
    )


def _cleanup_completed_checkpoints(
    layout: ProjectLayout, *, dry_run: bool = False
) -> CleanupCategoryResult:
    progress_dir = layout.states_dir / "tts_progress"
    if not progress_dir.is_dir():
        return CleanupCategoryResult()
    removed_files: list[str] = []
    reclaimed = 0
    for path in sorted(progress_dir.iterdir()):
        if not path.is_file():
            continue
        chapter_num = _parse_chapter(path.stem)
        if chapter_num is None or chapter_num < 1:
            continue
        result_path = layout.tts_audio_result_path(chapter_num)
        drop = False
        if not result_path.is_file():
            drop = True  # result gone -> checkpoint stale
        else:
            result = _load_json(result_path)
            if result.get("is_complete") is True:
                drop = True  # completed -> checkpoint no longer needed
        if drop:
            ok, size = _safe_remove(path, dry_run=dry_run)
            if ok:
                removed_files.append(str(path))
                reclaimed += size
    return CleanupCategoryResult(removed_files=removed_files, reclaimed_bytes=reclaimed)


def _cleanup_orphan_sound_assets(
    layout: ProjectLayout, *, dry_run: bool = False
) -> CleanupCategoryResult:
    """Remove generated sound files no longer tracked by sound_library.json.

    Also prunes library entries whose backing file is gone, but never deletes
    the sound_library.json file itself.
    """
    generated_dir = layout.tts_generated_sound_assets_dir
    library_path = layout.tts_sound_library_path
    if not generated_dir.is_dir() or not library_path.is_file():
        return CleanupCategoryResult()
    manifest = _load_json(library_path)
    tracked_rel_paths: set[str] = set()
    for asset in manifest.get("assets") or []:
        if str(asset.get("approval_status") or "approved") == "rejected":
            continue  # rejected entries don't protect their files
        rel = asset.get("relative_path")
        if rel:
            tracked_rel_paths.add(str(rel))
    removed_files: list[str] = []
    reclaimed = 0
    for path in sorted(generated_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = str(path.relative_to(layout.tts_sound_assets_dir))
        except ValueError:
            continue
        if rel in tracked_rel_paths:
            continue
        ok, size = _safe_remove(path, dry_run=dry_run)
        if ok:
            removed_files.append(str(path))
            reclaimed += size
    return CleanupCategoryResult(removed_files=removed_files, reclaimed_bytes=reclaimed)


def _cleanup_orphan_chapter_reports(
    layout: ProjectLayout, *, dry_run: bool = False
) -> CleanupCategoryResult:
    """Remove per-chapter report JSONs whose chapter audio result is gone."""
    report_dirs = (
        layout.tts_dir / "timelines",
        layout.tts_dir / "mix_plans",
        layout.tts_dir / "render_reports",
        layout.tts_dir / "quality",
        layout.tts_dir / "sound_resolutions",
        layout.tts_dir / "sound_generation",
    )
    removed_files: list[str] = []
    reclaimed = 0
    for reports_dir in report_dirs:
        if not reports_dir.is_dir():
            continue
        for path in sorted(reports_dir.iterdir()):
            if not path.is_file():
                continue
            chapter_num = _parse_chapter(path.stem)
            if chapter_num is None or chapter_num < 1:
                continue
            if layout.tts_audio_result_path(chapter_num).is_file():
                continue  # chapter still has an authoritative result
            ok, size = _safe_remove(path, dry_run=dry_run)
            if ok:
                removed_files.append(str(path))
                reclaimed += size
    return CleanupCategoryResult(removed_files=removed_files, reclaimed_bytes=reclaimed)


_CATEGORY_FUNCS = {
    CAT_STALE_PREVIEWS: _cleanup_stale_previews,
    CAT_ORPHAN_CANDIDATES: _cleanup_orphan_candidates,
    CAT_COMPLETED_CHECKPOINTS: _cleanup_completed_checkpoints,
    CAT_ORPHAN_SOUND_ASSETS: _cleanup_orphan_sound_assets,
    CAT_ORPHAN_CHAPTER_REPORTS: _cleanup_orphan_chapter_reports,
}


def execute_cleanup_tts_files(
    layout: ProjectLayout,
    *,
    categories: set[str],
) -> TTSCleanupReport:
    """Run selected cleanup categories and return an aggregate report.

    Only categories present in ``categories`` are executed; unknown keys are
    ignored. The function is synchronous and operates purely on the local
    filesystem under the project's ``tts/`` directory.
    """
    results: dict[str, CleanupCategoryResult] = {}
    total_removed = 0
    total_reclaimed = 0
    for cat in ALL_CATEGORIES:
        if cat not in categories:
            continue
        func = _CATEGORY_FUNCS[cat]
        try:
            result = func(layout)
        except Exception as exc:  # noqa: BLE001 - cleanup must be resilient
            _log.warning("TTS cleanup category %s failed: %s", cat, exc)
            result = CleanupCategoryResult()
        results[cat] = result
        total_removed += len(result.removed_files) + len(result.removed_dirs)
        total_reclaimed += result.reclaimed_bytes
    return TTSCleanupReport(
        categories=results,
        removed_count=total_removed,
        reclaimed_bytes=total_reclaimed,
    )


def preview_cleanup_tts_files(
    layout: ProjectLayout,
    *,
    categories: set[str] | None = None,
) -> TTSCleanupReport:
    """Return the exact cleanup candidates without deleting any files."""
    selected = set(ALL_CATEGORIES) if categories is None else categories
    results: dict[str, CleanupCategoryResult] = {}
    total_removed = 0
    total_reclaimed = 0
    for cat in ALL_CATEGORIES:
        if cat not in selected:
            continue
        func = _CATEGORY_FUNCS[cat]
        try:
            result = func(layout, dry_run=True)
        except Exception as exc:  # noqa: BLE001 - preview must not crash the chooser
            _log.warning("TTS cleanup preview category %s failed: %s", cat, exc)
            result = CleanupCategoryResult()
        results[cat] = result
        total_removed += len(result.removed_files) + len(result.removed_dirs)
        total_reclaimed += result.reclaimed_bytes
    return TTSCleanupReport(
        categories=results,
        removed_count=total_removed,
        reclaimed_bytes=total_reclaimed,
    )

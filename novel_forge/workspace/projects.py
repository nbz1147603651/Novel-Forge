"""Project inspection and summary services for API and desktop clients."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from novel_forge.core.constants import PipelineConstants
from novel_forge.core.project_state import ALLOWED_OPERATIONS, ProjectState, ProjectStateMachine
from novel_forge.core.review.review_orchestration import coerce_score, format_review_score_line
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    is_checkpoint_session_fresh_for_current_chain,
    scoped_stale_chapters,
)
from novel_forge.pipeline.progress import display_step_name
from novel_forge.workspace.contracts import (
    ChapterArtifactPreview,
    ChapterWorkspaceChapter,
    ChapterWorkspaceSnapshot,
    DecisionCheckpoint,
)

ProjectMode = Literal["short", "long"]
ProjectLoadStatus = Literal["ok", "degraded"]
ProjectStateSource = Literal["state_file", "inferred"]
_log = get_logger("workspace.projects")

_PROJECT_STATE_LABELS = {
    ProjectState.CREATED: "未开始",
    ProjectState.INITIALIZING: "初始化中",
    ProjectState.OUTLINE_READY: "待创作",
    ProjectState.WRITING: "创作中",
    ProjectState.COMPLETED: "已完成",
    ProjectState.PAUSED: "已暂停",
    ProjectState.ARCHIVED: "已归档",
    ProjectState.INIT_FAILED: "初始化失败",
}

_NON_PROJECT_DIR_NAMES = {"dlq"}
_GENERATED_TEST_PROJECT_NAMES = {
    "project_a",
    "project_b",
    "regression_test",
    "test",
    "test-project",
    "test_project",
}


@dataclass(frozen=True)
class InitReadinessResumeStatus:
    reached: bool = False
    allowed: bool = False
    summary: str = ""
    step: str = ""
    progress_percent: int = 0
_GENERATED_TEST_PROJECT_PREFIXES = ("pytest-", "pytest_", "smoke-", "smoke_", "test-", "test_")
_AUTO_GENERATED_PROJECT_ID_RE = re.compile(r"^(?:short|long)_[0-9a-f]{8}$", re.IGNORECASE)


@dataclass(frozen=True)
class TestProjectCleanupCandidate:
    """One safe-to-review project directory left by tests or an empty auto-run."""

    project_id: str
    reason: Literal["generated_test_name", "empty_generated_run"]
    file_count: int
    size_bytes: int


@dataclass(frozen=True)
class TestProjectCleanupResult:
    """Outcome of a confirmed test-project cleanup request."""

    removed_project_ids: tuple[str, ...] = ()
    skipped_project_ids: tuple[str, ...] = ()


def _open_issue_count(issues: Any) -> int:
    if not isinstance(issues, list):
        return 0
    count = 0
    for issue in issues:
        if isinstance(issue, dict):
            status = issue.get("status", "open")
        else:
            status = getattr(issue, "status", "open")
        if str(status or "open").strip().lower() == "open":
            count += 1
    return count


class ProjectIndex(BaseModel):
    """Lightweight project counters for fast snapshot hashing.

    Computed without per-chapter I/O (no rglob, no chapter file reads).
    Used by the desktop snapshot payload to detect changes without
    serialising the full ``ProjectDetail`` (chapters, recent_files, etc.).
    """

    project_id: str
    name: str = ""
    mode: str = "long"
    chapter_count: int = 0
    completed_chapters: int = 0
    total_words: int = 0
    updated_at: str | None = None
    is_degraded: bool = False


class ChapterSummary(BaseModel):
    chapter_number: int
    title: str = ""
    word_count: int = 0
    overall_score: float | None = None
    continuity_score: float | None = None
    updated_at: str | None = None
    preview: str = ""


class ProjectSummary(BaseModel):
    project_id: str
    mode: ProjectMode
    title: str
    genre: str = ""
    tone: str = ""
    premise: str = ""
    preview: str = ""
    total_chapters: int | None = None
    completed_chapters: int = 0
    latest_chapter: int | None = None
    completion_ratio: float = 0.0
    has_outline: bool = False
    has_canon: bool = False
    updated_at: str | None = None
    load_status: ProjectLoadStatus = "ok"
    load_error: str = ""
    project_state: str = ""
    project_state_label: str = ""
    project_state_updated_at: str | None = None
    project_state_source: ProjectStateSource = "inferred"
    allowed_operations: list[str] = Field(default_factory=list)
    # Number of outline chapters that have been generated so far (grows
    # incrementally during init_long). Used by the UI fingerprint so the
    # 卷帙 viewer refreshes as new batches are written to outline.json.
    outline_generated_count: int = 0


class ProjectDetail(ProjectSummary):
    language: str = ""
    words_per_chapter: int = 0
    volume_mode: str = "auto"
    chapters_per_volume: int = 0
    characters_hint: str = ""
    world_hint: str = ""
    conflict_hint: str = ""
    pov_hint: str = ""
    opening_style: str = ""
    ending_style: str = ""
    extra_instructions: str = ""
    init_resume_available: bool = False
    init_resume_step: str = ""
    init_resume_step_label: str = ""
    init_resume_progress_label: str = ""
    init_resume_progress_percent: int = 0
    init_resume_next_chapter: int | None = None
    chapters: list[ChapterSummary] = Field(default_factory=list)
    recent_files: list[str] = Field(default_factory=list)
    artifact_counts: dict[str, int] = Field(default_factory=dict)
    index: ProjectIndex = Field(default_factory=lambda: ProjectIndex(project_id=""))

    def model_post_init(self, __context: Any) -> None:
        """Sync ``index`` with parent fields when left at default."""
        super().model_post_init(__context)
        if self.index.project_id == "" and self.project_id:
            object.__setattr__(
                self,
                "index",
                ProjectIndex(
                    project_id=self.project_id,
                    name=self.title or self.project_id,
                    mode=self.mode,
                    chapter_count=len(self.chapters) or self.completed_chapters,
                    completed_chapters=self.completed_chapters,
                    total_words=sum(ch.word_count for ch in self.chapters),
                    updated_at=self.updated_at,
                    is_degraded=self.load_status == "degraded",
                ),
            )


class WorkspaceOverview(BaseModel):
    storage_root: str
    total_projects: int
    short_projects: int
    long_projects: int
    total_generated_chapters: int
    providers: list[str]
    default_provider: str


def _to_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _truncate(text: str, limit: int = 220) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _refresh_guard_checkpoint_summary(
    checkpoint: DecisionCheckpoint | None,
    *,
    current_text: str,
    alignment_score: float | None,
    continuity_score: float | None,
    overall_score: float | None,
    causal_score: float | None,
) -> DecisionCheckpoint | None:
    if checkpoint is None or checkpoint.checkpoint_type != "guard_checkpoint":
        return checkpoint

    score_line = format_review_score_line(
        alignment_score=alignment_score,
        continuity_score=continuity_score,
        overall_score=overall_score,
        causal_score=causal_score,
    )
    if not score_line:
        return checkpoint

    lines = [
        f"章节草稿已完成，当前 {display_word_count(current_text):,} 字。",
        f"{score_line}。",
    ]
    old_lines = [line.strip() for line in checkpoint.summary.splitlines() if line.strip()]
    decision_line = next((line for line in old_lines if "AI 判定" in line), "")
    if decision_line:
        lines.append(decision_line)
    return checkpoint.model_copy(update={"summary": "\n".join(lines)})


def _load_json(storage: FileSystemStorage, path: Path) -> dict[str, Any] | None:
    if not storage.exists(path):
        return None
    try:
        payload = storage.load_json(path)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_text(storage: FileSystemStorage, path: Path) -> str:
    if not storage.exists(path):
        return ""
    try:
        raw = storage.load_text(path)
    except Exception:
        return ""
    # Some chapter files were stored as JSON wrappers instead of plain text.
    # Known formats: {"_": "<text>"} and {"chapter_content": "<text>"}.
    # Extract the plain text so previews don't show raw JSON.
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        try:
            import json as _json

            obj = _json.loads(stripped)
            if isinstance(obj, dict):
                for key in ("_", "chapter_content"):
                    val = obj.get(key)
                    if isinstance(val, str) and val.strip():
                        return val
        except Exception:
            pass
    return raw


def _latest_file_mtime(
    root: Path,
    *,
    precomputed: list[tuple[Path, float]] | None = None,
) -> float | None:
    """Return the latest mtime among all files under *root*.

    If *precomputed* is provided (from a prior ``_iter_project_file_mtimes``
    call), reuse it to avoid a duplicate tree walk.
    """
    if precomputed is None:
        return _scan_latest_file_mtime(root)
    latest: float | None = None
    for _path, mtime in precomputed:
        latest = mtime if latest is None else max(latest, mtime)
    return latest


def _scan_latest_file_mtime(root: Path) -> float | None:
    latest: float | None = None
    stack: list[str] = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            mtime = Path(entry.path).stat().st_mtime
                            latest = mtime if latest is None else max(latest, mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return latest


def _iter_project_file_mtimes(
    root: Path,
    *,
    precomputed: list[tuple[Path, float]] | None = None,
) -> list[tuple[Path, float]]:
    """Return file mtimes, skipping files that disappear during UI scans.

    Uses ``os.walk`` instead of ``Path.rglob`` for the recursive traversal.
    If *precomputed* is provided, return it directly so callers can share a
    single scan across multiple consumers (e.g. ``_latest_file_mtime`` and
    ``_recent_project_files``).
    """
    if precomputed is not None:
        return precomputed
    files: list[tuple[Path, float]] = []
    stack: list[str] = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        path = Path(entry.path)
                        stat_result = path.stat()
                    except OSError:
                        continue
                    files.append((path, stat_result.st_mtime))
        except OSError:
            continue
    return files


def _recent_project_files(
    project_dir: Path,
    *,
    limit: int = 8,
    precomputed: list[tuple[Path, float]] | None = None,
) -> list[str]:
    entries = sorted(
        _iter_project_file_mtimes(project_dir, precomputed=precomputed),
        key=lambda item: item[1],
        reverse=True,
    )
    return [str(path.relative_to(project_dir)) for path, _mtime in entries[:limit]]


def _artifact_kind(path: Path) -> str:
    if path.suffix == ".md":
        return "markdown"
    if path.suffix == ".json":
        return "json"
    return "text"


def _count_generated_outline_chapters(outline: StoryOutline | None) -> int:
    if outline is None:
        return 0
    return len(
        [
            chapter
            for chapter in outline.chapters
            if chapter.notes != PipelineConstants.PLACEHOLDER_NOTE and chapter.beats_summary
        ]
    )


def _verified_outline_session_progress(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    session_payload: dict[str, Any],
) -> dict[str, int]:
    """Return the safe outline resume point recorded by accepted batch checkpoints."""
    try:
        total_chapters = int(session_payload.get("total_chapters") or 0)
    except (TypeError, ValueError):
        total_chapters = 0
    if total_chapters <= 0:
        return {
            "total_chapters": 0,
            "chapters_done": 0,
            "safe_saved_chapter": 0,
            "next_chapter": 1,
        }

    try:
        from novel_forge.pipeline.long.services.init.init_outline_helpers import (
            _load_partial_outline_chapters_from_session,
        )

        chapters = _load_partial_outline_chapters_from_session(
            storage,
            layout,
            total_chapters=total_chapters,
        )
    except Exception:
        chapters = []

    safe_saved_chapter = 0
    by_number = {
        int(chapter.chapter_number)
        for chapter in chapters
        if 1 <= int(chapter.chapter_number) <= total_chapters
    }
    for number in range(1, total_chapters + 1):
        if number not in by_number:
            break
        safe_saved_chapter = number

    return {
        "total_chapters": total_chapters,
        "chapters_done": safe_saved_chapter,
        "safe_saved_chapter": safe_saved_chapter,
        "next_chapter": min(safe_saved_chapter + 1, total_chapters),
    }


def is_generated_test_project_id(project_id: str) -> bool:
    name = project_id.casefold()
    if name in _GENERATED_TEST_PROJECT_NAMES:
        return True
    if name.startswith(_GENERATED_TEST_PROJECT_PREFIXES):
        return True
    return name.startswith("project_") and len(name) == len("project_a") and name[-1].isalpha()


def _directory_file_stats(path: Path) -> tuple[int, int]:
    """Return a best-effort file count and byte total without following links."""

    file_count = 0
    size_bytes = 0
    stack: list[str] = [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        stat_result = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    file_count += 1
                    size_bytes += stat_result.st_size
        except OSError:
            continue
    return file_count, size_bytes


def _has_project_identity_markers(path: Path) -> bool:
    layout = ProjectLayout(path)
    root_markers = (
        layout.spec_path,
        layout.bible_path,
        layout.characters_path,
        layout.outline_path,
        path / ProjectStateMachine.STATE_FILE_NAME,
        layout.story_kernel_db_path,
        layout.canon_dir / "canon_current.json",
        layout.narrative_state_dir / "entity_registry.json",
    )
    if any(marker.exists() for marker in root_markers):
        return True

    plan_markers = (
        layout.blueprint_path,
        layout.narrative_contract_path,
        layout.editorial_contract_path,
        layout.blueprint_elements_path,
    )
    if any(marker.exists() for marker in plan_markers):
        return True

    state_markers = (
        layout.init_request_meta_path,
        layout.outline_session_path,
        layout.outline_tracker_path,
    )
    if any(marker.exists() for marker in state_markers):
        return True

    try:
        return (
            any(layout.chapters_dir.glob("*.md"))
            or any(layout.drafts_dir.rglob("*.md"))
            or any(layout.plans_dir.glob("chapter_*_plan.json"))
            or any(layout.states_dir.glob("chapter_*_session.json"))
        )
    except OSError:
        return False


def test_project_cleanup_candidate(path: Path) -> TestProjectCleanupCandidate | None:
    """Classify only unequivocal test directories and empty generated run traces.

    A production short story is normally assigned an id like ``short_1234abcd``.
    The id alone therefore must *not* mark it as disposable.  It is eligible
    only when no spec, plan, chapter, draft, state checkpoint, or other project
    identity artifact was ever written.  Those directories are left behind by
    test/failed-run traces and contain at most logs plus ``short_run_meta``.
    """

    if not path.is_dir() or path.is_symlink():
        return None
    if is_generated_test_project_id(path.name):
        reason: Literal["generated_test_name", "empty_generated_run"] = "generated_test_name"
    elif _AUTO_GENERATED_PROJECT_ID_RE.fullmatch(path.name) and not _has_project_identity_markers(
        path
    ):
        reason = "empty_generated_run"
    else:
        return None
    file_count, size_bytes = _directory_file_stats(path)
    return TestProjectCleanupCandidate(
        project_id=path.name,
        reason=reason,
        file_count=file_count,
        size_bytes=size_bytes,
    )


def should_list_project_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    name = path.name.casefold()
    if path.name.startswith(".") or name in _NON_PROJECT_DIR_NAMES:
        return False
    if test_project_cleanup_candidate(path) is not None:
        return False
    if name.startswith(("long_", "short_")):
        return True
    return _has_project_identity_markers(path)


def _compute_resume_outline_progress(chapters_done: int, chapters_total: int) -> int:
    if chapters_total <= 0:
        return 92
    ratio = max(0.0, min(float(chapters_done) / float(chapters_total), 1.0))
    progress = 74 + round(ratio * 17)
    return max(75, min(progress, 91))


def _readiness_blocks_only_source_artifacts(readiness: dict[str, Any]) -> bool:
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return False
    source_stage = stages.get("source_artifacts")
    if not isinstance(source_stage, dict) or not bool(source_stage.get("blocked", False)):
        return False
    for stage_name, stage_payload in stages.items():
        if stage_name == "source_artifacts" or not isinstance(stage_payload, dict):
            continue
        if bool(stage_payload.get("blocked", False)):
            return False
    remaining = readiness.get("remaining_issues")
    if isinstance(remaining, list) and remaining:
        return all(
            isinstance(issue, dict) and issue.get("stage") == "source_artifacts"
            for issue in remaining
        )
    return True


def _init_readiness_resume_status(
    readiness: dict[str, Any] | None,
) -> InitReadinessResumeStatus:
    if not isinstance(readiness, dict) or not isinstance(readiness.get("stages"), dict):
        return InitReadinessResumeStatus()
    allowed = bool(readiness.get("allowed", False))
    summary = str(readiness.get("summary") or "").strip()
    raw_stages = readiness.get("stages")
    stages = raw_stages if isinstance(raw_stages, dict) else {}
    blocked_stage = ""
    for stage_key, stage_payload in stages.items():
        if not isinstance(stage_payload, dict):
            continue
        if bool(stage_payload.get("blocked", False)):
            blocked_stage = str(stage_key or "").strip()
            break
    step = "init_readiness"
    progress_percent = 98
    if not allowed and _readiness_blocks_only_source_artifacts(readiness):
        step = "init_source_artifacts"
        progress_percent = 98
        if not summary:
            summary = "源头 artifact 准入未通过，需修复章节契约实体引用后继续。"
    if not summary and blocked_stage:
        summary = f"初始化准入未通过，停在 {display_step_name(blocked_stage)}。"
    return InitReadinessResumeStatus(
        reached=True,
        allowed=allowed,
        summary=summary,
        step=step,
        progress_percent=progress_percent,
    )


def _artifact_preview(
    storage: FileSystemStorage, layout: ProjectLayout, label: str, path: Path
) -> ChapterArtifactPreview | None:
    if not storage.exists(path):
        return None
    preview = ""
    if path.suffix == ".json":
        payload = _load_json(storage, path) or {}
        preview = _truncate(str(payload)[:360], limit=220)
    else:
        preview = _truncate(_load_text(storage, path), limit=220)
    return ChapterArtifactPreview(
        artifact_id=path.stem,
        label=label,
        relative_path=str(path.relative_to(layout.root)),
        kind=_artifact_kind(path),
        preview=preview,
        updated_at=_to_iso(path.stat().st_mtime),
    )


def _parse_chapter_number(path: Path) -> int | None:
    stem = path.stem
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _project_state_label(state: ProjectState) -> str:
    return _PROJECT_STATE_LABELS.get(state, state.value)


def _allowed_operation_values(state: ProjectState) -> list[str]:
    return sorted(operation.value for operation in ALLOWED_OPERATIONS.get(state, set()))


def _load_project_state_record(
    project_id: str,
    project_dir: Path,
) -> tuple[ProjectState | None, str | None, ProjectStateSource, list[str]]:
    try:
        machine = ProjectStateMachine.load_from_disk(project_id, project_dir)
    except FileNotFoundError:
        return None, None, "inferred", []
    except Exception as exc:
        _log.warning("project_state_load_failed | project_id=%s | error=%s", project_id, exc)
        return None, None, "inferred", []
    record = machine.record
    return (
        record.state,
        record.updated_at.isoformat(),
        "state_file",
        _allowed_operation_values(record.state),
    )


def _infer_project_state(
    *,
    mode: ProjectMode,
    completed_chapters: int,
    total_chapters: int | None,
    has_outline: bool,
    has_canon: bool,
    init_resume_available: bool,
    outline_generated_count: int,
) -> ProjectState:
    if mode == "short":
        return ProjectState.COMPLETED if completed_chapters > 0 else ProjectState.CREATED

    if total_chapters and completed_chapters >= total_chapters and total_chapters > 0:
        return ProjectState.COMPLETED
    if completed_chapters > 0:
        return ProjectState.WRITING

    outline_complete = bool(
        has_outline and total_chapters and outline_generated_count >= total_chapters
    )
    if outline_complete and not has_canon:
        return ProjectState.INIT_FAILED
    if init_resume_available or has_outline or has_canon:
        if has_outline and has_canon:
            return ProjectState.OUTLINE_READY
        return ProjectState.INITIALIZING
    return ProjectState.CREATED


def _resolve_project_state_metadata(
    *,
    project_id: str,
    project_dir: Path,
    mode: ProjectMode,
    completed_chapters: int,
    total_chapters: int | None,
    has_outline: bool,
    has_canon: bool,
    init_resume_available: bool,
    outline_generated_count: int,
    updated_at: str | None,
) -> tuple[str, str, str | None, ProjectStateSource, list[str]]:
    state, state_updated_at, source, allowed_operations = _load_project_state_record(
        project_id,
        project_dir,
    )
    if state is None:
        state = _infer_project_state(
            mode=mode,
            completed_chapters=completed_chapters,
            total_chapters=total_chapters,
            has_outline=has_outline,
            has_canon=has_canon,
            init_resume_available=init_resume_available,
            outline_generated_count=outline_generated_count,
        )
        state_updated_at = updated_at
        allowed_operations = _allowed_operation_values(state)

    return (
        state.value,
        _project_state_label(state),
        state_updated_at,
        source,
        allowed_operations,
    )


class ProjectInspector:
    """Read project artifacts from storage and expose UI-friendly summaries."""

    def __init__(self, storage: FileSystemStorage) -> None:
        self._storage = storage

    def overview(self, router: ModelRouter) -> WorkspaceOverview:
        projects = self.list_projects()
        long_projects = sum(1 for item in projects if item.mode == "long")
        short_projects = len(projects) - long_projects
        total_generated_chapters = sum(item.completed_chapters for item in projects)
        providers = sorted(
            {
                getattr(adapter, "provider_name", key.split(":", 1)[0])
                for key, adapter in router.adapters.items()
            }
        )
        return WorkspaceOverview(
            storage_root=str(self._storage.root),
            total_projects=len(projects),
            short_projects=short_projects,
            long_projects=long_projects,
            total_generated_chapters=total_generated_chapters,
            providers=providers,
            default_provider=router.default_provider,
        )

    def list_projects(self) -> list[ProjectSummary]:
        projects: list[ProjectSummary] = []
        for path in self._iter_project_dirs():
            try:
                projects.append(self.get_project_detail(path.name))
            except Exception as exc:
                _log.warning("project_list_entry_failed | project_id=%s | error=%s", path.name, exc)
                projects.append(self._build_degraded_detail(path.name, exc))
        return sorted(projects, key=lambda item: item.updated_at or "", reverse=True)

    def list_project_ids(self) -> list[str]:
        return [path.name for path in self._iter_project_dirs()]

    def list_test_project_cleanup_candidates(self) -> list[TestProjectCleanupCandidate]:
        """List test and empty auto-run directories without changing the disk."""

        candidates = [
            candidate
            for path in self._storage.list_dir(self._storage.root)
            if (candidate := test_project_cleanup_candidate(path)) is not None
        ]
        return sorted(candidates, key=lambda item: item.project_id)

    def cleanup_test_project_dirs(
        self,
        project_ids: list[str] | tuple[str, ...],
        *,
        protected_project_ids: set[str] | frozenset[str] = frozenset(),
    ) -> TestProjectCleanupResult:
        """Permanently remove reviewed test artifacts, never active project data.

        The requested ids must still match the cleanup classifier at deletion
        time.  This second check prevents a stale preview from deleting a
        project that acquired real writing artifacts after the dialog opened.
        """

        requested_ids = {str(project_id).strip() for project_id in project_ids}
        requested_ids.discard("")
        protected = {str(project_id).strip() for project_id in protected_project_ids}
        candidates = {
            item.project_id: item for item in self.list_test_project_cleanup_candidates()
        }
        removed: list[str] = []
        skipped: list[str] = []
        root = self._storage.root.resolve(strict=False)

        for project_id in sorted(requested_ids):
            if project_id in protected or project_id not in candidates:
                skipped.append(project_id)
                continue
            try:
                path = self._storage.project_path(project_id)
                with self._storage.project_lock(project_id):
                    # Re-resolve and re-classify while holding the project lock.
                    if path.is_symlink() or test_project_cleanup_candidate(path) is None:
                        skipped.append(project_id)
                        continue
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(root)
                    if resolved == root:
                        skipped.append(project_id)
                        continue
                    shutil.rmtree(path)
                    removed.append(project_id)
            except (OSError, ValueError):
                _log.warning("test_project_cleanup_skipped | project_id=%s", project_id)
                skipped.append(project_id)

        return TestProjectCleanupResult(
            removed_project_ids=tuple(removed),
            skipped_project_ids=tuple(skipped),
        )

    def _iter_project_dirs(self) -> list[Path]:
        return [
            path
            for path in self._storage.list_dir(self._storage.root)
            if should_list_project_dir(path)
        ]

    def get_project_detail(self, project_id: str) -> ProjectDetail:
        project_dir = self._storage.project_path(project_id)
        if not project_dir.exists():
            raise KeyError(project_id)
        try:
            return self._build_project_detail(project_id)
        except Exception as exc:
            _log.warning("project_detail_load_failed | project_id=%s | error=%s", project_id, exc)
            return self._build_degraded_detail(project_id, exc)

    def get_project_index(self, project_id: str) -> ProjectIndex:
        """Compute lightweight project counters without per-chapter I/O.

        Fast path: one directory ``stat()`` + one ``glob()`` for chapter count.
        No chapter file reads, no JSON loads, no rglob tree walk.
        """
        project_dir = self._storage.project_path(project_id)
        if not project_dir.exists():
            raise KeyError(project_id)

        layout = ProjectLayout(project_dir)
        mode = self._detect_mode(project_id, layout)

        # Chapter count — single glob, no file reads
        chapter_paths = list(layout.chapters_dir.glob("chapter_*.md"))
        chapter_count = len(chapter_paths)
        if mode == "short" and (layout.chapters_dir / "short_story.md").exists():
            chapter_count = max(chapter_count, 1)

        # Display name — try story_bible title, fall back to project_id
        name = project_id
        bible_data = _load_json(self._storage, layout.bible_path)
        if bible_data:
            raw_title = bible_data.get("title")
            if isinstance(raw_title, str) and raw_title.strip():
                name = raw_title.strip()

        # Directory mtime — O(1) stat call
        try:
            dir_mtime = project_dir.stat().st_mtime
            updated_at = _to_iso(dir_mtime)
        except OSError:
            updated_at = None

        return ProjectIndex(
            project_id=project_id,
            name=name,
            mode=mode,
            chapter_count=chapter_count,
            completed_chapters=chapter_count,
            total_words=0,
            updated_at=updated_at,
            is_degraded=False,
        )

    def _build_project_detail(self, project_id: str) -> ProjectDetail:
        project_dir = self._storage.project_path(project_id)
        if not project_dir.exists():
            raise KeyError(project_id)

        layout = ProjectLayout(project_dir)
        mode = self._detect_mode(project_id, layout)
        spec_data = _load_json(self._storage, layout.spec_path) or {}
        story_bible_data = _load_json(self._storage, layout.bible_path) or {}
        outline_data = _load_json(self._storage, layout.outline_path) or {}
        blueprint_data = _load_json(self._storage, layout.blueprint_path) or {}
        canon_data = _load_json(self._storage, layout.canon_dir / "canon_current.json") or {}
        outline_session_data = _load_json(self._storage, layout.outline_session_path) or {}
        init_readiness_data = _load_json(self._storage, layout.reports_dir / "init_readiness.json")

        spec = StorySpec.model_validate(spec_data) if spec_data else None
        outline = StoryOutline.model_validate(outline_data) if outline_data else None
        outline_session_progress = _verified_outline_session_progress(
            self._storage,
            layout,
            outline_session_data,
        )

        title = str(
            story_bible_data.get("title") or (spec.title if spec else "") or project_id
        ).strip()
        premise = str(story_bible_data.get("premise") or (spec.theme if spec else "") or "").strip()
        genre = str((spec.genre if spec else "") or "").strip()
        tone = str((spec.tone if spec else "") or story_bible_data.get("tone") or "").strip()

        chapters = self._collect_chapters(layout, outline)
        completed_chapters = len(chapters)
        latest_chapter = chapters[-1].chapter_number if chapters else None
        total_chapters = outline.total_chapters if outline else None
        if total_chapters is None and outline_session_progress["total_chapters"] > 0:
            total_chapters = outline_session_progress["total_chapters"]
        outline_generated_count = _count_generated_outline_chapters(outline)
        safe_outline_chapters_done = outline_session_progress["chapters_done"]
        if safe_outline_chapters_done > outline_generated_count:
            outline_generated_count = safe_outline_chapters_done
        completion_ratio = (
            round(completed_chapters / total_chapters, 3)
            if total_chapters
            else (1.0 if completed_chapters else 0.0)
        )
        preview = self._build_project_preview(layout, mode, chapters)
        words_per_chapter = 0
        if spec is not None and total_chapters:
            words_per_chapter = max(500, int(round(spec.length_target / total_chapters)))
        chapters_per_volume = 0
        if outline is not None and bool(getattr(outline, "volume_mode", False)):
            volume_lengths = [
                max(0, int(volume.end_chapter) - int(volume.start_chapter) + 1)
                for volume in (outline.volumes or [])
                if int(volume.end_chapter) >= int(volume.start_chapter)
            ]
            if volume_lengths:
                chapters_per_volume = max(0, int(round(sum(volume_lengths) / len(volume_lengths))))

        init_resume_available = False
        init_resume_step = ""
        init_resume_progress_percent = 0
        init_resume_progress_label = ""
        init_resume_next_chapter: int | None = None
        if mode == "long":
            readiness_status = _init_readiness_resume_status(init_readiness_data)
            outline_generated = outline_generated_count
            outline_total = (
                outline.total_chapters
                if outline is not None
                else outline_session_progress["total_chapters"]
            )
            # Canon 丢失且大纲已完整时（含已有章节的情况），需回机杼重新完成初始化
            outline_complete = (
                outline is not None and outline_total > 0 and outline_generated >= outline_total
            )
            if readiness_status.reached and not readiness_status.allowed:
                init_resume_available = True
                init_resume_step = readiness_status.step or "init_readiness"
                init_resume_progress_percent = readiness_status.progress_percent or 98
                init_resume_progress_label = (
                    readiness_status.summary or "初始化准入未通过，需修复后继续"
                )
            elif readiness_status.reached and readiness_status.allowed and not canon_data:
                init_resume_available = True
                init_resume_step = "canon_state"
                init_resume_progress_percent = 99
                init_resume_progress_label = "初始化准入已通过，需完成规范初始化"
            elif outline_complete and not canon_data:
                init_resume_available = True
                init_resume_step = "plan_outline"
                init_resume_progress_percent = 92
                init_resume_progress_label = "Canon 状态丢失，需重新完成初始化"
            elif outline_complete and canon_data:
                # 大纲完整且 Canon 存在 → 立项已完成，无需显示恢复进度
                pass
            elif completed_chapters == 0:
                if safe_outline_chapters_done > 0 and outline_total > 0:
                    safe_saved_chapter = outline_session_progress["safe_saved_chapter"]
                    init_resume_available = True
                    init_resume_step = "plan_outline"
                    init_resume_progress_percent = _compute_resume_outline_progress(
                        safe_outline_chapters_done,
                        outline_total,
                    )
                    init_resume_progress_label = (
                        f"大纲已安全保存到第{safe_saved_chapter}章"
                        f"（{safe_outline_chapters_done}/{outline_total}章）"
                    )
                    init_resume_next_chapter = outline_session_progress["next_chapter"]
                elif outline is not None and outline_total > 0 and outline_generated < outline_total:
                    init_resume_available = True
                    init_resume_step = "plan_outline"
                    init_resume_progress_percent = _compute_resume_outline_progress(
                        outline_generated,
                        outline_total,
                    )
                    init_resume_progress_label = f"大纲暂存 {outline_generated}/{outline_total} 章"
                    latest_outline = outline_session_data.get("latest_chapter_number")
                    if not isinstance(latest_outline, int) or latest_outline <= 0:
                        latest_outline = outline_generated
                    init_resume_next_chapter = min(latest_outline + 1, outline_total)
                elif blueprint_data:
                    init_resume_available = True
                    init_resume_step = "plan_outline"
                    init_resume_progress_percent = 76
                    init_resume_progress_label = "叙事蓝图已完成"
                elif _load_json(self._storage, layout.characters_path):
                    init_resume_available = True
                    init_resume_step = "plan_blueprint_elements"
                    init_resume_progress_percent = 38
                    init_resume_progress_label = "角色设定已完成"
                elif story_bible_data:
                    init_resume_available = True
                    init_resume_step = "init_character_bible"
                    init_resume_progress_percent = 20
                    init_resume_progress_label = "世界观设定已完成"
                elif spec is not None:
                    init_resume_available = True
                    init_resume_step = "init_story_bible"
                    init_resume_progress_percent = 8
                    init_resume_progress_label = "故事规格已确认"

        # Single tree walk — shared between recent_files and latest_mtime.
        _file_mtimes = _iter_project_file_mtimes(project_dir)
        recent_files = _recent_project_files(project_dir, precomputed=_file_mtimes)

        artifact_counts = {
            "chapters": self._count_files(layout.chapters_dir, "*.md"),
            "drafts": self._count_files(layout.drafts_dir, "*.md", recursive=True),
            "reports": self._count_files(layout.reports_dir, "*.json"),
            "plans": self._count_files(layout.plans_dir, "*.json"),
            "states": self._count_files(layout.states_dir, "*.json"),
        }
        project_updated_at = _to_iso(
            _latest_file_mtime(project_dir, precomputed=_file_mtimes)
        )
        (
            project_state,
            project_state_label,
            project_state_updated_at,
            project_state_source,
            allowed_operations,
        ) = _resolve_project_state_metadata(
            project_id=project_id,
            project_dir=project_dir,
            mode=mode,
            completed_chapters=completed_chapters,
            total_chapters=total_chapters,
            has_outline=outline is not None,
            has_canon=bool(canon_data),
            init_resume_available=init_resume_available,
            outline_generated_count=outline_generated_count,
            updated_at=project_updated_at,
        )

        return ProjectDetail(
            project_id=project_id,
            mode=mode,
            title=title,
            genre=genre,
            tone=tone,
            premise=premise,
            preview=preview,
            total_chapters=total_chapters,
            language=str((spec.language if spec else "") or "").strip(),
            words_per_chapter=words_per_chapter,
            volume_mode=(
                "on"
                if bool(
                    (
                        outline.volume_mode
                        if outline is not None
                        else blueprint_data.get("volume_mode")
                    )
                )
                else "off"
            ),
            chapters_per_volume=chapters_per_volume,
            characters_hint=str((spec.characters_hint if spec else "") or "").strip(),
            world_hint=str((spec.world_hint if spec else "") or "").strip(),
            conflict_hint=str((spec.conflict_hint if spec else "") or "").strip(),
            pov_hint=str((spec.pov_hint if spec else "") or "").strip(),
            opening_style=str((spec.opening_style if spec else "") or "").strip(),
            ending_style=str((spec.ending_style if spec else "") or "").strip(),
            extra_instructions=str((spec.extra_instructions if spec else "") or "").strip(),
            init_resume_available=init_resume_available,
            init_resume_step=init_resume_step,
            init_resume_step_label=display_step_name(init_resume_step) if init_resume_step else "",
            init_resume_progress_label=init_resume_progress_label,
            init_resume_progress_percent=init_resume_progress_percent,
            init_resume_next_chapter=init_resume_next_chapter,
            completed_chapters=completed_chapters,
            latest_chapter=latest_chapter,
            completion_ratio=completion_ratio,
            has_outline=outline is not None,
            has_canon=bool(canon_data),
            updated_at=project_updated_at,
            project_state=project_state,
            project_state_label=project_state_label,
            project_state_updated_at=project_state_updated_at,
            project_state_source=project_state_source,
            allowed_operations=allowed_operations,
            outline_generated_count=outline_generated_count,
            chapters=chapters,
            recent_files=recent_files,
            artifact_counts=artifact_counts,
            index=ProjectIndex(
                project_id=project_id,
                name=title,
                mode=mode,
                chapter_count=len(chapters),
                completed_chapters=completed_chapters,
                total_words=sum(ch.word_count for ch in chapters),
                updated_at=project_updated_at,
                is_degraded=False,
            ),
        )

    def _build_degraded_detail(self, project_id: str, error: Exception) -> ProjectDetail:
        project_dir = self._storage.project_path(project_id)
        layout = ProjectLayout(project_dir)
        mode = self._detect_mode(project_id, layout)
        _file_mtimes = _iter_project_file_mtimes(project_dir)
        recent_files = _recent_project_files(project_dir, precomputed=_file_mtimes)
        artifact_counts = {
            "chapters": self._count_files(layout.chapters_dir, "*.md"),
            "drafts": self._count_files(layout.drafts_dir, "*.md", recursive=True),
            "reports": self._count_files(layout.reports_dir, "*.json"),
            "plans": self._count_files(layout.plans_dir, "*.json"),
            "states": self._count_files(layout.states_dir, "*.json"),
        }
        completed_chapters = self._count_files(layout.chapters_dir, "chapter_*.md")
        if mode == "short" and (layout.chapters_dir / "short_story.md").exists():
            completed_chapters = max(completed_chapters, 1)
        load_error = f"{type(error).__name__}: {error}"
        project_updated_at = _to_iso(
            _latest_file_mtime(project_dir, precomputed=_file_mtimes)
        )
        has_outline = layout.outline_path.exists()
        has_canon = (layout.canon_dir / "canon_current.json").exists()
        (
            project_state,
            project_state_label,
            project_state_updated_at,
            project_state_source,
            allowed_operations,
        ) = _resolve_project_state_metadata(
            project_id=project_id,
            project_dir=project_dir,
            mode=mode,
            completed_chapters=completed_chapters,
            total_chapters=None,
            has_outline=has_outline,
            has_canon=has_canon,
            init_resume_available=False,
            outline_generated_count=0,
            updated_at=project_updated_at,
        )
        return ProjectDetail(
            project_id=project_id,
            mode=mode,
            title=project_id,
            preview="项目元数据读取失败，请检查损坏文件或重新生成关键工件。",
            completed_chapters=completed_chapters,
            completion_ratio=1.0 if mode == "short" and completed_chapters else 0.0,
            has_outline=has_outline,
            has_canon=has_canon,
            updated_at=project_updated_at,
            project_state=project_state,
            project_state_label=project_state_label,
            project_state_updated_at=project_state_updated_at,
            project_state_source=project_state_source,
            allowed_operations=allowed_operations,
            recent_files=recent_files,
            artifact_counts=artifact_counts,
            load_status="degraded",
            load_error=load_error,
            index=ProjectIndex(
                project_id=project_id,
                name=project_id,
                mode=mode,
                chapter_count=completed_chapters,
                completed_chapters=completed_chapters,
                total_words=0,
                updated_at=project_updated_at,
                is_degraded=True,
            ),
        )

    def get_chapter_workspace_snapshot(
        self,
        project_id: str,
        chapter_number: int,
        *,
        project_detail: ProjectDetail | None = None,
    ) -> ChapterWorkspaceSnapshot:
        detail = (
            project_detail if project_detail is not None else self.get_project_detail(project_id)
        )
        if detail.mode != "long":
            raise ValueError(f"Project '{project_id}' is not a long-form project")

        layout = ProjectLayout(self._storage.project_path(project_id))
        outline_data = _load_json(self._storage, layout.outline_path) or {}
        outline = StoryOutline.model_validate(outline_data) if outline_data else None
        if outline is None:
            raise ValueError(f"Project '{project_id}' has no outline")

        chapter_index = {chapter.chapter_number: chapter for chapter in outline.chapters}
        current_outline = chapter_index.get(chapter_number)
        if current_outline is None:
            raise ValueError(f"Chapter {chapter_number} not found in outline")

        previous_outline = chapter_index.get(chapter_number - 1)
        next_outline = chapter_index.get(chapter_number + 1)
        previous_creative = {}
        if chapter_number > 1:
            long_report = _load_json(self._storage, layout.creative_report_path(chapter_number - 1))
            if long_report:
                previous_creative = long_report
            else:
                short_report = _load_json(self._storage, layout.short_creative_report_path())
                if short_report:
                    previous_creative = short_report
        previous_exit = (
            _load_json(self._storage, layout.chapter_exit_state_path(chapter_number - 1))
            if chapter_number > 1
            else None
        ) or {}
        alignment_data = (
            _load_json(self._storage, layout.alignment_report_path(chapter_number)) or {}
        )
        eval_data = _load_json(self._storage, layout.eval_report_path(chapter_number)) or {}
        continuity_data = (
            _load_json(self._storage, layout.continuity_report_path(chapter_number)) or {}
        )
        causal_data = (
            _load_json(self._storage, layout.chapter_causal_report_path(chapter_number)) or {}
        )
        rp_data = _load_json(self._storage, layout.reading_power_report_path(chapter_number)) or {}
        guard_data = _load_json(self._storage, layout.guard_report_path(chapter_number)) or {}
        checkpoint_data = (
            _load_json(self._storage, layout.chapter_checkpoint_path(chapter_number)) or {}
        )
        session_data = _load_json(self._storage, layout.chapter_session_path(chapter_number)) or {}
        pending_checkpoint = (
            DecisionCheckpoint.model_validate(checkpoint_data) if checkpoint_data else None
        )

        # 读取精确失效集合，用于检测因上游修订导致的下游失效章节。
        stale_chapters = scoped_stale_chapters(self._storage, layout)
        stale_cutoff = min(stale_chapters) if stale_chapters else None
        if stale_cutoff is not None:
            if chapter_number > stale_cutoff:
                pending_checkpoint = None
            elif (
                chapter_number in stale_chapters
                and pending_checkpoint is not None
                and not is_checkpoint_session_fresh_for_current_chain(
                    self._storage, layout, chapter_number
                )
            ):
                # 仅隐藏旧链路 checkpoint；若是当前链路刚生成的 checkpoint，允许继续。
                pending_checkpoint = None

        chapter_files = {chapter.chapter_number for chapter in detail.chapters}
        chapters: list[ChapterWorkspaceChapter] = []
        for item in outline.chapters:
            status = "pending"
            status_label = "待写"
            # Anything at or after ``stale_cutoff`` is considered stale unless a
            # fresh checkpoint for the current chain proves otherwise; this keeps
            # downstream chapters visibly "stale" the moment an upstream chapter
            # is rewritten, even if no explicit invalidation record exists yet.
            chapter_in_stale_window = (
                stale_cutoff is not None and item.chapter_number >= stale_cutoff
            )
            chapter_fresh_checkpoint = (
                pending_checkpoint is not None
                and is_checkpoint_session_fresh_for_current_chain(
                    self._storage, layout, item.chapter_number
                )
            )
            chapter_is_stale = item.chapter_number in stale_chapters or (
                chapter_in_stale_window and not chapter_fresh_checkpoint
            )
            if chapter_is_stale:
                status = "stale"
                status_label = "已失效"
            elif item.chapter_number in chapter_files:
                status = "done"
                status_label = "已完成"
            if item.chapter_number == chapter_number:
                if pending_checkpoint:
                    # 区分首次创作（空章节 → 待确认）和强制重写（已有文件 → 重写中）
                    if item.chapter_number in chapter_files:
                        status = "rewriting"
                        status_label = "重写中"
                    else:
                        status = "needs_decision"
                        status_label = "待确认"
                elif chapter_is_stale:
                    status = "stale"
                    status_label = "已失效"
                elif status not in ("done", "stale"):
                    # 未完成/未失效的当前章节标记为当前章
                    status = "current"
                    status_label = "当前章"
                # 已完成/已失效章节不降级为 "current"
            existing = next(
                (
                    chapter
                    for chapter in detail.chapters
                    if chapter.chapter_number == item.chapter_number
                ),
                None,
            )
            chapters.append(
                ChapterWorkspaceChapter(
                    chapter_number=item.chapter_number,
                    title=item.title or f"第 {item.chapter_number} 章",
                    status=status,
                    status_label=status_label,
                    word_count=existing.word_count if existing else 0,
                    overall_score=existing.overall_score if existing else None,
                    continuity_score=existing.continuity_score if existing else None,
                    updated_at=existing.updated_at if existing else None,
                )
            )

        artifacts: list[ChapterArtifactPreview] = []
        artifact_specs = [
            ("上一章正文", layout.chapter_path(chapter_number - 1) if chapter_number > 1 else None),
            ("章节上下文", layout.chapter_state_packet_path(chapter_number)),
            ("章节桥接", layout.chapter_bridge_path(chapter_number)),
            ("章节计划", layout.chapter_plan_path(chapter_number)),
            ("待归档正文", layout.chapter_review_draft_path(chapter_number)),
            ("最终正文", layout.chapter_path(chapter_number)),
            ("创作报告", layout.creative_report_path(chapter_number)),
            ("对齐报告", layout.alignment_report_path(chapter_number)),
            ("因果校验", layout.chapter_causal_report_path(chapter_number)),
            ("连贯性报告", layout.continuity_report_path(chapter_number)),
            ("质量评估", layout.eval_report_path(chapter_number)),
            ("知识边界审计", layout.knowledge_boundary_report_path(chapter_number)),
            ("AI 护栏", layout.guard_report_path(chapter_number)),
            ("记忆诊断", layout.chapter_memory_diagnostics_path(chapter_number)),
        ]
        for label, path in artifact_specs:
            if path is None:
                continue
            preview = _artifact_preview(self._storage, layout, label, path)
            if preview is not None:
                artifacts.append(preview)

        alignment_score = coerce_score(alignment_data.get("alignment_score"))
        overall_score = coerce_score(eval_data.get("overall_score"))
        continuity_score = coerce_score(continuity_data.get("continuity_score"))
        continuity_raw_issues = continuity_data.get("issues") or []
        continuity_issue_count = _open_issue_count(continuity_raw_issues)
        continuity_issues = [
            i if isinstance(i, dict) else i.model_dump(mode="json") for i in continuity_raw_issues
        ]
        causal_score = coerce_score(causal_data.get("causal_score"))
        causal_raw_issues = causal_data.get("issues") or []
        causal_issue_count = len(causal_raw_issues)
        causal_issues_normalized = [
            i if isinstance(i, dict) else i.model_dump(mode="json") for i in causal_raw_issues
        ]
        reading_power_score = coerce_score(rp_data.get("overall_score"))

        warning_messages = [
            str(item).strip()
            for item in (
                ((session_data.get("pending_result") or {}).get("warnings") or [])
                if session_data.get("stage") == "guard_checkpoint"
                else []
            )
            if str(item).strip()
        ]
        # Always regenerate causal warnings from the latest causal_data on disk,
        # because session warnings may be stale after post-guard repairs.
        _session_causal_warnings = [w for w in warning_messages if "因果链校验" in w]
        if _session_causal_warnings:
            # Remove stale causal warnings; regenerate from fresh data below
            warning_messages = [w for w in warning_messages if "因果链校验" not in w]
        if causal_issues_normalized:
            high_priority_count = sum(
                1
                for issue in causal_issues_normalized
                if str((issue.get("severity")) or "").lower() in {"critical", "high"}
            )
            if high_priority_count:
                warning_messages.append(
                    f"因果链校验发现 {causal_issue_count} 个问题，其中高优先级 {high_priority_count} 个，建议人工复核。"
                )
            elif causal_issue_count:
                warning_messages.append(
                    f"因果链校验发现 {causal_issue_count} 个问题，均为中低优先级。"
                )
        if stale_cutoff is not None and chapter_number >= stale_cutoff:
            warning_messages.insert(
                0,
                f"第 {stale_cutoff} 章之后的内容基于旧链路，需先从第 {stale_cutoff} 章重新生成。",
            )
        _current_text = _load_text(self._storage, layout.chapter_path(chapter_number))
        if not _current_text:
            _current_text = _load_text(
                self._storage, layout.chapter_review_draft_path(chapter_number)
            )
        pending_checkpoint = _refresh_guard_checkpoint_summary(
            pending_checkpoint,
            current_text=_current_text,
            alignment_score=alignment_score,
            continuity_score=continuity_score,
            overall_score=overall_score,
            causal_score=causal_score,
        )
        _current_hash = source_text_hash(_current_text) if _current_text else ""
        _causal_report_hash = str(causal_data.get("source_text_hash", "") or "").strip()
        if not _causal_report_hash and causal_issue_count > 0:
            warning_messages.append(
                "因果校验报告未绑定正文版本，当前问题可能来自旧版文本，建议重新评估。"
            )
        if _current_hash:
            if isinstance(alignment_data, dict):
                _alignment_report_hash = str(
                    alignment_data.get("source_text_hash", "") or ""
                ).strip()
                if not _alignment_report_hash and alignment_data.get("alignment_score") is not None:
                    warning_messages.append(
                        "对齐报告未绑定正文版本，当前问题可能来自旧版文本，建议重新评估。"
                    )
            for _label, _payload in (
                ("对齐报告", alignment_data),
                ("质量评估", eval_data),
                ("连贯性报告", continuity_data),
                ("因果校验", causal_data),
                ("AI 护栏", guard_data),
            ):
                if not isinstance(_payload, dict):
                    continue
                _report_hash = str(_payload.get("source_text_hash", "") or "").strip()
                if _report_hash and _report_hash != _current_hash:
                    warning_messages.append(f"{_label}基于旧版正文，建议重新评估或重新生成。")
        warning_messages = _dedupe_texts(warning_messages)

        previous_summary = ""
        if chapter_number > 1:
            previous_detail = next(
                (
                    chapter
                    for chapter in detail.chapters
                    if chapter.chapter_number == chapter_number - 1
                ),
                None,
            )
            previous_summary = (
                previous_detail.preview
                if previous_detail is not None
                else _truncate(
                    _load_text(self._storage, layout.chapter_path(chapter_number - 1)),
                    limit=260,
                )
            )

        carry_forward = [
            str(item).strip()
            for item in (
                previous_exit.get("must_carry_forward")
                or previous_creative.get("must_carry_forward")
                or []
            )
            if str(item).strip()
        ]

        # Check for resumable review progress (断点续传)
        _has_review_progress = False
        _review_progress_stage = ""
        _review_progress_path = layout.chapter_review_progress_path(chapter_number)
        if self._storage.exists(_review_progress_path):
            try:
                _rp_data = self._storage.load_json(_review_progress_path)
                if isinstance(_rp_data, dict) and _rp_data.get("completed_stage"):
                    _has_review_progress = True
                    _review_progress_stage = str(_rp_data["completed_stage"])
            except Exception:
                pass

        return ChapterWorkspaceSnapshot(
            project_id=detail.project_id,
            project_title=detail.title or detail.project_id,
            chapter_number=chapter_number,
            total_chapters=outline.total_chapters,
            genre=detail.genre,
            tone=detail.tone,
            project_summary=detail.premise or detail.preview or outline.synopsis or "",
            chapters=chapters,
            previous_title=previous_outline.title if previous_outline else "",
            previous_summary=previous_summary,
            previous_exit_summary="；".join(carry_forward[:3]) if carry_forward else "",
            current_title=current_outline.title or f"第 {chapter_number} 章",
            current_goal=current_outline.goal,
            current_outline_summary=_truncate(
                "；".join(
                    [
                        current_outline.goal,
                        "；".join(current_outline.main_plot_points[:3]),
                        "；".join(current_outline.subplot_points[:2]),
                    ]
                ),
                limit=260,
            ),
            next_title=next_outline.title if next_outline else "",
            next_goal=next_outline.goal if next_outline else "",
            carry_forward=carry_forward,
            suggestions_for_next_chapter=str(
                previous_creative.get("suggestions_for_next_chapter", "")
            ).strip(),
            alignment_score=alignment_score,
            overall_score=overall_score,
            continuity_score=continuity_score,
            continuity_issue_count=continuity_issue_count,
            continuity_issues=continuity_issues,
            causal_score=causal_score,
            causal_issue_count=causal_issue_count,
            causal_issues=causal_issues_normalized,
            reading_power_score=reading_power_score,
            warnings=warning_messages,
            artifacts=artifacts,
            pending_checkpoint=pending_checkpoint,
            has_review_progress=_has_review_progress,
            review_progress_stage=_review_progress_stage,
        )

    def _detect_mode(self, project_id: str, layout: ProjectLayout) -> ProjectMode:
        long_markers = (
            layout.bible_path,
            layout.characters_path,
            layout.outline_path,
            layout.canon_dir / "canon_current.json",
            # ``init_request_meta.json`` is written before the first long-form
            # artifact.  Without this marker, a failed/cancelled early init
            # that only has ``spec.json`` is misclassified as a short project,
            # which hides its resume state and breaks the re-init action.
            layout.init_request_meta_path,
        )
        if any(path.exists() for path in long_markers):
            return "long"
        if project_id.startswith("long_"):
            return "long"
        return "short"

    def _collect_chapters(
        self,
        layout: ProjectLayout,
        outline: StoryOutline | None,
    ) -> list[ChapterSummary]:
        chapter_paths = sorted(layout.chapters_dir.glob("chapter_*.md"))
        chapter_titles = {
            item.chapter_number: item.title for item in (outline.chapters if outline else [])
        }
        sidecar_path = layout.root / "_chapter_meta_cache.json"
        sidecar = self._load_chapter_meta_sidecar(sidecar_path)
        chapters: list[ChapterSummary] = []
        sidecar_dirty = False
        for path in chapter_paths:
            chapter_number = _parse_chapter_number(path)
            if chapter_number is None:
                continue
            text = _load_text(self._storage, path)
            chapter_mtime = path.stat().st_mtime
            cached = sidecar.get(str(chapter_number))
            if (
                cached is not None
                and cached.get("chapter_mtime") == chapter_mtime
            ):
                overall_score = cached.get("overall_score")
                continuity_score = cached.get("continuity_score")
                title = cached.get("title", "")
            else:
                meta = _load_json(self._storage, layout.chapter_meta_path(chapter_number)) or {}
                eval_data = _load_json(self._storage, layout.eval_report_path(chapter_number)) or {}
                continuity_data = (
                    _load_json(self._storage, layout.continuity_report_path(chapter_number)) or {}
                )
                overall_score = None
                if eval_data:
                    try:
                        overall_score = EvalReport.model_validate(eval_data).overall_score
                    except Exception:
                        raw_score = eval_data.get("overall_score")
                        if isinstance(raw_score, (int, float)):
                            overall_score = float(raw_score)
                continuity_score = continuity_data.get("continuity_score")
                title = str(
                    meta.get("title")
                    or chapter_titles.get(chapter_number)
                    or f"第{chapter_number}章"
                )
                sidecar[str(chapter_number)] = {
                    "overall_score": overall_score,
                    "continuity_score": (
                        float(continuity_score)
                        if isinstance(continuity_score, (int, float))
                        else None
                    ),
                    "title": title,
                    "chapter_mtime": chapter_mtime,
                }
                sidecar_dirty = True
            chapters.append(
                ChapterSummary(
                    chapter_number=chapter_number,
                    title=str(
                        title
                        or chapter_titles.get(chapter_number)
                        or f"第{chapter_number}章"
                    ),
                    word_count=display_word_count(text),
                    overall_score=overall_score,
                    continuity_score=(
                        float(continuity_score)
                        if isinstance(continuity_score, (int, float))
                        else None
                    ),
                    updated_at=_to_iso(chapter_mtime),
                    preview=_truncate(text, limit=260),
                )
            )
        if sidecar_dirty:
            self._save_chapter_meta_sidecar(sidecar_path, sidecar)
        return chapters

    @staticmethod
    def _load_chapter_meta_sidecar(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _save_chapter_meta_sidecar(path: Path, data: dict[str, Any]) -> None:
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _build_project_preview(
        self,
        layout: ProjectLayout,
        mode: ProjectMode,
        chapters: list[ChapterSummary],
    ) -> str:
        if mode == "short":
            short_path = layout.chapters_dir / "short_story.md"
            return _truncate(_load_text(self._storage, short_path), limit=280)
        if chapters:
            return chapters[-1].preview
        return ""

    @staticmethod
    def _count_files(root: Path, pattern: str, *, recursive: bool = False) -> int:
        if not root.exists():
            return 0
        iterator = root.rglob(pattern) if recursive else root.glob(pattern)
        return sum(1 for _ in iterator)

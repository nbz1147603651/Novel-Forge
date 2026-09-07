"""Read-only application queries shared by replaceable UI clients."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.app_service.engine_novel import (
    EngineChapterActivityView,
    EngineNovelStudioView,
    _novel_error_log,
    project_book_autorun_view,
    project_novel_studio_view,
)
from novel_forge.app_service.engine_voice import (
    VOICE_ACTIVE_TASK_KIND_BY_JOB_KIND,
    EngineVoiceActiveTaskView,
    EngineVoiceStudioView,
    project_voice_studio_view,
)
from novel_forge.core.config import Settings
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    DubbingScript,
    NarratorVoiceProfile,
    VoiceTeamContract,
)
from novel_forge.tts.services.studio_service import VoiceStudioProjectService
from novel_forge.workspace.projects import ProjectDetail, ProjectInspector
from novel_forge.workspace.publication import build_chapter_publication_view

if TYPE_CHECKING:
    from novel_forge.app_service.job_service import JobService


class EngineQueryError(ValueError):
    """Base error for ``EngineQueryService`` calls that the route layer can
    map to a typed HTTP response.

    Subclasses carry a stable ``code`` so the FastAPI handler does not have to
    pattern-match the message text.
    """


class ProjectNotLongError(EngineQueryError):
    code = "project_not_long"

    def __init__(self, project_id: str) -> None:
        super().__init__(f"项目 {project_id} 不是长篇项目，请用机杼页创建。")
        self.project_id = project_id


class ProjectOutlineMissingError(EngineQueryError):
    code = "project_outline_missing"

    def __init__(self, project_id: str) -> None:
        super().__init__(f"项目 {project_id} 尚未生成大纲，请先在机杼完成大纲规划。")
        self.project_id = project_id


class ChapterOutOfRangeError(EngineQueryError):
    code = "chapter_out_of_range"

    def __init__(self, project_id: str, chapter_number: int) -> None:
        super().__init__(f"项目 {project_id} 没有第 {chapter_number} 章。")
        self.project_id = project_id
        self.chapter_number = chapter_number


class InvalidChapterNumberError(EngineQueryError):
    code = "invalid_chapter_number"

    def __init__(self, chapter_number: int) -> None:
        super().__init__(f"章节号 {chapter_number} 非法，必须大于等于 1。")
        self.chapter_number = chapter_number


class EngineQueryService:
    """Compose validated domain artifacts into stable, credential-free UI views."""

    def __init__(
        self,
        *,
        storage: FileSystemStorage,
        settings: Settings,
        jobs: JobService,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._jobs = jobs
        self._projects = ProjectInspector(storage)

    def get_novel_studio(
        self,
        project_id: str,
        *,
        chapter_number: int | None = None,
    ) -> EngineNovelStudioView:
        autorun_getter = getattr(self._jobs, "book_autorun_state", None)
        autorun_state = autorun_getter(project_id) if callable(autorun_getter) else None
        detail = self._projects.get_project_detail(project_id)
        if detail.mode != "long":
            raise ProjectNotLongError(project_id)
        try:
            selected_chapter = _selected_novel_chapter(detail, chapter_number)
        except ValueError as exc:
            if "chapter_number" in str(exc):
                raise InvalidChapterNumberError(chapter_number or 0) from exc
            raise
        try:
            snapshot = self._projects.get_chapter_workspace_snapshot(
                project_id,
                selected_chapter,
                project_detail=detail,
            )
        except (ValueError, Exception):
            # Degrade gracefully: return a minimal view so the page can render.
            snapshot = None

        jobs = self._jobs.list(project_id=project_id)
        task_error_entries = self._jobs.list_error_log(project_id=project_id)
        if snapshot is not None:
            return project_novel_studio_view(
                detail,
                snapshot,
                jobs,
                autorun_state,
                task_error_entries,
            )

        # Degraded view — page renders with project info but empty chapter rail.
        next_ch = max(1, (detail.completed_chapters or 0) + 1)
        total_ch = detail.total_chapters or 0
        return EngineNovelStudioView(
            project_id=detail.project_id,
            project_title=detail.title or project_id,
            project_synopsis=detail.premise or detail.preview,
            next_chapter=next_ch,
            total_chapters=total_ch,
            chapters=[],
            plan_title="",
            plan_summary="项目大纲数据暂不可用，章台以降级模式显示。",
            activity=EngineChapterActivityView(),
            autorun=project_book_autorun_view(autorun_state),
            task_error_log=_novel_error_log(detail.project_id, jobs, task_error_entries),
        )

    def get_voice_studio(
        self,
        project_id: str,
        *,
        chapter_number: int | None = None,
    ) -> EngineVoiceStudioView:
        detail = self._projects.get_project_detail(project_id)
        selected_chapter = chapter_number or detail.latest_chapter or 1
        if selected_chapter < 1:
            raise ValueError("chapter_number must be at least 1")
        layout = ProjectLayout(self._storage.project_path(project_id))
        voice_team = self._load_voice_team(layout)
        script = self._load_script(layout, selected_chapter)
        audio_result = self._load_audio_result(layout, selected_chapter)
        studio_service = VoiceStudioProjectService(layout, project_id=project_id)
        segment_statuses = self._segment_statuses(audio_result)
        active_tasks = [
            EngineVoiceActiveTaskView(
                id=job.job_id,
                kind=VOICE_ACTIVE_TASK_KIND_BY_JOB_KIND[kind],
            )
            for job in self._jobs.list(project_id=project_id)
            if (kind := str(getattr(job.kind, "value", job.kind)))
            in VOICE_ACTIVE_TASK_KIND_BY_JOB_KIND
            and str(getattr(job.status, "value", job.status)) in {"queued", "running", "paused"}
        ]
        # Older clients only understand a synthesis task. Keep their fallback
        # behavior correct while new clients consume the complete task set.
        active_task_id = next(
            (task.id for task in active_tasks if task.kind == "synthesis"),
            None,
        )
        chapter_text = ""
        publication_ready = True
        publication_blocking_reasons: list[str] = []
        chapter_path = layout.chapter_path(selected_chapter)
        if chapter_path.is_file():
            try:
                publication = build_chapter_publication_view(
                    layout,
                    project_id,
                    selected_chapter,
                )
                chapter_text = publication.text
                publication_ready = publication.deliverable
                publication_blocking_reasons = list(publication.blocking_reasons)
            except (OSError, ValueError):
                publication_ready = False
                publication_blocking_reasons = ["publication_projection_unreadable"]
                try:
                    chapter_text = chapter_path.read_text(encoding="utf-8")
                except OSError:
                    chapter_text = ""
        return project_voice_studio_view(
            detail,
            chapter_number=selected_chapter,
            voice_team=voice_team,
            script=script,
            characters=self._load_characters(layout),
            narrator_profile=self._load_narrator_profile(layout),
            segment_statuses=segment_statuses,
            audio_result=audio_result,
            active_task_id=active_task_id,
            active_tasks=active_tasks,
            default_provider=self._settings.tts_default_provider,
            default_model=self._settings.tts_default_model,
            layout=layout,
            sound_assets=studio_service.all_sound_assets(),
            take_manifest=studio_service.load_take_manifest(selected_chapter),
            chapter_text=chapter_text,
            publication_ready=publication_ready,
            publication_blocking_reasons=publication_blocking_reasons,
        )

    def _load_voice_team(self, layout: ProjectLayout) -> VoiceTeamContract | None:
        payload = self._load_optional_json(layout.tts_voice_team_path)
        if payload is None:
            return None
        try:
            return VoiceTeamContract.model_validate(payload)
        except ValueError as exc:
            raise ValueError("Voice team artifact is invalid") from exc

    def _load_narrator_profile(self, layout: ProjectLayout) -> NarratorVoiceProfile | None:
        payload = self._load_optional_json(layout.tts_narrator_profile_path)
        if payload is None:
            return None
        try:
            return NarratorVoiceProfile.model_validate(payload)
        except ValueError as exc:
            raise ValueError("Narrator voice profile artifact is invalid") from exc

    def _load_script(self, layout: ProjectLayout, chapter_number: int) -> DubbingScript | None:
        payload = self._load_optional_json(layout.tts_dubbing_script_path(chapter_number))
        if payload is None:
            return None
        try:
            return DubbingScript.model_validate(payload)
        except ValueError as exc:
            raise ValueError(f"Chapter {chapter_number} dubbing script is invalid") from exc

    def _load_audio_result(
        self,
        layout: ProjectLayout,
        chapter_number: int,
    ) -> ChapterAudioResult | None:
        payload = self._load_optional_json(layout.tts_audio_result_path(chapter_number))
        if payload is None:
            return None
        try:
            return ChapterAudioResult.model_validate(payload)
        except ValueError as exc:
            raise ValueError(f"Chapter {chapter_number} audio result is invalid") from exc

    @staticmethod
    def _segment_statuses(result: ChapterAudioResult | None) -> dict[int, str]:
        if result is None:
            return {}
        return {
            item.segment_index: str(getattr(item.status, "value", item.status))
            for item in result.segment_results
        }

    def _load_characters(self, layout: ProjectLayout) -> list[dict[str, Any]]:
        payload = self._load_optional_json(layout.characters_path)
        if payload is None:
            return []
        try:
            bible = CharacterBible.model_validate(payload)
        except ValueError:
            return []
        return [profile.model_dump(mode="json") for profile in bible.characters]

    def _load_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not self._storage.exists(path):
            return None
        payload = self._storage.load_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"Artifact '{getattr(path, 'name', 'unknown')}' must be an object")
        return payload


def _selected_novel_chapter(detail: ProjectDetail, requested: int | None) -> int:
    if requested is not None:
        if requested < 1:
            raise ValueError("chapter_number must be at least 1")
        return requested
    total = detail.total_chapters or 0
    if total > 0 and detail.completed_chapters < total:
        return max(1, detail.completed_chapters + 1)
    if detail.latest_chapter is not None:
        return max(1, detail.latest_chapter)
    return 1


__all__ = [
    "ChapterOutOfRangeError",
    "EngineQueryError",
    "EngineQueryService",
    "InvalidChapterNumberError",
    "ProjectNotLongError",
    "ProjectOutlineMissingError",
]

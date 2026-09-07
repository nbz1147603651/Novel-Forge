from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.app_service.engine_queries import (
    EngineQueryService,
    ProjectNotLongError,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    DubbingScript,
    DubbingSegment,
    SegmentType,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import compute_dubbing_script_hash, compute_source_text_hash
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot
from novel_forge.workspace.projects import ProjectDetail


class StubJobs:
    def __init__(self, records: list[object] | None = None) -> None:
        self.records = records or []

    def list(self, project_id: str | None = None) -> list[object]:
        return list(self.records)

    def list_error_log(self, project_id: str | None = None) -> list[dict[str, object]]:
        return []


class StubProjects:
    def __init__(self, detail: ProjectDetail) -> None:
        self.detail = detail
        self.requested_chapter = 0
        self.snapshot_error: Exception | None = None

    def get_project_detail(self, project_id: str) -> ProjectDetail:
        if project_id != self.detail.project_id:
            raise KeyError(project_id)
        return self.detail

    def get_chapter_workspace_snapshot(
        self,
        project_id: str,
        chapter_number: int,
        *,
        project_detail: ProjectDetail,
    ) -> ChapterWorkspaceSnapshot:
        if self.snapshot_error is not None:
            raise self.snapshot_error
        assert project_detail is self.detail
        self.requested_chapter = chapter_number
        return ChapterWorkspaceSnapshot(
            project_id=project_id,
            project_title=self.detail.title,
            chapter_number=chapter_number,
            total_chapters=self.detail.total_chapters or 0,
        )


def _service(
    tmp_path: Path,
    detail: ProjectDetail,
    *,
    jobs: StubJobs | None = None,
) -> tuple[EngineQueryService, StubProjects]:
    service = EngineQueryService(
        storage=FileSystemStorage(tmp_path),
        settings=SimpleNamespace(  # type: ignore[arg-type]
            tts_default_provider="mock",
            tts_default_model="mock-voice",
        ),
        jobs=jobs or StubJobs(),  # type: ignore[arg-type]
    )
    projects = StubProjects(detail)
    service._projects = projects  # type: ignore[assignment]
    return service, projects


def test_novel_query_selects_next_unfinished_chapter(tmp_path: Path) -> None:
    detail = ProjectDetail(
        project_id="book",
        mode="long",
        title="青瓦梦匙",
        total_chapters=20,
        completed_chapters=5,
        latest_chapter=5,
    )
    service, projects = _service(tmp_path, detail)
    # Outline must exist for the new typed-error branch to skip the
    # ``ProjectOutlineMissingError`` early-return.
    (tmp_path / "book").mkdir(parents=True, exist_ok=True)
    (tmp_path / "book" / "outline.json").write_text(
        '{"chapters": [{"chapter_number": 6, "title": "测试章"}]}',
        encoding="utf-8",
    )

    view = service.get_novel_studio("book")

    assert projects.requested_chapter == 6
    assert view.next_chapter == 6
    assert view.project_title == "青瓦梦匙"


def test_novel_query_raises_project_not_long(tmp_path: Path) -> None:
    detail = ProjectDetail(
        project_id="short",
        mode="short",
        title="短篇",
    )
    service, _projects = _service(tmp_path, detail)

    with pytest.raises(ProjectNotLongError) as exc_info:
        service.get_novel_studio("short")

    assert "短篇项目" in str(exc_info.value) or "不是长篇" in str(exc_info.value)
    assert exc_info.value.code == "project_not_long"


def test_novel_query_degrades_when_outline_missing(tmp_path: Path) -> None:
    detail = ProjectDetail(
        project_id="outlineless",
        mode="long",
        title="缺大纲",
        total_chapters=10,
        completed_chapters=5,
    )
    service, projects = _service(tmp_path, detail)
    # Simulate the real get_chapter_workspace_snapshot raising when no outline.
    projects.snapshot_error = ValueError("Project 'outlineless' has no outline")

    view = service.get_novel_studio("outlineless")

    # Should return a degraded view, not raise.
    assert view.project_id == "outlineless"
    assert view.chapters == []
    assert "降级" in view.plan_summary
    assert view.next_chapter == 6  # completed_chapters + 1
    assert view.total_chapters == 10


def test_novel_query_degrades_when_chapter_out_of_range(tmp_path: Path) -> None:
    detail = ProjectDetail(
        project_id="book",
        mode="long",
        title="青瓦梦匙",
        total_chapters=5,
        completed_chapters=3,
        latest_chapter=3,
    )
    service, projects = _service(tmp_path, detail)
    (tmp_path / "book").mkdir(parents=True, exist_ok=True)
    # Outline only contains chapter 1 and 2.
    (tmp_path / "book" / "outline.json").write_text(
        '{"chapters": [{"chapter_number": 1, "title": "1"}, {"chapter_number": 2, "title": "2"}]}',
        encoding="utf-8",
    )
    # Simulate the real ``get_chapter_workspace_snapshot`` behaviour: it
    # raises ``ValueError`` when the requested chapter is not in the outline.
    projects.snapshot_error = ValueError("Chapter 99 not found in outline")

    view = service.get_novel_studio("book", chapter_number=99)

    # Should return a degraded view, not raise.
    assert view.project_id == "book"
    assert view.chapters == []
    assert "降级" in view.plan_summary


def test_voice_query_reads_validated_project_artifacts(tmp_path: Path) -> None:
    detail = ProjectDetail(
        project_id="book",
        mode="long",
        title="青瓦梦匙",
        latest_chapter=6,
    )
    service, _projects = _service(tmp_path, detail)
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_path("book"))
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="shen_an",
                character_name="沈岸",
                voice_id="voice-1",
                clone_status=VoiceCloneStatus.READY,
                approval_status="approved",
            )
        ],
        default_tts_model="speech-test",
    )
    script = DubbingScript(
        chapter_number=6,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="银链在灯下泛着冷光。",
            )
        ],
    )
    storage.save_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    storage.save_json(layout.tts_dubbing_script_path(6), script.model_dump(mode="json"))

    view = service.get_voice_studio("book")

    assert view.chapter_number == 6
    assert view.configured_model_label == "speech-test"
    assert view.cast[0].name == "沈岸"
    assert view.script[0].content == "银链在灯下泛着冷光。"


def test_voice_query_blocks_stale_script_and_audio_after_novel_revision(tmp_path: Path) -> None:
    detail = ProjectDetail(project_id="book", mode="long", title="青瓦梦匙", latest_chapter=6)
    service, _projects = _service(tmp_path, detail)
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_path("book"))
    layout.ensure_dirs()
    current_text = "这是改写后的当前终稿。"
    layout.chapter_path(6).write_text(current_text, encoding="utf-8")
    stale_script = DubbingScript(
        chapter_number=6,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="这是改写前的旧配音稿。",
            )
        ],
        source_text_hash=compute_source_text_hash("这是改写前的旧终稿。"),
    )
    stale_script.script_hash = compute_dubbing_script_hash(stale_script)
    audio = ChapterAudioResult(
        chapter_number=6,
        script=stale_script,
        assembled_audio_path=str(layout.tts_audio_dir(6) / "chapter.mp3"),
        subtitle_path=str(layout.tts_audio_dir(6) / "chapter.srt"),
        delivery_ready=True,
        metadata={
            "source_text_hash": stale_script.source_text_hash,
            "script_hash": stale_script.script_hash,
        },
    )
    storage.save_json(layout.tts_dubbing_script_path(6), stale_script.model_dump(mode="json"))
    storage.save_json(layout.tts_audio_result_path(6), audio.model_dump(mode="json"))

    view = service.get_voice_studio("book")

    assert view.novel_source_state == "ready"
    assert view.script_freshness == "stale"
    assert view.audio_freshness == "stale"
    assert view.script_fresh is False
    assert view.audio_ready is False
    assert view.subtitle_ready is False
    assert view.chapter_audio_url is None
    assert view.delivery_state == "blocked"
    assert view.freshness_blocking_reasons == [
        "stale_dubbing_script",
        "stale_tts_audio_result",
    ]


def test_voice_query_allows_current_compact_tts_lineage(tmp_path: Path) -> None:
    detail = ProjectDetail(project_id="book", mode="long", title="青瓦梦匙", latest_chapter=6)
    service, _projects = _service(tmp_path, detail)
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_path("book"))
    layout.ensure_dirs()
    current_text = "当前小说终稿与配音一致。"
    layout.chapter_path(6).write_text(current_text, encoding="utf-8")
    script = DubbingScript(
        chapter_number=6,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text=current_text,
            )
        ],
        source_text_hash=compute_source_text_hash(current_text),
    )
    script.script_hash = compute_dubbing_script_hash(script)
    audio = ChapterAudioResult(
        chapter_number=6,
        script=script,
        assembled_audio_path=str(layout.tts_audio_dir(6) / "chapter.mp3"),
        subtitle_path=str(layout.tts_audio_dir(6) / "chapter.srt"),
        delivery_ready=True,
        metadata={
            "source_text_hash": script.source_text_hash,
            "script_hash": script.script_hash,
        },
    )
    storage.save_json(layout.tts_dubbing_script_path(6), script.model_dump(mode="json"))
    storage.save_json(layout.tts_audio_result_path(6), audio.model_dump(mode="json"))

    view = service.get_voice_studio("book")

    assert view.script_freshness == "current"
    assert view.audio_freshness == "current"
    assert view.script_fresh is True
    assert view.audio_ready is True
    assert view.subtitle_ready is True
    assert view.chapter_audio_url is not None
    assert view.delivery_state == "ready"


def test_voice_query_projects_all_active_durable_tasks_for_restart_recovery(tmp_path: Path) -> None:
    detail = ProjectDetail(project_id="book", mode="long", title="青瓦梦匙", latest_chapter=6)
    jobs = StubJobs(
        [
            SimpleNamespace(job_id="voice-team", kind="tts_build_voice_team", status="running"),
            SimpleNamespace(job_id="voice-script", kind="tts_generate_script", status="paused"),
            SimpleNamespace(job_id="voice-synthesis", kind="tts_synthesize", status="queued"),
        ]
    )
    service, _projects = _service(tmp_path, detail, jobs=jobs)

    view = service.get_voice_studio("book")

    assert [(task.id, task.kind) for task in view.active_tasks] == [
        ("voice-team", "team_build"),
        ("voice-script", "script_generation"),
        ("voice-synthesis", "synthesis"),
    ]
    assert view.active_task_id == "voice-synthesis"
    assert view.delivery_state == "in_progress"


def test_voice_query_rejects_invalid_persisted_contract(tmp_path: Path) -> None:
    detail = ProjectDetail(project_id="book", mode="long", title="青瓦梦匙")
    service, _projects = _service(tmp_path, detail)
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_path("book"))
    layout.ensure_dirs()
    storage.save_json(layout.tts_voice_team_path, {"entries": [{"character_id": ""}]})

    with pytest.raises(ValueError, match="Voice team artifact is invalid"):
        service.get_voice_studio("book", chapter_number=1)

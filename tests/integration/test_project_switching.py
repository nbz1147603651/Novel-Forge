"""Integration tests for project switching flow in ChapterStudioCoordMixin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.workspace.contracts import ChapterWorkspaceChapter, ChapterWorkspaceSnapshot

with (
    patch("novel_forge.core.config.get_settings") as _mock_get_settings,
    patch("novel_forge.desktop.window.DesktopWorkspaceService") as _mock_ws_cls,
):
    _mock_settings = MagicMock()
    _mock_settings.outline_batch_size = 3
    _mock_settings.short_max_edit_rounds = 2
    _mock_settings.long_alignment_threshold = 7.0
    _mock_settings.long_plot_guard_mode = "balanced"
    _mock_settings.long_volume_auto_chapter_threshold = 60
    _mock_settings.long_default_chapters_per_volume = 20
    _mock_settings.long_plan_beats_min = 4
    _mock_settings.long_plan_beats_max = 8
    _mock_settings.long_plan_beat_max_chars = 260
    _mock_settings.long_prompt_max_character_profiles = 8
    _mock_settings.long_prompt_max_profile_field_chars = 240
    _mock_settings.auto_introduce_characters = False
    _mock_settings.long_polish_enabled = False
    _mock_settings.style_profile_enabled = True
    _mock_settings.style_profile_required = False
    _mock_settings.temp_spec_enrich = 0.7
    _mock_settings.temp_beats = 0.7
    _mock_settings.temp_draft = 0.8
    _mock_settings.temp_edit = 0.5
    _mock_settings.temp_evaluate = 0.3
    _mock_settings.temp_init_story_bible = 0.7
    _mock_settings.temp_init_character_bible = 0.7
    _mock_settings.temp_blueprint_element_select = 0.2
    _mock_settings.temp_plan_outline = 0.7
    _mock_settings.temp_plan_outline_batch = 0.7
    _mock_settings.temp_plan_outline_continue = 0.7
    _mock_settings.temp_plan_chapter = 0.4
    _mock_settings.temp_draft_chapter = 1.0
    _mock_settings.temp_edit_chapter = 0.5
    _mock_settings.temp_extract_canon = 0.3
    _mock_settings.temp_check_alignment = 0.2
    _mock_settings.temp_check_chapter = 0.2
    _mock_settings.temp_bridge_chapter = 0.3
    _mock_settings.temp_check_continuity = 0.2
    _mock_settings.temp_validate_causal = 0.2
    _mock_settings.temp_patch_chapter = 0.2
    _mock_settings.temp_repair_continuity = 0.4
    _mock_settings.temp_repair_causal = 0.35
    _mock_settings.temp_repair_reading_power = 0.3
    _mock_settings.temp_volume_audit = 0.3
    _mock_settings.temp_enrich_character = 0.7
    _mock_settings.temp_introduce_character = 0.75
    _mock_settings.temp_profile_style = 0.4
    _mock_settings.temp_profile_structure = 0.4
    _mock_settings.temp_evaluate_reading_power = 0.3
    _mock_settings.temp_adjust_outline = 0.7
    _mock_settings.temp_context_compress = 0.2
    _mock_settings.temp_verify_compression = 0.2
    _mock_settings.temp_extract_motifs = 0.3
    _mock_settings.temp_summarize_chapter = 0.3
    _mock_settings.temp_summarize_volume = 0.3
    _mock_settings.temp_summarize_arc = 0.3
    _mock_settings.temp_summarize_scene = 0.3
    _mock_settings.temp_critic_continuity = 0.2
    _mock_settings.temp_critic_character = 0.2
    _mock_settings.temp_critic_causal = 0.2
    _mock_settings.temp_critic_strengths = 0.3
    _mock_settings.temp_plot_guard_judge = 0.2
    _mock_settings.temp_book_consistency = 0.2
    _mock_settings.routes = {}
    _mock_settings.fallback_routes = {}
    _mock_settings.profiles = []
    _mock_settings.ollama_base_url = "http://localhost:11434/v1"
    _mock_settings.ollama_model = "llama3.2"
    _mock_settings.ollama_embedding_model = "nomic-embed-text"
    _mock_settings.log_level = "INFO"
    _mock_settings.log_keep_runs = 30
    _mock_settings.api_call_timeout_s = 120
    _mock_settings.long_prompt_max_relationships_per_profile = 6
    _mock_settings.canon_context_max_recent_events = 20
    _mock_settings.canon_context_max_characters = 10
    _mock_settings.canon_context_max_foreshadowing = 10
    _mock_settings.long_plan_max_foreshadowing = 10
    _mock_settings.canon_context_max_world_facts = 50
    _mock_settings.long_context_compress_enabled = True
    _mock_settings.long_context_compress_min_chars = 1000
    _mock_settings.long_context_compress_max_tokens = 2048
    _mock_settings.long_ai_judge_max_context_chapters = 24
    _mock_settings.long_ai_judge_max_tokens = 1536
    _mock_settings.long_ai_judge_apply_entity_actions = True
    _mock_settings.long_continuity_repair_threshold = 0.65
    _mock_settings.long_continuity_max_repair_rounds = 3
    _mock_settings.forbidden_elements_cross_chapter_window = 5
    _mock_settings.long_causal_repair_enabled = True
    _mock_settings.long_causal_threshold = 0.6
    _mock_settings.long_causal_max_repair_rounds = 3
    _mock_settings.max_auto_repair_attempts = 3
    _mock_settings.repair_must_fix_severity = "warning"
    _mock_settings.recheck_strategy = "failed-only"
    _mock_settings.change_budget_threshold = 30
    _mock_settings.repair_display_min_severity = "info"
    _mock_settings.repair_always_reaudit = False
    _mock_settings.long_check_chapter_enabled = True
    _mock_settings.long_max_consistency_replans = 2
    _mock_settings.local_check_as_prescreen = True
    _mock_settings.local_check_confidence_threshold = 0.7
    _mock_settings.local_guardrails_trust_level = "medium"
    _mock_settings.pronoun_autofix_mode = "auto"
    _mock_settings.element_progress_llm_arbiter_enabled = True
    _mock_settings.element_progress_llm_arbiter_max_items_per_chapter = 3
    _mock_settings.element_progress_llm_arbiter_max_tokens = 512
    _mock_settings.element_progress_llm_arbiter_temperature = 0.3
    _mock_settings.reading_power_window_size = 5
    _mock_settings.reading_power_window_left_offset = 2
    _mock_settings.reading_power_window_right_offset = 2
    _mock_settings.reading_power_suspense_delay_threshold = 3
    _mock_settings.reading_power_force_resolve_threshold = 5
    _mock_settings.reading_power_hook_alternation_threshold = 3
    _mock_settings.reading_power_tension_deviation_tolerance = 2
    _mock_settings.reading_power_enabled = True
    _mock_settings.memory_episodic_enabled = True
    _mock_settings.memory_embedding_profile_id = "auto"
    _mock_settings.memory_semantic_search_enabled = True
    _mock_settings.memory_multi_granularity_summary_enabled = True
    _mock_settings.memory_adaptive_compression_enabled = True
    _mock_settings.memory_motif_tracking_enabled = True
    _mock_settings.memory_motif_check_repetition = True
    _mock_settings.motif_suggestion_min_chapters = 5
    _mock_settings.motif_suggestion_min_occurrences = 3
    _mock_settings.memory_motif_related_lookback_chapters = 10
    _mock_settings.memory_critic_agent_enabled = False
    _mock_settings.memory_critic_agent_run_async = True
    _mock_settings.memory_critic_agent_timeout_s = 300
    _mock_settings.memory_critic_agent_timeout_extend_attempts = 2
    _mock_settings.memory_critic_agent_timeout_extend_multiplier = 1.5
    _mock_settings.memory_critic_agent_cache_enabled = True
    _mock_settings.memory_critic_agent_cache_max_entries = 100
    _mock_settings.memory_concurrent_indexing = True
    _mock_get_settings.return_value = _mock_settings
    _mock_ws = MagicMock()
    _mock_ws.build_snapshot.return_value = MagicMock(
        storage_root=Path("/tmp/fake_workspace"),
        default_provider="mock",
        overview=MagicMock(providers=[]),
        metrics=MagicMock(
            total_projects=0,
            total_chapters=0,
            total_words=0,
            configured_providers=0,
        ),
        providers=[],
        projects=[],
        featured_project=None,
        details={},
    )
    _mock_ws_cls.from_settings.return_value = _mock_ws

    from novel_forge.desktop.window import NovelForgeDesktopWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_job(
    job_id: str,
    project_id: str,
    status: DesktopJobState,
    chapter_number: int = 1,
    kind: str = "run_chapter",
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=job_id,
        kind=kind,
        label=f"章节续写 · {project_id} / 第 {chapter_number} 章",
        project_id=project_id,
        status=status,
        result={"chapter_number": chapter_number},
    )


def _make_snapshot(projects):
    return MagicMock(
        storage_root=Path("/tmp/fake_workspace"),
        default_provider="mock",
        overview=MagicMock(providers=[]),
        metrics=MagicMock(
            total_projects=0, total_chapters=0, total_words=0, configured_providers=0
        ),
        providers=[],
        projects=projects,
        featured_project=None,
        details={},
    )


def _make_studio_snapshot(
    project_id: str,
    *,
    chapter_number: int,
    total_chapters: int,
) -> ChapterWorkspaceSnapshot:
    chapters = [
        ChapterWorkspaceChapter(
            chapter_number=1,
            title="第一章",
            status="done",
            status_label="已完成",
        ),
        ChapterWorkspaceChapter(
            chapter_number=2,
            title="第二章",
            status="done",
            status_label="已完成",
        ),
        ChapterWorkspaceChapter(
            chapter_number=chapter_number,
            title=f"第 {chapter_number} 章",
            status="current",
            status_label="当前章",
        ),
    ]
    return ChapterWorkspaceSnapshot(
        project_id=project_id,
        project_title=project_id,
        chapter_number=chapter_number,
        total_chapters=total_chapters,
        chapters=chapters,
        current_title=f"第 {chapter_number} 章",
    )


class TestProjectSwitching:
    @pytest.fixture
    def window(self, qapp, monkeypatch):
        monkeypatch.setattr(
            "novel_forge.desktop.window.DesktopWorkspaceService",
            _mock_ws_cls,
        )

        win = NovelForgeDesktopWindow()

        self._shutdown_calls: dict[str, bool] = {}
        for page_id, page in win._pages.items():
            original = getattr(page, "shutdown", None)

            def make_spy(pid=page_id, orig=original):
                def _spy() -> None:
                    self._shutdown_calls[pid] = True
                    if orig is not None:
                        orig()

                return _spy

            monkeypatch.setattr(page, "shutdown", make_spy(), raising=False)

        yield win
        win._pre_close_cleanup()

    @pytest.fixture
    def studio_page(self, window):
        window.switch_page("chapter_studio")
        return window._pages["chapter_studio"]

    def test_switch_with_active_jobs_shows_dialog(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="project_a", mode="long", next_chapter=1),
                    MagicMock(project_id="project_b", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("project_a")
        studio_page._previous_project = "project_a"

        active_job = _make_job(
            job_id="job-1",
            project_id="project_a",
            status=DesktopJobState.RUNNING,
            chapter_number=3,
        )
        studio_page.bind_jobs([active_job])

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            return_value=True,
        ):
            studio_page._project_combo.setCurrentText("project_b")

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)

    def test_switch_with_active_jobs_cancel_restores_selection(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="project_x", mode="long", next_chapter=1),
                    MagicMock(project_id="project_y", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("project_x")
        studio_page._previous_project = "project_x"

        active_job = _make_job(
            job_id="job-2",
            project_id="project_x",
            status=DesktopJobState.RUNNING,
            chapter_number=5,
        )
        studio_page.bind_jobs([active_job])

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            return_value=False,
        ):
            studio_page._project_combo.setCurrentText("project_y")

            assert studio_page._project_combo.currentText() == "project_x"

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)

    def test_switch_without_active_jobs_no_dialog(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="proj_no_jobs", mode="long", next_chapter=1),
                    MagicMock(project_id="proj_target", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("proj_no_jobs")
        studio_page._previous_project = "proj_no_jobs"

        studio_page.bind_jobs([])

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            side_effect=AssertionError("Dialog should not be shown when no active jobs"),
        ):
            studio_page._project_combo.setCurrentText("proj_target")

            assert studio_page._project_combo.currentText() == "proj_target"

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)

    def test_background_jobs_continue_after_switch(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="old_project", mode="long", next_chapter=1),
                    MagicMock(project_id="new_project", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("old_project")
        studio_page._previous_project = "old_project"

        running_job = _make_job(
            job_id="job-running",
            project_id="old_project",
            status=DesktopJobState.RUNNING,
            chapter_number=2,
        )
        queued_job = _make_job(
            job_id="job-queued",
            project_id="new_project",
            status=DesktopJobState.QUEUED,
            chapter_number=1,
        )
        all_jobs = [running_job, queued_job]

        studio_page.bind_jobs(all_jobs)

        active_projects = studio_page._get_projects_with_active_jobs(all_jobs)
        assert "old_project" in active_projects
        assert "new_project" in active_projects

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            return_value=True,
        ):
            studio_page._project_combo.setCurrentText("new_project")

        assert studio_page._all_jobs is not None
        assert len(studio_page._all_jobs) == 2

        old_project_jobs = [j for j in studio_page._all_jobs if j.project_id == "old_project"]
        assert len(old_project_jobs) == 1
        assert old_project_jobs[0].status == DesktopJobState.RUNNING

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)

    def test_switching_project_isolates_rail_jobs_and_mode_state(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="project_a", mode="long", next_chapter=2),
                    MagicMock(project_id="project_b", mode="long", next_chapter=61),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("project_a")
        studio_page._previous_project = "project_a"
        studio_page._state.current_project_id = "project_a"
        studio_page._mode = studio_page.MODE_BOOK_AUTO
        studio_page._auto_started = True
        studio_page._mode_selector.set_mode(studio_page._MODE_INDEX[studio_page.MODE_BOOK_AUTO])

        running_job = _make_job(
            job_id="job-project-a-running",
            project_id="project_a",
            status=DesktopJobState.RUNNING,
            chapter_number=2,
        )
        studio_page.bind_jobs([running_job])
        assert studio_page._rail._chapter_job_status == {2: DesktopJobState.RUNNING}

        studio_page._project_combo.setCurrentText("project_b")

        assert studio_page._state.current_project_id == "project_b"
        assert studio_page._auto_started is False
        assert studio_page._mode_selector.current_mode() == studio_page._MODE_INDEX[
            studio_page.MODE_MANUAL
        ]
        assert studio_page._rail._chapter_job_status == {}

        studio_page._chapter_spin.blockSignals(True)
        studio_page._chapter_spin.setValue(61)
        studio_page._chapter_spin.blockSignals(False)
        monkeypatch.setattr(studio_page, "_project_layout_from_workspace", lambda _project_id: None)
        studio_page.bind_studio(
            _make_studio_snapshot("project_b", chapter_number=61, total_chapters=82)
        )

        assert studio_page._rail._chapter_job_status == {}

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)

    def test_switch_with_queued_job_shows_dialog(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="src_queued", mode="long", next_chapter=1),
                    MagicMock(project_id="dst_queued", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("src_queued")
        studio_page._previous_project = "src_queued"

        queued_job = _make_job(
            job_id="job-queued-test",
            project_id="src_queued",
            status=DesktopJobState.QUEUED,
            chapter_number=4,
        )
        studio_page.bind_jobs([queued_job])

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            return_value=True,
        ):
            studio_page._project_combo.setCurrentText("dst_queued")

            assert studio_page._project_combo.currentText() == "dst_queued"

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)

    def test_switch_to_same_project_no_dialog(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="same_proj", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("same_proj")
        studio_page._previous_project = "same_proj"

        studio_page.bind_jobs([])

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            side_effect=AssertionError("Dialog should not be shown for same project"),
        ):
            studio_page._project_combo.setCurrentText("same_proj")

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)

    def test_switch_from_terminal_jobs_no_dialog(self, studio_page, monkeypatch):
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="terminal_jobs_proj", mode="long", next_chapter=1),
                    MagicMock(project_id="target_proj", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._project_combo.setCurrentText("terminal_jobs_proj")
        studio_page._previous_project = "terminal_jobs_proj"

        succeeded_job = _make_job(
            job_id="job-succeeded",
            project_id="terminal_jobs_proj",
            status=DesktopJobState.SUCCEEDED,
            chapter_number=1,
        )
        failed_job = _make_job(
            job_id="job-failed",
            project_id="terminal_jobs_proj",
            status=DesktopJobState.FAILED,
            chapter_number=2,
        )
        studio_page.bind_jobs([succeeded_job, failed_job])

        active_projects = studio_page._get_projects_with_active_jobs([succeeded_job, failed_job])
        assert "terminal_jobs_proj" not in active_projects

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            side_effect=AssertionError("Dialog should not be shown for terminal jobs"),
        ):
            studio_page._project_combo.setCurrentText("target_proj")

            assert studio_page._project_combo.currentText() == "target_proj"

        studio_page._project_combo.blockSignals(True)
        studio_page._project_combo.setCurrentText("")
        studio_page._project_combo.blockSignals(False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

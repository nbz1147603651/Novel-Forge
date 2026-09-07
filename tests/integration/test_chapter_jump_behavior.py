"""Integration tests for chapter auto-jump behavior.

Tests the following scenarios:
1. Default behavior (follow_autorun=False): auto-advance does NOT force UI jump
2. follow_autorun=True: auto-advance DOES force UI jump
3. Project switch: background book auto-run keeps moving for old project
4. Jobs refresh timing after chapter switch
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState

# ─── Mock Settings Setup ────────────────────────────────────────────────────────


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
    _mock_settings.long_auto_chapter_cooldown_seconds = 0
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

    from novel_forge.desktop.workspace import DesktopWorkspaceMetrics, DesktopWorkspaceSnapshot

    _mock_ws = MagicMock()
    _mock_ws.build_snapshot.return_value = DesktopWorkspaceSnapshot(
        storage_root=Path("/tmp/fake_workspace"),
        default_provider="mock",
        overview=MagicMock(providers=[]),
        metrics=DesktopWorkspaceMetrics(
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


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(
        "novel_forge.desktop.window.DesktopWorkspaceService",
        _mock_ws_cls,
    )
    win = NovelForgeDesktopWindow()
    yield win
    win._pre_close_cleanup()


@pytest.fixture
def studio_page(window):
    window.switch_page("chapter_studio")
    return window._pages["chapter_studio"]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_snapshot(projects):
    from novel_forge.desktop.workspace import DesktopWorkspaceMetrics, DesktopWorkspaceSnapshot

    return DesktopWorkspaceSnapshot(
        storage_root=Path("/tmp/fake_workspace"),
        default_provider="mock",
        overview=MagicMock(providers=[]),
        metrics=DesktopWorkspaceMetrics(
            total_projects=0, total_chapters=0, total_words=0, configured_providers=0
        ),
        providers=[],
        projects=projects,
        featured_project=None,
        details={},
    )


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


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestChapterAutoJumpBehavior:
    """Tests for chapter auto-jump behavior based on follow_autorun setting."""

    def test_default_follow_autorun_is_false(self, studio_page):
        """follow_autorun defaults to False on fresh state."""
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="test_project", mode="long", next_chapter=1),
                ]
            )
        )
        studio_page._project_combo.setCurrentText("test_project")
        studio_page._previous_project = "test_project"
        studio_page._state.current_project_id = "test_project"

        assert studio_page._should_follow_autorun() is False

    def test_follow_autorun_property_reflects_state(
        self, studio_page
    ):
        """The follow_autorun property correctly reflects per-project state."""
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="proj_a", mode="long", next_chapter=1),
                    MagicMock(project_id="proj_b", mode="long", next_chapter=1),
                ]
            )
        )
        studio_page._project_combo.setCurrentText("proj_a")
        studio_page._previous_project = "proj_a"
        studio_page._state.current_project_id = "proj_a"

        assert studio_page._should_follow_autorun() is False

        studio_page._state.follow_autorun = True
        assert studio_page._should_follow_autorun() is True

        studio_page._state.current_project_id = "proj_b"
        assert studio_page._should_follow_autorun() is False

        studio_page._state.current_project_id = "proj_a"
        assert studio_page._should_follow_autorun() is True

    def test_auto_advance_chapter_respects_follow_autorun(
        self, studio_page, window, monkeypatch
    ):
        """When follow_autorun=False, focus_project is NOT called on auto-advance."""
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="auto_jump_test", mode="long", next_chapter=1),
                ]
            )
        )
        studio_page._project_combo.setCurrentText("auto_jump_test")
        studio_page._previous_project = "auto_jump_test"
        studio_page._state.current_project_id = "auto_jump_test"
        studio_page._state.follow_autorun = False

        studio_page._studio = MagicMock(
            project_id="auto_jump_test",
            chapter_number=1,
        )

        focus_called = []
        original_focus = studio_page.focus_project

        def spy_focus(pid, ch=None):
            focus_called.append((pid, ch))
            return original_focus(pid, ch)

        monkeypatch.setattr(studio_page, "focus_project", spy_focus)

        drive_calls = []
        monkeypatch.setattr(
            "novel_forge.desktop.window.autorun.QTimer.singleShot",
            lambda _delay, callback: drive_calls.append(callback),
        )

        window._auto_advance_chapter("auto_jump_test", 2)

        assert len(focus_called) == 0, (
            f"focus_project should NOT be called when follow_autorun=False, "
            f"but was called with {focus_called}"
        )

        assert window._chapter_studio_project_id == "auto_jump_test"
        assert window._get_chapter_number("auto_jump_test") == 2
        assert len(drive_calls) == 1

    def test_auto_advance_chapter_forces_jump_when_follow_enabled(
        self, studio_page, window, monkeypatch
    ):
        """When follow_autorun=True, focus_project IS called on auto-advance."""
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="follow_test", mode="long", next_chapter=1),
                ]
            )
        )
        studio_page._project_combo.setCurrentText("follow_test")
        studio_page._previous_project = "follow_test"
        studio_page._state.current_project_id = "follow_test"
        studio_page._state.follow_autorun = True

        studio_page._studio = MagicMock(
            project_id="follow_test",
            chapter_number=1,
        )

        focus_called = []
        original_focus = studio_page.focus_project

        def spy_focus(pid, ch=None):
            focus_called.append((pid, ch))
            return original_focus(pid, ch)

        monkeypatch.setattr(studio_page, "focus_project", spy_focus)

        window._auto_advance_chapter("follow_test", 2)

        assert len(focus_called) == 1, (
            f"focus_project SHOULD be called when follow_autorun=True, "
            f"but was called {len(focus_called)} times"
        )
        assert focus_called[0] == ("follow_test", 2)

    def test_auto_advance_uses_target_project_follow_switch(
        self, studio_page, window, monkeypatch
    ):
        """Background advances read the target project's live follow switch."""
        from novel_forge.desktop.pages.chapter_studio.state import AutoPilotProjectState

        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="proj_a", mode="long", next_chapter=1),
                    MagicMock(project_id="proj_b", mode="long", next_chapter=1),
                ]
            )
        )
        studio_page._state.current_project_id = "proj_b"
        studio_page._state.set_auto_pilot_state(
            "proj_a",
            AutoPilotProjectState(
                auto_started=True,
                mode=studio_page.MODE_BOOK_AUTO,
                follow_autorun=True,
            ),
        )
        studio_page._state.set_auto_pilot_state(
            "proj_b",
            AutoPilotProjectState(
                auto_started=True,
                mode=studio_page.MODE_BOOK_AUTO,
                follow_autorun=False,
            ),
        )

        focus_calls: list[tuple[str, int | None]] = []
        monkeypatch.setattr(
            studio_page,
            "focus_project",
            lambda project_id, chapter=None: focus_calls.append((project_id, chapter)),
        )

        window._auto_advance_chapter("proj_a", 2)
        assert focus_calls == [("proj_a", 2)]

        focus_calls.clear()
        studio_page._state.set_auto_pilot_state(
            "proj_a",
            AutoPilotProjectState(
                auto_started=True,
                mode=studio_page.MODE_BOOK_AUTO,
                follow_autorun=False,
            ),
        )
        window._auto_advance_chapter("proj_a", 3)
        assert focus_calls == []


class TestProjectSwitchKeepsBackgroundAutoPilot:
    """Tests that project switching does not stop background book auto-run."""

    def test_book_auto_project_switch_keeps_previous_project_running(
        self, studio_page, monkeypatch
    ):
        """Book auto-run stays active for the previous project on manual project switch."""
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
        studio_page._state.current_project_id = "old_project"

        running_job = _make_job(
            job_id="job-running-old",
            project_id="old_project",
            status=DesktopJobState.RUNNING,
            chapter_number=3,
        )
        studio_page.bind_jobs([running_job])

        studio_page._state.auto_started = True
        studio_page._state.mode = studio_page.MODE_BOOK_AUTO

        stop_calls = []

        def spy_stop(reason="", cancel_jobs=True):
            stop_calls.append({"reason": reason, "cancel_jobs": cancel_jobs})

        monkeypatch.setattr(studio_page, "_stop_auto_pilot", spy_stop)

        cancel_signals = []
        studio_page.cancel_job_requested.connect(
            lambda job_id, reason: cancel_signals.append({"job_id": job_id, "reason": reason})
        )
        dialog_calls = []

        def _dialog_exec_should_not_run(*_args, **_kwargs):
            dialog_calls.append(True)
            return True

        with patch(
            "novel_forge.desktop.pages.chapter_studio.dialogs.ProjectSwitchConfirmDialog.exec",
            _dialog_exec_should_not_run,
        ):
            studio_page._project_combo.setCurrentText("new_project")

        assert stop_calls == []
        assert len(cancel_signals) == 0, (
            f"cancel_job_requested should NOT be emitted when background auto-run continues, "
            f"but got {cancel_signals}"
        )
        assert dialog_calls == []
        assert studio_page._state.auto_pilot_state_for("old_project").auto_started is True

    def test_auto_pilot_state_is_per_project(
        self, studio_page
    ):
        """Auto-pilot state is isolated per project."""
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="proj_x", mode="long", next_chapter=1),
                    MagicMock(project_id="proj_y", mode="long", next_chapter=1),
                ]
            )
        )

        studio_page._state.current_project_id = "proj_x"
        studio_page._state.auto_started = True

        studio_page._state.current_project_id = "proj_y"
        assert studio_page._state.auto_started is False

        studio_page._state.current_project_id = "proj_x"
        assert studio_page._state.auto_started is True


class TestBackgroundAutoRunDriver:
    """Tests for window-level auto-run driving across projects."""

    def test_drive_active_autoruns_submits_each_project_without_focus(
        self, studio_page, window, monkeypatch
    ):
        from novel_forge.desktop.pages.chapter_studio.state import AutoPilotProjectState
        from novel_forge.desktop.workspace import DesktopWorkspaceMetrics, DesktopWorkspaceSnapshot
        from novel_forge.workspace.contracts import (
            ChapterWorkspaceChapter,
            ChapterWorkspaceSnapshot,
        )

        window._snapshot = DesktopWorkspaceSnapshot(
            storage_root=Path("/tmp/fake_workspace"),
            default_provider="mock",
            overview=MagicMock(providers=[]),
            metrics=DesktopWorkspaceMetrics(
                total_projects=2,
                total_chapters=0,
                total_words=0,
                configured_providers=0,
            ),
            providers=[],
            projects=[
                MagicMock(project_id="proj_a", mode="long", next_chapter=1),
                MagicMock(project_id="proj_b", mode="long", next_chapter=1),
            ],
            featured_project=None,
            details={
                "proj_a": MagicMock(mode="long"),
                "proj_b": MagicMock(mode="long"),
            },
        )

        def _chapter_snapshot(project_id: str, chapter_number: int) -> ChapterWorkspaceSnapshot:
            return ChapterWorkspaceSnapshot(
                project_id=project_id,
                project_title=project_id,
                chapter_number=chapter_number,
                total_chapters=3,
                chapters=[
                    ChapterWorkspaceChapter(
                        chapter_number=chapter_number,
                        status="current",
                    ),
                    ChapterWorkspaceChapter(
                        chapter_number=chapter_number + 1,
                        status="pending",
                    ),
                ],
                current_title=f"第{chapter_number}章",
            )

        window._workspace = MagicMock()
        window._workspace.get_chapter_workspace_snapshot.side_effect = _chapter_snapshot
        window._latest_jobs = []
        window._set_chapter_number("proj_a", 1)
        window._set_chapter_number("proj_b", 1)

        studio_page._state.set_auto_pilot_state(
            "proj_a",
            AutoPilotProjectState(auto_started=True, mode=studio_page.MODE_BOOK_AUTO),
        )
        studio_page._state.set_auto_pilot_state(
            "proj_b",
            AutoPilotProjectState(auto_started=True, mode=studio_page.MODE_BOOK_AUTO),
        )

        submissions: list[tuple[str, int, bool]] = []

        def _submit_prepare(request, *, mock=False):
            submissions.append((request.project_id, request.chapter_number, mock))
            return MagicMock(job_id=f"prepare-{request.project_id}")

        focus_calls: list[tuple[str, int | None]] = []
        monkeypatch.setattr(window._job_manager, "submit_prepare_chapter", _submit_prepare)
        monkeypatch.setattr(
            studio_page,
            "focus_project",
            lambda project_id, chapter=None: focus_calls.append((project_id, chapter)),
        )

        window._drive_active_autoruns()

        assert sorted(submissions) == [
            ("proj_a", 1, window._mock_enabled),
            ("proj_b", 1, window._mock_enabled),
        ]
        assert focus_calls == []

    def test_background_autorun_respects_chapter_cooldown(
        self, studio_page, window, monkeypatch
    ):
        from novel_forge.desktop.pages.chapter_studio.state import AutoPilotProjectState
        from novel_forge.desktop.workspace import DesktopWorkspaceMetrics, DesktopWorkspaceSnapshot
        from novel_forge.workspace.contracts import (
            ChapterWorkspaceChapter,
            ChapterWorkspaceSnapshot,
        )

        window._snapshot = DesktopWorkspaceSnapshot(
            storage_root=Path("/tmp/fake_workspace"),
            default_provider="mock",
            overview=MagicMock(providers=[]),
            metrics=DesktopWorkspaceMetrics(
                total_projects=1,
                total_chapters=0,
                total_words=0,
                configured_providers=0,
            ),
            providers=[],
            projects=[MagicMock(project_id="proj_cool", mode="long", next_chapter=1)],
            featured_project=None,
            details={"proj_cool": MagicMock(mode="long")},
        )

        def _chapter_snapshot(project_id: str, chapter_number: int) -> ChapterWorkspaceSnapshot:
            status = "done" if chapter_number == 1 else "current"
            return ChapterWorkspaceSnapshot(
                project_id=project_id,
                project_title=project_id,
                chapter_number=chapter_number,
                total_chapters=3,
                chapters=[
                    ChapterWorkspaceChapter(chapter_number=chapter_number, status=status),
                    ChapterWorkspaceChapter(chapter_number=chapter_number + 1, status="pending"),
                ],
                current_title=f"第{chapter_number}章",
            )

        window._workspace = MagicMock()
        window._workspace.get_chapter_workspace_snapshot.side_effect = _chapter_snapshot
        window._latest_jobs = []
        window._set_chapter_number("proj_cool", 1)
        studio_page._state.set_auto_pilot_state(
            "proj_cool",
            AutoPilotProjectState(auto_started=True, mode=studio_page.MODE_BOOK_AUTO),
        )

        submissions: list[tuple[str, int]] = []
        scheduled: list[int] = []
        monkeypatch.setattr(
            "novel_forge.desktop.window.autorun.get_settings",
            lambda: SimpleNamespace(
                long_auto_chapter_cooldown_seconds=30,
                max_auto_repair_attempts=3,
            ),
        )
        monkeypatch.setattr(
            "novel_forge.desktop.window.autorun.QTimer.singleShot",
            lambda delay, _callback: scheduled.append(delay),
        )
        monkeypatch.setattr(
            window._job_manager,
            "submit_prepare_chapter",
            lambda request, *, mock=False: submissions.append(
                (request.project_id, request.chapter_number)
            )
            or MagicMock(job_id="prepare-after-cooldown"),
        )

        window._drive_project_autorun("proj_cool")

        assert window._get_chapter_number("proj_cool") == 2
        assert submissions == []
        assert scheduled and scheduled[-1] >= 30_000

        window._drive_project_autorun("proj_cool")

        assert submissions == []
        assert window._workspace.get_chapter_workspace_snapshot.call_count == 1

        window._autorun_cooldown_until["proj_cool"] = 0
        window._drive_project_autorun("proj_cool")

        assert submissions == [("proj_cool", 2)]

    def test_background_autorun_submits_repair_before_reprepare(
        self, studio_page, window, monkeypatch
    ):
        from novel_forge.desktop.pages.chapter_studio.state import AutoPilotProjectState
        from novel_forge.desktop.workspace import DesktopWorkspaceMetrics, DesktopWorkspaceSnapshot
        from novel_forge.workspace.contracts import (
            ChapterWorkspaceChapter,
            ChapterWorkspaceSnapshot,
        )

        window._snapshot = DesktopWorkspaceSnapshot(
            storage_root=Path("/tmp/fake_workspace"),
            default_provider="mock",
            overview=MagicMock(providers=[]),
            metrics=DesktopWorkspaceMetrics(
                total_projects=1,
                total_chapters=0,
                total_words=0,
                configured_providers=0,
            ),
            providers=[],
            projects=[MagicMock(project_id="proj_repair", mode="long", next_chapter=1)],
            featured_project=None,
            details={"proj_repair": MagicMock(mode="long")},
        )
        window._workspace = MagicMock()
        window._workspace.get_chapter_workspace_snapshot.return_value = ChapterWorkspaceSnapshot(
            project_id="proj_repair",
            project_title="proj_repair",
            chapter_number=1,
            total_chapters=3,
            chapters=[ChapterWorkspaceChapter(chapter_number=1, status="current")],
            current_title="第1章",
            continuity_issues=[
                {"severity": "critical", "summary": "状态承接断裂", "issue_type": "state"}
            ],
        )
        window._latest_jobs = [
            DesktopJobRecord(
                job_id="repair-prev",
                kind="repair_continuity",
                label="连贯性修复 · proj_repair / 第 1 章",
                project_id="proj_repair",
                status=DesktopJobState.SUCCEEDED,
                result={"chapter_number": 1, "applied": True},
            )
        ]
        window._set_chapter_number("proj_repair", 1)
        studio_page._state.set_auto_pilot_state(
            "proj_repair",
            AutoPilotProjectState(auto_started=True, mode=studio_page.MODE_BOOK_AUTO),
        )

        repair_submissions: list[tuple[str, int, list[int]]] = []
        prepare_submissions: list[tuple[str, int]] = []
        monkeypatch.setattr(
            "novel_forge.desktop.window.get_settings",
            lambda: SimpleNamespace(
                long_auto_chapter_cooldown_seconds=0,
                max_auto_repair_attempts=3,
            ),
        )
        monkeypatch.setattr(
            window._job_manager,
            "submit_repair_continuity",
            lambda request, *, mock=False: repair_submissions.append(
                (request.project_id, request.chapter_number, request.issue_indices)
            )
            or MagicMock(job_id="repair-next"),
        )
        monkeypatch.setattr(
            window._job_manager,
            "submit_prepare_chapter",
            lambda request, *, mock=False: prepare_submissions.append(
                (request.project_id, request.chapter_number)
            )
            or MagicMock(job_id="prepare-should-wait"),
        )

        window._drive_project_autorun("proj_repair")

        assert repair_submissions == [("proj_repair", 1, [0])]
        assert prepare_submissions == []
        assert studio_page._state.auto_repair_attempts[("proj_repair", 1)] == 1

    def test_autorun_start_stop_resets_window_transients(self, window):
        window._autorun_cooldown_until = {"proj_reset": 123.0, "other": 456.0}
        window._autorun_refresh_counts = {"proj_reset": 8, "other": 2}
        window._autorun_last_submitted_checkpoint = {
            ("proj_reset", 1): "old-checkpoint",
            ("other", 1): "keep-checkpoint",
        }
        window._autorun_prepared_chapters = {("proj_reset", 1), ("other", 1)}

        window._reset_project_autorun_transients("proj_reset")

        assert window._autorun_cooldown_until == {"other": 456.0}
        assert window._autorun_refresh_counts == {"other": 2}
        assert window._autorun_last_submitted_checkpoint == {
            ("other", 1): "keep-checkpoint"
        }
        assert window._autorun_prepared_chapters == {("other", 1)}


class TestJobsRefreshTiming:
    """Tests for jobs refresh timing after chapter switch."""

    def test_bind_studio_triggers_force_refresh(
        self, studio_page, monkeypatch
    ):
        """bind_studio with snapshot triggers jobs refresh via QTimer.singleShot."""
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="refresh_test", mode="long", next_chapter=1),
                ]
            )
        )
        studio_page._project_combo.setCurrentText("refresh_test")
        studio_page._previous_project = "refresh_test"

        timer_calls = []
        from PySide6.QtCore import QTimer

        def spy_single_shot(delay, callback):
            timer_calls.append((delay, callback))
            if callback == studio_page._force_refresh_jobs:
                callback()

        monkeypatch.setattr(QTimer, "singleShot", spy_single_shot)

        monkeypatch.setattr(studio_page, "_render_compass", lambda: None)
        monkeypatch.setattr(studio_page, "_render_rail", lambda: None)
        monkeypatch.setattr(studio_page, "_render_action_panel", lambda: None)
        monkeypatch.setattr(studio_page, "_render_artifacts", lambda: None)
        monkeypatch.setattr(studio_page, "_render_inspector", lambda: None)
        monkeypatch.setattr(studio_page, "_render_memory_panel", lambda: None)
        monkeypatch.setattr(studio_page, "_render_relationship_card", lambda: None)
        monkeypatch.setattr(studio_page, "_render_continuity_checklist", lambda: None)
        monkeypatch.setattr(studio_page, "_render_causal_checklist", lambda: None)

        studio_snapshot = MagicMock(
            project_id="refresh_test",
            chapter_number=1,
            outline=MagicMock(total_chapters=10),
            pending_checkpoint=None,
        )

        studio_page.bind_studio(studio_snapshot)

        force_refresh_calls = [c for (d, c) in timer_calls if c == studio_page._force_refresh_jobs]
        assert len(force_refresh_calls) == 1, (
            f"_force_refresh_jobs should be scheduled once when snapshot is not None, "
            f"was called {len(force_refresh_calls)} times"
        )

    def test_bind_studio_with_no_data_does_not_force_refresh(
        self, studio_page, monkeypatch
    ):
        """bind_studio with None snapshot should not trigger jobs refresh."""
        studio_page.bind_workspace(
            _make_snapshot(
                [
                    MagicMock(project_id="no_refresh_test", mode="long", next_chapter=1),
                ]
            )
        )
        studio_page._project_combo.setCurrentText("no_refresh_test")
        studio_page._previous_project = "no_refresh_test"

        timer_calls = []
        from PySide6.QtCore import QTimer

        def spy_single_shot(delay, callback):
            timer_calls.append((delay, callback))

        monkeypatch.setattr(QTimer, "singleShot", spy_single_shot)

        studio_page.bind_studio(None)

        force_refresh_calls = [c for (d, c) in timer_calls if c == studio_page._force_refresh_jobs]
        assert len(force_refresh_calls) == 0, (
            f"_force_refresh_jobs should NOT be scheduled when snapshot is None, "
            f"but was called {len(force_refresh_calls)} times"
        )


# ─── Run ──────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

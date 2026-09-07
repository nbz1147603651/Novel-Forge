"""Integration test: Desktop MainWindow lifecycle (startup, page switch, shutdown)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

# Patch settings and workspace service BEFORE importing MainWindow so __init__
# never touches disk or real config.
with (
    patch("novel_forge.core.config.get_settings") as _mock_get_settings,
    patch("novel_forge.desktop.window.DesktopWorkspaceService") as _mock_ws_cls,
):
    # Configure mock settings with proper typed values to avoid TypeError
    # when SettingsPage accesses numeric settings during _build_ui().
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
    # Temperature settings (accessed via SHORT_TEMPERATURE_TASKS / LONG_TEMPERATURE_TASKS)
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
    # Routing config
    _mock_settings.routes = {}
    _mock_settings.fallback_routes = {}
    _mock_settings.profiles = []
    # Ollama string settings (accessed via make_line_setting)
    _mock_settings.ollama_base_url = "http://localhost:11434/v1"
    _mock_settings.ollama_model = "llama3.2"
    _mock_settings.ollama_embedding_model = "nomic-embed-text"
    _mock_settings.log_level = "INFO"
    _mock_settings.log_keep_runs = 30
    _mock_settings.api_call_timeout_s = 120
    # Additional int settings
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
    _mock_settings.element_progress_llm_gray_score_low = 0.8
    _mock_settings.element_progress_llm_gray_score_high = 1.4
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
    """Provide a shared QApplication for the module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestDesktopLifecycle:
    """End-to-end lifecycle tests for the desktop main window."""

    @pytest.fixture
    def window(self, qapp, monkeypatch):
        """Create a headless MainWindow with all external I/O mocked."""
        # Additional runtime patches (belt-and-suspenders)
        monkeypatch.setattr(
            "novel_forge.desktop.window.DesktopWorkspaceService",
            _mock_ws_cls,
        )

        win = NovelForgeDesktopWindow()

        # Spy on every page's shutdown() so we can assert later
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

        # Teardown: invoke the same cleanup path the app uses on close
        win._pre_close_cleanup()

    def test_startup_no_crash(self, window: NovelForgeDesktopWindow) -> None:
        """Instantiating MainWindow with mocked workspace should not raise."""
        assert window is not None
        assert set(window._pages) == {"dashboard"}
        assert set(window._pages).issubset(window.PAGE_META)
        assert window._current_page_id() in ("dashboard", "workflow", "chapter_studio")

    def test_page_switching(self, window: NovelForgeDesktopWindow) -> None:
        """Switch between Dashboard, Chapter Studio, and Settings pages."""
        # Dashboard, workflow, or chapter studio may be the default depending on state
        assert window._current_page_id() in ("dashboard", "workflow", "chapter_studio")

        # Switch to Chapter Studio
        window.switch_page("chapter_studio")
        assert window._current_page_id() == "chapter_studio"
        assert window._nav_buttons["chapter_studio"].isChecked()
        assert not window._nav_buttons["dashboard"].isChecked()

        # Switch to Settings
        window.switch_page("settings")
        assert window._current_page_id() == "settings"
        assert window._nav_buttons["settings"].isChecked()

        # Switch back to Dashboard
        window.switch_page("dashboard")
        assert window._current_page_id() == "dashboard"
        assert window._nav_buttons["dashboard"].isChecked()

    def test_shutdown_calls_page_shutdowns(
        self, window: NovelForgeDesktopWindow
    ) -> None:
        """_pre_close_cleanup must invoke shutdown() on every page."""
        self._shutdown_calls.clear()
        window._pre_close_cleanup()

        for page_id in window._pages:
            assert self._shutdown_calls.get(page_id), (
                f"page.shutdown() was not called for '{page_id}'"
            )

    def test_shutdown_stops_timers(
        self, window: NovelForgeDesktopWindow
    ) -> None:
        """_pre_close_cleanup should stop QTimers and clear animations."""
        assert window._refresh_timer.isActive()
        window._pre_close_cleanup()
        assert not window._refresh_timer.isActive()
        assert not window._jobs_bind_timer.isActive()
        assert window._page_animation is None

    def test_no_dangling_timers_after_teardown(
        self, window: NovelForgeDesktopWindow, qapp
    ) -> None:
        """After fixture teardown there must be no active QTimers owned by the window."""
        window._pre_close_cleanup()
        active_timers = [
            obj for obj in window.findChildren(QTimer)
            if obj.isActive()
        ]
        assert not active_timers, (
            f"Found active QTimers after shutdown: {active_timers}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

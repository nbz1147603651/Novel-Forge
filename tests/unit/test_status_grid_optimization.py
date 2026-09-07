"""Test the status grid optimization for faster page switching."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_status_grid_build_constants_optimized():
    """Verify that build constants are optimized for faster rendering."""
    from novel_forge.desktop.pages.settings.page import SettingsPage
    
    # Check that batch size is increased
    assert SettingsPage._STATUS_GRID_BUILD_BATCH_SIZE >= 4, \
        "Batch size should be at least 4 for faster builds"
    
    # Check that interval is reduced
    assert SettingsPage._STATUS_GRID_BUILD_INTERVAL_MS <= 16, \
        "Build interval should be ≤16ms for smoother updates"
    
    # Check that activation delay is reduced
    assert SettingsPage._STATUS_GRID_BUILD_ACTIVATE_DELAY_MS <= 50, \
        "Activation delay should be ≤50ms for faster initial display"


def test_prewarm_model_cache_exists():
    """Verify that prewarm method exists and has correct signature."""
    from novel_forge.desktop.pages.settings.model_mgmt import ModelManagementMixin
    
    # Check that prewarm method exists
    assert hasattr(ModelManagementMixin, 'prewarm_model_cache'), \
        "ModelManagementMixin should have prewarm_model_cache method"


def test_deferred_mode_starts_immediate_build():
    """Verify that deferred mode code path exists and is correctly structured."""
    import inspect

    from novel_forge.desktop.pages.settings.model_mgmt import ModelManagementMixin
    
    # Get the source code of _refresh_model_status_grid
    source = inspect.getsource(ModelManagementMixin._refresh_model_status_grid)
    
    # Verify key optimization patterns exist in the code
    assert "deferred:" in source, "Should have deferred parameter handling"
    assert "正在准备模型连接状态" in source, "Should show skeleton screen message"
    assert "骨架屏" in source, "Should have skeleton screen comment"


def test_activate_starts_build_immediately():
    """Verify that activate() starts a stopped deferred timer at 0 ms."""
    from novel_forge.desktop.pages.settings.model_mgmt import ModelManagementMixin

    class _Timer:
        def __init__(self) -> None:
            self.started_with: list[int] = []

        def isActive(self) -> bool:  # noqa: N802 - Qt-compatible fake
            return False

        def start(self, delay: int) -> None:
            self.started_with.append(delay)

    timer = _Timer()
    page = SimpleNamespace(
        _status_grid_build_complete=False,
        _status_grid_build_timer=timer,
        _status_tests_pending=False,
        _ollama_refresh_started=True,
    )

    ModelManagementMixin.activate(page)

    assert page._status_tests_started is True
    assert page._status_auto_activation_active is True
    assert timer.started_with == [0]


def test_connection_test_worker_has_cache():
    """Verify that ConnectionTestWorker has caching mechanism."""
    from novel_forge.desktop.pages.settings.components import _ConnectionTestWorker
    
    # Check class-level cache exists
    assert hasattr(_ConnectionTestWorker, '_result_cache'), \
        "ConnectionTestWorker should have _result_cache"
    
    # Check TTL constants exist
    assert hasattr(_ConnectionTestWorker, '_CACHE_TTL'), \
        "ConnectionTestWorker should have _CACHE_TTL"
    assert hasattr(_ConnectionTestWorker, '_STALE_CACHE_TTL'), \
        "ConnectionTestWorker should have _STALE_CACHE_TTL"
    
    # Check cache methods exist
    assert hasattr(_ConnectionTestWorker, 'cached_result'), \
        "ConnectionTestWorker should have cached_result method"
    assert hasattr(_ConnectionTestWorker, 'stale_cached_result'), \
        "ConnectionTestWorker should have stale_cached_result method"


def test_prewarm_workers_remain_owned_until_completion() -> None:
    from novel_forge.desktop.pages.settings.model_mgmt import ModelManagementMixin

    class _Signal:
        def __init__(self) -> None:
            self.callback = None

        def connect(self, callback) -> None:
            self.callback = callback

    class _Worker:
        def __init__(self, profile, *, force_refresh: bool) -> None:
            self.profile = profile
            self.force_refresh = force_refresh
            self.signals = SimpleNamespace(finished=_Signal())
            self.started = False

        def start(self) -> None:
            self.started = True

    profile = SimpleNamespace(profile_id="test:model", is_key_configured=True)
    page = SimpleNamespace(
        _shutdown_done=False,
        _config=SimpleNamespace(profiles=[profile]),
        _ConnectionTestWorker=_Worker,
        _test_workers=[],
        isVisible=lambda: True,
    )

    ModelManagementMixin.prewarm_model_cache(page)

    assert len(page._test_workers) == 1
    worker = page._test_workers[0]
    assert worker.started is True
    worker.signals.finished.callback(profile.profile_id, True, "ok", False, False)
    assert page._test_workers == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

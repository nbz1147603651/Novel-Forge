"""P0-4: Reference-check (``is``/``id()``) short-circuit in ``_build_snapshot_payload``.

When ``_build_snapshot_payload`` is called twice with the *same* snapshot
reference (or with snapshot attributes that are reference-identical to the
previous call), the second call should reuse cached section payloads instead
of recomputing ``_stable_snapshot_value`` for every field.

The cache lives on the window instance (``_last_payload_values``), NOT in
the ``@staticmethod _stable_snapshot_value``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from novel_forge.desktop.window import NovelForgeDesktopWindow
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
    WorkspaceOverview,
)
from novel_forge.workspace.projects import ProjectDetail


def _build_snapshot() -> DesktopWorkspaceSnapshot:
    """Build a minimal DesktopWorkspaceSnapshot for testing."""
    storage_root = Path("/tmp/novel_forge_test")
    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="当前已载入",
        ),
    ]
    overview = WorkspaceOverview(
        storage_root=str(storage_root),
        total_projects=1,
        short_projects=0,
        long_projects=1,
        total_generated_chapters=2,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=1,
        total_chapters=2,
        total_words=5000,
        configured_providers=1,
    )
    project_item = DesktopProjectItem(
        project_id="test_novel",
        title="测试小说",
        mode="long",
        mode_label="长篇",
        genre="",
        tone="",
        completed_chapters=2,
        total_chapters=10,
        next_chapter=3,
        has_outline=True,
        has_canon=False,
        init_resume_available=False,
        init_resume_step_label="",
        project_state="active",
        project_state_label="",
        allowed_operations=(),
        status="active",
        status_label="进行中",
        progress_label="2/10",
        progress_percent=20,
        last_updated_label="刚刚",
        headline="测试",
        next_action="续写",
    )
    project_detail = ProjectDetail(
        project_id="test_novel",
        title="测试小说",
        mode="long",
        total_chapters=10,
        completed_chapters=2,
        chapters=[],
        recent_files=[],
        artifact_counts={},
    )
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=[project_item],
        details={"test_novel": project_detail},
        featured_project=project_item,
    )


class TestPayloadShortCircuit:
    """Tests for reference-check short-circuit in _build_snapshot_payload."""

    def test_second_call_with_same_snapshot_uses_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Second call to _build_snapshot_payload with same snapshot reuses cache.

        After the first call populates ``_last_payload_values``, the second
        call with the *identical* snapshot reference should skip recomputing
        ``_stable_snapshot_value`` for sections whose attribute references
        haven't changed (``is`` check).
        """
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        snapshot = _build_snapshot()

        # First call — populates cache
        payload1 = window._build_snapshot_payload(snapshot)

        # Verify cache was populated
        assert hasattr(window, "_last_payload_values"), (
            "Window must have _last_payload_values attribute after first call"
        )
        assert len(window._last_payload_values) > 0, (
            "_last_payload_values should be populated after first call"
        )

        # Track calls to _stable_snapshot_value
        call_count = 0
        original_static = NovelForgeDesktopWindow._stable_snapshot_value

        def counting_wrapper(value: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return original_static(value)

        # Second call with SAME snapshot reference — should short-circuit
        with patch.object(
            NovelForgeDesktopWindow,
            "_stable_snapshot_value",
            staticmethod(counting_wrapper),
        ):
            payload2 = window._build_snapshot_payload(snapshot)

        # With short-circuit, _stable_snapshot_value should NOT be called
        # for any section whose reference is unchanged (all of them, since
        # the snapshot is the same frozen dataclass instance).
        assert call_count == 0, (
            f"_stable_snapshot_value was called {call_count} times on second "
            f"call with same snapshot reference — expected 0 (all cached)."
        )

        # Payloads must be equal
        assert payload1 == payload2

    def test_changed_section_recomputed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """When a snapshot attribute changes, only that section is recomputed."""
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        snapshot = _build_snapshot()

        # First call — populates cache
        window._build_snapshot_payload(snapshot)

        # Build a new snapshot with only metrics changed
        new_metrics = replace(
            snapshot.metrics,
            total_words=99999,
        )
        new_snapshot = replace(snapshot, metrics=new_metrics)

        # Track which values _stable_snapshot_value is called with
        called_with: list[Any] = []
        original_static = NovelForgeDesktopWindow._stable_snapshot_value

        def tracking_wrapper(value: Any) -> Any:
            called_with.append(value)
            return original_static(value)

        with patch.object(
            NovelForgeDesktopWindow,
            "_stable_snapshot_value",
            staticmethod(tracking_wrapper),
        ):
            window._build_snapshot_payload(new_snapshot)

        # _stable_snapshot_value should be called for metrics (changed)
        # but NOT for overview, providers, projects, featured_project, details
        # (all unchanged references).
        # Note: metrics is a frozen dataclass, so `replace` creates a new ref.
        metrics_calls = [
            v for v in called_with if v is new_metrics or v is snapshot.metrics
        ]
        assert len(metrics_calls) >= 1, (
            "metrics section should be recomputed after metrics changed"
        )

        # The unchanged sections should NOT have been recomputed
        # overview, providers, projects, featured_project, details are same refs
        unchanged_calls = [
            v
            for v in called_with
            if v is snapshot.overview
            or v is snapshot.providers
            or v is snapshot.projects
            or v is snapshot.featured_project
        ]
        assert len(unchanged_calls) == 0, (
            f"Unchanged sections were recomputed: {len(unchanged_calls)} calls"
        )

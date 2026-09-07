"""Shared fixtures and helpers for visual regression tests (Wave 3 / Task 20).

Provides:
- ``visual_marker`` fixture: ensures ``--update-visual`` is detected at
  collection time without coupling to ``pytestconfig``.
- ``compute_pixel_diff``: pure function that compares two PNG files and
  returns the fraction of pixels that differ (0.0–1.0).
- ``capture_page``: helper that forces a paint cycle and saves a screenshot.
- ``assert_matches_baseline``: top-level assertion used by all visual tests.
- Synthetic snapshot builders for Dashboard, Projects, and ChapterStudio.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops

# ─── Paths ────────────────────────────────────────────────────────────────

VISUAL_DIR = Path(__file__).resolve().parent
BASELINES_DIR = VISUAL_DIR / "baselines"

# Default threshold — 1 % of pixels may change before we treat it as a
# regression.  This absorbs tiny font-rendering / antialiasing drift that can
# happen across minor Qt or platform changes without hiding real regressions.
DEFAULT_DIFF_THRESHOLD = float(os.environ.get("VISUAL_DIFF_THRESHOLD", "0.01"))

# Standard page geometry used by every visual test.  All three pages are
# rendered at the same size so that baselines are directly comparable and
# don't depend on the host screen.
PAGE_WIDTH = 1280
PAGE_HEIGHT = 800


# ─── Diff helper ──────────────────────────────────────────────────────────


def compute_pixel_diff(actual_path: Path, baseline_path: Path) -> float:
    """Return the fraction of pixels that differ between *actual_path* and
    *baseline_path* (0.0–1.0).

    Both images are normalised to RGB so that small format differences (RGB
    vs RGBA, 8-bit vs 16-bit) don't false-trigger the diff.  Returns 1.0 when
    dimensions differ (full replacement).
    """
    with Image.open(baseline_path) as base_img, Image.open(actual_path) as actual_img:
        if base_img.size != actual_img.size:
            return 1.0
        base_rgb = base_img.convert("RGB")
        actual_rgb = actual_img.convert("RGB")
        diff_img = ImageChops.difference(base_rgb, actual_rgb)
        bbox = diff_img.getbbox()
        if bbox is None:
            return 0.0

        # Sum the absolute per-channel differences, normalise by max possible
        # difference per pixel (3 channels × 255).  We iterate pixel-by-pixel
        # because ``get_flattened_data`` on a diff image returns per-channel
        # tuples; this keeps the comparison simple and explicit.  The method
        # was added in Pillow 12 as a non-deprecated replacement for
        # ``getdata``.
        width, height = base_rgb.size
        total_diff = 0
        # Crop to the changed region only — saves time on tiny diffs.
        cropped = diff_img.crop(bbox)
        # PIL ships no type stubs, so mypy can't infer the per-pixel tuple
        # iterator type for ``get_flattened_data``.
        for r, g, b in cropped.get_flattened_data():  # type: ignore[misc]
            total_diff += int(r) + int(g) + int(b)
        max_possible = width * height * 3 * 255
        if not max_possible:
            return 0.0
        return total_diff / max_possible


# ─── Capture helper ───────────────────────────────────────────────────────


def capture_page(widget: Any, target_path: Path) -> None:
    """Resize *widget* to the standard visual test geometry, force a paint
    cycle, then save a PNG screenshot at *target_path*.

    The caller is responsible for seeding *widget* with deterministic data
    before this helper runs.  We deliberately do NOT call ``widget.show()``
    because offscreen widgets that have never been shown sometimes render
    with zero-size layouts; ``adjustSize()`` then ``grab()`` is sufficient.
    """
    from PySide6.QtCore import QCoreApplication, QSize
    from PySide6.QtWidgets import QWidget

    assert isinstance(widget, QWidget)
    widget.resize(QSize(PAGE_WIDTH, PAGE_HEIGHT))
    widget.adjustSize()
    # Drain any pending events so that layouts/paints scheduled by adjustSize
    # are flushed before we grab the pixmap.
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()

    target_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError(
            f"widget.grab() returned a null pixmap for {widget.__class__.__name__}"
        )
    if not pixmap.save(str(target_path), "PNG"):
        raise RuntimeError(f"Failed to save screenshot to {target_path}")


# ─── Top-level assertion ──────────────────────────────────────────────────


def assert_matches_baseline(
    *,
    name: str,
    actual_path: Path,
    threshold: float = DEFAULT_DIFF_THRESHOLD,
    update: bool = False,
) -> None:
    """Compare *actual_path* against the baseline named *name*.

    * If ``update`` is True or the baseline is missing: write the captured
      screenshot as the new baseline and ``pytest.skip`` the test.
    * Otherwise: assert pixel diff is below *threshold*.
    """
    baseline_path = BASELINES_DIR / f"{name}.png"
    if update or not baseline_path.exists():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        actual_bytes = actual_path.read_bytes()
        baseline_path.write_bytes(actual_bytes)
        warnings.warn(
            f"[visual] Baseline created/updated: {baseline_path}",
            stacklevel=2,
        )
        pytest.skip(
            f"Baseline {'updated' if update else 'created'}: {baseline_path.name}. "
            "Re-run the test to verify against the new baseline."
        )
        return  # unreachable — pytest.skip raises

    diff_ratio = compute_pixel_diff(actual_path, baseline_path)
    assert diff_ratio <= threshold, (
        f"Visual regression for '{name}': diff={diff_ratio:.4%} "
        f"(threshold={threshold:.2%}). "
        f"Inspect {actual_path} vs {baseline_path}. "
        f"Re-run with --update-visual to regenerate the baseline."
    )


# ─── Synthetic snapshot builders ─────────────────────────────────────────


def _build_dashboard_snapshot() -> Any:
    """Build a 5-project ``DesktopWorkspaceSnapshot`` for the dashboard."""
    from novel_forge.desktop.workspace import (
        DesktopProjectItem,
        DesktopWorkspaceMetrics,
        DesktopWorkspaceSnapshot,
        ProviderStatus,
    )
    from novel_forge.workspace.projects import (
        ChapterSummary,
        ProjectDetail,
        WorkspaceOverview,
    )

    projects: list[DesktopProjectItem] = []
    details: dict[str, ProjectDetail] = {}
    for i in range(5):
        pid = f"project_{i:03d}"
        chapters = [
            ChapterSummary(
                chapter_number=c,
                title=f"第{c}章",
                word_count=3200 + c * 40,
                overall_score=0.78 + (c % 7) * 0.02,
                continuity_score=0.82 + (c % 5) * 0.015,
                updated_at=f"2026-05-{(c % 28) + 1:02d}T12:00:00Z",
                preview=f"章节 {c} 预览文本...",
            )
            for c in range(1, 11)
        ]
        details[pid] = ProjectDetail(
            project_id=pid,
            mode="long",
            title=f"测试项目 {i}",
            genre="fantasy",
            tone="epic",
            premise=f"这是项目 {i} 的测试前提。",
            preview=f"项目 {i} 的预览文本。",
            total_chapters=10,
            completed_chapters=10,
            latest_chapter=10,
            completion_ratio=1.0,
            has_outline=True,
            has_canon=True,
            updated_at="2026-05-15T12:00:00Z",
            chapters=chapters,
            recent_files=[f"data/{pid}/chapter_{c}.md" for c in range(1, 6)],
            artifact_counts={"chapters": 10, "reports": 12},
        )
        projects.append(
            DesktopProjectItem(
                project_id=pid,
                title=f"测试项目 {i}",
                mode="long",
                mode_label="长篇",
                status="completed",
                status_label="已完结",
                progress_label="10/10",
                progress_percent=100,
                last_updated_label="2026-05-15",
                headline=f"项目 {i} 的标题信息",
                next_action="回看章节与质量报告",
                genre="fantasy",
                tone="epic",
                completed_chapters=10,
                total_chapters=10,
                next_chapter=None,
                has_outline=True,
                has_canon=True,
            )
        )

    overview = WorkspaceOverview(
        storage_root="/tmp/visual_test_workspace",
        total_projects=5,
        short_projects=0,
        long_projects=5,
        total_generated_chapters=50,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=5,
        total_chapters=50,
        total_words=50 * 3500,
        configured_providers=1,
    )
    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="Mock adapter",
        )
    ]
    return DesktopWorkspaceSnapshot(
        storage_root=Path("/tmp/visual_test_workspace"),
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=projects,
        featured_project=projects[0],
        details=details,
    )


def _build_projects_snapshot() -> Any:
    """Build a 3-project snapshot for the projects page."""
    from novel_forge.desktop.workspace import (
        DesktopProjectItem,
        DesktopWorkspaceMetrics,
        DesktopWorkspaceSnapshot,
        ProviderStatus,
    )
    from novel_forge.workspace.projects import (
        ChapterSummary,
        ProjectDetail,
        WorkspaceOverview,
    )

    projects: list[DesktopProjectItem] = []
    details: dict[str, ProjectDetail] = {}
    for i in range(3):
        pid = f"novel_{chr(ord('a') + i)}"
        chapters = [
            ChapterSummary(
                chapter_number=c,
                title=f"第{c}章",
                word_count=3000 + c * 30,
                overall_score=0.80 + (c % 6) * 0.02,
                continuity_score=0.83 + (c % 4) * 0.015,
                updated_at=f"2026-04-{(c % 28) + 1:02d}T10:00:00Z",
                preview=f"第 {c} 章预览...",
            )
            for c in range(1, 6)
        ]
        details[pid] = ProjectDetail(
            project_id=pid,
            mode="long",
            title=f"项目 {chr(ord('A') + i)}",
            genre="scifi",
            tone="dark",
            premise=f"项目 {chr(ord('A') + i)} 的核心前提。",
            preview=f"项目 {chr(ord('A') + i)} 概述。",
            total_chapters=5,
            completed_chapters=5,
            latest_chapter=5,
            completion_ratio=1.0,
            has_outline=True,
            has_canon=True,
            updated_at="2026-04-15T10:00:00Z",
            chapters=chapters,
            recent_files=[f"data/{pid}/chapter_{c}.md" for c in range(1, 4)],
            artifact_counts={"chapters": 5, "reports": 6},
        )
        projects.append(
            DesktopProjectItem(
                project_id=pid,
                title=f"项目 {chr(ord('A') + i)}",
                mode="long",
                mode_label="长篇",
                status="completed",
                status_label="已完结",
                progress_label="5/5",
                progress_percent=100,
                last_updated_label="2026-04-15",
                headline=f"项目 {chr(ord('A') + i)} 标题",
                next_action="回看章节",
                genre="scifi",
                tone="dark",
                completed_chapters=5,
                total_chapters=5,
                next_chapter=None,
                has_outline=True,
                has_canon=True,
            )
        )

    overview = WorkspaceOverview(
        storage_root="/tmp/visual_test_workspace",
        total_projects=3,
        short_projects=0,
        long_projects=3,
        total_generated_chapters=15,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=3,
        total_chapters=15,
        total_words=15 * 3200,
        configured_providers=1,
    )
    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="Mock adapter",
        )
    ]
    return DesktopWorkspaceSnapshot(
        storage_root=Path("/tmp/visual_test_workspace"),
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=projects,
        featured_project=projects[0],
        details=details,
    )


def _build_chapter_studio_snapshot() -> Any:
    """Build a 1-project + 5-chapter snapshot for the chapter-studio page."""
    from novel_forge.desktop.workspace import (
        DesktopProjectItem,
        DesktopWorkspaceMetrics,
        DesktopWorkspaceSnapshot,
        ProviderStatus,
    )
    from novel_forge.workspace.projects import (
        ChapterSummary,
        ProjectDetail,
        WorkspaceOverview,
    )

    pid = "studio_novel"
    chapters = [
        ChapterSummary(
            chapter_number=c,
            title=f"第{c}章",
            word_count=3500 + c * 25,
            overall_score=0.82 + (c % 5) * 0.02,
            continuity_score=0.85 + (c % 3) * 0.015,
            updated_at=f"2026-03-{(c % 28) + 1:02d}T08:00:00Z",
            preview=f"第 {c} 章预览...",
        )
        for c in range(1, 6)
    ]
    detail = ProjectDetail(
        project_id=pid,
        mode="long",
        title="章节工作室测试项目",
        genre="fantasy",
        tone="dark",
        premise="章节工作室回归测试的固定前提。",
        preview="项目概述。",
        total_chapters=5,
        completed_chapters=5,
        latest_chapter=5,
        completion_ratio=1.0,
        has_outline=True,
        has_canon=True,
        updated_at="2026-03-15T08:00:00Z",
        chapters=chapters,
        recent_files=[f"data/{pid}/chapter_{c}.md" for c in range(1, 4)],
        artifact_counts={"chapters": 5, "reports": 7},
    )
    project = DesktopProjectItem(
        project_id=pid,
        title="章节工作室测试项目",
        mode="long",
        mode_label="长篇",
        status="active",
        status_label="进行中",
        progress_label="5/5",
        progress_percent=100,
        last_updated_label="2026-03-15",
        headline="工作室项目标题",
        next_action="续写下一章",
        genre="fantasy",
        tone="dark",
        completed_chapters=5,
        total_chapters=5,
        next_chapter=None,
        has_outline=True,
        has_canon=True,
    )
    overview = WorkspaceOverview(
        storage_root="/tmp/visual_test_workspace",
        total_projects=1,
        short_projects=0,
        long_projects=1,
        total_generated_chapters=5,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=1,
        total_chapters=5,
        total_words=5 * 3500,
        configured_providers=1,
    )
    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="Mock adapter",
        )
    ]
    return DesktopWorkspaceSnapshot(
        storage_root=Path("/tmp/visual_test_workspace"),
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=[project],
        featured_project=project,
        details={pid: detail},
    )


# ─── Pytest fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def update_visual(request: pytest.FixtureRequest) -> bool:
    """True when ``--update-visual`` was passed on the command line."""
    return bool(request.config.getoption("--update-visual", default=False))


@pytest.fixture()
def dashboard_snapshot() -> Any:
    """A 5-project synthetic snapshot for the dashboard."""
    return _build_dashboard_snapshot()


@pytest.fixture()
def projects_snapshot() -> Any:
    """A 3-project synthetic snapshot for the projects page."""
    return _build_projects_snapshot()


@pytest.fixture()
def chapter_studio_snapshot() -> Any:
    """A 1-project / 5-chapter synthetic snapshot for the chapter-studio page."""
    return _build_chapter_studio_snapshot()


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--update-visual`` CLI flag."""
    group = parser.getgroup("visual")
    group.addoption(
        "--update-visual",
        action="store_true",
        default=False,
        help=(
            "Regenerate visual regression baselines from the current capture. "
            "Only meaningful together with -m visual."
        ),
    )
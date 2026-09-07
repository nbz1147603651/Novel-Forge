"""Shared test fixtures for desktop UI tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import DEFAULT_THRESHOLD


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-baselines",
        action="store_true",
        default=False,
        help="Update baseline screenshots instead of comparing",
    )
    parser.addoption(
        "--visual-threshold",
        type=float,
        default=None,
        help="Override pixel difference threshold (0.0-1.0)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return
    desktop_root = Path(__file__).resolve().parent
    skip_parallel_qt = pytest.mark.skip(
        reason="Desktop Qt tests are xdist-unsafe; run tests/desktop serially."
    )
    for item in items:
        item_path = Path(str(item.path)).resolve()
        if item_path == desktop_root or desktop_root in item_path.parents:
            item.add_marker(skip_parallel_qt)


@pytest.fixture
def qtbot(qtbot: QtBot) -> QtBot:
    return qtbot


@pytest.fixture
def desktop_app() -> object:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def visual_threshold(request: pytest.FixtureRequest) -> float:
    override = request.config.getoption("--visual-threshold")
    if override is not None:
        return float(override)
    return DEFAULT_THRESHOLD


@pytest.fixture
def update_baselines(request: pytest.FixtureRequest) -> bool:
    return request.config.getoption("--update-baselines", default=False)

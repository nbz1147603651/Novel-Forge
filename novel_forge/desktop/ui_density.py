"""Shared desktop UI density helpers.

Small, repeated UI surfaces such as side-panel tabs should not each reinvent
font sizes and padding.  Centralizing the density presets keeps narrow panels
predictable when more labels are added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget

TabDensityProfile = Literal["compact", "content", "roomy"]


@dataclass(frozen=True)
class TabDensitySpec:
    font_size: int
    horizontal_padding: int
    vertical_padding: int
    min_width: int
    min_height: int
    margin_right: int = 0
    scroll_buttons: bool = False


_TAB_DENSITY_SPECS: dict[TabDensityProfile, TabDensitySpec] = {
    "compact": TabDensitySpec(
        font_size=10,
        horizontal_padding=5,
        vertical_padding=3,
        min_width=18,
        min_height=18,
    ),
    "content": TabDensitySpec(
        font_size=12,
        horizontal_padding=7,
        vertical_padding=4,
        min_width=0,
        min_height=22,
        margin_right=1,
    ),
    "roomy": TabDensitySpec(
        font_size=13,
        horizontal_padding=10,
        vertical_padding=6,
        min_width=34,
        min_height=24,
        margin_right=1,
        scroll_buttons=True,
    ),
}


def configure_tab_widget_density(
    tabs: QTabWidget,
    profile: TabDensityProfile,
    *,
    scroll_buttons: bool | None = None,
) -> None:
    """Apply a consistent tab density preset to *tabs*."""
    spec = _TAB_DENSITY_SPECS[profile]
    uses_scroll_buttons = spec.scroll_buttons if scroll_buttons is None else scroll_buttons
    tabs.setElideMode(Qt.TextElideMode.ElideNone)
    tab_bar = tabs.tabBar()
    tab_bar.setExpanding(False)
    tab_bar.setUsesScrollButtons(uses_scroll_buttons)
    tab_bar.setDrawBase(False)
    tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
    tab_bar.setStyleSheet(
        "QTabBar::tab {"
        f" padding-left: {spec.horizontal_padding}px;"
        f" padding-right: {spec.horizontal_padding}px;"
        f" padding-top: {spec.vertical_padding}px;"
        f" padding-bottom: {spec.vertical_padding}px;"
        f" min-width: {spec.min_width}px;"
        f" min-height: {spec.min_height}px;"
        f" margin-right: {spec.margin_right}px;"
        f" font-size: {spec.font_size}px;"
        " font-weight: 600;"
        "}"
    )

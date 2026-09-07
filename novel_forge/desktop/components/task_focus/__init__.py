"""Reusable Desktop task focus widgets."""

from __future__ import annotations

from novel_forge.desktop.components.dialogs import MessageBoxAction, show_message_box
from novel_forge.desktop.components.stream_rendering import (
    _compact_text_lines,
    _html_from_segments,
    _html_from_text,
    _json_block_html,
    _json_candidate,
    _looks_like_guard_report,
    _raw_json_block_html,
    _render_json_scalar,
    _render_json_value,
    _render_report_body,
    _report_block_html,
    _split_json_candidate,
    _wrap_report_inline,
)
from novel_forge.desktop.components.task_focus.companion import FloatingTaskCompanion
from novel_forge.desktop.components.task_focus.dialog import TaskFocusDialog
from novel_forge.desktop.components.task_focus.focus_panel import TaskFocusPanel
from novel_forge.desktop.components.task_focus.model_call import TaskModelCallPanel
from novel_forge.desktop.components.task_focus.presentation import (
    _compact_label,
    _constrain_label_width,
    _elapsed_text,
    _fmt_cost,
    _fmt_count,
    _fmt_latency,
    _job_kind_label,
    _parse_dt,
    _stream_duration_text,
    _stream_history_label,
    _stream_notice,
    _stream_status_label,
)
from novel_forge.desktop.components.task_focus.stream_window import FloatingStreamWindow
from novel_forge.desktop.components.task_focus.switcher import (
    TaskSwitcherBar,
    TaskSwitcherChip,
)
from novel_forge.desktop.pets import (
    load_pet_atlas as _load_pet_atlas,
)
from novel_forge.desktop.pets import (
    load_pet_pixmap as _load_pet_pixmap,
)

__all__ = [
    "FloatingStreamWindow",
    "FloatingTaskCompanion",
    "MessageBoxAction",
    "TaskFocusDialog",
    "TaskFocusPanel",
    "TaskModelCallPanel",
    "TaskSwitcherBar",
    "TaskSwitcherChip",
    "_compact_label",
    "_compact_text_lines",
    "_constrain_label_width",
    "_elapsed_text",
    "_fmt_cost",
    "_fmt_count",
    "_fmt_latency",
    "_html_from_segments",
    "_html_from_text",
    "_job_kind_label",
    "_json_block_html",
    "_json_candidate",
    "_load_pet_atlas",
    "_load_pet_pixmap",
    "_looks_like_guard_report",
    "_parse_dt",
    "_raw_json_block_html",
    "_render_json_scalar",
    "_render_json_value",
    "_render_report_body",
    "_report_block_html",
    "_split_json_candidate",
    "_stream_duration_text",
    "_stream_history_label",
    "_stream_notice",
    "_stream_status_label",
    "_wrap_report_inline",
    "show_message_box",
]

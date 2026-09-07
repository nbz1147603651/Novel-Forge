"""Desktop UI components package.

All components are accessible directly from ``novel_forge.desktop.components``
or from their individual sub-modules:

- :mod:`primitives`  — Surface, Badge, ActionButton, FilterChip, SectionHeading, clear_layout
- :mod:`floating_surface` — PaintedFloatingSurface for translucent frameless popups
- :mod:`containers`  — ScrollPage, MetricCard, EmptyState, CollapsibleSection, add_card_grid
- :mod:`dialogs`     — MessageBoxAction, show_message_box, show_info_message,
                       show_warning_message, ask_confirmation
- :mod:`forms`       — SettingRow, FormFieldWithHint, make_spin_setting,
                       make_float_setting, make_combo_setting, make_line_setting
- :mod:`workflow`    — PipelineStep, StepIndicatorRow
- :mod:`toast`       — Toast, ToastManager, show_toast
- :mod:`memory_components` — UnifiedMemoryPanel, MemoryBadge, MemoryMotifCard, MemorySuggestionCard, etc.
"""

from __future__ import annotations

from novel_forge.desktop.components.checkpoint_dialog import (
    CheckpointDialog,
    build_checkpoint_dialog_content,
    build_checkpoint_summary_text,
)
from novel_forge.desktop.components.containers import (
    CollapsibleSection,
    EmptyState,
    MetricCard,
    ScrollPage,
    _FadeTopOverlay,
    add_card_grid,
)
from novel_forge.desktop.components.dialogs import (
    MessageBoxAction,
    _style_message_box_button,
    ask_confirmation,
    show_critical_message,
    show_info_message,
    show_message_box,
    show_multiline_input_dialog,
    show_structured_result_dialog,
    show_text_input_dialog,
    show_warning_message,
)
from novel_forge.desktop.components.floating_surface import PaintedFloatingSurface
from novel_forge.desktop.components.forms import (
    FormFieldWithHint,
    SettingRow,
    add_setting_group_description,
    add_setting_group_header,
    make_combo_setting,
    make_float_setting,
    make_line_setting,
    make_nested_setting_section,
    make_spin_setting,
)
from novel_forge.desktop.components.memory_components import (
    ContextOverviewCard,
    MemoryBadge,
    MemoryChip,
    MemoryGuidanceCard,
    MemoryMotifCard,
    MemoryRepetitionWarning,
    MemorySuggestionCard,
    MemorySummaryPanel,
    UnifiedMemoryPanel,
)
from novel_forge.desktop.components.primitives import (
    ActionButton,
    Badge,
    FilterChip,
    SectionHeading,
    Surface,
    clear_layout,
)
from novel_forge.desktop.components.signal_coalescing import (
    LatestValueCoalescer,
    TrailingDebounce,
)
from novel_forge.desktop.components.skeleton import (
    LoadingState,
    SkeletonCard,
    SkeletonLine,
    SkeletonPulse,
)
from novel_forge.desktop.components.toast import Toast, ToastManager, show_toast
from novel_forge.desktop.components.workflow import (
    PipelineStep,
    StepDotState,
    StepIndicatorRow,
    StepIndicatorState,
)

__all__ = [
    # primitives
    "clear_layout",
    "Surface",
    "Badge",
    "ActionButton",
    "FilterChip",
    "SectionHeading",
    "PaintedFloatingSurface",
    # checkpoint dialog
    "CheckpointDialog",
    "build_checkpoint_summary_text",
    "build_checkpoint_dialog_content",
    # containers
    "_FadeTopOverlay",
    "ScrollPage",
    "MetricCard",
    "EmptyState",
    "add_card_grid",
    "CollapsibleSection",
    # dialogs
    "MessageBoxAction",
    "_style_message_box_button",
    "show_message_box",
    "show_info_message",
    "show_warning_message",
    "show_critical_message",
    "show_multiline_input_dialog",
    "show_structured_result_dialog",
    "show_text_input_dialog",
    "ask_confirmation",
    # forms
    "SettingRow",
    "add_setting_group_header",
    "add_setting_group_description",
    "make_spin_setting",
    "make_float_setting",
    "make_combo_setting",
    "make_line_setting",
    "make_nested_setting_section",
    "FormFieldWithHint",
    # workflow
    "PipelineStep",
    "StepDotState",
    "StepIndicatorRow",
    "StepIndicatorState",
    # toast
    "Toast",
    "ToastManager",
    "show_toast",
    # memory components
    "UnifiedMemoryPanel",
    "MemoryBadge",
    "MemoryChip",
    "MemoryGuidanceCard",
    "MemoryMotifCard",
    "MemorySuggestionCard",
    "MemoryRepetitionWarning",
    "MemorySummaryPanel",
    "ContextOverviewCard",
    # loading states
    "LoadingState",
    "SkeletonPulse",
    # signal coalescing
    "LatestValueCoalescer",
    "TrailingDebounce",
    "SkeletonLine",
    "SkeletonCard",
]

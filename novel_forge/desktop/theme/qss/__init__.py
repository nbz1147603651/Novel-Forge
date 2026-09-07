"""QSS fragment sub-package for component styles.

Each module exports a ``CONTENT`` string containing a contiguous portion of
the original component QSS.  The order below is intentionally exact because
Qt QSS uses later rules to override earlier ones.
"""

from __future__ import annotations

from novel_forge.desktop.theme.qss.part_00_base import CONTENT as _PART_00_BASE
from novel_forge.desktop.theme.qss.part_01_tooltip_popups import (
    CONTENT as _PART_01_TOOLTIP_POPUPS,
)
from novel_forge.desktop.theme.qss.part_02_collapsible import (
    CONTENT as _PART_02_COLLAPSIBLE,
)
from novel_forge.desktop.theme.qss.part_03_humanize import CONTENT as _PART_03_HUMANIZE
from novel_forge.desktop.theme.qss.part_04_tabs import CONTENT as _PART_04_TABS
from novel_forge.desktop.theme.qss.part_05_artifact_text import (
    CONTENT as _PART_05_ARTIFACT_TEXT,
)
from novel_forge.desktop.theme.qss.part_06_step_indicator import (
    CONTENT as _PART_06_STEP_INDICATOR,
)
from novel_forge.desktop.theme.qss.part_07_job_progress import (
    CONTENT as _PART_07_JOB_PROGRESS,
)
from novel_forge.desktop.theme.qss.part_08_model_status import (
    CONTENT as _PART_08_MODEL_STATUS,
)
from novel_forge.desktop.theme.qss.part_09_capability_dots import (
    CONTENT as _PART_09_CAPABILITY_DOTS,
)
from novel_forge.desktop.theme.qss.part_10_memory import CONTENT as _PART_10_MEMORY
from novel_forge.desktop.theme.qss.part_11_audio_players import (
    CONTENT as _PART_11_AUDIO_PLAYERS,
)
from novel_forge.desktop.theme.qss.part_12_phase_progress import (
    CONTENT as _PART_12_PHASE_PROGRESS,
)
from novel_forge.desktop.theme.qss.part_13_stream_detail import (
    CONTENT as _PART_13_STREAM_DETAIL,
)
from novel_forge.desktop.theme.qss.part_14_task_focus import CONTENT as _PART_14_TASK_FOCUS
from novel_forge.desktop.theme.qss.part_15_standalone import CONTENT as _PART_15_STANDALONE

CONTENT = (
    _PART_00_BASE
    + _PART_01_TOOLTIP_POPUPS
    + _PART_02_COLLAPSIBLE
    + _PART_03_HUMANIZE
    + _PART_04_TABS
    + _PART_05_ARTIFACT_TEXT
    + _PART_06_STEP_INDICATOR
    + _PART_07_JOB_PROGRESS
    + _PART_08_MODEL_STATUS
    + _PART_09_CAPABILITY_DOTS
    + _PART_10_MEMORY
    + _PART_11_AUDIO_PLAYERS
    + _PART_12_PHASE_PROGRESS
    + _PART_13_STREAM_DETAIL
    + _PART_14_TASK_FOCUS
    + _PART_15_STANDALONE
)

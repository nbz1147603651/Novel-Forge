"""Chapter-specific progress signals for chapter studio jobs.

ChapterStudioWorker wraps DesktopJobManager to provide chapter-studio-aware
progress signals (mode change, checkpoint reached, stage completion). Pipeline
execution is submitted through DesktopJobManager's JobService adapter.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class ChapterStudioWorkerSignals(QObject):
    """Signals emitted during chapter-studio pipeline execution."""

    checkpoint_reached = Signal(str, int, str)
    mode_changed = Signal(str)
    review_stage_completed = Signal(str, int, str)

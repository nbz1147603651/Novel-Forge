"""Mixin module: jobs_binding methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import (
    QPropertyAnimation,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.jobs import (
    DesktopJobRecord,
)
from novel_forge.desktop.motion import Motion
from novel_forge.desktop.motion import animations_supported as _motion_animations_supported
from novel_forge.desktop.strings import UIStrings
from novel_forge.desktop.window._jobs import (
    active_write_job_for_project as _jobs_active_write_job_for_project,
)
from novel_forge.desktop.window._jobs import bind_jobs as _jobs_bind_jobs
from novel_forge.desktop.window._jobs import bind_jobs_for_page as _jobs_bind_jobs_for_page
from novel_forge.desktop.window._jobs import (
    handle_task_decision_required as _jobs_handle_task_decision_required,
)
from novel_forge.desktop.window._jobs import job_chapter_number as _jobs_job_chapter_number
from novel_forge.desktop.window._jobs import latest_chapter_job as _jobs_latest_chapter_job
from novel_forge.desktop.window._jobs import provide_task_decision as _jobs_provide_task_decision
from novel_forge.desktop.window._jobs import schedule_bind_jobs as _jobs_schedule_bind_jobs
from novel_forge.desktop.window._jobs import (
    update_status_bar_labels as _jobs_update_status_bar_labels,
)

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = 1180
_WINDOW_DEFAULT_MIN_HEIGHT = 760
_WINDOW_DEFAULT_START_WIDTH = 1440
_WINDOW_DEFAULT_START_HEIGHT = 900
_WINDOW_SCREEN_WIDTH_RATIO = 0.92
_WINDOW_SCREEN_HEIGHT_RATIO = 0.90
_COMPACT_WIDTH_THRESHOLD = 1360
_COMPACT_HEIGHT_THRESHOLD = 820

if TYPE_CHECKING:
    pass


def motion_animations_supported(kind: Literal["opacity", "color", "geometry", "any"]) -> bool:
    """Compatibility hook for callers patching the old window.py facade name."""
    window_module = sys.modules.get("novel_forge.desktop.window")
    facade_checker = getattr(window_module, "motion_animations_supported", None)
    if callable(facade_checker) and facade_checker is not motion_animations_supported:
        return bool(facade_checker(kind))
    return _motion_animations_supported(kind)


class JobsBindingMixin:
    """Mixin that contributes the **jobs_binding** method group."""

    def _schedule_bind_jobs(self) -> None:
        """Debounce frequent step updates to reduce UI re-render churn."""
        _jobs_schedule_bind_jobs(self)

    def _handle_task_decision_required(self, job_id: str, payload: object) -> None:
        _jobs_handle_task_decision_required(self, job_id, payload)

    def _provide_task_decision(
        self,
        job_id: str,
        decision_id: str,
        choice: str,
        custom_text: str = "",
        approval_version: str = "",
    ) -> None:
        _jobs_provide_task_decision(
            self,
            job_id,
            decision_id,
            choice,
            custom_text=custom_text,
            approval_version=approval_version,
        )

    def _clear_observed_task(self, job_id: str) -> None:
        job_key = str(job_id or "").strip()
        if not job_key:
            self.show_priority_status(UIStrings.JOB_NO_CLEANUP, 3_500, self._STATUS_INFO)
            return
        removed = self._job_manager.clear_jobs([job_key])
        if removed > 0:
            self.show_priority_status(
                UIStrings.JOB_CLEANED_UP.format(count=removed), 4_000, self._STATUS_INFO
            )
            self._bind_jobs()
            return
        self.show_priority_status(UIStrings.JOB_NO_CLEANUP, 3_500, self._STATUS_INFO)

    def _bind_jobs_for_page(self, page_id: str, jobs: list[DesktopJobRecord]) -> None:
        _jobs_bind_jobs_for_page(self, page_id, jobs)

    def _bind_jobs(self) -> None:
        _jobs_bind_jobs(self)

    def _update_status_bar_labels(self, jobs: list[DesktopJobRecord]) -> None:
        _jobs_update_status_bar_labels(self, jobs)

    def _update_cost_label(self, project_id: str, total_tokens: int, total_cost_usd: float) -> None:
        if total_tokens <= 0:
            Motion.stop_safely(self._cost_fade_anim)
            self._cost_fade_anim = None
            self._last_cost_text = ""
            if self._status_cost_label.text():
                self._status_cost_label.setText("")
            return
        text = f"{total_tokens / 1000:.1f}k tokens  ·  ${total_cost_usd:.3f}"
        if text == self._last_cost_text:
            return
        first_visible_text = not self._last_cost_text
        self._last_cost_text = text
        self._status_cost_label.setText(text)
        if first_visible_text and motion_animations_supported("opacity"):
            anim = Motion.fade_in(
                self._status_cost_label,
                duration=150,
                delete_when_stopped=False,
            )
            self._cost_fade_anim = anim

            def _clear_cost_fade() -> None:
                if self._cost_fade_anim is anim:
                    self._cost_fade_anim = None
                if self._status_cost_label.property("_motion_anim") is anim:
                    self._status_cost_label.setProperty("_motion_anim", None)
                anim.deleteLater()

            anim.finished.connect(_clear_cost_fade)

    def _stop_step_fade(self) -> None:
        self._step_fade_generation += 1
        for attr in ("_step_fade_out_anim", "_step_fade_in_anim"):
            anim = getattr(self, attr, None)
            Motion.stop_safely(anim)
            setattr(self, attr, None)

    def _update_step_name(self, new_text: str) -> None:
        if new_text == self._last_step_text:
            return
        self._last_step_text = new_text
        self._stop_step_fade()
        generation = self._step_fade_generation

        if not self._status_step_label.text():
            self._status_step_label.setText(new_text)
            return

        if not motion_animations_supported("opacity"):
            self._status_step_label.setText(new_text)
            return

        def _clear_step_anim(attr: str, anim: QPropertyAnimation) -> None:
            if self._step_fade_generation == generation and getattr(self, attr, None) is anim:
                setattr(self, attr, None)
            if self._status_step_label.property("_motion_anim") is anim:
                self._status_step_label.setProperty("_motion_anim", None)
            anim.deleteLater()

        def _on_fade_out_finished() -> None:
            if (
                self._step_fade_generation != generation
                or self._step_fade_out_anim is not fade_out_anim
            ):
                fade_out_anim.deleteLater()
                return
            self._step_fade_out_anim = None
            self._status_step_label.setText(new_text)
            if motion_animations_supported("opacity"):
                fade_in_anim = Motion.fade_in(
                    self._status_step_label,
                    duration=100,
                    delete_when_stopped=False,
                )
                self._step_fade_in_anim = fade_in_anim
                fade_in_anim.finished.connect(
                    lambda: _clear_step_anim("_step_fade_in_anim", fade_in_anim)
                )
            if self._status_step_label.property("_motion_anim") is fade_out_anim:
                self._status_step_label.setProperty("_motion_anim", None)
            fade_out_anim.deleteLater()

        fade_out_anim = Motion.fade_out(
            self._status_step_label,
            duration=100,
            delete_when_stopped=False,
        )
        self._step_fade_out_anim = fade_out_anim
        fade_out_anim.finished.connect(_on_fade_out_finished)

    @staticmethod
    def _job_chapter_number(job: DesktopJobRecord) -> int | None:
        return _jobs_job_chapter_number(job)

    def _active_write_job_for_project(self, project_id: str) -> DesktopJobRecord | None:
        return _jobs_active_write_job_for_project(
            jobs=self._latest_jobs,
            project_id=project_id,
        )

    def _latest_chapter_job(
        self,
        *,
        project_id: str,
        chapter_number: int,
    ) -> DesktopJobRecord | None:
        return _jobs_latest_chapter_job(
            jobs=self._latest_jobs,
            project_id=project_id,
            chapter_number=chapter_number,
        )

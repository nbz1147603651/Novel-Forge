"""Coordinator classes for ChapterStudioPage composition.

Reduces mixin inheritance chain to delegation-based composition.
ChapterStudioPage creates these coordinators and delegates to them.
"""

from __future__ import annotations

from typing import Any
from weakref import ReferenceType, ref


class _PageDelegate:
    """Hold a page weakly so a retained coordinator cannot retain its UI."""

    def __init__(self, page: Any) -> None:
        self._page_ref: ReferenceType[Any] = ref(page)
        self._detached = False

    @property
    def _page(self) -> Any | None:
        return None if self._detached else self._page_ref()

    def detach(self) -> None:
        """Make this coordinator inert once its owning page shuts down."""
        self._detached = True


class StudioStateManager(_PageDelegate):
    """Manages ChapterStudioPage state — auto-pilot, jobs, notes.

    Wraps the ChapterStudioState object and provides convenient access
    to commonly-used state attributes. Tests that access page._store,
    page._studio, etc. will still work because the page initializes
    these as None in __init__ and the coordinator reads from the page.
    """

    def __init__(self, page: Any) -> None:
        super().__init__(page)

    # ── Forward key attributes that tests commonly access ──────────

    @property
    def store(self) -> Any:
        return getattr(self._page, "_store", None)

    @property
    def studio(self) -> Any:
        return getattr(self._page, "_studio", None)

    @property
    def workspace(self) -> Any:
        return getattr(self._page, "_workspace", None)

    @property
    def runtime(self) -> Any:
        return getattr(self._page, "_runtime", None)

    @property
    def memory_presenter(self) -> Any:
        return getattr(self._page, "_memory_presenter", None)

    @property
    def state(self) -> Any:
        return getattr(self._page, "_state", None)

    @property
    def jobs(self) -> list[Any]:
        state = self.state
        return getattr(state, "jobs", []) if state else []

    @jobs.setter
    def jobs(self, value: list[Any]) -> None:
        state = self.state
        if state is not None:
            state.jobs = value

    # ── State property proxies (mirrors ChapterStudioPage properties) ──

    @property
    def mode(self) -> str:
        state = self.state
        return getattr(state, "mode", "manual") if state else "manual"

    @mode.setter
    def mode(self, value: str) -> None:
        state = self.state
        if state is not None:
            state.mode = value

    @property
    def auto_pilot_pending(self) -> bool:
        state = self.state
        return getattr(state, "auto_pilot_pending", False) if state else False

    @auto_pilot_pending.setter
    def auto_pilot_pending(self, value: bool) -> None:
        state = self.state
        if state is not None:
            state.auto_pilot_pending = value

    @property
    def auto_started(self) -> bool:
        state = self.state
        return getattr(state, "auto_started", False) if state else False

    @auto_started.setter
    def auto_started(self, value: bool) -> None:
        state = self.state
        if state is not None:
            state.auto_started = value

    @property
    def stopped_from_auto(self) -> bool:
        state = self.state
        return getattr(state, "stopped_from_auto", False) if state else False

    @stopped_from_auto.setter
    def stopped_from_auto(self, value: bool) -> None:
        state = self.state
        if state is not None:
            state.stopped_from_auto = value

    @property
    def stopped_mode(self) -> str:
        state = self.state
        return getattr(state, "stopped_mode", "auto") if state else "auto"

    @stopped_mode.setter
    def stopped_mode(self, value: str) -> None:
        state = self.state
        if state is not None:
            state.stopped_mode = value

    @property
    def auto_gen(self) -> int:
        state = self.state
        return getattr(state, "auto_gen", 0) if state else 0

    @auto_gen.setter
    def auto_gen(self, value: int) -> None:
        state = self.state
        if state is not None:
            state.auto_gen = value

    @property
    def notes_expanded_by_chapter(self) -> dict[tuple[str, int], bool]:
        state = self.state
        return getattr(state, "notes_expanded_by_chapter", {}) if state else {}

    @notes_expanded_by_chapter.setter
    def notes_expanded_by_chapter(self, value: dict[tuple[str, int], bool]) -> None:
        state = self.state
        if state is not None:
            state.notes_expanded_by_chapter = value

    @property
    def book_auto_skip_done(self) -> bool:
        state = self.state
        return getattr(state, "book_auto_skip_done", True) if state else True

    @book_auto_skip_done.setter
    def book_auto_skip_done(self, value: bool) -> None:
        state = self.state
        if state is not None:
            state.book_auto_skip_done = value

    @property
    def auto_repair_pending(self) -> bool:
        state = self.state
        return getattr(state, "auto_repair_pending", False) if state else False

    @auto_repair_pending.setter
    def auto_repair_pending(self, value: bool) -> None:
        state = self.state
        if state is not None:
            state.auto_repair_pending = value

    @property
    def auto_repair_attempts(self) -> dict[tuple[str, int], int]:
        state = self.state
        return getattr(state, "auto_repair_attempts", {}) if state else {}

    @auto_repair_attempts.setter
    def auto_repair_attempts(self, value: dict[tuple[str, int], int]) -> None:
        state = self.state
        if state is not None:
            state.auto_repair_attempts = value

    @property
    def last_submitted_checkpoint_id(self) -> str | None:
        state = self.state
        return getattr(state, "last_submitted_checkpoint_id", None) if state else None

    @last_submitted_checkpoint_id.setter
    def last_submitted_checkpoint_id(self, value: str | None) -> None:
        state = self.state
        if state is not None:
            state.last_submitted_checkpoint_id = value

    @property
    def notes_submitted(self) -> bool:
        state = self.state
        return getattr(state, "notes_submitted", False) if state else False

    @notes_submitted.setter
    def notes_submitted(self, value: bool) -> None:
        state = self.state
        if state is not None:
            state.notes_submitted = value

    @property
    def auto_chapter_prepared(self) -> bool:
        state = self.state
        return getattr(state, "auto_chapter_prepared", False) if state else False

    @auto_chapter_prepared.setter
    def auto_chapter_prepared(self, value: bool) -> None:
        state = self.state
        if state is not None:
            state.auto_chapter_prepared = value

    @property
    def auto_last_progress_at(self) -> float:
        state = self.state
        return getattr(state, "auto_last_progress_at", 0.0) if state else 0.0

    @auto_last_progress_at.setter
    def auto_last_progress_at(self, value: float) -> None:
        state = self.state
        if state is not None:
            state.auto_last_progress_at = value

    @property
    def auto_refresh_count(self) -> int:
        state = self.state
        return getattr(state, "auto_refresh_count", 0) if state else 0

    @auto_refresh_count.setter
    def auto_refresh_count(self, value: int) -> None:
        state = self.state
        if state is not None:
            state.auto_refresh_count = value

    @property
    def jobs_fingerprint(self) -> tuple[Any, ...]:
        state = self.state
        return getattr(state, "jobs_fingerprint", ()) if state else ()

    @jobs_fingerprint.setter
    def jobs_fingerprint(self, value: tuple[Any, ...]) -> None:
        state = self.state
        if state is not None:
            state.jobs_fingerprint = value

    @property
    def last_applied_memory_event_key(self) -> dict[str, tuple[str, str]]:
        state = self.state
        return getattr(state, "last_applied_memory_event_key", {}) if state else {}

    @last_applied_memory_event_key.setter
    def last_applied_memory_event_key(self, value: dict[str, tuple[str, str]]) -> None:
        state = self.state
        if state is not None:
            state.last_applied_memory_event_key = value


class StudioCoordinator(_PageDelegate):
    """Orchestrates chapter studio actions — delegation from mixin methods.

    Holds references to the page and provides methods that were previously
    defined in the mixin classes. The page delegates to this coordinator.
    """

    def __init__(self, page: Any) -> None:
        super().__init__(page)

    def on_state_changed(self, field_name: str, value: object) -> None:
        """React to state field changes — triggers targeted UI rebuilds."""
        page = self._page
        if page is None:
            return
        # Fields that affect the action panel
        if field_name in {
            "auto_pilot_pending",
            "auto_started",
            "stopped_from_auto",
            "stopped_mode",
            "auto_gen",
            "auto_chapter_prepared",
            "auto_refresh_count",
            "auto_repair_pending",
            "scheduled_retry_at",
            "mode",
        }:
            if hasattr(page, "_render_action_panel"):
                page._render_action_panel()
        # Fields that affect jobs panel
        elif field_name in {"jobs", "jobs_fingerprint"}:
            if hasattr(page, "_render_jobs_panel"):
                page._render_jobs_panel(getattr(page, "_jobs", []), loading=False)
        # Fields that affect inspector/checklists
        elif field_name in {"dismissed_warning_fingerprints", "auto_repair_attempts"}:
            if hasattr(page, "_render_inspector"):
                page._render_inspector()
            if hasattr(page, "_render_action_panel"):
                page._render_action_panel()

    @property
    def state_manager(self) -> StudioStateManager | None:
        value = getattr(self._page, "_state_manager", None)
        return value if isinstance(value, StudioStateManager) else None

    @property
    def state(self) -> Any:
        sm = self.state_manager
        return sm.state if sm else None

    @property
    def studio(self) -> Any:
        return getattr(self._page, "_studio", None)

    @property
    def workspace(self) -> Any:
        return getattr(self._page, "_workspace", None)

    @property
    def runtime(self) -> Any:
        return getattr(self._page, "_runtime", None)

    @property
    def jobs(self) -> list[Any]:
        sm = self.state_manager
        return sm.jobs if sm else []

    @property
    def book_level_jobs(self) -> list[Any]:
        return getattr(self._page, "_book_level_jobs", [])

    @property
    def memory_presenter(self) -> Any:
        return getattr(self._page, "_memory_presenter", None)


class StudioRenderer(_PageDelegate):
    """Renders chapter studio UI components."""

    def __init__(self, page: Any) -> None:
        super().__init__(page)

    @property
    def state_manager(self) -> StudioStateManager | None:
        value = getattr(self._page, "_state_manager", None)
        return value if isinstance(value, StudioStateManager) else None

    @property
    def state(self) -> Any:
        sm = self.state_manager
        return sm.state if sm else None

    @property
    def studio(self) -> Any:
        return getattr(self._page, "_studio", None)

    @property
    def workspace(self) -> Any:
        return getattr(self._page, "_workspace", None)

    @property
    def memory_presenter(self) -> Any:
        return getattr(self._page, "_memory_presenter", None)

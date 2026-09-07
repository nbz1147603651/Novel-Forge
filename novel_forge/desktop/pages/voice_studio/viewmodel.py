"""VoiceStudio ViewModel — pure business state and commands (Phase 10).

Extracts the mutable state and command logic from ``page.py`` into a
testable ViewModel.  The View (``VoiceStudioPage``) binds to ``changed``
signals and delegates user actions to command methods.

This module has NO Qt widget imports — only QtCore for signal bridging
via the ``ViewModel`` base class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.desktop.mvvm import Command, ViewModel

# ── Value types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GenerationProgress:
    """Snapshot of an in-progress TTS generation pipeline."""

    chapter_number: int = 0
    phase: str = ""  # "script" | "synthesize" | "assemble" | "idle"
    step_label: str = ""
    fraction: float = 0.0  # 0.0 – 1.0
    segment_index: int = -1
    segment_total: int = 0
    is_running: bool = False
    error: str | None = None


@dataclass(frozen=True)
class VoiceTeamSummary:
    """Lightweight projection of voice-team state for the View."""

    character_count: int = 0
    narrator_name: str = ""
    provider: str = ""
    voices_assigned: int = 0
    voices_total: int = 0


@dataclass(frozen=True)
class ScriptSummary:
    """Lightweight projection of dubbing-script state for the View."""

    chapter_number: int = 0
    segment_count: int = 0
    is_available: bool = False
    freshness: str = ""  # "fresh" | "stale" | "none"
    has_audio: bool = False


# ── ViewModel ─────────────────────────────────────────────────────────────────


class VoiceStudioViewModel(ViewModel):
    """Central state container for the Voice Studio page.

    Holds project context, voice-team data, script/audio state, and
    generation progress.  The View observes ``changed`` signals and
    renders accordingly.

    Commands (``generate_script``, ``synthesize``, ``export_audio``, etc.)
    are exposed as ``Command`` instances with ``can_execute`` gating.
    """

    def __init__(self) -> None:
        super().__init__()

        # ── Project context ──
        self._project_id: str = ""
        self._layout: Any = None  # ProjectLayout
        self._storage_root: Path | None = None

        # ── Voice team ──
        self._voice_team: Any = None  # VoiceTeamContract
        self._narrator_profile: Any = None  # NarratorVoiceProfile
        self._bible_characters: list[dict[str, Any]] = []

        # ── Script / Audio ──
        self._current_script: Any = None  # DubbingScript
        self._source_script_available: bool = False
        self._script_freshness: str = "none"
        self._current_audio_result: Any = None  # ChapterAudioResult
        self._current_timeline: Any = None
        self._active_chapter_number: int = 0

        # ── Generation pipeline ──
        self._progress = GenerationProgress()
        self._generating_chapter: int | None = None

        # ── Provider / settings ──
        self._provider: str = ""
        self._model: str = ""
        self._automation_mode: str = "standard"

        # ── Commands ──
        self.generate_script = Command(
            self._request_generate_script,
            self._can_generate_script,
        )
        self.synthesize = Command(
            self._request_synthesize,
            self._can_synthesize,
        )
        self.cancel = Command(
            self._request_cancel,
            self._can_cancel,
        )

    # ── Observable properties ─────────────────────────────────────────────

    @property
    def project_id(self) -> str:
        return self._project_id

    @project_id.setter
    def project_id(self, value: str) -> None:
        self._project_id = value
        self.notify("project_id")

    @property
    def progress(self) -> GenerationProgress:
        return self._progress

    @progress.setter
    def progress(self, value: GenerationProgress) -> None:
        self._progress = value
        self.notify("progress")

    @property
    def active_chapter_number(self) -> int:
        return self._active_chapter_number

    @active_chapter_number.setter
    def active_chapter_number(self, value: int) -> None:
        self._active_chapter_number = value
        self.notify("active_chapter_number")

    @property
    def automation_mode(self) -> str:
        return self._automation_mode

    @automation_mode.setter
    def automation_mode(self, value: str) -> None:
        self._automation_mode = value
        self.notify("automation_mode")

    # ── Derived read-only projections ─────────────────────────────────────

    @property
    def is_generating(self) -> bool:
        return self._progress.is_running

    @property
    def voice_team_summary(self) -> VoiceTeamSummary:
        if self._voice_team is None:
            return VoiceTeamSummary()
        entries = getattr(self._voice_team, "entries", [])
        assigned = sum(1 for e in entries if getattr(e, "voice_id", ""))
        narrator = getattr(self._narrator_profile, "display_name", "")
        return VoiceTeamSummary(
            character_count=len(self._bible_characters),
            narrator_name=narrator,
            provider=self._provider,
            voices_assigned=assigned,
            voices_total=len(entries),
        )

    @property
    def script_summary(self) -> ScriptSummary:
        if self._current_script is None:
            return ScriptSummary(
                chapter_number=self._active_chapter_number,
                is_available=False,
                freshness="none",
            )
        segments = getattr(self._current_script, "segments", [])
        return ScriptSummary(
            chapter_number=self._active_chapter_number,
            segment_count=len(segments),
            is_available=self._source_script_available,
            freshness=self._script_freshness,
            has_audio=self._current_audio_result is not None,
        )

    # ── State mutation methods (called by page or workers) ────────────────

    def set_project(self, project_id: str, layout: Any) -> None:
        """Update the active project context."""
        self._project_id = project_id
        self._layout = layout
        self.notify_many("project_id", "voice_team_summary", "script_summary")

    def set_storage_root(self, root: Path) -> None:
        self._storage_root = root

    def update_voice_team(
        self,
        voice_team: Any,
        narrator_profile: Any = None,
        bible_characters: list[dict[str, Any]] | None = None,
    ) -> None:
        """Replace voice-team state (after load or rebuild)."""
        self._voice_team = voice_team
        if narrator_profile is not None:
            self._narrator_profile = narrator_profile
        if bible_characters is not None:
            self._bible_characters = bible_characters
        self.notify("voice_team_summary")

    def update_script(
        self,
        script: Any,
        *,
        source_available: bool = True,
        freshness: str = "fresh",
    ) -> None:
        """Replace the current dubbing script."""
        self._current_script = script
        self._source_script_available = source_available
        self._script_freshness = freshness
        self.notify("script_summary")

    def update_audio_result(self, result: Any, timeline: Any = None) -> None:
        """Store the latest audio assembly result."""
        self._current_audio_result = result
        self._current_timeline = timeline
        self.notify("script_summary")

    def update_progress(
        self,
        *,
        phase: str = "",
        step_label: str = "",
        fraction: float = 0.0,
        segment_index: int = -1,
        segment_total: int = 0,
        is_running: bool = True,
        error: str | None = None,
    ) -> None:
        """Update generation progress (called from worker step callbacks)."""
        self._progress = GenerationProgress(
            chapter_number=self._active_chapter_number,
            phase=phase,
            step_label=step_label,
            fraction=fraction,
            segment_index=segment_index,
            segment_total=segment_total,
            is_running=is_running,
            error=error,
        )
        self.notify("progress")

    def finish_generation(self, *, error: str | None = None) -> None:
        """Mark generation as complete (or failed)."""
        self._progress = GenerationProgress(
            chapter_number=self._active_chapter_number,
            is_running=False,
            error=error,
        )
        self._generating_chapter = None
        self.notify("progress")

    def set_provider(self, provider: str, model: str = "") -> None:
        self._provider = provider
        self._model = model
        self.notify("voice_team_summary")

    # ── Command implementations ───────────────────────────────────────────

    def _can_generate_script(self) -> bool:
        return (
            not self._progress.is_running
            and self._project_id != ""
            and self._active_chapter_number > 0
        )

    def _request_generate_script(self) -> None:
        """Placeholder — the actual async work is triggered by the View
        calling the workspace execution layer.  This command gates the
        UI button state."""
        self._generating_chapter = self._active_chapter_number
        self.update_progress(phase="script", step_label="准备中…", is_running=True)

    def _can_synthesize(self) -> bool:
        return (
            not self._progress.is_running
            and self._source_script_available
            and self._active_chapter_number > 0
        )

    def _request_synthesize(self) -> None:
        self._generating_chapter = self._active_chapter_number
        self.update_progress(phase="synthesize", step_label="准备合成…", is_running=True)

    def _can_cancel(self) -> bool:
        return self._progress.is_running

    def _request_cancel(self) -> None:
        """Signal cancellation intent.  The View handles the actual worker
        cancellation and calls ``finish_generation()`` upon completion."""
        self.update_progress(
            phase=self._progress.phase,
            step_label="取消中…",
            fraction=self._progress.fraction,
            is_running=True,
        )

    # ── Serialization (for UI state persistence) ──────────────────────────

    def export_state(self) -> dict[str, Any]:
        """Export serializable state for workspace persistence."""
        return {
            "project_id": self._project_id,
            "active_chapter_number": self._active_chapter_number,
            "automation_mode": self._automation_mode,
            "provider": self._provider,
            "model": self._model,
        }

    def restore_state(self, payload: dict[str, Any]) -> None:
        """Restore state from a previously exported dict."""
        self._project_id = payload.get("project_id", "")
        self._active_chapter_number = payload.get("active_chapter_number", 0)
        self._automation_mode = payload.get("automation_mode", "standard")
        self._provider = payload.get("provider", "")
        self._model = payload.get("model", "")
        self.notify_many("project_id", "active_chapter_number", "automation_mode")


__all__ = [
    "VoiceStudioViewModel",
    "GenerationProgress",
    "VoiceTeamSummary",
    "ScriptSummary",
]

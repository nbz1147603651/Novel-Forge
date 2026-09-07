"""Tests for novel_forge.story_kernel.rules — StoryKernelConsistencyRules."""

from __future__ import annotations

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import CharacterState, PlotThreadDelta, PlotThreadState
from novel_forge.story_kernel.rules import StoryKernelConsistencyRules, ValidationResult
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    StoryKernel,
    TimelineAnchor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_kernel(**overrides: object) -> StoryKernel:
    """Build a minimal StoryKernel for testing."""
    defaults: dict[str, object] = {
        "project_id": "test-project",
        "current_chapter": 5,
        "entities": [],
        "promise_ledger": [],
        "timeline": [],
        "plot_threads": [],
    }
    defaults.update(overrides)
    return StoryKernel(**defaults)  # type: ignore[arg-type]


def _make_outcome(**overrides: object) -> ChapterOutcome:
    """Build a minimal ChapterOutcome for testing."""
    defaults: dict[str, object] = {
        "source_chapter": 6,
        "character_updates": {},
        "new_events": [],
        "foreshadowing_updates": [],
        "plot_thread_deltas": [],
    }
    defaults.update(overrides)
    return ChapterOutcome(**defaults)  # type: ignore[arg-type]


def _entity(
    name: str,
    *,
    status: str = "active",
    entity_type: str = "character",
    entity_id: str | None = None,
) -> Entity:
    """Helper to build a minimal Entity."""
    return Entity(
        entity_id=entity_id or f"ent-{name.lower()}",
        name=name,
        status=status,
        entity_type=entity_type,
    )


def _promise(
    entry_id: str,
    status: str = "planted",
    description: str = "test foreshadow",
) -> PromiseLedger:
    return PromiseLedger(
        entry_id=entry_id,
        description=description,
        status=status,
    )


def _timeline_event(chapter: int, event: str = "something happened") -> TimelineAnchor:
    return TimelineAnchor(
        anchor_id=f"evt-{chapter}",
        chapter=chapter,
        event=event,
    )


def _char_state(name: str, *, alive: bool = True) -> CharacterState:
    return CharacterState(name=name, alive=alive)


def _plot_thread(
    thread_id: str,
    *,
    title: str = "Thread",
    status: str = "active",
    last_touched_chapter: int = 3,
) -> PlotThreadState:
    return PlotThreadState(
        thread_id=thread_id,
        title=title,
        status=status,
        last_touched_chapter=last_touched_chapter,
    )


# ===========================================================================
# Layer 1 tests
# ===========================================================================


class TestLayer1TimelineMonotonicity:
    """Events with chapter < source_chapter are rejected."""

    def test_valid_timeline(self) -> None:
        kernel = _make_kernel()
        outcome = _make_outcome(
            source_chapter=6,
            new_events=[_timeline_event(6), _timeline_event(7)],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        timeline_viols = [v for v in result.violations if "Event" in v]
        assert timeline_viols == []

    def test_event_chapter_before_source(self) -> None:
        kernel = _make_kernel()
        outcome = _make_outcome(
            source_chapter=6,
            new_events=[_timeline_event(4, "flashback event")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("chapter 4" in v and "chapter 6" in v for v in result.violations)


class TestLayer1ChapterSequence:
    """source_chapter must equal kernel.current_chapter + 1."""

    def test_correct_sequence(self) -> None:
        kernel = _make_kernel(current_chapter=5)
        outcome = _make_outcome(source_chapter=6)
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        seq_viols = [v for v in result.violations if "Expected chapter" in v]
        assert seq_viols == []

    def test_wrong_sequence(self) -> None:
        kernel = _make_kernel(current_chapter=5)
        outcome = _make_outcome(source_chapter=8)
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("Expected chapter 6" in v and "got 8" in v for v in result.violations)


class TestLayer1InvalidCharacterNames:
    """System artifact names in character_updates are rejected."""

    def test_normal_name_accepted(self) -> None:
        kernel = _make_kernel()
        outcome = _make_outcome(
            character_updates={"Alice": _char_state("Alice")},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        name_viols = [v for v in result.violations if "leaked schema key" in v]
        assert name_viols == []

    def test_system_artifact_rejected(self) -> None:
        kernel = _make_kernel()
        outcome = _make_outcome(
            character_updates={"source_chapter": _char_state("source_chapter")},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("leaked schema key" in v for v in result.violations)


# ===========================================================================
# Layer 2 tests — dead characters
# ===========================================================================


class TestLayer2DeadCharacters:
    """Dead/destroyed entities must not be resurrected."""

    def test_dead_character_resurrection_blocked(self) -> None:
        """A destroyed entity reappearing as alive=True is a violation."""
        kernel = _make_kernel(
            entities=[_entity("Bob", status="destroyed")],
        )
        outcome = _make_outcome(
            source_chapter=6,
            character_updates={"Bob": _char_state("Bob", alive=True)},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("cannot be resurrected" in v for v in result.violations)

    def test_dead_character_appearing_as_dead_ok(self) -> None:
        """A destroyed entity with alive=False (ghost/memory) is fine."""
        kernel = _make_kernel(
            entities=[_entity("Bob", status="destroyed")],
        )
        outcome = _make_outcome(
            source_chapter=6,
            character_updates={"Bob": _char_state("Bob", alive=False)},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        resurrection_viols = [v for v in result.violations if "cannot be resurrected" in v]
        assert resurrection_viols == []

    def test_retired_character_resurrection_blocked(self) -> None:
        """Retired entities are also treated as dead."""
        kernel = _make_kernel(
            entities=[_entity("Carol", status="retired")],
        )
        outcome = _make_outcome(
            source_chapter=6,
            character_updates={"Carol": _char_state("Carol", alive=True)},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("cannot be resurrected" in v for v in result.violations)

    def test_active_character_update_ok(self) -> None:
        """An active entity can be updated freely."""
        kernel = _make_kernel(
            entities=[_entity("Alice", status="active")],
        )
        outcome = _make_outcome(
            source_chapter=6,
            character_updates={"Alice": _char_state("Alice", alive=True)},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        resurrection_viols = [v for v in result.violations if "cannot be resurrected" in v]
        assert resurrection_viols == []


class TestLayer2SuspiciousDeaths:
    """Previously-alive characters being killed raises a warning."""

    def test_new_death_warning(self) -> None:
        kernel = _make_kernel(
            entities=[_entity("Alice", status="active")],
        )
        outcome = _make_outcome(
            source_chapter=6,
            character_updates={"Alice": _char_state("Alice", alive=False)},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("being marked as dead" in w for w in result.warnings)

    def test_new_character_introduced_dead_warning(self) -> None:
        kernel = _make_kernel(entities=[])
        outcome = _make_outcome(
            source_chapter=6,
            character_updates={"GhostKid": _char_state("GhostKid", alive=False)},
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("being introduced as dead" in w for w in result.warnings)


# ===========================================================================
# Layer 3 tests — foreshadowing lifecycle
# ===========================================================================


class TestLayer3ForeshadowingLifecycle:
    """Promise status transitions must follow the valid transition table."""

    def test_planted_to_hinted_ok(self) -> None:
        kernel = _make_kernel(
            promise_ledger=[_promise("fs-1", status="planted")],
        )
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="hinted")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        fs_viols = [v for v in result.violations if "Foreshadowing" in v]
        assert fs_viols == []

    def test_planted_to_paid_ok(self) -> None:
        kernel = _make_kernel(
            promise_ledger=[_promise("fs-1", status="planted")],
        )
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="paid")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        fs_viols = [v for v in result.violations if "Foreshadowing" in v]
        assert fs_viols == []

    def test_planted_to_broken_ok(self) -> None:
        kernel = _make_kernel(
            promise_ledger=[_promise("fs-1", status="planted")],
        )
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="broken")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        fs_viols = [v for v in result.violations if "Foreshadowing" in v]
        assert fs_viols == []

    def test_hinted_to_paid_ok(self) -> None:
        kernel = _make_kernel(
            promise_ledger=[_promise("fs-1", status="hinted")],
        )
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="paid")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        fs_viols = [v for v in result.violations if "Foreshadowing" in v]
        assert fs_viols == []

    def test_paid_terminal_violation(self) -> None:
        """Paid is terminal — cannot transition to anything."""
        kernel = _make_kernel(
            promise_ledger=[_promise("fs-1", status="paid")],
        )
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="hinted")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("invalid transition" in v and "paid" in v for v in result.violations)

    def test_broken_terminal_violation(self) -> None:
        """Broken is terminal — cannot transition to anything."""
        kernel = _make_kernel(
            promise_ledger=[_promise("fs-1", status="broken")],
        )
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="planted")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("invalid transition" in v and "broken" in v for v in result.violations)

    def test_planted_to_hinted_then_paid_chain(self) -> None:
        """Multi-step chain: planted → hinted → paid is valid."""
        # Step 1: planted → hinted
        kernel1 = _make_kernel(
            promise_ledger=[_promise("fs-1", status="planted")],
        )
        outcome1 = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="hinted")],
        )
        result1 = StoryKernelConsistencyRules().validate(kernel1, outcome1)
        assert not any("Foreshadowing" in v for v in result1.violations)

        # Step 2: hinted → paid
        kernel2 = _make_kernel(
            promise_ledger=[_promise("fs-1", status="hinted")],
        )
        outcome2 = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="paid")],
        )
        result2 = StoryKernelConsistencyRules().validate(kernel2, outcome2)
        assert not any("Foreshadowing" in v for v in result2.violations)

    def test_same_status_noop(self) -> None:
        """Same-status transition is a no-op and should not violate."""
        kernel = _make_kernel(
            promise_ledger=[_promise("fs-1", status="hinted")],
        )
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-1", status="hinted")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        fs_viols = [v for v in result.violations if "Foreshadowing" in v]
        assert fs_viols == []

    def test_unknown_entry_no_violation(self) -> None:
        """Foreshadowing update for an entry not in the kernel is not flagged."""
        kernel = _make_kernel(promise_ledger=[])
        outcome = _make_outcome(
            foreshadowing_updates=[_promise("fs-new", status="hinted")],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        fs_viols = [v for v in result.violations if "Foreshadowing" in v]
        assert fs_viols == []


# ===========================================================================
# Layer 4 tests — plot thread consistency
# ===========================================================================


class TestLayer4PlotThreadConsistency:
    """Plot thread deltas must not regress status or chapter number."""

    def test_status_regression_blocked(self) -> None:
        kernel = _make_kernel(
            plot_threads=[_plot_thread("pt-1", title="Main Arc", status="resolved")],
        )
        new_thread = PlotThreadState(
            thread_id="pt-1", title="Main Arc", status="active", last_touched_chapter=6
        )
        outcome = _make_outcome(
            plot_thread_deltas=[PlotThreadDelta(thread_id="pt-1", thread=new_thread)],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("status regression" in v for v in result.violations)

    def test_chapter_regression_blocked(self) -> None:
        kernel = _make_kernel(
            plot_threads=[_plot_thread("pt-1", title="Subplot", last_touched_chapter=5)],
        )
        new_thread = PlotThreadState(
            thread_id="pt-1", title="Subplot", status="active", last_touched_chapter=3
        )
        outcome = _make_outcome(
            plot_thread_deltas=[PlotThreadDelta(thread_id="pt-1", thread=new_thread)],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert any("last_touched_chapter decreased" in v for v in result.violations)

    def test_valid_progression_ok(self) -> None:
        kernel = _make_kernel(
            plot_threads=[_plot_thread("pt-1", title="Arc", last_touched_chapter=3)],
        )
        new_thread = PlotThreadState(
            thread_id="pt-1", title="Arc", status="active", last_touched_chapter=6
        )
        outcome = _make_outcome(
            plot_thread_deltas=[PlotThreadDelta(thread_id="pt-1", thread=new_thread)],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        pt_viols = [v for v in result.violations if "Plot thread" in v]
        assert pt_viols == []

    def test_new_thread_no_violation(self) -> None:
        """A delta for a thread not in kernel is accepted (new thread)."""
        kernel = _make_kernel(plot_threads=[])
        new_thread = PlotThreadState(thread_id="pt-new", title="New Arc", status="active")
        outcome = _make_outcome(
            plot_thread_deltas=[PlotThreadDelta(thread_id="pt-new", thread=new_thread)],
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        pt_viols = [v for v in result.violations if "Plot thread" in v]
        assert pt_viols == []


# ===========================================================================
# Integration / edge-case tests
# ===========================================================================


class TestValidationResult:
    """ValidationResult aggregates violations and warnings correctly."""

    def test_empty_result(self) -> None:
        kernel = _make_kernel()
        outcome = _make_outcome()
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        assert isinstance(result, ValidationResult)

    def test_multiple_violations_accumulated(self) -> None:
        """Multiple rule failures produce multiple violations in one pass."""
        kernel = _make_kernel(
            current_chapter=5,
            entities=[_entity("Dead", status="destroyed")],
        )
        outcome = _make_outcome(
            source_chapter=99,  # wrong sequence
            character_updates={"Dead": _char_state("Dead", alive=True)},  # resurrection
        )
        result = StoryKernelConsistencyRules().validate(kernel, outcome)
        # Should have both a chapter-sequence violation and a resurrection violation
        assert len(result.violations) >= 2

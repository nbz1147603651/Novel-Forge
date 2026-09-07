"""Unit tests for workflow job UI components and sub-state hints."""

from __future__ import annotations

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.workflow.jobs import (
    _has_blueprint_coherence_stage_in_history,
    _indicator_state_for_job,
    _init_coherence_stage_step,
    _init_long_blueprint_to_outline_completed_key,
    _is_init_long_blueprint_to_outline_transition,
    _visible_steps_for_kind,
)
from novel_forge.desktop.progress import display_step_name_for_job


class TestDisplayStepNameForJob:
    """Tests for display_step_name_for_job() sub-state hint logic."""

    def _make_job(
        self,
        kind: str = "book_consistency",
        current_step: str = "",
        events: list[DesktopJobEvent] | None = None,
        **kwargs,
    ) -> DesktopJobRecord:
        return DesktopJobRecord(
            job_id="test-job",
            kind=kind,
            label="test",
            current_step=current_step,
            status=DesktopJobState.RUNNING,
            events=events or [],
            **kwargs,
        )

    def test_book_consistency_repair_progress_shows_chapter_progress(self) -> None:
        """book_consistency_repair_progress should show chapter progress."""
        job = self._make_job(
            kind="book_consistency",
            current_step="book_consistency_repair_progress",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="book_consistency_repair_progress",
                    payload={"chapter_number": 5, "processed": 3, "total": 10},
                )
            ],
        )
        result = display_step_name_for_job(job)
        assert "第 5 章" in result
        assert "3" in result
        assert "10" in result

    def test_draft_step_shows_word_count(self) -> None:
        """draft step should show word count when payload available."""
        job = self._make_job(
            kind="run_chapter",
            current_step="draft",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="draft",
                    payload={"word_count": 3456},
                )
            ],
        )
        result = display_step_name_for_job(job)
        assert "3456" in result
        assert "字" in result

    def test_edit_step_shows_iteration(self) -> None:
        """edit_N step should show iteration round."""
        job = self._make_job(
            kind="run_chapter",
            current_step="edit_2",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="edit_2",
                    payload={"iteration": 2},
                )
            ],
        )
        result = display_step_name_for_job(job)
        assert "第 2 轮" in result

    def test_edit_budget_trimmed_not_treated_as_edit_n(self) -> None:
        """edit_budget_trimmed should NOT get round suffix."""
        job = self._make_job(
            kind="run_chapter",
            current_step="edit_budget_trimmed",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="edit_budget_trimmed",
                    payload={"iteration": 1},
                )
            ],
        )
        result = display_step_name_for_job(job)
        assert "第" not in result or "轮" not in result

    def test_transient_step_falls_back_to_last_real_step(self) -> None:
        """Transient steps should fall back to last non-transient step."""
        job = self._make_job(
            kind="book_consistency",
            current_step="token_escalation",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="book_consistency_repair_progress",
                    payload={"chapter_number": 5, "processed": 3, "total": 10},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="token_escalation",
                    payload={},
                ),
            ],
        )
        result = display_step_name_for_job(job)
        assert "第 5 章" in result or "3" in result

    def test_unknown_step_returns_base_name(self) -> None:
        """Unknown steps should return base display name."""
        job = self._make_job(
            kind="run_chapter",
            current_step="some_unknown_step",
            events=[],
        )
        result = display_step_name_for_job(job)
        assert isinstance(result, str)
        assert len(result) > 0


class TestInitCoherenceStageStep:
    """Tests for _init_coherence_stage_step() mapping."""

    def test_blueprint_coherence_stage_returns_plan_blueprint(self) -> None:
        """blueprint_coherence stage should map to plan_blueprint."""
        payload = {"stage": "blueprint_coherence", "artifact": "blueprint"}
        result = _init_coherence_stage_step(
            "adjudicate_init_conflict_candidates", payload
        )
        assert result == "plan_blueprint"

    def test_outline_inheritance_stage_returns_plan_outline(self) -> None:
        """outline_inheritance stage should map to plan_outline."""
        payload = {"stage": "outline_inheritance", "artifact": "outline"}
        result = _init_coherence_stage_step(
            "adjudicate_outline_inheritance", payload
        )
        assert result == "plan_outline"

    def test_non_coherence_step_returns_empty(self) -> None:
        """Non-coherence steps should return empty string."""
        payload = {"stage": "blueprint_coherence"}
        result = _init_coherence_stage_step("plan_blueprint", payload)
        assert result == ""

    def test_missing_stage_with_artifact_fallback(self) -> None:
        """Missing stage should fall back to artifact mapping."""
        payload = {"artifact": "blueprint"}
        result = _init_coherence_stage_step(
            "adjudicate_init_conflict_candidates", payload
        )
        assert result == "plan_blueprint"

    def test_malformed_payload_returns_empty(self) -> None:
        """Malformed payloads should not crash and return empty."""
        # Non-dict payload
        result = _init_coherence_stage_step(
            "adjudicate_init_conflict_candidates", "not a dict"
        )
        assert result == ""
        # None payload
        result = _init_coherence_stage_step(
            "adjudicate_init_conflict_candidates", None
        )
        assert result == ""
        # Empty dict payload
        result = _init_coherence_stage_step(
            "adjudicate_init_conflict_candidates", {}
        )
        assert result == ""


class TestHasBlueprintCoherenceStageInHistory:
    """Tests for _has_blueprint_coherence_stage_in_history() helper."""

    def _make_job(
        self, events: list[DesktopJobEvent] | None = None
    ) -> DesktopJobRecord:
        return DesktopJobRecord(
            job_id="test-job",
            kind="init_long",
            label="test",
            current_step="",
            status=DesktopJobState.RUNNING,
            events=events or [],
        )

    def test_finds_blueprint_coherence_in_history(self) -> None:
        """Should find blueprint_coherence stage in event history."""
        job = self._make_job(
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="adjudicate_init_conflict_candidates",
                    payload={"stage": "blueprint_coherence"},
                )
            ]
        )
        assert _has_blueprint_coherence_stage_in_history(job) is True

    def test_returns_false_for_other_stages(self) -> None:
        """Should return False when only other stages exist."""
        job = self._make_job(
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="adjudicate_outline_inheritance",
                    payload={"stage": "outline_inheritance"},
                )
            ]
        )
        assert _has_blueprint_coherence_stage_in_history(job) is False

    def test_returns_false_for_empty_history(self) -> None:
        """Should return False when no events exist."""
        job = self._make_job(events=[])
        assert _has_blueprint_coherence_stage_in_history(job) is False

    def test_returns_false_for_non_coherence_events(self) -> None:
        """Should return False when no coherence events exist."""
        job = self._make_job(
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="plan_blueprint",
                    payload={"status": "done"},
                )
            ]
        )
        assert _has_blueprint_coherence_stage_in_history(job) is False

    def test_malformed_payload_in_history(self) -> None:
        """Malformed payloads in history should not crash."""
        job = self._make_job(
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="adjudicate_init_conflict_candidates",
                    payload="not a dict",
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="extract_init_coherence_claims",
                    payload={"stage": "blueprint_coherence"},
                ),
            ]
        )
        # Should skip malformed event and find the valid one
        assert _has_blueprint_coherence_stage_in_history(job) is True


class TestIsInitLongBlueprintToOutlineTransition:
    """Tests for _is_init_long_blueprint_to_outline_transition()."""

    def _make_job(
        self, events: list[DesktopJobEvent] | None = None
    ) -> DesktopJobRecord:
        return DesktopJobRecord(
            job_id="test-job",
            kind="init_long",
            label="test",
            current_step="",
            status=DesktopJobState.RUNNING,
            events=events or [],
        )

    def test_known_transition_steps_return_true(self) -> None:
        """Steps in _INIT_LONG_BLUEPRINT_TO_OUTLINE_RAW_STEPS return True."""
        for step in [
            "plan_blueprint_validated",
            "plan_blueprint_repaired",
            "plan_blueprint_fragments",
            "derive_init_coherence_profile",
            "adjudicate_blueprint_coherence",
            "repair_init_artifact_patch",
        ]:
            assert _is_init_long_blueprint_to_outline_transition(step, {}) is True

    def test_coherence_step_with_blueprint_coherence_stage(self) -> None:
        """Coherence steps with blueprint_coherence stage return True."""
        payload = {"stage": "blueprint_coherence"}
        assert (
            _is_init_long_blueprint_to_outline_transition(
                "adjudicate_init_conflict_candidates", payload
            )
            is True
        )

    def test_coherence_step_with_other_stage_returns_false(self) -> None:
        """Coherence steps with other stages return False."""
        payload = {"stage": "outline_inheritance"}
        assert (
            _is_init_long_blueprint_to_outline_transition(
                "adjudicate_outline_inheritance", payload
            )
            is False
        )

    def test_batch_step_missing_stage_fallback_to_history(self) -> None:
        """Batch steps with missing stage should fallback to event history."""
        job = self._make_job(
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="adjudicate_init_conflict_candidates",
                    payload={"stage": "blueprint_coherence"},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="adjudicate_init_conflict_candidates_1_5",
                    payload={},  # Missing stage
                ),
            ]
        )
        # With job parameter, should fallback to history
        assert (
            _is_init_long_blueprint_to_outline_transition(
                "adjudicate_init_conflict_candidates_1_5", {}, job=job
            )
            is True
        )

    def test_missing_stage_no_history_returns_false(self) -> None:
        """Missing stage with no history should return False."""
        assert (
            _is_init_long_blueprint_to_outline_transition(
                "adjudicate_init_conflict_candidates", {}
            )
            is False
        )

    def test_non_transition_step_returns_false(self) -> None:
        """Non-transition steps should return False."""
        assert (
            _is_init_long_blueprint_to_outline_transition("plan_outline", {})
            is False
        )


class TestInitLongBlueprintToOutlineCompletedKey:
    """Tests for _init_long_blueprint_to_outline_completed_key()."""

    def _make_job(
        self,
        current_step: str = "",
        events: list[DesktopJobEvent] | None = None,
    ) -> DesktopJobRecord:
        return DesktopJobRecord(
            job_id="test-job",
            kind="init_long",
            label="test",
            current_step=current_step,
            status=DesktopJobState.RUNNING,
            events=events or [],
        )

    def _visible_keys(self) -> set[str]:
        steps = _visible_steps_for_kind("init_long")
        return {step.key for step in steps}

    def test_coherence_step_returns_plan_blueprint(self) -> None:
        """Coherence step with blueprint_coherence returns plan_blueprint."""
        job = self._make_job(
            current_step="adjudicate_init_conflict_candidates",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="adjudicate_init_conflict_candidates",
                    payload={"stage": "blueprint_coherence"},
                )
            ],
        )
        result = _init_long_blueprint_to_outline_completed_key(
            job, self._visible_keys()
        )
        assert result == "plan_blueprint"

    def test_returns_empty_after_outline_entered(self) -> None:
        """Should return empty once outline steps have been entered."""
        job = self._make_job(
            current_step="plan_outline",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="plan_outline_batch_1_5",
                    payload={},
                )
            ],
        )
        result = _init_long_blueprint_to_outline_completed_key(
            job, self._visible_keys()
        )
        assert result == ""

    def test_returns_empty_for_non_init_long(self) -> None:
        """Should return empty for non-init_long jobs."""
        job = DesktopJobRecord(
            job_id="test-job",
            kind="run_chapter",
            label="test",
            current_step="draft",
            status=DesktopJobState.RUNNING,
            events=[],
        )
        result = _init_long_blueprint_to_outline_completed_key(
            job, self._visible_keys()
        )
        assert result == ""

    def test_fallback_to_history_when_current_not_coherence(self) -> None:
        """Should fallback to history when current step isn't coherence."""
        job = self._make_job(
            current_step="model_call_update",  # Non-coherence step
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="adjudicate_init_conflict_candidates",
                    payload={"stage": "blueprint_coherence"},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="model_call_update",
                    payload={},
                ),
            ],
        )
        result = _init_long_blueprint_to_outline_completed_key(
            job, self._visible_keys()
        )
        # Should fallback to history and find blueprint_coherence
        assert result == "plan_blueprint"

    def test_batch_step_with_missing_stage(self) -> None:
        """Batch step with missing stage should use history fallback."""
        job = self._make_job(
            current_step="adjudicate_init_conflict_candidates_1_5",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="adjudicate_init_conflict_candidates",
                    payload={"stage": "blueprint_coherence"},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="adjudicate_init_conflict_candidates_1_5",
                    payload={},  # Missing stage
                ),
            ],
        )
        result = _init_long_blueprint_to_outline_completed_key(
            job, self._visible_keys()
        )
        assert result == "plan_blueprint"


class TestIndicatorStateForJobBlueprintCoherence:
    """Tests for _indicator_state_for_job() during blueprint_coherence."""

    def _make_job(
        self,
        current_step: str = "",
        events: list[DesktopJobEvent] | None = None,
        status: DesktopJobState = DesktopJobState.RUNNING,
    ) -> DesktopJobRecord:
        return DesktopJobRecord(
            job_id="test-job",
            kind="init_long",
            label="test",
            current_step=current_step,
            status=status,
            events=events or [],
        )

    def test_between_completed_shows_plan_blueprint_as_done(self) -> None:
        """During blueprint_coherence, plan_blueprint should be 'done'."""
        job = self._make_job(
            current_step="adjudicate_init_conflict_candidates",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="plan_blueprint",
                    payload={},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="adjudicate_init_conflict_candidates",
                    payload={"stage": "blueprint_coherence"},
                ),
            ],
        )
        steps = _visible_steps_for_kind("init_long")
        state = _indicator_state_for_job(job, steps)

        # Find plan_blueprint index
        blueprint_idx = None
        for i, step in enumerate(steps):
            if step.key == "plan_blueprint":
                blueprint_idx = i
                break
        assert blueprint_idx is not None

        # plan_blueprint should be 'done', not 'active'
        assert state.dot_states[blueprint_idx] == "done"

    def test_steps_after_plan_blueprint_are_pending(self) -> None:
        """Steps after plan_blueprint should be 'pending' during coherence."""
        job = self._make_job(
            current_step="adjudicate_init_conflict_candidates",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="plan_blueprint",
                    payload={},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="adjudicate_init_conflict_candidates",
                    payload={"stage": "blueprint_coherence"},
                ),
            ],
        )
        steps = _visible_steps_for_kind("init_long")
        state = _indicator_state_for_job(job, steps)

        # Find plan_blueprint index
        blueprint_idx = None
        for i, step in enumerate(steps):
            if step.key == "plan_blueprint":
                blueprint_idx = i
                break
        assert blueprint_idx is not None

        # Steps after plan_blueprint should be 'pending'
        for i in range(blueprint_idx + 1, len(steps)):
            assert state.dot_states[i] == "pending", (
                f"Step {steps[i].key} at index {i} should be pending"
            )

    @staticmethod
    def _step_state(state: object, steps: list[object], key: str) -> str:
        index = next(index for index, step in enumerate(steps) if step.key == key)
        return state.dot_states[index]

    def test_style_profile_failure_stays_failed_after_later_steps_begin(self) -> None:
        """A non-blocking profile failure must not look like a saved artifact."""
        job = self._make_job(
            current_step="plan_outline",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="profile_style_failed",
                    payload={"error": "provider unavailable"},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="plan_outline",
                    payload={},
                ),
            ],
        )
        steps = _visible_steps_for_kind("init_long")
        state = _indicator_state_for_job(job, steps)

        assert self._step_state(state, steps, "profile_style") == "failed"

    def test_style_profile_skip_stays_skipped_after_later_steps_begin(self) -> None:
        """A disabled style profile must not look like an available artifact."""
        job = self._make_job(
            current_step="plan_outline",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="profile_style_skipped",
                    payload={},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="plan_outline",
                    payload={},
                ),
            ],
        )
        steps = _visible_steps_for_kind("init_long")
        state = _indicator_state_for_job(job, steps)

        assert self._step_state(state, steps, "profile_style") == "skipped"

    def test_style_profile_success_after_failure_restores_done_state(self) -> None:
        """A successful retry or resume must override an earlier failure marker."""
        job = self._make_job(
            current_step="plan_outline",
            events=[
                DesktopJobEvent(
                    at="2024-01-01T00:00:00",
                    step="profile_style_failed",
                    payload={"error": "temporary failure"},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:01",
                    step="profile_style_resumed",
                    payload={},
                ),
                DesktopJobEvent(
                    at="2024-01-01T00:00:02",
                    step="plan_outline",
                    payload={},
                ),
            ],
        )
        steps = _visible_steps_for_kind("init_long")
        state = _indicator_state_for_job(job, steps)

        assert self._step_state(state, steps, "profile_style") == "done"

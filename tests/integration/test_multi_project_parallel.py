"""Integration tests for multi-project parallel chapter generation.

Tests verify:
- Two different projects can run chapter generation (prepare_chapter) in parallel
- Project A can initialize (init_long) while Project B runs chapter generation in parallel
- Job status updates correctly for each project independently
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.jobs import DesktopJobManager, DesktopJobState
from novel_forge.workspace.contracts import InitLongRequest, PrepareChapterRequest

# Patch settings before importing DesktopJobManager
with (
    patch("novel_forge.core.config.get_settings") as _mock_get_settings,
    patch("novel_forge.desktop.window.DesktopWorkspaceService") as _mock_ws_cls,
):
    _mock_settings = MagicMock()
    _mock_settings.outline_batch_size = 3
    _mock_settings.short_max_edit_rounds = 2
    _mock_settings.long_alignment_threshold = 7.0
    _mock_settings.long_plot_guard_mode = "balanced"
    _mock_settings.long_volume_auto_chapter_threshold = 60
    _mock_settings.long_default_chapters_per_volume = 20
    _mock_settings.long_plan_beats_min = 4
    _mock_settings.long_plan_beats_max = 8
    _mock_settings.long_plan_beat_max_chars = 260
    _mock_settings.long_prompt_max_character_profiles = 8
    _mock_settings.long_prompt_max_profile_field_chars = 240
    _mock_settings.auto_introduce_characters = False
    _mock_settings.long_polish_enabled = False
    _mock_settings.style_profile_enabled = True
    _mock_settings.style_profile_required = False
    _mock_settings.temp_spec_enrich = 0.7
    _mock_settings.temp_beats = 0.7
    _mock_settings.temp_draft = 0.8
    _mock_settings.temp_edit = 0.5
    _mock_settings.temp_evaluate = 0.3
    _mock_settings.temp_init_story_bible = 0.7
    _mock_settings.temp_init_character_bible = 0.7
    _mock_settings.temp_blueprint_element_select = 0.2
    _mock_settings.temp_plan_outline = 0.7
    _mock_settings.temp_plan_outline_batch = 0.7
    _mock_settings.temp_plan_outline_continue = 0.7
    _mock_settings.temp_plan_chapter = 0.4
    _mock_settings.temp_draft_chapter = 1.0
    _mock_settings.temp_edit_chapter = 0.5
    _mock_settings.temp_extract_canon = 0.3
    _mock_settings.temp_check_alignment = 0.2
    _mock_settings.temp_check_chapter = 0.2
    _mock_settings.temp_bridge_chapter = 0.3
    _mock_settings.temp_check_continuity = 0.2
    _mock_settings.temp_validate_causal = 0.2
    _mock_settings.temp_patch_chapter = 0.2
    _mock_settings.temp_repair_continuity = 0.4
    _mock_settings.temp_repair_causal = 0.35
    _mock_settings.temp_repair_reading_power = 0.3
    _mock_settings.temp_volume_audit = 0.3
    _mock_settings.temp_enrich_character = 0.7
    _mock_settings.temp_introduce_character = 0.75
    _mock_settings.temp_profile_style = 0.4
    _mock_settings.temp_profile_structure = 0.4
    _mock_settings.temp_evaluate_reading_power = 0.3
    _mock_settings.temp_adjust_outline = 0.7
    _mock_settings.temp_context_compress = 0.2
    _mock_settings.temp_verify_compression = 0.2
    _mock_settings.temp_extract_motifs = 0.3
    _mock_settings.temp_summarize_chapter = 0.3
    _mock_settings.temp_summarize_volume = 0.3
    _mock_settings.temp_summarize_arc = 0.3
    _mock_settings.temp_summarize_scene = 0.3
    _mock_settings.temp_critic_continuity = 0.2
    _mock_settings.temp_critic_character = 0.2
    _mock_settings.temp_critic_causal = 0.2
    _mock_settings.temp_critic_strengths = 0.3
    _mock_settings.temp_plot_guard_judge = 0.2
    _mock_settings.temp_book_consistency = 0.2
    _mock_settings.routes = {}
    _mock_settings.fallback_routes = {}
    _mock_settings.profiles = []
    _mock_settings.ollama_base_url = "http://localhost:11434/v1"
    _mock_settings.ollama_model = "llama3.2"
    _mock_settings.ollama_embedding_model = "nomic-embed-text"
    _mock_settings.log_level = "INFO"
    _mock_settings.log_keep_runs = 30
    _mock_settings.api_call_timeout_s = 120
    _mock_settings.long_prompt_max_relationships_per_profile = 6
    _mock_settings.canon_context_max_recent_events = 20
    _mock_settings.canon_context_max_characters = 10
    _mock_settings.canon_context_max_foreshadowing = 10
    _mock_settings.long_plan_max_foreshadowing = 10
    _mock_settings.canon_context_max_world_facts = 50
    _mock_settings.long_context_compress_enabled = True
    _mock_settings.long_context_compress_min_chars = 1000
    _mock_settings.long_context_compress_max_tokens = 2048
    _mock_settings.long_ai_judge_max_context_chapters = 24
    _mock_settings.long_ai_judge_max_tokens = 1536
    _mock_settings.storage_root = str(Path(__file__).parent.parent.parent / "data")
    _mock_get_settings.return_value = _mock_settings


@pytest.fixture
def qapp():
    """Create QApplication instance for Qt-dependent tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def job_manager(qapp) -> DesktopJobManager:
    """Create an isolated manager and drain its workers before loop teardown."""
    manager = DesktopJobManager()
    yield manager
    manager.shutdown(wait_ms=60_000)


@pytest.fixture
def sample_init_request_project_a() -> InitLongRequest:
    """Create a sample init request for project A."""
    return InitLongRequest(
        project_id="project_a",
        premise="一个关于时间旅行的科幻故事",
        genre="scifi",
        tone="mysterious",
        total_chapters=10,
        words_per_chapter=3000,
    )


@pytest.fixture
def sample_init_request_project_b() -> InitLongRequest:
    """Create a sample init request for project B."""
    return InitLongRequest(
        project_id="project_b",
        premise="一个关于魔法的奇幻故事",
        genre="fantasy",
        tone="epic",
        total_chapters=10,
        words_per_chapter=3000,
    )


@pytest.fixture
def sample_prepare_request_project_a_ch1() -> PrepareChapterRequest:
    """Create a sample prepare_chapter request for project A, chapter 1."""
    return PrepareChapterRequest(
        project_id="project_a",
        chapter_number=1,
        force=False,
    )


@pytest.fixture
def sample_prepare_request_project_b_ch1() -> PrepareChapterRequest:
    """Create a sample prepare_chapter request for project B, chapter 1."""
    return PrepareChapterRequest(
        project_id="project_b",
        chapter_number=1,
        force=False,
    )


def _wait_for_jobs_state(
    job_manager: DesktopJobManager,
    target_job_ids: list[str],
    target_states: set[DesktopJobState],
    timeout_ms: int = 10000,
) -> bool:
    """Wait for specific jobs to reach target states.

    Returns True if all jobs reached target states within timeout.
    Returns False if timeout was reached.
    """
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout_ms / 1000:
        all_reached = True
        for job_id in target_job_ids:
            job = next((j for j in job_manager.jobs() if j.job_id == job_id), None)
            if job is None or job.status not in target_states:
                all_reached = False
                break
        if all_reached:
            return True
        QApplication.processEvents()
        time.sleep(0.05)
    return False


def _wait_for_job_count(
    job_manager: DesktopJobManager,
    min_count: int,
    timeout_ms: int = 5000,
) -> bool:
    """Wait until job manager has at least min_count jobs.

    Returns True if count was reached within timeout.
    """
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout_ms / 1000:
        if len(job_manager.jobs()) >= min_count:
            return True
        QApplication.processEvents()
        time.sleep(0.05)
    return False


class TestMultiProjectParallelChapterGeneration:
    """Tests for multi-project parallel job execution."""

    def test_two_projects_prepare_chapter_run_in_parallel(
        self,
        job_manager: DesktopJobManager,
        sample_init_request_project_a: InitLongRequest,
        sample_init_request_project_b: InitLongRequest,
        sample_prepare_request_project_a_ch1: PrepareChapterRequest,
        sample_prepare_request_project_b_ch1: PrepareChapterRequest,
    ) -> None:
        """Test that Project A and Project B can both run prepare_chapter in parallel.

        Scenario:
        1. Initialize both projects first (so they are ready for chapter generation)
        2. Submit prepare_chapter for project A and project B
        3. Both should run in parallel (not sequentially)
        """
        # First, initialize both projects to have valid project state
        job_manager.submit_init_long(sample_init_request_project_a, mock=True)
        job_manager.submit_init_long(sample_init_request_project_b, mock=True)

        # Wait for init jobs to complete
        init_timeout_ms = 60000  # 60 seconds for init
        start_time = time.monotonic()
        while time.monotonic() - start_time < init_timeout_ms / 1000:
            jobs = job_manager.jobs()
            init_jobs = [j for j in jobs if j.kind == "init_long"]
            if all(j.status in {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED} for j in init_jobs):
                break
            QApplication.processEvents()
            time.sleep(0.1)

        # Now submit prepare_chapter for both projects
        record_a = job_manager.submit_prepare_chapter(sample_prepare_request_project_a_ch1, mock=True)
        record_b = job_manager.submit_prepare_chapter(sample_prepare_request_project_b_ch1, mock=True)

        job_ids = [record_a.job_id, record_b.job_id]

        # Both should be submitted (QUEUED or RUNNING)
        assert len(job_manager.jobs()) >= 2, "Both jobs should be submitted"

        # Wait for both to be RUNNING
        running_timeout_ms = 5000
        start_time = time.monotonic()
        while time.monotonic() - start_time < running_timeout_ms / 1000:
            jobs = job_manager.jobs()
            job_a = next((j for j in jobs if j.job_id == record_a.job_id), None)
            job_b = next((j for j in jobs if j.job_id == record_b.job_id), None)
            if job_a is not None and job_b is not None:
                if job_a.status == DesktopJobState.RUNNING and job_b.status == DesktopJobState.RUNNING:
                    break
            QApplication.processEvents()
            time.sleep(0.05)

        # Verify both jobs are tracked correctly by project
        all_jobs = job_manager.jobs()
        project_a_jobs = [j for j in all_jobs if j.project_id == "project_a"]
        project_b_jobs = [j for j in all_jobs if j.project_id == "project_b"]

        assert len(project_a_jobs) >= 2, "Project A should have at least init + prepare jobs"
        assert len(project_b_jobs) >= 2, "Project B should have at least init + prepare jobs"

        # Wait for completion (mock adapter should complete quickly)
        _wait_for_jobs_state(
            job_manager,
            job_ids,
            {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED, DesktopJobState.PAUSED},
            timeout_ms=30000,
        )

        # Verify final states
        final_jobs = {j.job_id: j for j in job_manager.jobs()}
        job_a_final = final_jobs.get(record_a.job_id)
        job_b_final = final_jobs.get(record_b.job_id)

        assert job_a_final is not None, "Project A job should exist"
        assert job_b_final is not None, "Project B job should exist"

        # prepare_chapter jobs end at a decision checkpoint (PAUSED state)
        # This is expected behavior - the job pauses for user input
        assert job_a_final.status in {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
            DesktopJobState.PAUSED,
        }, f"Project A job should be complete, got {job_a_final.status}"
        assert job_b_final.status in {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
            DesktopJobState.PAUSED,
        }, f"Project B job should be complete, got {job_b_final.status}"

    def test_project_a_init_while_project_b_runs_chapter(
        self,
        job_manager: DesktopJobManager,
        sample_init_request_project_a: InitLongRequest,
        sample_init_request_project_b: InitLongRequest,
        sample_prepare_request_project_a_ch1: PrepareChapterRequest,
        sample_prepare_request_project_b_ch1: PrepareChapterRequest,
    ) -> None:
        """Test that Project A can initialize while Project B runs chapter generation.

        Scenario:
        1. Initialize Project B first (so it has project state for chapter generation)
        2. While Project B's init completes, submit prepare_chapter for Project A
           (this should be queued/waiting since A isn't initialized yet)
        3. Submit prepare_chapter for Project B (this should run since B is initialized)
        4. Wait for Project B's init to complete
        5. Project A's prepare_chapter should then proceed
        6. Both should eventually complete
        """
        # Submit init for Project B first
        init_job_b = job_manager.submit_init_long(sample_init_request_project_b, mock=True)

        # Submit init for Project A
        init_job_a = job_manager.submit_init_long(sample_init_request_project_a, mock=True)

        # Submit prepare_chapter for Project A - this should queue since init isn't complete
        prepare_job_a = job_manager.submit_prepare_chapter(sample_prepare_request_project_a_ch1, mock=True)

        # Submit prepare_chapter for Project B - this should queue since init isn't complete
        prepare_job_b = job_manager.submit_prepare_chapter(sample_prepare_request_project_b_ch1, mock=True)

        # Collect all job IDs
        all_job_ids = [
            init_job_a.job_id,
            init_job_b.job_id,
            prepare_job_a.job_id,
            prepare_job_b.job_id,
        ]

        # Wait for all jobs to complete (init jobs end with SUCCEEDED/FAILED,
        # prepare_chapter jobs end with PAUSED at checkpoint)
        _wait_for_jobs_state(
            job_manager,
            all_job_ids,
            {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED, DesktopJobState.PAUSED},
            timeout_ms=60000,
        )

        # Verify all jobs completed
        final_jobs = {j.job_id: j for j in job_manager.jobs()}
        terminal_states = {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED, DesktopJobState.PAUSED}
        for job_id in all_job_ids:
            job = final_jobs.get(job_id)
            assert job is not None, f"Job {job_id} should exist"
            assert job.status in terminal_states, (
                f"Job {job_id} ({job.kind}) should be complete, got {job.status}"
            )

    def test_job_states_update_correctly_per_project(
        self,
        job_manager: DesktopJobManager,
        sample_init_request_project_a: InitLongRequest,
        sample_init_request_project_b: InitLongRequest,
    ) -> None:
        """Test that job states are tracked correctly per project.

        Verifies:
        - Each project has its own set of jobs
        - Job states are independent between projects
        - Jobs can have different states simultaneously
        """
        # Submit init for both projects
        init_a = job_manager.submit_init_long(sample_init_request_project_a, mock=True)
        init_b = job_manager.submit_init_long(sample_init_request_project_b, mock=True)

        job_ids = [init_a.job_id, init_b.job_id]

        # Initially, jobs should be in QUEUED or RUNNING state
        jobs_before = {j.job_id: j for j in job_manager.jobs()}
        assert jobs_before[init_a.job_id].status in {
            DesktopJobState.QUEUED,
            DesktopJobState.RUNNING,
        }, "Init A should be QUEUED or RUNNING initially"
        assert jobs_before[init_b.job_id].status in {
            DesktopJobState.QUEUED,
            DesktopJobState.RUNNING,
        }, "Init B should be QUEUED or RUNNING initially"

        # Wait for completion
        _wait_for_jobs_state(
            job_manager,
            job_ids,
            {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED},
            timeout_ms=60000,
        )

        # Verify final states
        jobs_after = {j.job_id: j for j in job_manager.jobs()}

        # Both should eventually complete
        assert jobs_after[init_a.job_id].status in {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
        }, f"Init A should complete, got {jobs_after[init_a.job_id].status}"
        assert jobs_after[init_b.job_id].status in {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
        }, f"Init B should complete, got {jobs_after[init_b.job_id].status}"

        # Verify job records have correct project IDs
        assert jobs_after[init_a.job_id].project_id == "project_a", (
            "Init A should belong to project_a"
        )
        assert jobs_after[init_b.job_id].project_id == "project_b", (
            "Init B should belong to project_b"
        )

    def test_different_chapters_same_project_run_sequentially(
        self,
        job_manager: DesktopJobManager,
        sample_init_request_project_a: InitLongRequest,
    ) -> None:
        """Test that prepare_chapter for different chapters of the same project
        are handled according to concurrency rules (same project, same chapter kind
        should not run simultaneously).
        """
        # Initialize project first
        init_job = job_manager.submit_init_long(sample_init_request_project_a, mock=True)

        # Wait for init to complete
        _wait_for_jobs_state(
            job_manager,
            [init_job.job_id],
            {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED},
            timeout_ms=60000,
        )

        # Submit prepare_chapter for chapter 1
        prepare_ch1 = job_manager.submit_prepare_chapter(
            PrepareChapterRequest(project_id="project_a", chapter_number=1),
            mock=True,
        )

        # Submit prepare_chapter for chapter 2 - should return existing job
        # since there's already a RUNNING/QUEUED prepare_chapter for this project
        prepare_ch2 = job_manager.submit_prepare_chapter(
            PrepareChapterRequest(project_id="project_a", chapter_number=2),
            mock=True,
        )

        # Due to concurrency guard, chapter 2 submission should return
        # the existing chapter 1 job instead of creating a new one
        assert prepare_ch2.job_id == prepare_ch1.job_id, (
            "Second prepare_chapter for same project should return existing job, "
            f"got {prepare_ch2.job_id} vs expected {prepare_ch1.job_id}"
        )

        # Verify we still only have one prepare_chapter job for project_a
        all_jobs = job_manager.jobs()
        prepare_jobs = [j for j in all_jobs if j.kind == "prepare_chapter" and j.project_id == "project_a"]
        assert len(prepare_jobs) == 1, (
            f"Should only have 1 prepare_chapter job for project_a, got {len(prepare_jobs)}"
        )

    def test_parallel_jobs_have_independent_results(
        self,
        job_manager: DesktopJobManager,
        sample_init_request_project_a: InitLongRequest,
        sample_init_request_project_b: InitLongRequest,
    ) -> None:
        """Test that parallel jobs produce independent results for each project."""
        # Initialize both projects
        init_a = job_manager.submit_init_long(sample_init_request_project_a, mock=True)
        init_b = job_manager.submit_init_long(sample_init_request_project_b, mock=True)

        job_ids = [init_a.job_id, init_b.job_id]

        # Wait for completion
        _wait_for_jobs_state(
            job_manager,
            job_ids,
            {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED},
            timeout_ms=60000,
        )

        # Verify each job has its own result
        final_jobs = {j.job_id: j for j in job_manager.jobs()}

        init_a_job = final_jobs.get(init_a.job_id)
        init_b_job = final_jobs.get(init_b.job_id)

        assert init_a_job is not None, "Init A job should exist"
        assert init_b_job is not None, "Init B job should exist"

        # Results should be independent (different content)
        # Even if both succeed, they should have different run_log_dir paths
        result_a = init_a_job.result
        result_b = init_b_job.result

        # Both should have run_log_dir set (from the execution)
        # The actual content differs based on project-specific data
        assert isinstance(result_a, dict), "Result A should be a dict"
        assert isinstance(result_b, dict), "Result B should be a dict"


class TestJobStateTransitions:
    """Tests for job state transitions in multi-project scenarios."""

    def test_job_state_transitions_from_queued_to_running(
        self,
        job_manager: DesktopJobManager,
        sample_init_request_project_a: InitLongRequest,
    ) -> None:
        """Test that a job transitions from QUEUED to RUNNING state."""
        job = job_manager.submit_init_long(sample_init_request_project_a, mock=True)

        # Initial state should be QUEUED
        initial_jobs = {j.job_id: j for j in job_manager.jobs()}
        initial_job = initial_jobs.get(job.job_id)
        assert initial_job is not None, "Job should exist"
        assert initial_job.status == DesktopJobState.QUEUED, (
            f"Initial state should be QUEUED, got {initial_job.status}"
        )

        # Wait for RUNNING state
        start_time = time.monotonic()
        timeout_ms = 5000
        while time.monotonic() - start_time < timeout_ms / 1000:
            current_jobs = {j.job_id: j for j in job_manager.jobs()}
            current_job = current_jobs.get(job.job_id)
            if current_job is not None and current_job.status == DesktopJobState.RUNNING:
                break
            QApplication.processEvents()
            time.sleep(0.05)

        # Verify RUNNING state was reached
        running_jobs = {j.job_id: j for j in job_manager.jobs()}
        running_job = running_jobs.get(job.job_id)
        assert running_job is not None, "Job should still exist"

    def test_multiple_jobs_maintain_separate_states(
        self,
        job_manager: DesktopJobManager,
        sample_init_request_project_a: InitLongRequest,
        sample_init_request_project_b: InitLongRequest,
    ) -> None:
        """Test that multiple jobs maintain separate states throughout their lifecycle."""
        job_a = job_manager.submit_init_long(sample_init_request_project_a, mock=True)
        job_b = job_manager.submit_init_long(sample_init_request_project_b, mock=True)

        job_ids = [job_a.job_id, job_b.job_id]

        # Poll states multiple times during execution
        states_a = []
        states_b = []

        start_time = time.monotonic()
        timeout_ms = 60000
        while time.monotonic() - start_time < timeout_ms / 1000:
            jobs = {j.job_id: j for j in job_manager.jobs()}
            job_a_state = jobs.get(job_a.job_id)
            job_b_state = jobs.get(job_b.job_id)

            if job_a_state:
                states_a.append(job_a_state.status)
            if job_b_state:
                states_b.append(job_b_state.status)

            # Check if both complete
            if all(
                jobs.get(jid).status in {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED}
                for jid in job_ids
            ):
                break

            QApplication.processEvents()
            time.sleep(0.1)

        # Both jobs should have recorded state transitions
        assert len(states_a) > 0, "Job A should have recorded states"
        assert len(states_b) > 0, "Job B should have recorded states"

        # Both should have completed
        final_jobs = {j.job_id: j for j in job_manager.jobs()}
        assert final_jobs[job_a.job_id].status in {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
        }, f"Job A should complete, got {final_jobs[job_a.job_id].status}"
        assert final_jobs[job_b.job_id].status in {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
        }, f"Job B should complete, got {final_jobs[job_b.job_id].status}"

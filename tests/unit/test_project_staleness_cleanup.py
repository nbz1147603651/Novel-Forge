"""Tests for chapter cleanup pruning chapter-scoped aggregate artifacts."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    ChapterCleanupConvergenceError,
    invalidate_all_tts_audio_derivatives,
    invalidate_chapter_tts_artifacts,
    invalidate_downstream_generated_artifacts,
    record_upstream_artifact_revision,
    regenerate_from_chapter,
    regenerate_from_chapter_async,
    scoped_stale_chapters,
    stale_chapter_cutoff,
)
from novel_forge.story_kernel.store import StoryKernelStore
from novel_forge.workspace.book_ops.execution_book_entry import (
    _refresh_book_audit_staleness_markers,
)


def test_cleanup_prunes_element_progress_even_without_chapter_files(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    tmp_storage.save_json(
        layout.element_progress_path,
        {
            "schema_version": "1.0",
            "updated_at": "2026-01-01T00:00:00Z",
            "chapters": {
                "1": {
                    "chapter_number": 1,
                    "results": [{"element_id": "e_hit", "status": "hit"}],
                    "arbiter": {"reviewed_count": 0, "changed_count": 0},
                },
                "3": {
                    "chapter_number": 3,
                    "results": [{"element_id": "e_miss", "status": "miss"}],
                    "arbiter": {"reviewed_count": 2, "changed_count": 1},
                },
                "6": {
                    "chapter_number": 6,
                    "results": [{"element_id": "e_weak", "status": "weak"}],
                    "arbiter": {"reviewed_count": 3, "changed_count": 2},
                },
            },
            "totals": {"scheduled": 99, "hit": 99, "weak": 99, "miss": 99},
            "pending_element_ids": ["x", "y"],
            "arbiter_totals": {"runs": 99, "reviewed": 99, "changed": 99},
        },
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=3,
        delete_chapter_files=True,
    )

    # No chapter_* files existed, but stale aggregate chapter data is still invalidated.
    assert invalidated == [6]

    payload = tmp_storage.load_json(layout.element_progress_path)
    assert set(payload["chapters"].keys()) == {"1", "3"}
    assert payload["totals"] == {"scheduled": 0, "hit": 1, "weak": 0, "miss": 1}
    assert payload["pending_element_ids"] == ["e_miss"]
    assert payload["arbiter_totals"] == {"runs": 1, "reviewed": 2, "changed": 1}


def test_cleanup_invalidates_tts_while_preserving_stale_final_text(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("tts_staleness"))
    layout.ensure_dirs()
    chapter_number = 2
    chapter_path = layout.chapter_path(chapter_number)
    chapter_path.write_text("需要保留供用户查看的旧终稿", encoding="utf-8")

    metadata_path = layout.reports_dir / "chapter_002_tts_metadata.json"
    script_path = layout.tts_dubbing_script_path(chapter_number)
    audio_dir = layout.tts_audio_dir(chapter_number)
    take_dir = layout.tts_candidate_take_dir(chapter_number, "stale")
    result_path = layout.tts_audio_result_path(chapter_number)
    sound_resolution_path = layout.tts_sound_resolution_path(chapter_number)
    sound_generation_path = layout.tts_sound_generation_report_path(chapter_number)
    timeline_path = layout.tts_speech_timeline_path(chapter_number)
    mix_plan_path = layout.tts_mix_plan_path(chapter_number)
    render_report_path = layout.tts_mix_render_report_path(chapter_number)
    quality_report_path = layout.tts_audio_quality_report_path(chapter_number)
    metadata_path.write_text("{}", encoding="utf-8")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("{}", encoding="utf-8")
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "seg_0000.mp3").write_bytes(b"old audio")
    take_dir.mkdir(parents=True, exist_ok=True)
    (take_dir / "seg_0000.mp3").write_bytes(b"old audition")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text("{}", encoding="utf-8")
    sound_resolution_path.parent.mkdir(parents=True, exist_ok=True)
    sound_resolution_path.write_text("{}", encoding="utf-8")
    sound_generation_path.parent.mkdir(parents=True, exist_ok=True)
    sound_generation_path.write_text("{}", encoding="utf-8")
    for derivative in (
        timeline_path,
        mix_plan_path,
        render_report_path,
        quality_report_path,
    ):
        derivative.parent.mkdir(parents=True, exist_ok=True)
        derivative.write_text("{}", encoding="utf-8")
    tmp_storage.save_json(
        layout.tts_progress_path,
        {"chapter_number": chapter_number},
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=1,
        delete_chapter_files=False,
    )

    assert invalidated == [chapter_number]
    assert chapter_path.exists()
    assert not metadata_path.exists()
    assert not script_path.exists()
    assert not audio_dir.exists()
    assert not layout.tts_take_dir(chapter_number).exists()
    assert not result_path.exists()
    assert not sound_resolution_path.exists()
    assert not sound_generation_path.exists()
    assert not timeline_path.exists()
    assert not mix_plan_path.exists()
    assert not render_report_path.exists()
    assert not quality_report_path.exists()
    assert not layout.tts_progress_path.exists()


def test_voice_contract_change_invalidates_audio_but_preserves_scripts(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("voice_contract_change"))
    layout.ensure_dirs()
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("{}", encoding="utf-8")
    preview_dir = layout.tts_audio_dir(0)
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / "preview.mp3").write_bytes(b"preview")
    audio_dir = layout.tts_audio_dir(1)
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "seg_0000.mp3").write_bytes(b"chapter audio")
    take_dir = layout.tts_candidate_take_dir(1, "stale")
    take_dir.mkdir(parents=True, exist_ok=True)
    (take_dir / "seg_0000.mp3").write_bytes(b"candidate")
    result_path = layout.tts_audio_result_path(1)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text("{}", encoding="utf-8")
    tmp_storage.save_json(layout.tts_progress_path, {"chapter_number": 1})

    removed = invalidate_all_tts_audio_derivatives(layout)

    assert removed
    assert script_path.exists()
    assert preview_dir.exists()
    assert not audio_dir.exists()
    assert not layout.tts_take_dir(1).exists()
    assert not result_path.exists()
    assert not layout.tts_progress_path.exists()


def test_chapter_tts_invalidation_preserves_other_chapter_checkpoints(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("tts_chapter_checkpoints"))
    layout.ensure_dirs()
    first = layout.tts_progress_path_for_chapter(1)
    second = layout.tts_progress_path_for_chapter(2)
    tmp_storage.save_json(first, {"chapter_number": 1})
    tmp_storage.save_json(second, {"chapter_number": 2})

    removed = invalidate_chapter_tts_artifacts(layout, 1)

    assert first in removed
    assert not first.exists()
    assert second.exists()


def test_book_audit_repair_refreshes_downstream_staleness_markers(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("book_audit_repair"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        {"project_id": "book_audit_repair", "current_chapter": 3},
    )
    for chapter_number in range(1, 4):
        tmp_storage.save_text(
            layout.chapter_path(chapter_number),
            f"第 {chapter_number} 章正文",
        )

    context_path = layout.chapter_state_packet_path(2)
    tmp_storage.save_json(context_path, {"chapter": 2})
    old_mtime = 1_700_000_000
    new_mtime = old_mtime + 60
    os.utime(context_path, (old_mtime, old_mtime))
    os.utime(layout.chapter_path(1), (new_mtime, new_mtime))

    assert stale_chapter_cutoff(tmp_storage, layout) == 2

    _refresh_book_audit_staleness_markers(
        storage=tmp_storage,
        layout=layout,
        auto_repair_payload={
            "details": [
                {"chapter_number": 1, "status": "applied", "applied": True},
            ],
        },
    )

    assert stale_chapter_cutoff(tmp_storage, layout) is None


def test_cleanup_merges_file_deletions_with_aggregate_pruning(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    # Real chapter artifact to be deleted.
    layout.chapter_path(5).write_text("chapter 5", encoding="utf-8")

    # Aggregate tracker still contains a much newer stale chapter.
    tmp_storage.save_json(
        layout.element_progress_path,
        {
            "schema_version": "1.0",
            "updated_at": "2026-01-01T00:00:00Z",
            "chapters": {
                "4": {"chapter_number": 4, "results": [{"element_id": "e4", "status": "hit"}]},
                "8": {"chapter_number": 8, "results": [{"element_id": "e8", "status": "miss"}]},
            },
            "totals": {"scheduled": 0, "hit": 0, "weak": 0, "miss": 0},
            "pending_element_ids": [],
            "arbiter_totals": {"runs": 0, "reviewed": 0, "changed": 0},
        },
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=4,
        delete_chapter_files=True,
    )

    assert invalidated == [5, 8]
    assert not layout.chapter_path(5).exists()
    payload = tmp_storage.load_json(layout.element_progress_path)
    assert set(payload["chapters"].keys()) == {"4"}


def test_cleanup_removes_reading_power_and_canon_outcome_artifacts(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    layout.chapter_path(2).write_text("chapter 2", encoding="utf-8")
    tmp_storage.save_json(layout.reading_power_report_path(2), {"overall_score": 8.5})
    tmp_storage.save_json(
        layout.chapter_canon_outcome_path(2),
        {"source_chapter": 2, "chapter_summary": "stale"},
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=1,
        delete_chapter_files=True,
    )

    assert invalidated == [2]
    assert not layout.chapter_path(2).exists()
    assert not layout.reading_power_report_path(2).exists()
    assert not layout.chapter_canon_outcome_path(2).exists()


def test_cleanup_deletes_memory_diagnostics_and_quality_gate_reports(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    layout.chapter_path(3).write_text("chapter 3", encoding="utf-8")
    tmp_storage.save_json(
        layout.chapter_memory_diagnostics_path(3),
        {"chapter": 3, "diagnostics": "stale"},
    )
    tmp_storage.save_json(
        layout.quality_gate_report_path(3),
        {"chapter": 3, "quality_score": 0.9},
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=2,
        delete_chapter_files=True,
    )

    assert invalidated == [3]
    assert not layout.chapter_memory_diagnostics_path(3).exists()
    assert not layout.quality_gate_report_path(3).exists()


def _ledger_entry(chapter_number: int) -> dict:
    return {
        "entry_id": f"state_{chapter_number}",
        "chapter_number": chapter_number,
        "candidate_id": f"cand_{chapter_number}",
        "delta_type": "event",
        "summary": f"第{chapter_number}章状态",
        "state_update": {
            "candidate_id": f"cand_{chapter_number}",
            "state_path": f"plot.chapter_{chapter_number}",
            "value": f"kept_{chapter_number}",
        },
        "decision": {
            "candidate_id": f"cand_{chapter_number}",
            "verdict": "accept",
            "confidence": 0.9,
        },
        "evidence": [],
    }


def test_cleanup_prunes_narrative_state_artifacts_and_indexes(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    layout.chapter_path(3).write_text("chapter 3", encoding="utf-8")
    tmp_storage.save_json(layout.critic_report_path(3), {"chapter": 3})
    tmp_storage.save_json(layout.state_adjudication_report_path(3), {"chapter_number": 3})
    tmp_storage.save_json(
        layout.narrative_state_dir / "chapter_003_state_adjudication.json",
        {"chapter_number": 3},
    )
    tmp_storage.save_json(
        layout.narrative_state_dir / "evidence" / "chapter_003_evidence.json",
        {"chapter_number": 3},
    )
    tmp_storage.save_json(
        layout.narrative_state_dir / "adjudication_report_index.json",
        {"reports": [{"chapter_number": 2}, {"chapter_number": 3}]},
    )
    (layout.narrative_state_dir / "state_ledger.jsonl").write_text(
        "\n".join(json.dumps(_ledger_entry(chapter), ensure_ascii=False) for chapter in (2, 3))
        + "\n",
        encoding="utf-8",
    )
    tmp_storage.save_json(
        layout.narrative_state_dir / "pending_queue.json",
        {
            "pending_items": [
                {"pending_id": "p2", "chapter_number": 2, "summary": "keep"},
                {"pending_id": "p3", "chapter_number": 3, "summary": "drop"},
            ]
        },
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=2,
        delete_chapter_files=True,
    )

    assert invalidated == [3]
    assert not layout.critic_report_path(3).exists()
    assert not layout.state_adjudication_report_path(3).exists()
    assert not (layout.narrative_state_dir / "chapter_003_state_adjudication.json").exists()
    assert not (layout.narrative_state_dir / "evidence" / "chapter_003_evidence.json").exists()

    report_index = tmp_storage.load_json(
        layout.narrative_state_dir / "adjudication_report_index.json"
    )
    assert report_index["reports"] == [{"chapter_number": 2}]

    ledger_lines = (
        (layout.narrative_state_dir / "state_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert [json.loads(line)["chapter_number"] for line in ledger_lines] == [2]

    pending = tmp_storage.load_json(layout.narrative_state_dir / "pending_queue.json")
    assert pending["pending_items"] == [
        {"pending_id": "p2", "chapter_number": 2, "summary": "keep"}
    ]
    projection = tmp_storage.load_json(layout.narrative_state_dir / "story_state_projection.json")
    assert projection["last_chapter"] == 2
    memory_index = tmp_storage.load_json(layout.memory_dir / "narrative_state_index.json")
    assert memory_index["last_chapter"] == 2


def test_cleanup_prunes_chapter_scoped_aggregate_artifacts_without_files(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    tmp_storage.save_json(layout.forbidden_repetition_index_path, {"2": ["keep"], "4": ["drop"]})
    (layout.states_dir / "chapter_audit_log.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"chapter_number": 2, "risk_level": "low"}, ensure_ascii=False),
                json.dumps({"chapter_number": 4, "risk_level": "high"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (layout.narrative_state_dir / "state_ledger.jsonl").write_text(
        json.dumps(_ledger_entry(4), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=3,
        delete_chapter_files=True,
    )

    assert invalidated == [4]
    assert tmp_storage.load_json(layout.forbidden_repetition_index_path) == {"2": ["keep"]}
    audit_lines = (
        (layout.states_dir / "chapter_audit_log.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert [json.loads(line)["chapter_number"] for line in audit_lines] == [2]
    assert (layout.narrative_state_dir / "state_ledger.jsonl").read_text(encoding="utf-8") == ""
    projection = tmp_storage.load_json(layout.narrative_state_dir / "story_state_projection.json")
    assert projection["last_chapter"] == 0


def test_cleanup_deletes_macro_guard_hint_files(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    layout.chapter_path(4).write_text("chapter 4", encoding="utf-8")
    hint_path = layout.states_dir / "macro_guard_hint_ch4.json"
    hint_path.write_text('{"chapter": 4, "hints": []}', encoding="utf-8")

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=3,
        delete_chapter_files=True,
    )

    assert invalidated == [4]
    assert not hint_path.exists()


def test_cleanup_deletes_macro_guard_report_and_alert(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    layout.chapter_path(5).write_text("chapter 5", encoding="utf-8")
    report_path = layout.states_dir / "macro_guard_report_ch5.json"
    alert_path = layout.states_dir / "macro_guard_alert_ch5.json"
    report_path.write_text('{"chapter": 5, "report": "audit"}', encoding="utf-8")
    alert_path.write_text('{"chapter": 5, "alert": "warning"}', encoding="utf-8")

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=4,
        delete_chapter_files=True,
    )

    assert invalidated == [5]
    assert not report_path.exists()
    assert not alert_path.exists()


def test_cleanup_deletes_resume_and_auxiliary_state_artifacts(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    layout.chapter_path(3).write_text("chapter 3", encoding="utf-8")
    stale_paths = [
        layout.contract_execution_report_path(3),
        layout.expression_repetition_report_path(3),
        layout.arc_liveness_report_path(3),
        layout.milestone_window_report_path(3),
        layout.stage_visibility_diagnostics_path(3),
        layout.reports_dir / "chapter_003_guidance_plan.json",
        layout.reports_dir / "chapter_003_guidance_contract_audit.json",
        layout.states_dir / "chapter_003_repair_continuity_progress.json",
        layout.states_dir / "chapter_003_repair_causal_progress.json",
        layout.states_dir / "chapter_003_causal_repair_attempts.json",
        layout.states_dir / "chapter_003_rewrite_context.json",
        layout.states_dir / "chapter_0003_before_repair_checkpoint.json",
        layout.states_dir / "chapter_3_memory_pending.json",
        layout.states_dir / "word_count_archive_gate" / "chapter_003.json",
        layout.states_dir / "macro_guard_report_ch3.json",
        layout.states_dir / "macro_guard_hint_ch3.json",
        layout.states_dir / "macro_guard_alert_ch3.json",
        layout.states_dir / "reading_power_window_ch3.json",
        layout.states_dir / "reading_power_gate_ch3.json",
        layout.states_dir / "reading_power_constraints_ch3.json",
        layout.states_dir / "targeted_repairs_ch3.json",
    ]
    for path in stale_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"chapter_number": 3}', encoding="utf-8")

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=2,
        delete_chapter_files=True,
    )

    assert invalidated == [3]
    for path in stale_paths:
        assert not path.exists(), path


def test_cleanup_deletes_auxiliary_artifacts_without_chapter_files(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    pending_marker = layout.states_dir / "chapter_4_memory_pending.json"
    constraints = layout.states_dir / "reading_power_constraints_ch4.json"
    word_count_gate = layout.states_dir / "word_count_archive_gate" / "chapter_004.json"
    for path in (pending_marker, constraints, word_count_gate):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"chapter_number": 4}', encoding="utf-8")

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=3,
        delete_chapter_files=True,
    )

    assert invalidated == [4]
    assert not pending_marker.exists()
    assert not constraints.exists()
    assert not word_count_gate.exists()


def test_cleanup_deletes_discovered_chapter_scoped_artifacts(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    stale_paths = [
        layout.reports_dir / "chapter_003_continuity_verification.json",
        layout.reports_dir / "chapter_003_future_extra.json",
        layout.reports_dir / "revisions" / "chapter_003_humanize_layer.json",
        layout.plans_dir / "chapter_003_side_plan.json",
        layout.states_dir / "chapter_003_custom_state.json",
        layout.narrative_state_dir / "chapter_003_custom_state.json",
    ]
    kept_path = layout.reports_dir / "chapter_002_future_extra.json"
    for path in [*stale_paths, kept_path]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"chapter_number": 3}', encoding="utf-8")

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=2,
        delete_chapter_files=True,
    )

    assert invalidated == [3]
    for path in stale_paths:
        assert not path.exists(), path
    assert kept_path.exists()


def test_cleanup_prunes_progression_ledger(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()

    tmp_storage.save_json(
        layout.progression_ledger_path,
        {
            "entries": [
                {"entry_id": "pg2", "chapter_number": 2, "progression": "keep"},
                {"entry_id": "pg4", "chapter_number": 4, "progression": "drop"},
            ],
            "last_chapter": 4,
        },
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=3,
        delete_chapter_files=True,
    )

    assert invalidated == [4]
    payload = tmp_storage.load_json(layout.progression_ledger_path)
    assert payload["entries"] == [{"entry_id": "pg2", "chapter_number": 2, "progression": "keep"}]
    assert payload["last_chapter"] == 2


def test_regenerate_from_chapter_consumes_upstream_revision_marker(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()
    tmp_storage.save_json(layout.characters_path, {"characters": ["updated"]})
    tmp_storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 1})
    layout.chapter_path(1).write_text("old chapter 1", encoding="utf-8")

    record_upstream_artifact_revision(
        tmp_storage,
        layout,
        artifact_kind="character_bible",
        scope="whole_book",
        reason="test_character_update",
        apply_invalidation=False,
    )
    assert scoped_stale_chapters(tmp_storage, layout) == {1}

    invalidated = regenerate_from_chapter(tmp_storage, layout, from_chapter=1)

    assert invalidated == [1]
    assert not layout.chapter_path(1).exists()

    # A newly generated replacement chapter should not be marked stale by the
    # already-consumed upstream revision marker.
    layout.chapter_path(1).write_text("new chapter 1", encoding="utf-8")
    tmp_storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 1})
    assert scoped_stale_chapters(tmp_storage, layout) == set()


@pytest.mark.asyncio
async def test_sync_regeneration_adapter_fails_before_mutation_in_async_context(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("async_cleanup_guard"))
    layout.ensure_dirs()
    chapter_path = layout.chapter_path(1)
    chapter_path.write_text("必须保留", encoding="utf-8")

    with pytest.raises(RuntimeError, match="regenerate_from_chapter_async"):
        regenerate_from_chapter(tmp_storage, layout, from_chapter=1)

    assert chapter_path.read_text(encoding="utf-8") == "必须保留"


@pytest.mark.asyncio
async def test_async_regeneration_removes_publication_only_artifact(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("publication_cleanup"))
    layout.ensure_dirs()
    publication = layout.chapter_publication_path(3)
    tmp_storage.save_json(
        publication,
        {"chapter_number": 3, "publication_status": "ready", "deliverable": True},
    )

    invalidated = await regenerate_from_chapter_async(
        tmp_storage,
        layout,
        from_chapter=3,
    )

    assert invalidated == [3]
    assert not publication.exists()


@pytest.mark.asyncio
async def test_async_regeneration_fails_closed_without_exact_kernel_snapshot(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("missing_kernel_snapshot"))
    layout.ensure_dirs()
    tmp_storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 1})
    chapter_path = layout.chapter_path(1)
    chapter_path.write_text("旧正文", encoding="utf-8")
    store = StoryKernelStore(layout.story_kernel_db_path)
    try:
        await store.init_db()
        kernel = await store.create_kernel(layout.root.name)
        kernel.current_chapter = 1
        await store.save_kernel(kernel)
    finally:
        await store.close()

    with pytest.raises(ChapterCleanupConvergenceError, match="缺少第 0 章"):
        await regenerate_from_chapter_async(tmp_storage, layout, from_chapter=1)

    # Files may already be removed, but the visible JSON watermark must not
    # falsely advertise a completed rollback when the StoryKernel is still 1.
    assert not chapter_path.exists()
    assert tmp_storage.load_json(layout.canon_dir / "canon_current.json")["current_chapter"] == 1


def test_cleanup_consumes_manual_revision_marker_for_regenerated_downstream(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("test_project"))
    layout.ensure_dirs()
    chapter_1 = layout.chapter_path(1)
    chapter_2 = layout.chapter_path(2)
    chapter_1.write_text("chapter 1 revised", encoding="utf-8")
    chapter_2.write_text("old chapter 2", encoding="utf-8")
    tmp_storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 2})
    status_path = layout.states_dir / "final_revision_status" / "chapter_001.json"
    tmp_storage.save_json(
        status_path,
        {
            "chapter_number": 1,
            "source_text_hash": hashlib.sha256(chapter_1.read_bytes()).hexdigest(),
            "scope": "downstream",
            "requires_state_reextract": True,
            "affected_chapters": [2],
            "status": "applied",
        },
    )
    assert scoped_stale_chapters(tmp_storage, layout) == {2}

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=1,
        delete_chapter_files=True,
    )

    assert invalidated == [2]
    chapter_2.write_text("new chapter 2", encoding="utf-8")
    tmp_storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 2})
    assert scoped_stale_chapters(tmp_storage, layout) == set()

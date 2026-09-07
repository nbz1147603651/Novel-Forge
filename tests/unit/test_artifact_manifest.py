"""Tests for the unified workflow artifact manifest."""

from __future__ import annotations

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import (
    STATUS_BLOCKED,
    STATUS_SUCCEEDED,
    ArtifactManifest,
)
from novel_forge.workspace.helpers.execution_manifest import (
    record_entry_success,
    wrap_manifest_step_callback,
)


def _make_manifest(tmp_path):
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("manifest_project"))
    layout.ensure_dirs()
    return storage, layout, ArtifactManifest(storage, layout)


def test_manifest_matches_reusable_blocked_record_by_input_hash(tmp_path) -> None:
    _storage, _layout, manifest = _make_manifest(tmp_path)

    manifest.record_failure(
        artifact="init_coherence:contract_coherence",
        workflow="init_long",
        step="contract_coherence",
        status=STATUS_BLOCKED,
        input_hashes={"chapter_contracts": "abc"},
        output_hashes={"report": "def"},
        reusable_failure=True,
    )

    assert manifest.matching_record(
        "init_coherence:contract_coherence",
        input_hashes={"chapter_contracts": "abc"},
        allow_reusable_failure=True,
    )
    assert (
        manifest.matching_record(
            "init_coherence:contract_coherence",
            input_hashes={"chapter_contracts": "changed"},
            allow_reusable_failure=True,
        )
        is None
    )
    assert (
        manifest.matching_record(
            "init_coherence:contract_coherence",
            input_hashes={"chapter_contracts": "abc"},
            allow_reusable_failure=False,
        )
        is None
    )


def test_manifest_preserves_repair_rounds_across_record_updates(tmp_path) -> None:
    _storage, _layout, manifest = _make_manifest(tmp_path)

    manifest.record_success(
        artifact="chapter:001",
        workflow="run_chapter",
        step="persist",
        input_hashes={"plan": "p1"},
    )
    manifest.record_repair_round(
        artifact="chapter:001",
        workflow="run_chapter",
        issue_ids=["issue-a"],
        round_index=2,
    )
    manifest.record_success(
        artifact="chapter:001",
        workflow="run_chapter",
        step="memory_updated",
        input_hashes={"plan": "p1"},
        output_hashes={"chapter": "c1"},
    )

    record = manifest.get("chapter:001")
    assert record is not None
    assert record.repair_rounds == {"issue-a": 2}
    assert record.status == STATUS_SUCCEEDED
    assert record.step == "memory_updated"


def test_manifest_output_version_changes_only_when_output_hash_changes(tmp_path) -> None:
    _storage, _layout, manifest = _make_manifest(tmp_path)

    first = manifest.record_success(
        artifact="workflow:run_chapter:result",
        workflow="run_chapter",
        step="completed",
        output_hashes={"result": "hash-a"},
    )
    unchanged = manifest.record_success(
        artifact="workflow:run_chapter:result",
        workflow="run_chapter",
        step="completed",
        output_hashes={"result": "hash-a"},
    )
    changed = manifest.record_success(
        artifact="workflow:run_chapter:result",
        workflow="run_chapter",
        step="completed",
        output_hashes={"result": "hash-b"},
    )

    assert first.output_version == 1
    assert unchanged.output_version == 1
    assert changed.output_version == 2


def test_manifest_mutations_merge_records_from_other_live_instances(tmp_path) -> None:
    storage, layout, first = _make_manifest(tmp_path)
    second = ArtifactManifest(storage, layout)

    # Prime both caches before either writer mutates the shared manifest.
    assert first.get("chapter:001:final_text") is None
    assert second.get("workflow:run_chapter:latest_step") is None

    first.record_success(
        artifact="chapter:001:final_text",
        workflow="run_chapter",
        step="persist_final_text",
        output_hashes={"text": "final-hash"},
    )
    second.record_step(
        workflow="run_chapter",
        step="memory_finalization",
    )

    latest = ArtifactManifest(storage, layout)
    assert latest.get("chapter:001:final_text") is not None
    assert latest.get("workflow:run_chapter:latest_step") is not None


def test_step_callback_wrapper_records_and_forwards(tmp_path) -> None:
    storage, _layout, _manifest = _make_manifest(tmp_path)
    received: list[tuple[str, object]] = []
    callback = wrap_manifest_step_callback(
        storage,
        "manifest_project",
        "run_short",
        lambda step, payload: received.append((step, payload)),
    )

    assert callback is not None
    callback("draft", {"chapter": 1, "status": "running"})

    manifest = ArtifactManifest(
        storage, ProjectLayout(storage.existing_project_dir("manifest_project"))
    )
    record = manifest.get("workflow:run_short:latest_step")
    assert record is not None
    assert record.workflow == "run_short"
    assert record.step == "draft"
    assert record.metadata["chapter"] == 1
    assert received == [("draft", {"chapter": 1, "status": "running"})]


def test_record_entry_success_writes_known_workflow_paths(tmp_path) -> None:
    storage, _layout, _manifest = _make_manifest(tmp_path)

    record_entry_success(
        storage,
        "manifest_project",
        "run_short",
        result={"ok": True},
    )

    manifest = ArtifactManifest(
        storage, ProjectLayout(storage.existing_project_dir("manifest_project"))
    )
    record = manifest.get("workflow:run_short:result")
    assert record is not None
    assert record.status == STATUS_SUCCEEDED
    assert "draft" in record.paths
    assert "result" in record.output_hashes


def test_manifest_enforces_signature_and_creative_same_run_resume(tmp_path) -> None:
    _storage, _layout, manifest = _make_manifest(tmp_path)
    manifest.record_success(
        artifact="chapter:001:draft",
        workflow="run_chapter",
        step="draft",
        input_signature="sig-a",
        workflow_version="novel.chapter.v2",
        schema_version=2,
        reuse_policy="same_run_resume",
        run_attempt_id="attempt-1",
    )

    assert manifest.matching_record(
        "chapter:001:draft",
        input_signature="sig-a",
        run_attempt_id="attempt-1",
    )
    assert (
        manifest.matching_record(
            "chapter:001:draft",
            input_signature="sig-a",
            run_attempt_id="attempt-2",
        )
        is None
    )
    assert (
        manifest.matching_record(
            "chapter:001:draft",
            input_signature="sig-b",
            run_attempt_id="attempt-1",
        )
        is None
    )


def test_manifest_signature_cache_crosses_runs_but_never_reuses_stale(tmp_path) -> None:
    _storage, _layout, manifest = _make_manifest(tmp_path)
    manifest.record_success(
        artifact="chapter:001:review",
        workflow="run_chapter",
        step="review",
        input_signature="sig-review",
        reuse_policy="signature_cache",
        run_attempt_id="attempt-1",
    )
    assert manifest.matching_record(
        "chapter:001:review",
        input_signature="sig-review",
        run_attempt_id="attempt-2",
    )

    manifest.record_success(
        artifact="chapter:001:review",
        workflow="run_chapter",
        step="review",
        input_signature="sig-review",
        reuse_policy="signature_cache",
        run_attempt_id="attempt-1",
        derivation_status="stale",
    )
    assert (
        manifest.matching_record(
            "chapter:001:review",
            input_signature="sig-review",
            run_attempt_id="attempt-2",
        )
        is None
    )

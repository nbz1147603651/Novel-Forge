from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_batch_checkpoint import (
    init_batch_checkpoint_path,
    load_init_batch_checkpoint,
    save_init_batch_checkpoint,
)


def _context(tmp_path: Path) -> SimpleNamespace:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    return SimpleNamespace(storage=storage, layout=layout)


def test_init_batch_checkpoint_reuses_matching_input(tmp_path: Path) -> None:
    ctx = _context(tmp_path)

    assert save_init_batch_checkpoint(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
        input_hash="input-a",
        result={"verdict": "accept", "candidate_ids": ["cand-1"]},
    )

    assert load_init_batch_checkpoint(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
        input_hash="input-a",
    ) == {"verdict": "accept", "candidate_ids": ["cand-1"]}


def test_init_batch_checkpoint_overwrites_one_slot_when_input_changes(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    save_init_batch_checkpoint(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
        input_hash="input-a",
        result={"verdict": "accept"},
    )
    path = init_batch_checkpoint_path(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
    )
    assert path is not None

    assert (
        load_init_batch_checkpoint(
            ctx,
            namespace="candidate adjudication",
            batch_id="1/4",
            input_hash="input-b",
        )
        is None
    )
    save_init_batch_checkpoint(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
        input_hash="input-b",
        result={"verdict": "needs_repair"},
    )

    assert len(list(path.parent.glob("*.json"))) == 1
    assert (
        load_init_batch_checkpoint(
            ctx,
            namespace="candidate adjudication",
            batch_id="1/4",
            input_hash="input-a",
        )
        is None
    )
    assert load_init_batch_checkpoint(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
        input_hash="input-b",
    ) == {"verdict": "needs_repair"}


def test_init_batch_checkpoint_rejects_tampered_result(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    save_init_batch_checkpoint(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
        input_hash="input-a",
        result={"verdict": "accept"},
    )
    path = init_batch_checkpoint_path(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
    )
    assert path is not None
    payload = ctx.storage.load_json(path)
    payload["result"] = {"verdict": "needs_repair"}
    ctx.storage.save_json(path, payload)

    assert (
        load_init_batch_checkpoint(
            ctx,
            namespace="candidate adjudication",
            batch_id="1/4",
            input_hash="input-a",
        )
        is None
    )


def test_init_batch_checkpoint_rejects_manifest_version_mismatch(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    save_init_batch_checkpoint(
        ctx,
        namespace="candidate adjudication",
        batch_id="1/4",
        input_hash="input-a",
        result={"verdict": "accept"},
    )
    manifest_path = ctx.layout.states_dir / "artifact_manifest.json"
    payload = ctx.storage.load_json(manifest_path)
    record = next(
        value
        for key, value in payload["artifacts"].items()
        if key.startswith("init_batch:")
    )
    record["output_hashes"]["result"] = "tampered"
    ctx.storage.save_json(manifest_path, payload)

    assert (
        load_init_batch_checkpoint(
            ctx,
            namespace="candidate adjudication",
            batch_id="1/4",
            input_hash="input-a",
        )
        is None
    )

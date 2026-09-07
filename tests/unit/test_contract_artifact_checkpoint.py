from __future__ import annotations

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.contract_artifact_checkpoint import ContractArtifactCheckpoint


def test_contract_artifact_checkpoint_rolls_back_json_artifacts(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    storage.save_json(layout.plans_dir / "chapter_contracts.json", {"version": "before"})
    storage.save_json(layout.plot_milestone_index_path, {"milestones": ["before"]})

    checkpoint = ContractArtifactCheckpoint(storage, layout, 1)
    checkpoint.save()
    storage.save_json(layout.plans_dir / "chapter_contracts.json", {"version": "after"})
    storage.save_json(layout.plot_milestone_index_path, {"milestones": ["after"]})

    checkpoint.rollback()

    assert storage.load_json(layout.plans_dir / "chapter_contracts.json") == {"version": "before"}
    assert storage.load_json(layout.plot_milestone_index_path) == {"milestones": ["before"]}
    checkpoint.cleanup()
    assert not checkpoint.checkpoint_path.exists()


def test_contract_artifact_checkpoint_removes_files_created_after_save(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    checkpoint = ContractArtifactCheckpoint(storage, layout, 2)
    checkpoint.save()
    storage.save_json(layout.plans_dir / "chapter_contracts.json", {"created": True})

    checkpoint.rollback()

    assert not (layout.plans_dir / "chapter_contracts.json").exists()

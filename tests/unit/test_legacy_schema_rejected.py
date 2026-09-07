"""Tests that legacy schema projects are rejected in v2 loaders."""

from __future__ import annotations

import json

import pytest

from novel_forge.story_kernel.store import CanonStore


def test_canon_store_rejects_legacy_schema(tmp_path) -> None:
    project_dir = tmp_path / "legacy_project"
    canon_dir = project_dir / "canon"
    canon_dir.mkdir(parents=True, exist_ok=True)
    (canon_dir / "canon_current.json").write_text(
        json.dumps({"schema_version": "1.0", "project_id": "legacy", "current_chapter": 0}),
        encoding="utf-8",
    )

    store = CanonStore(project_dir)
    with pytest.raises(ValueError, match="当前版本不兼容旧项目，请重新 init-long 或等待迁移工具"):
        store.load()

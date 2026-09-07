from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.app_service.project_files import ProjectFileService


def test_project_file_service_permanently_deletes_project_tree(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    (project_dir / "chapters").mkdir(parents=True)
    (project_dir / "chapters" / "chapter_001.md").write_text("正文", encoding="utf-8")

    result = ProjectFileService(tmp_path).delete_project("demo")

    assert result.deleted is True
    assert result.path == project_dir
    assert not project_dir.exists()


def test_project_file_service_rejects_symlink_alias(tmp_path: Path) -> None:
    target = tmp_path / "real-project"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="symlink"):
        ProjectFileService(tmp_path).delete_project("alias")

    assert target.exists()

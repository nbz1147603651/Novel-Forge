"""Safe project-directory deletion shared by replaceable desktop clients."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from novel_forge.persistence.filesystem import normalize_project_id, resolve_project_path


@dataclass(frozen=True)
class ProjectDeleteResult:
    """Outcome of one storage-root-scoped project deletion."""

    project_id: str
    deleted: bool
    path: Path
    reason: str = ""


class ProjectFileService:
    """Own destructive project file operations without crossing the storage root."""

    def __init__(self, storage_root: Path) -> None:
        self._storage_root = storage_root

    def project_path(self, project_id: str) -> Path:
        return resolve_project_path(self._storage_root, project_id)

    def delete_project(self, project_id: str) -> ProjectDeleteResult:
        normalized_project_id = normalize_project_id(project_id)
        unresolved_path = self._storage_root / normalized_project_id
        if unresolved_path.is_symlink():
            raise ValueError(f"Refusing to delete a project through a symlink: {project_id!r}")

        path = self.project_path(normalized_project_id)
        if not path.exists():
            return ProjectDeleteResult(
                project_id=normalized_project_id,
                deleted=False,
                path=path,
                reason="project_not_found",
            )
        if not path.is_dir():
            raise NotADirectoryError(f"Project path is not a directory: {path}")

        shutil.rmtree(path)
        return ProjectDeleteResult(
            project_id=normalized_project_id,
            deleted=True,
            path=path,
        )


__all__ = ["ProjectDeleteResult", "ProjectFileService"]

"""Read/recover evidence for a single prose revision, never a project rollback."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, cast

from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout

_VERSION = re.compile(r"\d{8}T\d{12}Z")
_CHAPTER = re.compile(r"(?:chapter_|_ch)(\d+)")


def revision_meta_path(layout: ProjectLayout, chapter: int, revision_id: str) -> Path:
    if chapter < 1 or not _VERSION.fullmatch(revision_id):
        raise ValueError("无效正文修订版本")
    return (
        layout.states_dir
        / "final_revision_versions"
        / f"chapter_{chapter:03d}"
        / f"{revision_id}.json"
    )


def revision_history(layout: ProjectLayout, chapter: int) -> list[dict[str, Any]]:
    directory = layout.states_dir / "final_revision_versions" / f"chapter_{chapter:03d}"
    return [
        cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"), reverse=True)
        if _VERSION.fullmatch(path.stem)
    ]


def revision_text(
    layout: ProjectLayout, chapter: int, revision_id: str, side: str = "before"
) -> str:
    if side not in {"before", "after"}:
        raise ValueError("只允许修订前或修订后版本")
    path = revision_meta_path(layout, chapter, revision_id)
    meta = json.loads(path.read_text(encoding="utf-8"))
    text = path.with_name(f"{revision_id}_{side}.md").read_text(encoding="utf-8")
    expected = meta["previous_hash" if side == "before" else "current_hash"]
    if source_text_hash(text) != expected:
        raise ValueError("历史正文与保存哈希不符，未恢复")
    return text


def preserve_revision_dependencies(
    layout: ProjectLayout, version: dict[str, Any], affected: set[int]
) -> None:
    """Copy only potentially invalidated dependencies before the legal commit.

    These are evidence, not files to copy blindly back over later work. Existing
    per-chapter invalidation remains the domain authority for what becomes stale.
    """
    meta_path = Path(version["meta_path"])
    destination = meta_path.with_name(f"{version['timestamp']}_artifacts")
    hashes: dict[str, str] = {}
    for directory in (
        layout.states_dir,
        layout.plans_dir,
        layout.reports_dir,
        layout.drafts_dir,
        layout.narrative_state_dir,
        layout.tts_dir,
    ):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(layout.root)
            if (
                "final_revision_versions" in relative.parts
                or "control_plane_intents" in relative.parts
            ):
                continue
            numbers = {int(match) for match in _CHAPTER.findall(str(relative))}
            # Aggregate progress and narrative projections are also pruned by
            # existing invalidation; retain their old evidence without restoring it.
            aggregate = (
                not numbers
                and directory in {layout.states_dir, layout.plans_dir, layout.narrative_state_dir}
                and path.suffix in {".json", ".jsonl"}
            )
            if not (numbers.intersection(affected) or aggregate):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            with target.open("rb") as stream:
                hashes[str(relative)] = hashlib.file_digest(stream, "sha256").hexdigest()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        dependency_hashes=hashes, dependency_snapshot=str(destination.relative_to(layout.root))
    )
    atomic_write_json(meta_path, meta)


def revision_for_proposal(
    layout: ProjectLayout, chapter: int, proposal_id: str
) -> dict[str, Any] | None:
    for meta in revision_history(layout, chapter):
        if meta.get("reason") == f"authoring_proposal:{proposal_id}":
            return meta
    return None

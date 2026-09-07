"""Shared entry/dispatch/publication checks for all novel clients."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from novel_forge.core.authoring_context import authoring_dispatch_guard
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.authoring_budget import AuthoringBudget
from novel_forge.persistence.authoring_store import (
    AuthoringExecution,
    AuthoringStore,
    active_authoring,
    assert_authoring_current,
    content_version,
)
from novel_forge.persistence.models import ProjectLayout


def checkpoint_version(root: Path, chapter: int) -> str:
    layout = ProjectLayout(root)
    paths = [layout.chapter_checkpoint_path(chapter), layout.chapter_session_path(chapter)]
    return content_version(
        {
            str(path.relative_to(root)): path.read_text(encoding="utf-8")
            for path in paths
            if path.is_file()
        }
    )


def checkpoint_command_version(root: Path, chapter: int, option_id: str, notes: str = "") -> str:
    return content_version(
        {"checkpoint": checkpoint_version(root, chapter), "option_id": option_id, "notes": notes}
    )


@contextmanager
def authoring_operation(runtime: Any, request: Any, action: str) -> Iterator[None]:
    storage = getattr(runtime, "storage", None)
    root = storage.project_path(request.project_id) if storage is not None else None
    if not isinstance(root, Path):
        yield
        return
    store = AuthoringStore(root)
    if action != "pause":
        store.require_enabled()
    policy = store.policy()
    if policy is None:
        yield
        return
    if getattr(request, "force", False):
        from novel_forge.persistence.authoring_store import AuthoringDeniedError

        raise AuthoringDeniedError(
            "已启用作者授权；覆盖正式内容须使用版本化修订提案，不接受强制绕过"
        )
    chapter = request.chapter_number
    approval_id = str(getattr(request, "authoring_approval_id", "") or "")
    candidate = (
        checkpoint_command_version(
            root, chapter, getattr(request, "option_id", ""), getattr(request, "notes", "")
        )
        if action in {"generate", "archive", "revise"}
        else ""
    )
    store.require(
        action, chapter, explicit=True, approval_id=approval_id, candidate_version=candidate
    )
    from novel_forge.pipeline.finalization_manifest import require_authoring_chapter_predecessor

    require_authoring_chapter_predecessor(root, chapter, action)
    session_path = ProjectLayout(root).chapter_session_path(chapter)
    session = json.loads(session_path.read_text(encoding="utf-8")) if session_path.is_file() else {}
    accepted_text = str(session.get("pending_result", {}).get("current_text") or "")
    token = active_authoring.set(
        AuthoringExecution(
            root,
            policy.version,
            chapter,
            approval_id,
            candidate,
            source_text_hash(accepted_text) if accepted_text else "",
        )
    )
    try:
        budget = AuthoringBudget(root, assert_authoring_current)
        with authoring_dispatch_guard(
            assert_authoring_current, reserve=budget.reserve, settle=budget.settle
        ):
            yield
    finally:
        active_authoring.reset(token)


def checkpoint_action(option_id: str) -> str:
    if option_id == "pause_for_human":
        return "pause"
    if option_id == "adjust_outline_and_finalize":
        return "revise"
    if option_id.endswith("_finalize"):
        return "archive"
    return "generate"


@contextmanager
def authoring_review(runtime: Any, request: Any) -> Iterator[None]:
    """Read-only audits still respect selected chapters, stop and model budget."""
    root = runtime.storage.project_path(request.project_id)
    if not isinstance(root, Path) or AuthoringStore(root).policy() is None:
        yield
        return
    chapters = getattr(request, "chapter_range", None) or [
        int(path.stem.split("_")[-1])
        for path in (root / "chapters").glob("chapter_*.md")
        if path.stem.split("_")[-1].isdigit()
    ]
    if not chapters:
        raise ValueError("没有可审查的已归档章节")
    for chapter in chapters:
        AuthoringStore(root).require("repair", chapter, explicit=True)
    with authoring_operation(
        runtime,
        SimpleNamespace(project_id=request.project_id, chapter_number=min(chapters)),
        "repair",
    ):
        yield


@contextmanager
def planning_authority(runtime: Any, project_id: str, chapters: list[int]) -> Iterator[None]:
    """A bounded planning task shares the chapter dispatcher and budget hooks."""
    root = runtime.storage.existing_project_dir(project_id)
    store = AuthoringStore(root)
    for chapter in chapters:
        store.require("plan_candidates", chapter, explicit=True)
    with authoring_operation(
        runtime,
        SimpleNamespace(project_id=project_id, chapter_number=min(chapters)),
        "plan_candidates",
    ):
        yield

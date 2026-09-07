"""Small shared boundary for versioned foundation edits and their commit follow-up."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_candidate_root: ContextVar[Path | None] = ContextVar("foundation_candidate_root", default=None)
SYNC_PATH = "states/foundation_sync.json"


def is_foundation_candidate(root: Path) -> bool:
    return _candidate_root.get() == root.resolve()


def is_isolated_planning_candidate(root: Path) -> bool:
    directory = root.parent.parent
    if root.parent.name != "candidate" or directory.parent.name != ".planning_revisions":
        return False
    from novel_forge.persistence.planning_revision import PlanningRevision

    revision = PlanningRevision.load(directory.parent.parent, root.name, directory.name)
    return revision.project.resolve() == root.resolve()


def require_versioned_maintenance_write(root: Path, operation: str) -> None:
    """Legacy destructive maintenance is never an alternative approval channel."""
    from novel_forge.persistence.authoring_store import AuthoringDeniedError, AuthoringStore

    store = AuthoringStore(root)
    store.require_enabled()
    if store.policy() is not None:
        raise AuthoringDeniedError(
            f"{operation}会直接改动已确认产物，不能代替专项批准；"
            "请在共创中提出正文/设定修订，或使用规划候选并批准。既有内容未改变"
        )


@contextmanager
def foundation_candidate(root: Path) -> Iterator[None]:
    # Only an executor-created isolated revision may use legacy domain writers.
    directory = root.parent.parent
    context = json.loads((directory / "context.json").read_text(encoding="utf-8"))
    if (
        directory.parent.name != ".planning_revisions"
        or root.parent.name != "candidate"
        or context.get("purpose") != "foundation"
        or context.get("project_id") != root.name
    ):
        raise ValueError("不是有效的设定候选目录")
    token = _candidate_root.set(root.resolve())
    try:
        yield
    finally:
        _candidate_root.reset(token)


def require_versioned_foundation_write(root: Path) -> None:
    from novel_forge.persistence.authoring_store import AuthoringDeniedError

    if (root / "authoring_policy.json").exists() and not is_foundation_candidate(root):
        raise AuthoringDeniedError("此作品已采用作者授权；请将修改提交为设定专项提案，批准后再应用")


def assert_foundation_sync_complete(root: Path) -> None:
    from novel_forge.persistence.authoring_store import AuthoringDeniedError

    path = root / SYNC_PATH
    if not path.is_file():
        return
    try:
        status = json.loads(path.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError, AttributeError) as exc:
        raise AuthoringDeniedError("设定提交状态不可读，停止派发并等待恢复") from exc
    if status != "completed":
        raise AuthoringDeniedError("设定修改已提交，必需投影待恢复；请恢复该提案，暂不生成后续章节")

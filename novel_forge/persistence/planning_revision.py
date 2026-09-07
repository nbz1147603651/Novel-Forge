"""Isolated planning candidates and recoverable publication under the project lock."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_text

_REVISION_DIR = ".planning_revisions"
_JOURNAL = "planning_publish.json"
_RECEIPT = "published.json"
_WRITABLE_DIRS = {"plans", "reports", "states", "source_artifacts"}
_EXTENSION_FILES = {"spec.json", "story_bible.json"}
_FOUNDATION_FILES = _EXTENSION_FILES | {
    "character_bible.json",
    "narrative_state/entity_registry.json",
    "narrative_state/entity_graph.json",
}


def _writable(relative: str, purpose: str) -> bool:
    return (
        relative == "outline.json"
        or Path(relative).parts[0] in _WRITABLE_DIRS
        or (purpose == "extension" and relative in _EXTENSION_FILES)
        or (purpose == "foundation" and relative in _FOUNDATION_FILES)
    )


def _action(purpose: str) -> str:
    return {"extension": "extend", "foundation": "revise"}.get(purpose, "publish_planning")


# Workflow progress is written by the live callback during candidate generation.
# It is not a planning input and must never be overwritten by a candidate copy.
_LIVE_ONLY_FILES = {
    "states/artifact_manifest.json",
    "states/task_flow_history.json",
    "states/engine_autorun_session.json",
}


def _live_only(relative: Path) -> bool:
    return relative.as_posix() in _LIVE_ONLY_FILES or relative.parts[:2] == (
        "states",
        "control_plane_intents",
    )


def _path(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("Planning revision path escapes project")
    return path


def planning_publication_receipt(root: Path, revision_id: str) -> dict[str, Any] | None:
    """Read proof of a past commit; never replay writes against newer story data."""
    if len(revision_id) != 32 or any(c not in "0123456789abcdef" for c in revision_id):
        raise ValueError("Invalid planning revision id")
    for path in (_path(root, f"{_REVISION_DIR}/{revision_id}/{_RECEIPT}"), root / _JOURNAL):
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                data.get("revision_id") == revision_id
                and data.get("status") == "committed"
                and data.get("candidate_version")
            ):
                return {
                    "revision_id": revision_id,
                    "status": "committed",
                    "candidate_version": data["candidate_version"],
                    "changed_artifacts": data["changed_artifacts"],
                }
    return None


def _preserve_receipt(root: Path, journal: dict[str, Any]) -> None:
    if journal.get("candidate_version"):
        receipt = planning_publication_receipt(root, journal["revision_id"])
        if receipt is not None:
            atomic_write_text(
                _path(root, f"{_REVISION_DIR}/{journal['revision_id']}/{_RECEIPT}"),
                json.dumps(receipt, ensure_ascii=False),
            )


def recover_planning_publish(root: Path) -> bool:
    """Roll back an interrupted publish. Caller must hold an exclusive project lock."""
    journal = root / _JOURNAL
    if not journal.exists():
        return False
    data = json.loads(journal.read_text(encoding="utf-8"))
    if data["status"] == "publishing":
        for relative, previous in data["before"].items():
            if not _writable(relative, data.get("purpose", "planning")):
                raise ValueError("Planning journal attempts to restore a protected artifact")
            path = _path(root, relative)
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_text(path, previous)
        data["status"] = "rolled_back"
        atomic_write_text(journal, json.dumps(data, ensure_ascii=False))
        return True
    if data["status"] == "committed":
        # Keep a receipt before a subsequent transaction replaces the root journal.
        _preserve_receipt(root, data)
    return False


def assert_planning_publish_complete(root: Path) -> None:
    """Shared readers must not consume a transaction interrupted by a crash."""
    journal = root / _JOURNAL
    if (
        journal.exists()
        and json.loads(journal.read_text(encoding="utf-8"))["status"] == "publishing"
    ):
        raise RuntimeError("Planning publication needs recovery under an exclusive project lock")
    from novel_forge.persistence.foundation_guard import assert_foundation_sync_complete

    assert_foundation_sync_complete(root)


class PlanningRevision:
    """Snapshot JSON inputs; reuse existing synchronization only in the candidate.

    Canon, memory and prose are never publishable. Existing JSON inputs are
    compared again at commit, so concurrent edits invalidate a candidate.
    """

    def __init__(
        self,
        root: Path,
        project_id: str,
        *,
        purpose: Literal["planning", "extension", "foundation"] = "planning",
    ) -> None:
        self.root = root
        self.purpose = purpose
        self.revision_id = uuid.uuid4().hex
        self.directory = root / _REVISION_DIR / self.revision_id
        self.storage = FileSystemStorage(self.directory / "candidate")
        self.project = self.storage.ensure_project_dir(project_id)
        self.before: dict[str, str] = {}
        self.chapter_hashes = self._chapter_hashes()
        for path in root.rglob("*.json"):
            relative = path.relative_to(root)
            if relative.parts[0] in {
                _REVISION_DIR,
                ".authoring",
                "logs",
                "memory",
                "exports",
                "backups",
            }:
                continue
            if relative.name == _JOURNAL or path.is_symlink():
                continue
            if _live_only(relative):
                continue
            text = path.read_text(encoding="utf-8")
            self.before[str(relative)] = text
            atomic_write_text(self.project / relative, text)
        atomic_write_text(self.directory / "base.json", json.dumps(self.before, ensure_ascii=False))
        atomic_write_text(
            self.directory / "context.json",
            json.dumps(
                {
                    "project_id": project_id,
                    "purpose": purpose,
                    "chapter_hashes": self.chapter_hashes,
                },
                ensure_ascii=False,
            ),
        )

    @classmethod
    def load(cls, root: Path, project_id: str, revision_id: str) -> PlanningRevision:
        """Reopen a prepared candidate; never invent missing publication evidence."""
        if len(revision_id) != 32 or any(char not in "0123456789abcdef" for char in revision_id):
            raise ValueError("Invalid planning revision id")
        revision = cls.__new__(cls)
        revision.root = root
        revision.revision_id = revision_id
        revision.directory = _path(root, f"{_REVISION_DIR}/{revision_id}")
        context = json.loads((revision.directory / "context.json").read_text(encoding="utf-8"))
        if context["project_id"] != project_id:
            raise ValueError("Planning candidate belongs to another project")
        revision.purpose = context.get("purpose", "planning")
        if revision.purpose not in {"planning", "extension", "foundation"}:
            raise ValueError("Unknown planning revision purpose")
        revision.chapter_hashes = context["chapter_hashes"]
        revision.before = json.loads((revision.directory / "base.json").read_text(encoding="utf-8"))
        revision.storage = FileSystemStorage(revision.directory / "candidate")
        revision.project = revision.storage.existing_project_dir(project_id)
        return revision

    def mark_validated(self, affected_chapters: list[int]) -> dict[str, Any]:
        """Domain executors call this only after their existing strict validation."""
        from novel_forge.persistence.authoring_store import content_version

        proof = {
            "candidate_version": content_version(self.changes()),
            "action": _action(self.purpose),
            "affected_chapters": sorted(set(affected_chapters)),
        }
        atomic_write_text(self.directory / "validated.json", json.dumps(proof, ensure_ascii=False))
        return cast(dict[str, Any], proof)

    def validation(self) -> dict[str, Any] | None:
        from novel_forge.persistence.authoring_store import content_version

        path = self.directory / "validated.json"
        if not path.is_file():
            return None
        proof = json.loads(path.read_text(encoding="utf-8"))
        if proof.get("candidate_version") != content_version(self.changes()):
            raise ValueError("规划候选验证后发生变化；请重新准备")
        if proof.get("action") != _action(self.purpose):
            raise ValueError("候选的领域权限不匹配")
        return cast(dict[str, Any], proof)

    def _chapter_hashes(self) -> dict[str, str]:
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (self.root / "chapters").glob("chapter_*.md")
        }

    def changes(self) -> dict[str, str | None]:
        changes: dict[str, str | None] = {}
        for path in self.project.rglob("*.json"):
            relative = path.relative_to(self.project)
            if _live_only(relative) or relative.parts[0] == "backups":
                continue
            text = path.read_text(encoding="utf-8")
            if self.before.get(str(relative)) == text:
                continue
            if not _writable(str(relative), self.purpose):
                raise ValueError(f"Candidate attempted to change protected artifact: {relative}")
            changes[str(relative)] = text
        for relative_name in self.before:
            if not (self.project / relative_name).exists():
                if Path(relative_name).parts[0] not in _WRITABLE_DIRS:
                    raise ValueError(f"Candidate deleted a protected artifact: {relative_name}")
                changes[relative_name] = None
        return changes

    def publish(
        self,
        *,
        authoring_approval_id: str = "",
        authoring_chapter: int = 1,
        automatic_horizon: bool = False,
    ) -> list[str]:
        """Publish all prepared artifacts, or restore every old byte on failure."""
        from novel_forge.persistence.authoring_store import AuthoringStore

        # The caller holds the story lock. Pause can interrupt model work, but
        # must serialize with this short, already-authorized atomic commit.
        with AuthoringStore(self.root).lock():
            return self._publish_locked(
                authoring_approval_id=authoring_approval_id,
                authoring_chapter=authoring_chapter,
                automatic_horizon=automatic_horizon,
            )

    def _publish_locked(
        self, *, authoring_approval_id: str, authoring_chapter: int, automatic_horizon: bool
    ) -> list[str]:
        from novel_forge.persistence.authoring_store import AuthoringStore, content_version

        recover_planning_publish(self.root)
        changes = self.changes()
        receipt = planning_publication_receipt(self.root, self.revision_id)
        if receipt is not None:
            if receipt["candidate_version"] != content_version(changes):
                raise ValueError("Published planning candidate was modified")
            return sorted(receipt["changed_artifacts"])
        if self._chapter_hashes() != self.chapter_hashes:
            raise ValueError("Archived prose changed while evaluating candidate")
        current_inputs = {
            str(path.relative_to(self.root))
            for path in self.root.rglob("*.json")
            if path.relative_to(self.root).parts[0]
            not in {_REVISION_DIR, ".authoring", "logs", "memory", "exports", "backups"}
            and path.name != _JOURNAL
            and not path.is_symlink()
            and not _live_only(path.relative_to(self.root))
        }
        if current_inputs != self.before.keys():
            raise ValueError("Planning inputs appeared or disappeared while evaluating")
        # CAS covers read dependencies too, not just the files being replaced.
        for relative, previous in self.before.items():
            path = _path(self.root, relative)
            if not path.exists() or path.read_text(encoding="utf-8") != previous:
                raise ValueError(f"Planning source changed while evaluating: {relative}")
        for relative in changes:
            if relative not in self.before and _path(self.root, relative).exists():
                raise ValueError(f"Planning artifact appeared while evaluating: {relative}")
        if changes:
            authority = AuthoringStore(self.root)
            if authority.policy() is not None:
                if automatic_horizon:
                    if self.purpose != "planning":
                        raise ValueError("延长全书必须单独批准，不能作为自动细化发布")
                    before_outline = json.loads(self.before["outline.json"])
                    after_outline = json.loads(
                        (self.project / "outline.json").read_text(encoding="utf-8")
                    )
                    allowed_keys = {"chapters", "hard_through_chapter", "planned_through_chapter"}
                    if {k: v for k, v in before_outline.items() if k not in allowed_keys} != {
                        k: v for k, v in after_outline.items() if k not in allowed_keys
                    }:
                        raise ValueError("自动细化不得改变全书结构或目标")
                    hard = int(
                        before_outline.get("hard_through_chapter")
                        or before_outline["total_chapters"]
                    )
                    target = int(after_outline["hard_through_chapter"])
                    if not hard < target <= min(hard + 5, before_outline["total_chapters"]):
                        raise ValueError("自动细化范围不合法")
                    if [c for c in before_outline["chapters"] if c["chapter_number"] <= hard] != [
                        c for c in after_outline["chapters"] if c["chapter_number"] <= hard
                    ]:
                        raise ValueError("自动细化不得覆盖已确认章节")
                    for number in range(hard + 1, target + 1):
                        authority.require(
                            "publish_planning",
                            number,
                            major_change=target == before_outline["total_chapters"],
                        )
                else:
                    authority.require(
                        _action(self.purpose),
                        authoring_chapter,
                        approval_id=authoring_approval_id,
                        candidate_version=content_version(changes),
                        major_change=True,
                    )
        journal: dict[str, Any] = {
            "revision_id": self.revision_id,
            "purpose": self.purpose,
            "candidate_version": content_version(changes),
            "changed_artifacts": sorted(changes),
            "status": "publishing",
            "before": {relative: self.before.get(relative) for relative in changes},
        }
        journal_path = self.root / _JOURNAL
        atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False))
        try:
            for relative, text in changes.items():
                path = _path(self.root, relative)
                if text is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_text(path, text)
            journal["status"] = "committed"
            atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False))
        except BaseException:
            recover_planning_publish(self.root)
            raise
        _preserve_receipt(self.root, journal)
        return sorted(changes)

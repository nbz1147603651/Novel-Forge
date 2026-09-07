"""Small durable authority ledger; conversations/proposals are never story canon."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.core.authoring import AuthoringPolicy, authoring_permission
from novel_forge.core.authoring_context import AuthoringAuthorityError
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json


class AuthoringDeniedError(AuthoringAuthorityError):
    """An actionable wait, not a model failure to retry automatically."""


class AuthoringAcceptanceRequired(AuthoringDeniedError):
    def __init__(self, text: str) -> None:
        super().__init__("最终正文与已验收版本不同，请验收最终版本后归档")
        self.text = text
        self.review_state: dict[str, Any] = {}


@dataclass(frozen=True)
class AuthoringExecution:
    root: Path
    policy_version: int
    chapter: int
    approval_id: str = ""
    candidate_version: str = ""
    accepted_text_hash: str = ""


active_authoring: ContextVar[AuthoringExecution | None] = ContextVar(
    "authoring_execution", default=None
)


def assert_authoring_current() -> None:
    execution = active_authoring.get()
    if execution is None:
        return
    store = AuthoringStore(execution.root)
    store.require_enabled()
    policy = store.policy()
    if policy is None or policy.stopped or policy.version != execution.policy_version:
        raise AuthoringDeniedError("授权已停止或变化；保留现有候选，等待作者确认后恢复")


def require_archive_authority(root: Path, chapter: int, text: str = "") -> None:
    store = AuthoringStore(root)
    policy = store.policy()
    if policy is None:
        return
    assert_authoring_current()
    execution = active_authoring.get()
    if execution is not None and (execution.root != root or execution.chapter != chapter):
        raise AuthoringDeniedError("执行授权与归档目标不匹配")
    store.require(
        "archive",
        chapter,
        explicit=execution is not None,
        approval_id=execution.approval_id if execution else "",
        candidate_version=execution.candidate_version if execution else "",
        expected_policy_version=execution.policy_version if execution else None,
    )
    if policy.mode != "authorized_auto" and (
        execution is None
        or not execution.accepted_text_hash
        or source_text_hash(text) != execution.accepted_text_hash
    ):
        raise AuthoringAcceptanceRequired(text)


def content_version(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def story_input_version(root: Path) -> str:
    """Only authoritative story inputs; chatting/report refresh is not an edit."""
    paths = [
        root / name
        for name in (
            "spec.json",
            "story_bible.json",
            "character_bible.json",
            "style_profile.json",
            "outline.json",
            "blueprint.json",
            "plans/narrative_blueprint.json",
            "states/init_request_meta.json",
            "plans/planning_policy.json",
            "plans/narrative_contract.json",
            "plans/chapter_contracts.json",
        )
    ]
    paths.extend((root / "chapters").glob("chapter_*.md"))
    paths.extend((root / "source_artifacts").glob("*.json"))
    return content_version(
        {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))
            if path.is_file()
        }
    )


class AuthoringStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".authoring"
        self.policy_path = root / "authoring_policy.json"

    @contextmanager
    def lock(self) -> Iterator[None]:
        # Independent short authority lock: pause must not wait for a long model
        # call holding the story lock. No story lock may be acquired inside it.
        storage = FileSystemStorage(self.root)
        storage.ensure_project_dir(".authoring")
        with storage.project_lock(".authoring"):
            yield

    def policy(self) -> AuthoringPolicy | None:
        if not self.policy_path.exists():
            return None
        return AuthoringPolicy.model_validate_json(self.policy_path.read_text(encoding="utf-8"))

    def set_policy(self, policy: AuthoringPolicy, *, expected_version: int) -> AuthoringPolicy:
        with self.lock():
            self.require_enabled()
            old = self.policy()
            if (old.version if old else 0) != expected_version:
                raise AuthoringDeniedError("授权版本已变化，请刷新后确认")
            updated = policy.model_copy(
                update={
                    "version": expected_version + 1,
                    "stopped": True,
                    "stop_reason": "策略已保存，请明确启动",
                }
            )
            atomic_write_json(self.policy_path, updated.model_dump(mode="json"))
            return updated

    def stop(self, reason: str = "作者已暂停") -> None:
        with self.lock():
            self._stop_locked(reason)

    def _stop_locked(self, reason: str) -> None:
        policy = self.policy()
        if policy is not None:
            atomic_write_json(
                self.policy_path,
                policy.model_copy(
                    update={
                        "version": policy.version + 1,
                        "stopped": True,
                        "stop_reason": reason,
                    }
                ).model_dump(mode="json"),
            )

    def disabled(self) -> bool:
        path = self.directory / "disabled.json"
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("disabled") is not False
        except (OSError, ValueError, AttributeError):
            return True

    def require_enabled(self) -> None:
        if self.disabled():
            raise AuthoringDeniedError(
                "此作品共创已停用；请先核对并重新启用，不会自动恢复旧任务或批准"
            )

    def disable(self) -> None:
        with self.lock():
            if self.policy() is None:
                raise AuthoringDeniedError("此作品尚未采用共创策略，无需停用")
            # Freeze dispatch/publication first, even if persisting the policy
            # subsequently fails. Old compatible policy still records stopped.
            atomic_write_json(self.directory / "disabled.json", {"disabled": True})
            self._stop_locked("此作品共创已停用；保留候选、正文与已发生费用")

    def enable(self, *, expected_version: int, input_version: str) -> None:
        """Caller first drains task workers. Enabling never activates authority."""
        with self.lock():
            policy = self.policy()
            if (
                policy is None
                or policy.version != expected_version
                or input_version != story_input_version(self.root)
            ):
                raise AuthoringDeniedError("故事或授权版本已变化，请刷新后重新启用")
            self._stop_locked("共创已重新启用；请重新选择授权与启动，不恢复旧批准")
            atomic_write_json(self.directory / "disabled.json", {"disabled": False})

    def start(self, *, expected_version: int, input_version: str) -> AuthoringPolicy:
        with self.lock():
            self.require_enabled()
            policy = self.policy()
            if (
                policy is None
                or policy.version != expected_version
                or input_version != story_input_version(self.root)
            ):
                raise AuthoringDeniedError("故事或授权版本已变化，请刷新后重新启动")
            # Resume does not restore a broader old policy or old approvals.
            policy = policy.model_copy(
                update={"version": policy.version + 1, "stopped": False, "stop_reason": ""}
            )
            atomic_write_json(self.policy_path, policy.model_dump(mode="json"))
            return policy

    def require(
        self,
        action: str,
        chapter: int,
        *,
        explicit: bool = False,
        approval_id: str = "",
        candidate_version: str = "",
        major_change: bool = False,
        expected_policy_version: int | None = None,
    ) -> None:
        if action != "pause":
            self.require_enabled()
        policy = self.policy()
        if policy is None:
            return  # No silent migration of saved legacy controls.
        from novel_forge.persistence.foundation_guard import assert_foundation_sync_complete

        if action not in {"pause", "discuss"}:
            assert_foundation_sync_complete(self.root)
        if expected_policy_version is not None and policy.version != expected_policy_version:
            raise AuthoringDeniedError("授权已变化；结果保留为候选，请重新确认")
        approved = self.approval_matches(approval_id, action, chapter, candidate_version)
        from novel_forge.persistence.authoring_budget import AuthoringBudget

        totals = AuthoringBudget(self.root).totals() if policy.budget_usd is not None else {}
        permission = authoring_permission(
            policy,
            action,
            chapter,
            explicit=explicit,
            approved=approved,
            major_change=major_change,
            spent_usd=float(totals.get("spent_usd", 0)) + float(totals.get("reserved_usd", 0)),
        )
        if not permission.allowed:
            raise AuthoringDeniedError(permission.reason)

    def approve(
        self,
        *,
        action: str,
        chapter: int,
        candidate_version: str,
        input_version: str,
        policy_version: int,
    ) -> str:
        if not candidate_version:
            raise AuthoringDeniedError("批准必须绑定具体候选")
        with self.lock():
            return self.approve_locked(
                action=action,
                chapter=chapter,
                candidate_version=candidate_version,
                input_version=input_version,
                policy_version=policy_version,
            )

    def approve_locked(
        self,
        *,
        action: str,
        chapter: int,
        candidate_version: str,
        input_version: str,
        policy_version: int,
    ) -> str:
        """Caller holds the authority lock through approval AND proposal write."""
        self.require_enabled()
        policy = self.policy()
        if (
            not candidate_version
            or policy is None
            or policy.version != policy_version
            or input_version != story_input_version(self.root)
        ):
            raise AuthoringDeniedError("批准已过期，请检查最新候选和输入")
        permission = authoring_permission(policy, action, chapter, explicit=True, approved=True)
        if not permission.allowed:
            raise AuthoringDeniedError(permission.reason)
        data = {
            "action": action,
            "chapter": chapter,
            "candidate_version": candidate_version,
            "input_version": input_version,
            "policy_version": policy_version,
        }
        approval_id = content_version(data)
        atomic_write_json(self.directory / "approvals" / f"{approval_id}.json", data)
        return approval_id

    def approval_matches(
        self, approval_id: str, action: str, chapter: int, candidate_version: str
    ) -> bool:
        if (
            len(approval_id) != 64
            or not all(c in "0123456789abcdef" for c in approval_id)
            or not candidate_version
        ):
            return False
        path = self.directory / "approvals" / f"{approval_id}.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        policy = self.policy()
        return bool(
            policy
            and data
            == {
                "action": action,
                "chapter": chapter,
                "candidate_version": candidate_version,
                "input_version": story_input_version(self.root),
                "policy_version": policy.version,
            }
        )

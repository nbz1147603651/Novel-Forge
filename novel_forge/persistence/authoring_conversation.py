"""Conversation storage is separate from story canon and publication inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from novel_forge.core.authoring import AuthoringMessageView
from novel_forge.persistence.authoring_store import AuthoringStore
from novel_forge.persistence.filesystem import atomic_write_json


def message_path(root: Path, message_id: str) -> Path:
    if len(message_id) != 32 or any(c not in "0123456789abcdef" for c in message_id):
        raise ValueError("无效共创消息编号")
    return root / ".authoring" / "messages" / f"{message_id}.json"


def read_message(root: Path, message_id: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads(message_path(root, message_id).read_text(encoding="utf-8"))
    )


def save_message(root: Path, data: dict[str, Any]) -> None:
    view = AuthoringMessageView.model_validate(data["view"])
    with AuthoringStore(root).lock():
        atomic_write_json(message_path(root, view.id), data)


def message_views(root: Path) -> list[AuthoringMessageView]:
    views = [
        AuthoringMessageView.model_validate(read_message(root, path.stem)["view"])
        for path in (root / ".authoring" / "messages").glob("*.json")
    ]
    return sorted(views, key=lambda view: (view.created_at, view.id))[-100:]

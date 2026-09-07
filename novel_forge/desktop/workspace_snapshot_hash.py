"""Pure helpers for desktop workspace snapshot payload and hash calculation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot


def stable_snapshot_value(value: Any) -> Any:
    """Convert dataclass and Pydantic snapshot values into JSON-stable data."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [stable_snapshot_value(item) for item in value]
    if isinstance(value, tuple):
        return [stable_snapshot_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): stable_snapshot_value(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, Path):
        return str(value)
    return value


def stable_snapshot_json(value: Any) -> str:
    """Return a stable JSON string for hashing."""
    if hasattr(value, "model_dump_json"):
        rendered = value.model_dump_json()
        if isinstance(rendered, str):
            return rendered
        if isinstance(rendered, bytes | bytearray):
            return bytes(rendered).decode("utf-8", errors="replace")
        return json.dumps(
            rendered,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    return json.dumps(
        stable_snapshot_value(value),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def build_snapshot_payload_static(snapshot: DesktopWorkspaceSnapshot) -> dict[str, Any]:
    """Build payload dict from snapshot without accessing window instance state."""
    result: dict[str, Any] = {}
    result["storage_root"] = str(snapshot.storage_root)
    result["default_provider"] = snapshot.default_provider
    for section_name in ("overview", "metrics", "providers", "projects"):
        result[section_name] = stable_snapshot_value(getattr(snapshot, section_name))
    result["featured_project"] = stable_snapshot_value(snapshot.featured_project)
    result["details"] = {
        pid: stable_snapshot_value(detail.index) for pid, detail in sorted(snapshot.details.items())
    }
    return result


def compute_section_hashes(
    snapshot: DesktopWorkspaceSnapshot,
    payload: dict[str, Any],
    prev_payload: dict[str, Any] | None = None,
    prev_section_hash_cache: dict[str, str] | None = None,
) -> tuple[dict[str, str], set[str], str]:
    """Compute per-section hashes, changed-section set, and combined hash."""
    sections = [
        "storage_root",
        "default_provider",
        "overview",
        "metrics",
        "providers",
        "projects",
        "featured_project",
    ]
    prev_cache = prev_section_hash_cache or {}
    section_hashes: dict[str, str] = {}
    changed_sections: set[str] = set()

    for section in sections:
        current_value = payload.get(section)
        prev_value = (prev_payload or {}).get(section)

        if current_value == prev_value and section in prev_cache:
            section_hashes[section] = prev_cache[section]
        else:
            attr = getattr(snapshot, section, None)
            if attr is not None and (
                hasattr(attr, "model_dump_json") or hasattr(attr, "__dataclass_fields__")
            ):
                section_json = stable_snapshot_json(attr)
            else:
                section_json = json.dumps(
                    current_value,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            section_hashes[section] = hashlib.sha256(section_json.encode("utf-8")).hexdigest()
            if current_value != prev_value:
                changed_sections.add(section)

    prev_details = (prev_payload or {}).get("details", {})
    cur_details = payload.get("details", {})
    for pid, detail in sorted(snapshot.details.items()):
        key = f"details/{pid}"
        cur_idx_json = stable_snapshot_json(detail.index)
        prev_idx_json = prev_details.get(pid)
        if (
            prev_idx_json is not None
            and cur_idx_json == json.dumps(prev_idx_json, ensure_ascii=False, sort_keys=True, default=str)
            and key in prev_cache
        ):
            section_hashes[key] = prev_cache[key]
        else:
            section_hashes[key] = hashlib.sha256(cur_idx_json.encode("utf-8")).hexdigest()
            if cur_details.get(pid) != prev_details.get(pid):
                changed_sections.add(key)

    all_keys = sections + [f"details/{pid}" for pid in sorted(snapshot.details)]
    combined = "|".join(section_hashes[s] for s in all_keys)
    final_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return section_hashes, changed_sections, final_hash


__all__ = (
    "build_snapshot_payload_static",
    "compute_section_hashes",
    "stable_snapshot_json",
    "stable_snapshot_value",
)

"""Best-effort recovery helpers for style_profile.json from init-long logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.schemas.style_profile import ProjectStyleProfile
from novel_forge.pipeline.steps.profile_style_step import ProfileStyleStep


def _latest_init_long_log_dir(project_dir: Path, *, min_mtime: float | None = None) -> Path | None:
    logs_dir = project_dir / "logs"
    if not logs_dir.is_dir():
        return None
    candidates = [
        p
        for p in logs_dir.iterdir()
        if p.is_dir()
        and "_desktop-init-long_" in p.name
        and (min_mtime is None or p.stat().st_mtime >= min_mtime)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def _call_file_order(path: Path) -> tuple[int, str]:
    stem = path.stem
    prefix = stem.split("_", 1)[0]
    if prefix.isdigit():
        return (int(prefix), stem)
    return (-1, stem)


def _extract_payload_from_model_call(path: Path, expected_task: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None

    if not isinstance(raw, dict):
        return None
    if str(raw.get("event", "")).strip() not in {"api_call_done", "api_stream_done"}:
        return None
    if str(raw.get("task", "")).strip() != expected_task:
        return None
    response = raw.get("response")
    if not isinstance(response, dict):
        return None
    content = response.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = safe_parse_json(content)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _latest_task_payload(
    model_calls_dir: Path,
    *,
    task: str,
    min_mtime: float | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not model_calls_dir.is_dir():
        return None, None
    pattern = f"*_{task}.json"
    candidates = sorted(model_calls_dir.glob(pattern), key=_call_file_order, reverse=True)
    for path in candidates:
        if min_mtime is not None and path.stat().st_mtime < min_mtime:
            continue
        payload = _extract_payload_from_model_call(path, task)
        if payload is not None:
            return payload, path.name
    return None, None


def recover_style_profile_from_project_logs(
    project_dir: Path,
    *,
    min_mtime: float | None = None,
) -> tuple[ProjectStyleProfile | None, dict[str, Any]]:
    """Recover style profile from latest init-long model-call artifacts.

    Returns:
        (profile, meta)
        - profile: recovered profile when at least profile_style payload is available.
        - meta: diagnostic information about used files and fallback behavior.
    """
    log_dir = _latest_init_long_log_dir(project_dir, min_mtime=min_mtime)
    if log_dir is None:
        reason = "no_init_long_log_dir"
        if min_mtime is not None:
            reason = "no_init_long_log_dir_after_recovery_epoch"
        return None, {"reason": reason, "min_mtime": min_mtime}

    model_calls_dir = log_dir / "model_calls"
    style_data, style_file = _latest_task_payload(
        model_calls_dir,
        task="profile_style",
        min_mtime=min_mtime,
    )
    structure_data, structure_file = _latest_task_payload(
        model_calls_dir,
        task="profile_structure",
        min_mtime=min_mtime,
    )

    if style_data is None:
        return None, {
            "reason": "missing_profile_style_payload",
            "log_dir": str(log_dir),
            "style_file": style_file,
            "structure_file": structure_file,
        }

    merged = ProfileStyleStep._merge_results(style_data, structure_data or {})
    return merged, {
        "log_dir": str(log_dir),
        "style_file": style_file,
        "structure_file": structure_file,
        "used_default_structure": structure_data is None,
    }

"""Shared utilities for the document_renderer package.

Helpers placed here to avoid circular imports between sibling sub-modules
after the M3.2 giant-file split.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_dict(path: Path) -> dict[str, Any] | None:
    """Read a JSON file as a dict; return None on failure or non-dict payloads."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None

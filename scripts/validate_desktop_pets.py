#!/usr/bin/env python3
"""Validate Novel Forge Desktop pet manifests and optional atlases."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PET_ROOT = REPO_ROOT / "novel_forge" / "desktop" / "resources" / "pets"
CODEX_STANDARD = "codex-atlas-v1"
CODEX_COLUMNS = 8
CODEX_ROWS = 9
CODEX_CELL_WIDTH = 192
CODEX_CELL_HEIGHT = 208
CODEX_ATLAS_SIZE = (CODEX_COLUMNS * CODEX_CELL_WIDTH, CODEX_ROWS * CODEX_CELL_HEIGHT)
REQUIRED_ROWS = {
    "idle": (0, 6),
    "running-right": (1, 8),
    "running-left": (2, 8),
    "waving": (3, 4),
    "jumping": (4, 5),
    "failed": (5, 8),
    "waiting": (6, 6),
    "running": (7, 6),
    "review": (8, 6),
}


@dataclass(frozen=True)
class Issue:
    pet_id: str
    message: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PET_ROOT)
    parser.add_argument(
        "--require-atlas",
        action="store_true",
        help="Fail when a pet lacks the animated spritesheet asset.",
    )
    args = parser.parse_args(argv)

    issues = validate_pet_root(args.root, require_atlas=args.require_atlas)
    if issues:
        print("Desktop pet validation: FAIL")
        for issue in issues:
            print(f"[{issue.pet_id}] {issue.message}")
        return 1
    print("Desktop pet validation: PASS")
    return 0


def validate_pet_root(root: Path, *, require_atlas: bool = False) -> list[Issue]:
    issues: list[Issue] = []
    if not root.exists():
        return [Issue("<root>", f"missing pet root: {root}")]
    for manifest_path in sorted(root.glob("*/pet.json")):
        issues.extend(validate_pet_manifest(manifest_path, require_atlas=require_atlas))
    if not list(root.glob("*/pet.json")):
        issues.append(Issue("<root>", "no pet manifests found"))
    return issues


def validate_pet_manifest(path: Path, *, require_atlas: bool) -> list[Issue]:
    pet_id = path.parent.name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue(pet_id, f"invalid pet.json: {exc}")]
    if not isinstance(data, dict):
        return [Issue(pet_id, "pet.json must contain an object")]

    issues: list[Issue] = []
    if data.get("standard") != CODEX_STANDARD:
        issues.append(Issue(pet_id, f"standard must be {CODEX_STANDARD}"))
    if data.get("pet_id") != pet_id:
        issues.append(Issue(pet_id, "pet_id must match folder name"))

    assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
    sprite = path.parent / str(assets.get("sprite") or "")
    if not sprite.exists():
        issues.append(Issue(pet_id, "fallback sprite asset is required"))

    atlas_name = str(assets.get("spritesheet") or assets.get("atlas") or "")
    atlas_path = path.parent / atlas_name if atlas_name else None
    if require_atlas and (atlas_path is None or not atlas_path.exists()):
        issues.append(Issue(pet_id, "animated spritesheet asset is required"))
    if atlas_path is not None and atlas_path.exists():
        issues.extend(_validate_atlas_image(pet_id, atlas_path))

    atlas = data.get("atlas") if isinstance(data.get("atlas"), dict) else {}
    issues.extend(_validate_atlas_metadata(pet_id, atlas))
    return issues


def _validate_atlas_metadata(pet_id: str, atlas: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    expected = {
        "columns": CODEX_COLUMNS,
        "rows": CODEX_ROWS,
        "cell_width": CODEX_CELL_WIDTH,
        "cell_height": CODEX_CELL_HEIGHT,
    }
    for key, value in expected.items():
        if _int(atlas.get(key)) != value:
            issues.append(Issue(pet_id, f"atlas.{key} must be {value}"))

    row_order = atlas.get("row_order")
    if not isinstance(row_order, list):
        return issues + [Issue(pet_id, "atlas.row_order must be a list")]

    seen: dict[str, tuple[int, int]] = {}
    for item in row_order:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "")
        seen[state] = (_int(item.get("row")), _int(item.get("frames")))
    for state, expected_pair in REQUIRED_ROWS.items():
        if seen.get(state) != expected_pair:
            issues.append(
                Issue(
                    pet_id,
                    f"atlas row for {state} must be row={expected_pair[0]} frames={expected_pair[1]}",
                )
            )
    return issues


def _validate_atlas_image(pet_id: str, atlas_path: Path) -> list[Issue]:
    try:
        from PIL import Image
    except ImportError:
        return [Issue(pet_id, "Pillow is required to validate atlas image dimensions")]
    with Image.open(atlas_path) as image:
        if image.size != CODEX_ATLAS_SIZE:
            return [Issue(pet_id, f"atlas size must be {CODEX_ATLAS_SIZE}, got {image.size}")]
        if image.mode not in {"RGBA", "LA"} and "transparency" not in image.info:
            return [Issue(pet_id, "atlas must preserve transparency")]
    return []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


if __name__ == "__main__":
    raise SystemExit(main())

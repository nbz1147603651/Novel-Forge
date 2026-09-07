"""Desktop pet resource discovery and manifest loading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap

PET_RESOURCE_ROOT = Path(__file__).resolve().parent / "resources" / "pets"
DEFAULT_PET_ID = "nimo"
CODEX_ATLAS_COLUMNS = 8
CODEX_ATLAS_ROWS = 9
CODEX_ATLAS_CELL_SIZE = (192, 208)

CODEX_ROW_SPECS: tuple[tuple[str, int, int, int], ...] = (
    ("idle", 0, 6, 6),
    ("running-right", 1, 8, 8),
    ("running-left", 2, 8, 8),
    ("waving", 3, 4, 6),
    ("jumping", 4, 5, 7),
    ("failed", 5, 8, 6),
    ("waiting", 6, 6, 5),
    ("running", 7, 6, 7),
    ("review", 8, 6, 5),
)
CODEX_ROW_BY_STATE = {state: (row, frames, fps) for state, row, frames, fps in CODEX_ROW_SPECS}


@dataclass(frozen=True)
class PetStateDefinition:
    """One named visual/animation state for a pet."""

    name: str
    label: str
    animation: str
    interval_ms: int
    row_index: int = -1
    frame_count: int = 1
    fps: int = 4
    loop: bool = True


@dataclass(frozen=True)
class PetDefinition:
    """Manifest-backed desktop pet definition."""

    pet_id: str
    display_name: str
    description: str
    standard: str
    root_dir: Path
    atlas_path: Path | None
    sprite_path: Path
    source_path: Path | None
    atlas_columns: int
    atlas_rows: int
    cell_size: tuple[int, int]
    normal_size: tuple[int, int]
    compact_size: tuple[int, int]
    normal_sprite_size: int
    compact_sprite_size: int
    states: dict[str, PetStateDefinition]

    def state(self, name: str) -> PetStateDefinition:
        return self.states.get(name) or self.states.get("idle") or PetStateDefinition(
            name="idle",
            label="待命",
            animation="breathe",
            interval_ms=720,
        )

    @property
    def has_atlas(self) -> bool:
        return self.atlas_path is not None and self.atlas_path.exists()

    def frame_rect(self, state_name: str, frame_index: int) -> tuple[int, int, int, int] | None:
        state = self.state(state_name)
        if state.row_index < 0 or state.frame_count <= 0:
            return None
        width, height = self.cell_size
        column = int(frame_index or 0) % state.frame_count
        return (column * width, state.row_index * height, width, height)


def available_pet_ids(root: Path = PET_RESOURCE_ROOT) -> list[str]:
    """Return pet ids that have a manifest."""

    if not root.exists():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if child.is_dir() and (child / "pet.json").exists()
    )


def load_pet_definition(pet_id: str = "") -> PetDefinition:
    """Load a desktop pet definition, falling back to the built-in default."""

    requested = (
        str(pet_id or os.getenv("NOVEL_FORGE_DESKTOP_PET_ID") or DEFAULT_PET_ID).strip()
        or DEFAULT_PET_ID
    )
    manifest_path = PET_RESOURCE_ROOT / requested / "pet.json"
    if not manifest_path.exists() and requested != DEFAULT_PET_ID:
        manifest_path = PET_RESOURCE_ROOT / DEFAULT_PET_ID / "pet.json"
    data = _load_manifest(manifest_path)
    root_dir = manifest_path.parent

    assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
    atlas = data.get("atlas") if isinstance(data.get("atlas"), dict) else {}
    layout = data.get("layout") if isinstance(data.get("layout"), dict) else {}
    atlas_name = str(assets.get("spritesheet") or assets.get("atlas") or "")
    sprite_name = str(assets.get("sprite") or "sprite.png")
    source_name = str(assets.get("source") or "")
    states = _load_states(data.get("states"), atlas.get("row_order"))

    return PetDefinition(
        pet_id=str(data.get("pet_id") or root_dir.name),
        display_name=str(data.get("display_name") or root_dir.name),
        description=str(data.get("description") or ""),
        standard=str(data.get("standard") or "novel-forge-single-sprite-v1"),
        root_dir=root_dir,
        atlas_path=(root_dir / atlas_name).resolve() if atlas_name else None,
        sprite_path=(root_dir / sprite_name).resolve(),
        source_path=(root_dir / source_name).resolve() if source_name else None,
        atlas_columns=_int_or(atlas.get("columns"), CODEX_ATLAS_COLUMNS),
        atlas_rows=_int_or(atlas.get("rows"), CODEX_ATLAS_ROWS),
        cell_size=(
            _int_or(atlas.get("cell_width"), CODEX_ATLAS_CELL_SIZE[0]),
            _int_or(atlas.get("cell_height"), CODEX_ATLAS_CELL_SIZE[1]),
        ),
        normal_size=(
            _int_or(layout.get("normal_width"), 126),
            _int_or(layout.get("normal_height"), 92),
        ),
        compact_size=(
            _int_or(layout.get("compact_width"), 64),
            _int_or(layout.get("compact_height"), 72),
        ),
        normal_sprite_size=_int_or(layout.get("normal_sprite"), 68),
        compact_sprite_size=_int_or(layout.get("compact_sprite"), 54),
        states=states,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_states(raw: Any, row_order: Any = None) -> dict[str, PetStateDefinition]:
    states = _states_from_row_order(row_order)
    if not isinstance(raw, dict):
        raw = {}
    for name, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        state_name = str(name or "").strip()
        if not state_name:
            continue
        default_row, default_frames, default_fps = CODEX_ROW_BY_STATE.get(
            state_name,
            (-1, 1, 4),
        )
        fps = _int_or(payload.get("fps"), default_fps)
        states[state_name] = PetStateDefinition(
            name=state_name,
            label=str(payload.get("label") or state_name),
            animation=str(payload.get("animation") or "breathe"),
            interval_ms=_int_or(payload.get("interval_ms"), _interval_for_fps(fps)),
            row_index=_int_or(payload.get("row"), default_row, allow_zero=True),
            frame_count=_int_or(payload.get("frames"), default_frames),
            fps=fps,
            loop=bool(payload.get("loop", True)),
        )
    if "idle" not in states:
        states["idle"] = PetStateDefinition(
            "idle",
            "待命",
            "breathe",
            _interval_for_fps(6),
            row_index=0,
            frame_count=6,
            fps=6,
        )
    return states


def _states_from_row_order(raw: Any) -> dict[str, PetStateDefinition]:
    if not isinstance(raw, list):
        raw = []
    states: dict[str, PetStateDefinition] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        state_name = str(item.get("state") or "").strip()
        if not state_name:
            continue
        default_row, default_frames, default_fps = CODEX_ROW_BY_STATE.get(
            state_name,
            (-1, 1, 4),
        )
        fps = _int_or(item.get("fps"), default_fps)
        states[state_name] = PetStateDefinition(
            name=state_name,
            label=state_name,
            animation="atlas",
            interval_ms=_int_or(item.get("interval_ms"), _interval_for_fps(fps)),
            row_index=_int_or(item.get("row"), default_row, allow_zero=True),
            frame_count=_int_or(item.get("frames"), default_frames),
            fps=fps,
        )
    return states


def _int_or(value: Any, fallback: int, *, allow_zero: bool = False) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    if parsed > 0 or (allow_zero and parsed == 0):
        return parsed
    return fallback


def _interval_for_fps(fps: int) -> int:
    return max(80, int(round(1000 / max(1, fps))))


def load_pet_pixmap(pet: PetDefinition) -> "QPixmap":
    """Load the fallback still sprite for a pet definition."""

    from PySide6.QtGui import QPixmap

    if pet.sprite_path.exists():
        pixmap = QPixmap(str(pet.sprite_path))
        if not pixmap.isNull():
            return pixmap
    return QPixmap()


def load_pet_atlas(pet: PetDefinition) -> "QPixmap":
    """Load the animated atlas spritesheet for a pet definition.

    Returns an empty ``QPixmap`` when the atlas is missing or corrupt.
    This is the single canonical loader — all task-focus widgets should
    call this instead of duplicating the loading logic.
    """

    from PySide6.QtGui import QPixmap

    if pet.atlas_path is not None and pet.atlas_path.exists():
        pixmap = QPixmap(str(pet.atlas_path))
        if not pixmap.isNull():
            return pixmap
    return QPixmap()

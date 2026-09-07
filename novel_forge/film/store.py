"""Atomic persistence and optimistic versioning for film projects."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import opentimelineio as otio  # type: ignore[import-untyped]

from novel_forge.persistence.filesystem import atomic_write_json, atomic_write_text
from novel_forge.persistence.models import ProjectLayout

from .schemas import FilmStudioState, ProductionBible, utc_now_iso


class FilmProjectStore:
    def __init__(self, layout: ProjectLayout) -> None:
        self.layout = layout
        self.reset_notice = ""

    @property
    def film_dir(self) -> Path:
        return self.layout.root / "film"

    @property
    def state_path(self) -> Path:
        return self.film_dir / "studio_state.json"

    @property
    def production_bible_path(self) -> Path:
        # Root-level by design: novel, voice and film share this upstream artifact.
        return self.layout.root / "production_bible.json"

    @property
    def otio_path(self) -> Path:
        return self.film_dir / "exports" / "timeline.otio"

    def load(self) -> FilmStudioState | None:
        if not self.state_path.exists():
            return None
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            schema_version = str(payload.get("schema_version") or "") if isinstance(payload, dict) else ""
            if schema_version != "2.0":
                self._backup_for_reset(f"旧版映界状态 {schema_version or 'unknown'} 已备份并重建为 2.0。")
                return None
            return FilmStudioState.model_validate(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            self._backup_for_reset("映界状态无法验证，已备份原文件并安全重建。")
            return None

    def _backup_for_reset(self, notice: str) -> None:
        if not self.state_path.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.film_dir / "backups" / f"studio_state-v1-{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.state_path, backup)
        self.reset_notice = f"{notice} 备份：{backup.relative_to(self.layout.root)}"

    def save(self, state: FilmStudioState) -> FilmStudioState:
        updated = state.model_copy(update={"updated_at": utc_now_iso()})
        atomic_write_json(self.state_path, updated.model_dump(mode="json"))
        self.save_production_bible(updated.production_bible)
        return updated

    def save_production_bible(self, bible: ProductionBible) -> None:
        atomic_write_json(self.production_bible_path, bible.model_dump(mode="json"))

    def export_otio(self, state: FilmStudioState) -> Path:
        """Serialize the timeline through the upstream OpenTimelineIO library.

        Schema versioning, rational-time math and JSON layout are owned by the
        ASWF ``opentimelineio`` project; this layer only maps the internal
        timeline model onto it, so format upgrades ride the upstream release.
        """

        timeline = state.timeline
        rate = float(timeline.frame_rate) or 24.0
        root = otio.schema.Timeline(name=timeline.name)
        root.metadata["novel_forge"] = {
            "project_id": state.project_id,
            "production_bible": "../../production_bible.json",
            "frame_rate": timeline.frame_rate,
        }
        for track in timeline.tracks:
            kind = (
                otio.schema.TrackKind.Video
                if track.kind == "video"
                else otio.schema.TrackKind.Audio
            )
            otio_track = otio.schema.Track(name=track.name, kind=kind)
            for clip in track.clips:
                source_range = otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(float(clip.source_start_s) * rate, rate),
                    duration=otio.opentime.RationalTime(
                        max(0.0, float(clip.duration_s)) * rate, rate
                    ),
                )
                media_reference = otio.schema.ExternalReference(target_url=str(clip.source_url))
                otio_clip = otio.schema.Clip(
                    name=clip.name,
                    media_reference=media_reference,
                    source_range=source_range,
                )
                otio_clip.metadata.update(dict(clip.metadata))
                otio_track.append(otio_clip)
            root.tracks.append(otio_track)
        self.otio_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.otio_path, root.to_json_string(indent=2))
        return self.otio_path

    def read_raw(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

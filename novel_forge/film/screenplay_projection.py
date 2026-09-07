"""Level-1 finished-text backflow: WAVE chapter text → screenplay scenes.

The two-level backflow architecture keeps canon read-only:

1. **Level 1 (this module)** — ``ChapterScreenplayProjector`` deterministically
   extracts scenes / dialogue / action from the finished chapter text plus the
   upstream ``scene_intents`` and emotional annotations written by the planning
   stage.  Every produced :class:`ScreenplayScene` carries ``source_refs``
   (chapter + paragraph range) so lineage back to the novel is auditable.
2. **Level 2** — the ``ADAPT_SCREENPLAY`` task performs the semantic rewrite
   (narration → visual action, psychology → externalized behaviour, dialogue
   trimmed for shot rhythm).  Its output is validated scene-by-scene with
   :func:`validate_screenplay_adaptation`; rejected scenes fall back to the
   deterministic level-1 projection instead of drifting.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout

from .schemas import (
    ProductionBible,
    SceneSourceRef,
    Screenplay,
    ScreenplayLine,
    ScreenplayScene,
)

_CHAPTER_FILE_RE = re.compile(r"chapter_(\d{3,})\.md$")
_QUOTE_RE = re.compile(r"[“「]([^”」]{1,400})[”」]")
_MAX_SCENES_PER_CHAPTER = 8
_MAX_LINE_CHARS = 240


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _text(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _split_paragraphs(chapter_text: str) -> list[str]:
    """Non-heading, non-empty lines of the finished chapter text."""

    paragraphs: list[str] = []
    for raw_line in chapter_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        paragraphs.append(stripped)
    return paragraphs


def _partition_by_weight(total_items: int, weights: list[int]) -> list[tuple[int, int]]:
    """Split ``range(total_items)`` into ``len(weights)`` contiguous ranges.

    Used to map paragraphs onto scene intents proportionally (by target word
    budget, falling back to even splits); always covers every paragraph.
    """
    if total_items <= 0:
        return []
    buckets = len(weights)
    if buckets <= 0:
        return [(0, total_items - 1)]
    total_weight = sum(max(0, weight) for weight in weights) or buckets
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for index, weight in enumerate(weights):
        if index == buckets - 1:
            end = total_items - 1
        else:
            share = max(0, weight) / total_weight if total_weight else 1.0 / buckets
            end = min(total_items - 1, cursor + max(1, round(total_items * share)) - 1)
        ranges.append((cursor, max(cursor, end)))
        cursor = max(cursor, end) + 1
        if cursor >= total_items:
            cursor = total_items - 1
    # Merge degenerate tail ranges into the last real range.
    merged = [item for item in ranges if item[0] < total_items]
    return merged or [(0, total_items - 1)]


class ChapterScreenplayProjector:
    """Deterministic projector from finished chapters to screenplay scenes.

    Reads only upstream canon artifacts (chapter text, chapter plans,
    character bible); never writes.  The result can be merged with the
    outline-based projection so locked planning metadata (objective/conflict/
    turn) survives while lines come from the real finished text.
    """

    def __init__(self, layout: ProjectLayout, project_id: str) -> None:
        self.layout = layout
        self.project_id = project_id

    # -- discovery ----------------------------------------------------------

    def finished_chapters(self) -> list[int]:
        chapters_dir = self.layout.chapters_dir
        if not chapters_dir.is_dir():
            return []
        numbers: list[int] = []
        for path in sorted(chapters_dir.iterdir()):
            match = _CHAPTER_FILE_RE.search(path.name)
            if match and path.is_file():
                numbers.append(int(match.group(1)))
        return sorted(numbers)

    def _character_names(self) -> list[str]:
        payload = _load_json(self.layout.characters_path)
        raw = payload.get("characters")
        names: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    name = _text(item, "name", "character_name")
                    if name:
                        names.append(name)
        return names

    # -- extraction ---------------------------------------------------------

    def _scene_intents(self, chapter_number: int) -> list[dict[str, Any]]:
        plan = _load_json(self.layout.chapter_plan_path(chapter_number))
        raw = plan.get("scene_intents")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)][:_MAX_SCENES_PER_CHAPTER]

    def _attribute_speaker(
        self,
        paragraph: str,
        quote_start: int,
        cast_names: list[str],
        scene_characters: list[str],
    ) -> str:
        """Find the character name governing a quoted line.

        Scans the clause before the quote for a known cast name (Chinese
        dialogue convention: 「某人说："……"」); falls back to the scene's
        first declared character, then empty (narrator/unattributed).
        """
        prefix = paragraph[max(0, quote_start - 24) : quote_start]
        best_name, best_pos = "", -1
        for name in cast_names:
            position = prefix.rfind(name)
            if position > best_pos:
                best_name, best_pos = name, position
        if best_name:
            return best_name
        return scene_characters[0] if scene_characters else ""

    def _extract_scene_lines(
        self,
        paragraphs: list[str],
        *,
        chapter_number: int,
        cast_names: list[str],
        scene_characters: list[str],
    ) -> tuple[list[ScreenplayLine], list[str]]:
        lines: list[ScreenplayLine] = []
        speakers_seen: list[str] = []
        for offset, paragraph in enumerate(paragraphs):
            ref = f"chapter:{chapter_number}:p{offset + 1}"
            cursor = 0
            for match in _QUOTE_RE.finditer(paragraph):
                action_prefix = paragraph[cursor : match.start()].strip()
                if action_prefix:
                    lines.append(
                        ScreenplayLine(
                            kind="action", text=action_prefix[:_MAX_LINE_CHARS], source_ref=ref
                        )
                    )
                speaker = self._attribute_speaker(
                    paragraph, match.start(), cast_names, scene_characters
                )
                dialogue = match.group(1).strip()
                if dialogue:
                    lines.append(
                        ScreenplayLine(
                            kind="dialogue",
                            speaker=speaker,
                            text=dialogue[:_MAX_LINE_CHARS],
                            source_ref=ref,
                        )
                    )
                    if speaker and speaker not in speakers_seen:
                        speakers_seen.append(speaker)
                cursor = match.end()
            tail = paragraph[cursor:].strip()
            if tail:
                lines.append(
                    ScreenplayLine(kind="action", text=tail[:_MAX_LINE_CHARS], source_ref=ref)
                )
        return lines, speakers_seen

    def project_chapter(
        self,
        chapter_number: int,
        *,
        bible: ProductionBible,
        cast_names: list[str],
        sequence_start: int,
    ) -> list[ScreenplayScene]:
        """Project one finished chapter into 1..N screenplay scenes."""

        chapter_path = self.layout.chapter_path(chapter_number)
        if not chapter_path.is_file():
            return []
        try:
            chapter_text = chapter_path.read_text(encoding="utf-8")
        except OSError:
            return []
        paragraphs = _split_paragraphs(chapter_text)
        if not paragraphs:
            return []

        intents = self._scene_intents(chapter_number)
        if intents:
            weights = [int(item.get("target_words") or 0) or 1 for item in intents]
            ranges = _partition_by_weight(len(paragraphs), weights)
        else:
            ranges = [(0, len(paragraphs) - 1)]

        location_names = {location.name: location.location_id for location in bible.locations}
        scenes: list[ScreenplayScene] = []
        for slot, (start, end) in enumerate(ranges, 1):
            intent = intents[slot - 1] if slot <= len(intents) else {}
            scene_characters_intent = [
                str(item).strip()
                for item in (intent.get("required_characters") or [])
                if str(item).strip()
            ]
            chunk = paragraphs[start : end + 1]
            lines, speakers_seen = self._extract_scene_lines(
                chunk,
                chapter_number=chapter_number,
                cast_names=cast_names,
                scene_characters=scene_characters_intent,
            )
            if not lines:
                continue
            location_name = _text(intent, "location", "setting")
            sequence = sequence_start + len(scenes)
            summary = _text(intent, "summary")
            characters = list(dict.fromkeys([*scene_characters_intent, *speakers_seen]))
            emotional_beat = _text(intent, "emotional_beat")
            scenes.append(
                ScreenplayScene(
                    scene_id=f"sc-{sequence:03d}",
                    sequence_number=sequence,
                    heading=summary[:40] or f"第 {chapter_number} 章 · 场次 {slot}",
                    location_id=location_names.get(location_name)
                    or (bible.locations[0].location_id if bible.locations else ""),
                    time_of_day=_text(intent, "time_marker") or "待定",
                    characters=characters,
                    objective=_text(intent, "purpose") or summary,
                    conflict=_text(intent, "conflict"),
                    turn=_text(intent, "required_outcome"),
                    visual_hook=_text(intent, "sensory_notes") or emotional_beat,
                    sound_hook=emotional_beat,
                    duration_s=float(max(30, min(180, len(chunk) * 20))),
                    source_chapter=chapter_number,
                    source_scene_ref=_text(intent, "scene_id") or f"chapter:{chapter_number}",
                    source_refs=[
                        SceneSourceRef(
                            chapter_number=chapter_number,
                            paragraph_start=start + 1,
                            paragraph_end=end + 1,
                            scene_intent_id=_text(intent, "scene_id"),
                            note="deterministic_projection",
                        )
                    ],
                    lines=lines,
                )
            )
        return scenes

    # -- assembly -----------------------------------------------------------

    def build(
        self,
        *,
        bible: ProductionBible,
        title: str = "",
        chapter_numbers: list[int] | None = None,
        max_chapters: int = 6,
    ) -> Screenplay:
        """Project the finished chapters of a project into a screenplay."""

        numbers = chapter_numbers or self.finished_chapters()
        numbers = sorted(numbers)[:max_chapters]
        cast_names = self._character_names() or [
            character.name for character in bible.characters
        ]
        scenes: list[ScreenplayScene] = []
        for chapter_number in numbers:
            extracted = self.project_chapter(
                chapter_number,
                bible=bible,
                cast_names=cast_names,
                sequence_start=len(scenes) + 1,
            )
            scenes.extend(extracted)
        for index, scene in enumerate(scenes, 1):
            scenes[index - 1] = scene.model_copy(
                update={"scene_id": f"sc-{index:03d}", "sequence_number": index}
            )
        return Screenplay(
            title=title or bible.title,
            synopsis="成稿回流一级投影：场次/对白/动作均确定性提取自正文，未做语义改写。",
            acts=["成稿回流 · 确定性提取"],
            scenes=scenes,
            estimated_duration_s=sum(scene.duration_s for scene in scenes),
        )

    @staticmethod
    def merge_with_outline(
        chapter_screenplay: Screenplay,
        outline_screenplay: Screenplay,
    ) -> Screenplay:
        """Merge chapter-level extraction with the outline projection.

        Chapters with finished text use the extracted scenes (they carry real
        dialogue/action and lineage); outline scenes for those chapters are
        dropped.  Chapters without finished text keep their outline scenes,
        inheriting planning metadata into extracted scenes where the intent
        left a field empty.  Sequences are renumbered deterministically.
        """
        extracted_chapters = {
            scene.source_chapter
            for scene in chapter_screenplay.scenes
            if scene.source_chapter is not None
        }
        if not extracted_chapters:
            return outline_screenplay

        outline_meta: dict[int, list[ScreenplayScene]] = {}
        for scene in outline_screenplay.scenes:
            if scene.source_chapter in extracted_chapters:
                outline_meta.setdefault(scene.source_chapter or 0, []).append(scene)

        merged: list[ScreenplayScene] = []
        for scene in chapter_screenplay.scenes:
            outline_scenes = outline_meta.get(scene.source_chapter or 0, [])
            if outline_scenes:
                reference = outline_scenes[0]
                scene = scene.model_copy(
                    update={
                        "objective": scene.objective or reference.objective,
                        "conflict": scene.conflict or reference.conflict,
                        "turn": scene.turn or reference.turn,
                        "visual_hook": scene.visual_hook or reference.visual_hook,
                        "sound_hook": scene.sound_hook or reference.sound_hook,
                        "characters": list(
                            dict.fromkeys([*scene.characters, *reference.characters])
                        ),
                    }
                )
            merged.append(scene)
        merged.extend(
            scene
            for scene in outline_screenplay.scenes
            if scene.source_chapter not in extracted_chapters
        )
        for index, scene in enumerate(merged, 1):
            merged[index - 1] = scene.model_copy(
                update={"scene_id": f"sc-{index:03d}", "sequence_number": index}
            )
        return Screenplay(
            title=chapter_screenplay.title or outline_screenplay.title,
            synopsis=(
                f"成稿回流两级结构：{len(extracted_chapters)} 章已确定性提取，"
                f"其余场次沿用大纲投影。"
            ),
            acts=outline_screenplay.acts or ["成稿回流"],
            scenes=merged,
            estimated_duration_s=sum(scene.duration_s for scene in merged),
        )


def validate_screenplay_adaptation(
    deterministic: ScreenplayScene,
    adapted: ScreenplayScene,
    *,
    cast_names: tuple[str, ...] = (),
    min_length_ratio: float = 0.3,
    max_length_ratio: float = 2.5,
) -> tuple[bool, str]:
    """Level-2 accept/reject guard against adaptation drift.

    The rewrite may transform narration into visual action and trim dialogue,
    but it must stay anchored to the source scene: same speakers, no invented
    cast members, comparable volume and shared semantic skeleton.  Mirrors the
    spoken-text-rewrite validation policy (semantic, not substring-based).
    """

    def _joined(scene: ScreenplayScene) -> str:
        return "。".join(line.text for line in scene.lines)

    source_text = _joined(deterministic)
    adapted_text = _joined(adapted)
    if not adapted.lines or not adapted_text.strip():
        return False, "empty_adaptation"

    source_core = re.sub(r"\s+", "", source_text)
    adapted_core = re.sub(r"\s+", "", adapted_text)
    if source_core:
        ratio = len(adapted_core) / len(source_core)
        if ratio < min_length_ratio:
            return False, f"length_ratio_too_low={ratio:.2f}"
        if ratio > max_length_ratio:
            return False, f"length_ratio_too_high={ratio:.2f}"

    source_speakers = {
        line.speaker for line in deterministic.lines if line.kind == "dialogue" and line.speaker
    }
    adapted_speakers = {
        line.speaker for line in adapted.lines if line.kind == "dialogue" and line.speaker
    }
    missing = source_speakers - adapted_speakers
    if missing:
        return False, f"missing_speakers={','.join(sorted(missing))}"
    if cast_names:
        invented = {
            speaker
            for speaker in adapted_speakers
            if speaker not in cast_names and speaker not in source_speakers
        }
        if invented:
            return False, f"invented_speakers={','.join(sorted(invented))}"

    for name in cast_names:
        if name in source_core and name not in adapted_core:
            return False, f"dropped_cast_name={name}"

    similarity = SequenceMatcher(None, source_core, adapted_core).ratio()
    if similarity < 0.12:
        return False, f"semantic_skeleton_too_weak={similarity:.2f}"
    return True, "ok"

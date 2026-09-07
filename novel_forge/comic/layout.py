"""Deterministic comic layout planner.

Consumes the Batch-2 screenplay intermediate artifacts (``Screenplay``) and
the locked ``ProductionBible``; never touches upstream canon.  The planner:

1. turns each scene into panel beats (establishing beat + one beat per
   dialogue/action line) — dialogue becomes ``SpeechBubble`` (Clip 内气泡 =
   ScreenplayLine 映射);
2. packs beats into pages/strips within the hard panel budget via the shared
   feasible-region allocator (``film.storyboard.allocate_beat_budgets``) —
   分镜级拆解能力与 film 共用同一套算法;
3. inherits the visual identity lock (facial anchors / silhouette / costume /
   signature props / latest visual state) into every panel prompt so reused
   film providers keep cross-panel identity stable（身份锁与场景上下文共享
   ``ProductionBible``，投影助手同样复用 film.storyboard）.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from novel_forge.film.schemas import ProductionBible, Screenplay, ScreenplayScene
from novel_forge.film.storyboard import (
    allocate_beat_budgets,
    identity_lock_fragment,
    scene_context_fragment,
)

from .schemas import (
    BUBBLE_KIND_NARRATION,
    BUBBLE_KIND_SPEECH,
    BUBBLE_KIND_THOUGHT,
    ComicFormat,
    ComicPage,
    ComicPanel,
    SpeechBubble,
    panel_aspect,
    panel_budget,
)

_NEGATIVE_PROMPT = "身份漂移，多余人物，文字乱码，画幅不一致，镜像复制，肢体畸变"
_THOUGHT_MARKERS = ("心想", "内心", "暗想", "OS")
_NARRATION_MARKERS = ("旁白", "画外音", "VO")


@dataclass
class _Beat:
    scene_id: str
    beat: str
    action: str = ""
    bubbles: list[SpeechBubble] = field(default_factory=list)
    character_names: list[str] = field(default_factory=list)


def _bubble_from_line(
    scene: ScreenplayScene, index: int, kind: str, speaker: str, text: str
) -> SpeechBubble:
    return SpeechBubble(
        speaker=speaker,
        text=text,
        kind=kind,
        source_line_ref=f"{scene.scene_id}:L{index}",
    )


def build_panel_beats(scene: ScreenplayScene) -> list[_Beat]:
    """One establishing beat + one beat per significant screenplay line."""
    beats: list[_Beat] = []
    cast = list(scene.characters)
    establishing = scene.objective or scene.visual_hook or scene.heading
    beats.append(
        _Beat(
            scene_id=scene.scene_id,
            beat=f"建立镜头：{establishing}".strip(),
            action=scene.heading,
            character_names=list(cast),
        )
    )
    for index, line in enumerate(scene.lines):
        text = line.text.strip()
        if not text:
            continue
        ref_kind = line.kind or "action"
        if ref_kind == "dialogue":
            kind = (
                BUBBLE_KIND_THOUGHT
                if any(marker in (line.performance_note or "") for marker in _THOUGHT_MARKERS)
                else BUBBLE_KIND_SPEECH
            )
            speaker = line.speaker or (cast[0] if cast else "")
            beats.append(
                _Beat(
                    scene_id=scene.scene_id,
                    beat=f"{speaker}对白" if speaker else "对白",
                    bubbles=[_bubble_from_line(scene, index, kind, speaker, text)],
                    character_names=[speaker] if speaker else list(cast),
                )
            )
        elif ref_kind in ("vo", "narration") or any(
            text.startswith(marker) for marker in _NARRATION_MARKERS
        ):
            beats.append(
                _Beat(
                    scene_id=scene.scene_id,
                    beat="旁白/画外音",
                    bubbles=[_bubble_from_line(scene, index, BUBBLE_KIND_NARRATION, "", text)],
                    character_names=list(cast),
                )
            )
        else:
            beats.append(
                _Beat(
                    scene_id=scene.scene_id,
                    beat=text,
                    action=text,
                    character_names=list(cast),
                )
            )
    return beats


def plan_comic_pages(
    screenplay: Screenplay,
    bible: ProductionBible,
    fmt: ComicFormat,
    *,
    pacing_targets: dict[int, int] | None = None,
) -> list[ComicPage]:
    """Pack scene beats into pages (Track) of panels (Clip) within budget.

    ``pacing_targets`` maps page_number → desired panel count (LLM pacing
    hint); values are clamped into the hard budget range.
    """
    low, high = panel_budget(fmt)
    aspect = panel_aspect(fmt)
    beats: list[_Beat] = []
    for scene in screenplay.scenes:
        beats.extend(build_panel_beats(scene))
    if not beats:
        return []
    # Normalize beat count into an achievable page split: with k pages the
    # total must land in [k*low, k*high]; shortfalls are padded with silent
    # insert panels (空镜/反应格) derived from scene headings — a standard
    # comic pacing device, never new plot.
    page_count = max(1, math.ceil(len(beats) / high))
    scene_cycle = list(screenplay.scenes)
    cycle_index = 0
    while len(beats) < page_count * low and scene_cycle:
        scene = scene_cycle[cycle_index % len(scene_cycle)]
        cycle_index += 1
        insert = scene.visual_hook or scene.heading
        beats.append(
            _Beat(
                scene_id=scene.scene_id,
                beat=f"空镜/反应格：{insert}".strip(),
                action=insert,
                character_names=[],
            )
        )
    scene_context: dict[str, str] = {
        scene.scene_id: scene_context_fragment(bible, scene) for scene in screenplay.scenes
    }

    # Distribute beats across the fixed page count: after padding we know
    # N ∈ [k*low, k*high], so a feasible split always exists.  The shared
    # feasible-region allocator (film.storyboard) honors the LLM pacing
    # target but clamps every page into the window that keeps every
    # following page (and the tail) inside the budget.
    counts = allocate_beat_budgets(
        len(beats), page_count, low=low, high=high, pacing_targets=pacing_targets
    )
    pages: list[list[_Beat]] = []
    cursor = 0
    for count in counts:
        pages.append(beats[cursor : cursor + count])
        cursor += count

    result: list[ComicPage] = []
    for page_index, page_beats in enumerate(pages, start=1):
        panels: list[ComicPanel] = []
        for panel_index, beat in enumerate(page_beats, start=1):
            identity_fragments: list[str] = []
            character_ids: list[str] = []
            for name in beat.character_names:
                character = next((c for c in bible.characters if c.name == name), None)
                if character is None:
                    continue
                fragment, character_id = identity_lock_fragment(character)
                if fragment:
                    identity_fragments.append(fragment)
                if character_id and character_id not in character_ids:
                    character_ids.append(character_id)
            prompt_parts = [
                bible.style.texture_medium,
                scene_context.get(beat.scene_id, ""),
                beat.beat,
            ] + identity_fragments
            prompt_parts.append(f"漫画面板，画幅{aspect}，清晰分格，对白气泡留白")
            panels.append(
                ComicPanel(
                    panel_id=f"p{page_index:03d}-{panel_index:02d}",
                    page_number=page_index,
                    panel_number=panel_index,
                    scene_id=beat.scene_id,
                    beat=beat.beat,
                    action=beat.action,
                    character_ids=character_ids,
                    bubbles=list(beat.bubbles),
                    prompt="；".join(part for part in prompt_parts if part),
                    negative_prompt=_NEGATIVE_PROMPT,
                    aspect=aspect,
                )
            )
        result.append(ComicPage(page_number=page_index, panels=panels))
    return result


__all__ = ["build_panel_beats", "plan_comic_pages"]

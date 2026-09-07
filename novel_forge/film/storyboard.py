"""Deterministic film storyboard planner（分镜）.

定位与漫画层对齐：``ComicPanel``（格）镜像 ``FilmShot``（镜头），
``ComicPage``（页/条）镜像 Track —— 两者共享同一套**分镜头级拆解能力**。
本模块是这套能力的 film 侧落点，comic 层复用此处的可行域分配算法与
身份锁/场景上下文投影（身份锁与场景上下文共享 ``ProductionBible``）。

分工模式与 ``COMIC_PANEL_LAYOUT`` 一致：**LLM 只建议节奏，确定性兜底** ——
``FILM_SHOT_LAYOUT`` 只给出每场镜头数建议，本规划器负责钳位、补齐与
节拍装填，保证分镜永远落在硬预算可行域内。

硬标准（确定性、可测试）：
- 每场 3-8 个镜头（``SHOTS_PER_SCENE_RANGE``）；
- 每个镜头必须继承建立镜头或至少一个剧本节拍，不得凭空新增剧情；
- 出场角色的身份锁锚点必须进入镜头 prompt；
- 节拍不足时以空镜/反应镜头（insert）补齐，不得引入新对白。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from .schemas import (
    FilmShot,
    ProductionBible,
    ProductionCharacter,
    Screenplay,
    ScreenplayScene,
    ShotLanguage,
)
from .source_projection import FilmSourceProjector

# Hard storyboard standard (deterministic, testable): shots per scene.
SHOTS_PER_SCENE_RANGE = (3, 8)

_NEGATIVE_PROMPT = "身份漂移，服装漂移，空间错位，轴线错误，塑料肤质，多余肢体"
_NARRATION_MARKERS = ("旁白", "画外音", "VO")

# beat kind → (title, shot_size, camera_angle, camera_motion, lens_mm)
_SHOT_PATTERNS: dict[str, tuple[str, str, str, str, int]] = {
    "establishing": ("建立镜头", "wide", "eye_level", "slow_push", 28),
    "action": ("行动镜头", "medium", "eye_level", "tracking", 40),
    "dialogue": ("对白镜头", "medium", "eye_level", "subtle_handheld", 50),
    "narration": ("旁白空镜", "wide", "high_angle", "static", 35),
    "insert": ("插入/反应镜头", "close_up", "detail", "static", 85),
}


def allocate_beat_budgets(
    total: int,
    container_count: int,
    *,
    low: int,
    high: int,
    pacing_targets: Mapping[int, int] | None = None,
) -> list[int]:
    """Shared feasible-region allocation (film storyboard + comic pages).

    Distribute ``total`` beats across ``container_count`` containers so every
    container count lands in ``[low, high]``.  ``pacing_targets`` maps
    1-based container index → desired count (LLM pacing hint); each desired
    value is clamped into the window that keeps every following container
    (and the tail) inside the budget.  Callers must guarantee feasibility:
    ``container_count * low <= total <= container_count * high``.
    """
    if container_count <= 0:
        return []
    counts: list[int] = []
    remaining = total
    for index in range(container_count):
        if index == container_count - 1:
            count = remaining
        else:
            desired = high
            if pacing_targets:
                desired = min(max(pacing_targets.get(index + 1, high), low), high)
            rest = container_count - index - 1
            max_allowed = remaining - low * rest
            min_allowed = max(low, remaining - high * rest)
            count = min(max(desired, min_allowed), max_allowed, high)
        counts.append(count)
        remaining -= count
    return counts


@dataclass
class ShotBeat:
    """One storyboard beat; mirrors comic's panel beat (no bubbles)."""

    scene_id: str
    kind: str  # establishing | dialogue | narration | action | insert
    beat: str
    action: str = ""
    line_text: str = ""
    speaker: str = ""
    character_names: list[str] = field(default_factory=list)


def build_shot_beats(scene: ScreenplayScene) -> list[ShotBeat]:
    """One establishing beat + one beat per significant screenplay line."""
    beats: list[ShotBeat] = []
    cast = list(scene.characters)
    establishing = scene.objective or scene.visual_hook or scene.heading
    beats.append(
        ShotBeat(
            scene_id=scene.scene_id,
            kind="establishing",
            beat=f"建立镜头：{establishing}".strip(),
            action=scene.heading,
            character_names=list(cast),
        )
    )
    for line in scene.lines:
        text = line.text.strip()
        if not text:
            continue
        ref_kind = line.kind or "action"
        if ref_kind == "dialogue":
            speaker = line.speaker or (cast[0] if cast else "")
            beats.append(
                ShotBeat(
                    scene_id=scene.scene_id,
                    kind="dialogue",
                    beat=f"{speaker}对白" if speaker else "对白",
                    line_text=text,
                    speaker=speaker,
                    character_names=[speaker] if speaker else list(cast),
                )
            )
        elif ref_kind in ("vo", "narration") or any(
            text.startswith(marker) for marker in _NARRATION_MARKERS
        ):
            beats.append(
                ShotBeat(
                    scene_id=scene.scene_id,
                    kind="narration",
                    beat="旁白/画外音",
                    line_text=text,
                    character_names=list(cast),
                )
            )
        else:
            beats.append(
                ShotBeat(
                    scene_id=scene.scene_id,
                    kind="action",
                    beat=text,
                    action=text,
                    character_names=list(cast),
                )
            )
    return beats


def identity_lock_fragment(character: ProductionCharacter) -> tuple[str, str]:
    """Return (identity prompt fragment, character_id) from the shared bible.

    Shared by film storyboard shots and comic panels so both media keep the
    same cross-shot/cross-panel identity lock wording.
    """
    lock = character.screen_identity
    parts: list[str] = []
    if lock.facial_anchors:
        parts.append(f"面部锚点：{'、'.join(lock.facial_anchors)}")
    if lock.silhouette:
        parts.append(f"轮廓：{lock.silhouette}")
    if lock.costume_palette:
        parts.append(f"服装配色：{'、'.join(lock.costume_palette)}")
    if lock.signature_props:
        parts.append(f"标志道具：{'、'.join(lock.signature_props)}")
    if lock.visual_state_timeline:
        parts.append(f"当前状态：{lock.visual_state_timeline[-1]}")
    fragment = f"{character.name}（{'；'.join(parts)}）" if parts else character.name
    return fragment, character.character_id


def scene_context_fragment(bible: ProductionBible, scene: ScreenplayScene) -> str:
    """Shared scene-context projection (style thesis + location + motifs)."""
    location = next(
        (loc for loc in bible.locations if loc.location_id == scene.location_id),
        None,
    )
    parts = [bible.style.visual_thesis]
    if location is not None:
        parts.extend(
            item
            for item in (location.spatial_layout, location.key_light, location.color_mood)
            if item
        )
    if bible.style.visual_motifs:
        parts.append("视觉符号：" + "、".join(bible.style.visual_motifs[:3]))
    return "；".join(part for part in parts if part)


def plan_storyboard_shots(
    screenplay: Screenplay,
    bible: ProductionBible,
    *,
    pacing_targets: Mapping[str, tuple[int, str]] | None = None,
) -> list[FilmShot]:
    """Pack scene beats into shots within the hard per-scene shot budget.

    ``pacing_targets`` maps scene_id → (desired shot count, rhythm note);
    the LLM hint is clamped into ``SHOTS_PER_SCENE_RANGE`` and only shapes
    pacing — beat packing, identity locks and lineage stay deterministic
    (same division of labour as ``COMIC_PANEL_LAYOUT``).
    """
    low, high = SHOTS_PER_SCENE_RANGE
    shots: list[FilmShot] = []
    for scene in screenplay.scenes:
        beats = build_shot_beats(scene)
        target = pacing_targets.get(scene.scene_id) if pacing_targets else None
        desired = target[0] if target else len(beats)
        shot_count = min(max(desired, low), high)
        # Shortfalls are padded with insert shots (空镜/反应镜头) derived from
        # the scene's own hook — a standard film pacing device, never new plot.
        insert_source = scene.visual_hook or scene.heading
        while len(beats) < shot_count:
            beats.append(
                ShotBeat(
                    scene_id=scene.scene_id,
                    kind="insert",
                    beat=f"插入/反应镜头：{insert_source}".strip(),
                    action=insert_source,
                    character_names=[],
                )
            )
        # Feasible beat→shot split: every shot carries ≥1 beat; dense scenes
        # merge neighbouring beats into one shot via the shared allocator.
        # The per-shot capacity is balanced (ceil(n/k)) so the greedy pass
        # spreads beats evenly instead of front-loading the first shot.
        beat_capacity = max(1, math.ceil(len(beats) / shot_count))
        counts = allocate_beat_budgets(len(beats), shot_count, low=1, high=beat_capacity)
        groups: list[list[ShotBeat]] = []
        cursor = 0
        for count in counts:
            groups.append(beats[cursor : cursor + count])
            cursor += count

        location = next(
            (item for item in bible.locations if item.location_id == scene.location_id),
            bible.locations[0] if bible.locations else None,
        )
        seed_overrides = FilmSourceProjector._shot_seed_overrides(
            location.shot_language_seed if location is not None else ""
        )
        character_seeds = [
            item.shot_language_seed
            for item in bible.characters
            if (item.name in scene.characters or item.character_id in scene.characters)
            and item.shot_language_seed
        ]
        for char_seed in character_seeds:
            for key, value in FilmSourceProjector._shot_seed_overrides(char_seed).items():
                seed_overrides.setdefault(key, value)
        seed_suffix = (
            f"；镜头语言偏好：{location.shot_language_seed}"
            if location is not None and location.shot_language_seed
            else ""
        )
        if character_seeds:
            seed_suffix += f"；角色镜头偏好：{'、'.join(character_seeds)}"
        if bible.style.visual_motifs:
            seed_suffix += f"；视觉符号锚点：{'、'.join(bible.style.visual_motifs[:3])}"
        scene_context = scene_context_fragment(bible, scene)
        duration = max(2.0, min(12.0, scene.duration_s / max(1, shot_count)))

        for shot_number, group in enumerate(groups, start=1):
            lead = group[0]
            title, size, angle, motion, lens = _SHOT_PATTERNS[lead.kind]
            size = str(seed_overrides.get("shot_size", size))
            angle = str(seed_overrides.get("camera_angle", angle))
            motion = str(seed_overrides.get("camera_motion", motion))
            lens = int(seed_overrides.get("lens_mm", lens))
            lighting = str(seed_overrides.get("lighting", "motivated"))
            character_names: list[str] = []
            for beat in group:
                for name in beat.character_names:
                    if name not in character_names:
                        character_names.append(name)
            identity_fragments: list[str] = []
            character_ids: list[str] = []
            for name in character_names:
                character = next((c for c in bible.characters if c.name == name), None)
                if character is None:
                    continue
                fragment, character_id = identity_lock_fragment(character)
                if fragment:
                    identity_fragments.append(fragment)
                if character_id and character_id not in character_ids:
                    character_ids.append(character_id)
            action = "；".join(beat.action for beat in group if beat.action)
            dialogue = "；".join(beat.line_text for beat in group if beat.line_text)
            beat_text = "；".join(beat.beat for beat in group)
            prompt_parts = [
                scene_context,
                location.name if location is not None else "",
                scene.heading,
                f"{size}，{angle}，{motion}，{lens}mm",
                beat_text,
            ] + identity_fragments
            prompt_parts.append("保持角色身份锁、服装、空间动线与主光方向一致" + seed_suffix)
            shot = FilmShot(
                shot_id=f"{scene.scene_id}-sh-{shot_number:02d}",
                scene_id=scene.scene_id,
                shot_number=shot_number,
                title=f"{title}（{lead.speaker}）"
                if lead.kind == "dialogue" and lead.speaker
                else title,
                duration_s=duration,
                language=ShotLanguage(
                    shot_size=size,
                    camera_angle=angle,
                    camera_motion=motion,
                    lighting=lighting,
                    emotion=scene.conflict or scene.objective or "neutral",
                    time=scene.time_of_day or "continuous",
                    lens_mm=lens,
                    composition="保持视线、轴线与叙事主体清晰",
                    focus_strategy="主体优先，必要时以焦点转移揭示信息",
                ),
                action=action,
                dialogue=dialogue,
                sound_design=scene.sound_hook if shot_number == 1 else "",
                prompt="；".join(part for part in prompt_parts if part),
                negative_prompt=_NEGATIVE_PROMPT,
                character_ids=character_ids,
                location_id=scene.location_id,
            )
            if target and shot_number == 1 and target[1]:
                shot = shot.model_copy(update={"rhythm_note": target[1]})
            shots.append(shot)
    return shots


def audit_storyboard(shots: list[FilmShot], screenplay: Screenplay) -> list[str]:
    """Deterministic delivery gate: returns stable, machine-readable codes."""
    issues: list[str] = []
    low, high = SHOTS_PER_SCENE_RANGE
    scene_ids = {scene.scene_id for scene in screenplay.scenes}
    per_scene: dict[str, int] = {}
    seen: set[str] = set()
    for shot in shots:
        if shot.shot_id in seen:
            issues.append(f"duplicate_shot_id:{shot.shot_id}")
        seen.add(shot.shot_id)
        if scene_ids and shot.scene_id not in scene_ids:
            issues.append(f"orphan_shot:{shot.shot_id}")
        if not shot.prompt.strip():
            issues.append(f"empty_shot_prompt:{shot.shot_id}")
        per_scene[shot.scene_id] = per_scene.get(shot.scene_id, 0) + 1
    for scene_id, count in per_scene.items():
        if not low <= count <= high:
            issues.append(f"shot_count_out_of_range:{scene_id}:{count}")
        overflowing = [
            shot.shot_id for shot in shots if shot.scene_id == scene_id and shot.shot_number > count
        ]
        for shot_id in overflowing:
            issues.append(f"shot_number_overflow:{shot_id}")
    return issues


__all__ = [
    "SHOTS_PER_SCENE_RANGE",
    "ShotBeat",
    "allocate_beat_budgets",
    "audit_storyboard",
    "build_shot_beats",
    "identity_lock_fragment",
    "plan_storyboard_shots",
    "scene_context_fragment",
]

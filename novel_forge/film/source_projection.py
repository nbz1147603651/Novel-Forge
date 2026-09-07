"""Project novel/TTS artifacts → shared production-bible projection."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.publication import build_chapter_publication_view

from .reference_capabilities import (
    build_storyboard_contact_sheet,
    build_variant_assets,
)
from .schemas import (
    FILM_STAGE_ORDER,
    ArtifactSource,
    FilmDecision,
    FilmShot,
    FilmStage,
    FilmStageState,
    FilmStageStatus,
    FilmStudioState,
    FilmStyleLock,
    FilmTimeline,
    FilmVisualAsset,
    ProductionBible,
    ProductionCharacter,
    ProductionLocation,
    RunPlanNode,
    ScreenIdentityLock,
    Screenplay,
    ScreenplayLine,
    ScreenplayScene,
    ShotLanguage,
    TimelineTrack,
    VoicePerformanceLock,
)
from .style_library import match_style, style_decision_choices
from .visual_continuity import enrich_production_bible


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _revision(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def film_source_signature(sources: list[ArtifactSource]) -> str:
    """Hash all exact upstream revisions used by one film workspace."""

    payload = [
        {
            "artifact_type": source.artifact_type,
            "relative_path": source.relative_path,
            "revision": source.revision,
            "input_signature": source.input_signature,
            "quality_status": source.quality_status,
            "derivation_status": source.derivation_status,
        }
        for source in sorted(sources, key=lambda item: (item.artifact_type, item.relative_path))
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _text(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, (int, float)):
            return str(candidate)
    return ""


def _list_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[；;、\n]", value) if item.strip()]
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if isinstance(item, (str, int, float)) and str(item).strip()
        ]
    return []


def _mapping_list(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def _stable_id(prefix: str, value: str, index: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.strip()).strip("-")
    return f"{prefix}-{slug or index:0>2}"


class FilmSourceProjector:
    """Build a recoverable film workspace without bypassing upstream contracts."""

    def __init__(self, layout: ProjectLayout, project_id: str) -> None:
        self.layout = layout
        self.project_id = project_id

    def build(self) -> FilmStudioState:
        spec = _load_json(self.layout.spec_path)
        story_bible = _load_json(self.layout.bible_path)
        characters_payload = _load_json(self.layout.characters_path)
        style = _load_json(self.layout.style_profile_path)
        outline = _load_json(self.layout.outline_path)
        voice_team = _load_json(self.layout.tts_voice_team_path)
        audio_bible = _load_json(self.layout.tts_audio_creative_bible_path)

        title = _text(spec, "title") or self.project_id
        sources = self._sources()
        source_signature = film_source_signature(sources)
        production_bible = ProductionBible(
            project_id=self.project_id,
            title=title,
            logline=_text(spec, "theme", "premise") or _text(story_bible, "premise", "logline"),
            language=_text(spec, "language") or "zh",
            production_intent=_text(spec, "extra_instructions"),
            target_audience=_text(story_bible, "target_audience", "audience"),
            sources=sources,
            source_signature=source_signature,
            characters=self._characters(characters_payload, voice_team),
            locations=self._locations(story_bible, spec),
            style=self._style_lock(spec, style, audio_bible),
            world_rules=self._world_rules(story_bible),
            themes=_list_text(story_bible.get("themes")) or _list_text(spec.get("theme")),
            continuity_rules=_list_text(story_bible.get("continuity_rules")),
        )
        production_bible = enrich_production_bible(production_bible, self.layout)
        screenplay = self._screenplay(title, outline, production_bible)
        shots = [
            shot.model_copy(
                update={"source_signature": source_signature, "derivation_status": "fresh"}
            )
            for shot in self._shots(screenplay, production_bible)
        ]
        visual_assets = self._visual_assets(production_bible) + build_variant_assets(
            production_bible
        )
        contact_sheet = build_storyboard_contact_sheet(shots, production_bible)
        if contact_sheet is not None:
            visual_assets.append(contact_sheet)
        visual_assets = [
            asset.model_copy(
                update={"source_signature": source_signature, "derivation_status": "fresh"}
            )
            for asset in visual_assets
        ]
        return FilmStudioState(
            project_id=self.project_id,
            project_title=title,
            production_bible=production_bible,
            screenplay=screenplay,
            visual_assets=visual_assets,
            shots=shots,
            run_plan=self._run_plan(),
            timeline=FilmTimeline(
                name=f"{title} · 主时间线",
                frame_rate=production_bible.style.frame_rate,
                tracks=[
                    TimelineTrack(track_id="v1", name="画面 V1", kind="video"),
                    TimelineTrack(track_id="d1", name="对白 D1", kind="dialogue"),
                    TimelineTrack(track_id="s1", name="环境 / 音效 S1", kind="sfx"),
                    TimelineTrack(track_id="m1", name="音乐 M1", kind="music"),
                ],
            ),
            stages=self._stages(),
            decisions=[
                FilmDecision(
                    stage=FilmStage.PLANNING,
                    title="确认首轮改编范围",
                    description="系统已从小说与配音资产建立制片圣经，确认后进入剧本场次化。",
                    choices=["采用当前范围", "先调整人物与场景", "AI 自主决定"],
                ),
                FilmDecision(
                    stage=FilmStage.PLANNING,
                    title="选择视觉风格库基线",
                    description=(
                        "从内置 12 大类风格库匹配了候选风格，作为 FilmStyleLock 初始化基线，"
                        "可在人工检查点修改。"
                    ),
                    choices=style_decision_choices(
                        _text(spec, "genre"), _text(spec, "tone")
                    )
                    + ["AI 自主决定"],
                ),
                FilmDecision(
                    stage=FilmStage.VISUAL_DEVELOPMENT,
                    title="是否启用 face-warp 角色一致性素材子流程",
                    description=(
                        "face-warp 可生成无面版与拼图版角色素材，用于跨镜头人脸一致性；"
                        "属于可选子流程，不影响主链路。"
                    ),
                    choices=["启用无面版+拼图版", "仅启用拼图版", "跳过", "AI 自主决定"],
                ),
            ],
            source_signature=source_signature,
        )

    def _sources(self) -> list[ArtifactSource]:
        paths = [
            ("story_spec", self.layout.spec_path, ["title", "theme", "genre", "tone"]),
            ("story_bible", self.layout.bible_path, ["world_rules", "themes", "locations"]),
            (
                "character_bible",
                self.layout.characters_path,
                ["identity", "appearance", "personality", "arc", "voice", "tts_voice_hints"],
            ),
            ("style_profile", self.layout.style_profile_path, ["style", "rhythm", "imagery"]),
            ("outline", self.layout.outline_path, ["chapters", "scenes", "turning_points"]),
            (
                "narrative_blueprint",
                self.layout.blueprint_path,
                ["premise", "arcs", "turning_points", "payoffs"],
            ),
            (
                "voice_team",
                self.layout.tts_voice_team_path,
                ["character_id", "provider", "voice_id", "model_id"],
            ),
            (
                "audio_creative_bible",
                self.layout.tts_audio_creative_bible_path,
                ["sound_identity", "music", "mix_density"],
            ),
        ]
        sources = [
            ArtifactSource(
                artifact_type=kind,
                relative_path=str(path.relative_to(self.layout.root)),
                revision=_revision(path),
                exists=path.exists(),
                fields_used=fields,
                quality_status="actual" if path.exists() else "legacy_unknown",
                derivation_status="fresh" if path.exists() else "legacy_unknown",
            )
            for kind, path, fields in paths
        ]
        for chapter_path in sorted(self.layout.chapters_dir.glob("chapter_*.md")):
            match = re.fullmatch(r"chapter_(\d+)\.md", chapter_path.name)
            if match is None:
                continue
            chapter_number = int(match.group(1))
            try:
                publication = build_chapter_publication_view(
                    self.layout,
                    self.project_id,
                    chapter_number,
                )
            except (FileNotFoundError, OSError, ValueError):
                continue
            sources.append(
                ArtifactSource(
                    artifact_type=f"novel_chapter:{chapter_number}",
                    relative_path=str(chapter_path.relative_to(self.layout.root)),
                    revision=publication.final_text_hash,
                    exists=True,
                    fields_used=["final_text"],
                    source_text_hash=publication.final_text_hash,
                    input_signature=publication.input_signature,
                    output_version=publication.output_version,
                    quality_status=publication.quality_status,
                    derivation_status=publication.derivation_status,
                )
            )
        return sources

    def _characters(
        self,
        character_bible: dict[str, Any],
        voice_team: dict[str, Any],
    ) -> list[ProductionCharacter]:
        raw_characters = _mapping_list(character_bible, "characters", "profiles", "cast")
        voice_entries = _mapping_list(voice_team, "entries", "characters", "voice_team")
        by_id: dict[str, dict[str, Any]] = {}
        by_name: dict[str, dict[str, Any]] = {}
        for entry in voice_entries:
            character_id = _text(entry, "character_id", "id")
            name = _text(entry, "character_name", "name")
            if character_id:
                by_id[character_id] = entry
            if name:
                by_name[name] = entry

        characters: list[ProductionCharacter] = []
        for index, item in enumerate(raw_characters[:24], 1):
            name = _text(item, "name", "character_name") or f"角色{index}"
            character_id = _text(item, "character_id", "id") or _stable_id("char", name, index)
            voice = by_id.get(character_id) or by_name.get(name) or {}
            raw_hints = item.get("tts_voice_hints")
            hints: dict[str, Any] = raw_hints if isinstance(raw_hints, dict) else {}
            raw_visual_identity = item.get("visual_identity")
            visual_identity: dict[str, Any] = (
                raw_visual_identity if isinstance(raw_visual_identity, dict) else {}
            )
            appearance = _text(item, "appearance", "visual", "look")
            characters.append(
                ProductionCharacter(
                    character_id=character_id,
                    name=name,
                    role=_text(item, "role"),
                    dramatic_function=_text(item, "dramatic_function", "role"),
                    age=_text(item, "age"),
                    gender=_text(item, "gender"),
                    personality=_text(item, "personality"),
                    arc=_text(item, "arc", "arc_goal"),
                    shot_language_seed=_text(item, "shot_language_seed", "shot_seed"),
                    screen_identity=ScreenIdentityLock(
                        facial_anchors=_list_text(visual_identity.get("facial_anchors"))
                        or _list_text(appearance)[:5],
                        silhouette=_text(visual_identity, "silhouette")
                        or _text(item, "silhouette")
                        or appearance,
                        body_language=_text(visual_identity, "body_language")
                        or _text(item, "body_language", "mannerisms"),
                        costume_palette=_list_text(visual_identity.get("costume_palette"))
                        or _list_text(item.get("costume_palette")),
                        signature_props=_list_text(visual_identity.get("signature_props"))
                        or _list_text(item.get("signature_props")),
                        continuity_rules=_list_text(visual_identity.get("continuity_rules"))
                        or [f"{name}的脸型、年龄感与体态跨镜头保持一致"],
                        forbidden_drift=_list_text(visual_identity.get("forbidden_drift"))
                        or ["不可随机改变发色、瞳色、年龄与标志性服装"],
                    ),
                    voice_performance=VoicePerformanceLock(
                        provider=_text(voice, "provider", "provider_id"),
                        voice_id=_text(voice, "voice_id"),
                        model_id=_text(voice, "model_id"),
                        timbre=_text(voice, "voice_description", "timbre")
                        or _text(item, "voice")
                        or _text(hints, "timbre"),
                        vocal_register=_text(hints, "register", "pitch"),
                        cadence=_text(hints, "cadence", "speed", "pace"),
                        accent=_text(hints, "accent"),
                        emotion_range=_list_text(hints.get("emotion_range")),
                        pronunciation_notes=_list_text(voice.get("pronunciation_notes")),
                        delivery_rules=[f"沿用小说角色“{name}”的句式节奏与已批准声线"],
                        reference_audio_path=_text(
                            voice, "reference_audio_path", "preview_audio_path"
                        ),
                    ),
                )
            )
        if characters:
            return characters
        return [
            ProductionCharacter(
                character_id="char-protagonist",
                name="主角",
                role="protagonist",
                screen_identity=ScreenIdentityLock(
                    continuity_rules=["建立角色三视图后再生成含角色镜头"],
                    forbidden_drift=["禁止未审核的人脸、服装和年龄漂移"],
                ),
                voice_performance=VoicePerformanceLock(
                    delivery_rules=["先在声腔模块完成音色确认"],
                ),
            )
        ]

    def _locations(
        self,
        story_bible: dict[str, Any],
        spec: dict[str, Any],
    ) -> list[ProductionLocation]:
        raw_locations: list[dict[str, Any]] = []
        for key in ("locations", "key_locations", "settings", "scenes"):
            raw_locations = _mapping_list(story_bible.get(key))
            if raw_locations:
                break
        locations: list[ProductionLocation] = []
        for index, item in enumerate(raw_locations[:20], 1):
            name = _text(item, "name", "location", "title") or f"场景{index}"
            locations.append(
                ProductionLocation(
                    location_id=_text(item, "location_id", "id") or _stable_id("loc", name, index),
                    name=name,
                    dramatic_function=_text(item, "dramatic_function", "function", "meaning"),
                    geography=_text(item, "geography", "setting"),
                    era=_text(item, "era", "period"),
                    spatial_layout=_text(item, "spatial_layout", "layout", "description"),
                    materials=_list_text(item.get("materials")),
                    practical_lights=_list_text(item.get("practical_lights")),
                    weather_states=_list_text(item.get("weather_states") or item.get("weather")),
                    recurring_props=_list_text(item.get("props") or item.get("recurring_props")),
                    ambient_sound=_list_text(item.get("ambient_sound") or item.get("sound")),
                    continuity_rules=_list_text(item.get("continuity_rules"))
                    or [f"保持{name}的门窗、动线、光源方向与关键道具位置"],
                    shot_language_seed=_text(item, "shot_language_seed", "shot_seed"),
                    color_mood=_text(item, "color_mood", "color", "palette"),
                    key_light=_text(item, "key_light", "main_light", "lighting"),
                )
            )
        if locations:
            return locations
        world_hint = _text(spec, "world_hint") or _text(story_bible, "world", "setting")
        return [
            ProductionLocation(
                location_id="loc-primary",
                name="主场景",
                spatial_layout=world_hint,
                continuity_rules=["完成平面动线与主光方向审核后锁定空间"],
                ambient_sound=["从声腔环境声资产库继承场景底噪"],
            )
        ]

    def _style_lock(
        self,
        spec: dict[str, Any],
        style: dict[str, Any],
        audio_bible: dict[str, Any],
    ) -> FilmStyleLock:
        genre = _text(spec, "genre")
        tone = _text(spec, "tone")
        visual = _text(style, "visual_thesis", "style_summary", "description")
        library_entry = match_style(genre, tone)
        return FilmStyleLock(
            visual_thesis=visual or f"{genre}类型，{tone}情绪，服务人物与叙事的电影化影像",
            genre=genre,
            tone=tone,
            aspect_ratio=_text(style, "aspect_ratio") or "16:9",
            color_script=_list_text(style.get("color_script") or style.get("color_palette")),
            lighting_rules=_list_text(style.get("lighting_rules"))
            or ["所有光源必须有场景内动机", "人物肤色与环境色温保持可读层次"],
            lens_language=_list_text(style.get("lens_language"))
            or ["人物关系优先使用 35–50mm", "情绪特写使用 75–100mm"],
            camera_rules=_list_text(style.get("camera_rules"))
            or ["运动必须由人物动作或叙事信息驱动", "禁止无意义漂浮镜头"],
            texture_medium=_text(style, "texture_medium", "medium") or "cinematic live action",
            negative_style_rules=_list_text(style.get("negative_style_rules"))
            or ["避免塑料肤质、过度锐化、随机景别与轴线跳跃"],
            visual_motifs=_list_text(style.get("visual_motifs")),
            style_library_id=library_entry.style_id if library_entry else "",
            sound_thesis=_text(audio_bible, "sound_thesis", "sound_identity")
            or _text(spec, "audio_aesthetic_hint"),
            music_thesis=_text(audio_bible, "music_thesis", "music_direction"),
        )

    @staticmethod
    def _world_rules(story_bible: dict[str, Any]) -> list[str]:
        rules = story_bible.get("world_rules")
        if isinstance(rules, dict):
            nested = rules.get("rules")
            if isinstance(nested, list):
                return [
                    _text(item, "rule", "description", "name")
                    for item in nested
                    if isinstance(item, dict) and _text(item, "rule", "description", "name")
                ]
        return _list_text(rules)

    def _screenplay(
        self,
        title: str,
        outline: dict[str, Any],
        bible: ProductionBible,
    ) -> Screenplay:
        raw_chapters = _mapping_list(outline, "chapters", "chapter_outlines", "episodes")
        scenes: list[ScreenplayScene] = []
        for chapter_index, chapter in enumerate(raw_chapters[:6], 1):
            chapter_number = int(
                chapter.get("chapter_number") or chapter.get("number") or chapter_index
            )
            raw_scenes = _mapping_list(chapter, "scenes", "scene_plan", "beats") or [chapter]
            for item in raw_scenes[:6]:
                sequence = len(scenes) + 1
                location = _text(item, "location", "setting")
                location_match = next(
                    (loc for loc in bible.locations if loc.name == location), None
                )
                heading = _text(item, "heading", "title", "name") or f"场次 {sequence}"
                objective = _text(item, "objective", "goal", "purpose")
                conflict = _text(item, "conflict", "obstacle")
                summary = _text(item, "summary", "description", "content", "beat")
                scenes.append(
                    ScreenplayScene(
                        scene_id=f"sc-{sequence:03d}",
                        sequence_number=sequence,
                        heading=heading,
                        location_id=location_match.location_id
                        if location_match
                        else bible.locations[0].location_id,
                        time_of_day=_text(item, "time_of_day", "time") or "待定",
                        characters=_list_text(item.get("characters")),
                        objective=objective or summary,
                        conflict=conflict,
                        turn=_text(item, "turn", "turning_point", "outcome"),
                        visual_hook=_text(item, "visual_hook", "image"),
                        sound_hook=_text(item, "sound_hook", "sound"),
                        duration_s=float(item.get("duration_s") or 60),
                        source_chapter=chapter_number,
                        source_scene_ref=_text(item, "scene_id", "id")
                        or f"chapter:{chapter_number}",
                        lines=[ScreenplayLine(kind="action", text=summary or objective or heading)],
                    )
                )
                if len(scenes) >= 12:
                    break
            if len(scenes) >= 12:
                break
        if not scenes:
            scenes = [
                ScreenplayScene(
                    scene_id="sc-001",
                    sequence_number=1,
                    heading="第一场 · 主场景 · 待定",
                    location_id=bible.locations[0].location_id,
                    characters=[item.character_id for item in bible.characters[:2]],
                    objective=bible.logline or "建立主角、世界与首个不可逆行动",
                    conflict="由上游故事冲突生成",
                    turn="以一个改变行动方向的视觉钩子收束",
                    visual_hook="角色在空间中的第一次明确选择",
                    sound_hook=bible.style.sound_thesis,
                    lines=[ScreenplayLine(text=bible.logline or "从小说正文选择改编范围")],
                )
            ]
        return Screenplay(
            title=title,
            synopsis=bible.logline,
            acts=["建立与诱因", "升级与反转", "高潮与余韵"],
            scenes=scenes,
            estimated_duration_s=sum(scene.duration_s for scene in scenes),
        )

    @staticmethod
    def _visual_assets(bible: ProductionBible) -> list[FilmVisualAsset]:
        assets: list[FilmVisualAsset] = []
        for character in bible.characters:
            identity = character.screen_identity
            seed_suffix = (
                f"；镜头语言偏好：{character.shot_language_seed}"
                if character.shot_language_seed
                else ""
            )
            timeline = identity.visual_state_timeline
            timeline_suffix = (
                f"；跨卷状态连续性：{'；'.join(timeline[-3:])}" if timeline else ""
            )
            prompt = (
                f"{bible.style.visual_thesis}；角色资产卡：{character.name}，"
                f"{character.age}，{character.role}；剪影与体态：{identity.silhouette}；"
                f"服装配色：{'、'.join(identity.costume_palette)}；"
                f"标志性道具：{'、'.join(identity.signature_props)}；"
                f"{character.personality}；正面、侧面、背面三视图 + 表情与服装细节，"
                f"中性棚拍光，纯净背景，同一人物身份严格一致{seed_suffix}{timeline_suffix}"
            )
            assets.append(
                FilmVisualAsset(
                    asset_id=f"asset-{character.character_id}",
                    asset_type="character",
                    name=f"{character.name} · 身份设定表",
                    subject_id=character.character_id,
                    view="turnaround",
                    prompt=prompt,
                    negative_prompt="不同人物，多余肢体，身份漂移，年龄漂移，服装随机变化",
                )
            )
        for location in bible.locations:
            prompt = (
                f"{bible.style.visual_thesis}；场景资产卡：{location.name}；"
                f"空间布局：{location.spatial_layout}；"
                f"主光：{location.key_light or '场景内有动机的实景光'}；"
                f"关键道具：{'、'.join(location.recurring_props)}；"
                f"色彩基调：{location.color_mood or '跟随全片色彩脚本'}；"
                f"天气状态：{'、'.join(location.weather_states)}；"
                f"材质：{'、'.join(location.materials)}；"
                f"环境声参考：{'、'.join(location.ambient_sound)}；"
                "建立镜头、平面动线、主光方向、关键道具位置，空间结构严格一致"
            )
            assets.append(
                FilmVisualAsset(
                    asset_id=f"asset-{location.location_id}",
                    asset_type="location",
                    name=f"{location.name} · 场景资产",
                    subject_id=location.location_id,
                    view="environment_sheet",
                    prompt=prompt,
                    negative_prompt="空间漂移，门窗错位，光源方向矛盾，随机新增道具",
                )
            )
        return assets

    @staticmethod
    def _shot_seed_overrides(seed: str) -> dict[str, str | int]:
        """Deterministically map shot-language seed keywords to ShotLanguage fields.

        The seed is an optional upstream preference declared on the location
        (e.g. 「低机位、手持、35mm、实景侧光」).  Fields not mentioned in the
        seed are left to the generic shot patterns.
        """
        overrides: dict[str, str | int] = {}
        if not seed:
            return overrides
        size_map = (
            ("大特写", "extreme_close_up"),
            ("特写", "close_up"),
            ("近景", "close_up"),
            ("中景", "medium"),
            ("全景", "wide"),
            ("远景", "wide"),
        )
        angle_map = (
            ("低机位", "slight_low"),
            ("低角度", "slight_low"),
            ("仰拍", "slight_low"),
            ("高机位", "high_angle"),
            ("俯拍", "high_angle"),
            ("平视", "eye_level"),
        )
        motion_map = (
            ("手持", "subtle_handheld"),
            ("固定", "static"),
            ("跟拍", "tracking"),
            ("推镜", "slow_push"),
            ("缓推", "slow_push"),
            ("升降", "crane"),
            ("环绕", "orbit"),
        )
        light_map = (
            ("实景光", "practical"),
            ("侧光", "motivated"),
            ("逆光", "motivated"),
            ("硬光", "hard"),
            ("柔光", "soft"),
        )
        for keyword, value in size_map:
            if keyword in seed:
                overrides["shot_size"] = value
                break
        for keyword, value in angle_map:
            if keyword in seed:
                overrides["camera_angle"] = value
                break
        for keyword, value in motion_map:
            if keyword in seed:
                overrides["camera_motion"] = value
                break
        for keyword, value in light_map:
            if keyword in seed:
                overrides["lighting"] = value
                break
        lens_match = re.search(r"(\d{2,3})\s*mm", seed, re.IGNORECASE)
        if lens_match:
            overrides["lens_mm"] = int(lens_match.group(1))
        return overrides

    @staticmethod
    def _shots(screenplay: Screenplay, bible: ProductionBible) -> list[FilmShot]:
        shot_patterns = (
            ("建立镜头", "wide", "eye_level", "slow_push", 28),
            ("行动镜头", "medium", "eye_level", "tracking", 40),
            ("关系反应", "close_up", "slight_low", "subtle_handheld", 75),
            ("转折钩子", "extreme_close_up", "detail", "static", 100),
        )
        shots: list[FilmShot] = []
        for scene in screenplay.scenes:
            location = next(
                (item for item in bible.locations if item.location_id == scene.location_id),
                bible.locations[0],
            )
            seed_overrides = FilmSourceProjector._shot_seed_overrides(
                location.shot_language_seed
            )
            character_ids = [
                item.character_id
                for item in bible.characters
                if item.character_id in scene.characters or item.name in scene.characters
            ] or [item.character_id for item in bible.characters[:2]]
            character_seeds = [
                item.shot_language_seed
                for item in bible.characters
                if item.character_id in character_ids and item.shot_language_seed
            ]
            # 角色种子先作底，场景种子覆盖（空间布光与景别优先级更高）
            for char_seed in character_seeds:
                for key, value in FilmSourceProjector._shot_seed_overrides(char_seed).items():
                    seed_overrides.setdefault(key, value)
            for shot_number, (title, size, angle, motion, lens) in enumerate(shot_patterns, 1):
                shot_id = f"{scene.scene_id}-sh-{shot_number:02d}"
                size = str(seed_overrides.get("shot_size", size))
                angle = str(seed_overrides.get("camera_angle", angle))
                motion = str(seed_overrides.get("camera_motion", motion))
                lens = int(seed_overrides.get("lens_mm", lens))
                lighting = str(seed_overrides.get("lighting", "motivated"))
                seed_suffix = f"；镜头语言偏好：{location.shot_language_seed}" if (
                    location.shot_language_seed
                ) else ""
                if character_seeds:
                    seed_suffix += f"；角色镜头偏好：{'、'.join(character_seeds)}"
                if bible.style.visual_motifs:
                    seed_suffix += f"；视觉符号锚点：{'、'.join(bible.style.visual_motifs[:3])}"
                prompt = (
                    f"{bible.style.visual_thesis}；{location.name}；{scene.heading}；"
                    f"{size}，{angle}，{motion}，{lens}mm；{scene.visual_hook or scene.objective}；"
                    f"保持角色身份锁、服装、空间动线与主光方向一致{seed_suffix}"
                )
                shots.append(
                    FilmShot(
                        shot_id=shot_id,
                        scene_id=scene.scene_id,
                        shot_number=shot_number,
                        title=title,
                        duration_s=max(3, min(8, scene.duration_s / 4)),
                        language=ShotLanguage(
                            shot_size=size,
                            camera_angle=angle,
                            camera_motion=motion,
                            lighting=lighting,
                            emotion=scene.conflict or scene.objective,
                            time=scene.time_of_day,
                            lens_mm=lens,
                            composition="保持视线、轴线与叙事主体清晰",
                            focus_strategy="主体优先，必要时以焦点转移揭示信息",
                        ),
                        action=scene.objective,
                        dialogue="；".join(
                            line.text for line in scene.lines if line.kind == "dialogue"
                        ),
                        sound_design=scene.sound_hook,
                        prompt=prompt,
                        negative_prompt="身份漂移，服装漂移，空间错位，轴线错误，塑料肤质，多余肢体",
                        character_ids=character_ids,
                        location_id=scene.location_id,
                    )
                )
        return shots

    @staticmethod
    def _run_plan() -> list[RunPlanNode]:
        definitions: tuple[tuple[str, str, FilmStage, list[str], list[str]], ...] = (
            ("plan", "改编策划与制片圣经", FilmStage.PLANNING, [], ["production_bible"]),
            ("script", "剧本与场次化", FilmStage.SCREENPLAY, ["plan"], ["screenplay"]),
            (
                "assets",
                "角色 / 场景 / 道具资产锁定",
                FilmStage.VISUAL_DEVELOPMENT,
                ["plan", "script"],
                ["character_assets", "location_assets", "style_lock"],
            ),
            (
                "board",
                "分镜与摄影设计",
                FilmStage.STORYBOARD,
                ["script", "assets"],
                ["shots", "storyboard_contact_sheet"],
            ),
            (
                "render",
                "镜头生成与连续性质检",
                FilmStage.SHOT_PRODUCTION,
                ["board"],
                ["shot_candidates", "selected_takes"],
            ),
            (
                "sound",
                "对白 / 环境 / 音乐声画设计",
                FilmStage.SOUND_PICTURE,
                ["render"],
                ["dialogue_takes", "sound_cues", "music_cues"],
            ),
            (
                "edit",
                "时间线剪辑、混音与字幕",
                FilmStage.EDIT,
                ["render", "sound"],
                ["timeline", "rough_cut", "mix", "subtitles"],
            ),
            (
                "compliance",
                "合规审核（红线/高风险/正向价值观）",
                FilmStage.COMPLIANCE,
                ["edit"],
                ["compliance_report"],
            ),
            (
                "delivery",
                "总检、母版与交付包",
                FilmStage.DELIVERY,
                ["compliance"],
                ["master", "qc_report", "delivery_manifest"],
            ),
        )
        nodes: list[RunPlanNode] = []
        for index, (node_id, label, stage, depends, outputs) in enumerate(definitions):
            nodes.append(
                RunPlanNode(
                    node_id=node_id,
                    label=label,
                    stage=stage,
                    depends_on=list(depends),
                    artifact_inputs=list(depends),
                    artifact_outputs=list(outputs),
                    status=FilmStageStatus.ACTIVE if index == 0 else FilmStageStatus.PENDING,
                    human_checkpoint=stage
                    in {
                        FilmStage.PLANNING,
                        FilmStage.SCREENPLAY,
                        FilmStage.VISUAL_DEVELOPMENT,
                        FilmStage.STORYBOARD,
                        FilmStage.EDIT,
                        FilmStage.COMPLIANCE,
                        FilmStage.DELIVERY,
                    },
                    notes="协作模式停留审核；自主模式在质检通过后自动推进",
                )
            )
        return nodes

    @staticmethod
    def _stages() -> list[FilmStageState]:
        labels = {
            FilmStage.PLANNING: "上游素材已汇入制片圣经",
            FilmStage.SCREENPLAY: "等待剧本场次化",
            FilmStage.VISUAL_DEVELOPMENT: "等待角色、场景与风格资产锁定",
            FilmStage.STORYBOARD: "等待分镜与摄影设计",
            FilmStage.SHOT_PRODUCTION: "等待镜头生成与连续性质检",
            FilmStage.SOUND_PICTURE: "等待对白、环境声与音乐生产",
            FilmStage.EDIT: "等待时间线剪辑、混音与字幕",
            FilmStage.COMPLIANCE: "等待红线/高风险/正向价值观合规审核",
            FilmStage.DELIVERY: "等待总检与商业交付",
        }
        return [
            FilmStageState(
                stage=stage,
                status=FilmStageStatus.ACTIVE if index == 0 else FilmStageStatus.PENDING,
                progress=0.35 if index == 0 else 0,
                summary=labels[stage],
            )
            for index, stage in enumerate(FILM_STAGE_ORDER)
        ]

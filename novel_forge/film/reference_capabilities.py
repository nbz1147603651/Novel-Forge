"""P2 reference capabilities: asset variants (G4), storyboard contact sheet
(G2), face-warp sub-flow hooks (G6) and Hollywood screenplay export (G7).

All functions are deterministic and projection-only: they consume the locked
production bible / screenplay / shots and never bypass upstream contracts.
"""

from __future__ import annotations

from .schemas import FilmShot, FilmVisualAsset, ProductionBible, Screenplay

# G4: extended asset types beyond character/location.
ASSET_TYPE_EXPRESSION_SHEET = "expression_sheet"
ASSET_TYPE_POSE_SHEET = "pose_sheet"
ASSET_TYPE_PROP_SHEET = "prop_sheet"
ASSET_TYPE_SCENE_VARIANTS = "scene_variants"
ASSET_TYPE_STORYBOARD_CONTACT_SHEET = "storyboard_contact_sheet"

# Prompt craft rules inherited from the film-assets reference:
#  - 镜头绕行描述法: describe panels as camera moves around the subject to
#    avoid mirrored/duplicated identities.
#  - 形态自适应画幅: panel layout adapts to the locked aspect ratio.
#  - 面板数量强约束: every sheet declares an exact panel count.
_PANEL_RULES = (
    "以镜头绕行方式描述各面板视角，禁止镜像复制同一画面；"
    "面板布局按全片画幅自适应排列；面板数量严格遵守指定数量"
)

_CONTACT_GRID_COLUMNS = 4
_CONTACT_MAX_SHOTS = 16

# G7: deterministic heading localization mapping (zh → Hollywood standard).
_HEADING_PREFIX_MAP = (
    ("内景", "INT."),
    ("外景", "EXT."),
    ("内外景", "INT./EXT."),
    ("日", "DAY"),
    ("夜", "NIGHT"),
    ("黄昏", "DUSK"),
    ("清晨", "DAWN"),
)

LOCALIZATION_GUIDANCE: dict[str, list[str]] = {
    "en": [
        "场景标题使用 INT./EXT. + 地点 + DAY/NIGHT 的标准好莱坞格式",
        "成语与典故替换为英语观众可直解的意象，保留情绪不保留字面",
        "称谓按角色关系本地化（如「师父」→ Master，而非拼音直译）",
    ],
    "zh": [
        "保留中文场景标题「内景/外景」习惯写法",
        "方言与口语梗保留原文并附普通话释义",
    ],
}


def build_variant_assets(bible: ProductionBible) -> list[FilmVisualAsset]:
    """G4: expression/pose/prop sheets per character and lighting/weather
    variants per location, with templated panel-count prompts."""
    assets: list[FilmVisualAsset] = []
    for character in bible.characters:
        identity = character.screen_identity
        assets.append(
            FilmVisualAsset(
                asset_id=f"asset-{character.character_id}-expressions",
                asset_type=ASSET_TYPE_EXPRESSION_SHEET,
                name=f"{character.name} · 表情表",
                subject_id=character.character_id,
                view="expression_grid",
                prompt=(
                    f"{bible.style.visual_thesis}；角色表情设定表：{character.name}；"
                    f"{identity.silhouette}；中性/喜悦/愤怒/悲伤/恐惧/决意 6 格表情面板，"
                    f"同一身份严格一致；{_PANEL_RULES}"
                ),
                negative_prompt="身份漂移，表情同质化，镜像复制，多余人物",
            )
        )
        assets.append(
            FilmVisualAsset(
                asset_id=f"asset-{character.character_id}-poses",
                asset_type=ASSET_TYPE_POSE_SHEET,
                name=f"{character.name} · 姿态表",
                subject_id=character.character_id,
                view="pose_grid",
                prompt=(
                    f"{bible.style.visual_thesis}；角色姿态设定表：{character.name}；"
                    f"{identity.body_language or '符合角色性格的标志性体态'}；"
                    f"站立/行走/奔跑/对峙/低落 5 格全身姿态面板，比例严格一致；{_PANEL_RULES}"
                ),
                negative_prompt="比例漂移，身份漂移，镜像复制，多余人物",
            )
        )
        if identity.signature_props:
            assets.append(
                FilmVisualAsset(
                    asset_id=f"asset-{character.character_id}-props",
                    asset_type=ASSET_TYPE_PROP_SHEET,
                    name=f"{character.name} · 标志道具表",
                    subject_id=character.character_id,
                    view="prop_grid",
                    prompt=(
                        f"{bible.style.visual_thesis}；道具设定表："
                        f"{'、'.join(identity.signature_props)}；"
                        "每件道具含整体、细节与持握状态 3 格面板，材质与尺寸跨镜头严格一致；"
                        f"{_PANEL_RULES}"
                    ),
                    negative_prompt="道具变形，尺寸漂移，镜像复制",
                )
            )
    for location in bible.locations:
        variants = [
            variant
            for variant in (location.weather_states or []) + ([location.key_light] if location.key_light else [])
            if variant
        ][:4] or ["常态光", "夜景光"]
        assets.append(
            FilmVisualAsset(
                asset_id=f"asset-{location.location_id}-variants",
                asset_type=ASSET_TYPE_SCENE_VARIANTS,
                name=f"{location.name} · 光影/天气变体",
                subject_id=location.location_id,
                view="environment_variants",
                prompt=(
                    f"{bible.style.visual_thesis}；场景变体设定表：{location.name}；"
                    f"{location.spatial_layout}；{len(variants)} 格变体面板："
                    f"{'、'.join(variants)}；机位与空间结构保持不变，仅光照与天气变化；"
                    f"{_PANEL_RULES}"
                ),
                negative_prompt="空间漂移，门窗错位，机位改变，随机新增道具",
            )
        )
    return assets


def build_storyboard_contact_sheet(
    shots: list[FilmShot], bible: ProductionBible
) -> FilmVisualAsset | None:
    """G2: one grid-composited contact sheet so the director can review the
    rhythm of the whole sequence at a glance."""
    if not shots:
        return None
    selected = shots[:_CONTACT_MAX_SHOTS]
    columns = _CONTACT_GRID_COLUMNS
    rows = (len(selected) + columns - 1) // columns
    panel_lines = "；".join(
        f"第{shot.shot_number}格：{shot.title}（{shot.language.shot_size}）"
        for shot in selected
    )
    return FilmVisualAsset(
        asset_id="asset-storyboard-contact-sheet",
        asset_type=ASSET_TYPE_STORYBOARD_CONTACT_SHEET,
        name=f"{bible.title} · 分镜接触表",
        subject_id="storyboard",
        view="contact_sheet",
        prompt=(
            f"{bible.style.visual_thesis}；分镜接触表：{columns}列{rows}行网格，"
            f"共{len(selected)}格分镜缩略图，按场次顺序排列；{panel_lines}；"
            "全表统一光影基调与画幅，供导演审阅整体节奏；"
            f"{_PANEL_RULES}"
        ),
        negative_prompt="格序错乱，画幅不一致，身份漂移，多余装饰",
    )


def render_hollywood_screenplay(screenplay: Screenplay, shots: list[FilmShot]) -> str:
    """G7: render the screenplay in Hollywood format (INT./EXT. headings with
    SHOT annotations).  Deterministic: heading tokens are mapped from the
    Chinese convention; shots are annotated under their scene."""
    lines: list[str] = [f"{screenplay.title.upper()}", ""]
    if screenplay.synopsis:
        lines.extend([f"LOGLINE: {screenplay.synopsis}", ""])
    shots_by_scene: dict[str, list[FilmShot]] = {}
    for shot in shots:
        shots_by_scene.setdefault(shot.scene_id, []).append(shot)
    for scene in screenplay.scenes:
        heading = scene.heading
        for zh, en in _HEADING_PREFIX_MAP:
            heading = heading.replace(zh, en)
        lines.append(f"SCENE {scene.sequence_number:02d} — {heading}".upper())
        if scene.objective:
            lines.append(f"ACTION: {scene.objective}")
        for line in scene.lines:
            if line.kind == "dialogue" and line.speaker:
                lines.append(f"{line.speaker.upper()}")
                lines.append(f"\t{line.text}")
            elif line.text:
                lines.append(f"ACTION: {line.text}")
        scene_shots = shots_by_scene.get(scene.scene_id, [])
        for shot in scene_shots:
            lines.append(
                f"SHOT {shot.shot_number:02d}: {shot.title.upper()} — "
                f"{shot.language.shot_size.upper()}, {shot.language.camera_motion.upper()}, "
                f"{shot.language.lens_mm}MM"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

"""Registered, versioned node catalog for the 映界 workflow editor."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .prompting import prompt_template_for
from .schemas import FilmStage
from .workflow_models import (
    FilmGraphDefinition,
    FilmGraphEdge,
    FilmGraphNode,
    FilmGraphPosition,
    FilmNodeDefinition,
    FilmNodePort,
)


def _port(port_id: str, label: str, artifact_type: str, *, required: bool = True) -> FilmNodePort:
    return FilmNodePort(
        port_id=port_id,
        label=label,
        artifact_type=artifact_type,
        required=required,
    )


_FILM_NODE_CATALOG: tuple[FilmNodeDefinition, ...] = (
    FilmNodeDefinition(
        type_id="creative_brief",
        label="创意简报",
        description="汇总小说契约、制作目标、受众、格式和风险边界。",
        category="策划与风格",
        stage=FilmStage.PLANNING,
        output_ports=[_port("brief", "创意简报", "CreativeBrief")],
        config_schema={"type": "object", "properties": {"production_goal": {"type": "string"}}},
    ),
    FilmNodeDefinition(
        type_id="style_lock",
        label="风格锁定",
        description="锁定媒介、摄影、色彩、风格参考与负面约束。",
        category="策划与风格",
        stage=FilmStage.PLANNING,
        input_ports=[_port("brief", "创意简报", "CreativeBrief")],
        output_ports=[_port("style", "风格锁", "StyleLock")],
        human_checkpoint=True,
    ),
    FilmNodeDefinition(
        type_id="character_assets",
        label="角色资产生成",
        description="角色身份锁、转面、服装和表演参考资产。",
        category="资产生成",
        stage=FilmStage.VISUAL_DEVELOPMENT,
        input_ports=[
            _port("brief", "创意简报", "CreativeBrief"),
            _port("style", "风格锁", "StyleLock"),
        ],
        output_ports=[_port("assets", "角色资产", "CharacterAssetPack")],
        paid=True,
        estimated_cost_usd=4.8,
        estimated_duration_s=90,
    ),
    FilmNodeDefinition(
        type_id="scene_assets",
        label="场景资产生成",
        description="场景空间、材质、光线、环境声和连续性资产。",
        category="资产生成",
        stage=FilmStage.VISUAL_DEVELOPMENT,
        input_ports=[
            _port("brief", "创意简报", "CreativeBrief"),
            _port("style", "风格锁", "StyleLock"),
        ],
        output_ports=[_port("assets", "场景资产", "SceneAssetPack")],
        paid=True,
        estimated_cost_usd=4.8,
        estimated_duration_s=90,
    ),
    FilmNodeDefinition(
        type_id="prop_assets",
        label="道具资产生成",
        description="叙事道具、版本、材质和跨镜头一致性资产。",
        category="资产生成",
        stage=FilmStage.VISUAL_DEVELOPMENT,
        input_ports=[
            _port("brief", "创意简报", "CreativeBrief"),
            _port("style", "风格锁", "StyleLock"),
        ],
        output_ports=[_port("assets", "道具资产", "PropAssetPack")],
        paid=True,
        estimated_cost_usd=3.2,
        estimated_duration_s=70,
    ),
    FilmNodeDefinition(
        type_id="shot_list",
        label="镜头清单",
        description="将剧本和资产锁转成可确认的镜头表。",
        category="镜头与分镜",
        stage=FilmStage.STORYBOARD,
        input_ports=[
            _port("brief", "创意简报", "CreativeBrief"),
            _port("characters", "角色资产", "CharacterAssetPack"),
            _port("scenes", "场景资产", "SceneAssetPack"),
            _port("props", "道具资产", "PropAssetPack", required=False),
        ],
        output_ports=[_port("shots", "镜头表", "ShotList")],
        human_checkpoint=True,
    ),
    FilmNodeDefinition(
        type_id="storyboard",
        label="分镜脚本",
        description="按确认后的镜头表生成结构化分镜。",
        category="镜头与分镜",
        stage=FilmStage.STORYBOARD,
        input_ports=[_port("shots", "镜头表", "ShotList"), _port("style", "风格锁", "StyleLock")],
        output_ports=[_port("storyboard", "分镜", "Storyboard")],
        human_checkpoint=True,
    ),
    FilmNodeDefinition(
        type_id="h3_context_ir",
        label="平台视频上下文 IR",
        description="按目标平台与模型，把参考素材、画面过程和声音结构编译为视频提示词 IR。",
        category="检索与上下文",
        stage=FilmStage.SHOT_PRODUCTION,
        input_ports=[
            _port("storyboard", "分镜", "Storyboard"),
            _port("style", "风格锁", "StyleLock"),
        ],
        output_ports=[_port("prompt", "视频提示词 IR", "H3PromptIR")],
        provider_capabilities=[
            "minimax:h3_context_ir",
            "bailian:prompt_adapter",
            "volcengine_ark:prompt_adapter",
        ],
        paid=True,
        human_checkpoint=True,
        estimated_cost_usd=0.4,
        estimated_duration_s=45,
    ),
    FilmNodeDefinition(
        type_id="minimax_h3_video",
        label="多平台视频生成",
        description="按平台能力注册表路由 MiniMax H3、阿里百炼 Wan 或火山方舟 Seedance。",
        category="视频生成",
        stage=FilmStage.SHOT_PRODUCTION,
        input_ports=[_port("prompt", "H3 提示词", "H3PromptIR")],
        output_ports=[_port("video", "视频片段", "VideoClip")],
        config_schema={
            "type": "object",
            "required": ["mode", "duration", "resolution"],
            "properties": {
                "mode": {
                    "enum": [
                        "text_to_video",
                        "image_to_video",
                        "first_last_frame",
                        "reference_to_video",
                    ]
                },
                "duration": {"type": "integer", "minimum": 2, "maximum": 15},
                "resolution": {"enum": ["720P", "768P", "1080P", "2K"]},
                "ratio": {"enum": ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]},
                "aigc_watermark": {"type": "boolean"},
                "watermark": {"type": "boolean"},
                "prompt_optimizer": {"type": "boolean"},
                "generate_audio": {"type": "boolean"},
                "return_last_frame": {"type": "boolean"},
                "seed": {"type": ["integer", "null"], "minimum": 0},
            },
        },
        default_config={
            "mode": "reference_to_video",
            "duration": 10,
            "resolution": "768P",
            "ratio": "adaptive",
            "aigc_watermark": False,
            "reference_roles": [],
        },
        provider_capabilities=[
            "minimax:h3_video_v2",
            "bailian:wan_video_2_7",
            "volcengine_ark:seedance_2_0",
            "native_audio",
            "multimodal_reference",
        ],
        paid=True,
        estimated_cost_usd=6.8,
        estimated_duration_s=210,
        allows_bypass=False,
    ),
    FilmNodeDefinition(
        type_id="minimax_h3_regenerate_2k",
        label="H3 768P → 2K 重生成",
        description="仅对符合 H3 768P 输出规格的任务或源视频执行 2K 重生成。",
        category="视频生成",
        stage=FilmStage.SHOT_PRODUCTION,
        input_ports=[_port("source", "H3 768P 视频", "VideoClip")],
        output_ports=[_port("video", "2K 视频片段", "VideoClip")],
        config_schema={
            "type": "object",
            "properties": {
                "source_mode": {"enum": ["source_task_id", "base_video"]},
                "aigc_watermark": {"type": "boolean"},
            },
        },
        default_config={"source_mode": "source_task_id", "aigc_watermark": False},
        provider_capabilities=["minimax:regenerate_2k"],
        paid=True,
        estimated_cost_usd=3.4,
        estimated_duration_s=160,
        allows_bypass=False,
    ),
    FilmNodeDefinition(
        type_id="film_qc",
        label="QC 质检与评分",
        description="检查身份、连续性、镜头、声音、合规与交付规格。",
        category="质量与交付",
        stage=FilmStage.COMPLIANCE,
        input_ports=[_port("video", "视频片段", "VideoClip")],
        output_ports=[_port("report", "质检报告", "QCReport")],
        human_checkpoint=True,
        allows_bypass=False,
    ),
    FilmNodeDefinition(
        type_id="delivery",
        label="成片交付",
        description="输出媒体、字幕、声音、清单、OTIO 与质检记录。",
        category="质量与交付",
        stage=FilmStage.DELIVERY,
        input_ports=[
            _port("video", "视频片段", "VideoClip"),
            _port("report", "质检报告", "QCReport"),
        ],
        output_ports=[_port("package", "交付包", "DeliveryPackage")],
        human_checkpoint=True,
        allows_bypass=False,
    ),
)

FILM_NODE_CATALOG: tuple[FilmNodeDefinition, ...] = tuple(
    definition.model_copy(
        update={"prompt_template": prompt_template_for(definition.type_id)}
    )
    for definition in _FILM_NODE_CATALOG
)


def film_node_catalog() -> list[FilmNodeDefinition]:
    return [item.model_copy(deep=True) for item in FILM_NODE_CATALOG]


def node_definition_map() -> dict[str, FilmNodeDefinition]:
    return {item.type_id: item for item in FILM_NODE_CATALOG}


def _node(type_id: str, node_id: str, x: float, y: float, **config: Any) -> FilmGraphNode:
    definition = node_definition_map()[type_id]
    merged_config = {**definition.default_config, **config}
    provider_id = "minimax" if type_id in {"h3_context_ir", "minimax_h3_video"} else ""
    model_id = "MiniMax-H3" if type_id in {"h3_context_ir", "minimax_h3_video"} else ""
    return FilmGraphNode(
        node_id=node_id,
        type_id=type_id,
        type_version=definition.version,
        label=definition.label,
        stage=definition.stage,
        position=FilmGraphPosition(x=x, y=y),
        config=merged_config,
        prompt=definition.prompt_template.model_copy(deep=True),
        input_ports=definition.input_ports,
        output_ports=definition.output_ports,
        provider_id=provider_id,
        model_id=model_id,
        human_checkpoint=definition.human_checkpoint,
        estimated_cost_usd=definition.estimated_cost_usd,
    )


def default_film_graph(project_id: str) -> FilmGraphDefinition:
    nodes = [
        _node("creative_brief", "creative-brief", 40, 60),
        _node("style_lock", "style-lock", 300, 60),
        _node("character_assets", "character-assets", 560, 20),
        _node("scene_assets", "scene-assets", 560, 170),
        _node("prop_assets", "prop-assets", 560, 320),
        _node("shot_list", "shot-list", 840, 60),
        _node("storyboard", "storyboard", 1100, 60),
        _node("h3_context_ir", "h3-context-ir", 1360, 60),
        _node("minimax_h3_video", "minimax-h3", 1620, 60),
        _node("film_qc", "film-qc", 1880, 60),
        _node("delivery", "delivery", 2140, 60),
    ]
    links: list[tuple[str, str, str, str]] = [
        ("creative-brief", "brief", "style-lock", "brief"),
        ("creative-brief", "brief", "character-assets", "brief"),
        ("style-lock", "style", "character-assets", "style"),
        ("creative-brief", "brief", "scene-assets", "brief"),
        ("style-lock", "style", "scene-assets", "style"),
        ("creative-brief", "brief", "prop-assets", "brief"),
        ("style-lock", "style", "prop-assets", "style"),
        ("creative-brief", "brief", "shot-list", "brief"),
        ("character-assets", "assets", "shot-list", "characters"),
        ("scene-assets", "assets", "shot-list", "scenes"),
        ("prop-assets", "assets", "shot-list", "props"),
        ("shot-list", "shots", "storyboard", "shots"),
        ("style-lock", "style", "storyboard", "style"),
        ("storyboard", "storyboard", "h3-context-ir", "storyboard"),
        ("style-lock", "style", "h3-context-ir", "style"),
        ("h3-context-ir", "prompt", "minimax-h3", "prompt"),
        ("minimax-h3", "video", "film-qc", "video"),
        ("minimax-h3", "video", "delivery", "video"),
        ("film-qc", "report", "delivery", "report"),
    ]
    edges = [
        FilmGraphEdge(
            edge_id=f"{source}:{source_port}->{target}:{target_port}",
            source_node_id=source,
            source_port_id=source_port,
            target_node_id=target,
            target_port_id=target_port,
        )
        for source, source_port, target, target_port in links
    ]
    return FilmGraphDefinition(project_id=project_id, nodes=nodes, edges=edges)


def catalog_by_category(
    items: Iterable[FilmNodeDefinition] | None = None,
) -> dict[str, list[FilmNodeDefinition]]:
    grouped: dict[str, list[FilmNodeDefinition]] = {}
    for item in items or FILM_NODE_CATALOG:
        grouped.setdefault(item.category, []).append(item)
    return grouped

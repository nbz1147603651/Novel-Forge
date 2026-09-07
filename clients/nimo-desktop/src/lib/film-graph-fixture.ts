import type {
  FilmGraphDefinitionView,
  FilmNodePromptView,
  FilmNodeDefinitionView,
  FilmNodePortView,
  FilmStageId,
} from "@nimo/engine-contracts";

const port = (
  portId: string,
  label: string,
  artifactType: string,
  required = true,
): FilmNodePortView => ({ portId, label, artifactType, required, multiple: false });

function promptTemplate(typeId: string): FilmNodePromptView {
  const isH3 = typeId === "h3_context_ir" || typeId === "minimax_h3_video";
  const specs = isH3 ? [
    ["reference_materials", "参考素材说明", "逐项声明 @图片、@视频、@音频的用途和保持特征。"],
    ["core_creative", "核心创意", "锁定主体、地点、事件、风格、时长与画幅。"],
    ["visual_timeline", "画面过程描述", "按时长拆分 Shot、动作、景别、运镜与切镜。"],
    ["dialogue_performance", "对白与表演", "使用已登记角色和原文台词，明确画内外关系。"],
    ["overall_soundscape", "整体声景", "分离环境底床与瞬时 SFX，按事件描述相对混音。"],
    ["non_diegetic_music", "非叙事性音乐", "无配乐时写 N/A；否则描述进入与退出事件。"],
    ["negative_constraints", "负向与能力边界", "禁止身份漂移、无来源对白与随机新增资产。"],
  ] : [
    ["source_contract", "上游叙事契约", "只消费已连接且版本化的小说与配音上游产物。"],
    ["creative_task", "当前创作任务", "执行当前节点唯一职责，不与相邻节点重复生成。"],
    ["hard_constraints", "P0 硬约束", "P0 硬约束优先于 P1 叙事执行与 P2 风格润色。"],
    ["quality_contract", "质量与产物契约", "输出符合注册端口的结构化产物并可追溯来源。"],
  ];
  return {
    schemaVersion: "1.0",
    templateId: `film.${typeId}`,
    templateVersion: "2.0.0",
    purpose: `${typeId} 节点提示词`,
    sections: specs.map(([sectionId, label, content]) => ({
      sectionId: sectionId!,
      label: label!,
      guidance: "可编辑的结构化提示词段落",
      content: content!,
      required: true,
      editable: true,
      sources: isH3 ? ["novel", "voice", "h3_guide"] : ["novel", "voice", "workflow"],
    })),
    userNotes: "",
    updatedAt: "2026-08-09T08:00:00Z",
  };
}

export const mockFilmNodeCatalog: readonly FilmNodeDefinitionView[] = [
  definition("creative_brief", "创意简报", "策划与风格", "planning", [], [port("brief", "创意简报", "CreativeBrief")]),
  definition("style_lock", "风格锁定", "策划与风格", "planning", [port("brief", "创意简报", "CreativeBrief")], [port("style", "风格锁", "StyleLock")], { humanCheckpoint: true }),
  definition("character_assets", "角色资产生成", "资产生成", "visual_development", [port("brief", "创意简报", "CreativeBrief"), port("style", "风格锁", "StyleLock")], [port("assets", "角色资产", "CharacterAssetPack")], { paid: true, cost: 4.8, duration: 90 }),
  definition("scene_assets", "场景资产生成", "资产生成", "visual_development", [port("brief", "创意简报", "CreativeBrief"), port("style", "风格锁", "StyleLock")], [port("assets", "场景资产", "SceneAssetPack")], { paid: true, cost: 4.8, duration: 90 }),
  definition("prop_assets", "道具资产生成", "资产生成", "visual_development", [port("brief", "创意简报", "CreativeBrief"), port("style", "风格锁", "StyleLock")], [port("assets", "道具资产", "PropAssetPack")], { paid: true, cost: 3.2, duration: 70 }),
  definition("shot_list", "镜头清单", "镜头与分镜", "storyboard", [port("brief", "创意简报", "CreativeBrief"), port("characters", "角色资产", "CharacterAssetPack"), port("scenes", "场景资产", "SceneAssetPack"), port("props", "道具资产", "PropAssetPack", false)], [port("shots", "镜头表", "ShotList")], { humanCheckpoint: true }),
  definition("storyboard", "分镜脚本", "镜头与分镜", "storyboard", [port("shots", "镜头表", "ShotList"), port("style", "风格锁", "StyleLock")], [port("storyboard", "分镜", "Storyboard")], { humanCheckpoint: true }),
  definition("h3_context_ir", "平台视频上下文 IR", "检索与上下文", "shot_production", [port("storyboard", "分镜", "Storyboard"), port("style", "风格锁", "StyleLock")], [port("prompt", "视频提示词 IR", "H3PromptIR")], { paid: true, cost: .4, duration: 45, humanCheckpoint: true, capabilities: ["minimax:h3_context_ir", "bailian:prompt_adapter", "volcengine_ark:prompt_adapter"] }),
  definition("minimax_h3_video", "多平台视频生成", "视频生成", "shot_production", [port("prompt", "视频提示词 IR", "H3PromptIR")], [port("video", "视频片段", "VideoClip")], { paid: true, cost: 6.8, duration: 210, capabilities: ["minimax:h3_video_v2", "bailian:wan_video_2_7", "volcengine_ark:seedance_2_0", "native_audio", "multimodal_reference"], defaultConfig: { mode: "reference_to_video", duration: 10, resolution: "768P", ratio: "adaptive", aigc_watermark: false, reference_roles: [] } }),
  definition("minimax_h3_regenerate_2k", "H3 768P → 2K 重生成", "视频生成", "shot_production", [port("source", "H3 768P 视频", "VideoClip")], [port("video", "2K 视频片段", "VideoClip")], { paid: true, cost: 3.4, duration: 160, capabilities: ["minimax:regenerate_2k"], defaultConfig: { source_mode: "source_task_id", aigc_watermark: false } }),
  definition("film_qc", "QC 质检与评分", "质量与交付", "compliance", [port("video", "视频片段", "VideoClip")], [port("report", "质检报告", "QCReport")], { humanCheckpoint: true }),
  definition("delivery", "成片交付", "质量与交付", "delivery", [port("video", "视频片段", "VideoClip"), port("report", "质检报告", "QCReport")], [port("package", "交付包", "DeliveryPackage")], { humanCheckpoint: true }),
];

function definition(
  typeId: string,
  label: string,
  category: string,
  stage: FilmStageId,
  inputPorts: readonly FilmNodePortView[],
  outputPorts: readonly FilmNodePortView[],
  options: {
    readonly paid?: boolean;
    readonly cost?: number;
    readonly duration?: number;
    readonly humanCheckpoint?: boolean;
    readonly capabilities?: readonly string[];
    readonly defaultConfig?: Readonly<Record<string, unknown>>;
  } = {},
): FilmNodeDefinitionView {
  return {
    typeId,
    version: "1.0",
    label,
    description: label,
    category,
    stage,
    inputPorts,
    outputPorts,
    configSchema: {},
    defaultConfig: options.defaultConfig ?? {},
    promptTemplate: promptTemplate(typeId),
    permissions: [],
    providerCapabilities: options.capabilities ?? [],
    cacheable: true,
    allowsBypass: !["minimax_h3_video", "delivery"].includes(typeId),
    paid: options.paid ?? false,
    humanCheckpoint: options.humanCheckpoint ?? false,
    estimatedCostUsd: options.cost ?? 0,
    estimatedDurationS: options.duration ?? 0,
  };
}

const layout: readonly [string, string, number, number][] = [
  ["creative-brief", "creative_brief", 40, 60],
  ["style-lock", "style_lock", 300, 60],
  ["character-assets", "character_assets", 560, 20],
  ["scene-assets", "scene_assets", 560, 170],
  ["prop-assets", "prop_assets", 560, 320],
  ["shot-list", "shot_list", 840, 60],
  ["storyboard", "storyboard", 1100, 60],
  ["h3-context-ir", "h3_context_ir", 1360, 60],
  ["minimax-h3", "minimax_h3_video", 1620, 60],
  ["film-qc", "film_qc", 1880, 60],
  ["delivery", "delivery", 2140, 60],
];

const links: readonly [string, string, string, string][] = [
  ["creative-brief", "brief", "style-lock", "brief"],
  ["creative-brief", "brief", "character-assets", "brief"],
  ["style-lock", "style", "character-assets", "style"],
  ["creative-brief", "brief", "scene-assets", "brief"],
  ["style-lock", "style", "scene-assets", "style"],
  ["creative-brief", "brief", "prop-assets", "brief"],
  ["style-lock", "style", "prop-assets", "style"],
  ["creative-brief", "brief", "shot-list", "brief"],
  ["character-assets", "assets", "shot-list", "characters"],
  ["scene-assets", "assets", "shot-list", "scenes"],
  ["prop-assets", "assets", "shot-list", "props"],
  ["shot-list", "shots", "storyboard", "shots"],
  ["style-lock", "style", "storyboard", "style"],
  ["storyboard", "storyboard", "h3-context-ir", "storyboard"],
  ["style-lock", "style", "h3-context-ir", "style"],
  ["h3-context-ir", "prompt", "minimax-h3", "prompt"],
  ["minimax-h3", "video", "film-qc", "video"],
  ["minimax-h3", "video", "delivery", "video"],
  ["film-qc", "report", "delivery", "report"],
];

export function createMockFilmGraph(projectId: string): FilmGraphDefinitionView {
  const definitions = new Map(mockFilmNodeCatalog.map((item) => [item.typeId, item]));
  const definitionView: FilmGraphDefinitionView = {
    schemaVersion: "2.0",
    graphId: `film-graph-${projectId}`,
    projectId,
    revision: 1,
    nodes: layout.map(([nodeId, typeId, x, y]) => {
      const item = definitions.get(typeId)!;
      return {
        nodeId,
        typeId,
        typeVersion: item.version,
        label: item.label,
        stage: item.stage,
        position: { x, y },
        groupId: "",
        config: item.defaultConfig,
        prompt: item.promptTemplate,
        inputPorts: item.inputPorts,
        outputPorts: item.outputPorts,
        providerId: typeId.startsWith("h3_") || typeId === "minimax_h3_video" ? "minimax" : "",
        modelId: typeId === "minimax_h3_video" || typeId === "h3_context_ir" ? "MiniMax-H3" : "",
        bypassed: false,
        disabled: false,
        humanCheckpoint: item.humanCheckpoint,
        estimatedCostUsd: item.estimatedCostUsd,
      };
    }),
    edges: links.map(([sourceNodeId, sourcePortId, targetNodeId, targetPortId]) => ({
      edgeId: `${sourceNodeId}:${sourcePortId}->${targetNodeId}:${targetPortId}`,
      sourceNodeId,
      sourcePortId,
      targetNodeId,
      targetPortId,
    })),
    groups: [],
    viewport: { x: 0, y: 0, zoom: .82 },
    updatedAt: "2026-08-09T08:00:00Z",
  };
  return definitionView;
}

"""Versioned, editable prompt contracts for the 映界 workflow graph.

The registry borrows the novel pipeline's P0/P1/P2 constraint ordering and
source-projection discipline, plus the dubbing pipeline's separation of actor
performance, ambience, effects and music.  MiniMax-specific rendering follows
the H3 guide without leaking provider grammar into React components.
"""

from __future__ import annotations

import re

from .schemas import utc_now_iso
from .workflow_models import (
    FilmGraphNode,
    FilmNodePrompt,
    FilmPromptOptimizationResult,
    FilmPromptSection,
    FilmPromptSource,
)

PROMPT_TEMPLATE_VERSION = "2.0.0"
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_TIMECODE = re.compile(r"(?:^|\n)\s*(?:\d+(?:\.\d+)?\s*[-〜~至]\s*)?\d+(?:\.\d+)?\s*秒")


def _section(
    section_id: str,
    label: str,
    content: str,
    guidance: str,
    *sources: FilmPromptSource,
    required: bool = True,
) -> FilmPromptSection:
    return FilmPromptSection(
        section_id=section_id,
        label=label,
        guidance=guidance,
        content=content,
        required=required,
        sources=list(sources),
    )


def _prompt(type_id: str, purpose: str, *sections: FilmPromptSection) -> FilmNodePrompt:
    return FilmNodePrompt(
        template_id=f"film.{type_id}",
        template_version=PROMPT_TEMPLATE_VERSION,
        purpose=purpose,
        sections=list(sections),
    )


def _novel_contract(task: str, output: str) -> tuple[FilmPromptSection, ...]:
    return (
        _section(
            "source_contract",
            "上游叙事契约",
            "只消费已连接的小说成稿、故事圣经、人物圣经与版本化上游产物；保留人物、因果、时间线、POV 与来源引用，不从未来章节补写信息。",
            "沿用小说模块的有界来源投影，防止节点绕过上游契约。",
            FilmPromptSource.NOVEL,
            FilmPromptSource.WORKFLOW,
        ),
        _section(
            "creative_task",
            "当前创作任务",
            task,
            "说明本节点唯一职责，避免与相邻节点重复生成。",
            FilmPromptSource.NOVEL,
            FilmPromptSource.WORKFLOW,
        ),
        _section(
            "hard_constraints",
            "P0 硬约束",
            "事实忠实度、已锁定资产、画幅时长、人物身份、空间连续性和合规要求优先；冲突顺序为 P0 硬约束 > P1 叙事执行 > P2 风格润色。",
            "继承小说 PromptBuilder 的 P0/P1/P2 优先级。",
            FilmPromptSource.NOVEL,
        ),
        _section(
            "quality_contract",
            "质量与产物契约",
            output,
            "描述下游可消费的结构化结果与自检标准。",
            FilmPromptSource.NOVEL,
            FilmPromptSource.WORKFLOW,
        ),
    )


def _sound_contract(content: str) -> FilmPromptSection:
    return _section(
        "voice_sound_contract",
        "对白、表演与声景合同",
        content,
        "继承配音模块的角色声线、潜台词、对白、环境底床、SFX 与 BGM 职责分离。",
        FilmPromptSource.VOICE,
    )


_H3_SECTIONS = (
    _section(
        "reference_materials",
        "参考素材说明",
        "逐项声明 @图片、@视频、@音频的编号、用途和必须保持的特征；可用角色包括人物身份、物体、场景、首尾关键帧、风格、构图、故事版、动作、运镜、音色、音频复用或视频编辑。无素材时明确写“无参考素材”。",
        "H3 不会替用户猜素材用途；R2V 素材角色必须显式且互不冲突。",
        FilmPromptSource.H3_GUIDE,
        FilmPromptSource.WORKFLOW,
    ),
    _section(
        "core_creative",
        "核心创意",
        "用一句可视化的话锁定主体、地点、事件、题材/风格、时长、画幅与镜头策略；避免抽象比喻，不新增小说契约外的剧情事实。",
        "核心创意是全片不漂移的意图锚点。",
        FilmPromptSource.NOVEL,
        FilmPromptSource.H3_GUIDE,
    ),
    _section(
        "visual_timeline",
        "画面过程描述",
        "按实际时长拆分 Shot 或时间段；每段写景别、已登记主体、可见动作、空间与光线连续性、运镜和切镜。对白长度必须符合镜头时长；跨镜对白明确 J-cut/L-cut 与画内/画外说话人。",
        "长于单一动作的片段使用时间线；一镜到底时保持连续描述并删除互相冲突的切镜。",
        FilmPromptSource.NOVEL,
        FilmPromptSource.H3_GUIDE,
    ),
    _section(
        "dialogue_performance",
        "对白与表演",
        "逐句写明已登记角色、原文台词、潜台词、可见表演和画内/画外关系；声线身份保持稳定。没有对白时明确人物保持安静、嘴巴自然闭合、无念白与无口型变化。",
        "台词来自小说成稿，表演合同来自配音模块，不让模型生成无意义对白。",
        FilmPromptSource.NOVEL,
        FilmPromptSource.VOICE,
        FilmPromptSource.H3_GUIDE,
    ),
    _section(
        "overall_soundscape",
        "整体声景",
        "按画面事件顺序描述环境底床、动作音效、空间远近和相对混音；持续环境声与瞬时 SFX 分开，声音事件同时靠近对应动作描述。",
        "沿用配音 cue sheet；用相对强弱与叙事优先级，不使用百分比或 dB 控制 H3。",
        FilmPromptSource.VOICE,
        FilmPromptSource.H3_GUIDE,
    ),
    _section(
        "non_diegetic_music",
        "非叙事性音乐",
        "无配乐时写 N/A；需要配乐时描述情绪、乐器、进入/退出所依附的画面事件和对白避让，不使用不可验证的精确音量数值。",
        "音乐是独立叙事层，不与环境声或演员表演混写。",
        FilmPromptSource.VOICE,
        FilmPromptSource.H3_GUIDE,
    ),
    _section(
        "negative_constraints",
        "负向与能力边界",
        "禁止身份漂移、随机新增人物/道具、空间轴线错误、无来源对白、文字水印与可识别品牌。字幕抑制、人物替换、精确循环和严格秒点均为不稳定项，只能作为倾向描述，不作结果保证。",
        "把已知不稳定能力转成风险提示，避免伪保证。",
        FilmPromptSource.H3_GUIDE,
        FilmPromptSource.WORKFLOW,
    ),
)


PROMPT_TEMPLATES: dict[str, FilmNodePrompt] = {
    "creative_brief": _prompt(
        "creative_brief",
        "把小说项目契约投影成影视生产的唯一创意简报。",
        *_novel_contract(
            "提炼受众、格式、核心戏剧问题、改编范围、不可变事实和生产风险；不写镜头提示词。",
            "输出 CreativeBrief，所有结论可追溯到小说来源版本，并标出缺失输入、假设和人工确认项。",
        ),
        _sound_contract("提取作品的声音命题、叙述距离、角色声线身份与需继承的声音母题，但不在此节点制作音频。"),
    ),
    "style_lock": _prompt(
        "style_lock",
        "把小说风格画像与声音身份转换为可执行的视听风格锁。",
        *_novel_contract(
            "锁定媒介、摄影、构图、色彩、光线、节奏、参考维度和负面约束；只迁移抽象形式，不复制参考作品内容。",
            "输出 StyleLock，区分可变建议与不可变风格约束，并为画面与声音提供统一检索词。",
        ),
        _sound_contract("锁定对白距离、声场密度、环境声身份、音乐职责与静默策略；声音风格不得覆盖人物身份和原文台词。"),
    ),
    "character_assets": _prompt(
        "character_assets",
        "生成可跨镜头复用的角色身份与表演参考资产。",
        *_novel_contract(
            "依据人物圣经建立脸部、年龄、体态、服装、标志道具、状态时间线和允许变体；禁止把临时情绪固化成永久身份。",
            "输出 CharacterAssetPack，包含身份锚点、转面/表情/服装变体、版本与一致性检查。",
        ),
        _sound_contract("关联角色 voice_id、年龄感、音高中心、语速范围和潜台词表演合同；视觉表情与声线情绪必须同源但职责分离。"),
    ),
    "scene_assets": _prompt(
        "scene_assets",
        "建立可复用的场景空间、光线和声音身份。",
        *_novel_contract(
            "从故事圣经与场次投影抽取空间拓扑、时代材质、入口出口、关键机位、光源方向和允许变化，不引入未来剧情信息。",
            "输出 SceneAssetPack，包含空间锚点、日夜/天气变体、连续性禁区和可复用场景版本。",
        ),
        _sound_contract("为地点建立持续环境底床、远近层次、可复用 asset_hint 与静默基线；不要把一次性动作声写成地点身份。"),
    ),
    "prop_assets": _prompt(
        "prop_assets",
        "建立叙事道具的外观、尺度、状态与版本谱系。",
        *_novel_contract(
            "只生成在小说事实、场次动作或人物身份中有依据的道具；标记持有人、首次出现、状态变化、材质和跨镜头连续性。",
            "输出 PropAssetPack，包含正交视图、尺度参照、允许磨损/损坏变体、状态时间线和版本锁。",
        ),
        _sound_contract("记录道具的真实声源特征与可复用 SFX 标签；只有被动作触发时才进入镜头声景。"),
    ),
    "shot_list": _prompt(
        "shot_list",
        "把锁定场次和资产转换为可确认的镜头清单。",
        *_novel_contract(
            "逐场保留目标、冲突、转折、视觉钩子与来源引用；镜头数量服从时长、对白密度和动作复杂度，不改写事件结果。",
            "输出 ShotList；每镜包含时长、景别、主体、动作、机位/运镜、连续性锚点、对白/声音引用和前后依赖。",
        ),
        _sound_contract("台词按镜头可容纳长度裁剪但保留原意和说话人；标记 J-cut/L-cut、画外音、静默、环境底床和关键 SFX。"),
    ),
    "storyboard": _prompt(
        "storyboard",
        "把已确认镜头表扩展为结构化视听分镜。",
        *_novel_contract(
            "严格按镜头表顺序生成，不增加场次；明确建立镜头、动作镜头、反应镜头、特写钩子和切镜关系，维持轴线与身份连续性。",
            "输出 Storyboard；逐镜提供构图、动作阶段、镜头运动、光线、资产引用、对白、声景和验收标准。",
        ),
        _sound_contract("每镜分离演员台词/表演、环境底床、瞬时 SFX 与非叙事性音乐；跨镜声音明确进入与退出事件。"),
    ),
    "h3_context_ir": _prompt(
        "h3_context_ir",
        "把结构化分镜与多模态素材整理为可人工确认的 H3 增强提示词。",
        *_H3_SECTIONS,
    ),
    "minimax_h3_video": _prompt(
        "minimax_h3_video",
        "向 MiniMax H3 提交经过确认的多模态视频生成意图。",
        *_H3_SECTIONS,
    ),
    "minimax_h3_regenerate_2k": _prompt(
        "minimax_h3_regenerate_2k",
        "在不改变已批准内容的前提下重生成合规 H3 768P 任务为 2K。",
        *_novel_contract(
            "只引用已成功的 H3 768P 来源任务或合规源视频；不得借重生成修改人物、剧情、对白、时长或画幅。",
            "输出必须保持源片内容、时长、比例、声音和时间关系；2K 是独立重生成任务，不伪装为直接生成参数。",
        ),
        _sound_contract("保留源片对白、声线、声景和音乐；不新增或替换声音内容。"),
    ),
    "film_qc": _prompt(
        "film_qc",
        "对生成片段执行叙事、视觉、声音、H3 能力边界与合规质检。",
        *_novel_contract(
            "以小说来源、锁定资产、镜头表和分镜为证据，检查身份、因果、时序、动作、空间、风格、字幕/品牌风险和生成缺陷。",
            "输出 QCReport；每项问题包含证据、严重度、可接受偏差、修复节点和是否阻断交付。不得把 blocked 计为完成。",
        ),
        _sound_contract("检查说话人、原文台词、口型倾向、声线身份、噪声、SFX/BGM 职责和对白可懂度；不因情绪不同误判声线漂移。"),
    ),
    "delivery": _prompt(
        "delivery",
        "按批准版本和质检结论组装可追溯交付包。",
        *_novel_contract(
            "只打包已批准的媒体、字幕、声音、清单、版本、来源谱系与质量报告；阻断项未关闭时不得标记最终完成。",
            "输出 DeliveryPackage，包含媒体清单、哈希、图版本、运行/尝试、字幕、音频声道、QC、版权与导出规格。",
        ),
        _sound_contract("导出对白、环境、SFX、音乐等可用分轨与混音版本；保留角色声线授权、来源和商业使用元数据。"),
    ),
}


def prompt_template_for(type_id: str) -> FilmNodePrompt:
    template = PROMPT_TEMPLATES.get(type_id)
    if template is None:
        return _prompt(
            type_id,
            "执行注册节点的有界任务。",
            *_novel_contract(
                "只消费已连接的上游产物并执行当前节点职责。",
                "输出必须符合注册端口的产物类型和版本。",
            ),
        )
    return template.model_copy(deep=True)


def ensure_node_prompt(node: FilmGraphNode) -> FilmGraphNode:
    if node.prompt.template_id and node.prompt.sections:
        return node
    return node.model_copy(update={"prompt": prompt_template_for(node.type_id)})


def render_node_prompt(prompt: FilmNodePrompt) -> str:
    blocks = [
        f"【{section.label}】\n{section.content.strip()}"
        for section in prompt.sections
        if section.content.strip()
    ]
    if prompt.user_notes.strip():
        blocks.append(f"【用户补充】\n{prompt.user_notes.strip()}")
    return "\n\n".join(blocks).strip()


def optimize_node_prompt(node: FilmGraphNode) -> FilmPromptOptimizationResult:
    baseline = prompt_template_for(node.type_id)
    current = {section.section_id: section for section in node.prompt.sections}
    changes: list[str] = []
    sections: list[FilmPromptSection] = []
    for template_section in baseline.sections:
        existing = current.pop(template_section.section_id, None)
        if existing is None:
            sections.append(template_section)
            changes.append(f"恢复缺失段落：{template_section.label}")
            continue
        normalized = _normalize(existing.content)
        if normalized != existing.content:
            changes.append(f"整理段落格式：{existing.label}")
        sections.append(
            existing.model_copy(
                update={
                    "label": template_section.label,
                    "guidance": template_section.guidance,
                    "content": normalized,
                    "required": template_section.required,
                    "sources": template_section.sources,
                }
            )
        )
    for section in current.values():
        sections.append(section.model_copy(update={"content": _normalize(section.content)}))
    optimized = node.prompt.model_copy(
        update={
            "template_id": baseline.template_id,
            "template_version": baseline.template_version,
            "purpose": baseline.purpose,
            "sections": sections,
            "updated_at": utc_now_iso(),
        }
    )
    rendered = render_node_prompt(optimized)
    warnings = prompt_warnings(node.model_copy(update={"prompt": optimized}), rendered=rendered)
    return FilmPromptOptimizationResult(
        prompt=optimized,
        rendered_prompt=rendered,
        changes=changes or ["提示词结构已符合当前模板，无需改写用户内容。"],
        warnings=warnings,
        character_count=len(rendered),
    )


def prompt_warnings(node: FilmGraphNode, *, rendered: str | None = None) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()
    for section in node.prompt.sections:
        if section.section_id in seen:
            warnings.append(f"提示词段落重复：{section.label}")
        seen.add(section.section_id)
        if section.required and not section.content.strip():
            warnings.append(f"必填提示词段落为空：{section.label}")
    text = rendered if rendered is not None else render_node_prompt(node.prompt)
    if node.type_id in {"h3_context_ir", "minimax_h3_video"}:
        baseline_sections = {
            section.section_id: section.content
            for section in prompt_template_for(node.type_id).sections
        }
        if len(text) > 5000:
            warnings.append("H3 提示词超过 5000 字符，请拆分镜头或压缩描述。")
        duration = int(node.config.get("duration", 10) or 10)
        timeline = next(
            (
                section.content
                for section in node.prompt.sections
                if section.section_id == "visual_timeline"
            ),
            "",
        )
        if (
            duration >= 8
            and timeline.strip() != baseline_sections.get("visual_timeline", "").strip()
            and not _TIMECODE.search(timeline)
        ):
            warnings.append("长镜头建议按秒段或 Shot 拆解画面过程，明确切镜点与对白容量。")
        mode = str(node.config.get("mode", "reference_to_video"))
        references = next(
            (
                section.content
                for section in node.prompt.sections
                if section.section_id == "reference_materials"
            ),
            "",
        )
        references_customized = (
            references.strip() != baseline_sections.get("reference_materials", "").strip()
        )
        if references_customized and mode == "text_to_video" and "@" in references:
            warnings.append("T2V 不应引用上传素材；请切换 R2V/I2V 或移除 @素材引用。")
        if (
            references_customized
            and mode in {"image_to_video", "first_last_frame", "reference_to_video"}
            and "@" not in references
        ):
            warnings.append("当前模式需要逐项声明 @图片/@视频/@音频的用途和保持特征。")
    return warnings


def _normalize(content: str) -> str:
    normalized_newlines = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE.sub(" ", line).strip() for line in normalized_newlines.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


__all__ = [
    "PROMPT_TEMPLATE_VERSION",
    "PROMPT_TEMPLATES",
    "ensure_node_prompt",
    "optimize_node_prompt",
    "prompt_template_for",
    "prompt_warnings",
    "render_node_prompt",
]

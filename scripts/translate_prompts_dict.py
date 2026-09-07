#!/usr/bin/env python3
"""Comprehensive ZH→EN translation using extensive dictionary.

This script translates all Chinese text in Jinja2 templates to English
using a comprehensive phrase dictionary. It preserves Jinja2 syntax,
variable names, and field names.
"""
from __future__ import annotations

import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "novel_forge/prompts/packs"
ZH = BASE / "zh" / "templates"
EN = BASE / "en" / "templates"

# Comprehensive translation dictionary - ordered by specificity (longest first)
# Each entry is (zh_text, en_text)
PHRASE_MAP: list[tuple[str, str]] = [
    # === META / COMMENT HEADERS ===
    ("【备注】", "[NOTE]"),
    ("作用简述：", "Purpose: "),
    ("服务步骤：", "Serving step: "),
    ("上游输入：", "Upstream inputs:"),
    ("下游输出：", "Downstream outputs:"),
    ("消费链优先级：", "Consumption chain priority:"),
    ("定义宏：", "Defined macros:"),
    ("使用方式：", "Usage:"),
    ("被调用方：", "Called by:"),
    ("文件类型：宏模块（macro module）", "File type: macro module"),
    ("文件类型：宏模块", "File type: macro module"),
    ("类型：宏模块（不被直接渲染，仅供其他模板 import）", "Type: macro module (import only, not rendered directly)"),
    ("类型：宏模块", "Type: macro module"),
    ("提示词：", "Prompt: "),

    # === SECTION HEADERS ===
    ("## 任务", "## Task"),
    ("## 规划规则", "## Planning Rules"),
    ("## 质量校准", "## Quality Calibration"),
    ("## 交付前核验", "## Pre-Delivery Checklist"),
    ("## 角色身份卡", "## Character Identity Card"),
    ("## 桥接卡", "## Bridge Card"),
    ("## 契约卡", "## Contract Card"),
    ("## 记忆薄片", "## Memory Sliver"),
    ("## 要素薄片", "## Element Sliver"),
    ("## 追读力薄片", "## Reading Power Sliver"),
    ("## 风格/节奏薄片", "## Style/Pace Sliver"),
    ("## 编辑契约卡", "## Editorial Contract Card"),
    ("## 情节线节奏卡", "## Plot Strand Pace Card"),
    ("## 认知约束卡", "## Cognitive Constraint Card"),
    ("## 支线交织卡", "## Subplot Weave Card"),
    ("## 角色知识边界", "## Character Knowledge Boundary"),
    ("## 叙事人称卡", "## Narrative Person Card"),
    ("## 时间连续性卡", "## Time Continuity Card"),
    ("## 母题避重复提示", "## Motif Repetition Avoidance"),
    ("## 表达通道冷却", "## Expression Channel Cooldown"),
    ("## 副线弧光活跃度", "## Subplot Arc Activity"),
    ("## 要素执行回补", "## Element Execution Backfill"),
    ("## 六要素文学合同卡", "## Six-Element Literary Contract Card"),
    ("## 六要素落字段规则", "## Six-Element Field Mapping Rules"),
    ("## 六要素覆盖重试指令", "## Six-Element Coverage Retry Directive"),
    ("## 上次方案失败原因", "## Previous Plan Failure Reason"),
    ("## 顶层字段要求", "## Top-Level Field Requirements"),
    ("## 章节卡", "## Chapter Card"),
    ("## Artifact 消费规则", "## Artifact Consumption Rules"),
    ("## Cast / Emotion 执行规则", "## Cast / Emotion Execution Rules"),
    ("## 跨场景约束输出", "## Cross-Scene Constraint Output"),
    ("## 字段说明", "## Field Description"),
    ("## 写出要求", "## Output Requirements"),
    ("## 角色状态", "## Character Status"),
    ("### 必要叙事要素", "### Required Narrative Elements"),
    ("### 本章重点扩展要素", "### Chapter Focus Extension Elements"),
    ("### 其余扩展要素", "### Remaining Extension Elements"),
    ("### 题材扩展要素", "### Genre Extension Elements"),
    ("## 叙事要素约束", "## Narrative Element Constraints"),
    ("## 叙事要素重点约束", "## Narrative Element Focus Constraints"),
    ("## 本轮修复策略更新", "## This Round Repair Strategy Update"),
    ("## 修复后自检清单", "## Post-Repair Self-Check"),
    ("## 共享证据锚点", "## Shared Evidence Anchor"),
    ("## 全局叙事契约", "## Global Narrative Contract"),
    ("## 节奏与风格约束", "## Pace and Style Constraints"),
    ("### 对话密度要求", "### Dialogue Density Requirements"),
    ("### 对话格式规范", "### Dialogue Format Rules"),
    ("### 说明腔禁止", "### Anti-Exposition Rules"),
    ("### 章尾与悬念洁净", "### Chapter Ending and Suspense Cleanliness"),
    ("### 创造性具体化与反机械句式", "### Creative Specificity and Anti-Mechanical Phrasing"),
    ("### 网文节奏约束", "### Pacing Rules"),
    ("### 感官多样性", "### Sensory Diversity"),
    ("### 自称语境意识", "### Self-Reference Context Awareness"),
    ("### 情感工艺", "### Emotional Craft"),
    ("### ⚠️ 意象复用禁止（★ 硬约束）", "### ⚠️ Imagery Repetition Ban (★ Hard Constraint)"),
    ("### ⚠️ 模板化短语黑名单", "### ⚠️ Template Phrase Blacklist"),
    ("### ️ 段落唯一性（★ 硬约束）", "### ⚠️ Paragraph Uniqueness (★ Hard Constraint)"),
    ("## 本模板职责范围", "## This Template Scope"),
    ("## POV 知识边界约束", "## POV Knowledge Boundary Constraints"),
    ("## Source Artifact 契约（P0）", "## Source Artifact Contract (P0)"),
    ("### chapter_contract", "### chapter_contract"),
    ("### relevant_entities", "### relevant_entities"),
    ("### forbidden_reveal_boundaries", "### forbidden_reveal_boundaries"),
    ("### 局部写作投影", "### Local Writing Projection"),
    ("### ticket/window 修复边界", "### Ticket/Window Repair Boundary"),
    ("### polish 边界", "### Polish Boundary"),
    ("### extraction 边界", "### Extraction Boundary"),
    ("## 编辑契约卡（P1）", "## Editorial Contract Card (P1)"),
    ("## LLM 已裁定叙事状态（P0 最高事实源）", "## LLM-Adjudicated Narrative State (P0 Highest Fact Source)"),
    ("## 本章叙事契约（P0 执行边界）", "## Chapter Narrative Contract (P0 Execution Boundary)"),
    ("### 实体/身份指代图（P0/P1 消歧）", "### Entity/Identity Reference Map (P0/P1 Disambiguation)"),
    ("## 角色知识边界（P0）", "## Character Knowledge Boundary (P0)"),
    ("## 角色知识边界（编辑时不得改坏）", "## Character Knowledge Boundary (Do Not Corrupt During Editing)"),

    # === COMMON LABELS ===
    ("章节：", "Chapter: "), ("标题：", "Title: "), ("目标：", "Goal: "),
    ("补充说明：", "Notes: "), ("场景：", "Setting: "),
    ("目标字数：", "Target word count: "), ("主线：", "Main plot: "),
    ("支线：", "Subplot: "), ("节拍：", "Beats: "),
    ("入口状态：", "Entry state: "), ("必达事件：", "Required events: "),
    ("必达推进：", "Required progressions: "), ("允许铺垫：", "Allowed foreshadowing: "),
    ("禁止变化：", "Forbidden changes: "), ("禁止提前推进：", "Forbidden early progressions: "),
    ("出口目标：", "Exit targets: "), ("完成标准：", "Completion criteria: "),
    ("护栏：", "Guardrails: "), ("硬事实：", "Hard facts: "),
    ("时间/地点/POV：", "Time/Location/POV: "),
    ("动作接力：", "Action handoff: "), ("情绪余波：", "Emotional carryover: "),
    ("开头验收窗口：", "Opening acceptance window: "),
    ("开头验收条件：", "Opening acceptance criteria: "),
    ("待推进悬念：", "Pending suspense: "), ("开放线索：", "Open threads: "),
    ("待解问题：", "Unresolved questions: "),
    ("本章承诺变化：", "Chapter promised changes: "), ("证据锚：", "Evidence anchor: "),
    ("未指定角色的知识承诺：", "Unbound character knowledge commitments: "),
    ("时间锚：", "Time anchor: "), ("间隔：", "Gap: "),
    ("本章跨度：", "Chapter span: "), ("倒计时状态：", "Countdown status: "),
    ("上章事件：", "Previous chapter events: "), ("相关历史：", "Relevant history: "),
    ("本章桥接禁用通道：", "Bridge forbidden channels this chapter: "),
    ("跨章高频通道：", "Cross-chapter high-frequency channels: "),
    ("记忆系统提示：", "Memory system hints: "),
    ("本章焦点：", "Chapter focus: "), ("必要要素：", "Required elements: "),
    ("本章聚焦扩展：", "Chapter focus extension: "), ("执行约束：", "Execution constraints: "),
    ("近期未命中：", "Recently missed: "), ("近期弱命中：", "Recently weak hits: "),
    ("建议优先关注：", "Suggested priority focus: "),
    ("本章微兑现：", "In-chapter micro-payoffs: "),
    ("必须回应悬念：", "Must resolve suspense: "),
    ("章尾钩子方向：", "Chapter hook direction: "),
    ("高光模式：", "Highlight mode: "),
    ("距离主高潮：", "Distance from main climax: "),
    ("余波预算：", "Aftermath budget: "),
    ("主题呈现：", "Theme presentation: "),
    ("象征物策略：", "Symbol strategy: "),
    ("场景阻力候选：", "Scene resistance candidates: "),
    ("角色声纹目标：", "Character voice targets: "),
    ("揭示阶梯：", "Revelation ladder: "),
    ("叙述要素编辑指令：", "Narrative element editorial directives: "),
    ("时间桥规则：", "Time bridge rules: "),
    ("本章活跃支线：", "Active subplots this chapter: "),
    ("交织/收束提示：", "Weave/resolution hints: "),
    ("依赖警告：", "Dependency warnings: "),
    ("核心主题：", "Core themes: "), ("角色弧光：", "Character arcs: "),
    ("关键转折：", "Key turning points: "),
    ("场景切换：", "Scene transitions: "), ("桥接落地：", "Bridge landing: "),
    ("时间合法性：", "Time legitimacy: "), ("POV可见性：", "POV visibility: "),
    ("禁复用规则：", "Repetition ban: "),
    ("悬念留存：", "Suspense retention: "),
    ("节奏模式：", "Pace mode: "), ("环境描写占比：", "Environment description ratio: "),
    ("情绪风格：", "Emotional style: "), ("信息密度：", "Information density: "),
    ("项目级禁用短语：", "Project-level banned phrases: "),
    ("对话占比遵守风格规范：", "Dialogue ratio per style spec: "),
    ("确认型台词禁/限复用：", "Confirmation-type dialogue ban/limit reuse: "),
    ("节奏：", "Pace: "),
    ("预期张力=", "Expected tension="),
    ("本章章尾优先使用：", "Chapter ending hook priority: "),

    # === STATUS / VALUE LABELS ===
    ("已死亡", "deceased"), ("存活", "alive"),
    ("未知", "unknown"), ("无特殊已知事实", "no specific known facts"),
    ("当前 POV", "current POV"), ("当前/候选 POV", "current/candidate POV"),
    ("章未出场", "chapters absent"),
    ("暂无已追踪的角色状态", "No tracked character status available"),

    # === COMMON INSTRUCTION FRAGMENTS ===
    ("本章已超过主高潮预计余波：", "This chapter exceeds the main climax aftermath budget: "),
    ("必须写出新行动、新冲突或新信息，不得重复圆满确认。", "Must introduce new actions, conflicts, or information; do not repeat confirmation scenes."),
    ("删减重复确认，保留新行动、新冲突或新信息。", "Cut repetitive confirmations; keep new actions, conflicts, or information."),
    ("余波/尾声：补过渡时不得额外添加纯确认段。", "Aftermath/denouement: do not add pure-confirmation paragraphs when filling transitions."),
    ("精修时不得新增纯确认段。", "Do not add pure-confirmation paragraphs during polish."),
    ("高情绪场景身体信号预算：每场最多", "High-emotion scene body signal budget: max"),
    ("编辑准入风险", "Editorial gate risk"),
    ("余波/尾声：", "Aftermath/denouement: "),
    ("起草动作", "drafting action"), ("修订动作", "revision action"),
    ("编织动作", "weaving action"), ("精修动作", "polish action"),
    ("对白声纹", "Dialogue voice"), ("声纹修订目标", "Voice revision target"),
    ("声纹参考", "Voice reference"),
    ("解释倾向", "explanation bias"), ("情绪句法", "emotion syntax"),
    ("禁用口吻：", "Banned tones: "),
    ("标志动作/句法：", "Signature moves/syntax: "),
    ("禁用", "banned"),
    ("声纹样例（只作节奏参考，禁止原句复用）：", "Voice samples (rhythm reference only, verbatim reuse prohibited): "),

    # === QUALITY CALIBRATION ===
    ("工作身份：", "Role: "), ("修复路径：", "Repair approach: "),
    ("审校路径：", "Audit approach: "), ("推导路径：", "Derivation approach: "),
    ("抽取路径：", "Extraction approach: "),
    ("字段级质量示例", "Field-level quality examples"),
    ("非输出格式", "not output format"),
    ("可操作路径：", "Actionable path: "),
    ("✅ 负责：", "✅ Responsible for: "),
    ("❌ 不负责：", "❌ Not responsible for: "),
    ("平弱=", "Weak="), ("高质量=", "High-quality="),
    ("你是一位谨慎的小说修复编辑", "You are a careful novel repair editor"),
    ("你是一位严谨的小说质量审校员", "You are a rigorous novel quality auditor"),
    ("你是一位项目特异性写作顾问", "You are a project-specific writing consultant"),
    ("你是一位证据优先的叙事事实抽取员", "You are an evidence-first narrative fact extractor"),
    ("你是一位契约感很强的小说执行编辑", "You are a contract-aware novel execution editor"),

    # === NARRATIVE CONTRACT ===
    ("— 规划时必须遵守", " — must be followed during planning"),
    ("— 所有章节桥接必须遵守", " — must be followed for all chapter bridges"),
    ("（★ 硬约束", "(★ Hard constraint"),
    ("世界规则：", "World rules: "), ("基调约束：", "Tone constraint: "),
    ("时代语境/礼制约束", "Era context/etiquette constraints"),
    ("社会等级：", "Social hierarchy: "), ("称谓规则：", "Address rules: "),
    ("自称规则：", "Self-reference rules: "),
    ("礼制/行为边界：", "Etiquette/behavior boundaries: "),
    ("制度术语：", "Institutional terms: "), ("时代器物：", "Era material culture: "),
    ("时代错位禁用：", "Anachronism blacklist: "), ("对白语体：", "Dialogue register: "),
    ("结局走向", "Ending direction"),
    ("连贯性协议", "Continuity protocol"), ("初始化硬规则", "Initialization hard rules"),
    ("信息密度预算", "Information density budget"),
    ("按项目时代背景自然选择时间单位", "choose time units naturally per project era"),
    ("限知视角仅描写可观察事实。", "Limited POV only describes observable facts."),
    ("禁用元素不得近义复现。", "Forbidden elements must not reappear via synonyms."),

    # === QUALITY STANDARDS ===
    ("禁止在正文中出现任何规划层标记", "Strictly prohibit planning-layer markers in prose"),
    ("这些都是规划层概念，不能出现在小说正文中", "These are planning-layer concepts and must not appear in novel prose"),
    ("禁止使用说明腔开头", "Prohibit exposition-style openings"),
    ("不输出任何规划标签", "Do not output planning labels"),
    ("开篇必须让读者自然感到", "The opening must let readers naturally feel"),
    ("叙述者告知结果", "narrator tells the result"),
    ("读者从行动推断发现", "reader infers discovery from action"),
    ("禁止用", "Prohibit using"),
    ("等元语言替代叙事", "etc. meta-language as narrative substitute"),
    ("悬念用动作、异象、对白留白或新阻力呈现", "Present suspense through action, anomalies, dialogue pauses, or new resistance"),
    ("具体化落到角色选择、动作后果、对白潜台词、环境阻力或场面调度", "Specificity lands on character choices, action consequences, dialogue subtext, environmental resistance, or blocking"),
    ("反差出情感", "Contrast creates emotion"),
    ("留白即表达", "Silence is expression"),
    ("情绪不叠加同构", "Emotions do not stack isomorphically"),
    ("感官只服务冲突、动作、转场和情绪判断", "Senses only serve conflict, action, transitions, and emotional judgment"),
    ("代词混用是严重错误，必须避免", "Pronoun mixing is a serious error and must be avoided"),
    ("避免原样或近似复用", "avoid verbatim or near-verbatim reuse"),
    ("本章全文禁止使用任何近似表达", "this chapter must not use any similar expressions"),
    ("自检规则", "Self-check rule"),
    ("禁止连续两段或间隔一段后使用相同/近似的核心句子", "Prohibit identical or near-identical core sentences in consecutive or alternating paragraphs"),

    # === POV KNOWLEDGE ===
    ("该角色不可能知道以下类别的信息", "This character cannot know the following categories of information"),
    ("该角色的感知限制", "This character's sensory limits"),
    ("违反此约束将导致角色获取全知信息", "Violating this constraint gives the character omniscient information"),

    # === SHARED ANCHOR ===
    ("请以此作为", "Please use this as"),
    ("的共同依据；维度特需信息只能补充，不得覆盖锚点事实", "'s common basis; dimension-specific info may only supplement, not override anchor facts"),

    # === WORD CONSTRAINT ===
    ("编辑完成后自检字数", "After editing, self-check word count"),
    ("本章目标", "Chapter target"),

    # === PLAN CHAPTER SPECIFIC ===
    ("为第", "Generate executable ChapterPlan for chapter"),
    ("章生成可执行", ""),
    ("写第", "Write chapter"),
    ("》正文。", " prose."),
    ("章节位置：", "Chapter position: "),
    ("本章不是全书终章", "This chapter is not the book's final chapter"),
    ("本章是当前 outline 的全书终章", "This chapter is the current outline's book final chapter"),
    ("本章曾是旧终章但已被延长", "This chapter was formerly the final chapter but has been extended"),

    # === INIT TEMPLATES ===
    ("作品信息", "Work Information"),
    ("角色信息", "Character Information"),
    ("世界观设定", "Worldview Setting"),

    # === TTS / DUBBING ===
    ("音效描述", "Sound effect description"),
    ("音效名称", "Sound effect name"),
    ("音色类型描述", "Voice type description"),
    ("语气提示", "Tone hint"),
    ("情绪氛围", "Emotional mood"),
    ("场景上下文", "Scene context"),
    ("旁白声音画像", "Narrator voice profile"),
    ("文本内容", "Text content"),
    ("曲目描述", "Track description"),
    ("重音词", "Stress word"),
    ("关键词", "Keyword"),
    ("角色名称", "Character name"),
    ("场景简述", "Scene brief"),
    ("设计说明", "Design notes"),
    ("次情绪", "Sub-emotion"),
    ("前场景", "Previous scene"),
    ("后场景", "Next scene"),

    # === GENERIC PROSE FRAGMENTS ===
    ("只作事实/母题参考", "fact/motif reference only"),
    ("规划参考，非已发生历史", "planning reference, not established history"),
    ("仅供连续性识别，不要求主动使用", "for continuity identification only, not required to actively use"),
    ("软约束", "soft constraint"),
    ("这些条目只用于减少修辞通道重复", "These entries only reduce rhetorical channel repetition"),
    ("以下弧光存在停滞风险", "The following arcs risk stagnation"),
    ("只用于规划", "for planning only"),
    ("不强制", "not enforced"),

    # === PUNCTUATION / SYMBOLS (applied last, carefully) ===
    ("；", "; "), ("、", ", "),
    ("（", "("), ("）", ")"),
    ("【", "["), ("】", "]"),
    ("「", '"'), ("」", '"'),
    ("『", "'"), ("』", "'"),
    ("——", "—"), ("……", "..."),
    ("，", ", "), ("。", ". "),
    ("！", "!"), ("？", "?"),
    ("：", ": "),
]


def translate_line(line: str) -> str:
    """Apply phrase-map translation to a single line."""
    result = line
    for zh, en in PHRASE_MAP:
        if zh in result:
            result = result.replace(zh, en)
    return result


def has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def translate_file(zh_path: Path, en_path: Path) -> None:
    """Translate a single ZH template to EN."""
    content = zh_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    translated_lines = [translate_line(line) for line in lines]
    en_path.parent.mkdir(parents=True, exist_ok=True)
    en_path.write_text("\n".join(translated_lines), encoding="utf-8")


def main() -> None:
    zh_files = sorted(ZH.rglob("*.j2"))
    translated = 0
    for zh_path in zh_files:
        rel = zh_path.relative_to(ZH)
        en_path = EN / rel
        translate_file(zh_path, en_path)
        translated += 1
        # Check remaining Chinese
        content = en_path.read_text(encoding="utf-8")
        remaining = sum(1 for line in content.split("\n") if has_chinese(line))
        if remaining > 0:
            print(f"  ⚠ {rel}: {remaining} Chinese lines remain")
        else:
            print(f"  ✓ {rel}")
    print(f"\nTranslated {translated} files")

    # Summary
    total_remaining = 0
    for en_path in sorted(EN.rglob("*.j2")):
        content = en_path.read_text(encoding="utf-8")
        total_remaining += sum(1 for line in content.split("\n") if has_chinese(line))
    print(f"Total remaining Chinese lines: {total_remaining}")


if __name__ == "__main__":
    main()

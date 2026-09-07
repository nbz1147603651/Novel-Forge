import type {
  EngineClient,
  EngineCommandClient,
  ConfigureOllamaRuntimeCommand,
  DeleteProjectsCommand,
  DeleteProjectsResult,
  DeleteOllamaModelCommand,
  ChapterStudioView,
  ChapterCommandResult,
  CleanupTestProjectsResult,
  AcknowledgeTaskErrorsCommand,
  ClearClosedTaskErrorsCommand,
  ClearClosedTaskErrorsResult,
  ClearErrorArchiveCommand,
  ClearErrorArchiveResult,
  ClearJobHistoryResult,
  ErrorArchiveSummaryView,
  RestartLongInitResult,
  ExportAudioCommand,
  ExportAudiobookCommand,
  FilmGraphRunScope,
  FilmGraphRunView,
  FilmGraphView,
  FilmProviderCatalogView,
  FilmRunEstimateView,
  FilmStageId,
  FilmStudioView,
  DramaStudioView,
  DramaExportResult,
  ComicStudioView,
  ComicAuditResult,
  ComicExportResult,
  ComicPageView,
  InitManualRepairView,
  JobView,
  OllamaManagerView,
  OllamaRuntimeCommand,
  NarrativeToolsView,
  NarrativeCharacterMutationResult,
  HumanizeLibraryMutationResult,
  MergeHumanizePatternsCommand,
  RetireNarrativeCharacterCommand,
  RemoveNarrativeRelationshipCommand,
  RemoveHumanizePatternCommand,
  SaveNarrativeCharacterCommand,
  SaveNarrativeRelationshipCommand,
  SaveHumanizePatternCommand,
  NarrativeSubplotInput,
  NarrativeSubplotMutationResult,
  ProjectReaderView,
  PullOllamaModelCommand,
  RelationshipOverviewView,
  SaveSettingsCommand,
  SaveInitManualRepairCommand,
  SaveInitManualRepairResult,
  SaveNarrativeSubplotsCommand,
  SaveSettingsResult,
  SetHumanizePatternEnabledCommand,
  SetOllamaModelRolesCommand,
  SettingsView,
  TestModelProfileResult,
  TestProjectCleanupPreview,
  ReopenTaskErrorsCommand,
  TaskErrorResolutionResult,
  TaskStreamEvent,
  TaskStreamView,
  VoiceCommandResult,
  VoiceAudioModelCenterView,
  VoiceAudioModelOperationRequest,
  VoiceAudioModelOperationView,
  VoiceAudioRuntimeView,
  WorkflowCommandResult,
  WorkflowErrorLogEntryView,
  WorkflowAiHistoryEntry,
  WorkflowDraftView,
  WorkflowFormMode,
  WorkflowPersistenceResult,
  WorkflowPresetRecordView,
  SaveWorkflowAiHistoryCommand,
  StepArtifactFile,
  WorkflowView,
  ConvertNarrativeArcsToSubplotsCommand,
  GenerateNarrativeSubplotsCommand,
  GenerateNarrativeSubplotsResult,
  VoiceStudioView,
  WorkspaceView,
} from "@nimo/engine-contracts";

import {
  generatedSettingsFixture,
  generatedVoiceProviderCatalog,
} from "./generated-settings.fixture";
import { createMockFilmGraph, mockFilmNodeCatalog } from "./film-graph-fixture";
import pageParityFixture from "../../../../tools/ui-parity/fixtures/reader-workflow.json";

let workspace: WorkspaceView = {
  defaultProvider: "openai",
  metrics: {
    totalProjects: 2,
    totalChapters: 3,
    totalWords: 15_000,
    configuredProviders: 3,
  },
  providerGroups: [
    {
      providerId: "openai",
      providerLabel: "OpenAI",
      ready: true,
      models: [
        { modelId: "gpt-4o", displayName: "GPT-4o", health: "green", statusLabel: "已载入", supportsThinking: false, supportsMultiTurn: true },
        { modelId: "gpt-4o-mini", displayName: "GPT-4o Mini", health: "green", statusLabel: "已载入", supportsThinking: false, supportsMultiTurn: true },
      ],
    },
    {
      providerId: "deepseek",
      providerLabel: "DeepSeek",
      ready: true,
      models: [
        { modelId: "deepseek-chat", displayName: "DeepSeek Chat", health: "green", statusLabel: "已载入", supportsThinking: true, supportsMultiTurn: true },
      ],
    },
    {
      providerId: "qwen",
      providerLabel: "阿里百炼",
      ready: false,
      models: [
        { modelId: "qwen-max", displayName: "Qwen Max", health: "yellow", statusLabel: "密钥就绪 · 未载入", supportsThinking: true, supportsMultiTurn: true },
        { modelId: "qwen-turbo", displayName: "Qwen Turbo", health: "red", statusLabel: "密钥未配置", supportsThinking: false, supportsMultiTurn: true },
      ],
    },
  ],
  projects: [
    {
      id: "test-short",
      title: "测试短篇",
      mode: "short",
      initResumeAvailable: false,
      status: "completed",
      statusLabel: "已完稿",
      progressLabel: "短篇完稿",
      progressPercent: 100,
      nextAction: "查看作品",
      updatedLabel: "2026-04-30",
      headline: "一个关于遗物整理的短篇故事",
      genre: "mystery",
      tone: "suspenseful",
      completedChapters: 1,
      totalChapters: null,
    },
    {
      id: "test-long",
      title: "测试长篇",
      mode: "long",
      initResumeAvailable: false,
      status: "writing",
      statusLabel: "连载中",
      progressLabel: "第 4 章完成",
      progressPercent: 45,
      nextAction: "续写第 5 章",
      updatedLabel: "2026-04-29",
      headline: "记忆回收师的长篇故事",
      genre: "scifi",
      tone: "dark",
      completedChapters: 4,
      totalChapters: 24,
    },
  ],
};

let jobs: readonly JobView[] = [
  {
    id: "job-long-005",
    label: "续写第 5 章 · 测试长篇",
    projectId: "test-long",
    state: "running",
    currentStep: "正在编织章节波次",
    stepLabel: "章节波次 · 第 5 章 · 正在统一场景节奏",
    progressPercent: 62,
    detail: "草稿已完成，正在统一场景节奏与角色声线。",
  },
];

const settings: SettingsView = generatedSettingsFixture;

let errorArchiveSummary: ErrorArchiveSummaryView = {
  entryCount: 2,
  projectCount: 1,
  latestTime: "2026-07-14T12:35:12+00:00",
};

let workflow: WorkflowView = {
  errorCount: 1,
  errorLog: [
    {
      id: "fixture-format-error",
      timeLabel: "2026-07-14 12:34:56",
      jobLabel: "长篇立项 · 青瓦梦起",
      taskId: "INIT_STORY_BIBLE",
      taskLabel: "故事圣经",
      attemptLabel: "2/3",
      errorMessage: "JSON 输出缺少字段：themes_and_symbols",
      excerpt: "模型返回的对象未通过格式合同校验；已保存原始片段，等待下一次重试。",
      logPath: "logs/fixture/format_errors/story_bible.json",
      kindLabel: "格式错误",
      autoResolved: false,
    },
    {
      id: "fixture-auto-resolved",
      timeLabel: "2026-07-14 12:35:12",
      jobLabel: "长篇立项 · 青瓦梦起",
      taskId: "PROFILE_STYLE",
      taskLabel: "风格规范",
      attemptLabel: "1/3",
      errorMessage: "格式重试后已恢复",
      excerpt: "系统已接受后续的合规结果；本条保留为运行审计记录。",
      logPath: "",
      kindLabel: "格式错误",
      autoResolved: true,
    },
  ],
  runs: [
    {
      id: pageParityFixture.workflow.jobId,
      title: pageParityFixture.workflow.title,
      projectLabel: pageParityFixture.workflow.projectLabel,
      elapsedLabel: pageParityFixture.workflow.elapsedLabel,
      progressPercent: pageParityFixture.workflow.progressPercent,
      stateLabel: "执行中",
      currentStageLabel: pageParityFixture.workflow.currentStageLabel,
      stepLabel: "冲突候选裁判 · 当前层：章节契约 · 批次 3/7 · 判定：通过",
      validationLabel: "后台步骤：当前 一致性画像；已过 蓝图验证",
      activityLabel: pageParityFixture.workflow.activityLabel,
      kind: "init_long",
      initRepairAvailable: false,
      hasCheckpoint: false,
      // stages 是 PySide/Engine 的权威顺序；StepIndicatorRow 在运行中直接
      // 消费该序列，静态 INIT_LONG_STEPS 仅负责启动前和国际化回退。
      stages: [
        { id: "spec", label: "规格确认", state: "completed" },
        { id: "init_web_research", label: "资料检索", state: "completed" },
        { id: "init_story_bible", label: "世界观设定", state: "completed" },
        { id: "plan_blueprint_elements", label: "要素选择", state: "completed" },
        { id: "init_character_bible", label: "角色设定", state: "completed" },
        { id: "profile_style", label: "风格规范", state: "completed" },
        { id: "plan_blueprint", label: "叙事蓝图", state: "active" },
        { id: "derive_editorial_contract", label: "编辑契约", state: "pending" },
        { id: "plan_chapter_design_matrix", label: "章节设计矩阵", state: "pending" },
        { id: "plan_outline", label: "章节大纲", state: "pending" },
        { id: "init_narrative_contract", label: "叙事契约", state: "pending" },
        { id: "plan_chapter_contracts", label: "章节契约", state: "pending" },
        { id: "init_claim_contract_coverage", label: "契约覆盖", state: "pending" },
        { id: "init_readiness", label: "初始化准入", state: "pending" },
        { id: "canon_state", label: "规范初始化", state: "pending" },
      ],
    },
  ],
  focus: {
    kindLabel: "当前节点输出",
    statusLabel: "输出中",
    title: "当前节点正文",
    summary: "JSON 片段　预览中，未校验，未写入磁盘。",
    fragment: "…少需要两个不同视角的证据交叉验证。主题兑现章节必须出现可见的场景 / 行动 / 后果，不能停留在认知层面。",
  },
};

const projectReaders: Readonly<Record<string, ProjectReaderView>> = {
  "test-long": {
    projectId: "test-long",
    projectTitle: "测试长篇",
    modeLabel: "长篇项目",
    updatedLabel: "近次同步 2026-04-29",
    tabs: [
      {
        id: "foundation",
        label: "基础设定",
        artifacts: [
          {
            id: "spec",
            label: "故事规格",
            caption: pageParityFixture.project.reader.caption,
            sourceLabel: "spec.json",
            format: "json",
            content: JSON.stringify(pageParityFixture.project.spec, null, 2),
            paragraphs: pageParityFixture.project.reader.paragraphs,
            facts: pageParityFixture.project.reader.facts,
          },
          {
            id: "world",
            label: "世界观",
            caption: "记忆技术、秩序与代价",
            sourceLabel: "story_bible.json",
            paragraphs: ["记忆可被合法存档、出售与回收，但提取并不意味着拥有：每一次读取都会在原主意识中留下可追溯的空洞。", "城市的公共记忆库由一套严格的授权链维护；未经许可的私人副本，会成为调查与伦理审查的对象。"],
            facts: [{ label: "关键技术", value: "记忆存档与回收" }, { label: "制度张力", value: "授权链 / 私人副本" }, { label: "世界规则", value: "读取必须留下可追溯痕迹" }],
          },
          {
            id: "characters",
            label: "角色与实体",
            caption: "人物动力与关系锚点",
            sourceLabel: "character_bible.json",
            paragraphs: ["林逐是持证记忆回收师，习惯以流程隔离情感；姐姐失踪案件重新出现后，这种自我保护开始失效。", "调查官周砚掌握授权链漏洞的证据，他既是合作方，也是对林逐最不信任的人。"],
            facts: [{ label: "主视角", value: "林逐 · 记忆回收师" }, { label: "主要张力", value: "亲缘真相 / 职业伦理" }, { label: "关系锚点", value: "林逐 ↔ 周砚" }],
          },
          {
            id: "elements",
            label: "要素与风格",
            caption: "母题、意象与表达约束",
            sourceLabel: "plans/blueprint_elements_selection.json",
            paragraphs: ["反复出现的意象是旧磁带、雨夜高架与被擦除的语音留言。它们不是装饰，而是每次真相推进时的感官锚点。", "文风克制、近距离，避免以抽象判断代替人物可见的选择和后果。"],
            facts: [{ label: "母题", value: "记忆的所有权" }, { label: "意象", value: "旧磁带 · 雨夜高架" }, { label: "表达约束", value: "少判断，多动作" }],
          },
        ],
      },
      {
        id: "research",
        label: "资料检索",
        artifacts: [
          {
            id: "research-report",
            label: "资料检索报告",
            caption: "目标场景、制度与可验证资料",
            sourceLabel: "reports/init_web_research.json",
            paragraphs: ["档案室、公共记忆库与高架桥下的旧磁带摊构成本卷可重复出现的场景。每处资料都须能转换为角色可见的行动阻力。", "授权链、回收许可证与离线副本的制度条目已经交叉校验；正文引用时只保留与林逐当下选择有关的部分。"],
            facts: [{ label: "资料状态", value: "已校准" }, { label: "重点场景", value: "档案室 / 高架桥" }, { label: "引用边界", value: "服务人物行动" }],
          },
          {
            id: "research-dossier",
            label: "资料分析报告",
            caption: "冲突可用性与叙事风险",
            sourceLabel: "reports/init_research_dossier.json",
            paragraphs: ["资料的主要价值不在于展示技术名词，而在于让授权链的空白直接改变林逐与周砚的信任关系。", "未证实的外部传闻不得作为解决方案；它们只能推动调查、误导或增加人物选择成本。"],
            facts: [{ label: "可用冲突", value: "权限与亲缘" }, { label: "风险", value: "技术说明过量" }, { label: "处理", value: "转为场景证据" }],
          },
          {
            id: "research-grounding",
            label: "大纲资料校准",
            caption: "章节节点与资料证据的映射",
            sourceLabel: "reports/outline_research_grounding.json",
            paragraphs: ["第 5 章的档案室对照必须留出一次可验证的记录差异；第 6 章才允许追踪操作者。", "每个资料锚点都已对应到章节动作，不以背景段落独立占用叙述篇幅。"],
            facts: [{ label: "下一节点", value: "档案室对照" }, { label: "资料锚点", value: "夜间回收记录" }, { label: "状态", value: "可执行" }],
          },
        ],
      },
      {
        id: "blueprint",
        label: "叙事蓝图",
        artifacts: [
          {
            id: "narrative-blueprint",
            label: "叙事蓝图",
            caption: "冲突升级与兑现节点",
            sourceLabel: "plans/narrative_blueprint.json",
            paragraphs: ["第一卷让林逐确认记忆残片来自姐姐；第二卷把授权链漏洞从技术问题转为人际背叛；终局必须由林逐主动决定是否公开记忆。", "每一卷都要将一个先前的承诺转化为可见行动，不允许只在总结性对白中完成回收。"],
            facts: [{ label: "卷一", value: "发现残片来源" }, { label: "卷二", value: "授权链背叛" }, { label: "终局选择", value: "公开或封存记忆" }],
          },
        ],
      },
      {
        id: "chapter_design",
        label: "章节设计矩阵",
        artifacts: [
          {
            id: "chapter-design-matrix",
            label: "章节设计矩阵",
            caption: "章节契约、推进与回收位置",
            sourceLabel: "plans/chapter_design_matrix.json",
            paragraphs: ["第 5 章将时间戳作为可复查证据而非答案；周砚提出的权限疑点必须迫使林逐作出一次会留下后果的隐瞒。", "角色声线、场景压力和结尾钩子已按章节排序，当前只暴露可执行的近期范围。"],
            facts: [{ label: "当前章", value: "第 5 章" }, { label: "核心冲突", value: "证据 / 信任" }, { label: "结尾钩子", value: "记录被人为隐藏" }],
          },
        ],
      },
      {
        id: "outline",
        label: "章节大纲",
        artifacts: [
          {
            id: "outline",
            label: "章节大纲",
            caption: "当前章节的因果与伏笔",
            sourceLabel: "outline.json",
            paragraphs: ["第 4 章完成后，林逐拿到一段未经登记的夜间回收记录；第 5 章应让她在档案室对照时间戳，并第一次与周砚正面冲突。", "本章结束时只确认记录被人为隐藏，暂不揭示操作者身份，为第 6 章的跟踪行动留下空间。"],
            facts: [{ label: "已归档", value: "第 4 章" }, { label: "下一章", value: "档案室对照时间戳" }, { label: "待兑现伏笔", value: "夜间回收记录" }],
          },
        ],
      },
      {
        id: "chapters",
        label: "章节",
        chapters: [
          { number: 4, title: "高架桥下", state: "final" },
          { number: 5, title: "档案室对照", state: "draft" },
          { number: 6, title: "权限追踪", state: "pending" },
        ],
        artifacts: [
          {
            id: "chapter-4",
            label: "第 4 章正文",
            caption: "已归档正文 · 4,216 字",
            sourceLabel: "chapters/chapter_4.md",
            content: "雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。\n她并无立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，像有人在这座城里替她保留了一次迟到的呼吸。",
            paragraphs: ["雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。", "她没有立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，像有人在这座城里替她保留了一次迟到的呼吸。"],
            facts: [{ label: "归档状态", value: "已完成" }, { label: "正文长度", value: "4,216 字" }, { label: "质量门禁", value: "通过" }],
          },
          {
            id: "chapter-4-draft-v0_draft",
            label: "DRAFT 原稿",
            caption: "DRAFT 原稿 · 3,840 字",
            sourceLabel: "drafts/chapter_004/v0_draft.md",
            content: "雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。\n她没有立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，像有人在这座城里替她保留了一次迟到的呼吸。",
            paragraphs: ["雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。", "她没有立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，像有人在这座城里替她保留了一次迟到的呼吸。"],
            facts: [{ label: "正文长度", value: "3,840 字" }],
          },
          {
            id: "chapter-4-draft-v1_wave",
            label: "初稿成章",
            caption: "初稿成章 · 4,216 字",
            sourceLabel: "drafts/chapter_004/v1_wave.md",
            content: "雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。\n她并无立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，像有人在这座城里替她保留了一次迟到的呼吸。",
            paragraphs: ["雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。", "她并无立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，像有人在这座城里替她保留了一次迟到的呼吸。"],
            facts: [{ label: "正文长度", value: "4,216 字" }],
          },
          {
            id: "chapter-5-draft-v_humanize_candidate",
            label: "章节草稿（v_humanize_candidate）",
            caption: "拟人化候选稿 · 只读",
            sourceLabel: "drafts/chapter_005/v_humanize_candidate.md",
            content: "档案室的雨声比桥下更轻。林逐将两份时间戳并排放好，等待那七分钟的缺口自己开口。",
            paragraphs: ["档案室的雨声比桥下更轻。林逐将两份时间戳并排放好，等待那七分钟的缺口自己开口。"],
            facts: [{ label: "版本", value: "v_humanize_candidate" }],
          },
          {
            id: "chapter-4-report-evaluation",
            label: "质量评估",
            caption: "第 4 章的综合质量结论",
            sourceLabel: "reports/chapter_004_eval.json",
            paragraphs: ["章节已保持主线推进：时间戳出现、林逐选择隐瞒、与周砚的信任裂缝被明确。", "下一章需避免立即解释时间戳来源；先让档案室场景带来一条可证实又不完整的证据。"],
            facts: [{ label: "综合评分", value: "8.3 / 10" }, { label: "一致性", value: "已通过" }, { label: "建议", value: "延迟身份揭示" }],
          },
          {
            id: "chapter-4-report-quality-gate",
            label: "质量门禁",
            caption: "终稿进入归档前的质量检查",
            sourceLabel: "reports/chapter_004_quality_gate.json",
            paragraphs: ["字数、承接、人物行动与场景落点均满足第 4 章的归档门槛。", "保留第 5 章的验证点，不将未证实的操作者身份提前写入正文。"],
            facts: [{ label: "门禁", value: "通过" }, { label: "风险", value: "无高风险" }, { label: "复查", value: "第 5 章" }],
          },
          {
            id: "chapter-4-report-guard",
            label: "护栏",
            caption: "规则、知识边界与禁区检查",
            sourceLabel: "reports/chapter_004_guard.json",
            paragraphs: ["章节没有越过授权链的既定代价，也没有让任何角色获得超出当前证据的知识。"],
            facts: [{ label: "越界", value: "0" }, { label: "规则", value: "通过" }, { label: "保留", value: "操作者身份" }],
          },
          {
            id: "chapter-4-report-knowledge-boundary",
            label: "知识边界",
            caption: "角色知情范围验证",
            sourceLabel: "reports/chapter_004_knowledge_boundary_verification.json",
            paragraphs: ["林逐只掌握时间戳异常，周砚只提供权限线索；两人的信息差仍可在档案室场景里继续施压。"],
            facts: [{ label: "林逐", value: "异常时间戳" }, { label: "周砚", value: "权限线索" }, { label: "边界", value: "清晰" }],
          },
          {
            id: "chapter-4-report-alignment",
            label: "对齐报告",
            caption: "章节合同、计划与正文的对齐结果",
            sourceLabel: "reports/chapter_004_alignment.json",
            paragraphs: ["第 4 章完成了夜间记录的可见发现，并将隐瞒行为落实为下一章的关系压力。"],
            facts: [{ label: "章节合同", value: "通过" }, { label: "主线", value: "推进" }, { label: "伏笔", value: "已携带" }],
          },
          {
            id: "chapter-4-report-continuity",
            label: "连贯性",
            caption: "角色、物件与时间线连续性",
            sourceLabel: "reports/chapter_004_continuity.json",
            paragraphs: ["读卡器、纸质本和夜间时间戳都从前章承接，人物行动没有跳过已有的门禁限制。"],
            facts: [{ label: "连续性", value: "9.0 / 10" }, { label: "状态", value: "通过" }, { label: "锚点", value: "纸质本" }],
          },
          {
            id: "chapter-4-report-causal",
            label: "因果链",
            caption: "行动、证据与后果的可追溯性",
            sourceLabel: "reports/chapter_004_causal.json",
            paragraphs: ["时间戳触发林逐的隐瞒；隐瞒改变她与周砚的合作条件，并把档案室对照设为下一步的必要行动。"],
            facts: [{ label: "因果", value: "8.4 / 10" }, { label: "缺口", value: "无" }, { label: "下一因", value: "档案室" }],
          },
          {
            id: "chapter-4-report-reading-power",
            label: "追读力",
            caption: "章尾钩子与未解压力",
            sourceLabel: "reports/chapter_004_reading_power.json",
            paragraphs: ["结尾将“晚了七分钟”的时间戳保留为未解证据，让读者与林逐一起进入下一章的核对行动。"],
            facts: [{ label: "追读力", value: "8.5 / 10" }, { label: "钩子", value: "时间戳" }, { label: "延迟揭示", value: "有效" }],
          },
          {
            id: "chapter-4-report-control",
            label: "控制",
            caption: "阶段可见性与流程控制记录",
            sourceLabel: "reports/chapter_004_stage_visibility.json",
            paragraphs: ["归档后仅暴露第 5 章所需的近端线索，后续卷的解决方案仍保持在运行时边界之外。"],
            facts: [{ label: "可见范围", value: "近端" }, { label: "状态", value: "归档" }, { label: "泄露", value: "0" }],
          },
          {
            id: "chapter-4-report-expression",
            label: "表达",
            caption: "重复表达与句式观察",
            sourceLabel: "reports/chapter_004_expression_repetition.json",
            paragraphs: ["雨声与磁带意象保持为动作锚点，没有在同一段落堆叠解释性修辞。"],
            facts: [{ label: "重复", value: "低" }, { label: "动作锚点", value: "雨声 / 磁带" }, { label: "建议", value: "保持克制" }],
          },
          {
            id: "chapter-4-report-humanize",
            label: "拟人化",
            caption: "AI 痕迹清理建议",
            sourceLabel: "reports/revisions/chapter_004_humanize_scan_with_paragraph_evidence.json",
            paragraphs: ["没有发现需要阻断的模板式结论；局部解释性语句已改为人物看见、抄写和隐瞒的动作。"],
            facts: [{ label: "风险", value: "低" }, { label: "处理", value: "已清理" }, { label: "保留", value: "近距离叙述" }],
            format: "json",
            content: JSON.stringify({
              chapter_number: 4,
              source_text_hash: "6f3059c35445d9d3b0b8eb8f96e60e1b042cb51d65fe1db0762e90df8ab1dbce8",
              summary: "分批候选裁判完成，保留 3 条真实命中。建议先核对原文证据，再决定是否接受局部修复。",
              total_hits: 3,
              critical_hits: 0,
              patchable_hits: 2,
              unpatchable_hits: 1,
              humanize_score: 4.5,
              hits_by_category: { "标点习惯": 1, "抽象判断": 2 },
              pattern_hits: [
                { pattern_id: "significance_inflation", pattern_name: "意义抬高", category: "抽象判断", severity: "high", evidence_quote: "这一刻的选择，注定将改变他们所有人的命运。", paragraph_index: 5, suggestion: "他把时间戳抄进笔记，没有抬头。", actionable: true, confidence: 0.94, source: "merged" },
                { pattern_id: "abstract_emotion", pattern_name: "抽象情绪说明", category: "抽象判断", severity: "medium", evidence_quote: "林逐感到一种难以言说的压迫感。", paragraph_index: 2, suggestion: "与前后动作一起复核；没有唯一局部替换时不应自动改写。", actionable: false, confidence: 0.78, source: "llm" },
                { pattern_id: "em_dash_overuse", pattern_name: "破折号滥用", category: "标点习惯", severity: "low", evidence_quote: "他终于看清了——那是一个被改写过的时间戳。", paragraph_index: 8, suggestion: "他终于看清了：那是一个被改写过的时间戳。", actionable: true, confidence: 0.9, source: "local" },
              ],
            }),
          },
          {
            id: "chapter-4-report-humanize-layer",
            label: "拟人化对比",
            caption: "清理前后的可审阅差异",
            sourceLabel: "reports/revisions/chapter_004_humanize_layer.json",
            paragraphs: ["差异仅压缩了抽象判断，未替换角色决定与关键物件的叙事功能。"],
            facts: [{ label: "变更", value: "局部" }, { label: "回滚锚点", value: "v4" }, { label: "复核", value: "通过" }],
            format: "json",
            content: JSON.stringify({
              artifact_type: "text_revision_diff",
              source: "humanize_layer",
              chapter_number: 4,
              label_before: "原文",
              label_after: "拟人化层输出",
              status: "accepted",
              patches_applied: 2,
              additions: 13,
              deletions: 13,
              total_changes: 26,
              similarity_ratio: 0.953,
              change_ratio: 0.047,
              hunks: [
                { tag: "equal", a_text: "沈岸潮气的指尖扣住醒梦事务所掉漆的木门把手，指缝蹭着一点青灰色的瓦屑。", b_text: "沈岸潮气的指尖扣住醒梦事务所掉漆的木门把手，指缝蹭着一点青灰色的瓦屑。" },
                { tag: "replace", a_text: "门内是一间不大的接待厅，放着旧藤椅、老木桌、墙边立着一排玻璃罐，罐身上刻着不同的姓氏。空气里弥漫着淡淡的樟脑味和旧纸味。", b_text: "门内是一间不大的接待厅，摆着旧藤椅、老木桌、墙边立着一排玻璃罐，罐身上刻着不同的姓氏。空气里弥漫着淡淡的樟脑味和旧纸味。" },
                { tag: "replace", a_text: "林小满坐在最里面，神情显得很平静。", b_text: "林小满坐在最里面，指尖慢慢压平桌角翘起的纸页，抬眼时才看见他。" },
                { tag: "equal", a_text: "雨水沿着窗沿滑下来，屋里只剩挂钟走针的声音。沈岸没有立刻开口。", b_text: "雨水沿着窗沿滑下来，屋里只剩挂钟走针的声音。沈岸没有立刻开口。" },
              ],
              unified_diff: "",
              metadata: { change_ratio_cap: 0.08 },
            }),
          },
          {
            id: "chapter-4-report-polish",
            label: "润色对比",
            caption: "润色层差异与版本说明",
            sourceLabel: "reports/revisions/chapter_004_polish_chapter.json",
            paragraphs: ["润色提升了场景动作的可见性，同时保持时间戳作为证据而非解释答案。"],
            facts: [{ label: "版本", value: "终稿" }, { label: "影响", value: "表达" }, { label: "结果", value: "通过" }],
          },
          {
            id: "chapter-4-report-creative",
            label: "创作总结",
            caption: "本章的创作意图与后续提醒",
            sourceLabel: "reports/chapter_004_creative.json",
            paragraphs: ["本章让证据先改变林逐的选择，再把解释留给下一章。档案室不应立刻给出操作者身份。"],
            facts: [{ label: "意图", value: "证据改变选择" }, { label: "下一章", value: "核对记录" }, { label: "禁区", value: "过早揭示" }],
          },
        ],
      },
      {
        id: "governance",
        label: "治理",
        artifacts: [
          {
            id: "book-consistency-audit",
            label: "一致性审计",
            caption: "全书状态、矛盾与修复建议",
            sourceLabel: "reports/book_consistency_audit.json",
            paragraphs: ["第 1 至 4 章的记忆授权规则保持一致；当前仍需在第 5 章验证周砚是否知道夜间回收记录的来源。", "高严重度冲突为零，剩余建议均为后续章节的验证点，不自动改写已归档正文。"],
            facts: [{ label: "审计结果", value: "通过" }, { label: "高严重度", value: "0" }, { label: "待验证", value: "周砚的知情范围" }],
          },
          {
            id: "book-consistency-repair",
            label: "修复报告",
            caption: "已执行修复与保留回滚点",
            sourceLabel: "reports/book_consistency_repair_report.json",
            paragraphs: ["本轮没有执行全文改写；所有轻微提示保持为可审阅建议，避免为统一术语而抹平人物语气。", "如后续修复影响一致性分数，将回退到最近一次已验证的章节版本。"],
            facts: [{ label: "修复轮次", value: "0" }, { label: "回滚锚点", value: "第 4 章终稿" }, { label: "状态", value: "无需处理" }],
          },
          {
            id: "init-readiness",
            label: "初始化准入",
            caption: "立项输入完整性与可执行性",
            sourceLabel: "reports/init_readiness.json",
            paragraphs: ["故事规格、人物关系、世界规则和章节合同均已通过准入检查。", "该报告只保留立项时的审计结论，不覆盖当前章节的运行判断。"],
            facts: [{ label: "准入", value: "通过" }, { label: "合同", value: "已生成" }, { label: "审计", value: "已归档" }],
          },
          {
            id: "init-editorial-readiness",
            label: "编辑契约准入",
            caption: "编辑契约、章节边界与审阅入口",
            sourceLabel: "reports/init_editorial_readiness.json",
            paragraphs: ["编辑契约为每个章节保留了可验证的冲突、人物选择与收束边界；它不替代正文，只约束可执行范围。"],
            facts: [{ label: "契约", value: "已生成" }, { label: "章节", value: "24" }, { label: "边界", value: "可审阅" }],
          },
          {
            id: "init-artifact-repair",
            label: "初始化修复",
            caption: "立项产物的修复记录与回滚信息",
            sourceLabel: "reports/init_artifact_repair.json",
            paragraphs: ["初始化阶段没有遗留阻断性修复项；轻微术语统一均保留了原始产物与可追溯差异。"],
            facts: [{ label: "阻断项", value: "0" }, { label: "回滚", value: "可用" }, { label: "状态", value: "完成" }],
          },
          {
            id: "init-creative-refinement",
            label: "创意精炼",
            caption: "核心前提、母题与风格的精炼结论",
            sourceLabel: "reports/init_creative_refinement.json",
            paragraphs: ["“记忆的所有权”已被落实为授权链中的选择成本，避免只作为背景设定或主题说明出现。"],
            facts: [{ label: "母题", value: "记忆的所有权" }, { label: "表达", value: "行动兑现" }, { label: "风格", value: "克制" }],
          },
          {
            id: "init-coherence-profile",
            label: "一致性画像",
            caption: "立项产物的一致性基线",
            sourceLabel: "reports/init_coherence_profile.json",
            paragraphs: ["人物目标、世界规则和卷级冲突已形成同一条因果链；后续章节只需继承相关的近端投影。"],
            facts: [{ label: "基线", value: "建立" }, { label: "冲突", value: "0" }, { label: "投影", value: "近端" }],
          },
          {
            id: "init-coherence-claims",
            label: "一致性 Claims",
            caption: "关键事实与证据链声明",
            sourceLabel: "memory/init_coherence_claims.jsonl",
            paragraphs: ["每一项关键事实都保留来源与适用范围；未经裁决的候选事实不得被当作已知真相写入正文。"],
            facts: [{ label: "声明", value: "48" }, { label: "证据", value: "完整" }, { label: "裁决", value: "可追溯" }],
          },
          {
            id: "init-coherence-theme-structure",
            label: "主题与结构",
            caption: "主题兑现节点与结构支撑",
            sourceLabel: "reports/init_theme_structure_coherence.json",
            paragraphs: ["主题必须由授权链中的选择与后果来兑现，卷级结构已为三次递进留出动作位置。"],
            facts: [{ label: "兑现点", value: "3" }, { label: "结构", value: "递进" }, { label: "状态", value: "通过" }],
          },
          {
            id: "init-coherence-character-theme",
            label: "角色主题",
            caption: "人物弧光与主题关系",
            sourceLabel: "reports/init_character_theme_coherence.json",
            paragraphs: ["林逐的选择承载记忆所有权的代价，周砚的权限线索负责把制度冲突转为具体人际压力。"],
            facts: [{ label: "主视角", value: "林逐" }, { label: "对照", value: "周砚" }, { label: "主题", value: "选择成本" }],
          },
          {
            id: "init-coherence-world-theme",
            label: "世界观主题",
            caption: "世界规则与主题边界",
            sourceLabel: "reports/init_world_theme_coherence.json",
            paragraphs: ["授权链既是保护原主的制度，也是可能被伪造为官方真相的系统性压力；规则不允许被无代价绕过。"],
            facts: [{ label: "规则", value: "可追溯" }, { label: "代价", value: "不可绕过" }, { label: "风险", value: "伪造" }],
          },
        ],
      },
      {
        id: "tracking",
        label: "追踪",
        artifacts: [
          {
            id: "relationship",
            label: "关系追踪",
            caption: "角色关系与变化依据",
            sourceLabel: "story_kernel/relationships.sqlite",
            paragraphs: ["林逐与周砚当前处于“合作但不互信”状态。第 4 章的隐瞒使信任值下调，但周砚提供的授权链线索仍保留合作必要性。", "关系变化必须在后续章节以对话选择、协同行动或相互隐瞒体现，不能只写入后台状态。"],
            facts: [{ label: "关系", value: "合作但不互信" }, { label: "最近变化", value: "林逐隐瞒时间戳" }, { label: "下次验证", value: "第 5 章档案室" }],
          },
          {
            id: "token-analytics",
            label: "Token 追踪",
            caption: "本项目调用与预算摘要",
            sourceLabel: "logs/token_analytics.json",
            format: "json",
            content: JSON.stringify({
              total_tokens: 703400,
              total_prompt_tokens: 511200,
              total_completion_tokens: 192200,
              total_call_count: 42,
              logged_cost_usd: 1.06,
              run_count: 6,
              models: [{ key: "openai/gpt-4o-mini", display_name: "OpenAI · GPT-4o mini", calls: 42, tokens: 703400, prompt_tokens: 511200, completion_tokens: 192200, cost_usd: 1.06 }],
              steps: [
                { step: "章节草稿", runs: 3, calls: 18, tokens: 302800, prompt_tokens: 211900, completion_tokens: 90900, cost_usd: 0.42, kind_tokens: { init: 0, chapter: 302800, repair: 0 } },
                { step: "波次编织", runs: 2, calls: 14, tokens: 247900, prompt_tokens: 185600, completion_tokens: 62300, cost_usd: 0.38, kind_tokens: { init: 0, chapter: 247900, repair: 0 } },
                { step: "一致性检查", runs: 1, calls: 10, tokens: 152700, prompt_tokens: 113700, completion_tokens: 39000, cost_usd: 0.26, kind_tokens: { init: 0, chapter: 0, repair: 152700 } },
              ],
            }),
            paragraphs: ["当前项目累计消耗 703.4k tokens；本章主要消耗位于草稿、波次编织和一致性检查。", "预算数据仅用于创作调度，不会出现在导出的小说正文中。"],
            facts: [{ label: "累计用量", value: "703.4k tokens" }, { label: "本章估算", value: "$1.06" }, { label: "高消耗阶段", value: "波次编织" }],
          },
        ],
      },
    ],
  },
  "test-short": {
    projectId: "test-short",
    projectTitle: "测试短篇",
    modeLabel: "短篇项目",
    updatedLabel: "近次同步 2026-04-30",
    tabs: [
      {
        id: "short-story",
        label: "正文",
        artifacts: [
          {
            id: "short-prose",
            label: "短篇正文",
            caption: "已完成 · 8,904 字",
            sourceLabel: "chapters/short_story.md",
            paragraphs: ["外婆留下的盒子里没有珠宝，只有一串被反复倒带的录音。许知在最后一段杂音里听见了自己的名字。", "她合上盒盖时，雨停了。窗外那条旧巷仍在，像从来没有要求谁把过去归还。"],
            facts: [{ label: "完成状态", value: "已完稿" }, { label: "正文长度", value: "8,904 字" }, { label: "导出", value: "Markdown · DOCX" }],
          },
        ],
      },
      {
        id: "short-foundation",
        label: "基础设定",
        artifacts: [
          {
            id: "short-spec",
            label: "故事规格",
            caption: "人物、事件与情绪曲线",
            sourceLabel: "spec.json",
            paragraphs: ["遗物整理员许知在外婆的录音中听见自己被隐瞒的身世线索。故事在一天内完成，从整理到播放，再到她决定保留这段不完整的真相。"],
            facts: [{ label: "类型", value: "现实悬疑短篇" }, { label: "时间跨度", value: "一天" }, { label: "核心动作", value: "播放最后一段录音" }],
          },
          {
            id: "short-beats",
            label: "节拍结构",
            caption: "开端、转折与余韵",
            sourceLabel: "beats.json",
            paragraphs: ["盒子出现 → 录音失真 → 名字被听见 → 许知决定不追问所有答案。结尾保留旧巷与雨停的意象，落在她对过去的主动选择。"],
            facts: [{ label: "开端", value: "整理遗物" }, { label: "转折", value: "录音出现姓名" }, { label: "余韵", value: "主动保留不完整" }],
          },
        ],
      },
      {
        id: "short-report",
        label: "报告",
        artifacts: [
          {
            id: "short-evaluation",
            label: "评估报告",
            caption: "质量门禁与修改记录",
            sourceLabel: "reports/eval_report.json",
            paragraphs: ["故事完成度、人物行动和意象回收均通过质量门禁。建议在后续修订中保持录音物件的物理细节，不补写解释性背景。"],
            facts: [{ label: "综合评分", value: "8.7 / 10" }, { label: "质量门禁", value: "通过" }, { label: "修订建议", value: "保持留白" }],
          },
        ],
      },
    ],
  },
};

let chapterStudio: ChapterStudioView = {
  projectId: "test-long",
  projectTitle: "测试长篇",
  projectSynopsis: "记忆回收师循着被篡改的时间戳，追查一桩被掩埋的失踪案。",
  nextChapter: 5,
  totalChapters: 24,
  chapters: [
    { number: 1, title: "雨夜来客", state: "completed", detail: "已归档 · 3,526 字" },
    { number: 2, title: "旧桥回声", state: "completed", detail: "已归档 · 3,712 字" },
    { number: 3, title: "失真的录音", state: "completed", detail: "已归档 · 3,589 字" },
    { number: 4, title: "高架桥下", state: "completed", detail: "已归档 · 3,921 字" },
    { number: 5, title: "档案室", state: "current", detail: "待创作" },
    { number: 6, title: "错误的授权", state: "pending", detail: "待排队" },
    ...Array.from({ length: 18 }, (_, offset) => ({ number: offset + 7, title: `章节 ${offset + 7}`, state: "pending" as const, detail: "尚未准备" })),
  ],
  planTitle: "准备章节方案",
  planSummary: "章节工作台当前无运行中任务。系统将先构建章节上下文、衔接与章节计划，生成方案后供你确认。",
  previousSummary: "沈岸从废弃高架桥下取回一枚被擦除编号的存储芯片，发现其中的时间戳与苏晚失踪当天完全重合。",
  previousExitSummary: "芯片上的授权链指向旧城区档案室，门禁记录却显示沈岸从未拥有访问权限。",
  currentGoal: "让沈岸在档案室取得可验证的授权链证据，同时暴露一个足以改变调查方向的代价。",
  currentOutlineSummary: "沈岸借用临时权限进入档案室，发现记录被分层加密；他必须在保安到达前决定是否调用苏晚留下的非法记忆片段。",
  nextGoal: "追查伪造授权的来源，并让主角承担动用记忆片段的后果。",
  suggestion: "让证据先改变人物的行动选择，再揭露其解释；避免把授权链写成无代价的线索投放。",
  memories: [
    { label: "承接要点", value: "存储芯片的时间戳", detail: "与苏晚失踪日重合，不能将其解释为偶然。" },
    { label: "人物状态", value: "沈岸 / 林小满", detail: "沈岸尚未说明旧门禁协议；林小满在外部监控保安动向。" },
    { label: "表达观察", value: "档案室 · 雨声", detail: "让证据先改变行动，再揭露其解释。" },
  ],
  memoryTabs: [
    {
      id: "overview",
      label: "概览",
      cards: [
        { id: "checkpoint", label: "待决策节点", content: "暂无待处理的决策节点。", tone: "highlight" },
        { id: "carry-forward", label: "承接要点", content: "• 存储芯片的时间戳与苏晚失踪日重合，不能将其解释为偶然。\n• 沈岸尚未说明自己为何熟悉旧门禁协议。\n• 林小满承诺在外部监控保安动向，不能在本章中失联。" },
        { id: "suggestion", label: "后续提示", content: "让证据先改变人物的行动选择，再揭露其解释；避免把授权链写成无代价的线索投放。" },
        { id: "scores", label: "评分", content: "对齐 8.7 · 连贯 9.0 · 综合 8.6 · 因果 8.4" },
      ],
    },
    {
      id: "motifs",
      label: "母题",
      cards: [
        { id: "timestamp", label: "时间戳", content: "第 4 章出现；本章应转化为人物行动分歧。" },
        { id: "archive", label: "档案室", content: "保持门禁与授权链的物理限制，不让线索无代价出现。" },
      ],
    },
    {
      id: "relationships",
      label: "关系",
      cards: [
        { id: "shen-lin", label: "沈岸 × 林小满", content: "协作仍在；她只掌握外围监控，不替代主角解释证据。" },
        { id: "shen-su", label: "沈岸 × 苏晚", content: "失踪案是当前调查的情感核心，非法记忆片段尚未使用。" },
      ],
    },
    {
      id: "issues",
      label: "问题",
      cards: [
        { id: "causal", label: "章节提醒", content: "档案室场景需保留门禁失效的因果链。", tone: "warning" },
      ],
    },
    {
      id: "reading_power",
      label: "追读力",
      cards: [
        { id: "hook", label: "章尾钩子", content: "授权链指向旧城区档案室，但门禁记录显示沈岸从未拥有访问权限。" },
      ],
    },
    {
      id: "guardrails",
      label: "护栏",
      cards: [
        { id: "boundary", label: "当前边界", content: "本章只验证记录被隐藏，不公开操作者身份。" },
      ],
    },
    {
      id: "control",
      label: "控制",
      cards: [
        { id: "story-control", label: "剧情控制", content: "当前章节保持调查推进；授权链只作为下一章进入旧城区的触发条件。" },
      ],
    },
  ],
  activity: {
    state: "idle",
    taskLabel: "",
    currentStepLabel: "",
    progressPercent: null,
    checkpoint: null,
    stages: [],
  },
  autorun: {
    status: "idle",
    phase: "",
    mode: "book",
    startChapter: 0,
    currentChapter: 0,
    endChapter: 0,
    totalChapters: 0,
    completedChapters: [],
    activeTaskId: "",
    checkpoint: null,
    checkpointAttempts: 0,
    checkpointBudget: 0,
    totalFailures: 0,
    failureBudget: 0,
    nextRetryAt: "",
    lastError: "",
    updatedAt: "",
  },
  taskErrorLog: [
    {
      id: "fixture-chapter-causal-error",
      timeLabel: "2026-07-14 14:08:31",
      jobLabel: "第 5 章 · 档案室",
      taskId: "VALIDATE_CAUSAL",
      taskLabel: "因果验证",
      attemptLabel: "1/2",
      errorMessage: "因果验证等待上游计划检查点",
      excerpt: "章节计划尚未确认，因果校验被安全地延后；现有草稿与故事状态均未被修改。",
      logPath: "logs/fixture/chapter_005/format_errors/causal.json",
      kindLabel: "等待确认",
      autoResolved: false,
    },
  ],
};

const voiceStudio: VoiceStudioView = {
  projectId: "test-long",
  projectTitle: "测试长篇",
  // Engine projections carry a provider-native ID. The Web surface renders
  // the human-readable MiniMax label without persisting presentation text.
  providerLabel: "MiniMax",
  configuredModelLabel: "speech-02-hd",
  providerCatalog: generatedVoiceProviderCatalog,
  chapterNumber: 4,
  availableChapters: [1, 2, 3, 4],
  teamConfirmed: true,
  scriptFresh: true,
  novelSourceState: "ready",
  scriptFreshness: "current",
  audioFreshness: "missing",
  freshnessBlockingReasons: [],
  unresolvedSpeakerCount: 0,
  audioReady: false,
  subtitleReady: false,
  deliveryState: "not_started",
  activeTaskId: null,
  subtitleText: "",
  mixTracks: [
    { id: "voice", label: "人声主轨", durationMs: 0, eventCount: 0, failedEventCount: 0, status: "not_started", stemAudioUrl: "" },
    { id: "bgm", label: "背景音乐", durationMs: 0, eventCount: 0, failedEventCount: 0, status: "not_started", stemAudioUrl: "" },
    { id: "soundscape", label: "环境声", durationMs: 0, eventCount: 0, failedEventCount: 0, status: "not_started", stemAudioUrl: "" },
    { id: "sfx", label: "短音效", durationMs: 0, eventCount: 0, failedEventCount: 0, status: "not_started", stemAudioUrl: "" },
  ],
  soundAssets: [
    { id: "mock-rain", kind: "soundscape", name: "高架桥夜雨", status: "approved", scope: "project", source: "imported", tags: ["夜雨", "城市"], provider: "", model: "", license: "用户自有素材", commercialUseStatus: "cleared", prompt: "", audioUrl: "" },
    { id: "mock-memory", kind: "bgm", name: "记忆回收主题", status: "pending", scope: "project", source: "generated", tags: ["悬疑", "记忆"], provider: "stable-audio", model: "open-1.0", license: "", commercialUseStatus: "review_required", prompt: "克制的电子氛围与磁带质感", audioUrl: "" },
  ],
  roomTakes: [],
  cast: [
    {
      id: "narrator", name: "旁白", role: "作品级叙述者", statusLabel: "已配", voiceLabel: "沉静女声 · Yue", voiceId: "Chinese (Mandarin)_Gentle_Yue", voiceSourceLabel: "作品级旁白", description: "克制、近距离，保留雨声、磁带等感官锚点的停顿。", speedOffset: 0, pitchOffset: 0, volumeOffset: 0, performancePolicyLabel: "旁白参数已写入作品级叙述档案。", matchSummary: "作品风格匹配", detailFacts: [{ label: "音色类型", value: "沉静女声" }, { label: "叙述范围", value: "moderate · close" }], matchReasons: ["克制", "近距离", "保留感官锚点的停顿"], auditionText: "雨声压在高架桥底，像一盘没倒回去的磁带。", designBrief: "近距离、克制而不失温度的中文女声旁白。",
    },
    {
      id: "lin-zhu", name: "林逐", role: "主视角 · 记忆回收师", statusLabel: "已配", voiceLabel: "冷静女声 · Chen", voiceId: "Chinese (Mandarin)_Cool_Chen", voiceSourceLabel: "系统音色库", description: "表层精确、节奏偏快；触及姐姐线索时才出现短促的迟疑。", speedOffset: 0.04, pitchOffset: 0, volumeOffset: 0, performancePolicyLabel: "自动策略：保持角色声纹稳定。", matchSummary: "画像匹配 77%", detailFacts: [{ label: "角色依据", value: "主角 · 女性 · 26岁 · 理性、警觉、压抑失去亲人的悲伤" }, { label: "LLM 选角", value: "needs_audition · 77%：中性冷静的起句能支撑调查职业感" }, { label: "对比试听候选", value: "热心大婶、温暖闺蜜、南方小哥" }, { label: "合成质量", value: "93%" }], matchReasons: ["中性偏冷的音色与调查职业感一致", "中速起句利于保留叙述中的证据细节", "情绪仅在姐姐线索处收紧，避免全程悲伤"], auditionWarnings: ["仍需试听确认：慢节奏表达是否足够克制", "避免把短促迟疑读成持续低落"], auditionText: "这串时间戳，不该出现在这里。", designBrief: "女声，26岁左右，理性清晰、稍偏冷；说到姐姐和授权链时压低气息，不要渲染成哭腔。",
    },
    {
      id: "zhou-yan", name: "周砚", role: "调查官", statusLabel: "已配", voiceLabel: "低沉男声 · Bo", voiceId: "Chinese (Mandarin)_Steady_Bo", voiceSourceLabel: "系统音色库", description: "语句短，压低声线；信任不足时避免解释性长句。", speedOffset: 0, pitchOffset: -1, volumeOffset: 0.02, performancePolicyLabel: "人工覆盖：音调、音量；其余参数保持自动策略。", matchSummary: "画像匹配 70%", detailFacts: [{ label: "角色依据", value: "配角 · 男性 · 32岁 · 审慎、克制" }, { label: "合成质量", value: "91%" }], matchReasons: ["低沉但不粗粝的声线留住调查官的压迫感"], auditionWarnings: ["确认低音在快速对白中仍清晰"], auditionText: "你确定没有把它带出授权链？", designBrief: "男声，低沉克制，短句有停顿；压迫感来自节奏而非音量。",
    },
    { id: "system", name: "系统播报", role: "城市记忆库", statusLabel: "待配", voiceLabel: "未分配", description: "冷静、无情绪的公共提示音；只在界面和授权记录中出现。", matchReasons: ["公共提示音必须与人物声纹明显区分"], auditionWarnings: ["避免机械感掩盖关键授权信息"], designBrief: "中性、清晰、无多余情绪的公共提示音。" },
  ],
  script: [
    { id: "seg-1", segmentIndex: 0, speakerId: "narrator", speakerLabel: "旁白", kindLabel: "叙述", emotionLabel: "低语", content: "雨声压在高架桥底，像一盘没倒回去的磁带。", statusLabel: "已合成", needsSpeakerReview: false },
    { id: "seg-2", segmentIndex: 1, speakerId: "lin-zhu", speakerLabel: "林逐", kindLabel: "对白", emotionLabel: "克制", content: "这串时间戳，不该出现在这里。", statusLabel: "已合成", needsSpeakerReview: false },
    { id: "seg-3", segmentIndex: 2, speakerId: "zhou-yan", speakerLabel: "周砚", kindLabel: "对白", emotionLabel: "警觉", content: "你确定没有把它带出授权链？", statusLabel: "待试听", needsSpeakerReview: false },
    { id: "seg-4", segmentIndex: 3, speakerId: "narrator", speakerLabel: "旁白", kindLabel: "叙述", emotionLabel: "中性", content: "林逐没有回答，只把数字抄进纸质本。", statusLabel: "待合成", needsSpeakerReview: false },
  ],
};

const filmStageOrder: readonly FilmStageId[] = [
  "planning",
  "screenplay",
  "visual_development",
  "storyboard",
  "shot_production",
  "sound_picture",
  "edit",
  "compliance",
  "delivery",
];

let filmStudio: FilmStudioView = {
  schemaVersion: "2.0",
  projectId: "test-long",
  projectTitle: "测试长篇",
  mode: "collaborative",
  currentStage: "storyboard",
  activeSceneId: "sc-001",
  activeShotId: "sc-001-sh-03",
  productionBible: {
    schemaVersion: "1.0",
    projectId: "test-long",
    title: "测试长篇",
    logline: "记忆回收师在雨夜档案库发现一段被制度刻意抹除的往事。",
    format: "series",
    language: "zh",
    targetAudience: "都市悬疑与近未来剧情观众",
    productionIntent: "以人物选择推动悬疑，不依赖解释性旁白。",
    sources: [
      { artifactType: "story_spec", relativePath: "spec.json", revision: "81f2a31c", exists: true, fieldsUsed: ["title", "theme", "genre", "tone"] },
      { artifactType: "character_bible", relativePath: "character_bible.json", revision: "97be223d", exists: true, fieldsUsed: ["identity", "appearance", "arc", "voice"] },
      { artifactType: "outline", relativePath: "outline.json", revision: "249cd41a", exists: true, fieldsUsed: ["chapters", "scenes", "turning_points"] },
      { artifactType: "voice_team", relativePath: "tts/voice_team.json", revision: "a389de71", exists: true, fieldsUsed: ["character_id", "voice_id", "model_id"] },
    ],
    characters: [
      {
        characterId: "lin-zhu",
        name: "林逐",
        role: "protagonist",
        dramaticFunction: "调查者 / 主视角",
        age: "26",
        gender: "女",
        personality: "理性、警觉，压抑失去亲人的悲伤",
        arc: "从保护私人记忆到承担公开真相的代价",
        screenIdentity: {
          facialAnchors: ["黑色齐肩短发", "右眼下浅痣", "克制的眼神"],
          silhouette: "清瘦、肩线挺直、深灰防雨风衣",
          bodyLanguage: "思考时以拇指摩挲食指第二指节",
          costumePalette: ["石墨灰", "旧铜", "雨夜青"],
          signatureProps: ["纸质记录本", "旧式记忆读取器"],
          continuityRules: ["脸型、痣位、发长、风衣剪裁跨镜头固定"],
          forbiddenDrift: ["不可改变年龄感与发色", "不可随机增加首饰"],
          referenceAssetUrls: [],
        },
        voicePerformance: {
          provider: "minimax",
          voiceId: "Chinese (Mandarin)_Cool_Chen",
          modelId: "speech-2.8-hd",
          timbre: "清晰偏冷，近讲有轻微气声",
          vocalRegister: "中低音",
          cadence: "偏快；触及姐姐线索时出现短促迟疑",
          accent: "普通话",
          emotionRange: ["克制", "警觉", "短暂失控"],
          pronunciationNotes: [],
          deliveryRules: ["压迫感来自停顿与呼吸，不来自喊叫"],
          referenceAudioPath: "",
        },
      },
      {
        characterId: "zhou-yan",
        name: "周砚",
        role: "supporting",
        dramaticFunction: "制度内的见证者",
        age: "32",
        gender: "男",
        personality: "审慎、克制，习惯把风险说成程序",
        arc: "从服从授权链到为证据作证",
        screenIdentity: {
          facialAnchors: ["窄长脸", "左眉旧伤", "深色眼圈"],
          silhouette: "高瘦、黑色制服外套",
          bodyLanguage: "对话时很少直视对方",
          costumePalette: ["黑", "藏蓝", "冷白"],
          signatureProps: ["授权徽章"],
          continuityRules: ["制服编号与左眉伤固定"],
          forbiddenDrift: ["不可改变制服系统"],
          referenceAssetUrls: [],
        },
        voicePerformance: {
          provider: "minimax",
          voiceId: "Chinese (Mandarin)_Steady_Bo",
          modelId: "speech-2.8-hd",
          timbre: "低沉、清晰，不粗粝",
          vocalRegister: "低音",
          cadence: "短句，有审讯式停顿",
          accent: "普通话",
          emotionRange: ["克制", "防御", "决断"],
          pronunciationNotes: [],
          deliveryRules: ["避免解释性长句"],
          referenceAudioPath: "",
        },
      },
    ],
    locations: [
      {
        locationId: "loc-archive",
        name: "废弃记忆档案库",
        dramaticFunction: "秘密被物理封存的空间",
        geography: "旧城区高架桥下",
        era: "近未来",
        spatialLayout: "中央检索台朝北；西侧七排磁带柜；东墙雾面玻璃后为授权室。",
        materials: ["氧化钢", "磨砂玻璃", "旧磁带塑料"],
        practicalLights: ["检索台冷白灯", "授权室红色状态灯"],
        weatherStates: ["持续夜雨"],
        recurringProps: ["编号 0714 的磁带盒", "纸质记录本"],
        ambientSound: ["雨打高架", "荧光灯电流", "磁带机走带"],
        continuityRules: ["柜列编号、门窗、动线与主光方向固定"],
        referenceAssetUrls: [],
      },
    ],
    style: {
      visualThesis: "雨夜青灰与旧铜暖色对冲的近未来悬疑；质感克制，人物优先。",
      genre: "scifi mystery",
      tone: "dark, restrained",
      aspectRatio: "2.39:1",
      frameRate: 24,
      colorScript: ["雨夜青灰", "旧铜记忆暖色", "危险节点红"],
      lightingRules: ["光源必须来自实景动机", "肤色与环境保持一档分离"],
      lensLanguage: ["关系镜头 35–50mm", "情绪特写 75–100mm"],
      cameraRules: ["运动由信息揭示或人物动作驱动", "保持轴线与视线连续"],
      textureMedium: "cinematic live action",
      negativeStyleRules: ["禁止塑料肤质", "禁止无意义漂浮镜头"],
      soundThesis: "近讲呼吸、雨夜宽声场、磁带机械声作为记忆母题",
      musicThesis: "低频模拟合成器，只在人物做出选择后进入",
    },
    worldRules: ["私人记忆读取必须经过双重授权", "被抹除记录会留下不可删除的时间戳"],
    themes: ["记忆是否属于个人", "公开真相的代价"],
    continuityRules: ["连续三个雨夜", "授权链编号不可变化"],
    updatedAt: "2026-08-01T10:00:00Z",
  },
  screenplay: {
    title: "测试长篇 · 影视改编",
    version: 2,
    synopsis: "林逐在废弃档案库发现与姐姐失踪有关的抹除记录。",
    acts: ["档案开门", "授权链断裂", "记忆公开"],
    estimatedDurationS: 318,
    scenes: [
      {
        sceneId: "sc-001", sequenceNumber: 1, heading: "内景 · 废弃记忆档案库 · 雨夜", locationId: "loc-archive", timeOfDay: "雨夜", characters: ["lin-zhu", "zhou-yan"], objective: "林逐找到异常时间戳的原始载体", conflict: "周砚要求她立即交还未授权磁带", turn: "磁带中传出姐姐的声音", visualHook: "红色授权灯在断电后亮起", soundHook: "走带声逐渐与雨刷节拍同步", durationS: 168, sourceChapter: 4, sourceSceneRef: "chapter-004:archive", lines: [{ kind: "action", speaker: "", text: "林逐将编号 0714 的磁带按入读取器。", performanceNote: "动作克制", sourceRef: "chapter_004.md" }],
      },
      {
        sceneId: "sc-002", sequenceNumber: 2, heading: "内景 · 授权室 · 连续", locationId: "loc-archive", timeOfDay: "雨夜", characters: ["lin-zhu", "zhou-yan"], objective: "两人确认是谁删除了双重授权", conflict: "周砚隐瞒自己当年的签名", turn: "系统显示第二授权人仍在线", visualHook: "雾面玻璃后出现第三个人影", soundHook: "公共提示音第一次出现呼吸", durationS: 150, sourceChapter: 4, sourceSceneRef: "chapter-004:auth-room", lines: [{ kind: "dialogue", speaker: "林逐", text: "这串时间戳，不该出现在这里。", performanceNote: "短促，句尾压低", sourceRef: "chapter_004.md" }],
      },
    ],
  },
  visualAssets: [
    { assetId: "asset-lin-zhu", assetType: "character", name: "林逐 · 身份设定表", subjectId: "lin-zhu", view: "turnaround", prompt: "林逐角色设定表，正侧背与表情，深灰防雨风衣，雨夜青灰电影质感，同一身份", negativePrompt: "身份漂移，发色变化，随机首饰", referenceUrls: [], candidates: [], selectedUrl: "", providerId: "bailian", modelId: "wan2.7-image-pro", providerTask: null, qcStatus: "ready", locked: false },
    { assetId: "asset-zhou-yan", assetType: "character", name: "周砚 · 身份设定表", subjectId: "zhou-yan", view: "turnaround", prompt: "周砚角色设定表，黑色制服，左眉旧伤，近未来悬疑电影质感", negativePrompt: "身份漂移，制服编号变化", referenceUrls: [], candidates: [], selectedUrl: "", providerId: "bailian", modelId: "wan2.7-image-pro", providerTask: null, qcStatus: "ready", locked: false },
    { assetId: "asset-loc-archive", assetType: "location", name: "废弃记忆档案库 · 场景资产", subjectId: "loc-archive", view: "environment_sheet", prompt: "废弃记忆档案库场景概念与动线图，氧化钢、磨砂玻璃、冷白灯、雨夜高架", negativePrompt: "空间漂移，门窗错位", referenceUrls: [], candidates: [], selectedUrl: "", providerId: "bailian", modelId: "wan2.7-image-pro", providerTask: null, qcStatus: "ready", locked: false },
  ],
  shots: Array.from({ length: 8 }, (_, index) => {
    const sceneIndex = index < 4 ? 1 : 2;
    const shotNumber = (index % 4) + 1;
    const sizes = ["wide", "medium", "close_up", "extreme_close_up"];
    const motions = ["slow_push", "tracking", "subtle_handheld", "static"];
    return {
      shotId: `sc-00${sceneIndex}-sh-0${shotNumber}`,
      sceneId: `sc-00${sceneIndex}`,
      shotNumber,
      title: ["空间建立", "行动跟随", "关系反应", "转折钩子"][shotNumber - 1]!,
      durationS: [6, 7, 5, 4][shotNumber - 1]!,
      language: { shotSize: sizes[shotNumber - 1]!, cameraAngle: shotNumber === 4 ? "detail" : "eye_level", cameraMotion: motions[shotNumber - 1]!, lighting: "motivated practical", emotion: sceneIndex === 1 ? "警觉升级" : "隐瞒被揭穿", time: "continuous", lensMm: [28, 40, 75, 100][shotNumber - 1]!, composition: "保持轴线与视线连续", focusStrategy: "主体优先；揭示时焦点转移" },
      action: sceneIndex === 1 ? "林逐走向检索台并装入磁带" : "两人核对授权记录，玻璃后出现人影",
      dialogue: shotNumber === 3 ? "这串时间戳，不该出现在这里。" : "",
      soundDesign: "雨声、设备电流、磁带走带",
      transition: "cut",
      prompt: "雨夜青灰近未来悬疑，废弃记忆档案库，角色身份与空间连续，电影级构图与实景动机光",
      negativePrompt: "身份漂移，服装漂移，空间错位，轴线错误，多余肢体",
      characterIds: ["lin-zhu", "zhou-yan"],
      locationId: "loc-archive",
      identityReferenceUrls: [],
      styleReferenceUrls: [],
      referenceVideoUrls: [],
      referenceAudioUrls: [],
      firstFrameUrl: "",
      lastFrameUrl: "",
      generationParams: {},
      selectedAssetUrl: "",
      candidates: [],
      providerId: index % 3 === 0 ? "minimax" : "bailian",
      modelId: index % 3 === 0 ? "MiniMax-Hailuo-2.3" : "wan2.7-r2v",
      generationMode: index % 3 === 0 ? "text_to_video" : "reference_to_video",
      providerTask: null,
      qcStatus: index < 2 ? "approved" : "ready",
      qcNotes: index < 2 ? ["轴线、身份与光向通过"] : [],
      locked: index < 2,
    };
  }),
  runPlan: filmStageOrder.map((stage, index) => ({ nodeId: stage, label: ["改编策划与制片圣经", "剧本与场次化", "角色 / 场景 / 道具资产", "分镜与摄影设计", "镜头生成与连续性质检", "对白 / 环境 / 音乐声画设计", "时间线剪辑、混音与字幕", "总检、母版与交付包"][index]!, stage, dependsOn: index === 0 ? [] : [filmStageOrder[index - 1]!], artifactInputs: index === 0 ? [] : [filmStageOrder[index - 1]!], artifactOutputs: [stage], providerId: "", modelId: "", status: index < 3 ? "completed" : index === 3 ? "active" : "pending", humanCheckpoint: [0, 1, 2, 3, 6, 7].includes(index), retryLimit: 2, estimatedCostUsd: 0, notes: "协作模式停留审核；自主模式在质检通过后自动推进" })),
  timeline: {
    name: "测试长篇 · 主时间线",
    frameRate: 24,
    tracks: [
      { trackId: "v1", name: "画面 V1", kind: "video", clips: [{ clipId: "clip-1", name: "SC01-SH01 空间建立", mediaKind: "video", sourceUrl: "", startS: 0, durationS: 6, sourceStartS: 0, enabled: true, metadata: {} }, { clipId: "clip-2", name: "SC01-SH02 行动跟随", mediaKind: "video", sourceUrl: "", startS: 6, durationS: 7, sourceStartS: 0, enabled: true, metadata: {} }] },
      { trackId: "d1", name: "对白 D1", kind: "dialogue", clips: [{ clipId: "dialogue-1", name: "林逐对白", mediaKind: "dialogue", sourceUrl: "", startS: 8, durationS: 4, sourceStartS: 0, enabled: true, metadata: { voiceId: "Chinese (Mandarin)_Cool_Chen" } }] },
      { trackId: "s1", name: "环境 / 音效 S1", kind: "sfx", clips: [{ clipId: "rain-bed", name: "高架桥夜雨", mediaKind: "sfx", sourceUrl: "", startS: 0, durationS: 22, sourceStartS: 0, enabled: true, metadata: {} }] },
      { trackId: "m1", name: "音乐 M1", kind: "music", clips: [] },
    ],
    markers: [{ name: "磁带声音揭示", timeS: 18, color: "orange", metadata: { sceneId: "sc-001" } }],
  },
  stages: filmStageOrder.map((stage, index) => ({ stage, status: index < 3 ? "completed" : index === 3 ? "active" : "pending", progress: index < 3 ? 1 : index === 3 ? 0.66 : 0, artifactCount: [4, 2, 3, 8, 0, 3, 4, 0, 0][index]!, summary: ["制片圣经已锁定", "场次剧本 v2 已通过", "3 项视觉资产待选图", "8 个镜头已设计，2 个已锁定", "等待批量生成镜头", "已继承声腔角色声线与环境声", "已建立四轨时间线", "等待红线/高风险/正向价值观合规审核", "等待总检与交付"][index]!, warnings: [], updatedAt: "2026-08-01T10:00:00Z" })),
  decisions: [{ decisionId: "decision-board", stage: "storyboard", title: "确认第一场摄影方案", description: "已根据角色身份锁与场景动线生成四镜头方案。", choices: ["通过并生成", "逐镜调整", "AI 自主选择"], selected: "", status: "pending" }],
  mediaArtifacts: [],
  jobs: [],
  qcReports: [],
  visionQcReports: [],
  complianceReport: {
    targetId: "test-long",
    passed: true,
    blocked: false,
    findings: [
      {
        checkId: "PV_FAMILY",
        severity: "positive_value",
        title: "亲情与责任的正向呈现",
        detail: "建议在家庭线收束处补充和解细节。",
        location: "场次 sc-003",
        action: "review",
      },
    ],
    summary: "红线 0 项；高风险 0 项；正向价值观建议 1 项",
    createdAt: "2026-08-01T10:00:00Z",
  },
  delivery: null,
  notices: [],
  updatedAt: "2026-08-01T10:00:00Z",
};

let filmGraph: FilmGraphView = {
  definition: createMockFilmGraph("test-long"),
  validationIssues: [],
  latestRun: null,
};
let filmGraphRuns: FilmGraphRunView[] = [];

function mockFilmRunEstimate(
  scope: FilmGraphRunScope,
  targetNodeIds: readonly string[],
): FilmRunEstimateView {
  const targets = targetNodeIds.length > 0
    ? targetNodeIds
    : filmGraph.definition.nodes.slice(-1).map((node) => node.nodeId);
  const executionNodeIds = scope === "selected"
    ? targets
    : filmGraph.definition.nodes.map((node) => node.nodeId);
  const executionSet = new Set(executionNodeIds);
  const estimatedCostUsd = filmGraph.definition.nodes
    .filter((node) => executionSet.has(node.nodeId))
    .reduce((sum, node) => sum + node.estimatedCostUsd, 0);
  return {
    graphRevision: filmGraph.definition.revision,
    scope,
    targetNodeIds: targets,
    executionNodeIds,
    cachedNodeIds: [],
    estimatedCostUsd: Number(estimatedCostUsd.toFixed(2)),
    estimatedDurationS: Math.max(12, executionNodeIds.length * 38),
    missingInputs: [],
    validationIssues: filmGraph.validationIssues,
    requiresConfirmation: estimatedCostUsd > 0,
  };
}

const filmProviderCatalog: FilmProviderCatalogView = {
  bailian: {
    label: "阿里百炼",
    shortLabel: "百炼",
    strengths: ["长时多镜头", "多主体参考", "声画同步", "角色图集"],
    bestFor: "多镜头叙事、参考图驱动和同一角色成组资产",
    imageModels: [{ id: "wan2.7-image-pro", label: "Wan 2.7 Image Pro", recommended: true }],
    videoModels: [
      { id: "wan2.7-r2v", label: "Wan 2.7 Reference-to-Video", recommended: true, durations: { min: 2, max: 15 }, resolutions: ["720P", "1080P"], defaultDuration: 10, defaultResolution: "720P", features: ["multi_subject_reference", "voice_reference", "prompt_optimizer", "negative_prompt", "watermark", "seed"] },
      { id: "wan2.7-i2v", label: "Wan 2.7 Image-to-Video", recommended: true, durations: { min: 2, max: 15 }, resolutions: ["720P", "1080P"], defaultDuration: 5, defaultResolution: "1080P", features: ["driving_audio", "prompt_optimizer", "negative_prompt", "watermark", "seed"] },
      { id: "wan2.7-t2v", label: "Wan 2.7 Text-to-Video", recommended: true, durations: { min: 2, max: 15 }, resolutions: ["720P", "1080P"], defaultDuration: 5, defaultResolution: "1080P", aspectRatios: ["16:9", "9:16", "1:1", "4:3", "3:4"], features: ["multi_shot", "driving_audio", "prompt_optimizer", "negative_prompt", "watermark", "seed"] },
    ],
  },
  minimax: {
    label: "MiniMax",
    shortLabel: "MiniMax",
    recommended: true,
    strengths: ["镜头运动", "快速图生视频", "主体身份", "语音音乐"],
    bestFor: "镜头运动控制、快速预演和角色脸部身份保持",
    docsUrl: "https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create",
    imageModels: [{ id: "image-01", label: "MiniMax Image-01", recommended: true }],
    videoModels: [
      { id: "MiniMax-H3", label: "MiniMax H3", recommended: true, durations: { min: 4, max: 15 }, resolutions: ["768P", "2K"], defaultDuration: 10, defaultResolution: "768P", aspectRatios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], features: ["native_audio", "multi_modal_reference", "context_ir", "structured_timeline_prompt", "regenerate_2k", "queued_cancel", "watermark"] },
      { id: "MiniMax-Hailuo-2.3", label: "Hailuo 2.3", recommended: false },
      { id: "MiniMax-Hailuo-02", label: "Hailuo 02 首尾帧", recommended: false },
      { id: "S2V-01", label: "S2V-01 主体参考", recommended: false },
    ],
    audioModels: { speech: ["speech-2.8-hd", "speech-2.8-turbo"], music: ["music-3.0"] },
  },
  volcengine_ark: {
    label: "火山方舟",
    shortLabel: "方舟",
    strengths: ["多模态参考", "原生声画", "可信演员资产", "首尾帧"],
    bestFor: "多模态镜头、带声音成片和已授权真人资产复用",
    imageModels: [
      { id: "doubao-seedream-5-0-lite-260128", label: "Seedream 5.0 Lite", recommended: true, features: ["multi_image_reference", "character_consistency"] },
    ],
    videoModels: [
      { id: "doubao-seedance-2-0-260128", label: "Seedance 2.0", recommended: true, durations: { min: 2, max: 15 }, resolutions: ["720P", "1080P"], defaultDuration: 5, defaultResolution: "1080P", aspectRatios: ["16:9", "9:16", "1:1", "4:3", "3:4"], features: ["multi_modal_reference", "native_audio", "trusted_actor_asset", "generate_audio", "return_last_frame", "watermark", "seed"] },
      { id: "doubao-seedance-2-0-fast-260128", label: "Seedance 2.0 Fast", recommended: false, durations: { min: 2, max: 15 }, resolutions: ["720P", "1080P"], defaultDuration: 5, defaultResolution: "720P", aspectRatios: ["16:9", "9:16", "1:1", "4:3", "3:4"], features: ["fast_generation", "native_audio", "generate_audio", "return_last_frame", "watermark", "seed"] },
    ],
    assetSchemes: ["https://", "asset://"],
  },
};

const mockVoiceCatalog = [
  { id: "Chinese (Mandarin)_Cool_Chen", label: "冷静陈", description: "中文 · 女声 · 清晰克制" },
  { id: "Chinese (Mandarin)_Gentle_Yue", label: "温柔玥", description: "中文 · 女声 · 温和叙述" },
  { id: "Chinese (Mandarin)_Steady_Bo", label: "沉稳博", description: "中文 · 男声 · 低沉清晰" },
  { id: "Chinese (Mandarin)_Warm_Qi", label: "温暖祺", description: "中文 · 中性 · 亲和自然" },
] as const;

const mockVoiceCatalogSelections = new Map<string, string>();

let mockVoiceAudioModelCenter: VoiceAudioModelCenterView = {
  installedModelCount: 1,
  modelCount: 3,
  repository: {
    root: "~/Library/Application Support/NIMO/audio-models",
    sizeBytes: 3_420_000_000,
    freeBytes: 126_000_000_000,
    rollbackAvailable: false,
  },
  runtimes: [
    {
      id: "qwen3_tts",
      name: "Qwen3–TTS Sidecar",
      detail: "Python ≥3.12,<3.13 · 端口 8011 · 目标版本 2",
      state: "running",
      status: "运行中",
      version: "2",
      targetVersion: "2",
      managedProcess: true,
      rollbackAvailable: false,
      lastError: "",
    },
    {
      id: "whisperx",
      name: "WhisperX Sidecar",
      detail: "Python ≥3.12,<3.13 · 端口 8013 · 目标版本 3",
      state: "not_installed",
      status: "未安装",
      version: "",
      targetVersion: "3",
      managedProcess: true,
      rollbackAvailable: false,
      lastError: "",
    },
    {
      id: "sherpa_onnx",
      name: "Sherpa ONNX Sidecar",
      detail: "Python ≥3.12,<3.13 · 端口 8014 · 目标版本 4",
      state: "not_installed",
      status: "未安装",
      version: "",
      targetVersion: "4",
      managedProcess: true,
      rollbackAvailable: false,
      lastError: "",
    },
  ],
  models: [
    {
      id: "qwen3_tts_formal",
      name: "Qwen3–TTS Formal",
      roles: ["正式合成", "声音设计"],
      state: "installed",
      stateLabel: "已安装",
      family: "Qwen3–TTS",
      recommendedFor: "高质量中文正式成片",
      detail: "本地文件完整。",
      compatible: true,
      compatibilityReason: "",
      installedSizeBytes: 3_420_000_000,
      estimatedDownloadBytes: 3_420_000_000,
      runtimeStatus: "healthy",
      runtimeVersion: "2",
      installedRevision: "main",
      localPath: "~/Library/Application Support/NIMO/audio-models/plugins/qwen3_tts_formal/main",
      projectReferences: ["测试长篇"],
      requiresLicenseAcceptance: false,
      licenseName: "",
      licenseUrl: "",
      licenseAccepted: false,
      selfTestPassed: true,
    },
    {
      id: "whisperx_large_v3",
      name: "WhisperX Large-v3",
      roles: ["精校复核", "字幕"],
      state: "external",
      stateLabel: "Sidecar 管理",
      family: "WhisperX",
      recommendedFor: "高精度转写与二次对齐",
      detail: "模型由 sidecar 管理；连接运行时后可安装并执行自检。",
      compatible: true,
      compatibilityReason: "",
      installedSizeBytes: 0,
      estimatedDownloadBytes: 2_900_000_000,
      runtimeStatus: "unchecked",
      runtimeVersion: "",
      installedRevision: "",
      localPath: "",
      projectReferences: [],
      requiresLicenseAcceptance: false,
      licenseName: "",
      licenseUrl: "",
      licenseAccepted: false,
      selfTestPassed: null,
    },
    {
      id: "stable_audio_open_small",
      name: "Stable Audio Open Small",
      roles: ["环境声", "SFX", "BGM"],
      state: "not_installed",
      stateLabel: "未安装",
      family: "Stable Audio",
      recommendedFor: "环境声、短音效与背景音乐候选",
      detail: "等待下载。",
      compatible: true,
      compatibilityReason: "",
      installedSizeBytes: 0,
      estimatedDownloadBytes: 1_900_000_000,
      runtimeStatus: "unchecked",
      runtimeVersion: "",
      installedRevision: "",
      localPath: "",
      projectReferences: [],
      requiresLicenseAcceptance: true,
      licenseName: "Stable Audio / Gemma 条款",
      licenseUrl: "https://huggingface.co/stabilityai/stable-audio-open-small",
      licenseAccepted: false,
      selfTestPassed: null,
    },
  ],
};

const mockVoiceAudioOperations = new Map<string, VoiceAudioModelOperationView>();

function completeMockVoiceAudioOperation(operationId: string, request: VoiceAudioModelOperationRequest) {
  const record = mockVoiceAudioOperations.get(operationId);
  if (record === undefined || record.status === "cancelled") return;
  const completed: VoiceAudioModelOperationView = {
    ...record,
    status: "completed",
    message: request.operation === "install"
      ? "模型已下载、校验并登记。"
      : request.operation === "delete"
        ? "模型已删除。"
        : request.operation === "self_test"
          ? "自检通过。"
          : request.operation === "accept_license"
            ? "已记录模型许可确认。"
            : "运行时操作已完成。",
    percent: 100,
  };
  mockVoiceAudioOperations.set(operationId, completed);
  if (request.targetKind === "model") {
    mockVoiceAudioModelCenter = {
      ...mockVoiceAudioModelCenter,
      models: mockVoiceAudioModelCenter.models.map((model) => {
        if (model.id !== request.targetId) return model;
        if (request.operation === "delete") {
          return { ...model, state: "not_installed", stateLabel: "未安装", installedSizeBytes: 0, localPath: "", selfTestPassed: null };
        }
        if (request.operation === "install") {
          return { ...model, state: "installed", stateLabel: "已安装", installedSizeBytes: model.estimatedDownloadBytes, selfTestPassed: null };
        }
        if (request.operation === "accept_license") return { ...model, licenseAccepted: true };
        if (request.operation === "self_test") return { ...model, selfTestPassed: true };
        return model;
      }),
    };
    mockVoiceAudioModelCenter = {
      ...mockVoiceAudioModelCenter,
      installedModelCount: mockVoiceAudioModelCenter.models.filter((model) => model.state === "installed").length,
    };
    return;
  }
  mockVoiceAudioModelCenter = {
    ...mockVoiceAudioModelCenter,
    runtimes: mockVoiceAudioModelCenter.runtimes.map((runtime) => (
      runtime.id !== request.targetId
        ? runtime
        : request.operation === "stop_runtime"
          ? { ...runtime, state: "stopped", status: "已停止" }
          : { ...runtime, state: "running", status: "运行中", version: runtime.targetVersion || runtime.version }
    )),
  };
}

/**
 * A deterministic observation fixture.  It models the same event boundary a
 * desktop sidecar or cloud stream will provide, while remaining deliberately
 * local and free of model/API-key access in Phase 1.
 */
const taskStreamEvents: readonly TaskStreamEvent[] = [
  { streamId: "stream-init-qingwa", sequence: 1, kind: "stream_start", segment: "system", message: "开始接收 一致性画像 输出" },
  { streamId: "stream-init-qingwa", sequence: 2, kind: "delta", segment: "reasoning", text: "先核对主题兑现是否落在可见行动，而非抽象说明。" },
  { streamId: "stream-init-qingwa", sequence: 3, kind: "delta", segment: "content", text: "主题兑现章节必须出现可见的场景、行动或后果，不能停留在认知层面。" },
  { streamId: "stream-init-qingwa", sequence: 4, kind: "delta", segment: "reasoning", text: "继续检查前文锚点是否能支持本章的因果推进。" },
  { streamId: "stream-init-qingwa", sequence: 5, kind: "delta", segment: "content", text: "当前校验通过后，才会将结果写入下一阶段的工作契约。" },
  { streamId: "stream-init-qingwa", sequence: 6, kind: "stream_end", segment: "system", message: "当前预览已完整；等待下一批任务事件。" },
];

const taskStream: TaskStreamView = {
  taskId: "init-long-qingwa",
  title: "长篇立项 · 青瓦梦起",
  stepLabel: "冲突候选裁判 · 当前层：章节契约 · 批次 3/7 · 判定：通过",
  stepId: "adjudicate_init_conflict_candidates_25_36",
  status: "streaming",
  progressPercent: 35,
  jobState: "running",
  summary: {
    outputKind: "文本",
    attempt: 1,
    outputCharacters: 57,
    elapsedMs: 7_000,
    provider: "openai",
    model: "gpt-4o-mini",
    promptTokens: 2_150,
    completionTokens: 832,
    totalTokens: 2_982,
    costUsd: 0.0112,
  },
  calls: [
    {
      callId: "mock-draft-plan",
      task: "PLAN_CHAPTER",
      taskLabel: "章节计划",
      provider: "openai",
      model: "gpt-4o-mini",
      route: "primary",
      status: "success",
      event: "api_stream_done",
      promptTokens: 1_420,
      completionTokens: 510,
      totalTokens: 1_930,
      latencyMs: 4_800,
      costUsd: 0.0071,
    },
    {
      callId: "mock-wave",
      task: "WAVE_CHAPTER",
      taskLabel: "章节织波",
      provider: "deepseek",
      model: "deepseek-chat",
      route: "primary",
      status: "running",
      event: "api_stream_start",
      attempt: 1,
      maxAttempts: 3,
      maxTokens: 8_192,
    },
  ],
  // The deterministic streaming fixture remains in progress, but includes
  // the second content fragment so compact focus and full readers exercise
  // an actual interleaved output sequence.
  events: taskStreamEvents.slice(0, 5),
};

let narrativeTools: NarrativeToolsView = {
  characters: [
    { id: "lin-zhu", name: "林逐", role: "主视角 · 记忆回收师", statusLabel: "活跃", summary: "以职业流程隔离情感；姐姐线索迫使她重新承担私人风险。", arc: "从保留证据到主动决定是否公开记忆。" },
    { id: "zhou-yan", name: "周砚", role: "调查官", statusLabel: "活跃", summary: "掌握授权链漏洞，却不愿轻易交出全部判断。", arc: "从审视林逐到与她共同承担公开的代价。" },
    { id: "lin-jie", name: "林澈", role: "失踪的姐姐", statusLabel: "缺席", summary: "只在残片、录音与他人的行动后果中出现。", arc: "她留下的选择逐步改变当下角色的关系。" },
  ],
  characterDetails: [
    { characterId: "lin-zhu", timelineLabel: "当前线", ageLabel: "28", genderLabel: "女", occupation: "持证记忆回收师。习惯以流程隔离情感，在无主残片案件中被迫正视姐姐失踪留下的空洞。", personality: "外表沉稳寡言，行事恪守梦境采集规范；核心欲望是查清姐姐失踪真相，弱点是在关键线索出现时会以职业流程压住私人情绪。", backstory: "十年前姐姐林澈参与神经同步实验后失踪。官方把事故归为数据错误，林逐此后成为记忆回收师，并长期回避与实验相关的私人关系。", abilities: "记忆采集、证据链核验、授权记录比对；随身保留纸质笔记以避免重要线索被自动同步。", appearance: "短发，常穿灰色工装外套，左手腕有一道旧疤。眼神专注而警惕。", arc: "从以流程自保到主动承担真相的代价，最终在姐姐残片前完成迟到十年的告别。", voice: "语速偏慢，喜欢用短句和职业术语；情绪激动时会突然沉默。", notes: "核心视角角色。前四章以第一人称限制视角展开。" },
    { characterId: "zhou-yan", timelineLabel: "当前线", ageLabel: "32", genderLabel: "男", occupation: "调查官。掌握授权链漏洞的旧案材料，却不愿在证据不足时给出结论。", personality: "克制、警觉，习惯先验证再表态；对林逐既合作也保留审视。", backstory: "曾参与处理早期记忆库违规案件，因一次过早公开证据造成证人风险，之后对“正确但不完整”的结论格外谨慎。", abilities: "调查取证、授权链追踪、跨部门协调。", appearance: "中等身材，戴无框眼镜，着装正式但袖口常卷起。", arc: "从旁观审视到主动为林逐承担一次制度性风险。", voice: "措辞精确，善用反问；偶尔冷幽默。", notes: "次要视角角色，第 5 章起提供补充视角。" },
    { characterId: "lin-jie", timelineLabel: "过去线", ageLabel: "30", genderLabel: "女", occupation: "神经同步实验研究员。", personality: "理性而有保护欲，习惯把风险留给自己。", backstory: "失踪前留下了未经登记的记忆残片与语音线索。", abilities: "神经同步研究、实验数据解读。", appearance: "长发，常扎低马尾；实验服口袋里总插着一支录音笔。", arc: "虽已缺席，但通过残片逐步揭示她主动选择消失保护妹妹。", voice: "温柔但逻辑严密，录音中常以“你以后会明白的”开头。", notes: "只在残片、录音与他人回忆中出现，不直接登场。" },
  ],
  relationships: [
    { id: "lin-zhou", fromCharacterId: "lin-zhu", toCharacterId: "zhou-yan", typeLabel: "同盟 · 不互信", evidence: "第 4 章：林逐隐瞒夜间时间戳。" },
    { id: "lin-sister", fromCharacterId: "lin-zhu", toCharacterId: "lin-jie", typeLabel: "亲属 · 未完成告别", evidence: "无主残片与姐姐失踪日期形成可验证关联。" },
    { id: "zhou-sister", fromCharacterId: "zhou-yan", toCharacterId: "lin-jie", typeLabel: "隐秘 · 调查线", evidence: "周砚持有授权链漏洞的旧案材料。" },
  ],
  visualization: {
    totalChapters: 24,
    phases: [
      { id: "opening", label: "入局阶段", chapterStart: 1, chapterEnd: 6, tensionLabel: "低压渐进", summary: "无主残片出现，林逐被迫重新进入姐姐失踪的旧案。", tone: "opening" },
      { id: "rising", label: "裂隙阶段", chapterStart: 7, chapterEnd: 12, tensionLabel: "中压升级", summary: "授权链漏洞把制度问题转为角色之间的责任选择。", tone: "rising" },
      { id: "climax", label: "崩塌阶段", chapterStart: 13, chapterEnd: 18, tensionLabel: "高压对峙", summary: "隐藏记录被证实，信任关系与公开代价同时升级。", tone: "climax" },
      { id: "resolution", label: "归位阶段", chapterStart: 19, chapterEnd: 24, tensionLabel: "回落与兑现", summary: "角色必须选择公开或封存记忆，并承担行动后果。", tone: "resolution" },
    ],
    milestones: [
      { id: "m4", chapter: 4, label: "Ch.4", description: "林逐发现夜间回收时间戳，却选择暂不告知周砚。" },
      { id: "m8", chapter: 8, label: "Ch.8", description: "授权链漏洞被证实，主线调查的行动条件随之改变。" },
      { id: "m12", chapter: 12, label: "Ch.12", description: "隐藏记录迫使林逐与周砚正面处理信任裂痕。" },
      { id: "m17", chapter: 17, label: "Ch.17", description: "姐姐留下的记录曝光，公开真相的代价急剧上升。" },
      { id: "m20", chapter: 20, label: "Ch.20", description: "角色必须在公开与封存记忆之间作出不可逆的选择。" },
      { id: "m24", chapter: 24, label: "Ch.24", description: "终局选择落地，角色共同承担公开真相带来的后果。" },
    ],
    subplotLanes: [
      { id: "authorization", label: "授权链漏洞", tone: "jade", events: [{ id: "a4", chapter: 4, label: "时间戳", description: "夜间回收时间戳显示有人绕开了既有授权流程。", emphasis: "normal" }, { id: "a8", chapter: 8, label: "漏洞证实", description: "多份记录相互印证，授权链漏洞从猜测变为可验证事实。", emphasis: "turning" }, { id: "a17", chapter: 17, label: "公开代价", description: "公开隐藏记录会牵连无辜证人与整个记忆库的信任。", emphasis: "reveal" }, { id: "a24", chapter: 24, label: "选择", description: "角色决定以何种方式公开漏洞，并接受制度层面的后果。" }] },
      { id: "sister", label: "姐姐的残片", tone: "blue", events: [{ id: "s2", chapter: 2, label: "残片", description: "无主残片出现，关联到姐姐失踪前从未登记的一段记忆。", emphasis: "normal" }, { id: "s6", chapter: 6, label: "语音", description: "残片中的语音指向姐姐主动留下线索而非意外失踪。", emphasis: "turning" }, { id: "s15", chapter: 15, label: "动机", description: "姐姐留下信息的真正动机被确认，改变了林逐的判断。", emphasis: "reveal" }, { id: "s22", chapter: 22, label: "告别", description: "林逐在完整证据前完成迟到的告别，也放下了执念。" }] },
      { id: "trust", label: "林逐与周砚", tone: "red", events: [{ id: "t4", chapter: 4, label: "隐瞒", description: "林逐选择隐瞒时间戳，合作关系第一次出现裂缝。" }, { id: "t10", chapter: 10, label: "分歧", description: "两人围绕证据是否公开产生无法回避的行动分歧。", emphasis: "turning" }, { id: "t18", chapter: 18, label: "并肩", description: "周砚选择承担风险，与林逐共同推进公开决定。", emphasis: "reveal" }, { id: "t24", chapter: 24, label: "承担", description: "两人在终局共同承担公开真相带来的个人与制度代价。" }] },
    ],
    weaveLinks: [
      { id: "authorization-feed", sourceLaneId: "authorization", target: "mainline", chapter: 8, type: "feed_main", label: "反哺主线", description: "授权链漏洞在第 8 章改变主线推进条件。" },
      { id: "authorization-reveal", sourceLaneId: "authorization", target: "mainline", chapter: 17, type: "reveal_key", label: "揭露关键", description: "隐藏记录在第 17 章反哺主线，并提高公开代价。" },
      { id: "trust-feed", sourceLaneId: "trust", target: "mainline", chapter: 18, type: "feed_main", label: "共同承担", description: "两人的关系转变为终局选择提供行动条件。" },
    ],
    characterArcs: [
      { id: "arc-lin", character: "林逐", arcSummary: "从回避旧案到直面姐姐失踪的真相并承担公开代价。", milestones: [{ chapterStart: 1, chapterEnd: 6, description: "被动重返旧案" }, { chapterStart: 7, chapterEnd: 12, description: "在责任选择中动摇" }, { chapterStart: 13, chapterEnd: 18, description: "直面隐藏记录" }, { chapterStart: 19, chapterEnd: 24, description: "做出公开或封存的抉择" }] },
      { id: "arc-zhou", character: "周砚", arcSummary: "从旁观的同事到与林逐并肩承担信任的代价。", milestones: [{ chapterStart: 4, chapterEnd: 10, description: "察觉隐瞒并产生分歧" }, { chapterStart: 11, chapterEnd: 18, description: "在崩塌中选择并肩" }, { chapterStart: 19, chapterEnd: 24, description: "共同承担终局" }] },
    ],
  },
  outline: [
    {
      id: "ch-4", chapterNumber: 4, chapterLabel: "第 4 章", title: "高架桥下", summary: "发现夜间回收时间戳，林逐选择暂不告知周砚。", stateLabel: "已归档",
      goal: "将授权异常转化为可核验的证据，同时让林逐第一次在职业规则与私人选择之间承担代价。",
      facts: [{ label: "视角", value: "林逐" }, { label: "场景", value: "高架桥下的回收站" }, { label: "时间", value: "次日清晨" }],
      beatsSummary: [
        "林逐按例行程序检查回收箱，发现一份没有登记来源的残片。她逐项比对封条、运输单与现场记录，确认箱体没有被打开，却多出一次发生在停机时段的回收时间戳。",
        "周砚追问授权记录的缺口。林逐以设备校验为由暂缓提交残片，并把异常时间抄在纸质笔记上；她没有告诉周砚，这个时间与姐姐最后一通电话完全重合。",
        "两人从运输站找到独立记录，证实异常不是时钟误差。林逐选择保留原始证据，周砚要求次日复核，两人的合作因此出现一道尚未说破的裂痕。",
        "离开回收站时，林逐收到来自停用账号的短讯，只有一串新的授权编号。她回头看见桥下灯光熄灭，本章停在核验编号的行动之前。",
      ],
      mainPlotPoints: ["发现可核验的异常时间戳。", "留下下一次授权链核验的行动入口。"],
      subplotPoints: ["姐姐残片线与职业调查首次交叉。", "林逐的隐瞒使合作关系产生裂隙。"],
      sceneDesignGoals: ["异常必须有两份独立记录佐证。", "不提前揭示停用账号的实际操作者。"],
      notes: "章尾保留行动悬念；本章只确认异常存在，不给出最终解释。",
    },
    { id: "ch-5", chapterNumber: 5, chapterLabel: "第 5 章", title: "档案室", summary: "验证记录被人为隐藏，让证据转为人物之间的行动分歧。", stateLabel: "当前计划" },
    { id: "ch-6", chapterNumber: 6, chapterLabel: "第 6 章", title: "雨幕跟踪", summary: "追踪一条不完整的授权链，避免立即揭示操作者身份。", stateLabel: "待展开" },
  ],
  subplots: [
    {
      id: "authorization",
      title: "授权链漏洞",
      priorityLabel: "准主线",
      chaptersLabel: "第 4–24 章",
      description: "制度漏洞从技术异常转为角色间的责任选择。",
      resolution: "第 24 章 · 揭露关键 · 公开授权链漏洞。",
      plan: {
        name: "授权链漏洞",
        description: "制度漏洞从技术异常转为角色间的责任选择。",
        involvedChapters: [4, 8, 17, 24],
        chapterEvents: [
          { chapterNumber: 4, event: "夜间时间戳显示有人绕开既有授权流程。", weaveNotes: "触发调查", dependsOn: [] },
          { chapterNumber: 8, event: "多份记录相互印证，漏洞从猜测变为可验证事实。", weaveNotes: "反哺主线", dependsOn: [] },
          { chapterNumber: 17, event: "隐藏记录曝光，公开代价急剧上升。", weaveNotes: "揭露关键", dependsOn: [] },
          { chapterNumber: 24, event: "角色决定公开漏洞并承担制度后果。", weaveNotes: "收束", dependsOn: [] },
        ],
        weaveLinks: [
          { sourceType: "主线转折", sourceRef: "夜间时间戳", targetSubplot: "主线", triggerChapter: 4, linkType: "trigger_start", description: "主线异常触发支线调查。" },
          { sourceType: "支线证据", sourceRef: "隐藏记录", targetSubplot: "主线", triggerChapter: 17, linkType: "reveal_key", description: "证据揭露改变主线公开的代价。" },
        ],
        priority: "primary",
        resolutionChapter: 24,
        resolutionTarget: "公开授权链漏洞。",
        resolutionType: "reveal",
      },
    },
    {
      id: "sister",
      title: "姐姐的残片",
      priorityLabel: "常规",
      chaptersLabel: "第 2–22 章",
      description: "遗留记忆只能通过现实证据逐步复原。",
      resolution: "第 22 章 · 价值升华 · 确认她留下信息的动机。",
      plan: {
        name: "姐姐的残片",
        description: "遗留记忆只能通过现实证据逐步复原。",
        involvedChapters: [2, 6, 15, 22],
        chapterEvents: [
          { chapterNumber: 2, event: "无主残片关联到姐姐失踪前未登记的记忆。", weaveNotes: "引入悬念", dependsOn: [] },
          { chapterNumber: 6, event: "残片语音指向姐姐主动留下线索。", weaveNotes: "推进", dependsOn: [] },
          { chapterNumber: 15, event: "她留下信息的真正动机被确认。", weaveNotes: "反哺主线", dependsOn: [] },
          { chapterNumber: 22, event: "林逐在完整证据前完成迟到的告别。", weaveNotes: "收束", dependsOn: [] },
        ],
        weaveLinks: [
          { sourceType: "主线异常", sourceRef: "无主残片", targetSubplot: "主线", triggerChapter: 2, linkType: "trigger_start", description: "主线异常开启姐姐线。" },
          { sourceType: "支线证据", sourceRef: "姐姐动机", targetSubplot: "主线", triggerChapter: 15, linkType: "feed_main", description: "动机证据改变主角的行动目标。" },
        ],
        priority: "normal",
        resolutionChapter: 22,
        resolutionTarget: "确认姐姐留下信息的动机。",
        resolutionType: "ascend",
      },
    },
  ],
  humanizePatterns: [
    { id: "generic-conclusion", name: "万能结语", sourceLabel: "内置", category: "模板结尾", severity: "高", hitCount: 18, lastChapterLabel: "第 4 章", enabled: true },
    { id: "passive-subjectless", name: "被动 / 无主语", sourceLabel: "项目", category: "句法虚化", severity: "高", hitCount: 9, lastChapterLabel: "第 3 章", enabled: true },
    { id: "ai-vocabulary", name: "AI 高频词汇", sourceLabel: "内置", category: "词汇重复", severity: "中", hitCount: 6, lastChapterLabel: "第 4 章", enabled: true },
    { id: "false-ranges", name: "假范围", sourceLabel: "导入", category: "模板句式", severity: "中", hitCount: 0, lastChapterLabel: "—", enabled: false },
  ],
  revisionCandidates: [
    { id: "timing", title: "保留时间戳的解释延迟", summary: "第 5 章只证实记录被隐藏，不补写操作者的动机。", impactLabel: "因果 / 悬念" },
    { id: "voice", title: "缩短周砚的解释性台词", summary: "让他以证据和提问施压，避免代替读者归纳。", impactLabel: "角色声线" },
    { id: "motif", title: "回收旧磁带的感官锚点", summary: "在档案室加入纸页受潮与播放暂停的可见动作。", impactLabel: "意象 / 追读力" },
  ],
  relationshipOverview: {
    totalRelationships: 3,
    highTensionPairs: ["林逐 × 周砚"],
    timelines: [
      { pairId: "lin-zhou", characterA: "林逐", characterB: "周砚", currentStatus: "并肩但存疑", currentTrust: 0.62, currentTension: 0.71, snapshots: [{ chapter: 2, status: "同事", trust: 0.5, tension: 0.3, shift: "周砚注意到林逐对旧案的回避。" }, { chapter: 5, status: "存疑", trust: 0.45, tension: 0.55, shift: "林逐隐瞒档案室发现，周砚察觉。" }, { chapter: 10, status: "分歧", trust: 0.4, tension: 0.75, shift: "两人就授权链的处理方式正面冲突。" }, { chapter: 18, status: "并肩", trust: 0.7, tension: 0.6, shift: "共同承担公开代价后重建信任。" }] },
      { pairId: "lin-su", characterA: "林逐", characterB: "苏晚", currentStatus: "追寻与羁绊", currentTrust: 0.8, currentTension: 0.4, snapshots: [{ chapter: 1, status: "失踪", trust: 0.7, tension: 0.5, shift: "苏晚留下的怀表成为唯一线索。" }, { chapter: 12, status: "线索", trust: 0.75, tension: 0.45, shift: "残片记忆显示她早知风险。" }, { chapter: 22, status: "告别", trust: 0.85, tension: 0.3, shift: "林逐在梦中完成迟来的告别。" }] },
    ],
  },
  blueprintRevision: "mock-blueprint-1",
  characterRevision: "mock-character-1",
  humanizeLibraryRevision: "mock-humanize-library-1",
};

// ── Short drama workbench mock ─────────────────────────────────────────────

function emptyDramaStudio(projectId: string): DramaStudioView {
  return {
    schemaVersion: "1.0",
    projectId,
    title: "",
    language: "zh",
    seriesPlan: null,
    outlines: [],
    screenplays: [],
    runPlan: [],
    timelineMarkers: [],
    updatedAt: new Date().toISOString(),
  };
}

let dramaStudio: DramaStudioView = emptyDramaStudio("demo");

// ── Comic workbench mock ────────────────────────────────────────────────────────

function emptyComicStudio(projectId: string): ComicStudioView {
  return {
    schemaVersion: "1.0",
    projectId,
    title: "",
    format: "page",
    pages: [],
    sourceRevision: "",
    updatedAt: new Date().toISOString(),
  };
}

let comicStudio: ComicStudioView = emptyComicStudio("demo");

function mockComicPages(format: "page" | "webtoon"): ComicPageView[] {
  const panelCount = format === "page" ? 5 : 3;
  const aspect = format === "page" ? "16:9" : "9:16";
  return [1, 2].map((pageNumber) => ({
    pageNumber,
    rhythmNote: pageNumber === 1 ? "开场密集对白，紧凑推进" : "悬念收尾，留白大格",
    panels: Array.from({ length: panelCount }, (_, index) => ({
      panelId: `p${String(pageNumber).padStart(3, "0")}-${String(index + 1).padStart(2, "0")}`,
      pageNumber,
      panelNumber: index + 1,
      sceneId: "sc01",
      beat: index === 0 ? "建立镜头：内景 旧电台 夜" : `对白节拍 ${index}`,
      action: index === 0 ? "林一推门进入控制室" : "",
      characterIds: ["char-a"],
      bubbles:
        index === 0
          ? []
          : [
              {
                speaker: "林一",
                text: `第${pageNumber}页第${index}句对白。`,
                kind: "speech",
                sourceLineRef: `sc01:L${index}`,
              },
            ],
      prompt: `冷峻都市影像；旧电台；${index === 0 ? "建立镜头" : "对白特写"}；画幅${aspect}`,
      aspect,
      qcStatus: "pending",
      locked: false,
    })),
  }));
}

function mockDramaScreenplayScenes() {
  return [0, 1, 2].map((index) => ({
    sceneNumber: index + 1,
    heading: index === 0 ? "宴会厅·日" : "宴会厅·夜",
    action: "灯光骤暗，人群骚动。",
    shotCount: 4,
    lines: Array.from({ length: 7 }, (_, lineIndex) => ({
      speaker: lineIndex % 2 === 0 ? "林晚" : "赵天霸",
      line: `第${lineIndex + 1}句对白。`,
      action: "",
    })),
  }));
}

const delay = async <T,>(value: T): Promise<T> => {
  await new Promise((resolve) => window.setTimeout(resolve, 70));
  return value;
};

/**
 * Simulates a realistic save delay for command operations.
 * The mock returns structured results that match what a real
 * Python sidecar or cloud adapter would produce.
 */
const commandDelay = async <T,>(ms: number, value: T): Promise<T> => {
  await new Promise((resolve) => globalThis.setTimeout(resolve, ms));
  return value;
};

const mockWorkflowDrafts = new Map<WorkflowFormMode, WorkflowDraftView>();
const mockCancelledTaskIds = new Set<string>();
const mockWorkflowPresets = new Map<WorkflowFormMode, Map<string, WorkflowPresetRecordView>>([
  ["short", new Map()],
  ["long", new Map()],
]);
const mockWorkflowHistory = new Map<string, WorkflowAiHistoryEntry[]>();
const mockVoiceRoomTakes = new Map<string, VoiceStudioView["roomTakes"]>();
const mockVoiceScripts = new Map<string, VoiceStudioView["script"]>();

function mockWorkflowHistoryKey(mode: WorkflowFormMode, presetName: string): string {
  return `${mode}:${presetName}`;
}

function mockVoiceRoomTakeKey(projectId: string, chapterNumber: number): string {
  return `${projectId}:${chapterNumber}`;
}

function mockRevision(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

const subplotTones = ["jade", "blue", "red", "violet"] as const;

function mockSubplotView(plan: NarrativeSubplotInput, index: number) {
  const chapters = [...plan.involvedChapters].sort((a, b) => a - b);
  const chaptersLabel = chapters.length === 0
    ? "未配置章节"
    : chapters.length === 1
      ? `第 ${chapters[0]} 章`
      : `第 ${chapters[0]}–${chapters.at(-1)} 章`;
  const priorityLabel = { primary: "准主线", normal: "常规", background: "背景" }[plan.priority];
  const resolution = plan.resolutionChapter > 0
    ? `第 ${plan.resolutionChapter} 章 · ${plan.resolutionTarget || "尚未填写收束目标"}`
    : "尚未规划收束。";
  return {
    id: `subplot-${index}-${plan.name}`,
    title: plan.name,
    priorityLabel,
    chaptersLabel,
    description: plan.description,
    resolution,
    plan,
  };
}

function updateMockSubplots(subplots: readonly NarrativeSubplotInput[]) {
  narrativeTools = {
    ...narrativeTools,
    blueprintRevision: `mock-blueprint-${mockRevision()}`,
    subplots: subplots.map(mockSubplotView),
    visualization: {
      ...narrativeTools.visualization,
      subplotLanes: subplots.map((plan, index) => ({
        id: `subplot-${index}-${plan.name}`,
        label: plan.name,
        tone: subplotTones[index % subplotTones.length]!,
        events: plan.chapterEvents.map((event) => ({
          id: `subplot-${index}-${plan.name}-${event.chapterNumber}`,
          chapter: event.chapterNumber,
          label: event.event.slice(0, 12) || `第${event.chapterNumber}章`,
          description: event.event || `第 ${event.chapterNumber} 章支线节点`,
          emphasis: "normal" as const,
        })),
      })),
      weaveLinks: subplots.flatMap((plan, index) => plan.weaveLinks
        .filter((link) => link.targetSubplot === "主线" && link.triggerChapter > 0)
        .map((link, linkIndex) => {
          const supportedType = ["feed_main", "reveal_key", "theme_echo", "trigger_start", "trigger_turn"].includes(link.linkType)
            ? link.linkType as "feed_main" | "reveal_key" | "theme_echo" | "trigger_start" | "trigger_turn"
            : "feed_main";
          return {
            id: `subplot-${index}-weave-${linkIndex}`,
            sourceLaneId: `subplot-${index}-${plan.name}`,
            target: "mainline" as const,
            chapter: link.triggerChapter,
            type: supportedType,
            label: link.description.slice(0, 12) || "支线交织",
            description: link.description,
          };
        })),
    },
  };
}

function mockGeneratedSubplot(index: number): NarrativeSubplotInput {
  const start = 5 + index * 3;
  const midpoint = start + 5;
  const end = Math.min(24, start + 12);
  return {
    name: index === 0 ? "旧案证人线" : "记忆修复线",
    description: index === 0
      ? "一名旧案证人必须在被抹除前决定是否交出关键记录，迫使主角重新衡量公开的代价。"
      : "受损记忆的修复过程揭开制度漏洞的受害者名单，为主线公开行动提供具体后果。",
    involvedChapters: [start, midpoint, end],
    chapterEvents: [
      { chapterNumber: start, event: "关键人物带来一份无法公开的旧案记录。", weaveNotes: "主线触发", dependsOn: [] },
      { chapterNumber: midpoint, event: "记录的代价迫使角色在保护与揭露之间选择。", weaveNotes: "制造冲突", dependsOn: [] },
      { chapterNumber: end, event: "证据回流主线，改变终局公开的行动条件。", weaveNotes: "反哺主线", dependsOn: [] },
    ],
    weaveLinks: [
      { sourceType: "主线转折", sourceRef: "授权链调查", targetSubplot: "主线", triggerChapter: start, linkType: "trigger_start", description: "主线调查触发支线人物行动。" },
      { sourceType: "支线证据", sourceRef: "旧案记录", targetSubplot: "主线", triggerChapter: end, linkType: "reveal_key", description: "支线证据为主线公开提供关键依据。" },
    ],
    priority: "normal",
    resolutionChapter: end,
    resolutionTarget: "将证据交回主线并改变公开方式。",
    resolutionType: "reveal",
  };
}

function mockNarrativeCharacterConflict(expectedRevision: string): NarrativeCharacterMutationResult | null {
  if (expectedRevision === narrativeTools.characterRevision) return null;
  return {
    status: "conflict",
    message: "演练角色设定已被其他会话更新，请刷新后再保存。",
    ...(narrativeTools.characterRevision === undefined ? {} : { characterRevision: narrativeTools.characterRevision }),
    invalidatedChapters: [],
    warnings: [],
  };
}

function mockNarrativeCharacterSaved(message: string): NarrativeCharacterMutationResult {
  const characterRevision = `mock-character-${mockRevision()}`;
  narrativeTools = { ...narrativeTools, characterRevision };
  return { status: "saved", message, characterRevision, invalidatedChapters: [], warnings: [] };
}

function mockHumanizeLibraryConflict(expectedRevision: string): HumanizeLibraryMutationResult | null {
  if (expectedRevision === narrativeTools.humanizeLibraryRevision) return null;
  return {
    status: "conflict",
    message: "演练拟人化库已被其他会话更新，请刷新后再保存。",
    ...(narrativeTools.humanizeLibraryRevision === undefined ? {} : { humanizeLibraryRevision: narrativeTools.humanizeLibraryRevision }),
  };
}

function mockHumanizeLibrarySaved(message: string, pattern?: NarrativeToolsView["humanizePatterns"][number]): HumanizeLibraryMutationResult {
  const humanizeLibraryRevision = `mock-humanize-library-${mockRevision()}`;
  narrativeTools = { ...narrativeTools, humanizeLibraryRevision };
  return {
    status: "saved",
    message,
    humanizeLibraryRevision,
    ...(pattern === undefined ? {} : { pattern }),
  };
}

function formatTimeLabel(): string {
  const now = new Date();
  return [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

let mockOllama: OllamaManagerView = {
  contractVersion: "1.0",
  revision: "mock-ollama-1",
  endpointScope: "engine_host",
  ownership: "engine_owned",
  runtime: { status: "healthy", version: "0.12.0", detail: "演练 Engine 已连接 Ollama。" },
  sidecar: { enabled: true, autoStart: true, preferLocal: true, binaryAvailable: true },
  storage: { scope: "engine_managed", displayLabel: "Engine 托管的模型目录", pathRevealCapability: false },
  capabilities: { canEnsure: true, canRestart: true, canStop: true, canPull: true, canDelete: true, canConfigurePaths: false },
  models: [{ name: "qwen3:8b", size: 5_200_000_000, modifiedAt: "2026-08-14", details: { family: "qwen3" }, roles: ["generation", "managed"] }],
  configuredRoles: { generationModel: "qwen3:8b", embeddingModel: "", managedProfileIds: ["ollama:qwen3:8b"] },
  routingImpactByModel: {
    "qwen3:8b": { profileIds: ["ollama:qwen3:8b"], primaryRouteIds: [], fallbackRouteIds: [], generationSelected: true, embeddingSelected: false, confirmationToken: "mock-confirmation-token" },
  },
  activeOperations: [],
};

export const mockEngineCommandClient: EngineCommandClient = {
  async saveSettings(command: SaveSettingsCommand): Promise<SaveSettingsResult> {
    // Simulate sidecar latency (350–550ms) without claiming to persist data.
    const latency = 350 + Math.floor(Math.random() * 200);
    const routeIds = command.routes ? Object.keys(command.routes) : [];
    const result: SaveSettingsResult = {
      status: "saved",
      persistence: "accepted_only",
      message: routeIds.length > 0
        ? `前端演练已接受 ${routeIds.length} 条路由${command.themeId ? "、主题" : ""}${command.creativeTemperature ? "、创作温度" : ""}；未写入 model_profiles.json 或 .env。`
        : command.themeId
          ? `前端演练已接受主题「${command.themeId}」；未写入本地配置。`
          : command.creativeTemperature
            ? "前端演练已接受创作温度参数；未写入本地配置。"
            : "前端演练已接受当前设置；未写入本地配置。",
      acceptedRouteIds: routeIds,
      rejectedRoutes: [],
      savedAtLabel: formatTimeLabel(),
      runtimeReloadStatus: "unavailable",
    };
    return commandDelay(latency, result);
  },

  async deleteProjects(command: DeleteProjectsCommand): Promise<DeleteProjectsResult> {
    const requestedIds = [...new Set(command.projectIds)];
    const existingIds = new Set(workspace.projects.map((project) => project.id));
    const deletedProjectIds = requestedIds.filter((projectId) => existingIds.has(projectId));
    const failures = requestedIds
      .filter((projectId) => !existingIds.has(projectId))
      .map((projectId) => ({
        projectId,
        reason: "project_not_found" as const,
        message: "项目目录不存在。",
      }));
    const deletedSet = new Set(deletedProjectIds);
    workspace = {
      ...workspace,
      projects: workspace.projects.filter((project) => !deletedSet.has(project.id)),
      metrics: {
        ...workspace.metrics,
        totalProjects: Math.max(0, workspace.metrics.totalProjects - deletedProjectIds.length),
      },
    };
    return commandDelay(120, {
      status: failures.length === 0 ? "deleted" : deletedProjectIds.length > 0 ? "partial" : "rejected",
      message: deletedProjectIds.length > 0
        ? `已永久删除 ${deletedProjectIds.length} 个项目目录。`
        : "没有项目被删除。",
      deletedProjectIds,
      failures,
    });
  },

  async configureOllamaRuntime(_command: ConfigureOllamaRuntimeCommand): Promise<OllamaManagerView> {
    mockOllama = { ...mockOllama, revision: `mock-ollama-${mockRevision()}` };
    return commandDelay(120, mockOllama);
  },

  async setOllamaModelRoles(command: SetOllamaModelRolesCommand): Promise<OllamaManagerView> {
    const roles = [
      ...(command.generation ? ["generation" as const] : []),
      ...(command.embedding ? ["embedding" as const] : []),
      ...(command.managed ? ["managed" as const] : []),
    ];
    mockOllama = {
      ...mockOllama,
      revision: `mock-ollama-${mockRevision()}`,
      models: mockOllama.models.map((model) => model.name === command.model ? { ...model, roles } : model),
      configuredRoles: {
        ...mockOllama.configuredRoles,
        generationModel: command.generation ? command.model : mockOllama.configuredRoles.generationModel,
        embeddingModel: command.embedding ? command.model : mockOllama.configuredRoles.embeddingModel,
      },
    };
    return commandDelay(120, mockOllama);
  },

  async controlOllamaRuntime(_command: OllamaRuntimeCommand): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", message: "演练 Ollama 运行控制任务已提交。", taskId: `mock-ollama-${mockRevision()}` });
  },

  async pullOllamaModel(command: PullOllamaModelCommand): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", message: `演练下载 ${command.model} 已提交。`, taskId: `mock-ollama-${mockRevision()}` });
  },

  async deleteOllamaModel(_command: DeleteOllamaModelCommand): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", message: "演练删除模型已提交。", taskId: `mock-ollama-${mockRevision()}` });
  },

  async saveChapterRevision(command) {
    return commandDelay(100, {
      status: "applied",
      message: "演练引擎已持久化终稿修订。",
      revision: `mock-revision-${Date.now().toString(36)}`,
      backgroundReevaluateScheduled: command.backgroundReevaluate,
    });
  },

  async generateChapterRevisionCandidate(command) {
    const replacement = command.selectedText.replaceAll("没有", "并无");
    return commandDelay(160, {
      status: "generated",
      message: "演练 Engine 已生成选段精修候选；请审阅后再纳入正文。",
      replacement: replacement === command.selectedText
        ? `${command.selectedText.trim()}。`
        : replacement,
    });
  },

  async saveNarrativeSubplots(command: SaveNarrativeSubplotsCommand): Promise<NarrativeSubplotMutationResult> {
    if (command.expectedRevision !== undefined && command.expectedRevision !== narrativeTools.blueprintRevision) {
      return commandDelay(80, {
        status: "conflict",
        message: "演练蓝图已被其他会话更新，请刷新后再保存。",
        blueprintRevision: narrativeTools.blueprintRevision ?? "mock-blueprint-current",
      });
    }
    updateMockSubplots(command.subplots);
    return commandDelay(100, {
      status: "saved",
      message: `演练引擎已保存 ${command.subplots.length} 条支线，并刷新章节规划投影。`,
      blueprintRevision: narrativeTools.blueprintRevision ?? "mock-blueprint-current",
    });
  },

  async saveNarrativeCharacter(command: SaveNarrativeCharacterCommand): Promise<NarrativeCharacterMutationResult> {
    const conflict = mockNarrativeCharacterConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    const profile = command.profile;
    if (command.characterId === undefined) {
      const name = profile.name?.trim() ?? "";
      if (name.length === 0) return commandDelay(80, { status: "rejected", message: "角色名不能为空。", invalidatedChapters: [], warnings: [] });
      const id = `mock-character-${mockRevision()}`;
      narrativeTools = {
        ...narrativeTools,
        characters: [...narrativeTools.characters, { id, name, role: profile.role ?? "配角", statusLabel: "活跃", summary: profile.personality ?? "暂无角色摘要。", arc: profile.arc ?? "" }],
        characterDetails: [...narrativeTools.characterDetails, { characterId: id, timelineLabel: profile.timeLayer ?? "当前线", ageLabel: profile.age ?? "", genderLabel: profile.gender ?? "", occupation: profile.socialStatus ?? "", personality: profile.personality ?? "", backstory: profile.backstory ?? "", abilities: profile.abilities ?? "", appearance: profile.appearance ?? "", arc: profile.arc ?? "", voice: profile.voice ?? "", notes: profile.notes ?? "" }],
      };
      return commandDelay(100, mockNarrativeCharacterSaved(`演练引擎已新增角色「${name}」。`));
    }
    narrativeTools = {
      ...narrativeTools,
      characters: narrativeTools.characters.map((character) => character.id === command.characterId ? { ...character, ...(profile.name === undefined ? {} : { name: profile.name }), ...(profile.role === undefined ? {} : { role: profile.role }), ...(profile.personality === undefined ? {} : { summary: profile.personality }), ...(profile.arc === undefined ? {} : { arc: profile.arc }) } : character),
      characterDetails: narrativeTools.characterDetails.map((detail) => detail.characterId === command.characterId ? { ...detail, ...(profile.timeLayer === undefined ? {} : { timelineLabel: profile.timeLayer }), ...(profile.age === undefined ? {} : { ageLabel: profile.age }), ...(profile.gender === undefined ? {} : { genderLabel: profile.gender }), ...(profile.socialStatus === undefined ? {} : { occupation: profile.socialStatus }), ...(profile.personality === undefined ? {} : { personality: profile.personality }), ...(profile.backstory === undefined ? {} : { backstory: profile.backstory }), ...(profile.abilities === undefined ? {} : { abilities: profile.abilities }), ...(profile.appearance === undefined ? {} : { appearance: profile.appearance }), ...(profile.arc === undefined ? {} : { arc: profile.arc }), ...(profile.voice === undefined ? {} : { voice: profile.voice }), ...(profile.notes === undefined ? {} : { notes: profile.notes }) } : detail),
    };
    return commandDelay(100, mockNarrativeCharacterSaved("演练引擎已更新角色资料。"));
  },

  async retireNarrativeCharacter(command: RetireNarrativeCharacterCommand): Promise<NarrativeCharacterMutationResult> {
    const conflict = mockNarrativeCharacterConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    narrativeTools = { ...narrativeTools, characters: narrativeTools.characters.map((character) => character.id === command.characterId ? { ...character, statusLabel: "退场" } : character) };
    return commandDelay(100, mockNarrativeCharacterSaved("演练引擎已标记角色退场。"));
  },

  async saveNarrativeRelationship(command: SaveNarrativeRelationshipCommand): Promise<NarrativeCharacterMutationResult> {
    const conflict = mockNarrativeCharacterConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    const existing = narrativeTools.relationships.find((relationship) => (relationship.fromCharacterId === command.sourceCharacterId && relationship.toCharacterId === command.targetCharacterId) || (relationship.fromCharacterId === command.targetCharacterId && relationship.toCharacterId === command.sourceCharacterId));
    const relationship = { id: existing?.id ?? `mock-relationship-${mockRevision()}`, fromCharacterId: command.sourceCharacterId, toCharacterId: command.targetCharacterId, typeLabel: command.relationType, evidence: command.description };
    narrativeTools = { ...narrativeTools, relationships: existing === undefined ? [...narrativeTools.relationships, relationship] : narrativeTools.relationships.map((item) => item.id === existing.id ? relationship : item) };
    return commandDelay(100, mockNarrativeCharacterSaved("演练引擎已保存角色关系。"));
  },

  async removeNarrativeRelationship(command: RemoveNarrativeRelationshipCommand): Promise<NarrativeCharacterMutationResult> {
    const conflict = mockNarrativeCharacterConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    narrativeTools = { ...narrativeTools, relationships: narrativeTools.relationships.filter((relationship) => !((relationship.fromCharacterId === command.sourceCharacterId && relationship.toCharacterId === command.targetCharacterId) || (relationship.fromCharacterId === command.targetCharacterId && relationship.toCharacterId === command.sourceCharacterId))) };
    return commandDelay(100, mockNarrativeCharacterSaved("演练引擎已移除角色关系。"));
  },

  async saveHumanizePattern(command: SaveHumanizePatternCommand): Promise<HumanizeLibraryMutationResult> {
    const conflict = mockHumanizeLibraryConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    const existing = command.patternId === undefined
      ? undefined
      : narrativeTools.humanizePatterns.find((pattern) => pattern.id === command.patternId);
    if (existing?.sourceLabel === "内置") {
      return commandDelay(80, { status: "rejected", message: "内置拟人化模式为代码所有，不能编辑。" });
    }
    const profile = command.pattern;
    const pattern = {
      id: existing?.id ?? command.patternId ?? profile.patternId ?? `lib_user_${mockRevision().slice(-8).padStart(8, "0")}`,
      name: profile.name.trim(),
      sourceLabel: existing?.sourceLabel ?? "用户",
      category: profile.category ?? "",
      severity: profile.severity ?? "medium",
      hitCount: existing?.hitCount ?? 0,
      lastChapterLabel: existing?.lastChapterLabel ?? "—",
      enabled: existing?.enabled ?? true,
      keywords: profile.keywords ?? [],
      notes: profile.notes ?? "",
      examplePhrase: profile.examplePhrase ?? "",
    };
    narrativeTools = {
      ...narrativeTools,
      humanizePatterns: existing === undefined
        ? [...narrativeTools.humanizePatterns, pattern]
        : narrativeTools.humanizePatterns.map((item) => item.id === existing.id ? pattern : item),
    };
    return commandDelay(100, mockHumanizeLibrarySaved(`演练引擎已保存拟人化模式「${pattern.name}」。`, pattern));
  },

  async setHumanizePatternEnabled(command: SetHumanizePatternEnabledCommand): Promise<HumanizeLibraryMutationResult> {
    const conflict = mockHumanizeLibraryConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    const pattern = narrativeTools.humanizePatterns.find((item) => item.id === command.patternId);
    if (pattern === undefined) return commandDelay(80, { status: "rejected", message: "未找到拟人化模式。" });
    const updated = { ...pattern, enabled: command.enabled };
    narrativeTools = { ...narrativeTools, humanizePatterns: narrativeTools.humanizePatterns.map((item) => item.id === pattern.id ? updated : item) };
    return commandDelay(100, mockHumanizeLibrarySaved(`演练引擎已${command.enabled ? "启用" : "停用"}拟人化模式。`, updated));
  },

  async removeHumanizePattern(command: RemoveHumanizePatternCommand): Promise<HumanizeLibraryMutationResult> {
    const conflict = mockHumanizeLibraryConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    const pattern = narrativeTools.humanizePatterns.find((item) => item.id === command.patternId);
    if (pattern === undefined) return commandDelay(80, { status: "rejected", message: "未找到拟人化模式。" });
    if (pattern.sourceLabel === "内置") return commandDelay(80, { status: "rejected", message: "内置拟人化模式不能删除。" });
    narrativeTools = { ...narrativeTools, humanizePatterns: narrativeTools.humanizePatterns.filter((item) => item.id !== pattern.id) };
    return commandDelay(100, mockHumanizeLibrarySaved(`演练引擎已删除拟人化模式「${pattern.name}」。`));
  },

  async mergeHumanizePatterns(command: MergeHumanizePatternsCommand): Promise<HumanizeLibraryMutationResult> {
    const conflict = mockHumanizeLibraryConflict(command.expectedRevision);
    if (conflict !== null) return commandDelay(80, conflict);
    const source = narrativeTools.humanizePatterns.find((item) => item.id === command.sourcePatternId);
    const target = narrativeTools.humanizePatterns.find((item) => item.id === command.targetPatternId);
    if (source === undefined || target === undefined || source.id === target.id) return commandDelay(80, { status: "rejected", message: "拟人化模式合并目标无效。" });
    if (source.sourceLabel === "内置") return commandDelay(80, { status: "rejected", message: "内置拟人化模式不能作为合并来源。" });
    const merged = { ...target, hitCount: target.hitCount + source.hitCount, lastChapterLabel: source.lastChapterLabel !== "—" ? source.lastChapterLabel : target.lastChapterLabel };
    narrativeTools = { ...narrativeTools, humanizePatterns: narrativeTools.humanizePatterns.filter((item) => item.id !== source.id).map((item) => item.id === target.id ? merged : item) };
    return commandDelay(100, mockHumanizeLibrarySaved(`演练引擎已将「${source.name}」合并到「${target.name}」。`, merged));
  },

  async generateNarrativeSubplots(command: GenerateNarrativeSubplotsCommand): Promise<GenerateNarrativeSubplotsResult> {
    const count = Math.max(1, Math.min(command.count ?? 2, 2));
    return commandDelay(520, {
      status: "generated",
      message: `演练引擎已生成 ${count} 条支线候选，请审阅后写入蓝图。`,
      candidates: Array.from({ length: count }, (_, index) => mockGeneratedSubplot(index)),
    });
  },

  async convertNarrativeArcsToSubplots(command: ConvertNarrativeArcsToSubplotsCommand): Promise<NarrativeSubplotMutationResult> {
    if (command.expectedRevision !== undefined && command.expectedRevision !== narrativeTools.blueprintRevision) {
      return commandDelay(80, {
        status: "conflict",
        message: "演练蓝图已被其他会话更新，请刷新后再转换。",
        blueprintRevision: narrativeTools.blueprintRevision ?? "mock-blueprint-current",
      });
    }
    const arcs = (narrativeTools.visualization.characterArcs ?? []).filter((arc) => command.arcIds.includes(arc.id));
    if (arcs.length === 0) return commandDelay(80, { status: "rejected", message: "没有可转换的角色弧光。" });
    const existing = narrativeTools.subplots.map((subplot) => subplot.plan);
    const converted = arcs.map((arc) => {
      const chapters = arc.milestones.flatMap((milestone) => [milestone.chapterStart, milestone.chapterEnd]).filter((chapter) => chapter > 0);
      return {
        name: `${arc.character}线`,
        description: arc.arcSummary,
        involvedChapters: [...new Set(chapters)].sort((a, b) => a - b),
        chapterEvents: arc.milestones.map((milestone) => ({ chapterNumber: milestone.chapterStart, event: milestone.description, weaveNotes: "由角色弧光转换", dependsOn: [] })),
        weaveLinks: [],
        priority: "normal" as const,
        resolutionChapter: 0,
        resolutionTarget: "",
        resolutionType: "" as const,
      };
    });
    updateMockSubplots([...existing, ...converted]);
    return commandDelay(100, {
      status: "saved",
      message: `演练引擎已将 ${converted.length} 条角色弧光转换为支线；原始弧光保持不变。`,
      blueprintRevision: narrativeTools.blueprintRevision ?? "mock-blueprint-current",
    });
  },

  async loadTokenDashboardPreferences() {
    return commandDelay(60, {
      currency: "USD",
      exchangeRates: { USD: 1, CNY: 1, EUR: 0.92 },
      stepWaterfallFilter: "all",
      pricePerMillion: 8,
      priceUnit: "million",
      modelPricePerMillion: {},
      modelPriceUnit: {},
      revision: "mock-token-prefs-1",
    });
  },

  async saveTokenDashboardPreferences(command) {
    const revision = `mock-token-prefs-${Date.now().toString(36)}`;
    return commandDelay(60, {
      status: "saved",
      message: "演练引擎已持久化 Token 追踪偏好。",
      revision,
      preferences: {
        currency: command.currency,
        exchangeRates: command.exchangeRates,
        stepWaterfallFilter: command.stepWaterfallFilter,
        pricePerMillion: command.pricePerMillion ?? 8,
        priceUnit: command.priceUnit ?? "million",
        modelPricePerMillion: command.modelPricePerMillion ?? {},
        modelPriceUnit: command.modelPriceUnit ?? {},
        revision,
      },
    });
  },

  async testModelProfile(command): Promise<TestModelProfileResult> {
    const latency = 220 + Math.floor(Math.random() * 180);
    return commandDelay(latency, {
      ok: true,
      detail: `前端演练连接正常（${command.provider} · ${command.model}）；真实桌面运行时会调用供应商探活。`,
      latencyMs: latency,
      supportsThinking: command.model.toLowerCase().includes("reason")
        || command.model.toLowerCase().includes("qwen3"),
      supportsMultiTurn: true,
    });
  },

  async prepareChapter(command): Promise<ChapterCommandResult> {
    const latency = 200 + Math.floor(Math.random() * 150);
    return commandDelay(latency, {
      status: "accepted",
      message: `第 ${command.chapterNumber} 章准备命令已接受；真实引擎接入后将构建上下文与方案。`,
    });
  },

  async cancelChapter(command): Promise<ChapterCommandResult> {
    const latency = 150 + Math.floor(Math.random() * 100);
    return commandDelay(latency, {
      status: "accepted",
      message: `第 ${command.chapterNumber} 章取消命令已接受；运行中的任务将在下一步骤停止。`,
    });
  },

  async resolveChapterCheckpoint(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-checkpoint-${command.chapterNumber}`, message: "演练引擎已确认检查点选择。" });
  },

  async polishChapter(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-polish-${command.chapterNumber}`, message: "演练引擎已接受润色任务。" });
  },

  async repairContinuity(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-continuity-${command.chapterNumber}`, message: `演练引擎已接受第 ${command.chapterNumber} 章连续性修复。` });
  },

  async repairCausal(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-causal-${command.chapterNumber}`, message: `演练引擎已接受第 ${command.chapterNumber} 章因果链修复。` });
  },

  async repairIssues(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-issues-${command.chapterNumber}`, message: `演练引擎已接受第 ${command.chapterNumber} 章综合问题修复。` });
  },

  async reevaluateChapter(command): Promise<ChapterCommandResult> {
    return commandDelay(100, { status: "accepted", taskId: `mock-reevaluate-${command.chapterNumber}`, message: `演练引擎已接受第 ${command.chapterNumber} 章重评估。` });
  },

  async reextractRelationships(command): Promise<ChapterCommandResult> {
    const scope = command.chapterNumber === undefined || command.chapterNumber === 0 ? "全部已完成章节" : `第 ${command.chapterNumber} 章`;
    return commandDelay(120, { status: "accepted", taskId: `mock-reextract-relationships-${command.projectId}`, message: `演练引擎已接受${scope}的关系重提取。` });
  },

  async repairMotifHistory(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-motif-history-${command.chapterNumber}`, message: `演练引擎已接受第 ${command.chapterNumber} 章的母题历史修复。` });
  },

  async polishOutline(command): Promise<ChapterCommandResult> {
    const scope = command.chapterRange?.trim() || "已选章节";
    return commandDelay(120, {
      status: "accepted",
      taskId: `mock-outline-polish-${command.projectId}`,
      message: `大纲润色任务已提交（${scope}）；完成后会同步章节契约。`,
    });
  },

  async syncChapterContracts(command): Promise<ChapterCommandResult> {
    const scope = command.affectedChapterNumbers?.length
      ? `第 ${command.affectedChapterNumbers.join("、")} 章`
      : "全部受影响章节";
    return commandDelay(120, {
      status: "accepted",
      taskId: `mock-outline-sync-${command.projectId}`,
      message: `${scope}的契约同步任务已提交；章节正文不会被修改。`,
    });
  },

  async extendOutline(command): Promise<ChapterCommandResult> {
    const target = command.targetTotal === undefined
      ? `追加 ${command.additionalChapters ?? 0} 章`
      : `延长至 ${command.targetTotal} 章`;
    return commandDelay(120, {
      status: "accepted",
      taskId: `mock-outline-extend-${command.projectId}`,
      message: `大纲${target}任务已提交；完成后会同步新增章节契约。`,
    });
  },

  async auditBook(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-audit-${command.projectId}`, message: "演练引擎已接受全书审校任务。" });
  },

  async auditBookEditorial(command): Promise<ChapterCommandResult> {
    return commandDelay(120, {
      status: "accepted",
      taskId: `mock-editorial-audit-${command.projectId}`,
      message: "演练引擎已接受全书出版编辑审查。",
    });
  },

  async executeGlobalRepairQueue(command): Promise<ChapterCommandResult> {
    return commandDelay(120, {
      status: "accepted",
      taskId: `mock-global-repair-${command.projectId}`,
      message: `演练引擎已接受最多 ${command.maxItems ?? 20} 项全书审计修复。`,
    });
  },

  async exportBook(command): Promise<ChapterCommandResult> {
    return commandDelay(120, { status: "accepted", taskId: `mock-export-${command.projectId}`, message: `演练引擎已接受 ${command.format} 导出任务。` });
  },

  async cleanChapters(command): Promise<ChapterCommandResult> {
    return commandDelay(80, { status: "accepted", message: `演练引擎已从第 ${command.fromChapter} 章起清理失效产物。` });
  },

  async cancelJob(command): Promise<ChapterCommandResult> {
    const latency = 100 + Math.floor(Math.random() * 80);
    // 镜像 PySide6：取消 = FAILED + cancelled 标记，渲染为「已取消」(muted)。
    // mock 引擎必须把 state 写回 workflow fixture，否则 refreshRuns 拉回旧状态。
    const target = workflow.runs.find((run) => run.id === command.taskId);
    if (target !== undefined && target.stateLabel !== "已取消") {
      workflow = {
        ...workflow,
        runs: workflow.runs.map((run) => run.id === command.taskId
          ? {
              ...run,
              stateLabel: "已取消",
              isCancelled: true,
              validationLabel: "",
              activityLabel: "任务已取消",
            }
          : run),
      };
    }
    mockCancelledTaskIds.add(command.taskId);
    return commandDelay(latency, {
      status: "accepted",
      message: `任务 ${command.taskId} 已取消。`,
    });
  },

  async resumeJob(command): Promise<ChapterCommandResult> {
    return commandDelay(120, {
      status: "accepted",
      taskId: command.taskId,
      message: `任务 ${command.taskId} 已由演练引擎确认恢复。`,
    });
  },

  async retryInitRepair(command): Promise<ChapterCommandResult> {
    return commandDelay(120, {
      status: "accepted",
      taskId: `mock-init-repair-${command.projectId}`,
      message: "演练引擎已接受立项修复与一致性复审。",
    });
  },

  async saveInitManualRepair(
    command: SaveInitManualRepairCommand,
  ): Promise<SaveInitManualRepairResult> {
    return commandDelay(80, {
      status: "rejected",
      message: `演练项目「${command.projectId}」没有可人工修复的初始化产物。`,
    });
  },

  async rebuildMemoryVectors(command): Promise<ChapterCommandResult> {
    return commandDelay(120, {
      status: "accepted",
      taskId: `mock-vector-rebuild-${command.projectId}`,
      message: "演练引擎已提交向量索引重建任务。",
    });
  },

  async clearJobHistory(command): Promise<ClearJobHistoryResult> {
    const clearedTaskIds = command.taskIds ?? workflow.runs
      .filter((run) => run.stateLabel !== "执行中" && run.stateLabel !== "排队中")
      .map((run) => run.id);
    const cleared = new Set(clearedTaskIds);
    // Keep the mock's root task list in lockstep with its workflow cards so
    // the companion cannot retain a task the user has just dismissed.
    jobs = jobs.filter((job) => !cleared.has(job.id));
    workflow = {
      ...workflow,
      runs: workflow.runs.filter((run) => !cleared.has(run.id)),
      errorCount: workflow.errorLog.length,
    };
    return commandDelay(100, {
      status: "cleared",
      message: "演练引擎已清理终态任务记录；完整运行日志仍保留。",
      clearedTaskIds,
    });
  },

  async acknowledgeTaskErrors(command: AcknowledgeTaskErrorsCommand): Promise<TaskErrorResolutionResult> {
    const selected = new Set(command.errorEntryIds);
    const acknowledgedAt = new Date().toISOString();
    const updateEntries = (entries: readonly WorkflowErrorLogEntryView[]) => entries.map((entry) => (
      selected.has(entry.id) && !entry.autoResolved ? { ...entry, acknowledgedAt } : entry
    ));
    const updatedErrorEntryIds = [...new Set([...workflow.errorLog, ...chapterStudio.taskErrorLog]
      .filter((entry) => selected.has(entry.id) && !entry.autoResolved)
      .map((entry) => entry.id))];
    const updated = new Set(updatedErrorEntryIds);
    const workflowErrorLog = updateEntries(workflow.errorLog);
    workflow = {
      ...workflow,
      errorCount: workflowErrorLog.filter((entry) => !entry.autoResolved && !entry.acknowledgedAt).length,
      errorLog: workflowErrorLog,
    };
    chapterStudio = { ...chapterStudio, taskErrorLog: updateEntries(chapterStudio.taskErrorLog) };
    return commandDelay(100, {
      status: "acknowledged",
      message: `演练引擎已确认 ${updatedErrorEntryIds.length} 条错误完成核查。`,
      updatedErrorEntryIds,
    });
  },

  async reopenTaskErrors(command: ReopenTaskErrorsCommand): Promise<TaskErrorResolutionResult> {
    const selected = new Set(command.errorEntryIds);
    const allEntries: readonly WorkflowErrorLogEntryView[] = [
      ...workflow.errorLog,
      ...chapterStudio.taskErrorLog,
    ];
    const updatedErrorEntryIds = [...new Set(allEntries
      .filter((entry) => selected.has(entry.id) && !entry.autoResolved && entry.acknowledgedAt)
      .map((entry) => entry.id))];
    const updated = new Set(updatedErrorEntryIds);
    const updateEntries = (entries: readonly WorkflowErrorLogEntryView[]): readonly WorkflowErrorLogEntryView[] => entries.map((entry) => {
      if (!updated.has(entry.id)) return entry;
      const { acknowledgedAt: _acknowledgedAt, ...pendingEntry } = entry;
      return pendingEntry;
    });
    const workflowErrorLog = updateEntries(workflow.errorLog);
    workflow = {
      ...workflow,
      errorCount: workflowErrorLog.filter((entry) => !entry.autoResolved && !entry.acknowledgedAt).length,
      errorLog: workflowErrorLog,
    };
    chapterStudio = { ...chapterStudio, taskErrorLog: updateEntries(chapterStudio.taskErrorLog) };
    return commandDelay(100, {
      status: "reopened",
      message: `演练引擎已重新打开 ${updatedErrorEntryIds.length} 条错误。`,
      updatedErrorEntryIds,
    });
  },

  async clearClosedTaskErrors(
    command: ClearClosedTaskErrorsCommand,
  ): Promise<ClearClosedTaskErrorsResult> {
    const selected = new Set(command.errorEntryIds);
    const allEntries: readonly WorkflowErrorLogEntryView[] = [
      ...workflow.errorLog,
      ...chapterStudio.taskErrorLog,
    ];
    const closedEntries = allEntries.filter(
      (entry) => selected.has(entry.id) && (entry.autoResolved || Boolean(entry.acknowledgedAt)),
    );
    const clearedErrorEntryIds = [...new Set(closedEntries.map((entry) => entry.id))];
    const cleared = new Set(clearedErrorEntryIds);
    const retainedPendingErrorEntryIds = [...new Set(allEntries
      .filter((entry) => selected.has(entry.id) && !cleared.has(entry.id))
      .map((entry) => entry.id))];
    const relatedTaskIds = new Set(closedEntries.map((entry) => entry.taskId));
    const clearedTaskIds = workflow.runs
      .filter((run) => relatedTaskIds.has(run.id) && run.stateLabel !== "执行中" && run.stateLabel !== "排队中")
      .map((run) => run.id);
    const clearedTasks = new Set(clearedTaskIds);
    jobs = jobs.filter((job) => !clearedTasks.has(job.id));
    workflow = {
      ...workflow,
      runs: workflow.runs.filter((run) => !clearedTasks.has(run.id)),
      errorCount: workflow.errorLog.filter(
        (entry) => !cleared.has(entry.id) && !entry.autoResolved && !entry.acknowledgedAt,
      ).length,
      errorLog: workflow.errorLog.filter((entry) => !cleared.has(entry.id)),
    };
    chapterStudio = {
      ...chapterStudio,
      taskErrorLog: chapterStudio.taskErrorLog.filter((entry) => !cleared.has(entry.id)),
    };
    return commandDelay(100, {
      status: "cleared",
      message: `演练引擎已清理 ${clearedErrorEntryIds.length} 条已闭环诊断索引；完整运行日志仍保留。`,
      clearedTaskIds,
      clearedErrorEntryIds,
      retainedPendingErrorEntryIds,
    });
  },

  async clearErrorArchive(_command: ClearErrorArchiveCommand): Promise<ClearErrorArchiveResult> {
    const removedProjectCount = errorArchiveSummary.projectCount;
    errorArchiveSummary = {
      entryCount: 0,
      projectCount: 0,
      latestTime: "",
    };
    return commandDelay(100, {
      status: "cleared",
      message: "演练引擎已清空任务错误档案；完整运行日志仍保留。",
      removedProjectCount,
    });
  },

  async previewTestProjectCleanup(): Promise<TestProjectCleanupPreview> {
    return commandDelay(80, {
      candidates: [],
      message: "演练工作区没有可清理的测试或空白执行残留。",
    });
  },

  async cleanupTestProjects(): Promise<CleanupTestProjectsResult> {
    return commandDelay(80, {
      candidates: [],
      message: "演练工作区没有可清理的测试或空白执行残留。",
      removedProjectIds: [],
      skippedProjectIds: [],
    });
  },

  async continueLongInit(command): Promise<WorkflowCommandResult> {
    const activeRun = workflow.runs.find(
      (run) => run.kind === "init_long"
        && run.projectLabel === command.projectId
        && (run.stateLabel === "执行中" || run.stateLabel === "排队中"),
    );
    if (activeRun !== undefined) {
      return commandDelay(100, {
        status: "already_running",
        taskId: activeRun.id,
        message: "该项目已有立项任务正在执行",
      });
    }
    const taskId = `mock-long-init-resume-${Date.now().toString(36)}`;
    return commandDelay(120, {
      status: "accepted",
      taskId,
      message: "已按原始立项参数恢复，将从首个未完成节点继续",
    });
  },

  async restartLongInit(command): Promise<RestartLongInitResult> {
    const protectedRun = workflow.runs.find(
      (run) => run.kind === "init_long"
        && run.projectLabel === command.projectId
        && (run.stateLabel === "执行中" || run.stateLabel === "排队中"),
    );
    if (protectedRun !== undefined) {
      return commandDelay(100, {
        status: "rejected",
        message: "该项目仍有正在执行或排队的任务，暂不能重新立项。",
        clearedTaskIds: [],
        removedArtifactCount: 0,
      });
    }

    const clearedTaskIds = workflow.runs
      .filter((run) => run.kind === "init_long" && run.projectLabel === command.projectId)
      .map((run) => run.id);
    const cleared = new Set(clearedTaskIds);
    jobs = jobs.filter((job) => job.projectId !== command.projectId && !cleared.has(job.id));
    workflow = {
      ...workflow,
      runs: workflow.runs.filter((run) => !cleared.has(run.id)),
    };
    workspace = {
      ...workspace,
      projects: workspace.projects.map((project) => project.id !== command.projectId
        ? project
        : {
            ...project,
            initResumeAvailable: false,
            status: "planning",
            statusLabel: "待立项",
            progressLabel: "等待创建长篇项目",
            progressPercent: 0,
            nextAction: "创建长篇项目",
          }),
    };
    return commandDelay(100, {
      status: "reset",
      message: "演练引擎已清理旧立项产物与任务记录；现在可创建新的长篇项目。",
      clearedTaskIds,
      removedArtifactCount: 0,
    });
  },

  async generateWorkflowFields(command) {
    const isLong = command.mode === "long";
    const generated = isLong
      ? {
          premise: "记忆校准师沈岸在一段被判定为噪声的旧梦里听见失踪姐姐留下的求救声；他必须在城市记忆库封存前证明这段记忆真实存在。",
          charactersHint: "沈岸：克制而执拗的记忆校准师；林小满：能辨认情绪残响的旧友；苏晚：失踪者，也是整条授权链的异常源头。",
          worldHint: "近未来滨海城以公共记忆库维护社会秩序，私人记忆副本必须经过授权链登记。",
          conflictHint: "沈岸越接近真相，越发现自己的职业权限正是抹除姐姐存在的工具。",
          openingStyle: "从校准台上突然出现的陌生心跳声切入，让异常先于解释发生。",
          endingStyle: "让沈岸公开半份证据，既救回姐姐的存在，也把自己推入新的审查。",
        }
      : {
          theme: "雨夜停摆的怀表让女儿听见一段被父亲剪断的旧录音；在老宅拆除前，她必须决定要不要让真相重新响起。",
          charactersHint: "女儿：执意整理遗物；父亲：把沉默当作保护；旧友：掌握录音缺失的一面。",
          conflictHint: "父亲的沉默保护了一个人，却让另一段关系重复受伤。",
          endingStyle: "让录音在拆迁现场被真正播放，留下行动后的安静。",
        };
    const requested = new Set(command.focusFields ?? []);
    const operationPayload = command.operation === "polish" && requested.size > 0
      ? Object.fromEntries(Object.entries(generated).filter(([key]) => requested.has(key)))
      : generated;
    return commandDelay(520, {
      status: "generated",
      message: command.operation === "generate"
        ? "演练引擎已生成创作候选，请审阅差异。"
        : "演练引擎已按选定方向生成润色候选。",
      payload: { ...command.currentPayload, ...operationPayload },
      suggestions: ["让证据通过人物选择出现", "减少解释性旁白", "为结尾保留一项未偿代价"],
      creativeNote: {
        corePitch: isLong ? "被制度删除的姐姐，迫使维护制度的人证明她存在过。" : "一段旧录音迫使父女重新选择沉默的代价。",
      },
    });
  },

  async startWorkflow(command): Promise<WorkflowCommandResult> {
    const latency = 250 + Math.floor(Math.random() * 200);
    const taskId = `mock-${command.workflowType}-${Date.now().toString(36)}`;
    if (command.workflowType === "long_chapter" && command.runMode === "autorun") {
      // 演练引擎回写持久化连跑投影，使「启动章节连跑」后轮询刷新能观察到
      // running 状态（对标真实 Engine 的 BookAutorunCoordinator 会话）。
      const scope = command.payload.autorunScope === "chapter" ? "chapter" : "book";
      const currentChapter = command.payload.chapterNumber;
      chapterStudio = {
        ...chapterStudio,
        autorun: {
          status: "running",
          phase: "preparing",
          mode: scope,
          startChapter: currentChapter,
          currentChapter,
          endChapter: scope === "chapter" ? currentChapter : chapterStudio.totalChapters,
          totalChapters: chapterStudio.totalChapters,
          completedChapters: [],
          activeTaskId: taskId,
          checkpoint: null,
          checkpointAttempts: 0,
          checkpointBudget: 5,
          totalFailures: 0,
          failureBudget: 3,
          nextRetryAt: "",
          lastError: "",
          updatedAt: formatTimeLabel(),
        },
        activity: {
          state: "running",
          taskLabel: `第 ${currentChapter} 章生成`,
          currentStepLabel: "准备章节方案",
          operationDetail: "正在调用模型生成章节规划（第 1/3 次）",
          progressPercent: 5,
          checkpoint: null,
          stages: [],
          runInsights: [
            { id: "intent", status: "running", label: "意图保护", summary: "运行中持续检查", detail: "人物、关系、POV 与结局不会被静默覆盖。", count: 0, artifactStepKey: "" },
            { id: "research", status: "inactive", label: "研究与证据", summary: "本次运行未触发章节研究", detail: "研究仍受项目开关与章节缺口门禁控制。", count: 0, artifactStepKey: "chapter_research" },
            { id: "revision", status: "pending", label: "修订与回滚", summary: "尚未进入修订阶段", detail: "只有诊断命中问题后才会改文。", count: 0, artifactStepKey: "" },
            { id: "final_verify", status: "pending", label: "最终验证", summary: "等待最终文本验证", detail: "验证通过后禁止继续语义修改。", count: 0, artifactStepKey: "" },
          ],
          efficiency: {
            llmCalls: 0,
            promptTokens: 0,
            completionTokens: 0,
            totalTokens: 0,
            costUsd: 0,
            researchQueries: 0,
            researchCacheHits: 0,
            semanticMutations: 0,
            reportRefreshes: 0,
            repairRounds: 0,
            shortRevisionRounds: 0,
            rollbacks: 0,
            finalHashVerifications: 0,
          },
        },
      };
      return commandDelay(latency, {
        status: "accepted",
        taskId,
        message: "Engine 连跑状态机已启动",
      });
    }
    return commandDelay(latency, {
      status: "accepted",
      taskId,
      message: `${command.workflowType === "short" ? "短篇" : command.workflowType === "long_init" ? "长篇初始化" : "长篇章节"}工作流已接受；真实引擎接入后将启动任务 ${taskId}。`,
    });
  },

  async loadWorkflowDraft(mode): Promise<WorkflowDraftView> {
    return commandDelay(60, mockWorkflowDrafts.get(mode) ?? {
      mode,
      payload: null,
      revision: "",
      savedAtLabel: "",
    });
  },

  async saveWorkflowDraft(command): Promise<WorkflowPersistenceResult> {
    const current = mockWorkflowDrafts.get(command.mode);
    if (command.expectedRevision !== undefined && command.expectedRevision !== (current?.revision ?? "")) {
      return commandDelay(60, {
        status: "conflict",
        message: "草稿已被其他窗口更新。",
        revision: current?.revision ?? "",
      });
    }
    const revision = mockRevision();
    const savedAtLabel = formatTimeLabel();
    mockWorkflowDrafts.set(command.mode, {
      mode: command.mode,
      payload: command.payload,
      revision,
      savedAtLabel,
    });
    return commandDelay(60, { status: "saved", message: "表单草稿已持久化。", revision, savedAtLabel });
  },

  async listWorkflowPresets(mode): Promise<readonly WorkflowPresetRecordView[]> {
    return commandDelay(60, [...(mockWorkflowPresets.get(mode)?.values() ?? [])]);
  },

  async saveWorkflowPreset(command): Promise<WorkflowPersistenceResult> {
    const store = mockWorkflowPresets.get(command.mode)!;
    const current = store.get(command.name);
    if (command.expectedRevision !== undefined && command.expectedRevision !== (current?.revision ?? "")) {
      return commandDelay(60, {
        status: "conflict",
        message: "预设已被其他窗口更新。",
        revision: current?.revision ?? "",
      });
    }
    const revision = mockRevision();
    const savedAtLabel = formatTimeLabel();
    store.set(command.name, {
      name: command.name,
      payload: command.payload,
      revision,
      updatedAtLabel: savedAtLabel,
    });
    return commandDelay(60, { status: "saved", message: `预设「${command.name}」已保存。`, revision, savedAtLabel });
  },

  async deleteWorkflowPreset(command): Promise<WorkflowPersistenceResult> {
    const store = mockWorkflowPresets.get(command.mode)!;
    const current = store.get(command.name);
    if (current === undefined) {
      return commandDelay(60, { status: "not_found", message: "预设不存在。" });
    }
    if (command.expectedRevision !== undefined && command.expectedRevision !== current.revision) {
      return commandDelay(60, { status: "conflict", message: "预设已被其他窗口更新。", revision: current.revision });
    }
    store.delete(command.name);
    return commandDelay(60, { status: "deleted", message: `预设「${command.name}」已删除。` });
  },

  async listWorkflowAiHistory(mode, presetName): Promise<readonly WorkflowAiHistoryEntry[]> {
    return commandDelay(60, mockWorkflowHistory.get(mockWorkflowHistoryKey(mode, presetName)) ?? []);
  },

  async saveWorkflowAiHistory(command: SaveWorkflowAiHistoryCommand): Promise<WorkflowPersistenceResult> {
    const key = mockWorkflowHistoryKey(command.mode, command.presetName);
    const timestamp = new Date().toISOString().replaceAll(/[-:.]/g, "").replace("Z", "Z");
    const entry: WorkflowAiHistoryEntry = {
      id: `${timestamp}_${command.operation}.json`,
      timestamp,
      operation: command.operation,
      data: command.data,
      ...(command.hint ? { hint: command.hint } : {}),
      ...(command.selectedSuggestions?.length ? { selectedSuggestions: command.selectedSuggestions } : {}),
      ...(command.focusFields?.length ? { focusFields: command.focusFields } : {}),
      ...(command.metadata === undefined ? {} : { metadata: command.metadata }),
    };
    mockWorkflowHistory.set(key, [entry, ...(mockWorkflowHistory.get(key) ?? [])].slice(0, 20));
    return commandDelay(60, { status: "saved", message: "创作札记已持久化。" });
  },

  async clearWorkflowAiHistory(mode, presetName): Promise<WorkflowPersistenceResult> {
    mockWorkflowHistory.delete(mockWorkflowHistoryKey(mode, presetName));
    return commandDelay(60, { status: "deleted", message: "创作札记已清空。" });
  },

  async synthesizeVoice(command): Promise<VoiceCommandResult> {
    const latency = 300 + Math.floor(Math.random() * 250);
    const segmentCount = command.segmentIds?.length ?? 0;
    const taskId = segmentCount > 0 ? `mock-tts-${Date.now().toString(36)}` : undefined;
    if (taskId !== undefined) mockCancelledTaskIds.delete(taskId);
    const result: VoiceCommandResult = taskId !== undefined
      ? { status: "accepted", taskId, message: `${segmentCount} 段语音合成命令已接受；真实 TTS 接入后将开始生成。` }
      : { status: "no_segments", message: "未指定合成片段；请先在配音脚本中选择目标片段。" };
    return commandDelay(latency, result);
  },

  async buildVoiceTeam(command): Promise<VoiceCommandResult> {
    const taskId = `mock-voice-team-${command.projectId}`;
    mockCancelledTaskIds.delete(taskId);
    return commandDelay(100, { status: "accepted", taskId, message: "演练引擎已接受声线团队构建任务。" });
  },

  async rebuildNarratorVoice(command): Promise<VoiceCommandResult> {
    return commandDelay(100, { status: "accepted", message: `演练引擎已为 ${command.projectId} 重新生成旁白音色。` });
  },

  async confirmVoiceTeam(): Promise<VoiceCommandResult> {
    return commandDelay(80, { status: "accepted", message: "演练引擎已确认配音团队。" });
  },

  async cloneCharacterVoice(command): Promise<VoiceCommandResult> {
    return commandDelay(100, { status: "accepted", message: `演练引擎已为 ${command.characterId} 持久化克隆音色。` });
  },

  async designCharacterVoice(command): Promise<VoiceCommandResult> {
    return commandDelay(100, { status: "accepted", message: `演练引擎已为 ${command.characterId} 持久化设计音色。` });
  },

  async approveCharacterVoice(command): Promise<VoiceCommandResult> {
    return commandDelay(80, { status: "accepted", message: `演练引擎已批准 ${command.characterId} 的音色。` });
  },

  async previewCharacterVoice(command): Promise<VoiceCommandResult> {
    return commandDelay(100, { status: "accepted", audioUrl: "", message: `演练引擎已生成 ${command.characterId} 的音色试听。` });
  },

  async buildVoicePreviewPlan(command): Promise<VoiceCommandResult> {
    const characters = command.characters ?? [];
    const plans = characters.map((character, index) => {
      const characterId = String(
        character.character_id ?? character.characterId ?? `mock-char-${index}`,
      );
      const characterName = String(
        character.name ?? character.character_name ?? characterId,
      );
      const candidates = mockVoiceCatalog.slice(0, 3).map((option) => ({
        voiceId: option.id,
        voiceName: option.label,
        description: option.description,
        gender: "neutral",
        matchReasons: ["演练目录匹配"],
      }));
      return {
        characterId,
        characterName,
        label: characterName.toLowerCase(),
        sampleText:
          command.sampleText ??
          `${characterName}。黄昏时分，我站在老槐树下，风从巷口吹来。`,
        speed: 1.0,
        volume: 1.0,
        candidates,
      };
    });
    return commandDelay(120, {
      status: "accepted",
      message: `演练引擎已为 ${plans.length} 个角色生成音色候选计划。`,
      data: { plans },
    });
  },

  async generateVoicePreviews(command): Promise<VoiceCommandResult> {
    const plan = command.plan;
    const candidates = plan.candidates.map((candidate) => ({
      ...candidate,
      audioUrl: "",
      samplePath: `/mock/tts/audio/chapter_000/preview_${candidate.voiceId}.mp3`,
    }));
    return commandDelay(140, {
      status: "accepted",
      message: `演练引擎已生成 ${candidates.length} 个候选试听。`,
      data: { plan: { ...plan, candidates } },
    });
  },

  async confirmVoicePreview(command): Promise<VoiceCommandResult> {
    mockVoiceCatalogSelections.set(`${command.projectId}:${command.characterId}`, command.voiceId);
    return commandDelay(90, {
      status: "accepted",
      message: `演练引擎已确认 ${command.characterId} 的音色并写入配音团队。`,
      data: { team: { entries: [], narrator_voice_id: "", default_provider: "" } },
    });
  },

  async updateVoicePerformance(command): Promise<VoiceCommandResult> {
    return commandDelay(80, { status: "accepted", message: `演练引擎已保存 ${command.characterId} 的表达参数。` });
  },

  async assignCatalogVoice(command): Promise<VoiceCommandResult> {
    const option = mockVoiceCatalog.find((item) => item.id === command.voiceId);
    if (option === undefined) {
      return commandDelay(80, { status: "rejected", message: "所选音色不在演练目录中。" });
    }
    mockVoiceCatalogSelections.set(`${command.projectId}:${command.characterId}`, option.id);
    return commandDelay(80, {
      status: "accepted",
      message: `演练引擎已将 ${option.label} 写入 ${command.characterId}，请重新试听并确认团队。`,
    });
  },

  async analyzeVoiceScriptStyle(command): Promise<VoiceCommandResult> {
    return commandDelay(120, {
      status: "accepted",
      message: `演练引擎已建立「${command.sourceName}」的抽象配音风格画像。`,
      data: {
        profile: {
          profile_id: "mock-style-profile",
          source_name: command.sourceName,
          analysis_mode: "hybrid",
          confidence: 0.78,
        },
      },
    });
  },

  async generateVoiceScript(command): Promise<VoiceCommandResult> {
    const taskId = `mock-voice-script-${command.chapterNumber}`;
    mockCancelledTaskIds.delete(taskId);
    return commandDelay(100, { status: "accepted", taskId, message: "演练引擎已接受配音脚本生成任务。" });
  },

  async saveVoiceScript(command): Promise<VoiceCommandResult> {
    const key = mockVoiceRoomTakeKey(command.projectId, command.chapterNumber);
    const current = mockVoiceScripts.get(key) ?? voiceStudio.script;
    const editsByIndex = new Map(command.edits.map((edit) => [edit.segmentIndex, edit]));
    mockVoiceScripts.set(key, current.map((segment) => {
      const edit = editsByIndex.get(segment.segmentIndex);
      if (edit === undefined) return segment;
      return {
        ...segment,
        content: edit.content,
        speakerId: edit.speakerId,
        speakerLabel: edit.speakerLabel,
        kindLabel: edit.segmentType === "dialogue"
          ? "对白"
          : edit.segmentType === "inner_thought"
            ? "内心"
            : "叙述",
        emotionLabel: edit.emotionLabel,
      };
    }));
    mockVoiceRoomTakes.delete(key);
    return commandDelay(80, { status: "accepted", message: `演练引擎已保存 ${command.edits.length} 个脚本片段。` });
  },

  async saveVoiceGuidance(command): Promise<VoiceCommandResult> {
    const key = mockVoiceRoomTakeKey(command.projectId, command.chapterNumber);
    const current = mockVoiceRoomTakes.get(key) ?? [];
    const editedIndices = new Set(command.edits.map((edit) => edit.segmentIndex));
    mockVoiceRoomTakes.set(key, [
      ...current.filter((take) => !editedIndices.has(take.segmentIndex)),
      ...command.edits.map((edit) => ({
        segmentIndex: edit.segmentIndex,
        state: "guidance_saved" as const,
        guidance: edit.segmentOverride,
      })),
    ]);
    return commandDelay(80, {
      status: "accepted",
      message: `演练引擎已保存 ${command.edits.length} 段待审试听指导；已有候选试听已舍弃，正式脚本未变。`,
    });
  },

  async previewVoiceSegment(command): Promise<VoiceCommandResult> {
    const takeId = `mock-take-${command.segmentIndex}-${Date.now().toString(36)}`;
    const key = mockVoiceRoomTakeKey(command.projectId, command.chapterNumber);
    const current = mockVoiceRoomTakes.get(key) ?? [];
    mockVoiceRoomTakes.set(key, [
      ...current.filter((take) => take.segmentIndex !== command.segmentIndex),
      {
        segmentIndex: command.segmentIndex,
        state: "candidate",
        takeId,
        ...(command.segmentOverride === undefined ? {} : { guidance: command.segmentOverride }),
      },
    ]);
    return commandDelay(100, { status: "accepted", takeId, audioUrl: "", message: "演练引擎已生成试听版本。" });
  },

  async acceptVoiceTake(command): Promise<VoiceCommandResult> {
    const key = mockVoiceRoomTakeKey(command.projectId, command.chapterNumber);
    const current = mockVoiceRoomTakes.get(key) ?? [];
    mockVoiceRoomTakes.set(key, current.map((take) => take.takeId === command.takeId
      ? { segmentIndex: take.segmentIndex, state: "accepted" as const, takeId: take.takeId }
      : take));
    return commandDelay(100, { status: "accepted", takeId: command.takeId, message: "演练引擎已确认采用此版本。" });
  },

  async reassembleVoice(command): Promise<VoiceCommandResult> {
    return commandDelay(100, { status: "accepted", taskId: `mock-reassemble-${command.chapterNumber}`, message: "演练引擎已接受重新合成任务。" });
  },

  async runFullVoicePipeline(command): Promise<VoiceCommandResult> {
    return commandDelay(100, { status: "accepted", taskId: `mock-full-voice-${command.chapterNumber}`, message: "演练引擎已接受完整配音流程。" });
  },

  async rejectVoiceTake(command): Promise<VoiceCommandResult> {
    const key = mockVoiceRoomTakeKey(command.projectId, command.chapterNumber);
    const current = mockVoiceRoomTakes.get(key) ?? [];
    mockVoiceRoomTakes.set(key, current.flatMap((take) => {
      if (take.takeId !== command.takeId) return [take];
      return take.guidance === undefined
        ? []
        : [{
            segmentIndex: take.segmentIndex,
            state: "guidance_saved" as const,
            guidance: take.guidance,
          }];
    }));
    return commandDelay(80, { status: "accepted", takeId: command.takeId, message: "演练引擎已舍弃试听，正式版本未变。" });
  },

  async clearVoiceArtifacts(command): Promise<VoiceCommandResult> {
    return commandDelay(80, { status: "accepted", message: `演练引擎已接受 ${command.scope} 清理请求。` });
  },

  async generateSoundPalette(): Promise<VoiceCommandResult> {
    return commandDelay(120, { status: "accepted", message: "演练引擎已生成作品声音候选。" });
  },

  async updateSoundAsset(): Promise<VoiceCommandResult> {
    return commandDelay(80, { status: "accepted", message: "演练引擎已更新声音资产。" });
  },

  async importSoundAssets(command): Promise<VoiceCommandResult> {
    return commandDelay(100, { status: "accepted", message: `演练引擎已导入 ${command.files.length} 个声音资产。` });
  },

  async resolveSpeakers(command): Promise<VoiceCommandResult> {
    const count = command.resolutions.length;
    return commandDelay(200, {
      status: "accepted",
      message: `已确认 ${count} 段说话人分配；配音脚本已更新。`,
    });
  },

  async exportAudio(command: ExportAudioCommand): Promise<VoiceCommandResult> {
    const latency = 200 + Math.floor(Math.random() * 150);
    const description = command.scope === "book"
      ? `全书音频打包${command.includeSubtitles ? "（含字幕）" : ""}`
      : command.format === "srt"
        ? `第 ${command.chapterNumber} 章字幕`
        : `第 ${command.chapterNumber} 章 ${command.format.toUpperCase()} 音频${command.targetLufs === undefined ? "" : `（${command.targetLufs} LUFS）`}`;
    return commandDelay(latency, {
      status: "accepted",
      message: `${description}导出命令已在前端演练中接受；真实 EngineCommandClient 接入后才会生成文件。`,
    });
  },

  async exportAudiobook(_command: ExportAudiobookCommand): Promise<VoiceCommandResult> {
    return commandDelay(260, {
      status: "accepted",
      message: "有声书包导出命令已在前端演练中接受；真实 EngineCommandClient 接入后才会生成交付包。",
    });
  },

  async bootstrapFilm(command) {
    filmStudio = { ...filmStudio, projectId: command.projectId, mode: command.mode };
    return commandDelay(180, filmStudio);
  },

  async advanceFilm(command) {
    const currentIndex = filmStageOrder.indexOf(filmStudio.currentStage);
    const requestedIndex = command.mode === "autonomous"
      ? filmStageOrder.indexOf(command.runUntil ?? "shot_production")
      : Math.min(currentIndex + 1, filmStageOrder.length - 1);
    const targetIndex = Math.max(currentIndex, Math.min(requestedIndex, filmStageOrder.indexOf("shot_production")));
    const target = filmStageOrder[targetIndex]!;
    filmStudio = {
      ...filmStudio,
      mode: command.mode,
      currentStage: target,
      stages: filmStudio.stages.map((item) => {
        const index = filmStageOrder.indexOf(item.stage);
        if (index < targetIndex) return { ...item, status: "completed", progress: 1 };
        if (index === targetIndex) {
          return {
            ...item,
            status: target === "shot_production" && command.mode === "autonomous" ? "blocked" : "active",
            progress: target === "shot_production" ? 0 : Math.max(item.progress, 0.1),
            warnings: target === "shot_production" && command.mode === "autonomous"
              ? ["已完成自主策划；付费镜头生成等待明确授权。"]
              : item.warnings,
          };
        }
        return item;
      }),
    };
    return commandDelay(420, filmStudio);
  },

  async generateFilmAsset(command) {
    const swatch = command.assetId.includes("loc") ? "25374c" : "8b6a55";
    const generated = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="420"><rect width="100%" height="100%" fill="#${swatch}"/><circle cx="320" cy="180" r="90" fill="#d9b08c" opacity=".55"/><text x="32" y="380" fill="white" font-size="26">${command.assetId} · ${command.modelId}</text></svg>`)}`;
    filmStudio = {
      ...filmStudio,
      visualAssets: filmStudio.visualAssets.map((asset) => asset.assetId === command.assetId
        ? { ...asset, providerId: command.providerId, modelId: command.modelId, candidates: [generated], selectedUrl: generated, qcStatus: "review" }
        : asset),
    };
    return commandDelay(520, filmStudio);
  },

  async selectFilmAsset(command) {
    filmStudio = {
      ...filmStudio,
      visualAssets: filmStudio.visualAssets.map((asset) => asset.assetId === command.assetId
        ? { ...asset, selectedUrl: command.url, locked: command.lock, qcStatus: command.lock ? "approved" : "review" }
        : asset),
    };
    return commandDelay(120, filmStudio);
  },

  async updateFilmShot(command) {
    filmStudio = {
      ...filmStudio,
      shots: filmStudio.shots.map((shot) => shot.shotId === command.shotId
        ? ({ ...shot, ...command.patch } as unknown as typeof shot)
        : shot),
    };
    return commandDelay(120, filmStudio);
  },

  async generateFilmShot(command) {
    const generated = `mock://film/${command.projectId}/${command.shotId}/${command.modelId}.mp4`;
    filmStudio = {
      ...filmStudio,
      shots: filmStudio.shots.map((shot) => shot.shotId === command.shotId
        ? { ...shot, providerId: command.providerId, modelId: command.modelId, candidates: [generated], selectedAssetUrl: generated, qcStatus: "review" }
        : shot),
    };
    return commandDelay(650, filmStudio);
  },

  async queryFilmMedia() {
    return commandDelay(120, filmStudio);
  },

  async pollFilmShot(command) {
    filmStudio = {
      ...filmStudio,
      shots: filmStudio.shots.map((shot) => shot.shotId === command.shotId
        ? { ...shot, qcStatus: "review" }
        : shot),
    };
    return commandDelay(150, filmStudio);
  },

  async rerunFilmWorkflowNode(command) {
    filmStudio = {
      ...filmStudio,
      runPlan: filmStudio.runPlan.map((node) =>
        node.nodeId === command.nodeId
          ? { ...node, status: "pending" as const }
          : node),
    };
    return commandDelay(100, filmStudio);
  },

  async saveFilmGraph(command) {
    const nextRevision = Math.max(
      filmGraph.definition.revision + 1,
      command.expectedRevision + 1,
    );
    filmGraph = {
      definition: {
        ...command.graph,
        projectId: command.projectId,
        revision: nextRevision,
        updatedAt: new Date().toISOString(),
      },
      validationIssues: [],
      latestRun: filmGraphRuns[0] ?? null,
    };
    return commandDelay(120, filmGraph);
  },

  async validateFilmGraph() {
    return commandDelay(60, filmGraph.validationIssues);
  },

  async optimizeFilmNodePrompt(command) {
    const definition = mockFilmNodeCatalog.find((item) => item.typeId === command.node.typeId);
    const template = definition?.promptTemplate ?? command.node.prompt;
    const current = new Map(command.node.prompt.sections.map((section) => [section.sectionId, section]));
    const sections = template.sections.map((section) => {
      const existing = current.get(section.sectionId);
      return existing === undefined ? section : {
        ...existing,
        label: section.label,
        guidance: section.guidance,
        content: existing.content.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim(),
        required: section.required,
        sources: section.sources,
      };
    });
    const prompt = {
      ...command.node.prompt,
      templateId: template.templateId,
      templateVersion: template.templateVersion,
      purpose: template.purpose,
      sections,
      updatedAt: new Date().toISOString(),
    };
    const renderedPrompt = [
      ...prompt.sections.map((section) => `【${section.label}】\n${section.content}`),
      ...(prompt.userNotes.trim() ? [`【用户补充】\n${prompt.userNotes.trim()}`] : []),
    ].join("\n\n");
    return commandDelay(80, {
      prompt,
      renderedPrompt,
      changes: ["已按后端节点模板整理段落顺序与格式。"],
      warnings: [],
      characterCount: renderedPrompt.length,
    });
  },

  async estimateFilmGraphRun(command) {
    return commandDelay(
      90,
      mockFilmRunEstimate(command.scope, command.targetNodeIds),
    );
  },

  async createFilmGraphRun(command) {
    const now = new Date().toISOString();
    const estimate = mockFilmRunEstimate(command.scope, command.targetNodeIds);
    const run: FilmGraphRunView = {
      runId: `mock-film-run-${Date.now().toString(36)}`,
      projectId: command.projectId,
      graphId: filmGraph.definition.graphId,
      graphRevision: filmGraph.definition.revision,
      scope: command.scope,
      targetNodeIds: estimate.targetNodeIds,
      highPriority: command.highPriority,
      status: estimate.requiresConfirmation && !command.confirmedCost
        ? "waiting_confirmation"
        : "queued",
      confirmedCost: command.confirmedCost,
      estimate,
      attempts: estimate.executionNodeIds.map((nodeId, index) => ({
        attemptId: `mock-attempt-${Date.now().toString(36)}-${index}`,
        runId: "",
        nodeId,
        attempt: 1,
        status: "queued",
        inputSignature: `mock:${filmGraph.definition.revision}:${nodeId}`,
        providerTaskId: "",
        artifactUris: [],
        costUsd: 0,
        errorCode: "",
        errorMessage: "",
        startedAt: "",
        finishedAt: "",
      })),
      createdAt: now,
      updatedAt: now,
    };
    const attempts = run.attempts.map((attempt) => ({
      ...attempt,
      runId: run.runId,
    }));
    const saved = { ...run, attempts };
    filmGraphRuns = [saved, ...filmGraphRuns];
    filmGraph = { ...filmGraph, latestRun: saved };
    return commandDelay(140, saved);
  },

  async cancelFilmGraphRun(command) {
    const existing = filmGraphRuns.find((run) => run.runId === command.runId);
    if (existing === undefined) {
      throw new Error(`Unknown mock film run: ${command.runId}`);
    }
    const cancelled: FilmGraphRunView = {
      ...existing,
      status: "cancelled",
      attempts: existing.attempts.map((attempt) =>
        attempt.status === "queued" || attempt.status === "running"
          ? { ...attempt, status: "cancelled" }
          : attempt),
      updatedAt: new Date().toISOString(),
    };
    filmGraphRuns = filmGraphRuns.map((run) =>
      run.runId === command.runId ? cancelled : run);
    filmGraph = { ...filmGraph, latestRun: cancelled };
    return commandDelay(90, cancelled);
  },

  async exportFilmTimeline(command) {
    return commandDelay(160, {
      path: `data/${command.projectId}/film/exports/timeline.otio`,
      format: "OpenTimelineIO" as const,
    });
  },

  async materializeFilmMedia(command) {
    const artifact = {
      artifactId: `mock-artifact-${command.targetId}`,
      kind: command.targetId.startsWith("asset") ? "image" as const : "video" as const,
      subjectRef: command.targetId,
      sourceUrl: "",
      localPath: `data/${command.projectId}/film/media/${command.targetId}-mock.mp4`,
      checksum: "0".repeat(64),
      sizeBytes: 1_204_800,
      width: 1920,
      height: 1080,
      durationS: 6,
      fps: 24,
      codec: "h264",
      hasAudio: false,
      providerId: "bailian",
      taskId: "",
      createdAt: new Date().toISOString(),
    };
    filmStudio = {
      ...filmStudio,
      mediaArtifacts: [...filmStudio.mediaArtifacts.filter((item) => item.subjectRef !== command.targetId), artifact],
      jobs: [...filmStudio.jobs, { jobId: `mock-job-mat-${command.targetId}`, idempotencyKey: `materialize:${command.targetId}::`, kind: "materialize", targetId: command.targetId, providerId: "", modelId: "", state: "succeeded", attempts: 1, maxAttempts: 3, errorMessage: "", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() }],
    };
    return commandDelay(220, filmStudio);
  },

  async qcFilmShot(command) {
    const report = {
      targetId: command.shotId,
      targetType: "shot",
      passed: true,
      checks: [
        { name: "materialized", status: "pass" as const, detail: "本地素材已落盘" },
        { name: "duration_drift", status: "pass" as const, detail: "实际 6.0s / 计划 6.0s", metric: 0 },
        { name: "resolution", status: "pass" as const, detail: "1920x1080" },
        { name: "black_frames", status: "pass" as const, detail: "黑帧正常", metric: 0.01 },
      ],
      createdAt: new Date().toISOString(),
    };
    filmStudio = {
      ...filmStudio,
      qcReports: [...filmStudio.qcReports.filter((item) => item.targetId !== command.shotId), report],
      visionQcReports: [
        ...filmStudio.visionQcReports.filter((item) => item.targetId !== command.shotId),
        {
          targetId: command.shotId,
          targetType: "shot",
          passed: true,
          overallScore: 8.2,
          dimensions: [
            { dimension: "composition", score: 8.5, note: "构图说明已写入镜头语言" },
            { dimension: "lighting", score: 8.0, note: "主光动机明确" },
            { dimension: "subject", score: 8.5, note: "叙事动作明确" },
            { dimension: "medium_consistency", score: 8.0, note: "媒介质感与全片一致" },
            { dimension: "identity_consistency", score: 8.0, note: "身份锁与状态时间线已覆盖" },
            { dimension: "style_consistency", score: 8.0, note: "风格主张已注入 prompt" },
          ],
          retriesUsed: 0,
          summary: "达到可交付线",
          createdAt: new Date().toISOString(),
        },
      ],
      shots: filmStudio.shots.map((shot) => shot.shotId === command.shotId ? { ...shot, qcStatus: "passed" } : shot),
    };
    return commandDelay(260, filmStudio);
  },

  async batchGenerateFilmShots(command) {
    const targets = command.shotIds.length > 0
      ? command.shotIds
      : filmStudio.shots.filter((shot) => shot.selectedAssetUrl.length === 0).map((shot) => shot.shotId);
    filmStudio = {
      ...filmStudio,
      shots: filmStudio.shots.map((shot) => targets.includes(shot.shotId)
        ? { ...shot, candidates: [`mock://film/${command.projectId}/${shot.shotId}.mp4`], selectedAssetUrl: `mock://film/${command.projectId}/${shot.shotId}.mp4`, qcStatus: "review" }
        : shot),
    };
    return commandDelay(600, filmStudio);
  },

  async renderFilmMaster(command) {
    filmStudio = {
      ...filmStudio,
      delivery: {
        projectId: command.projectId,
        title: filmStudio.projectTitle,
        generatedAt: new Date().toISOString(),
        masterVideoPath: `data/${command.projectId}/film/exports/delivery/master.mp4`,
        masterAudioPath: `data/${command.projectId}/film/exports/delivery/dialogue_mix.wav`,
        subtitlePath: `data/${command.projectId}/film/exports/delivery/master.srt`,
        otioPath: `data/${command.projectId}/film/exports/timeline.otio`,
        durationS: filmStudio.shots.reduce((total, shot) => total + shot.durationS, 0),
        width: 1920,
        height: 1080,
        frameRate: 24,
        shotCount: filmStudio.shots.filter((shot) => shot.selectedAssetUrl.length > 0).length,
        clipPaths: [],
        qcPassed: true,
        compliancePassed: filmStudio.complianceReport?.passed ?? false,
        complianceReportPath: `data/${command.projectId}/film/compliance/report.json`,
        notes: [],
      },
    };
    return commandDelay(480, filmStudio);
  },

  async cancelFilmJob(command) {
    filmStudio = {
      ...filmStudio,
      jobs: filmStudio.jobs.map((job) => job.jobId === command.jobId ? { ...job, state: "cancelled" as const } : job),
    };
    return commandDelay(100, filmStudio);
  },

  async planDrama(command) {
    const beats = [
      { episodeNumber: 1, position: "end" as const, strategy: "cliffhanger_reversal", description: "首集结尾悬念卡点" },
      {
        episodeNumber: Math.min(8, command.totalEpisodes),
        position: "end" as const,
        strategy: "identity_reveal",
        description: "付费集身份揭晓卡点",
      },
    ];
    dramaStudio = {
      ...emptyDramaStudio(command.projectId),
      title: command.title,
      language: command.language,
      seriesPlan: {
        title: command.title,
        genre: command.genre,
        logline: command.logline,
        totalEpisodes: command.totalEpisodes,
        episodeDurationS: command.episodeDurationS,
        threeActs: ["建置与钩子", "对抗与反转", "高潮与收束"],
        paywallBeats: beats,
        thrillMatrix: beats.map((beat, index) => ({
          episodeNumber: beat.episodeNumber,
          kind: "身份反转",
          intensity: 5 + index,
          description: beat.description,
        })),
        waveformStages: ["开局钩子", "中段反转", "高潮对决", "收尾钩子"].map((stage, index) => {
          const span = Math.max(1, Math.floor(command.totalEpisodes / 4));
          const start = index * span + 1;
          const end = index === 3 ? command.totalEpisodes : Math.min((index + 1) * span, command.totalEpisodes);
          return { stage, episodeStart: start, episodeEnd: Math.max(start, end), intensityTarget: 4 + index * 2, note: "" };
        }),
        antagonistSystem: [
          { name: "表层对手", tier: "surface" as const, function: "日常冲突", characterRef: command.characterRoster[0] ?? "" },
          { name: "中层黑手", tier: "mid" as const, function: "阶段性压制", characterRef: "" },
          { name: "幕后主使", tier: "boss" as const, function: "终极对决", characterRef: "" },
        ],
      },
      runPlan: beats.map((beat) => ({
        nodeId: `drama_paywall:ep${beat.episodeNumber}`,
        label: `付费卡点 第${beat.episodeNumber}集 ${beat.strategy}`,
        stage: "planning",
        humanCheckpoint: true,
        notes: beat.description,
      })),
      timelineMarkers: beats.map((beat) => ({
        name: `paywall:ep${beat.episodeNumber}:${beat.strategy}`,
        timeS: (beat.episodeNumber - 1) * command.episodeDurationS + command.episodeDurationS * 0.9,
        color: "crimson",
        metadata: { episodeNumber: beat.episodeNumber, position: beat.position },
      })),
    };
    return commandDelay(500, dramaStudio);
  },

  async expandDramaOutlines(command) {
    const plan = dramaStudio.seriesPlan;
    const total = plan?.totalEpisodes ?? command.end;
    const outlines = [];
    for (let number = command.start; number <= Math.min(command.end, total); number += 1) {
      const beat = plan?.paywallBeats.find((item) => item.episodeNumber === number);
      outlines.push({
        episodeNumber: number,
        title: `第${number}集`,
        summary: `第${number}集：冲突升级并完成一次反转。`,
        waveformStage: plan?.waveformStages.find(
          (stage) => number >= stage.episodeStart && number <= stage.episodeEnd,
        )?.stage ?? "",
        paywallMarker: beat ? `${beat.strategy}@${beat.position}` : "",
        scenes: [1, 2, 3, 4].map((sceneNumber) => ({
          sceneNumber,
          summary: `场次${sceneNumber}：冲突推进与反转铺垫。`,
          location: "主场景",
          emotionalBeat: sceneNumber === 1 ? "压抑→爆发" : "反转",
        })),
      });
    }
    const merged = [...dramaStudio.outlines.filter((item) => item.episodeNumber < command.start || item.episodeNumber > command.end), ...outlines]
      .sort((left, right) => left.episodeNumber - right.episodeNumber);
    dramaStudio = { ...dramaStudio, outlines: merged };
    return commandDelay(420, dramaStudio);
  },

  async writeDramaScreenplay(command) {
    const scenes = mockDramaScreenplayScenes();
    const outline = dramaStudio.outlines.find((item) => item.episodeNumber === command.episodeNumber);
    const screenplay = {
      episodeNumber: command.episodeNumber,
      title: outline?.title ?? `第${command.episodeNumber}集`,
      scenes,
    };
    dramaStudio = {
      ...dramaStudio,
      screenplays: [
        ...dramaStudio.screenplays.filter((item) => item.episodeNumber !== command.episodeNumber),
        screenplay,
      ].sort((left, right) => left.episodeNumber - right.episodeNumber),
    };
    return commandDelay(480, dramaStudio);
  },

  async exportDramaPackage(command) {
    const plan = dramaStudio.seriesPlan;
    const issues: string[] = [];
    if (plan === null) {
      issues.push("missing_series_plan");
    } else {
      for (const beat of plan.paywallBeats) {
        const outline = dramaStudio.outlines.find((item) => item.episodeNumber === beat.episodeNumber);
        if (outline === undefined) {
          issues.push(`paywall_episode_missing_outline:${beat.episodeNumber}`);
        }
      }
    }
    const result: DramaExportResult = {
      projectId: command.projectId,
      title: dramaStudio.title,
      totalEpisodes: plan?.totalEpisodes ?? 0,
      outlinedEpisodes: dramaStudio.outlines.map((item) => item.episodeNumber),
      screenplayEpisodes: dramaStudio.screenplays.map((item) => item.episodeNumber),
      paywallMarkers: dramaStudio.timelineMarkers.map((marker) => marker.name),
      auditIssues: issues,
      gatePassed: issues.length === 0,
      artifacts: dramaStudio.screenplays.map(
        (item) => `data/${command.projectId}/film/drama/export/episode_${String(item.episodeNumber).padStart(3, "0")}.md`,
      ),
    };
    return commandDelay(320, result);
  },

  async planComicPages(command) {
    comicStudio = {
      ...emptyComicStudio(command.projectId),
      title: "演示漫画",
      format: command.format,
      pages: mockComicPages(command.format),
      sourceRevision: "screenplay_v1",
    };
    return commandDelay(420, comicStudio);
  },

  async exportComicPackage(command) {
    const panelCount = comicStudio.pages.reduce(
      (sum, page) => sum + page.panels.length,
      0,
    );
    const result: ComicExportResult = {
      exportDir: `data/${command.projectId}/comic/export`,
      pagesPath: `data/${command.projectId}/comic/export/comic_pages.json`,
      previewPath: `data/${command.projectId}/comic/export/comic_preview.md`,
      manifestPath: `data/${command.projectId}/comic/export/delivery_manifest.json`,
      panelCount,
      auditIssues: [],
    };
    return commandDelay(320, result);
  },
};

export const mockEngineClient: EngineClient = {
  getWorkspace: () => delay(workspace),
  listJobs: () => delay(jobs),
  getSettings: () => delay(settings),
  getOllama: () => delay(mockOllama),
  getWorkflow: () => delay(workflow),
  getErrorArchiveSummary: () => delay(errorArchiveSummary),
  getInitManualRepair: (projectId): Promise<InitManualRepairView> => delay({
    projectId,
    available: false,
    artifact: "",
    artifactLabel: "",
    artifactPath: "",
    payload: null,
    revision: "",
    summary: "演练数据未提供可人工修复的初始化产物。",
    issues: [],
  }),
  getProjectReader: (projectId) => delay(projectReaders[projectId] ?? projectReaders["test-long"]!),
  getChapterStudio: () => delay(chapterStudio),
  getVoiceStudio: (projectId, chapterNumber) => {
    const project = workspace.projects.find((item) => item.id === projectId);
    const selectedChapterNumber = chapterNumber ?? voiceStudio.chapterNumber;
    const cast = voiceStudio.cast.map((member) => {
      const selectedId = mockVoiceCatalogSelections.get(`${projectId}:${member.id}`);
      const selected = mockVoiceCatalog.find((item) => item.id === selectedId);
      return selected === undefined
        ? member
        : {
            ...member,
            statusLabel: "待试听确认",
            voiceId: selected.id,
            voiceLabel: selected.label,
            voiceSourceLabel: "人工指定",
            matchSummary: "人工分配 / 未评估",
            matchReasons: ["使用作者明确指定的系统音色"],
            auditionWarnings: ["请通过试听人工确认角色特质与指定音色是否一致"],
          };
    });
    return delay({
      ...voiceStudio,
      projectId,
      projectTitle: project?.title ?? voiceStudio.projectTitle,
      chapterNumber: selectedChapterNumber,
      cast,
      script: mockVoiceScripts.get(mockVoiceRoomTakeKey(projectId, selectedChapterNumber)) ?? voiceStudio.script,
      roomTakes: mockVoiceRoomTakes.get(mockVoiceRoomTakeKey(projectId, selectedChapterNumber)) ?? voiceStudio.roomTakes,
    });
  },
  getFilmStudio: (projectId) => {
    const project = workspace.projects.find((item) => item.id === projectId);
    return delay({
      ...filmStudio,
      projectId,
      projectTitle: project?.title ?? filmStudio.projectTitle,
      productionBible: {
        ...filmStudio.productionBible,
        projectId,
        title: project?.title ?? filmStudio.productionBible.title,
      },
    });
  },
  getFilmProviderCatalog: () => delay(filmProviderCatalog),
  getFilmGraph: (projectId) => {
    if (filmGraph.definition.projectId !== projectId) {
      filmGraph = {
        definition: createMockFilmGraph(projectId),
        validationIssues: [],
        latestRun: null,
      };
      filmGraphRuns = [];
    }
    return delay(filmGraph);
  },
  getFilmNodeCatalog: () => delay(mockFilmNodeCatalog),
  listFilmGraphRuns: (projectId) => delay(
    filmGraphRuns.filter((run) => run.projectId === projectId),
  ),
  getFilmGraphRun: (projectId, runId) => {
    const run = filmGraphRuns.find(
      (item) => item.projectId === projectId && item.runId === runId,
    );
    if (run === undefined) throw new Error(`Unknown mock film run: ${runId}`);
    return delay(run);
  },
  getDramaStudio: (projectId) => delay({ ...dramaStudio, projectId }),
  getComicStudio: (projectId) => delay({ ...comicStudio, projectId }),
  getComicAudit: () =>
    delay({ gatePassed: comicStudio.pages.length > 0, issues: [] } as ComicAuditResult),
  getVoiceCatalog: (projectId) => delay({
    projectId,
    providerLabel: voiceStudio.providerLabel,
    options: mockVoiceCatalog,
  }),
  getVoicePlatformRuntimes: () => delay(mockVoiceAudioModelCenter.runtimes),
  getVoiceAudioModelCenter: () => delay(mockVoiceAudioModelCenter),
  startVoiceAudioModelOperation: (request) => {
    const operationId = `mock-audio-${request.targetKind}-${request.targetId}-${Date.now()}`;
    const record: VoiceAudioModelOperationView = {
      id: operationId,
      targetKind: request.targetKind,
      targetId: request.targetId,
      operation: request.operation,
      status: "queued",
      message: "等待模型中心队列…",
      percent: -1,
    };
    mockVoiceAudioOperations.set(operationId, record);
    window.setTimeout(() => {
      const current = mockVoiceAudioOperations.get(operationId);
      if (current === undefined || current.status === "cancelled") return;
      mockVoiceAudioOperations.set(operationId, {
        ...current,
        status: "running",
        message: "正在准备下载…",
        percent: 30,
      });
    }, 60);
    window.setTimeout(() => completeMockVoiceAudioOperation(operationId, request), 180);
    return delay(record);
  },
  getVoiceAudioModelOperation: (operationId) => delay(
    mockVoiceAudioOperations.get(operationId) ?? {
      id: operationId,
      targetKind: "model",
      targetId: "",
      operation: "install",
      status: "failed",
      message: "未找到模型中心任务。",
      percent: -1,
    },
  ),
  cancelVoiceAudioModelOperation: (operationId) => {
    const current = mockVoiceAudioOperations.get(operationId);
    const cancelled: VoiceAudioModelOperationView = current === undefined
      ? {
        id: operationId,
        targetKind: "model",
        targetId: "",
        operation: "install",
        status: "failed",
        message: "未找到模型中心任务。",
        percent: -1,
      }
      : { ...current, status: "cancelled", message: "下载已取消。", percent: -1 };
    mockVoiceAudioOperations.set(operationId, cancelled);
    return delay(cancelled);
  },
  getNarrativeTools: () => delay(narrativeTools),
  getRelationshipOverview: () => delay(narrativeTools.relationshipOverview ?? { totalRelationships: 0, highTensionPairs: [], timelines: [] }),
  getTaskStream: (taskId) => delay(
    mockCancelledTaskIds.has(taskId)
      ? {
          ...taskStream,
          taskId,
          title: "任务观察",
          stepId: "cancelled",
          stepLabel: "任务已取消",
          status: "failed",
          jobState: "failed",
          error: {
            code: "cancelled",
            message: `任务 ${taskId} 已取消。`,
            retryable: false,
          },
        }
      : taskId === taskStream.taskId
        ? taskStream
        : { ...taskStream, taskId, title: "任务观察" },
  ),
  subscribeTaskStream: (taskId, listener) => {
    if (taskId !== taskStream.taskId) {
      return () => undefined;
    }
    const tail = taskStreamEvents.slice(4);
    const timers = tail.map((event, index) => window.setTimeout(() => listener(event), 850 + index * 850));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  },
  getStepArtifacts: (projectId, kind, stepKey, _chapterNumber) => delay({
    projectId,
    kind,
    stepKey,
    artifacts: stepArtifactFixtures(projectId, kind, stepKey),
    candidatePaths: [],
    emptyHint:
      stepArtifactFixtures(projectId, kind, stepKey).length > 0
        ? ""
        : `该步骤尚无可查看的产出文件。任务完成后此处将展示相关内容。`,
  }),
};

/**
 * Deterministic step-artifact fixtures so clicking a completed step opens a
 * nicely rendered artifact instead of an empty surface. Keys mirror the
 * StepIndicatorRow contract keys (INIT_LONG_STEPS / RUN_SHORT_STEPS).
 */
function stepArtifactFixtures(
  projectId: string,
  kind: string,
  stepKey: string,
): readonly StepArtifactFile[] {
  const project = (path: string) => `projects/${projectId}/${path}`;
  const chapterArtifact = (
    label: string,
    path: string,
    content: string,
    format: StepArtifactFile["format"] = "markdown",
  ): StepArtifactFile => ({ label, path: project(path), format, content });
  const fixtures: Readonly<Record<
    string,
    Readonly<Record<string, readonly StepArtifactFile[]>>
  >> = {
  init_long: {
    spec: [
      {
        label: "spec.md",
        path: project("spec.md"),
        format: "markdown",
        content: `# 故事规格

## 核心前提

记忆回收师在废弃档案库中唤醒一段被刻意抹除的往事。

## 题材与基调

- 题材：科幻
- 基调：沉郁悬疑

> 本文件为前端 fixture，真实产物由 Engine 在任务完成后写入。`,
      },
    ],
    init_story_bible: [
      {
        label: "story_bible.md",
        path: project("story_bible.md"),
        format: "markdown",
        content: `# 世界观设定

## 世界规则

1. 记忆可被物理归档与回收。
2. 回收的记忆片段只能读回一次。

## 势力

| 势力 | 立场 | 说明 |
| --- | --- | --- |
| 档案库 | 中立 | 保管所有回收记忆 |
| 回收师工会 | 秩序 | 执行记忆回收 |
`,
      },
    ],
    plan_blueprint: [
      {
        label: "blueprint.json",
        path: project("blueprint.json"),
        format: "json",
        content: JSON.stringify(
          {
            blueprint: {
              title: "青瓦梦起",
              arcs: [
                { id: "arc-1", label: "觉醒", beats: 6 },
                { id: "arc-2", label: "追索", beats: 8 },
              ],
            },
            status: "completed",
          },
          null,
          2,
        ),
      },
    ],
    plan_outline: [
      {
        label: "outline.md",
        path: project("outline.md"),
        format: "markdown",
        content: `# 章节大纲

## 第 1 章 档案库的黎明

- 主线：记忆回收师收到第一份异常档案
- 伏笔：档案编号 000 缺失
`,
      },
    ],
  },
  run_short: {
    spec: [
      {
        label: "spec.md",
        path: project("spec.md"),
        format: "markdown",
        content: `# 短篇规格

一个关于遗物整理的短篇故事。`,
      },
    ],
    draft: [
      {
        label: "draft.md",
        path: project("draft.md"),
        format: "markdown",
        content: `# 初稿

夜色漫过档案柜，灯光在旧纸边缘留下淡影。`,
      },
    ],
  },
  run_chapter: {
    state_packet: [chapterArtifact("chapter_005_state_packet.json", "states/chapter_005_state_packet.json", JSON.stringify({ chapter: 5, carry_forward: ["授权链未验证", "林小满在外部监控"] }, null, 2), "json")],
    bridge: [chapterArtifact("chapter_005_bridge.json", "plans/chapter_005_bridge.json", "# 章节桥接\n\n承接被擦除的时间戳，推进沈岸进入档案室的选择。")],
    plan: [chapterArtifact("chapter_005_plan.json", "plans/chapter_005_plan.json", JSON.stringify({ goal: "取得可验证的授权链证据", scenes: 3, opening_anchor: "门禁记录显示从未授权" }, null, 2), "json")],
    draft: [chapterArtifact("v0_draft.md", "drafts/chapter_005/v0_draft.md", "# DRAFT 草稿\n\n档案室的门禁灯在雨声里闪烁，沈岸停在最后一道锁前。")],
    wave: [chapterArtifact("v1_wave.md", "drafts/chapter_005/v1_wave.md", "# WAVE 编织稿\n\n档案室的门禁灯在雨声里闪烁。沈岸停在最后一道锁前，林小满在耳机里报出保安的脚步。")],
    opening_guard: [chapterArtifact("chapter_005_guard.json", "reports/chapter_005_guard.json", JSON.stringify({ opening_anchor: "已承接", forbidden_reveal: "未泄露操作者身份" }, null, 2), "json")],
    alignment: [chapterArtifact("chapter_005_alignment.json", "reports/chapter_005_alignment.json", JSON.stringify({ score: 8.7, status: "passed", note: "本章目标与大纲一致" }, null, 2), "json")],
    continuity_repair: [chapterArtifact("chapter_005_continuity_repair.json", "reports/revisions/chapter_005_continuity_repair.json", JSON.stringify({ repaired: ["补足林小满的外部监控职责"] }, null, 2), "json")],
    alignment_repair: [chapterArtifact("chapter_005_alignment_repair.json", "reports/revisions/chapter_005_alignment_repair.json", JSON.stringify({ repaired: ["收束授权链的解释范围"] }, null, 2), "json")],
    guard_review: [chapterArtifact("chapter_005_quality_gate.json", "reports/chapter_005_quality_gate.json", JSON.stringify({ status: "passed", high_severity: 0 }, null, 2), "json")],
    causal_repair: [chapterArtifact("chapter_005_causal.json", "reports/chapter_005_causal.json", JSON.stringify({ score: 8.4, repaired: ["保留调用记忆片段的代价"] }, null, 2), "json")],
    reading_power_repair: [chapterArtifact("chapter_005_reading_power.json", "reports/chapter_005_reading_power.json", JSON.stringify({ score: 8.8, hook: "授权链记录与现实权限相悖" }, null, 2), "json")],
    polish: [chapterArtifact("chapter_005_polish.md", "reports/revisions/chapter_005_polish_chapter.md", "# 文学精修\n\n压缩解释性语句，保留门禁灯与脚步声的紧张间隔。")],
    humanize: [chapterArtifact("chapter_005_humanize.json", "reports/chapter_005_humanize.json", JSON.stringify({ cleaned_patterns: ["同构句", "泛化总结"], status: "passed" }, null, 2), "json")],
    extract_canon: [chapterArtifact("chapter_005_state_adjudication.json", "reports/chapter_005_state_adjudication.json", JSON.stringify({ accepted_deltas: ["沈岸获得受限授权链", "非法记忆片段尚未调用"] }, null, 2), "json")],
    persist: [chapterArtifact("chapter_005.md", "chapters/chapter_005.md", "# 第五章 档案室\n\n门禁记录显示沈岸从未拥有访问权限，而他掌心的临时令牌正在发热。")],
    memory_updated: [chapterArtifact("chapter_005_memory_diagnostics.json", "reports/chapter_005_memory_diagnostics.json", JSON.stringify({ episodic_memory: "updated", motif: "时间戳" }, null, 2), "json")],
  },
};

  return fixtures[kind]?.[stepKey] ?? [];
}

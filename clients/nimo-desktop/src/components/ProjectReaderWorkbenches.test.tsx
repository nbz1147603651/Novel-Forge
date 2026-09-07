import { renderToStaticMarkup } from "react-dom/server";

import type { EngineCommandClient, ProjectReaderArtifactView, RelationshipOverviewView } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import {
  ChapterReportBrowser,
  OutlineSessionWorkbench,
  TokenAnalyticsWorkbench,
  chapterReportTypeLabel,
  createHumanizeSelectionDraft,
  humanizePatternFromSelection,
} from "./ProjectReaderWorkbenches";
import { ChapterReader, ProjectsReader, RelationshipEvolutionView } from "./ProjectsReader";

const qualityReport: ProjectReaderArtifactView = {
  id: "chapter-1-report-quality",
  label: "第 1 章 · 质量评估",
  caption: "质量评估",
  sourceLabel: "reports/chapter_001_eval.json",
  paragraphs: ["节奏稳定，线索推进清晰。"],
  facts: [{ label: "总分", value: "8.6" }],
  content: JSON.stringify({ summary: "节奏稳定", score: 8.6 }),
  format: "json",
};

const humanizeDiffReport: ProjectReaderArtifactView = {
  id: "chapter-4-report-humanize-layer",
  label: "第 4 章 · 拟人化对比",
  caption: "拟人化对比",
  sourceLabel: "reports/revisions/chapter_004_humanize_layer.json",
  paragraphs: ["修订差异"],
  facts: [],
  format: "json",
  content: JSON.stringify({
    artifact_type: "text_revision_diff",
    label_before: "原文",
    label_after: "拟人化层输出",
    status: "accepted",
    patches_applied: 2,
    additions: 13,
    deletions: 13,
    similarity_ratio: 0.953,
    change_ratio: 0.047,
    hunks: [{ tag: "replace", a_text: "她显得很平静。", b_text: "她压平纸页，抬眼时才看见他。" }],
    metadata: { change_ratio_cap: 0.08 },
  }),
};

const tokenAnalytics: ProjectReaderArtifactView = {
  id: "token-analytics",
  label: "Token 追踪",
  caption: "项目调用与成本摘要",
  sourceLabel: "logs/（运行日志聚合）",
  paragraphs: [],
  facts: [],
  format: "json",
  content: JSON.stringify({
    total_tokens: 703400,
    total_prompt_tokens: 511200,
    total_completion_tokens: 192200,
    total_call_count: 42,
    logged_cost_usd: 1.06,
    run_count: 6,
    models: [{ key: "openai/gpt-4o-mini", display_name: "OpenAI · GPT-4o mini", calls: 42, tokens: 703400, cost_usd: 1.06 }],
    steps: [{ step: "波次编织", runs: 2, tokens: 247900, cost_usd: 0.38, kind_tokens: { init: 0, chapter: 247900, repair: 0 } }],
  }),
};

const relationshipOverview: RelationshipOverviewView = {
  totalRelationships: 1,
  highTensionPairs: [],
  timelines: [{
    pairId: "shen-an-lin-xiaoman",
    characterA: "沈岸",
    characterB: "林小满",
    currentStatus: "老板与助理",
    currentTrust: 0.62,
    currentTension: 0.38,
    snapshots: [{ chapter: 8, status: "合作出现裂痕", trust: 0.5, tension: 0.5, shift: "林小满隐瞒线索，沈岸开始怀疑她的动机。" }],
  }],
};

describe("ChapterReportBrowser", () => {
  it("groups catalog labels by their PySide report leaf caption", () => {
    expect(chapterReportTypeLabel(qualityReport)).toBe("质量评估");

    const html = renderToStaticMarkup(
      <ChapterReportBrowser
        artifacts={[qualityReport]}
        onOperation={() => undefined}
        projectTitle="梦侦探"
      />,
    );

    expect(html).toContain("章节报告阅读器");
    expect(html).toContain(">质量<");
    expect(html).toContain(">质量评估<");
    expect(html).not.toContain("本章暂无报告");
  });

  it("renders revision reports as the PySide-style inline reading diff instead of a raw key-value table", () => {
    const html = renderToStaticMarkup(
      <ChapterReportBrowser
        artifacts={[humanizeDiffReport]}
        onOperation={() => undefined}
        projectTitle="梦侦探"
      />,
    );

    expect(html).toContain("版本对比");
    expect(html).toContain("95.3%");
    expect(html).toContain("变更详情");
    expect(html).toContain("reader-text-diff-delete");
    expect(html).toContain("reader-text-diff-add");
    expect(html).toContain("变更统计");
    expect(html).not.toContain("similarity_ratio");
    expect(html).not.toContain("total_changes");
  });
});

describe("selected-text humanize library command payload", () => {
  it("keeps editable metadata with the full selection for the Engine-owned library", () => {
    const draft = createHumanizeSelectionDraft({
      chapter: 4,
      projectTitle: "梦侦探",
      selection: "  这并非偶然，而是一切的开始。  ",
    });
    const pattern = humanizePatternFromSelection({
      ...draft,
      keywords: "模板句式，否定式并列",
      severity: "high",
    }, "  这并非偶然，而是一切的开始。  ");

    expect(draft.name).toContain("第 4 章选段");
    expect(pattern).toEqual({
      name: draft.name,
      category: "正文选段",
      severity: "high",
      keywords: ["模板句式", "否定式并列"],
      notes: "来源：梦侦探 第 4 章终稿选段。",
      examplePhrase: "这并非偶然，而是一切的开始。",
      source: "user",
    });
  });
});

describe("OutlineSessionWorkbench", () => {
  it("distinguishes an 80-chapter goal from its first ten outlines and offers completion", () => {
    const html = renderToStaticMarkup(<OutlineSessionWorkbench
      commandClient={{} as EngineCommandClient} onOperation={() => undefined} projectId="book"
      planning={{ totalChapters: 80, hardThroughChapter: 5, plannedThroughChapter: 10 }}
      outline={Array.from({ length: 10 }, (_, index) => ({
        id: `outline-${index + 1}`, chapterNumber: index + 1, chapterLabel: `第 ${index + 1} 章`,
        title: "药证", summary: "待展开", stateLabel: index < 5 ? "待写" : "预规划",
      }))}
    />);
    expect(html).toContain("全书目标 80 章 · 已有大纲 10 章 · 已细化至第 5 章");
    expect(html).toContain("补齐全书规划");
    expect(html).toContain("不是全书章数上限");
    expect(html).not.toContain("当前大纲共 10 章");
  });

  it("does not offer completion when all chapters are committed", () => {
    const html = renderToStaticMarkup(<OutlineSessionWorkbench
      commandClient={{} as EngineCommandClient} onOperation={() => undefined} projectId="book"
      planning={{ totalChapters: 1, hardThroughChapter: 1, plannedThroughChapter: 1 }}
      outline={[{ id: "1", chapterNumber: 1, chapterLabel: "第 1 章", title: "结尾", summary: "完", stateLabel: "待写" }]}
    />);
    expect(html).not.toContain("补齐全书规划");
  });

  it("separates goal, numbered beats, and folded supporting constraints without repeating the summary", () => {
    const html = renderToStaticMarkup(<OutlineSessionWorkbench
      commandClient={{} as EngineCommandClient}
      onOperation={() => undefined}
      projectId="book"
      outline={[{
        id: "chapter-1", chapterNumber: 1, chapterLabel: "第 1 章", title: "门槛", stateLabel: "待写",
        summary: "['旧版列表表示不应再次展示']",
        goal: "把委托落实为行动。",
        beatsSummary: ["第一拍：核验门槛。", "第二拍：保留疑点。"],
        facts: [{ label: "视角", value: "沈岸" }],
        mainPlotPoints: ["接下委托"], subplotPoints: ["留存纸鹤"],
        sceneDesignGoals: ["不可提前揭示身份"], notes: "注意物证的出场顺序。",
      }]}
    />);
    expect(html).toContain('aria-label="章节目标"');
    expect(html).toContain('aria-label="叙事节拍"');
    expect(html).toContain("第一拍：核验门槛。");
    expect(html).toContain("第二拍：保留疑点。");
    expect(html.match(/outline-beat-index/g)).toHaveLength(2);
    expect(html.match(/<details>/g)).toHaveLength(4);
    expect(html).not.toContain("<details open");
    expect(html).not.toContain("旧版列表表示");
    expect(html).toContain("不可提前揭示身份");
    expect(html).toContain("沈岸");
  });

  it("renders an empty outline without inventing chapter content", () => {
    const html = renderToStaticMarkup(<OutlineSessionWorkbench commandClient={{} as EngineCommandClient} onOperation={() => undefined} outline={[]} projectId="empty" />);
    expect(html).toContain("暂无章节大纲");
    expect(html).not.toContain("outline-beat-index");
  });

  it("keeps the PySide contract-sync and outline-extension actions available", () => {
    const html = renderToStaticMarkup(
      <OutlineSessionWorkbench
        commandClient={{} as EngineCommandClient}
        onOperation={() => undefined}
        outline={[{
          id: "outline-1",
          chapterNumber: 1,
          chapterLabel: "第 1 章",
          title: "怀表倒转",
          summary: "沈岸循着怀表留下的裂痕进入第一层梦境。",
          stateLabel: "已完成",
        }]}
        projectId="梦侦探"
      />,
    );

    expect(html).toContain("同步契约");
    expect(html).toContain("延长全书");
    expect(html).toContain("沈岸循着怀表留下的裂痕");
  });
});

describe("TokenAnalyticsWorkbench", () => {
  it("renders the live log aggregate instead of a demonstration model row", () => {
    const html = renderToStaticMarkup(
      <TokenAnalyticsWorkbench
        artifact={tokenAnalytics}
        commandClient={{} as EngineCommandClient}
        onOperation={() => undefined}
        projectId="梦侦探"
      />,
    );

    expect(html).toContain("703.4k");
    expect(html).toContain("42");
    expect(html).toContain("实际费用");
    expect(html).toContain("拆分覆盖 100%");
    expect(html).toContain('aria-label="输入 73%，输出 27%"');
    expect(html).toContain("reader-token-step-track");
    expect(html).toContain("reader-token-donut");
    expect(html).toContain("模型计价覆盖");
    expect(html).toContain("主要消耗模型");
    expect(html).toContain("运行日志实付");
    expect(html).not.toContain("OpenAI Mini");
  });
});

describe("ProjectsReader", () => {
  it("keeps every PySide long-project top-level tab in the source order", () => {
    const html = renderToStaticMarkup(
      <ProjectsReader
        commandClient={{} as EngineCommandClient}
        isLoading={false}
        onSelectProject={() => undefined}
        projects={[]}
        reader={{
          projectId: "梦侦探",
          projectTitle: "梦侦探",
          modeLabel: "长篇项目",
          updatedLabel: "刚刚同步",
          tabs: [
            { id: "foundation", label: "基础设定", artifacts: [qualityReport] },
            { id: "research", label: "资料检索", artifacts: [] },
            { id: "blueprint", label: "叙事蓝图", artifacts: [] },
            { id: "chapter_design", label: "章节设计矩阵", artifacts: [] },
            { id: "outline", label: "章节大纲", artifacts: [] },
            { id: "chapters", label: "章节", artifacts: [] },
            { id: "governance", label: "治理", artifacts: [] },
            { id: "tracking", label: "追踪", artifacts: [] },
          ],
        }}
        tools={null}
      />,
    );

    expect(html).toContain("基础设定");
    expect(html).toMatch(/资料检索.*叙事蓝图.*支线管理.*章节设计矩阵.*章节大纲.*章节.*治理.*追踪/s);
    expect(html).toContain('title="章节设计矩阵"');
  });

  it("renders the PySide outline rail and opens a draft before final archive", () => {
    const html = renderToStaticMarkup(
      <ChapterReader
        artifacts={[{
          id: "chapter-1-draft-v_humanize_candidate",
          label: "章节草稿（v_humanize_candidate）",
          emptyLabel: "章节草稿",
          artifact: {
            id: "chapter-1-draft-v_humanize_candidate",
            label: "章节草稿（v_humanize_candidate）",
            caption: "章节草稿（v_humanize_candidate）",
            sourceLabel: "drafts/chapter_001/v_humanize_candidate.md",
            paragraphs: ["晨钟前两刻，黎明未至。"],
            facts: [],
            content: "晨钟前两刻，黎明未至。",
            format: "markdown",
          },
        }]}
        chapters={[
          { number: 1, title: "晨钟嗅香", state: "draft", wordCount: 1341 },
          { number: 2, title: "铜匙问诊", state: "pending" },
        ]}
        commandClient={{} as EngineCommandClient}
        onOperation={() => undefined}
        onUnsavedSessionChange={() => undefined}
        projectId="药证"
        projectTitle="药证"
      />,
    );

    expect(html).toContain("第 1 章 · 晨钟嗅香");
    expect(html).toContain("第 2 章 · 铜匙问诊");
    expect(html).toContain("草稿");
    expect(html).toContain("1,341 字");
    expect(html).toContain("待续写");
    expect(html).toContain("收起章节轨道");
    expect(html).toContain('aria-controls="reader-chapter-rail"');
    expect(html).toContain("晨钟前两刻，黎明未至。");
    expect(html).not.toContain("暂无章节数据");
  });
});

describe("RelationshipEvolutionView", () => {
  it("reuses the shared dropdown and renders complete metric tracks with a compact timeline", () => {
    const html = renderToStaticMarkup(<RelationshipEvolutionView overview={relationshipOverview} />);

    expect(html).toContain("dropdown-select relationship-character-select");
    expect(html).toContain('aria-haspopup="listbox"');
    expect(html).not.toContain("<select");
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="62"');
    expect(html).toContain("关系时间线");
    expect(html).toContain("信任 50% · 张力 50%");
  });
});

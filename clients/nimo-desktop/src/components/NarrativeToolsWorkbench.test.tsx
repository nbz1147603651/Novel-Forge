import { renderToStaticMarkup } from "react-dom/server";

import type { AuthoringSessionView, EngineCommandClient, NarrativeToolsView } from "@nimo/engine-contracts";
import { describe, expect, it, vi } from "vitest";

import { createNarrativeToolsSession } from "../lib/narrative-tools-session";
import { findNarrativeSessionMutation, NarrativeToolsWorkbench } from "./NarrativeToolsWorkbench";
import * as authoring from "./AuthoringWorkspace";
import { OutlineWorkbench } from "./narrative-tools/OutlineSubplotWorkbench";

const characterTools: NarrativeToolsView = {
  characters: [
    {
      id: "lin-zhu",
      name: "林逐",
      role: "主视角 · 记忆回收师",
      statusLabel: "活跃",
      summary: "持证记忆回收师。",
      arc: "从以流程自保到主动承担真相的代价。",
    },
    {
      id: "zhou-yan",
      name: "周砚",
      role: "调查官",
      statusLabel: "活跃",
      summary: "调查官。",
      arc: "在秩序与信任之间作出选择。",
    },
  ],
  characterDetails: [{
    characterId: "lin-zhu",
    timelineLabel: "当前线",
    ageLabel: "28",
    genderLabel: "女",
    occupation: "持证记忆回收师。",
    personality: "沉稳寡言。",
    backstory: "姐姐失踪后成为记忆回收师。",
    abilities: "记忆采集与证据链核验。",
    appearance: "短发，常穿灰色工装外套。",
    arc: "从以流程自保到主动承担真相的代价。",
    voice: "语速偏慢。",
    notes: "核心视角角色。",
  }],
  relationships: [{
    id: "lin-zhu-zhou-yan",
    fromCharacterId: "lin-zhu",
    toCharacterId: "zhou-yan",
    typeLabel: "同盟 · 不互信",
    evidence: "第 4 章：林逐隐瞒夜间时间戳。",
  }],
  visualization: {
    totalChapters: 0,
    phases: [],
    milestones: [],
    subplotLanes: [],
    weaveLinks: [],
  },
  outline: [],
  subplots: [],
  humanizePatterns: [],
  revisionCandidates: [],
};

describe("NarrativeToolsWorkbench character archive", () => {
  it("explains that extending a configured book creates a candidate, not a committed goal", () => {
    const method = vi.fn();
    const hook = vi.spyOn(authoring, "useAuthoring").mockReturnValue({
      session: { configured: true } as AuthoringSessionView,
      open: method, setLocation: method, propose: async () => {},
      entry: { visible: true, expanded: false, label: "AI 共创", toggle: method },
    });
    try {
      const html = renderToStaticMarkup(<OutlineWorkbench outline={[]} projectId="book" onAction={method} />);
      expect(html).toContain("专项批准前不改变全书目标或正式规划");
      expect(method).not.toHaveBeenCalled();
    } finally {
      hook.mockRestore();
    }
  });

  it("turns each locally confirmed character or relationship draft into one Engine mutation", () => {
    const current = createNarrativeToolsSession(characterTools);
    const changedCharacter = {
      ...current,
      characterDetails: current.characterDetails.map((detail) => detail.characterId === "lin-zhu"
        ? { ...detail, personality: "沉稳、警觉。" }
        : detail),
    };
    const savedCharacter = findNarrativeSessionMutation(current, changedCharacter);
    expect(savedCharacter).toMatchObject({ kind: "save_character", character: { id: "lin-zhu" } });

    const changedRelationship = {
      ...current,
      relationships: current.relationships.map((relationship) => ({ ...relationship, evidence: "第 4 章：共同隐瞒时间戳。" })),
    };
    const savedRelationship = findNarrativeSessionMutation(current, changedRelationship);
    expect(savedRelationship).toMatchObject({ kind: "save_relationship", relationship: { id: "lin-zhu-zhou-yan" } });

    const removedRelationship = findNarrativeSessionMutation(current, { ...current, relationships: [] });
    expect(removedRelationship).toMatchObject({ kind: "remove_relationship", relationship: { id: "lin-zhu-zhou-yan" } });
  });

  it("keeps relationships in the side inspector without duplicating unavailable fields in the dossier", () => {
    const html = renderToStaticMarkup(
      <NarrativeToolsWorkbench
        hideTabs
        onAction={() => undefined}
        sourceCharacterEditor
        tools={characterTools}
      />,
    );

    expect(html).toContain("character-roster-filters");
    expect(html).toContain("关系脉络");
    expect(html).toContain("1 条关联");
    expect(html).not.toContain("登场章节");
    expect(html).not.toContain("EngineClient 接入章节索引后回填");
    expect(html).not.toContain("character-inline-relationships");
  });

  it("keeps the reader timeline focused on the narrative blueprint", () => {
    const tools: NarrativeToolsView = {
      ...characterTools,
      blueprintRevision: "blueprint-1",
      visualization: {
        totalChapters: 12,
        phases: [{
          id: "phase-1",
          label: "引子",
          chapterStart: 1,
          chapterEnd: 4,
          tensionLabel: "递进",
          summary: "主线触发。",
          tone: "opening",
        }],
        milestones: [{ id: "m1", chapter: 3, label: "转折", description: "发现旧案。" }],
        subplotLanes: [],
        weaveLinks: [],
      },
      subplots: [{
        id: "subplot-1",
        title: "旧案证人线",
        priorityLabel: "常规",
        chaptersLabel: "第 3–12 章",
        description: "证人交出账本后，主线必须调整公开策略。",
        resolution: "第 12 章 · 悬念揭示",
        plan: {
          name: "旧案证人线",
          description: "证人交出账本后，主线必须调整公开策略。",
          involvedChapters: [3, 8, 12],
          chapterEvents: [{ chapterNumber: 3, event: "证人交出账本。", weaveNotes: "引入证据", dependsOn: [] }],
          weaveLinks: [],
          priority: "normal",
          resolutionChapter: 12,
          resolutionTarget: "公开账本",
          resolutionType: "reveal",
        },
      }],
    };

    const html = renderToStaticMarkup(
      <NarrativeToolsWorkbench
        commandClient={{} as EngineCommandClient}
        hideTabs
        initialTab="timeline"
        onAction={() => undefined}
        projectId="dream-detective"
        tools={tools}
        visibleTabs={["timeline"]}
      />,
    );

    expect(html).toContain('aria-label="叙事时间线"');
    expect(html).not.toContain('aria-label="支线管理"');
    expect(html).not.toContain("应用到章节大纲");
  });

  it("keeps editing and outline calibration in the expanded subplot workbench", () => {
    const tools: NarrativeToolsView = {
      ...characterTools,
      blueprintRevision: "blueprint-1",
      visualization: {
        totalChapters: 12,
        phases: [],
        milestones: [],
        subplotLanes: [],
        weaveLinks: [],
      },
      subplots: [{
        id: "subplot-1",
        title: "旧案证人线",
        priorityLabel: "常规",
        chaptersLabel: "第 3–12 章",
        description: "证人交出账本后，主线必须调整公开策略。",
        resolution: "第 12 章 · 悬念揭示",
        plan: {
          name: "旧案证人线",
          description: "证人交出账本后，主线必须调整公开策略。",
          involvedChapters: [3, 8, 12],
          chapterEvents: [{ chapterNumber: 3, event: "证人交出账本。", weaveNotes: "引入证据", dependsOn: [] }],
          weaveLinks: [],
          priority: "normal",
          resolutionChapter: 12,
          resolutionTarget: "公开账本",
          resolutionType: "reveal",
        },
      }],
    };

    const html = renderToStaticMarkup(
      <NarrativeToolsWorkbench
        commandClient={{} as EngineCommandClient}
        initialTab="subplots"
        onAction={() => undefined}
        projectId="dream-detective"
        tools={tools}
        visibleTabs={["subplots"]}
      />,
    );

    expect(html).toContain("应用到章节大纲");
    expect(html).toContain("AI 生成支线");
    expect(html).toContain("弧光转支线");
    expect(html).toContain("大纲影响");
    expect(html).toContain("Ch.3-12");
  });
});

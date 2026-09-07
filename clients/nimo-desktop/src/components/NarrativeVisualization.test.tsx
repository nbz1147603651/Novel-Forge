import { renderToStaticMarkup } from "react-dom/server";

import type { NarrativeVisualizationView } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import { NarrativeTimelineRenderer, timelineDetailFor, timelineLabelLines } from "./NarrativeVisualization";

const visualization: NarrativeVisualizationView = {
  totalChapters: 60,
  phases: [{
    id: "opening",
    label: "引入触发阶段",
    chapterStart: 1,
    chapterEnd: 15,
    tensionLabel: "逐步升级",
    summary: "引入世界观与核心冲突。",
    tone: "opening",
  }],
  milestones: [{ id: "m1", chapter: 1, label: "M1", description: "主线转折的完整说明。" }],
  subplotLanes: [{
    id: "subplot-a",
    label: "旧案残片",
    tone: "jade",
    events: [{ id: "subplot-a-event", chapter: 6, label: "残片浮现", description: "旧案残片首次揭示失踪者留下的真实动机。", emphasis: "reveal" }],
  }],
  weaveLinks: [{
    id: "weave-a",
    sourceLaneId: "subplot-a",
    target: "mainline",
    chapter: 6,
    type: "reveal_key",
    label: "线索汇入",
    description: "旧案残片推动主线转折。",
  }],
  characterArcs: [{
    id: "arc-a",
    character: "林逐",
    arcSummary: "从回避旧案走向直面真相。",
    milestones: [{ chapterStart: 1, chapterEnd: 6, description: "第一次主动追查。" }],
  }],
};

describe("NarrativeTimelineRenderer", () => {
  it("keeps the chapter count inside the canvas without a redundant overview header", () => {
    const html = renderToStaticMarkup(<NarrativeTimelineRenderer visualization={visualization} />);

    expect(html).toContain("timeline-total-chapters");
    expect(html).toContain("共 60 章");
    expect(html).toContain('aria-label="查看第 1 章转折：M1"');
    expect(html).toContain('aria-label="查看支线 旧案残片 第 6 章节点：残片浮现"');
    expect(html).toContain("角色弧光 · 1");
    expect(html).not.toContain('aria-label="查看林逐在 Ch.1–6 的角色弧光"');
    expect(html).toContain('aria-label="蓝图图层"');
    expect(html).not.toContain('aria-label="叙事阶段摘要"');
    expect(html).not.toContain("▼ 旧案残片");
    expect(html).not.toContain("<title>");
    expect(html).not.toContain("沿用 PySide 的章节坐标");
    expect(html).not.toContain("<h3>叙事总览</h3>");
  });

  it("keeps compact timeline labels while rendering full milestone and subplot descriptions", () => {
    const milestoneHtml = renderToStaticMarkup(
      <NarrativeTimelineRenderer visualization={{ ...visualization, phases: [] }} />,
    );

    expect(milestoneHtml).toContain("主线转折的完整说明。");
    expect(timelineDetailFor(visualization, "m1")?.description).toBe("主线转折的完整说明。");
    expect(timelineDetailFor(visualization, "subplot-a-event")?.description).toBe(
      "旧案残片首次揭示失踪者留下的真实动机。",
    );
  });

  it("wraps full subplot names and exposes the whole lane to selection", () => {
    const label = "纸鹤盲区与势力权限继承之间的秘密";
    const data = { ...visualization, subplotLanes: [{ ...visualization.subplotLanes[0]!, label }] };
    const html = renderToStaticMarkup(<NarrativeTimelineRenderer visualization={data} />);
    expect(timelineLabelLines(label).join("")).toBe(label);
    expect(timelineLabelLines("短名")).toEqual(["短名"]);
    expect(html).toContain(`aria-label="查看支线：${label}"`);
    expect(html).toContain("<tspan");
    expect(timelineDetailFor(data, "subplot-a")?.title).toBe(label);
    expect(timelineDetailFor(data, "subplot-a")?.description).toContain("第 6 章");
  });

  it("supports persistent arc selection instead of hover-only details", () => {
    const html = renderToStaticMarkup(<NarrativeTimelineRenderer visualization={{ ...visualization, subplotLanes: [] }} />);
    expect(html).toContain('aria-label="查看林逐在 Ch.1–6 的角色弧光"');
    expect(timelineDetailFor(visualization, "arc-a-ms-0")?.description).toBe("第一次主动追查。");
    expect(timelineDetailFor(visualization, "arc-a")?.description).toBe("从回避旧案走向直面真相。");
  });

  it("falls back to compact labels while an older engine response has no descriptions", () => {
    const legacyVisualization: NarrativeVisualizationView = {
      ...visualization,
      phases: [],
      milestones: [{ id: "legacy-m1", chapter: 1, label: "旧版主线摘要" }],
      subplotLanes: [{
        ...visualization.subplotLanes[0]!,
        events: [{ id: "legacy-event", chapter: 6, label: "旧版支线摘要" }],
      }],
    };

    expect(timelineDetailFor(legacyVisualization, "legacy-m1")?.description).toBe("旧版主线摘要");
    expect(timelineDetailFor(legacyVisualization, "legacy-event")?.description).toBe("旧版支线摘要");
  });

  it("renders an explicit empty state instead of a zero-chapter timeline skeleton", () => {
    const html = renderToStaticMarkup(
      <NarrativeTimelineRenderer visualization={{
        totalChapters: 0,
        phases: [],
        milestones: [],
        subplotLanes: [],
        weaveLinks: [],
      }} />,
    );

    expect(html).toContain("叙事蓝图尚未形成可绘制节点");
    expect(html).toContain("当前项目尚无可用的蓝图或章节大纲");
    expect(html).not.toContain("共 0 章");
    expect(html).not.toContain("主线推进：关键转折与阶段收束");
  });
});

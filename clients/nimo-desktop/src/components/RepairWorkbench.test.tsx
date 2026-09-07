import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";

import type { EngineClient, EngineCommandClient, RepairCase } from "@nimo/engine-contracts";
import { describe, expect, it, vi } from "vitest";

vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({ children }: { readonly children: ReactNode }) => <div>{children}</div>,
}));

import { RepairWorkbench, sliceUnicodeRange, summarizeRepairCases } from "./RepairWorkbench";

describe("RepairWorkbench", () => {
  it("uses backend code-point offsets when an emoji precedes the target", () => {
    expect(sliceUnicodeRange("🧪序章线索", 2, 4)).toBe("章线");
  });

  it("summarizes only actionable repair cases for the chapter entry badge", () => {
    const repairCase = (status: RepairCase["status"], severity: "high" | "medium") => ({
      status,
      issues: [{ severity }],
    }) as unknown as RepairCase;
    expect(summarizeRepairCases([
      repairCase("located", "high"),
      repairCase("published", "high"),
      repairCase("deferred", "medium"),
      repairCase("needs_verification", "medium"),
    ])).toEqual({ attention: 2, urgent: 1 });
  });

  it("renders the layered repair workbench without granting publication on render", () => {
    const method = vi.fn();
    const engine = {
      getRepairSource: method,
      listRepairCases: method,
      getRepairCase: method,
    } as unknown as EngineClient;
    const commands = {
      createRepairAnnotation: method,
      saveRepairCandidate: method,
      verifyRepairCandidate: method,
      decideRepairCase: method,
    } as unknown as EngineCommandClient;

    const html = renderToStaticMarkup(
      <RepairWorkbench
        chapterNumber={3}
        commandClient={commands}
        engineClient={engine}
        onClose={() => undefined}
        projectId="book"
      />,
    );

    expect(html).toContain("统一修复工作台");
    expect(html).toContain("分级发布 · 正文需批准");
    expect(html).toContain("问题");
    expect(html).toContain("定位");
    expect(html).toContain("候选");
    expect(html).toContain("验证");
    expect(html).toContain("历史");
    expect(html).toContain("保存的是哈希与位置，不会改写正文");
    expect(html).toContain("修复证据范围");
    expect(html).toContain("本章");
    expect(html).toContain("全书");
    expect(html).toContain("长篇·连贯性");
    expect(html).toContain("长篇·因果");
    expect(html).toContain("长篇·追读力");
    expect(html).toContain("全书·逐章候选");
    expect(html).toContain("上游·语义一致性");
    expect(html).toContain("全书一致性审查");
    expect(html).toContain("等待批准");
    expect(html).toContain("已确认兼容");
    expect(html).toContain("已过期");
    expect(method).not.toHaveBeenCalled();
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ChapterRuntimePolicyCommand, ChapterRuntimePolicyPresetView } from "@nimo/engine-contracts";

import { ChapterRuntimePolicyCard, chapterRuntimePolicyCommand } from "./ChapterRuntimePolicyCard";

const options: readonly ChapterRuntimePolicyPresetView[] = [
  {
    id: "compat",
    label: "兼容当前",
    description: "不增加调用",
    values: {
      intentGuardMode: "warn",
      factRefreshEnabled: false,
      inspirationEnabled: false,
      inspirationCooldown: 3,
      shortAdaptiveRevisionEnabled: false,
      longSingleFinalVerifyEnabled: false,
    },
  },
  {
    id: "balanced",
    label: "均衡",
    description: "低频灵感",
    values: {
      intentGuardMode: "block",
      factRefreshEnabled: true,
      inspirationEnabled: true,
      inspirationCooldown: 3,
      shortAdaptiveRevisionEnabled: true,
      longSingleFinalVerifyEnabled: true,
    },
  },
];

const value: ChapterRuntimePolicyCommand = { preset: "compat", ...options[0]!.values };

describe("ChapterRuntimePolicyCard", () => {
  it("renders engine-owned presets and the explicit research boundary", () => {
    const html = renderToStaticMarkup(<ChapterRuntimePolicyCard onChange={vi.fn()} options={options} value={value} />);

    expect(html).toContain("章节质量与研究");
    expect(html).toContain("兼容当前");
    expect(html).toContain("项目或短篇请求显式开启 research");
    expect(html).toContain("高级控制");
  });

  it("marks an advanced edit as custom unless it matches a known preset", () => {
    expect(chapterRuntimePolicyCommand(value, options, { inspirationEnabled: true }).preset).toBe("custom");
    expect(chapterRuntimePolicyCommand(value, options, options[1]!.values).preset).toBe("balanced");
  });
});

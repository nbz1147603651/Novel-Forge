import { renderToStaticMarkup } from "react-dom/server";

import type { EngineCommandClient } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import { HumanizeWorkbench } from "./HumanizeWorkbench";

describe("HumanizeWorkbench", () => {
  it("presents the Engine-backed library instead of an ephemeral local session", () => {
    const html = renderToStaticMarkup(
      <HumanizeWorkbench
        commandClient={{} as EngineCommandClient}
        humanizeLibraryRevision="humanize-v1"
        onAction={() => undefined}
        patterns={[{
          id: "lib_user_a1b2c3d4",
          name: "空泛收束",
          sourceLabel: "用户",
          category: "模板结尾",
          severity: "high",
          hitCount: 3,
          lastChapterLabel: "第 4 章",
          enabled: true,
          keywords: ["最终"],
          notes: "审稿标注",
          examplePhrase: "最终，一切都归于命运。",
        }]}
        projectId="demo"
      />,
    );

    expect(html).toContain("由 Engine 统一维护");
    expect(html).toContain("空泛收束");
    expect(html).not.toContain("本地会话视图");
    expect(html).not.toContain("第一阶段不触碰拟人化库文件");
  });
});

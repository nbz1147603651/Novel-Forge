import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import longInitSource from "./LongInitFormPanel.tsx?raw";
import shortSource from "./ShortWorkflowComposer.tsx?raw";
import workflowSource from "./WorkflowPage.tsx?raw";
import chapterSource from "./ChapterWorkflowTimeline.tsx?raw";

// The shared overlay portals into document.body.  Tests run without a DOM, so
// render its children directly while verifying the dialog's visible states.
vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({ children }: { readonly children: React.ReactNode }) => children,
}));

import { WorkflowArtifactDialog } from "./WorkflowArtifactDialog";

describe("WorkflowArtifactDialog", () => {
  it.each([["LongInitFormPanel", longInitSource], ["ShortWorkflowComposer", shortSource], ["WorkflowPage", workflowSource], ["ChapterWorkflowTimeline", chapterSource]])(
    "keeps %s on the same bounded artifact dialog",
    (_component, source) => {
      expect(source).toContain("<WorkflowArtifactDialog");
      expect(source).not.toContain("<ArtifactViewer");
      expect(source).not.toContain('title="产出文件"');
    },
  );

  it("shows an immediate loading state while a completed step's artifacts resolve", () => {
    const markup = renderToStaticMarkup(
      <WorkflowArtifactDialog
        artifacts={[]}
        loading
        onClose={vi.fn()}
        stageLabel="世界观设定"
      />,
    );

    expect(markup).toContain("世界观设定 · 正在读取产出文件");
    expect(markup).toContain("正在读取“世界观设定”的产物…");
    expect(markup).not.toContain("暂无可查看文件");
  });

  it("keeps an empty result visible instead of silently closing the dialog", () => {
    const markup = renderToStaticMarkup(
      <WorkflowArtifactDialog
        artifacts={[]}
        emptyHint="尚未生成可预览文件。"
        onClose={vi.fn()}
        stageLabel="世界观设定"
      />,
    );

    expect(markup).toContain("世界观设定 · 暂无可查看文件");
    expect(markup).toContain("尚未生成可预览文件。");
  });
});

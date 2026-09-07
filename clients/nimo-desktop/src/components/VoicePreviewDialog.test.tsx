import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";

import type { VoiceCastMemberView } from "@nimo/engine-contracts";
import { describe, expect, it, vi } from "vitest";

vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({ children }: { readonly children: ReactNode }) => children,
}));

import { VoicePreviewDialog } from "./VoicePreviewDialog";

const member: VoiceCastMemberView = {
  id: "lin-yuan",
  name: "林远",
  role: "lead",
  statusLabel: "待试听确认",
  voiceLabel: "系统音色",
  voiceSourceLabel: "系统音色目录",
  matchSummary: "",
  description: "沉稳克制的男主角。",
};

describe("VoicePreviewDialog", () => {
  it("renders the building state with shared sample controls", () => {
    const markup = renderToStaticMarkup(
      <VoicePreviewDialog
        commandClient={
          {
            buildVoicePreviewPlan: vi.fn(),
            generateVoicePreviews: vi.fn(),
            confirmVoicePreview: vi.fn(),
          } as never
        }
        member={member}
        onClose={() => undefined}
        onConfirmed={() => undefined}
        projectId="demo"
        providerLabel="MiniMax"
      />,
    );

    expect(markup).toContain("候选音色 A/B 对比");
    expect(markup).toContain("试听并确认 林远 的音色");
    expect(markup).toContain("平台 · MiniMax");
    expect(markup).toContain("正在从系统音色目录挑选候选…");
    expect(markup).toContain("候选音色试听文本");
    expect(markup).toContain("试听语速倍率");
    expect(markup).toContain("试听音量倍率");
    expect(markup).toContain("重新合成候选试听");
    expect(markup).toContain("确认此音色并写入团队");
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";

import type { VoiceCastMemberView } from "@nimo/engine-contracts";
import { describe, expect, it, vi } from "vitest";

vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({ children }: { readonly children: ReactNode }) => children,
}));

import { VoiceDesignDialog } from "./VoiceDesignDialog";

describe("VoiceDesignDialog", () => {
  it("keeps the complete voice evidence inside the dedicated editor", () => {
    const member: VoiceCastMemberView = {
      id: "chen-banxian",
      name: "陈半仙",
      role: "supporting",
      statusLabel: "待试听确认",
      voiceLabel: "ttv-voice-chen",
      voiceSourceLabel: "AI 特征设计",
      matchSummary: "画像匹配 92%",
      description: "声线克制，适合以旁观口吻揭示线索。",
      detailFacts: [{ label: "角色依据", value: "配角 · 男性 · 58岁" }],
      matchReasons: ["低沉声线能托住旁观者的距离感"],
      auditionWarnings: ["确认慢句不会拖沓"],
      designBrief: "男声，偏低但清晰，语速平稳。",
    };

    const markup = renderToStaticMarkup(
      <VoiceDesignDialog
        member={member}
        onClose={() => undefined}
        onSubmit={async () => ({ status: "accepted", message: "ok" })}
        providerLabel="MiniMax"
      />,
    );

    expect(markup).toContain("声纹编辑");
    expect(markup).toContain("ttv-voice-chen");
    expect(markup).toContain("低沉声线能托住旁观者的距离感");
    expect(markup).toContain("确认慢句不会拖沓");
    expect(markup).toContain("角色依据：配角 · 男性 · 58岁");
  });

  it("gives the narrator a dedicated rebuild flow rather than a character brief editor", () => {
    const member: VoiceCastMemberView = {
      id: "narrator",
      name: "旁白",
      role: "作品级叙述者",
      statusLabel: "已配置",
      voiceLabel: "Chinese (Mandarin)_Radio_Host",
      voiceSourceLabel: "人工指定",
      matchSummary: "匹配待评估",
      description: "中性、清晰，适合中文叙述。",
      detailFacts: [{ label: "音色类型", value: "标准旁白" }],
    };

    const markup = renderToStaticMarkup(
      <VoiceDesignDialog
        member={member}
        onClose={() => undefined}
        onSubmit={async () => ({ status: "accepted", message: "ok" })}
        providerLabel="MiniMax"
      />,
    );

    expect(markup).toContain("重新生成旁白音色");
    expect(markup).toContain("当前大纲、故事圣经与风格档案");
    expect(markup).toContain("重新生成并准备试听");
    expect(markup).not.toContain('aria-label="角色音色简报"');
  });
});

import { describe, expect, it } from "vitest";

import { allVoiceRebuildIds, rebuildableVoiceCast, selectedVoiceRebuildIds, systemMatchedVoiceIds, toggleVoiceRebuildId } from "./voice-rebuild-session";

const cast = [
  { id: "narrator", name: "旁白", role: "作品级叙述者", statusLabel: "已配", voiceLabel: "沉静女声", description: "" },
  { id: "lin-zhu", name: "林逐", role: "主视角", statusLabel: "已配", voiceLabel: "冷静女声", description: "" },
  { id: "system", name: "系统播报", role: "城市记忆库", statusLabel: "待配", voiceLabel: "未分配", description: "" },
] as const;

describe("voice rebuild local session", () => {
  it("excludes the work-level narrator from a team rebuild", () => {
    const rebuildable = rebuildableVoiceCast(cast);
    expect([...allVoiceRebuildIds(rebuildable)]).toEqual(["lin-zhu", "system"]);
  });

  it("toggles a role and keeps confirmation scoped to selected rebuildable roles", () => {
    const rebuildable = rebuildableVoiceCast(cast);
    const selected = toggleVoiceRebuildId(new Set(), "lin-zhu");
    expect(selectedVoiceRebuildIds(rebuildable, selected)).toEqual(["lin-zhu"]);
  });

  it("selects only source-system or unassigned roles", () => {
    expect([...systemMatchedVoiceIds(rebuildableVoiceCast(cast))]).toEqual(["system"]);
  });
});

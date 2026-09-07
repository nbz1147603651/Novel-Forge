import type { VoiceCastMemberView } from "@nimo/engine-contracts";

/** The work-level narrator has its own source action and is never team-rebuilt. */
export function rebuildableVoiceCast(cast: readonly VoiceCastMemberView[]): readonly VoiceCastMemberView[] {
  return cast.filter((member) => member.id !== "narrator");
}

export function toggleVoiceRebuildId(current: ReadonlySet<string>, id: string): ReadonlySet<string> {
  const next = new Set(current);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export function allVoiceRebuildIds(cast: readonly VoiceCastMemberView[]): ReadonlySet<string> {
  return new Set(cast.map((member) => member.id));
}

export function systemMatchedVoiceIds(cast: readonly VoiceCastMemberView[]): ReadonlySet<string> {
  return new Set(cast.filter((member) => member.id === "system" || member.statusLabel === "待配").map((member) => member.id));
}

export function selectedVoiceRebuildIds(cast: readonly VoiceCastMemberView[], selectedIds: ReadonlySet<string>): readonly string[] {
  return cast.filter((member) => selectedIds.has(member.id)).map((member) => member.id);
}

export function rebuildRoleLabel(member: VoiceCastMemberView): string {
  if (member.role.includes("主")) return "主角";
  if (member.role.includes("调查") || member.role.includes("配")) return "配角";
  return "龙套";
}

export function rebuildSourceLabel(member: VoiceCastMemberView): string {
  return member.statusLabel === "待配" ? "系统" : "AI设计";
}

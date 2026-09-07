import type { VoiceStudioView } from "@nimo/engine-contracts";

/** 声腔音色细节事实表（摘要/完整两态，mirrors PySide6 音色依据卡）。 */
export function VoiceDetailFacts({
  expanded,
  member,
  providerLabel,
}: {
  readonly expanded: boolean;
  readonly member: VoiceStudioView["cast"][number];
  readonly providerLabel: string;
}) {
  const summaryFacts = [
    ["配置状态", member.statusLabel],
    ["评估状态", member.matchSummary ?? "待评估"],
    ["平台", providerLabel],
    ["音色来源", member.voiceSourceLabel ?? "待分配"],
  ];
  const detailFacts = [
    ["音色 ID", (member.voiceId ?? member.voiceLabel) || "待分配"],
    ["适用范围", member.role || "未指定"],
    ["表达策略", member.performancePolicyLabel ?? "自动策略：保持角色声纹稳定。"],
    ...(member.detailFacts ?? []).map((fact) => [fact.label, fact.value] as const),
  ];
  const facts = expanded ? [...summaryFacts, ...detailFacts] : summaryFacts;

  return (
    <dl
      aria-label={expanded ? "完整音色依据" : "音色摘要"}
      className={`voice-detail-facts ${expanded ? "is-expanded" : "is-summary"}`}
    >
      {facts.map(([label, value], index) => (
        <div key={`${label}-${index}`}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

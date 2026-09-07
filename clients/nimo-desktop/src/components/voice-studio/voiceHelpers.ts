/** 解析语速/音量倍率（default → undefined）。 */
export function parseVoiceMultiplier(value: string): number | undefined {
  if (value === "default") return undefined;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** 解析音调半音偏移（default → undefined）。 */
export function parseVoicePitch(value: string): number | undefined {
  if (value === "default") return undefined;
  const parsed = Number.parseInt(value.replaceAll(/[^\d+-]/g, ""), 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** 按逗号（中英文）拆分标签/重音/发音覆盖列表。 */
export function splitVoiceList(value: string): readonly string[] {
  return value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
}

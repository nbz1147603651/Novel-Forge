import type { LongInitPayload, LongInitPayloadPatch } from "../../../lib/long-init-session";

export type FieldKind = "multiline" | "singleline" | "title-language";
export type FieldGroup = "core" | "advanced";

export interface FieldDefinition {
  readonly id: string;
  readonly key?: keyof LongInitPayload;
  readonly kind: FieldKind;
  readonly label: string;
  readonly hint: string;
  readonly required?: boolean;
}

export const coreFields: readonly FieldDefinition[] = [
  { id: "premise", key: "premise", kind: "multiline", label: "故事前提", hint: "前提是世界观与大纲的基础，建议详细描述核心设定。", required: true },
  { id: "charactersHint", key: "charactersHint", kind: "multiline", label: "主角群提示", hint: "例：主角A（核心欲望与弱点）；盟友B（资源与代价）；对手C（价值观冲突）" },
  { id: "worldHint", key: "worldHint", kind: "multiline", label: "世界观 / 时代背景", hint: "例：近未来东亚超级都市，底层运行着持续删改个体痛苦与群体记忆的系统" },
  { id: "conflictHint", key: "conflictHint", kind: "multiline", label: "主冲突提示", hint: "例：外部危机、内在困境与关系代价同时推进" },
];

export const advancedFields: readonly FieldDefinition[] = [
  { id: "titleLanguage", kind: "title-language", label: "作品名 / 语言", hint: "作品名可留空交给 AI；语言决定生成文本的主要输出语种。" },
  { id: "povHint", key: "povHint", kind: "multiline", label: "叙事视角", hint: "例：第三人称多视角，女主主视角为主，少量穿插男主" },
  { id: "openingStyle", key: "openingStyle", kind: "multiline", label: "开篇方式", hint: "开篇方式，例：开篇即高概念与高情绪" },
  { id: "endingStyle", key: "endingStyle", kind: "multiline", label: "结尾方式", hint: "结尾方向，例：HE 余韵型收束" },
  { id: "extraInstructions", key: "extraInstructions", kind: "multiline", label: "额外创作指令", hint: "例：女频优势优先，关系拉扯与情绪流动贯穿始终" },
  { id: "projectId", key: "projectId", kind: "singleline", label: "项目 ID", hint: "留空自动生成（用作保存目录名）" },
];

export function getFieldSummary(field: FieldDefinition, payload: LongInitPayload): string {
  if (field.kind === "title-language") {
    return [payload.title, payload.language].filter(Boolean).join(" · ");
  }
  if (field.key) {
    const value = payload[field.key];
    if (typeof value === "string") return value.length > 60 ? value.slice(0, 59) + "…" : value;
  }
  return "";
}

export function getFieldValue(field: FieldDefinition, payload: LongInitPayload): string {
  if (field.kind === "title-language") {
    return [payload.title, payload.language].filter(Boolean).join(" · ");
  }
  if (field.key) {
    const value = payload[field.key];
    if (typeof value === "string") return value;
    if (typeof value === "number") return String(value);
  }
  return "";
}

export type { LongInitPayload, LongInitPayloadPatch };

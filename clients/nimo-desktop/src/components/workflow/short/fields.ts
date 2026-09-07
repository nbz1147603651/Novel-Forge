import type {
  ShortWorkflowKey,
  ShortWorkflowPayload,
  ShortWorkflowTextKey,
} from "../../../lib/short-workflow-session";
import { shortLanguageOptions } from "../../../lib/short-workflow-session";

export type FieldKind = "multiline" | "singleline" | "title-language";
export type FieldGroup = "core" | "advanced";

export interface FieldDefinition {
  readonly id: string;
  readonly key?: ShortWorkflowTextKey;
  readonly kind: FieldKind;
  readonly label: string;
  readonly hint: string;
  readonly required?: boolean;
}

export const coreFields: readonly FieldDefinition[] = [
  { id: "theme", key: "theme", kind: "multiline", label: "故事主题", hint: "主题是创作核心，描述越具体，生成质量越高。", required: true },
  { id: "charactersHint", key: "charactersHint", kind: "multiline", label: "人物提示", hint: "例：主角A（外在目标明确、内在有缺口）；角色B（与主角目标相冲突）" },
  { id: "worldHint", key: "worldHint", kind: "multiline", label: "世界观 / 场景提示", hint: "例：当代城市、架空王国、近未来空间站或任意自定义舞台" },
  { id: "conflictHint", key: "conflictHint", kind: "multiline", label: "核心冲突提示", hint: "例：外部阻力、内在困境与关系代价同时推进" },
];

export const advancedFields: readonly FieldDefinition[] = [
  { id: "titleLanguage", kind: "title-language", label: "作品名 / 语言", hint: "作品名可留空交给 AI；语言决定生成文本的主要输出语种。" },
  { id: "povHint", key: "povHint", kind: "multiline", label: "叙事视角", hint: "例：第三人称双主角限知；非对话正文禁用我/我们" },
  { id: "openingStyle", key: "openingStyle", kind: "multiline", label: "开篇方式", hint: "开篇方式，例：以训练事故现场切入" },
  { id: "endingStyle", key: "endingStyle", kind: "multiline", label: "结尾方式", hint: "结尾方式，例：甜向余韵收束" },
  { id: "extraInstructions", key: "extraInstructions", kind: "multiline", label: "额外创作指令", hint: "例：减少解释性旁白，强化动作和感官细节" },
  { id: "projectId", key: "projectId", kind: "singleline", label: "项目 ID", hint: "留空自动生成（用作保存目录名）" },
];

export function shortFieldSummary(field: FieldDefinition, payload: ShortWorkflowPayload): string {
  if (field.kind === "title-language") {
    const language = shortLanguageOptions.find((option) => option.value === payload.language)?.label ?? payload.language;
    return `${payload.title.trim() || "作品名留空，由 AI 生成"} · ${language}`;
  }
  return field.key === undefined ? "" : payload[field.key];
}

export type { ShortWorkflowKey, ShortWorkflowPayload };

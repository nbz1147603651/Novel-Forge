import type { CharacterDetailView, CharacterProfileView, RelationshipLinkView, RevisionCandidateView, SubplotView } from "@nimo/engine-contracts";

/** 叙事工具页签（与 PySide6 NarrativeToolsWorkbench tabs 对齐）。 */
export type NarrativeToolTab = "timeline" | "characters" | "relationships" | "graph" | "outline" | "subplots" | "humanize" | "revision";

export type RelationshipFilter = "全部" | "同盟" | "亲属" | "师徒" | "隐秘" | "对抗";
export type RelationshipViewMode = "graph" | "table";

export type CharacterSessionDetail = Pick<CharacterDetailView, "timelineLabel" | "ageLabel" | "genderLabel" | "occupation" | "personality" | "backstory" | "abilities" | "appearance" | "arc" | "voice" | "notes">;
export type EditableCharacterField = "ageLabel" | "genderLabel" | "occupation" | "personality" | "backstory" | "abilities" | "appearance" | "arc" | "voice" | "notes";

export const tabLabels: Readonly<Record<NarrativeToolTab, string>> = {
  timeline: "叙事总览",
  characters: "角色档案",
  relationships: "关系网络",
  graph: "图谱",
  outline: "大纲编辑",
  subplots: "支线管理",
  humanize: "拟人化库",
  revision: "终稿修订",
};

/** 叙事工具操作意图（由父页面接收并上报）。 */
export interface NarrativeToolsAction {
  readonly kind:
    | "character-selected"
    | "character-create-requested"
    | "character-edit-requested"
    | "character-local-saved"
    | "character-edit-discarded"
    | "character-retire-requested"
    | "relationship-focused"
    | "relationship-focus-cleared"
    | "relationship-selected"
    | "relationship-create-requested"
    | "relationship-edit-requested"
    | "relationship-remove-requested"
    | "outline-extend-requested"
    | "subplot-create-requested"
    | "subplot-edit-requested"
    | "subplot-remove-requested"
    | "subplot-refreshed"
    | "subplot-polish-requested"
    | "subplot-arc-convert-requested"
    | "subplot-command-unavailable"
    | "subplot-save-failed"
    | "subplot-saved"
    | "subplot-convert-empty"
    | "subplot-arc-converted"
    | "subplot-arc-convert-failed"
    | "subplot-ai-generated"
    | "subplot-ai-generate-failed"
    | "subplot-outline-impact-empty"
    | "subplot-outline-sync-submitted"
    | "subplot-outline-sync-failed"
    | "humanize-create-requested"
    | "humanize-import-requested"
    | "humanize-export-requested"
    | "humanize-dedupe-requested"
    | "humanize-toggled"
    | "humanize-edit-requested"
    | "humanize-remove-requested"
    | "humanize-merge-requested"
    | "humanize-imported"
    | "humanize-refreshed"
    | "humanize-hit-examples-requested"
    | "revision-toggled";
  readonly targetId?: string;
  readonly message: string;
}

/** 构造操作意图（所有工作台共享）。 */
export function action(kind: NarrativeToolsAction["kind"], message: string, targetId?: string): NarrativeToolsAction {
  return targetId === undefined ? { kind, message } : { kind, message, targetId };
}

/** 关系展示行（图谱/表格共享）。 */
export interface RelationshipRow {
  readonly id: string;
  readonly from: string;
  readonly fromCharacterId: string;
  readonly to: string;
  readonly toCharacterId: string;
  readonly typeLabel: string;
  readonly evidence: string;
}

export function asRelationshipRows(
  links: readonly RelationshipLinkView[],
  characters: readonly CharacterProfileView[],
): readonly RelationshipRow[] {
  return links.map((link) => ({
    id: link.id,
    from: characters.find((character) => character.id === link.fromCharacterId)?.name ?? "未知",
    fromCharacterId: link.fromCharacterId,
    to: characters.find((character) => character.id === link.toCharacterId)?.name ?? "未知",
    toCharacterId: link.toCharacterId,
    typeLabel: link.typeLabel,
    evidence: link.evidence,
  }));
}

export type { CharacterProfileView, RelationshipLinkView, RevisionCandidateView, SubplotView };

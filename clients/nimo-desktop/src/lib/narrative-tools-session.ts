import type { CharacterDetailView, CharacterProfileView, NarrativeToolsView, RelationshipLinkView } from "@nimo/engine-contracts";

/**
 * Draft projection for the character and relationship workbenches.
 *
 * A reducer prepares one confirmed user edit for the parent controller.  The
 * controller persists that edit through Engine commands, then replaces this
 * projection with the refreshed authoritative read model.  Nothing in this
 * module writes project artifacts or serves as a durable source of truth.
 */
export interface NarrativeToolsSession {
  readonly characters: readonly CharacterProfileView[];
  readonly characterDetails: readonly CharacterDetailView[];
  readonly relationships: readonly RelationshipLinkView[];
}

export interface CharacterSessionDraft {
  readonly name: string;
  readonly role: string;
  readonly genderLabel: string;
  readonly ageLabel: string;
}

export interface RelationshipSessionDraft {
  readonly sourceCharacterId: string;
  readonly targetCharacterId: string;
  readonly typeLabel: string;
  readonly evidence: string;
}

/**
 * Source-shaped form data for ``SubplotDialog``.  The EngineClient contract
 * intentionally exposes display fields, so chapter range and resolution values
 * remain a draft projection until the subplot command confirms them.
 */
export function createNarrativeToolsSession(tools: NarrativeToolsView): NarrativeToolsSession {
  return {
    characters: tools.characters,
    characterDetails: tools.characterDetails,
    relationships: tools.relationships,
  };
}

function uniqueId(prefix: string, label: string, usedIds: readonly string[]): string {
  const base = label.trim().toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "item";
  let candidate = `${prefix}-${base}`;
  let suffix = 2;
  while (usedIds.includes(candidate)) {
    candidate = `${prefix}-${base}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

export function addSessionCharacter(session: NarrativeToolsSession, draft: CharacterSessionDraft): { readonly session: NarrativeToolsSession; readonly character: CharacterProfileView } {
  const character: CharacterProfileView = {
    id: uniqueId("draft-character", draft.name, session.characters.map((item) => item.id)),
    name: draft.name.trim(),
    role: draft.role,
    statusLabel: "活跃",
    summary: "等待 Engine 确认后刷新角色档案。",
    arc: "尚未设置人物弧线。",
  };
  const detail: CharacterDetailView = {
    characterId: character.id,
    timelineLabel: "默认",
    ageLabel: draft.ageLabel.trim() || "—",
    genderLabel: draft.genderLabel.trim() || "—",
    occupation: draft.role,
    personality: "尚未填写性格。",
    backstory: "尚未填写背景。",
    abilities: "尚未填写能力 / 资源。",
    appearance: "尚未填写外貌。",
    arc: "尚未设置人物弧线。",
    voice: "尚未填写声纹。",
    notes: "",
  };
  return {
    character,
    session: {
      ...session,
      characters: [...session.characters, character],
      characterDetails: [...session.characterDetails, detail],
    },
  };
}

export function retireSessionCharacter(session: NarrativeToolsSession, characterId: string): NarrativeToolsSession {
  return {
    ...session,
    characters: session.characters.map((character) => character.id === characterId
      ? { ...character, statusLabel: "退场" }
      : character),
  };
}

export function saveSessionCharacterDetail(session: NarrativeToolsSession, detail: CharacterDetailView): NarrativeToolsSession {
  const hasDetail = session.characterDetails.some((item) => item.characterId === detail.characterId);
  return {
    ...session,
    characterDetails: hasDetail
      ? session.characterDetails.map((item) => item.characterId === detail.characterId ? detail : item)
      : [...session.characterDetails, detail],
  };
}

export function upsertSessionRelationship(session: NarrativeToolsSession, draft: RelationshipSessionDraft): { readonly relationship: RelationshipLinkView; readonly session: NarrativeToolsSession } {
  // CharacterGraphWidget treats a relationship as a single undirected edge:
  // the artifact writer mirrors it onto both character records and the graph
  // deduplicates the pair.  Preserve that semantic boundary in the local
  // session as well, so dragging B → A updates an existing A → B relation
  // instead of drawing a duplicate visual edge.
  const existing = session.relationships.find((item) => (
    (item.fromCharacterId === draft.sourceCharacterId && item.toCharacterId === draft.targetCharacterId)
    || (item.fromCharacterId === draft.targetCharacterId && item.toCharacterId === draft.sourceCharacterId)
  ));
  const relationship: RelationshipLinkView = {
    id: existing?.id ?? uniqueId("local-relationship", `${draft.sourceCharacterId}-${draft.targetCharacterId}`, session.relationships.map((item) => item.id)),
    fromCharacterId: draft.sourceCharacterId,
    toCharacterId: draft.targetCharacterId,
    typeLabel: draft.typeLabel,
    evidence: draft.evidence.trim(),
  };
  return {
    relationship,
    session: {
      ...session,
      relationships: existing === undefined
        ? [...session.relationships, relationship]
        : session.relationships.map((item) => item.id === existing.id ? relationship : item),
    },
  };
}

export function removeSessionRelationship(session: NarrativeToolsSession, relationshipId: string): NarrativeToolsSession {
  return { ...session, relationships: session.relationships.filter((item) => item.id !== relationshipId) };
}

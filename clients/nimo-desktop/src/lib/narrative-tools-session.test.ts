import { describe, expect, it } from "vitest";

import type { NarrativeToolsView } from "@nimo/engine-contracts";

import {
  addSessionCharacter,
  createNarrativeToolsSession,
  removeSessionRelationship,
  retireSessionCharacter,
  saveSessionCharacterDetail,
  upsertSessionRelationship,
} from "./narrative-tools-session";

const fixture = {
  characters: [{ id: "lin", name: "林逐", role: "主角", statusLabel: "活跃", summary: "追查时间戳。", arc: "由自保转为承担。" }],
  characterDetails: [{ characterId: "lin", timelineLabel: "当前线", ageLabel: "28", genderLabel: "女", occupation: "调查员", personality: "克制", backstory: "旧案未结", abilities: "证据检索" }],
  relationships: [],
  subplots: [],
} as unknown as NarrativeToolsView;

describe("narrative tools local session", () => {
  it("shares an added character and its retirement state across workbenches", () => {
    const initial = createNarrativeToolsSession(fixture);
    const created = addSessionCharacter(initial, { name: "周砚", role: "重要配角", genderLabel: "男", ageLabel: "31" });
    const retired = retireSessionCharacter(created.session, created.character.id);

    expect(retired.characters).toHaveLength(2);
    expect(retired.characterDetails.find((item) => item.characterId === created.character.id)?.ageLabel).toBe("31");
    expect(retired.characters.find((item) => item.id === created.character.id)?.statusLabel).toBe("退场");
  });

  it("upserts and removes a relationship without mutating the EngineClient view", () => {
    const initial = createNarrativeToolsSession(fixture);
    const added = addSessionCharacter(initial, { name: "周砚", role: "重要配角", genderLabel: "男", ageLabel: "31" });
    const linked = upsertSessionRelationship(added.session, { sourceCharacterId: "lin", targetCharacterId: added.character.id, typeLabel: "同盟", evidence: "关键行动中互相掩护。" });
    const revised = upsertSessionRelationship(linked.session, { sourceCharacterId: "lin", targetCharacterId: added.character.id, typeLabel: "隐秘", evidence: "双方隐瞒了旧案线索。" });
    const withoutLink = removeSessionRelationship(revised.session, revised.relationship.id);

    expect(linked.session.relationships).toHaveLength(1);
    expect(revised.session.relationships[0]).toMatchObject({ typeLabel: "隐秘", evidence: "双方隐瞒了旧案线索。" });
    expect(withoutLink.relationships).toEqual([]);
    expect(fixture.relationships).toEqual([]);
  });

  it("treats either direction as the same source graph edge", () => {
    const initial = createNarrativeToolsSession(fixture);
    const added = addSessionCharacter(initial, { name: "周砚", role: "重要配角", genderLabel: "男", ageLabel: "31" });
    const forward = upsertSessionRelationship(added.session, { sourceCharacterId: "lin", targetCharacterId: added.character.id, typeLabel: "同盟", evidence: "关键行动中互相掩护。" });
    const reverse = upsertSessionRelationship(forward.session, { sourceCharacterId: added.character.id, targetCharacterId: "lin", typeLabel: "对抗", evidence: "线索归属出现分歧。" });

    expect(reverse.session.relationships).toHaveLength(1);
    expect(reverse.relationship).toMatchObject({
      id: forward.relationship.id,
      fromCharacterId: added.character.id,
      toCharacterId: "lin",
      typeLabel: "对抗",
    });
  });

  it("replaces a saved local detail by character id", () => {
    const initial = createNarrativeToolsSession(fixture);
    const saved = saveSessionCharacterDetail(initial, { ...fixture.characterDetails[0]!, personality: "谨慎且善于观察。" });

    expect(saved.characterDetails).toHaveLength(1);
    expect(saved.characterDetails[0]?.personality).toBe("谨慎且善于观察。");
    expect(fixture.characterDetails[0]?.personality).toBe("克制");
  });

});

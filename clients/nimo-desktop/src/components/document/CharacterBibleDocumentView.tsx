import { useMemo, useState } from "react";

import type { CharacterProfileView, RelationshipLinkView } from "@nimo/engine-contracts";

import { RelationshipGraphRenderer } from "../NarrativeVisualization";
import { GenericReportView } from "./GenericReportView";
import { parseJsonContent, str, type JsonDict } from "./document-parse";

const roles: Record<string, string> = {
  protagonist: "主角", deuteragonist: "第二主角", antagonist: "对手",
  supporting: "重要配角", minor: "次要角色",
};
const statuses: Record<string, string> = { active: "活跃", absent: "缺席", dormant: "未登场", retired: "退场", deceased: "已故" };
const isRecord = (value: unknown): value is JsonDict => value !== null && typeof value === "object" && !Array.isArray(value);

/** Read the actual artifact; never substitute demo characters or require canon initialization. */
export function characterDocumentData(content: string | undefined) {
  const data = parseJsonContent(content);
  const rows = Array.isArray(data?.characters) ? data.characters.filter(isRecord) : [];
  const profiles = new Map<string, JsonDict>();
  const characters: CharacterProfileView[] = [];
  for (const row of rows) {
    const name = str(row, "name").trim();
    const id = str(row, "character_id") || str(row, "id") || name;
    if (!name || profiles.has(id)) continue;
    profiles.set(id, row);
    characters.push({
      id, name, role: roles[str(row, "role")] ?? str(row, "role"),
      statusLabel: statuses[str(row, "status")] ?? (str(row, "status") || "未标注"),
      summary: str(row, "personality") || str(row, "backstory"), arc: str(row, "arc"),
    });
  }
  const byName = new Map(characters.map((character) => [character.name, character.id]));
  const seen = new Set<string>();
  const relationships: RelationshipLinkView[] = [];
  for (const character of characters) {
    const relations = profiles.get(character.id)?.relationships;
    if (!isRecord(relations)) continue;
    for (const [name, description] of Object.entries(relations)) {
      const target = byName.get(name.trim());
      if (!target || target === character.id) continue;
      const id = JSON.stringify([character.id, target].sort());
      if (seen.has(id)) continue;
      seen.add(id);
      relationships.push({ id, fromCharacterId: character.id, toCharacterId: target,
        typeLabel: "人物关系", evidence: typeof description === "string" ? description : JSON.stringify(description),
      });
    }
  }
  return { characters, relationships, profiles };
}

/** Shared read-only graph + roster + complete profile, also usable during initialization. */
export function CharacterBibleDocumentView({ content }: { readonly content: string | undefined }) {
  const { characters, relationships, profiles } = useMemo(() => characterDocumentData(content), [content]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [compactView, setCompactView] = useState<"graph" | "profile">("graph");
  const visible = characters.filter((character) => `${character.name} ${character.role}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const selected = visible.find((character) => character.id === selectedId) ?? visible[0];
  const selectedProfile = selected ? profiles.get(selected.id) : undefined;
  const selectedLink = relationships.find((link) => link.id === selectedLinkId);
  const selectCharacter = (character: CharacterProfileView) => {
    setQuery("");
    setSelectedId(character.id);
    setFocusedId(character.id);
    setSelectedLinkId(null);
  };
  const profileContent = useMemo(() => {
    if (!selected) return undefined;
    const profile = { ...profiles.get(selected.id) };
    for (const key of ["name", "id", "character_id", "role", "status", "age", "gender", "time_layer"]) delete profile[key];
    return JSON.stringify(profile);
  }, [profiles, selected]);
  if (characters.length === 0) return <p className="narrative-empty">角色设定尚未写入，生成完成后将显示角色档案与关系图谱。</p>;
  return <section className="character-document" aria-label="角色设定图谱阅读器">
    <header><strong>角色设定</strong><span>{characters.length} 位角色 · {relationships.length} 条关系</span><small>点击角色查阅完整设定，点击连线查看关系</small></header>
    <div className="character-document-switch" role="group" aria-label="角色阅读视图">
      <button type="button" aria-pressed={compactView === "graph"} onClick={() => setCompactView("graph")}>关系图谱</button>
      <button type="button" aria-pressed={compactView === "profile"} onClick={() => setCompactView("profile")}>完整设定</button>
    </div>
    <div className="character-document-grid" data-compact-view={compactView}>
      <aside className="character-document-roster" aria-label="产物角色列表">
        <input aria-label="搜索产物角色" type="search" placeholder="搜索角色" value={query} onChange={(event) => setQuery(event.target.value)} />
        <div>{visible.map((character) => <button key={character.id} type="button" aria-pressed={character.id === selected?.id} onClick={() => selectCharacter(character)}><strong>{character.name}</strong><small>{character.role} · {character.statusLabel}</small></button>)}</div>
        {visible.length === 0 && <p>没有匹配的角色。</p>}
      </aside>
      <div className="character-document-graph"><RelationshipGraphRenderer characters={characters} links={relationships} focusedCharacterId={focusedId} selectedLinkId={selectedLinkId} onFocusCharacter={(character) => { selectCharacter(character); setCompactView("profile"); }} onFocusLink={(link) => { setSelectedLinkId(link.id); setFocusedId(null); setCompactView("profile"); }} onClearFocus={() => { setFocusedId(null); setSelectedLinkId(null); }} sourceFrame /></div>
      <article className="character-document-profile" aria-label="产物角色完整设定">
        {selectedLink && <section className="doc-hint-block"><strong>{characters.find((character) => character.id === selectedLink.fromCharacterId)?.name} ↔ {characters.find((character) => character.id === selectedLink.toCharacterId)?.name}</strong><p>{selectedLink.evidence}</p></section>}
        {selected && <><h2>{selected.name}<small>{selected.role} · {selected.statusLabel}</small></h2>
          <dl className="character-document-facts">{[["年龄", selectedProfile?.age], ["性别", selectedProfile?.gender], ["时间线", selectedProfile?.time_layer === "default" ? "当前线" : selectedProfile?.time_layer]].map(([label, value]) => <div key={String(label)}><dt>{String(label)}</dt><dd>{value === undefined || value === null || value === "" ? "未标注" : String(value)}</dd></div>)}</dl>
          <GenericReportView key={selected.id} content={profileContent} /></>}
      </article>
    </div>
  </section>;
}

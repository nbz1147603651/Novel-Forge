import { useEffect, useMemo, useState } from "react";

import type { CharacterProfileView, RelationshipLinkView } from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import { RelationshipGraphRenderer } from "../NarrativeVisualization";
import {
  removeSessionRelationship,
  upsertSessionRelationship,
  type NarrativeToolsSession,
} from "../../lib/narrative-tools-session";
import { action, asRelationshipRows, type NarrativeToolsAction, type RelationshipFilter, type RelationshipViewMode } from "./types";

// Mirrors `character_artifact_writer.RELATIONSHIP_TYPE_OPTIONS`; source labels
// are kept at the React boundary while a future EngineClient owns type keys.
const relationshipTypeOptions = ["一般关系", "情感张力", "亲属", "师徒", "职场/组织", "同盟", "竞争", "对抗", "社群", "身份映射"] as const;

function sourceRelationshipTypeLabel(value: string): (typeof relationshipTypeOptions)[number] {
  const displayPrefix = value.split("·", 1)[0]?.trim() ?? "";
  return relationshipTypeOptions.includes(displayPrefix as (typeof relationshipTypeOptions)[number])
    ? displayPrefix as (typeof relationshipTypeOptions)[number]
    : "一般关系";
}

type RelationshipEditorState =
  | { readonly mode: "create"; readonly evidence?: string; readonly relationship?: undefined; readonly sourceCharacterId?: string; readonly targetCharacterId?: string; readonly typeLabel?: string }
  | { readonly mode: "edit"; readonly relationship: RelationshipLinkView };

export type { RelationshipEditorState };

/** 关系网络工作台（图谱/表格双视图，mirrors PySide6 关系网络页）。 */
export function RelationshipWorkbench({ externalFocusedCharacterId, onAction, onSessionChange, session }: { readonly externalFocusedCharacterId?: string | null; readonly onAction: (action: NarrativeToolsAction) => void; readonly onSessionChange: (session: NarrativeToolsSession) => void; readonly session: NarrativeToolsSession }) {
  const { characterDetails, characters, relationships: links } = session;
  const [filter, setFilter] = useState<RelationshipFilter>("全部");
  const [focusedCharacterId, setFocusedCharacterId] = useState<string | null>(null);
  const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<RelationshipViewMode>("graph");
  const [editor, setEditor] = useState<RelationshipEditorState | null>(null);
  const [removalLinkId, setRemovalLinkId] = useState<string | null>(null);
  const rows = useMemo(() => asRelationshipRows(links, characters), [characters, links]);
  useEffect(() => {
    if (externalFocusedCharacterId === undefined) return;
    setFocusedCharacterId(externalFocusedCharacterId);
    setSelectedLinkId(null);
  }, [externalFocusedCharacterId]);
  const visibleRows = rows.filter((row) => {
    const matchesKind = filter === "全部" || row.typeLabel.includes(filter);
    const matchesCharacter = focusedCharacterId === null || row.fromCharacterId === focusedCharacterId || row.toCharacterId === focusedCharacterId;
    return matchesKind && matchesCharacter;
  });

  const focusCharacter = (character: CharacterProfileView) => {
    // CharacterGraphWidget treats every node click as an explicit focus; the
    // blank canvas, not a second click on the same node, clears that focus.
    setFocusedCharacterId(character.id);
    setSelectedLinkId(null);
    onAction(action("relationship-focused", `已聚焦「${character.name}」的关系。`, character.id));
  };

  const clearFocus = () => {
    setFocusedCharacterId(null);
    setSelectedLinkId(null);
    onAction(action("relationship-focus-cleared", "已显示全部角色关系。"));
  };

  const focusLink = (link: RelationshipLinkView) => {
    setSelectedLinkId(link.id);
    setFocusedCharacterId(null);
    const row = rows.find((candidate) => candidate.id === link.id);
    onAction(action("relationship-selected", `已选择「${row?.from ?? "未知"}」与「${row?.to ?? "未知"}」的关系。`, link.id));
  };
  const selectedLink = visibleRows.find((link) => link.id === selectedLinkId) ?? null;
  const focusedCharacter = characters.find((character) => character.id === focusedCharacterId) ?? null;
  const removalLink = links.find((link) => link.id === removalLinkId);
  const removalSourceName = removalLink === undefined ? "未知" : characters.find((character) => character.id === removalLink.fromCharacterId)?.name ?? "未知";
  const removalTargetName = removalLink === undefined ? "未知" : characters.find((character) => character.id === removalLink.toCharacterId)?.name ?? "未知";
  const saveRelationship = (draft: { readonly evidence: string; readonly sourceCharacterId: string; readonly targetCharacterId: string; readonly typeLabel: string }) => {
    const result = upsertSessionRelationship(session, draft);
    onSessionChange(result.session);
    setSelectedLinkId(result.relationship.id);
    setFocusedCharacterId(null);
    const source = characters.find((character) => character.id === result.relationship.fromCharacterId)?.name ?? "未知";
    const target = characters.find((character) => character.id === result.relationship.toCharacterId)?.name ?? "未知";
    onAction(action(editor?.mode === "edit" ? "relationship-edit-requested" : "relationship-create-requested", `正在将「${source}」与「${target}」的关系提交给 Engine。`, result.relationship.id));
    setEditor(null);
  };
  const removeRelationship = () => {
    if (removalLink === undefined) return;
    onSessionChange(removeSessionRelationship(session, removalLink.id));
    setSelectedLinkId(null);
    setRemovalLinkId(null);
    onAction(action("relationship-remove-requested", "正在请求 Engine 移除该关系并刷新图谱。", removalLink.id));
  };

  return (
    <div className="relationship-workbench">
      <header>
        <div><h3>关系网络</h3><p>图谱为主视图；点击角色聚焦关联，筛选不会改变故事状态。</p></div>
        <div className="relationship-header-actions"><span>{visibleRows.length} 条关系</span><div className="relationship-view-toggle" role="group" aria-label="关系视图"><button className={viewMode === "graph" ? "is-active" : ""} onClick={() => setViewMode("graph")} type="button">图谱</button><button className={viewMode === "table" ? "is-active" : ""} onClick={() => setViewMode("table")} type="button">表格</button></div><button className="button button-primary" disabled={characters.length < 2} onClick={() => setEditor({ mode: "create" })} type="button">新增关系</button></div>
      </header>
      <div className="relationship-filters" role="group" aria-label="关系筛选">
        {(["全部", "同盟", "亲属", "师徒", "隐秘", "对抗"] as const).map((item) => <button className={filter === item ? "is-active" : ""} key={item} onClick={() => setFilter(item)} type="button">{item}</button>)}
      </div>
      {viewMode === "graph" ? <RelationshipGraphRenderer characterDetails={characterDetails} characters={characters} focusedCharacterId={focusedCharacterId} links={visibleRows} onClearFocus={clearFocus} onFocusCharacter={focusCharacter} onFocusLink={focusLink} selectedLinkId={selectedLinkId} /> : <div className="relationship-table" role="table" aria-label="关系表格"><div role="row"><span role="columnheader">角色</span><span role="columnheader">关系角色</span><span role="columnheader">类型</span><span role="columnheader">依据</span></div>{visibleRows.map((link) => <button className={link.id === selectedLinkId ? "is-selected" : ""} key={link.id} onClick={() => focusLink(link)} onDoubleClick={() => setEditor({ mode: "edit", relationship: link })} role="row" type="button"><strong role="cell">{link.from}</strong><strong role="cell">{link.to}</strong><span role="cell">{link.typeLabel}</span><small role="cell">{link.evidence}</small></button>)}{visibleRows.length === 0 && <p className="narrative-empty">没有匹配的关系。</p>}</div>}
      {viewMode === "graph" && <div className="relationship-list">
        {visibleRows.length === 0 ? <p className="narrative-empty">没有匹配的关系。</p> : visibleRows.map((link) => (
          <button className={link.id === selectedLinkId ? "is-selected" : ""} key={link.id} onClick={() => focusLink(link)} type="button"><strong>{link.from} <i>→</i> {link.to}</strong><span>{link.typeLabel}</span><p>{link.evidence}</p></button>
        ))}
      </div>}
      <aside className="relationship-inspector"><strong>{selectedLink !== null ? "关系详情" : focusedCharacter !== null ? "角色焦点" : "关系检查器"}</strong>{selectedLink !== null ? <><b>{selectedLink.from} ↔ {selectedLink.to}</b><span>{selectedLink.typeLabel}</span><p>{selectedLink.evidence}</p><div className="relationship-inspector-actions"><button className="button button-secondary" onClick={() => setEditor({ mode: "edit", relationship: selectedLink })} type="button">编辑关系</button><button className="button button-quiet" onClick={() => setRemovalLinkId(selectedLink.id)} type="button">移除关系</button></div></> : focusedCharacter !== null ? <><b>{focusedCharacter.name}</b><p>{focusedCharacter.summary}</p></> : <p>选择节点或连线可查看只读详情；图谱、表格和筛选共享同一份关系数据。</p>}</aside>
      {editor !== null && <RelationshipEditorDialog characters={characters} editor={editor} onClose={() => setEditor(null)} onSave={saveRelationship} />}
      {removalLink !== undefined && <AppDialog confirmLabel="移除" description={`移除「${removalSourceName}」与「${removalTargetName}」之间的关系？`} onClose={() => setRemovalLinkId(null)} onConfirm={removeRelationship} title="移除关系" tone="danger"><p className="narrative-dialog-note">确认后由 Engine 同步角色册、实体图谱、StoryKernel 与章节失效状态。</p></AppDialog>}
    </div>
  );
}

/** 关系编辑对话框（新增/编辑，源端 520×417 几何契约）。 */
export function RelationshipEditorDialog({ characters, editor, fixedSourceCharacterId, onClose, onSave, sourcePresentation = false }: { readonly characters: readonly CharacterProfileView[]; readonly editor: RelationshipEditorState; readonly fixedSourceCharacterId?: string; readonly onClose: () => void; readonly onSave: (draft: { readonly evidence: string; readonly sourceCharacterId: string; readonly targetCharacterId: string; readonly typeLabel: string }) => void; readonly sourcePresentation?: boolean }) {
  const initialSource = editor.mode === "edit"
    ? editor.relationship.fromCharacterId
    : fixedSourceCharacterId ?? editor.sourceCharacterId ?? characters[0]?.id ?? "";
  const initialTarget = editor.mode === "edit"
    ? editor.relationship.toCharacterId
    : editor.targetCharacterId ?? characters.find((character) => character.id !== initialSource)?.id ?? "";
  const initialTypeLabel = editor.mode === "edit" ? editor.relationship.typeLabel : editor.typeLabel ?? "一般关系";
  const sourceTypeLabel = sourcePresentation ? sourceRelationshipTypeLabel(initialTypeLabel) : initialTypeLabel;
  const [sourceCharacterId, setSourceCharacterId] = useState(initialSource);
  const [targetCharacterId, setTargetCharacterId] = useState(initialTarget);
  // The graph's display label may carry a prose suffix (for example
  // "同盟 · 不互信"). PySide resolves that back through its relationship
  // matrix before opening the edit dialog; mirror that canonical option here.
  const [typeLabel, setTypeLabel] = useState(sourceTypeLabel);
  const [evidence, setEvidence] = useState(editor.mode === "edit" ? editor.relationship.evidence : editor.evidence ?? "");
  const sourceName = characters.find((character) => character.id === sourceCharacterId)?.name ?? "—";
  const targetName = characters.find((character) => character.id === targetCharacterId)?.name ?? "—";
  const invalid = sourceCharacterId.length === 0 || targetCharacterId.length === 0 || sourceCharacterId === targetCharacterId || evidence.trim().length === 0;
  const availableTypeOptions = sourcePresentation || relationshipTypeOptions.includes(typeLabel as (typeof relationshipTypeOptions)[number])
    ? relationshipTypeOptions
    : [typeLabel, ...relationshipTypeOptions];

  const sourceIsFixed = fixedSourceCharacterId !== undefined;
  const save = () => onSave({ sourceCharacterId, targetCharacterId, typeLabel, evidence });
  const targetControl = <select aria-label="关系对象" disabled={!sourcePresentation && editor.mode === "edit"} onChange={(event) => setTargetCharacterId(event.target.value)} value={targetCharacterId}>{characters.filter((character) => character.id !== sourceCharacterId).map((character) => <option key={character.id} value={character.id}>{character.name}</option>)}</select>;
  const typeControl = <select aria-label="关系类型" onChange={(event) => setTypeLabel(event.target.value)} value={typeLabel}>{availableTypeOptions.map((type) => <option key={type}>{type}</option>)}</select>;
  const descriptionControl = <textarea aria-label="关系描述" onChange={(event) => setEvidence(event.target.value)} placeholder="关系描述，例如：彼此试探但在关键行动中互相掩护。" value={evidence} />;
  const validation = invalid && <p className="narrative-dialog-validation">{sourceCharacterId === targetCharacterId ? "关系双方必须是不同角色。" : "请补充关系描述。"}</p>;
  if (sourcePresentation) {
    return <AppDialog className="source-relationship-dialog" confirmDisabled={invalid} confirmLabel="保存" description={`${sourceName} 的关系`} onClose={onClose} onConfirm={save} size="standard" title="编辑关系"><div className="source-relationship-dialog-form"><label>对象</label>{targetControl}<label>类型</label>{typeControl}<label className="is-description">描述</label>{descriptionControl}{validation}</div></AppDialog>;
  }
  return <AppDialog confirmDisabled={invalid} confirmLabel="保存" description={editor.mode === "edit" ? "修改关系类型和描述后，由 Engine 同步完整角色状态。" : sourceIsFixed ? `为「${sourceName}」选择对象并补充具体描述。` : "选择两名角色并补充具体描述，创建一条 Engine 持久化关系。"} onClose={onClose} onConfirm={save} size="standard" title={editor.mode === "edit" ? "编辑关系" : "新增关系"}><div className="narrative-dialog-form">{!sourceIsFixed && <label>来源<select aria-label="关系来源" disabled={editor.mode === "edit"} onChange={(event) => setSourceCharacterId(event.target.value)} value={sourceCharacterId}>{characters.map((character) => <option key={character.id} value={character.id}>{character.name}</option>)}</select></label>}<label>对象{targetControl}</label><label>类型{typeControl}</label><label className="narrative-dialog-form-wide">描述{descriptionControl}</label>{validation}<p className="narrative-dialog-note narrative-dialog-form-wide">{sourceName} → {targetName}；确认后由 Engine 写入项目并刷新图谱。</p></div></AppDialog>;
}

/**
 * The relationship inner tab of PySide6's CharacterBibleEditor.  It is not
 * the standalone relationship-network page: the selected character stays as
 * the source, the table lists only that character's outgoing records, and a
 * compact detail card follows the selected row.
 */
export function SourceCharacterRelationshipWorkbench({ onAction, onEditCharacter, onSelectCharacter, onSessionChange, selectedCharacterId, session }: { readonly onAction: (action: NarrativeToolsAction) => void; readonly onEditCharacter: (character: CharacterProfileView) => void; readonly onSelectCharacter: (characterId: string) => void; readonly onSessionChange: (session: NarrativeToolsSession) => void; readonly selectedCharacterId: string; readonly session: NarrativeToolsSession }) {
  const { characters, relationships } = session;
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("全部状态");
  const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);
  const [editor, setEditor] = useState<RelationshipEditorState | null>(null);
  const [removalLinkId, setRemovalLinkId] = useState<string | null>(null);
  const visibleCharacters = characters.filter((character) => {
    const term = query.trim().toLocaleLowerCase();
    return (statusFilter === "全部状态" || character.statusLabel === statusFilter)
      && (term.length === 0 || `${character.name} ${character.role}`.toLocaleLowerCase().includes(term));
  });
  const selectedCharacter = visibleCharacters.find((character) => character.id === selectedCharacterId) ?? visibleCharacters[0];
  const rows = selectedCharacter === undefined
    ? []
    : relationships.filter((link) => link.fromCharacterId === selectedCharacter.id);
  const selectedLink = rows.find((link) => link.id === selectedLinkId) ?? rows[0];
  const removalLink = relationships.find((link) => link.id === removalLinkId);
  const targetName = selectedLink === undefined ? "" : characters.find((character) => character.id === selectedLink.toCharacterId)?.name ?? "未知";

  useEffect(() => {
    if (selectedCharacter !== undefined && selectedCharacter.id !== selectedCharacterId) onSelectCharacter(selectedCharacter.id);
  }, [onSelectCharacter, selectedCharacter, selectedCharacterId]);
  useEffect(() => {
    if (selectedLink !== undefined && selectedLink.id !== selectedLinkId) setSelectedLinkId(selectedLink.id);
    if (selectedLink === undefined && selectedLinkId !== null) setSelectedLinkId(null);
  }, [selectedLink, selectedLinkId]);

  const selectCharacter = (character: CharacterProfileView) => {
    onSelectCharacter(character.id);
    setSelectedLinkId(null);
    onAction(action("character-selected", `已切换到「${character.name}」的角色关系。`, character.id));
  };
  const selectLink = (link: RelationshipLinkView) => {
    setSelectedLinkId(link.id);
    const target = characters.find((character) => character.id === link.toCharacterId)?.name ?? "未知";
    onAction(action("relationship-selected", `已选择「${selectedCharacter?.name ?? "未知"}」与「${target}」的关系。`, link.id));
  };
  const saveRelationship = (draft: { readonly evidence: string; readonly sourceCharacterId: string; readonly targetCharacterId: string; readonly typeLabel: string }) => {
    const result = upsertSessionRelationship(session, draft);
    onSessionChange(result.session);
    setSelectedLinkId(result.relationship.id);
    const target = characters.find((character) => character.id === result.relationship.toCharacterId)?.name ?? "未知";
    onAction(action(editor?.mode === "edit" ? "relationship-edit-requested" : "relationship-create-requested", `正在将「${selectedCharacter?.name ?? "未知"}」与「${target}」的关系提交给 Engine。`, result.relationship.id));
    setEditor(null);
  };
  const removeRelationship = () => {
    if (removalLink === undefined) return;
    onSessionChange(removeSessionRelationship(session, removalLink.id));
    setRemovalLinkId(null);
    setSelectedLinkId(null);
    onAction(action("relationship-remove-requested", "正在请求 Engine 移除该关系并刷新图谱。", removalLink.id));
  };

  const relationType = (link: RelationshipLinkView) => link.typeLabel.split("·", 1)[0]?.trim() || "一般关系";
  const relationDescription = (link: RelationshipLinkView) => link.typeLabel.includes("·") ? link.typeLabel : link.evidence;

  return <div className="source-character-relationship-workbench">
    <aside aria-label="角色列表" className="source-character-relationship-sidebar">
      <label className="character-search"><span className="sr-only">搜索角色</span><input onChange={(event) => setQuery(event.target.value)} placeholder="搜索角色" type="search" value={query} /></label>
      <select aria-label="角色状态筛选" onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}><option>全部状态</option><option>活跃</option><option>缺席</option><option>退场</option></select>
      <div className="source-character-relationship-roster">{visibleCharacters.map((character) => <button className={character.id === selectedCharacter?.id ? "is-active" : ""} key={character.id} onClick={() => selectCharacter(character)} type="button"><strong>{character.name} · {nodeRoleForSourceEditor(character)}</strong></button>)}</div>
      {visibleCharacters.length === 0 && <p className="narrative-empty">没有匹配的角色。</p>}
    </aside>
    <section className="source-character-relationship-panel">
      <header><strong>关系</strong><div><button className="button button-secondary" disabled={selectedCharacter === undefined} onClick={() => setEditor({ mode: "create" })} type="button">新增关系</button><button className="button button-secondary" disabled={selectedLink === undefined} onClick={() => selectedLink !== undefined && setEditor({ mode: "edit", relationship: selectedLink })} type="button">编辑关系</button><button className="button button-secondary" disabled={selectedLink === undefined} onClick={() => setRemovalLinkId(selectedLink?.id ?? null)} type="button">移除关系</button><button className="button button-primary" disabled={selectedCharacter === undefined} onClick={() => selectedCharacter !== undefined && onEditCharacter(selectedCharacter)} type="button">编辑角色</button></div></header>
      <div aria-label="角色关系表格" className="source-character-relationship-table" role="table"><div role="row"><span role="columnheader">对象</span><span role="columnheader">类型</span><span role="columnheader">描述</span></div>{rows.map((link) => <button aria-selected={link.id === selectedLink?.id} className={link.id === selectedLink?.id ? "is-selected" : ""} key={link.id} onClick={() => selectLink(link)} onDoubleClick={() => setEditor({ mode: "edit", relationship: link })} role="row" type="button"><strong role="cell">{characters.find((character) => character.id === link.toCharacterId)?.name ?? "未知"}</strong><span role="cell">{relationType(link)}</span><small role="cell">{relationDescription(link)}</small></button>)}{rows.length === 0 && <p className="narrative-empty">该角色暂无关系记录。</p>}</div>
      <article className="source-character-relationship-detail"><strong>{selectedLink === undefined ? "关系详情" : `${selectedCharacter?.name ?? "未知"} ↔ ${targetName} · ${relationType(selectedLink)}`}</strong><p>{selectedLink === undefined ? "选择一条关系查看完整描述。" : relationDescription(selectedLink)}</p></article>
    </section>
    {editor !== null && <RelationshipEditorDialog characters={characters} editor={editor} onClose={() => setEditor(null)} onSave={saveRelationship} {...(selectedCharacter === undefined ? {} : { fixedSourceCharacterId: selectedCharacter.id })} />}
    {removalLink !== undefined && <AppDialog confirmLabel="移除" description={`移除「${selectedCharacter?.name ?? "未知"}」与「${characters.find((character) => character.id === removalLink.toCharacterId)?.name ?? "未知"}」之间的关系？`} onClose={() => setRemovalLinkId(null)} onConfirm={removeRelationship} title="移除关系" tone="danger"><p className="narrative-dialog-note">确认后由 Engine 同步角色册、实体图谱、StoryKernel 与章节失效状态。</p></AppDialog>}
  </div>;
}

/** 源端角色定位归一（图谱/关系列表共用）。 */
export function nodeRoleForSourceEditor(character: CharacterProfileView): string {
  if (character.role.includes("主视角") || character.role.includes("主角")) return "主角";
  if (character.role.includes("重要")) return "重要配角";
  if (character.role.includes("对手") || character.role.includes("反派")) return "对手";
  return "配角";
}

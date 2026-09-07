import { useEffect, useState } from "react";

import type { CharacterDetailView, CharacterProfileView, RelationshipLinkView } from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import { RelationshipGraphRenderer } from "../NarrativeVisualization";
import {
  addSessionCharacter,
  removeSessionRelationship,
  retireSessionCharacter,
  upsertSessionRelationship,
  type NarrativeToolsSession,
} from "../../lib/narrative-tools-session";
import { action, type NarrativeToolsAction } from "./types";
import { nodeRoleForSourceEditor, RelationshipEditorDialog, type RelationshipEditorState } from "./RelationshipWorkbench";

/**
 * ProjectsPage embeds the graph in CharacterBibleEditor's persistent split
 * view.  This deliberately differs from the standalone relationship network:
 * the source keeps the character roster visible while the canvas is open.
 */
export function CharacterGraphDetailContent({ character, detail, relationships, characters }: { readonly character: CharacterProfileView; readonly detail: CharacterDetailView | undefined; readonly relationships: readonly RelationshipLinkView[]; readonly characters: readonly CharacterProfileView[] }) {
  const related = relationships
    .filter((link) => link.fromCharacterId === character.id || link.toCharacterId === character.id)
    .slice(0, 5)
    .map((link) => {
      const relatedId = link.fromCharacterId === character.id ? link.toCharacterId : link.fromCharacterId;
      return { name: characters.find((candidate) => candidate.id === relatedId)?.name ?? relatedId, type: link.typeLabel };
    });
  return <section className="character-graph-detail-content">
    <div className="character-graph-detail-facts"><section><span>定位</span><strong>{nodeRoleForSourceEditor(character)}</strong></section><section><span>状态</span><strong>{character.statusLabel}</strong></section><section><span>时间线</span><strong>{detail?.timelineLabel || "未标注"}</strong></section></div>
    <section className="character-graph-detail-copy"><span>角色摘要</span><p>{character.summary || "当前角色尚无摘要。"}</p></section>
    <section className="character-graph-detail-copy"><span>人物弧光</span><p>{detail?.arc || character.arc || "尚未记录人物弧光。"}</p></section>
    {related.length > 0 && <section className="character-graph-detail-relations"><span>关键关系</span><div>{related.map((relation) => <p key={`${relation.name}-${relation.type}`}><b>{relation.name}</b><small>{relation.type}</small></p>)}</div></section>}
  </section>;
}

/** 源端角色图谱工作台（角色清单 + 可编辑关系图谱）。 */
export function SourceCharacterGraphWorkbench({ onAction, onEditCharacter, onSelectCharacter, onSessionChange, selectedCharacterId: controlledCharacterId, session }: { readonly onAction: (action: NarrativeToolsAction) => void; readonly onEditCharacter: (character: CharacterProfileView) => void; readonly onSelectCharacter: (characterId: string) => void; readonly onSessionChange: (session: NarrativeToolsSession) => void; readonly selectedCharacterId: string; readonly session: NarrativeToolsSession }) {
  const { characterDetails, characters, relationships } = session;
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("全部状态");
  const [selectedCharacterId, setSelectedCharacterId] = useState(controlledCharacterId || (characters[0]?.id ?? ""));
  const [graphFocusCharacterId, setGraphFocusCharacterId] = useState<string | null>(controlledCharacterId || (characters[0]?.id ?? null));
  const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [retireCharacterId, setRetireCharacterId] = useState<string | null>(null);
  const [relationshipEditor, setRelationshipEditor] = useState<RelationshipEditorState | null>(null);
  const [removalLinkId, setRemovalLinkId] = useState<string | null>(null);
  const [detailCharacterId, setDetailCharacterId] = useState<string | null>(null);
  const [newCharacter, setNewCharacter] = useState({ name: "", role: "配角", genderLabel: "", ageLabel: "" });
  const visibleCharacters = characters.filter((character) => {
    const term = query.trim().toLocaleLowerCase();
    return (statusFilter === "全部状态" || character.statusLabel === statusFilter)
      && (term.length === 0 || `${character.name} ${character.role}`.toLocaleLowerCase().includes(term));
  });
  const selectedCharacter = visibleCharacters.find((character) => character.id === selectedCharacterId) ?? visibleCharacters[0];
  const retiringCharacter = characters.find((character) => character.id === retireCharacterId);
  const removalLink = relationships.find((link) => link.id === removalLinkId);
  const detailCharacter = characters.find((character) => character.id === detailCharacterId);
  const detailForCharacter = detailCharacter === undefined ? undefined : characterDetails.find((detail) => detail.characterId === detailCharacter.id);

  useEffect(() => {
    if (controlledCharacterId.length > 0 && characters.some((character) => character.id === controlledCharacterId) && controlledCharacterId !== selectedCharacterId) {
      setSelectedCharacterId(controlledCharacterId);
      return;
    }
    if (selectedCharacter !== undefined && selectedCharacter.id !== selectedCharacterId) setSelectedCharacterId(selectedCharacter.id);
  }, [characters, controlledCharacterId, selectedCharacter, selectedCharacterId]);

  useEffect(() => {
    if (controlledCharacterId.length > 0 && characters.some((character) => character.id === controlledCharacterId)) setGraphFocusCharacterId(controlledCharacterId);
  }, [characters, controlledCharacterId]);

  const selectCharacter = (character: CharacterProfileView) => {
    setSelectedCharacterId(character.id);
    setGraphFocusCharacterId(character.id);
    onSelectCharacter(character.id);
    setSelectedLinkId(null);
    onAction(action("character-selected", `已聚焦「${character.name}」的角色图谱节点。`, character.id));
  };
  const selectLink = (link: RelationshipLinkView) => {
    setSelectedLinkId(link.id);
    setGraphFocusCharacterId(null);
    setSelectedCharacterId(link.fromCharacterId);
    const source = characters.find((character) => character.id === link.fromCharacterId)?.name ?? "未知";
    const target = characters.find((character) => character.id === link.toCharacterId)?.name ?? "未知";
    onAction(action("relationship-selected", `已选择「${source}」与「${target}」的关系。`, link.id));
  };
  const createCharacter = () => {
    const name = newCharacter.name.trim();
    if (name.length === 0 || characters.some((character) => character.name === name)) return;
    const created = addSessionCharacter(session, { ...newCharacter, name });
    onSessionChange(created.session);
    setSelectedCharacterId(created.character.id);
    onSelectCharacter(created.character.id);
    setAddDialogOpen(false);
    setNewCharacter({ name: "", role: "配角", genderLabel: "", ageLabel: "" });
    onAction(action("character-create-requested", `正在创建「${created.character.name}」并等待 Engine 刷新角色档案。`, created.character.id));
  };
  const retireCharacter = () => {
    if (retiringCharacter === undefined) return;
    onSessionChange(retireSessionCharacter(session, retiringCharacter.id));
    setRetireCharacterId(null);
    onAction(action("character-retire-requested", `已将「${retiringCharacter.name}」标记为退场；历史引用保持不变。`, retiringCharacter.id));
  };
  const editCharacter = (character: CharacterProfileView) => {
    onEditCharacter(character);
    onAction(action("character-edit-requested", `已打开「${character.name}」的角色档案。`, character.id));
  };
  const createRelationshipFromGraph = (source: CharacterProfileView, target: CharacterProfileView) => {
    const existing = relationships.find((link) => (
      (link.fromCharacterId === source.id && link.toCharacterId === target.id)
      || (link.fromCharacterId === target.id && link.toCharacterId === source.id)
    ));
    setRelationshipEditor({
      mode: "create",
      sourceCharacterId: source.id,
      targetCharacterId: target.id,
      ...(existing === undefined ? {} : { evidence: existing.evidence }),
    });
    setSelectedCharacterId(source.id);
    setGraphFocusCharacterId(source.id);
    onSelectCharacter(source.id);
    onAction(action("relationship-create-requested", `已从「${source.name}」拖向「${target.name}」，请补充关系类型与描述。`, source.id));
  };
  const saveGraphRelationship = (draft: { readonly evidence: string; readonly sourceCharacterId: string; readonly targetCharacterId: string; readonly typeLabel: string }) => {
    const result = upsertSessionRelationship(session, draft);
    onSessionChange(result.session);
    setRelationshipEditor(null);
    setSelectedLinkId(result.relationship.id);
    setGraphFocusCharacterId(null);
    const source = characters.find((character) => character.id === draft.sourceCharacterId)?.name ?? "未知";
    const target = characters.find((character) => character.id === draft.targetCharacterId)?.name ?? "未知";
    onAction(action("relationship-create-requested", `已保存「${source}」与「${target}」的关系草案。`, result.relationship.id));
  };
  const removeGraphRelationship = () => {
    if (removalLink === undefined) return;
    onSessionChange(removeSessionRelationship(session, removalLink.id));
    setRemovalLinkId(null);
    setSelectedLinkId(null);
    onAction(action("relationship-remove-requested", "正在请求 Engine 移除该关系并刷新图谱。", removalLink.id));
  };

  const sourceRole = (character: CharacterProfileView) => nodeRoleForSourceEditor(character);

  return <div className="source-character-graph-workbench">
    <aside aria-label="角色列表" className="source-character-graph-sidebar">
      <label className="character-search"><span className="sr-only">搜索角色</span><input onChange={(event) => setQuery(event.target.value)} placeholder="搜索角色" type="search" value={query} /></label>
      <select aria-label="角色状态筛选" onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}><option>全部状态</option><option>活跃</option><option>缺席</option><option>退场</option></select>
      <div className="source-character-graph-roster">{visibleCharacters.map((character) => <button className={character.id === selectedCharacter?.id ? "is-active" : ""} key={character.id} onClick={() => selectCharacter(character)} type="button"><strong>{character.name} · {sourceRole(character)}</strong></button>)}</div>
      {visibleCharacters.length === 0 && <p className="narrative-empty">没有匹配的角色。</p>}
      <button className="button button-secondary" onClick={() => setAddDialogOpen(true)} type="button">新增角色</button>
      <button className="button button-secondary" disabled={selectedCharacter === undefined || selectedCharacter.statusLabel === "退场"} onClick={() => setRetireCharacterId(selectedCharacter?.id ?? null)} type="button">标记退场</button>
    </aside>
    <header className="source-character-graph-toolbar"><button className="button button-secondary" disabled={selectedCharacter === undefined} onClick={() => setDetailCharacterId(selectedCharacter?.id ?? null)} type="button">查看详情</button><button className="button button-primary" disabled={selectedCharacter === undefined} onClick={() => {
      if (selectedCharacter === undefined) return;
      editCharacter(selectedCharacter);
    }} type="button">编辑角色</button><button className="button button-quiet" disabled type="button">放弃修改</button><button className="button button-primary" disabled type="button">保存修改</button></header>
    <article className="source-character-graph-canvas"><span>关系图谱</span>{characters.length === 0 && <p className="character-graph-empty" role="status">角色设定尚未生成。完成对应步骤后，档案和图谱会自动同步到这里。</p>}<RelationshipGraphRenderer characterDetails={characterDetails} characters={characters} focusedCharacterId={graphFocusCharacterId} links={relationships} onClearFocus={() => {
      setGraphFocusCharacterId(null);
      setSelectedLinkId(null);
      onAction(action("relationship-focus-cleared", "已清除图谱焦点；角色列表保持当前选中项。"));
    }} editable onCreateCharacter={() => setAddDialogOpen(true)} onCreateRelationship={createRelationshipFromGraph} onEditCharacter={editCharacter} onEditRelationship={(link) => setRelationshipEditor({ mode: "edit", relationship: link })} onFocusCharacter={selectCharacter} onFocusLink={selectLink} onRemoveRelationship={(link) => setRemovalLinkId(link.id)} onRetireCharacter={(character) => setRetireCharacterId(character.id)} selectedLinkId={selectedLinkId} sourceFrame /></article>
    {detailCharacter !== undefined && <AppDialog className="character-graph-detail-dialog" confirmLabel="完成" description={`${nodeRoleForSourceEditor(detailCharacter)} · ${detailCharacter.statusLabel} · 所有信息仅供当前项目查阅`} onClose={() => setDetailCharacterId(null)} title={`${detailCharacter.name} · 角色详情`}><CharacterGraphDetailContent character={detailCharacter} characters={characters} detail={detailForCharacter} relationships={relationships} /></AppDialog>}
    {addDialogOpen && <AppDialog className="narrative-entity-dialog" confirmDisabled={newCharacter.name.trim().length === 0 || characters.some((character) => character.name === newCharacter.name.trim())} confirmLabel="创建" description="创建后由 Engine 保存角色册、关系图谱与 StoryKernel。" onClose={() => setAddDialogOpen(false)} onConfirm={createCharacter} title="新增角色"><div className="narrative-dialog-form"><label>姓名<input aria-label="角色姓名" autoFocus onChange={(event) => setNewCharacter((current) => ({ ...current, name: event.target.value }))} placeholder="角色姓名" value={newCharacter.name} /></label><label>定位<select aria-label="角色定位" onChange={(event) => setNewCharacter((current) => ({ ...current, role: event.target.value }))} value={newCharacter.role}><option>主角</option><option>重要配角</option><option>对手</option><option>配角</option><option>次要角色</option></select></label><label>性别<input aria-label="新角色性别" onChange={(event) => setNewCharacter((current) => ({ ...current, genderLabel: event.target.value }))} value={newCharacter.genderLabel} /></label><label>年龄<input aria-label="新角色年龄" onChange={(event) => setNewCharacter((current) => ({ ...current, ageLabel: event.target.value }))} value={newCharacter.ageLabel} /></label></div></AppDialog>}
    {retiringCharacter !== undefined && <AppDialog className="narrative-entity-dialog" confirmLabel="标记退场" description={`将「${retiringCharacter.name}」标记为退场，不会删除任何历史引用或关系记录。`} onClose={() => setRetireCharacterId(null)} onConfirm={retireCharacter} title="标记角色退场"><p className="narrative-dialog-note">确认后由 Engine 同步角色册、实体图谱、StoryKernel 与章节失效状态。</p></AppDialog>}
    {relationshipEditor !== null && (() => {
      const fixedSourceCharacterId = relationshipEditor.mode === "create"
        ? relationshipEditor.sourceCharacterId
        : relationshipEditor.relationship.fromCharacterId;
      return <RelationshipEditorDialog characters={characters} editor={relationshipEditor} {...(fixedSourceCharacterId === undefined ? {} : { fixedSourceCharacterId })} onClose={() => setRelationshipEditor(null)} onSave={saveGraphRelationship} sourcePresentation />;
    })()}
    {removalLink !== undefined && <AppDialog confirmLabel="移除" description={`移除「${characters.find((character) => character.id === removalLink.fromCharacterId)?.name ?? "未知"}」与「${characters.find((character) => character.id === removalLink.toCharacterId)?.name ?? "未知"}」之间的关系？`} onClose={() => setRemovalLinkId(null)} onConfirm={removeGraphRelationship} title="移除关系" tone="danger" />}
  </div>;
}

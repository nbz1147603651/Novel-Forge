import { useCallback, useEffect, useLayoutEffect, useState } from "react";

import type { CharacterDetailView, CharacterProfileView, RelationshipLinkView } from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import {
  addSessionCharacter,
  retireSessionCharacter,
  saveSessionCharacterDetail,
  type NarrativeToolsSession,
} from "../../lib/narrative-tools-session";
import type { ReaderUnsavedSessionChange } from "../../lib/reader-unsaved-session";
import {
  action,
  type CharacterSessionDetail,
  type EditableCharacterField,
  type NarrativeToolsAction,
} from "./types";

function sessionDetail(detail: CharacterDetailView | undefined, character: CharacterProfileView): CharacterSessionDetail {
  return {
    timelineLabel: detail?.timelineLabel ?? "当前线",
    ageLabel: detail?.ageLabel ?? "—",
    genderLabel: detail?.genderLabel ?? "—",
    occupation: detail?.occupation ?? character.role,
    personality: detail?.personality ?? character.summary,
    backstory: detail?.backstory ?? "暂无背景资料。",
    abilities: detail?.abilities ?? "暂无能力资料。",
    appearance: detail?.appearance ?? "暂无外貌资料。",
    arc: detail?.arc ?? character.arc,
    voice: detail?.voice ?? "暂无声纹资料。",
    notes: detail?.notes ?? "",
  };
}

const characterRoleOptions = ["主角", "重要配角", "对手", "配角", "次要角色"] as const;

/** 角色档案工作台（mirrors PySide6 CharacterBibleEditor 档案页）。 */
export function CharacterWorkbench({ onAction, onSessionChange, onSourceCharacterSelected, onUnsavedSessionChange, requestedCharacterId, session }: { readonly onAction: (action: NarrativeToolsAction) => void; readonly onSessionChange: (session: NarrativeToolsSession) => void; readonly onSourceCharacterSelected?: (characterId: string) => void; readonly onUnsavedSessionChange?: ReaderUnsavedSessionChange; readonly requestedCharacterId?: string | null; readonly session: NarrativeToolsSession }) {
  const { characterDetails: details, characters, relationships } = session;
  const [characterId, setCharacterId] = useState(requestedCharacterId ?? characters[0]?.id ?? "");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("全部状态");
  const [draft, setDraft] = useState<{ readonly characterId: string; readonly values: CharacterSessionDetail } | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [retireCharacterId, setRetireCharacterId] = useState<string | null>(null);
  const [newCharacter, setNewCharacter] = useState({ name: "", role: "配角", genderLabel: "", ageLabel: "" });
  const visibleCharacters = characters.filter((character) => {
    const term = query.trim().toLocaleLowerCase();
    return (statusFilter === "全部状态" || character.statusLabel === statusFilter)
      && (term.length === 0 || `${character.name} ${character.role}`.toLocaleLowerCase().includes(term));
  });
  const selected = visibleCharacters.find((character) => character.id === characterId) ?? visibleCharacters[0];
  const sourceDetail = details.find((item) => item.characterId === selected?.id);
  const displayedDetail = selected === undefined ? undefined : sessionDetail(sourceDetail, selected);
  const editingDraft = draft !== null && draft.characterId === selected?.id ? draft : null;
  const isEditing = editingDraft !== null;
  const detail = editingDraft?.values ?? displayedDetail;

  useEffect(() => {
    if (requestedCharacterId === undefined || requestedCharacterId === null || !characters.some((character) => character.id === requestedCharacterId)) return;
    setCharacterId(requestedCharacterId);
    setDraft(null);
  }, [characters, requestedCharacterId]);

  const selectCharacter = (character: CharacterProfileView) => {
    setCharacterId(character.id);
    setDraft(null);
    onSourceCharacterSelected?.(character.id);
    onAction(action("character-selected", `已切换到「${character.name}」的角色档案。`, character.id));
  };
  const beginEdit = () => {
    if (selected === undefined || displayedDetail === undefined) return;
    setDraft({ characterId: selected.id, values: displayedDetail });
    onAction(action("character-edit-requested", `正在编辑「${selected.name}」；保存时将由 Engine 写入项目。`, selected.id));
  };
  const updateDraft = (field: EditableCharacterField, value: string) => {
    setDraft((current) => current === null ? current : { ...current, values: { ...current.values, [field]: value } });
  };
  const saveDraft = useCallback(() => {
    if (selected === undefined || draft === null || draft.characterId !== selected.id) return;
    onSessionChange(saveSessionCharacterDetail(session, { characterId: selected.id, ...draft.values }));
    setDraft(null);
    onAction(action("character-local-saved", `正在将「${selected.name}」的修改提交给 Engine。`, selected.id));
  }, [draft, onAction, onSessionChange, selected, session]);
  const discardDraft = useCallback(() => {
    if (selected === undefined) return;
    setDraft(null);
    onAction(action("character-edit-discarded", `已放弃「${selected.name}」本次未保存的前端修改。`, selected.id));
  }, [onAction, selected]);
  const createCharacter = () => {
    const name = newCharacter.name.trim();
    if (name.length === 0 || characters.some((character) => character.name === name)) return;
    const created = addSessionCharacter(session, { ...newCharacter, name });
    onSessionChange(created.session);
    setCharacterId(created.character.id);
    onSourceCharacterSelected?.(created.character.id);
    setAddDialogOpen(false);
    setNewCharacter({ name: "", role: "配角", genderLabel: "", ageLabel: "" });
    onAction(action("character-create-requested", `正在创建「${created.character.name}」并等待 Engine 刷新角色档案。`, created.character.id));
  };
  const retireCharacter = characters.find((character) => character.id === retireCharacterId);
  const confirmRetire = () => {
    if (retireCharacter === undefined) return;
    onSessionChange(retireSessionCharacter(session, retireCharacter.id));
    setRetireCharacterId(null);
    onAction(action("character-retire-requested", `已将「${retireCharacter.name}」标记为退场；历史引用保持不变。`, retireCharacter.id));
  };

  useLayoutEffect(() => {
    if (onUnsavedSessionChange === undefined) return;
    if (!isEditing) {
      onUnsavedSessionChange("characters", null);
      return;
    }
    onUnsavedSessionChange("characters", {
      cancelLabel: "继续编辑",
      discardLabel: "放弃修改",
      id: "characters",
      informativeText: "可以保存后继续，也可以放弃这次未保存修改。",
      message: "当前角色资料尚未提交给 Engine 保存。",
      onDiscard: discardDraft,
      onSave: saveDraft,
      saveLabel: "保存",
      title: "角色设定尚未保存",
    });
  }, [discardDraft, isEditing, onUnsavedSessionChange, saveDraft]);

  useEffect(() => () => onUnsavedSessionChange?.("characters", null), [onUnsavedSessionChange]);

  return (
    <div className="character-workbench">
      <header className="character-workbench-actions"><div><button className="button button-secondary" onClick={() => setAddDialogOpen(true)} type="button">新增角色</button><button className="button button-secondary" disabled={selected === undefined || selected.statusLabel === "退场"} onClick={() => setRetireCharacterId(selected?.id ?? null)} type="button">标记退场</button></div><div><button className="button button-secondary" onClick={() => setInspectorOpen((open) => !open)} type="button">{inspectorOpen ? "隐藏详情" : "显示详情"}</button>{isEditing ? <><button className="button button-secondary" onClick={discardDraft} type="button">放弃修改</button><button className="button button-primary" onClick={saveDraft} type="button">保存修改</button></> : <button className="button button-primary" disabled={selected === undefined} onClick={beginEdit} type="button">编辑</button>}</div></header>
      <div className={`character-workbench-grid${inspectorOpen ? "" : " is-inspector-hidden"}`}>
        <aside aria-label="角色列表"><div className="character-roster-filters"><label className="character-search"><span className="sr-only">搜索角色</span><input onChange={(event) => setQuery(event.target.value)} placeholder="搜索角色" type="search" value={query} /></label><select aria-label="角色状态筛选" onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}><option>全部状态</option><option>活跃</option><option>缺席</option><option>退场</option></select></div>
          {visibleCharacters.map((character) => (
            <button className={character.id === selected?.id ? "is-active" : ""} key={character.id} onClick={() => selectCharacter(character)} type="button"><strong>{character.name}</strong><small>{character.role} · {character.statusLabel}</small></button>
          ))}
          {visibleCharacters.length === 0 && <p className="narrative-empty">没有匹配的角色。</p>}
        </aside>
        <article>
          {selected === undefined ? <p>尚无角色档案。</p> : <>
            <header className="character-profile-heading"><div><span className="section-kicker">角色档案 · {detail?.timelineLabel ?? "当前线"}</span><h3>{selected.name}</h3></div><span className="character-status-pill">{selected.statusLabel}</span></header>
            <div className="character-identity-cards"><section><h4>定位</h4><p>{selected.role}</p></section><section><h4>时间线</h4><p>{detail?.timelineLabel ?? "当前线"}</p></section><section><h4>年龄</h4>{isEditing ? <input aria-label="年龄" onChange={(event) => updateDraft("ageLabel", event.target.value)} value={detail?.ageLabel ?? ""} /> : <p>{detail?.ageLabel ?? "—"}</p>}</section><section><h4>性别</h4>{isEditing ? <input aria-label="性别" onChange={(event) => updateDraft("genderLabel", event.target.value)} value={detail?.genderLabel ?? ""} /> : <p>{detail?.genderLabel ?? "—"}</p>}</section><section><h4>身份</h4>{isEditing ? <textarea aria-label="身份或职业" onChange={(event) => updateDraft("occupation", event.target.value)} value={detail?.occupation ?? ""} /> : <p>{detail?.occupation ?? selected.role}</p>}</section></div>
            <section><h4>人物弧线</h4>{isEditing ? <textarea aria-label="人物弧线" onChange={(event) => updateDraft("arc", event.target.value)} value={detail?.arc ?? ""} /> : <p>{detail?.arc ?? selected.arc}</p>}</section>
            <section><h4>背景 / 性格 / 能力</h4>{isEditing ? <div className="character-edit-stack"><label>背景<textarea aria-label="背景" onChange={(event) => updateDraft("backstory", event.target.value)} value={detail?.backstory ?? ""} /></label><label>性格<textarea aria-label="性格" onChange={(event) => updateDraft("personality", event.target.value)} value={detail?.personality ?? ""} /></label><label>能力 / 资源<textarea aria-label="能力或资源" onChange={(event) => updateDraft("abilities", event.target.value)} value={detail?.abilities ?? ""} /></label></div> : <p><b>【背景故事】</b>{detail?.backstory ?? "暂无背景资料。"}<br /><br /><b>【性格】</b>{detail?.personality ?? selected.summary}<br /><br /><b>【能力 / 资源】</b>{detail?.abilities ?? "暂无能力资料。"}</p>}</section>
            <section><h4>外貌 / 声纹 / 备注</h4>{isEditing ? <div className="character-edit-stack"><label>外貌<textarea aria-label="外貌" onChange={(event) => updateDraft("appearance", event.target.value)} value={detail?.appearance ?? ""} /></label><label>声纹<textarea aria-label="声纹" onChange={(event) => updateDraft("voice", event.target.value)} value={detail?.voice ?? ""} /></label><label>备注<textarea aria-label="备注" onChange={(event) => updateDraft("notes", event.target.value)} value={detail?.notes ?? ""} /></label></div> : <p><b>【外貌】</b>{detail?.appearance ?? "暂无外貌资料。"}<br /><br /><b>【声纹】</b>{detail?.voice ?? "暂无声纹资料。"}{(detail?.notes ?? "").length > 0 && <><br /><br /><b>【备注】</b>{detail?.notes}</>}</p>}</section>
            {!inspectorOpen && <CharacterInlineRelationships character={selected} characters={characters} relationships={relationships} />}
          </>}
        </article>
        {inspectorOpen && selected !== undefined && <CharacterInspector character={selected} characters={characters} relationships={relationships} />}
      </div>
      {addDialogOpen && <AppDialog confirmDisabled={newCharacter.name.trim().length === 0 || characters.some((character) => character.name === newCharacter.name.trim())} confirmLabel="创建" description="创建后将由 Engine 保存角色册、关系图谱与 StoryKernel。" onClose={() => setAddDialogOpen(false)} onConfirm={createCharacter} title="新增角色"><div className="narrative-dialog-form"><label>姓名<input aria-label="角色姓名" autoFocus onChange={(event) => setNewCharacter((current) => ({ ...current, name: event.target.value }))} placeholder="角色姓名" value={newCharacter.name} /></label><label>定位<select aria-label="角色定位" onChange={(event) => setNewCharacter((current) => ({ ...current, role: event.target.value }))} value={newCharacter.role}>{characterRoleOptions.map((role) => <option key={role}>{role}</option>)}</select></label><label>性别<input aria-label="新角色性别" onChange={(event) => setNewCharacter((current) => ({ ...current, genderLabel: event.target.value }))} value={newCharacter.genderLabel} /></label><label>年龄<input aria-label="新角色年龄" onChange={(event) => setNewCharacter((current) => ({ ...current, ageLabel: event.target.value }))} value={newCharacter.ageLabel} /></label>{characters.some((character) => character.name === newCharacter.name.trim()) && <p className="narrative-dialog-validation">角色名已存在，请使用一个新名称。</p>}</div></AppDialog>}
      {retireCharacter !== undefined && <AppDialog confirmLabel="标记退场" description={`将「${retireCharacter.name}」标记为退场，不会删除任何历史引用或关系记录。`} onClose={() => setRetireCharacterId(null)} onConfirm={confirmRetire} title="标记角色退场"><p className="narrative-dialog-note">确认后由 Engine 同步角色册、实体图谱、StoryKernel 与章节失效状态。</p></AppDialog>}
    </div>
  );
}

/** 角色关系检查器（右侧栏）。 */
function CharacterInspector({ character, characters, relationships }: { readonly character: CharacterProfileView; readonly characters: readonly CharacterProfileView[]; readonly relationships: readonly RelationshipLinkView[] }) {
  const related = relationships.filter((link) => link.fromCharacterId === character.id || link.toCharacterId === character.id).map((link) => {
    const relatedId = link.fromCharacterId === character.id ? link.toCharacterId : link.fromCharacterId;
    return { name: characters.find((candidate) => candidate.id === relatedId)?.name ?? "未知", typeLabel: link.typeLabel };
  });
  return <aside className="character-inspector" aria-label="角色关系检查器"><header><div><strong>关系脉络</strong><span>{related.length} 条关联</span></div></header><section><h4>当前关系</h4>{related.length === 0 ? <p>该角色暂无关联记录。</p> : <ul>{related.map((item) => <li key={`${item.name}-${item.typeLabel}`}><strong>{item.name}</strong><span>{item.typeLabel}</span></li>)}</ul>}</section></aside>;
}

/** 角色编辑器内联关系摘要（档案页收起详情时显示）。 */
function CharacterInlineRelationships({ character, characters, relationships }: { readonly character: CharacterProfileView; readonly characters: readonly CharacterProfileView[]; readonly relationships: readonly RelationshipLinkView[] }) {
  const related = relationships.filter((link) => link.fromCharacterId === character.id || link.toCharacterId === character.id);
  const rows = related.map((link) => {
    const isSource = link.fromCharacterId === character.id;
    const otherId = isSource ? link.toCharacterId : link.fromCharacterId;
    const otherName = characters.find((candidate) => candidate.id === otherId)?.name ?? "未知";
    return { id: link.id, otherName, typeLabel: link.typeLabel, evidence: link.evidence, direction: isSource ? "→" : "←" };
  });
  return <section className="character-inline-relationships" aria-label="角色关系摘要">
    <header><h4>关系</h4><span>{rows.length} 条</span></header>
    {rows.length === 0 ? <p className="narrative-empty">该角色暂无关系记录。可在「关系网络」工作台新增。</p> : <ul className="character-inline-relationship-list">{rows.map((row) => <li key={row.id}><span className="character-rel-direction">{row.direction}</span><strong>{row.otherName}</strong><span className="character-rel-type">{row.typeLabel}</span><small>{row.evidence}</small></li>)}</ul>}
  </section>;
}

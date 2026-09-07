import { useCallback, useMemo, useState } from "react";

/**
 * Character Bible Editor page (角色圣经编辑器).
 * Replicates PySide6 CharacterBibleEditor with character list, profile editing,
 * relationship matrix, and character graph visualization.
 */

// ── Constants ────────────────────────────────────────────────────────────
const ROLE_OPTIONS = [
  { value: "protagonist", label: "主角" },
  { value: "deuteragonist", label: "重要配角" },
  { value: "antagonist", label: "对手" },
  { value: "supporting", label: "配角" },
  { value: "minor", label: "次要角色" },
] as const;

const STATUS_OPTIONS = [
  { value: "active", label: "活跃" },
  { value: "dormant", label: "暂离" },
  { value: "retired", label: "退场" },
] as const;

const TIME_LAYER_OPTIONS = [
  { value: "default", label: "默认" },
  { value: "modern", label: "当前线" },
  { value: "past", label: "过去线" },
  { value: "cross_temporal", label: "跨时间线" },
  { value: "memory_only", label: "回忆线" },
] as const;

const RELATIONSHIP_TYPE_OPTIONS = [
  { value: "family", label: "亲属" },
  { value: "mentor", label: "师徒" },
  { value: "secret", label: "隐秘" },
  { value: "ally", label: "同盟" },
  { value: "antagonist", label: "对抗" },
  { value: "relationship", label: "一般关系" },
  { value: "romantic_tension", label: "情感张力" },
  { value: "rival", label: "竞争" },
] as const;

// ── Types ────────────────────────────────────────────────────────────────
interface CharacterProfile {
  readonly id: string;
  readonly name: string;
  readonly role: string;
  readonly status: string;
  readonly timeLayer: string;
  readonly gender: string;
  readonly age: string;
  readonly abilities: string;
  readonly appearance: string;
  readonly personality: string;
  readonly backstory: string;
  readonly arc: string;
  readonly voice: string;
  readonly notes: string;
}

interface Relationship {
  readonly source: string;
  readonly target: string;
  readonly type: string;
  readonly description: string;
}

// ── Mock data ────────────────────────────────────────────────────────────
const MOCK_CHARACTERS: readonly CharacterProfile[] = [
  {
    id: "c1",
    name: "林逐",
    role: "protagonist",
    status: "active",
    timeLayer: "default",
    gender: "男",
    age: "22",
    abilities: "梦境穿梭、逻辑推理",
    appearance: "清瘦青年，眼神锐利",
    personality: "冷静、执着、内心柔软",
    backstory: "自幼父母双亡，由姐姐抚养长大",
    arc: "从逃避到直面，从孤独到联结",
    voice: "低沉平稳，偶有急促",
    notes: "主角，核心视角人物",
  },
  {
    id: "c2",
    name: "林晚",
    role: "deuteragonist",
    status: "active",
    timeLayer: "default",
    gender: "女",
    age: "28",
    abilities: "医术、情报网络",
    appearance: "温婉知性，长发及腰",
    personality: "温柔、坚韧、保护欲强",
    backstory: "为保护弟弟放弃医学研究",
    arc: "从牺牲到自我实现",
    voice: "柔和舒缓，关切时语速加快",
    notes: "主角姐姐，重要配角",
  },
  {
    id: "c3",
    name: "陈半仙",
    role: "supporting",
    status: "active",
    timeLayer: "default",
    gender: "男",
    age: "55",
    abilities: "玄学、人脉",
    appearance: "邋遢老者，眼神狡黠",
    personality: "玩世不恭、深藏不露",
    backstory: "曾是知名学者，因事故隐退",
    arc: "从逃避到救赎",
    voice: "沙哑慵懒，偶尔犀利",
    notes: "导师型配角",
  },
];

const MOCK_RELATIONSHIPS: readonly Relationship[] = [
  { source: "c1", target: "c2", type: "family", description: "姐弟，相互扶持" },
  { source: "c1", target: "c3", type: "mentor", description: "师徒，传授技艺" },
  { source: "c2", target: "c3", type: "ally", description: "同盟，共同目标" },
];

// ── Dialog state ─────────────────────────────────────────────────────────
type DialogMode = "add" | "edit" | null;

export function CharacterBibleEditorPage() {
  const [characters, setCharacters] = useState<readonly CharacterProfile[]>(MOCK_CHARACTERS);
  const [relationships, setRelationships] = useState<readonly Relationship[]>(MOCK_RELATIONSHIPS);
  const [selectedId, setSelectedId] = useState<string | null>("c1");
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);
  const [dirty, setDirty] = useState(false);
  const [viewTab, setViewTab] = useState<"list" | "matrix" | "graph">("list");

  // Form state
  const [form, setForm] = useState<Partial<CharacterProfile>>({});

  const selectedCharacter = characters.find((c) => c.id === selectedId) ?? null;

  const openAddDialog = useCallback(() => {
    setDialogMode("add");
    setForm({
      name: "",
      role: "supporting",
      status: "active",
      timeLayer: "default",
      gender: "",
      age: "",
      abilities: "",
      appearance: "",
      personality: "",
      backstory: "",
      arc: "",
      voice: "",
      notes: "",
    });
  }, []);

  const openEditDialog = useCallback((character: CharacterProfile) => {
    setDialogMode("edit");
    setForm({ ...character });
  }, []);

  const handleFormChange = useCallback((field: keyof CharacterProfile, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  }, []);

  const handleSave = useCallback(() => {
    if (dialogMode === "add") {
      const newCharacter: CharacterProfile = {
        id: `c${Date.now()}`,
        name: form.name ?? "",
        role: form.role ?? "supporting",
        status: form.status ?? "active",
        timeLayer: form.timeLayer ?? "default",
        gender: form.gender ?? "",
        age: form.age ?? "",
        abilities: form.abilities ?? "",
        appearance: form.appearance ?? "",
        personality: form.personality ?? "",
        backstory: form.backstory ?? "",
        arc: form.arc ?? "",
        voice: form.voice ?? "",
        notes: form.notes ?? "",
      };
      setCharacters((prev) => [...prev, newCharacter]);
      setSelectedId(newCharacter.id);
    } else if (dialogMode === "edit" && form.id) {
      setCharacters((prev) =>
        prev.map((c) => (c.id === form.id ? { ...c, ...form } as CharacterProfile : c))
      );
    }
    setDialogMode(null);
    setDirty(true);
  }, [dialogMode, form]);

  const handleRetire = useCallback((id: string) => {
    setCharacters((prev) =>
      prev.map((c) => (c.id === id ? { ...c, status: "retired" } : c))
    );
    setDirty(true);
  }, []);

  const handleDelete = useCallback((id: string) => {
    setCharacters((prev) => prev.filter((c) => c.id !== id));
    setRelationships((prev) => prev.filter((r) => r.source !== id && r.target !== id));
    if (selectedId === id) setSelectedId(null);
    setDirty(true);
  }, [selectedId]);

  const getRoleLabel = (role: string) => ROLE_OPTIONS.find((r) => r.value === role)?.label ?? role;
  const getStatusLabel = (status: string) => STATUS_OPTIONS.find((s) => s.value === status)?.label ?? status;
  const getRelationshipLabel = (type: string) => RELATIONSHIP_TYPE_OPTIONS.find((r) => r.value === type)?.label ?? type;

  const relationshipMatrix = useMemo(() => {
    const matrix: Record<string, Record<string, string>> = {};
    for (const char of characters) {
      matrix[char.id] = {};
    }
    for (const rel of relationships) {
      const sourceRow = matrix[rel.source];
      if (sourceRow) {
        sourceRow[rel.target] = rel.type;
      }
    }
    return matrix;
  }, [characters, relationships]);

  return (
    <div className="character-bible-page">
      <header className="character-bible-header">
        <div>
          <h2>角色圣经编辑器</h2>
          <p>管理角色档案、关系矩阵与角色图谱。</p>
        </div>
        <button className="button button-primary" onClick={openAddDialog} type="button">
          + 新增角色
        </button>
      </header>

      {/* View tabs */}
      <div className="character-bible-tabs">
        <button
          className={`tab-btn${viewTab === "list" ? " is-active" : ""}`}
          onClick={() => setViewTab("list")}
          type="button"
        >
          角色列表
        </button>
        <button
          className={`tab-btn${viewTab === "matrix" ? " is-active" : ""}`}
          onClick={() => setViewTab("matrix")}
          type="button"
        >
          关系矩阵
        </button>
        <button
          className={`tab-btn${viewTab === "graph" ? " is-active" : ""}`}
          onClick={() => setViewTab("graph")}
          type="button"
        >
          角色图谱
        </button>
      </div>

      {/* List view */}
      {viewTab === "list" && (
        <div className="character-bible-body">
          <aside className="character-list">
            {characters.map((char) => (
              <button
                className={`character-item${selectedId === char.id ? " is-selected" : ""}${char.status === "retired" ? " is-retired" : ""}`}
                key={char.id}
                onClick={() => setSelectedId(char.id)}
                type="button"
              >
                <strong>{char.name}</strong>
                <small>{getRoleLabel(char.role)} · {getStatusLabel(char.status)}</small>
              </button>
            ))}
          </aside>

          <article className="character-detail">
            {selectedCharacter === null ? (
              <div className="character-empty">
                <h3>选择角色</h3>
                <p>从左侧列表选择角色查看详情。</p>
              </div>
            ) : (
              <>
                <header>
                  <h3>{selectedCharacter.name}</h3>
                  <div className="character-badges">
                    <span className={`badge-role is-${selectedCharacter.role}`}>{getRoleLabel(selectedCharacter.role)}</span>
                    <span className={`badge-status is-${selectedCharacter.status}`}>{getStatusLabel(selectedCharacter.status)}</span>
                  </div>
                </header>
                <div className="character-actions">
                  <button className="button button-secondary" onClick={() => openEditDialog(selectedCharacter)} type="button">
                    编辑
                  </button>
                  {selectedCharacter.status !== "retired" && (
                    <button className="button button-secondary" onClick={() => handleRetire(selectedCharacter.id)} type="button">
                      退休
                    </button>
                  )}
                  <button className="button button-secondary" onClick={() => handleDelete(selectedCharacter.id)} type="button">
                    删除
                  </button>
                </div>
                <dl className="character-fields">
                  <div><dt>性别</dt><dd>{selectedCharacter.gender || "—"}</dd></div>
                  <div><dt>年龄</dt><dd>{selectedCharacter.age || "—"}</dd></div>
                  <div><dt>时间层</dt><dd>{TIME_LAYER_OPTIONS.find((t) => t.value === selectedCharacter.timeLayer)?.label ?? selectedCharacter.timeLayer}</dd></div>
                  <div><dt>能力 / 资源</dt><dd>{selectedCharacter.abilities || "—"}</dd></div>
                  <div><dt>外貌</dt><dd>{selectedCharacter.appearance || "—"}</dd></div>
                  <div><dt>性格</dt><dd>{selectedCharacter.personality || "—"}</dd></div>
                  <div><dt>背景</dt><dd>{selectedCharacter.backstory || "—"}</dd></div>
                  <div><dt>角色弧光</dt><dd>{selectedCharacter.arc || "—"}</dd></div>
                  <div><dt>声纹</dt><dd>{selectedCharacter.voice || "—"}</dd></div>
                  <div><dt>备注</dt><dd>{selectedCharacter.notes || "—"}</dd></div>
                </dl>
              </>
            )}
          </article>
        </div>
      )}

      {/* Matrix view (Segment 48) */}
      {viewTab === "matrix" && (
        <div className="character-matrix-view">
          <table className="character-matrix">
            <thead>
              <tr>
                <th></th>
                {characters.map((char) => (
                  <th key={char.id}>{char.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {characters.map((rowChar) => (
                <tr key={rowChar.id}>
                  <th>{rowChar.name}</th>
                  {characters.map((colChar) => (
                    <td key={colChar.id}>
                      {rowChar.id === colChar.id ? (
                        <span className="matrix-self">—</span>
                      ) : relationshipMatrix[rowChar.id]?.[colChar.id] ? (
                        <span className="matrix-rel">{getRelationshipLabel(relationshipMatrix[rowChar.id]?.[colChar.id] ?? "")}</span>
                      ) : (
                        <span className="matrix-empty">·</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Graph view (Segment 48) */}
      {viewTab === "graph" && (
        <div className="character-graph-view">
          <h3>角色图谱可视化</h3>
          <p>CharacterGraphWidget 占位 — 显示角色节点与关系边</p>
          <div className="character-graph-mock">
            {characters.map((char) => (
              <div className="graph-character-node" key={char.id}>
                <strong>{char.name}</strong>
                <small>{getRoleLabel(char.role)}</small>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Add/Edit dialog */}
      {dialogMode !== null && (
        <div className="character-dialog-backdrop" onClick={() => setDialogMode(null)}>
          <div className="character-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>{dialogMode === "add" ? "新增角色" : "编辑角色"}</h3>
            <div className="character-form">
              <div className="character-form-row">
                <label>
                  姓名
                  <input onChange={(e) => handleFormChange("name", e.target.value)} value={form.name ?? ""} />
                </label>
                <label>
                  定位
                  <select onChange={(e) => handleFormChange("role", e.target.value)} value={form.role ?? "supporting"}>
                    {ROLE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="character-form-row">
                <label>
                  性别
                  <input onChange={(e) => handleFormChange("gender", e.target.value)} value={form.gender ?? ""} />
                </label>
                <label>
                  年龄
                  <input onChange={(e) => handleFormChange("age", e.target.value)} value={form.age ?? ""} />
                </label>
                <label>
                  状态
                  <select onChange={(e) => handleFormChange("status", e.target.value)} value={form.status ?? "active"}>
                    {STATUS_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  时间层
                  <select onChange={(e) => handleFormChange("timeLayer", e.target.value)} value={form.timeLayer ?? "default"}>
                    {TIME_LAYER_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                能力 / 资源
                <textarea onChange={(e) => handleFormChange("abilities", e.target.value)} rows={2} value={form.abilities ?? ""} />
              </label>
              <label>
                外貌
                <textarea onChange={(e) => handleFormChange("appearance", e.target.value)} rows={2} value={form.appearance ?? ""} />
              </label>
              <label>
                性格
                <textarea onChange={(e) => handleFormChange("personality", e.target.value)} rows={2} value={form.personality ?? ""} />
              </label>
              <label>
                背景
                <textarea onChange={(e) => handleFormChange("backstory", e.target.value)} rows={2} value={form.backstory ?? ""} />
              </label>
              <label>
                角色弧光
                <textarea onChange={(e) => handleFormChange("arc", e.target.value)} rows={2} value={form.arc ?? ""} />
              </label>
              <label>
                声纹
                <textarea onChange={(e) => handleFormChange("voice", e.target.value)} rows={2} value={form.voice ?? ""} />
              </label>
              <label>
                备注
                <textarea onChange={(e) => handleFormChange("notes", e.target.value)} rows={2} value={form.notes ?? ""} />
              </label>
            </div>
            <div className="character-dialog-footer">
              <button className="button button-secondary" onClick={() => setDialogMode(null)} type="button">
                取消
              </button>
              <button className="button button-primary" disabled={!form.name?.trim()} onClick={handleSave} type="button">
                保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SaveBar */}
      {dirty && (
        <div className="character-save-bar">
          <span>有未保存的更改</span>
          <div>
            <button className="button button-secondary" onClick={() => setDirty(false)} type="button">
              放弃
            </button>
            <button className="button button-primary" onClick={() => setDirty(false)} type="button">
              保存到 character_bible.json
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

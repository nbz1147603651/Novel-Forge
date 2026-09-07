import { useCallback, useMemo, useState } from "react";

/**
 * Subplot manager page (子情节管理).
 * Replicates PySide6 SubplotManager with CRUD operations, arc-to-subplot
 * conversion, and data binding to narrative_blueprint.json.
 */

// ── Constants ────────────────────────────────────────────────────────────
const LINK_TYPE_LABELS: Record<string, string> = {
  trigger_start: "触发启动",
  trigger_turn: "触发转折",
  constrain: "约束走向",
  enable: "提供条件",
  conflict: "制造冲突",
  feed_main: "反哺主线",
  reveal_key: "揭露关键",
  create_tension: "制造张力",
  theme_echo: "呼应主题",
};

const PRIORITY_LABELS: Record<string, string> = {
  primary: "准主线",
  normal: "常规",
  background: "背景",
};

const RESOLUTION_TYPE_LABELS: Record<string, string> = {
  resolve: "问题解决",
  reveal: "悬念揭示",
  ascend: "价值升华",
  merge: "并入主线",
};

// ── Types ────────────────────────────────────────────────────────────────
interface SubplotMilestone {
  readonly chapter: number;
  readonly description: string;
}

interface Subplot {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly priority: "primary" | "normal" | "background";
  readonly startChapter: number;
  readonly endChapter: number;
  readonly milestones: readonly SubplotMilestone[];
  readonly linkType: string;
  readonly resolutionType: string;
}

interface CharacterArc {
  readonly id: string;
  readonly characterName: string;
  readonly arcType: string;
  readonly milestones: readonly { chapter: number; description: string }[];
}

// ── Mock data ────────────────────────────────────────────────────────────
const MOCK_SUBPLOTS: readonly Subplot[] = [
  {
    id: "sp1",
    name: "林逐的成长之路",
    description: "主角从懵懂少年成长为独当一面的英雄",
    priority: "primary",
    startChapter: 1,
    endChapter: 20,
    milestones: [
      { chapter: 3, description: "初次觉醒能力" },
      { chapter: 8, description: "遭遇重大挫折" },
      { chapter: 15, description: "突破瓶颈" },
    ],
    linkType: "trigger_start",
    resolutionType: "ascend",
  },
  {
    id: "sp2",
    name: "姐姐的秘密",
    description: "姐姐隐藏的身份逐渐揭露",
    priority: "normal",
    startChapter: 5,
    endChapter: 18,
    milestones: [
      { chapter: 5, description: "神秘信件出现" },
      { chapter: 12, description: "身份线索浮现" },
    ],
    linkType: "reveal_key",
    resolutionType: "reveal",
  },
];

const MOCK_ARCS: readonly CharacterArc[] = [
  {
    id: "arc1",
    characterName: "陈半仙",
    arcType: "救赎弧光",
    milestones: [
      { chapter: 2, description: "以骗子身份登场" },
      { chapter: 10, description: "展现真实实力" },
      { chapter: 16, description: "为保护主角牺牲" },
    ],
  },
];

// ── Dialog state ─────────────────────────────────────────────────────────
type DialogMode = "add" | "edit" | null;

export function SubplotManagerPage() {
  const [subplots, setSubplots] = useState<readonly Subplot[]>(MOCK_SUBPLOTS);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set(["sp1"]));
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);
  const [editingSubplot, setEditingSubplot] = useState<Subplot | null>(null);
  const [dirty, setDirty] = useState(false);

  // Form state
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formPriority, setFormPriority] = useState<"primary" | "normal" | "background">("normal");
  const [formStartChapter, setFormStartChapter] = useState(1);
  const [formEndChapter, setFormEndChapter] = useState(10);
  const [formLinkType, setFormLinkType] = useState("trigger_start");
  const [formResolutionType, setFormResolutionType] = useState("resolve");

  const toggleExpanded = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const openAddDialog = useCallback(() => {
    setDialogMode("add");
    setEditingSubplot(null);
    setFormName("");
    setFormDescription("");
    setFormPriority("normal");
    setFormStartChapter(1);
    setFormEndChapter(10);
    setFormLinkType("trigger_start");
    setFormResolutionType("resolve");
  }, []);

  const openEditDialog = useCallback((subplot: Subplot) => {
    setDialogMode("edit");
    setEditingSubplot(subplot);
    setFormName(subplot.name);
    setFormDescription(subplot.description);
    setFormPriority(subplot.priority);
    setFormStartChapter(subplot.startChapter);
    setFormEndChapter(subplot.endChapter);
    setFormLinkType(subplot.linkType);
    setFormResolutionType(subplot.resolutionType);
  }, []);

  const handleSave = useCallback(() => {
    if (dialogMode === "add") {
      const newSubplot: Subplot = {
        id: `sp${Date.now()}`,
        name: formName,
        description: formDescription,
        priority: formPriority,
        startChapter: formStartChapter,
        endChapter: formEndChapter,
        milestones: [],
        linkType: formLinkType,
        resolutionType: formResolutionType,
      };
      setSubplots((prev) => [...prev, newSubplot]);
    } else if (dialogMode === "edit" && editingSubplot) {
      setSubplots((prev) =>
        prev.map((sp) =>
          sp.id === editingSubplot.id
            ? {
                ...sp,
                name: formName,
                description: formDescription,
                priority: formPriority,
                startChapter: formStartChapter,
                endChapter: formEndChapter,
                linkType: formLinkType,
                resolutionType: formResolutionType,
              }
            : sp
        )
      );
    }
    setDialogMode(null);
    setDirty(true);
  }, [dialogMode, editingSubplot, formName, formDescription, formPriority, formStartChapter, formEndChapter, formLinkType, formResolutionType]);

  const handleDelete = useCallback((id: string) => {
    setSubplots((prev) => prev.filter((sp) => sp.id !== id));
    setDirty(true);
  }, []);

  const handleConvertArc = useCallback((arc: CharacterArc) => {
    const newSubplot: Subplot = {
      id: `sp${Date.now()}`,
      name: `${arc.characterName}的${arc.arcType}`,
      description: `由角色弧光转换：${arc.characterName}的${arc.arcType}`,
      priority: "normal",
      startChapter: arc.milestones[0]?.chapter ?? 1,
      endChapter: arc.milestones[arc.milestones.length - 1]?.chapter ?? 10,
      milestones: arc.milestones,
      linkType: "theme_echo",
      resolutionType: "ascend",
    };
    setSubplots((prev) => [...prev, newSubplot]);
    setDirty(true);
  }, []);

  const sortedSubplots = useMemo(
    () => [...subplots].sort((a, b) => a.startChapter - b.startChapter),
    [subplots]
  );

  return (
    <div className="subplot-manager-page">
      <header className="subplot-manager-header">
        <div>
          <h2>子情节管理</h2>
          <p>管理叙事蓝图中的子情节线索，支持从角色弧光转换。</p>
        </div>
        <button className="button button-primary" onClick={openAddDialog} type="button">
          + 添加子情节
        </button>
      </header>

      {/* Subplot list */}
      <div className="subplot-list">
        {sortedSubplots.map((subplot) => (
          <details
            className={`subplot-item${expandedIds.has(subplot.id) ? " is-expanded" : ""}`}
            key={subplot.id}
            open={expandedIds.has(subplot.id)}
          >
            <summary onClick={() => toggleExpanded(subplot.id)}>
              <span className={`subplot-priority is-${subplot.priority}`}>
                {PRIORITY_LABELS[subplot.priority]}
              </span>
              <strong>{subplot.name}</strong>
              <small>
                第 {subplot.startChapter}-{subplot.endChapter} 章 ·{" "}
                {LINK_TYPE_LABELS[subplot.linkType] ?? subplot.linkType}
              </small>
              <div className="subplot-actions">
                <button
                  className="button button-secondary"
                  onClick={(e) => {
                    e.preventDefault();
                    openEditDialog(subplot);
                  }}
                  type="button"
                >
                  编辑
                </button>
                <button
                  className="button button-secondary"
                  onClick={(e) => {
                    e.preventDefault();
                    handleDelete(subplot.id);
                  }}
                  type="button"
                >
                  删除
                </button>
              </div>
            </summary>
            <div className="subplot-detail">
              <p>{subplot.description}</p>
              <div className="subplot-meta">
                <span>解决方式：{RESOLUTION_TYPE_LABELS[subplot.resolutionType] ?? subplot.resolutionType}</span>
              </div>
              {subplot.milestones.length > 0 && (
                <div className="subplot-milestones">
                  <h4>里程碑</h4>
                  <ul>
                    {subplot.milestones.map((milestone, index) => (
                      <li key={index}>
                        <span>第 {milestone.chapter} 章</span>
                        {milestone.description}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </details>
        ))}
      </div>

      {/* Arc conversion section (Segment 46) */}
      <section className="subplot-arc-conversion">
        <h3>从角色弧光转换</h3>
        <p>检测事件驱动的角色弧光，一键转换为子情节线索。</p>
        <div className="arc-list">
          {MOCK_ARCS.map((arc) => (
            <article className="arc-item" key={arc.id}>
              <div>
                <strong>{arc.characterName}</strong>
                <small>{arc.arcType} · {arc.milestones.length} 个里程碑</small>
              </div>
              <button
                className="button button-secondary"
                onClick={() => handleConvertArc(arc)}
                type="button"
              >
                转换为子情节
              </button>
            </article>
          ))}
        </div>
      </section>

      {/* Add/Edit dialog */}
      {dialogMode !== null && (
        <div className="subplot-dialog-backdrop" onClick={() => setDialogMode(null)}>
          <div className="subplot-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>{dialogMode === "add" ? "添加子情节" : "编辑子情节"}</h3>
            <div className="subplot-form">
              <label>
                名称
                <input onChange={(e) => setFormName(e.target.value)} value={formName} />
              </label>
              <label>
                描述
                <textarea onChange={(e) => setFormDescription(e.target.value)} rows={3} value={formDescription} />
              </label>
              <div className="subplot-form-row">
                <label>
                  优先级
                  <select onChange={(e) => setFormPriority(e.target.value as "primary" | "normal" | "background")} value={formPriority}>
                    {Object.entries(PRIORITY_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  起始章
                  <input min={1} onChange={(e) => setFormStartChapter(Number(e.target.value))} type="number" value={formStartChapter} />
                </label>
                <label>
                  结束章
                  <input min={1} onChange={(e) => setFormEndChapter(Number(e.target.value))} type="number" value={formEndChapter} />
                </label>
              </div>
              <div className="subplot-form-row">
                <label>
                  关联类型
                  <select onChange={(e) => setFormLinkType(e.target.value)} value={formLinkType}>
                    {Object.entries(LINK_TYPE_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  解决方式
                  <select onChange={(e) => setFormResolutionType(e.target.value)} value={formResolutionType}>
                    {Object.entries(RESOLUTION_TYPE_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
            <div className="subplot-dialog-footer">
              <button className="button button-secondary" onClick={() => setDialogMode(null)} type="button">
                取消
              </button>
              <button className="button button-primary" disabled={!formName.trim()} onClick={handleSave} type="button">
                保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SaveBar */}
      {dirty && (
        <div className="subplot-save-bar">
          <span>有未保存的更改</span>
          <div>
            <button className="button button-secondary" onClick={() => setDirty(false)} type="button">
              放弃
            </button>
            <button className="button button-primary" onClick={() => setDirty(false)} type="button">
              保存到 narrative_blueprint.json
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

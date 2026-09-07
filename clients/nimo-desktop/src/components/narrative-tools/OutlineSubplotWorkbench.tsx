import { useEffect, useMemo, useState } from "react";

import type {
  EngineCommandClient,
  NarrativeCharacterArcView,
  NarrativeSubplotInput,
  NarrativeToolsView,
  SubplotView,
} from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import { useAuthoring } from "../AuthoringWorkspace";
import { action, type NarrativeToolsAction } from "./types";

/** 大纲工作台（只读章节大纲 + Engine 延展任务）。 */
export function OutlineWorkbench({ commandClient, onAction, onRefresh, outline, projectId }: {
  readonly commandClient?: EngineCommandClient | undefined;
  readonly onAction: (action: NarrativeToolsAction) => void;
  readonly onRefresh?: (() => void) | undefined;
  readonly outline: NarrativeToolsView["outline"];
  readonly projectId?: string | undefined;
}) {
  const [isExtending, setIsExtending] = useState(false);
  const authoring = useAuthoring();
  const extendNextChapter = () => {
    if (commandClient === undefined || projectId === undefined) {
      onAction(action("outline-extend-requested", "当前 Engine 不支持提交大纲延展任务。"));
      return;
    }
    setIsExtending(true);
    void commandClient.extendOutline({
      kind: "extend_outline",
      projectId,
      additionalChapters: 1,
      decommissionOldEnding: false,
      syncContracts: true,
      reason: "nimo_extend_next_chapter",
    }).then((result) => {
      const tracking = result.taskId === undefined ? "" : `（任务 ${result.taskId}）`;
      onAction(action("outline-extend-requested", `${result.message}${tracking}`));
      if (authoring?.session?.configured) authoring.open();
      onRefresh?.();
    }).catch((error: unknown) => {
      onAction(action("outline-extend-requested", error instanceof Error ? error.message : "提交大纲延展任务失败。"));
    }).finally(() => {
      setIsExtending(false);
    });
  };
  return (
    <div className="outline-workbench">
      <header><h3>章节大纲</h3><button className="button button-secondary" disabled={isExtending} onClick={extendNextChapter} type="button">{isExtending ? "提交中…" : "延展下一章"}</button></header>
      {authoring?.session?.configured && <p>延长会先生成候选；专项批准前不改变全书目标或正式规划。</p>}
      {outline.map((node) => <article key={node.id}><span>{node.chapterLabel}</span><div><strong>{node.title}</strong><p>{node.summary}</p></div><small>{node.stateLabel}</small></article>)}
    </div>
  );
}

const priorityLabels = { primary: "准主线", normal: "常规", background: "背景" } as const;
const resolutionLabels = { "": "未指定", resolve: "问题解决", reveal: "悬念揭示", ascend: "价值升华", merge: "并入主线" } as const;

type SubplotEditorState = { readonly mode: "create" } | { readonly mode: "edit"; readonly subplot: SubplotView };
type AiDialogState = "prompt" | "review" | null;

function rangeFromPlan(plan: NarrativeSubplotInput, totalChapters: number) {
  const chapters = [...new Set([
    ...plan.involvedChapters,
    ...plan.chapterEvents.map((event) => event.chapterNumber),
  ])].filter((chapter) => chapter > 0).sort((a, b) => a - b);
  return {
    start: chapters[0] ?? 1,
    end: chapters.at(-1) ?? Math.max(1, totalChapters),
  };
}

function draftFor(subplot: SubplotView | undefined): NarrativeSubplotInput {
  if (subplot === undefined) {
    return {
      name: "",
      description: "",
      involvedChapters: [],
      chapterEvents: [],
      weaveLinks: [],
      priority: "normal",
      resolutionChapter: 0,
      resolutionTarget: "",
      resolutionType: "",
    };
  }
  return subplot.plan;
}

function planWithRange(plan: NarrativeSubplotInput, start: number, end: number): NarrativeSubplotInput {
  const eventChapters = plan.chapterEvents.map((event) => event.chapterNumber);
  const involvedChapters = [...new Set([...plan.involvedChapters, ...eventChapters, start, end])]
    .filter((chapter) => chapter > 0)
    .sort((a, b) => a - b);
  return { ...plan, involvedChapters };
}

function affectedChapterNumbers(subplots: readonly SubplotView[]): readonly number[] {
  return [...new Set(subplots.flatMap((subplot) => [
    ...subplot.plan.involvedChapters,
    ...subplot.plan.chapterEvents.map((event) => event.chapterNumber),
    subplot.plan.resolutionChapter,
  ]))]
    .filter((chapter) => chapter > 0)
    .sort((left, right) => left - right);
}

function chapterRangeFor(chapters: readonly number[]): string {
  if (chapters.length === 0) return "";
  return chapters.length === 1 ? String(chapters[0]) : `${chapters[0]}-${chapters.at(-1)}`;
}

function outlineSyncHintFor(subplots: readonly SubplotView[]): string {
  const lines = subplots.map((subplot) => {
    const plan = subplot.plan;
    const range = rangeFromPlan(plan, 0);
    const events = plan.chapterEvents.map((event) => `Ch.${event.chapterNumber}：${event.event}`).join("；") || "未配置节点";
    const links = plan.weaveLinks.map((link) => `Ch.${link.triggerChapter} ${link.linkType} → ${link.targetSubplot}：${link.description}`).join("；") || "未配置交织";
    const resolution = plan.resolutionChapter > 0
      ? `Ch.${plan.resolutionChapter} ${plan.resolutionType}：${plan.resolutionTarget}`
      : "未配置收束";
    return `【${plan.name}】范围 Ch.${range.start}–${range.end}\n描述：${plan.description}\n节点：${events}\n交织：${links}\n收束：${resolution}`;
  });
  return [
    "将以下已确认支线编排进受影响章节的大纲。保持主线节奏、已归档正文和既有关键转折不变；明确每章的 subplot_points、subplot_focus 与 beats_summary，不要把支线写成脱离主线的平行剧情。",
    ...lines,
  ].join("\n\n");
}

function chapterEventsText(events: NarrativeSubplotInput["chapterEvents"]): string {
  return events.map((event) => [
    event.chapterNumber,
    event.event,
    event.weaveNotes,
    event.dependsOn.join(","),
  ].join(" | ")).join("\n");
}

function parseChapterEvents(text: string): {
  readonly events: NarrativeSubplotInput["chapterEvents"];
  readonly error: string;
} {
  const events: Array<NarrativeSubplotInput["chapterEvents"][number]> = [];
  const invalidRows: number[] = [];
  for (const [index, rawLine] of text.split("\n").entries()) {
    const line = rawLine.trim();
    if (line.length === 0) continue;
    const [chapterText = "", event = "", weaveNotes = "", dependsOn = ""] = line.split("|").map((part) => part.trim());
    const chapterNumber = Number(chapterText);
    if (!Number.isInteger(chapterNumber) || chapterNumber < 1 || event.length === 0) {
      invalidRows.push(index + 1);
      continue;
    }
    events.push({
      chapterNumber,
      event,
      weaveNotes,
      dependsOn: dependsOn.split(",").map((item) => item.trim()).filter(Boolean),
    });
  }
  return {
    events,
    error: invalidRows.length === 0 ? "" : `章节节点第 ${invalidRows.join("、")} 行须填写“章节号 | 事件”。`,
  };
}

function weaveLinksText(links: NarrativeSubplotInput["weaveLinks"]): string {
  return links.map((link) => [
    link.triggerChapter,
    link.sourceType,
    link.sourceRef,
    link.targetSubplot,
    link.linkType,
    link.description,
  ].join(" | ")).join("\n");
}

function parseWeaveLinks(text: string): {
  readonly links: NarrativeSubplotInput["weaveLinks"];
  readonly error: string;
} {
  const links: Array<NarrativeSubplotInput["weaveLinks"][number]> = [];
  const invalidRows: number[] = [];
  for (const [index, rawLine] of text.split("\n").entries()) {
    const line = rawLine.trim();
    if (line.length === 0) continue;
    const [chapterText = "", sourceType = "", sourceRef = "", targetSubplot = "", linkType = "", description = ""] = line.split("|").map((part) => part.trim());
    const triggerChapter = Number(chapterText);
    if (!Number.isInteger(triggerChapter) || triggerChapter < 1 || linkType.length === 0) {
      invalidRows.push(index + 1);
      continue;
    }
    links.push({
      triggerChapter,
      sourceType: sourceType || "subplot",
      sourceRef,
      targetSubplot: targetSubplot || "主线",
      linkType,
      description,
    });
  }
  return {
    links,
    error: invalidRows.length === 0 ? "" : `交织关系第 ${invalidRows.join("、")} 行须填写“章节号 | 类型”。`,
  };
}

function subplotViewFor(plan: NarrativeSubplotInput, index: number, previous?: SubplotView): SubplotView {
  const { end, start } = rangeFromPlan(plan, 0);
  const resolution = plan.resolutionChapter > 0
    ? `第 ${plan.resolutionChapter} 章 · ${resolutionLabels[plan.resolutionType]} · ${plan.resolutionTarget || "未填写收束目标"}`
    : "尚未规划收束。";
  return {
    id: previous?.id ?? `local-subplot-${Date.now()}-${index}`,
    title: plan.name,
    priorityLabel: priorityLabels[plan.priority],
    chaptersLabel: plan.involvedChapters.length === 0 ? "未配置章节" : `第 ${start}–${end} 章`,
    description: plan.description,
    resolution,
    plan,
  };
}

function eventDrivenArc(arc: NarrativeCharacterArcView, totalChapters: number): boolean {
  const chapters = arc.milestones.flatMap((milestone) => [milestone.chapterStart, milestone.chapterEnd]).filter((chapter) => chapter > 0);
  if (chapters.length === 0) return false;
  const span = Math.max(...chapters) - Math.min(...chapters) + 1;
  if (span < Math.max(10, Math.floor(totalChapters * 0.2))) return false;
  const events = ["发现", "揭露", "对抗", "阴谋", "行动", "冲突", "危机", "背叛", "调查", "追踪", "谈判", "交易"];
  const internal = ["内心", "成长", "转变", "觉悟", "领悟", "释怀", "挣扎", "信念", "情感", "心理"];
  const copy = arc.milestones.map((milestone) => milestone.description);
  const eventScore = copy.reduce((score, item) => score + events.filter((keyword) => item.includes(keyword)).length, 0);
  const internalScore = copy.reduce((score, item) => score + internal.filter((keyword) => item.includes(keyword)).length, 0);
  return eventScore >= internalScore || internalScore <= 1;
}

interface SubplotWorkbenchProps {
  readonly blueprintRevision?: string | undefined;
  readonly commandClient?: EngineCommandClient | undefined;
  readonly onAction: (action: NarrativeToolsAction) => void;
  readonly onRefresh?: (() => void) | undefined;
  readonly projectId?: string | undefined;
  readonly sourceSubplots: readonly SubplotView[];
  readonly totalChapters: number;
  readonly visualization: NarrativeToolsView["visualization"];
}

/**
 * Durable counterpart of PySide6's SubplotManagerPanel.
 *
 * The only local state is modal/UI state. Every confirmed mutation crosses the
 * EngineCommandClient boundary and then refreshes the timeline projection.
 */
export function SubplotWorkbench({ blueprintRevision, commandClient, onAction, onRefresh, projectId, sourceSubplots, totalChapters, visualization }: SubplotWorkbenchProps) {
  const authoring = useAuthoring();
  const [subplots, setSubplots] = useState<readonly SubplotView[]>(sourceSubplots);
  const [currentRevision, setCurrentRevision] = useState(blueprintRevision);
  const [editor, setEditor] = useState<SubplotEditorState | null>(null);
  const [pendingSave, setPendingSave] = useState<{ readonly draft: NarrativeSubplotInput; readonly original?: SubplotView } | null>(null);
  const [removeSubplot, setRemoveSubplot] = useState<SubplotView | null>(null);
  const [arcDialogOpen, setArcDialogOpen] = useState(false);
  const [selectedArcIds, setSelectedArcIds] = useState<ReadonlySet<string>>(new Set());
  const [aiDialog, setAiDialog] = useState<AiDialogState>(null);
  const [aiHint, setAiHint] = useState("");
  const [aiCount, setAiCount] = useState(2);
  const [aiCandidates, setAiCandidates] = useState<readonly NarrativeSubplotInput[]>([]);
  const [selectedCandidateIndexes, setSelectedCandidateIndexes] = useState<ReadonlySet<number>>(new Set());
  const [outlineSyncOpen, setOutlineSyncOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setSubplots(sourceSubplots);
    setCurrentRevision(blueprintRevision);
  }, [blueprintRevision, sourceSubplots]);

  const canPersist = commandClient !== undefined && projectId !== undefined;
  const convertibleArcs = useMemo(
    () => (visualization.characterArcs ?? []).filter((arc) => eventDrivenArc(arc, totalChapters)),
    [totalChapters, visualization.characterArcs],
  );
  const impactedChapters = useMemo(() => affectedChapterNumbers(subplots), [subplots]);
  const impactedChapterRange = chapterRangeFor(impactedChapters);

  const reportUnavailable = () => onAction(action("subplot-command-unavailable", "当前引擎未提供支线写入命令；请连接本地写作引擎后再操作。"));
  const persist = async (plans: readonly NarrativeSubplotInput[], successMessage: string) => {
    if (!canPersist || commandClient === undefined || projectId === undefined) {
      reportUnavailable();
      return;
    }
    setSaving(true);
    try {
      const result = await commandClient.saveNarrativeSubplots({
        kind: "save_narrative_subplots",
        projectId,
        subplots: plans,
        ...(currentRevision === undefined ? {} : { expectedRevision: currentRevision }),
      });
      if (result.status !== "saved") {
        if (result.status === "candidate") authoring?.open();
        onAction(action("subplot-save-failed", result.message));
        return;
      }
      setSubplots(plans.map((plan, index) => subplotViewFor(plan, index, subplots[index])));
      setCurrentRevision(result.blueprintRevision ?? currentRevision);
      onAction(action("subplot-saved", `${successMessage} ${result.message}`));
      onRefresh?.();
    } catch (error) {
      onAction(action("subplot-save-failed", `保存支线失败：${error instanceof Error ? error.message : "未知错误"}`));
    } finally {
      setSaving(false);
    }
  };

  const confirmSave = () => {
    if (pendingSave === null) return;
    const next = pendingSave.original === undefined
      ? [...subplots.map((subplot) => subplot.plan), pendingSave.draft]
      : subplots.map((subplot) => subplot.id === pendingSave.original?.id ? pendingSave.draft : subplot.plan);
    setPendingSave(null);
    void persist(next, pendingSave.original === undefined ? `已新增支线「${pendingSave.draft.name}」。` : `已更新支线「${pendingSave.draft.name}」。`);
  };

  const confirmRemoval = () => {
    if (removeSubplot === null) return;
    const title = removeSubplot.title;
    const next = subplots.filter((subplot) => subplot.id !== removeSubplot.id).map((subplot) => subplot.plan);
    setRemoveSubplot(null);
    void persist(next, `已删除支线「${title}」。`);
  };

  const startArcConversion = () => {
    if (!canPersist) return reportUnavailable();
    if (convertibleArcs.length === 0) {
      onAction(action("subplot-convert-empty", "没有检测到可转换的事件驱动型角色弧光。"));
      return;
    }
    setSelectedArcIds(new Set(convertibleArcs.map((arc) => arc.id)));
    setArcDialogOpen(true);
  };

  const confirmArcConversion = async () => {
    if (!canPersist || commandClient === undefined || projectId === undefined) return reportUnavailable();
    if (selectedArcIds.size === 0) return;
    setSaving(true);
    try {
      const result = await commandClient.convertNarrativeArcsToSubplots({
        kind: "convert_narrative_arcs_to_subplots",
        projectId,
        arcIds: [...selectedArcIds],
        ...(currentRevision === undefined ? {} : { expectedRevision: currentRevision }),
      });
      if (result.status === "candidate") {
        onAction(action("subplot-arc-converted", result.message));
        authoring?.open();
      } else if (result.status === "saved") {
        setArcDialogOpen(false);
        setCurrentRevision(result.blueprintRevision ?? currentRevision);
        onAction(action("subplot-arc-converted", result.message));
        onRefresh?.();
      } else {
        onAction(action("subplot-arc-convert-failed", result.message));
      }
    } catch (error) {
      onAction(action("subplot-arc-convert-failed", `转换角色弧光失败：${error instanceof Error ? error.message : "未知错误"}`));
    } finally {
      setSaving(false);
    }
  };

  const generateCandidates = async () => {
    if (!canPersist || commandClient === undefined || projectId === undefined) return reportUnavailable();
    setSaving(true);
    try {
      const result = await commandClient.generateNarrativeSubplots({
        kind: "generate_narrative_subplots",
        projectId,
        userHint: aiHint,
        count: aiCount,
      });
      if (result.status !== "generated") {
        onAction(action("subplot-ai-generate-failed", result.message));
        return;
      }
      setAiCandidates(result.candidates);
      setSelectedCandidateIndexes(new Set(result.candidates.map((_, index) => index)));
      setAiDialog("review");
      onAction(action("subplot-ai-generated", result.message));
    } catch (error) {
      onAction(action("subplot-ai-generate-failed", `AI 支线生成失败：${error instanceof Error ? error.message : "未知错误"}`));
    } finally {
      setSaving(false);
    }
  };

  const saveSelectedCandidates = () => {
    const candidates = aiCandidates.filter((_, index) => selectedCandidateIndexes.has(index));
    if (candidates.length === 0) return;
    setAiDialog(null);
    void persist([...subplots.map((subplot) => subplot.plan), ...candidates], `已写入 ${candidates.length} 条 AI 支线候选。`);
  };

  const applyToOutline = async () => {
    if (!canPersist || commandClient === undefined || projectId === undefined) return reportUnavailable();
    if (impactedChapterRange.length === 0) {
      onAction(action("subplot-outline-impact-empty", "请先为至少一条支线配置章节范围、节点或收束章节。"));
      return;
    }
    setSaving(true);
    try {
      const result = await commandClient.polishOutline({
        kind: "polish_outline",
        projectId,
        chapterRange: impactedChapterRange,
        focusFields: ["subplot_points", "subplot_focus", "beats_summary", "goal"],
        syncContracts: true,
        userHint: outlineSyncHintFor(subplots),
      });
      if (result.status === "accepted") {
        setOutlineSyncOpen(false);
        onAction(action("subplot-outline-sync-submitted", result.message));
      } else {
        onAction(action("subplot-outline-sync-failed", result.message));
      }
    } catch (error) {
      onAction(action("subplot-outline-sync-failed", `提交章节大纲校准失败：${error instanceof Error ? error.message : "未知错误"}`));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-label="支线管理" className="subplot-workbench">
      <header>
        <div>
          <span className="section-kicker">叙事蓝图 · 可写入</span>
          <h3>支线管理</h3>
          <p>编辑节点、交织与收束后，提交章节大纲校准；已归档正文不会被改写。</p>
        </div>
        <div className="subplot-actions">
          <button className="button button-primary" disabled={saving || !canPersist || impactedChapters.length === 0} onClick={() => setOutlineSyncOpen(true)} type="button">应用到章节大纲</button>
          <button className="button button-secondary" disabled={saving || !canPersist} onClick={() => { setAiHint(""); setAiCount(2); setAiDialog("prompt"); }} type="button">AI 生成支线</button>
          <button className="button button-secondary" disabled={saving || !canPersist} onClick={startArcConversion} type="button">弧光转支线</button>
          <button className="button button-primary" disabled={saving || !canPersist} onClick={() => setEditor({ mode: "create" })} type="button">+ 新增支线</button>
        </div>
      </header>

      <div className="subplot-management-summary">
        <span>{subplots.length} 条已规划支线</span>
        <span>{impactedChapters.length} 个章节锚点</span>
        <span>{convertibleArcs.length} 条可转换角色弧光</span>
        <span>{currentRevision ? "蓝图数据已加载" : "等待引擎提供蓝图版本"}</span>
      </div>

      <div className="subplot-workbench-content">
        <div className="subplot-plans">
          <div className="subplot-list">
            {subplots.map((subplot) => <details key={subplot.id}>
              <summary>
                <span className={`subplot-priority is-${subplot.plan.priority}`}>{priorityLabels[subplot.plan.priority]}</span>
                <strong>{subplot.title}</strong>
                <small>{subplot.chaptersLabel} · {subplot.plan.chapterEvents.length} 个节点</small>
                <span className="subplot-summary-hint">查看节点与交织</span>
              </summary>
              <div className="subplot-detail">
                <p>{subplot.description || "尚未填写支线描述。"}</p>
                <dl>
                  <div><dt>收束</dt><dd>{subplot.resolution}</dd></div>
                  <div><dt>交织</dt><dd>{subplot.plan.weaveLinks.length} 条 · {subplot.plan.weaveLinks.map((link) => link.linkType || "未标注").join("、") || "尚未规划"}</dd></div>
                </dl>
                {subplot.plan.chapterEvents.length > 0 && <div className="subplot-detail-events"><h4>章节节点</h4><ol>{subplot.plan.chapterEvents.map((event) => <li key={event.chapterNumber}><span>Ch.{event.chapterNumber}</span><p>{event.event || "待补充事件"}</p></li>)}</ol></div>}
                {subplot.plan.weaveLinks.length > 0 && <div className="subplot-detail-links"><h4>交织关系</h4><ul>{subplot.plan.weaveLinks.map((link, index) => <li key={`${link.triggerChapter}-${link.linkType}-${index}`}><span>Ch.{link.triggerChapter || "—"}</span><b>{link.linkType || "未标注"}</b><p>{link.description || `${link.sourceRef || "主线"} → ${link.targetSubplot}`}</p></li>)}</ul></div>}
                <footer><button className="button button-secondary" disabled={saving || !canPersist} onClick={() => setEditor({ mode: "edit", subplot })} type="button">编辑支线</button><button className="button button-quiet" disabled={saving || !canPersist} onClick={() => setRemoveSubplot(subplot)} type="button">删除</button></footer>
              </div>
            </details>)}
          </div>
          {subplots.length === 0 && <p className="narrative-empty">当前没有支线。可以新增一条、由角色弧光转换，或让 AI 根据蓝图生成候选。</p>}
        </div>
        <aside aria-label="支线对章节大纲的影响" className="subplot-impact-panel">
          <header><div><span>大纲影响</span><strong>章节校准范围</strong></div><b>{impactedChapterRange.length > 0 ? `Ch.${impactedChapterRange}` : "待配置"}</b></header>
          <p>影响范围由已保存的章节锚点、节点与收束章节实时计算。</p>
          <ol>
            {subplots.map((subplot) => {
              const range = rangeFromPlan(subplot.plan, totalChapters);
              return <li key={subplot.id}><span>{`Ch.${range.start}–${range.end}`}</span><div><strong>{subplot.title}</strong><small>{subplot.plan.chapterEvents.length} 个节点 · {subplot.plan.weaveLinks.length} 条交织</small></div></li>;
            })}
          </ol>
          <footer>{impactedChapterRange.length > 0
            ? "提交“应用到章节大纲”后，会更新对应章节的支线焦点、节点与节拍，并同步章节契约。"
            : "先为支线配置范围、章节节点或收束章节，才能提交章节大纲校准。"}</footer>
        </aside>
      </div>

      {editor !== null && <SubplotEditorDialog editor={editor} onClose={() => setEditor(null)} onSave={(draft) => { setEditor(null); setPendingSave({ draft, ...(editor.mode === "edit" ? { original: editor.subplot } : {}) }); }} totalChapters={totalChapters} />}
      {pendingSave !== null && <AppDialog confirmDisabled={saving} confirmLabel="写入蓝图" description="这会原子更新 narrative_blueprint.json，并标记后续章节规划需要重新校准；不会改写任何已归档正文。" onClose={() => setPendingSave(null)} onConfirm={confirmSave} title={pendingSave.original === undefined ? "确认新增支线" : "确认更新支线"}><p className="narrative-dialog-note">{pendingSave.draft.name} · {pendingSave.draft.involvedChapters.length > 0 ? `覆盖 ${pendingSave.draft.involvedChapters.length} 个章节锚点` : "暂未设置章节锚点"}</p></AppDialog>}
      {outlineSyncOpen && <AppDialog confirmDisabled={saving} confirmLabel={saving ? "正在提交…" : "提交大纲校准"} description={`将提交第 ${impactedChapterRange} 章的支线编排任务：更新章节大纲中的支线焦点、节点与节拍，并同步章节契约；已归档正文不会改写。`} onClose={() => setOutlineSyncOpen(false)} onConfirm={() => void applyToOutline()} title="应用支线到章节大纲"><p className="narrative-dialog-note">本次覆盖 {impactedChapters.length} 个章节锚点。任务完成后可在「章节大纲」中审阅变更，再决定是否写作或重写章节。</p></AppDialog>}
      {removeSubplot !== null && <AppDialog confirmDisabled={saving} confirmLabel="删除并写入蓝图" description={`删除「${removeSubplot.title}」会移除其节点事件和交织关系；章节正文不会被删除。`} onClose={() => setRemoveSubplot(null)} onConfirm={confirmRemoval} title="确认删除支线" tone="danger"><p className="narrative-dialog-note">蓝图更新后，下游章节规划会被标记为待校准。</p></AppDialog>}
      {arcDialogOpen && <AppDialog confirmDisabled={saving || selectedArcIds.size === 0} confirmLabel="转换并写入蓝图" description="只会转换事件驱动型弧光；源角色弧光会保留，生成的支线可继续编辑。" onClose={() => setArcDialogOpen(false)} onConfirm={() => void confirmArcConversion()} size="wide" title="从角色弧光转换支线"><div className="subplot-selection-list">{convertibleArcs.map((arc) => <label key={arc.id}><input checked={selectedArcIds.has(arc.id)} onChange={() => setSelectedArcIds((current) => { const next = new Set(current); next.has(arc.id) ? next.delete(arc.id) : next.add(arc.id); return next; })} type="checkbox" /><span><strong>{arc.character}</strong><small>{arc.arcSummary}</small><em>{arc.milestones.length} 个里程碑</em></span></label>)}</div></AppDialog>}
      {aiDialog === "prompt" && <AppDialog confirmDisabled={saving} confirmLabel={saving ? "正在生成…" : "生成候选"} description="AI 只生成可审阅的支线草案；您选择后才会写入叙事蓝图。" onClose={() => setAiDialog(null)} onConfirm={() => void generateCandidates()} size="wide" title="AI 生成支线"><div className="narrative-dialog-form subplot-ai-form"><label className="narrative-dialog-form-wide">补充方向<textarea aria-label="AI 支线生成方向" onChange={(event) => setAiHint(event.target.value)} placeholder="例如：强化周砚的独立行动线，但不要抢走主角视角。" value={aiHint} /></label><label>生成数量<select aria-label="AI 生成数量" onChange={(event) => setAiCount(Number(event.target.value))} value={aiCount}>{[1, 2, 3].map((count) => <option key={count} value={count}>{count} 条</option>)}</select></label><p className="narrative-dialog-note narrative-dialog-form-wide">模型会依据现有阶段、关键转折、人物弧光与已存在支线生成外部事件线，并给出节点、交织和收束。</p></div></AppDialog>}
      {aiDialog === "review" && <AppDialog confirmDisabled={saving || selectedCandidateIndexes.size === 0} confirmLabel={`写入 ${selectedCandidateIndexes.size} 条候选`} description="逐条审阅后选择要纳入蓝图的候选；未选择的内容不会保存。" onClose={() => setAiDialog(null)} onConfirm={saveSelectedCandidates} size="wide" title="审阅 AI 支线候选"><div className="subplot-candidate-list">{aiCandidates.map((candidate, index) => <label key={`${candidate.name}-${index}`}><input checked={selectedCandidateIndexes.has(index)} onChange={() => setSelectedCandidateIndexes((current) => { const next = new Set(current); next.has(index) ? next.delete(index) : next.add(index); return next; })} type="checkbox" /><span><strong>{candidate.name}</strong><p>{candidate.description}</p><small>Ch.{rangeFromPlan(candidate, totalChapters).start}–{rangeFromPlan(candidate, totalChapters).end} · {candidate.chapterEvents.length} 个节点 · {candidate.weaveLinks.length} 条交织</small></span></label>)}</div></AppDialog>}
    </section>
  );
}

/** 支线编辑对话框：编辑计划、章节节点与交织关系，而非只改展示文本。 */
function SubplotEditorDialog({ editor, onClose, onSave, totalChapters }: {
  readonly editor: SubplotEditorState;
  readonly onClose: () => void;
  readonly onSave: (draft: NarrativeSubplotInput) => void;
  readonly totalChapters: number;
}) {
  const [draft, setDraft] = useState(() => draftFor(editor.mode === "edit" ? editor.subplot : undefined));
  const [eventsText, setEventsText] = useState(() => chapterEventsText(draft.chapterEvents));
  const [linksText, setLinksText] = useState(() => weaveLinksText(draft.weaveLinks));
  const range = rangeFromPlan(draft, totalChapters);
  const [start, setStart] = useState(range.start);
  const [end, setEnd] = useState(range.end);
  const maxChapter = Math.max(999, totalChapters);
  const parsedEvents = parseChapterEvents(eventsText);
  const parsedLinks = parseWeaveLinks(linksText);
  const invalidRange = start < 1 || end < start;
  const invalid = draft.name.trim().length === 0
    || invalidRange
    || (draft.resolutionChapter > 0 && draft.resolutionChapter < start)
    || parsedEvents.error.length > 0
    || parsedLinks.error.length > 0;
  const update = <Key extends keyof NarrativeSubplotInput>(key: Key, value: NarrativeSubplotInput[Key]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };
  const validationMessage = draft.name.trim().length === 0
    ? "请填写支线名称。"
    : parsedEvents.error || parsedLinks.error || (draft.resolutionChapter > 0 && draft.resolutionChapter < start)
      ? parsedEvents.error || parsedLinks.error || "收束章节不能早于支线开始章节。"
      : "结束章节必须不早于起始章节。";

  return <AppDialog
    confirmDisabled={invalid}
    confirmLabel="继续保存"
    description={editor.mode === "create"
      ? "新支线会先进入保存确认，随后原子写入叙事蓝图。"
      : "修改后的节点与交织关系会随支线计划一并保存，并用于章节大纲校准。"}
    onClose={onClose}
    onConfirm={() => onSave(planWithRange({
      ...draft,
      name: draft.name.trim(),
      description: draft.description.trim(),
      resolutionTarget: draft.resolutionTarget.trim(),
      chapterEvents: parsedEvents.events,
      weaveLinks: parsedLinks.links,
    }, start, end))}
    size="wide"
    title={editor.mode === "create" ? "新增支线" : `编辑支线 · ${editor.subplot.title}`}
  >
    <div className="narrative-dialog-form subplot-editor-form">
      <label className="narrative-dialog-form-wide">名称
        <input aria-label="支线名称" autoFocus onChange={(event) => update("name", event.target.value)} placeholder="支线名称" value={draft.name} />
      </label>
      <label>优先级
        <select aria-label="支线优先级" onChange={(event) => update("priority", event.target.value as NarrativeSubplotInput["priority"])} value={draft.priority}>
          {Object.entries(priorityLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>收束章节
        <input aria-label="支线收束章节" max={maxChapter} min="0" onChange={(event) => update("resolutionChapter", Number(event.target.value))} type="number" value={draft.resolutionChapter} />
      </label>
      <label className="narrative-dialog-form-wide">描述
        <textarea aria-label="支线描述" onChange={(event) => update("description", event.target.value)} placeholder="说明这条线如何推动主线、角色或世界状态。" value={draft.description} />
      </label>
      <fieldset className="subplot-range">
        <legend>章节范围</legend>
        <label>起始章节
          <input aria-label="支线起始章节" max={maxChapter} min="1" onChange={(event) => setStart(Number(event.target.value))} type="number" value={start} />
        </label>
        <label>结束章节
          <input aria-label="支线结束章节" max={maxChapter} min="1" onChange={(event) => setEnd(Number(event.target.value))} type="number" value={end} />
        </label>
      </fieldset>
      <label>收束类型
        <select aria-label="支线收束类型" onChange={(event) => update("resolutionType", event.target.value as NarrativeSubplotInput["resolutionType"])} value={draft.resolutionType}>
          {Object.entries(resolutionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="narrative-dialog-form-wide">收束目标
        <textarea aria-label="支线收束目标" onChange={(event) => update("resolutionTarget", event.target.value)} placeholder="例如：在关键选择中将新证据并入主线。" value={draft.resolutionTarget} />
      </label>
      <label className="narrative-dialog-form-wide">章节节点
        <textarea aria-label="支线章节节点" onChange={(event) => setEventsText(event.target.value)} placeholder="3 | 证人交出账本 | 引入新证据 | 主线线索" value={eventsText} />
      </label>
      <p className="narrative-dialog-note narrative-dialog-form-wide">每行：章节号 | 事件 | 交织备注 | 依赖项（逗号分隔）。章节节点会成为对应章节大纲的支线节拍。</p>
      <label className="narrative-dialog-form-wide">交织关系
        <textarea aria-label="支线交织关系" onChange={(event) => setLinksText(event.target.value)} placeholder="16 | subplot | 账本 | 主线 | reveal_key | 账本证据改变公开策略" value={linksText} />
      </label>
      <p className="narrative-dialog-note narrative-dialog-form-wide">每行：章节号 | 来源类型 | 来源引用 | 目标支线 | 关系类型 | 说明。目标可填“主线”。</p>
      {invalid && <p className="narrative-dialog-validation narrative-dialog-form-wide">{validationMessage}</p>}
    </div>
  </AppDialog>;
}

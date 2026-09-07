import { useEffect, useMemo, useRef, useState } from "react";

import type { EngineCommandClient, HumanizeLibraryMutationResult, HumanizePatternView } from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import { action, type NarrativeToolsAction } from "./types";

interface HumanizePatternRecord extends HumanizePatternView {
  readonly examplePhrase: string;
  readonly keywords: readonly string[];
  readonly notes: string;
}

interface HumanizePatternDraft {
  readonly category: string;
  readonly examplePhrase: string;
  readonly id: string;
  readonly keywords: readonly string[];
  readonly name: string;
  readonly notes: string;
  readonly severity: string;
}

interface HumanizeDuplicatePair {
  readonly left: HumanizePatternRecord;
  readonly right: HumanizePatternRecord;
  readonly similarity: number;
}

type HumanizeEditorState =
  | { readonly mode: "create"; readonly pattern?: undefined }
  | { readonly mode: "edit"; readonly pattern: HumanizePatternRecord };

interface PendingHumanizeMerge {
  readonly source: HumanizePatternRecord;
  readonly target: HumanizePatternRecord;
}

function recordFor(pattern: HumanizePatternView): HumanizePatternRecord {
  return {
    ...pattern,
    examplePhrase: pattern.examplePhrase ?? "",
    keywords: pattern.keywords ?? [],
    notes: pattern.notes ?? "",
  };
}

function defaultHumanizeDraft(): HumanizePatternDraft {
  return { id: "", name: "", category: "", severity: "中", keywords: [], notes: "", examplePhrase: "" };
}

function patternDraft(pattern: HumanizePatternRecord): HumanizePatternDraft {
  return {
    id: pattern.id,
    name: pattern.name,
    category: pattern.category,
    severity: pattern.severity,
    keywords: pattern.keywords,
    notes: pattern.notes,
    examplePhrase: pattern.examplePhrase,
  };
}

function canDeleteOrMergeHumanizePattern(pattern: HumanizePatternRecord): boolean {
  return pattern.sourceLabel !== "内置";
}

function normalizeTerms(value: string): readonly string[] {
  return value.toLocaleLowerCase().split(/[^\p{L}\p{N}]+/u).map((term) => term.trim()).filter(Boolean);
}

function findHumanizeDuplicates(patterns: readonly HumanizePatternRecord[], minimumSimilarity = 0.36): readonly HumanizeDuplicatePair[] {
  const pairs: HumanizeDuplicatePair[] = [];
  for (let leftIndex = 0; leftIndex < patterns.length; leftIndex += 1) {
    const left = patterns[leftIndex];
    if (left === undefined) continue;
    const leftTerms = new Set([...
      normalizeTerms(left.name),
      ...normalizeTerms(left.category),
      ...left.keywords.flatMap(normalizeTerms),
      ...normalizeTerms(left.notes),
    ]);
    for (let rightIndex = leftIndex + 1; rightIndex < patterns.length; rightIndex += 1) {
      const right = patterns[rightIndex];
      if (right === undefined) continue;
      const rightTerms = new Set([...
        normalizeTerms(right.name),
        ...normalizeTerms(right.category),
        ...right.keywords.flatMap(normalizeTerms),
        ...normalizeTerms(right.notes),
      ]);
      const shared = [...leftTerms].filter((term) => rightTerms.has(term)).length;
      const combined = new Set([...leftTerms, ...rightTerms]).size;
      const similarity = left.name.trim().toLocaleLowerCase() === right.name.trim()
        ? 1
        : combined === 0 ? 0 : shared / combined;
      if (similarity >= minimumSimilarity) pairs.push({ left, right, similarity });
    }
  }
  return pairs.sort((left, right) => right.similarity - left.similarity);
}

function exportHumanizePatterns(patterns: readonly HumanizePatternRecord[]): string {
  return JSON.stringify({ format: "nimo-humanize-library/v2", patterns }, null, 2);
}

function parseHumanizeImport(payload: string): readonly HumanizePatternDraft[] {
  const parsed: unknown = JSON.parse(payload);
  const candidates: unknown[] | null = Array.isArray(parsed)
    ? parsed
    : typeof parsed === "object" && parsed !== null && Array.isArray((parsed as { patterns?: unknown }).patterns)
      ? (parsed as { patterns: unknown[] }).patterns
      : null;
  if (candidates === null) throw new Error("导入内容必须是模式数组，或包含 patterns 数组的对象。");
  return candidates.flatMap((candidate) => {
    if (typeof candidate !== "object" || candidate === null) return [];
    const item = candidate as Partial<HumanizePatternRecord>;
    if (typeof item.name !== "string" || item.name.trim().length === 0) return [];
    return [{
      id: typeof item.id === "string" ? item.id : "",
      name: item.name.trim(),
      category: typeof item.category === "string" ? item.category : "",
      severity: typeof item.severity === "string" ? item.severity : "medium",
      keywords: Array.isArray(item.keywords) ? item.keywords.filter((term): term is string => typeof term === "string") : [],
      notes: typeof item.notes === "string" ? item.notes : "",
      examplePhrase: typeof item.examplePhrase === "string" ? item.examplePhrase : "",
    }];
  });
}

/**
 * Engine-backed counterpart of PySide6's HumanizeLibraryDashboard.
 *
 * The component owns only selection, filters and form drafts.  Every confirmed
 * library mutation crosses ``EngineCommandClient`` and the Engine read model is
 * refreshed before another mutation is permitted.
 */
export function HumanizeWorkbench({ commandClient, humanizeLibraryRevision, onAction, onRefresh, patterns, projectId }: {
  readonly commandClient?: EngineCommandClient | undefined;
  readonly humanizeLibraryRevision?: string | undefined;
  readonly onAction: (action: NarrativeToolsAction) => void;
  readonly onRefresh?: (() => void) | undefined;
  readonly patterns: readonly HumanizePatternView[];
  readonly projectId?: string | undefined;
}) {
  const [displayPatterns, setDisplayPatterns] = useState<readonly HumanizePatternRecord[]>(() => patterns.map(recordFor));
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("全部");
  const [categoryFilter, setCategoryFilter] = useState("全部");
  const [severityFilter, setSeverityFilter] = useState("全部");
  const [selectedPatternId, setSelectedPatternId] = useState<string | null>(null);
  const [editor, setEditor] = useState<HumanizeEditorState | null>(null);
  const [removePatternId, setRemovePatternId] = useState<string | null>(null);
  const [mergeSourceId, setMergeSourceId] = useState<string | null>(null);
  const [pendingMerge, setPendingMerge] = useState<PendingHumanizeMerge | null>(null);
  const [duplicatesOpen, setDuplicatesOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [hitExamplesId, setHitExamplesId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const revisionRef = useRef(humanizeLibraryRevision ?? "");
  const awaitingRefreshRef = useRef(false);
  const sourceOptions = useMemo(() => ["全部", ...new Set(displayPatterns.map((pattern) => pattern.sourceLabel))], [displayPatterns]);
  const categoryOptions = useMemo(() => ["全部", ...new Set(displayPatterns.map((pattern) => pattern.category))], [displayPatterns]);
  const severityOptions = useMemo(() => ["全部", ...new Set(displayPatterns.map((pattern) => pattern.severity))], [displayPatterns]);
  const visiblePatterns = displayPatterns.filter((pattern) => {
    const term = query.trim().toLocaleLowerCase();
    return (sourceFilter === "全部" || pattern.sourceLabel === sourceFilter)
      && (categoryFilter === "全部" || pattern.category === categoryFilter)
      && (severityFilter === "全部" || pattern.severity === severityFilter)
      && (term.length === 0 || `${pattern.id} ${pattern.name} ${pattern.category} ${pattern.keywords.join(" ")} ${pattern.notes}`.toLocaleLowerCase().includes(term));
  });
  const selectedPattern = displayPatterns.find((pattern) => pattern.id === selectedPatternId);
  const pendingRemoval = displayPatterns.find((pattern) => pattern.id === removePatternId);
  const mergeSource = displayPatterns.find((pattern) => pattern.id === mergeSourceId);
  const hitPattern = displayPatterns.find((pattern) => pattern.id === hitExamplesId);
  const duplicates = useMemo(() => findHumanizeDuplicates(displayPatterns), [displayPatterns]);

  useEffect(() => {
    setDisplayPatterns(patterns.map(recordFor));
    revisionRef.current = humanizeLibraryRevision ?? "";
    awaitingRefreshRef.current = false;
    setSelectedPatternId(null);
  }, [humanizeLibraryRevision, patterns]);

  const canWrite = () => {
    if (commandClient === undefined || projectId === undefined || revisionRef.current.length === 0) {
      onAction(action("humanize-refreshed", "当前 Engine 未提供拟人化库版本，已拒绝写入；请刷新项目后重试。"));
      return false;
    }
    if (isSaving || awaitingRefreshRef.current) {
      onAction(action("humanize-refreshed", "上一项拟人化库修改仍在同步权威视图，请稍候。"));
      return false;
    }
    return true;
  };
  const applyMutation = (result: HumanizeLibraryMutationResult, updater: (current: readonly HumanizePatternRecord[]) => readonly HumanizePatternRecord[], kind: NarrativeToolsAction["kind"], targetId?: string) => {
    if (result.status !== "saved") {
      onAction(action(kind, result.message, targetId));
      return;
    }
    revisionRef.current = result.humanizeLibraryRevision ?? revisionRef.current;
    setDisplayPatterns(updater);
    awaitingRefreshRef.current = true;
    onAction(action(kind, result.message, result.pattern?.id ?? targetId));
    onRefresh?.();
  };
  const submit = (request: () => Promise<HumanizeLibraryMutationResult>, updater: (current: readonly HumanizePatternRecord[]) => readonly HumanizePatternRecord[], kind: NarrativeToolsAction["kind"], targetId?: string) => {
    if (!canWrite()) return;
    setIsSaving(true);
    void request().then((result) => applyMutation(result, updater, kind, targetId)).catch((error: unknown) => {
      onAction(action(kind, error instanceof Error ? error.message : "拟人化库变更提交失败。", targetId));
    }).finally(() => setIsSaving(false));
  };

  const togglePattern = (pattern: HumanizePatternRecord) => {
    if (commandClient === undefined || projectId === undefined) {
      canWrite();
      return;
    }
    const enabled = !pattern.enabled;
    submit(
      () => commandClient.setHumanizePatternEnabled({ kind: "set_humanize_pattern_enabled", projectId, patternId: pattern.id, enabled, expectedRevision: revisionRef.current }),
      (current) => current.map((item) => item.id === pattern.id ? { ...item, enabled } : item),
      "humanize-toggled",
      pattern.id,
    );
  };
  const savePattern = (draft: HumanizePatternDraft) => {
    if (editor === null || commandClient === undefined || projectId === undefined) {
      canWrite();
      return;
    }
    const patternId = editor.mode === "edit" ? editor.pattern.id : undefined;
    const profile = {
      name: draft.name.trim(),
      category: draft.category.trim(),
      severity: draft.severity,
      keywords: draft.keywords,
      notes: draft.notes.trim(),
      examplePhrase: draft.examplePhrase.trim(),
      ...(editor.mode === "create" && draft.id.trim().length > 0 ? { patternId: draft.id.trim() } : {}),
    };
    submit(
      () => commandClient.saveHumanizePattern({ kind: "save_humanize_pattern", projectId, ...(patternId === undefined ? {} : { patternId }), pattern: profile, expectedRevision: revisionRef.current }),
      (current) => {
        const saved = current.find((item) => item.id === patternId);
        const fallback = saved === undefined ? undefined : { ...saved, ...recordFor({ ...saved, ...profile }) };
        return fallback === undefined ? current : current.map((item) => item.id === fallback.id ? fallback : item);
      },
      editor.mode === "create" ? "humanize-create-requested" : "humanize-edit-requested",
      patternId,
    );
    setEditor(null);
  };
  const confirmRemoval = () => {
    if (pendingRemoval === undefined || commandClient === undefined || projectId === undefined) {
      canWrite();
      return;
    }
    submit(
      () => commandClient.removeHumanizePattern({ kind: "remove_humanize_pattern", projectId, patternId: pendingRemoval.id, expectedRevision: revisionRef.current }),
      (current) => current.filter((item) => item.id !== pendingRemoval.id),
      "humanize-remove-requested",
      pendingRemoval.id,
    );
    setSelectedPatternId((current) => current === pendingRemoval.id ? null : current);
    setRemovePatternId(null);
  };
  const chooseMergeTarget = (targetId: string) => {
    if (mergeSource === undefined) return;
    const target = displayPatterns.find((pattern) => pattern.id === targetId);
    if (target === undefined) return;
    setMergeSourceId(null);
    setPendingMerge({ source: mergeSource, target });
  };
  const confirmMerge = () => {
    if (pendingMerge === null || commandClient === undefined || projectId === undefined) {
      canWrite();
      return;
    }
    const { source, target } = pendingMerge;
    submit(
      () => commandClient.mergeHumanizePatterns({ kind: "merge_humanize_patterns", projectId, sourcePatternId: source.id, targetPatternId: target.id, expectedRevision: revisionRef.current }),
      (current) => current.filter((item) => item.id !== source.id).map((item) => item.id === target.id ? { ...item, hitCount: target.hitCount + source.hitCount, lastChapterLabel: source.lastChapterLabel === "—" ? target.lastChapterLabel : source.lastChapterLabel } : item),
      "humanize-merge-requested",
      target.id,
    );
    setSelectedPatternId(target.id);
    setPendingMerge(null);
  };
  const importPatterns = (payload: string) => {
    if (commandClient === undefined || projectId === undefined || !canWrite()) return;
    let drafts: readonly HumanizePatternDraft[];
    try {
      drafts = parseHumanizeImport(payload);
    } catch (error) {
      onAction(action("humanize-imported", error instanceof Error ? error.message : "无法解析导入内容。"));
      return;
    }
    const existingIds = new Set(displayPatterns.map((pattern) => pattern.id));
    const existingNames = new Set(displayPatterns.map((pattern) => pattern.name));
    const candidates = drafts.filter((draft) => !existingIds.has(draft.id) && !existingNames.has(draft.name));
    if (candidates.length === 0) {
      onAction(action("humanize-imported", "导入内容没有新的有效模式。"));
      return;
    }
    setIsSaving(true);
    void (async () => {
      let revision = revisionRef.current;
      const saved: HumanizePatternRecord[] = [];
      for (const draft of candidates) {
        const result = await commandClient.saveHumanizePattern({
          kind: "save_humanize_pattern",
          projectId,
          expectedRevision: revision,
          pattern: {
            name: draft.name,
            category: draft.category,
            severity: draft.severity,
            keywords: draft.keywords,
            notes: draft.notes,
            examplePhrase: draft.examplePhrase,
            source: "imported",
            ...(draft.id.startsWith("lib_imported_") ? { patternId: draft.id } : {}),
          },
        });
        if (result.status !== "saved") throw new Error(result.message);
        revision = result.humanizeLibraryRevision ?? revision;
        if (result.pattern !== undefined) saved.push(recordFor(result.pattern));
      }
      revisionRef.current = revision;
      setDisplayPatterns((current) => [...current, ...saved]);
      awaitingRefreshRef.current = true;
      onAction(action("humanize-imported", `已由 Engine 导入 ${saved.length} 条拟人化模式。`));
      onRefresh?.();
    })().catch((error: unknown) => {
      onAction(action("humanize-imported", error instanceof Error ? error.message : "导入拟人化模式失败。"));
    }).finally(() => setIsSaving(false));
  };
  const refreshPatterns = () => {
    setDisplayPatterns(patterns.map(recordFor));
    setSelectedPatternId(null);
    onRefresh?.();
    onAction(action("humanize-refreshed", "已请求刷新 Engine 拟人化库。"));
  };
  const showHitExamples = (pattern: HumanizePatternRecord) => {
    setHitExamplesId(pattern.id);
    onAction(action("humanize-hit-examples-requested", `正在查看「${pattern.name}」的命中样例。`, pattern.id));
  };

  return <div className="humanize-workbench">
    <header>
      <div><h3>拟人化库</h3><p>由 Engine 统一维护；筛选与编辑草稿只存在于当前界面，确认后即写入共享库。</p></div>
      <div className="humanize-toolbar-actions">
        <button className="button button-secondary" onClick={() => setDuplicatesOpen(true)} type="button">查重</button>
        <button className="button button-secondary" disabled={isSaving} onClick={() => setImportOpen(true)} type="button">导入…</button>
        <button className="button button-secondary" onClick={() => setExportOpen(true)} type="button">导出…</button>
        <button className="button button-secondary" disabled={isSaving} onClick={refreshPatterns} type="button">刷新</button>
        <button className="button button-primary" disabled={isSaving} onClick={() => setEditor({ mode: "create" })} type="button">新建模式</button>
      </div>
    </header>
    <div className="humanize-filters">
      <label>来源<select aria-label="来源筛选" onChange={(event) => setSourceFilter(event.target.value)} value={sourceFilter}>{sourceOptions.map((option) => <option key={option}>{option}</option>)}</select></label>
      <label>分类<select aria-label="分类筛选" onChange={(event) => setCategoryFilter(event.target.value)} value={categoryFilter}>{categoryOptions.map((option) => <option key={option}>{option}</option>)}</select></label>
      <label>严重度<select aria-label="严重度筛选" onChange={(event) => setSeverityFilter(event.target.value)} value={severityFilter}>{severityOptions.map((option) => <option key={option}>{option}</option>)}</select></label>
      <label className="humanize-search"><span className="sr-only">搜索拟人化模式</span><input aria-label="搜索拟人化模式" onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称或分类" type="search" value={query} /></label>
    </div>
    <div className="humanize-table" role="table" aria-label="拟人化模式库">
      <div className="humanize-table-head" role="row"><span role="columnheader">ID</span><span role="columnheader">名称</span><span role="columnheader">来源</span><span role="columnheader">分类</span><span role="columnheader">严重度</span><span role="columnheader">命中数</span><span role="columnheader">最近章节</span><span role="columnheader">启用</span></div>
      {visiblePatterns.map((pattern) => <div aria-selected={selectedPattern?.id === pattern.id} className={selectedPattern?.id === pattern.id ? "is-selected" : ""} key={pattern.id} role="row"><button aria-label={`选择 ${pattern.name}`} className="humanize-row-select humanize-row-id" onClick={() => setSelectedPatternId(pattern.id)} onDoubleClick={() => showHitExamples(pattern)} type="button">{pattern.id}</button><button aria-label={`查看 ${pattern.name} 命中样例`} className="humanize-row-select humanize-row-name" onClick={() => setSelectedPatternId(pattern.id)} onDoubleClick={() => showHitExamples(pattern)} type="button">{pattern.name}</button><span role="cell">{pattern.sourceLabel}</span><span role="cell">{pattern.category}</span><span role="cell">{pattern.severity}</span><span role="cell">{pattern.hitCount}</span><span role="cell">{pattern.lastChapterLabel}</span><input aria-label={`启用 ${pattern.name}`} checked={pattern.enabled} disabled={isSaving} onChange={() => togglePattern(pattern)} type="checkbox" /></div>)}
      {visiblePatterns.length === 0 && <p className="narrative-empty">没有匹配的拟人化模式。</p>}
    </div>
    <HumanizeRowActions onEdit={() => selectedPattern !== undefined && setEditor({ mode: "edit", pattern: selectedPattern })} onMerge={() => selectedPattern !== undefined && setMergeSourceId(selectedPattern.id)} onRemove={() => selectedPattern !== undefined && setRemovePatternId(selectedPattern.id)} onViewHits={() => selectedPattern !== undefined && showHitExamples(selectedPattern)} pattern={selectedPattern} />
    {editor !== null && <HumanizePatternEditorDialog editor={editor} onClose={() => setEditor(null)} onSave={savePattern} />}
    {pendingRemoval !== undefined && <AppDialog confirmLabel="删除" description={`将永久删除「${pendingRemoval.name}」(${pendingRemoval.id})，此操作不可恢复。`} onClose={() => setRemovePatternId(null)} onConfirm={confirmRemoval} title="确认删除" tone="danger"><p className="narrative-dialog-note">内置条目为只读，不能删除；确认后由 Engine 更新共享库。</p></AppDialog>}
    {mergeSource !== undefined && <HumanizeMergeDialog candidates={displayPatterns.filter((pattern) => pattern.id !== mergeSource.id)} onClose={() => setMergeSourceId(null)} onSave={chooseMergeTarget} source={mergeSource} />}
    {pendingMerge !== null && <AppDialog confirmLabel="合并" description={`把「${pendingMerge.source.name}」的 ${pendingMerge.source.hitCount} 次命中合并到「${pendingMerge.target.name}」，并删除源条目？`} onClose={() => setPendingMerge(null)} onConfirm={confirmMerge} title="确认合并"><p className="narrative-dialog-note">确认后由 Engine 原子地保留目标条目并转移命中历史。</p></AppDialog>}
    {duplicatesOpen && <HumanizeDuplicatesDialog duplicates={duplicates} onClose={() => setDuplicatesOpen(false)} />}
    {importOpen && <HumanizeImportDialog onClose={() => setImportOpen(false)} onImport={importPatterns} />}
    {exportOpen && <HumanizeExportDialog exported={exportHumanizePatterns(displayPatterns)} onClose={() => setExportOpen(false)} onExport={() => onAction(action("humanize-export-requested", "已生成当前 Engine 拟人化库的 JSON 互换副本。"))} />}
    {hitPattern !== undefined && <HumanizeHitExamplesDialog onClose={() => setHitExamplesId(null)} pattern={hitPattern} />}
  </div>;
}

function HumanizeRowActions({ onEdit, onMerge, onRemove, onViewHits, pattern }: { readonly onEdit: () => void; readonly onMerge: () => void; readonly onRemove: () => void; readonly onViewHits: () => void; readonly pattern: HumanizePatternRecord | undefined }) {
  if (pattern === undefined) return <p className="humanize-selection-hint">选择一个模式后可查看命中样例、编辑、禁用、删除或合并。</p>;
  const mutable = canDeleteOrMergeHumanizePattern(pattern);
  return <div className="humanize-row-actions" aria-label="拟人化模式操作"><span><strong>{pattern.name}</strong><small>{pattern.id} · {pattern.enabled ? "已启用" : "已禁用"}</small></span><div><button className="button button-secondary" onClick={onViewHits} type="button">查看命中样例</button><button className="button button-secondary" onClick={onEdit} type="button">编辑</button>{mutable && <><button className="button button-secondary" onClick={onMerge} type="button">合并到…</button><button className="button button-quiet" onClick={onRemove} type="button">删除</button></>}</div></div>;
}

function HumanizePatternEditorDialog({ editor, onClose, onSave }: { readonly editor: HumanizeEditorState; readonly onClose: () => void; readonly onSave: (draft: HumanizePatternDraft) => void }) {
  const [draft, setDraft] = useState<HumanizePatternDraft>(() => editor.mode === "edit" ? patternDraft(editor.pattern) : defaultHumanizeDraft());
  const update = <Key extends keyof HumanizePatternDraft>(key: Key, value: HumanizePatternDraft[Key]) => setDraft((current) => ({ ...current, [key]: value }));
  const invalid = draft.name.trim().length === 0;
  return <AppDialog confirmDisabled={invalid} confirmLabel="保存" description={editor.mode === "edit" ? "保存后由 Engine 更新共享拟人化库。" : "填写模式元数据；确认后由 Engine 创建用户模式。"} onClose={onClose} onConfirm={() => onSave(draft)} size="wide" title={editor.mode === "edit" ? "编辑模式" : "新建模式"}><div className="narrative-dialog-form humanize-editor-form"><label>ID<input aria-label="模式 ID" autoFocus={editor.mode === "create"} onChange={(event) => update("id", event.target.value)} placeholder="留空由 Engine 生成 lib_user_ ID" readOnly={editor.mode === "edit"} value={draft.id} /></label><label>名称<input aria-label="模式名称" autoFocus={editor.mode === "edit"} onChange={(event) => update("name", event.target.value)} value={draft.name} /></label><label>分类<input aria-label="模式分类" onChange={(event) => update("category", event.target.value)} value={draft.category} /></label><label>严重度<select aria-label="模式严重度" onChange={(event) => update("severity", event.target.value)} value={draft.severity}><option>高</option><option>中</option><option>低</option><option>critical</option><option>high</option><option>medium</option><option>low</option></select></label><label className="narrative-dialog-form-wide">关键词<input aria-label="模式关键词" onChange={(event) => update("keywords", event.target.value.split(/[，,]/).map((item) => item.trim()).filter((item) => item.length > 0))} placeholder="逗号分隔，例如：滥情，堆砌，口水" value={draft.keywords.join("，")} /></label><label className="narrative-dialog-form-wide">备注<textarea aria-label="模式备注" onChange={(event) => update("notes", event.target.value)} value={draft.notes} /></label><label className="narrative-dialog-form-wide">示例短语<input aria-label="模式示例短语" onChange={(event) => update("examplePhrase", event.target.value)} placeholder="一句话举例" value={draft.examplePhrase} /></label>{invalid && <p className="narrative-dialog-validation">请填写模式名称。</p>}<p className="narrative-dialog-note narrative-dialog-form-wide">字段顺序与 PySide6 模式元数据表单一致；写入由 Engine 统一裁决。</p></div></AppDialog>;
}

function HumanizeMergeDialog({ candidates, onClose, onSave, source }: { readonly candidates: readonly HumanizePatternRecord[]; readonly onClose: () => void; readonly onSave: (targetId: string) => void; readonly source: HumanizePatternRecord }) {
  const [targetId, setTargetId] = useState(candidates[0]?.id ?? "");
  return <AppDialog confirmDisabled={targetId.length === 0} confirmLabel="下一步" description={`选择接收「${source.name}」命中记录的目标模式。`} onClose={onClose} onConfirm={() => onSave(targetId)} title="合并到…"><div className="narrative-dialog-form"><label className="narrative-dialog-form-wide">目标模式<select aria-label="合并目标模式" onChange={(event) => setTargetId(event.target.value)} value={targetId}>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name} · {candidate.id}</option>)}</select></label><p className="narrative-dialog-note narrative-dialog-form-wide">合并来源：{source.name} · 当前命中 {source.hitCount} 次。下一步会显示不可逆操作的确认。</p></div></AppDialog>;
}

function HumanizeDuplicatesDialog({ duplicates, onClose }: { readonly duplicates: readonly HumanizeDuplicatePair[]; readonly onClose: () => void }) {
  return <AppDialog confirmLabel="关闭" description={`共 ${duplicates.length} 对高相似模式。`} onClose={onClose} onConfirm={onClose} size="wide" title="重复模式对"><div className="humanize-duplicates">{duplicates.length === 0 ? <p>未发现相似对 — 当前库无重复。</p> : <><p>按名称、分类、关键词和备注计算相似度；可关闭后在主表中选择用户或导入条目进行合并。</p>{duplicates.map((pair) => <article key={`${pair.left.id}-${pair.right.id}`}><strong>{pair.left.name}</strong><span>↔</span><strong>{pair.right.name}</strong><small>相似度 {(pair.similarity * 100).toFixed(0)}%</small></article>)}</>}</div></AppDialog>;
}

function HumanizeImportDialog({ onClose, onImport }: { readonly onClose: () => void; readonly onImport: (payload: string) => void }) {
  const [payload, setPayload] = useState("");
  const [error, setError] = useState("");
  const importPayload = () => {
    try {
      parseHumanizeImport(payload);
      onImport(payload);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法解析 JSON。");
    }
  };
  return <AppDialog closeOnConfirm={false} confirmDisabled={payload.trim().length === 0} confirmLabel="导入到 Engine" description="粘贴 Nimo 导出的 JSON；有效模式将由 Engine 持久化为导入条目。" onClose={onClose} onConfirm={importPayload} size="wide" title="导入拟人化库"><div className="narrative-dialog-form"><label className="narrative-dialog-form-wide">拟人化库 JSON<textarea aria-label="导入拟人化库 JSON" onChange={(event) => { setPayload(event.target.value); setError(""); }} placeholder='{"format":"nimo-humanize-library/v2","patterns":[…]}' value={payload} /></label>{error.length > 0 && <p className="narrative-dialog-validation">{error}</p>}<p className="narrative-dialog-note narrative-dialog-form-wide">导入会跳过当前库已有的 ID 或名称，并保留“导入”来源标记。</p></div></AppDialog>;
}

function HumanizeExportDialog({ exported, onClose, onExport }: { readonly exported: string; readonly onClose: () => void; readonly onExport: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    onExport();
    const write = navigator.clipboard?.writeText(exported);
    if (write === undefined) {
      setCopied(false);
      return;
    }
    void write.then(() => setCopied(true)).catch(() => setCopied(false));
  };
  return <AppDialog closeOnConfirm={false} confirmLabel={copied ? "已复制" : "复制 JSON"} description="由当前 Engine 读模型生成的互换副本；复制不会改写库。" onClose={onClose} onConfirm={copy} size="wide" title="导出拟人化库"><div className="narrative-dialog-form"><label className="narrative-dialog-form-wide">互换预览<textarea aria-label="拟人化库导出 JSON" readOnly value={exported} /></label><p className="narrative-dialog-note narrative-dialog-form-wide">可在另一台 Nimo 客户端的“导入”窗口粘贴，并由其 Engine 校验后写入。</p></div></AppDialog>;
}

function HumanizeHitExamplesDialog({ onClose, pattern }: { readonly onClose: () => void; readonly pattern: HumanizePatternRecord }) {
  return <AppDialog confirmLabel="关闭" description={`${pattern.id} · 累计命中 ${pattern.hitCount} 次 · 最近章节 ${pattern.lastChapterLabel}`} onClose={onClose} onConfirm={onClose} title={`命中样例 — ${pattern.name}`}><div className="humanize-hit-examples"><p><b>分类：</b>{pattern.category}</p><p><b>严重度：</b>{pattern.severity}</p><p><b>关键词：</b>{pattern.keywords.length > 0 ? pattern.keywords.join("、") : "—"}</p><p><b>备注：</b>{pattern.notes || "—"}</p><p><b>内置示例短语：</b>{pattern.examplePhrase || "Engine 未提供示例短语。"}</p></div></AppDialog>;
}

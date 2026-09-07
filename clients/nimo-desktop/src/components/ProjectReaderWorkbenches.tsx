import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AppDialog } from "./AppDialog";
import { useAuthoring } from "./AuthoringWorkspace";

import type { EngineCommandClient, NarrativeToolsView, ProjectReaderArtifactView } from "@nimo/engine-contracts";

import { OverlaySurface } from "./OverlaySurface";
import { OutlineReadingView } from "./OutlineReadingView";
import { SourceMessageDialog } from "./SourceMessageDialog";
import { ReaderArtifactDocument, type ReaderArtifactDocumentProps } from "./ReaderArtifactDocument";
export { ReaderArtifactDocument } from "./ReaderArtifactDocument";

export { TokenAnalyticsWorkbench } from "./reader/TokenAnalyticsWorkbench";
import {
  buildRevisionDiff,
  countRevisionWords,
  defaultRevisionDirections,
  scopeLabel,
  selectionWordCount,
  type RevisionInvalidationScope,
  validateRevisionDraft,
} from "../lib/final-revision-session";
import type { ReaderUnsavedSessionChange } from "../lib/reader-unsaved-session";
import { revealFileInFolder } from "../lib/native-bridge";
import { nextSelectedIds } from "../lib/workflow-preset-session";

function sourceTab<T extends string>({ activeId, ariaLabel, items, onSelect }: { readonly activeId: T; readonly ariaLabel: string; readonly items: readonly { readonly id: T; readonly label: string }[]; readonly onSelect: (id: T) => void }) {
  return <div aria-label={ariaLabel} className="reader-source-tabs" role="tablist">{items.map((item) => <button aria-selected={item.id === activeId} className={item.id === activeId ? "is-active" : ""} key={item.id} onClick={() => onSelect(item.id)} role="tab" type="button">{item.label}</button>)}</div>;
}

function chapterNumber(artifact: ProjectReaderArtifactView): number | null {
  const match = /第\s*(\d+)\s*章/.exec(artifact.label);
  return match === null ? null : Number(match[1]);
}

function chapterTitle(artifact: ProjectReaderArtifactView): string {
  return artifact.label.replace(/^第\s*\d+\s*章(?:正文)?\s*/, "").trim() || artifact.label;
}

type RevisionOverlay = "selection" | "save-preview" | "scope" | "discard" | "return" | "humanize-add" | null;

interface PendingRevisionCandidate {
  readonly original: string;
  readonly replacement: string;
  readonly selection: { readonly start: number; readonly end: number };
}

type HumanizePatternSeverity = "critical" | "high" | "medium" | "low";

interface HumanizeSelectionDraft {
  readonly category: string;
  readonly keywords: string;
  readonly name: string;
  readonly notes: string;
  readonly severity: HumanizePatternSeverity;
}

const HUMANIZE_SELECTION_MAX_CHARS = 4_000;

function compactSelectionPreview(text: string, maximumLength = 32): string {
  const compact = text.replace(/\s+/gu, " ").trim();
  return compact.length <= maximumLength ? compact : `${compact.slice(0, maximumLength)}…`;
}

/** Build the editable defaults for the PySide-compatible selected-text flow. */
export function createHumanizeSelectionDraft({ chapter, projectTitle, selection }: {
  readonly chapter: number | null;
  readonly projectTitle: string;
  readonly selection: string;
}): HumanizeSelectionDraft {
  const chapterLabel = chapter === null ? "本章" : `第 ${chapter} 章`;
  const preview = compactSelectionPreview(selection) || "AI 痕迹样例";
  return {
    name: `${chapterLabel}选段 · ${preview}`.slice(0, 200),
    category: "正文选段",
    severity: "medium",
    keywords: `${chapterLabel},正文选段`,
    notes: `来源：${projectTitle} ${chapterLabel}终稿选段。`,
  };
}

/** Project reader writes the same Engine pattern shape as the humanize workbench. */
export function humanizePatternFromSelection(draft: HumanizeSelectionDraft, selection: string) {
  return {
    name: draft.name.trim(),
    category: draft.category.trim(),
    severity: draft.severity,
    keywords: draft.keywords.split(/[，,]/u).map((item) => item.trim()).filter(Boolean),
    notes: draft.notes.trim(),
    examplePhrase: selection.trim(),
    source: "user" as const,
  };
}

function artifactExpectedWordCount(artifact: ProjectReaderArtifactView): number {
  const fact = artifact.facts.find((item) => /(?:长度|字数)/u.test(item.label));
  if (fact === undefined) return 0;
  const match = fact.value.replace(/,/gu, "").match(/\d+(?:\.\d+)?/u);
  return match === null ? 0 : Math.round(Number(match[0]));
}

interface RevisionDialogProps {
  readonly cancelLabel?: string;
  readonly children: React.ReactNode;
  readonly confirmDisabled?: boolean;
  readonly confirmLabel: string;
  readonly description?: string;
  readonly footerOrder?: "cancel-confirm" | "confirm-cancel";
  readonly hideHeader?: boolean;
  readonly initialFocusSelector?: string;
  readonly onClose: () => void;
  readonly onConfirm: () => void;
  readonly showClose?: boolean;
  readonly title: string;
  readonly variant: "selection" | "preview" | "scope" | "confirm";
}

/** Source-shaped Qt dialog counterpart used only by the final-revision workflow. */
function RevisionDialog({ cancelLabel = "取消", children, confirmDisabled = false, confirmLabel, description, footerOrder = "cancel-confirm", hideHeader = false, initialFocusSelector, onClose, onConfirm, showClose = true, title, variant }: RevisionDialogProps) {
  const focusProps = initialFocusSelector === undefined ? {} : { initialFocusSelector };
  const cancel = <button className="button button-secondary" onClick={onClose} type="button">{cancelLabel}</button>;
  const confirm = <button className="button button-primary" disabled={confirmDisabled} onClick={onConfirm} type="button">{confirmLabel}</button>;
  return <OverlaySurface ariaLabel={title} onClose={onClose} {...focusProps}>
    <section className={`reader-revision-dialog is-${variant}`}>
      {!hideHeader && <header><div><h2>{title}</h2>{description !== undefined && <p>{description}</p>}</div>{showClose && <button aria-label="关闭对话框" className="reader-revision-dialog-close" onClick={onClose} type="button">×</button>}</header>}
      <div className="reader-revision-dialog-content">{children}</div>
      <footer>{footerOrder === "confirm-cancel" ? <>{confirm}{cancel}</> : <>{cancel}{confirm}</>}</footer>
    </section>
  </OverlaySurface>;
}

/** The source page exposes a revision session whose candidate generation is Engine-owned. */
export function ChapterProseRevision({ artifact, commandClient, humanizeLibraryRevision, onOperation, onRefreshHumanizeLibrary, onUnsavedSessionChange, projectId, projectTitle }: ReaderArtifactDocumentProps & { readonly commandClient: EngineCommandClient; readonly humanizeLibraryRevision?: string | undefined; readonly onOperation: (message: string) => void; readonly onRefreshHumanizeLibrary?: (() => void) | undefined; readonly onUnsavedSessionChange?: ReaderUnsavedSessionChange; readonly projectId: string }) {
  const authoring = useAuthoring();
  // Prefer the full raw chapter text; the paragraph excerpt is truncated to a
  // bounded preview and would silently cut long chapters in the prose reader.
  const sourceText = artifact.content ?? artifact.paragraphs.join("\n\n");
  const [savedText, setSavedText] = useState(sourceText);
  const [draftText, setDraftText] = useState(sourceText);
  const [mode, setMode] = useState<"read" | "revision">("read");
  const [selection, setSelection] = useState<{ readonly start: number; readonly end: number }>({ start: 0, end: 0 });
  const [candidate, setCandidate] = useState<PendingRevisionCandidate | null>(null);
  const [overlay, setOverlay] = useState<RevisionOverlay>(null);
  const [contextMenu, setContextMenu] = useState<{ readonly x: number; readonly y: number } | null>(null);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [direction, setDirection] = useState("压缩解释，保留人物动作与物件细节。");
  const [invalidationScope, setInvalidationScope] = useState<RevisionInvalidationScope>("downstream");
  const [backgroundReevaluate, setBackgroundReevaluate] = useState(false);
  const [savedRevision, setSavedRevision] = useState(artifact.sourceRevision);
  const [saving, setSaving] = useState(false);
  const [humanizeDraft, setHumanizeDraft] = useState<HumanizeSelectionDraft>(() => createHumanizeSelectionDraft({ chapter: chapterNumber(artifact), projectTitle, selection: "" }));
  const [humanizeRevision, setHumanizeRevision] = useState(humanizeLibraryRevision ?? "");
  const [humanizeSaving, setHumanizeSaving] = useState(false);
  const [humanizeAwaitingRefresh, setHumanizeAwaitingRefresh] = useState(false);
  const [humanizeSaveError, setHumanizeSaveError] = useState("");
  const dirty = draftText !== savedText;
  const selectionText = draftText.slice(selection.start, selection.end);
  const humanizeSelection = selectionText.trim();
  const selectionWords = selectionWordCount(draftText, selection);
  const chapterLabel = chapterTitle(artifact);
  const expectedWordCount = artifactExpectedWordCount(artifact);
  const guard = validateRevisionDraft({ draft: draftText, expectedWordCount, saved: savedText });
  const canSave = dirty && guard.ok;
  const appliedProse = authoring?.appliedProse?.chapter === chapterNumber(artifact) ? authoring.appliedProse : undefined;
  const currentSource = appliedProse?.text ?? sourceText;
  const currentRevision = appliedProse?.revision ?? artifact.sourceRevision;
  const externalConflict = dirty && currentSource !== savedText && currentSource !== draftText;
  const observedSource = useRef(sourceText);
  useEffect(() => {
    const changed = observedSource.current !== sourceText;
    observedSource.current = sourceText;
    if (!appliedProse && !changed) return;
    if (currentSource === savedText || externalConflict) return;
    // Reconcile only server-confirmed prose. Independent unsaved edits survive.
    setSavedText(currentSource);
    if (!dirty) setDraftText(currentSource);
    setSavedRevision(currentRevision);
    setCandidate(null);
  }, [appliedProse, sourceText, currentSource, currentRevision, savedText, dirty, externalConflict]);

  useEffect(() => {
    setHumanizeRevision(humanizeLibraryRevision ?? "");
    setHumanizeAwaitingRefresh(false);
  }, [humanizeLibraryRevision]);

  const enterRevision = () => {
    setDraftText(savedText);
    setCandidate(null);
    setSelection({ start: 0, end: 0 });
    setMode("revision");
    onOperation(`已进入第 ${chapterNumber(artifact) ?? "—"} 章砚修会话。`);
  };
  const openSavePreview = useCallback(() => {
    if (!canSave) return;
    setOverlay("save-preview");
  }, [canSave]);
  const saveUnsavedSession = useCallback(async (returnToReading = false) => {
    const number = chapterNumber(artifact);
    if (number === null || saving) return false;
    setSaving(true);
    try {
      if (authoring?.session?.configured) {
        await authoring.propose({ command: "revise_chapter", chapterNumber: number, candidate: draftText, title: `第 ${number} 章人工改稿`, expectedInputVersion: authoring.session.inputVersion });
        setOverlay(null);
        onOperation("改稿已保存为待批准提案；正式正文尚未改变，当前编辑草稿继续保留。");
        return false;
      }
      const result = await commandClient.saveChapterRevision({
        kind: "save_chapter_revision",
        projectId,
        chapterNumber: number,
        text: draftText,
        ...(savedRevision === undefined ? {} : { expectedRevision: savedRevision }),
        scope: invalidationScope,
        backgroundReevaluate,
      });
      if (result.status === "conflict") {
        setSavedRevision(result.revision);
        onOperation(`${result.message} 当前草稿仍保留，未覆盖引擎中的最新终稿。`);
        return false;
      }
      if (result.status === "rejected") {
        onOperation(`保存失败：${result.message}`);
        return false;
      }
      setSavedText(draftText);
      setSavedRevision(result.revision);
      onOperation(result.message);
      setCandidate(null);
      setOverlay(null);
      if (returnToReading) setMode("read");
      return true;
    } catch (error) {
      onOperation(`保存失败：${error instanceof Error ? error.message : String(error)}`);
      return false;
    } finally {
      setSaving(false);
    }
  }, [artifact, authoring, backgroundReevaluate, commandClient, draftText, invalidationScope, onOperation, projectId, savedRevision, saving]);
  const commitRevision = () => { void saveUnsavedSession(); };
  const restoreRevision = () => {
    setDraftText(savedText);
    setCandidate(null);
    setSelection({ start: 0, end: 0 });
    onOperation("已还原到本次会话中最近保存的终稿。");
  };
  const createCandidate = async () => {
    const number = chapterNumber(artifact);
    const selected = selectionText;
    if (number === null || selected.trim().length === 0 || candidateLoading) return;
    const requestedSelection = selection;
    setCandidateLoading(true);
    try {
      const result = await commandClient.generateChapterRevisionCandidate({
        kind: "generate_chapter_revision_candidate",
        projectId,
        chapterNumber: number,
        chapterTitle: chapterLabel,
        selectedText: selected,
        beforeContext: draftText.slice(Math.max(0, requestedSelection.start - 900), requestedSelection.start),
        afterContext: draftText.slice(requestedSelection.end, requestedSelection.end + 900),
        instruction: direction.trim(),
      });
      const replacement = result.replacement?.trim() ?? "";
      if (result.status !== "generated" || replacement.length === 0) {
        onOperation(`选段精修未生成：${result.message}`);
        return;
      }
      setCandidate({ original: selected, replacement, selection: requestedSelection });
      setOverlay(null);
      onOperation(result.message);
    } catch (error) {
      onOperation(`选段精修请求失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setCandidateLoading(false);
    }
  };
  const applyCandidate = () => {
    if (candidate === null) return;
    const { original } = candidate;
    let start = candidate.selection.start;
    let end = candidate.selection.end;
    if (draftText.slice(start, end) !== original) {
      const first = draftText.indexOf(original);
      if (first < 0 || draftText.indexOf(original, first + 1) >= 0) {
        setCandidate(null);
        onOperation("原选区已被修改或出现多处匹配，请重新选择文本后再让 Engine 改写。");
        return;
      }
      start = first;
      end = first + original.length;
    }
    const replacement = candidate.replacement;
    setDraftText(`${draftText.slice(0, start)}${replacement}${draftText.slice(end)}`);
    setSelection({ start, end: start + replacement.length });
    setCandidate(null);
    onOperation("已将 Engine 精修候选纳入当前砚修草稿。请在保存前审阅。");
  };
  const appendDirection = (value: string) => setDirection((current) => current.includes(value) ? current : current.trim() ? `${current}；${value}` : value);
  const openHumanizeAdd = () => {
    if (humanizeSelection.length === 0 || humanizeSaving || humanizeAwaitingRefresh) return;
    setHumanizeDraft(createHumanizeSelectionDraft({ chapter: chapterNumber(artifact), projectTitle, selection: humanizeSelection }));
    setHumanizeSaveError("");
    setContextMenu(null);
    setOverlay("humanize-add");
  };
  const humanizePattern = humanizePatternFromSelection(humanizeDraft, humanizeSelection);
  const humanizeFormError = humanizeRevision.length === 0
    ? "拟人化库尚未加载可写版本；请刷新项目后重试。"
    : humanizeSelection.length === 0
      ? "请选择一段正文后再加入拟人化库。"
      : humanizeSelection.length > HUMANIZE_SELECTION_MAX_CHARS
        ? `选段超过 ${HUMANIZE_SELECTION_MAX_CHARS.toLocaleString()} 字符；请缩小选区后再提交。`
        : humanizePattern.name.length === 0
          ? "请填写模式名称。"
          : humanizePattern.name.length > 200
            ? "模式名称不能超过 200 个字符。"
            : humanizePattern.category.length > 200
              ? "模式分类不能超过 200 个字符。"
              : humanizePattern.keywords.length > 100
                ? "关键词不能超过 100 项。"
                : humanizePattern.notes.length > 8_000
                  ? "备注不能超过 8,000 个字符。"
                  : "";
  const saveHumanizeSelection = async () => {
    if (humanizeSaving || humanizeAwaitingRefresh || humanizeFormError.length > 0) return;
    setHumanizeSaving(true);
    setHumanizeSaveError("");
    try {
      const result = await commandClient.saveHumanizePattern({
        kind: "save_humanize_pattern",
        projectId,
        pattern: humanizePattern,
        expectedRevision: humanizeRevision,
      });
      if (result.status !== "saved") {
        setHumanizeSaveError(result.message);
        onOperation(`拟人化库未写入：${result.message}`);
        return;
      }
      setHumanizeRevision(result.humanizeLibraryRevision ?? humanizeRevision);
      setHumanizeAwaitingRefresh(onRefreshHumanizeLibrary !== undefined);
      onRefreshHumanizeLibrary?.();
      onOperation(result.message);
      setOverlay(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setHumanizeSaveError(message);
      onOperation(`拟人化库写入失败：${message}`);
    } finally {
      setHumanizeSaving(false);
    }
  };
  const discardUnsavedSession = useCallback(() => {
    setDraftText(savedText);
    setCandidate(null);
    setSelection({ start: 0, end: 0 });
    setOverlay(null);
    setMode("read");
  }, [savedText]);
  const requestReturnToReading = () => {
    if (!dirty) { setMode("read"); setCandidate(null); return; }
    setOverlay("return");
  };
  const abandonAndReturn = () => {
    discardUnsavedSession();
    onOperation("已放弃未保存的砚修草稿并回到阅稿。当前项目文件未被改动。");
  };

  useLayoutEffect(() => {
    if (onUnsavedSessionChange === undefined) return;
    if (!dirty) {
      onUnsavedSessionChange("final-revision", null);
      return;
    }
    onUnsavedSessionChange("final-revision", {
      cancelLabel: "继续修订",
      discardLabel: "放弃修改",
      id: "final-revision",
      informativeText: "可以先保存定稿，也可以放弃本次修订。",
      message: "当前终稿有未保存修改。",
      onDiscard: discardUnsavedSession,
      onSave: () => saveUnsavedSession(),
      saveLabel: "保存定稿",
      title: "终稿修订尚未保存",
    });
  }, [dirty, discardUnsavedSession, onUnsavedSessionChange, saveUnsavedSession]);

  useEffect(() => () => onUnsavedSessionChange?.("final-revision", null), [onUnsavedSessionChange]);

  return <section aria-label="终稿修订" className="reader-final-revision">
    {externalConflict && <p role="alert">正式正文已在其他操作中更新；当前未保存草稿已保留。请比较最新正式版本后再提交，系统不会自动合并或覆盖。</p>}
    <header className="reader-final-revision-toolbar"><div><span className="reader-revision-mode">{mode === "read" ? "阅稿" : "砚修"}</span><span>{mode === "revision" ? `约 ${countRevisionWords(draftText).toLocaleString()} 字${dirty ? " · 亲笔修订未落盘" : " · 已同步"}` : `终稿约 ${countRevisionWords(savedText).toLocaleString()} 字`}</span></div>{mode === "read" ? <button className="button button-secondary" onClick={enterRevision} type="button">入砚修</button> : <div><button className="button button-primary" disabled={!dirty || saving} onClick={openSavePreview} type="button">{saving ? "保存中…" : "保存定稿"}</button><button className="button button-secondary" disabled={!dirty || saving} onClick={() => setOverlay("discard")} type="button">还原本稿</button><button className="button button-secondary" disabled={selectionText.trim().length === 0 || saving} onClick={() => setOverlay("selection")} type="button">选段精修</button><button className="button button-quiet" disabled={saving} onClick={requestReturnToReading} type="button">回到阅稿</button></div>}</header>
    {mode === "read" ? <article className="reader-prose-read"><header><span className="section-kicker">{projectTitle} · 第 {chapterNumber(artifact) ?? "—"} 章</span><h2>{chapterLabel}</h2><p>{artifact.caption}</p></header><div>{savedText.split("\n\n").map((paragraph, index) => <p key={`${artifact.id}-prose-${index}`}>{paragraph}</p>)}</div><footer><span>{artifact.facts.map((fact) => `${fact.label}：${fact.value}`).join(" · ")}</span></footer></article> : <div className="reader-revision-workspace"><label className="sr-only" htmlFor={`chapter-revision-${artifact.id}`}>第 {chapterNumber(artifact) ?? "—"} 章终稿编辑器</label><textarea id={`chapter-revision-${artifact.id}`} onChange={(event) => setDraftText(event.target.value)} onContextMenu={(event) => { if (selectionText.trim().length > 0) { event.preventDefault(); setContextMenu({ x: event.clientX, y: event.clientY }); } }} onSelect={(event) => setSelection({ start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd })} value={draftText} /><aside><header><strong>精修候选</strong><span>{selectionText.trim().length > 0 ? "已选择文本" : "请选择正文片段"}</span></header>{candidate === null ? <p>选择一段正文后，可请求 Engine 生成精修候选。</p> : <><p className="reader-revision-candidate-boundary">Engine 精修候选 · 尚未纳入正文</p><blockquote>{candidate.replacement}</blockquote><div><button className="button button-primary" onClick={applyCandidate} type="button">纳入正文</button><button className="button button-quiet" onClick={() => setCandidate(null)} type="button">收起候选</button></div></>}</aside></div>}
    {contextMenu !== null && <div className="reader-revision-context-menu" onClick={() => setContextMenu(null)} role="presentation" style={{ position: "fixed", inset: 0, zIndex: 900 }}><menu className="reader-revision-context-items" style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y }}><li><button onClick={() => { setContextMenu(null); setOverlay("selection"); }} type="button">选段精修…</button></li><li><button disabled={humanizeRevision.length === 0 || humanizeSaving || humanizeAwaitingRefresh} onClick={openHumanizeAdd} title={humanizeRevision.length === 0 ? "拟人化库尚未加载可写版本" : undefined} type="button">加入拟人化库…</button></li></menu></div>}
    {mode === "revision" && !guard.ok && <p className="reader-revision-guard" role="alert">{guard.errors.join(" ")}</p>}
    {overlay === "selection" && <RevisionDialog confirmDisabled={selectionText.trim().length === 0 || candidateLoading} confirmLabel={candidateLoading ? "Engine 精修中…" : "生成候选"} initialFocusSelector="textarea" onClose={() => setOverlay(null)} onConfirm={() => { void createCandidate(); }} title={`选中约 ${selectionWords.toLocaleString()} 字`} variant="selection"><div aria-live="polite" className="reader-revision-analysis"><span aria-hidden="true" className="is-ready">✓</span><p>可直接输入修订方向，也可点选一个常用火候关键词；候选将由 Engine 模型生成。</p></div><div className="reader-revision-direction-chips">{defaultRevisionDirections.map((item) => <button key={item} onClick={() => appendDirection(item)} type="button">{item}</button>)}</div><label className="reader-dialog-field">精修方向<textarea aria-label="精修方向" onChange={(event) => setDirection(event.target.value)} placeholder="写下你想要的修订方向，例如：保留含蓄感，把动作写得更克制。" value={direction} /></label></RevisionDialog>}
    {overlay === "save-preview" && <RevisionDialog cancelLabel="继续修订" confirmLabel={authoring?.session?.configured ? "保存为待批准提案" : "确认保存"} footerOrder="confirm-cancel" hideHeader onClose={() => setOverlay(null)} onConfirm={() => { if (authoring?.session?.configured) commitRevision(); else setOverlay("scope"); }} title="保存终稿修订" variant="preview"><p className="reader-revision-save-summary">候选约 {guard.wordCount.toLocaleString()} 字{guard.expectedWordCount > 0 ? ` · 目标 ${guard.expectedWordCount.toLocaleString()} 字` : ""}</p><ul className="reader-revision-notes">{[...guard.warnings, authoring?.session?.configured ? "仅保存候选；正式正文和报告不变。后端核验受影响章节，在共创提案中展示并等待专项批准。" : "保存后将标记本章评估/叙事状态需要刷新，并在下一步选择下游影响范围。"].map((note) => <li key={note}>{note}</li>)}</ul><pre className="reader-revision-diff">{buildRevisionDiff(savedText, draftText)}</pre></RevisionDialog>}
    {overlay === "scope" && <RevisionDialog confirmDisabled={saving} confirmLabel={saving ? "保存中…" : "按此范围保存"} footerOrder="confirm-cancel" onClose={() => setOverlay(null)} onConfirm={commitRevision} title="保存影响范围" variant="scope"><p className="reader-revision-save-summary">第 {chapterNumber(artifact) ?? "—"} 章终稿已改动</p><p className="reader-revision-dialog-copy">保存将写入章节终稿、记录版本历史，并按所选范围标记下游产物状态。</p><fieldset className="reader-revision-scope"><legend>下游影响范围</legend>{(["none", "next", "volume", "downstream"] as const).map((scope) => <label key={scope}><input checked={invalidationScope === scope} name="revision-scope" onChange={() => setInvalidationScope(scope)} type="radio" value={scope} /><span><b>{scopeLabel(scope)}</b><small>{({ none: "只记录本章修订痕迹，不标记下游章节。", next: "仅标记下一章的可恢复生成链路。", volume: "标记本卷内的下游章节。", downstream: "标记所有下游章节，保持原端默认。" })[scope]}</small></span></label>)}</fieldset><label className="reader-revision-checkbox"><input checked={backgroundReevaluate} onChange={(event) => setBackgroundReevaluate(event.target.checked)} type="checkbox" />保存后后台重评本章</label></RevisionDialog>}
    {overlay === "discard" && <RevisionDialog cancelLabel="继续修订" confirmLabel="还原本稿" onClose={() => setOverlay(null)} onConfirm={() => { restoreRevision(); setOverlay(null); }} title="还原未保存修改" variant="confirm"><p className="reader-revision-dialog-copy">将放弃当前砚修草稿，恢复到最近一次在本地会话中保存的终稿。</p></RevisionDialog>}
    {overlay === "return" && <SourceMessageDialog actions={[
      { id: "save", label: "保存定稿", tone: "primary" },
      { id: "discard", label: "放弃修改", tone: "danger" },
      { id: "cancel", label: "继续修订", tone: "secondary" },
    ]} informativeText="可以先保存定稿，也可以放弃本次修订。" message="当前终稿有未保存修改。" onAction={(actionId) => {
      if (actionId === "save") { void saveUnsavedSession(true); return; }
      if (actionId === "discard") { abandonAndReturn(); return; }
      setOverlay(null);
    }} onClose={() => setOverlay(null)} title="终稿修订尚未保存" />}
    {overlay === "humanize-add" && <RevisionDialog confirmDisabled={humanizeSaving || humanizeFormError.length > 0} confirmLabel={humanizeSaving ? "写入 Engine…" : "加入拟人化库"} onClose={() => setOverlay(null)} onConfirm={() => { void saveHumanizeSelection(); }} title="加入拟人化库" variant="confirm"><p className="reader-revision-dialog-copy">将选段作为共享拟人化库中的 AI 写作痕迹样例。确认后由 Engine 进行版本裁决并持久化。</p><blockquote className="reader-revision-humanize-preview">{humanizeSelection.slice(0, 120)}{humanizeSelection.length > 120 ? "…" : ""}</blockquote><div className="narrative-dialog-form humanize-editor-form"><label>名称<input aria-label="模式名称" maxLength={200} onChange={(event) => setHumanizeDraft((current) => ({ ...current, name: event.target.value }))} value={humanizeDraft.name} /></label><label>分类<input aria-label="模式分类" maxLength={200} onChange={(event) => setHumanizeDraft((current) => ({ ...current, category: event.target.value }))} value={humanizeDraft.category} /></label><label>严重度<select aria-label="模式严重度" onChange={(event) => setHumanizeDraft((current) => ({ ...current, severity: event.target.value as HumanizePatternSeverity }))} value={humanizeDraft.severity}><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label><label className="narrative-dialog-form-wide">关键词<input aria-label="模式关键词" onChange={(event) => setHumanizeDraft((current) => ({ ...current, keywords: event.target.value }))} placeholder="逗号分隔，例如：滥情，堆砌，口水" value={humanizeDraft.keywords} /></label><label className="narrative-dialog-form-wide">备注<textarea aria-label="模式备注" maxLength={8_000} onChange={(event) => setHumanizeDraft((current) => ({ ...current, notes: event.target.value }))} value={humanizeDraft.notes} /></label></div>{humanizeFormError.length > 0 && <p className="narrative-dialog-validation" role="alert">{humanizeFormError}</p>}{humanizeSaveError.length > 0 && <p className="narrative-dialog-validation" role="alert">{humanizeSaveError}</p>}<p className="reader-revision-dialog-copy">示例选段将随本次写入保存；成功后可在「叙事工具 → 拟人化库」继续管理。</p></RevisionDialog>}
  </section>;
}

type ChapterReportGroupId = "quality" | "structure" | "expression" | "creative";

const chapterReportGroups: readonly { readonly id: ChapterReportGroupId; readonly label: string; readonly labels: readonly string[] }[] = [
  { id: "quality", label: "质量", labels: ["质量评估", "质量门禁", "护栏", "知识边界"] },
  { id: "structure", label: "结构", labels: ["对齐报告", "连贯性", "因果链", "追读力", "控制"] },
  { id: "expression", label: "表达", labels: ["表达", "拟人化", "拟人化对比", "润色对比"] },
  { id: "creative", label: "创作", labels: ["创作总结"] },
];

function groupReports(artifacts: readonly ProjectReaderArtifactView[], groupId: ChapterReportGroupId): readonly ProjectReaderArtifactView[] {
  const group = chapterReportGroups.find((item) => item.id === groupId);
  return artifacts.filter((artifact) => group?.labels.includes(chapterReportTypeLabel(artifact)) ?? false);
}

/**
 * The reader catalog keeps the chapter context in `label`
 * ("第 1 章 · 质量评估") and the PySide leaf label in `caption`
 * ("质量评估").  Report grouping must use the leaf label; comparing the
 * catalog label directly filtered every real report out of the new reader.
 */
export function chapterReportTypeLabel(artifact: ProjectReaderArtifactView): string {
  const caption = artifact.caption.trim();
  if (chapterReportGroups.some((group) => group.labels.includes(caption))) return caption;
  return artifact.label.split("·").at(-1)?.trim() || artifact.label.trim();
}

export function ChapterReportBrowser({ artifacts, onOperation, projectTitle }: { readonly artifacts: readonly ProjectReaderArtifactView[]; readonly onOperation: (message: string) => void; readonly projectTitle: string }) {
  const availableGroups = chapterReportGroups.filter((group) => groupReports(artifacts, group.id).length > 0);
  const [activeGroupId, setActiveGroupId] = useState<ChapterReportGroupId>(availableGroups[0]?.id ?? "quality");
  const groupArtifacts = groupReports(artifacts, activeGroupId);
  const [activeArtifactId, setActiveArtifactId] = useState(groupArtifacts[0]?.id ?? "");
  const activeArtifact = groupArtifacts.find((artifact) => artifact.id === activeArtifactId) ?? groupArtifacts[0];
  const selectGroup = (groupId: ChapterReportGroupId) => {
    const nextArtifacts = groupReports(artifacts, groupId);
    setActiveGroupId(groupId);
    setActiveArtifactId(nextArtifacts[0]?.id ?? "");
    onOperation(`已切换章节报告分组：${chapterReportGroups.find((group) => group.id === groupId)?.label ?? ""}。`);
  };
  if (availableGroups.length === 0) return <section className="reader-empty">本章暂无报告。<br />章节流程完成后此处将展示质量评估等信息。</section>;
  return <section aria-label="章节报告阅读器" className="reader-report-workbench">
    {sourceTab({ activeId: activeGroupId, ariaLabel: "章节报告分组", items: availableGroups, onSelect: selectGroup })}
    <div className="reader-report-leaves" aria-label="章节报告类型" role="tablist">{groupArtifacts.map((artifact) => <button aria-selected={artifact.id === activeArtifact?.id} className={artifact.id === activeArtifact?.id ? "is-active" : ""} key={artifact.id} onClick={() => { setActiveArtifactId(artifact.id); onOperation(`已展开「${chapterReportTypeLabel(artifact)}」。`); }} role="tab" type="button">{chapterReportTypeLabel(artifact)}</button>)}</div>
    {activeArtifact === undefined ? <section className="reader-empty">暂无此类报告。</section> : <ReaderArtifactDocument artifact={activeArtifact} projectTitle={projectTitle} />}
  </section>;
}

type GovernanceGroupId = "book" | "admission" | "coherence";

const governanceGroups: readonly { readonly id: GovernanceGroupId; readonly label: string; readonly labels: readonly string[] }[] = [
  { id: "book", label: "全书审修", labels: ["一致性审计", "出版编辑审查", "修复报告"] },
  { id: "admission", label: "初始化准入", labels: ["初始化准入", "编辑契约准入", "初始化修复", "创意精炼"] },
  { id: "coherence", label: "一致性治理", labels: ["一致性画像", "一致性 Claims", "一致性 Claims 账本", "一致性索引", "冲突候选", "冲突裁决", "一致性 Claims 契约覆盖", "主题与结构", "角色主题", "世界观主题"] },
];

function governanceArtifacts(artifacts: readonly ProjectReaderArtifactView[], groupId: GovernanceGroupId): readonly ProjectReaderArtifactView[] {
  const group = governanceGroups.find((item) => item.id === groupId);
  return artifacts.filter((artifact) => group?.labels.includes(artifact.label) ?? false);
}

export function GovernanceBrowser({ artifacts, onOperation, projectTitle }: { readonly artifacts: readonly ProjectReaderArtifactView[]; readonly onOperation: (message: string) => void; readonly projectTitle: string }) {
  const availableGroups = governanceGroups.filter((group) => governanceArtifacts(artifacts, group.id).length > 0);
  const [groupId, setGroupId] = useState<GovernanceGroupId>(availableGroups[0]?.id ?? "book");
  const leaves = governanceArtifacts(artifacts, groupId);
  const [artifactId, setArtifactId] = useState(leaves[0]?.id ?? "");
  const selected = leaves.find((artifact) => artifact.id === artifactId) ?? leaves[0];
  const selectGroup = (id: GovernanceGroupId) => {
    const nextLeaves = governanceArtifacts(artifacts, id);
    setGroupId(id);
    setArtifactId(nextLeaves[0]?.id ?? "");
    onOperation(`已切换治理分组：${governanceGroups.find((group) => group.id === id)?.label ?? ""}。`);
  };
  if (availableGroups.length === 0) return <section className="reader-empty">暂无治理数据。<br />完成对应流程后将自动生成。</section>;
  return <section aria-label="治理文档" className="reader-governance-workbench">
    {sourceTab({ activeId: groupId, ariaLabel: "治理分类", items: availableGroups, onSelect: selectGroup })}
    <div aria-label="治理文档类型" className="reader-report-leaves" role="tablist">{leaves.map((artifact) => <button aria-selected={artifact.id === selected?.id} className={artifact.id === selected?.id ? "is-active" : ""} key={artifact.id} onClick={() => { setArtifactId(artifact.id); onOperation(`已展开「${artifact.label}」。`); }} role="tab" type="button">{artifact.label}</button>)}</div>
    {selected === undefined ? <section className="reader-empty">暂无此类治理文档。</section> : <ReaderArtifactDocument artifact={selected} projectTitle={projectTitle} />}
  </section>;
}

/**
 * 折叠连续章节号为区间段，对标 PySide6
 * `InteractiveOutlineWidget._format_selected_chapters`：
 * [4,5,6,9] → "4-6、9"；超过 maxSegments 段时以「… 等」截断。
 */
function formatSelectedChapters(numbers: readonly number[], maxSegments = 6): string {
  const ordered = [...new Set(numbers.filter((num) => num > 0))].sort((a, b) => a - b);
  if (ordered.length === 0) return "";
  const segments: string[] = [];
  let start = ordered[0]!;
  let prev = start;
  for (const num of ordered.slice(1)) {
    if (num === prev + 1) {
      prev = num;
      continue;
    }
    segments.push(start === prev ? String(start) : `${start}-${prev}`);
    start = prev = num;
  }
  segments.push(start === prev ? String(start) : `${start}-${prev}`);
  if (segments.length > maxSegments) {
    return `${segments.slice(0, maxSegments).join("、")} 等`;
  }
  return segments.join("、");
}

const outlinePolishFocusFields = [
  { key: "goal", label: "章节目标" },
  { key: "beats_summary", label: "节拍摘要" },
  { key: "main_plot_points", label: "主线推进" },
  { key: "subplot_points", label: "支线推进" },
  { key: "involved_characters", label: "角色参与" },
  { key: "notes", label: "创作备注" },
] as const;

export function OutlineSessionWorkbench({ commandClient, onOperation, outline, planning, totalChapters, projectId }: { readonly commandClient: EngineCommandClient; readonly onOperation: (message: string) => void; readonly outline: NarrativeToolsView["outline"]; readonly planning?: NarrativeToolsView["planning"]; readonly totalChapters?: number | undefined; readonly projectId: string }) {
  const [mode, setMode] = useState<"read" | "polish" | "maintain">("read");
  const [maintenanceAction, setMaintenanceAction] = useState<"sync" | "extend">("sync");
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set(outline.map((node) => node.id)));
  const [focusedNodeId, setFocusedNodeId] = useState(outline[0]?.id ?? "");
  const [direction, setDirection] = useState("强化章尾钩子，让每次线索推进都落到人物选择。");
  const [selectedFocus, setSelectedFocus] = useState<ReadonlySet<string>>(
    () => new Set(outlinePolishFocusFields.map((field) => field.key)),
  );
  const [submitting, setSubmitting] = useState(false);
  const [taskMessage, setTaskMessage] = useState("");
  const [extensionMode, setExtensionMode] = useState<"additional" | "target">("additional");
  const [extensionValue, setExtensionValue] = useState("10");
  const [maintenanceMessage, setMaintenanceMessage] = useState("");
  const [completionOpen, setCompletionOpen] = useState(false);
  const focused = outline.find((node) => node.id === focusedNodeId) ?? outline[0];
  const selectedChapterNumbers = outline.filter((node) => selectedIds.has(node.id)).map((node) => node.chapterNumber);
  const selectedCount = selectedChapterNumbers.length;
  const visibleThrough = outline.reduce((maximum, node) => Math.max(maximum, node.chapterNumber), 0);
  const currentTotal = planning?.totalChapters ?? totalChapters ?? visibleThrough;
  const hardThrough = planning?.hardThroughChapter;
  const needsCompletion = currentTotal > visibleThrough || (hardThrough !== undefined && hardThrough < currentTotal);
  const submitCompletion = async () => {
    if (submitting || !needsCompletion) return;
    setSubmitting(true);
    setMaintenanceMessage("");
    try {
      const result = await commandClient.extendOutline({
        kind: "extend_outline", projectId, targetTotal: currentTotal,
        decommissionOldEnding: false, syncContracts: true, reason: "complete_existing_planning",
      });
      if (result.status !== "accepted") throw new Error(result.message);
      setCompletionOpen(false);
      setMaintenanceMessage(`${result.message}${result.taskId ? `（任务 ${result.taskId}）` : ""}`);
      onOperation(`补齐全书规划任务已提交；保持 ${currentTotal} 章目标和已确认章节不变，可在机杼查看进度。`);
    } catch (error) {
      setMaintenanceMessage(error instanceof Error ? error.message : "补齐规划任务提交失败。");
    } finally {
      setSubmitting(false);
    }
  };
  const selectionCountLabel = selectedCount === 0 ? `未选章节 / 共 ${outline.length} 章` : `已选 ${selectedCount}/${outline.length} 章 · 第 ${formatSelectedChapters(selectedChapterNumbers)} 章`;
  const toggleNode = (nodeId: string) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(nodeId)) next.delete(nodeId);
    else next.add(nodeId);
    return next;
  });
  const submit = async () => {
    if (selectedChapterNumbers.length === 0 || submitting) return;
    setSubmitting(true);
    setTaskMessage("");
    try {
      const result = await commandClient.polishOutline({
        kind: "polish_outline",
        projectId,
        userHint: direction,
        focusFields: [...selectedFocus],
        chapterRange: selectedChapterNumbers.join(","),
        syncContracts: true,
      });
      if (result.status !== "accepted") throw new Error(result.message);
      setTaskMessage(`${result.message}${result.taskId ? `（任务 ${result.taskId}）` : ""}`);
      onOperation("大纲润色任务已提交；任务完成后会刷新受影响章节契约。可在机杼查看实时进度。");
    } catch (error) {
      setTaskMessage(error instanceof Error ? error.message : "大纲润色任务提交失败。");
    } finally {
      setSubmitting(false);
    }
  };
  const openMaintenance = (action: "sync" | "extend") => {
    setMaintenanceAction(action);
    setMaintenanceMessage("");
    setMode("maintain");
  };
  const submitSync = async () => {
    if (selectedChapterNumbers.length === 0 || submitting) return;
    setSubmitting(true);
    setMaintenanceMessage("");
    try {
      const result = await commandClient.syncChapterContracts({
        kind: "sync_chapter_contracts",
        projectId,
        affectedChapterNumbers: selectedChapterNumbers,
        cascadeDownstream: true,
        rebuildMilestones: true,
        markStale: true,
        proseUntouched: true,
      });
      if (result.status !== "accepted") throw new Error(result.message);
      setMaintenanceMessage(`${result.message}${result.taskId ? `（任务 ${result.taskId}）` : ""}`);
      onOperation("章节契约同步任务已提交；正文保持不变，可在机杼查看实时进度。");
    } catch (error) {
      setMaintenanceMessage(error instanceof Error ? error.message : "章节契约同步任务提交失败。");
    } finally {
      setSubmitting(false);
    }
  };
  const submitExtend = async () => {
    const value = Number.parseInt(extensionValue, 10);
    if (!Number.isFinite(value) || value < 1 || submitting) return;
    setSubmitting(true);
    setMaintenanceMessage("");
    try {
      const result = await commandClient.extendOutline({
        kind: "extend_outline",
        projectId,
        ...(extensionMode === "additional" ? { additionalChapters: value } : { targetTotal: value }),
        decommissionOldEnding: true,
        syncContracts: true,
      });
      if (result.status !== "accepted") throw new Error(result.message);
      setMaintenanceMessage(`${result.message}${result.taskId ? `（任务 ${result.taskId}）` : ""}`);
      onOperation("延长全书任务已提交；新章节生成后会自动同步章节契约。");
    } catch (error) {
      setMaintenanceMessage(error instanceof Error ? error.message : "延长全书任务提交失败。");
    } finally {
      setSubmitting(false);
    }
  };
  const extensionValueValid = Number.isFinite(Number.parseInt(extensionValue, 10))
    && Number.parseInt(extensionValue, 10) >= (extensionMode === "target" ? currentTotal + 1 : 1);
  return <section aria-label="章节大纲工作台" className="reader-outline-workbench">
    <div aria-label="全书规划进度" className="reader-outline-coverage">
      <span>全书目标 {currentTotal} 章 · 已有大纲 {outline.length} 章{hardThrough === undefined ? "" : ` · 已细化至第 ${hardThrough} 章`}</span>
      {needsCompletion && <button className="button button-secondary" disabled={submitting} onClick={() => setCompletionOpen(true)} type="button">补齐全书规划</button>}
      {needsCompletion && <small>当前显示的是已规划范围，不是全书章数上限；补齐无需延长全书。</small>}
      {planning?.archivedChapters !== undefined && <small>已归档 {planning.archivedChapters} 章 · 预览至第 {planning.plannedThroughChapter} 章</small>}
      {planning?.task && <p role="status">补纲：{({ queued: "等待执行", running: "正在细化", candidate: "候选待批准", published: "已发布", paused: "已暂停", failed: "规划待重试，已归档章节不受影响" } as Record<string, string>)[planning.task.status] ?? planning.task.status}{planning.task.error ? ` · ${planning.task.error}` : ""}
        {commandClient.retryPlanningHorizon && ["failed", "paused"].includes(planning.task.status) && <button className="button button-quiet" disabled={submitting} type="button" onClick={async () => {
          setSubmitting(true);
          try { const result = await commandClient.retryPlanningHorizon!(projectId, planning.task?.chapterNumber ?? 1); setMaintenanceMessage(result.message); onOperation(result.message); }
          catch (error) { setMaintenanceMessage(error instanceof Error ? error.message : "规划重试失败"); }
          finally { setSubmitting(false); }
        }}>重试本批规划</button>}
      </p>}
      {maintenanceMessage && mode !== "maintain" && <p role="status">{maintenanceMessage}</p>}
    </div>
    {completionOpen && <AppDialog title="补齐全书规划" description={`保持全书 ${currentTotal} 章目标、已确认大纲和正文不变；补齐后续大纲并同步契约。该操作会调用模型，耗时和用量取决于剩余章数。`} confirmLabel={submitting ? "正在提交…" : "确认补齐规划"} confirmDisabled={submitting} closeOnConfirm={false} onConfirm={() => void submitCompletion()} onClose={() => setCompletionOpen(false)}>
      {maintenanceMessage && <p role="status">{maintenanceMessage}</p>}
    </AppDialog>}
    <header className="reader-outline-toolbar"><div className="reader-outline-mode" role="tablist" aria-label="大纲模式"><button aria-selected={mode === "read"} className={mode === "read" ? "is-active" : ""} onClick={() => setMode("read")} role="tab" type="button">阅读</button><button aria-selected={mode === "polish"} className={mode === "polish" ? "is-active" : ""} onClick={() => setMode("polish")} role="tab" type="button">润色</button></div><div className="reader-outline-toolbar-actions"><span className="reader-outline-task-note">后台任务完成后自动刷新项目资料</span><button className={mode === "maintain" && maintenanceAction === "sync" ? "button button-secondary is-active" : "button button-quiet"} onClick={() => openMaintenance("sync")} type="button">同步契约</button><button className={mode === "maintain" && maintenanceAction === "extend" ? "button button-secondary is-active" : "button button-quiet"} onClick={() => openMaintenance("extend")} type="button">延长全书</button></div></header>
    <div className="reader-outline-body"><aside aria-label="大纲章节列表">{outline.map((node) => <label className={node.id === focused?.id ? "is-active" : ""} key={node.id}><input aria-label={`选择 ${node.chapterLabel}`} checked={selectedIds.has(node.id)} onChange={() => toggleNode(node.id)} type="checkbox" /><button onClick={() => setFocusedNodeId(node.id)} type="button"><span>{node.chapterLabel}</span><strong>{node.title}</strong><small>{node.stateLabel}</small></button></label>)}</aside><div className="reader-outline-content">{mode === "read" ? <article><header><span className="section-kicker">{focused?.chapterLabel ?? "章节"} · {focused?.stateLabel ?? ""}</span><h2>{focused?.title ?? "暂无章节大纲"}</h2></header><OutlineReadingView key={focused?.id ?? "empty"} node={focused} /><footer>已选择 {selectedCount} / {outline.length} 章 · 阅读视图不会修改项目数据。</footer></article> : mode === "polish" ? <div className="reader-outline-polish"><div className="reader-outline-polish-controls"><label>润色范围<span>{selectionCountLabel}</span></label><div><button className="button button-quiet" onClick={() => setSelectedIds(new Set(outline.map((node) => node.id)))} type="button">全选</button><button className="button button-quiet" onClick={() => setSelectedIds(new Set())} type="button">全不选</button></div><label className="reader-outline-direction">润色方向<textarea aria-label="大纲润色方向" onChange={(event) => setDirection(event.target.value)} value={direction} /></label><fieldset className="reader-outline-focus"><legend>重点润色内容</legend><div>{outlinePolishFocusFields.map((field) => <label key={field.key}><input checked={selectedFocus.has(field.key)} onChange={() => setSelectedFocus((current) => nextSelectedIds(current, field.key))} type="checkbox" />{field.label}</label>)}</div></fieldset><div className="reader-outline-actions"><button className="button button-primary" disabled={selectedCount === 0 || selectedFocus.size === 0 || submitting} onClick={() => void submit()} type="button">{submitting ? "正在提交…" : "提交润色任务"}</button></div></div><div className="reader-outline-preview"><header><strong>任务状态</strong><span>{submitting ? "提交中" : taskMessage ? "已提交" : "等待提交"}</span></header><p>{taskMessage || "润色任务属于卷帙：选定章节和重点内容后提交；引擎会写入 outline.json、同步契约，任务进度仍可在机杼查看。"}</p></div></div> : <div className="reader-outline-maintenance"><header><span className="section-kicker">卷帙维护</span><h2>{maintenanceAction === "sync" ? "同步章节契约" : "延长全书"}</h2><p>{maintenanceAction === "sync" ? "依据当前大纲刷新章节契约与里程碑；只标记受影响的下游产物，绝不改写章节正文。" : `当前大纲共 ${currentTotal} 章。扩展会续写 outline.json，并自动同步新增章节契约。`}</p></header>{maintenanceAction === "sync" ? <div className="reader-outline-maintenance-form"><strong>{selectionCountLabel}</strong><p>同步范围沿用左侧勾选；如需调整，可直接勾选或取消章节。</p><button className="button button-primary" disabled={selectedCount === 0 || submitting} onClick={() => void submitSync()} type="button">{submitting ? "正在提交…" : "确认同步契约"}</button></div> : <div className="reader-outline-maintenance-form"><fieldset><legend>扩展方式</legend><label><input checked={extensionMode === "additional"} name="outline-extension-mode" onChange={() => setExtensionMode("additional")} type="radio" />追加章节</label><label><input checked={extensionMode === "target"} name="outline-extension-mode" onChange={() => { setExtensionMode("target"); setExtensionValue(String(Math.max(currentTotal + 10, 1))); }} type="radio" />延长至总章数</label></fieldset><label className="reader-outline-extension-value">{extensionMode === "additional" ? "追加章数" : "目标总章数"}<input aria-label={extensionMode === "additional" ? "追加章数" : "目标总章数"} min={extensionMode === "additional" ? 1 : currentTotal + 1} onChange={(event) => setExtensionValue(event.target.value)} type="number" value={extensionValue} /></label><label className="reader-outline-maintenance-check"><input checked readOnly type="checkbox" />停用旧结局标记，并同步新增章节契约</label><button className="button button-primary" disabled={!extensionValueValid || submitting} onClick={() => void submitExtend()} type="button">{submitting ? "正在提交…" : "确认延长全书"}</button></div>}{maintenanceMessage.length > 0 && <div aria-live="polite" className="reader-outline-maintenance-status">{maintenanceMessage}</div>}</div>}</div></div>
  </section>;
}

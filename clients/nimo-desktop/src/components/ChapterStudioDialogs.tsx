import { useState } from "react";

import type { ChapterStudioCheckpointView } from "@nimo/engine-contracts";

import { defaultChapterAuditParameters, validateChapterBookAuditRequest, type ChapterAuditAnalysisMode, type ChapterAuditParameters, type ChapterAuditRange, type ChapterAuditStrictness, type ChapterBookAuditRequest } from "../lib/chapter-book-audit-session";
import { defaultChapterBookRepairParameters, normalizeChapterBookRepairParameters, type ChapterBookRepairParameters } from "../lib/chapter-book-repair-session";
import { chapterExportFormats, createChapterExportRequest, type ChapterExportFormat, type ChapterExportRange, type ChapterExportRequest } from "../lib/chapter-export-session";
import { usePanelDrag } from "../lib/use-panel-drag";
import { createChapterVersionComparison, type ChapterVersionComparison, type ChapterVersionView } from "../lib/chapter-version-diff";
import { OverlaySurface } from "./OverlaySurface";

interface ChapterDialogFrameProps {
  readonly title: string;
  readonly subtitle: React.ReactNode;
  readonly children: React.ReactNode;
  readonly confirmLabel: string;
  readonly confirmDisabled?: boolean;
  readonly onConfirm: () => void;
  readonly onClose: () => void;
  readonly variant?: "audit" | "export";
  readonly width: "compact" | "standard" | "wide";
}

function ChapterDialogFrame({ children, confirmDisabled = false, confirmLabel, onClose, onConfirm, subtitle, title, variant, width }: ChapterDialogFrameProps) {
  return (
    <OverlaySurface ariaLabel={title} onClose={onClose}>
      <section className={`chapter-dialog is-${width}${variant === undefined ? "" : ` is-${variant}`}`}>
        <div className="chapter-dialog-scroll">
          <header><h2>{title}</h2><p>{subtitle}</p></header>
          {children}
        </div>
        <footer><button className="chapter-dialog-quiet" onClick={onClose} type="button">取消</button><button className="chapter-dialog-primary" disabled={confirmDisabled} onClick={onConfirm} type="button">{confirmLabel}</button></footer>
      </section>
    </OverlaySurface>
  );
}

export function ChapterExportDialog({ chapterNumbers, onClose, onConfirm, projectTitle }: { readonly chapterNumbers: readonly number[]; readonly onClose: () => void; readonly onConfirm: (request: ChapterExportRequest) => void; readonly projectTitle: string }) {
  const [format, setFormat] = useState<ChapterExportFormat>("markdown");
  const [range, setRange] = useState<ChapterExportRange>("all");
  const [selectedChapters, setSelectedChapters] = useState<ReadonlySet<number>>(() => new Set(chapterNumbers));
  const [title, setTitle] = useState(projectTitle);
  const selectChapter = (chapter: number) => setSelectedChapters((current) => {
    const next = new Set(current);
    if (next.has(chapter)) next.delete(chapter);
    else next.add(chapter);
    return next;
  });
  const accept = () => {
    onConfirm(createChapterExportRequest({
      format,
      range,
      selectedChapters,
      outputDirectory: "",
      bookTitle: title,
      defaultBookTitle: projectTitle,
    }));
    onClose();
  };

  return (
    <ChapterDialogFrame confirmLabel="开始导出" onClose={onClose} onConfirm={accept} subtitle={`当前共 ${chapterNumbers.length} 个已完成章节可供导出。`} title="导出设置" variant="export" width="standard">
      <DialogSection title="导出格式">
        <p className="chapter-dialog-hint">导出保留当前正文，并附正文哈希与验证状态清单；待重验章节可作为工作稿导出，但不代表已通过交付验证。</p>
        <div className="chapter-radio-row">{chapterExportFormats.map((item) => <label key={item.id}><input checked={format === item.id} name="export-format" onChange={() => setFormat(item.id)} type="radio" />{item.label}</label>)}</div>
        <p className="chapter-dialog-hint">Markdown 保留章节标题标记；纯文本去除所有格式；EPUB 可直接导入 Kindle / Apple Books 等阅读器。</p>
      </DialogSection>
      <DialogSection title="导出范围">
        <div className="chapter-radio-stack"><label><input checked={range === "all"} name="export-range" onChange={() => setRange("all")} type="radio" />全部已完成章节（共 {chapterNumbers.length} 章）</label><label><input checked={range === "custom"} name="export-range" onChange={() => setRange("custom")} type="radio" />选择特定章节</label></div>
        {range === "custom" && <div className="chapter-checkbox-grid" role="group" aria-label="导出章节">{chapterNumbers.map((chapter) => <label key={chapter}><input checked={selectedChapters.has(chapter)} onChange={() => selectChapter(chapter)} type="checkbox" />第{chapter}章</label>)}</div>}
      </DialogSection>
      <DialogSection title="交付位置">
        <p className="chapter-dialog-hint">文件将由 Engine 写入当前项目的 exports 目录。本地与远程模式共用同一交付边界，界面不会伪造未实际选中的路径。</p>
      </DialogSection>
      <DialogSection title="书名">
        <input aria-label="导出书名" className="chapter-text-input" onChange={(event) => setTitle(event.target.value)} value={title} />
        <p className="chapter-dialog-hint">此处书名用于导出文件名。不填则自动采用项目初始化时的书名。</p>
      </DialogSection>
    </ChapterDialogFrame>
  );
}

export function ChapterBookAuditDialog({ auditDomain = "consistency", chapterNumbers, onClose, onConfirm }: { readonly auditDomain?: "consistency" | "editorial"; readonly chapterNumbers: readonly number[]; readonly onClose: () => void; readonly onConfirm: (request: ChapterBookAuditRequest) => void }) {
  const [range, setRange] = useState<ChapterAuditRange>("all");
  const [selectedChapters, setSelectedChapters] = useState<ReadonlySet<number>>(() => new Set(chapterNumbers));
  const [advanced, setAdvanced] = useState(false);
  const [parameters, setParameters] = useState<ChapterAuditParameters>(defaultChapterAuditParameters);
  const [minimumChapterWarningOpen, setMinimumChapterWarningOpen] = useState(false);
  const editorial = auditDomain === "editorial";
  const setParameter = <Key extends keyof ChapterAuditParameters>(key: Key, value: ChapterAuditParameters[Key]) => {
    setParameters((current) => ({ ...current, [key]: value }));
  };
  const toggleChapter = (chapter: number) => setSelectedChapters((current) => {
    const next = new Set(current);
    if (next.has(chapter)) next.delete(chapter);
    else next.add(chapter);
    return next;
  });
  const submit = () => {
    const result = validateChapterBookAuditRequest({
      range,
      selectedChapters,
      parameters,
      completedChapterCount: chapterNumbers.length,
    });
    if (result.kind === "minimum-chapter-count") {
      setMinimumChapterWarningOpen(true);
      return;
    }
    onConfirm(result.request);
    onClose();
  };

  return (
    <>
      <ChapterDialogFrame confirmLabel={editorial ? "开始出版审查" : "开始审计"} onClose={onClose} onConfirm={submit} subtitle={<>当前共 {chapterNumbers.length} 个已完成章节。<br />{editorial ? "AI 将按出版编辑契约审查结构、节奏、人物弧光与语言成熟度，并生成需人工确认的修订队列。" : "AI 将检查章节间的命名一致性、时间线连贯性、世界观设定、角色状态和叙事走向。"}</>} title={editorial ? "全书出版编辑审查" : "全书一致性审计"} variant="audit" width="wide">
        <DialogSection title="审计范围"><div className="chapter-radio-stack"><label><input checked={range === "all"} name="audit-range" onChange={() => setRange("all")} type="radio" />全部已完成章节（共 {chapterNumbers.length} 章）</label><label><input checked={range === "custom"} name="audit-range" onChange={() => setRange("custom")} type="radio" />选择特定章节（至少 2 章）</label></div>{range === "custom" && <div className="chapter-checkbox-grid" role="group" aria-label="审计章节">{chapterNumbers.map((chapter) => <label key={chapter}><input checked={selectedChapters.has(chapter)} onChange={() => toggleChapter(chapter)} type="checkbox" />第{chapter}章</label>)}</div>}</DialogSection>
        <DialogSection title="审查策略"><div className="chapter-radio-stack"><label><input checked name="audit-strategy" readOnly type="radio" />{editorial ? "从出版编辑视角重新评估已完成章节" : "重新审计：从头执行完整全书审计（将覆盖上次审计结果）"}</label></div><p className="chapter-dialog-hint">{editorial ? "出版审查只写入报告与可追溯修订队列，不会直接改写正文。" : "审计会从头扫描并更新全局问题池；正文修复由独立的安全修复队列执行。"}</p></DialogSection>
        <DialogSection title="分析模式与参数">
          {!editorial && <div className="chapter-compact-actions"><button className="chapter-dialog-quiet" onClick={() => setAdvanced((current) => !current)} type="button">{advanced ? "简单模式" : "高级模式"}</button></div>}
          {editorial ? <>
            <AuditSettingRow label="审查输出上限"><input aria-label="出版审查输出上限" inputMode="numeric" max="65536" min="512" onChange={(event) => setParameter("maxTokens", Number(event.target.value))} type="number" value={parameters.maxTokens} /></AuditSettingRow>
            <AuditSettingRow label="审查温度"><input aria-label="出版审查温度" inputMode="decimal" max="2" min="0" onChange={(event) => setParameter("temperature", Number(event.target.value))} step="0.05" type="number" value={parameters.temperature} /></AuditSettingRow>
            <AuditSettingRow label="每批章节"><input aria-label="出版审查每批章节" inputMode="numeric" max={Math.max(1, Math.min(100, chapterNumbers.length))} min="1" onChange={(event) => setParameter("chaptersPerBatch", Number(event.target.value))} type="number" value={parameters.chaptersPerBatch} /></AuditSettingRow>
            <label className="chapter-audit-prompt"><span>出版审查重点（可选）</span><textarea aria-label="出版审查重点" onChange={(event) => setParameter("promptHint", event.target.value)} placeholder="例如：优先评估前三章留存、中段拖沓和终局情感回报。" value={parameters.promptHint} /></label>
          </> : <>
          <AuditSettingRow label="分析模式"><select aria-label="审计分析模式" onChange={(event) => setParameter("analysisMode", event.target.value as ChapterAuditAnalysisMode)} value={parameters.analysisMode}><option value="full_text">智能深审（摘要筛查→定向全文，推荐）</option><option value="summary">摘要审计（更快）</option></select></AuditSettingRow>
          <label className="chapter-check-label"><input checked={parameters.useTwoPhase} onChange={(event) => setParameter("useTwoPhase", event.target.checked)} type="checkbox" />智能漏斗：先摘要筛查，再定向全文深审</label>
          <AuditSettingRow label="定向深审章节"><input aria-label="定向深审章节" inputMode="numeric" max={Math.max(1, chapterNumbers.length)} min="1" onChange={(event) => setParameter("targetChapterLimit", Number(event.target.value))} type="number" value={parameters.targetChapterLimit} /></AuditSettingRow>
          {advanced && <>
            <AuditSettingRow label="定位严格度"><select aria-label="审计定位严格度" onChange={(event) => setParameter("locationStrictness", event.target.value as ChapterAuditStrictness)} value={parameters.locationStrictness}><option value="strict">严格（优先精确到段）</option><option value="balanced">平衡（推荐）</option><option value="loose">宽松（覆盖更多）</option></select></AuditSettingRow>
            <div className="chapter-audit-advanced-grid"><AuditNumericField label="max_tokens" onChange={(value) => setParameter("maxTokens", value)} value={parameters.maxTokens} /><AuditNumericField label="temperature" onChange={(value) => setParameter("temperature", value)} step="0.05" value={parameters.temperature} /><AuditNumericField label="每块问题数" onChange={(value) => setParameter("issuesPerChunk", value)} value={parameters.issuesPerChunk} /><AuditNumericField label="问题池条目" onChange={(value) => setParameter("issuePoolLimit", value)} value={parameters.issuePoolLimit} /><AuditNumericField label="漏斗阈值" onChange={(value) => setParameter("twoPhaseThreshold", value)} step="0.05" value={parameters.twoPhaseThreshold} /><AuditNumericField label="单章最大字符" onChange={(value) => setParameter("chapterMaxChars", value)} value={parameters.chapterMaxChars} /></div>
            <p className="chapter-dialog-hint">全文深审会注入章节全文并输出“章节+段落锚点”；摘要模式更省成本，但定位粒度较粗。</p>
          </>}
          <AuditSettingRow label="每批审查章节"><input aria-label="每批审查章节" inputMode="numeric" max={Math.max(1, chapterNumbers.length)} min="1" onChange={(event) => setParameter("chaptersPerBatch", Number(event.target.value))} type="number" value={parameters.chaptersPerBatch} /></AuditSettingRow>
          <div className="chapter-audit-parallel"><strong>并行加速</strong><p className="chapter-dialog-hint">注意：并行加速会消耗更多 Token（并发调用），但大幅缩短等待时间。多维度和分块并行可叠加。</p><label className="chapter-check-label"><input checked={parameters.parallelChunks} onChange={(event) => setParameter("parallelChunks", event.target.checked)} type="checkbox" />分块并行：多块 LLM 请求同时发出（2-4x 加速）</label><label className="chapter-check-label"><input checked={parameters.parallelDimensions} onChange={(event) => setParameter("parallelDimensions", event.target.checked)} type="checkbox" />维度并行：命名/时间线/世界观/角色/漂移分别审查（3-5x 加速）</label></div>
          {advanced && <label className="chapter-audit-prompt"><span>审计提示词（可选）</span><textarea aria-label="审计提示词" onChange={(event) => setParameter("promptHint", event.target.value)} placeholder="例如：优先检查人物称谓变体、法术规则边界、关键道具归属与时间线跳跃。" value={parameters.promptHint} /></label>}
          </>}
        </DialogSection>
      </ChapterDialogFrame>
      {minimumChapterWarningOpen && <ChapterAuditMinimumWarning onClose={() => setMinimumChapterWarningOpen(false)} />}
    </>
  );
}

export function ChapterBookRepairDialog({ onClose, onConfirm }: { readonly onClose: () => void; readonly onConfirm: (parameters: ChapterBookRepairParameters) => void }) {
  const [parameters, setParameters] = useState<ChapterBookRepairParameters>(defaultChapterBookRepairParameters);
  const submit = () => {
    onConfirm(normalizeChapterBookRepairParameters(parameters));
    onClose();
  };

  return (
    <ChapterDialogFrame
      confirmLabel="执行安全修复"
      onClose={onClose}
      onConfirm={submit}
      subtitle="使用最近一次已完成的全书审计，仅执行已具备精确锚点且被标记为 ready 的修复项。"
      title="全书审计修复队列"
      variant="audit"
      width="standard"
    >
      <DialogSection title="安全边界">
        <label className="chapter-check-label"><input checked disabled type="checkbox" />修复前复验正文版本与问题锚点（强制）</label>
        <label className="chapter-check-label"><input checked disabled type="checkbox" />修复失败或验证回归时回滚（强制）</label>
        <p className="chapter-dialog-hint">待验证、人工复核和已阻断的项不会自动改写正文。修复后的章节会进入拟人化、状态回放与最终校验链。</p>
      </DialogSection>
      <DialogSection title="本次执行">
        <AuditSettingRow label="最多处理项数"><input aria-label="全书修复最多项数" inputMode="numeric" max="500" min="1" onChange={(event) => setParameters((current) => ({ ...current, maxItems: Number(event.target.value) }))} type="number" value={parameters.maxItems} /></AuditSettingRow>
        <AuditSettingRow label="并发章节数"><input aria-label="全书修复并发数" inputMode="numeric" max="8" min="1" onChange={(event) => setParameters((current) => ({ ...current, concurrency: Number(event.target.value) }))} type="number" value={parameters.concurrency} /></AuditSettingRow>
        <p className="chapter-dialog-hint">同一章的修复始终串行；商业发布前建议保持并发数为 1，避免跨章关联修复产生竞争。</p>
      </DialogSection>
    </ChapterDialogFrame>
  );
}

export function ChapterVersionDiffDialog({ chapterNumber, onClose, onCompare, versions }: { readonly chapterNumber: number; readonly onClose: () => void; readonly onCompare: (comparison: ChapterVersionComparison) => void; readonly versions: readonly ChapterVersionView[] }) {
  const [olderVersionId, setOlderVersionId] = useState(() => versions[0]?.id ?? "");
  const [newerVersionId, setNewerVersionId] = useState(() => versions[1]?.id ?? versions[0]?.id ?? "");
  const submit = () => {
    const older = versions.find((version) => version.id === olderVersionId);
    const newer = versions.find((version) => version.id === newerVersionId);
    if (older === undefined || newer === undefined) return;
    const comparison = createChapterVersionComparison(chapterNumber, older, newer);
    // VersionDiffDialog deliberately leaves the dialog open for an invalid
    // same-version choice; the PySide6 source has the same silent guard.
    if (comparison === null) return;
    onCompare(comparison);
    onClose();
  };
  return (
    <ChapterDialogFrame confirmLabel="开始对比" onClose={onClose} onConfirm={submit} subtitle={`Engine 已提供 ${versions.length} 个版本可供对比，请选择两个版本进行比较。`} title={`第 ${chapterNumber} 章 · 版本对比`} width="compact">
      <DialogSection title="对比基准（旧版本）">{versions.map((version) => <VersionChoice checked={olderVersionId === version.id} key={version.id} label={`${version.label}　·　${version.wordCount.toLocaleString()} 字`} name="older-version" onChange={() => setOlderVersionId(version.id)} />)}</DialogSection>
      <DialogSection title="对比目标（新版本）">{versions.map((version) => <VersionChoice checked={newerVersionId === version.id} key={version.id} label={`${version.label}　·　${version.wordCount.toLocaleString()} 字`} name="newer-version" onChange={() => setNewerVersionId(version.id)} />)}<p className="chapter-dialog-hint">选择两个不同版本，系统将以行级差异高亮显示修改前后的变化——绿色为新增、红色为删除。</p></DialogSection>
    </ChapterDialogFrame>
  );
}

interface ChapterCheckpointDialogProps {
  readonly checkpoint: ChapterStudioCheckpointView;
  readonly onClose: () => void;
  readonly onResolve: (optionId: string, notes: string) => void;
}

/**
 * The PySide6 source presents checkpoint decisions as a right-side floating
 * tool, not a centered confirmation modal.  Keeping its decision state here
 * makes the host page responsible only for resuming the workflow.
 */
export function ChapterCheckpointDialog({ checkpoint, onClose, onResolve }: ChapterCheckpointDialogProps) {
  const [notes, setNotes] = useState("");
  const drag = usePanelDrag();
  const choose = (optionId: string) => onResolve(optionId, notes.trim());

  return (
    <OverlaySurface ariaLabel={checkpoint.title} backdropClassName="checkpoint-overlay" modal={false} onClose={onClose} style={{ transform: `translate(${drag.offset.x}px, ${drag.offset.y}px)` }}>
      <section className="checkpoint-dialog" aria-label="章节方案确认">
        <header className="checkpoint-dialog-header" {...drag.headerProps}>
          <span>{checkpoint.title}</span>
          <button aria-label="关闭方案确认" onClick={onClose} type="button">×</button>
        </header>
        <div className="checkpoint-dialog-scroll">
          <section className="checkpoint-summary"><p>{checkpoint.summary}</p><small>{checkpoint.prompt}</small></section>
          {checkpoint.options.map((option) => <CheckpointChoice description={option.description} key={option.id} label={option.label} onChoose={() => choose(option.id)} recommended={option.recommended} />)}
          <textarea aria-label="对 AI 的补充说明" onChange={(event) => setNotes(event.target.value)} placeholder="对 AI 的补充说明…" value={notes} />
        </div>
      </section>
    </OverlaySurface>
  );
}

function CheckpointChoice({ description, label, onChoose, recommended = false }: { readonly description: string; readonly label: string; readonly onChoose: () => void; readonly recommended?: boolean }) {
  return (
    <article className={recommended ? "checkpoint-choice is-recommended" : "checkpoint-choice"}>
      <header>{recommended && <span>推荐</span>}<strong>{label}</strong><button onClick={onChoose} type="button">{recommended ? "确认" : "选择"}</button></header>
      <p>{description}</p>
    </article>
  );
}

function DialogSection({ children, title }: { readonly children: React.ReactNode; readonly title: string }) {
  return <section className="chapter-dialog-section"><h3>{title}</h3>{children}</section>;
}

function VersionChoice({ checked, label, name, onChange }: { readonly checked: boolean; readonly label: string; readonly name: string; readonly onChange: () => void }) {
  return <label className="chapter-version-choice"><input checked={checked} name={name} onChange={onChange} type="radio" />{label}</label>;
}

function AuditSettingRow({ children, label }: { readonly children: React.ReactNode; readonly label: string }) {
  return <label className="chapter-field-row"><span>{label}</span>{children}</label>;
}

function AuditNumericField({ label, onChange, step = "1", value }: { readonly label: string; readonly onChange: (value: number) => void; readonly step?: string; readonly value: number }) {
  return <label><span>{label}</span><input aria-label={label} inputMode="decimal" onChange={(event) => onChange(Number(event.target.value))} step={step} type="number" value={value} /></label>;
}

function ChapterAuditMinimumWarning({ onClose }: { readonly onClose: () => void }) {
  return <OverlaySurface ariaLabel="章节不足" onClose={onClose}>
    <section className="chapter-audit-warning-dialog"><h2>章节不足</h2><p>至少需要选择 2 个章节才能进行一致性审计。</p><footer><button className="chapter-dialog-primary" onClick={onClose} type="button">确定</button></footer></section>
  </OverlaySurface>;
}

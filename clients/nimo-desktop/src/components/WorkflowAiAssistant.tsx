import { useMemo, useState } from "react";

import type {
  EngineCommandClient,
  GenerateWorkflowFieldsCommand,
  WorkflowFormMode,
} from "@nimo/engine-contracts";

import { allDiffIds, nextSelectedIds } from "../lib/workflow-preset-session";
import { OverlaySurface } from "./OverlaySurface";

export interface WorkflowAiField {
  readonly key: string;
  readonly label: string;
}

export interface WorkflowAiApplyContext {
  readonly creativeNote?: Readonly<Record<string, unknown>> | undefined;
  readonly creativeProfile: Readonly<Record<string, string>>;
  readonly focusFields: readonly string[];
  readonly generationMode: "replace" | "fill_blanks" | "variant";
  readonly hardConstraints: Readonly<Record<string, string>>;
  readonly selectedSuggestions: readonly string[];
  readonly userHint: string;
}

interface WorkflowAiAssistantProps {
  readonly available: boolean;
  readonly className: string;
  readonly commandClient: EngineCommandClient;
  readonly disabled?: boolean;
  readonly fields: readonly WorkflowAiField[];
  readonly mode: WorkflowFormMode;
  readonly onApply: (
    operation: "generate" | "polish",
    patch: Readonly<Record<string, unknown>>,
    changedKeys: readonly string[],
    context: WorkflowAiApplyContext,
  ) => void;
  readonly onNotice?: (message: string) => void;
  readonly payload: Readonly<Record<string, unknown>>;
}

interface WorkflowAiDiff {
  readonly id: string;
  readonly key: string;
  readonly label: string;
  readonly oldValue: string;
  readonly newValue: string;
  readonly nextValue: unknown;
}

type WorkflowAiDialog =
  | { readonly type: "input"; readonly operation: "generate" | "polish" }
  | { readonly type: "progress"; readonly operation: "generate" | "polish" }
  | {
      readonly type: "preview";
      readonly operation: "generate" | "polish";
      readonly diffs: readonly WorkflowAiDiff[];
      readonly creativeNote?: Readonly<Record<string, unknown>> | undefined;
      readonly message: string;
    }
  | { readonly type: "error"; readonly operation: "generate" | "polish"; readonly message: string }
  | null;

function DialogFrame({
  children,
  className,
  initialFocusSelector,
  label,
  onClose,
}: {
  readonly children: React.ReactNode;
  readonly className: string;
  readonly initialFocusSelector?: string;
  readonly label: string;
  readonly onClose: () => void;
}) {
  return (
    <OverlaySurface
      ariaLabel={label}
      className={`workflow-preset-overlay ${className}`}
      onClose={onClose}
      {...(initialFocusSelector === undefined ? {} : { initialFocusSelector })}
    >
      {children}
    </OverlaySurface>
  );
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "（空）";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

function buildDiffs(
  fields: readonly WorkflowAiField[],
  before: Readonly<Record<string, unknown>>,
  after: Readonly<Record<string, unknown>>,
): readonly WorkflowAiDiff[] {
  return fields.flatMap((field) => {
    if (!(field.key in after)) return [];
    const oldValue = displayValue(before[field.key]);
    const newValue = displayValue(after[field.key]);
    if (oldValue === newValue) return [];
    return [{
      id: `workflow-ai:${field.key}`,
      key: field.key,
      label: field.label,
      oldValue,
      newValue,
      nextValue: after[field.key],
    }];
  });
}

export type WorkflowInlineDiffKind = "equal" | "delete" | "insert";

export interface WorkflowInlineDiffSegment {
  readonly kind: WorkflowInlineDiffKind;
  readonly text: string;
}

const INLINE_DIFF_MATRIX_LIMIT = 120_000;

function appendInlineDiffSegment(
  segments: WorkflowInlineDiffSegment[],
  kind: WorkflowInlineDiffKind,
  text: string,
): void {
  if (text.length === 0) return;
  const previous = segments[segments.length - 1];
  if (previous?.kind === kind) {
    segments[segments.length - 1] = { kind, text: previous.text + text };
    return;
  }
  segments.push({ kind, text });
}

function prefixSuffixInlineDiff(before: readonly string[], after: readonly string[]): readonly WorkflowInlineDiffSegment[] {
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < before.length - prefix
    && suffix < after.length - prefix
    && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  ) suffix += 1;
  const segments: WorkflowInlineDiffSegment[] = [];
  appendInlineDiffSegment(segments, "equal", before.slice(0, prefix).join(""));
  appendInlineDiffSegment(segments, "delete", before.slice(prefix, before.length - suffix).join(""));
  appendInlineDiffSegment(segments, "insert", after.slice(prefix, after.length - suffix).join(""));
  appendInlineDiffSegment(segments, "equal", before.slice(before.length - suffix).join(""));
  return segments;
}

/**
 * Character-level LCS gives Chinese prose an inspectable, humanize-style
 * red/green comparison without turning a long field into two opaque blocks.
 * Large fields use the same prefix/suffix fallback as the document renderer,
 * so opening a preview never stalls the dialog.
 */
export function buildWorkflowInlineDiffSegments(beforeText: string, afterText: string): readonly WorkflowInlineDiffSegment[] {
  const before = Array.from(beforeText);
  const after = Array.from(afterText);
  if (before.length === 0 && after.length === 0) return [];
  if (before.length * after.length > INLINE_DIFF_MATRIX_LIMIT) {
    return prefixSuffixInlineDiff(before, after);
  }

  const matrix = Array.from({ length: before.length + 1 }, () => new Uint16Array(after.length + 1));
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      const currentRow = matrix[left]!;
      const nextRow = matrix[left + 1]!;
      currentRow[right] = before[left]! === after[right]!
        ? nextRow[right + 1]! + 1
        : Math.max(nextRow[right]!, currentRow[right + 1]!);
    }
  }

  const segments: WorkflowInlineDiffSegment[] = [];
  let left = 0;
  let right = 0;
  while (left < before.length && right < after.length) {
    const currentRow = matrix[left]!;
    const nextRow = matrix[left + 1]!;
    const beforeCharacter = before[left]!;
    const afterCharacter = after[right]!;
    if (beforeCharacter === afterCharacter) {
      appendInlineDiffSegment(segments, "equal", beforeCharacter);
      left += 1;
      right += 1;
    } else if (nextRow[right]! >= currentRow[right + 1]!) {
      appendInlineDiffSegment(segments, "delete", beforeCharacter);
      left += 1;
    } else {
      appendInlineDiffSegment(segments, "insert", afterCharacter);
      right += 1;
    }
  }
  appendInlineDiffSegment(segments, "delete", before.slice(left).join(""));
  appendInlineDiffSegment(segments, "insert", after.slice(right).join(""));
  return segments;
}

function WorkflowInlineDiff({ after, before }: { readonly after: string; readonly before: string }) {
  const segments = buildWorkflowInlineDiffSegments(before, after);
  if (segments.length === 0) return <span>（空）</span>;
  return <>
    {segments.map((segment, index) => {
      const key = `${segment.kind}-${index}`;
      if (segment.kind === "delete") return <del key={key}>{segment.text}</del>;
      if (segment.kind === "insert") return <ins key={key}>{segment.text}</ins>;
      return <span key={key}>{segment.text}</span>;
    })}
  </>;
}

function nonEmptyConstraints(values: Readonly<Record<string, string>>): Readonly<Record<string, string>> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value.trim().length > 0));
}

function WorkflowAiInputDialog({
  creativeBrief,
  creativeStyle,
  fields,
  generationMode,
  genre,
  hint,
  novelty,
  lengthTarget,
  conflictDensity,
  emotionIntensity,
  mode,
  onClose,
  onCreativeBriefChange,
  onCreativeStyleChange,
  onGenerationModeChange,
  onGenreChange,
  onHintChange,
  onLengthTargetChange,
  onNoveltyChange,
  onConflictDensityChange,
  onEmotionIntensityChange,
  onSelectedSuggestionsChange,
  onStart,
  onToneChange,
  onTotalChaptersChange,
  onWordsPerChapterChange,
  operation,
  selectedSuggestions,
  selectedFocus,
  suggestions,
  tone,
  totalChapters,
  toggleFocus,
  wordsPerChapter,
}: {
  readonly creativeBrief: string;
  readonly creativeStyle: string;
  readonly fields: readonly WorkflowAiField[];
  readonly generationMode: "replace" | "fill_blanks" | "variant";
  readonly genre: string;
  readonly hint: string;
  readonly novelty: string;
  readonly lengthTarget: string;
  readonly conflictDensity: string;
  readonly emotionIntensity: string;
  readonly mode: WorkflowFormMode;
  readonly onClose: () => void;
  readonly onCreativeBriefChange: (value: string) => void;
  readonly onCreativeStyleChange: (value: string) => void;
  readonly onGenerationModeChange: (value: "replace" | "fill_blanks" | "variant") => void;
  readonly onGenreChange: (value: string) => void;
  readonly onHintChange: (value: string) => void;
  readonly onLengthTargetChange: (value: string) => void;
  readonly onNoveltyChange: (value: string) => void;
  readonly onConflictDensityChange: (value: string) => void;
  readonly onEmotionIntensityChange: (value: string) => void;
  readonly onSelectedSuggestionsChange: (value: ReadonlySet<string>) => void;
  readonly onStart: () => void;
  readonly onToneChange: (value: string) => void;
  readonly onTotalChaptersChange: (value: string) => void;
  readonly onWordsPerChapterChange: (value: string) => void;
  readonly operation: "generate" | "polish";
  readonly selectedSuggestions: ReadonlySet<string>;
  readonly selectedFocus: ReadonlySet<string>;
  readonly suggestions: readonly string[];
  readonly tone: string;
  readonly totalChapters: string;
  readonly toggleFocus: (key: string) => void;
  readonly wordsPerChapter: string;
}) {
  const isGenerate = operation === "generate";
  const title = isGenerate ? "AI 生成并预览" : "AI 定向润色";
  const hasVariantConflict = generationMode === "variant" && (genre.trim().length > 0 || tone.trim().length > 0);
  const toggleSuggestion = (suggestion: string) => onSelectedSuggestionsChange(nextSelectedIds(selectedSuggestions, suggestion));
  const resetForRandomTheme = () => {
    onHintChange("");
    onGenerationModeChange("replace");
    onCreativeStyleChange("balanced");
    onNoveltyChange("fresh");
    onConflictDensityChange("layered");
    onEmotionIntensityChange("textured");
    onCreativeBriefChange("");
    onGenreChange("");
    onToneChange("");
    onLengthTargetChange("");
    onTotalChaptersChange("");
    onWordsPerChapterChange("");
  };
  return (
    <DialogFrame className={isGenerate ? "is-ai-hint" : "is-ai-polish"} initialFocusSelector="textarea" label={title} onClose={onClose}>
      <div className={isGenerate ? "workflow-ai-form" : "workflow-polish-form"}>
        <label>
          <span>{isGenerate ? "输入简要提示（可以是几个关键词、一句话灵感，也可以留空让 AI 完全自由发挥）：" : "输入你想强化的方向（例如：加强暧昧拉扯、提升悬疑密度、压缩解释性旁白）。"}</span>
          {isGenerate && <small>例如：失控的委托 / 架空王国的权力裂痕 / 当代都市中的关系谜题</small>}
          <textarea autoFocus onChange={(event) => onHintChange(event.target.value)} placeholder={isGenerate ? "在此输入你的创作灵感……" : "例：让设定表达更清晰，人物动机更立体，冲突推进更有压迫感。"} value={hint} />
        </label>
        {isGenerate ? <>
          <div className="workflow-ai-grid is-two is-mode">
            <label><span>生成方式</span><select aria-label="生成方式" onChange={(event) => onGenerationModeChange(event.target.value as typeof generationMode)} value={generationMode}><option value="replace">重写新方案</option><option value="fill_blanks">只补空白</option><option value="variant">生成变体</option></select></label>
            <label><span>创意取向</span><select aria-label="创意取向" onChange={(event) => onCreativeStyleChange(event.target.value)} value={creativeStyle}><option value="balanced">均衡完整</option><option value="plot">剧情优先</option><option value="character">人物优先</option></select></label>
          </div>
          <p className="workflow-ai-mode-desc">ℹ {generationMode === "replace" ? "重写新方案：丢弃当前所有配置，基于提示词从头生成全新方案" : generationMode === "fill_blanks" ? "只补空白：保留已填写字段，并以其为边界补全。" : "生成变体：保留题材与核心设定，换一个可比较的表达角度。"}</p>
          {hasVariantConflict && <p className="workflow-ai-warning" role="status">⚠ 生成变体会保留题材与核心设定；若要彻底更换题材，请切换到“重写新方案”。</p>}
          <div className="workflow-ai-grid">
            <label><span>新奇度</span><select aria-label="新奇度" onChange={(event) => onNoveltyChange(event.target.value)} value={novelty}><option value="fresh">新鲜独特</option><option value="safe">稳妥清晰</option><option value="bold">大胆冒险</option></select></label>
            <label><span>冲突密度</span><select aria-label="冲突密度" onChange={(event) => onConflictDensityChange(event.target.value)} value={conflictDensity}><option value="layered">多层压力</option><option value="light">轻量推进</option><option value="intense">高压转折</option></select></label>
            <label><span>情感浓度</span><select aria-label="情感浓度" onChange={(event) => onEmotionIntensityChange(event.target.value)} value={emotionIntensity}><option value="textured">细腻有层次</option><option value="reserved">克制留白</option><option value="strong">浓烈外显</option></select></label>
          </div>
          <label className="workflow-ai-free-brief"><span>自由创意侧重点</span><input aria-label="自由创意侧重点" onChange={(event) => onCreativeBriefChange(event.target.value)} placeholder="可选：输入你希望模型自由发挥的创意方向，例：更民国海派 / 更宿命感 / 避免霸总套路" value={creativeBrief} /></label>
          <strong className="workflow-ai-parameters">可选硬参数（留“由 AI 决定”则全部交给 AI）：</strong>
          <div className={`workflow-ai-grid is-two is-hard-constraints ${mode === "short" ? "is-short" : ""}`}>
            <label><span>题材</span><input aria-label="题材硬参数" onChange={(event) => onGenreChange(event.target.value)} placeholder="留空由 AI 决定，例：言情、悬疑言情、科幻" value={genre} /></label>
            <label><span>基调</span><input aria-label="基调硬参数" onChange={(event) => onToneChange(event.target.value)} placeholder="留空由 AI 决定，例：温暖、悬疑、幽默、阴郁" value={tone} /></label>
            {mode === "short" && <label><span>目标字数</span><input aria-label="目标字数硬参数" min="500" onChange={(event) => onLengthTargetChange(event.target.value)} placeholder="由 AI 决定" type="number" value={lengthTarget} /></label>}
            {mode === "long" && <><label><span>总章节数</span><input aria-label="总章节数硬参数" min="1" onChange={(event) => onTotalChaptersChange(event.target.value)} placeholder="由 AI 决定" type="number" value={totalChapters} /></label><label><span>每章字数</span><input aria-label="每章字数硬参数" min="500" onChange={(event) => onWordsPerChapterChange(event.target.value)} placeholder="由 AI 决定" type="number" value={wordsPerChapter} /></label></>}
          </div>
          <p className="workflow-preset-helper">AI 将基于提示和创意控制生成完整配置，包括主题、人物、世界观、冲突等所有要素。<br />同时会生成一组可复用的润色灵感，供「AI 润色」一键调用。<br />生成完成后会先显示字段变更预览，确认应用后会自动保存为预设并写入表单。</p>
        </> : <>
          <section>
            <header><h3>润色灵感（可多选）</h3><div><button onClick={() => onSelectedSuggestionsChange(new Set(suggestions))} type="button">全选灵感</button><button onClick={() => onSelectedSuggestionsChange(new Set())} type="button">清空灵感</button></div></header>
            <p>以下灵感来自最近一次“AI 随机生成”，可直接勾选后用于本轮润色。</p>
            {suggestions.length > 0 ? <div className="workflow-check-grid">{suggestions.map((suggestion) => <label key={suggestion}><input checked={selectedSuggestions.has(suggestion)} onChange={() => toggleSuggestion(suggestion)} type="checkbox" />{suggestion}</label>)}</div> : <p className="workflow-polish-suggestions-empty">暂无可复用灵感。先完成一次 AI 生成，或直接填写上方润色方向。</p>}
          </section>
          <section>
            <header><h3>重点润色字段（可多选）</h3><div><button onClick={() => fields.forEach((field) => { if (!selectedFocus.has(field.key)) toggleFocus(field.key); })} type="button">全选字段</button><button onClick={() => [...selectedFocus].forEach(toggleFocus)} type="button">清空字段</button></div></header>
            <p>默认已勾选标题、核心梗概、人物、世界观、冲突与叙事常用字段。</p>
            <div className="workflow-check-grid">{fields.map((field) => <label key={field.key}><input checked={selectedFocus.has(field.key)} onChange={() => toggleFocus(field.key)} type="checkbox" />{field.label}</label>)}</div>
          </section>
        </>}
      </div>
      <footer className="workflow-preset-dialog-footer is-ai">
        {isGenerate && <button className="button button-secondary" onClick={resetForRandomTheme} title="清空所有输入与参数，切换到「重写新方案」模式，完全交给 AI 自由发挥。" type="button">🎲 随机新题材</button>}
        <span />
        <button className="button button-secondary" onClick={onClose} type="button">取消</button>
        <button className="button button-primary" disabled={!isGenerate && selectedFocus.size === 0} onClick={onStart} type="button">{isGenerate ? "生成并预览" : "开始润色"}</button>
      </footer>
    </DialogFrame>
  );
}

function WorkflowAiDiffDialog({
  diffs,
  message,
  onApply,
  onClose,
  operation,
}: {
  readonly diffs: readonly WorkflowAiDiff[];
  readonly message: string;
  readonly onApply: (selectedIds: ReadonlySet<string>) => void;
  readonly onClose: () => void;
  readonly operation: "generate" | "polish";
}) {
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => allDiffIds(diffs));
  const [activeId, setActiveId] = useState(diffs[0]?.id ?? "");
  const activeDiff = diffs.find((diff) => diff.id === activeId) ?? diffs[0];
  if (activeDiff === undefined) return null;

  return (
    <DialogFrame
      className="is-diff"
      label={operation === "generate" ? "AI 生成变更预览" : "AI 润色变更预览"}
      onClose={onClose}
    >
      <header className="workflow-source-dialog-intro">
        <h2>{operation === "generate" ? `AI 生成了 ${diffs.length} 个字段变更` : `AI 润色修改了 ${diffs.length} 个字段`}</h2>
        <p>{message} 未勾选字段会保留当前值。</p>
      </header>
      <div className="workflow-diff-body">
        <aside>
          <header>
            <strong>已选择 {selectedIds.size}/{diffs.length} 项</strong>
            <div>
              <button onClick={() => setSelectedIds(allDiffIds(diffs))} type="button">全选</button>
              <button onClick={() => setSelectedIds(new Set())} type="button">清空</button>
              <button onClick={() => setSelectedIds(new Set(diffs.filter((diff) => !selectedIds.has(diff.id)).map((diff) => diff.id)))} type="button">反选</button>
            </div>
          </header>
          <div>
            {diffs.map((diff) => (
              <button
                className={diff.id === activeDiff.id ? "is-active" : ""}
                key={diff.id}
                onClick={() => setActiveId(diff.id)}
                type="button"
              >
                <input
                  aria-label={`选择变更：${diff.label}`}
                  checked={selectedIds.has(diff.id)}
                  onChange={() => setSelectedIds((current) => nextSelectedIds(current, diff.id))}
                  onClick={(event) => event.stopPropagation()}
                  type="checkbox"
                />
                <span><strong>{diff.label}</strong><small>{diff.key}</small></span>
              </button>
            ))}
          </div>
        </aside>
        <article>
          <h3>{activeDiff.label}</h3>
          <div className="workflow-diff-detail">
            <p>最终应用范围以左侧勾选为准。</p>
            <section><h4>原内容</h4><div className="is-before">{activeDiff.oldValue}</div></section>
            <section><h4>{operation === "generate" ? "生成后" : "润色后"}</h4><div className="is-after">{activeDiff.newValue}</div></section>
            <section><h4>行内对比</h4><div aria-label="行内对比" className="workflow-inline-diff"><WorkflowInlineDiff after={activeDiff.newValue} before={activeDiff.oldValue} /></div></section>
          </div>
        </article>
      </div>
      <footer className="workflow-preset-dialog-footer">
        <button className="button button-secondary" onClick={onClose} type="button">全部拒绝</button>
        <button className="button button-primary" disabled={selectedIds.size === 0} onClick={() => onApply(selectedIds)} type="button">应用 {selectedIds.size} 项变更</button>
      </footer>
    </DialogFrame>
  );
}

export function WorkflowAiAssistant({
  available,
  className,
  commandClient,
  disabled = false,
  fields,
  mode,
  onApply,
  onNotice,
  payload,
}: WorkflowAiAssistantProps) {
  const [dialog, setDialog] = useState<WorkflowAiDialog>(null);
  const [hint, setHint] = useState("");
  const [generationMode, setGenerationMode] = useState<"replace" | "fill_blanks" | "variant">("replace");
  const [creativeStyle, setCreativeStyle] = useState("balanced");
  const [novelty, setNovelty] = useState("fresh");
  const [conflictDensity, setConflictDensity] = useState("layered");
  const [emotionIntensity, setEmotionIntensity] = useState("textured");
  const [creativeBrief, setCreativeBrief] = useState("");
  const [genre, setGenre] = useState("");
  const [tone, setTone] = useState("");
  const [lengthTarget, setLengthTarget] = useState("");
  const [totalChapters, setTotalChapters] = useState("");
  const [wordsPerChapter, setWordsPerChapter] = useState("");
  const [selectedFocus, setSelectedFocus] = useState<ReadonlySet<string>>(
    () => new Set(fields.map((field) => field.key)),
  );
  const [suggestions, setSuggestions] = useState<readonly string[]>([]);
  const [selectedSuggestions, setSelectedSuggestions] = useState<ReadonlySet<string>>(() => new Set());
  const previewDiffs = dialog?.type === "preview" ? dialog.diffs : [];
  const diffById = useMemo(() => new Map(previewDiffs.map((diff) => [diff.id, diff])), [previewDiffs]);

  const open = (operation: "generate" | "polish") => {
    if (!available) {
      onNotice?.("当前 Engine 未开放 AI 创作字段生成能力。");
      return;
    }
    setDialog({ type: "input", operation });
  };

  const toggleFocus = (key: string) => setSelectedFocus((current) => nextSelectedIds(current, key));

  const start = async (operation: "generate" | "polish") => {
    setDialog({ type: "progress", operation });
    try {
      const creativeProfile = {
        style: creativeStyle,
        novelty,
        conflict: conflictDensity,
        emotion: emotionIntensity,
        custom_brief: creativeBrief.trim(),
      };
      const hardConstraints = nonEmptyConstraints({
        genre,
        tone,
        length_target: lengthTarget,
        total_chapters: totalChapters,
        words_per_chapter: wordsPerChapter,
      });
      const command: GenerateWorkflowFieldsCommand = {
        kind: "generate_workflow_fields",
        mode,
        operation,
        currentPayload: payload,
        userHint: hint,
        generationMode,
        creativeProfile,
        hardConstraints,
        selectedSuggestions: [...selectedSuggestions],
        focusFields: [...selectedFocus],
      };
      const result = await commandClient.generateWorkflowFields(command);
      if (result.status !== "generated") throw new Error(result.message);
      const diffs = buildDiffs(fields, payload, result.payload);
      if (diffs.length === 0) {
        setDialog({
          type: "error",
          operation,
          message: "模型已返回结果，但可编辑字段没有产生变化。可调整提示或重点字段后重试。",
        });
        return;
      }
      if (result.suggestions.length > 0) setSuggestions(result.suggestions);
      setDialog({ type: "preview", operation, diffs, creativeNote: result.creativeNote, message: result.message });
    } catch (error) {
      setDialog({
        type: "error",
        operation,
        message: error instanceof Error ? error.message : "AI 创作请求失败，当前表单未被修改。",
      });
    }
  };

  const apply = (
    operation: "generate" | "polish",
    selectedIds: ReadonlySet<string>,
    creativeNote: Readonly<Record<string, unknown>> | undefined,
  ) => {
    const selectedDiffs = [...selectedIds]
      .map((id) => diffById.get(id))
      .filter((diff): diff is WorkflowAiDiff => diff !== undefined);
    const patch = Object.fromEntries(selectedDiffs.map((diff) => [diff.key, diff.nextValue]));
    onApply(operation, patch, selectedDiffs.map((diff) => diff.key), {
      creativeNote,
      creativeProfile: {
        style: creativeStyle,
        novelty,
        conflict: conflictDensity,
        emotion: emotionIntensity,
        custom_brief: creativeBrief.trim(),
      },
      focusFields: [...selectedFocus],
      generationMode,
      hardConstraints: nonEmptyConstraints({ genre, tone, length_target: lengthTarget, total_chapters: totalChapters, words_per_chapter: wordsPerChapter }),
      selectedSuggestions: [...selectedSuggestions],
      userHint: hint.trim(),
    });
    onNotice?.(`已应用 ${selectedDiffs.length} 项 AI ${operation === "generate" ? "生成" : "润色"}变更；草稿将自动保存。`);
    setDialog(null);
  };

  const unavailableTitle = available ? undefined : "当前 Engine 未提供 AI 创作字段生成能力";
  return (
    <>
      <div className={className}>
        <button disabled={disabled || !available} onClick={() => open("generate")} title={unavailableTitle} type="button">AI 生成并预览</button>
        <button disabled={disabled || !available} onClick={() => open("polish")} title={unavailableTitle} type="button">AI 定向润色</button>
      </div>
      {dialog?.type === "input" && (
        <WorkflowAiInputDialog
          creativeBrief={creativeBrief}
          creativeStyle={creativeStyle}
          conflictDensity={conflictDensity}
          emotionIntensity={emotionIntensity}
          fields={fields}
          generationMode={generationMode}
          genre={genre}
          hint={hint}
          lengthTarget={lengthTarget}
          mode={mode}
          novelty={novelty}
          onClose={() => setDialog(null)}
          onCreativeBriefChange={setCreativeBrief}
          onCreativeStyleChange={setCreativeStyle}
          onConflictDensityChange={setConflictDensity}
          onEmotionIntensityChange={setEmotionIntensity}
          onGenerationModeChange={setGenerationMode}
          onGenreChange={setGenre}
          onHintChange={setHint}
          onLengthTargetChange={setLengthTarget}
          onNoveltyChange={setNovelty}
          onSelectedSuggestionsChange={setSelectedSuggestions}
          onStart={() => void start(dialog.operation)}
          onToneChange={setTone}
          onTotalChaptersChange={setTotalChapters}
          onWordsPerChapterChange={setWordsPerChapter}
          operation={dialog.operation}
          selectedSuggestions={selectedSuggestions}
          selectedFocus={selectedFocus}
          suggestions={suggestions}
          tone={tone}
          totalChapters={totalChapters}
          toggleFocus={toggleFocus}
          wordsPerChapter={wordsPerChapter}
        />
      )}
      {dialog?.type === "progress" && (
        <DialogFrame
          className="is-ai-progress"
          label={dialog.operation === "generate" ? "AI 正在构思" : "AI 正在润色"}
          onClose={() => undefined}
        >
          <div className="workflow-ai-progress">
            <p>{dialog.operation === "generate" ? "AI 正在生成创作候选，请稍候。" : "AI 正在按选定方向整理字段变更，请稍候。"}</p>
            <span aria-label="处理中" />
          </div>
        </DialogFrame>
      )}
      {dialog?.type === "error" && (
        <DialogFrame className="is-ai-failure" label="AI 创作失败" onClose={() => setDialog(null)}>
          <section className="workflow-ai-failure">
            <h2>本次请求没有修改表单</h2>
            <p>{dialog.message}</p>
            <footer><button className="button button-primary" onClick={() => setDialog({ type: "input", operation: dialog.operation })} type="button">调整后重试</button></footer>
          </section>
        </DialogFrame>
      )}
      {dialog?.type === "preview" && (
        <WorkflowAiDiffDialog
          diffs={dialog.diffs}
          message={dialog.message}
          onApply={(selectedIds) => apply(dialog.operation, selectedIds, dialog.creativeNote)}
          onClose={() => setDialog(null)}
          operation={dialog.operation}
        />
      )}
    </>
  );
}

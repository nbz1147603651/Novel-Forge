import type { TaskStreamValidationStatus } from "@nimo/engine-contracts";

import {
  detectStreamRenderKind,
  extractJsonBody,
  textToParagraphs,
  tokenizeJson,
  truncateJsonStrings,
  STREAM_RENDER_KIND_LABEL,
  type StreamRenderKind,
} from "../lib/stream-render-kind";

export type StreamContentDensity = "reader" | "preview";

interface StreamContentProps {
  readonly text: string;
  readonly density?: StreamContentDensity;
  /** True only after the Engine has closed the current model stream. */
  readonly settled?: boolean;
  /** Adds a restrained caret to the latest prose fragment while streaming. */
  readonly live?: boolean;
  /**
   * Engine-declared output contract (text | json | report). When present it
   * selects the renderer directly; otherwise the kind is inferred from the
   * partial text (which can misjudge incomplete JSON fragments).
   */
  readonly outputKind?: string | undefined;
  /** Backend validation verdict for this exact logical stream attempt. */
  readonly validationStatus?: TaskStreamValidationStatus;
  /** Full backend-reported length when the retained snapshot is compacted. */
  readonly textLength?: number;
  /** True when `text` is only the bounded observation preview. */
  readonly textTruncated?: boolean;
  /** Open the retained source immediately when a settled JSON payload is invalid. */
  readonly revealInvalidSource?: boolean;
}

const CLIP_PREVIEW_LIMIT = 2_000;
const CLIP_COMPACT_LIMIT = 600;
const LIVE_READER_LIMIT = 8_000;

function renderKindFromOutputKind(
  outputKind: string | undefined,
  text: string,
): StreamRenderKind | null {
  if (outputKind === undefined || outputKind === "") return null;
  const normalized = outputKind.trim().toLowerCase();
  if (normalized === "text" || normalized === "文本") return "text";
  if (normalized === "json") {
    // The engine declares the *contract*, not the arrival state: a mid-stream
    // fragment is still incomplete JSON and must stay readable as source text
    // (the tokenizer over partial strings drops unmatched characters).
    try {
      JSON.parse(extractJsonBody(text));
      return "json";
    } catch {
      return "json_partial";
    }
  }
  if (normalized === "json_partial" || normalized === "json-partial") return "json_partial";
  if (normalized === "report") return "report";
  return null;
}

function clipStreamText(text: string, limit: number): [string, boolean] {
  if (text.length <= limit) return [text, false];
  return [text.slice(0, limit).trimEnd(), true];
}

function JsonSource({ text }: { readonly text: string }) {
  const tokens = tokenizeJson(text);
  if (tokens.length === 0) return <code>{text}</code>;
  return (
    <code>{tokens.map((token, index) =>
      token.type === "whitespace"
        ? token.value
        : <span className={`json-tok-${token.type}`} key={index}>{token.value}</span>,
    )}</code>
  );
}

function jsonShape(value: unknown): string {
  if (Array.isArray(value)) return `数组 · ${value.length} 项`;
  if (value !== null && typeof value === "object") return `对象 · ${Object.keys(value).length} 个字段`;
  return typeof value;
}

function scalarPreview(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") {
    const characters = [...value];
    return characters.length > 120 ? `${characters.slice(0, 120).join("")}…` : value;
  }
  return String(value);
}

function countCodePoints(text: string): number {
  let count = 0;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff && index + 1 < text.length) {
      const next = text.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) index += 1;
    }
    count += 1;
  }
  return count;
}

function tailCodePoints(text: string, limit: number): string {
  let index = text.length;
  let count = 0;
  while (index > 0 && count < limit) {
    index -= 1;
    const code = text.charCodeAt(index);
    if (code >= 0xdc00 && code <= 0xdfff && index > 0) {
      const previous = text.charCodeAt(index - 1);
      if (previous >= 0xd800 && previous <= 0xdbff) index -= 1;
    }
    count += 1;
  }
  return text.slice(index);
}

function liveStreamPreview(
  text: string,
  density: StreamContentDensity,
  characterCount: number,
): {
  readonly source: string;
  readonly omittedCharacters: number;
} {
  const limit = density === "preview" ? CLIP_COMPACT_LIMIT : LIVE_READER_LIMIT;
  if (characterCount <= limit) return { source: text, omittedCharacters: 0 };

  const omittedCharacters = characterCount - limit;
  return {
    source: tailCodePoints(text, limit),
    omittedCharacters,
  };
}

function StructuredOutputPending({
  text,
  density,
  live,
  validationStatus,
}: {
  readonly text: string;
  readonly density: StreamContentDensity;
  readonly live: boolean;
  readonly validationStatus?: TaskStreamValidationStatus;
}) {
  const characterCount = countCodePoints(text);
  const { source, omittedCharacters } = liveStreamPreview(text, density, characterCount);
  const state = validationStatus === "validating"
    ? {
      label: "结构化结果校验中",
      detail: "输出已接收，正在执行字段与语义校验；通过前不会作为正式结果。",
    }
    : validationStatus === "repairing"
      ? {
        label: "结构化结果修复中",
        detail: "后端正在修正可恢复的格式问题；当前片段仍不是正式结果。",
      }
      : validationStatus === "retrying"
        ? {
          label: "结构化结果重试中",
          detail: "本次输出未通过校验，后端正在开始新尝试；当前片段仅作诊断证据。",
        }
        : {
          label: "结构化结果生成中",
          detail: "未校验草稿，完成校验后展示字段。草稿不会写入正式结果。",
        };
  return (
    <section aria-label={state.label} className={`stream-structured-state is-pending is-${density}`}>
      <header className="stream-structured-pending-header">
        <span aria-hidden="true" className="stream-structured-mark">&#123; &#125;</span>
        <div className="stream-structured-pending-copy">
          <div className="stream-structured-pending-title">
            <strong>{state.label}</strong>
            <span>本段 {new Intl.NumberFormat("zh-CN").format(characterCount)} 字符</span>
            {omittedCharacters > 0 && <span>显示最新 {new Intl.NumberFormat("zh-CN").format(characterCount - omittedCharacters)} 字符，已省略前 {new Intl.NumberFormat("zh-CN").format(omittedCharacters)} 字符</span>}
          </div>
          <p>{state.detail}</p>
        </div>
        <span aria-hidden="true" className="stream-structured-pulse" />
      </header>
      <pre aria-label="未校验 JSON 草稿" className="stream-json-pre stream-structured-pending-source"><code>{source}</code>{live && <span aria-hidden="true" className="stream-live-caret" />}</pre>
    </section>
  );
}

function StructuredOutputVerifiedSnapshot({
  density,
  text,
  textLength,
}: {
  readonly density: StreamContentDensity;
  readonly text: string;
  readonly textLength: number;
}) {
  const previewCharacters = countCodePoints(text);
  return (
    <section aria-label="已校验结构化结果快照" className={`stream-structured-state is-verified-snapshot is-${density}`}>
      <header className="stream-structured-pending-header">
        <span aria-hidden="true" className="stream-structured-mark">&#123; &#10003; &#125;</span>
        <div className="stream-structured-pending-copy">
          <div className="stream-structured-pending-title">
            <strong>结构化结果已校验</strong>
            <span>完整长度 {new Intl.NumberFormat("zh-CN").format(textLength)} 字符</span>
            <span>当前预览 {new Intl.NumberFormat("zh-CN").format(previewCharacters)} 字符</span>
          </div>
          <p>后端校验已通过；当前仅显示截取快照，不能用该预览判断 JSON 完整性。</p>
        </div>
        <span className="stream-json-result-status">已校验</span>
      </header>
      <details className="stream-json-source-fold" open={density === "reader" || undefined}>
        <summary>查看截取预览</summary>
        <pre className="stream-json-pre stream-structured-pending-source"><code>{text}</code></pre>
      </details>
    </section>
  );
}

function StructuredOutputUnverified({
  revealSource,
  text,
  validationStatus,
}: {
  readonly revealSource: boolean;
  readonly text: string;
  readonly validationStatus?: TaskStreamValidationStatus;
}) {
  const [source] = clipStreamText(text, CLIP_PREVIEW_LIMIT);
  const failed = validationStatus === "failed";
  return (
    <div aria-label={failed ? "结构化结果未通过校验" : "结构化结果待核验"} className="stream-structured-state is-invalid">
      <span aria-hidden="true" className="stream-structured-mark">&#123; ! &#125;</span>
      <div>
        <strong>{failed ? "结构化结果未通过校验" : "结构化结果待核验"}</strong>
        <p>{failed
          ? "后端已记录校验失败；该片段仅作诊断证据，不会作为正式结果。"
          : "输出已停止更新，但尚无与该片段对应的后端校验结论。"}</p>
        <details className="stream-json-source-fold" open={revealSource || undefined}>
          <summary>查看输出片段</summary>
          <pre className="stream-json-pre"><code>{source}</code></pre>
        </details>
      </div>
    </div>
  );
}

function StructuredOutputInvalid({
  revealSource,
  text,
}: {
  readonly revealSource: boolean;
  readonly text: string;
}) {
  const [source] = clipStreamText(text, CLIP_PREVIEW_LIMIT);
  return (
    <div aria-label="结构化结果待校验" className="stream-structured-state is-invalid">
      <span aria-hidden="true" className="stream-structured-mark">&#123; ! &#125;</span>
      <div>
        <strong>结构化结果尚未通过校验</strong>
        <p>输出已经结束，但 JSON 仍不完整；修复完成前不会把片段当成正式结果。</p>
        <details className="stream-json-source-fold" open={revealSource || undefined}>
          <summary>查看原始片段</summary>
          <pre className="stream-json-pre"><code>{source}</code></pre>
        </details>
      </div>
    </div>
  );
}

function StructuredJsonResult({ density, source, value }: { readonly density: StreamContentDensity; readonly source: string; readonly value: unknown }) {
  const entries: readonly [string, unknown][] = Array.isArray(value)
    ? value.map((item, index) => [`第 ${index + 1} 项`, item])
    : value !== null && typeof value === "object"
      ? Object.entries(value)
      : [["值", value]];
  const entryLimit = density === "preview" ? 8 : 24;
  const visibleEntries = entries.slice(0, entryLimit);

  return (
    <section aria-label="结构化结果" className={`stream-json-result is-${density}`}>
      <header className="stream-json-result-header">
        <span aria-hidden="true" className="stream-json-result-mark">&#123; &#125;</span>
        <div>
          <strong>结构化结果</strong>
          <small>{jsonShape(value)}</small>
        </div>
        <span className="stream-json-result-status">已校验</span>
      </header>
      <div className="stream-json-field-list">
        {visibleEntries.map(([key, fieldValue]) => {
          const nested = fieldValue !== null && typeof fieldValue === "object";
          if (!nested) {
            return (
              <div className="stream-json-field is-scalar" key={key}>
                <span>{key}</span>
                <strong>{scalarPreview(fieldValue)}</strong>
              </div>
            );
          }
          const nestedText = JSON.stringify(fieldValue, null, 2);
          const [nestedPreview, nestedClipped] = clipStreamText(nestedText, density === "preview" ? 500 : 1_800);
          return (
            <details className="stream-json-field is-nested" key={key}>
              <summary><span>{key}</span><strong>{jsonShape(fieldValue)}</strong></summary>
              <pre className="stream-json-field-preview"><JsonSource text={truncateJsonStrings(nestedPreview)} /></pre>
              {nestedClipped && <p className="stream-clip-notice">内容较长，完整内容请查看 JSON 源码。</p>}
            </details>
          );
        })}
      </div>
      {entries.length > visibleEntries.length && (
        <p className="stream-json-more">另有 {entries.length - visibleEntries.length} 个字段未在概览中展开。</p>
      )}
      {density === "reader" && (
        <details className="stream-json-source-fold">
          <summary>查看完整 JSON 源码</summary>
          <pre className="stream-json-pre"><JsonSource text={source} /></pre>
        </details>
      )}
    </section>
  );
}

/**
 * Shared, lossless body renderer for every task-stream reader.
 *
 * The engine deliberately emits incomplete JSON while a structured response is
 * still arriving.  Those fragments must stay readable as source text: running
 * a tokenizer over incomplete strings used to drop unmatched characters and
 * leave an empty card behind.
 */
export function StreamContent({
  density = "reader",
  live = false,
  outputKind,
  revealInvalidSource = false,
  settled = true,
  text,
  textLength,
  textTruncated = false,
  validationStatus,
}: StreamContentProps) {
  const trimmed = text.trim();
  if (trimmed.length === 0) return null;

  const kind = renderKindFromOutputKind(outputKind, text) ?? detectStreamRenderKind(text);
  const structured = kind === "json" || kind === "json_partial" || kind === "report";
  const previewLimit = density === "preview" ? CLIP_COMPACT_LIMIT : CLIP_PREVIEW_LIMIT;
  const [clipped, wasTruncated] = clipStreamText(text, previewLimit);

  if (kind === "text") {
    const livePreview = live && !settled
      ? liveStreamPreview(text, density, countCodePoints(text))
      : null;
    const paragraphs = textToParagraphs(livePreview?.source ?? clipped);
    if (paragraphs.length === 0) return null;
    return (
      <div className={`stream-content-prose is-${density}${live ? " is-live" : ""}`}>
        {paragraphs.map((line, index) =>
          line === null
            ? <div aria-hidden="true" className="stream-prose-blank" key={`blank-${index}`} />
            : <p key={`p-${index}`}>{line}</p>,
        )}
        {live && <span aria-hidden="true" className="stream-live-caret" />}
        {livePreview !== null
          ? livePreview.omittedCharacters > 0 && <p className="stream-clip-notice">正在显示最新输出，已省略前 {new Intl.NumberFormat("zh-CN").format(livePreview.omittedCharacters)} 个字符。</p>
          : wasTruncated && <p className="stream-clip-notice">…已截断，仅显示前部预览。</p>}
      </div>
    );
  }

  if (structured && validationStatus === "validated" && textTruncated) {
    return <StructuredOutputVerifiedSnapshot
      density={density}
      text={text}
      textLength={Math.max(textLength ?? 0, countCodePoints(text))}
    />;
  }

  if (structured && !settled) {
    return <StructuredOutputPending
      density={density}
      live={live}
      text={text}
      {...(validationStatus === undefined ? {} : { validationStatus })}
    />;
  }

  if (structured && validationStatus !== "validated") {
    return <StructuredOutputUnverified
      revealSource={revealInvalidSource}
      text={text}
      {...(validationStatus === undefined ? {} : { validationStatus })}
    />;
  }

  if (kind === "json" || kind === "json_partial") {
    const jsonBody = extractJsonBody(text);
    try {
      return <StructuredJsonResult density={density} source={jsonBody} value={JSON.parse(jsonBody) as unknown} />;
    } catch {
      return <StructuredOutputInvalid revealSource={revealInvalidSource} text={text} />;
    }
  }

  const jsonBody = extractJsonBody(text);
  let report: Record<string, unknown> | null = null;
  try { report = JSON.parse(jsonBody) as Record<string, unknown>; } catch { /* retained below as source */ }
  if (report === null) return settled
    ? <StructuredOutputInvalid revealSource={revealInvalidSource} text={text} />
    : <StructuredOutputPending density={density} live={live} text={text} />;
  const scoreKeys = ["overall_score", "alignment_score", "continuity_score", "causal_score", "humanize_score"];
  const scores = scoreKeys
    .filter((key) => typeof report[key] === "number")
    .map((key) => ({ label: key.replace(/_/g, " "), value: report[key] as number }));
  return (
    <div className="stream-content-report">
      <span className="stream-json-kind-badge">{STREAM_RENDER_KIND_LABEL.report}</span>
      {scores.length > 0 && (
        <div className="stream-report-scores">
          {scores.map((score) => (
            <span className="stream-report-score" key={score.label}>
              <strong>{score.value.toFixed(1)}</strong>
              <small>{score.label}</small>
            </span>
          ))}
        </div>
      )}
      <details className="stream-json-source-fold">
        <summary>查看完整报告数据</summary>
        <pre className="stream-json-pre"><JsonSource text={JSON.stringify(report, null, 2)} /></pre>
      </details>
    </div>
  );
}

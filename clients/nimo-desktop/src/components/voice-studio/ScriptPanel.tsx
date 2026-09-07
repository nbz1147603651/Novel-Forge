import type {
  EngineCommandClient,
  VoiceStudioView,
} from "@nimo/engine-contracts";

import type { VoiceStudioTabId } from "../../lib/ui-session";
import { DropdownSelect } from "../DropdownSelect";
import { voiceLineageState } from "../../lib/voice-lineage";

export type VoiceScriptDialog =
  | "script"
  | "speaker-review";

interface ScriptPanelProps {
  readonly activeScriptProgressDetail: string;
  readonly activeScriptProgressPercent: number | undefined;
  readonly activeScriptProgressStep: string;
  readonly activeSegment: VoiceStudioView["script"][number];
  readonly changeChapter: (chapter: number) => Promise<void>;
  readonly commandClient: EngineCommandClient;
  readonly dialogueSegmentCount: number;
  readonly generateScript: () => void;
  readonly hasScriptSegments: boolean;
  readonly narrationSegmentCount: number;
  readonly onAnalyzeReferenceStyle: () => void;
  readonly onActiveTab: (tab: VoiceStudioTabId) => void;
  readonly onDialog: (dialog: VoiceScriptDialog) => void;
  readonly onGuidanceNotice: (message: string) => void;
  readonly onPostWorkspaceTab: (tab: "assembly" | "library") => void;
  readonly onRefresh: () => Promise<void>;
  readonly onReferenceScriptFile: (file: File | null) => Promise<void>;
  readonly onReferenceStyleStrength: (strength: number) => void;
  readonly onScriptSegmentsChange: (segments: readonly VoiceStudioView["script"][number][]) => void;
  readonly onScriptSpeakerFilter: (filter: string | null) => void;
  readonly onSetActiveSegmentId: (segmentId: string) => void;
  readonly scriptListRef: React.RefObject<HTMLDivElement | null>;
  readonly scriptSegments: readonly VoiceStudioView["script"][number][];
  readonly scriptSpeakerFilter: string | null;
  readonly scriptSpeakerLabels: readonly string[];
  readonly scriptState: string;
  readonly scriptTaskIsActive: boolean;
  readonly selectedChapter: number;
  readonly referenceScriptName: string;
  readonly referenceStyleState: "idle" | "loaded" | "analyzing" | "ready" | "error";
  readonly referenceStyleStrength: number;
  readonly referenceStyleSummary: string;
  readonly streamingAnomaly: { readonly hasAnomalies: boolean } | null;
  readonly streamingScriptText: string;
  readonly streamingSegments: readonly { readonly speaker: string; readonly emotion?: string; readonly text: string }[];
  readonly studio: VoiceStudioView;
  readonly synthesize: () => void;
  readonly synthesizingSegmentIndex: number;
  readonly unresolvedSpeakerCount: number;
  readonly visibleScriptSegments: readonly VoiceStudioView["script"][number][];
}

/**
 * 声腔「脚本」页签：工具栏 / 版本状态 / 角色筛选 / 说话人复核 / 流式生成 / 脚本文档。
 * 从 VoiceStudioPage 提取，全部依赖通过 props 注入。
 */
export function ScriptPanel({
  activeScriptProgressDetail,
  activeScriptProgressPercent,
  activeScriptProgressStep,
  activeSegment,
  changeChapter,
  commandClient,
  dialogueSegmentCount,
  generateScript,
  hasScriptSegments,
  narrationSegmentCount,
  onAnalyzeReferenceStyle,
  onActiveTab,
  onDialog,
  onGuidanceNotice,
  onPostWorkspaceTab,
  onRefresh,
  onReferenceScriptFile,
  onReferenceStyleStrength,
  onScriptSegmentsChange,
  onScriptSpeakerFilter,
  onSetActiveSegmentId,
  scriptListRef,
  scriptSegments,
  scriptSpeakerFilter,
  scriptSpeakerLabels,
  scriptState,
  scriptTaskIsActive,
  selectedChapter,
  referenceScriptName,
  referenceStyleState,
  referenceStyleStrength,
  referenceStyleSummary,
  streamingAnomaly,
  streamingScriptText,
  streamingSegments,
  studio,
  synthesize,
  synthesizingSegmentIndex,
  unresolvedSpeakerCount,
  visibleScriptSegments,
}: ScriptPanelProps) {
  const lineage = voiceLineageState(studio);
  return (
    <div className="voice-script">
      {/* Row 1: toolbar — matches PySide6 top_bar */}
      <header className="voice-toolbar">
        <div className="voice-toolbar-actions">
          <label>
            <span>章节:</span>
            <DropdownSelect
              ariaLabel="配音脚本章节"
              onChange={(v) => void changeChapter(Number(v))}
              options={(studio.availableChapters.length > 0 ? studio.availableChapters : [studio.chapterNumber]).map((num) => ({ value: String(num), label: `第 ${num} 章` }))}
              value={String(selectedChapter)}
            />
          </label>
          <button
            className="button button-primary"
            onClick={generateScript}
            type="button"
          >
            {scriptState === "generating" ? "重新生成中…" : "重新生成"}
          </button>
          <button
            className="button button-secondary"
            disabled={!hasScriptSegments}
            onClick={() => onDialog("script")}
            title="逐段修改台词、情绪、语气与语速；保存后自动使旧音频待重建"
            type="button"
          >
            编辑
          </button>
          <button
            className="button button-secondary"
            disabled={!hasScriptSegments}
            onClick={() => {
              onPostWorkspaceTab("library");
              onActiveTab("post");
            }}
            title="管理环境音、剧情音效和 BGM 的片段锚点与混音素材"
            type="button"
          >
            声场设计
          </button>
          <button
            className="button button-secondary"
            disabled={!hasScriptSegments || !lineage.synthesisAllowed}
            onClick={synthesize}
            title={lineage.synthesisAllowed ? "合成当前版本脚本" : lineage.detail}
            type="button"
          >
            {scriptState === "synthesizing" ? "合成中…" : "合成"}
          </button>
        </div>
        <button
          className="button button-quiet voice-toolbar-clear"
          disabled={!hasScriptSegments}
          onClick={async () => {
            if (!window.confirm(`清理第 ${selectedChapter} 章源配音脚本？\n\n已合成的音频和字幕仍可播放，但再次合成前需要重新生成脚本。`)) return;
            const result = await commandClient.clearVoiceArtifacts({
              kind: "clear_voice_artifacts",
              projectId: studio.projectId,
              chapterNumber: selectedChapter,
              scope: "script_chapter",
            });
            onGuidanceNotice(result.message);
            if (result.status === "accepted") {
              onScriptSegmentsChange([]);
              await onRefresh();
            }
          }}
          title="清理当前章节可重新生成的源配音脚本"
          type="button"
        >
          清理
        </button>
      </header>
      {/* Row 2: freshness bar — matches PySide6 _script_freshness_bar */}
      <section className={`voice-script-freshness is-${lineage.tone}`} aria-live="polite">
        <strong>{lineage.title}</strong>
        <span>{lineage.detail}</span>
      </section>
      <section
        aria-label="参考配音脚本风格"
        className={`voice-reference-style is-${referenceStyleState}`}
      >
        <strong>风格模仿</strong>
        <label className="voice-reference-file">
          <span>{referenceScriptName || "选择参考配音脚本"}</span>
          <input
            accept=".txt,.md,.srt,.vtt,text/plain,text/markdown,text/vtt,application/x-subrip"
            aria-label="上传参考配音脚本"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0] ?? null;
              void onReferenceScriptFile(file);
              event.currentTarget.value = "";
            }}
            type="file"
          />
        </label>
        <label className="voice-reference-strength">
          <span>强度 {Math.round(referenceStyleStrength * 100)}%</span>
          <input
            aria-label="参考配音风格强度"
            max="100"
            min="0"
            onChange={(event) => onReferenceStyleStrength(Number(event.currentTarget.value) / 100)}
            step="5"
            type="range"
            value={Math.round(referenceStyleStrength * 100)}
          />
        </label>
        <button
          className="button button-secondary"
          disabled={!referenceScriptName || referenceStyleState === "analyzing" || scriptTaskIsActive}
          onClick={onAnalyzeReferenceStyle}
          type="button"
        >
          {referenceStyleState === "analyzing" ? "分析中…" : "分析并应用"}
        </button>
        {referenceScriptName && (
          <button
            aria-label="移除本次参考配音脚本"
            className="button button-quiet"
            onClick={() => void onReferenceScriptFile(null)}
            type="button"
          >
            移除
          </button>
        )}
        <small aria-live="polite">{referenceStyleSummary}</small>
      </section>
      {/* Row 4: character chips + metrics — matches PySide6 avatar_bar */}
      <section className="voice-script-context">
        <div className="voice-script-speakers" aria-label="脚本角色筛选">
          <span>角色:</span>
          <button
            className={scriptSpeakerFilter === null ? "is-active" : ""}
            onClick={() => onScriptSpeakerFilter(null)}
            type="button"
          >
            全部
          </button>
          {scriptSpeakerLabels.map((speakerLabel) => (
            <button
              className={scriptSpeakerFilter === speakerLabel ? "is-active" : ""}
              key={speakerLabel}
              onClick={() => onScriptSpeakerFilter(speakerLabel)}
              type="button"
            >
              {speakerLabel}
            </button>
          ))}
        </div>
        <div className="voice-script-metrics">
          <span>片段总数 {scriptSegments.length}</span>
          <span>旁白 {narrationSegmentCount}</span>
          <span>对白 {dialogueSegmentCount}</span>
          <span>场景声音 0</span>
        </div>
      </section>
      {/* Row 4: speaker review bar — matches PySide6 _speaker_review_bar */}
      <section className={`voice-speaker-review ${unresolvedSpeakerCount === 0 ? "is-ready" : ""}`}>
        <strong>{unresolvedSpeakerCount === 0 ? "说话人已确认" : `待确认 ${unresolvedSpeakerCount} 段`}</strong>
        <span>
          {unresolvedSpeakerCount === 0
            ? "本章说话人复核已完成，可以进入配音室。"
            : "请复核标记片段的说话人后再进入正式合成。"}
        </span>
        {unresolvedSpeakerCount > 0 && (
          <button
            className="button button-secondary"
            onClick={() => onDialog("speaker-review")}
            type="button"
          >
            开始复核
          </button>
        )}
      </section>
      {/* Row 5: generation progress — matches PySide6 _script_generation_bar.
          Always in DOM so grid rows stay aligned; collapses to 0 height when idle. */}
      <div className={`voice-progress-indicator ${scriptTaskIsActive ? "is-active" : "is-hidden"}`}>
        <progress
          aria-label={scriptState === "synthesizing" ? "音频合成进度" : "配音脚本生成进度"}
          aria-valuetext={activeScriptProgressDetail}
          className="voice-progress-bar"
          max={100}
          value={activeScriptProgressPercent}
        />
        <span>{activeScriptProgressStep}</span>
        <small>{activeScriptProgressDetail}</small>
      </div>
      {/* Row 6: script document — fills remaining space, matches PySide6 script_card */}
      <section className="voice-script-document">
        <div className="voice-script-document-heading">
          <h2>配音脚本</h2>
          <span>
            {scriptState === "generating"
              ? `流式生成中 · ${streamingSegments.length} 段已识别`
              : scriptState === "generated" ? "源脚本已更新 · 待审听" : "源脚本已就绪"}
            {scriptState === "generating" && streamingAnomaly?.hasAnomalies === true
              ? " · 检测到异常，终态以校验为准"
              : ""}
          </span>
        </div>
        {scriptState === "generating" ? (
          streamingSegments.length > 0 ? (
            <div className="voice-script-stream">
              {streamingSegments.map((seg, idx) => (
                <div className="voice-script-stream-segment" key={idx}>
                  <header>
                    <span>#{String(idx + 1).padStart(2, "0")}</span>
                    <span>{seg.speaker}</span>
                    {seg.emotion && <span>· {seg.emotion}</span>}
                  </header>
                  <p>{seg.text}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="voice-script-stream-placeholder">
              {streamingScriptText
                ? "正在解析模型输出…"
                : "正在分析正文与角色，请稍候…"}
            </div>
          )
        ) : hasScriptSegments ? (
          <div className="voice-script-list" ref={scriptListRef}>
            {visibleScriptSegments.map((segment) => {
              const isSynthesizing = scriptState === "synthesizing"
                && segment.segmentIndex === synthesizingSegmentIndex;
              const isCompleted = scriptState === "synthesizing"
                && synthesizingSegmentIndex >= 0
                && segment.segmentIndex < synthesizingSegmentIndex;
              const className = [
                activeSegment.id === segment.id ? "is-active" : "",
                isSynthesizing ? "is-synthesizing" : "",
                isCompleted ? "is-completed" : "",
              ].filter(Boolean).join(" ");
              return (
                <button
                  aria-label={`片段 ${segment.segmentIndex + 1} · ${segment.speakerLabel}`}
                  className={className}
                  data-segment-id={segment.id}
                  key={segment.id}
                  onClick={() => onSetActiveSegmentId(segment.id)}
                  type="button"
                >
                  <span>[{segment.speakerLabel}]</span>
                  <small>
                    {segment.kindLabel} · {segment.emotionLabel}
                  </small>
                  <p>{segment.content}</p>
                  <strong>编辑</strong>
                </button>
              );
            })}
          </div>
        ) : (
          <p className="voice-empty-result">
            当前章节还没有配音脚本。请先点击"生成脚本"。
          </p>
        )}
      </section>
    </div>
  );
}

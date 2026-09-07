import { useState } from "react";

import type {
  ChapterExportFormat,
  ExportScope,
} from "../VoiceDeliveryDialog";
import type { VoiceStudioView } from "@nimo/engine-contracts";

import { DropdownSelect } from "../DropdownSelect";
import { SoundLibraryPanel } from "./SoundLibraryPanel";
import type { VoiceScriptDraft } from "../../lib/voice-script-session";
import type { VoiceStudioTabId } from "../../lib/ui-session";
import { voiceLineageState } from "../../lib/voice-lineage";

export type VoicePostDialog =
  | "assemble"
  | "export"
  | "export-audiobook"
  | "cleanup"
  | "mix-manifest";

export type VoiceCleanupMode = "all" | "chapter" | "batch" | "expired" | "reset";

interface PostPanelProps {
  readonly changeChapter: (chapter: number) => Promise<void>;
  readonly chapterAudioRef: React.MutableRefObject<HTMLAudioElement | null>;
  readonly chapterDuration: number;
  readonly chapterPlaying: boolean;
  readonly chapterProgress: number;
  readonly chapterVolume: number;
  readonly commandClient: Parameters<typeof SoundLibraryPanel>[0]["commandClient"];
  readonly exportPreset: { readonly format: ChapterExportFormat; readonly includeSubtitles: boolean; readonly normalizeLoudness: boolean; readonly scope: ExportScope };
  readonly followReading: boolean;
  readonly hasScriptSegments: boolean;
  readonly onChapterVolume: (volume: number) => void;
  readonly onCleanupMode: (mode: VoiceCleanupMode) => void;
  readonly onDialog: (dialog: VoicePostDialog) => void;
  readonly onExportPreset: (preset: PostPanelProps["exportPreset"]) => void;
  readonly onFollowReading: (follow: boolean) => void;
  readonly onGuidanceNotice: (message: string) => void;
  readonly onPostDetailTab: (tab: "transcript" | "subtitle" | "mix") => void;
  readonly onPostWorkspaceTab: (tab: "assembly" | "library") => void;
  readonly onRefresh: () => Promise<void>;
  readonly onVoiceSettingsDraft: (patch: Readonly<Record<string, unknown>>) => void;
  readonly postDetailTab: "transcript" | "subtitle" | "mix";
  readonly postWorkspaceTab: "assembly" | "library";
  readonly runFullPipeline: (mode: "assisted" | "autonomous") => Promise<void>;
  readonly scriptSegments: readonly VoiceStudioView["script"][number][];
  readonly seekChapterAudio: (seconds: number) => void;
  readonly seekChapterBoundary: (boundary: "start" | "end") => void;
  readonly selectedChapter: number;
  readonly soundLibraryBusy: boolean;
  readonly studio: VoiceStudioView;
  readonly synthesize: () => void;
  readonly toggleChapterPlayback: () => Promise<void>;
  readonly voiceSettingsDraft: {
    readonly automationMode: "manual" | "assisted" | "autonomous";
    readonly soundReuse?: "off" | "project" | "application";
    readonly textRoutes?: unknown;
    readonly ttsScriptTemperature?: string | number;
    readonly ttsReviewTemperature?: string | number;
    readonly ttsNarratorTemperature?: string | number;
    readonly ttsSoundDesignTemperature?: string | number;
  };
}

/**
 * 声腔「后处理」页签：成品装配 / 声音资源库 / 播放与混音清单。
 * 从 VoiceStudioPage 提取，SoundLibraryPanel 作为既有共享组件复用。
 */
export function PostPanel({
  changeChapter,
  chapterAudioRef,
  chapterDuration,
  chapterPlaying,
  chapterProgress,
  chapterVolume,
  commandClient,
  exportPreset: _exportPreset,
  followReading,
  hasScriptSegments,
  onChapterVolume,
  onCleanupMode,
  onDialog,
  onExportPreset,
  onFollowReading,
  onGuidanceNotice,
  onPostDetailTab,
  onPostWorkspaceTab,
  onRefresh,
  onVoiceSettingsDraft,
  postDetailTab,
  postWorkspaceTab,
  runFullPipeline,
  scriptSegments,
  seekChapterAudio,
  seekChapterBoundary,
  selectedChapter,
  soundLibraryBusy,
  studio,
  synthesize,
  toggleChapterPlayback,
  voiceSettingsDraft,
}: PostPanelProps) {
  const [actionMenu, setActionMenu] = useState<"export" | "more" | null>(null);
  const lineage = voiceLineageState(studio);

  return (
    <div className={`voice-post${postWorkspaceTab === "library" ? " is-library" : ""}`}>
      <nav className="voice-post-subtabs" aria-label="后处理区域">
        <button
          className={postWorkspaceTab === "assembly" ? "is-active" : ""}
          onClick={() => onPostWorkspaceTab("assembly")}
          type="button"
        >
          成品装配
        </button>
        <button
          className={postWorkspaceTab === "library" ? "is-active" : ""}
          onClick={() => onPostWorkspaceTab("library")}
          title="切换到项目与应用声音资源库"
          type="button"
        >
          声音资源库（项目 / 应用）
        </button>
      </nav>
      {postWorkspaceTab === "library" ? (
        <SoundLibraryPanel
          assets={studio.soundAssets}
          busy={soundLibraryBusy}
          commandClient={commandClient}
          onNotice={onGuidanceNotice}
          onRefresh={onRefresh}
          projectId={studio.projectId}
        />
      ) : (
        <>
          <div className="voice-post-toolbar">
            <label>章节
              <DropdownSelect
                ariaLabel="后处理章节"
                onChange={(v) => void changeChapter(Number(v))}
                options={(studio.availableChapters.length > 0 ? studio.availableChapters : [studio.chapterNumber]).map((num) => ({ value: String(num), label: `第 ${num} 章` }))}
                value={String(selectedChapter)}
              />
            </label>
            <fieldset className="voice-post-mode">
              <legend>推进模式</legend>
              {([
                ["manual", "全人工"],
                ["assisted", "AI 伴随"],
                ["autonomous", "AI 自主"],
              ] as const).map(([mode, label]) => (
                <label key={mode}>
                  <input
                    checked={voiceSettingsDraft.automationMode === mode}
                    name="voice-post-mode"
                    onChange={() => onVoiceSettingsDraft({
                      automationMode: mode,
                    })}
                    type="radio"
                  />
                  {label}
                </label>
              ))}
            </fieldset>
            <button className="button button-primary" disabled={!lineage.synthesisAllowed} onClick={synthesize} title={lineage.synthesisAllowed ? "合成当前版本脚本" : lineage.detail} type="button">合成</button>
            <button className="button button-secondary" disabled={!lineage.synthesisAllowed} onClick={() => onDialog("assemble")} title={lineage.synthesisAllowed ? "重新装配当前版本音频" : lineage.detail} type="button">重装配</button>
            <button className="button button-primary" disabled={!lineage.sourceReady} onClick={() => void runFullPipeline("autonomous")} title={lineage.sourceReady ? "从当前小说终稿生成完整配音" : lineage.detail} type="button">AI 自主成片</button>
            <div className="voice-action-menu">
              <button
                aria-controls="voice-export-menu"
                aria-expanded={actionMenu === "export"}
                aria-haspopup="menu"
                className="button button-secondary"
                disabled={!lineage.deliveryAllowed}
                onClick={() => setActionMenu((current) => current === "export" ? null : "export")}
                title={lineage.deliveryAllowed ? "导出当前版本音频" : lineage.detail}
                type="button"
              >
                导出 ▾
              </button>
              {actionMenu === "export" ? (
                <div
                  aria-label="导出菜单"
                  className="voice-action-menu-popover"
                  id="voice-export-menu"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setActionMenu(null);
                  }}
                  role="menu"
                >
                {([
                  ["导出 MP3", "chapter", "mp3", false, false],
                  ["导出 WAV", "chapter", "wav", false, false],
                  ["导出 FLAC", "chapter", "flac", false, false],
                  ["导出 MP3（-14 LUFS）", "chapter", "mp3", false, true],
                  ["导出字幕", "chapter", "srt", false, false],
                  ["全书打包导出（MP3）", "book", "mp3", false, false],
                  ["全书打包导出（含字幕）", "book", "mp3", true, false],
                ] as const).map(([label, scope, format, includeSubtitles, normalizeLoudness]) => (
                  <button
                    key={label}
                    onClick={() => {
                      setActionMenu(null);
                      onExportPreset({ format, includeSubtitles, normalizeLoudness, scope });
                      onDialog("export");
                    }}
                    role="menuitem"
                    type="button"
                  >
                    {label}
                  </button>
                ))}
                <button
                  onClick={() => {
                    setActionMenu(null);
                    onDialog("export-audiobook");
                  }}
                  role="menuitem"
                  title="分章 MP3 + 目录/封面元数据 + 段级汇编报告（按章节顺序门控）"
                  type="button"
                >
                  导出有声书包
                </button>
                </div>
              ) : null}
            </div>
            <div className="voice-action-menu">
              <button
                aria-controls="voice-more-menu"
                aria-expanded={actionMenu === "more"}
                aria-haspopup="menu"
                className="button button-secondary"
                onClick={() => setActionMenu((current) => current === "more" ? null : "more")}
                type="button"
              >
                更多 ▾
              </button>
              {actionMenu === "more" ? (
                <div
                  aria-label="更多菜单"
                  className="voice-action-menu-popover"
                  id="voice-more-menu"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setActionMenu(null);
                  }}
                  role="menu"
                >
                {([
                  ["清理旧资产…", "all"],
                  ["清理当前章节产物", "chapter"],
                  ["批量清理章节", "batch"],
                  ["清理过期 TTS 文件…", "expired"],
                  ["重新开始：清空可再生配音产物…", "reset"],
                ] as const).map(([label, mode]) => (
                  <button
                    key={mode}
                    onClick={() => {
                      setActionMenu(null);
                      onCleanupMode(mode);
                      onDialog("cleanup");
                    }}
                    role="menuitem"
                    type="button"
                  >
                    {label}
                  </button>
                ))}
                </div>
              ) : null}
            </div>
          </div>
          <div className="voice-post-status-row">
            <span className="voice-post-progress">进度 {lineage.deliveryAllowed ? "100%" : studio.deliveryState === "in_progress" ? "进行中" : "0/0"}</span>
            <strong>{voiceSettingsDraft.automationMode === "autonomous" ? "AI 自主 · 待命" : voiceSettingsDraft.automationMode === "assisted" ? "AI 伴随 · 待命" : "全人工 · 待命"}</strong>
            <span>场景声音: {hasScriptSegments ? "等待素材确认" : "等待脚本生成"}</span>
            <button className="button button-quiet" onClick={() => onDialog("mix-manifest")} type="button">查看混音清单</button>
            <button className="button button-secondary" onClick={() => onPostWorkspaceTab("library")} type="button">补齐声音素材</button>
            <label className="voice-follow-reading">
              <input checked={followReading} onChange={(event) => onFollowReading(event.target.checked)} type="checkbox" />
              跟随朗读
            </label>
          </div>
          <div className="voice-post-workbench">
            <article className="voice-post-player">
              <div className="voice-post-player-stage">
                <span>{studio.audioReady ? `章节主音轨已装配 · ${formatVoiceTime(chapterProgress)} / ${formatVoiceTime(chapterDuration)}` : "未加载音频"}</span>
              </div>
              <div className="voice-post-seek"><i style={{ width: chapterDuration > 0 ? `${Math.min(100, chapterProgress / chapterDuration * 100)}%` : "0%" }} /></div>
              <div className="voice-post-transport">
                <button disabled={!studio.chapterAudioUrl} onClick={() => seekChapterBoundary("start")} type="button">|◀ 开头</button>
                <button disabled={!studio.chapterAudioUrl} onClick={() => seekChapterAudio(-10)} type="button">◀ 10s</button>
                <button disabled={!studio.chapterAudioUrl} onClick={() => void toggleChapterPlayback()} type="button">{chapterPlaying ? "❚❚ 暂停" : "▶ 播放"}</button>
                <button disabled={!studio.chapterAudioUrl} onClick={() => seekChapterAudio(10)} type="button">10s ▶</button>
                <button disabled={!studio.chapterAudioUrl} onClick={() => seekChapterBoundary("end")} type="button">结尾 ▶|</button>
              </div>
              <label className="voice-post-volume">音量 <input max="100" min="0" onChange={(event) => {
                const next = Number(event.target.value) / 100;
                onChapterVolume(next);
                if (chapterAudioRef.current) chapterAudioRef.current.volume = next;
              }} type="range" value={Math.round(chapterVolume * 100)} /> <span>{Math.round(chapterVolume * 100)}%</span></label>
              <label className="voice-post-output">输出设备
                <select disabled={!studio.audioReady}><option>跟随系统输出（当前：系统默认设备）</option></select>
              </label>
              <p className="voice-post-player-status">
                {lineage.deliveryAllowed
                  ? "成品已通过交付门禁。"
                  : lineage.detail}
              </p>
              <button className="button button-secondary voice-post-assets" onClick={() => {
                onPostWorkspaceTab("library");
              }} type="button">补齐声音素材</button>
            </article>
            <article className="voice-post-inspector">
              <div className="voice-post-inspector-tabs" role="tablist">
                {(["transcript", "subtitle", "mix"] as const).map((tab) => (
                  <button
                    aria-selected={postDetailTab === tab}
                    className={postDetailTab === tab ? "is-active" : ""}
                    key={tab}
                    onClick={() => onPostDetailTab(tab)}
                    role="tab"
                    type="button"
                  >
                    {tab === "transcript" ? "实时脚本" : tab === "subtitle" ? "字幕" : "混音清单"}
                  </button>
                ))}
              </div>
              <div className="voice-post-inspector-body">
                {postDetailTab === "transcript" && scriptSegments.map((segment) => (
                  <p key={segment.id}><strong>{segment.speakerLabel}</strong>{segment.content}</p>
                ))}
                {postDetailTab === "subtitle" && (
                  studio.subtitleText
                    ? <pre className="voice-post-subtitle">{studio.subtitleText}</pre>
                    : <p>{studio.subtitleReady ? "字幕已与章节主音轨对齐。" : "字幕将在装配与语音对齐完成后显示。"}</p>
                )}
                {postDetailTab === "mix" && (
                  <div className="voice-post-mix-summary">
                    {studio.mixTracks.map((track) => (
                      <p key={track.id}>
                        <strong>{track.label}</strong>
                        {track.status === "ready" ? "已装配" : track.status === "blocked" ? "存在失败事件" : "待装配"}
                        {` · ${track.eventCount} 个事件`}
                        {track.stemAudioUrl ? <a download href={track.stemAudioUrl}>下载分轨</a> : null}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            </article>
          </div>
        </>
      )}
    </div>
  );
}

function formatVoiceTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

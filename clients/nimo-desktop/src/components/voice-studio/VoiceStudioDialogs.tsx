import type {
  EngineCommandClient,
  VoiceStudioView,
} from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import { SpeakerReviewDialog } from "./SpeakerReviewDialog";
import { AudiobookExportDialog } from "./AudiobookExportDialog";
import { VoiceCloneDialog } from "../VoiceCloneDialog";
import { VoiceDeliveryDialog, type ChapterExportFormat, type ExportScope } from "../VoiceDeliveryDialog";
import { VoiceDesignDialog } from "../VoiceDesignDialog";
import { VoicePreviewDialog } from "../VoicePreviewDialog";
import { VoiceRebuildDialog } from "../VoiceRebuildDialog";
import { VoiceScriptEditorDialog } from "../VoiceScriptEditorDialog";
import {
  changedVoicePerformanceDraftIds,
  hasVoiceScriptChanges,
  type VoiceRoomSegmentSession,
} from "../../lib/voice-room-session";
import type { VoiceScriptDraft } from "../../lib/voice-script-session";
import type { TaskStreamClient } from "../../lib/use-task-stream";
import { parseVoiceMultiplier, parseVoicePitch, splitVoiceList } from "./voiceHelpers";
import { buildVoiceSegmentOverride } from "./voiceSegmentOverride";

export type VoiceDialogKind =
  | "clone"
  | "preview"
  | "design"
  | "script"
  | "guidance"
  | "assemble"
  | "export"
  | "export-audiobook"
  | "rebuild"
  | "speaker-review"
  | "mix-manifest"
  | "cleanup"
  | null;

export type VoiceCleanupCategory =
  | "stale_previews"
  | "orphan_candidates"
  | "completed_checkpoints"
  | "orphan_sound_assets"
  | "orphan_chapter_reports";

export type VoiceCleanupMode = "all" | "chapter" | "batch" | "expired" | "reset";

interface VoiceStudioDialogsProps {
  readonly activeCast: VoiceStudioView["cast"][number];
  readonly activeSegment: VoiceStudioView["script"][number];
  readonly cleanupCategories: readonly VoiceCleanupCategory[];
  readonly cleanupChapterNumbers: readonly number[];
  readonly cleanupMode: VoiceCleanupMode;
  readonly cloneReferenceMode: "local-file" | "provider-file-id";
  readonly commandClient: EngineCommandClient;
  readonly dialog: VoiceDialogKind;
  readonly exportPreset: {
    readonly format: ChapterExportFormat;
    readonly includeSubtitles: boolean;
    readonly normalizeLoudness: boolean;
    readonly scope: ExportScope;
  };
  readonly hasCastMembers: boolean;
  readonly hasScriptSegments: boolean;
  readonly onCleanupCategories: (categories: readonly VoiceCleanupCategory[]) => void;
  readonly onCleanupChapterNumbers: (chapters: readonly number[]) => void;
  readonly onClose: () => void;
  readonly onDialog: (dialog: VoiceDialogKind) => void;
  readonly onGuidanceNotice: (message: string) => void;
  readonly onRefresh: () => Promise<void>;
  readonly onRoomGuidanceDrafts: (drafts: Readonly<Record<string, VoiceScriptDraft>>) => void;
  readonly onScriptState: (state: "generated" | "ready" | "generating" | "synthesizing") => void;
  readonly refreshAfterVoiceMutation: () => Promise<void>;
  readonly roomGuidanceDrafts: Readonly<Record<string, VoiceScriptDraft>>;
  readonly roomTakeSession: Readonly<Record<string, VoiceRoomSegmentSession>>;
  readonly scriptSegments: readonly VoiceStudioView["script"][number][];
  readonly selectedChapter: number;
  readonly studio: VoiceStudioView;
  readonly submitVoiceTeamBuild: (characterIds?: readonly string[]) => Promise<void>;
  readonly streamClient: TaskStreamClient;
}

/**
 * 声腔全部对话框渲染：克隆/试听/设计/脚本/指导/装配/导出/重建/说话人复核/
 * 混音清单/清理，从 VoiceStudioPage 提取，主页面仅保留 dialog 状态开关。
 */
export function VoiceStudioDialogs({
  activeCast,
  activeSegment,
  cleanupCategories,
  cleanupChapterNumbers,
  cleanupMode,
  cloneReferenceMode,
  commandClient,
  dialog,
  exportPreset,
  hasCastMembers,
  hasScriptSegments,
  onCleanupCategories,
  onCleanupChapterNumbers,
  onClose,
  onDialog,
  onGuidanceNotice,
  onRefresh,
  onRoomGuidanceDrafts,
  onScriptState,
  refreshAfterVoiceMutation,
  roomGuidanceDrafts,
  roomTakeSession,
  scriptSegments,
  selectedChapter,
  studio,
  submitVoiceTeamBuild,
  streamClient,
}: VoiceStudioDialogsProps) {
  return (
    <>
      {dialog === "clone" && hasCastMembers && (
        <VoiceCloneDialog
          member={activeCast}
          onClose={onClose}
          onSubmit={async (input) => {
            const result = await commandClient.cloneCharacterVoice({
              kind: "clone_character_voice",
              projectId: studio.projectId,
              characterId: activeCast.id,
              referenceAudio: input.referenceAudio,
              referenceTranscript: input.referenceTranscript,
              authorized: input.authorized,
            });
            if (result.status === "accepted") await refreshAfterVoiceMutation();
            return result;
          }}
          providerLabel={studio.providerLabel}
          referenceMode={cloneReferenceMode}
          requiresConsent
        />
      )}
      {dialog === "preview" && hasCastMembers && (
        <VoicePreviewDialog
          commandClient={commandClient}
          member={activeCast}
          onClose={onClose}
          onConfirmed={(message) => {
            onClose();
            onGuidanceNotice(message);
            void refreshAfterVoiceMutation();
          }}
          projectId={studio.projectId}
          providerLabel={studio.providerLabel}
        />
      )}
      {dialog === "design" && hasCastMembers && (
        <VoiceDesignDialog
          member={activeCast}
          onClose={onClose}
          onSubmit={async (brief) => {
            const result = activeCast.id === "narrator"
              ? await commandClient.rebuildNarratorVoice({
                kind: "rebuild_narrator_voice",
                projectId: studio.projectId,
                provider: studio.providerLabel,
              })
              : await commandClient.designCharacterVoice({
                kind: "design_character_voice",
                projectId: studio.projectId,
                characterId: activeCast.id,
                description: brief,
              });
            if (result.status === "accepted") await refreshAfterVoiceMutation();
            return result;
          }}
          providerLabel={studio.providerLabel}
        />
      )}
      {dialog === "script" && hasScriptSegments && (
        <VoiceScriptEditorDialog
          chapterNumber={studio.chapterNumber}
          initialSegmentId={activeSegment.id}
          onClose={onClose}
          onSave={(result) => {
            if (!hasVoiceScriptChanges(scriptSegments, result.segments)) {
              onGuidanceNotice("未检测到脚本修改；旧音频与试听状态保持不变。");
              onClose();
              return;
            }
            onGuidanceNotice("正在保存配音脚本并使旧音频进入待重建状态…");
            void commandClient.saveVoiceScript({
              kind: "save_voice_script",
              projectId: studio.projectId,
              chapterNumber: studio.chapterNumber,
              edits: result.segments.map((segment) => {
                const draft = result.drafts[segment.id];
                const speedOverride = parseVoiceMultiplier(draft?.speed ?? "default");
                const volumeOverride = parseVoiceMultiplier(draft?.volume ?? "default");
                const pitchOverride = parseVoicePitch(draft?.pitch ?? "default");
                const narratorDistance = draft?.narratorDistance === "project-default"
                  ? ""
                  : draft?.narratorDistance;
                return {
                  segmentIndex: segment.segmentIndex,
                  content: segment.content,
                  speakerId: segment.speakerId,
                  speakerLabel: segment.speakerLabel,
                  segmentType: segment.kindLabel.includes("对白")
                    ? "dialogue"
                    : segment.kindLabel.includes("内心")
                      ? "inner_thought"
                      : "narration",
                  emotionLabel: segment.emotionLabel,
                  emotionIntensity: (draft?.emotionIntensity ?? 50) / 100,
                  toneHint: draft?.toneHint ?? "",
                  ...(speedOverride === undefined ? {} : { speedOverride }),
                  ...(volumeOverride === undefined ? {} : { volumeOverride }),
                  ...(pitchOverride === undefined ? {} : { pitchOverride }),
                  stressWords: splitVoiceList(draft?.stressWords ?? ""),
                  ...(narratorDistance === undefined ? {} : { narratorDistance }),
                  pronunciationOverrides: splitVoiceList(draft?.pronunciationOverrides ?? ""),
                  languageCode: draft?.language ?? "auto",
                };
              }),
            }).then(async (saveResult) => {
              onGuidanceNotice(saveResult.message);
              if (saveResult.status !== "accepted") return;
              onRoomGuidanceDrafts({});
              onScriptState("generated");
              await refreshAfterVoiceMutation();
              onClose();
            }).catch(() => {
              onGuidanceNotice("配音脚本保存失败；正式脚本、音频与字幕均保持不变。");
            });
          }}
          providerLabel={studio.providerLabel}
          segments={scriptSegments}
        />
      )}
      {dialog === "guidance" && hasScriptSegments && (
        <VoiceScriptEditorDialog
          chapterNumber={studio.chapterNumber}
          editMode="performance"
          initialDrafts={roomGuidanceDrafts}
          initialSegmentId={activeSegment.id}
          onClose={onClose}
          onSave={(result) => {
            const changedIds = changedVoicePerformanceDraftIds(
              scriptSegments,
              result.drafts,
              roomGuidanceDrafts,
            );
            if (changedIds.length === 0) {
              onGuidanceNotice("未检测到试听指导修改；候选试听与正式版本保持不变。");
              onClose();
              return;
            }
            const editedSegments = changedIds.flatMap((segmentId) => {
              const segment = scriptSegments.find((item) => item.id === segmentId);
              const draft = result.drafts[segmentId];
              return segment === undefined || draft === undefined
                ? []
                : [{
                    segmentIndex: segment.segmentIndex,
                    segmentOverride: buildVoiceSegmentOverride(segment, draft),
                  }];
            });
            void commandClient.saveVoiceGuidance({
              kind: "save_voice_guidance",
              projectId: studio.projectId,
              chapterNumber: studio.chapterNumber,
              edits: editedSegments,
            }).then(async (saveResult) => {
              onGuidanceNotice(saveResult.message);
              if (saveResult.status !== "accepted") return;
              onRoomGuidanceDrafts(result.drafts);
              await refreshAfterVoiceMutation();
              onClose();
            }).catch(() => {
              onGuidanceNotice("试听指导保存失败；候选试听与正式版本保持不变。");
            });
          }}
          providerLabel={studio.providerLabel}
          segments={scriptSegments}
        />
      )}
      {dialog === "assemble" && (
        <VoiceDeliveryDialog
          chapterNumber={studio.chapterNumber}
          commandClient={commandClient}
          kind="assemble"
          onClose={onClose}
          projectId={studio.projectId}
          streamClient={streamClient}
        />
      )}
      {dialog === "export" && (
        <VoiceDeliveryDialog
          chapterNumber={studio.chapterNumber}
          commandClient={commandClient}
          initialFormat={exportPreset.format}
          initialIncludeSubtitles={exportPreset.includeSubtitles}
          initialNormalizeLoudness={exportPreset.normalizeLoudness}
          initialScope={exportPreset.scope}
          kind="export"
          onClose={onClose}
          projectId={studio.projectId}
          streamClient={streamClient}
        />
      )}
      {dialog === "export-audiobook" && (
        <AudiobookExportDialog
          commandClient={commandClient}
          onClose={onClose}
          projectId={studio.projectId}
          streamClient={streamClient}
        />
      )}
      {dialog === "rebuild" && (
        <VoiceRebuildDialog
          cast={studio.cast}
          onClose={onClose}
          onConfirm={(ids) => {
            onClose();
            void submitVoiceTeamBuild(ids);
          }}
        />
      )}
      {dialog === "speaker-review" && (
        <SpeakerReviewDialog
          cast={studio.cast}
          chapterNumber={studio.chapterNumber}
          commandClient={commandClient}
          onClose={onClose}
          onResolved={async (resolvedSegmentIndices) => {
            const resolved = new Set(resolvedSegmentIndices);
            const remainingCount = scriptSegments.filter(
              (segment) =>
              segment.needsSpeakerReview
              && !resolved.has(segment.segmentIndex),
            ).length;
            try {
              await refreshAfterVoiceMutation();
            } catch {
              onGuidanceNotice("说话人复核已保存；刷新失败时请重新打开当前章节确认结果。");
              onClose();
              return;
            }
            onGuidanceNotice(
              remainingCount === 0
                ? "说话人复核已完成，可以开始合成。"
                : `已保存本次复核，仍有 ${remainingCount} 段待确认。`,
            );
            onClose();
          }}
          projectId={studio.projectId}
          segments={scriptSegments}
          unresolvedSegments={scriptSegments
            .filter((segment) => segment.needsSpeakerReview)
            .map((segment) => ({
              segmentIndex: segment.segmentIndex,
              text: segment.content,
              contextBefore: segment.contextBefore ?? "",
              contextAfter: segment.contextAfter ?? "",
              candidates: segment.speakerCandidates ?? [],
            }))}
        />
      )}
      {dialog === "mix-manifest" && (
        <AppDialog description="章节混音清单：人声、BGM 与音效的时间轴分布、响度归一化参数和导出格式。" onClose={onClose} title="混音清单">
          <table className="mix-manifest-table">
            <thead><tr><th>轨道</th><th>时长</th><th>事件</th><th>状态</th></tr></thead>
            <tbody>
              {studio.mixTracks.map((track) => (
                <tr key={track.id}>
                  <td>{track.label}</td>
                  <td>{formatVoiceTime(track.durationMs / 1000)}</td>
                  <td>{track.eventCount}{track.failedEventCount > 0 ? `（失败 ${track.failedEventCount}）` : ""}</td>
                  <td>{track.status === "ready" ? "已装配" : track.status === "blocked" ? "需处理" : "待装配"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <small>数据来自当前章节正式混音渲染报告；重装配后会自动刷新。</small>
        </AppDialog>
      )}
      {dialog === "cleanup" && (
        <AppDialog
          description={
            cleanupMode === "batch"
              ? "选择多个章节，一次移除脚本、音频、字幕、试听与混音报告。"
              : cleanupMode === "expired"
                ? "按类别删除未被正式产物引用的缓存；已采纳音轨始终保留。"
                : "只清理可重新生成的配音产物。配音团队、平台设置和声音资源库会被保留。"
          }
          onClose={onClose}
          title={
            cleanupMode === "chapter"
              ? `清理第 ${selectedChapter} 章产物`
              : cleanupMode === "batch"
                ? "批量清理章节"
                : cleanupMode === "expired"
                  ? "清理过期 TTS 文件"
                  : cleanupMode === "reset"
                    ? "重新开始配音工作流"
                    : "配音产物管理"
          }
        >
          {(cleanupMode === "all" || cleanupMode === "chapter" || cleanupMode === "reset") && (
            <div className="voice-cleanup-actions">
              {([
                ["script_chapter", `清理第 ${selectedChapter} 章脚本`, "已合成音频仍可播放；重新合成前需重新生成脚本。"],
                ["chapter", `清理第 ${selectedChapter} 章全部产物`, "移除本章脚本、音频、字幕、试听与混音报告。"],
                ["redundant_takes", `清理第 ${selectedChapter} 章冗余试听`, "保留正式使用中的已接受音轨。"],
                ["project_reset", "重置全项目可再生产物", "保留团队、旁白、平台路由、声音创意与声音资源库。"],
              ] as const)
                .filter(([scope]) =>
                  cleanupMode === "all"
                  || (cleanupMode === "chapter" && scope === "chapter")
                  || (cleanupMode === "reset" && scope === "project_reset"))
                .map(([scope, label, detail]) => (
                  <button
                    className="voice-cleanup-option"
                    key={scope}
                    onClick={async () => {
                      const confirmed = window.confirm(`${label}？\n\n${detail}`);
                      if (!confirmed) return;
                      const result = await commandClient.clearVoiceArtifacts({
                        kind: "clear_voice_artifacts",
                        projectId: studio.projectId,
                        scope,
                        ...(scope === "project_reset" ? {} : { chapterNumber: selectedChapter }),
                      });
                      onGuidanceNotice(result.message);
                      if (result.status === "accepted") {
                        await onRefresh();
                        onClose();
                      }
                    }}
                    type="button"
                  >
                    <strong>{label}</strong>
                    <span>{detail}</span>
                  </button>
                ))}
            </div>
          )}
          {(cleanupMode === "all" || cleanupMode === "batch") && (
            <section className="voice-cleanup-group">
              <header>
                <strong>批量清理章节</strong>
                <span>选择多个章节，一次移除脚本、音频、字幕、试听与混音报告。</span>
              </header>
              <div className="voice-cleanup-check-grid">
                {(studio.availableChapters.length > 0
                  ? studio.availableChapters
                  : [selectedChapter]).map((chapterNumber) => (
                  <label key={chapterNumber}>
                    <input
                      checked={cleanupChapterNumbers.includes(chapterNumber)}
                      onChange={(event) => onCleanupChapterNumbers(
                        event.target.checked
                          ? [...cleanupChapterNumbers, chapterNumber].sort((left, right) => left - right)
                          : cleanupChapterNumbers.filter((number) => number !== chapterNumber),
                      )}
                      type="checkbox"
                    />
                    第 {chapterNumber} 章
                  </label>
                ))}
              </div>
              <button
                className="button button-secondary"
                disabled={cleanupChapterNumbers.length === 0}
                onClick={async () => {
                  const chapterLabel = cleanupChapterNumbers.join("、");
                  if (!window.confirm(`清理第 ${chapterLabel} 章的全部配音产物？\n\n此操作只移除可重新生成的文件。`)) return;
                  const result = await commandClient.clearVoiceArtifacts({
                    kind: "clear_voice_artifacts",
                    projectId: studio.projectId,
                    scope: "chapters",
                    chapterNumbers: cleanupChapterNumbers,
                  });
                  onGuidanceNotice(result.message);
                  if (result.status === "accepted") {
                    await onRefresh();
                    onClose();
                  }
                }}
                type="button"
              >
                清理选中章节（{cleanupChapterNumbers.length}）
              </button>
            </section>
          )}
          {(cleanupMode === "all" || cleanupMode === "expired") && (
            <section className="voice-cleanup-group">
              <header>
                <strong>清理过期 TTS 文件</strong>
                <span>按类别删除未被正式产物引用的缓存；已采纳音轨和项目契约始终保留。</span>
              </header>
              <div className="voice-cleanup-category-list">
                {([
                  ["stale_previews", "过期试听 / 激活音频"],
                  ["orphan_candidates", "未引用的候选试听"],
                  ["completed_checkpoints", "已完成章节的检查点"],
                  ["orphan_sound_assets", "孤儿生成音效"],
                  ["orphan_chapter_reports", "孤儿章节报告"],
                ] as const).map(([category, label]) => (
                  <label key={category}>
                    <input
                      checked={cleanupCategories.includes(category)}
                      onChange={(event) => onCleanupCategories(
                        event.target.checked
                          ? [...cleanupCategories, category]
                          : cleanupCategories.filter((item) => item !== category),
                      )}
                      type="checkbox"
                    />
                    {label}
                  </label>
                ))}
              </div>
              <button
                className="button button-secondary"
                disabled={cleanupCategories.length === 0}
                onClick={async () => {
                  if (!window.confirm(`清理所选 ${cleanupCategories.length} 类过期 TTS 文件？\n\n正在使用的正式资产不会被删除。`)) return;
                  const result = await commandClient.clearVoiceArtifacts({
                    kind: "clear_voice_artifacts",
                    projectId: studio.projectId,
                    scope: "stale_files",
                    categories: cleanupCategories,
                  });
                  onGuidanceNotice(result.message);
                  if (result.status === "accepted") {
                    await onRefresh();
                    onClose();
                  }
                }}
                type="button"
              >
                预留正式资产并清理
              </button>
            </section>
          )}
        </AppDialog>
      )}
    </>
  );
}

function formatVoiceTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

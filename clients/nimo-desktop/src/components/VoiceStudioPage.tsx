import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  EngineClient,
  EngineCommandClient,
  SettingsView,
  VoiceActiveTaskView,
  VoiceCatalogOptionView,
  VoiceStudioView,
} from "@nimo/engine-contracts";

import { DropdownSelect } from "./DropdownSelect";
import {
  VoicePlatformSettings,
  createVoicePlatformSettingsDraft,
  voicePlatformSettingsEqual,
  voicePlatformSettingsToCreationParameters,
  type VoicePlatformSettingsDraft,
} from "./VoicePlatformSettings";
import type { VoiceAudioModelCenterClient } from "./VoiceLocalModelCenter";
import type { ChapterExportFormat, ExportScope } from "./VoiceDeliveryDialog";
import { VoiceStudioDialogs } from "./voice-studio/VoiceStudioDialogs";
import { VoiceStudioStatusBar } from "./voice-studio/VoiceStudioStatusBar";
import { SoundLibraryPanel } from "./voice-studio/SoundLibraryPanel";
import { ScriptPanel } from "./voice-studio/ScriptPanel";
import { PostPanel } from "./voice-studio/PostPanel";
import { RoomPanel } from "./voice-studio/RoomPanel";
import { TeamPanel } from "./voice-studio/TeamPanel";
import { VoiceRoomGuidanceSummary } from "./voice-studio/VoiceRoomGuidanceSummary";
import { buildVoiceSegmentOverride } from "./voice-studio/voiceSegmentOverride";
import type { VoiceScriptDraft } from "../lib/voice-script-session";
import { voiceCloneReferenceMode } from "../lib/voice-clone-mode";
import { voiceLineageState } from "../lib/voice-lineage";
import { voiceTextRoutesToCommands } from "../lib/voice-text-routing";
import { createIncrementalJsonParser } from "../lib/incremental-json";
import { detectStreamAnomalies } from "../lib/stream-anomaly";
import {
  beginVoiceRoomAcceptance,
  beginVoiceRoomCandidate,
  createVoiceRoomGuidanceDrafts,
  completeVoiceRoomAcceptance,
  createVoiceRoomSession,
  discardVoiceRoomCandidate,
  registerVoiceRoomCandidate,
  type VoiceRoomSegmentSession,
} from "../lib/voice-room-session";
import type { VoiceDialogFixture } from "../lib/parity-fixture";
import type { VoiceStudioTabId } from "../lib/ui-session";
import { useTaskStream, type TaskStreamClient } from "../lib/use-task-stream";

interface VoiceStudioPageProps {
  /** Read-only catalog boundary paired with the durable assignment command. */
  readonly catalogClient: Pick<EngineClient, "getVoiceCatalog">;
  readonly commandClient: EngineCommandClient;
  readonly modelCenterClient: VoiceAudioModelCenterClient;
  /** Durable task stream used for observable long-running voice operations. */
  readonly streamClient: TaskStreamClient;
  /** Safe, credential-free values returned by the shared Settings view. */
  readonly creationParameters?: Readonly<Record<string, string>> | undefined;
  /** Credential-free model profiles and TTS route projections shared with 火候. */
  readonly routingSettings?: Pick<SettingsView, "defaultProfileId" | "modelProfiles" | "routingGroups"> | undefined;
  readonly initialDialogFixture?: VoiceDialogFixture | null;
  /** Restored tab id — mirrors voice_studio restore_ui_state()["tab_index"]. */
  readonly initialTab?: VoiceStudioTabId;
  /** Restored segment index within the voice room tab. */
  readonly initialSegmentIndex?: number;
  readonly parityState?: "configured" | null;
  readonly studio: VoiceStudioView;
  readonly onChapterChange?: (chapterNumber: number) => Promise<void>;
  /** Reload the shared, credential-free studio projection after a persisted mutation. */
  readonly onRefresh?: () => Promise<void>;
  /** Persist the active tab — mirrors voice_studio export_ui_state()["tab_index"]. */
  readonly onTabChange?: (tab: VoiceStudioTabId) => void;
  /** Persist the selected segment index for session restore. */
  readonly onSegmentIndexChange?: (index: number) => void;
  /**
   * Notify the host when long-running voice workers are active. The top-bar
   * provider dropdown disables and shows a guarding status message while
   * the boolean is true, mirroring PySide6 `_switch_provider`'s lock.
   */
  readonly onWorkerBusyChange?: (busy: boolean) => void;
  /** Bumped after a top-bar provider change to refresh the model center. */
  readonly modelCenterRefreshToken?: number;
}

const noOpVoiceStudioRefresh = async (): Promise<void> => undefined;
const noOpVoiceChapterChange = async (_chapterNumber: number): Promise<void> => undefined;
type VoiceCleanupCategory =
  | "stale_previews"
  | "orphan_candidates"
  | "completed_checkpoints"
  | "orphan_sound_assets"
  | "orphan_chapter_reports";

type VoiceTeamTaskPhase = "idle" | "submitting" | "submitted" | "completed" | "cancelled" | "failed";

interface VoiceTeamTaskFeedback {
  readonly phase: VoiceTeamTaskPhase;
  readonly taskId: string | null;
  readonly message: string;
  readonly requestedCount: number;
}

const initialVoiceTeamTaskFeedback: VoiceTeamTaskFeedback = {
  phase: "idle",
  taskId: null,
  message: "",
  requestedCount: 0,
};

function voiceStepLabel(stepLabel: string): string {
  const labels: Readonly<Record<string, string>> = {
    // Voice team build steps
    voice_team_workflow_start: "正在准备配音团队",
    voice_team_narrator_check_start: "正在检查旁白音色",
    voice_team_cast_phase_start: "正在同步角色与音色库",
    build_voice_team_start: "正在匹配角色音色",
    build_voice_team_progress: "正在分配角色音色",
    build_voice_team_complete: "正在整理配音团队",
    blueprint: "正在匹配角色与音色",
    // TTS pipeline steps
    tts_prepare: "正在准备声音资源",
    tts_auto_trigger_queued: "自动配音已排程",
    tts_auto_trigger_started: "自动配音已启动",
    tts_automation_mode: "正在确认推进模式",
    tts_manual_prerequisites_ready: "手动前置条件已就绪",
    tts_narrator_start: "正在准备旁白音色",
    tts_narrator_llm_call: "正在生成旁白音色",
    tts_narrator_done: "旁白音色已就绪",
    tts_voice_team_narrator_only: "仅旁白音色已就绪",
    tts_voice_team_reused: "复用已有配音团队",
    tts_voice_team_confirmed: "配音团队已确认",
    tts_script_start: "正在生成配音脚本",
    tts_script_llm_call: "正在调用模型改写脚本",
    tts_script_llm_batch_start: "配音脚本分批改写",
    tts_script_llm_batch_complete: "配音脚本分批完成",
    tts_script_llm_rejected: "配音脚本未通过保真检查",
    tts_script_rule_fallback: "配音脚本已本地降级",
    tts_script_llm_review_start: "配音脚本专业审校",
    tts_script_llm_review_complete: "配音脚本审校完成",
    tts_script_parsed: "配音脚本已解析",
    tts_script_done: "配音脚本已就绪",
    tts_script_reused: "复用已有配音脚本",
    tts_spoken_rewrite_batch_start: "口语改写批次开始",
    tts_spoken_rewrite_complete: "口语改写完成",
    tts_synthesis_start: "正在并行合成音频",
    tts_segment: "正在合成音频片段",
    tts_synthesis_complete: "音频片段合成完成",
    tts_synthesis_reused: "复用已合成音频",
    tts_alignment_start: "正在对齐语音时间线",
    tts_alignment_repair_start: "正在修复语音对齐",
    tts_alignment_repair_complete: "语音对齐修复完成",
    tts_alignment_complete: "语音时间线已对齐",
    tts_assembly_start: "正在装配章节音频",
    tts_assembly_complete: "章节音频装配完成",
    tts_assembly_partial: "章节音频部分装配",
    tts_quality_complete: "音频质量检查完成",
    tts_auto_trigger_completed: "自动配音交付完成",
    tts_auto_trigger_partial: "自动配音可续跑",
    tts_auto_trigger_failed: "自动配音失败",
    tts_speaker_repair_start: "正在修复未指定角色的对白",
    tts_speaker_repair_done: "说话人修复完成",
    tts_speaker_repair_failed: "说话人修复失败",
    tts_export_start: "正在准备音频交付文件",
    tts_export_complete: "音频交付文件已生成",
    tts_audiobook_export_start: "正在汇编有声书交付包",
    tts_audiobook_export_complete: "有声书交付包已生成",
  };
  return labels[stepLabel] ?? (stepLabel ? `正在执行：${stepLabel}` : "正在启动配音任务");
}

function formatVoiceTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "00:00";
  const whole = Math.floor(seconds);
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

function voiceTeamFailureMessage(message: string): string {
  const normalized = message.toLowerCase();
  if (
    normalized.includes("voiceperformancedirection")
    && normalized.includes("strength")
    && normalized.includes("subtle")
    && normalized.includes("moderate")
  ) {
    return "表演强度参数未通过校验；当前团队未被改动，修复后可直接重新构建。";
  }
  return message;
}

function isCancelledVoiceTask(stepId: string | undefined): boolean {
  return stepId?.trim().toLowerCase() === "cancelled";
}

function activeVoiceTasks(studio: VoiceStudioView): readonly VoiceActiveTaskView[] {
  if (studio.activeTasks !== undefined) return studio.activeTasks;
  return studio.activeTaskId === null
    ? []
    : [{ id: studio.activeTaskId, kind: "synthesis" }];
}

function normalizeVoiceProgress(value: number | undefined): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  return Math.max(0, Math.min(100, Math.round(value)));
}

const emptyVoiceCastMember: VoiceStudioView["cast"][number] = {
  id: "",
  name: "尚未建立配音团队",
  role: "请先生成或导入角色音色",
  statusLabel: "待配",
  voiceLabel: "",
  description: "当前项目还没有可编辑的配音角色。",
};

const emptyVoiceScriptSegment: VoiceStudioView["script"][number] = {
  id: "",
  segmentIndex: 0,
  speakerId: "",
  speakerLabel: "暂无片段",
  kindLabel: "",
  emotionLabel: "",
  content: "当前章节还没有配音脚本。",
  statusLabel: "待生成",
  needsSpeakerReview: false,
};

function performanceOffsetsForMember(
  member: VoiceStudioView["cast"][number] | undefined,
) {
  return {
    speed: Math.round((member?.speedOffset ?? 0) * 100),
    pitch: member?.pitchOffset ?? 0,
    volume: Math.round((member?.volumeOffset ?? 0) * 100),
  };
}

export function VoiceStudioPage({
  catalogClient,
  commandClient,
  creationParameters,
  initialDialogFixture = null,
  initialTab = "team",
  initialSegmentIndex = 0,
  onChapterChange = noOpVoiceChapterChange,
  parityState = null,
  modelCenterClient,
  onRefresh = noOpVoiceStudioRefresh,
  routingSettings,
  streamClient,
  studio,
  onTabChange,
  onSegmentIndexChange,
  onWorkerBusyChange,
  modelCenterRefreshToken,
}: VoiceStudioPageProps) {
  const projectedVoiceTasks = useMemo(() => activeVoiceTasks(studio), [
    studio.activeTaskId,
    studio.activeTasks,
  ]);
  const projectedTeamTask = projectedVoiceTasks.find((task) => task.kind === "team_build") ?? null;
  const projectedScriptTask = projectedVoiceTasks.find((task) => task.kind === "script_generation") ?? null;
  const projectedSynthesisTask = projectedVoiceTasks.find((task) => task.kind === "synthesis") ?? null;
  const [activeTab, setActiveTabState] = useState<VoiceStudioTabId>(initialTab);
  const [settingsPreparing, setSettingsPreparing] = useState(initialTab === "settings");
  const setActiveTab = (tab: VoiceStudioTabId) => {
    if (tab === "settings" && activeTab !== "settings") {
      // The PySide surface constructs the settings page lazily.  Keep that
      // small, visible preparation state while retaining the already-loaded
      // React view and its real settings persistence path.
      setSettingsPreparing(true);
    }
    setActiveTabState(tab);
    onTabChange?.(tab);
  };
  const [activeCastId, setActiveCastId] = useState(studio.cast[0]?.id ?? "");
  const [castQuery, setCastQuery] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [parametersApplied, setParametersApplied] = useState(false);
  const [voiceCatalog, setVoiceCatalog] = useState<readonly VoiceCatalogOptionView[]>([]);
  const [voiceCatalogLoading, setVoiceCatalogLoading] = useState(false);
  const [voiceCatalogError, setVoiceCatalogError] = useState("");
  const [assignedVoiceId, setAssignedVoiceId] = useState<string | null>(null);
  const [voiceAssigning, setVoiceAssigning] = useState(false);
  const [voiceAssignmentNotice, setVoiceAssignmentNotice] = useState("");
  const [performanceOffsets, setPerformanceOffsets] = useState(() =>
    performanceOffsetsForMember(studio.cast[0]),
  );
  const [voiceTeamTask, setVoiceTeamTask] = useState<VoiceTeamTaskFeedback>(
    () => projectedTeamTask === null
      ? initialVoiceTeamTaskFeedback
      : {
          phase: "submitted",
          taskId: projectedTeamTask.id,
          message: "已恢复引擎中的配音团队任务，正在同步执行进度。",
          requestedCount: 0,
        },
  );
  const [selectedChapter, setSelectedChapter] = useState(studio.chapterNumber);
  const [scriptTaskId, setScriptTaskId] = useState<string | null>(
    () => projectedScriptTask?.id ?? null,
  );
  const [synthesisTaskId, setSynthesisTaskId] = useState<string | null>(
    () => projectedSynthesisTask?.id ?? null,
  );
  const [scriptState, setScriptState] = useState<
    "ready" | "generating" | "generated" | "synthesizing"
  >(() => projectedSynthesisTask !== null
    ? "synthesizing"
    : projectedScriptTask !== null
      ? "generating"
      : "ready");
  const [scriptSegments, setScriptSegments] = useState(studio.script);
  const [roomGuidanceDrafts, setRoomGuidanceDrafts] = useState<
    Readonly<Record<string, VoiceScriptDraft>>
  >(() => createVoiceRoomGuidanceDrafts(studio.script, studio.roomTakes));
  const [roomTakeSession, setRoomTakeSession] = useState(() =>
    createVoiceRoomSession(studio.script, studio.roomTakes),
  );
  const [guidanceNotice, setGuidanceNotice] = useState("");
  const [activeSegmentId, setActiveSegmentIdRaw] = useState(
    studio.script[initialSegmentIndex]?.id ?? studio.script[0]?.id ?? "",
  );
  const setActiveSegmentId = useCallback((id: string) => {
    setActiveSegmentIdRaw(id);
    const seg = scriptSegments.find((s) => s.id === id);
    if (seg) onSegmentIndexChange?.(seg.segmentIndex);
  }, [onSegmentIndexChange, scriptSegments]);
  const [playingSegmentId, setPlayingSegmentId] = useState<string | null>(null);
  const [chapterPlaying, setChapterPlaying] = useState(false);
  const [chapterProgress, setChapterProgress] = useState(0);
  const [chapterDuration, setChapterDuration] = useState(0);
  const [chapterVolume, setChapterVolume] = useState(0.8);
  const [cleanupChapterNumbers, setCleanupChapterNumbers] = useState<readonly number[]>([]);
  const [cleanupCategories, setCleanupCategories] = useState<readonly VoiceCleanupCategory[]>([
    "orphan_candidates",
  ]);
  const [cleanupMode, setCleanupMode] = useState<
    "all" | "chapter" | "batch" | "expired" | "reset"
  >("all");
  const soundLibraryBusy = false;
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [scriptSpeakerFilter, setScriptSpeakerFilter] = useState<string | null>(null);
  const [roomReferenceOpen, setRoomReferenceOpen] = useState(false);
  const [postWorkspaceTab, setPostWorkspaceTab] = useState<"assembly" | "library">("assembly");
  const [exportPreset, setExportPreset] = useState<{
    readonly format: ChapterExportFormat;
    readonly includeSubtitles: boolean;
    readonly normalizeLoudness: boolean;
    readonly scope: ExportScope;
  }>({
    format: "mp3",
    includeSubtitles: false,
    normalizeLoudness: false,
    scope: "chapter",
  });
  const [followReading, setFollowReading] = useState(true);
  const [postDetailTab, setPostDetailTab] = useState<"transcript" | "subtitle" | "mix">("transcript");
  const [voiceSettingsDraft, setVoiceSettingsDraft] = useState<VoicePlatformSettingsDraft>(
    () => createVoicePlatformSettingsDraft(studio, creationParameters, routingSettings),
  );
  const [savedVoiceSettingsDraft, setSavedVoiceSettingsDraft] = useState<VoicePlatformSettingsDraft>(
    () => createVoicePlatformSettingsDraft(studio, creationParameters, routingSettings),
  );
  const [referenceScriptText, setReferenceScriptText] = useState("");
  const [referenceScriptName, setReferenceScriptName] = useState("");
  const [referenceStyleStrength, setReferenceStyleStrength] = useState(0.65);
  const [referenceStyleState, setReferenceStyleState] = useState<
    "idle" | "loaded" | "analyzing" | "ready" | "error"
  >("idle");
  const [referenceStyleSummary, setReferenceStyleSummary] = useState(
    "未上传；项目已有画像时会按强度复用。参考原文不会写入风格画像。",
  );
  const [dialog, setDialog] = useState<
    "clone" | "design" | "preview" | "script" | "guidance" | "assemble" | "export" | "export-audiobook" | "rebuild" | "mix-manifest" | "speaker-review" | "cleanup" | null
  >(() =>
    initialDialogFixture === "clone-provider-file-id" ? "clone" : null,
  );
  const cloneReferenceMode = initialDialogFixture === "clone-provider-file-id"
    ? "provider-file-id"
    : voiceCloneReferenceMode(studio);
  const hasCastMembers = studio.cast.length > 0;
  const hasScriptSegments = scriptSegments.length > 0;
  const lineage = voiceLineageState(studio);
  const activeCast =
    studio.cast.find((member) => member.id === activeCastId) ??
    studio.cast[0] ??
    emptyVoiceCastMember;
  const activeSegment =
    scriptSegments.find((segment) => segment.id === activeSegmentId) ??
    scriptSegments[0] ??
    emptyVoiceScriptSegment;
  const activeRoomTake: VoiceRoomSegmentSession =
    (hasScriptSegments ? roomTakeSession[activeSegment.id] : undefined) ?? {
      state: "formal",
      hasDraftGuidance: false,
    };
  const activeCastApproved =
    !hasCastMembers || ["已配", "已配置", "已就绪"].includes(activeCast.statusLabel);
  const selectedVoiceId = assignedVoiceId ?? activeCast.voiceId ?? "";
  const voiceOptions = useMemo(() => {
    if (!selectedVoiceId || voiceCatalog.some((option) => option.id === selectedVoiceId)) {
      return voiceCatalog;
    }
    return [
      {
        id: selectedVoiceId,
        label: activeCast.voiceLabel || selectedVoiceId,
        description: "已保存的当前音色不在本次目录结果中。",
      },
      ...voiceCatalog,
    ];
  }, [activeCast.voiceLabel, selectedVoiceId, voiceCatalog]);
  const candidateAudioRef = useRef<HTMLAudioElement | null>(null);
  const castPreviewAudioRef = useRef<HTMLAudioElement | null>(null);
  const chapterAudioRef = useRef<HTMLAudioElement | null>(null);
  const recoveredVoiceTaskIdsRef = useRef(new Set(projectedVoiceTasks.map((task) => task.id)));
  const handledVoiceTeamTerminalTaskRef = useRef<string | null>(null);
  const observedVoiceTeamStream = useTaskStream(streamClient, voiceTeamTask.taskId);
  // A previous task snapshot can remain in the reducer until the next durable
  // snapshot arrives. Never present it as feedback for a newly submitted task.
  const voiceTeamStream = observedVoiceTeamStream?.taskId === voiceTeamTask.taskId
    ? observedVoiceTeamStream
    : null;
  const observedScriptStream = useTaskStream(streamClient, scriptTaskId);
  const scriptStream = observedScriptStream?.taskId === scriptTaskId ? observedScriptStream : null;
  const observedSynthesisStream = useTaskStream(streamClient, synthesisTaskId);
  const synthesisStream = observedSynthesisStream?.taskId === synthesisTaskId ? observedSynthesisStream : null;

  // The Engine view retains every durable voice operation across a browser
  // restart. Adopt newly projected tasks rather than leaving the page idle;
  // after this hand-off the task stream remains the progress authority.
  useEffect(() => {
    const newlyProjected = projectedVoiceTasks.filter((task) => {
      if (recoveredVoiceTaskIdsRef.current.has(task.id)) return false;
      recoveredVoiceTaskIdsRef.current.add(task.id);
      return true;
    });
    if (newlyProjected.length === 0) return;

    const teamTask = newlyProjected.find((task) => task.kind === "team_build");
    const scriptTask = newlyProjected.find((task) => task.kind === "script_generation");
    const synthesisTask = newlyProjected.find((task) => task.kind === "synthesis");
    if (teamTask !== undefined) {
      setVoiceTeamTask({
        phase: "submitted",
        taskId: teamTask.id,
        message: "已恢复引擎中的配音团队任务，正在同步执行进度。",
        requestedCount: 0,
      });
    }
    if (synthesisTask !== undefined) {
      setScriptTaskId(null);
      setSynthesisTaskId(synthesisTask.id);
      setScriptState("synthesizing");
    } else if (scriptTask !== undefined) {
      setSynthesisTaskId(null);
      setScriptTaskId(scriptTask.id);
      setScriptState("generating");
    }
    setGuidanceNotice("已恢复引擎中的配音任务，正在同步执行进度。");
  }, [projectedVoiceTasks]);
  const scriptTaskIsActive = scriptState === "generating" || scriptState === "synthesizing";
  const activeScriptTaskStream = scriptState === "generating"
    ? scriptStream
    : scriptState === "synthesizing"
      ? synthesisStream
      : null;
  const activeScriptProgressPercent = normalizeVoiceProgress(activeScriptTaskStream?.progressPercent);
  const activeScriptProgressStep = scriptState === "generating"
    ? (activeScriptTaskStream?.stepLabel
      ? voiceStepLabel(activeScriptTaskStream.stepLabel)
      : "正在生成配音脚本")
    : scriptState === "synthesizing"
      ? (activeScriptTaskStream?.stepLabel
        ? voiceStepLabel(activeScriptTaskStream.stepLabel)
        : "正在合成音频片段")
      : "等待配音任务";
  const activeScriptProgressDetail = activeScriptProgressPercent === undefined
    ? "正在同步任务进度"
    : `${activeScriptProgressPercent}%`;

  // Synthesis progress: calculate which segment is currently being synthesized
  // based on the overall progress percentage. This mirrors the PySide6
  // _on_segment_progress signal which highlights segments during synthesis.
  const synthesizingSegmentIndex = useMemo(() => {
    if (scriptState !== "synthesizing" || activeScriptProgressPercent === undefined) {
      return -1;
    }
    const total = scriptSegments.length;
    if (total === 0) return -1;
    // Map progress percent to segment index (0-based)
    const idx = Math.floor((activeScriptProgressPercent / 100) * total);
    return Math.min(idx, total - 1);
  }, [scriptState, activeScriptProgressPercent, scriptSegments.length]);

  // Auto-scroll to the currently synthesizing segment in the script list
  const scriptListRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (synthesizingSegmentIndex < 0 || !scriptListRef.current) return;
    const segment = scriptSegments[synthesizingSegmentIndex];
    if (!segment) return;
    const button = scriptListRef.current.querySelector(
      `[data-segment-id="${segment.id}"]`,
    );
    if (button) {
      button.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [synthesizingSegmentIndex, scriptSegments]);
  const voiceTeamTaskIsActive = voiceTeamTask.phase === "submitting"
    || (voiceTeamTask.phase === "submitted"
      && (voiceTeamStream === null
        || ["queued", "running", "paused"].includes(voiceTeamStream.jobState)));
  const voiceTeamTaskStatus = voiceTeamTask.phase === "submitting"
    ? "正在提交"
    : voiceTeamTask.phase === "completed"
      ? "已完成"
      : voiceTeamTask.phase === "cancelled"
        ? "已取消"
      : voiceTeamTask.phase === "failed"
        ? "构建失败"
        : voiceTeamStream?.jobState === "queued"
          ? "已提交 · 排队中"
          : voiceTeamStream?.jobState === "paused"
            ? "任务已暂停"
            : voiceTeamStream?.jobState === "running"
              ? "正在构建"
              : "已提交 · 等待启动";
  const voiceTeamTaskStep = voiceTeamTask.phase === "failed"
    ? "构建未完成，请检查配置后重试"
    : voiceTeamTask.phase === "completed"
      ? "配音团队已更新"
      : voiceTeamTask.phase === "cancelled"
        ? voiceTeamTask.message
      : voiceTeamStream === null
        ? voiceTeamTask.phase === "submitting"
          ? "正在将构建请求提交给引擎"
          : "正在等待引擎返回第一个执行阶段"
        : voiceStepLabel(voiceTeamStream.stepLabel);
  const voiceTeamProgressPercent = normalizeVoiceProgress(voiceTeamStream?.progressPercent);
  const voiceTeamProgressDetail = voiceTeamProgressPercent === undefined
    ? "正在同步任务进度"
    : `${voiceTeamProgressPercent}%`;
  const visibleCast = studio.cast.filter((member) =>
    `${member.name} ${member.role} ${member.statusLabel} ${member.voiceLabel}`
      .toLocaleLowerCase()
      .includes(castQuery.trim().toLocaleLowerCase()),
  );
  const scriptSpeakerLabels = Array.from(
    new Set(scriptSegments.map((segment) => segment.speakerLabel).filter(Boolean)),
  );
  const visibleScriptSegments = scriptSpeakerFilter === null
    ? scriptSegments
    : scriptSegments.filter((segment) => segment.speakerLabel === scriptSpeakerFilter);
  const narrationSegmentCount = scriptSegments.filter((segment) =>
    segment.kindLabel.includes("旁白") || segment.speakerLabel === "旁白",
  ).length;
  const dialogueSegmentCount = Math.max(0, scriptSegments.length - narrationSegmentCount);
  const unresolvedSpeakerCount = scriptSegments.filter((segment) => segment.needsSpeakerReview).length;

  // Extract streaming text from script generation task stream events.
  // Mirrors PySide6 ScriptRenderMixin._render_script_stream which accumulates
  // delta content events and incrementally parses JSON segment objects.
  const streamingScriptText = useMemo(() => {
    if (scriptState !== "generating" || scriptStream === null) return "";
    return scriptStream.events
      .filter((event) => event.kind === "delta" && event.segment === "content" && event.text)
      .map((event) => event.text!)
      .join("");
  }, [scriptState, scriptStream]);

  // Parse partial JSON array from streaming text to show segments as they arrive.
  // Mirrors PySide6 incremental_json_array_objects: a state machine that
  // handles escaped quotes and nested objects (the previous regex approach
  // truncated texts containing \" and dropped nested structures).
  const streamParserRef = useRef(createIncrementalJsonParser());
  const lastStreamIdRef = useRef("");
  const streamingStreamId = scriptStream?.events.find((event) => event.streamId !== "")?.streamId ?? "";
  const streamingSegments = useMemo(() => {
    if (!streamingScriptText) return [];
    // Per-stream cache semantics (mirrors PySide6): reset the parser when the
    // underlying stream switches, so stale scan positions never bleed across
    // a retry or a different task's stream.
    if (lastStreamIdRef.current !== streamingStreamId) {
      lastStreamIdRef.current = streamingStreamId;
      streamParserRef.current.reset();
    }
    const parsed = streamParserRef.current.parse(streamingScriptText, "segments");
    return parsed.map((item) => ({
      speaker: String(item.character_name ?? item.speaker ?? "旁白"),
      text: String(item.text ?? "").replace(/\\n/g, "\n"),
      emotion: String(item.emotion ?? ""),
    }));
  }, [streamingScriptText, streamingStreamId]);
  // Stream anomaly detection mirrors PySide6 detect_stream_anomalies:
  // duplicated segments, JSON structural echoes, and character inflation
  // flag a preview that the backend's validated output will replace.
  const streamingAnomaly = useMemo(
    () => detectStreamAnomalies(streamingSegments, streamingScriptText.length, 0),
    [streamingSegments, streamingScriptText],
  );
  const tabLabels: Readonly<Record<VoiceStudioTabId, string>> = {
    team: "1 配音团队",
    script: "2 配音脚本",
    room: "3 配音室",
    post: "4 后处理",
    settings: "平台设置",
  };

  useEffect(() => () => {
    candidateAudioRef.current?.pause();
    candidateAudioRef.current = null;
    castPreviewAudioRef.current?.pause();
    castPreviewAudioRef.current = null;
    chapterAudioRef.current?.pause();
    chapterAudioRef.current = null;
  }, []);

  const changeChapter = async (chapterNumber: number): Promise<void> => {
    if (chapterNumber === selectedChapter) return;
    const previousChapter = selectedChapter;
    candidateAudioRef.current?.pause();
    candidateAudioRef.current = null;
    chapterAudioRef.current?.pause();
    chapterAudioRef.current = null;
    setChapterPlaying(false);
    setPlayingSegmentId(null);
    setSelectedChapter(chapterNumber);
    setGuidanceNotice(`正在加载第 ${chapterNumber} 章的配音工作区…`);
    try {
      await onChapterChange(chapterNumber);
    } catch {
      setSelectedChapter(previousChapter);
      setGuidanceNotice(`第 ${chapterNumber} 章加载失败；仍保留第 ${previousChapter} 章的工作区。`);
    }
  };

  const previewCharacterVoice = async () => {
    if (!hasCastMembers) {
      setGuidanceNotice("尚未建立配音团队；请先自动组建或重新构建角色音色。");
      return;
    }
    if (previewing) {
      castPreviewAudioRef.current?.pause();
      castPreviewAudioRef.current = null;
      setPreviewing(false);
      setGuidanceNotice("已停止角色音色试听。");
      return;
    }
    setPreviewing(true);
    setGuidanceNotice(`正在生成 ${activeCast.name} 的真实音色试听…`);
    try {
      const result = await commandClient.previewCharacterVoice({
        kind: "preview_character_voice",
        projectId: studio.projectId,
        characterId: activeCast.id,
      });
      if (result.status !== "accepted") {
        setPreviewing(false);
        setGuidanceNotice(result.message);
        return;
      }
      if (!result.audioUrl) {
        setPreviewing(false);
        setGuidanceNotice(result.message);
        return;
      }
      const audio = new Audio(result.audioUrl);
      castPreviewAudioRef.current = audio;
      audio.onended = () => {
        castPreviewAudioRef.current = null;
        setPreviewing(false);
      };
      audio.onerror = () => {
        castPreviewAudioRef.current = null;
        setPreviewing(false);
        setGuidanceNotice("试听音频加载失败；项目中的音色记录未改变。");
      };
      await audio.play();
      setGuidanceNotice(result.message);
    } catch (error) {
      castPreviewAudioRef.current = null;
      setPreviewing(false);
      // The engine now surfaces lock contention and provider failures as
      // structured rejections; this branch only handles transport/5xx errors,
      // so surface the real detail instead of a generic engine-runtime hint.
      const detail = error instanceof Error && error.message ? error.message : "";
      setGuidanceNotice(
        detail
          ? `角色音色试听失败：${detail}`
          : "角色音色试听失败；请检查引擎与 TTS 运行时。",
      );
    }
  };

  // Source restore_ui_state() may resolve after the page module itself has
  // loaded. Keep the controlled tab in sync with that external UI-session
  // value without making normal user tab clicks depend on a remount.
  useEffect(() => {
    setActiveTabState(initialTab);
  }, [initialTab]);

  useEffect(() => {
    if (activeTab !== "settings") return undefined;
    const timer = window.setTimeout(() => setSettingsPreparing(false), 260);
    return () => window.clearTimeout(timer);
  }, [activeTab]);

  // The selected role is the source of truth for the detail workbench.  A
  // refreshed engine view therefore rehydrates its persisted performance
  // values instead of leaving the controls at a previous role's draft.
  useEffect(() => {
    setPerformanceOffsets(performanceOffsetsForMember(activeCast));
  }, [activeCast.id, activeCast.pitchOffset, activeCast.speedOffset, activeCast.volumeOffset]);

  useEffect(() => {
    setPreviewing(false);
    setParametersApplied(false);
    setAssignedVoiceId(null);
  }, [activeCast.id]);

  // Candidate and acceptance records survive a restart in the Engine take
  // manifest. Rehydrate them whenever the authoritative studio view refreshes;
  // local state is retained only while an interactive request is in flight.
  useEffect(() => {
    setScriptSegments(studio.script);
    setRoomTakeSession(createVoiceRoomSession(studio.script, studio.roomTakes));
    setRoomGuidanceDrafts(createVoiceRoomGuidanceDrafts(studio.script, studio.roomTakes));
  }, [studio.roomTakes, studio.script]);

  useEffect(() => {
    if (activeTab !== "team" || !hasCastMembers) return undefined;
    let cancelled = false;
    setVoiceCatalogLoading(true);
    setVoiceCatalogError("");
    void catalogClient.getVoiceCatalog(studio.projectId).then((catalog) => {
      if (cancelled) return;
      setVoiceCatalog(catalog.options);
    }).catch(() => {
      if (cancelled) return;
      setVoiceCatalog([]);
      setVoiceCatalogError("当前平台音色目录加载失败；已保存的音色不会被改动。");
    }).finally(() => {
      if (!cancelled) setVoiceCatalogLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [activeTab, catalogClient, hasCastMembers, studio.projectId, studio.providerLabel]);

  const refreshAfterVoiceMutation = useCallback(async (): Promise<void> => {
    try {
      await onRefresh();
    } catch {
      setGuidanceNotice("项目音色已更新，但详情刷新失败；重新进入声腔页面即可读取最新数据。");
    }
  }, [onRefresh]);

  const assignCatalogVoice = async (voiceId: string): Promise<void> => {
    if (!activeCast.id || voiceId === selectedVoiceId) return;
    setVoiceAssigning(true);
    setVoiceAssignmentNotice("正在写入音色选择…");
    setGuidanceNotice(`正在将 ${activeCast.name} 切换到所选系统音色…`);
    try {
      const result = await commandClient.assignCatalogVoice({
        kind: "assign_catalog_voice",
        projectId: studio.projectId,
        characterId: activeCast.id,
        voiceId,
        provider: studio.providerLabel,
      });
      setGuidanceNotice(result.message);
      setVoiceAssignmentNotice(result.message);
      if (result.status === "accepted") {
        setAssignedVoiceId(voiceId);
        await refreshAfterVoiceMutation();
      }
    } catch {
      const failure = "音色切换失败；已保存的项目音色保持不变。";
      setGuidanceNotice(failure);
      setVoiceAssignmentNotice(failure);
    } finally {
      setVoiceAssigning(false);
    }
  };

  // The command acknowledgement only proves that the job was accepted. The
  // durable task stream is the authority for completion, failure, and the
  // point at which refreshing the cast can reveal newly persisted voices.
  useEffect(() => {
    const taskId = voiceTeamTask.taskId;
    if (taskId === null || voiceTeamStream === null || voiceTeamStream.taskId !== taskId) return;
    if (handledVoiceTeamTerminalTaskRef.current === taskId) return;

    if (voiceTeamStream.status === "completed") {
      handledVoiceTeamTerminalTaskRef.current = taskId;
      const completedMessage = "配音团队已构建完成，正在刷新角色音色详情。";
      setVoiceTeamTask((current) => current.taskId === taskId
        ? { ...current, phase: "completed", message: completedMessage }
        : current);
      setGuidanceNotice(completedMessage);
      void refreshAfterVoiceMutation();
    } else if (voiceTeamStream.status === "failed") {
      handledVoiceTeamTerminalTaskRef.current = taskId;
      if (isCancelledVoiceTask(voiceTeamStream.stepId)) {
        const cancelledMessage = "配音团队构建已取消；现有角色音色保持不变。";
        setVoiceTeamTask((current) => current.taskId === taskId
          ? { ...current, phase: "cancelled", message: cancelledMessage }
          : current);
        setGuidanceNotice(cancelledMessage);
        return;
      }
      const failedMessage = voiceTeamFailureMessage(voiceTeamStream.error?.message
        ?? "配音团队构建失败；现有角色音色保持不变。");
      setVoiceTeamTask((current) => current.taskId === taskId
        ? { ...current, phase: "failed", message: failedMessage }
        : current);
      setGuidanceNotice(failedMessage);
    }
  }, [
    refreshAfterVoiceMutation,
    voiceTeamStream?.error?.message,
    voiceTeamStream?.stepId,
    voiceTeamStream?.status,
    voiceTeamStream?.taskId,
    voiceTeamTask.taskId,
  ]);

  // Script generation task stream completion handler.
  useEffect(() => {
    if (scriptTaskId === null || scriptStream === null || scriptStream.taskId !== scriptTaskId) return;
    if (scriptStream.status === "completed") {
      setScriptTaskId(null);
      setScriptState("generated");
      setGuidanceNotice("配音脚本生成完成，正在刷新脚本内容。");
      void onRefresh();
    } else if (scriptStream.status === "failed") {
      setScriptTaskId(null);
      setScriptState("ready");
      setGuidanceNotice(
        isCancelledVoiceTask(scriptStream.stepId)
          ? "配音脚本生成已取消；尚未保存的草稿保持不变。"
          : (scriptStream.error?.message ?? "配音脚本生成失败；请重试。"),
      );
    }
  }, [onRefresh, scriptStream?.error?.message, scriptStream?.status, scriptStream?.stepId, scriptStream?.taskId, scriptTaskId]);

  // Synthesis task stream completion handler.
  useEffect(() => {
    if (synthesisTaskId === null || synthesisStream === null || synthesisStream.taskId !== synthesisTaskId) return;
    if (synthesisStream.status === "completed") {
      setSynthesisTaskId(null);
      setScriptState("ready");
      setGuidanceNotice("语音合成完成，正在刷新音频状态。");
      void onRefresh();
    } else if (synthesisStream.status === "failed") {
      setSynthesisTaskId(null);
      setScriptState("ready");
      setGuidanceNotice(
        isCancelledVoiceTask(synthesisStream.stepId)
          ? "语音合成已取消；已生成音频保持不变。"
          : (synthesisStream.error?.message ?? "语音合成失败；请重试。"),
      );
    }
  }, [onRefresh, synthesisStream?.error?.message, synthesisStream?.status, synthesisStream?.stepId, synthesisStream?.taskId, synthesisTaskId]);

  const submitVoiceTeamBuild = async (rebuildCharacterIds?: readonly string[]): Promise<void> => {
    if (voiceTeamTaskIsActive) return;
    const requestedCount = rebuildCharacterIds?.length ?? 0;
    const submittingMessage = requestedCount > 0
      ? `正在提交 ${requestedCount} 位角色的音色重建任务…`
      : "正在提交配音团队构建任务…";
    setVoiceTeamTask({
      phase: "submitting",
      taskId: null,
      message: submittingMessage,
      requestedCount,
    });
    setGuidanceNotice(submittingMessage);

    try {
      const result = await commandClient.buildVoiceTeam({
        kind: "build_voice_team",
        projectId: studio.projectId,
        ...(rebuildCharacterIds === undefined ? {} : { rebuildCharacterIds }),
      });
      if (result.status !== "accepted") {
        setVoiceTeamTask({
          phase: "failed",
          taskId: null,
          message: voiceTeamFailureMessage(result.message),
          requestedCount,
        });
        setGuidanceNotice(voiceTeamFailureMessage(result.message));
        return;
      }
      if (!result.taskId) {
        // Compatibility with a pre-task-stream sidecar. Current engines always
        // return a task id; do not leave the user with an indeterminate card.
        const fallbackMessage = `${result.message} 引擎未返回任务编号，已刷新当前详情。`;
        setVoiceTeamTask({
          phase: "completed",
          taskId: null,
          message: fallbackMessage,
          requestedCount,
        });
        setGuidanceNotice(fallbackMessage);
        await refreshAfterVoiceMutation();
        return;
      }
      setVoiceTeamTask({
        phase: "submitted",
        taskId: result.taskId,
        message: result.message,
        requestedCount,
      });
      setGuidanceNotice(`${result.message} 正在读取实时进度。`);
    } catch {
      const failedMessage = "配音团队构建失败；现有角色音色保持不变。";
      setVoiceTeamTask({
        phase: "failed",
        taskId: null,
        message: failedMessage,
        requestedCount,
      });
      setGuidanceNotice(failedMessage);
    }
  };

  useEffect(() => {
    if (creationParameters === undefined) return;
    const nextDraft = createVoicePlatformSettingsDraft(studio, creationParameters, routingSettings);
    setVoiceSettingsDraft(nextDraft);
    setSavedVoiceSettingsDraft(nextDraft);
  }, [creationParameters, routingSettings, studio]);

  const voiceSettingsDirty = !voicePlatformSettingsEqual(
    voiceSettingsDraft,
    savedVoiceSettingsDraft,
  );

  const selectReferenceScript = async (file: File | null): Promise<void> => {
    if (file === null) {
      setReferenceScriptText("");
      setReferenceScriptName("");
      setReferenceStyleState("idle");
      setReferenceStyleSummary(
        "未上传；项目已有画像时会按强度复用。参考原文不会写入风格画像。",
      );
      return;
    }
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!["txt", "md", "srt", "vtt"].includes(extension)) {
      setReferenceScriptText("");
      setReferenceScriptName("");
      setReferenceStyleState("error");
      setReferenceStyleSummary("仅支持 TXT、Markdown、SRT 或 VTT 配音脚本。");
      return;
    }
    if (file.size > 1_048_576) {
      setReferenceScriptText("");
      setReferenceScriptName("");
      setReferenceStyleState("error");
      setReferenceStyleSummary("参考脚本超过 1 MB，请先裁剪为具有代表性的片段。");
      return;
    }
    let text = "";
    try {
      text = await file.text();
    } catch {
      setReferenceScriptText("");
      setReferenceScriptName("");
      setReferenceStyleState("error");
      setReferenceStyleSummary("无法读取该参考脚本，请检查文件编码后重试。");
      return;
    }
    if (text.trim().length < 160 || text.length > 200_000) {
      setReferenceScriptText("");
      setReferenceScriptName("");
      setReferenceStyleState("error");
      setReferenceStyleSummary("有效文本需介于 160–200,000 字符。");
      return;
    }
    setReferenceScriptText(text);
    setReferenceScriptName(file.name);
    setReferenceStyleState("loaded");
    setReferenceStyleSummary("已读取，待分析；参考原文不会写入风格画像。");
  };

  const analyzeReferenceStyle = async (): Promise<boolean> => {
    if (!referenceScriptText.trim()) return true;
    setReferenceStyleState("analyzing");
    try {
      const result = await commandClient.analyzeVoiceScriptStyle({
        kind: "analyze_voice_script_style",
        projectId: studio.projectId,
        sourceName: referenceScriptName || "参考配音脚本",
        referenceScriptText,
      });
      const data = result.data as {
        readonly profile?: {
          readonly analysis_mode?: string;
          readonly confidence?: number;
        };
      } | undefined;
      const confidence = data?.profile?.confidence;
      const mode = data?.profile?.analysis_mode === "hybrid" ? "LLM+统计" : "统计兜底";
      setReferenceStyleState("ready");
      setReferenceStyleSummary(
        `已应用抽象风格画像 · ${mode}${typeof confidence === "number" ? ` · 置信 ${Math.round(confidence * 100)}%` : ""}`,
      );
      setGuidanceNotice(result.message);
      return true;
    } catch {
      setReferenceStyleState("error");
      setReferenceStyleSummary("风格分析失败；已有画像和配音脚本保持不变。");
      return false;
    }
  };

  const generateScript = async () => {
    if (!lineage.sourceReady) {
      setGuidanceNotice(lineage.detail);
      return;
    }
    if (referenceScriptText.trim() && referenceStyleState !== "ready") {
      const styleReady = await analyzeReferenceStyle();
      if (!styleReady) return;
    }
    setScriptState("generating");
    setScriptTaskId(null);
    try {
      const result = await commandClient.generateVoiceScript({
        kind: "generate_voice_script",
        projectId: studio.projectId,
        chapterNumber: selectedChapter,
        provider: voiceSettingsDraft.provider,
        referenceStyleStrength,
      });
      setGuidanceNotice(
        result.taskId ? `${result.message}（任务 ${result.taskId}）` : result.message,
      );
      if (result.taskId) {
        setScriptTaskId(result.taskId);
      } else {
        setScriptState("ready");
      }
    } catch {
      setScriptState("ready");
      setGuidanceNotice("配音稿生成命令失败；请检查引擎连接后重试。");
    }
  };
  const synthesize = async () => {
    if (!lineage.synthesisAllowed) {
      setGuidanceNotice(lineage.detail);
      return;
    }
    if (!hasScriptSegments) {
      setGuidanceNotice("当前章节还没有配音脚本；请先生成脚本后再合成。");
      return;
    }
    setScriptState("synthesizing");
    setSynthesisTaskId(null);
    try {
      const segmentIds = scriptSegments.map((s) => s.id);
      const result = await commandClient.synthesizeVoice({
        kind: "synthesize_voice",
        projectId: studio.projectId,
        chapterNumber: selectedChapter,
        segmentIds,
        provider: voiceSettingsDraft.provider,
      });
      setGuidanceNotice(result.message);
      if (result.taskId) {
        setSynthesisTaskId(result.taskId);
      } else {
        setScriptState("ready");
      }
    } catch {
      setScriptState("ready");
      setGuidanceNotice("语音合成命令失败；请检查引擎连接后重试。");
    }
  };
  const runFullPipeline = async (
    automationMode: VoicePlatformSettingsDraft["automationMode"] = voiceSettingsDraft.automationMode,
  ) => {
    if (!lineage.sourceReady) {
      setGuidanceNotice(lineage.detail);
      return;
    }
    setScriptState("synthesizing");
    setSynthesisTaskId(null);
    try {
      const result = await commandClient.runFullVoicePipeline({
        kind: "full_voice_pipeline",
        projectId: studio.projectId,
        chapterNumber: selectedChapter,
        automationMode,
        provider: voiceSettingsDraft.provider,
      });
      setGuidanceNotice(result.message);
      if (result.taskId) {
        setSynthesisTaskId(result.taskId);
      } else {
        setScriptState("ready");
        if (result.status === "accepted") await onRefresh();
      }
    } catch {
      setScriptState("ready");
      setGuidanceNotice("完整配音流程提交失败；既有脚本与音频保持不变。");
    }
  };
  const ensureChapterAudio = (): HTMLAudioElement | null => {
    if (!studio.chapterAudioUrl) return null;
    if (chapterAudioRef.current === null) {
      const audio = new Audio(studio.chapterAudioUrl);
      audio.volume = chapterVolume;
      audio.addEventListener("timeupdate", () => {
        setChapterProgress(audio.currentTime);
      });
      audio.addEventListener("loadedmetadata", () => {
        setChapterDuration(Number.isFinite(audio.duration) ? audio.duration : 0);
      });
      audio.addEventListener("ended", () => {
        setChapterPlaying(false);
        setChapterProgress(0);
      });
      audio.addEventListener("error", () => {
        setChapterPlaying(false);
        setGuidanceNotice("章节主音轨加载失败；请重新装配后再试。");
      });
      chapterAudioRef.current = audio;
    }
    return chapterAudioRef.current;
  };
  const toggleChapterPlayback = async () => {
    const audio = ensureChapterAudio();
    if (audio === null) {
      setGuidanceNotice("全章音频尚未装配，请先到“后处理”合成或重装配。");
      return;
    }
    if (!audio.paused) {
      audio.pause();
      setChapterPlaying(false);
      return;
    }
    try {
      await audio.play();
      setChapterPlaying(true);
    } catch {
      setGuidanceNotice("系统未能开始播放章节主音轨；请检查输出设备。");
    }
  };
  const seekChapterAudio = (deltaSeconds: number) => {
    const audio = ensureChapterAudio();
    if (audio === null) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + deltaSeconds));
    setChapterProgress(audio.currentTime);
  };
  const seekChapterBoundary = (boundary: "start" | "end") => {
    const audio = ensureChapterAudio();
    if (audio === null) return;
    audio.currentTime = boundary === "start" ? 0 : Math.max(0, (audio.duration || 0) - 0.1);
    setChapterProgress(audio.currentTime);
  };
  const scheduleRoomOperation = async (
    segmentId: string,
    kind: "candidate" | "accept",
  ) => {
    if (!hasScriptSegments) {
      setGuidanceNotice("当前章节还没有可试听的配音片段；请先生成脚本。");
      return;
    }
    if (!lineage.synthesisAllowed) {
      setGuidanceNotice(lineage.detail);
      return;
    }
    if (kind === "candidate") {
      setRoomTakeSession((current) =>
        beginVoiceRoomCandidate(current, segmentId),
      );
      const segIndex = scriptSegments.findIndex((segment) => segment.id === segmentId);
      setGuidanceNotice(
        `正在为第 ${segIndex + 1} 段生成隔离试听；正式脚本与成品未变。`,
      );
      try {
        const result = await commandClient.previewVoiceSegment({
          kind: "preview_voice_segment",
          projectId: studio.projectId,
          chapterNumber: studio.chapterNumber,
          segmentIndex: activeSegment.segmentIndex,
          segmentOverride: buildVoiceSegmentOverride(
            activeSegment,
            roomGuidanceDrafts[segmentId],
          ),
        });
        if (result.status === "accepted" && result.takeId) {
          setRoomTakeSession((current) =>
            registerVoiceRoomCandidate(
              current,
              segmentId,
              result.takeId!,
              result.audioUrl,
            ),
          );
          setGuidanceNotice(
            `第 ${segIndex + 1} 段真实候选音轨已生成；试听后请选择采纳或丢弃。`,
          );
          await refreshAfterVoiceMutation();
        } else {
          setRoomTakeSession((current) =>
            discardVoiceRoomCandidate(current, segmentId),
          );
          setGuidanceNotice(result.message);
        }
      } catch {
        setRoomTakeSession((current) =>
          discardVoiceRoomCandidate(current, segmentId),
        );
        setGuidanceNotice("试听生成命令失败；请检查引擎连接后重试。");
      }
    } else {
      setRoomTakeSession((current) =>
        beginVoiceRoomAcceptance(current, segmentId),
      );
      setGuidanceNotice(
        `正在接受第 ${
          scriptSegments.findIndex((segment) => segment.id === segmentId) + 1
        } 段候选试听…`,
      );
      if (!activeRoomTake.takeId) {
        setRoomTakeSession((current) =>
          discardVoiceRoomCandidate(current, segmentId),
        );
        setGuidanceNotice("候选音轨缺少持久化版本号，请重新生成。");
        return;
      }
      try {
        const result = await commandClient.acceptVoiceTake({
          kind: "accept_voice_take",
          projectId: studio.projectId,
          chapterNumber: studio.chapterNumber,
          takeId: activeRoomTake.takeId,
        });
        if (result.status !== "accepted") {
          setRoomTakeSession((current) =>
            discardVoiceRoomCandidate(current, segmentId),
          );
          setGuidanceNotice(result.message);
          return;
        }
        setRoomTakeSession((current) =>
          completeVoiceRoomAcceptance(current, segmentId),
        );
        // The engine acknowledgement confirms the durable take; retain the
        // PySide next-step cue so an accepted audition never looks final.
        setGuidanceNotice("候选试听已接受；请到后处理装配章节主音轨。");
        await refreshAfterVoiceMutation();
      } catch {
        setRoomTakeSession((current) =>
          discardVoiceRoomCandidate(current, segmentId),
        );
        setGuidanceNotice("候选音轨采纳失败；正式产物保持不变。");
      }
    }
  };
  const discardActiveCandidate = async () => {
    if (!hasScriptSegments) return;
    candidateAudioRef.current?.pause();
    const takeId = activeRoomTake.takeId;
    if (takeId) {
      try {
        const result = await commandClient.rejectVoiceTake({
          kind: "reject_voice_take",
          projectId: studio.projectId,
          chapterNumber: selectedChapter,
          takeId,
        });
        if (result.status !== "accepted") {
          setGuidanceNotice(result.message);
          return;
        }
      } catch {
        setGuidanceNotice("舍弃试听失败；候选版本仍保留，正式版本未变。");
        return;
      }
    }
    setRoomTakeSession((current) =>
      discardVoiceRoomCandidate(current, activeSegment.id),
    );
    setGuidanceNotice(
      `已舍弃第 ${
        scriptSegments.findIndex(
          (segment) => segment.id === activeSegment.id,
        ) + 1
      } 段试听；正式版本未变。`,
    );
    await refreshAfterVoiceMutation();
  };
  const roomOperationInFlight =
    activeRoomTake.state === "generating" ||
    activeRoomTake.state === "accepting";
  const cancelActiveVoiceTask = async (): Promise<void> => {
    const task = voiceTeamTaskIsActive
      ? { id: voiceTeamTask.taskId, kind: "team" as const }
      : scriptState === "generating"
        ? { id: scriptTaskId, kind: "script" as const }
        : scriptState === "synthesizing"
          ? { id: synthesisTaskId, kind: "synthesis" as const }
          : { id: null, kind: null };
    const taskId = task.id;
    if (taskId === null) {
      setGuidanceNotice(
        roomOperationInFlight
          ? "当前片段试听尚未具备可取消的引擎任务；完成后可舍弃候选版本。"
          : "任务正在提交，尚未获得引擎任务号；请稍候再试。",
      );
      return;
    }
    try {
      const result = await commandClient.cancelJob({
        kind: "cancel_job",
        taskId,
        reason: "用户从声腔页面取消任务",
      });
      if (result.status === "accepted") {
        // `accepted` is emitted only after Engine has persisted the cancellation.
        // Project that authoritative command acknowledgement immediately; the
        // task stream separately covers cancellation recovered after a restart.
        if (task.kind === "team") {
          const message = "配音团队构建已取消；现有角色音色保持不变。";
          setVoiceTeamTask((current) => current.taskId === taskId
            ? { ...current, phase: "cancelled", taskId: null, message }
            : current);
          setGuidanceNotice(message);
        } else if (task.kind === "script") {
          setScriptTaskId(null);
          setScriptState("ready");
          setGuidanceNotice("配音脚本生成已取消；尚未保存的草稿保持不变。");
        } else {
          setSynthesisTaskId(null);
          setScriptState("ready");
          setGuidanceNotice("语音合成已取消；已生成音频保持不变。");
        }
        return;
      }
      setGuidanceNotice(result.message);
    } catch {
      setGuidanceNotice("取消请求未送达引擎；当前任务仍在执行。请检查连接后重试。");
    }
  };
  const activeRoomGuidanceLabel = activeRoomTake.hasDraftGuidance
    ? "待审指导"
    : "尚未保存本段的附加指导参数。";

  // Forward worker-active state to the host so the top-bar provider
  // dropdown can lock itself during script / synthesis / voice-team runs.
  // Placed after `roomOperationInFlight` so the dependency can read it
  // safely without tripping the block-scope pre-declaration check.
  useEffect(() => {
    if (onWorkerBusyChange === undefined) return;
    onWorkerBusyChange(
      voiceTeamTaskIsActive || scriptTaskIsActive || roomOperationInFlight,
    );
  }, [
    onWorkerBusyChange,
    roomOperationInFlight,
    scriptTaskIsActive,
    voiceTeamTaskIsActive,
  ]);

  return (
    <div
      className="voice-page"
      data-parity-state={parityState ?? undefined}
    >
      <section className="voice-frame">
        <div className="voice-tabs" role="tablist">
          {Object.entries(tabLabels).map(([id, label]) => (
            <button
              aria-selected={activeTab === id}
              className={activeTab === id ? "is-active" : ""}
              key={id}
              onClick={() => setActiveTab(id as VoiceStudioTabId)}
              role="tab"
              type="button"
            >
              {label}
            </button>
          ))}
          {/* Mirrors `_on_custom_tab_changed`: the 保存设置 button is only
              visible on the final 平台设置 tab (is_settings_tab). */}
          {activeTab === "settings" && (
            <button
              className="button button-primary voice-save"
              disabled={settingsSaved}
              onClick={() => {
                if (!voiceSettingsDirty) {
                  setGuidanceNotice("平台设置没有待保存的更改。");
                  return;
                }
                setSettingsSaved(true);
                void commandClient.saveSettings({
                  kind: "save_settings",
                  routes: voiceTextRoutesToCommands(voiceSettingsDraft.textRoutes, {
                    ttsScriptTemperature: voiceSettingsDraft.ttsScriptTemperature,
                    ttsReviewTemperature: voiceSettingsDraft.ttsReviewTemperature,
                    ttsNarratorTemperature: voiceSettingsDraft.ttsNarratorTemperature,
                    ttsSoundDesignTemperature: voiceSettingsDraft.ttsSoundDesignTemperature,
                  }),
                  creationParameters: {
                    ...voicePlatformSettingsToCreationParameters(voiceSettingsDraft),
                    "tts-voice-library-scope": voiceSettingsDraft.soundReuse === "application"
                      ? "global_with_names"
                      : voiceSettingsDraft.soundReuse === "off" ? "off" : "project_only",
                  },
                }).then((result) => {
                  if (result.persistence === "persisted") {
                    setSavedVoiceSettingsDraft(voiceSettingsDraft);
                  }
                  setGuidanceNotice(result.message);
                }).catch(() => {
                  setGuidanceNotice("平台设置保存失败；原项目配置未改变。");
                }).finally(() => setSettingsSaved(false));
              }}
              type="button"
            >
              {settingsSaved ? "保存中…" : "保存设置"}
            </button>
          )}
        </div>
        <aside
          aria-live="polite"
          className={`voice-lineage-banner is-${lineage.tone}`}
          role="status"
        >
          <strong>{lineage.title}</strong>
          <span>{lineage.detail}</span>
        </aside>
        {activeTab === "team" && (
          <TeamPanel
            activeCast={activeCast}
            activeCastApproved={activeCastApproved}
            assignCatalogVoice={assignCatalogVoice}
            castQuery={castQuery}
            commandClient={commandClient}
            hasCastMembers={hasCastMembers}
            onDialog={(dialog) => setDialog(dialog)}
            onGuidanceNotice={setGuidanceNotice}
            onParametersAppliedChange={setParametersApplied}
            onPerformanceOffsetsChange={setPerformanceOffsets}
            onPreviewingChange={setPreviewing}
            onResetPerformanceOffsets={() =>
              setPerformanceOffsets(performanceOffsetsForMember(activeCast))
            }
            onSetActiveCastId={setActiveCastId}
            onSetCastQuery={setCastQuery}
            parametersApplied={parametersApplied}
            performanceOffsets={performanceOffsets}
            previewCharacterVoice={previewCharacterVoice}
            previewing={previewing}
            refreshAfterVoiceMutation={refreshAfterVoiceMutation}
            selectedVoiceId={selectedVoiceId}
            studio={studio}
            submitVoiceTeamBuild={submitVoiceTeamBuild}
            visibleCast={visibleCast}
            voiceAssignmentNotice={voiceAssignmentNotice}
            voiceAssigning={voiceAssigning}
            voiceCatalogError={voiceCatalogError}
            voiceCatalogLoading={voiceCatalogLoading}
            voiceOptions={voiceOptions}
            voiceTeamTaskIsActive={voiceTeamTaskIsActive}
          />
        )}
        {activeTab === "script" && (
          <ScriptPanel
            activeScriptProgressDetail={activeScriptProgressDetail}
            activeScriptProgressPercent={activeScriptProgressPercent}
            activeScriptProgressStep={activeScriptProgressStep}
            activeSegment={activeSegment}
            changeChapter={changeChapter}
            commandClient={commandClient}
            dialogueSegmentCount={dialogueSegmentCount}
            generateScript={generateScript}
            hasScriptSegments={hasScriptSegments}
            narrationSegmentCount={narrationSegmentCount}
            onAnalyzeReferenceStyle={analyzeReferenceStyle}
            onActiveTab={setActiveTab}
            onDialog={(dialog) => setDialog(dialog)}
            onGuidanceNotice={setGuidanceNotice}
            onPostWorkspaceTab={setPostWorkspaceTab}
            onRefresh={onRefresh}
            onReferenceScriptFile={selectReferenceScript}
            onReferenceStyleStrength={setReferenceStyleStrength}
            onScriptSegmentsChange={setScriptSegments}
            onScriptSpeakerFilter={setScriptSpeakerFilter}
            onSetActiveSegmentId={setActiveSegmentId}
            scriptListRef={scriptListRef}
            scriptSegments={scriptSegments}
            scriptSpeakerFilter={scriptSpeakerFilter}
            scriptSpeakerLabels={scriptSpeakerLabels}
            scriptState={scriptState}
            scriptTaskIsActive={scriptTaskIsActive}
            selectedChapter={selectedChapter!}
            referenceScriptName={referenceScriptName}
            referenceStyleState={referenceStyleState}
            referenceStyleStrength={referenceStyleStrength}
            referenceStyleSummary={referenceStyleSummary}
            streamingAnomaly={streamingAnomaly}
            streamingScriptText={streamingScriptText}
            streamingSegments={streamingSegments}
            studio={studio}
            synthesize={synthesize}
            synthesizingSegmentIndex={synthesizingSegmentIndex}
            unresolvedSpeakerCount={unresolvedSpeakerCount}
            visibleScriptSegments={visibleScriptSegments}
          />
        )}
        {activeTab === "room" && (
          <RoomPanel
            activeRoomGuidanceLabel={activeRoomGuidanceLabel}
            activeRoomTake={activeRoomTake}
            activeSegment={activeSegment}
            candidateAudioRef={candidateAudioRef}
            changeChapter={changeChapter}
            chapterAudioRef={chapterAudioRef}
            chapterPlaying={chapterPlaying}
            chapterVolume={chapterVolume}
            discardActiveCandidate={discardActiveCandidate}
            hasScriptSegments={hasScriptSegments}
            onChapterVolume={setChapterVolume}
            onDialog={(dialog) => setDialog(dialog)}
            onGuidanceNotice={setGuidanceNotice}
            onPlayingSegmentId={setPlayingSegmentId}
            onRoomReferenceOpen={setRoomReferenceOpen}
            onSetActiveSegmentId={setActiveSegmentId}
            playingSegmentId={playingSegmentId}
            roomGuidanceDrafts={roomGuidanceDrafts}
            roomOperationInFlight={roomOperationInFlight}
            roomReferenceOpen={roomReferenceOpen}
            roomTakeSession={roomTakeSession}
            scheduleRoomOperation={scheduleRoomOperation}
            scriptSegments={scriptSegments}
            seekChapterAudio={seekChapterAudio}
            selectedChapter={selectedChapter!}
            studio={studio}
            toggleChapterPlayback={toggleChapterPlayback}
          />
        )}
        {activeTab === "post" && (
          <PostPanel
            changeChapter={changeChapter}
            chapterAudioRef={chapterAudioRef}
            chapterDuration={chapterDuration}
            chapterPlaying={chapterPlaying}
            chapterProgress={chapterProgress}
            chapterVolume={chapterVolume}
            commandClient={commandClient}
            exportPreset={exportPreset}
            followReading={followReading}
            hasScriptSegments={hasScriptSegments}
            onChapterVolume={setChapterVolume}
            onCleanupMode={setCleanupMode}
            onDialog={(dialog) => setDialog(dialog)}
            onExportPreset={setExportPreset}
            onFollowReading={setFollowReading}
            onGuidanceNotice={setGuidanceNotice}
            onPostDetailTab={setPostDetailTab}
            onPostWorkspaceTab={setPostWorkspaceTab}
            onRefresh={onRefresh}
            onVoiceSettingsDraft={(patch) => setVoiceSettingsDraft((current) => ({ ...current, ...patch }))}
            postDetailTab={postDetailTab}
            postWorkspaceTab={postWorkspaceTab}
            runFullPipeline={runFullPipeline}
            scriptSegments={scriptSegments}
            seekChapterAudio={seekChapterAudio}
            seekChapterBoundary={seekChapterBoundary}
            selectedChapter={selectedChapter!}
            soundLibraryBusy={soundLibraryBusy}
            studio={studio}
            synthesize={synthesize}
            toggleChapterPlayback={toggleChapterPlayback}
            voiceSettingsDraft={voiceSettingsDraft}
          />
        )}
        {activeTab === "settings" && (
          settingsPreparing ? (
            <section aria-live="polite" className="voice-platform-preparing">
              <span>正在准备平台设置…</span>
            </section>
          ) : (
            <VoicePlatformSettings
              draft={voiceSettingsDraft}
              modelCenterClient={modelCenterClient}
              modelCenterRefreshToken={modelCenterRefreshToken ?? 0}
              onChange={(patch) => {
                setVoiceSettingsDraft((current) => ({ ...current, ...patch }));
                setSettingsSaved(false);
              }}
              routingSettings={routingSettings}
              studio={studio}
            />
          )
        )}
      </section>
      <VoiceStudioStatusBar
        activeScriptProgressDetail={activeScriptProgressDetail}
        activeScriptProgressPercent={activeScriptProgressPercent}
        activeScriptProgressStep={activeScriptProgressStep}
        guidanceNotice={guidanceNotice}
        onCancelActiveTask={cancelActiveVoiceTask}
        roomOperationInFlight={roomOperationInFlight}
        scriptState={scriptState}
        scriptTaskIsActive={scriptTaskIsActive}
        voiceTeamProgressDetail={voiceTeamProgressDetail}
        voiceTeamProgressPercent={voiceTeamProgressPercent}
        voiceTeamTask={voiceTeamTask}
        voiceTeamTaskIsActive={voiceTeamTaskIsActive}
        voiceTeamTaskStatus={voiceTeamTaskStatus}
        voiceTeamTaskStep={voiceTeamTaskStep}
      />
      <VoiceStudioDialogs
        activeCast={activeCast}
        activeSegment={activeSegment}
        cleanupCategories={cleanupCategories}
        cleanupChapterNumbers={cleanupChapterNumbers}
        cleanupMode={cleanupMode}
        cloneReferenceMode={cloneReferenceMode}
        commandClient={commandClient}
        dialog={dialog}
        exportPreset={exportPreset}
        hasCastMembers={hasCastMembers}
        hasScriptSegments={hasScriptSegments}
        onCleanupCategories={setCleanupCategories}
        onCleanupChapterNumbers={setCleanupChapterNumbers}
        onClose={() => setDialog(null)}
        onDialog={setDialog}
        onGuidanceNotice={setGuidanceNotice}
        onRefresh={onRefresh}
        onRoomGuidanceDrafts={setRoomGuidanceDrafts}
        onScriptState={setScriptState}
        refreshAfterVoiceMutation={refreshAfterVoiceMutation}
        roomGuidanceDrafts={roomGuidanceDrafts}
        roomTakeSession={roomTakeSession}
        scriptSegments={scriptSegments}
        selectedChapter={selectedChapter!}
        studio={studio}
        submitVoiceTeamBuild={submitVoiceTeamBuild}
        streamClient={streamClient}
      />
    </div>
  );
}

import type { PageId, ProjectView, VoiceStudioView } from "@nimo/engine-contracts";

import { DropdownSelect } from "./DropdownSelect";
import { ThemeSwitcher } from "./ThemeSwitcher";
import type { WorkflowMode } from "./WorkflowComposer";
import {
  ttsProviderKeyFromLabel,
  ttsProviderOptions,
} from "../lib/voice-provider-options";

export interface TopBarControlsProps {
  readonly activePage: PageId;
  /** 章台顶栏 primary 动态标签（对标 coord.primary_action_label 四态）。 */
  readonly chapterStudioPrimaryLabel?: string;
  /** 案头当前选中 / 卷帧当前阅览的项目 —— 顶栏动作组分支依据（对标 page_binding selected/detail）。 */
  readonly focusedProject: ProjectView | null;
  readonly onNavigate: (page: PageId) => void;
  readonly onReadProject: (projectId: string) => void;
  readonly onImportSettings: () => void;
  readonly onExportSettings: () => void;
  readonly onRequestShortTemplateExport: () => void;
  readonly onSaveSettings: () => void;
  readonly onStatus: (message: string) => void;
  readonly projectCount: number;
  readonly settingsDirty: boolean;
  readonly settingsReady: boolean;
  readonly totalChapters: number;
  readonly totalWords: number;
  readonly voiceProjects: readonly ProjectView[];
  readonly voiceProjectId: string | null;
  readonly voiceStudio: VoiceStudioView | null;
  readonly onVoiceProjectChange: (projectId: string) => void;
  /** Switch the active TTS provider; mirrors PySide6 ProviderDropdown. */
  readonly onVoiceProviderChange: (provider: string) => void;
  /** Disabled while a script/synthesis run is in flight. */
  readonly voiceProviderLocked?: boolean;
  readonly workflowMode: WorkflowMode;
  readonly onWorkflowModeChange: (mode: WorkflowMode) => void;
}

export function TopBarControls({
  activePage,
  chapterStudioPrimaryLabel = "继续当前节点 →",
  focusedProject,
  onNavigate,
  onReadProject,
  onImportSettings,
  onExportSettings,
  onRequestShortTemplateExport,
  onSaveSettings,
  onStatus,
  projectCount,
  settingsDirty,
  settingsReady,
  totalChapters,
  totalWords,
  voiceProjects,
  voiceProjectId,
  voiceStudio,
  onVoiceProjectChange,
  onVoiceProviderChange,
  voiceProviderLocked = false,
  workflowMode,
  onWorkflowModeChange,
}: TopBarControlsProps) {
  if (activePage === "voice_studio") {
    const cast = voiceStudio?.cast ?? [];
    const configuredCount = cast.filter(
      (member) => member.statusLabel === "已配",
    ).length;
    const pendingCount = cast.filter(
      (member) => member.statusLabel === "待确认",
    ).length;
    const expiredCount = cast.filter(
      (member) => member.statusLabel === "过期",
    ).length;
    // `studio.providerLabel` carries the engine's display label; reverse it
    // to a stable provider key so the dropdown reflects the persisted
    // selection across reloads and parity snapshots.
    const providerCatalog = voiceStudio?.providerCatalog;
    const selectedProvider = ttsProviderKeyFromLabel(
      voiceStudio?.providerLabel,
      providerCatalog,
      "",
    );
    const providerOptions = ttsProviderOptions(providerCatalog, selectedProvider);
    return (
      <div className="topbar-voice-summary" aria-label="配音工作室概览">
        {/* 标题对标 _refresh_voice_metrics：数据加载后首项为「配音角色」（初始占位「角色」会被覆盖）。 */}
        <span>
          <small>配音角色</small>
          <strong>{cast.length}</strong>
        </span>
        <span>
          <small>已配</small>
          <strong>{configuredCount}</strong>
        </span>
        <span>
          <small>待确认</small>
          <strong>{pendingCount}</strong>
        </span>
        <span>
          <small>过期</small>
          <strong>{expiredCount}</strong>
        </span>
        <DropdownSelect
          ariaLabel="TTS 配音平台"
          className="topbar-provider-dropdown"
          disabled={voiceProviderLocked || voiceStudio === null}
          onChange={(value) => {
            if (voiceProviderLocked) {
              onStatus("当前任务运行中，请完成或取消后再切换平台");
              return;
            }
            if (value !== selectedProvider) onVoiceProviderChange(value);
          }}
          options={providerOptions}
          value={selectedProvider}
        />
        <DropdownSelect
          ariaLabel="当前配音项目"
          className="topbar-dropdown"
          disabled={voiceProjects.length === 0}
          onChange={onVoiceProjectChange}
          options={voiceProjects.length === 0
            ? [{ value: "", label: "未选择项目" }]
            : voiceProjects.map((project) => ({ value: project.id, label: project.title }))}
          value={voiceProjectId ?? ""}
        />
      </div>
    );
  }

  const meta = `项目 ${projectCount} · 已归档 ${totalChapters} 章 · 字数 ${totalWords.toLocaleString("en-US")}`;
  const feedback = (message: string) => () => onStatus(message);
  const controls =
    activePage === "dashboard" ? (
      // 对标 PySide6 page_binding dashboard 顶栏：按选中项目 mode + init_resume_available 分支。
      focusedProject === null ? (
        <>
          <button
            className="topbar-action is-primary"
            onClick={() => onNavigate("workflow")}
            type="button"
          >
            起笔 →
          </button>
          <button
            className="topbar-action"
            onClick={() => onNavigate("settings")}
            type="button"
          >
            去火候
          </button>
        </>
      ) : focusedProject.mode === "long" && focusedProject.initResumeAvailable ? (
        <>
          <button
            className="topbar-action is-primary"
            onClick={() => {
              onWorkflowModeChange("long");
              onNavigate("workflow");
            }}
            type="button"
          >
            继续立项 →
          </button>
          <button
            className="topbar-action"
            onClick={() => onReadProject(focusedProject.id)}
            type="button"
          >
            阅卷
          </button>
        </>
      ) : focusedProject.mode === "long" ? (
        <>
          <button
            className="topbar-action is-primary"
            onClick={() => onNavigate("chapter_studio")}
            type="button"
          >
            续此卷 →
          </button>
          <button
            className="topbar-action"
            onClick={() => onReadProject(focusedProject.id)}
            type="button"
          >
            阅卷
          </button>
        </>
      ) : (
        <>
          <button
            className="topbar-action is-primary"
            onClick={() => onReadProject(focusedProject.id)}
            type="button"
          >
            阅卷 →
          </button>
          <button
            className="topbar-action"
            onClick={() => onNavigate("workflow")}
            type="button"
          >
            去机杼
          </button>
        </>
      )
    ) : activePage === "projects" ? (
      // 对标 PySide6 page_binding projects 顶栏：按当前阅览项目 mode + init_resume_available 分支。
      focusedProject !== null &&
      focusedProject.mode === "long" &&
      focusedProject.initResumeAvailable ? (
        <>
          <button
            className="topbar-action is-primary"
            onClick={() => {
              onWorkflowModeChange("long");
              onNavigate("workflow");
            }}
            type="button"
          >
            继续立项 →
          </button>
          <button
            className="topbar-action"
            onClick={() => onNavigate("dashboard")}
            type="button"
          >
            返回案头
          </button>
        </>
      ) : focusedProject !== null && focusedProject.mode === "long" ? (
        <>
          <button
            className="topbar-action is-primary"
            onClick={() => onNavigate("chapter_studio")}
            type="button"
          >
            续此卷 →
          </button>
          <button
            className="topbar-action"
            onClick={() => onNavigate("dashboard")}
            type="button"
          >
            返回案头
          </button>
        </>
      ) : (
        <>
          <button
            className="topbar-action is-primary"
            onClick={() => onNavigate("dashboard")}
            type="button"
          >
            返回案头
          </button>
          <button
            className="topbar-action"
            onClick={() => onNavigate("workflow")}
            type="button"
          >
            去机杼
          </button>
        </>
      )
    ) : activePage === "workflow" ? (
      <>
        <button
          className="topbar-action is-primary"
          onClick={
            workflowMode === "short"
              ? onRequestShortTemplateExport
              : feedback("长篇模板已准备，等待引擎接入后导出")
          }
          type="button"
        >
          {workflowMode === "short" ? "导出短篇模板" : "导出长篇模板"}
        </button>
        {workflowMode === "short" ? (
          <button
            className="topbar-action"
            onClick={() => {
              onWorkflowModeChange("long");
              onStatus("已切到长篇初始化");
            }}
            type="button"
          >
            切到长篇
          </button>
        ) : (
          // 对标 PySide6 page_binding long-init 顶栏：第二按钮为「去章台 →」
          // （switch_page chapter_studio）；短/长切换由页内模式 Tab 承担，
          // LongPanel 的 SubModeBar 已隐藏，故顶栏不再有「切到短篇」。
          <button
            className="topbar-action"
            onClick={() => onNavigate("chapter_studio")}
            type="button"
          >
            去章台 →
          </button>
        )}
      </>
    ) : activePage === "settings" ? (
      <>
        <button
          className="topbar-action is-primary"
          disabled={!settingsReady}
          onClick={onSaveSettings}
          type="button"
        >
          {settingsDirty ? "保存设置 · 有变更" : "保存设置"}
        </button>
        <button
          className="topbar-action"
          disabled={!settingsReady}
          onClick={onImportSettings}
          type="button"
        >
          导入配置文件
        </button>
        <button
          className="topbar-action"
          disabled={!settingsReady}
          onClick={onExportSettings}
          type="button"
        >
          导出配置文件
        </button>
      </>
    ) : activePage === "chapter_studio" ? (
      <>
        <button
          className="topbar-action is-primary"
          onClick={feedback("章台已聚焦当前节点")}
          type="button"
        >
          {chapterStudioPrimaryLabel}
        </button>
        <button
          className="topbar-action"
          onClick={feedback("项目目录由本地引擎授权后打开")}
          type="button"
        >
          打开文件夹
        </button>
      </>
    ) : null;
  return (
    <div className="topbar-controls">
      <span className="topbar-meta">{meta}</span>
      {controls}
      <ThemeSwitcher />
    </div>
  );
}

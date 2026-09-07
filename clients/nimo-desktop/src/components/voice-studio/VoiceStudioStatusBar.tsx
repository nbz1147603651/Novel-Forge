interface VoiceStudioStatusBarProps {
  readonly activeScriptProgressDetail: string;
  readonly activeScriptProgressPercent: number | undefined;
  readonly activeScriptProgressStep: string;
  readonly guidanceNotice: string;
  readonly onCancelActiveTask: () => Promise<void>;
  readonly roomOperationInFlight: boolean;
  readonly scriptState: string;
  readonly scriptTaskIsActive: boolean;
  readonly voiceTeamProgressDetail: string;
  readonly voiceTeamProgressPercent: number | undefined;
  readonly voiceTeamTask: { readonly phase: string; readonly taskId: string | null; readonly message: string; readonly requestedCount: number };
  readonly voiceTeamTaskIsActive: boolean;
  readonly voiceTeamTaskStatus: string;
  readonly voiceTeamTaskStep: string;
}

/**
 * 声腔底部状态栏（mirrors PySide6 voiceStudioStatusBar）：
 * 操作反馈条 + 团队/脚本/合成任务进度 + 取消动作。
 * 从 VoiceStudioPage 提取，任务状态与取消回调通过 props 注入。
 */
export function VoiceStudioStatusBar({
  activeScriptProgressDetail,
  activeScriptProgressPercent,
  activeScriptProgressStep,
  guidanceNotice,
  onCancelActiveTask,
  roomOperationInFlight,
  scriptState,
  scriptTaskIsActive,
  voiceTeamProgressDetail,
  voiceTeamProgressPercent,
  voiceTeamTask,
  voiceTeamTaskIsActive,
  voiceTeamTaskStatus,
  voiceTeamTaskStep,
}: VoiceStudioStatusBarProps) {
  return (
    <div className="voice-status-stack">
      {guidanceNotice && (
        <aside className="voice-action-feedback" aria-live="polite" role="status">
          <strong>操作反馈</strong>
          <span>{guidanceNotice}</span>
        </aside>
      )}
      <footer className="voice-status" aria-live="polite" role="status">
        <div className="voice-status-content">
          {voiceTeamTask.phase !== "idle" ? (
            <>
              <span className={`voice-status-badge is-${voiceTeamTask.phase === "failed" ? "danger" : voiceTeamTask.phase === "completed" ? "success" : "warning"}`}>
                {voiceTeamTaskStatus}
              </span>
              <span className="voice-status-step">{voiceTeamTaskStep}</span>
              {voiceTeamTask.phase === "submitted" && (
                <>
                  <progress
                    aria-label="配音团队构建进度"
                    aria-valuetext={voiceTeamProgressDetail}
                    className="voice-status-progress"
                    max={100}
                    value={voiceTeamProgressPercent}
                  />
                  <span className="voice-status-detail">{voiceTeamProgressDetail}</span>
                </>
              )}
            </>
          ) : scriptTaskIsActive ? (
            <>
              <span className="voice-status-badge is-warning">
                {scriptState === "synthesizing" ? "音频合成" : "脚本生成"}
              </span>
              <span className="voice-status-step">{activeScriptProgressStep}</span>
              <progress
                aria-label={scriptState === "synthesizing" ? "底部音频合成进度" : "底部配音脚本生成进度"}
                aria-valuetext={activeScriptProgressDetail}
                className="voice-status-progress"
                max={100}
                value={activeScriptProgressPercent}
              />
              <span className="voice-status-detail">{activeScriptProgressDetail}</span>
            </>
          ) : (
            <span>进度待命</span>
          )}
        </div>
        <div className="voice-status-actions">
          {(scriptState === "synthesizing" || scriptState === "generating" || roomOperationInFlight || voiceTeamTaskIsActive) && (
            <span aria-hidden="true" className="voice-status-activity" />
          )}
          <button
            className="button button-quiet"
            disabled={
              scriptState !== "synthesizing" && scriptState !== "generating" && !roomOperationInFlight && !voiceTeamTaskIsActive
            }
            onClick={() => { void onCancelActiveTask(); }}
            type="button"
          >
            取消
          </button>
        </div>
      </footer>
    </div>
  );
}

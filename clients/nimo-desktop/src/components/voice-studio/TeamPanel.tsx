import type {
  EngineCommandClient,
  VoiceCatalogOptionView,
  VoiceStudioView,
} from "@nimo/engine-contracts";

import { VoiceDetailFacts } from "./VoiceDetailFacts";
import { VoiceEmptyState } from "./VoiceEmptyState";
import { VoiceSlider } from "./VoiceSlider";

export type VoiceTeamDialog =
  | "rebuild"
  | "design"
  | "preview"
  | "clone";

interface TeamPanelProps {
  readonly activeCast: VoiceStudioView["cast"][number] | undefined;
  readonly activeCastApproved: boolean;
  readonly assignCatalogVoice: (voiceId: string) => Promise<void>;
  readonly castQuery: string;
  readonly commandClient: EngineCommandClient;
  readonly hasCastMembers: boolean;
  readonly onDialog: (dialog: VoiceTeamDialog) => void;
  readonly onGuidanceNotice: (message: string) => void;
  readonly onParametersAppliedChange: (applied: boolean) => void;
  readonly onPerformanceOffsetsChange: (
    offsets: Readonly<{ readonly speed: number; readonly pitch: number; readonly volume: number }>,
  ) => void;
  readonly onPreviewingChange: (previewing: boolean) => void;
  readonly onResetPerformanceOffsets: () => void;
  readonly onSetActiveCastId: (castId: string) => void;
  readonly onSetCastQuery: (query: string) => void;
  readonly parametersApplied: boolean;
  readonly performanceOffsets: Readonly<{ readonly speed: number; readonly pitch: number; readonly volume: number }>;
  readonly previewCharacterVoice: () => Promise<void>;
  readonly previewing: boolean;
  readonly refreshAfterVoiceMutation: () => Promise<void>;
  readonly selectedVoiceId: string;
  readonly studio: VoiceStudioView;
  readonly submitVoiceTeamBuild: () => Promise<void>;
  readonly visibleCast: readonly VoiceStudioView["cast"][number][];
  readonly voiceAssignmentNotice: string;
  readonly voiceAssigning: boolean;
  readonly voiceCatalogError: string;
  readonly voiceCatalogLoading: boolean;
  readonly voiceOptions: readonly VoiceCatalogOptionView[];
  readonly voiceTeamTaskIsActive: boolean;
}

/**
 * 声腔「团队」页签：角色清单 + 音色详情/试听/表达微调/团队确认。
 * 从 VoiceStudioPage 提取，全部依赖通过 props 注入，主页面仅保留状态编排。
 */
export function TeamPanel({
  activeCast,
  activeCastApproved,
  assignCatalogVoice,
  castQuery,
  commandClient,
  hasCastMembers,
  onDialog,
  onGuidanceNotice,
  onParametersAppliedChange,
  onPerformanceOffsetsChange,
  onPreviewingChange,
  onResetPerformanceOffsets,
  onSetActiveCastId,
  onSetCastQuery,
  parametersApplied,
  performanceOffsets,
  previewCharacterVoice,
  previewing,
  refreshAfterVoiceMutation,
  selectedVoiceId,
  studio,
  submitVoiceTeamBuild,
  visibleCast,
  voiceAssignmentNotice,
  voiceAssigning,
  voiceCatalogError,
  voiceCatalogLoading,
  voiceOptions,
  voiceTeamTaskIsActive,
}: TeamPanelProps) {
  const isNarrator = activeCast?.id === "narrator";
  const designActionLabel = isNarrator ? "重新生成旁白音色" : "AI 角色设计";
  const designActionTitle = isNarrator
    ? "根据当前大纲、故事圣经与风格档案重新生成作品级旁白音色"
    : "使用角色画像和编辑声纹生成可复用专属音色；部分平台会对试听文本计费";

  return (
    <div className="voice-team">
      <aside className="voice-cast">
        <div className="voice-cast-heading">
          <h2>配音角色</h2>
          <p className="voice-cast-strategy">
            先复用音色库；没有可靠候选时再由 AI 设计。组建完成后自动准备每位角色的试听。
          </p>
        </div>
        <div className="voice-search-wrapper">
          <input
            aria-label="搜索配音角色"
            onChange={(event) => onSetCastQuery(event.target.value)}
            placeholder="搜索旁白、角色、定位或音色状态"
            value={castQuery}
          />
          {castQuery && (
            <button
              aria-label="清除搜索"
              className="voice-search-clear"
              onClick={() => onSetCastQuery("")}
              type="button"
            >
              ×
            </button>
          )}
        </div>
        <div className="voice-cast-list">
          {visibleCast.length > 0 ? (
            visibleCast.map((member) => (
              <button
                className={
                  activeCast?.id === member.id ? "is-active" : ""
                }
                key={member.id}
                onClick={() => {
                  onSetActiveCastId(member.id);
                  onPreviewingChange(false);
                  onPerformanceOffsetsChange({
                    speed: 0,
                    pitch: 0,
                    volume: 0,
                  });
                }}
                type="button"
              >
                <div className="voice-cast-copy">
                  <strong>{member.name}</strong>
                  <small>
                    {member.role || "未标注定位"} · {member.voiceSourceLabel || "系统"} · {member.matchSummary || "匹配待评估"} · {member.statusLabel}
                  </small>
                </div>
                <span
                  aria-label={`音色状态：${member.statusLabel}`}
                  className={`voice-status-badge ${
                    ["已配", "已配置", "已就绪"].includes(member.statusLabel)
                      ? "is-configured"
                      : "is-pending"
                  }`}
                >
                  {member.statusLabel}
                </span>
              </button>
            ))
          ) : (
            <p className="voice-empty-result">没有匹配的角色</p>
          )}
        </div>
        <div className="voice-team-actions voice-team-primary-actions">
          <button
            className="button button-primary"
            disabled={voiceTeamTaskIsActive}
            onClick={() => {
              if (hasCastMembers) {
                onDialog("rebuild");
                return;
              }
              void submitVoiceTeamBuild();
            }}
            type="button"
          >
            {voiceTeamTaskIsActive
              ? "正在组建并准备试听…"
              : hasCastMembers ? "重新组建并准备试听" : "自动组建并准备试听"}
          </button>
          {hasCastMembers && (
            <button
              className="button button-secondary"
              disabled={studio.teamConfirmed}
              onClick={() => {
                void commandClient.confirmVoiceTeam({
                  kind: "confirm_voice_team",
                  projectId: studio.projectId,
                }).then(async (result) => {
                  onGuidanceNotice(result.message);
                  if (result.status === "accepted") {
                    await refreshAfterVoiceMutation();
                  }
                }).catch(() => {
                  onGuidanceNotice("团队确认失败；请先处理未批准或失效的音色。");
                });
              }}
              type="button"
            >
              {studio.teamConfirmed ? "团队已确认" : "确认团队"}
            </button>
          )}
        </div>
      </aside>
      {hasCastMembers && activeCast !== undefined ? (
        <article className="voice-detail">
          <p className="voice-capability-hint">
            {studio.providerLabel} 可用能力 · 语音合成 / 音色目录 / 特征设计 / 参考音频克隆 / 停顿与气息 / 发音字典 / 原生情绪 / 方言增强 / 声音效果器
          </p>
          <header className="voice-detail-heading">
            <div>
              <span className="section-kicker">音色详情</span>
              <h2>{activeCast.name}</h2>
            </div>
            <div className="voice-detail-heading-actions">
              <span className="voice-match-badge">{activeCast.matchSummary || "匹配待评估"}</span>
              <button
                className="button button-quiet voice-dossier-toggle"
                onClick={() => onDialog("design")}
                type="button"
              >
                {`声纹档案 · ${7 + (activeCast.detailFacts?.length ?? 0)}项`}
              </button>
            </div>
          </header>
          <div className="voice-profile">
            <div>
              <strong>{activeCast.voiceLabel || "待分配音色"}</strong>
              <p>{activeCast.description || "暂未生成角色音色说明。"}</p>
            </div>
            <button
              className="button button-quiet"
              data-testid={isNarrator ? "rebuild-narrator-voice" : undefined}
              onClick={() => onDialog("design")}
              type="button"
            >
              {isNarrator ? "重新生成旁白音色" : "编辑声纹"}
            </button>
          </div>
          <div className="voice-detail-workbench">
            <div className="voice-detail-main">
              <section className="voice-preview-strip">
                <div>
                  <strong>角色试听</strong>
                  <small>{previewing ? "正在按当前参数生成试听" : "尚未生成试听 · 将按当前参数缓存"}</small>
                </div>
                <select aria-label="角色试听文案" defaultValue="identity">
                  <option value="identity">常规台词</option>
                  <option value="short">短句</option>
                  <option value="emotion">情绪台词</option>
                  <option value="urgent">紧急台词</option>
                </select>
                <button
                  className="button button-primary"
                  onClick={() => void previewCharacterVoice()}
                  type="button"
                >
                  {previewing ? "停止试听" : "生成试听"}
                </button>
                <button
                  className="button button-secondary"
                  onClick={() => onDialog("preview")}
                  type="button"
                >
                  候选 A/B 对比
                </button>
              </section>
              {previewing && (
                <div
                  className="voice-preview-wave"
                  aria-label="试听波形"
                >
                  <i />
                  <i />
                  <i />
                  <i />
                  <i />
                  <span>正在试听当前音色与参数</span>
                </div>
              )}
              <label className="voice-select">
                <span>
                  选择音色
                  <small>
                    {voiceCatalogLoading
                      ? "正在读取当前平台目录…"
                      : "为当前角色分配 TTS 系统音色；切换后必须重新试听并确认团队"}
                  </small>
                </span>
                <select
                  aria-label="当前角色音色"
                  disabled={voiceCatalogLoading || voiceAssigning || voiceOptions.length === 0}
                  onChange={(event) => void assignCatalogVoice(event.target.value)}
                  title={
                    voiceCatalogError
                    || "选择后将持久化到项目，旧试听缓存会失效。"
                  }
                  value={selectedVoiceId}
                >
                  {!selectedVoiceId && <option value="">尚未分配</option>}
                  {voiceOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}{option.description ? ` · ${option.description}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              {(voiceCatalogError || voiceAssignmentNotice) && (
                <p className="voice-catalog-error" role="status">
                  {voiceCatalogError || voiceAssignmentNotice}
                </p>
              )}
              <section className="voice-parameter-card">
                <div className="voice-parameter-heading">
                  <strong>表达微调</strong>
                  <div className="voice-parameter-heading-actions">
                    <button
                      className="button button-quiet"
                      onClick={() => {
                        onResetPerformanceOffsets();
                        onParametersAppliedChange(false);
                      }}
                      type="button"
                    >
                      恢复自动
                    </button>
                    <button
                      className="button button-secondary"
                      onClick={() => {
                        void commandClient.updateVoicePerformance({
                          kind: "update_voice_performance",
                          projectId: studio.projectId,
                          characterId: activeCast.id,
                          speedOffset: performanceOffsets.speed / 100,
                          pitchOffset: performanceOffsets.pitch,
                          volumeOffset: performanceOffsets.volume / 100,
                        }).then(async (result) => {
                          onParametersAppliedChange(result.status === "accepted");
                          onGuidanceNotice(result.message);
                          if (result.status === "accepted") {
                            await refreshAfterVoiceMutation();
                          }
                        }).catch(() => {
                          onParametersAppliedChange(false);
                          onGuidanceNotice("表达参数保存失败；试听与正式合成仍使用原项目值。");
                        });
                      }}
                      type="button"
                    >
                      {parametersApplied ? "参数已保存" : "保存表演参数"}
                    </button>
                  </div>
                </div>
                <p>{activeCast.performancePolicyLabel || "自动策略会保持角色声纹稳定；只有拖动过的字段会转为人工覆盖。"}</p>
                <div className="voice-sliders">
                  <VoiceSlider label="语速" max={50} onChange={(speed) => { onPerformanceOffsetsChange({ ...performanceOffsets, speed }); onParametersAppliedChange(false); }} suffix="×" tooltip="最终语速倍率；试听和正式合成都会使用此值" value={performanceOffsets.speed} />
                  <VoiceSlider label="音调" max={12} onChange={(pitch) => { onPerformanceOffsetsChange({ ...performanceOffsets, pitch }); onParametersAppliedChange(false); }} suffix=" st" tooltip="音调半音偏移；供应商不支持时由本地音频处理补齐" value={performanceOffsets.pitch} />
                  <VoiceSlider label="音量" max={50} onChange={(volume) => { onPerformanceOffsetsChange({ ...performanceOffsets, volume }); onParametersAppliedChange(false); }} suffix="×" tooltip="最终音量倍率；供应商不支持时由本地音频处理补齐" value={performanceOffsets.volume} />
                </div>
              </section>
              <section className="voice-main-evidence">
                <header>
                  <div>
                    <span className="section-kicker">声纹依据</span>
                    <strong>当前参数将用于试听与正式合成</strong>
                  </div>
                  <button
                    className="button button-quiet"
                    onClick={() => onDialog("design")}
                    type="button"
                  >
                    查看完整档案
                  </button>
                </header>
                <div className="voice-evidence-grid">
                  {(activeCast.detailFacts?.length ?? 0) > 0 ? (
                    activeCast.detailFacts!.slice(0, 4).map((fact) => (
                      <div key={fact.label}>
                        <span>{fact.label}</span>
                        <strong title={fact.value}>{fact.value}</strong>
                      </div>
                    ))
                  ) : (
                    <>
                      <div>
                        <span>角色定位</span>
                        <strong>{activeCast.role || "未标注定位"}</strong>
                      </div>
                      <div>
                        <span>音色来源</span>
                        <strong>{activeCast.voiceSourceLabel || "待分配"}</strong>
                      </div>
                    </>
                  )}
                </div>
                <p
                  className="voice-evidence-summary"
                  title={activeCast.designBrief
                    || activeCast.performancePolicyLabel
                    || "当前仍采用自动表达策略；拖动上方参数后可保存为角色专属覆盖。"}
                >
                  {activeCast.designBrief
                    || activeCast.performancePolicyLabel
                    || "当前仍采用自动表达策略；拖动上方参数后可保存为角色专属覆盖。"}
                </p>
              </section>
            </div>
            <aside aria-label="当前角色声音档案" className="voice-detail-sidebar">
              <section className="voice-sidebar-card voice-sidebar-facts">
                <div className="voice-sidebar-heading">
                  <span className="section-kicker">声音档案</span>
                  <span
                    className={`voice-sidebar-status ${activeCastApproved ? "is-configured" : "is-pending"}`}
                  >
                    {activeCast.statusLabel}
                  </span>
                </div>
                <VoiceDetailFacts
                  expanded={false}
                  member={activeCast}
                  providerLabel={studio.providerLabel}
                />
              </section>
              <section className="voice-sidebar-card voice-sidebar-next-step" aria-live="polite">
                <div className="voice-sidebar-heading">
                  <div>
                    <span className="section-kicker">工作进度</span>
                    <strong>{activeCast.name} 的下一步</strong>
                  </div>
                </div>
                <ol className="voice-next-step-list">
                  <li className={selectedVoiceId ? "is-complete" : ""}>
                    <span>1</span>
                    <div>
                      <strong>{selectedVoiceId ? "音色已分配" : "等待分配音色"}</strong>
                      <small>{activeCast.voiceSourceLabel || "从系统音色库或角色设计开始"}</small>
                    </div>
                  </li>
                  <li className={previewing ? "is-active" : ""}>
                    <span>2</span>
                    <div>
                      <strong>{previewing ? "正在生成试听" : "生成试听确认效果"}</strong>
                      <small>{previewing ? "将按当前参数缓存试听" : "确认语气后再进入团队确认"}</small>
                    </div>
                  </li>
                  <li className={studio.teamConfirmed ? "is-complete" : ""}>
                    <span>3</span>
                    <div>
                      <strong>{studio.teamConfirmed ? "配音团队已确认" : "确认配音团队"}</strong>
                      <small>{studio.teamConfirmed ? "可继续生成脚本" : "团队确认后可进入配音脚本"}</small>
                    </div>
                  </li>
                </ol>
                <div className="voice-sidebar-actions">
                  {!isNarrator && (
                    <button
                      className="button button-secondary"
                      onClick={() => {
                        onDialog("clone");
                      }}
                      title="当前模型决定使用本地参考音频或供应商文件 ID"
                      type="button"
                    >
                      上传参考音频
                    </button>
                  )}
                  <button
                    aria-label={designActionLabel}
                    className="button button-secondary"
                    onClick={() => onDialog("design")}
                    title={designActionTitle}
                    type="button"
                  >
                    {designActionLabel}
                  </button>
                  {!activeCastApproved && (
                    <button
                      className="button button-secondary"
                      onClick={() => {
                        void commandClient.approveCharacterVoice({
                          kind: "approve_character_voice",
                          projectId: studio.projectId,
                          characterId: activeCast.id,
                        }).then(async (result) => {
                          onGuidanceNotice(result.message);
                          if (result.status === "accepted") {
                            await refreshAfterVoiceMutation();
                          }
                        }).catch(() => onGuidanceNotice("角色音色批准失败；原状态保持不变。"));
                      }}
                      type="button"
                    >
                      批准音色
                    </button>
                  )}
                </div>
              </section>
            </aside>
          </div>
        </article>
      ) : (
        <VoiceEmptyState
          description="当前项目还没有可编辑的角色音色。自动组建完成后，角色、音色和试听控件会显示在这里。"
          title="尚未建立配音团队"
        />
      )}
    </div>
  );
}

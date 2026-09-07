import { useState } from "react";

import type { VoiceCastMemberView, VoiceCommandResult } from "@nimo/engine-contracts";

import { canStartVoiceDesign, createVoiceDesignDraft, defaultVoiceDesignBrief, startVoiceDesign, updateVoiceDesignDraft } from "../lib/voice-design-session";
import { OverlaySurface } from "./OverlaySurface";

export function VoiceDesignDialog({ member, onClose, onSubmit, providerLabel }: { readonly member: VoiceCastMemberView; readonly onClose: () => void; readonly onSubmit: (brief: string) => Promise<VoiceCommandResult>; readonly providerLabel: string }) {
  const [draft, setDraft] = useState(() => createVoiceDesignDraft(member));
  const [errorMessage, setErrorMessage] = useState("");
  const isNarrator = member.id === "narrator";
  const matchReasons = member.matchReasons?.filter(Boolean) ?? [];
  const auditionWarnings = member.auditionWarnings?.filter(Boolean) ?? [];
  const detailFacts = member.detailFacts?.filter(
    (fact) => fact.label.trim() && fact.value.trim(),
  ) ?? [];

  const update = (patch: Partial<Pick<typeof draft, "brief">>) => setDraft((current) => updateVoiceDesignDraft(current, patch));
  const begin = async () => {
    if (!canStartVoiceDesign(draft)) return;
    setDraft((current) => startVoiceDesign(current));
    setErrorMessage("");
    try {
      const result = await onSubmit(draft.brief);
      if (result.status !== "accepted") {
        setErrorMessage(result.message);
        setDraft((current) => ({ ...current, status: "failed" }));
        return;
      }
      setDraft((current) => ({ ...current, status: "completed" }));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "音色设计请求失败。");
      setDraft((current) => ({ ...current, status: "failed" }));
    }
  };
  return (
    <OverlaySurface ariaLabel={isNarrator ? "重新生成旁白音色" : `为 ${member.name} 设计音色`} onClose={onClose}>
      <section className={`voice-design-dialog is-${draft.status}`}>
        {draft.status === "editing" && <>
          <header><div><span className="section-kicker">{isNarrator ? "作品级旁白" : "声纹编辑"}</span><h2>{isNarrator ? "重新生成旁白音色" : `调整 ${member.name} 的音色方向`}</h2></div></header>
          <p className="voice-design-context">{isNarrator ? `旁白 · ${member.name}　 平台 · ${providerLabel}　 来源 · 当前大纲、故事圣经与风格档案` : `角色 · ${member.name}　 平台 · ${providerLabel}　 来源 · 上游角色档案`}</p>
          <section className="voice-design-evidence" aria-label="当前声纹依据">
            <article>
              <span>当前声纹</span>
              <strong>{member.voiceLabel || "待分配音色"}</strong>
              <small>{member.voiceSourceLabel || providerLabel} · {member.matchSummary || "匹配待评估"}</small>
            </article>
            <article>
              <span>选色依据</span>
              <p>{matchReasons.join("；") || member.description || "尚未形成可审计的匹配依据。"}</p>
            </article>
            <article>
              <span>试听边界</span>
              <p>{auditionWarnings.join("；") || "确认身份辨识度、长句稳定性与情绪边界。"}</p>
            </article>
            <article>
              <span>角色事实</span>
              <p>{detailFacts.map((fact) => `${fact.label}：${fact.value}`).join("；") || member.role || "未指定"}</p>
            </article>
          </section>
          <p className="voice-design-description">{isNarrator ? "系统会以当前作品的叙事气质、题材、基调与风格档案重建旁白画像和可试听音色；不会把它当作普通角色处理。" : "下方简报决定声线、节奏与表达边界；页面主工作台只保留日常试听和微调。"}</p>
          {isNarrator ? (
            <p className="voice-design-help">将覆盖当前旁白画像与音色选择，已有试听和正式音频会失效并需重新确认。</p>
          ) : (
            <>
              <p className="voice-design-help">提交后将生成候选音色并自动进入试听；Ctrl/⌘ + Enter 可快速提交。</p>
              <label className="voice-design-brief"><textarea aria-label="角色音色简报" onChange={(event) => update({ brief: event.target.value })} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canStartVoiceDesign(draft)) { event.preventDefault(); begin(); } }} placeholder="描述角色的年龄感、音域、语速、情绪底色与表达习惯…" value={draft.brief} /></label>
            </>
          )}
          <footer><small>{isNarrator ? "将使用当前作品档案" : `${draft.brief.trim().length} 字`}</small>{!isNarrator && <button className="button button-secondary" onClick={() => update({ brief: defaultVoiceDesignBrief(member) })} type="button">恢复上游简报</button>}<span /><button className="button button-secondary" onClick={onClose} type="button">取消</button><button className="button button-primary" disabled={!canStartVoiceDesign(draft)} onClick={begin} type="button">{isNarrator ? "重新生成并准备试听" : "生成并试听"}</button></footer>
        </>}
        {draft.status === "running" && <section className="voice-design-run" aria-live="polite"><i aria-hidden="true" /><h2>{isNarrator ? "正在重新生成旁白音色" : `正在设计 ${member.name} 的音色`}</h2><p>{isNarrator ? "正在分析当前作品档案并调用 TTS 平台生成新的旁白音色。" : "正在调用当前 TTS 平台生成候选音色，并将返回的音色标识与试听产物写入项目。"}</p><div><span>✓ 锁定角色识别度</span><span className="is-active">● 生成音色候选</span><span>○ 写入试听产物</span></div></section>}
        {draft.status === "completed" && <section className="voice-design-result" aria-live="polite"><span className="section-kicker">项目产物已更新</span><h2>{isNarrator ? "旁白音色已重新生成" : "音色方向已生成"}</h2><strong>{isNarrator ? `作品级旁白 · ${member.name}` : `AI 设计 · ${member.name}`}</strong><p>{isNarrator ? "新的旁白画像和音色已写入项目，请生成试听后重新确认团队。" : "候选音色已持久化为待试听状态，试听通过后可批准进入正式合成。"}</p><footer><span /><button className="button button-primary" onClick={onClose} type="button">完成</button></footer></section>}
        {draft.status === "failed" && <section className="voice-design-result is-failed" aria-live="polite"><span className="section-kicker">执行失败</span><h2>音色设计未完成</h2><p>{errorMessage || "TTS 平台未接受此音色设计请求。"}</p><footer><button className="button button-secondary" onClick={onClose} type="button">取消</button><span /><button className="button button-primary" onClick={() => update({ brief: draft.brief })} type="button">返回修改</button></footer></section>}
      </section>
    </OverlaySurface>
  );
}

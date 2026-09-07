import { useId, useState } from "react";

import type { VoiceCastMemberView, VoiceCommandResult } from "@nimo/engine-contracts";

import { canStartVoiceClone, createVoiceCloneDraft, startVoiceClone, updateVoiceCloneDraft } from "../lib/voice-clone-session";
import { isTauriEnvironment, pickAudioReferenceFile } from "../lib/native-bridge";
import { OverlaySurface } from "./OverlaySurface";

export function VoiceCloneDialog({ member, onClose, onSubmit, providerLabel, referenceMode = "local-file", requiresConsent = true }: { readonly member: VoiceCastMemberView; readonly onClose: () => void; readonly onSubmit: (input: { readonly referenceAudio: string; readonly referenceTranscript: string; readonly authorized: boolean }) => Promise<VoiceCommandResult>; readonly providerLabel: string; readonly referenceMode?: "local-file" | "provider-file-id"; readonly requiresConsent?: boolean }) {
  const inputId = useId();
  const [draft, setDraft] = useState(createVoiceCloneDraft);
  const [errorMessage, setErrorMessage] = useState("");

  const update = (patch: Partial<Pick<typeof draft, "referenceLabel" | "referenceTranscript" | "consentConfirmed">>) => setDraft((current) => updateVoiceCloneDraft(current, patch));
  const chooseLocalReference = async () => {
    const path = await pickAudioReferenceFile();
    if (path !== null && path.trim() !== "") {
      update({ referenceLabel: path });
    }
  };
  const begin = async () => {
    if (!canStartVoiceClone(draft, requiresConsent)) return;
    setDraft((current) => startVoiceClone(current, requiresConsent));
    setErrorMessage("");
    try {
      const result = await onSubmit({
        referenceAudio: draft.referenceLabel,
        referenceTranscript: draft.referenceTranscript,
        authorized: draft.consentConfirmed,
      });
      if (result.status !== "accepted") {
        setErrorMessage(result.message);
        setDraft((current) => ({ ...current, status: "failed" }));
        return;
      }
      setDraft((current) => ({ ...current, status: "completed" }));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "音色克隆请求失败。");
      setDraft((current) => ({ ...current, status: "failed" }));
    }
  };

  const sourceProviderPrompt = referenceMode === "provider-file-id";
  const nativeLocalReferencePicker = referenceMode === "local-file" && isTauriEnvironment();

  return <OverlaySurface ariaLabel={sourceProviderPrompt ? "供应商参考文件 ID" : `为 ${member.name} 上传声音克隆`} {...(sourceProviderPrompt ? { initialFocusSelector: "[data-nimo-source-autofocus]" } : {})} onClose={onClose}><section className={`voice-clone-dialog is-${draft.status} is-${referenceMode}${sourceProviderPrompt ? " voice-clone-source-dialog" : ""}`}>
    {draft.status === "editing" && <>
      <header><h2>{sourceProviderPrompt ? "供应商参考文件 ID" : "上传声音克隆"}</h2><p>{sourceProviderPrompt ? "当前平台要求先在供应商侧上传音频，请输入返回的 File ID：" : `为 ${member.name} 选择参考音频。仅记录授权与本地路径；文件内容由同机 Engine 按当前 TTS 平台规则处理。`}</p></header>
      {referenceMode === "local-file" && <><p className="voice-clone-context">角色 · {member.name}　 平台 · {providerLabel}</p>{nativeLocalReferencePicker ? <section className="voice-clone-file is-native"><span>参考音频</span><strong>{draft.referenceLabel || "选择 WAV、MP3、FLAC 或 M4A 文件"}</strong><small>将把本地路径交给同机 Engine；不会由 Nimo 读取或上传文件内容。</small><button aria-label="选择本地参考音频" className="button button-secondary" onClick={() => void chooseLocalReference()} type="button">选择本地音频…</button></section> : <label className="voice-clone-file" htmlFor={inputId}><span>参考音频</span><strong>{draft.referenceLabel || "选择 WAV、MP3、FLAC 或 M4A 文件"}</strong><small>建议样本时长：30 秒至 5 分钟</small><input accept="audio/wav,audio/mpeg,audio/flac,audio/mp4,.wav,.mp3,.flac,.m4a" id={inputId} onChange={(event) => { const file = event.target.files?.[0] as (File & { readonly path?: string }) | undefined; update({ referenceLabel: file?.path ?? file?.name ?? "" }); }} type="file" /></label>}</>}
      {sourceProviderPrompt && <label className="voice-clone-id"><input aria-label="供应商参考文件 ID" data-nimo-source-autofocus onChange={(event) => update({ referenceLabel: event.target.value })} placeholder="例如：file_01H..." value={draft.referenceLabel} /></label>}
      {requiresConsent && <><label className="voice-clone-consent"><input checked={draft.consentConfirmed} onChange={(event) => update({ consentConfirmed: event.target.checked })} type="checkbox" /><span>我确认已获得参考音频说话人的明确授权，可将其声音用于本作品配音。</span></label><label className="voice-clone-id"><span>参考音频转写（可选）</span><textarea aria-label="参考音频转写" onChange={(event) => update({ referenceTranscript: event.target.value })} placeholder="留空将仅在未来的受保护克隆命令中使用声纹向量" value={draft.referenceTranscript} /></label></>}
      <footer><button className="button button-secondary" onClick={onClose} type="button">取消</button><button className="button button-primary" disabled={!canStartVoiceClone(draft, requiresConsent)} onClick={begin} type="button">{sourceProviderPrompt ? "继续克隆" : "开始克隆"}</button></footer>
    </>}
    {draft.status === "running" && <section className="voice-clone-state" aria-live="polite"><i aria-hidden="true" /><h2>正在执行声音克隆</h2><p>正在校验授权、调用当前 TTS 平台并将音色标识写入项目。</p><div><span>✓ 记录角色与参考标识</span><span className="is-active">● 调用克隆服务</span><span>○ 写入配音团队</span></div></section>}
    {draft.status === "completed" && <section className="voice-clone-state" aria-live="polite"><span className="section-kicker">项目产物已更新</span><h2>克隆音色已写入角色</h2><p>音色已持久化为待试听状态；试听通过后再批准进入正式合成。</p><footer><span /><button className="button button-primary" onClick={onClose} type="button">完成</button></footer></section>}
    {draft.status === "failed" && <section className="voice-clone-state is-failed" aria-live="polite"><span className="section-kicker">执行失败</span><h2>克隆请求未完成</h2><p>{errorMessage || "TTS 平台未接受此克隆请求。"}</p><footer><button className="button button-secondary" onClick={onClose} type="button">取消</button><span /><button className="button button-primary" onClick={() => setDraft((current) => updateVoiceCloneDraft(current, { referenceLabel: current.referenceLabel }))} type="button">返回修改</button></footer></section>}
  </section></OverlaySurface>;
}

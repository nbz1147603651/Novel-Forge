import type { VoiceRoomSegmentSession } from "../../lib/voice-room-session";
import type { VoiceScriptDraft } from "../../lib/voice-script-session";

/** 声房指导状态摘要（mirrors PySide6 指导状态卡）。 */
export function VoiceRoomGuidanceSummary({
  draft,
  state,
}: {
  readonly draft: VoiceScriptDraft | undefined;
  readonly state: VoiceRoomSegmentSession["state"];
}) {
  const directions =
    draft === undefined
      ? []
      : [
          draft.toneHint && `语气：${draft.toneHint}`,
          draft.speed !== "default" && `语速：${draft.speed}`,
          draft.volume !== "default" && `音量：${draft.volume}`,
          draft.pitch !== "default" && `音高：${draft.pitch}`,
          draft.stressWords && `重音：${draft.stressWords}`,
          draft.intent && `意图：${draft.intent}`,
        ].filter((item): item is string => Boolean(item));
  const stateCopy: Readonly<
    Record<VoiceRoomSegmentSession["state"], string>
  > = {
    formal: "当前使用正式片段。编辑指导不会覆盖它。",
    guidance_saved:
      "已保存待审指导；请生成隔离试听后再决定是否接受。",
    generating:
      "正在以当前指导生成隔离试听，正式片段保持不变。",
    candidate: "候选试听已就绪；接受前不会进入章节主音轨。",
    accepting:
      "正在接受候选试听；章节主音轨仍需后处理装配。",
    accepted: "候选试听已接受；请到后处理装配章节主音轨。",
  };
  return (
    <section aria-live="polite" className="voice-room-guidance">
      <header>
        <span>指导状态</span>
        <strong>{stateCopy[state]}</strong>
      </header>
      {directions.length > 0 ? (
        <ul>
          {directions.map((direction) => (
            <li key={direction}>{direction}</li>
          ))}
        </ul>
      ) : (
        <small>尚未保存本段的附加指导参数。</small>
      )}
    </section>
  );
}

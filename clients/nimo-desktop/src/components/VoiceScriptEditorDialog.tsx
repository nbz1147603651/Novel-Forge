import { useMemo, useState } from "react";

import type { VoiceScriptSegmentView } from "@nimo/engine-contracts";

import { applySafeVoiceSuggestion, createVoiceScriptEditResult, createVoiceScriptDrafts, mergeVoiceScriptDrafts, updateVoiceScriptDraft, type VoiceScriptDraft, type VoiceScriptEditMode, type VoiceScriptEditResult } from "../lib/voice-script-session";
import { OverlaySurface } from "./OverlaySurface";

const emotionOptions = ["中性", "克制", "低语", "警觉", "哀伤", "愤怒", "温柔"] as const;

function VoiceScriptControls({ draft, onChange }: { readonly draft: VoiceScriptDraft; readonly onChange: (patch: Partial<VoiceScriptDraft>) => void }) {
  return <>
    <label className="voice-script-text"><span>台词 / 旁白</span><textarea aria-label="当前片段文本" onChange={(event) => onChange({ content: event.target.value })} value={draft.content} /></label>
    <div className="voice-script-control-grid">
      <label><span>情绪</span><select aria-label="当前片段情绪" onChange={(event) => onChange({ emotionLabel: event.target.value })} value={draft.emotionLabel}>{emotionOptions.map((emotion) => <option key={emotion}>{emotion}</option>)}</select></label>
      <label><span>语气提示</span><input aria-label="当前片段语气提示" onChange={(event) => onChange({ toneHint: event.target.value })} placeholder="例如：克制、迟疑、压低声音" value={draft.toneHint} /></label>
      <label className="voice-script-intensity"><span>强度</span><input aria-label="当前片段情绪强度" max="100" min="0" onChange={(event) => onChange({ emotionIntensity: Number(event.target.value) })} type="range" value={draft.emotionIntensity} /><output>{draft.emotionIntensity}%</output></label>
      <label><span>语速</span><select aria-label="当前片段语速" onChange={(event) => onChange({ speed: event.target.value })} value={draft.speed}><option value="default">使用项目默认</option>{["0.6×", "0.7×", "0.8×", "0.9×", "1.0×", "1.1×", "1.2×", "1.3×", "1.4×", "1.5×"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label><span>音量</span><select aria-label="当前片段音量" onChange={(event) => onChange({ volume: event.target.value })} value={draft.volume}><option value="default">使用项目默认</option>{["0.6×", "0.7×", "0.8×", "0.9×", "1.0×", "1.1×", "1.2×", "1.3×", "1.4×"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label><span>音高</span><select aria-label="当前片段音高" onChange={(event) => onChange({ pitch: event.target.value })} value={draft.pitch}><option value="default">使用项目默认</option>{["-4 半音", "-3 半音", "-2 半音", "-1 半音", "+0 半音", "+1 半音", "+2 半音", "+3 半音", "+4 半音"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label><span>重音词</span><input onChange={(event) => onChange({ stressWords: event.target.value })} placeholder="用逗号分隔，例如：绝不，今晚" value={draft.stressWords} /></label>
      <label><span>旁白距离</span><select onChange={(event) => onChange({ narratorDistance: event.target.value })} value={draft.narratorDistance}><option value="project-default">不指定</option><option value="close">近距（贴近角色）</option><option value="medium">中距（常规叙述）</option><option value="distant">远距（全知 / 宏观）</option></select></label>
      <label><span>发音覆盖</span><input onChange={(event) => onChange({ pronunciationOverrides: event.target.value })} placeholder="例如：角色名 / 外来词读音" value={draft.pronunciationOverrides} /></label>
      <label><span>空间 / 设备效果</span><select onChange={(event) => onChange({ voiceEffect: event.target.value })} value={draft.voiceEffect}><option value="none">无（推荐）</option><option value="spacious_echo">空旷回声</option><option value="auditorium_echo">礼堂广播</option><option value="lofi_telephone">电话失真</option><option value="robotic">机械声</option></select></label>
      <label><span>主要语言</span><select onChange={(event) => onChange({ language: event.target.value })} value={draft.language}><option value="auto">自动识别</option><option value="zh">普通话</option><option value="yue">粤语</option><option value="en">英语</option><option value="ja">日语</option><option value="ko">韩语</option><option value="fr">法语</option><option value="de">德语</option><option value="es">西班牙语</option><option value="pt">葡萄牙语</option><option value="ru">俄语</option></select></label>
      <label><span>表达风格</span><select onChange={(event) => onChange({ deliveryStyle: event.target.value })} value={draft.deliveryStyle}><option value="natural">自然</option><option value="intimate">贴近 / 私语</option><option value="narrative">有声书叙事</option><option value="conversational">自然交谈</option><option value="dramatic">戏剧表达</option><option value="broadcast">播音表达</option></select></label>
      <label><span>能量</span><select onChange={(event) => onChange({ energy: event.target.value })} value={draft.energy}><option value="restrained">收敛</option><option value="natural">自然</option><option value="intense">充沛</option></select></label>
      <label><span>吐字</span><select onChange={(event) => onChange({ articulation: event.target.value })} value={draft.articulation}><option value="soft">松弛</option><option value="natural">自然</option><option value="clear">清晰利落</option></select></label>
      <label><span>气声</span><select onChange={(event) => onChange({ breathiness: event.target.value })} value={draft.breathiness}><option value="light">很少</option><option value="natural">自然</option><option value="noticeable">明显</option></select></label>
      <label><span>张力</span><select onChange={(event) => onChange({ tension: event.target.value })} value={draft.tension}><option value="low">放松</option><option value="natural">自然</option><option value="high">紧绷</option></select></label>
      <label className="is-wide"><span>表达意图</span><input onChange={(event) => onChange({ intent: event.target.value })} placeholder="例如：让对方相信自己其实并不害怕" value={draft.intent} /></label>
      <label className="is-wide"><span>平台原生补充</span><input onChange={(event) => onChange({ platformInstruction: event.target.value })} placeholder="仅发送给当前平台，例如 Qwen/CosyVoice 的自然语言 instruct" value={draft.platformInstruction} /></label>
    </div>
  </>;
}

/** Source-shaped staged script editor; it preserves the original audio until review. */
export function VoiceScriptEditorDialog({
  chapterNumber,
  editMode = "script",
  initialDrafts,
  initialSegmentId,
  onClose,
  onSave,
  providerLabel,
  segments,
}: {
  readonly chapterNumber: number;
  readonly editMode?: VoiceScriptEditMode;
  readonly initialDrafts?: Readonly<Record<string, VoiceScriptDraft>>;
  readonly initialSegmentId: string;
  readonly onClose: () => void;
  readonly onSave: (result: VoiceScriptEditResult) => void;
  readonly providerLabel: string;
  readonly segments: readonly VoiceScriptSegmentView[];
}) {
  const [workingSegments, setWorkingSegments] = useState(segments);
  const [drafts, setDrafts] = useState(() => mergeVoiceScriptDrafts(segments, initialDrafts));
  const [activeId, setActiveId] = useState(initialSegmentId || (segments[0]?.id ?? ""));
  const [validationMessage, setValidationMessage] = useState("");
  const performanceOnly = editMode === "performance";
  const activeIndex = workingSegments.findIndex((segment) => segment.id === activeId);
  const active = useMemo(() => drafts[activeId] ?? drafts[workingSegments[0]?.id ?? ""], [activeId, drafts, workingSegments]);
  const speakerOptions = useMemo(() => [...new Map(workingSegments.map((segment) => [segment.speakerId, { id: segment.speakerId, label: segment.speakerLabel }])).values()], [workingSegments]);
  if (active === undefined) return null;
  const move = (offset: number) => setActiveId(workingSegments[Math.max(0, Math.min(workingSegments.length - 1, activeIndex + offset))]?.id ?? activeId);
  const update = (patch: Partial<VoiceScriptDraft>) => {
    setValidationMessage("");
    setDrafts((current) => updateVoiceScriptDraft(current, active.id, patch));
  };
  const deleteActive = () => {
    if (performanceOnly || workingSegments.length <= 1) return;
    if (!window.confirm(`删除第 ${activeIndex + 1} 段？\n\n保存脚本后，该段关联的音频与字幕会进入待重建状态。`)) return;
    const remaining = workingSegments
      .filter((segment) => segment.id !== active.id)
      .map((segment, index) => ({ ...segment, segmentIndex: index }));
    const nextActive = remaining[Math.min(activeIndex, remaining.length - 1)];
    setWorkingSegments(remaining);
    setDrafts((current) => Object.fromEntries(
      Object.entries(current).filter(([segmentId]) => segmentId !== active.id),
    ));
    setActiveId(nextActive?.id ?? "");
    setValidationMessage("");
  };
  const save = () => {
    const missingIndex = workingSegments.findIndex((segment) => !(drafts[segment.id]?.content ?? "").trim());
    if (missingIndex >= 0) {
      setActiveId(workingSegments[missingIndex]?.id ?? active.id);
      setValidationMessage(`第 ${missingIndex + 1} 段缺少台词或旁白，不能保存。`);
      return;
    }
    onSave(createVoiceScriptEditResult(editMode, workingSegments, drafts));
  };
  const unresolvedSpeakerCount = workingSegments.filter(
    (segment) => segment.needsSpeakerReview,
  ).length;
  const acceptAllSuggestions = () => {
    setDrafts((current) => Object.fromEntries(
      Object.entries(current).map(([segmentId, draft]) => [
        segmentId,
        { ...draft, content: applySafeVoiceSuggestion(draft.content) },
      ]),
    ));
    setValidationMessage("");
  };

  return (
    <OverlaySurface
      ariaLabel={`编辑第 ${chapterNumber} 章配音脚本`}
      backdropClassName="voice-script-editor-backdrop"
      onClose={onClose}
    >
      <section className={`voice-script-editor-dialog${performanceOnly ? " is-performance" : ""}`}>
        <header>
          <h2>逐段编辑配音脚本</h2>
          <p>{performanceOnly
            ? "这里保存的是待审试听指导，不会改动源配音脚本或已装配成品。只有生成试听并点击“接受此版”后才会推广为正式分段，随后再到后处理重新装配全章。"
            : "修改文本、说话人、情绪与表达参数后统一保存。保存会原子写入项目脚本，并使旧音频和字幕进入待重建状态。"}</p>
        </header>
        <section className="voice-script-editor-review">
          <div>
            <strong>{unresolvedSpeakerCount === 0
              ? "说话人已确认"
              : `待复核 ${unresolvedSpeakerCount} 段说话人`}</strong>
            <span>{unresolvedSpeakerCount === 0
              ? "本章没有需要人工复核的说话人。"
              : "请先确认候选说话人，再进入正式合成。"}</span>
          </div>
          <button className="button button-secondary" onClick={acceptAllSuggestions} type="button">
            全部采纳推荐
          </button>
        </section>
        <p className="voice-script-editor-capabilities">
          当前平台：{providerLabel}　原生：情绪、重音、副语言、发音、语言、空间效果　｜
          本地补齐：语速、音量、音高　｜
          导演提示：语气、表达风格、能量、吐字、气声、共鸣、张力、亲密度
        </p>
        <div className="voice-script-editor-body">
          <aside aria-label="配音脚本片段列表">
            {workingSegments.map((segment, index) => (
              <button
                className={segment.id === active.id ? "is-active" : ""}
                key={segment.id}
                onClick={() => setActiveId(segment.id)}
                type="button"
              >
                <span>#{index + 1} · {segment.speakerLabel}</span>
                <strong>{segment.kindLabel} · {drafts[segment.id]?.emotionLabel}</strong>
                <small>{drafts[segment.id]?.content}</small>
              </button>
            ))}
          </aside>
          <article>
            <header>
              <span className="section-kicker">片段 #{activeIndex + 1} · {active.speakerLabel}</span>
              <h3>{active.kindLabel}</h3>
              <p>预计 {Math.max(2, Math.ceil(active.content.length / 7))} 秒 · 当前为待审编辑草案</p>
            </header>
            {!performanceOnly && (
              <div className="voice-script-identifiers">
                <label className="voice-script-speaker">
                  <span>片段类型</span>
                  <select
                    aria-label="当前片段类型"
                    onChange={(event) => update({ kindLabel: event.target.value })}
                    value={active.kindLabel}
                  >
                    {[...new Set([active.kindLabel, "旁白", "对白"])].map((kind) => (
                      <option key={kind}>{kind}</option>
                    ))}
                  </select>
                </label>
                <label className="voice-script-speaker">
                  <span>说话人</span>
                  <select
                    aria-label="当前片段说话人"
                    onChange={(event) => {
                      const speaker = speakerOptions.find((option) => option.id === event.target.value);
                      if (speaker !== undefined) update({
                        speakerId: speaker.id,
                        speakerLabel: speaker.label,
                      });
                    }}
                    value={active.speakerId}
                  >
                    {speakerOptions.map((speaker) => (
                      <option key={speaker.id} value={speaker.id}>{speaker.label}</option>
                    ))}
                  </select>
                </label>
              </div>
            )}
            <VoiceScriptControls draft={active} onChange={update} />
            {validationMessage && (
              <p aria-live="polite" className="voice-script-validation" role="alert">
                {validationMessage}
              </p>
            )}
            <div className="voice-script-editor-assist">
              <button
                className="button button-secondary"
                onClick={() => update({ content: applySafeVoiceSuggestion(active.content) })}
                title="仅应用标点和明显语气建议，不会改写正文含义"
                type="button"
              >
                应用安全建议
              </button>
              <button
                className="button button-secondary"
                onClick={() => setDrafts((current) => updateVoiceScriptDraft(
                  current,
                  active.id,
                  createVoiceScriptDrafts(workingSegments)[active.id] ?? {},
                ))}
                type="button"
              >
                还原当前段
              </button>
              <span>安全建议只规范空白与句末标点，不改写正文含义。</span>
            </div>
          </article>
        </div>
        <footer>
          <div>
            <button className="button button-secondary" disabled={activeIndex <= 0} onClick={() => move(-1)} type="button">上一段</button>
            <button className="button button-secondary" disabled={activeIndex >= workingSegments.length - 1} onClick={() => move(1)} type="button">下一段</button>
            {!performanceOnly && (
              <button
                className="button button-danger"
                disabled={workingSegments.length <= 1}
                onClick={deleteActive}
                type="button"
              >
                删除当前段
              </button>
            )}
          </div>
          <div>
            <button className="button button-secondary" onClick={onClose} type="button">取消</button>
            <button
              className="button button-primary"
              onClick={save}
              title={performanceOnly ? "只保存待审指导，不覆盖正式版本" : "保存修改，并使本章派生的音频和字幕待重建"}
              type="button"
            >
              {performanceOnly ? "保存试听指导" : "保存脚本"}
            </button>
          </div>
        </footer>
      </section>
    </OverlaySurface>
  );
}

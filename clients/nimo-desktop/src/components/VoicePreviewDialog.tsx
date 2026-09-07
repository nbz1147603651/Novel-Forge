import { useEffect, useRef, useState } from "react";

import type {
  EngineCommandClient,
  VoiceCastMemberView,
  VoicePreviewPlanView,
} from "@nimo/engine-contracts";

import { OverlaySurface } from "./OverlaySurface";

interface VoicePreviewDialogProps {
  readonly projectId: string;
  readonly member: VoiceCastMemberView;
  readonly providerLabel: string;
  readonly commandClient: EngineCommandClient;
  readonly onClose: () => void;
  /** Fired after the author-confirmed voice has been written to the team. */
  readonly onConfirmed: (message: string) => void;
}

type PreviewStage = "building" | "generated" | "generating" | "confirming" | "failed";

function characterPayload(member: VoiceCastMemberView): Record<string, unknown> {
  return {
    character_id: member.id,
    name: member.name,
    role: member.role,
    description: member.description,
  };
}

/**
 * Multi-candidate voice A/B preview dialog — the React counterpart of the
 * PySide6 ``voice_preview_panel``. Both surfaces share the same Python
 * service (``tts/services/voice_preview.py``) through the engine command
 * boundary: build plan → generate candidates → listen → confirm.
 */
export function VoicePreviewDialog({
  projectId,
  member,
  providerLabel,
  commandClient,
  onClose,
  onConfirmed,
}: VoicePreviewDialogProps) {
  const [plan, setPlan] = useState<VoicePreviewPlanView | null>(null);
  const [stage, setStage] = useState<PreviewStage>("building");
  const [sampleText, setSampleText] = useState("");
  const [speed, setSpeed] = useState(1);
  const [volume, setVolume] = useState(1);
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [playingVoiceId, setPlayingVoiceId] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await commandClient.buildVoicePreviewPlan({
          kind: "build_voice_preview_plan",
          projectId,
          characters: [characterPayload(member)],
        });
        if (cancelled) return;
        const payload = result.data as { plans?: VoicePreviewPlanView[] } | undefined;
        const next = payload?.plans?.[0];
        if (result.status !== "accepted" || next === undefined) {
          setErrorMessage(result.message || "未返回音色候选计划。");
          setStage("failed");
          return;
        }
        setPlan(next);
        setSampleText(next.sampleText);
        setSpeed(next.speed);
        setVolume(next.volume);
        setSelectedVoiceId(next.candidates[0]?.voiceId ?? "");
        setStage("generated");
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof Error ? error.message : "音色候选计划构建失败。");
          setStage("failed");
        }
      }
    })();
    return () => {
      cancelled = true;
      audioRef.current?.pause();
    };
  }, [commandClient, member.id, projectId]);

  const generate = async () => {
    if (plan === null || stage === "generating") return;
    audioRef.current?.pause();
    setPlayingVoiceId("");
    setStage("generating");
    setErrorMessage("");
    setNotice("");
    try {
      const result = await commandClient.generateVoicePreviews({
        kind: "generate_voice_previews",
        projectId,
        plan: { ...plan, sampleText, speed, volume },
      });
      const payload = result.data as { plan?: VoicePreviewPlanView } | undefined;
      const next = payload?.plan;
      if (result.status !== "accepted" || next === undefined) {
        setErrorMessage(result.message || "候选试听生成失败。");
        setStage("failed");
        return;
      }
      setPlan(next);
      setStage("generated");
      setNotice(result.message);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "候选试听生成失败。");
      setStage("failed");
    }
  };

  const confirm = async () => {
    if (plan === null || stage === "confirming") return;
    const candidate = plan.candidates.find((item) => item.voiceId === selectedVoiceId);
    if (candidate === undefined) return;
    audioRef.current?.pause();
    setStage("confirming");
    setErrorMessage("");
    try {
      const result = await commandClient.confirmVoicePreview({
        kind: "confirm_voice_preview",
        projectId,
        characterId: plan.characterId,
        voiceId: candidate.voiceId,
        speed,
        volume,
        sampleText: plan.sampleText,
        samplePath: candidate.samplePath ?? "",
      });
      if (result.status !== "accepted") {
        setErrorMessage(result.message);
        setStage("generated");
        return;
      }
      onConfirmed(result.message);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "音色确认失败。");
      setStage("generated");
    }
  };

  const togglePlay = (voiceId: string, audioUrl: string) => {
    if (audioRef.current !== null) {
      audioRef.current.pause();
      audioRef.current = null;
      setPlayingVoiceId("");
      if (playingVoiceId === voiceId) return;
    }
    if (!audioUrl) return;
    const audio = new Audio(audioUrl);
    audioRef.current = audio;
    setPlayingVoiceId(voiceId);
    audio.onended = () => {
      audioRef.current = null;
      setPlayingVoiceId("");
    };
    audio.onerror = () => {
      audioRef.current = null;
      setPlayingVoiceId("");
      setErrorMessage("试听音频加载失败；候选记录未改变。");
    };
    void audio.play();
  };

  const busy = stage === "building" || stage === "generating" || stage === "confirming";
  const candidates = plan?.candidates ?? [];

  return (
    <OverlaySurface ariaLabel={`为 ${member.name} 进行候选音色 A/B 对比`} onClose={onClose}>
      <section className="voice-preview-dialog">
        <header>
          <div>
            <span className="section-kicker">候选音色 A/B 对比</span>
            <h2>试听并确认 {member.name} 的音色</h2>
          </div>
        </header>
        <p className="voice-design-context">
          角色 · {member.name}　 平台 · {providerLabel}　 来源 · 系统音色目录
          {"　"}全部候选使用同一段试听文本，便于横向比较。
        </p>

        <label className="voice-preview-text">
          <span>试听文本</span>
          <textarea
            aria-label="候选音色试听文本"
            disabled={stage === "building"}
            onChange={(event) => setSampleText(event.target.value)}
            rows={3}
            value={sampleText}
          />
        </label>

        <div className="voice-preview-params">
          <label>
            <span>语速</span>
            <input
              aria-label="试听语速倍率"
              disabled={stage === "building"}
              max={2}
              min={0.5}
              onChange={(event) => setSpeed(Number(event.target.value))}
              step={0.1}
              type="number"
              value={speed}
            />
          </label>
          <label>
            <span>音量</span>
            <input
              aria-label="试听音量倍率"
              disabled={stage === "building"}
              max={2}
              min={0}
              onChange={(event) => setVolume(Number(event.target.value))}
              step={0.1}
              type="number"
              value={volume}
            />
          </label>
          <button
            className="button button-secondary"
            disabled={busy || stage === "failed"}
            onClick={() => void generate()}
            type="button"
          >
            {stage === "generating" ? "正在合成候选试听…" : "重新合成候选试听"}
          </button>
        </div>

        <section className="voice-preview-candidates" aria-label="音色候选列表">
          {stage === "building" && (
            <p className="voice-preview-empty" role="status">
              正在从系统音色目录挑选候选…
            </p>
          )}
          {stage !== "building" && candidates.length === 0 && (
            <p className="voice-preview-empty" role="status">
              当前目录没有可用候选；可先构建配音团队或更换平台。
            </p>
          )}
          {candidates.map((candidate) => {
            const reasons = candidate.matchReasons?.filter(Boolean) ?? [];
            const failed = Boolean(candidate.error);
            return (
              <article
                className={`voice-preview-candidate${failed ? " is-failed" : ""}`}
                key={candidate.voiceId}
              >
                <label className="voice-preview-candidate-select">
                  <input
                    checked={selectedVoiceId === candidate.voiceId}
                    disabled={busy || failed}
                    onChange={() => {
                      setSelectedVoiceId(candidate.voiceId);
                      togglePlay(candidate.voiceId, candidate.audioUrl ?? "");
                    }}
                    type="radio"
                    name={`preview-${plan?.characterId}`}
                  />
                  <span>
                    <strong>{candidate.voiceName}</strong>
                    {reasons.length > 0 && <small>{reasons.join(" · ")}</small>}
                  </span>
                </label>
                {candidate.description && <p className="voice-preview-candidate-desc">{candidate.description}</p>}
                {failed && <p className="voice-catalog-error">生成失败：{candidate.error}</p>}
                <button
                  className="button button-quiet"
                  disabled={!candidate.audioUrl || busy}
                  onClick={() => togglePlay(candidate.voiceId, candidate.audioUrl ?? "")}
                  type="button"
                >
                  {playingVoiceId === candidate.voiceId ? "停止试听" : "试听"}
                </button>
              </article>
            );
          })}
        </section>

        {errorMessage && <p className="voice-catalog-error" role="status">{errorMessage}</p>}
        {notice && !errorMessage && <p className="voice-preview-notice" role="status">{notice}</p>}

        <footer>
          <span />
          <button className="button button-secondary" disabled={busy} onClick={onClose} type="button">取消</button>
          <button
            className="button button-primary"
            disabled={busy || selectedVoiceId === "" || stage === "failed" || candidates.length === 0}
            onClick={() => void confirm()}
            type="button"
          >
            {stage === "confirming" ? "正在写入配音团队…" : "确认此音色并写入团队"}
          </button>
        </footer>
      </section>
    </OverlaySurface>
  );
}

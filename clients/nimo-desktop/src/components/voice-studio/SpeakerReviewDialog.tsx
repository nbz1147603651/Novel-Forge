import { useMemo, useState } from "react";

import type {
  EngineCommandClient,
  VoiceCastMemberView,
  VoiceScriptSegmentView,
} from "@nimo/engine-contracts";

import { OverlaySurface } from "../OverlaySurface";

export interface SpeakerCandidate {
  readonly characterId: string;
  readonly characterName: string;
  readonly confidence: number;
  readonly reason: string;
}

export interface UnresolvedSegmentInfo {
  readonly segmentIndex: number;
  readonly text: string;
  readonly contextBefore: string;
  readonly contextAfter: string;
  readonly candidates: readonly SpeakerCandidate[];
}

interface SpeakerReviewDialogProps {
  readonly cast: readonly VoiceCastMemberView[];
  readonly chapterNumber: number;
  readonly commandClient: EngineCommandClient;
  readonly onClose: () => void;
  readonly onResolved: (segmentIndices: readonly number[]) => Promise<void>;
  readonly projectId: string;
  readonly segments: readonly VoiceScriptSegmentView[];
  readonly unresolvedSegments: readonly UnresolvedSegmentInfo[];
}

/** Interactive dialog for confirming unresolved speaker assignments. */
export function SpeakerReviewDialog({
  cast,
  chapterNumber,
  commandClient,
  onClose,
  onResolved,
  projectId,
  segments,
  unresolvedSegments,
}: SpeakerReviewDialogProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [resolutions, setResolutions] = useState<
    ReadonlyMap<number, string>
  >(() => new Map());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const total = unresolvedSegments.length;
  const resolvedCount = resolutions.size;
  const current = unresolvedSegments[currentIndex];

  const castOptions = useMemo(
    () => cast.filter((member) => member.id !== "narrator"),
    [cast],
  );

  const selectCharacter = (characterId: string) => {
    if (current === undefined) return;
    setResolutions((prev) => {
      const next = new Map(prev);
      next.set(current.segmentIndex, characterId);
      return next;
    });
  };

  const convertToNarration = () => {
    if (current === undefined) return;
    setResolutions((prev) => {
      const next = new Map(prev);
      next.set(current.segmentIndex, "");
      return next;
    });
  };

  const goToNext = () => {
    if (currentIndex < total - 1) setCurrentIndex(currentIndex + 1);
  };

  const goToPrev = () => {
    if (currentIndex > 0) setCurrentIndex(currentIndex - 1);
  };

  const goToNextUnresolved = () => {
    const nextIndex = unresolvedSegments.findIndex(
      (segment, index) => index > currentIndex && !resolutions.has(segment.segmentIndex),
    );
    if (nextIndex >= 0) {
      setCurrentIndex(nextIndex);
      return;
    }
    const firstIndex = unresolvedSegments.findIndex(
      (segment) => !resolutions.has(segment.segmentIndex),
    );
    if (firstIndex >= 0) setCurrentIndex(firstIndex);
  };

  const acceptRecommendations = () => {
    setResolutions((previous) => {
      const next = new Map(previous);
      for (const segment of unresolvedSegments) {
        const recommendation = [...segment.candidates]
          .sort((left, right) => right.confidence - left.confidence)[0];
        if (recommendation !== undefined && recommendation.confidence >= 0.75) {
          next.set(segment.segmentIndex, recommendation.characterId);
        }
      }
      return next;
    });
  };

  const submit = async () => {
    setSubmitting(true);
    setSubmitError("");
    try {
      const resolutionList = [...resolutions.entries()].map(
        ([segmentIndex, characterId]) =>
          characterId === ""
            ? { segmentIndex, characterId, segmentType: "narration" }
            : { segmentIndex, characterId },
      );
      const result = await commandClient.resolveSpeakers({
        kind: "resolve_speakers",
        projectId,
        chapterNumber,
        resolutions: resolutionList,
      });
      if (result.status !== "accepted") {
        setSubmitError(result.message);
        return;
      }
      await onResolved(resolutionList.map((item) => item.segmentIndex));
    } catch {
      setSubmitError("说话人复核提交失败；请检查引擎连接后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  const getSegmentContext = (index: number): { before: string; after: string } => {
    const segIdx = unresolvedSegments[index]?.segmentIndex ?? 0;
    const position = segments.findIndex((segment) => segment.segmentIndex === segIdx);
    const before = position > 0 ? segments[position - 1]?.content ?? "" : "";
    const after =
      position >= 0 && position < segments.length - 1
        ? segments[position + 1]?.content ?? ""
        : "";
    return { before, after };
  };

  if (!current) {
    return (
      <OverlaySurface ariaLabel="说话人复核" onClose={onClose}>
        <section className="speaker-review-dialog">
          <header><h2>说话人复核</h2></header>
          <p>没有待复核的片段。</p>
          <footer>
            <button className="button button-secondary" onClick={onClose} type="button">
              关闭
            </button>
          </footer>
        </section>
      </OverlaySurface>
    );
  }

  const derivedContext = getSegmentContext(currentIndex);
  const context = {
    before: current.contextBefore || derivedContext.before,
    after: current.contextAfter || derivedContext.after,
  };
  const currentResolution = resolutions.get(current.segmentIndex);

  return (
    <OverlaySurface ariaLabel="说话人复核" onClose={onClose}>
      <section className="speaker-review-dialog">
        <header>
          <h2>说话人复核</h2>
          <span>
            已确认 {resolvedCount}/{total}
          </span>
        </header>
        <div className="speaker-review-tools">
          <button className="button button-secondary" onClick={goToNextUnresolved} type="button">
            下一处待复核
          </button>
          <button
            className="button button-secondary"
            disabled={!unresolvedSegments.some((segment) =>
              segment.candidates.some((candidate) => candidate.confidence >= 0.75))}
            onClick={acceptRecommendations}
            type="button"
          >
            全部采纳高置信推荐
          </button>
        </div>

        <div className="speaker-review-body">
          {/* Left: segment list */}
          <nav className="speaker-review-list" aria-label="待复核片段">
            {unresolvedSegments.map((seg, idx) => (
              <button
                className={idx === currentIndex ? "is-active" : ""}
                key={seg.segmentIndex}
                onClick={() => setCurrentIndex(idx)}
                type="button"
              >
                <span className="speaker-review-idx">
                  第 {idx + 1} 段
                </span>
                <span className="speaker-review-preview">
                  {seg.text.length > 20 ? `${seg.text.slice(0, 20)}…` : seg.text}
                </span>
                {resolutions.has(seg.segmentIndex) && (
                  <span className="speaker-review-done">✓</span>
                )}
              </button>
            ))}
          </nav>

          {/* Right: detail */}
          <div className="speaker-review-detail">
            {context.before && (
              <p className="speaker-review-context">…{context.before}</p>
            )}
            <blockquote className="speaker-review-text">{current.text}</blockquote>
            {context.after && (
              <p className="speaker-review-context">{context.after}…</p>
            )}

            {/* Candidates from AI adjudication */}
            {current.candidates.length > 0 && (
              <div className="speaker-review-candidates">
                <h4>AI 推荐</h4>
                {current.candidates.map((candidate) => (
                  <button
                    className={
                      currentResolution === candidate.characterId
                        ? "is-selected"
                        : ""
                    }
                    key={candidate.characterId}
                    onClick={() => selectCharacter(candidate.characterId)}
                    type="button"
                  >
                    <strong>{candidate.characterName}</strong>
                    <span>{Math.round(candidate.confidence * 100)}%</span>
                    {candidate.reason && <small>{candidate.reason}</small>}
                  </button>
                ))}
              </div>
            )}

            {/* Full cast selection */}
            <div className="speaker-review-cast">
              <h4>全部角色</h4>
              <select
                onChange={(e) => selectCharacter(e.target.value)}
                value={currentResolution ?? ""}
              >
                <option value="">— 选择角色 —</option>
                {castOptions.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.name}
                  </option>
                ))}
              </select>
              <button
                className="button button-secondary"
                onClick={convertToNarration}
                type="button"
              >
                转为旁白
              </button>
            </div>
          </div>
        </div>

        {submitError && (
          <p aria-live="polite" className="speaker-review-error" role="alert">
            {submitError}
          </p>
        )}

        <footer>
          <button
            className="button button-secondary"
            disabled={currentIndex === 0}
            onClick={goToPrev}
            type="button"
          >
            上一段
          </button>
          <button
            className="button button-secondary"
            disabled={currentIndex >= total - 1}
            onClick={goToNext}
            type="button"
          >
            下一段
          </button>
          <span />
          <button
            className="button button-secondary"
            onClick={onClose}
            type="button"
          >
            稍后处理
          </button>
          <button
            className="button button-primary"
            disabled={resolvedCount === 0 || submitting}
            onClick={submit}
            type="button"
          >
            {submitting
              ? "提交中…"
              : resolvedCount === total
                ? "完成复核"
                : `提交已确认 (${resolvedCount}/${total})`}
          </button>
        </footer>
      </section>
    </OverlaySurface>
  );
}

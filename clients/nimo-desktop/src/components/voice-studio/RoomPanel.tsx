import type { VoiceStudioView } from "@nimo/engine-contracts";

import { DropdownSelect } from "../DropdownSelect";
import { VoiceEmptyState } from "./VoiceEmptyState";
import { VoiceRoomGuidanceSummary } from "./VoiceRoomGuidanceSummary";
import type { VoiceScriptDraft } from "../../lib/voice-script-session";
import type { VoiceRoomSegmentSession } from "../../lib/voice-room-session";
import { voiceRoomTakeLabel } from "../../lib/voice-room-session";

export type VoiceRoomDialog =
  | "guidance";

interface RoomPanelProps {
  readonly activeRoomGuidanceLabel: string;
  readonly activeRoomTake: VoiceRoomSegmentSession;
  readonly activeSegment: VoiceStudioView["script"][number];
  readonly candidateAudioRef: React.MutableRefObject<HTMLAudioElement | null>;
  readonly changeChapter: (chapter: number) => Promise<void>;
  readonly chapterAudioRef: React.MutableRefObject<HTMLAudioElement | null>;
  readonly chapterPlaying: boolean;
  readonly chapterVolume: number;
  readonly discardActiveCandidate: () => void;
  readonly hasScriptSegments: boolean;
  readonly onChapterVolume: (volume: number) => void;
  readonly onDialog: (dialog: VoiceRoomDialog) => void;
  readonly onGuidanceNotice: (message: string) => void;
  readonly onPlayingSegmentId: (segmentId: string | null) => void;
  readonly onRoomReferenceOpen: (open: boolean) => void;
  readonly onSetActiveSegmentId: (segmentId: string) => void;
  readonly playingSegmentId: string | null;
  readonly roomGuidanceDrafts: Readonly<Record<string, VoiceScriptDraft>>;
  readonly roomOperationInFlight: boolean;
  readonly roomReferenceOpen: boolean;
  readonly roomTakeSession: Readonly<Record<string, VoiceRoomSegmentSession>>;
  readonly scheduleRoomOperation: (segmentId: string, action: "candidate" | "accept") => void;
  readonly scriptSegments: readonly VoiceStudioView["script"][number][];
  readonly seekChapterAudio: (seconds: number) => void;
  readonly selectedChapter: number;
  readonly studio: VoiceStudioView;
  readonly toggleChapterPlayback: () => Promise<void>;
}

/**
 * 声腔「配音室」页签：分段列表 / 提词器 / 指导编辑 / 试听生成与接受 / 全章播放。
 * 从 VoiceStudioPage 提取，音频 ref 与操作调度通过 props 注入。
 */
export function RoomPanel({
  activeRoomGuidanceLabel,
  activeRoomTake,
  activeSegment,
  candidateAudioRef,
  changeChapter,
  chapterAudioRef,
  chapterPlaying,
  chapterVolume,
  discardActiveCandidate,
  hasScriptSegments,
  onChapterVolume,
  onDialog,
  onGuidanceNotice,
  onPlayingSegmentId,
  onRoomReferenceOpen,
  onSetActiveSegmentId,
  playingSegmentId,
  roomGuidanceDrafts,
  roomOperationInFlight,
  roomReferenceOpen,
  roomTakeSession,
  scheduleRoomOperation,
  scriptSegments,
  seekChapterAudio,
  selectedChapter,
  studio,
  toggleChapterPlayback,
}: RoomPanelProps) {
  return (
    <div className="voice-room">
      <header className="voice-room-header">
        <div>
          <h2>配音室</h2>
          <p>
            选择片段试听或播放全章，右侧台词与字幕会实时跟随。
          </p>
        </div>
        <div className="voice-room-header-actions">
          <label>
            <span>章节:</span>
            <DropdownSelect
              ariaLabel="配音室章节"
              onChange={(v) => void changeChapter(Number(v))}
              options={(studio.availableChapters.length > 0 ? studio.availableChapters : [studio.chapterNumber]).map((num) => ({ value: String(num), label: `第 ${num} 章` }))}
              value={String(selectedChapter)}
            />
          </label>
          <button
            className="button button-secondary"
            disabled={!studio.audioReady}
            onClick={() => void toggleChapterPlayback()}
            title={studio.audioReady ? "播放本章已装配音频" : "装配完成后可播放全章"}
            type="button"
          >
            {chapterPlaying ? "暂停全章" : "播放全章"}
          </button>
        </div>
      </header>
      <div className="voice-room-grid">
        <aside className="voice-room-segments">
          <h3>本章分段</h3>
          <div className="voice-room-segment-list">
            {scriptSegments.map((segment) => (
              <button
                aria-label={`片段 ${segment.segmentIndex + 1} · ${segment.speakerLabel}`}
                className={
                  activeSegment.id === segment.id ? "is-active" : ""
                }
                key={segment.id}
                onClick={() => onSetActiveSegmentId(segment.id)}
                type="button"
              >
                <span>{String(segment.segmentIndex + 1).padStart(2, "0")}</span>
                <strong>{segment.speakerLabel} · {segment.content}</strong>
                <small>{segment.content}</small>
              </button>
            ))}
          </div>
        </aside>
        {hasScriptSegments ? (
          <article className="voice-room-editor">
            <header className="voice-room-segment-heading">
              <h3>第 {activeSegment.segmentIndex + 1} 段 · {activeSegment.speakerLabel}</h3>
              <div className="voice-room-state-summary">
                <span>{voiceRoomTakeLabel(roomTakeSession, activeSegment)}</span>
                <small>{activeRoomGuidanceLabel}</small>
              </div>
            </header>
            <section className="voice-room-reference">
              <button
                aria-expanded={roomReferenceOpen}
                onClick={() => onRoomReferenceOpen(!roomReferenceOpen)}
                type="button"
              >
                参考信息
              </button>
              {roomReferenceOpen && (
                <VoiceRoomGuidanceSummary
                  draft={roomGuidanceDrafts[activeSegment.id]}
                  state={activeRoomTake.state}
                />
              )}
            </section>
            <section className="voice-room-caption-stage">
              <header>
                <span>提词器 · 前后文</span>
                <span>字幕 {activeSegment.segmentIndex + 1} / {scriptSegments.length}</span>
                <strong>{activeSegment.speakerLabel}</strong>
              </header>
              <div>
                <small>
                  {scriptSegments[Math.max(0, activeSegment.segmentIndex - 1)]?.content || "本章开场"}
                </small>
                <strong>正在演绎 · {activeSegment.speakerLabel}</strong>
                <p>{activeSegment.content}</p>
                <small>
                  {scriptSegments[activeSegment.segmentIndex + 1]?.content || "本章片段结束"}
                </small>
              </div>
            </section>
            <div className="button-row voice-room-actions">
              <button
                className="button button-secondary"
                disabled={roomOperationInFlight}
                onClick={() => onDialog("guidance")}
                title="修改文本、情绪、语气、语速、音量或音高"
                type="button"
              >
                编辑指导
              </button>
              <button
                className="button button-primary"
                disabled={roomOperationInFlight}
                onClick={() =>
                  scheduleRoomOperation(
                    activeSegment.id,
                    "candidate",
                  )
                }
                type="button"
              >
                {activeRoomTake.state === "generating"
                  ? "正在生成试听…"
                  : activeRoomTake.state === "candidate"
                    ? "重新生成试听"
                    : "生成试听"}
              </button>
              <button
                className="button button-secondary"
                disabled={activeRoomTake.state !== "candidate"}
                onClick={() =>
                  scheduleRoomOperation(activeSegment.id, "accept")
                }
                type="button"
              >
                {activeRoomTake.state === "accepted"
                  ? "已接受此版"
                  : activeRoomTake.state === "accepting"
                    ? "正在接受…"
                    : "接受此版"}
              </button>
              <button
                className="button button-quiet"
                disabled={activeRoomTake.state !== "candidate"}
                onClick={discardActiveCandidate}
                type="button"
              >
                舍弃试听
              </button>
            </div>
            <section className="voice-room-player-panel">
              <div className="voice-player">
                <button
                  aria-label="播放片段"
                  disabled={!activeRoomTake.audioUrl}
                  onClick={() => {
                    if (!activeRoomTake.audioUrl) return;
                    if (playingSegmentId === activeSegment.id) {
                      candidateAudioRef.current?.pause();
                      onPlayingSegmentId(null);
                      return;
                    }
                    candidateAudioRef.current?.pause();
                    const player = new Audio(activeRoomTake.audioUrl);
                    candidateAudioRef.current = player;
                    player.addEventListener("ended", () => {
                      onPlayingSegmentId(null);
                    }, { once: true });
                    void player.play().then(() => {
                      onPlayingSegmentId(activeSegment.id);
                    }).catch(() => {
                      onGuidanceNotice("试听音频无法播放；请检查产物是否仍存在。");
                    });
                  }}
                  type="button"
                >
                  {playingSegmentId === activeSegment.id ? "❚❚" : "▶"}
                </button>
                <div>
                  <i
                    style={{
                      width:
                        playingSegmentId === activeSegment.id
                          ? "64%"
                          : "12%",
                    }}
                  />
                </div>
                <span>00:03 / 00:08</span>
              </div>
              <div className="voice-room-transport">
                <button disabled={activeSegment.segmentIndex <= 0} onClick={() => onSetActiveSegmentId(scriptSegments[Math.max(0, activeSegment.segmentIndex - 1)]?.id ?? activeSegment.id)} type="button">上段</button>
                <button disabled={!studio.chapterAudioUrl} onClick={() => seekChapterAudio(-10)} type="button">10s</button>
                <button disabled={!studio.chapterAudioUrl} onClick={() => void toggleChapterPlayback()} type="button">{chapterPlaying ? "暂停" : "播放"}</button>
                <button disabled={!studio.chapterAudioUrl} onClick={() => seekChapterAudio(10)} type="button">10s</button>
                <button disabled={activeSegment.segmentIndex >= scriptSegments.length - 1} onClick={() => onSetActiveSegmentId(scriptSegments[Math.min(scriptSegments.length - 1, activeSegment.segmentIndex + 1)]?.id ?? activeSegment.id)} type="button">下段</button>
                <label>音量 <input aria-label="配音室音量" max="100" min="0" onChange={(event) => {
                  const next = Number(event.target.value) / 100;
                  onChapterVolume(next);
                  if (chapterAudioRef.current) chapterAudioRef.current.volume = next;
                }} type="range" value={Math.round(chapterVolume * 100)} /> <strong>{Math.round(chapterVolume * 100)}%</strong></label>
              </div>
              <label className="voice-room-output">
                输出设备
                <select aria-label="配音室输出设备" defaultValue="system">
                  <option value="system">跟随系统输出（当前：系统默认设备）</option>
                </select>
              </label>
            </section>
          </article>
        ) : (
          <VoiceEmptyState
            description="生成脚本后，可在这里为每段台词编辑指导并生成隔离试听。"
            title="暂无可试听片段"
          />
        )}
      </div>
    </div>
  );
}

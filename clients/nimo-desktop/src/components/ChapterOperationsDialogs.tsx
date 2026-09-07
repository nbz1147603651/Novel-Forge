import { useState } from "react";

import type { ChapterStudioChapterView } from "@nimo/engine-contracts";

import { createChapterCleanRequest } from "../lib/chapter-clean-session";
import { OverlaySurface } from "./OverlaySurface";

function ChapterOperationFrame({ children, label, onClose, variant }: { readonly children: React.ReactNode; readonly label: string; readonly onClose: () => void; readonly variant?: "clean" | "project-switch" }) {
  return <OverlaySurface ariaLabel={label} onClose={onClose}><section className={`chapter-operation-dialog${variant === undefined ? "" : ` is-${variant}`}`}>{children}</section></OverlaySurface>;
}

export function ChapterCleanDialog({ chapters, defaultCutoff, maxChapter, onClose, onConfirm }: { readonly chapters: readonly ChapterStudioChapterView[]; readonly defaultCutoff: number; readonly maxChapter: number; readonly onClose: () => void; readonly onConfirm: (cutoff: number) => void }) {
  const [cutoff, setCutoff] = useState(defaultCutoff);
  const normalizedCutoff = createChapterCleanRequest({ cutoff, maxChapter }).cutoff;
  const affected = chapters.filter((chapter) => chapter.number >= normalizedCutoff);
  const materialized = affected.filter((chapter) => chapter.state !== "pending");
  const accept = () => {
    onConfirm(normalizedCutoff);
    onClose();
  };
  return <ChapterOperationFrame label="清理失效章节" onClose={onClose} variant="clean">
    <h2>清理失效章节</h2>
    <p>将从第 <b>{normalizedCutoff}</b> 章起删除已生成文件（正文、草稿、报告等），从该章节重新开始。你可以手动调整起始章；Canon 水位将同步回滚至清理起始章的前一章。该范围内已暂停的待确认方案也会一并作废；正在运行、排队或等待重试的任务仍需先完全停止。</p>
    <hr />
    <label className="chapter-clean-cutoff"><span>从第</span><input aria-label="清理起始章节" max={maxChapter} min="1" onChange={(event) => setCutoff(Number(event.target.value))} type="number" value={cutoff} /><span>章起清理</span></label>
    <div className="chapter-clean-impact" aria-live="polite">
      <strong>预计影响第 {normalizedCutoff}–{maxChapter} 章，共 {Math.max(0, maxChapter - normalizedCutoff + 1)} 章</strong>
      <span>其中 {materialized.length} 章有已归档、进行中或待确认状态。清理后这些章节的任务流卡片和续跑指针会重置；错误日志与费用证据仍保留。</span>
    </div>
    <small className="chapter-operation-warning">⚠️ 此操作不可撤销，清理后只能重新生成。</small>
    <footer><button className="chapter-operation-quiet" onClick={onClose} type="button">取消</button><span /><button className="chapter-operation-danger" onClick={accept} type="button">确认清理</button></footer>
  </ChapterOperationFrame>;
}

export function ChapterProjectSwitchDialog({ chapterNumber, jobStatus, projectTitle, onClose, onConfirm }: { readonly chapterNumber: number; readonly jobStatus: string; readonly projectTitle: string; readonly onClose: () => void; readonly onConfirm: () => void }) {
  return <ChapterOperationFrame label="切换项目" onClose={onClose} variant="project-switch">
    <h2>切换项目</h2>
    <p>当前项目「{projectTitle}」{jobStatus || `正在生成第 ${chapterNumber} 章`}</p>
    <hr />
    <small>切换后任务将继续在后台运行</small>
    <footer><button className="chapter-operation-primary" onClick={onConfirm} type="button">确定</button><button className="chapter-operation-quiet" onClick={onClose} type="button">取消</button></footer>
  </ChapterOperationFrame>;
}

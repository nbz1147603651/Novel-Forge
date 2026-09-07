import { useState } from "react";

import type { RevisionCandidateView } from "@nimo/engine-contracts";

import { action, type NarrativeToolsAction } from "./types";

/** 终稿修订工作台（候选确认清单，mirrors PySide6 FinalRevisionWidget）。 */
export function RevisionWorkbench({ candidates, onAction }: { readonly candidates: readonly RevisionCandidateView[]; readonly onAction: (action: NarrativeToolsAction) => void }) {
  const [acceptedIds, setAcceptedIds] = useState<ReadonlySet<string>>(() => new Set());

  const toggleRevision = (candidate: RevisionCandidateView) => {
    setAcceptedIds((current) => {
      const next = new Set(current);
      if (next.has(candidate.id)) next.delete(candidate.id);
      else next.add(candidate.id);
      return next;
    });
    onAction(action("revision-toggled", `已在修订清单中${acceptedIds.has(candidate.id) ? "移除" : "纳入"}「${candidate.title}」。`, candidate.id));
  };

  return (
    <div className="revision-workbench">
      <header><div><h3>终稿修订</h3><p>所有候选修改都要先由创作者确认；真实写回由后续 EngineClient 命令负责。</p></div></header>
      {candidates.map((candidate) => <article className={acceptedIds.has(candidate.id) ? "is-accepted" : ""} key={candidate.id}><div><span>{candidate.impactLabel}</span><strong>{candidate.title}</strong><p>{candidate.summary}</p></div><button className={acceptedIds.has(candidate.id) ? "button button-secondary" : "button button-primary"} onClick={() => toggleRevision(candidate)} type="button">{acceptedIds.has(candidate.id) ? "已纳入" : "纳入修订"}</button></article>)}
    </div>
  );
}

import type { ChapterStudioView } from "@nimo/engine-contracts";

import { summarizeChapterContextValue } from "../../lib/chapter-context-document";

export type ChapterContextDetail = {
  readonly kind: "context-detail";
  readonly primaryLabel: string;
  readonly title: string;
  readonly preview: string;
  readonly secondary?: string;
  readonly secondaryLabel?: string;
};

/** 章节上下文卡（上一章结果 / 本章目标 / 下一章预埋）。 */
export function ChapterContextCards({
  onOpenDetail,
  studio,
}: {
  readonly onOpenDetail: (detail: ChapterContextDetail) => void;
  readonly studio: ChapterStudioView;
}) {
  const cards: readonly ChapterContextDetail[] = [
    {
      kind: "context-detail",
      title: "上一章实际结果",
      primaryLabel: "章节结果",
      preview: studio.previousSummary,
      secondary: studio.previousExitSummary,
      secondaryLabel: "退出点",
    },
    {
      kind: "context-detail",
      title: "本章目标",
      primaryLabel: "写作目标",
      preview: studio.currentGoal,
      secondary: studio.currentOutlineSummary,
      secondaryLabel: "章节方案",
    },
    {
      kind: "context-detail",
      title: "下一章预埋",
      primaryLabel: "预埋目标",
      preview: studio.nextGoal,
    },
  ];

  return (
    <section className="studio-context-stack" aria-label="章节上下文">
      {cards.map((card) => (
        <button
          aria-label={`查看${card.title}详情`}
          className="studio-context-card"
          key={card.title}
          onClick={() => onOpenDetail(card)}
          title={`查看${card.title}详情`}
          type="button"
        >
          <span>{card.title}</span>
          <span className="studio-context-preview">
            {summarizeChapterContextValue(card.preview)}
          </span>
          <span className="studio-context-open">查看详情 →</span>
        </button>
      ))}
    </section>
  );
}

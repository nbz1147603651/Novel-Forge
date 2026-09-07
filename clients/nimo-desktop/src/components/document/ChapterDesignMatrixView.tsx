import { useMemo } from "react";

import { DocHintBlock, DocTag } from "./DocumentPrimitives";
import { num, parseJsonContent, str, strList, type JsonDict } from "./document-parse";

function asDict(value: unknown): JsonDict {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as JsonDict) : {};
}

/** Render one chapter's structural-constraint card (PySide6 `_chapter_design_matrix_item_html`). */
function ChapterMatrixCard({ chapter }: { readonly chapter: JsonDict }) {
  const chapterNumber = chapter.chapter_number;
  const numLabel = chapterNumber === undefined || chapterNumber === null ? "?" : String(chapterNumber);
  const plotDuties = strList(chapter, "plot_duties");
  const castPlan = asDict(chapter.cast_plan);
  const emotionalBrief = asDict(chapter.emotional_brief);
  const pov = str(castPlan, "pov_entity_id") || str(chapter, "pov_entity_id");
  const required = castPlan.required_character_ids;
  const requiredCount = Array.isArray(required) ? required.length : 0;
  const support = castPlan.support_character_ids;
  const supportCount = Array.isArray(support) ? support.length : 0;
  const pressureEvidence = strList(emotionalBrief, "pressure_evidence");
  const arcEvidence = strList(emotionalBrief, "arc_evidence");
  const sceneGoals = strList(chapter, "scene_design_goals");
  const duties = plotDuties.length > 0 ? plotDuties : sceneGoals;

  return (
    <div className="doc-section doc-matrix-card">
      <div className="doc-matrix-head">
        <span className="doc-matrix-title">第 {numLabel} 章 · 结构约束</span>
        {pov.length > 0 && <DocTag>{pov}</DocTag>}
        {requiredCount > 0 && <DocTag muted>必需角色 {requiredCount}</DocTag>}
        {supportCount > 0 && <DocTag muted>支援角色 {supportCount}</DocTag>}
      </div>
      {pressureEvidence.length > 0 && (
        <div className="doc-matrix-pressure">压力证据：{pressureEvidence.slice(0, 2).join("；")}</div>
      )}
      {arcEvidence.length > 0 && (
        <div className="doc-matrix-arc">情感弧证据：{arcEvidence.slice(0, 2).join("；")}</div>
      )}
      {duties.length > 0 && (
        <div className="doc-matrix-duties">
          {duties.slice(0, 4).map((duty, index) => (
            // eslint-disable-next-line react/no-array-index-key
            <div key={index}>· {duty}</div>
          ))}
          {duties.length > 4 && <DocTag muted>另有 {duties.length - 4} 条约束</DocTag>}
        </div>
      )}
    </div>
  );
}

/**
 * Rich renderer for plans/chapter_design_matrix.json (章节设计矩阵) — port of
 * PySide6 `render_chapter_design_matrix`
 * (novel_forge/desktop/pages/document_renderer/story_artifacts/chapter_matrix.py).
 */
export function ChapterDesignMatrixView({ content }: { readonly content: string | undefined }) {
  const data = useMemo(() => parseJsonContent(content), [content]);

  if (data === null) return null;

  const rawChapters = data.chapters;
  const chapters = Array.isArray(rawChapters)
    ? rawChapters.filter((item): item is JsonDict => item !== null && typeof item === "object" && !Array.isArray(item))
    : [];
  const total = num(data, "total_chapters") || chapters.length || "?";
  const entityCatalog = data.entity_catalog;
  const entityCount = Array.isArray(entityCatalog) ? entityCatalog.length : 0;

  return (
    <div className="doc-rich">
      <div className="doc-center-tags">
        <DocTag muted>结构覆盖 {total} 章</DocTag>
        <DocTag muted>实体 {entityCount}</DocTag>
      </div>
      <DocHintBlock>
        章节设计矩阵是章节大纲生成前的结构约束：锁定角色 ID，并提供蓝图压力与情感弧证据。它不替 AI
        编写情绪计划，也不是可断点续跑的章节大纲正文；正文仍以 <code>outline.json</code> 为准。
      </DocHintBlock>
      {chapters.map((chapter, index) => (
        <ChapterMatrixCard chapter={chapter} key={String(chapter.chapter_number ?? index)} />
      ))}
    </div>
  );
}

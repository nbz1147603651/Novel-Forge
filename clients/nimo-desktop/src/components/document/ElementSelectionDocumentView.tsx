import { useMemo } from "react";

import {
  DocH2,
  DocHintBlock,
  DocRuleItem,
  DocSection,
  DocTag,
  DocText,
} from "./DocumentPrimitives";
import { parseJsonContent, str, strList, type JsonDict } from "./document-parse";

/** Render one element group (必要项 / 扩展项) as rule-item cards. */
function ElementGroup({ title, elements }: { readonly title: string; readonly elements: unknown }) {
  if (!Array.isArray(elements) || elements.length === 0) return null;
  return (
    <>
      <DocH2>{title}</DocH2>
      {elements.map((raw, index) => {
        if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
        const element = raw as JsonDict;
        const name = str(element, "name");
        const elementId = str(element, "element_id");
        const category = str(element, "category");
        const desc = str(element, "description");
        const rationale = str(element, "rationale");
        const reason = str(element, "selection_reason");
        const promptHint = str(element, "prompt_hint");
        const uiHint = str(element, "ui_hint");
        const genres = strList(element, "recommended_genres");

        const tags: string[] = [];
        if (elementId.length > 0) tags.push(elementId);
        if (category.length > 0) tags.push(category);
        if (uiHint.length > 0) tags.push(`UI:${uiHint}`);

        return (
          <DocRuleItem key={elementId || index}>
            <span className="doc-element-name">{name}</span>
            {tags.length > 0 && (
              <DocTag muted>
                <span className="doc-element-tags">{tags.join(" | ")}</span>
              </DocTag>
            )}
            {desc.length > 0 && (
              <div className="doc-element-desc">
                <DocText text={desc} />
              </div>
            )}
            {rationale.length > 0 && <div className="doc-element-rationale">价值：{rationale}</div>}
            {reason.length > 0 && <div className="doc-element-reason">选用原因：{reason}</div>}
            {genres.length > 0 && <div className="doc-element-genres">推荐题材：{genres.join("、")}</div>}
            {promptHint.length > 0 && <div className="doc-element-prompt">提示词：{promptHint}</div>}
          </DocRuleItem>
        );
      })}
    </>
  );
}

/**
 * Rich renderer for plans/blueprint_elements_selection.json (要素与风格) — port
 * of PySide6 `render_blueprint_element_selection`
 * (novel_forge/desktop/pages/document_renderer/story_artifacts/blueprint_element.py).
 */
export function ElementSelectionDocumentView({ content }: { readonly content: string | undefined }) {
  const data = useMemo(() => parseJsonContent(content), [content]);

  if (data === null) return null;

  const mode = str(data, "mode");
  const version = str(data, "library_version");
  const summary = str(data, "selector_summary");
  const genres = strList(data, "genre_inference");
  const constraints = strList(data, "focus_constraints");

  const hasAny =
    mode.length > 0 ||
    version.length > 0 ||
    summary.length > 0 ||
    genres.length > 0 ||
    constraints.length > 0 ||
    Array.isArray(data.required_elements) ||
    Array.isArray(data.extension_elements);

  return (
    <div className="doc-rich">
      {!hasAny && <div className="doc-empty-hint">暂无叙事要素选择数据</div>}

      {(mode.length > 0 || version.length > 0) && (
        <div className="doc-center-tags">
          {mode.length > 0 && <DocTag muted>模式：{mode}</DocTag>}
          {version.length > 0 && <DocTag muted>库版本：{version}</DocTag>}
        </div>
      )}

      {summary.length > 0 && (
        <DocHintBlock>
          <DocText text={summary} />
        </DocHintBlock>
      )}

      {genres.length > 0 && (
        <DocSection title="题材判断">{genres.join("、")}</DocSection>
      )}

      {constraints.length > 0 && (
        <>
          <DocH2>执行约束</DocH2>
          {constraints.map((item, index) => (
            <DocRuleItem key={index}>· {item}</DocRuleItem>
          ))}
        </>
      )}

      <ElementGroup elements={data.required_elements} title="必要项" />
      <ElementGroup elements={data.extension_elements} title="扩展项" />
    </div>
  );
}

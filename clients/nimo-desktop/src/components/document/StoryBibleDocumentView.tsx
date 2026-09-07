import { useMemo } from "react";

import { BIBLE_FIELD_LABELS } from "../../lib/document-labels";
import {
  DocH2,
  DocRuleItem,
  DocSection,
  DocTag,
  DocText,
  DocThemeItem,
} from "./DocumentPrimitives";
import { parseJsonContent, str, strList, type JsonDict } from "./document-parse";

const SEVERITY_LABELS: Readonly<Record<string, string>> = { hard: "硬约束", soft: "软约束" };
const CATEGORY_LABELS: Readonly<Record<string, string>> = {
  time_space: "时空",
  social_language: "社会/语言",
  resource_material: "资源/物质",
  information: "信息",
  ability_tech: "能力/技术",
  general: "通用",
};

const RULE_DETAIL_FIELDS: readonly (readonly [string, string])[] = [
  ["trigger_conditions", "触发条件"],
  ["allowed_behavior", "允许行为"],
  ["forbidden_behavior", "禁止行为"],
  ["cost_or_consequence", "代价/后果"],
  ["exceptions", "例外"],
];

function ruleDetailText(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item).trim())
      .filter((item) => item.length > 0)
      .join("；");
  }
  return value === null || value === undefined ? "" : String(value).trim();
}

/** Port of PySide6 `_render_world_rule_book` — structured rule cards. */
function WorldRuleBook({ ruleBook }: { readonly ruleBook: JsonDict }) {
  const description = str(ruleBook, "description");
  const rawRules = ruleBook.rules;
  const rules = Array.isArray(rawRules) ? rawRules.filter((r): r is JsonDict => r !== null && typeof r === "object") : [];

  return (
    <>
      {description.length > 0 && (
        <DocSection title="规则账本概述">
          <DocText text={description} />
        </DocSection>
      )}
      {rules.length > 0 && <DocH2>世界规则账本</DocH2>}
      {rules.map((rule, index) => {
        const content = str(rule, "content");
        if (content.length === 0) return null;
        const ruleId = str(rule, "rule_id");
        const severity = str(rule, "severity");
        const category = str(rule, "category");
        const alwaysOn = Boolean(rule.always_on);

        const detailRows: { label: string; value: string }[] = [];
        for (const [key, label] of RULE_DETAIL_FIELDS) {
          const text = ruleDetailText(rule[key]);
          if (text.length > 0) detailRows.push({ label, value: text });
        }
        const applicabilityTags = strList(rule, "applicability_tags");
        if (applicabilityTags.length > 0) {
          detailRows.push({ label: "适用标签", value: applicabilityTags.join("、") });
        }

        return (
          // eslint-disable-next-line react/no-array-index-key
          <div className="doc-rule-card" key={ruleId || index}>
            {ruleId.length > 0 && <div className="doc-rule-id">{ruleId}</div>}
            {(severity.length > 0 || category.length > 0 || alwaysOn) && (
              <div className="doc-rule-badges">
                {severity.length > 0 && (
                  <DocTag muted={severity !== "hard"}>{SEVERITY_LABELS[severity] ?? severity}</DocTag>
                )}
                {category.length > 0 && <DocTag muted>{CATEGORY_LABELS[category] ?? category}</DocTag>}
                {alwaysOn && <DocTag>始终生效</DocTag>}
              </div>
            )}
            <div className="doc-rule-content">
              <DocText text={content} />
            </div>
            {detailRows.length > 0 && (
              <div className="doc-rule-details">
                {detailRows.map((row) => (
                  <div className="doc-rule-detail-row" key={row.label}>
                    <span className="doc-rule-detail-label">{row.label}</span>
                    <span className="doc-rule-detail-value">
                      <DocText text={row.value} />
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

/**
 * Rich renderer for story_bible.json (世界观) — port of PySide6
 * `render_story_bible` (novel_forge/desktop/pages/document_renderer/reports/spec.py).
 *
 * Renders the six bible field sections, the structured world_rule_book cards,
 * the 世界规则 rule-item list and the 核心主题 theme-item list.
 */
export function StoryBibleDocumentView({ content }: { readonly content: string | undefined }) {
  const data = useMemo(() => parseJsonContent(content), [content]);

  if (data === null) return null;

  const rules = strList(data, "rules");
  const themes = strList(data, "themes");
  const worldRuleBook = data.world_rule_book;
  const hasRuleBook = worldRuleBook !== null && typeof worldRuleBook === "object" && !Array.isArray(worldRuleBook);

  return (
    <div className="doc-rich">
      {Object.entries(BIBLE_FIELD_LABELS).map(([key, label]) => {
        const value = str(data, key);
        if (value.length === 0) return null;
        return (
          <DocSection key={key} title={label}>
            <DocText text={value} />
          </DocSection>
        );
      })}

      {hasRuleBook && <WorldRuleBook ruleBook={worldRuleBook as JsonDict} />}

      {rules.length > 0 && (
        <>
          <DocH2>世界规则</DocH2>
          {rules.map((rule, index) => (
            <DocRuleItem key={index}>
              <DocText text={rule} />
            </DocRuleItem>
          ))}
        </>
      )}

      {themes.length > 0 && (
        <>
          <DocH2>核心主题</DocH2>
          {themes.map((theme, index) => (
            <DocThemeItem key={index}>
              <DocText text={theme} />
            </DocThemeItem>
          ))}
        </>
      )}
    </div>
  );
}

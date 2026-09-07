import { useState } from "react";

import type {
  RunEfficiencyView,
  RunInsightStatus,
  RunInsightView,
} from "@nimo/engine-contracts";

import { useLocale } from "../lib/i18n";
import { OverlaySurface } from "./OverlaySurface";

interface RunInsightStripProps {
  readonly efficiency?: RunEfficiencyView;
  readonly insights?: readonly RunInsightView[];
  readonly onOpenArtifact?: (stepKey: string) => void;
}

const labels = {
  intent: { zh: "意图保护", en: "Intent Guard" },
  research: { zh: "研究与证据", en: "Research" },
  revision: { zh: "修订与回滚", en: "Revision" },
  final_verify: { zh: "最终验证", en: "Final Verify" },
} as const;

const statusLabels: Record<RunInsightStatus, { readonly zh: string; readonly en: string }> = {
  inactive: { zh: "未触发", en: "Inactive" },
  pending: { zh: "待执行", en: "Pending" },
  running: { zh: "执行中", en: "Running" },
  success: { zh: "已通过", en: "Passed" },
  warning: { zh: "需留意", en: "Warning" },
  blocked: { zh: "已阻断", en: "Blocked" },
  skipped: { zh: "已跳过", en: "Skipped" },
  rolled_back: { zh: "已回滚", en: "Rolled back" },
};

function localizedInsightLabel(insight: RunInsightView, locale: "zh" | "en"): string {
  const known = labels[insight.id as keyof typeof labels];
  return known?.[locale] ?? insight.label;
}

function efficiencyItems(
  efficiency: RunEfficiencyView,
  locale: "zh" | "en",
): readonly { readonly label: string; readonly value: string }[] {
  const numberLocale = locale === "zh" ? "zh-CN" : "en-US";
  return [
    { label: locale === "zh" ? "模型调用" : "Model calls", value: String(efficiency.llmCalls) },
    { label: "Tokens", value: efficiency.totalTokens.toLocaleString(numberLocale) },
    { label: locale === "zh" ? "费用" : "Cost", value: `$${efficiency.costUsd.toFixed(4)}` },
    { label: locale === "zh" ? "MCP 查询" : "MCP queries", value: String(efficiency.researchQueries) },
    { label: locale === "zh" ? "证据缓存" : "Evidence cache", value: String(efficiency.researchCacheHits) },
    { label: locale === "zh" ? "语义修改" : "Semantic edits", value: String(efficiency.semanticMutations) },
    { label: locale === "zh" ? "报告刷新" : "Report refreshes", value: String(efficiency.reportRefreshes) },
    { label: locale === "zh" ? "回滚" : "Rollbacks", value: String(efficiency.rollbacks) },
    { label: locale === "zh" ? "终稿验证" : "Final verifies", value: String(efficiency.finalHashVerifications) },
  ];
}

/**
 * Compact Engine-owned run transparency. The client localizes stable labels
 * but never reconstructs intent, research, repair, or verification semantics.
 */
export function RunInsightStrip({ efficiency, insights = [], onOpenArtifact }: RunInsightStripProps) {
  const locale = useLocale();
  const [selected, setSelected] = useState<RunInsightView | null>(null);
  if (insights.length === 0) return null;

  return (
    <>
      <section aria-label={locale === "zh" ? "章节运行透明度" : "Chapter run transparency"} className="run-insight-strip">
        {insights.map((insight) => (
          <button
            aria-haspopup="dialog"
            className={`run-insight-card is-${insight.status}`}
            key={insight.id}
            onClick={() => setSelected(insight)}
            type="button"
          >
            <span className="run-insight-card-heading">
              <strong>{localizedInsightLabel(insight, locale)}</strong>
              <small>{statusLabels[insight.status][locale]}</small>
            </span>
            <span className="run-insight-card-summary">{insight.summary}</span>
            {insight.count > 0 ? <span className="run-insight-card-count">{insight.count}</span> : null}
          </button>
        ))}
      </section>
      {selected !== null ? (
        <OverlaySurface
          ariaLabel={localizedInsightLabel(selected, locale)}
          backdropClassName="run-insight-drawer-backdrop"
          className="run-insight-drawer"
          onClose={() => setSelected(null)}
        >
          <header className="run-insight-drawer-header">
            <div>
              <span className="section-kicker">{locale === "zh" ? "运行透明度" : "RUN INSIGHT"}</span>
              <h2>{localizedInsightLabel(selected, locale)}</h2>
              <span className={`run-insight-status is-${selected.status}`}>
                {statusLabels[selected.status][locale]}
              </span>
            </div>
            <button
              aria-label={locale === "zh" ? "关闭详情" : "Close details"}
              className="run-insight-drawer-close"
              onClick={() => setSelected(null)}
              type="button"
            >
              ×
            </button>
          </header>
          <div className="run-insight-drawer-body">
            <section>
              <h3>{locale === "zh" ? "本次结论" : "Outcome"}</h3>
              <strong>{selected.summary}</strong>
              <p>{selected.detail || (locale === "zh" ? "Engine 未提供更多说明。" : "No additional Engine detail.")}</p>
            </section>
            {efficiency !== undefined ? (
              <section>
                <h3>{locale === "zh" ? "效率摘要" : "Efficiency"}</h3>
                <dl className="run-insight-efficiency">
                  {efficiencyItems(efficiency, locale).map((item) => (
                    <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>
                  ))}
                </dl>
              </section>
            ) : null}
          </div>
          <footer className="run-insight-drawer-actions">
            {selected.artifactStepKey.length > 0 && onOpenArtifact !== undefined ? (
              <button
                className="button button-secondary"
                onClick={() => {
                  onOpenArtifact(selected.artifactStepKey);
                  setSelected(null);
                }}
                type="button"
              >
                {locale === "zh" ? "查看证据包 / 报告" : "View evidence / report"}
              </button>
            ) : null}
            <button className="button button-primary" onClick={() => setSelected(null)} type="button">
              {locale === "zh" ? "关闭" : "Close"}
            </button>
          </footer>
        </OverlaySurface>
      ) : null}
    </>
  );
}

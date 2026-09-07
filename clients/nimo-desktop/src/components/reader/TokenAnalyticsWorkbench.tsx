import { useEffect, useMemo, useState } from "react";

import type {
  EngineCommandClient,
  ProjectReaderArtifactView,
} from "@nimo/engine-contracts";

import { parseJsonContent } from "../document/document-parse";

type TokenPanelId = "overview" | "models" | "steps" | "pricing";
type TokenStepFilter = "all" | "init" | "chapter" | "repair";
type TokenPriceUnit = "million" | "thousand";

interface TokenAnalyticsStep {
  readonly calls: number;
  readonly completionTokens: number;
  readonly costUsd: number;
  readonly kindTokens: Readonly<Record<Exclude<TokenStepFilter, "all">, number>>;
  readonly promptTokens: number;
  readonly runs: number;
  readonly step: string;
  readonly tokens: number;
}

interface TokenAnalyticsModel {
  readonly calls: number;
  readonly completionTokens: number;
  readonly costUsd: number;
  readonly key: string;
  readonly label: string;
  readonly promptTokens: number;
  readonly tokens: number;
}

interface TokenAnalyticsPayload {
  readonly loggedCostUsd: number;
  readonly models: readonly TokenAnalyticsModel[];
  readonly runCount: number;
  readonly steps: readonly TokenAnalyticsStep[];
  readonly totalCallCount: number;
  readonly totalCompletionTokens: number;
  readonly totalPromptTokens: number;
  readonly totalTokens: number;
}

const tokenPanels: readonly { readonly id: TokenPanelId; readonly label: string }[] = [
  { id: "overview", label: "总览" },
  { id: "models", label: "模型" },
  { id: "steps", label: "步骤" },
  { id: "pricing", label: "价格" },
];

const emptyTokenAnalytics: TokenAnalyticsPayload = {
  loggedCostUsd: 0,
  models: [],
  runCount: 0,
  steps: [],
  totalCallCount: 0,
  totalCompletionTokens: 0,
  totalPromptTokens: 0,
  totalTokens: 0,
};

function tokenNumber(value: unknown): number {
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
}

function tokenText(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim().length > 0 ? value : fallback;
}

function tokenRecords(value: unknown): readonly Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object" && !Array.isArray(item))
    : [];
}

function parseTokenAnalytics(artifact: ProjectReaderArtifactView): TokenAnalyticsPayload {
  const raw = parseJsonContent(artifact.content);
  if (raw === null) return emptyTokenAnalytics;
  return {
    loggedCostUsd: tokenNumber(raw.logged_cost_usd),
    models: tokenRecords(raw.models).map((model) => ({
      calls: tokenNumber(model.calls),
      completionTokens: tokenNumber(model.completion_tokens),
      costUsd: tokenNumber(model.cost_usd),
      key: tokenText(model.key, tokenText(model.label, "unknown/unknown")),
      label: tokenText(model.display_name, tokenText(model.label, "未知模型")),
      promptTokens: tokenNumber(model.prompt_tokens),
      tokens: tokenNumber(model.tokens),
    })),
    runCount: tokenNumber(raw.run_count),
    steps: tokenRecords(raw.steps).map((step) => {
      const kinds = step.kind_tokens !== null && typeof step.kind_tokens === "object" && !Array.isArray(step.kind_tokens)
        ? step.kind_tokens as Record<string, unknown>
        : {};
      return {
        calls: tokenNumber(step.calls),
        completionTokens: tokenNumber(step.completion_tokens),
        costUsd: tokenNumber(step.cost_usd),
        kindTokens: { init: tokenNumber(kinds.init), chapter: tokenNumber(kinds.chapter), repair: tokenNumber(kinds.repair) },
        promptTokens: tokenNumber(step.prompt_tokens),
        runs: tokenNumber(step.runs),
        step: tokenText(step.step, "未命名步骤"),
        tokens: tokenNumber(step.tokens),
      };
    }),
    totalCallCount: tokenNumber(raw.total_call_count),
    totalCompletionTokens: tokenNumber(raw.total_completion_tokens),
    totalPromptTokens: tokenNumber(raw.total_prompt_tokens),
    totalTokens: tokenNumber(raw.total_tokens),
  };
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}m`;
  if (value >= 10_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toLocaleString("zh-CN");
}

function formatCost(value: number, currency: "USD" | "CNY" | "EUR"): string {
  const symbol = currency === "CNY" ? "¥" : currency === "EUR" ? "€" : "$";
  return `${symbol}${value.toLocaleString("zh-CN", { maximumFractionDigits: value >= 100 ? 0 : 2, minimumFractionDigits: value > 0 && value < 10 ? 2 : 0 })}`;
}

function perMillionPrice(value: number, unit: TokenPriceUnit): number {
  return unit === "thousand" ? value * 1_000 : value;
}

function sourceTab<T extends string>({ activeId, ariaLabel, items, onSelect }: { readonly activeId: T; readonly ariaLabel: string; readonly items: readonly { readonly id: T; readonly label: string }[]; readonly onSelect: (id: T) => void }) {
  return (
    <div className="reader-source-tabs" role="tablist" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          aria-selected={item.id === activeId}
          className={item.id === activeId ? "is-active" : ""}
          key={item.id}
          onClick={() => onSelect(item.id)}
          role="tab"
          type="button"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

/** Source-shaped Token analytics preferences backed by the legacy durable file. */
export function TokenAnalyticsWorkbench({ artifact, commandClient, onOperation, projectId }: { readonly artifact: ProjectReaderArtifactView; readonly commandClient: EngineCommandClient; readonly onOperation: (message: string) => void; readonly projectId: string }) {
  const [panel, setPanel] = useState<TokenPanelId>("overview");
  const [currency, setCurrency] = useState<"USD" | "CNY" | "EUR">("USD");
  const [exchangeRate, setExchangeRate] = useState("1.000000");
  const [stepFilter, setStepFilter] = useState<TokenStepFilter>("all");
  const [pricePerMillion, setPricePerMillion] = useState(8);
  const [priceUnit, setPriceUnit] = useState<TokenPriceUnit>("million");
  const [modelPricePerMillion, setModelPricePerMillion] = useState<Readonly<Record<string, number>>>({});
  const [modelPriceUnit, setModelPriceUnit] = useState<Readonly<Record<string, TokenPriceUnit>>>({});
  const [preferencesDirty, setPreferencesDirty] = useState(false);
  const [preferencesRevision, setPreferencesRevision] = useState<string | undefined>();
  const [savingPreferences, setSavingPreferences] = useState(false);
  const analytics = useMemo(() => parseTokenAnalytics(artifact), [artifact]);
  const activeStepFilter = stepFilter === "all" ? undefined : stepFilter;
  const visibleSteps = analytics.steps.filter((step) => activeStepFilter === undefined || step.kindTokens[activeStepFilter] > 0);
  const currentRate = Number(exchangeRate);
  const normalizedRate = Number.isFinite(currentRate) && currentRate > 0 ? currentRate : 1;
  const effectivePrice = perMillionPrice(pricePerMillion, priceUnit);
  const priceRows = [
    { key: "default", label: "默认模型价格", price: pricePerMillion, unit: priceUnit },
    ...analytics.models.map((model) => ({
      key: model.key,
      label: model.label,
      price: modelPricePerMillion[model.key] ?? pricePerMillion,
      unit: modelPriceUnit[model.key] ?? priceUnit,
    })),
  ];
  const topSteps = [...analytics.steps].sort((left, right) => right.tokens - left.tokens).slice(0, 3);
  const topModels = [...analytics.models].sort((left, right) => right.tokens - left.tokens).slice(0, 3);
  const splitTokens = analytics.totalPromptTokens + analytics.totalCompletionTokens;
  const modelTokens = analytics.models.reduce((total, model) => total + model.tokens, 0);
  const stepTokens = analytics.steps.reduce((total, step) => total + step.tokens, 0);
  const hasUsage = analytics.totalTokens > 0 || splitTokens > 0 || modelTokens > 0 || stepTokens > 0;
  const totalTokens = Math.max(
    analytics.totalTokens,
    splitTokens,
    modelTokens,
    stepTokens,
    1,
  );
  const reconciledFromDetails = totalTokens > analytics.totalTokens;
  const inputShare = Math.min(100, Math.round(analytics.totalPromptTokens / totalTokens * 100));
  const outputShare = Math.min(100 - inputShare, Math.round(analytics.totalCompletionTokens / totalTokens * 100));
  const unclassifiedTokens = Math.max(
    0,
    totalTokens - splitTokens,
  );
  const unclassifiedShare = Math.max(0, 100 - inputShare - outputShare);
  const splitCoverage = totalTokens > 0
    ? Math.min(100, Math.round(splitTokens / totalTokens * 100))
    : 0;
  const modelCostScale = modelTokens > totalTokens ? totalTokens / modelTokens : 1;
  const modelCostUsd = analytics.models.reduce((total, model) => {
    const modelPrice = perMillionPrice(
      modelPricePerMillion[model.key] ?? pricePerMillion,
      modelPriceUnit[model.key] ?? priceUnit,
    );
    return total + model.tokens * modelCostScale / 1_000_000 * modelPrice;
  }, 0);
  const modelLoggedCostUsd = analytics.models.reduce((total, model) => total + model.costUsd, 0);
  const modelCoveredTokens = Math.min(totalTokens, modelTokens);
  const unpricedTokens = Math.max(0, totalTokens - modelCoveredTokens);
  const estimatedUsd = modelTokens > 0
    ? modelCostUsd + unpricedTokens / 1_000_000 * effectivePrice
    : totalTokens / 1_000_000 * effectivePrice;
  const resolvedCostUsd = analytics.loggedCostUsd > 0
    ? analytics.loggedCostUsd
    : modelLoggedCostUsd > 0 ? modelLoggedCostUsd : estimatedUsd;
  const displayedCost = currency === "USD" ? resolvedCostUsd : resolvedCostUsd * normalizedRate;
  const topStepPeak = topSteps[0]?.tokens ?? 0;
  const topModelPeak = topModels[0]?.tokens ?? 0;
  const modelCoverage = totalTokens > 0 ? Math.min(100, Math.round(modelCoveredTokens / totalTokens * 100)) : 0;
  const costSource = analytics.loggedCostUsd > 0
    ? "运行日志实付"
    : modelLoggedCostUsd > 0 ? "模型明细实付"
    : modelTokens > 0 ? "按模型单价预估" : "按默认单价预估";
  const hasActualCost = analytics.loggedCostUsd > 0 || modelLoggedCostUsd > 0;
  const costExplanation = analytics.loggedCostUsd > 0
    ? "已优先采用运行日志中的实际金额；价格设置仅用于无账单记录时的预估。"
    : modelLoggedCostUsd > 0
      ? "项目总账单缺失，已汇总模型明细中的实际金额。"
    : modelTokens > 0
      ? `模型计价覆盖 ${modelCoverage}%；未归属部分按默认单价补足。`
      : "当前没有模型级明细，按默认单价估算。";
  const tokenRingStyle = {
    background: `conic-gradient(var(--nf-accent-primary) 0 ${inputShare}%, var(--nf-status-success) ${inputShare}% ${inputShare + outputShare}%, var(--nf-text-muted-soft) ${inputShare + outputShare}% 100%)`,
  };
  useEffect(() => {
    let disposed = false;
    void commandClient.loadTokenDashboardPreferences(projectId).then((preferences) => {
      if (disposed) return;
      setCurrency(preferences.currency);
      setExchangeRate(String(preferences.exchangeRates[preferences.currency] ?? 1));
      setStepFilter(preferences.stepWaterfallFilter);
      setPricePerMillion(preferences.pricePerMillion);
      setPriceUnit(preferences.priceUnit);
      setModelPricePerMillion(preferences.modelPricePerMillion);
      setModelPriceUnit(preferences.modelPriceUnit);
      setPreferencesRevision(preferences.revision);
      setPreferencesDirty(false);
    }).catch((error) => {
      if (!disposed) onOperation(`Token 追踪偏好加载失败：${error instanceof Error ? error.message : String(error)}`);
    });
    return () => { disposed = true; };
  }, [commandClient, onOperation, projectId]);
  const updateCurrency = (value: "USD" | "CNY" | "EUR") => {
    setCurrency(value);
    setPreferencesDirty(true);
  };
  const updatePrice = (key: string, rawValue: string) => {
    const nextValue = Number(rawValue);
    if (!Number.isFinite(nextValue) || nextValue < 0) return;
    if (key === "default") setPricePerMillion(nextValue);
    else setModelPricePerMillion((current) => ({ ...current, [key]: nextValue }));
    setPreferencesDirty(true);
  };
  const updatePriceUnit = (key: string, value: TokenPriceUnit) => {
    if (key === "default") setPriceUnit(value);
    else setModelPriceUnit((current) => ({ ...current, [key]: value }));
    setPreferencesDirty(true);
  };
  const savePreferences = async () => {
    if (savingPreferences || !preferencesDirty) return;
    setSavingPreferences(true);
    try {
      const numericRate = Number(exchangeRate);
      const result = await commandClient.saveTokenDashboardPreferences({
        kind: "save_token_dashboard_preferences",
        projectId,
        currency,
        exchangeRates: { [currency]: Number.isFinite(numericRate) && numericRate > 0 ? numericRate : 1 },
        stepWaterfallFilter: stepFilter,
        pricePerMillion,
        priceUnit,
        modelPricePerMillion,
        modelPriceUnit,
        ...(preferencesRevision === undefined ? {} : { expectedRevision: preferencesRevision }),
      });
      if (result.status === "conflict") {
        if (result.preferences !== undefined) {
          setCurrency(result.preferences.currency);
          setExchangeRate(String(result.preferences.exchangeRates[result.preferences.currency] ?? 1));
          setStepFilter(result.preferences.stepWaterfallFilter);
          setPricePerMillion(result.preferences.pricePerMillion);
          setPriceUnit(result.preferences.priceUnit);
          setModelPricePerMillion(result.preferences.modelPricePerMillion);
          setModelPriceUnit(result.preferences.modelPriceUnit);
        }
        setPreferencesRevision(result.revision);
        setPreferencesDirty(false);
        onOperation(`${result.message} 已载入引擎中的最新设置。`);
        return;
      }
      if (result.status === "rejected") {
        onOperation(`Token 追踪偏好保存失败：${result.message}`);
        return;
      }
      setPreferencesRevision(result.revision);
      setPreferencesDirty(false);
      onOperation(result.message);
    } catch (error) {
      onOperation(`Token 追踪偏好保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSavingPreferences(false);
    }
  };
  return <section aria-label="Token 追踪工作台" className="reader-token-workbench">
    <header><div>{sourceTab({ activeId: panel, ariaLabel: "Token 追踪分类", items: tokenPanels, onSelect: setPanel })}</div><div><button className="button button-primary" disabled={!preferencesDirty || savingPreferences} onClick={() => { void savePreferences(); }} type="button">{savingPreferences ? "保存中…" : preferencesDirty ? "保存设置 *" : "保存设置"}</button><button className="button button-secondary" onClick={() => onOperation("Token 用量来自运行日志；刷新会在下次打开面板时重新汇总。")} type="button">刷新</button></div></header>
    {panel === "overview" && <div className="reader-token-overview">
      <div className="reader-token-metrics">
        <section><span>累计 Token</span><strong>{formatTokens(hasUsage ? totalTokens : 0)}</strong><small>{reconciledFromDetails ? "已按明细校正总量" : "总量与明细已对齐"}</small></section>
        <section><span>模型调用</span><strong>{analytics.totalCallCount.toLocaleString("zh-CN")}</strong><small>{analytics.runCount.toLocaleString("zh-CN")} 个运行批次 · {analytics.steps.length.toLocaleString("zh-CN")} 个记录步骤</small></section>
        <section><span>{hasActualCost ? "实际费用" : "预估费用"}</span><strong>{formatCost(displayedCost, currency)}</strong><small>{costSource}</small></section>
      </div>
      <dl className="reader-token-summary">
        <div><dt>输入</dt><dd>{formatTokens(analytics.totalPromptTokens)}</dd></div>
        <div><dt>输出</dt><dd>{formatTokens(analytics.totalCompletionTokens)}</dd></div>
        <div><dt>模型明细</dt><dd>{formatTokens(modelTokens)}</dd></div>
        <div><dt>步骤明细</dt><dd>{formatTokens(stepTokens)}</dd></div>
      </dl>
      {hasUsage && <>
        <div className="reader-token-insight-grid">
          <section aria-label="输入输出构成" className="reader-token-breakdown">
            <header><strong>输入 / 输出构成</strong><span>拆分覆盖 {splitCoverage}%</span></header>
            <div className="reader-token-composition">
              <div className="reader-token-donut" role="img" aria-label={`输入 ${inputShare}%，输出 ${outputShare}%${unclassifiedTokens > 0 ? `，未拆分 ${unclassifiedShare}%` : ""}`} style={tokenRingStyle}><div><strong>{formatTokens(totalTokens)}</strong><span>Token 总量</span></div></div>
              <div className="reader-token-composition-legend">
                <div className="reader-token-split-legend"><span><i className="is-input" />输入</span><strong>{formatTokens(analytics.totalPromptTokens)}</strong><small>{inputShare}%</small></div>
                <div className="reader-token-split-legend"><span><i className="is-output" />输出</span><strong>{formatTokens(analytics.totalCompletionTokens)}</strong><small>{outputShare}%</small></div>
                {unclassifiedTokens > 0 && <div className="reader-token-split-legend"><span><i className="is-unclassified" />未拆分</span><strong>{formatTokens(unclassifiedTokens)}</strong><small>{unclassifiedShare}%</small></div>}
              </div>
            </div>
            <div className="reader-token-split-track" aria-hidden="true"><i className="is-input" style={{ width: `${inputShare}%` }} /><i className="is-output" style={{ width: `${outputShare}%` }} />{unclassifiedTokens > 0 && <i className="is-unclassified" style={{ width: `${unclassifiedShare}%` }} />}</div>
          </section>
          <section aria-label="费用计算" className="reader-token-cost-card">
            <header><strong>费用计算</strong><span>{costSource}</span></header>
            <div className="reader-token-cost-formula" aria-label={costExplanation}>{hasActualCost ? <><span>运行明细</span><i>→</i></> : <><span>{formatTokens(totalTokens)}</span><i>×</i><span>{modelTokens > 0 ? "模型单价" : "默认单价"}</span><i>=</i></>}<strong>{formatCost(displayedCost, currency)}</strong></div>
            <p>{costExplanation}</p>
            <dl><div><dt>模型计价覆盖</dt><dd>{modelCoverage}%</dd></div><div><dt>默认单价补足</dt><dd>{formatTokens(unpricedTokens)}</dd></div></dl>
          </section>
        </div>
        <div className="reader-token-overview-detail">
          <section aria-label="主要消耗步骤" className="reader-token-top-steps"><header><strong>主要消耗步骤</strong><span>{topSteps.length} 项</span></header>{topSteps.map((step) => <div className="reader-token-step" key={step.step}><span title={step.step}>{step.step}</span><span className="reader-token-step-track"><i style={{ width: `${topStepPeak > 0 ? Math.max(4, Math.round(step.tokens / topStepPeak * 100)) : 0}%` }} /></span><strong>{formatTokens(step.tokens)}</strong><small>{formatCost(step.costUsd, "USD")}</small></div>)}</section>
          <section aria-label="主要消耗模型" className="reader-token-top-models"><header><strong>主要消耗模型</strong><span>{topModels.length} 项</span></header>{topModels.length > 0 ? topModels.map((model) => <div className="reader-token-step" key={model.key}><span title={model.label}>{model.label}</span><span className="reader-token-step-track"><i style={{ width: `${topModelPeak > 0 ? Math.max(4, Math.round(model.tokens / topModelPeak * 100)) : 0}%` }} /></span><strong>{formatTokens(model.tokens)}</strong><small>{Math.round(model.tokens / totalTokens * 100)}%</small></div>) : <p className="reader-token-empty">暂无模型级调用记录。</p>}</section>
        </div>
      </>}
      <p>{hasUsage ? "总量会在汇总、输入/输出、模型与步骤明细之间自动取完整覆盖值；费用优先采用真实账单，缺失部分才使用已保存的价格设置。" : "当前项目尚未记录模型调用。执行立项、章节或修复流程后，这里会自动汇总运行日志。"}</p>
    </div>}
    {panel === "models" && <div className="reader-token-table" role="table" aria-label="模型用量"><div role="row"><span role="columnheader">模型</span><span role="columnheader">调用次数</span><span role="columnheader">Tokens</span><span role="columnheader">费用</span></div>{analytics.models.length > 0 ? analytics.models.map((model) => <div key={model.key} role="row"><strong role="cell">{model.label}</strong><span role="cell">{model.calls.toLocaleString("zh-CN")}</span><span role="cell">{formatTokens(model.tokens)}</span><span role="cell">{formatCost(model.costUsd, "USD")}</span></div>) : <p className="reader-token-empty">暂无模型级调用记录。</p>}</div>}
    {panel === "steps" && <div className="reader-token-steps"><div className="reader-token-filter" role="group" aria-label="步骤筛选">{([["all", "全部"], ["init", "立项"], ["chapter", "章节"], ["repair", "修复"]] as const).map(([id, label]) => <button className={stepFilter === id ? "is-active" : ""} key={id} onClick={() => { setStepFilter(id); setPreferencesDirty(true); }} type="button">{label}</button>)}</div><div className="reader-token-table" role="table" aria-label="步骤用量"><div role="row"><span role="columnheader">步骤</span><span role="columnheader">运行</span><span role="columnheader">Tokens</span><span role="columnheader">费用</span></div>{visibleSteps.length > 0 ? visibleSteps.map((step) => <div key={step.step} role="row"><strong role="cell">{step.step}</strong><span role="cell">{step.runs.toLocaleString("zh-CN")}</span><span role="cell">{formatTokens(step.tokens)}</span><span role="cell">{formatCost(step.costUsd, "USD")}</span></div>) : <p className="reader-token-empty">当前筛选下没有步骤记录。</p>}</div></div>}
    {panel === "pricing" && <div className="reader-token-pricing"><p>货币与模型价格用于预算呈现；偏好会写入项目并在重新打开后恢复，不影响模型供应商的真实计费。每模型价格留空时，会使用默认价格。</p><label>显示货币<select aria-label="显示货币" onChange={(event) => updateCurrency(event.target.value as "USD" | "CNY" | "EUR")} value={currency}><option>USD</option><option>CNY</option><option>EUR</option></select></label><label>兑换比例<input aria-label="兑换比例" disabled={currency === "USD"} inputMode="decimal" onChange={(event) => { setExchangeRate(event.target.value); setPreferencesDirty(true); }} value={exchangeRate} /></label><div className="reader-token-table reader-token-price-table" role="table" aria-label="模型价格"><div role="row"><span role="columnheader">模型</span><span role="columnheader">价格</span><span role="columnheader">单位</span><span role="columnheader">折算 / 1M</span></div>{priceRows.map((row) => <div key={row.key} role="row"><strong role="cell">{row.label}</strong><input aria-label={`${row.label} 价格`} inputMode="decimal" min="0" onChange={(event) => updatePrice(row.key, event.target.value)} type="number" value={row.price} /><select aria-label={`${row.label} 单位`} onChange={(event) => updatePriceUnit(row.key, event.target.value as TokenPriceUnit)} value={row.unit}><option value="million">每百万 Token</option><option value="thousand">每千 Token</option></select><span role="cell">{formatCost(perMillionPrice(row.price, row.unit), "USD")}</span></div>)}</div></div>}
  </section>;
}

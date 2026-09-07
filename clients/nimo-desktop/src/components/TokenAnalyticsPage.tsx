import { useCallback, useMemo, useState } from "react";

/**
 * Token Analytics page (Token 成本追踪).
 * Replicates PySide6 TokenAnalytics with cost overview, multi-currency support,
 * model pricing configuration, and step waterfall chart.
 */

// ── Constants ────────────────────────────────────────────────────────────
const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: "¥",
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
};

const EXCHANGE_RATES: Record<string, number> = {
  CNY: 1.0,
  USD: 0.14,
  EUR: 0.13,
  GBP: 0.11,
  JPY: 21.0,
};

const STEP_FILTER_OPTIONS = [
  { id: "all", label: "全部" },
  { id: "init", label: "立项" },
  { id: "chapter", label: "正文" },
  { id: "repair", label: "修复" },
] as const;

// ── Types ────────────────────────────────────────────────────────────────
interface ModelUsage {
  readonly model: string;
  readonly provider: string;
  readonly promptTokens: number;
  readonly completionTokens: number;
  readonly calls: number;
  readonly costCny: number;
}

interface StepUsage {
  readonly step: string;
  readonly kind: "init" | "chapter" | "repair";
  readonly tokens: number;
  readonly costCny: number;
  readonly chapter?: number;
}

interface RunRecord {
  readonly id: string;
  readonly startedAt: string;
  readonly kind: string;
  readonly chapter?: number;
  readonly tokens: number;
  readonly costCny: number;
  readonly status: "success" | "failed";
}

// ── Mock data ────────────────────────────────────────────────────────────
const MOCK_MODELS: readonly ModelUsage[] = [
  { model: "gpt-4o", provider: "openai", promptTokens: 125000, completionTokens: 45000, calls: 28, costCny: 136.0 },
  { model: "deepseek-chat", provider: "deepseek", promptTokens: 89000, completionTokens: 32000, calls: 15, costCny: 9.68 },
  { model: "qwen3.7-max", provider: "qwen", promptTokens: 56000, completionTokens: 21000, calls: 12, costCny: 6.16 },
];

const MOCK_STEPS: readonly StepUsage[] = [
  { step: "INIT_STORY_BIBLE", kind: "init", tokens: 12500, costCny: 10.0 },
  { step: "INIT_CHARACTER_BIBLE", kind: "init", tokens: 8900, costCny: 7.12 },
  { step: "PLAN_OUTLINE", kind: "init", tokens: 15600, costCny: 12.48 },
  { step: "DRAFT_CHAPTER", kind: "chapter", tokens: 45000, costCny: 36.0, chapter: 1 },
  { step: "WAVE_CHAPTER", kind: "chapter", tokens: 38000, costCny: 30.4, chapter: 1 },
  { step: "REPAIR_CONTINUITY", kind: "repair", tokens: 12000, costCny: 9.6, chapter: 1 },
  { step: "DRAFT_CHAPTER", kind: "chapter", tokens: 42000, costCny: 33.6, chapter: 2 },
  { step: "WAVE_CHAPTER", kind: "chapter", tokens: 35000, costCny: 28.0, chapter: 2 },
];

const MOCK_RUNS: readonly RunRecord[] = [
  { id: "run1", startedAt: "2024-01-15 10:30", kind: "长篇初始化", tokens: 37000, costCny: 29.6, status: "success" },
  { id: "run2", startedAt: "2024-01-15 14:20", kind: "章节写作", chapter: 1, tokens: 95000, costCny: 76.0, status: "success" },
  { id: "run3", startedAt: "2024-01-16 09:15", kind: "章节写作", chapter: 2, tokens: 77000, costCny: 61.6, status: "success" },
  { id: "run4", startedAt: "2024-01-16 11:45", kind: "连续性修复", chapter: 1, tokens: 12000, costCny: 9.6, status: "failed" },
];

export function TokenAnalyticsPage() {
  const [currency, setCurrency] = useState("CNY");
  const [stepFilter, setStepFilter] = useState("all");
  const [activeTab, setActiveTab] = useState<"overview" | "steps" | "models" | "pricing">("overview");
  const [defaultPrice, setDefaultPrice] = useState(8.0);
  const [modelPrices, setModelPrices] = useState<Record<string, number>>({
    "gpt-4o": 8.0,
    "deepseek-chat": 1.0,
    "qwen3.7-max": 2.0,
  });

  const totalTokens = useMemo(
    () => MOCK_MODELS.reduce((sum, m) => sum + m.promptTokens + m.completionTokens, 0),
    []
  );
  const totalPromptTokens = useMemo(
    () => MOCK_MODELS.reduce((sum, m) => sum + m.promptTokens, 0),
    []
  );
  const totalCompletionTokens = useMemo(
    () => MOCK_MODELS.reduce((sum, m) => sum + m.completionTokens, 0),
    []
  );
  const totalCalls = useMemo(
    () => MOCK_MODELS.reduce((sum, m) => sum + m.calls, 0),
    []
  );
  const totalCostCny = useMemo(
    () => MOCK_MODELS.reduce((sum, m) => sum + m.costCny, 0),
    []
  );

  const filteredSteps = useMemo(
    () => (stepFilter === "all" ? MOCK_STEPS : MOCK_STEPS.filter((s) => s.kind === stepFilter)),
    [stepFilter]
  );

  const formatCurrency = useCallback(
    (cnyAmount: number) => {
      const rate = EXCHANGE_RATES[currency] ?? 1;
      const symbol = CURRENCY_SYMBOLS[currency] ?? "¥";
      const converted = cnyAmount * rate;
      return `${symbol}${converted.toFixed(2)}`;
    },
    [currency]
  );

  const formatNumber = (n: number) => n.toLocaleString("zh-CN");

  // Waterfall chart data
  const waterfallData = useMemo(() => {
    const sorted = [...filteredSteps].sort((a, b) => a.costCny - b.costCny);
    const total = sorted.reduce((sum, s) => sum + s.costCny, 0);
    let cumulative = 0;
    return sorted.map((step) => {
      cumulative += step.costCny;
      return {
        ...step,
        percentage: total > 0 ? (step.costCny / total) * 100 : 0,
        cumulativePercentage: total > 0 ? (cumulative / total) * 100 : 0,
      };
    });
  }, [filteredSteps]);

  return (
    <div className="token-analytics-page">
      <header className="token-analytics-header">
        <div>
          <h2>Token 成本追踪</h2>
          <p>统计范围：自项目首个运行起</p>
        </div>
        <div className="token-analytics-controls">
          <label>
            货币
            <select onChange={(e) => setCurrency(e.target.value)} value={currency}>
              {Object.keys(CURRENCY_SYMBOLS).map((code) => (
                <option key={code} value={code}>{code}</option>
              ))}
            </select>
          </label>
          <button className="button button-secondary" type="button">刷新</button>
        </div>
      </header>

      {/* Tabs */}
      <div className="token-analytics-tabs">
        <button
          className={`tab-btn${activeTab === "overview" ? " is-active" : ""}`}
          onClick={() => setActiveTab("overview")}
          type="button"
        >
          总览
        </button>
        <button
          className={`tab-btn${activeTab === "steps" ? " is-active" : ""}`}
          onClick={() => setActiveTab("steps")}
          type="button"
        >
          步骤明细
        </button>
        <button
          className={`tab-btn${activeTab === "models" ? " is-active" : ""}`}
          onClick={() => setActiveTab("models")}
          type="button"
        >
          模型明细
        </button>
        <button
          className={`tab-btn${activeTab === "pricing" ? " is-active" : ""}`}
          onClick={() => setActiveTab("pricing")}
          type="button"
        >
          模型价格设置
        </button>
      </div>

      {/* Overview tab (Segment 49) */}
      {activeTab === "overview" && (
        <div className="token-overview">
          <div className="token-metrics">
            <article className="token-metric-card">
              <h4>累计 Token</h4>
              <strong>{formatNumber(totalTokens)}</strong>
            </article>
            <article className="token-metric-card">
              <h4>输入 Token</h4>
              <strong>{formatNumber(totalPromptTokens)}</strong>
            </article>
            <article className="token-metric-card">
              <h4>输出 Token</h4>
              <strong>{formatNumber(totalCompletionTokens)}</strong>
            </article>
            <article className="token-metric-card">
              <h4>模型调用</h4>
              <strong>{totalCalls}</strong>
            </article>
            <article className="token-metric-card">
              <h4>运行次数</h4>
              <strong>{MOCK_RUNS.length}</strong>
            </article>
            <article className="token-metric-card is-highlight">
              <h4>估算成本</h4>
              <strong>{formatCurrency(totalCostCny)}</strong>
            </article>
          </div>

          {/* Run history */}
          <section className="token-run-history">
            <h3>运行历史</h3>
            <table className="token-run-table">
              <thead>
                <tr>
                  <th>开始时间</th>
                  <th>任务</th>
                  <th>章节</th>
                  <th>Tokens</th>
                  <th>成本</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {MOCK_RUNS.map((run) => (
                  <tr key={run.id}>
                    <td>{run.startedAt}</td>
                    <td>{run.kind}</td>
                    <td>{run.chapter ?? "—"}</td>
                    <td>{formatNumber(run.tokens)}</td>
                    <td>{formatCurrency(run.costCny)}</td>
                    <td>
                      <span className={`run-status is-${run.status}`}>
                        {run.status === "success" ? "成功" : "失败"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}

      {/* Steps tab with waterfall (Segment 50) */}
      {activeTab === "steps" && (
        <div className="token-steps">
          <div className="token-step-filters">
            <span>瀑布筛选：</span>
            {STEP_FILTER_OPTIONS.map((opt) => (
              <button
                className={`filter-chip${stepFilter === opt.id ? " is-active" : ""}`}
                key={opt.id}
                onClick={() => setStepFilter(opt.id)}
                type="button"
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Waterfall chart */}
          <section className="token-waterfall">
            <h3>步骤瀑布图</h3>
            <p>左至右为累计消耗进度，右侧显示累计占比。</p>
            {waterfallData.length === 0 ? (
              <p className="token-waterfall-empty">当前筛选下暂无可展示步骤。</p>
            ) : (
              <div className="token-waterfall-chart">
                {waterfallData.map((item, index) => (
                  <div className="token-waterfall-row" key={index}>
                    <span className="token-waterfall-label">{item.step}</span>
                    <div className="token-waterfall-bar-container">
                      <div
                        className="token-waterfall-bar"
                        style={{ width: `${item.percentage}%` }}
                        title={`${item.step}: ${formatCurrency(item.costCny)} (${item.percentage.toFixed(1)}%)`}
                      />
                    </div>
                    <span className="token-waterfall-value">{formatCurrency(item.costCny)}</span>
                    <span className="token-waterfall-cumulative">{item.cumulativePercentage.toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Step details table */}
          <section className="token-step-details">
            <h3>步骤明细</h3>
            <table className="token-step-table">
              <thead>
                <tr>
                  <th>步骤</th>
                  <th>类型</th>
                  <th>章节</th>
                  <th>Tokens</th>
                  <th>成本</th>
                </tr>
              </thead>
              <tbody>
                {filteredSteps.map((step, index) => (
                  <tr key={index}>
                    <td>{step.step}</td>
                    <td>
                      <span className={`step-kind is-${step.kind}`}>
                        {step.kind === "init" ? "立项" : step.kind === "chapter" ? "正文" : "修复"}
                      </span>
                    </td>
                    <td>{step.chapter ?? "—"}</td>
                    <td>{formatNumber(step.tokens)}</td>
                    <td>{formatCurrency(step.costCny)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}

      {/* Models tab */}
      {activeTab === "models" && (
        <div className="token-models">
          <h3>模型明细</h3>
          <table className="token-model-table">
            <thead>
              <tr>
                <th>模型</th>
                <th>供应商</th>
                <th>输入 Token</th>
                <th>输出 Token</th>
                <th>调用次数</th>
                <th>成本</th>
              </tr>
            </thead>
            <tbody>
              {MOCK_MODELS.map((model) => (
                <tr key={model.model}>
                  <td><strong>{model.model}</strong></td>
                  <td>{model.provider}</td>
                  <td>{formatNumber(model.promptTokens)}</td>
                  <td>{formatNumber(model.completionTokens)}</td>
                  <td>{model.calls}</td>
                  <td>{formatCurrency(model.costCny)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pricing tab */}
      {activeTab === "pricing" && (
        <div className="token-pricing">
          <h3>模型价格设置</h3>
          <p>在此统一管理默认单价与各模型单价；改动会立即影响总览、模型明细与成本环图。</p>
          <div className="token-pricing-form">
            <label className="token-pricing-default">
              默认价格（每百万 Token）
              <input
                min={0}
                onChange={(e) => setDefaultPrice(Number(e.target.value))}
                step={0.1}
                type="number"
                value={defaultPrice}
              />
              <span>CNY</span>
            </label>
            <div className="token-pricing-models">
              <h4>参与模型价格（批量设置）</h4>
              {MOCK_MODELS.map((model) => (
                <label className="token-pricing-model" key={model.model}>
                  <span>{model.model}</span>
                  <input
                    min={0}
                    onChange={(e) =>
                      setModelPrices((prev) => ({ ...prev, [model.model]: Number(e.target.value) }))
                    }
                    step={0.1}
                    type="number"
                    value={modelPrices[model.model] ?? defaultPrice}
                  />
                  <span>CNY / 百万 Token</span>
                </label>
              ))}
            </div>
            <button className="button button-primary" type="button">保存设置</button>
          </div>
        </div>
      )}
    </div>
  );
}

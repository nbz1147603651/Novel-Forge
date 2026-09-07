import type { WorkspaceView } from "@nimo/engine-contracts";

interface MetricsPanelProps {
  readonly workspace: WorkspaceView;
  /** 数据目录标签（settings.storageRootLabel）。 */
  readonly storageRootLabel?: string;
}

/**
 * 工坊脉息指标卡（mirrors dashboard_page.py 工坊脉息 MetricCard grid）。
 * 就绪 provider 名单对齐 overview.providers；无就绪 provider 时显示「当前仅 Mock」。
 */
export function MetricsPanel({ storageRootLabel, workspace }: MetricsPanelProps) {
  const longProjects = workspace.projects.filter((project) => project.mode === "long").length;
  const shortProjects = workspace.projects.length - longProjects;
  const readyProviders =
    workspace.providerGroups?.filter((group) => group.ready).map((group) => group.providerLabel) ?? [];
  const providerDetail =
    readyProviders.length > 0 ? readyProviders.join("、") : "当前仅 Mock";
  return (
    <div className="metrics-block">
      <h3>工坊脉息</h3>
      <p>总揽卷册规模、字数流转与通路起伏。</p>
      <div className="metric-grid">
        <Metric
          detail={`长篇 ${longProjects} · 短篇 ${shortProjects}`}
          label="项目卷轴"
          value={String(workspace.metrics.totalProjects)}
        />
        <Metric
          detail={`默认通路：${workspace.defaultProvider}`}
          label="创作章节"
          value={String(workspace.metrics.totalChapters)}
        />
        <Metric
          detail={`数据目录：${storageRootLabel ?? "未照见"}`}
          label="累计字数"
          value={workspace.metrics.totalWords.toLocaleString("zh-CN")}
        />
        <Metric
          detail={providerDetail}
          label="可用 Provider"
          value={String(workspace.metrics.configuredProviders)}
        />
      </div>
    </div>
  );
}

function Metric({
  detail,
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
  readonly detail: string;
}) {
  return (
    <article className="metric-card">
      <h4>{label}</h4>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

import type { ProjectView } from "@nimo/engine-contracts";

const CLAUSE_BOUNDARIES = new Set(["。", "，", "、", "；", "！", "？", "\n", "\r"]);

/** 在中文标点边界智能截断，避免半句被砍（mirrors workspace.py _truncate_at_clause）。 */
function truncateAtClause(text: string, limit = 80): string {
  const value = (text ?? "").split(/\s+/).join(" ").trim();
  if (value.length <= limit) return value;
  let boundary = -1;
  for (let i = 0; i < limit && i < value.length; i++) {
    if (CLAUSE_BOUNDARIES.has(value.charAt(i))) boundary = i;
  }
  const cut = boundary >= 0 ? boundary + 1 : Math.max(limit - 1, 0);
  return `${value.slice(0, cut).trimEnd()}…`;
}

const QUICK_ACTIONS = [
  { action: "view", label: "阅卷" },
  { action: "compose", label: "续写" },
  { action: "blueprint", label: "蓝图" },
  { action: "graph", label: "图谱" },
  { action: "profile", label: "档案" },
  { action: "book_consistency", label: "一致性" },
] as const;

interface ProjectCardProps {
  readonly project: ProjectView;
  readonly batchMode: boolean;
  readonly isBatchSelected: boolean;
  readonly isSelected: boolean;
  readonly onBatchSelect: (selected: boolean) => void;
  readonly onSelect: () => void;
  readonly onQuickAction?: ((action: string, projectId: string) => void) | undefined;
}

/** 在库卷册网格卡（mirrors dashboard_page.py _ProjectCard）。 */
export function ProjectCard({
  batchMode,
  isBatchSelected,
  isSelected,
  onBatchSelect,
  onQuickAction,
  onSelect,
  project,
}: ProjectCardProps) {
  const modeLabel = project.mode === "long" ? "长篇" : "短篇";
  return (
    <article
      aria-current={isSelected ? "true" : undefined}
      className={`project-card${isSelected ? " is-selected" : ""}${batchMode ? " is-batch-mode" : ""}${isBatchSelected ? " is-batch-selected" : ""}`}
    >
      {batchMode && (
        <button
            aria-checked={isBatchSelected}
            aria-label={`勾选卷册：${project.title}`}
            className="project-card-batch-select"
            onClick={() => onBatchSelect(!isBatchSelected)}
            role="checkbox"
            type="button"
          >
            <span aria-hidden="true" className="project-card-batch-box" />
          <span>选择</span>
        </button>
      )}
      <button
        aria-label={`选择卷册：${project.title}`}
        className="project-card-select"
        onClick={onSelect}
        type="button"
      >
        <div className="project-card-heading">
          <h3 title={project.title}>{project.title}</h3>
          <span
            className={
              project.status === "completed"
                ? "status-badge is-success"
                : "status-badge is-warning"
            }
          >
            {project.statusLabel}
          </span>
        </div>
        <p className="project-card-body" title={project.headline}>
          {truncateAtClause(project.headline)}
        </p>
        <small>
          {modeLabel} · {project.genre} · {project.tone}
        </small>
        <small className="card-next-action">下一步：{project.nextAction}</small>
        <div className="project-progress">
          <span style={{ width: `${project.progressPercent}%` }} />
          <small>{project.progressLabel}</small>
        </div>
      </button>
      {onQuickAction !== undefined && (
        <div aria-label={`${project.title}快捷动作`} className="project-card-actions">
          {QUICK_ACTIONS.map(({ action, label }) => (
            <button
              aria-label={`${label}：${project.title}`}
              className="project-card-action-btn"
              key={action}
              onClick={() => onQuickAction(action, project.id)}
              title={label}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

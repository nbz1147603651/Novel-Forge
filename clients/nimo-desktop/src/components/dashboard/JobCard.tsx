import type { JobView } from "@nimo/engine-contracts";

/** 案头后台任务卡（mirrors dashboard_page.py JobCard）。 */
export function JobCard({ job, onTaskDecision }: { readonly job: JobView; readonly onTaskDecision?: ((jobId: string, decisionId: string) => void) | undefined }) {
  return (
    <article className="job-card">
      <div>
        <span className="section-kicker">
          {job.state === "running" ? "正在执行" : job.state === "failed" ? "执行失败" : job.state === "paused" ? "等待决策" : job.state}
        </span>
        <h3>{job.label}</h3>
        <p>{job.currentStep}</p>
        <small>{job.detail}</small>
      </div>
      {job.state === "paused" && job.decisions !== undefined && job.decisions.length > 0 ? (
        <div className="job-decision-panel">
          <span className="section-kicker">等待裁决</span>
          <div className="job-decision-options">
            {job.decisions.map((decision) => (
              <button
                className="button button-secondary job-decision-btn"
                key={decision.id}
                onClick={() => onTaskDecision?.(job.id, decision.id)}
                type="button"
              >
                {decision.label}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="job-progress">
          <strong>{job.progressPercent}%</strong>
          <div>
            <span style={{ width: `${job.progressPercent}%` }} />
          </div>
        </div>
      )}
    </article>
  );
}

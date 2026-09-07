import type { StepArtifactFile } from "@nimo/engine-contracts";

import { ArtifactViewer } from "./ArtifactViewer";
import { OverlaySurface } from "./OverlaySurface";

export interface WorkflowArtifactDialogProps {
  readonly artifacts: readonly StepArtifactFile[];
  readonly candidatePaths?: readonly { readonly label: string; readonly path: string }[];
  readonly emptyHint?: string;
  /** Keep the feedback surface visible while the Engine resolves artifacts. */
  readonly loading?: boolean;
  readonly onClose: () => void;
  readonly stageLabel: string;
}

/**
 * React/Tauri counterpart to PySide6 `StepArtifactDialog`.
 *
 * The source dialog is a large, read-only document window rather than a
 * confirmation prompt. Keeping it separate from AppDialog preserves its
 * single close action, file context, future multi-artifact tabs and source
 * geometry without making every generic confirmation dialog oversized.
 *
 * Content rendering is delegated to the shared `ArtifactViewer`: recognized
 * long-form artifacts reuse the same reader shelf and rich document view as
 * the PySide6 project page, while unknown files preserve a raw fallback.
 */
export function WorkflowArtifactDialog({
  artifacts,
  candidatePaths,
  emptyHint,
  loading = false,
  onClose,
  stageLabel,
}: WorkflowArtifactDialogProps) {
  const artifactCount = artifacts.length;
  const subtitle = loading
    ? `${stageLabel} · 正在读取产出文件`
    : artifactCount === 0
      ? `${stageLabel} · 暂无可查看文件`
      : `${stageLabel} · 共 ${artifactCount} 个产出文件${
          artifactCount > 1 ? " · 点击标签切换查看" : ""
        }`;

  return (
    <OverlaySurface
      ariaLabel={`${stageLabel} · 产出文件`}
      className="workflow-artifact-surface"
      onClose={onClose}
    >
      <section className="workflow-artifact-dialog">
        <header>
          <h2>产出文件</h2>
          <p>{subtitle}</p>
        </header>

        <div className="workflow-artifact-workspace">
          {loading ? (
            <div aria-live="polite" className="workflow-artifact-loading" role="status">
              正在读取“{stageLabel}”的产物…
            </div>
          ) : (
            <ArtifactViewer
              artifacts={artifacts}
              {...(candidatePaths !== undefined ? { candidatePaths } : {})}
              {...(emptyHint !== undefined ? { emptyHint } : {})}
              stageLabel={stageLabel}
            />
          )}
        </div>

        <footer>
          <button className="button button-secondary" onClick={onClose} type="button">
            关闭
          </button>
        </footer>
      </section>
    </OverlaySurface>
  );
}

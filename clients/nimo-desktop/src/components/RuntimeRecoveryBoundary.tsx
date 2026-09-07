import { Component, Fragment, type ErrorInfo, type ReactNode } from "react";

import { reportFrontendRenderFailure } from "../lib/native-bridge";

export function RuntimeRecoverySurface({
  onRecover,
  onReload,
  title = "界面需要恢复",
  detail = "桌面界面在恢复时没有完成渲染。你的已保存作品不受影响；请恢复界面后继续。",
}: {
  readonly onRecover: () => void;
  readonly onReload?: () => void;
  readonly title?: string;
  readonly detail?: string;
}) {
  return (
    <main className="nimo-runtime-recovery connection-error-surface" role="alert">
      <span className="connection-error-icon" aria-hidden>⚠</span>
      <h2>{title}</h2>
      <p>{detail}</p>
      <button className="button button-primary" onClick={onRecover} type="button">
        恢复界面
      </button>
      {onReload !== undefined && (
        <button className="button button-secondary" onClick={onReload} type="button">
          重新加载界面（可能丢失未保存编辑）
        </button>
      )}
    </main>
  );
}

interface RuntimeRecoveryBoundaryProps {
  readonly children: ReactNode;
}

interface RuntimeRecoveryBoundaryState {
  readonly failed: boolean;
  readonly recoveryRevision: number;
}

export function retryRuntimeRecovery(
  state: RuntimeRecoveryBoundaryState,
): RuntimeRecoveryBoundaryState {
  return { failed: false, recoveryRevision: state.recoveryRevision + 1 };
}

/** Keep a resumed WebView render failure actionable instead of leaving an empty window. */
export class RuntimeRecoveryBoundary extends Component<
  RuntimeRecoveryBoundaryProps,
  RuntimeRecoveryBoundaryState
> {
  state: RuntimeRecoveryBoundaryState = { failed: false, recoveryRevision: 0 };

  static getDerivedStateFromError(): Pick<RuntimeRecoveryBoundaryState, "failed"> {
    return { failed: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error("NIMO render recovery boundary caught an error", error, errorInfo);
    void reportFrontendRenderFailure(error, errorInfo.componentStack ?? "");
  }

  private recover = () => this.setState(retryRuntimeRecovery);

  private reload = () => window.location.reload();

  render(): ReactNode {
    if (this.state.failed) {
      return <RuntimeRecoverySurface onRecover={this.recover} onReload={this.reload} />;
    }
    // The fallback replaces the failed child tree. Bumping this key on retry
    // ensures React does not reuse a poisoned subtree after a WebView resume.
    return <Fragment key={this.state.recoveryRevision}>{this.props.children}</Fragment>;
  }
}

import type { ResolvedPageMeta } from "./types";
import { useT } from "../lib/i18n";

export function LoadingSurface({
  error,
  onRetry,
  title,
}: {
  readonly error?: string;
  readonly onRetry?: () => void;
  /**
   * Error-state heading. Defaults to "章台数据加载失败" to match the
   * contract documented in the bug-fix plan; callers (e.g. workflow /
   * voice-studio shells) may override it.
   */
  readonly title?: string;
} = {}) {
  if (error !== undefined) {
    return (
      <div className="connection-error-surface" role="alert">
        <span className="connection-error-icon" aria-hidden>⚠</span>
        <h2>{title ?? "章台数据加载失败"}</h2>
        <p>{error}</p>
        {onRetry !== undefined ? (
          <button
            className="button button-primary"
            onClick={onRetry}
            type="button"
          >
            重试
          </button>
        ) : null}
      </div>
    );
  }
  return (
    <div className="loading-surface" aria-label="正在装载案头状态">
      <span />
      <span />
      <span />
    </div>
  );
}

/** Full-page error state when engine backend is unreachable. */
export function ConnectionErrorSurface({ onRetry }: { readonly onRetry: () => void }) {
  return (
    <div className="connection-error-surface" role="alert">
      <span className="connection-error-icon" aria-hidden>⚠</span>
      <h2>引擎连接中断</h2>
      <p>无法连接到后端服务。请确认 Python 引擎已启动，然后重试。</p>
      <button className="button button-primary" onClick={onRetry} type="button">
        重新连接
      </button>
    </div>
  );
}

/** Full-page diagnostic when the backend contract version is incompatible. */
export function IncompatibleVersionSurface({
  contractVersion,
  expectedMajor,
  onRetry,
}: {
  readonly contractVersion: string;
  readonly expectedMajor: number;
  readonly onRetry: () => void;
}) {
  return (
    <div className="connection-error-surface" role="alert">
      <span className="connection-error-icon" aria-hidden>⛔</span>
      <h2>契约版本不兼容</h2>
      <p>
        后端返回契约版本 <code>{contractVersion}</code>，客户端期望 {expectedMajor}.x。
        请更新 Python 引擎或前端客户端至同一主版本。
      </p>
      <button className="button button-primary" onClick={onRetry} type="button">
        重新协商
      </button>
    </div>
  );
}

export function SettingsLoadingSurface() {
  return (
    <div
      aria-busy="true"
      aria-label="正在准备火候页"
      className="settings-page-skeleton"
    >
      <section>
        <span />
        <span />
        <span />
      </section>
      <div>
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

export function ParityInProgress({ page }: { readonly page: ResolvedPageMeta }) {
  const t = useT();
  return (
    <div className="parity-in-progress">
      <h2>{t("loading.parity.title", { label: page.label })}</h2>
      <p>{t("loading.parity.body")}</p>
    </div>
  );
}

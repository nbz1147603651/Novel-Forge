/**
 * ErrorSummary — structured error display component.
 *
 * Mirrors the PySide6 DesktopErrorSummary pattern with summary/detail fields.
 * Provides a consistent error display across all pages with retry capability.
 */

import { useState } from "react";

export interface ErrorSummaryData {
  /** One-line human-readable summary (e.g. "模型调用超时"). */
  readonly summary: string;
  /** Detailed diagnostic info (stack trace, API response, etc.). */
  readonly detail?: string;
  /** Machine-readable error code for programmatic handling. */
  readonly code?: string;
  /** Whether the operation can be retried. */
  readonly retryable?: boolean;
  /** Timestamp when the error occurred. */
  readonly timestamp?: string;
}

export interface ErrorSummaryProps {
  readonly error: ErrorSummaryData;
  readonly onRetry?: (() => void) | undefined;
  readonly onDismiss?: (() => void) | undefined;
  readonly variant?: "inline" | "card" | "banner" | undefined;
}

/**
 * Structured error display with expandable details and optional retry.
 *
 * Usage:
 * ```tsx
 * <ErrorSummary
 *   error={{ summary: "模型调用失败", detail: "Rate limit exceeded", retryable: true }}
 *   onRetry={() => retryOperation()}
 * />
 * ```
 */
export function ErrorSummary({ error, onDismiss, onRetry, variant = "inline" }: ErrorSummaryProps) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = Boolean(error.detail && error.detail.length > 0);

  const containerClass =
    variant === "banner"
      ? "error-summary error-summary-banner"
      : variant === "card"
        ? "error-summary error-summary-card"
        : "error-summary";

  return (
    <div className={containerClass} role="alert">
      <div className="error-summary-header">
        <span className="error-summary-icon" aria-hidden>⚠</span>
        <span className="error-summary-message">{error.summary}</span>
        {error.code && <code className="error-summary-code">{error.code}</code>}
        <span className="error-summary-actions">
          {error.retryable && onRetry && (
            <button className="button button-secondary button-sm" onClick={onRetry} type="button">
              重试
            </button>
          )}
          {onDismiss && (
            <button aria-label="关闭错误提示" className="error-summary-dismiss" onClick={onDismiss} type="button">
              ✕
            </button>
          )}
        </span>
      </div>
      {hasDetail && (
        <div className="error-summary-detail-section">
          <button
            aria-expanded={expanded}
            className="error-summary-detail-toggle"
            onClick={() => setExpanded((prev) => !prev)}
            type="button"
          >
            {expanded ? "收起详情" : "查看详情"}
          </button>
          {expanded && (
            <pre className="error-summary-detail">{error.detail}</pre>
          )}
        </div>
      )}
      {error.timestamp && (
        <time className="error-summary-time">{error.timestamp}</time>
      )}
    </div>
  );
}

/**
 * Four-state wrapper for async data loading.
 * Renders the appropriate UI for loading, empty, error, and success states.
 */
export interface AsyncStateProps<T> {
  readonly data: T | null;
  readonly error: ErrorSummaryData | null;
  readonly loading: boolean;
  readonly isEmpty?: (data: T) => boolean;
  readonly renderLoading?: () => React.ReactNode;
  readonly renderEmpty?: () => React.ReactNode;
  readonly renderError?: (error: ErrorSummaryData) => React.ReactNode;
  readonly renderSuccess: (data: T) => React.ReactNode;
  readonly onRetry?: () => void;
}

/**
 * Generic four-state renderer for async data.
 *
 * States:
 * 1. Loading → skeleton/spinner
 * 2. Error → ErrorSummary with retry
 * 3. Empty → guidance prompt
 * 4. Success → actual content
 */
export function AsyncState<T>({
  data,
  error,
  loading,
  isEmpty,
  renderLoading,
  renderEmpty,
  renderError,
  renderSuccess,
  onRetry,
}: AsyncStateProps<T>) {
  // 1. Loading state
  if (loading) {
    return renderLoading ? <>{renderLoading()}</> : <LoadingSkeleton />;
  }

  // 2. Error state
  if (error) {
    return renderError ? <>{renderError(error)}</> : <ErrorSummary error={error} onRetry={onRetry} variant="card" />;
  }

  // 3. Empty state
  if (data === null || (isEmpty && isEmpty(data))) {
    return renderEmpty ? <>{renderEmpty()}</> : <EmptyState />;
  }

  // 4. Success state
  return <>{renderSuccess(data)}</>;
}

/** Default loading skeleton. */
export function LoadingSkeleton({ lines = 3 }: { readonly lines?: number }) {
  return (
    <div className="loading-skeleton" aria-busy="true" aria-label="正在加载">
      {Array.from({ length: lines }, (_, i) => (
        <div className="skeleton-line" key={i} style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

/** Default empty state with guidance. */
export function EmptyState({ message = "暂无数据", hint }: { readonly message?: string; readonly hint?: string }) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon" aria-hidden>📭</span>
      <p className="empty-state-message">{message}</p>
      {hint && <p className="empty-state-hint">{hint}</p>}
    </div>
  );
}

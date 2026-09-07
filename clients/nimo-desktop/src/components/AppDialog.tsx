import { OverlaySurface } from "./OverlaySurface";

export interface AppDialogProps {
  readonly title: string;
  readonly description: string;
  readonly children?: React.ReactNode;
  readonly confirmLabel?: string;
  readonly onClose: () => void;
  readonly onConfirm?: () => void;
  readonly closeOnConfirm?: boolean;
  readonly confirmDisabled?: boolean;
  readonly tone?: "default" | "danger";
  readonly size?: "standard" | "wide";
  readonly className?: string;
}

export function AppDialog({
  children,
  className = "",
  closeOnConfirm = true,
  confirmDisabled = false,
  confirmLabel = "知道了",
  description,
  onClose,
  onConfirm,
  size = "standard",
  title,
  tone = "default",
}: AppDialogProps) {
  return (
    <OverlaySurface ariaLabel={title} onClose={onClose}>
      <section className={`app-dialog is-${tone} is-${size}${className.length ? ` ${className}` : ""}`}>
        <header><div><span className="section-kicker">NIMO 工作台</span><h2>{title}</h2><p>{description}</p></div><button aria-label="关闭对话框" className="app-dialog-close" onClick={onClose} type="button">×</button></header>
        {children !== undefined && <div className="app-dialog-content">{children}</div>}
        <footer><button className="button button-secondary" onClick={onClose} type="button">取消</button><button className={tone === "danger" ? "button button-danger" : "button button-primary"} disabled={confirmDisabled} onClick={() => { onConfirm?.(); if (closeOnConfirm) onClose(); }} type="button">{confirmLabel}</button></footer>
      </section>
    </OverlaySurface>
  );
}

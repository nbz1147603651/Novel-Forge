import { useEffect, useId, useRef, type CSSProperties } from "react";
import { createPortal } from "react-dom";

export interface OverlaySurfaceProps {
  readonly ariaLabel: string;
  readonly children: React.ReactNode;
  /** Visual presentation can remain non-modal while focus stays contained. */
  readonly backdropClassName?: string;
  readonly className?: string;
  /**
   * Source dialogs with a text-entry affordance can name their measured
   * initial focus target. All other overlays keep the neutral surface focus
   * used by the native window shell.
   */
  readonly initialFocusSelector?: string;
  /** Floating windows keep Escape/focus restoration without blocking the workspace. */
  readonly modal?: boolean;
  readonly onClose: () => void;
  readonly style?: CSSProperties;
}

const focusableSelector = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * Shared modal shell for every workbench dialog. It owns Escape, focus
 * restoration, and a compact focus trap so individual pages only own their
 * intent/state. Nested business dialogs should be avoided; replace content
 * within the same overlay instead.
 */
export function OverlaySurface({ ariaLabel, backdropClassName = "", children, className = "", initialFocusSelector, modal = true, onClose, style }: OverlaySurfaceProps) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // A source-shaped Qt.Tool window is non-blocking: it restores focus when
    // closed but must not steal focus (or paint a browser-only focus ring)
    // when it appears above an active workspace.
    if (modal) {
      // Native Qt dialogs receive focus as a window, not as a highlighted
      // default action. Keep the browser's initial focus equally quiet unless
      // the corresponding source dialog explicitly autofocuses a field.
      const focusTarget = initialFocusSelector === undefined
        ? null
        : dialogRef.current?.querySelector<HTMLElement>(initialFocusSelector);
      (focusTarget ?? dialogRef.current)?.focus({ preventScroll: true });
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (!modal || event.key !== "Tab" || dialogRef.current === null) {
        return;
      }
      const targets = [...dialogRef.current.querySelectorAll<HTMLElement>(focusableSelector)];
      if (targets.length === 0) {
        event.preventDefault();
        return;
      }
      const first = targets[0]!;
      const last = targets.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    // A previous dialog can restore focus after its successor has opened.
    // Only the top modal may reclaim it; an underlying drawer must not steal
    // focus from the explicit approval dialog layered above it.
    const handleFocusIn = (event: FocusEvent) => {
      const surface = dialogRef.current;
      if (!modal || surface === null || surface.contains(event.target as Node)) return;
      const top = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')].at(-1);
      if (top === surface.parentElement) surface.focus({ preventScroll: true });
    };
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("focusin", handleFocusIn);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("focusin", handleFocusIn);
      openerRef.current?.focus();
    };
  }, [initialFocusSelector, modal]);

  return createPortal(
    <div aria-labelledby={titleId} aria-modal={modal || undefined} className={`overlay-backdrop ${backdropClassName}`.trim()} role="dialog">
      <section className={`overlay-surface ${className}`.trim()} ref={dialogRef} style={style} tabIndex={-1}>
        <span className="sr-only" id={titleId}>{ariaLabel}</span>
        {children}
      </section>
    </div>,
    document.body,
  );
}

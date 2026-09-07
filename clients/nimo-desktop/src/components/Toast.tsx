import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * Global toast notification system — 1:1 projection of the PySide6
 * ``ToastManager`` (novel_forge/desktop/components/toast.py).
 *
 * Visual contract:
 * - Slide in from right (300ms, cubic-bezier(.22,.61,.36,1))
 * - Auto-dismiss after 4000ms with 200ms fade-out
 * - Stack vertically with 8px spacing, anchored top-right
 * - Variant accent border: success | error | warning | info
 */

export type ToastVariant = "success" | "error" | "warning" | "info";

interface ToastEntry {
  readonly id: number;
  readonly message: string;
  readonly variant: ToastVariant;
  readonly dismissing: boolean;
}

interface ToastContextValue {
  showToast: (message: string, variant?: ToastVariant) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_DISMISS_MS = 4000;
const FADE_OUT_MS = 200;

const variantIcons: Record<ToastVariant, string> = {
  success: "✓",
  error: "✕",
  warning: "!",
  info: "i",
};

export function ToastProvider({ children }: { readonly children: ReactNode }) {
  const [toasts, setToasts] = useState<readonly ToastEntry[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) =>
      current.map((toast) =>
        toast.id === id ? { ...toast, dismissing: true } : toast,
      ),
    );
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, FADE_OUT_MS);
  }, []);

  const showToast = useCallback(
    (message: string, variant: ToastVariant = "info") => {
      const id = ++nextId.current;
      setToasts((current) => [
        ...current.slice(-4),
        { id, message, variant, dismissing: false },
      ]);
      window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div aria-live="polite" className="toast-stack" role="status">
        {toasts.map((toast) => (
          <div
            className={`toast is-${toast.variant}${toast.dismissing ? " is-dismissing" : ""}`}
            key={toast.id}
          >
            <span className="toast-icon">{variantIcons[toast.variant]}</span>
            <p>{toast.message}</p>
            <button
              aria-label="关闭通知"
              className="toast-close"
              onClick={() => dismiss(toast.id)}
              type="button"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (context === null) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
}

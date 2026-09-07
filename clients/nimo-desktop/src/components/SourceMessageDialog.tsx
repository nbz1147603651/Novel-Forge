import { OverlaySurface } from "./OverlaySurface";

export type SourceMessageDialogActionTone = "danger" | "primary" | "secondary";

export interface SourceMessageDialogAction {
  readonly id: string;
  readonly label: string;
  readonly tone: SourceMessageDialogActionTone;
}

export interface SourceMessageDialogProps {
  readonly actions: readonly SourceMessageDialogAction[];
  readonly informativeText?: string;
  readonly message: string;
  readonly onAction: (actionId: string) => void;
  readonly onClose: () => void;
  readonly title: string;
}

/**
 * Counterpart to desktop/components/dialogs.py::show_message_box.
 *
 * The source has a native window title and a compact content body; the shared
 * OverlaySurface supplies the same modal, Escape, and focus-restoration
 * semantics to web and Tauri builds.  It deliberately accepts arbitrary
 * actions because source confirmations commonly have save/discard/cancel,
 * which should not be squeezed into AppDialog's two-action form.
 */
export function SourceMessageDialog({ actions, informativeText, message, onAction, onClose, title }: SourceMessageDialogProps) {
  return (
    <OverlaySurface ariaLabel={title} onClose={onClose}>
      <section className="source-message-dialog">
        <div className="source-message-dialog-copy">
          <p className="source-message-dialog-text">{message}</p>
          {informativeText !== undefined && <p className="source-message-dialog-info">{informativeText}</p>}
        </div>
        <footer className="source-message-dialog-actions">
          {actions.map((action) => <button className={`button button-${action.tone}`} key={action.id} onClick={() => onAction(action.id)} type="button">{action.label}</button>)}
        </footer>
      </section>
    </OverlaySurface>
  );
}

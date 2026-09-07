/**
 * Transport-neutral navigation guard for transient reader edits.
 *
 * The shape separates the source-shaped unsaved-change decision from the
 * widget that owns its draft and its Engine-backed persistence command.
 */
export type ReaderUnsavedSessionId = "final-revision" | "outline" | "characters";

export interface ReaderUnsavedSession {
  readonly cancelLabel: string;
  readonly discardLabel: string;
  readonly id: ReaderUnsavedSessionId;
  readonly informativeText: string;
  readonly message: string;
  readonly onDiscard: () => void;
  readonly onSave: () => void | Promise<boolean>;
  readonly saveLabel: string;
  readonly title: string;
}

export type ReaderUnsavedSessionChange = (
  id: ReaderUnsavedSessionId,
  session: ReaderUnsavedSession | null,
) => void;

export const readerUnsavedSessionOrder: readonly ReaderUnsavedSessionId[] = [
  "final-revision",
  "outline",
  "characters",
];

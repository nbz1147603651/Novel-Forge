import type { WorkflowPresetDiffView } from "@nimo/engine-contracts";

/** Immutable selection helpers shared by Engine-backed review dialogs. */
export function nextSelectedIds(
  selectedIds: ReadonlySet<string>,
  id: string,
): ReadonlySet<string> {
  const next = new Set(selectedIds);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return next;
}

export function allDiffIds(diffs: readonly WorkflowPresetDiffView[]): ReadonlySet<string> {
  return new Set(diffs.map((diff) => diff.id));
}

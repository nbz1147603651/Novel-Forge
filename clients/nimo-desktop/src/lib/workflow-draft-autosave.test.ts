import { describe, expect, it, vi } from "vitest";

import type { WorkflowDraftView, WorkflowPersistenceResult } from "@nimo/engine-contracts";

import { RevisionedDraftSaveQueue } from "./workflow-draft-autosave";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

const remoteDraft: WorkflowDraftView = {
  mode: "short",
  payload: { title: "远端版本" },
  revision: "remote-r2",
  savedAtLabel: "10:00:00",
};

describe("RevisionedDraftSaveQueue", () => {
  it("serializes overlapping saves and writes only the newest queued payload", async () => {
    const firstSave = deferred<WorkflowPersistenceResult>();
    const secondSave = deferred<WorkflowPersistenceResult>();
    const save = vi.fn()
      .mockReturnValueOnce(firstSave.promise)
      .mockReturnValueOnce(secondSave.promise);
    const onSaved = vi.fn();
    const queue = new RevisionedDraftSaveQueue({
      load: vi.fn(),
      save,
    }, { onFailure: vi.fn(), onSaved });

    const finished = queue.enqueue({ title: "第一版" });
    queue.enqueue({ title: "最终版" });
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenLastCalledWith({ title: "第一版" }, undefined);

    firstSave.resolve({ status: "saved", message: "已保存", revision: "r1" });
    await flush();
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith({ title: "最终版" }, "r1");

    secondSave.resolve({ status: "saved", message: "已保存", revision: "r2" });
    await finished;
    expect(onSaved).toHaveBeenCalledTimes(2);
  });

  it("reloads the revision after a conflict and retries the newest local edit", async () => {
    const conflict = deferred<WorkflowPersistenceResult>();
    const reloaded = deferred<WorkflowDraftView>();
    const retry = deferred<WorkflowPersistenceResult>();
    const save = vi.fn()
      .mockReturnValueOnce(conflict.promise)
      .mockReturnValueOnce(retry.promise);
    const load = vi.fn(() => reloaded.promise);
    const onFailure = vi.fn();
    const queue = new RevisionedDraftSaveQueue({ load, save }, { onFailure, onSaved: vi.fn() });

    const finished = queue.enqueue({ title: "冲突前" });
    queue.enqueue({ title: "本地最新" });
    conflict.resolve({ status: "conflict", message: "远端草稿已更新", revision: "remote-r2" });
    await flush();
    expect(load).toHaveBeenCalledTimes(1);

    reloaded.resolve(remoteDraft);
    await flush();
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith({ title: "本地最新" }, "remote-r2");

    retry.resolve({ status: "saved", message: "已保存", revision: "r3" });
    await finished;
    expect(onFailure).not.toHaveBeenCalled();
  });
});

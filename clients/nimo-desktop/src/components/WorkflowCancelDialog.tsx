import { OverlaySurface } from "./OverlaySurface";

/** Mirrors the source-sized confirmation before a workflow job is stopped. */
export function WorkflowCancelDialog({ currentStep, jobLabel, onClose, onConfirm }: { readonly currentStep: string; readonly jobLabel: string; readonly onClose: () => void; readonly onConfirm: () => void }) {
  return <OverlaySurface ariaLabel="确认停止任务" onClose={onClose}>
    <section className="workflow-cancel-dialog">
      <h2>确认停止任务</h2>
      <p>任务：{jobLabel}</p>
      <p>当前步骤：{currentStep}</p>
      <small>停止后可以从断点恢复</small>
      <footer><button className="button button-secondary" onClick={onClose} type="button">取消</button><button className="button button-danger" onClick={onConfirm} type="button">确认停止</button></footer>
    </section>
  </OverlaySurface>;
}

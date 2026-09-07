import { useState } from "react";

import {
  isNimoManagedEngine,
  type EngineRuntimeDiagnostic,
} from "../lib/engine-runtime-status";

import { AppDialog } from "./AppDialog";

const LABELS: Readonly<Record<EngineRuntimeDiagnostic["connection"], string>> = {
  checking: "正在检查后端",
  ready: "后端已连接",
  restartRequired: "后端需安全重启",
  unsupported: "检测到旧后端",
  unavailable: "后端暂不可达",
} as const;

function supportingLabel(diagnostic: EngineRuntimeDiagnostic): string | null {
  if (diagnostic.connection === "restartRequired" || diagnostic.connection === "unsupported") {
    return "新任务已保护";
  }
  if (diagnostic.connection === "unavailable") return "点击查看诊断";
  if (diagnostic.connection === "checking") return "暂不提交新任务";
  return null;
}

export interface EngineRuntimeStatusProps {
  readonly diagnostic: EngineRuntimeDiagnostic;
  readonly onRefresh: () => void;
}

export function EngineRuntimeStatus({ diagnostic, onRefresh }: EngineRuntimeStatusProps) {
  const [open, setOpen] = useState(false);
  const runtime = diagnostic.runtime;
  const hasProtectedTasks = (runtime?.activeJobCount ?? 0) + (runtime?.queuedJobCount ?? 0) > 0;
  const stateClass = diagnostic.connection.toLowerCase();
  const supporting = supportingLabel(diagnostic);

  return (
    <>
      <button
        aria-label="查看 Engine 运行诊断"
        className={`engine-runtime-status is-${stateClass}`}
        onClick={() => setOpen(true)}
        type="button"
      >
        <span aria-hidden="true" className="engine-runtime-status-dot" />
        <span>{LABELS[diagnostic.connection]}</span>
        {supporting ? <small>{supporting}</small> : null}
      </button>
      {open ? (
        <AppDialog
          confirmLabel="重新检测"
          description={diagnostic.message}
          onClose={() => setOpen(false)}
          onConfirm={onRefresh}
          title="Engine 运行诊断"
          tone={diagnostic.canSubmitTasks ? "default" : "danger"}
        >
          <dl className="engine-runtime-details">
            <div><dt>提交新任务</dt><dd>{diagnostic.canSubmitTasks ? "允许" : "已保护性禁用"}</dd></div>
            <div><dt>运行模式</dt><dd>{isNimoManagedEngine(runtime) ? "NIMO 受管本地后端" : runtime ? "外部后端" : "未知"}</dd></div>
            <div><dt>活动任务</dt><dd>{runtime ? `${runtime.activeJobCount} 运行 / ${runtime.queuedJobCount} 排队` : "无法读取"}</dd></div>
            {runtime ? <div><dt>启动时间</dt><dd>{new Date(runtime.startedAt).toLocaleString("zh-CN")}</dd></div> : null}
            {runtime ? <div><dt>后端版本</dt><dd>{runtime.currentRevision}</dd></div> : null}
          </dl>
          {!diagnostic.canSubmitTasks ? (
            <p className="engine-runtime-next-action">
              {hasProtectedTasks
                ? "检测到活动任务：请等待完成或在任务面板取消后，再运行 nimo --restart-owned-backend。"
                : "请关闭外部旧后端，或运行 nimo --restart-owned-backend 后重新打开客户端。"}
            </p>
          ) : null}
        </AppDialog>
      ) : null}
    </>
  );
}

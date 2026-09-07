import { useCallback, useEffect, useState } from "react";

import type { EngineRuntimeView } from "@nimo/engine-contracts";

import type { EngineRuntimeConfig } from "./engine-client-factory";

export type EngineRuntimeConnection = "checking" | "ready" | "restartRequired" | "unsupported" | "unavailable";

export interface EngineRuntimeDiagnostic {
  readonly connection: EngineRuntimeConnection;
  readonly runtime?: EngineRuntimeView;
  readonly message: string;
  readonly canSubmitTasks: boolean;
}

const POLL_INTERVAL_MS = 15_000;

export function isNimoManagedEngine(runtime: EngineRuntimeView | undefined): boolean {
  return runtime?.managedBy === "nimo" || runtime?.managedBy === "nimo-t";
}

function initialDiagnostic(config: EngineRuntimeConfig): EngineRuntimeDiagnostic {
  if (config.mode === "mock") {
    return {
      connection: "ready",
      message: "Mock Engine 已就绪。",
      canSubmitTasks: !config.readOnly,
    };
  }
  return {
    connection: "checking",
    message: config.startupDiagnostic ?? "正在检查本地 Engine 运行状态。",
    canSubmitTasks: false,
  };
}

function runtimeMessage(
  runtime: EngineRuntimeView,
  config: EngineRuntimeConfig,
): string {
  if (config.startupDiagnostic) return config.startupDiagnostic;
  if (runtime.status === "restartRequired") {
    return isNimoManagedEngine(runtime)
      ? "后端源码已更新；受管启动器会等待活动任务结束后安全重启，已保存的连跑将自动恢复。"
      : "本地 Engine 的源码已更新；请在没有活动任务时安全重启后端，已保存的连跑将在重启后恢复。";
  }
  if (config.backendReload) {
    return "开发期 reload 已开启；保存后端代码会重载服务并可能中断任务。";
  }
  if (isNimoManagedEngine(runtime)) return "本地受管 Engine 已连接。";
  return "已连接外部 Engine；NIMO 不会停止或重启该服务。";
}

export function projectEngineRuntimeDiagnostic(
  runtime: EngineRuntimeView,
  config: EngineRuntimeConfig,
): EngineRuntimeDiagnostic {
  // The launcher's expectedRevision is a startup snapshot. A freshly restarted
  // Engine owns its current revision; comparing it forever to that snapshot
  // would keep a healthy replacement backend permanently disabled.
  const restartRequired = runtime.status !== "ready"
    || runtime.bootRevision !== runtime.currentRevision;
  return {
    connection: restartRequired ? "restartRequired" : "ready",
    runtime,
    message: runtimeMessage(runtime, config),
    canSubmitTasks: !config.readOnly && !restartRequired && runtime.canSubmitTasks,
  };
}

export function useEngineRuntimeStatus(config: EngineRuntimeConfig) {
  const [diagnostic, setDiagnostic] = useState<EngineRuntimeDiagnostic>(() => initialDiagnostic(config));

  const refresh = useCallback(async (): Promise<void> => {
    if (config.mode === "mock") {
      setDiagnostic(initialDiagnostic(config));
      return;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5_000);
    try {
      const headers: Record<string, string> = {};
      if (config.accessToken) headers.Authorization = `Bearer ${config.accessToken}`;
      const response = await fetch(`${config.baseUrl}/api/v1/engine/runtime`, {
        headers,
        signal: controller.signal,
      });
      if (!response.ok) {
        setDiagnostic({
          connection: "unsupported",
          message: config.startupDiagnostic ?? "当前 Engine 缺少运行时诊断接口，已禁止提交新任务。",
          canSubmitTasks: false,
        });
        return;
      }
      const runtime = (await response.json()) as EngineRuntimeView;
      setDiagnostic(projectEngineRuntimeDiagnostic(runtime, config));
    } catch {
      setDiagnostic({
        connection: "unavailable",
        message: config.startupDiagnostic ?? "无法连接 Engine 运行时诊断接口。",
        canSubmitTasks: false,
      });
    } finally {
      clearTimeout(timeout);
    }
  }, [config.accessToken, config.backendReload, config.baseUrl, config.expectedRevision, config.mode, config.readOnly, config.startupDiagnostic]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  return { diagnostic, refresh };
}

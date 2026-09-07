import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  EngineClient,
  VoiceAudioModelCenterView,
  VoiceAudioModelOperationRequest,
  VoiceAudioModelOperationView,
  VoiceAudioModelView,
  VoiceAudioRuntimeView,
} from "@nimo/engine-contracts";

export type VoiceAudioModelCenterClient = Pick<
  EngineClient,
  | "cancelVoiceAudioModelOperation"
  | "getVoiceAudioModelCenter"
  | "getVoiceAudioModelOperation"
  | "startVoiceAudioModelOperation"
>;

interface VoiceLocalModelCenterProps {
  readonly client: VoiceAudioModelCenterClient;
  /**
   * Bumped by the host (e.g. after a provider switch in the top-bar) to
   * force an out-of-band refresh. The component otherwise only refetches
   * when its `client` reference changes.
   */
  readonly refreshToken?: number;
}

const activeOperationStates = new Set<VoiceAudioModelOperationView["status"]>([
  "queued",
  "running",
]);

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return unit === 3 ? `${value.toFixed(2)} ${units[unit]}` : `${value.toFixed(1)} ${units[unit]}`;
}

function modelStateTone(model: VoiceAudioModelView): "danger" | "success" | "warning" {
  if (!model.compatible) return "danger";
  return model.state === "installed" ? "success" : "warning";
}

function runtimeStateTone(runtime: VoiceAudioRuntimeView): "danger" | "success" | "warning" | "muted" {
  if (runtime.state === "running") return "success";
  if (runtime.state === "failed" || runtime.state === "incompatible") return "danger";
  if (runtime.state === "not_installed") return "warning";
  return "muted";
}

/**
 * Application-scoped model and sidecar lifecycle UI.
 *
 * It deliberately accepts only the Engine's catalog IDs. The Python service
 * owns download sources, cache locations, checksums and license requirements;
 * the React layer only presents those records and drives their lifecycle.
 */
export function VoiceLocalModelCenter({ client, refreshToken }: VoiceLocalModelCenterProps) {
  const [center, setCenter] = useState<VoiceAudioModelCenterView | null>(null);
  const [error, setError] = useState("");
  const [hfToken, setHfToken] = useState("");
  const [operations, setOperations] = useState<Readonly<Record<string, VoiceAudioModelOperationView>>>({});

  const refresh = useCallback(async () => {
    try {
      const next = await client.getVoiceAudioModelCenter();
      setCenter(next);
      setError("");
    } catch {
      setError("无法读取本机模型中心；请确认本地 Engine 正在运行。");
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Out-of-band refresh trigger: when the host bumps refreshToken (e.g.
  // after a top-bar provider change), re-pull the model center without
  // requiring a new `client` reference.
  useEffect(() => {
    if (refreshToken === undefined || refreshToken === 0) return;
    void refresh();
  }, [refreshToken, refresh]);

  const activeOperations = useMemo(
    () => Object.values(operations).filter((operation) => activeOperationStates.has(operation.status)),
    [operations],
  );
  const activeOperationIds = activeOperations.map((operation) => operation.id).join("|");

  useEffect(() => {
    if (activeOperationIds.length === 0) return undefined;
    const operationIds = activeOperationIds.split("|");
    let disposed = false;
    const poll = async () => {
      try {
        const next = await Promise.all(
          operationIds.map((operationId) => client.getVoiceAudioModelOperation(operationId)),
        );
        if (disposed) return;
        setOperations((current) => ({
          ...current,
          ...Object.fromEntries(next.map((operation) => [operation.id, operation])),
        }));
        if (next.some((operation) => !activeOperationStates.has(operation.status))) {
          void refresh();
        }
      } catch {
        if (!disposed) setError("模型中心任务状态读取失败；任务可能仍在后台继续。");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 700);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [activeOperationIds, client, refresh]);

  const start = useCallback(async (request: VoiceAudioModelOperationRequest) => {
    try {
      const operation = await client.startVoiceAudioModelOperation(request);
      setOperations((current) => ({ ...current, [operation.id]: operation }));
      setError("");
    } catch {
      setError("无法启动模型中心任务；本地模型与运行时均未改变。");
    }
  }, [client]);

  const cancel = useCallback(async (operationId: string) => {
    try {
      const operation = await client.cancelVoiceAudioModelOperation(operationId);
      setOperations((current) => ({ ...current, [operation.id]: operation }));
    } catch {
      setError("取消下载失败；任务可能仍在后台继续。");
    }
  }, [client]);

  const currentOperation = (targetKind: "model" | "runtime", targetId: string) => (
    activeOperations.find(
      (operation) => operation.targetKind === targetKind && operation.targetId === targetId,
    )
  );

  const startModelOperation = (model: VoiceAudioModelView, operation: "accept_license" | "delete" | "install" | "self_test") => {
    if (operation === "delete" && !window.confirm(`删除本机模型“${model.name}”？项目引用不会被删除。`)) {
      return;
    }
    void start({
      targetKind: "model",
      targetId: model.id,
      operation,
      acceptLicense: model.licenseAccepted,
      ...(operation === "install" && hfToken.trim() ? { token: hfToken.trim() } : {}),
    });
  };

  const startRuntimeOperation = (
    runtime: VoiceAudioRuntimeView,
    operation: "install_runtime" | "reconcile_runtime" | "rollback_runtime" | "start_runtime" | "stop_runtime" | "upgrade_runtime",
  ) => {
    void start({ targetKind: "runtime", targetId: runtime.id, operation });
  };

  const installMissingRuntimes = () => {
    for (const runtime of center?.runtimes ?? []) {
      if (runtime.state === "not_installed" || runtime.state === "failed") {
        startRuntimeOperation(runtime, "install_runtime");
      }
    }
  };

  if (center === null) {
    return (
      <div className="voice-model-center-loading" role="status">
        {error || "正在读取应用共享的模型库与独立运行时…"}
        {error && <button className="button button-secondary" onClick={() => void refresh()} type="button">重试</button>}
      </div>
    );
  }

  const missingRuntime = center.runtimes.some(
    (runtime) => runtime.state === "failed" || runtime.state === "not_installed",
  );

  return (
    <div className="voice-model-center">
      <p className="voice-settings-intro">
        模型权重与运行时属于应用，所有项目共享；项目只保存模型 ID、版本与引用。
      </p>
      <p className="voice-model-repository">
        已识别模型约 {formatBytes(center.repository.sizeBytes)} · 所在磁盘可用 {formatBytes(center.repository.freeBytes)}
        {center.repository.rollbackAvailable ? " · 存在可回滚迁移" : " · 无回滚锚点"}
        {center.repository.root && <small title={center.repository.root}>{center.repository.root}</small>}
      </p>
      <div className="voice-runtime-summary">
        <span>{center.installedModelCount}/{center.modelCount} 已安装</span>
        <p>已连接的 sidecar 会同步报告运行时和模型状态；未连接时仍可管理应用模型文件。</p>
      </div>
      <div className="voice-settings-action-row">
        <button className="button button-secondary" onClick={() => void refresh()} type="button">
          检查本机运行时
        </button>
        <button
          className="button button-secondary"
          disabled={!missingRuntime || activeOperations.length > 0}
          onClick={installMissingRuntimes}
          type="button"
        >
          安装缺失运行时
        </button>
        <span aria-live="polite" role="status">
          {error || (activeOperations[0]?.message ?? "就绪")}
        </span>
      </div>

      <h3 className="voice-runtime-heading">独立运行时</h3>
      <ul className="voice-runtime-list voice-runtime-cards" aria-label="本机独立运行时">
        {center.runtimes.map((runtime) => {
          const operation = currentOperation("runtime", runtime.id);
          const canInstall = runtime.state === "not_installed" || runtime.state === "failed";
          const canUpdate = runtime.managedProcess && runtime.version !== runtime.targetVersion;
          return (
            <li key={runtime.id}>
              <span>
                <strong>{runtime.name}</strong>
                <small>{runtime.detail}</small>
                {runtime.lastError && <small className="voice-model-error">原因：{runtime.lastError}</small>}
                {operation && <OperationProgress operation={operation} onCancel={cancel} />}
              </span>
              <div className="voice-model-card-actions">
                <em data-tone={runtimeStateTone(runtime)}>{runtime.status}</em>
                {!operation && canInstall && (
                  <button className="button button-primary" onClick={() => startRuntimeOperation(runtime, "install_runtime")} type="button">
                    {runtime.state === "failed" ? "重试安装" : "安装独立环境"}
                  </button>
                )}
                {!operation && !canInstall && canUpdate && (
                  <button className="button button-primary" onClick={() => startRuntimeOperation(runtime, "upgrade_runtime")} type="button">升级运行时</button>
                )}
                {!operation && !canInstall && !canUpdate && runtime.managedProcess && (
                  <>
                    <button className="button button-secondary" onClick={() => startRuntimeOperation(runtime, runtime.state === "running" ? "stop_runtime" : "start_runtime")} type="button">
                      {runtime.state === "running" ? "停止" : "启动"}
                    </button>
                    <button className="button button-secondary" onClick={() => startRuntimeOperation(runtime, "upgrade_runtime")} type="button">检查/更新</button>
                    <button className="button button-secondary" disabled={!runtime.rollbackAvailable} onClick={() => startRuntimeOperation(runtime, "rollback_runtime")} title={runtime.rollbackAvailable ? "切换到上一个已验证运行时版本。" : "尚无可回滚的运行时版本。"} type="button">回滚</button>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <h3 className="voice-runtime-heading">模型权重</h3>
      <label className="voice-model-token">
        <span><strong>Hugging Face Token（仅本次下载）</strong><small>受限仓库下载时使用；不会保存或回显。</small></span>
        <input aria-label="Hugging Face Token（仅本次下载）" autoComplete="off" onChange={(event) => setHfToken(event.target.value)} placeholder="可选" type="password" value={hfToken} />
      </label>
      <ul className="voice-model-list" aria-label="可添加的本机音频模型">
        {center.models.map((model) => {
          const operation = currentOperation("model", model.id);
          const isInstalled = model.state === "installed";
          const downloadLabel = model.state === "external" ? "通过 Sidecar 安装" : "下载";
          return (
            <li key={model.id}>
              <div className="voice-model-card-heading">
                <strong>{model.name}</strong>
                <span>{model.roles.join(" / ")}</span>
                <em data-tone={modelStateTone(model)}>{model.compatible ? model.stateLabel : "不兼容"}</em>
              </div>
              <p>{model.family} · {model.recommendedFor} · {model.installedSizeBytes ? "占用" : "预计"} {formatBytes(model.installedSizeBytes || model.estimatedDownloadBytes)} · {model.runtimeStatus === "healthy" ? `运行时就绪 ${model.runtimeVersion}` : model.runtimeStatus === "unavailable" ? "运行时不可用" : "运行时未检测"}</p>
              {model.compatibilityReason && <small className="voice-model-error">{model.compatibilityReason}</small>}
              {model.localPath && <small>本机路径：{model.localPath}</small>}
              {model.projectReferences.length > 0 && <small>项目引用：{model.projectReferences.join("、")}</small>}
              {model.requiresLicenseAcceptance && (
                <label className="voice-model-license">
                  <input
                    aria-label={`接受 ${model.name} 许可`}
                    checked={model.licenseAccepted}
                    disabled={model.licenseAccepted || operation !== undefined}
                    onChange={(event) => {
                      if (event.target.checked) startModelOperation(model, "accept_license");
                    }}
                    type="checkbox"
                  />
                  已阅读并接受 {model.licenseName || "模型许可条款"}
                  {model.licenseUrl && <a href={model.licenseUrl} rel="noreferrer" target="_blank">查看条款</a>}
                </label>
              )}
              {operation && <OperationProgress operation={operation} onCancel={cancel} />}
              <div className="voice-model-card-actions">
                {!operation && !isInstalled && (
                  <button
                    className="button button-primary"
                    disabled={model.requiresLicenseAcceptance && !model.licenseAccepted}
                    onClick={() => startModelOperation(model, "install")}
                    title={model.requiresLicenseAcceptance && !model.licenseAccepted ? "请先确认模型许可条款。" : undefined}
                    type="button"
                  >
                    {downloadLabel}
                  </button>
                )}
                {!operation && isInstalled && <button className="button button-secondary" onClick={() => startModelOperation(model, "self_test")} type="button">自检</button>}
                {!operation && isInstalled && <button className="button button-danger" onClick={() => startModelOperation(model, "delete")} type="button">删除</button>}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function OperationProgress({
  operation,
  onCancel,
}: {
  readonly operation: VoiceAudioModelOperationView;
  readonly onCancel: (operationId: string) => Promise<void>;
}) {
  return (
    <div className="voice-model-progress" role="status">
      <small>{operation.message}</small>
      {operation.percent >= 0 ? <progress max="100" value={operation.percent} /> : <progress aria-label="正在处理" />}
      {operation.operation === "install" && (
        <button className="button button-secondary" onClick={() => void onCancel(operation.id)} type="button">取消下载</button>
      )}
    </div>
  );
}

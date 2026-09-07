/**
 * Engine-owned Ollama management panel.
 *
 * Nimo never reaches Ollama's native API. It observes the Engine read model
 * and submits versioned commands whose durable system jobs can be watched by
 * either desktop client.
 */
import { useCallback, useEffect, useState } from "react";

import type {
  EngineClient,
  EngineCommandClient,
  OllamaManagerView,
  OllamaModelView,
  OllamaRuntimeCommand,
} from "@nimo/engine-contracts";

import { formatOllamaSize, OLLAMA_RECOMMENDED_MODELS } from "../lib/ollama-catalog";

interface OllamaModelPanelProps {
  readonly commandClient: Pick<
    EngineCommandClient,
    | "cancelJob"
    | "configureOllamaRuntime"
    | "controlOllamaRuntime"
    | "deleteOllamaModel"
    | "pullOllamaModel"
    | "resumeJob"
    | "setOllamaModelRoles"
  >;
  readonly engineClient: Pick<EngineClient, "getOllama">;
}

type PanelStatus = {
  readonly tone: "danger" | "muted" | "success" | "warning";
  readonly text: string;
  readonly detail: string;
};

const initialStatus: PanelStatus = {
  tone: "muted",
  text: "未检测",
  detail: "尚未读取 Engine 主机上的 Ollama 状态。",
};

function idempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `nimo-ollama-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function statusFromView(view: OllamaManagerView): PanelStatus {
  const runtime = view.runtime;
  if (runtime.status === "healthy" || runtime.status === "external") {
    return {
      tone: "success",
      text: `${view.models.length} 个模型`,
      detail: runtime.detail || "Engine 主机上的 Ollama 已就绪。",
    };
  }
  if (runtime.status === "starting" || runtime.status === "degraded") {
    return { tone: "warning", text: "处理中", detail: runtime.detail || "Ollama 正在检查或恢复。" };
  }
  return { tone: "danger", text: "不可用", detail: runtime.detail || "Ollama 当前不可用。" };
}

function roleLabel(model: OllamaModelView): string {
  if (model.roles.includes("generation") && model.roles.includes("embedding")) return "生成 + 嵌入";
  if (model.roles.includes("generation")) return "生成";
  if (model.roles.includes("embedding")) return "嵌入";
  if (model.roles.includes("managed")) return "已纳入路由";
  return "未配置";
}

function impactDescription(view: OllamaManagerView, model: string): string {
  const impact = view.routingImpactByModel[model];
  if (impact === undefined) return "";
  const parts: string[] = [];
  if (impact.generationSelected) parts.push("当前生成模型");
  if (impact.embeddingSelected) parts.push("当前嵌入模型");
  if (impact.profileIds.length) parts.push(`${impact.profileIds.length} 个模型配置`);
  if (impact.primaryRouteIds.length) parts.push(`${impact.primaryRouteIds.length} 条主路由`);
  if (impact.fallbackRouteIds.length) parts.push(`${impact.fallbackRouteIds.length} 条备用路由`);
  return parts.join("、");
}

export function OllamaModelPanel({ commandClient, engineClient }: OllamaModelPanelProps) {
  const [status, setStatus] = useState<PanelStatus>(initialStatus);
  const [view, setView] = useState<OllamaManagerView | null>(null);
  const [pullInput, setPullInput] = useState("");
  const [actionModel, setActionModel] = useState<string | null>(null);
  const [actionLabel, setActionLabel] = useState("");

  const refresh = useCallback(async () => {
    try {
      const next = await engineClient.getOllama();
      setView(next);
      setStatus(statusFromView(next));
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setView(null);
      setStatus({
        tone: "danger",
        text: "需要升级 Engine",
        detail: `无法读取 Ollama Engine 契约。请更新或重新连接 Engine；不会回退为浏览器直连。${detail ? ` (${detail})` : ""}`,
      });
    }
  }, [engineClient]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (view === null || !view.activeOperations.some((item) => item.state === "queued" || item.state === "running")) return undefined;
    const interval = window.setInterval(() => void refresh(), 1200);
    return () => window.clearInterval(interval);
  }, [refresh, view]);

  const run = useCallback(async (label: string, fn: () => Promise<unknown>, model = "") => {
    setActionLabel(label);
    setActionModel(model || null);
    try {
      await fn();
      await refresh();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setStatus({ tone: "danger", text: "操作失败", detail });
    } finally {
      setActionLabel("");
      setActionModel(null);
    }
  }, [refresh]);

  const handleRuntime = useCallback((kind: OllamaRuntimeCommand["kind"]) => {
    if (view === null) return;
    return run("运行时操作", () => commandClient.controlOllamaRuntime({
      kind,
      expectedRevision: view.revision,
      idempotencyKey: idempotencyKey(),
    }));
  }, [commandClient, run, view]);

  const handlePull = useCallback(() => {
    const model = pullInput.trim();
    if (view === null || !model) return;
    return run(`下载 ${model}`, () => commandClient.pullOllamaModel({
      kind: "pull_ollama_model",
      model,
      expectedRevision: view.revision,
      idempotencyKey: idempotencyKey(),
    }), model);
  }, [commandClient, pullInput, run, view]);

  const updateRoles = useCallback((model: OllamaModelView, patch: Partial<Record<"managed" | "generation" | "embedding", boolean>>) => {
    if (view === null) return;
    const roles = {
      managed: model.roles.includes("managed"),
      generation: model.roles.includes("generation"),
      embedding: model.roles.includes("embedding"),
      ...patch,
    };
    if (roles.generation || roles.embedding) roles.managed = true;
    return run(`更新 ${model.name} 角色`, () => commandClient.setOllamaModelRoles({
      kind: "set_ollama_model_roles",
      model: model.name,
      ...roles,
      expectedRevision: view.revision,
      idempotencyKey: idempotencyKey(),
    }), model.name);
  }, [commandClient, run, view]);

  const handleDelete = useCallback((model: OllamaModelView) => {
    if (view === null) return;
    const impact = view.routingImpactByModel[model.name];
    if (impact === undefined) {
      setStatus({ tone: "warning", text: "请刷新", detail: "模型影响信息已过期。" });
      return;
    }
    const impactText = impactDescription(view, model.name);
    const confirmText = impactText
      ? `删除「${model.name}」会同时清理：${impactText}。此操作不可撤销，是否继续？`
      : `确定从 Engine 主机的 Ollama 删除「${model.name}」？此操作不可撤销。`;
    if (!window.confirm(confirmText)) return;
    return run(`删除 ${model.name}`, () => commandClient.deleteOllamaModel({
      kind: "delete_ollama_model",
      model: model.name,
      confirmationToken: impact.confirmationToken,
      cascadeConfiguration: Boolean(impactText),
      expectedRevision: view.revision,
      idempotencyKey: idempotencyKey(),
    }), model.name);
  }, [commandClient, run, view]);

  const configure = useCallback((field: "enabled" | "autoStart" | "preferLocal", value: boolean) => {
    if (view === null) return;
    return run("保存运行时设置", () => commandClient.configureOllamaRuntime({
      kind: "configure_ollama_runtime",
      [field]: value,
      expectedRevision: view.revision,
      idempotencyKey: idempotencyKey(),
    }));
  }, [commandClient, run, view]);

  const cancelOperation = useCallback((taskId: string) => run("取消任务", () => commandClient.cancelJob({
    kind: "cancel_job",
    taskId,
    reason: "用户在 Nimo 取消 Ollama 管理任务",
  })), [commandClient, run]);

  const retryOperation = useCallback((taskId: string) => run("重试任务", () => commandClient.resumeJob({
    kind: "resume_job",
    taskId,
  })), [commandClient, run]);

  const operating = actionLabel !== "";
  const runningModelNames = new Set(view?.activeOperations.filter((item) => item.state === "queued" || item.state === "running").map((item) => item.model));

  return (
    <div className="ollama-model-panel">
      <div className="ollama-panel-header">
        <strong>Engine 主机 Ollama</strong>
        <span className={`ollama-badge ollama-badge-${status.tone}`}>{status.text}</span>
        <button className="button button-secondary" disabled={operating} onClick={() => void refresh()} type="button">刷新</button>
        <a className="button button-quiet" href="https://ollama.com/library" rel="noreferrer" target="_blank">模型库</a>
      </div>
      <p className="ollama-panel-description">{status.detail}</p>

      {view !== null && <>
        <div className="ollama-runtime-summary">
          <small>运行态：{view.runtime.status}{view.runtime.version ? ` · ${view.runtime.version}` : ""}</small>
          <small>存储：{view.storage.displayLabel}</small>
          <small>服务所有权：{view.ownership === "engine_owned" ? "Engine 托管" : view.ownership === "external" ? "外部服务" : "不可用"}</small>
        </div>
        <div className="ollama-model-card-actions">
          {view.capabilities.canEnsure && <button className="button button-secondary" disabled={operating} onClick={() => void handleRuntime("ensure_ollama_runtime")} type="button">检查/启动</button>}
          {view.capabilities.canRestart && <button className="button button-secondary" disabled={operating} onClick={() => void handleRuntime("restart_ollama_runtime")} type="button">重启</button>}
          {view.capabilities.canStop && <button className="button button-danger" disabled={operating} onClick={() => void handleRuntime("stop_ollama_runtime")} type="button">停止托管服务</button>}
        </div>
        <div className="ollama-runtime-options">
          <label><input checked={view.sidecar.enabled} disabled={operating} onChange={(event) => void configure("enabled", event.target.checked)} type="checkbox" /> 启用 Engine sidecar</label>
          <label><input checked={view.sidecar.autoStart} disabled={operating || !view.sidecar.enabled} onChange={(event) => void configure("autoStart", event.target.checked)} type="checkbox" /> 自动启动</label>
          <label><input checked={view.sidecar.preferLocal} disabled={operating} onChange={(event) => void configure("preferLocal", event.target.checked)} type="checkbox" /> 优先本机托管</label>
        </div>
        {view.endpointScope === "external_host" && <p className="ollama-panel-description">当前显示并管理的是 Engine 主机的外部 Ollama；客户端不会读取其目录或路径。</p>}

        {view.activeOperations.length > 0 && <div className="ollama-active-operations" role="status">
          {view.activeOperations.map((operation) => <div key={operation.taskId}>
            <span>{operation.model || "Ollama 运行时"} · {operation.operation || operation.kind} · {operation.state}</span>
            {(operation.state === "queued" || operation.state === "running") && <button className="button button-quiet" disabled={operating} onClick={() => void cancelOperation(operation.taskId)} type="button">取消</button>}
            {operation.state === "failed" && <button className="button button-quiet" disabled={operating} onClick={() => void retryOperation(operation.taskId)} type="button">重试</button>}
          </div>)}
        </div>}

        <div className="ollama-model-list" role="list" aria-label="Engine 主机 Ollama 模型">
          {view.models.length === 0 && <p className="ollama-empty">Engine 主机上的模型会显示在这里。请检查或启动运行时。</p>}
          {view.models.map((model) => {
            const meta = [
              typeof model.details.family === "string" ? model.details.family : "",
              typeof model.details.parameterSize === "string" ? model.details.parameterSize : "",
              typeof model.details.quantizationLevel === "string" ? model.details.quantizationLevel : "",
              formatOllamaSize(model.size),
              model.modifiedAt.split("T")[0],
            ].filter(Boolean).join(" · ");
            const pending = actionModel === model.name || runningModelNames.has(model.name);
            return <div className="ollama-model-card" key={model.name} role="listitem">
              <div className="ollama-model-card-heading"><strong>{model.name}</strong><span className="ollama-badge ollama-badge-default">{roleLabel(model)}</span></div>
              <small>{meta || "由 Engine 管理"}</small>
              <div className="ollama-model-card-actions">
                {!model.roles.includes("managed") && <button className="button button-secondary" disabled={operating || pending} onClick={() => void updateRoles(model, { managed: true })} type="button">纳入路由</button>}
                {model.roles.includes("managed") && <button className="button button-quiet" disabled={operating || pending} onClick={() => void updateRoles(model, { managed: false, generation: false, embedding: false })} type="button">移出路由</button>}
                <button className="button button-quiet" disabled={operating || pending} onClick={() => void updateRoles(model, { generation: !model.roles.includes("generation") })} type="button">{model.roles.includes("generation") ? "取消生成" : "设为生成"}</button>
                <button className="button button-quiet" disabled={operating || pending} onClick={() => void updateRoles(model, { embedding: !model.roles.includes("embedding") })} type="button">{model.roles.includes("embedding") ? "取消嵌入" : "设为嵌入"}</button>
                <button className="button button-danger" disabled={operating || pending || !view.capabilities.canDelete} onClick={() => void handleDelete(model)} type="button">{pending ? "处理中…" : "删除"}</button>
              </div>
            </div>;
          })}
        </div>

        <hr className="ollama-separator" />
        <div className="ollama-pull-section">
          <strong>新增模型到 Engine 主机</strong>
          <p className="ollama-panel-description">下载通过 Engine 系统任务执行；重启后会按模型实际状态恢复。</p>
          <div className="ollama-pull-row">
            <input aria-label="模型名称" onChange={(event) => setPullInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void handlePull(); }} placeholder="输入模型名，如 llama3.2、qwen2.5:7b" type="text" value={pullInput} />
            <select aria-label="常用模型" onChange={(event) => { if (event.target.value) setPullInput(event.target.value); }} value="">
              <option value="">常用</option>
              {OLLAMA_RECOMMENDED_MODELS.map((item) => <option key={item.model} value={item.model}>{item.label} ({item.model})</option>)}
            </select>
            <button className="button button-primary" disabled={operating || !view.capabilities.canPull || !pullInput.trim()} onClick={() => void handlePull()} type="button">{actionLabel.startsWith("下载") ? "下载中…" : "新增"}</button>
          </div>
          {actionLabel && <p className="ollama-pull-status" role="status">{actionLabel}：任务已交由 Engine 持久化执行。</p>}
        </div>
      </>}
    </div>
  );
}

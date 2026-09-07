import { useState } from "react";

import type { ModelProviderOptionView } from "@nimo/engine-contracts";

import type { ModelProfileDraft } from "../lib/model-routing-session";
import { OverlaySurface } from "./OverlaySurface";

export type ModelProfileDialogMode = "add" | "edit";

export interface ModelProfileConnectionDraft {
  readonly apiKey: string;
  readonly apiKeyAction: "preserve" | "replace" | "clear";
  readonly baseUrl: string;
}

const fallbackProviderOptions: readonly ModelProviderOptionView[] = [
  { id: "tongyi", label: "阿里百炼", models: ["qwen3.7-max", "qwen-plus", "text-embedding-v4"], apiKeyRequired: true },
  { id: "tongyi_coding", label: "阿里百炼 Coding Plan", models: ["qwen3.7-plus", "kimi-k2.5"], apiKeyRequired: true },
  { id: "tongyi_token_plan", label: "阿里百炼 Token Plan", models: ["qwen3.8-max", "qwen3.7-plus"], apiKeyRequired: true, defaultBaseUrl: "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", connectionHint: "仅使用以 sk-sp- 开头的 Token Plan 专属 API Key；不得与按量付费或 Coding Plan 的密钥、接口地址混用。" },
  { id: "deepseek", label: "DeepSeek", models: ["deepseek-v4-flash", "deepseek-v4-pro"], apiKeyRequired: true },
  { id: "openai", label: "OpenAI", models: ["gpt-5.6", "gpt-4o", "o3"], apiKeyRequired: true },
  { id: "anthropic", label: "Anthropic", models: ["claude-sonnet-5", "claude-opus-4-8"], apiKeyRequired: true },
  { id: "kimi", label: "Kimi (Moonshot)", models: ["kimi-k2.7-code", "kimi-k2.6"], apiKeyRequired: true },
  { id: "mimo", label: "小米 MiMo", models: ["mimo-v2.5", "mimo-v2.5-pro"], apiKeyRequired: true },
  { id: "tencent", label: "腾讯混元", models: ["hy3", "hy3-preview"], apiKeyRequired: true },
  { id: "minimax", label: "MiniMax", models: ["MiniMax-M3", "MiniMax-M2.7"], apiKeyRequired: true },
  { id: "siliconflow", label: "硅基流动", models: ["deepseek-ai/DeepSeek-V4-Flash"], apiKeyRequired: true },
  { id: "volcengine_ark", label: "火山方舟 (Volcano Ark)", models: ["deepseek-v4-flash", "doubao-seed-2.0-lite"], apiKeyRequired: true },
  { id: "opencode", label: "OpenCode Go", models: ["deepseek-v4-flash"], apiKeyRequired: true },
  { id: "ollama", label: "Ollama", models: ["qwen2.5", "llama3.3"], apiKeyRequired: false },
  { id: "custom", label: "自定义 OpenAI 兼容", models: [], apiKeyRequired: true },
];

function providerOption(providerId: string, options: readonly ModelProviderOptionView[]): ModelProviderOptionView {
  return options.find((option) => option.id === providerId)
    ?? { id: providerId, label: providerId, models: [], apiKeyRequired: true };
}

function inferredMetadata(provider: string, model: string, fallback: Pick<ModelProfileDraft, "tierLabel" | "supportsThinking" | "supportsMultiTurn">): Pick<ModelProfileDraft, "tierLabel" | "supportsThinking" | "supportsMultiTurn"> {
  const normalized = `${provider}:${model}`.toLowerCase();
  if (normalized.includes("reasoner") || normalized.includes(":o3") || normalized.includes("qwen3")) {
    return { tierLabel: "高质量", supportsThinking: true, supportsMultiTurn: true };
  }
  if (normalized.includes("mini") || normalized.includes("turbo")) {
    return { tierLabel: "经济", supportsThinking: false, supportsMultiTurn: true };
  }
  return fallback;
}

function sourceLabel(provider: string, model: string, options: readonly ModelProviderOptionView[]): string {
  return `${providerOption(provider, options).label} ${model}`.trim();
}

export interface ModelProfileFormProps {
  readonly initialApiKey?: string | undefined;
  readonly initialBaseUrl?: string | undefined;
  readonly initialKeyConfigured?: boolean | undefined;
  readonly initialDraft: ModelProfileDraft;
  readonly mode: ModelProfileDialogMode;
  readonly onCancel: () => void;
  readonly onSave: (draft: ModelProfileDraft, connection: ModelProfileConnectionDraft) => void;
  readonly providerOptions?: readonly ModelProviderOptionView[] | undefined;
}

/**
 * The form is intentionally shared by the regular registry and source-sized
 * dialog fixture: one set of provider behavior, validation, and field order.
 */
export function ModelProfileForm({ initialApiKey = "", initialBaseUrl = "", initialKeyConfigured = false, initialDraft, mode, onCancel, onSave, providerOptions = fallbackProviderOptions }: ModelProfileFormProps) {
  const [draft, setDraft] = useState<ModelProfileDraft>(initialDraft);
  const [apiKey, setApiKey] = useState(initialApiKey);
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [clearApiKey, setClearApiKey] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [validationMessage, setValidationMessage] = useState("");
  const provider = providerOption(draft.provider, providerOptions);
  const hasKnownModel = provider.models.includes(draft.model);
  const [customModelRequested, setCustomModelRequested] = useState(draft.model.length > 0 && !hasKnownModel);
  const usesCustomModel = customModelRequested || (draft.model.length > 0 && !hasKnownModel);
  const selectedModel = usesCustomModel ? "__custom__" : draft.model;
  const apiKeyDisabled = !provider.apiKeyRequired;

  const updateDraft = (patch: Partial<ModelProfileDraft>) => setDraft((current) => ({ ...current, ...patch }));
  const updateProvider = (providerId: string) => {
    const nextProvider = providerOption(providerId, providerOptions);
    updateDraft({
      provider: nextProvider.id,
      model: nextProvider.models[0] ?? "",
      label: "",
    });
    setBaseUrl(nextProvider.defaultBaseUrl ?? "");
    setCustomModelRequested(false);
    setClearApiKey(false);
    setValidationMessage("");
  };
  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const model = draft.model.trim();
    if (!model) {
      setValidationMessage("请填写或选择模型。 ");
      return;
    }
    if (draft.provider === "custom" && !baseUrl.trim()) {
      setValidationMessage("自定义供应商需要填写接口地址。 ");
      return;
    }
    const metadata = inferredMetadata(draft.provider, model, draft);
    const normalizedKey = apiKey.trim();
    onSave(
      { ...draft, ...metadata, label: draft.label.trim() || sourceLabel(draft.provider, model, providerOptions), model },
      {
        apiKey: apiKeyDisabled ? "" : normalizedKey,
        apiKeyAction: apiKeyDisabled || clearApiKey
          ? "clear"
          : normalizedKey
            ? "replace"
            : initialKeyConfigured
              ? "preserve"
              : "clear",
        baseUrl: baseUrl.trim(),
      },
    );
  };

  return (
    <form className="model-profile-source-form" onSubmit={submit}>
      <label className="model-profile-source-row"><span>供应商</span><select aria-label="供应商" onChange={(event) => updateProvider(event.target.value)} value={draft.provider}>{!providerOptions.some((option) => option.id === draft.provider) && <option value={draft.provider}>{draft.provider}</option>}{providerOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
      <label className="model-profile-source-row"><span>模型</span><select aria-label="模型" onChange={(event) => {
        const value = event.target.value;
        setCustomModelRequested(value === "__custom__");
        updateDraft({ model: value === "__custom__" ? "" : value });
      }} value={selectedModel}><option value="">请选择模型</option>{provider.models.map((model) => <option key={model} value={model}>{model}</option>)}<option value="__custom__">自定义模型 ID…</option></select></label>
      {usesCustomModel && <label className="model-profile-source-row is-follow-up"><span>模型 ID</span><input aria-label="自定义模型 ID" autoFocus onChange={(event) => updateDraft({ model: event.target.value })} placeholder="输入模型 ID" required value={draft.model} /></label>}
      <label className="model-profile-source-row"><span>显示名称</span><input aria-label="显示名称" onChange={(event) => updateDraft({ label: event.target.value })} placeholder={sourceLabel(draft.provider, draft.model || "模型", providerOptions)} value={draft.label} /></label>
      <label className="model-profile-source-row"><span>API Key</span><span className="model-profile-secret"><input aria-label="API Key" disabled={apiKeyDisabled || clearApiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={apiKeyDisabled ? "本地服务，无需 API Key" : initialKeyConfigured ? "已安全配置；留空则保留" : "sk-..."} type={showApiKey ? "text" : "password"} value={apiKey} /><button className="model-profile-secret-toggle" disabled={apiKeyDisabled || clearApiKey} onClick={() => setShowApiKey((visible) => !visible)} type="button">{showApiKey ? "隐藏" : "显示"}</button></span></label>
      {initialKeyConfigured && !apiKeyDisabled && <label className="model-profile-clear-key"><input checked={clearApiKey} onChange={(event) => setClearApiKey(event.target.checked)} type="checkbox" />清除已保存的 API Key</label>}
      <label className="model-profile-source-row"><span>接口地址</span><input aria-label="接口地址" onChange={(event) => setBaseUrl(event.target.value)} placeholder="（可选）自定义接口地址，默认留空" value={baseUrl} /></label>
      {provider.connectionHint && <p className="model-profile-source-hint">{provider.connectionHint}</p>}
      <p className="model-profile-source-hint">同一供应商的不同模型可共用相同的 API Key。添加后可在「流程路由」中为各个步骤指定使用此模型。</p>
      {validationMessage && <p className="model-profile-source-error" role="alert">{validationMessage}</p>}
      <footer><button className="model-profile-source-cancel" onClick={onCancel} type="button">取消</button><button className="model-profile-source-submit" type="submit">{mode === "edit" ? "保存修改" : "添加模型"}</button></footer>
    </form>
  );
}

export interface ModelProfileDialogProps extends ModelProfileFormProps {}

export function ModelProfileDialog(props: ModelProfileDialogProps) {
  const label = props.mode === "edit" ? "编辑模型" : "添加模型";
  return <OverlaySurface ariaLabel={label} className={`model-profile-source-dialog is-${props.mode}`} onClose={props.onCancel}><ModelProfileForm {...props} /></OverlaySurface>;
}

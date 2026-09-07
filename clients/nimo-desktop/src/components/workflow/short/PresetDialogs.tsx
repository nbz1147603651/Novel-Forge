import { useState } from "react";

import { AppDialog } from "../../AppDialog";
import { parseShortWorkflowJson, type ShortWorkflowPayloadPatch } from "../../../lib/short-workflow-session";

/** 存为预设弹窗。 */
export function SavePresetDialog({ onClose, onSave, suggestedName }: { readonly onClose: () => void; readonly onSave: (name: string) => void; readonly suggestedName: string }) {
  const [name, setName] = useState(suggestedName);
  return <AppDialog confirmDisabled={name.trim() === ""} confirmLabel="保存" description="为此预设命名；保存后由 Engine 持久化，应用重启后仍可恢复。" onClose={onClose} onConfirm={() => onSave(name)} title="存为预设">
    <label className="workflow-short-dialog-field"><span>预设名称</span><input aria-label="预设名称" autoFocus onChange={(event) => setName(event.target.value)} placeholder="例如：都市悬疑短篇 v1" value={name} /></label>
  </AppDialog>;
}

/** 导入 JSON 弹窗（解析 short / run_short 配置）。 */
export function ImportJsonDialog({ onClose, onParsed }: { readonly onClose: () => void; readonly onParsed: (payload: ShortWorkflowPayloadPatch) => void }) {
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const parse = () => {
    const result = parseShortWorkflowJson(source);
    if (result.payload === undefined) {
      setError(result.error ?? "无法解析 JSON。");
      return;
    }
    onParsed(result.payload);
  };
  return <AppDialog closeOnConfirm={false} confirmLabel="解析配置" description="粘贴短篇模板或包含 short / run_short 的 JSON；解析前会执行与启动命令一致的字段校验。" onClose={onClose} onConfirm={parse} title="导入 JSON">
    <label className="workflow-short-editor"><span>JSON 内容</span><textarea aria-label="短篇 JSON 内容" onChange={(event) => { setSource(event.target.value); setError(""); }} placeholder={'{\n  "theme": "…"\n}'} value={source} /></label>
    {error && <p aria-live="polite" className="workflow-field-validation">{error}</p>}
  </AppDialog>;
}

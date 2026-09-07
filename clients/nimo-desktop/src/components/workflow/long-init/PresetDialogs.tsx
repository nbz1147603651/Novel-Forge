import { useState } from "react";

import { AppDialog } from "../../AppDialog";
import type { LongInitPayloadPatch } from "./fields";

/** 存为预设弹窗（写入引擎持久层）。 */
export function SavePresetDialog({ onClose, onSave, suggestedName }: { readonly onClose: () => void; readonly onSave: (name: string) => Promise<void> | void; readonly suggestedName: string }) {
  const [name, setName] = useState(suggestedName);
  return (
    <AppDialog confirmDisabled={name.trim() === ""} confirmLabel="保存" description="预设将写入与旧版共用的引擎持久层，应用重启后仍可恢复。" onClose={onClose} onConfirm={() => void onSave(name.trim())} title="存为预设">
      <label className="long-init-dialog-field"><span>预设名称</span><input autoFocus onChange={(e) => setName(e.target.value)} placeholder="例如：都市悬疑长篇 v1" value={name} /></label>
    </AppDialog>
  );
}

/** 导入 JSON 弹窗（解析 long_init / init_long 字段补丁）。 */
export function ImportDialog({ onClose, onImport }: { readonly onClose: () => void; readonly onImport: (patch: LongInitPayloadPatch) => void }) {
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const parse = () => {
    try {
      const parsed = JSON.parse(source) as Record<string, unknown>;
      const candidate = (parsed.long_init ?? parsed.init_long ?? parsed) as Record<string, unknown>;
      const patch: Record<string, unknown> = {};
      if (typeof candidate.premise === "string") patch.premise = candidate.premise;
      if (typeof candidate.genre === "string") patch.genre = candidate.genre;
      if (typeof candidate.characters_hint === "string") patch.charactersHint = candidate.characters_hint;
      if (typeof candidate.world_hint === "string") patch.worldHint = candidate.world_hint;
      if (typeof candidate.conflict_hint === "string") patch.conflictHint = candidate.conflict_hint;
      if (typeof candidate.project_id === "string") patch.projectId = candidate.project_id;
      if (Object.keys(patch).length === 0) { setError("JSON 中未找到可用字段。"); return; }
      onImport(patch as LongInitPayloadPatch);
    } catch { setError("无法解析 JSON。"); }
  };
  return (
    <AppDialog closeOnConfirm={false} confirmLabel="导入" description="粘贴包含 long_init / init_long 的 JSON。" onClose={onClose} onConfirm={parse} title="导入 JSON">
      <textarea className="long-init-textarea is-dialog" onChange={(e) => { setSource(e.target.value); setError(""); }} placeholder='{"premise": "…"}' value={source} />
      {error && <p className="long-init-validation-text">{error}</p>}
    </AppDialog>
  );
}

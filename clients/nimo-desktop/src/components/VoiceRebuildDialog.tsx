import { useMemo, useState } from "react";

import type { VoiceCastMemberView } from "@nimo/engine-contracts";

import { OverlaySurface } from "./OverlaySurface";
import { allVoiceRebuildIds, rebuildableVoiceCast, rebuildRoleLabel, rebuildSourceLabel, selectedVoiceRebuildIds, systemMatchedVoiceIds, toggleVoiceRebuildId } from "../lib/voice-rebuild-session";

/** Selective voice-team rebuild that preserves every unselected assignment. */
export function VoiceRebuildDialog({ cast, onClose, onConfirm }: { readonly cast: readonly VoiceCastMemberView[]; readonly onClose: () => void; readonly onConfirm: (ids: readonly string[]) => void }) {
  // The native dialog deliberately keeps the work-level narrator outside this
  // list: it has a separate item and can be regenerated on its own.
  const rebuildableCast = useMemo(() => rebuildableVoiceCast(cast), [cast]);
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set());
  const systemIds = useMemo(() => systemMatchedVoiceIds(rebuildableCast), [rebuildableCast]);
  const selectAll = () => setSelectedIds(allVoiceRebuildIds(rebuildableCast));
  const selectSystem = () => setSelectedIds(systemIds);
  const confirm = () => onConfirm(selectedVoiceRebuildIds(rebuildableCast, selectedIds));

  return (
    <OverlaySurface ariaLabel="重新构建配音团队" onClose={onClose}>
      <section className="voice-rebuild-dialog">
        <header><h2>重新构建配音团队</h2><span>已选 {selectedIds.size} 位</span></header>
        <p>只会重新分配勾选角色的音色；未勾选的既有分配保持不变。旁白采用独立的作品级音色设计，可在左侧“旁白”条目中单独重新生成。</p>
        <h3>选择需要重建的角色</h3>
        <div className="voice-rebuild-list" role="group" aria-label="选择需要重建的角色">
          {rebuildableCast.map((member) => (
            <label key={member.id}>
              <input checked={selectedIds.has(member.id)} onChange={() => setSelectedIds((current) => toggleVoiceRebuildId(current, member.id))} type="checkbox" />
              <span>{member.name} · {rebuildRoleLabel(member)} · {rebuildSourceLabel(member)}</span>
            </label>
          ))}
        </div>
        <div className="voice-rebuild-tools"><button className="button button-secondary" onClick={selectAll} type="button">全选</button><button className="button button-secondary" onClick={selectSystem} type="button">仅系统匹配</button></div>
        <footer><button className="button button-secondary" onClick={onClose} type="button">取消</button><span /><button className="button button-primary" disabled={selectedIds.size === 0} onClick={confirm} type="button">{selectedIds.size > 0 ? `重建 ${selectedIds.size} 位角色` : "选择要重建的角色"}</button></footer>
      </section>
    </OverlaySurface>
  );
}

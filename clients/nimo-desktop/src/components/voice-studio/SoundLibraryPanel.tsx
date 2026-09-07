import { useEffect, useState } from "react";

import type { EngineCommandClient, VoiceStudioView } from "@nimo/engine-contracts";

export type SoundAssetKind = "bgm" | "soundscape" | "sfx";
export type SoundAssetStatus = "approved" | "pending" | "rejected";
export type SoundAssetScope = "project" | "application";
export type CommercialUseStatus = "cleared" | "review_required" | "restricted";

const KIND_LABELS: Record<SoundAssetKind, string> = { bgm: "背景音乐", soundscape: "环境声", sfx: "短音效" };
const STATUS_LABELS: Record<SoundAssetStatus, string> = { approved: "已批准", pending: "待试听", rejected: "已停用" };
const STATUS_ICONS: Record<SoundAssetStatus, string> = { approved: "✓", pending: "◌", rejected: "—" };
const COMMERCIAL_STATUS_LABELS: Record<CommercialUseStatus, string> = {
  cleared: "已确认可商用",
  review_required: "待核对授权",
  restricted: "限制商业使用",
};

async function encodeSoundFile(file: File): Promise<{ readonly name: string; readonly base64: string }> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return { name: file.name, base64: window.btoa(binary) };
}

/**
 * Sound library panel (声音库) — manage voice assets: generate, import, approve.
 * Replicates PySide6 SoundLibraryPanel with generation schemes, import, and
 * approval workflow.
 */
export function SoundLibraryPanel({
  busy,
  commandClient,
  onNotice,
  onRefresh,
  projectId,
  assets,
}: {
  readonly assets: VoiceStudioView["soundAssets"];
  readonly busy: boolean;
  readonly commandClient: EngineCommandClient;
  readonly onNotice: (message: string) => void;
  readonly onRefresh: () => Promise<void>;
  readonly projectId: string;
}) {
  const initialAsset = assets[0] ?? null;
  const [kindFilter, setKindFilter] = useState<"" | SoundAssetKind>("");
  const [statusFilter, setStatusFilter] = useState<"" | SoundAssetStatus>("");
  const [scopeFilter, setScopeFilter] = useState<"" | SoundAssetScope>("");
  const [importKind, setImportKind] = useState<SoundAssetKind>("soundscape");
  const [importTags, setImportTags] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(initialAsset?.id ?? null);
  const [tagDraft, setTagDraft] = useState(initialAsset?.tags.join("、") ?? "");
  const [commercialUseStatus, setCommercialUseStatus] = useState<CommercialUseStatus>(initialAsset?.commercialUseStatus ?? "review_required");
  const [licenseDraft, setLicenseDraft] = useState(initialAsset?.license ?? "");
  const [operationBusy, setOperationBusy] = useState(false);
  const isBusy = busy || operationBusy;

  useEffect(() => {
    if (selectedId !== null && assets.some((asset) => asset.id === selectedId)) return;
    const first = assets[0] ?? null;
    setSelectedId(first?.id ?? null);
    setTagDraft(first?.tags.join("、") ?? "");
    setCommercialUseStatus(first?.commercialUseStatus ?? "review_required");
    setLicenseDraft(first?.license ?? "");
  }, [assets, selectedId]);

  const filteredAssets = assets.filter((asset) => {
    if (kindFilter && asset.kind !== kindFilter) return false;
    if (statusFilter && asset.status !== statusFilter) return false;
    if (scopeFilter && asset.scope !== scopeFilter) return false;
    return true;
  });

  const selectedAsset = assets.find((asset) => asset.id === selectedId) ?? null;
  const approvedCount = assets.filter((a) => a.status === "approved").length;
  const projectCount = assets.filter((a) => a.scope === "project").length;
  const applicationCount = assets.filter((a) => a.scope === "application").length;

  const updateAsset = async (
    action: "set_status" | "set_tags" | "set_commercial_rights" | "publish",
    options: {
      readonly status?: SoundAssetStatus;
      readonly tags?: readonly string[];
      readonly commercialUseStatus?: CommercialUseStatus;
      readonly licenseNote?: string;
    } = {},
  ) => {
    if (selectedAsset === null || isBusy) return;
    setOperationBusy(true);
    try {
      const result = await commandClient.updateSoundAsset({
        kind: "update_sound_asset",
        projectId,
        assetId: selectedAsset.id,
        action,
        ...options,
      });
      onNotice(result.message);
      if (result.status === "accepted") await onRefresh();
    } catch {
      onNotice("声音资产更新失败；资源库保持原状。");
    } finally {
      setOperationBusy(false);
    }
  };

  return (
    <section className="voice-library-panel">
      <div className="voice-library-body">
          {/* Intro + badge + generate button */}
          <div className="voice-library-intro">
            <div>
              <h4>作品与应用声音资源库</h4>
              <p>项目资产服务当前作品；已批准的候选可发布到应用资源库，供其他作品按标签复用。短音效仍按具体剧情线索补充。</p>
            </div>
            <span className="voice-library-badge">{assets.length} 项 · 项目 {projectCount} · 应用 {applicationCount} · 已批准 {approvedCount}</span>
            <button className="button button-primary" disabled={isBusy} onClick={async () => {
              onNotice("正在生成作品声音候选；完成后请逐项试听并批准。");
              setOperationBusy(true);
              try {
                const result = await commandClient.generateSoundPalette({
                  kind: "generate_sound_palette",
                  projectId,
                });
                onNotice(result.message);
                if (result.status === "accepted") await onRefresh();
              } catch {
                onNotice("作品声音方案生成失败；现有声音资源库未改变。");
              } finally {
                setOperationBusy(false);
              }
            }} title="生成 3 首纯音乐与 2 组环境声候选，默认进入待试听状态" type="button">{isBusy ? "正在处理…" : "生成作品声音方案"}</button>
          </div>

          {/* Filter controls row (Segment 39) */}
          <div className="voice-library-filters">
            <label>筛选
              <select onChange={(e) => setKindFilter(e.target.value as "" | SoundAssetKind)} value={kindFilter}>
                <option value="">全部类型</option>
                <option value="bgm">背景音乐</option>
                <option value="soundscape">环境声</option>
                <option value="sfx">短音效</option>
              </select>
            </label>
            <label>
              <select onChange={(e) => setStatusFilter(e.target.value as "" | SoundAssetStatus)} value={statusFilter}>
                <option value="">全部状态</option>
                <option value="pending">待试听</option>
                <option value="approved">已批准</option>
                <option value="rejected">已停用</option>
              </select>
            </label>
            <label>
              <select onChange={(e) => setScopeFilter(e.target.value as "" | SoundAssetScope)} value={scopeFilter}>
                <option value="">全部范围</option>
                <option value="project">当前项目</option>
                <option value="application">应用共享</option>
              </select>
            </label>
            <div className="voice-library-import">
              <input onChange={(event) => setImportTags(event.target.value)} placeholder="导入标签：夜雨、城市" value={importTags} />
              <select onChange={(e) => setImportKind(e.target.value as SoundAssetKind)} value={importKind}>
                <option value="soundscape">导入环境声</option>
                <option value="bgm">导入背景音乐</option>
                <option value="sfx">导入短音效</option>
              </select>
              <label className={`button button-secondary${isBusy ? " is-disabled" : ""}`}>
                导入并打标签
                <input
                  accept=".mp3,.wav,.flac,.ogg,.m4a,audio/*"
                  disabled={isBusy}
                  hidden
                  multiple
                  onChange={async (event) => {
                    const files = [...(event.target.files ?? [])];
                    event.target.value = "";
                    if (files.length === 0) return;
                    onNotice(`正在导入 ${files.length} 个声音资产…`);
                    setOperationBusy(true);
                    try {
                      const encoded = await Promise.all(files.map(encodeSoundFile));
                      const result = await commandClient.importSoundAssets({
                        kind: "import_sound_assets",
                        projectId,
                        assetKind: importKind,
                        tags: importTags.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
                        files: encoded,
                      });
                      onNotice(result.message);
                      if (result.status === "accepted") await onRefresh();
                    } catch {
                      onNotice("声音资产导入失败；现有资源库未改变。");
                    } finally {
                      setOperationBusy(false);
                    }
                  }}
                  type="file"
                />
              </label>
            </div>
          </div>

          {/* Split layout: list + detail (Segment 40) */}
          <div className="voice-library-split">
            <div className="voice-library-list-card">
              <h4>项目候选与应用复用资产</h4>
              <div className="voice-library-list">
                {filteredAssets.map((asset) => (
                  <button
                    className={`voice-library-item${selectedId === asset.id ? " is-selected" : ""}`}
                    key={asset.id}
                    onClick={() => {
                      setSelectedId(asset.id);
                      setTagDraft(asset.tags.join("、"));
                      setCommercialUseStatus(asset.commercialUseStatus);
                      setLicenseDraft(asset.license);
                    }}
                    title={asset.tags.join("、")}
                    type="button"
                  >
                    <span className="voice-library-item-icon">{STATUS_ICONS[asset.status]}</span>
                    <span className="voice-library-item-info">
                      <strong>{asset.name}</strong>
                      <small>{KIND_LABELS[asset.kind]} · {asset.scope === "application" ? "应用库" : "本项目"} · {STATUS_LABELS[asset.status]}</small>
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Detail panel (Segment 41) */}
            <div className="voice-library-detail-card">
              {selectedAsset === null ? (
                <div className="voice-library-empty">
                  <h4>选择声音资产</h4>
                  <p>从左侧选择候选进行试听、编辑标签，并决定是否进入正式混音。</p>
                </div>
              ) : (
                <>
                  <h4>{selectedAsset.name}</h4>
                  <table className="voice-library-meta">
                    <tbody>
                      <tr><td><b>用途</b></td><td>{KIND_LABELS[selectedAsset.kind]}</td></tr>
                      <tr><td><b>状态</b></td><td>{STATUS_LABELS[selectedAsset.status]}</td></tr>
                      <tr><td><b>来源</b></td><td>{selectedAsset.source === "generated" ? "AI 生成" : selectedAsset.source === "imported" ? "用户导入" : "人工登记"}</td></tr>
                      <tr><td><b>归属</b></td><td>{selectedAsset.scope === "application" ? "应用共享资源库" : "当前项目"}</td></tr>
                      <tr><td><b>模型</b></td><td>{selectedAsset.provider ?? "—"} · {selectedAsset.model ?? "—"}</td></tr>
                      <tr><td><b>授权</b></td><td>{selectedAsset.license ?? "未填写"}</td></tr>
                      <tr><td><b>商用结论</b></td><td>{COMMERCIAL_STATUS_LABELS[selectedAsset.commercialUseStatus]}</td></tr>
                      <tr><td><b>生成方向</b></td><td>{selectedAsset.prompt ?? "—"}</td></tr>
                    </tbody>
                  </table>
                  <div className="voice-library-player">
                    {selectedAsset.audioUrl
                      ? <audio controls preload="metadata" src={selectedAsset.audioUrl} />
                      : <span>当前资产缺少可播放文件。</span>}
                  </div>
                  <div className="voice-library-tags">
                    <label>检索标签
                      <input onChange={(e) => setTagDraft(e.target.value)} placeholder="如：悬疑、夜雨、城市、人物余韵" value={tagDraft} />
                    </label>
                    <button className="button button-secondary" disabled={isBusy} onClick={() => void updateAsset("set_tags", { tags: tagDraft.split(/[,，、]/).map((item) => item.trim()).filter(Boolean) })} type="button">保存标签</button>
                  </div>
                  <div className="voice-library-rights">
                    <label>商用权利复核
                      <select onChange={(event) => setCommercialUseStatus(event.target.value as CommercialUseStatus)} value={commercialUseStatus}>
                        <option value="review_required">待核对授权</option>
                        <option value="cleared">已确认可商用</option>
                        <option value="restricted">限制商业使用</option>
                      </select>
                    </label>
                    <label>授权依据 / 条款备注
                      <input onChange={(event) => setLicenseDraft(event.target.value)} placeholder="例：购买订单、API 账号条款、CC0 来源链接" value={licenseDraft} />
                    </label>
                    <button
                      className="button button-secondary"
                      disabled={isBusy || (commercialUseStatus === "cleared" && licenseDraft.trim().length === 0)}
                      onClick={() => void updateAsset("set_commercial_rights", { commercialUseStatus, licenseNote: licenseDraft.trim() })}
                      type="button"
                    >保存授权结论</button>
                    <small>听感批准不代表获得发行权；商业/Master 交付仅接受“已确认可商用”资产。</small>
                  </div>
                  {/* Decision buttons (Segment 42) */}
                  <div className="voice-library-decisions">
                    <button className="button button-primary" disabled={isBusy} onClick={() => void updateAsset("set_status", { status: "approved" })} type="button">批准并用于混音</button>
                    <button className="button button-secondary" disabled={isBusy} onClick={() => void updateAsset("set_status", { status: "pending" })} type="button">保留待试听</button>
                    <button className="button button-secondary" disabled={isBusy} onClick={() => void updateAsset("set_status", { status: "rejected" })} type="button">停用</button>
                    <button
                      className="button button-secondary"
                      disabled={isBusy || selectedAsset.scope !== "project" || selectedAsset.status !== "approved" || selectedAsset.commercialUseStatus !== "cleared"}
                      onClick={() => void updateAsset("publish")}
                      title="复制已批准的项目资产到本机应用资源库，供其他项目复用"
                      type="button"
                    >
                      发布到应用库
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
      </div>
    </section>
  );
}

import type { ProviderGroupView } from "@nimo/engine-contracts";

/** 通路点检：供应商分组 + 模型状态卡片（mirrors dashboard_page.py _render_status）。 */
export function ProviderStatusSection({ groups }: { readonly groups: readonly ProviderGroupView[] }) {
  return (
    <section className="provider-status-section">
      {groups.map((group) => (
        <div className="provider-group" key={group.providerId}>
          <div className="provider-group-header">
            <span className={`provider-dot ${group.ready ? "is-ready" : "is-offline"}`} />
            <h4>{group.providerLabel}</h4>
            <span className="provider-state-label">
              {group.ready ? "已载入" : "未载入"}
            </span>
          </div>
          <div className="provider-models-grid">
            {group.models.map((model) => (
              <article className="provider-model-card" key={model.modelId}>
                <div className="model-card-top">
                  <span className={`status-dot dot-${model.health}`} />
                  <span className="model-name">{model.displayName}</span>
                </div>
                <small className="model-status-meta">
                  {model.modelId} · {model.statusLabel}
                </small>
                <div className="model-cap-row">
                  <span className={`cap-dot ${model.supportsThinking ? "cap-on" : "cap-off"}`} />
                  <small>思考</small>
                  <span className={`cap-dot ${model.supportsMultiTurn ? "cap-on" : "cap-off"}`} />
                  <small>多轮</small>
                </div>
              </article>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

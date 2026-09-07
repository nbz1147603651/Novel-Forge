import type {
  ChapterRuntimePolicyCommand,
  ChapterRuntimePolicyPreset,
  ChapterRuntimePolicyPresetView,
} from "@nimo/engine-contracts";

interface ChapterRuntimePolicyCardProps {
  readonly onChange: (value: ChapterRuntimePolicyCommand) => void;
  readonly options: readonly ChapterRuntimePolicyPresetView[];
  readonly value: ChapterRuntimePolicyCommand;
}

function sameValues(
  left: ChapterRuntimePolicyCommand,
  right: ChapterRuntimePolicyPresetView["values"],
): boolean {
  return left.intentGuardMode === right.intentGuardMode
    && left.factRefreshEnabled === right.factRefreshEnabled
    && left.inspirationEnabled === right.inspirationEnabled
    && left.inspirationCooldown === right.inspirationCooldown
    && left.shortAdaptiveRevisionEnabled === right.shortAdaptiveRevisionEnabled
    && left.longSingleFinalVerifyEnabled === right.longSingleFinalVerifyEnabled;
}

function detectPreset(
  value: ChapterRuntimePolicyCommand,
  options: readonly ChapterRuntimePolicyPresetView[],
): ChapterRuntimePolicyPreset {
  return options.find((option) => sameValues(value, option.values))?.id ?? "custom";
}

export function chapterRuntimePolicyCommand(
  value: ChapterRuntimePolicyCommand,
  options: readonly ChapterRuntimePolicyPresetView[],
  patch: Partial<ChapterRuntimePolicyCommand>,
): ChapterRuntimePolicyCommand {
  const next = { ...value, ...patch, preset: "custom" as const };
  return { ...next, preset: detectPreset(next, options) };
}

export function ChapterRuntimePolicyCard({ onChange, options, value }: ChapterRuntimePolicyCardProps) {
  const activeOption = options.find((option) => option.id === value.preset);
  return <section className="settings-card chapter-runtime-policy-card">
    <div className="settings-card-body">
      <div className="chapter-runtime-policy-heading">
        <span>
          <span className="section-kicker">章节质量与研究</span>
          <h3>用一套策略约束意图、证据、修订与终检</h3>
        </span>
        <span aria-live="polite" className="settings-dirty-badge">
          {activeOption?.label ?? "自定义"}
        </span>
      </div>
      <p>策略只作用于保存后新提交的任务。章节联网仍要求项目或短篇请求显式开启 research，不会自动覆盖作者设定。</p>
      <div aria-label="章节运行策略预设" className="chapter-runtime-preset-grid" role="radiogroup">
        {options.map((option) => <button
          aria-checked={value.preset === option.id}
          className={value.preset === option.id ? "chapter-runtime-preset is-active" : "chapter-runtime-preset"}
          key={option.id}
          onClick={() => onChange({ preset: option.id, ...option.values })}
          role="radio"
          type="button"
        >
          <strong>{option.label}</strong>
          <small>{option.description}</small>
        </button>)}
      </div>
      <details className="chapter-runtime-advanced">
        <summary>高级控制</summary>
        <div className="chapter-runtime-advanced-grid">
          <label className="setting-row">
            <span><strong>意图门禁</strong><small>block 会拒绝改变人物、关系、POV、结局和 locked 要素的自动修复。</small></span>
            <select aria-label="意图门禁" onChange={(event) => onChange(chapterRuntimePolicyCommand(value, options, { intentGuardMode: event.target.value as ChapterRuntimePolicyCommand["intentGuardMode"] }))} value={value.intentGuardMode}>
              <option value="off">关闭</option><option value="warn">记录警告</option><option value="block">阻断冲突</option>
            </select>
          </label>
          <PolicyToggle checked={value.factRefreshEnabled} description="仅在本章存在 must 事实缺口时，最多执行一次事实查询。" label="章节事实刷新" onChange={(checked) => onChange(chapterRuntimePolicyCommand(value, options, { factRefreshEnabled: checked }))} />
          <PolicyToggle checked={value.inspirationEnabled} description="低频抽取场景机制与感官纹理，不写入 StoryKernel。" label="低频外部灵感" onChange={(checked) => onChange(chapterRuntimePolicyCommand(value, options, { inspirationEnabled: checked }))} />
          <label className="setting-row">
            <span><strong>灵感冷却章数</strong><small>同类灵感刷新之间至少间隔 1–20 章。</small></span>
            <input aria-label="灵感冷却章数" max={20} min={1} onChange={(event) => onChange(chapterRuntimePolicyCommand(value, options, { inspirationCooldown: Number(event.target.value) }))} type="number" value={value.inspirationCooldown} />
          </label>
          <PolicyToggle checked={value.shortAdaptiveRevisionEnabled} description="按问题执行 0–2 轮修订，不再固定消耗编辑轮次。" label="短篇自适应修订" onChange={(checked) => onChange(chapterRuntimePolicyCommand(value, options, { shortAdaptiveRevisionEnabled: checked }))} />
          <PolicyToggle checked={value.longSingleFinalVerifyEnabled} description="所有语义改文收敛后，对最终文本哈希统一验证。" label="长篇单次最终验证" onChange={(checked) => onChange(chapterRuntimePolicyCommand(value, options, { longSingleFinalVerifyEnabled: checked }))} />
        </div>
      </details>
    </div>
  </section>;
}

function PolicyToggle({ checked, description, label, onChange }: { readonly checked: boolean; readonly description: string; readonly label: string; readonly onChange: (checked: boolean) => void }) {
  return <label className="setting-row">
    <span><strong>{label}</strong><small>{description}</small></span>
    <input aria-label={label} checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
  </label>;
}

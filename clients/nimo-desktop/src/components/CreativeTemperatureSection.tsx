import { useEffect, useRef, useState } from "react";

import type { SettingsView } from "@nimo/engine-contracts";

export type CreativeTemperatureScope = "recommended" | "chapter_core" | "init_and_chapter" | "custom";

export interface CreativeTemperatureDraft {
  readonly enabled: boolean;
  readonly lowerDelta: number;
  readonly scope: CreativeTemperatureScope;
  readonly upperDelta: number;
  readonly customTaskKeys?: readonly string[];
}

export const defaultCreativeTemperatureDraft: CreativeTemperatureDraft = {
  enabled: false,
  scope: "recommended",
  lowerDelta: 0.3,
  upperDelta: 0.1,
  customTaskKeys: [],
};

const scopeLabels: Readonly<Record<CreativeTemperatureScope, string>> = {
  recommended: "推荐创意任务",
  chapter_core: "章节核心",
  init_and_chapter: "初始化 + 章节",
  custom: "自定义",
};

export function isCreativeTemperatureDraftValid(draft: CreativeTemperatureDraft): boolean {
  return [draft.lowerDelta, draft.upperDelta].every((value) => Number.isFinite(value) && value >= 0 && value <= 2);
}

export function isSameCreativeTemperatureDraft(left: CreativeTemperatureDraft, right: CreativeTemperatureDraft): boolean {
  return left.enabled === right.enabled
    && left.lowerDelta === right.lowerDelta
    && left.scope === right.scope
    && left.upperDelta === right.upperDelta
    && (left.customTaskKeys ?? []).join(",") === (right.customTaskKeys ?? []).join(",");
}

export interface CreativeTemperatureSectionProps {
  readonly draft: CreativeTemperatureDraft;
  readonly initiallyExpanded?: boolean;
  readonly onDraftChange: (draft: CreativeTemperatureDraft) => void;
  readonly routingGroups: SettingsView["routingGroups"];
  readonly showValidation: boolean;
}

/**
 * Source-shaped inline equivalent of PySide6's "创作火候 — 浮动与适用范围"
 * section. It owns only temporary form affordances; persistence remains an
 * EngineClient concern in its parent page.
 */
export function CreativeTemperatureSection({ draft, initiallyExpanded = false, onDraftChange, routingGroups, showValidation }: CreativeTemperatureSectionProps) {
  const [expanded, setExpanded] = useState(() => {
    if (initiallyExpanded) return true;
    try { const v = localStorage.getItem("nimo:settings:creativeTempExpanded"); return v === null ? false : v === "1"; } catch { return false; }
  });
  const sectionRef = useRef<HTMLElement | null>(null);
  const lowerError = !Number.isFinite(draft.lowerDelta) || draft.lowerDelta < 0 || draft.lowerDelta > 2;
  const upperError = !Number.isFinite(draft.upperDelta) || draft.upperDelta < 0 || draft.upperDelta > 2;

  const update = (partial: Partial<CreativeTemperatureDraft>) => onDraftChange({ ...draft, ...partial });
  const selectedTaskKeys = new Set(draft.customTaskKeys ?? []);
  const toggleTask = (taskKey: string) => {
    const next = new Set(selectedTaskKeys);
    if (next.has(taskKey)) next.delete(taskKey);
    else next.add(taskKey);
    update({ customTaskKeys: [...next] });
  };

  useEffect(() => { try { localStorage.setItem("nimo:settings:creativeTempExpanded", expanded ? "1" : "0"); } catch { /* ignore */ } }, [expanded]);

  useEffect(() => {
    if (!initiallyExpanded) return;
    const frame = window.requestAnimationFrame(() => sectionRef.current?.scrollIntoView({ block: "start" }));
    return () => window.cancelAnimationFrame(frame);
  }, [initiallyExpanded]);

  return <section className={expanded ? "creative-temperature-section is-expanded" : "creative-temperature-section"} ref={sectionRef}>
    <button aria-expanded={expanded} className="settings-accordion-toggle creative-temperature-toggle" onClick={() => setExpanded((current) => !current)} type="button">
      <span>{expanded ? "⌄" : "›"}</span>创作火候 — 浮动与适用范围
    </button>
    {expanded && <div className="creative-temperature-body">
      <p className="creative-temperature-description">开启后仅创意类任务围绕基础火候轻微随机；保护任务保持固定温度。</p>
      <header><h3>基础浮动</h3><p>设置基础火候附近的随机上下浮动范围。</p></header>
      <label className="creative-temperature-field">
        <span><strong>启用浮动</strong><small>默认关闭；开启后基础 1.0 时约为 0.7–1.1</small></span>
        <select aria-label="启用创作火候浮动" onChange={(event) => update({ enabled: event.target.value === "true" })} value={String(draft.enabled)}><option value="false">false</option><option value="true">true</option></select>
      </label>
      <label className="creative-temperature-field">
        <span><strong>适用范围</strong><small>推荐模式最稳；自定义可逐项勾选</small></span>
        <select aria-label="创作火候适用范围" onChange={(event) => update({ scope: event.target.value as CreativeTemperatureScope })} value={draft.scope}>{(Object.keys(scopeLabels) as CreativeTemperatureScope[]).map((scope) => <option key={scope} value={scope}>{scopeLabels[scope]}</option>)}</select>
      </label>
      <TemperatureDeltaField ariaLabel="创作火候下浮幅度" error={lowerError} hint="实际温度最多低于基础火候多少；推荐 0.30" label="下浮幅度" onChange={(lowerDelta) => update({ lowerDelta })} showValidation={showValidation} value={draft.lowerDelta} />
      <TemperatureDeltaField ariaLabel="创作火候上浮幅度" error={upperError} hint="实际温度最多高于基础火候多少；推荐 0.10" label="上浮幅度" onChange={(upperDelta) => update({ upperDelta })} showValidation={showValidation} value={draft.upperDelta} />
      {draft.scope === "custom" && <section className="creative-temperature-custom" aria-label="高级自定义浮动任务">
        <header><h3>高级：自定义浮动任务</h3><p>仅在适用范围选择“自定义”时生效；灰色任务受保护，保持固定温度。</p></header>
        {routingGroups.map((group) => <section className="creative-temperature-task-group" key={group.id}>
          <h4>{group.icon ?? "✦"} {group.label}</h4>
          <div className="creative-temperature-task-grid">{group.routes.map((task) => <label className={task.temperatureJitterProtected ? "is-protected" : ""} key={task.id} title={task.temperatureJitterProtected ? "保护任务：检查、抽取、裁判、修复或审计链路保持固定温度。" : task.hint}><input checked={!task.temperatureJitterProtected && selectedTaskKeys.has(task.taskKey)} disabled={task.temperatureJitterProtected} onChange={() => toggleTask(task.taskKey)} type="checkbox" />{task.label}</label>)}</div>
        </section>)}
      </section>}
    </div>}
  </section>;
}

function TemperatureDeltaField({ ariaLabel, error, hint, label, onChange, showValidation, value }: { readonly ariaLabel: string; readonly error: boolean; readonly hint: string; readonly label: string; readonly onChange: (value: number) => void; readonly showValidation: boolean; readonly value: number }) {
  return <label className={error && showValidation ? "creative-temperature-field has-error" : "creative-temperature-field"}>
    <span><strong>{label}</strong><small>{hint}</small>{error && showValidation && <em>请输入 0.0–2.0 之间的数值。</em>}</span>
    <input aria-label={ariaLabel} max="2" min="0" onChange={(event) => onChange(Number(event.target.value))} step="0.1" type="number" value={Number.isFinite(value) ? value : ""} />
  </label>;
}

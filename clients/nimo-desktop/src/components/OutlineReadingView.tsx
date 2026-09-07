import type { OutlineNodeView } from "@nimo/engine-contracts";

/** Read-only projection: lists stay separate and supporting detail is opt-in. */
export function OutlineReadingView({ node }: { readonly node: OutlineNodeView | undefined }) {
  if (node === undefined) return <div className="outline-reading"><p>完成立项后这里将显示章节因果与伏笔。</p></div>;
  const beats = node.beatsSummary?.length
    ? node.beatsSummary
    : node.summary.split(/\n+/u).map((text) => text.trim()).filter(Boolean);
  const supportingSections = [
    { id: "mainline", label: "主线推进", items: node.mainPlotPoints ?? [] },
    { id: "subplot", label: "支线推进", items: node.subplotPoints ?? [] },
    { id: "scene-goals", label: "场景设计约束", items: node.sceneDesignGoals ?? [] },
    { id: "notes", label: "创作备注", items: node.notes?.trim() ? [node.notes] : [] },
  ].filter((section) => section.items.length > 0);

  return <div className="outline-reading">
    {(node.facts?.length ?? 0) > 0 && <dl className="outline-reading-facts">{node.facts?.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
    {node.goal?.trim() && <section className="outline-reading-goal" aria-label="章节目标"><h3>章节目标</h3><p>{node.goal}</p></section>}
    {beats.length > 0 && <section className="outline-reading-beats" aria-label="叙事节拍"><h3>叙事节拍 <span>{beats.length} 个</span></h3><ol>{beats.map((beat, index) => <li key={`${node.id}-beat-${index}`}><span className="outline-beat-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><p>{beat}</p></li>)}</ol></section>}
    {supportingSections.length > 0 && <div className="outline-reading-support">{supportingSections.map((section) => <details key={section.id}><summary>{section.label}<span>{section.items.length} 项</span></summary><ul>{section.items.map((text, index) => <li key={`${section.id}-${index}`}>{text}</li>)}</ul></details>)}</div>}
    {!node.goal?.trim() && beats.length === 0 && supportingSections.length === 0 && <p>本章暂未提供详细大纲。</p>}
  </div>;
}

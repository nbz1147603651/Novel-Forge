import type { ChapterVersionComparison, VersionDiffSegment } from "../lib/chapter-version-diff";

function DiffSegment({ segment }: { readonly segment: VersionDiffSegment }) {
  return <span className={`chapter-version-diff-segment is-${segment.kind}`}>{segment.text}</span>;
}

/** PySide6 render_version_diff counterpart for a selected chapter-version pair. */
export function ChapterVersionDiffArtifact({ comparison }: { readonly comparison: ChapterVersionComparison }) {
  return <article aria-label="版本对比结果" className="chapter-version-diff-artifact">
    <h3>版本对比</h3>
    <section className="chapter-version-diff-summary" aria-label="版本差异摘要">
      <strong>{(comparison.similarityRatio * 100).toFixed(1)}%</strong>
      <span>相似度</span>
      <div><b>+{comparison.additions}</b><i>-{comparison.deletions}</i></div>
      <small>{comparison.older.label} → {comparison.newer.label}</small>
    </section>
    <section className="chapter-version-diff-details">
      <h4>变更详情</h4>
      {comparison.hunks.length === 0
        ? <p>所选版本的正文内容一致。</p>
        : comparison.hunks.map((hunk) => <p key={hunk.id}>{hunk.segments.map((segment, index) => <DiffSegment key={`${hunk.id}-${index}`} segment={segment} />)}</p>)}
    </section>
  </article>;
}

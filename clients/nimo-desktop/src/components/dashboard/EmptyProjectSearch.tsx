/** 卷库检索空态（mirrors dashboard_page.py EmptyState）。 */
export function EmptyProjectSearch({ query }: { readonly query: string }) {
  return (
    <div className="empty-project-search" role="status">
      <span className="section-kicker">卷库暂静</span>
      <h3>未寻得相合卷帙</h3>
      <p>
        {query.trim() === ""
          ? "请改换筛选条件，再检索在库卷册。"
          : `"${query.trim()}"尚未命中卷名、项目 ID、题材或气口。`}
      </p>
    </div>
  );
}

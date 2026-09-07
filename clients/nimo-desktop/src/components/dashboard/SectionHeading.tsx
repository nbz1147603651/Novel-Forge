/** 案头/卷帙通用区块标题（mirrors dashboard_page.py SectionHeading）。 */
export function SectionHeading({
  description,
  title,
}: {
  readonly title: string;
  readonly description: string;
}) {
  return (
    <div className="section-heading">
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  );
}

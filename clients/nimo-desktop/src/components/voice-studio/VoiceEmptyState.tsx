/** 声腔空态（mirrors PySide6 配音准备空态）。 */
export function VoiceEmptyState({
  description,
  title,
}: {
  readonly description: string;
  readonly title: string;
}) {
  return (
    <article className="voice-detail voice-empty-state">
      <span className="section-kicker">配音准备</span>
      <h2>{title}</h2>
      <p>{description}</p>
    </article>
  );
}

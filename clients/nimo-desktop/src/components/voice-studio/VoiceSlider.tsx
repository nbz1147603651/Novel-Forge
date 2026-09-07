/** 声腔参数滑杆（语速/音调/音量，mirrors PySide6 表演参数滑块）。 */
export function VoiceSlider({
  label,
  max = 20,
  onChange,
  suffix,
  tooltip,
  value,
}: {
  readonly label: string;
  readonly max?: number;
  readonly onChange: (value: number) => void;
  readonly suffix: string;
  readonly tooltip?: string;
  readonly value: number;
}) {
  return (
    <label title={tooltip}>
      <span>{label}</span>
      <input
        aria-label={label}
        max={max}
        min={-max}
        onChange={(event) => onChange(Number(event.target.value))}
        type="range"
        value={value}
      />
      <strong>
        {suffix === "×"
          ? (1 + value / 100).toFixed(2)
          : `${value >= 0 ? "+" : ""}${value}`}
        {suffix}
      </strong>
    </label>
  );
}

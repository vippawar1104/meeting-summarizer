/** A bar whose fill is the value and whose track is the lighter step behind it. */
export function Meter({ value, label }: { value: number; label: string }) {
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <div
      className="meter"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
    >
      <span style={{ width: `${clamped * 100}%` }} />
    </div>
  );
}

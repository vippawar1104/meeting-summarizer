/** Placeholder shapes while the first load is in flight: the page keeps its layout instead of jumping. */
export function Skeleton() {
  return (
    <div className="skeleton" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Loading your dashboard…</span>
      <div className="tiles" aria-hidden="true">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="tile sk-tile">
            <span className="sk sk-icon" />
            <span className="sk sk-line" />
            <span className="sk sk-value" />
          </div>
        ))}
      </div>
      <div className="card sk-block" aria-hidden="true">
        <span className="sk sk-line" />
        <span className="sk sk-line short" />
        <span className="sk sk-line" />
      </div>
    </div>
  );
}

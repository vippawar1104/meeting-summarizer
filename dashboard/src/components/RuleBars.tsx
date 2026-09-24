import { int, pct } from "../format";
import type { Rule } from "../types";
import { Meter } from "./Meter";

/** Precision per rule: of the findings people judged, the share they accepted. */
export function RuleBars({ rules }: { rules: Rule[] }) {
  if (rules.length === 0) {
    return <div className="empty">No findings yet. Open a pull request on a repository where Reviewly is installed.</div>;
  }
  const anyJudged = rules.some((r) => r.accepted + r.dismissed > 0);
  return (
    <>
      <div className="rules">
        {rules.map((r) => (
          <div className="rule" key={r.category}>
            <div className="name">{r.category}</div>
            <Meter value={r.precision ?? 0} label={`${r.category} precision`} />
            <div className="p">{pct(r.precision)}</div>
            <div className="counts">
              {int(r.accepted)} accepted · {int(r.dismissed)} dismissed · {int(r.pending)} not judged yet
              {r.resolved ? ` · ${int(r.resolved)} resolved` : ""}
            </div>
          </div>
        ))}
      </div>
      {!anyJudged && (
        <p className="sub" style={{ marginTop: 8 }}>
          Precision appears once someone reacts 👍 or 👎 to a comment, or replies <code>@reviewly accept</code> or{" "}
          <code>@reviewly dismiss</code>.
        </p>
      )}
    </>
  );
}

import { useState } from "react";
import { compact, int, monthLabel, usd } from "../format";
import type { UsageRow } from "../types";
import { Tooltip, useTooltip } from "./Tooltip";

export const MAX_BAR_PX = 96;

/** Strictly proportional to the value (a zero stays a 2px baseline sliver so the column is visible). */
export function barHeight(value: number, max: number): number {
  return value <= 0 ? 2 : Math.max(2, (value / max) * MAX_BAR_PX);
}

/** Reviews per month as columns. A table view of the same numbers is one click away. */
export function UsageChart({ rows }: { rows: UsageRow[] }) {
  const [asTable, setAsTable] = useState(false);
  const { tip, show, hide } = useTooltip();
  if (rows.length === 0) return <div className="empty">No usage yet.</div>;
  const max = Math.max(1, ...rows.map((r) => r.reviews));

  return (
    <figure style={{ margin: 0 }}>
      <div className="toolbar">
        <button className="link" onClick={() => setAsTable((v) => !v)}>
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>
      {asTable ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Month</th>
                <th className="num">Reviews</th>
                <th className="num">Findings</th>
                <th className="num">Tokens</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.period}>
                  <td>{monthLabel(r.period)}</td>
                  <td className="num">{int(r.reviews)}</td>
                  <td className="num">{int(r.findings)}</td>
                  <td className="num">{int(r.tokens)}</td>
                  <td className="num">{usd(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart" role="img" aria-label={`Reviews per month, ${rows.map((r) => `${monthLabel(r.period)}: ${r.reviews}`).join(", ")}`}>
          {rows.map((r) => {
            const label = `${monthLabel(r.period)}: ${int(r.reviews)} reviews, ${int(r.findings)} findings, ${compact(r.tokens)} tokens`;
            return (
              <div className="col" key={r.period}>
                <button
                  className="hit"
                  aria-label={label}
                  onMouseEnter={(e) => show(label, e.currentTarget)}
                  onMouseLeave={hide}
                  onFocus={(e) => show(label, e.currentTarget)}
                  onBlur={hide}
                >
                  <span className="val">{int(r.reviews)}</span>
                  <span className="bar" style={{ height: `${barHeight(r.reviews, max)}px` }} />
                  <span className="lab">{monthLabel(r.period)}</span>
                </button>
              </div>
            );
          })}
        </div>
      )}
      <Tooltip tip={tip} />
    </figure>
  );
}

import { compact, int, pct, relativeTime, usd } from "../format";
import type { RecentReview, RepoRow } from "../types";
import { Icon, type IconName } from "./Icon";

export function RepoTable({ repos }: { repos: RepoRow[] }) {
  if (repos.length === 0) return <div className="empty">No repositories with findings yet.</div>;
  return (
    <div className="table-wrap card">
      <table>
        <thead>
          <tr>
            <th>Repository</th>
            <th className="num">Findings</th>
            <th className="num">Accepted</th>
            <th className="num">Dismissed</th>
            <th className="num">Precision</th>
          </tr>
        </thead>
        <tbody>
          {repos.map((r) => (
            <tr key={r.repo}>
              <td>
                <span className="repo">
                  <Icon name="folder" size={14} />
                  {r.repo}
                </span>
              </td>
              <td className="num">{int(r.findings)}</td>
              <td className="num">{int(r.accepted)}</td>
              <td className="num">{int(r.dismissed)}</td>
              <td className="num">{pct(r.precision)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A status is always an icon plus a word, never color alone. */
export function statusOf(r: RecentReview): { word: string; cls: string; icon: IconName } {
  if (r.note?.startsWith("skipped")) return { word: "Skipped", cls: "skipped", icon: "minus-circle" };
  if (r.status === "done") return { word: "Reviewed", cls: "done", icon: "check-circle" };
  if (r.status === "dead") return { word: "Failed", cls: "dead", icon: "x-circle" };
  return { word: "In progress", cls: "queued", icon: "clock" };
}

export function RecentTable({ recent, now }: { recent: RecentReview[]; now?: Date }) {
  if (recent.length === 0) return <div className="empty">No reviews yet.</div>;
  return (
    <div className="table-wrap card">
      <table>
        <thead>
          <tr>
            <th>Pull request</th>
            <th>Status</th>
            <th className="num">Findings</th>
            <th className="num">Tokens</th>
            <th className="num">Cost</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((r) => {
            const s = statusOf(r);
            return (
              <tr key={`${r.repo}#${r.pr}@${r.sha}@${r.at}`}>
                <td>
                  {r.repo}#{r.pr} <span className="muted">{r.sha}</span>
                </td>
                <td title={r.note ?? undefined}>
                  <span className={`status ${s.cls}`}>
                    <Icon name={s.icon} size={14} />
                    {s.word}
                  </span>
                </td>
                <td className="num">{int(r.findings)}</td>
                <td className="num">{compact(r.tokens)}</td>
                <td className="num">{usd(r.cost_usd)}</td>
                <td className="muted">{relativeTime(r.at, now)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

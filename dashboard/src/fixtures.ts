import type { Overview } from "./types";

export const overview: Overview = {
  installation_id: 42,
  plan: { name: "free", limit: 20, used: 3 },
  period: "2026-09",
  totals: { reviews: 12, prs: 9, findings: 31, accepted: 14, dismissed: 5, resolved: 8, precision: 14 / 19, tokens: 45200, cost_usd: 0 },
  month: { reviews: 3, findings: 7, tokens: 9000, cost_usd: 0 },
  rules: [
    { category: "bug", posted: 18, accepted: 10, dismissed: 2, pending: 6, resolved: 5, precision: 10 / 12 },
    { category: "security", posted: 6, accepted: 4, dismissed: 3, pending: 0, resolved: 2, precision: 4 / 7 },
    { category: "testing", posted: 7, accepted: 0, dismissed: 0, pending: 7, resolved: 1, precision: null },
  ],
  repos: [{ repo: "acme/widgets", findings: 20, accepted: 9, dismissed: 3, precision: 0.75 }],
  usage: [
    { period: "2026-07", reviews: 4, findings: 9, tokens: 12000, cost_usd: 0 },
    { period: "2026-08", reviews: 5, findings: 15, tokens: 24000, cost_usd: 0 },
    { period: "2026-09", reviews: 3, findings: 7, tokens: 9000, cost_usd: 0 },
  ],
  recent: [
    { repo: "acme/widgets", pr: 12, sha: "a1b2c3d", status: "done", findings: 3, tokens: 4100, cost_usd: 0, note: null, at: "2026-09-25T11:00:00Z" },
    { repo: "acme/widgets", pr: 11, sha: "e4f5a6b", status: "dead", findings: 0, tokens: 0, cost_usd: 0, note: "boom", at: "2026-09-24T11:00:00Z" },
    { repo: "acme/api", pr: 3, sha: "c7d8e9f", status: "done", findings: 0, tokens: 0, cost_usd: 0, note: "skipped: superseded by a newer commit", at: "2026-09-24T09:00:00Z" },
  ],
};

export interface Plan {
  name: "free" | "pro";
  limit: number | null; // reviews per month; null = unlimited
  used: number;
}

export interface Totals {
  reviews: number;
  prs: number;
  findings: number;
  accepted: number;
  dismissed: number;
  resolved: number;
  precision: number | null; // null until someone has judged a finding
  tokens: number;
  cost_usd: number | null;
}

export interface Rule {
  category: string;
  posted: number;
  accepted: number;
  dismissed: number;
  pending: number;
  resolved: number;
  precision: number | null;
}

export interface RepoRow {
  repo: string;
  findings: number;
  accepted: number;
  dismissed: number;
  precision: number | null;
}

export interface UsageRow {
  period: string;
  reviews: number;
  findings: number;
  tokens: number;
  cost_usd: number | null;
}

export interface RecentReview {
  repo: string;
  pr: number;
  sha: string;
  status: string;
  findings: number;
  tokens: number;
  cost_usd: number | null;
  note: string | null;
  at: string;
}

export interface Overview {
  installation_id: number;
  plan: Plan;
  period: string;
  totals: Totals;
  month: { reviews: number; findings: number; tokens: number; cost_usd: number | null };
  rules: Rule[];
  repos: RepoRow[];
  usage: UsageRow[];
  recent: RecentReview[];
}

export interface Me {
  login: string;
  installations: number[];
}

import { useState } from "react";
import { startCheckout } from "../api";
import { int, monthLabel, share } from "../format";
import type { Plan } from "../types";
import { Icon } from "./Icon";
import { Meter } from "./Meter";

export function PlanCard({ plan, period, installation }: { plan: Plan; period: string; installation: number }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upgrade() {
    setBusy(true);
    setError(null);
    try {
      window.location.assign(await startCheckout(installation));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start checkout.");
      setBusy(false);
    }
  }

  if (plan.name === "pro" || plan.limit === null) {
    return (
      <div className="plan card">
        <div className="grow">
          <div className="name">
            <Icon name="check-circle" size={16} />
            Pro plan
          </div>
          <div className="note">Unlimited reviews. {int(plan.used)} so far in {monthLabel(period)}.</div>
        </div>
      </div>
    );
  }

  const nearLimit = plan.used >= plan.limit * 0.9;
  return (
    <div className="plan card">
      <div className="grow">
        <div className="name">
          <span className="pill">Free</span>
          {int(plan.used)} of {int(plan.limit)} reviews used in {monthLabel(period)}
        </div>
        <Meter value={share(plan.used, plan.limit)} label="Free reviews used this month" />
        <div className="note">
          {plan.used >= plan.limit
            ? "You have used all your free reviews this month."
            : nearLimit
              ? "You are almost at this month's limit."
              : "The count resets at the start of each month."}
        </div>
        {error && <div className="note" role="alert">{error}</div>}
      </div>
      <button className="btn primary" onClick={upgrade} disabled={busy}>
        {busy ? "Opening checkout…" : "Upgrade"}
        {!busy && <Icon name="arrow-up-right" size={14} />}
      </button>
    </div>
  );
}

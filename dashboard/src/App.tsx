import { useCallback, useEffect, useState } from "react";
import { Unauthorized, fetchConfig, fetchLLM, fetchMe, fetchOverview } from "./api";
import { GettingStarted } from "./components/GettingStarted";
import { Header } from "./components/Header";
import { Icon } from "./components/Icon";
import { Login } from "./components/Login";
import { ModelSettings } from "./components/ModelSettings";
import { PlanCard } from "./components/PlanCard";
import { RuleBars } from "./components/RuleBars";
import { SectionTitle } from "./components/SectionTitle";
import { Skeleton } from "./components/Skeleton";
import { RecentTable, RepoTable } from "./components/Tables";
import { Tile } from "./components/Tile";
import { UsageChart } from "./components/UsageChart";
import { compact, int, pct, relativeTime, usd } from "./format";
import { useRoute } from "./route";
import { useTheme } from "./theme";
import type { Me, Overview, PublicConfig } from "./types";

type Session = { kind: "loading" } | { kind: "login" } | { kind: "error"; message: string } | { kind: "ready"; me: Me };

const TITLES = { overview: "Overview", settings: "Settings" } as const;

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [route] = useRoute();
  const [session, setSession] = useState<Session>({ kind: "loading" });
  const [installation, setInstallation] = useState<number | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [ownKey, setOwnKey] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const installUrl = config?.app_install_url ?? null;

  useEffect(() => {
    document.title = session.kind === "ready" ? `${TITLES[route]} · Reviewly` : "Reviewly";
  }, [route, session.kind]);

  useEffect(() => {
    fetchConfig()
      .then(setConfig)
      .catch(() => setConfig(null)); // optional: the page works without it
    fetchMe()
      .then((me) => {
        setSession({ kind: "ready", me });
        setInstallation(me.installations[0] ?? null);
      })
      .catch((e) => setSession(e instanceof Unauthorized ? { kind: "login" } : { kind: "error", message: String(e.message ?? e) }));
  }, []);

  const load = useCallback(async (id: number) => {
    setError(null);
    setRefreshing(true);
    try {
      setOverview(await fetchOverview(id));
      setUpdatedAt(new Date());
    } catch (e) {
      if (e instanceof Unauthorized) setSession({ kind: "login" });
      else setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (installation === null) return;
    setOverview(null);
    setOwnKey(false);
    void load(installation);
    fetchLLM(installation)
      .then((s) => setOwnKey(s.configured && s.enabled))
      .catch(() => undefined); // only decides whether a checklist step shows as done
  }, [installation, load]);

  const signedIn = session.kind === "ready";
  const hasInstallation = signedIn && installation !== null;
  const shell = (body: React.ReactNode) => (
    <div className="page">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header
        installations={signedIn ? session.me.installations : []}
        selected={installation}
        onSelect={setInstallation}
        theme={theme}
        onToggleTheme={toggleTheme}
        signedIn={signedIn}
        login={signedIn ? session.me.login : undefined}
        route={route}
        showNav={hasInstallation}
      />
      <main id="main" tabIndex={-1}>
        {body}
      </main>
      <footer className="footer">
        <span>Reviewly</span>
        <a className="inline-link" href="https://github.com/vippawar1104/meeting-summarizer" target="_blank" rel="noopener noreferrer">
          Source <Icon name="external-link" size={11} />
        </a>
      </footer>
    </div>
  );

  if (session.kind === "loading") return shell(<Skeleton />);
  if (session.kind === "login") return shell(<Login config={config} />);
  if (session.kind === "error") {
    return shell(
      <div className="error" role="alert">
        <span>Could not reach the server: {session.message}</span>
        <button className="btn" onClick={() => window.location.reload()}>
          Try again
        </button>
      </div>,
    );
  }
  if (installation === null) {
    return shell(
      <div className="center">
        <h1>No installations yet</h1>
        <p>Install the Reviewly GitHub App on a repository, and it will show up here after its first review.</p>
        {installUrl && (
          <a className="btn primary" href={installUrl} style={{ textDecoration: "none", padding: "8px 14px" }}>
            <Icon name="plus" size={15} />
            Install on GitHub
          </a>
        )}
      </div>,
    );
  }

  const t = overview?.totals;
  return shell(
    <>
      {error && (
        <div className="error" role="alert">
          <span>Could not load data: {error}</span>
          <button className="btn" onClick={() => void load(installation)}>
            Try again
          </button>
        </div>
      )}
      {!overview && !error && <Skeleton />}

      {overview && t && (
        <>
          <div className="toolbar">
            <h1 className="page-title">{TITLES[route]}</h1>
            <span className="row">
              <span className="muted" aria-live="polite">
                {updatedAt ? `Updated ${relativeTime(updatedAt.toISOString()) || "just now"}` : ""}
              </span>
              <button className="btn" onClick={() => void load(installation)} disabled={refreshing} aria-label="Refresh data">
                <Icon name="refresh" size={14} />
                <span className="hide-narrow">{refreshing ? "Refreshing…" : "Refresh"}</span>
              </button>
            </span>
          </div>

          {route === "overview" ? (
            <>
              {t.reviews === 0 && <GettingStarted hasReviews={false} usesOwnKey={ownKey} installUrl={installUrl} />}

              <section aria-label="Summary">
                <SectionTitle icon="grid">Summary</SectionTitle>
                <div className="tiles">
                  <Tile icon="check-circle" label="Reviews" value={int(t.reviews)} />
                  <Tile icon="git-pull-request" label="Pull requests" value={int(t.prs)} />
                  <Tile icon="message-square" label="Findings posted" value={int(t.findings)} />
                  <Tile icon="target" label="Precision" value={pct(t.precision)} hint={t.precision === null ? "No feedback yet" : "Accepted of judged"} />
                  <Tile icon="thumbs-up" label="Accepted" value={int(t.accepted)} />
                  <Tile icon="thumbs-down" label="Dismissed" value={int(t.dismissed)} />
                  <Tile icon="cpu" label="Tokens" value={compact(t.tokens)} />
                  <Tile icon="coins" label="Cost" value={usd(t.cost_usd)} hint={t.cost_usd ? undefined : "No verified price yet"} />
                </div>
              </section>

              <section aria-label="Precision by rule">
                <SectionTitle icon="target">Precision by rule</SectionTitle>
                <p className="sub">Of the findings people judged, the share they accepted.</p>
                <RuleBars rules={overview.rules} />
              </section>

              <section aria-label="Monthly usage">
                <SectionTitle icon="calendar">Reviews per month</SectionTitle>
                <UsageChart rows={overview.usage} />
              </section>

              <section aria-label="Repositories">
                <SectionTitle icon="folder">Repositories</SectionTitle>
                <RepoTable repos={overview.repos} />
              </section>

              <section aria-label="Recent reviews">
                <SectionTitle icon="clock">Recent reviews</SectionTitle>
                <RecentTable recent={overview.recent} />
              </section>
            </>
          ) : (
            <>
              <section aria-label="Plan">
                <SectionTitle icon="credit-card">Plan and usage</SectionTitle>
                <PlanCard plan={overview.plan} period={overview.period} installation={overview.installation_id} />
              </section>

              <section aria-label="AI model">
                <SectionTitle icon="cpu">AI model</SectionTitle>
                <p className="sub">Reviews use Reviewly's models unless you add your own key.</p>
                <ModelSettings installation={overview.installation_id} onChange={setOwnKey} />
              </section>
            </>
          )}
        </>
      )}
    </>,
  );
}

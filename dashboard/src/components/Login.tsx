import { Icon } from "./Icon";

export function Login({ error }: { error?: string }) {
  return (
    <div className="center">
      <span className="mark">
        <Icon name="check" size={22} />
      </span>
      <h1>Reviewly</h1>
      <p>Sign in with GitHub to see reviews, findings and precision for your installations.</p>
      <a className="btn primary" href="/auth/github/login" style={{ textDecoration: "none", padding: "8px 14px" }}>
        <Icon name="log-in" size={15} />
        Sign in with GitHub
      </a>
      {error && <p role="alert">{error}</p>}
      {import.meta.env.DEV && (
        <p style={{ fontSize: 12 }}>
          Local development: open <code>/auth/dev-login?installation=ID</code> on the API (needs{" "}
          <code>REVIEWLY_DASHBOARD_DEV_LOGIN=true</code>).
        </p>
      )}
    </div>
  );
}

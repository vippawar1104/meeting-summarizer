export function Login({ error }: { error?: string }) {
  return (
    <div className="center">
      <h1>Reviewly</h1>
      <p>Sign in with GitHub to see reviews, findings and precision for your installations.</p>
      <div>
        <a className="btn primary" href="/auth/github/login" style={{ display: "inline-block", textDecoration: "none" }}>
          Sign in with GitHub
        </a>
      </div>
      {error && <p role="alert">{error}</p>}
      {import.meta.env.DEV && (
        <p className="muted" style={{ fontSize: 13 }}>
          Local development: open <code>/auth/dev-login?installation=ID</code> on the API (needs{" "}
          <code>REVIEWLY_DASHBOARD_DEV_LOGIN=true</code>).
        </p>
      )}
    </div>
  );
}

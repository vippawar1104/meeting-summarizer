import { FormEvent, useState } from "react";
import type { PublicConfig } from "../types";
import { Icon } from "./Icon";

/** Offers only the ways to sign in that this server actually supports. */
export function Login({ config, error }: { config: PublicConfig | null; error?: string }) {
  const [installation, setInstallation] = useState("42");
  // Until the config is known (or if it could not be loaded) assume GitHub sign-in works.
  const githubReady = config === null || config.github_login;

  function devLogin(e: FormEvent) {
    e.preventDefault();
    window.location.assign(`/auth/dev-login?installation=${encodeURIComponent(installation.trim())}`);
  }

  return (
    <div className="center">
      <span className="mark">
        <Icon name="check" size={22} />
      </span>
      <h1>Reviewly</h1>
      <p>Sign in to see reviews, findings and precision for your installations.</p>

      {githubReady ? (
        <a className="btn primary" href="/auth/github/login" style={{ textDecoration: "none", padding: "8px 14px" }}>
          <Icon name="log-in" size={15} />
          Sign in with GitHub
        </a>
      ) : (
        <p role="status" className="notice">
          GitHub sign-in is not set up on this server yet. Whoever hosts Reviewly needs to finish the GitHub App setup (see the README).
        </p>
      )}

      {config?.dev_login && (
        <form onSubmit={devLogin} className="dev-login">
          <span className="pill">Local development</span>
          <label>
            Installation id
            <input value={installation} onChange={(e) => setInstallation(e.target.value)} inputMode="numeric" pattern="[0-9]+" required />
          </label>
          <button className="btn" type="submit">
            Continue as developer
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            42 has demo data if you ran <code>make seed</code>.
          </span>
        </form>
      )}

      {error && <p role="alert">{error}</p>}
    </div>
  );
}

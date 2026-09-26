import type { Route } from "../route";
import type { Theme } from "../theme";
import { Icon } from "./Icon";

interface Props {
  installations: number[];
  selected: number | null;
  onSelect: (id: number) => void;
  theme: Theme;
  onToggleTheme: () => void;
  signedIn: boolean;
  login?: string;
  route?: Route;
  showNav?: boolean;
}

export function Header({ installations, selected, onSelect, theme, onToggleTheme, signedIn, login, route, showNav }: Props) {
  return (
    <header className="header">
      <span className="brand">
        <span className="mark">
          <Icon name="check" size={14} />
        </span>
        Reviewly
      </span>
      {showNav && (
        <nav aria-label="Main" className="nav">
          <a href="#/overview" aria-current={route === "overview" ? "page" : undefined}>
            <Icon name="grid" size={14} />
            Overview
          </a>
          <a href="#/settings" aria-current={route === "settings" ? "page" : undefined}>
            <Icon name="cpu" size={14} />
            Settings
          </a>
        </nav>
      )}
      <span className="spacer" />
      {installations.length > 1 && selected !== null && (
        <select aria-label="Installation" value={selected} onChange={(e) => onSelect(Number(e.target.value))}>
          {installations.map((id) => (
            <option key={id} value={id}>
              Installation {id}
            </option>
          ))}
        </select>
      )}
      <button className="btn" onClick={onToggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
        <Icon name={theme === "dark" ? "sun" : "moon"} size={14} />
        <span className="hide-narrow">{theme === "dark" ? "Light mode" : "Dark mode"}</span>
      </button>
      {signedIn && (
        <>
          {login && <span className="who hide-narrow">{login}</span>}
          <a className="link" href="/auth/logout">
            <Icon name="log-out" size={14} />
            Sign out
          </a>
        </>
      )}
    </header>
  );
}

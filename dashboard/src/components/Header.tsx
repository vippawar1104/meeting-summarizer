import type { Theme } from "../theme";

interface Props {
  installations: number[];
  selected: number | null;
  onSelect: (id: number) => void;
  theme: Theme;
  onToggleTheme: () => void;
  signedIn: boolean;
}

export function Header({ installations, selected, onSelect, theme, onToggleTheme, signedIn }: Props) {
  return (
    <header className="header">
      <span className="brand">Reviewly</span>
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
        {theme === "dark" ? "Light mode" : "Dark mode"}
      </button>
      {signedIn && (
        <a className="link" href="/auth/logout">
          Sign out
        </a>
      )}
    </header>
  );
}

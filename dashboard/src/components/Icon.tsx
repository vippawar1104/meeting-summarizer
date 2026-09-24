import type { ReactNode } from "react";

/** Small line icons (24x24, 1.75 stroke), drawn inline so the dashboard needs no icon dependency. */
const PATHS: Record<string, ReactNode> = {
  "check-circle": (<><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.7 2.7L16 9.5" /></>),
  "x-circle": (<><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></>),
  "minus-circle": (<><circle cx="12" cy="12" r="9" /><path d="M8.5 12h7" /></>),
  clock: (<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  "git-pull-request": (<><circle cx="6" cy="6" r="2.2" /><circle cx="6" cy="18" r="2.2" /><circle cx="18" cy="18" r="2.2" /><path d="M6 8.2v7.6M18 15.8V10a3 3 0 0 0-3-3h-3" /><path d="M14 4.5L11.5 7 14 9.5" /></>),
  "message-square": (<path d="M20 15a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z" />),
  target: (<><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.2" /></>),
  "thumbs-up": (<><path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z" /><path d="M7 11l4-7c1.5 0 2.5 1 2.5 2.5V9h5a2 2 0 0 1 2 2.3l-1.2 7a2 2 0 0 1-2 1.7H7" /></>),
  "thumbs-down": (<g transform="rotate(180 12 12)"><path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z" /><path d="M7 11l4-7c1.5 0 2.5 1 2.5 2.5V9h5a2 2 0 0 1 2 2.3l-1.2 7a2 2 0 0 1-2 1.7H7" /></g>),
  cpu: (<><rect x="6" y="6" width="12" height="12" rx="2" /><rect x="9.5" y="9.5" width="5" height="5" rx="1" /><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" /></>),
  coins: (<><circle cx="12" cy="12" r="9" /><path d="M14.5 9.2c-.5-.8-1.5-1.2-2.6-1.2-1.5 0-2.6.8-2.6 2 0 3 5.4 1.4 5.4 4.2 0 1.2-1.2 2-2.8 2-1.2 0-2.3-.5-2.8-1.4M12 6.5V8m0 8v1.5" /></>),
  "credit-card": (<><rect x="3" y="5.5" width="18" height="13" rx="2" /><path d="M3 10h18M7 15h3" /></>),
  calendar: (<><rect x="3.5" y="5" width="17" height="15.5" rx="2" /><path d="M3.5 10h17M8 3v4M16 3v4" /></>),
  folder: (<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />),
  grid: (<><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></>),
  bug: (<><path d="M9 8V6.5a3 3 0 0 1 6 0V8" /><rect x="7" y="8" width="10" height="11" rx="5" /><path d="M12 8v11M3.5 12H7M17 12h3.5M4.5 7l3 2.5M19.5 7l-3 2.5M4.5 18l3-2.5M19.5 18l-3-2.5" /></>),
  shield: (<><path d="M12 3l7.5 3v5.5c0 4.5-3.2 8-7.5 9.5-4.3-1.5-7.5-5-7.5-9.5V6z" /><path d="M9 12l2.2 2.2L15.5 10" /></>),
  flask: (<><path d="M9.5 3.5h5M10.5 3.5v5.2L5 18.5a1.6 1.6 0 0 0 1.4 2.4h11.2A1.6 1.6 0 0 0 19 18.5l-5.5-9.8V3.5" /><path d="M8 14h8" /></>),
  alert: (<><path d="M12 4l9 15.5H3z" /><path d="M12 10v4M12 17v.2" /></>),
  zap: (<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z" />),
  wrench: (<path d="M14.5 6.5a4 4 0 0 0 4.8 4.8l-9 9a2.1 2.1 0 0 1-3-3l9-9a4 4 0 0 1-1.8-1.8z" />),
  dot: (<circle cx="12" cy="12" r="3" />),
  sun: (<><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></>),
  moon: (<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />),
  "log-out": (<path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3M16 8l4 4-4 4M20 12H9" />),
  "log-in": (<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l4 4-4 4M14 12H3" />),
  "arrow-up-right": (<path d="M7 17L17 7M8 7h9v9" />),
  table: (<><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M3.5 10h17M3.5 15h17M9 4.5v15" /></>),
  "bar-chart": (<path d="M5 20V11M12 20V5M19 20v-7" />),
  check: (<path d="M5 12.5l4.5 4.5L19 7.5" />),
};

export type IconName = keyof typeof PATHS;

/** Decorative by default (the words next to it carry the meaning), so it is hidden from screen readers. */
export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name] ?? PATHS.dot}
    </svg>
  );
}

/** One icon per finding category; anything new falls back to a dot. */
export function categoryIcon(category: string): IconName {
  const map: Record<string, IconName> = {
    bug: "bug",
    security: "shield",
    testing: "flask",
    risk: "alert",
    performance: "zap",
    maintainability: "wrench",
  };
  return map[category] ?? "dot";
}

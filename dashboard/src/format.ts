/** Percent for a 0..1 ratio. null means "no data yet", which is not the same as 0%. */
export function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function int(value: number): string {
  return value.toLocaleString("en-US");
}

/** 1,284 / 12.9K / 4.2M */
export function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`;
  return String(value);
}

/** Cost is n/a when no verified price is known (the API reports 0). */
export function usd(value: number | null): string {
  if (!value) return "n/a";
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export function relativeTime(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const units: [number, string][] = [
    [60, "minute"],
    [60 * 60, "hour"],
    [60 * 60 * 24, "day"],
  ];
  let label = "";
  for (const [size, name] of units) {
    if (seconds >= size) {
      const n = Math.floor(seconds / size);
      label = `${n} ${name}${n === 1 ? "" : "s"} ago`;
    }
  }
  return label;
}

/** "2026-09" -> "Sep 2026" */
export function monthLabel(period: string): string {
  const [year, month] = period.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleString("en-US", {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function share(used: number, limit: number | null): number {
  if (!limit) return 0;
  return Math.min(1, used / limit);
}

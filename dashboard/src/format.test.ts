import { compact, int, monthLabel, pct, relativeTime, share, usd } from "./format";

describe("pct", () => {
  it("shows null as a dash, never 0%", () => {
    expect(pct(null)).toBe("—");
  });
  it("rounds ratios to whole percents", () => {
    expect(pct(0)).toBe("0%");
    expect(pct(0.666)).toBe("67%");
    expect(pct(1)).toBe("100%");
  });
});

describe("compact", () => {
  it.each([
    [999, "999"],
    [1284, "1.3K"],
    [12900, "13K"],
    [4_200_000, "4.2M"],
    [25_000_000, "25M"],
    [0, "0"],
  ])("%d -> %s", (n, expected) => expect(compact(n)).toBe(expected));
});

describe("usd", () => {
  it("is n/a when there is no verified cost", () => {
    expect(usd(0)).toBe("n/a");
    expect(usd(null)).toBe("n/a");
  });
  it("uses more precision for tiny amounts", () => {
    expect(usd(0.0042)).toBe("$0.0042");
    expect(usd(1.239)).toBe("$1.24");
  });
});

describe("int", () => {
  it("adds thousands separators", () => expect(int(1234567)).toBe("1,234,567"));
});

describe("relativeTime", () => {
  const now = new Date("2026-09-25T12:00:00Z");
  it.each([
    ["2026-09-25T11:59:40Z", "just now"],
    ["2026-09-25T11:55:00Z", "5 minutes ago"],
    ["2026-09-25T11:00:00Z", "1 hour ago"],
    ["2026-09-25T06:00:00Z", "6 hours ago"],
    ["2026-09-23T12:00:00Z", "2 days ago"],
  ])("%s -> %s", (iso, expected) => expect(relativeTime(iso, now)).toBe(expected));
});

describe("monthLabel", () => {
  it("formats a period", () => expect(monthLabel("2026-09")).toBe("Sep 2026"));
  it("does not shift across timezones", () => expect(monthLabel("2026-01")).toBe("Jan 2026"));
});

describe("share", () => {
  it("is used/limit capped at 1, and 0 without a limit", () => {
    expect(share(5, 20)).toBe(0.25);
    expect(share(30, 20)).toBe(1);
    expect(share(5, null)).toBe(0);
    expect(share(5, 0)).toBe(0);
  });
});

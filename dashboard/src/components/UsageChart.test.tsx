import { render, screen } from "@testing-library/react";
import { MAX_BAR_PX, UsageChart, barHeight } from "./UsageChart";

describe("barHeight", () => {
  it("is strictly proportional to the value", () => {
    expect(barHeight(15, 15)).toBe(MAX_BAR_PX);
    expect(barHeight(3, 15)).toBeCloseTo(MAX_BAR_PX / 5);
    expect(barHeight(7.5, 15)).toBeCloseTo(MAX_BAR_PX / 2);
  });
  it("keeps zero visible as a thin baseline sliver", () => {
    expect(barHeight(0, 15)).toBe(2);
  });
  it("never lets a small value vanish", () => {
    expect(barHeight(1, 1000)).toBe(2);
  });
});

describe("UsageChart", () => {
  const rows = [
    { period: "2026-08", reviews: 10, findings: 0, tokens: 0, cost_usd: null },
    { period: "2026-09", reviews: 5, findings: 0, tokens: 0, cost_usd: null },
  ];

  it("draws each column in proportion to the largest", () => {
    const { container } = render(<UsageChart rows={rows} />);
    const heights = [...container.querySelectorAll<HTMLElement>(".bar")].map((b) => parseFloat(b.style.height));
    expect(heights[0]).toBe(MAX_BAR_PX);
    expect(heights[1]).toBe(MAX_BAR_PX / 2);
  });

  it("labels the value at the top of each column and keeps every value in the accessible name", () => {
    render(<UsageChart rows={rows} />);
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAccessibleName(/Aug 2026: 10, Sep 2026: 5/);
  });

  it("says so when there is no data", () => {
    render(<UsageChart rows={[]} />);
    expect(screen.getByText("No usage yet.")).toBeInTheDocument();
  });
});

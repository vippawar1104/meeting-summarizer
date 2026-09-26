import { render } from "@testing-library/react";
import { Icon, categoryIcon } from "./Icon";

describe("Icon", () => {
  it("is decorative: hidden from screen readers and not focusable", () => {
    const { container } = render(<Icon name="bug" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
  });

  it("draws in the current text colour so it follows light and dark mode", () => {
    const { container } = render(<Icon name="shield" size={20} />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(svg).toHaveAttribute("width", "20");
    expect(svg.children.length).toBeGreaterThan(0);
  });

  it("falls back to a dot for an unknown name instead of drawing nothing", () => {
    const { container } = render(<Icon name={"nope" as never} />);
    expect(container.querySelector("circle")).not.toBeNull();
  });
});

describe("categoryIcon", () => {
  it.each([
    ["bug", "bug"],
    ["security", "shield"],
    ["testing", "flask"],
    ["risk", "alert"],
    ["performance", "zap"],
    ["maintainability", "wrench"],
    ["something-new", "dot"],
  ])("%s -> %s", (category, icon) => expect(categoryIcon(category)).toBe(icon));
});

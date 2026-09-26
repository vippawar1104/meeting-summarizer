import { render, screen } from "@testing-library/react";
import { GettingStarted } from "./GettingStarted";

describe("GettingStarted", () => {
  it("lists the three steps, with only the install step done at first", () => {
    const { container } = render(<GettingStarted hasReviews={false} usesOwnKey={false} installUrl={null} />);
    expect(screen.getByText("Reviewly is installed")).toBeInTheDocument();
    expect(screen.getByText("Open a pull request")).toBeInTheDocument();
    expect(screen.getByText(/Optional: use your own AI key/)).toBeInTheDocument();
    expect(container.querySelectorAll("li.done")).toHaveLength(1);
  });

  it("marks the optional key step done once a key is configured", () => {
    const { container } = render(<GettingStarted hasReviews={false} usesOwnKey installUrl={null} />);
    expect(container.querySelectorAll("li.done")).toHaveLength(2);
  });

  it("links to adding more repositories only when the install URL is known", () => {
    const { rerender } = render(<GettingStarted hasReviews={false} usesOwnKey={false} installUrl={null} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    rerender(<GettingStarted hasReviews={false} usesOwnKey={false} installUrl="https://github.com/apps/reviewly/installations/new" />);
    expect(screen.getByRole("link", { name: /add them on github/i })).toHaveAttribute("href", "https://github.com/apps/reviewly/installations/new");
  });
});

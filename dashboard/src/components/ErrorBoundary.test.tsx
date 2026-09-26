import { render, screen } from "@testing-library/react";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): never {
  throw new Error("render bug");
}

describe("ErrorBoundary", () => {
  it("shows a way out instead of a blank page when rendering crashes", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/something went wrong/i);
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    spy.mockRestore();
  });

  it("renders its children when nothing is wrong", () => {
    render(
      <ErrorBoundary>
        <p>fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("fine")).toBeInTheDocument();
  });
});

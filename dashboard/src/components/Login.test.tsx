import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Login } from "./Login";

const cfg = (over: Partial<{ github_login: boolean; dev_login: boolean }> = {}) => ({
  app_install_url: null, github_login: true, dev_login: false, ...over,
});

describe("Login", () => {
  it("offers GitHub sign-in when the server has it configured", () => {
    render(<Login config={cfg()} />);
    expect(screen.getByRole("link", { name: /sign in with github/i })).toHaveAttribute("href", "/auth/github/login");
    expect(screen.queryByText(/not set up/i)).not.toBeInTheDocument();
  });

  it("does not offer a button that cannot work when GitHub sign-in is not configured", () => {
    render(<Login config={cfg({ github_login: false })} />);
    expect(screen.queryByRole("link", { name: /sign in with github/i })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/GitHub sign-in is not set up on this server/i);
  });

  it("still offers GitHub sign-in while the config is unknown (it could not be loaded)", () => {
    render(<Login config={null} />);
    expect(screen.getByRole("link", { name: /sign in with github/i })).toBeInTheDocument();
  });

  it("shows no developer login on a normal server", () => {
    render(<Login config={cfg()} />);
    expect(screen.queryByRole("button", { name: /continue as developer/i })).not.toBeInTheDocument();
  });

  it("offers a developer login in local development, defaulting to the demo installation", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<Login config={cfg({ github_login: false, dev_login: true })} />);
    expect(screen.getByLabelText("Installation id")).toHaveValue("42");
    await userEvent.click(screen.getByRole("button", { name: /continue as developer/i }));
    expect(assign).toHaveBeenCalledWith("/auth/dev-login?installation=42");
    vi.unstubAllGlobals();
  });

  it("uses whatever installation id was typed, safely encoded", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<Login config={cfg({ dev_login: true })} />);
    const input = screen.getByLabelText("Installation id");
    await userEvent.clear(input);
    await userEvent.type(input, "4242");
    await userEvent.click(screen.getByRole("button", { name: /continue as developer/i }));
    expect(assign).toHaveBeenCalledWith("/auth/dev-login?installation=4242");
    vi.unstubAllGlobals();
  });
});

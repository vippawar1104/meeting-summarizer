import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { overview } from "./fixtures";

function mockApi(routes: Record<string, () => Response | Promise<Response>>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const handler = routes[key];
    return handler ? handler() : new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => vi.unstubAllGlobals());

const loggedIn = {
  "GET /api/me": () => json({ login: "octocat", installations: [42] }),
  "GET /api/installations/42/overview": () => json(overview),
};

describe("signed out", () => {
  it("shows the sign-in screen when the API says 401", async () => {
    mockApi({ "GET /api/me": () => new Response("", { status: 401 }) });
    render(<App />);
    const link = await screen.findByRole("link", { name: /sign in with github/i });
    expect(link).toHaveAttribute("href", "/auth/github/login");
    expect(screen.queryByText(/sign out/i)).not.toBeInTheDocument();
  });

  it("shows a retry when the server is unreachable, not the login screen", async () => {
    mockApi({ "GET /api/me": () => new Response("bad gateway", { status: 502, statusText: "Bad Gateway" }) });
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the server/i);
    expect(screen.queryByRole("link", { name: /sign in/i })).not.toBeInTheDocument();
  });
});

describe("signed in", () => {
  it("shows the summary numbers", async () => {
    mockApi(loggedIn);
    render(<App />);
    const summary = await screen.findByRole("region", { name: "Summary" });
    expect(within(summary).getByText("Reviews").nextSibling).toHaveTextContent("12");
    expect(within(summary).getByText("Precision").nextSibling).toHaveTextContent("74%");
    expect(within(summary).getByText("Tokens").nextSibling).toHaveTextContent("45K");
    expect(within(summary).getByText("Cost").nextSibling).toHaveTextContent("n/a");
  });

  it("shows precision per rule, and a dash instead of 0% when nothing is judged", async () => {
    mockApi(loggedIn);
    render(<App />);
    const rules = await screen.findByRole("region", { name: "Precision by rule" });
    expect(within(rules).getByText("bug")).toBeInTheDocument();
    expect(within(rules).getByText("83%")).toBeInTheDocument();
    expect(within(rules).getByText("—")).toBeInTheDocument();
    expect(within(rules).getByText(/7 not judged yet/)).toBeInTheDocument();
    expect(within(rules).getByRole("meter", { name: "bug precision" })).toHaveAttribute("aria-valuenow", "83");
  });

  it("shows the plan, its meter and the upgrade button", async () => {
    mockApi(loggedIn);
    render(<App />);
    const plan = await screen.findByRole("region", { name: "Plan" });
    expect(within(plan).getByText(/3 of 20 reviews used in Sep 2026/)).toBeInTheDocument();
    expect(within(plan).getByRole("meter")).toHaveAttribute("aria-valuenow", "15");
    expect(within(plan).getByRole("button", { name: "Upgrade" })).toBeEnabled();
  });

  it("does not offer an upgrade to a paying installation", async () => {
    mockApi({ ...loggedIn, "GET /api/installations/42/overview": () => json({ ...overview, plan: { name: "pro", limit: null, used: 30 } }) });
    render(<App />);
    expect(await screen.findByText("Pro plan")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upgrade" })).not.toBeInTheDocument();
  });

  it("marks statuses with a word, so color is never the only signal", async () => {
    mockApi(loggedIn);
    render(<App />);
    const recent = await screen.findByRole("region", { name: "Recent reviews" });
    expect(within(recent).getByText("Reviewed")).toBeInTheDocument();
    expect(within(recent).getByText("Failed")).toBeInTheDocument();
    expect(within(recent).getByText("Skipped")).toBeInTheDocument();
  });

  it("starts checkout and sends the browser to Stripe", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const fetchMock = mockApi({ ...loggedIn, "POST /api/installations/42/billing/checkout": () => json({ url: "https://checkout.stripe.com/c/x" }) });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Upgrade" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.com/c/x"));
    expect(fetchMock).toHaveBeenCalledWith("/api/installations/42/billing/checkout", expect.objectContaining({ method: "POST" }));
  });

  it("explains when billing is not configured instead of failing silently", async () => {
    mockApi({ ...loggedIn, "POST /api/installations/42/billing/checkout": () => new Response("", { status: 503 }) });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Upgrade" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Billing is not set up yet.");
  });

  it("offers a table view of the monthly chart", async () => {
    mockApi(loggedIn);
    render(<App />);
    const usage = await screen.findByRole("region", { name: "Monthly usage" });
    expect(within(usage).getByRole("img")).toHaveAccessibleName(/Sep 2026: 3/);
    await userEvent.click(within(usage).getByRole("button", { name: "Show as table" }));
    expect(within(usage).getByRole("table")).toBeInTheDocument();
    expect(within(usage).getByText("Aug 2026")).toBeInTheDocument();
  });

  it("shows a friendly empty state for a brand-new installation", async () => {
    mockApi({
      ...loggedIn,
      "GET /api/installations/42/overview": () =>
        json({ ...overview, totals: { ...overview.totals, reviews: 0, precision: null }, rules: [], repos: [], recent: [], usage: [] }),
    });
    render(<App />);
    expect(await screen.findByText(/No findings yet/)).toBeInTheDocument();
    expect(screen.getByText("No reviews yet.")).toBeInTheDocument();
    expect(screen.getByText("No feedback yet")).toBeInTheDocument();
  });

  it("returns to the sign-in screen if the session expires mid-use", async () => {
    mockApi({ ...loggedIn, "GET /api/installations/42/overview": () => new Response("", { status: 401 }) });
    render(<App />);
    expect(await screen.findByRole("link", { name: /sign in with github/i })).toBeInTheDocument();
  });

  it("offers a retry when the data fails to load", async () => {
    let calls = 0;
    mockApi({
      ...loggedIn,
      "GET /api/installations/42/overview": () => (++calls === 1 ? new Response("", { status: 500, statusText: "Server Error" }) : json(overview)),
    });
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load data/i);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Summary" })).toBeInTheDocument();
  });

  it("lets a user with several installations switch between them", async () => {
    const fetchMock = mockApi({
      "GET /api/me": () => json({ login: "octocat", installations: [42, 7] }),
      "GET /api/installations/42/overview": () => json(overview),
      "GET /api/installations/7/overview": () => json({ ...overview, installation_id: 7, totals: { ...overview.totals, reviews: 99 } }),
    });
    render(<App />);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Installation" }), "7");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/installations/7/overview", expect.anything()));
    const summary = await screen.findByRole("region", { name: "Summary" });
    await waitFor(() => expect(within(summary).getByText("Reviews").nextSibling).toHaveTextContent("99"));
  });

  it("says so when the user has no installations", async () => {
    mockApi({ "GET /api/me": () => json({ login: "octocat", installations: [] }) });
    render(<App />);
    expect(await screen.findByText("No installations yet")).toBeInTheDocument();
  });
});

describe("theme", () => {
  it("starts light, toggles to dark, and remembers the choice", async () => {
    mockApi(loggedIn);
    const { unmount } = render(<App />);
    await screen.findByRole("region", { name: "Summary" });
    expect(document.documentElement.dataset.theme).toBe("light");
    await userEvent.click(screen.getByRole("button", { name: /switch to dark mode/i }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("reviewly-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: /switch to light mode/i })).toHaveTextContent("Light mode");
    unmount();
    mockApi(loggedIn);
    render(<App />);
    await screen.findByRole("region", { name: "Summary" });
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("follows the operating system when nothing has been chosen", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {} }));
    mockApi(loggedIn);
    render(<App />);
    await screen.findByRole("region", { name: "Summary" });
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});

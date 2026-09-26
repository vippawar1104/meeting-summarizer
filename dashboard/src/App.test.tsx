import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { llmNone, overview } from "./fixtures";

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
  window.location.hash = "";
  delete document.documentElement.dataset.theme;
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => vi.unstubAllGlobals());

const loggedIn = {
  "GET /api/config": () => json({ app_install_url: null, github_login: true, dev_login: false }),
  "GET /api/installations/42/llm": () => json(llmNone),
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
    window.location.hash = "#/settings";
    mockApi(loggedIn);
    render(<App />);
    const plan = await screen.findByRole("region", { name: "Plan" });
    expect(within(plan).getByText(/3 of 20 reviews used in Sep 2026/)).toBeInTheDocument();
    expect(within(plan).getByRole("meter")).toHaveAttribute("aria-valuenow", "15");
    expect(within(plan).getByRole("button", { name: "Upgrade" })).toBeEnabled();
  });

  it("does not offer an upgrade to a paying installation", async () => {
    window.location.hash = "#/settings";
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
    window.location.hash = "#/settings";
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const fetchMock = mockApi({ ...loggedIn, "POST /api/installations/42/billing/checkout": () => json({ url: "https://checkout.stripe.com/c/x" }) });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Upgrade" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.com/c/x"));
    expect(fetchMock).toHaveBeenCalledWith("/api/installations/42/billing/checkout", expect.objectContaining({ method: "POST" }));
  });

  it("explains when billing is not configured instead of failing silently", async () => {
    window.location.hash = "#/settings";
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
      "GET /api/installations/42/llm": () => json(llmNone),
      "GET /api/installations/7/llm": () => json(llmNone),
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

describe("onboarding and AI model", () => {
  it("shows the getting-started checklist until the first review", async () => {
    mockApi({
      ...loggedIn,
      "GET /api/installations/42/overview": () => json({ ...overview, totals: { ...overview.totals, reviews: 0, precision: null } }),
    });
    render(<App />);
    expect(await screen.findByRole("region", { name: "Getting started" })).toBeInTheDocument();
  });

  it("hides it once reviews exist, and shows the AI model section under Settings", async () => {
    window.location.hash = "#/settings";
    mockApi(loggedIn);
    render(<App />);
    await screen.findByRole("region", { name: "Plan" });
    expect(screen.queryByRole("region", { name: "Getting started" })).not.toBeInTheDocument();
    const model = screen.getByRole("region", { name: "AI model" });
    expect(await within(model).findByText("Using Reviewly's built-in models")).toBeInTheDocument();
  });

  it("offers an Install on GitHub button to a user with no installations, when the app URL is configured", async () => {
    mockApi({
      "GET /api/config": () => json({ app_install_url: "https://github.com/apps/reviewly/installations/new", github_login: true, dev_login: false }),
      "GET /api/me": () => json({ login: "octocat", installations: [] }),
    });
    render(<App />);
    expect(await screen.findByRole("link", { name: /install on github/i })).toHaveAttribute("href", "https://github.com/apps/reviewly/installations/new");
  });

  it("still works when the config endpoint is unavailable", async () => {
    mockApi({ ...loggedIn, "GET /api/config": () => new Response("", { status: 500 }) });
    render(<App />);
    expect(await screen.findByRole("region", { name: "Summary" })).toBeInTheDocument();
  });
});

describe("navigation and shell", () => {
  it("opens on Overview and marks it as the current page", async () => {
    mockApi(loggedIn);
    render(<App />);
    await screen.findByRole("region", { name: "Summary" });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Settings" })).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("region", { name: "AI model" })).not.toBeInTheDocument();
    expect(document.title).toBe("Overview · Reviewly");
  });

  it("switches to Settings when its link is followed, and back", async () => {
    mockApi(loggedIn);
    render(<App />);
    await screen.findByRole("region", { name: "Summary" });
    window.location.hash = "#/settings";
    expect(await screen.findByRole("region", { name: "AI model" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Plan" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
    expect(document.title).toBe("Settings · Reviewly");
    window.location.hash = "#/overview";
    expect(await screen.findByRole("region", { name: "Summary" })).toBeInTheDocument();
  });

  it("treats an unknown hash as Overview", async () => {
    window.location.hash = "#/nonsense";
    mockApi(loggedIn);
    render(<App />);
    expect(await screen.findByRole("region", { name: "Summary" })).toBeInTheDocument();
  });

  it("shows no navigation to a signed-out visitor", async () => {
    mockApi({ "GET /api/me": () => new Response("", { status: 401 }) });
    render(<App />);
    await screen.findByRole("link", { name: /sign in with github/i });
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("has a skip link, a main landmark and shows who is signed in", async () => {
    mockApi(loggedIn);
    render(<App />);
    await screen.findByRole("region", { name: "Summary" });
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByText("octocat")).toBeInTheDocument();
  });

  it("shows a loading placeholder, not a blank page, while data loads", async () => {
    let release: (r: Response) => void = () => undefined;
    mockApi({ ...loggedIn, "GET /api/installations/42/overview": () => new Promise<Response>((r) => (release = r)) });
    render(<App />);
    expect(await screen.findByRole("status")).toHaveTextContent(/loading your dashboard/i);
    release(json(overview));
    expect(await screen.findByRole("region", { name: "Summary" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("refreshes on demand and says when it last updated", async () => {
    let calls = 0;
    mockApi({
      ...loggedIn,
      "GET /api/installations/42/overview": () => json({ ...overview, totals: { ...overview.totals, reviews: 12 + calls++ } }),
    });
    render(<App />);
    const summary = await screen.findByRole("region", { name: "Summary" });
    expect(within(summary).getByText("Reviews").nextSibling).toHaveTextContent("12");
    expect(screen.getByText(/Updated just now/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh data" }));
    await waitFor(() => expect(within(summary).getByText("Reviews").nextSibling).toHaveTextContent("13"));
  });

  it("links each pull request to GitHub, safely", async () => {
    mockApi(loggedIn);
    render(<App />);
    const recent = await screen.findByRole("region", { name: "Recent reviews" });
    const link = within(recent).getByRole("link", { name: "acme/widgets#12" });
    expect(link).toHaveAttribute("href", "https://github.com/acme/widgets/pull/12");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("marks the checklist's key step done when the installation already uses its own key", async () => {
    mockApi({
      ...loggedIn,
      "GET /api/installations/42/overview": () => json({ ...overview, totals: { ...overview.totals, reviews: 0, precision: null } }),
      "GET /api/installations/42/llm": () => json({ ...llmNone, configured: true, enabled: true, provider: "openai", model: "gpt-4.1" }),
    });
    const { container } = render(<App />);
    await screen.findByRole("region", { name: "Getting started" });
    await waitFor(() => expect(container.querySelectorAll("li.done")).toHaveLength(2));
  });
});

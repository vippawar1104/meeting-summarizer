import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { llmNone, llmOpenAI } from "../fixtures";
import { ModelSettings } from "./ModelSettings";

type Handler = (body: unknown) => Response | Promise<Response>;

function mockApi(routes: Record<string, Handler>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const handler = routes[key];
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    return handler ? handler(body) : new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
const LLM = "/api/installations/42/llm";
afterEach(() => vi.unstubAllGlobals());

describe("not configured yet", () => {
  it("says the built-in models are used and offers the form", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmNone) });
    render(<ModelSettings installation={42} />);
    expect(await screen.findByText("Using Reviewly's built-in models")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Provider" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
  });

  it("keeps Save disabled until provider, model and key are all filled in", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmNone) });
    render(<ModelSettings installation={42} />);
    const save = await screen.findByRole("button", { name: /save and test key/i });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Provider" }), "openai");
    await userEvent.type(screen.getByRole("combobox", { name: "Model" }), "gpt-4.1");
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText("Paste your API key"), "sk-abcdefgh1234");
    expect(save).toBeEnabled();
  });

  it("masks the key field and does not autofill it", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmNone) });
    render(<ModelSettings installation={42} />);
    const key = await screen.findByPlaceholderText("Paste your API key");
    expect(key).toHaveAttribute("type", "password");
    expect(key).toHaveAttribute("autocomplete", "off");
  });

  it("suggests models for the chosen provider and links to where a key is created", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmNone) });
    render(<ModelSettings installation={42} />);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Provider" }), "anthropic");
    expect(screen.getByPlaceholderText("claude-sonnet-5")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /get a key/i })).toHaveAttribute("href", "https://console.anthropic.com/settings/keys");
  });

  it("shows a base URL field only for a custom endpoint, and requires it", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmNone) });
    render(<ModelSettings installation={42} />);
    const provider = await screen.findByRole("combobox", { name: "Provider" });
    await userEvent.selectOptions(provider, "openai");
    expect(screen.queryByPlaceholderText(/openrouter/)).not.toBeInTheDocument();
    await userEvent.selectOptions(provider, "custom");
    expect(screen.getByPlaceholderText("https://openrouter.ai/api/v1")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("combobox", { name: "Model" }), "meta/llama");
    await userEvent.type(screen.getByPlaceholderText("Paste your API key"), "sk-abcdefgh1234");
    expect(screen.getByRole("button", { name: /save and test key/i })).toBeDisabled(); // no base URL yet
  });
});

describe("saving", () => {
  it("sends the provider, model and key, then clears the key from the page", async () => {
    const fetchMock = mockApi({ [`GET ${LLM}`]: () => json(llmNone), [`PUT ${LLM}`]: () => json(llmOpenAI) });
    render(<ModelSettings installation={42} />);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Provider" }), "openai");
    await userEvent.type(screen.getByRole("combobox", { name: "Model" }), "gpt-4.1");
    await userEvent.type(screen.getByPlaceholderText("Paste your API key"), "sk-abcdefgh1234");
    await userEvent.click(screen.getByRole("button", { name: /save and test key/i }));

    expect(await screen.findByText(/reviewed with gpt-4.1/i)).toBeInTheDocument();
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(JSON.parse(String(put[1]!.body))).toEqual({ provider: "openai", model: "gpt-4.1", api_key: "sk-abcdefgh1234" });
    // after saving, only the last four characters are shown, and the field is empty again
    expect(screen.getByText(/key \.\.\.3456/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Saved \(\.\.\.3456\)/)).toHaveValue("");
    expect(document.body.textContent).not.toContain("sk-abcdefgh1234");
  });

  it("shows the server's plain-language reason when the key does not work", async () => {
    mockApi({
      [`GET ${LLM}`]: () => json(llmNone),
      [`PUT ${LLM}`]: () => json({ detail: "The provider rejected this API key (or it lacks access to that model)." }, 400),
    });
    render(<ModelSettings installation={42} />);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Provider" }), "openai");
    await userEvent.type(screen.getByRole("combobox", { name: "Model" }), "gpt-4.1");
    await userEvent.type(screen.getByPlaceholderText("Paste your API key"), "sk-wrong-key-1234");
    await userEvent.click(screen.getByRole("button", { name: /save and test key/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rejected this API key");
    expect(screen.getByText("Using Reviewly's built-in models")).toBeInTheDocument(); // nothing was saved
  });

  it("disables the buttons while it is working so it cannot be double-submitted", async () => {
    let release: (r: Response) => void = () => {};
    mockApi({ [`GET ${LLM}`]: () => json(llmNone), [`PUT ${LLM}`]: () => new Promise<Response>((r) => (release = r)) });
    render(<ModelSettings installation={42} />);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Provider" }), "openai");
    await userEvent.type(screen.getByRole("combobox", { name: "Model" }), "gpt-4.1");
    await userEvent.type(screen.getByPlaceholderText("Paste your API key"), "sk-abcdefgh1234");
    await userEvent.click(screen.getByRole("button", { name: /save and test key/i }));
    expect(screen.getByRole("button", { name: /testing and saving/i })).toBeDisabled();
    release(json(llmOpenAI));
    await screen.findByText(/reviewed with/i);
  });
});

describe("already configured", () => {
  it("shows the provider, model and only the last four characters of the key", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmOpenAI) });
    render(<ModelSettings installation={42} />);
    expect(await screen.findByText(/OpenAI · gpt-4.1/)).toBeInTheDocument();
    expect(screen.getByText(/key \.\.\.3456/)).toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Provider" })).toHaveValue("openai");
  });

  it("lets you change only the model and keeps the saved key by sending none", async () => {
    const fetchMock = mockApi({ [`GET ${LLM}`]: () => json(llmOpenAI), [`PUT ${LLM}`]: () => json({ ...llmOpenAI, model: "gpt-4o-mini" }) });
    render(<ModelSettings installation={42} />);
    const model = await screen.findByRole("combobox", { name: "Model" });
    await userEvent.clear(model);
    await userEvent.type(model, "gpt-4o-mini");
    await userEvent.click(screen.getByRole("button", { name: /save and test key/i }));
    await screen.findByText(/reviewed with gpt-4o-mini/i);
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(JSON.parse(String(put[1]!.body))).toEqual({ provider: "openai", model: "gpt-4o-mini" }); // no api_key
  });

  it("warns clearly when the saved key stopped working", async () => {
    mockApi({ [`GET ${LLM}`]: () => json({ ...llmOpenAI, last_test_ok: false, last_test_error: "Your provider account is rate limited or out of quota." }) });
    render(<ModelSettings installation={42} />);
    expect(await screen.findByText(/Not working: Your provider account is rate limited/)).toBeInTheDocument();
  });

  it("re-tests on request and reports the result", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmOpenAI), [`POST ${LLM}/test`]: () => json({ ok: true, error: null, settings: llmOpenAI }) });
    render(<ModelSettings installation={42} />);
    await userEvent.click(await screen.findByRole("button", { name: /test again/i }));
    expect(await screen.findByText("The key works.")).toBeInTheDocument();
  });

  it("removes the key and goes back to the built-in models", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmOpenAI), [`DELETE ${LLM}`]: () => json(llmNone) });
    render(<ModelSettings installation={42} />);
    await userEvent.click(await screen.findByRole("button", { name: /remove/i }));
    expect(await screen.findByText("Using Reviewly's built-in models")).toBeInTheDocument();
    expect(screen.getByText(/Removed\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
  });
});

describe("loading and failure", () => {
  it("offers a retry when the settings cannot be loaded", async () => {
    let calls = 0;
    mockApi({ [`GET ${LLM}`]: () => (++calls === 1 ? new Response("{}", { status: 500 }) : json(llmNone)) });
    render(<ModelSettings installation={42} />);
    expect(await screen.findByText(/could not load model settings/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Using Reviewly's built-in models")).toBeInTheDocument();
  });

  it("tells the page whether an own key is in use, for the getting-started checklist", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmOpenAI) });
    const onChange = vi.fn();
    render(<ModelSettings installation={42} onChange={onChange} />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(true));
  });

  it("explains that the key is private and that the free plan does not apply to it", async () => {
    mockApi({ [`GET ${LLM}`]: () => json(llmNone) });
    render(<ModelSettings installation={42} />);
    expect(await screen.findByText(/encrypted, never shown again/i)).toBeInTheDocument();
    expect(screen.getByText(/don't count against the free plan/i)).toBeInTheDocument();
  });
});

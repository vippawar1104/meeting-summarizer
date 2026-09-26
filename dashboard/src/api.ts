import type { LLMSettings, Me, Overview, PublicConfig } from "./types";

export class Unauthorized extends Error {}
/** An error whose message came from the server and is meant to be shown to the user. */
export class ApiError extends Error {}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) {
    let detail = "";
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail || `${res.status} ${res.statusText}`.trim());
  }
  return (await res.json()) as T;
}

export const fetchMe = () => request<Me>("GET", "/api/me");
export const fetchConfig = () => request<PublicConfig>("GET", "/api/config");
export const fetchOverview = (installation: number) => request<Overview>("GET", `/api/installations/${installation}/overview`);

export const fetchLLM = (installation: number) => request<LLMSettings>("GET", `/api/installations/${installation}/llm`);
export const saveLLM = (
  installation: number,
  body: { provider: string; model: string; api_key?: string; base_url?: string },
) => request<LLMSettings>("PUT", `/api/installations/${installation}/llm`, body);
export const testLLM = (installation: number) =>
  request<{ ok: boolean; error: string | null; settings?: LLMSettings }>("POST", `/api/installations/${installation}/llm/test`);
export const deleteLLM = (installation: number) => request<LLMSettings>("DELETE", `/api/installations/${installation}/llm`);

export async function startCheckout(installation: number): Promise<string> {
  try {
    const body = await request<{ url: string }>("POST", `/api/installations/${installation}/billing/checkout`);
    return body.url;
  } catch (e) {
    if (e instanceof ApiError && e.message.startsWith("503")) throw new ApiError("Billing is not set up yet.");
    throw new ApiError("Could not start checkout.");
  }
}

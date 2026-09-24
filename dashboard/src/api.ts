import type { Me, Overview } from "./types";

export class Unauthorized extends Error {}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const fetchMe = () => get<Me>("/api/me");
export const fetchOverview = (installation: number) =>
  get<Overview>(`/api/installations/${installation}/overview`);

export async function startCheckout(installation: number): Promise<string> {
  const res = await fetch(`/api/installations/${installation}/billing/checkout`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error(res.status === 503 ? "Billing is not set up yet." : "Could not start checkout.");
  const body = (await res.json()) as { url: string };
  return body.url;
}

// REST helpers. Relative URLs: the backend serves the UI from the same origin.

import {
  parseHealth,
  parseProfiles,
  parseSettings,
  parseUsageResponse,
  type Budget,
  type Health,
  type Profile,
  type ServerSettings,
} from "./protocol.ts";

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, ...init });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response;
}

export async function fetchProfiles(signal?: AbortSignal): Promise<Profile[]> {
  const response = await request("/api/profiles", { signal });
  return parseProfiles(await response.json());
}

export async function deleteProfile(id: string): Promise<void> {
  await request(`/api/profiles/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function deleteAllProfiles(): Promise<void> {
  await request("/api/profiles", { method: "DELETE" });
}

export async function fetchUsage(signal?: AbortSignal): Promise<Budget | null> {
  const response = await request("/api/usage", { signal });
  return parseUsageResponse(await response.json());
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health | null> {
  const response = await request("/api/health", { signal });
  return parseHealth(await response.json());
}

export async function fetchSettings(signal?: AbortSignal): Promise<ServerSettings | null> {
  const response = await request("/api/settings", { signal });
  return parseSettings(await response.json());
}

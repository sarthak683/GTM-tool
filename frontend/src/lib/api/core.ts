import type { Paginated } from "../../types";

async function requestList<T>(path: string): Promise<T[]> {
  const res = await request<Paginated<T> | T[]>(path);
  if (Array.isArray(res)) return res;
  return res.items ?? [];
}

async function requestPaginated<T>(path: string): Promise<Paginated<T>> {
  const res = await request<Paginated<T> | T[]>(path);
  if (Array.isArray(res)) {
    return {
      items: res,
      total: res.length,
      page: 1,
      size: res.length,
      pages: 1,
    };
  }
  return res;
}

const ISO_DATETIME_NO_TZ = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;

function normalizeUtcDateStrings<T>(value: T): T {
  if (value == null) return value;
  if (Array.isArray(value)) {
    return value.map((item) => normalizeUtcDateStrings(item)) as T;
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const normalized: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(obj)) {
      normalized[key] = normalizeUtcDateStrings(item);
    }
    return normalized as T;
  }
  if (typeof value === "string" && ISO_DATETIME_NO_TZ.test(value)) {
    return `${value}Z` as T;
  }
  return value;
}

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem("beacon_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const BASE = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...getAuthHeaders(), ...options?.headers },
    ...options,
  });
  if (res.status === 401) {
    localStorage.removeItem("beacon_token");
    window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Request failed");
  }
  if (res.status === 204) return undefined as T;
  const payload = await res.json();
  return normalizeUtcDateStrings(payload) as T;
}

export { BASE, getAuthHeaders, normalizeUtcDateStrings, request, requestList, requestPaginated };

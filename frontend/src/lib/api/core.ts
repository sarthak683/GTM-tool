import type { Paginated } from "../../types";

async function requestList<T>(path: string): Promise<T[]> {
  const res = await request<Paginated<T> | T[]>(path);
  if (Array.isArray(res)) return res;
  return res.items ?? [];
}

/**
 * Fetch every page of a paginated endpoint.
 *
 * Several call sites used to request one fixed page — `list(0, 500)` — and
 * treat it as the whole dataset. Production outgrew all of those ceilings
 * (704 deals, 1,353 companies), so records simply went missing from pickers
 * and filters with nothing on screen saying the list was partial.
 *
 * `buildPath` receives the offset and returns the URL for that page. Pages are
 * fetched in sequence and stop as soon as the server says there is nothing
 * left, so a small dataset still costs exactly one request. `maxPages` is a
 * seatbelt against an endpoint that never reports completion — not a data
 * limit; size `pageSize` so real volumes finish well inside it.
 */
async function requestAllPages<T>(
  buildPath: (skip: number, limit: number) => string,
  { pageSize = 500, maxPages = 40 }: { pageSize?: number; maxPages?: number } = {},
): Promise<T[]> {
  const all: T[] = [];
  for (let page = 0; page < maxPages; page += 1) {
    const res = await requestPaginated<T>(buildPath(all.length, pageSize));
    const items = res.items ?? [];
    all.push(...items);
    // Short page, empty page, or we already hold everything the server counted.
    if (items.length < pageSize) break;
    if (typeof res.total === "number" && all.length >= res.total) break;
  }
  return all;
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

/** FastAPI returns `detail` as a string, or as an array of validation objects
 *  on 422. Stringifying the array yields "[object Object]" in the UI, so pull
 *  out the messages instead. */
function readErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => (typeof d === "string" ? d : (d as { msg?: string })?.msg))
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  // A multipart body must keep the browser-generated Content-Type, which
  // carries the boundary. Forcing application/json here made the server see no
  // file at all ("Field required"), which is why every upload call site until
  // now hand-rolled its own fetch instead of using this helper.
  const isMultipart = options?.body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    // Must come AFTER the ...options spread: options carries its own `headers`
    // at some call sites, and spreading options last replaced this merged
    // object wholesale — dropping the Authorization/Content-Type entries.
    headers: {
      ...(isMultipart ? {} : { "Content-Type": "application/json" }),
      ...getAuthHeaders(),
      ...options?.headers,
    },
  });
  if (res.status === 401) {
    localStorage.removeItem("beacon_token");
    window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(readErrorDetail(err?.detail, res.statusText || "Request failed"));
  }
  if (res.status === 204) return undefined as T;
  const payload = await res.json();
  return normalizeUtcDateStrings(payload) as T;
}

export { BASE, getAuthHeaders, normalizeUtcDateStrings, request, requestAllPages, requestList, requestPaginated };

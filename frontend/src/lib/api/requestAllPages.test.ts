import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { requestAllPages } from "./core";

/**
 * Cover for the truncation bug: several call sites fetched one fixed page
 * (`list(0, 500)`) and treated it as the whole dataset. Production outgrew
 * every one of those ceilings, so records vanished from pickers and filters
 * with nothing on screen saying the list was partial.
 */

const PATH = (skip: number, limit: number) => `/api/v1/things/?skip=${skip}&limit=${limit}`;

/** Serve `total` rows out of a fake paginated endpoint. */
function serve(total: number) {
  return vi.fn(async (url: string) => {
    const params = new URL(url, "http://localhost").searchParams;
    const skip = Number(params.get("skip"));
    const limit = Number(params.get("limit"));
    const items = Array.from({ length: Math.max(0, Math.min(limit, total - skip)) }, (_, i) => ({
      id: skip + i,
    }));
    return new Response(JSON.stringify({ items, total, page: 1, size: limit }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

beforeEach(() => {
  localStorage.setItem("beacon_token", "t");
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("requestAllPages", () => {
  it("returns every row across pages, not just the first", async () => {
    const fetchMock = serve(1_353); // production company count
    vi.stubGlobal("fetch", fetchMock);

    const rows = await requestAllPages<{ id: number }>(PATH, { pageSize: 500 });

    expect(rows).toHaveLength(1_353);
    expect(rows[0].id).toBe(0);
    expect(rows[1_352].id).toBe(1_352);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("costs a single request when everything fits on one page", async () => {
    const fetchMock = serve(12);
    vi.stubGlobal("fetch", fetchMock);

    const rows = await requestAllPages<{ id: number }>(PATH, { pageSize: 500 });

    expect(rows).toHaveLength(12);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("stops on an exact page boundary instead of looping on an empty page", async () => {
    const fetchMock = serve(1_000);
    vi.stubGlobal("fetch", fetchMock);

    const rows = await requestAllPages<{ id: number }>(PATH, { pageSize: 500 });

    // Two full pages reach `total`, so the third request is never made.
    expect(rows).toHaveLength(1_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("handles an empty collection", async () => {
    vi.stubGlobal("fetch", serve(0));
    await expect(requestAllPages<{ id: number }>(PATH)).resolves.toEqual([]);
  });

  it("stops at maxPages if the server never reports completion", async () => {
    // A misbehaving endpoint that always returns a full page and no total.
    const fetchMock = vi.fn(async (url: string) => {
      const limit = Number(new URL(url, "http://localhost").searchParams.get("limit"));
      const items = Array.from({ length: limit }, (_, i) => ({ id: i }));
      return new Response(JSON.stringify({ items }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await requestAllPages<{ id: number }>(PATH, { pageSize: 10, maxPages: 4 });

    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});

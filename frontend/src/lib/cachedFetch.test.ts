import { describe, expect, it, vi } from "vitest";

import { createCachedFetch } from "./cachedFetch";


function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("createCachedFetch", () => {
  it("does not let a request started before invalidate repopulate the cache", async () => {
    const first = deferred<string>();
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce("fresh");
    const cache = createCachedFetch(fetcher, 60_000);

    const staleRequest = cache.get();
    cache.invalidate();
    first.resolve("stale");

    await expect(staleRequest).resolves.toBe("stale");
    await expect(cache.get()).resolves.toBe("fresh");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("deduplicates concurrent reads and retries after a failure", async () => {
    const first = deferred<string>();
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce("recovered");
    const cache = createCachedFetch(fetcher);

    const readA = cache.get();
    const readB = cache.get();
    expect(readA).toBe(readB);
    first.reject(new Error("network"));

    await expect(readA).rejects.toThrow("network");
    await expect(cache.get()).resolves.toBe("recovered");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

import { describe, expect, it, vi } from "vitest";
import { installChunkRecovery } from "./chunkRecovery";

describe("chunk recovery", () => {
  it("reloads once and keeps the guard across load and a second document", () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
    const reload = vi.fn();
    const first = new EventTarget();
    installChunkRecovery(first, storage, reload, () => 100_000);
    const failure = new Event("vite:preloadError", { cancelable: true });
    first.dispatchEvent(failure);
    expect(failure.defaultPrevented).toBe(true);
    first.dispatchEvent(new Event("load"));
    first.dispatchEvent(new Event("vite:preloadError"));
    const second = new EventTarget();
    installChunkRecovery(second, storage, reload, () => 100_001);
    second.dispatchEvent(new Event("load"));
    const persistentFailure = new Event("vite:preloadError", { cancelable: true });
    second.dispatchEvent(persistentFailure);
    expect(persistentFailure.defaultPrevented).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
    const later = new EventTarget();
    installChunkRecovery(later, storage, reload, () => 200_000);
    later.dispatchEvent(new Event("vite:preloadError"));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("does not crash or reload when storage is unavailable", () => {
    const target = new EventTarget();
    const reload = vi.fn();
    installChunkRecovery(target, { getItem: () => { throw new Error("Storage blocked"); }, setItem: vi.fn() }, reload);
    expect(() => target.dispatchEvent(new Event("vite:preloadError"))).not.toThrow();
    expect(reload).not.toHaveBeenCalled();
  });
});

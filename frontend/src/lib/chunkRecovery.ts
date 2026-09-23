const KEY = "beacon:stale-chunk-reload";
const RETRY_WINDOW_MS = 60_000;

// Keep the guard across page loads: window.load can fire before a lazy route
// fails, so clearing it there creates a reload loop for persistently missing assets.
export function installChunkRecovery(
  target: EventTarget,
  storage: Pick<Storage, "getItem" | "setItem">,
  reload: () => void,
  now: () => number = Date.now,
): () => void {
  let attempted = false;
  const recover = (event: Event) => {
    if (attempted) return;
    try {
      const previous = Number(storage.getItem(KEY));
      if (previous && now() - previous < RETRY_WINDOW_MS) return;
      storage.setItem(KEY, String(now()));
    } catch {
      // Without a persistent guard, leave the recoverable error UI in place.
      return;
    }
    attempted = true;
    event.preventDefault();
    reload();
  };
  target.addEventListener("vite:preloadError", recover);
  return () => target.removeEventListener("vite:preloadError", recover);
}

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// Every deploy ships lazy-loaded chunks (DealDetailDrawer, Pipeline, etc.)
// under a NEW content hash, and the old hashed files stop existing on the
// server — nothing keeps them around. A tab left open across a deploy still
// has the OLD hash in memory, so the first time it dynamic-imports a chunk
// it hasn't fetched yet, that request 404s and Vite fires this event
// instead of letting the raw network error surface. Previously nothing
// listened for it, so ErrorBoundary caught the resulting exception and
// showed "Something went wrong" — technically correct, but a full page
// reload (which re-fetches index.html pointing at the CURRENT hashes) is
// enough to fix it, so do that automatically instead of surfacing an error
// for something that isn't actually broken.
//
// Guarded by sessionStorage so a genuinely broken deploy (one where the
// reload doesn't fix it) reloads once, not in an infinite loop — the guard
// is cleared once a page load actually completes.
window.addEventListener("vite:preloadError", () => {
  const key = "beacon:stale-chunk-reload";
  if (sessionStorage.getItem(key)) return;
  sessionStorage.setItem(key, "1");
  window.location.reload();
});
window.addEventListener("load", () => {
  sessionStorage.removeItem("beacon:stale-chunk-reload");
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Register the Web Push service worker. Browsers without SW support
// silently skip; the rest of the app works without push.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .catch((err) => console.warn("Service worker registration failed:", err));
  });
}

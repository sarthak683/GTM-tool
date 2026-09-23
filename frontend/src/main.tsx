import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { installChunkRecovery } from "./lib/chunkRecovery";

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
// Use wrappers so browsers that deny storage access do not fail at startup.
installChunkRecovery(window, {
  getItem: (key) => window.sessionStorage.getItem(key),
  setItem: (key, value) => window.sessionStorage.setItem(key, value),
}, () => window.location.reload());

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

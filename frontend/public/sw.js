/* Beacon CRM service worker.
 *
 * Purpose
 * -------
 * 1. Receive Web Push notifications even when the PWA tab is closed.
 * 2. When the user taps the notification, deep-link into the OS dialer
 *    with the prospect's phone number pre-filled.
 *
 * This worker deliberately does NOT cache assets — Vite's own dev server
 * + the existing static hosting handle that. We're using a service worker
 * solely because Web Push *requires* one (the browser delivers push
 * messages by waking the SW, not the page).
 */

// Skip the "wait for old SW to finish" handshake on first install — the
// next push event will be handled by this worker without a page reload.
self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  // Defensive parse: if the payload is malformed we still show a generic
  // notification so the user knows *something* happened, rather than the
  // push being silently dropped.
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    payload = { title: "Beacon", body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "Beacon CRM";
  const body = payload.body || "";
  const tel = payload.tel || "";

  const options = {
    body,
    icon: "/beacon-logo.jpg",
    badge: "/beacon-logo.jpg",
    tag: payload.contact_id ? `ring-${payload.contact_id}` : "beacon-call",
    // `renotify: true` so a second click on Call within ~seconds re-buzzes
    // the device instead of being collapsed silently by the OS.
    renotify: true,
    requireInteraction: true,
    data: {
      tel,
      contact_id: payload.contact_id || null,
      contact_name: payload.contact_name || "",
      type: payload.type || "ring-mobile",
    },
    actions: tel
      ? [{ action: "dial", title: "Call now" }]
      : [],
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const tel = data.tel || "";
  const contactName = data.contact_name || "";

  // Why we don't openWindow("tel:...") directly:
  // - iOS Safari (even in installed PWA mode) refuses non-https schemes
  //   from a service worker's openWindow — call silently no-ops.
  // - Android Chrome on a notable subset of OEM builds opens an empty
  //   tab next to the dialer or eats the call.
  // The reliable pattern is to open a same-origin HTTPS bridge page,
  // /dial, which then sets `window.location.href = "tel:..."` from a
  // document context — the OS honors that because the notification tap
  // is the user gesture authorizing it.
  let targetUrl = "/contacts";
  if (tel) {
    const params = new URLSearchParams({ tel });
    if (contactName) params.set("name", contactName);
    targetUrl = `/dial?${params.toString()}`;
  }

  event.waitUntil(
    (async () => {
      // Prefer focusing an already-open CRM window and navigating it to
      // /dial — that's faster than spawning a fresh tab and reusing an
      // authenticated window keeps subsequent navigation in-session.
      const allClients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const sameOriginClient = allClients.find((c) => {
        try { return new URL(c.url).origin === self.location.origin; } catch { return false; }
      });
      if (sameOriginClient && tel && "navigate" in sameOriginClient) {
        try {
          await sameOriginClient.navigate(targetUrl);
          return sameOriginClient.focus();
        } catch {
          // Some browsers throw on cross-document navigate(); fall through
          // to openWindow which always works for same-origin HTTPS.
        }
      }
      if (sameOriginClient && !tel) {
        return sameOriginClient.focus();
      }
      return self.clients.openWindow(targetUrl);
    })()
  );
});

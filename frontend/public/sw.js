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
    data: { tel, contact_id: payload.contact_id || null, type: payload.type || "ring-mobile" },
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

  // Two paths: the user tapped the "Call now" action OR tapped the body.
  // Either way we want the dialer to open if we have a number; otherwise
  // we just focus / open the CRM so they can see the prospect details.
  const targetUrl = tel ? `tel:${tel}` : "/contacts";

  event.waitUntil(
    (async () => {
      // openWindow on iOS Safari needs to run inside the notificationclick
      // handler's microtask chain — that's why we don't await any network
      // call before calling it.
      const allClients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      // If there's already a CRM window open and we don't need the dialer,
      // focus it instead of spawning a duplicate tab.
      if (!tel && allClients.length) {
        return allClients[0].focus();
      }
      return self.clients.openWindow(targetUrl);
    })()
  );
});

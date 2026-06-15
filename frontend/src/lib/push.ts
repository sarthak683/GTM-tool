/**
 * Browser-side Web Push helpers.
 *
 * The flow this implements:
 *   1. Page → check if the SW is registered and pushManager is available.
 *   2. Page → fetch the server's VAPID public key (so the browser can
 *      register against it).
 *   3. Page → ask the user for Notification permission.
 *   4. Page → pushManager.subscribe(VAPID_PUBLIC_KEY) — browser returns
 *      a PushSubscription object.
 *   5. Page → POST that subscription to /push/subscribe so the server can
 *      ring it later.
 *
 * `getSubscriptionState` is the read side — used by the Settings toggle to
 * render the current opt-in state without calling subscribe() again.
 */
import { pushApi } from "./api";

export type PushSubscriptionState = {
  supported: boolean;        // navigator + SW + pushManager all exist
  configured: boolean;       // server has VAPID keys
  permission: NotificationPermission | "unsupported";
  subscribed: boolean;       // a PushSubscription already exists for this browser
  endpoint: string | null;
};

// VAPID public keys come from the server as base64url; the Web Push API
// wants them as a Uint8Array. Standard conversion.
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; ++i) arr[i] = raw.charCodeAt(i);
  return arr;
}

function isSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export async function getSubscriptionState(): Promise<PushSubscriptionState> {
  if (!isSupported()) {
    return { supported: false, configured: false, permission: "unsupported", subscribed: false, endpoint: null };
  }
  const reg = await navigator.serviceWorker.ready;
  const existing = await reg.pushManager.getSubscription();
  let configured = false;
  try {
    const k = await pushApi.getVapidPublicKey();
    configured = !!k.configured && !!k.publicKey;
  } catch {
    // If the endpoint errors (likely auth) treat as not configured for now.
    configured = false;
  }
  return {
    supported: true,
    configured,
    permission: Notification.permission,
    subscribed: !!existing,
    endpoint: existing?.endpoint ?? null,
  };
}

export async function enablePush(): Promise<{ ok: boolean; reason?: string; endpoint?: string }> {
  if (!isSupported()) return { ok: false, reason: "Push not supported in this browser." };

  const reg = await navigator.serviceWorker.ready;

  // Permission must be requested from a user gesture, so callers should
  // invoke this from a click handler — the browser will throw otherwise
  // on Safari.
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return { ok: false, reason: `Notification permission ${permission}.` };

  const keyResp = await pushApi.getVapidPublicKey();
  if (!keyResp.configured || !keyResp.publicKey) {
    return { ok: false, reason: "Server has no VAPID keys configured." };
  }

  // Reuse an existing subscription if the browser already has one — this
  // covers the "user toggled off then on" path without spawning a duplicate.
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    // TS's lib.dom narrows applicationServerKey to BufferSource which has
    // become picky about SharedArrayBuffer-typed views. Cast to the wider
    // BufferSource the spec accepts — the runtime value is a real
    // Uint8Array<ArrayBuffer>, only TS doesn't believe us.
    const appServerKey = urlBase64ToUint8Array(keyResp.publicKey) as unknown as BufferSource;
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: appServerKey,
    });
  }

  const json = sub.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    return { ok: false, reason: "Browser returned an incomplete subscription." };
  }

  await pushApi.subscribe({
    endpoint: json.endpoint,
    keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
    user_agent: navigator.userAgent.slice(0, 200),
  });

  return { ok: true, endpoint: json.endpoint };
}

export async function disablePush(): Promise<{ ok: boolean; reason?: string }> {
  if (!isSupported()) return { ok: false, reason: "Push not supported." };
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return { ok: true };
  // Best-effort server delete first, then unsubscribe the browser. If the
  // server call fails the browser unsubscribe still runs — keeps state from
  // diverging into "browser has sub, server doesn't" (which would silently
  // drop pushes).
  try {
    await pushApi.unsubscribe(sub.endpoint);
  } catch {
    /* ignore */
  }
  await sub.unsubscribe();
  return { ok: true };
}

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, CheckCircle2, X, Loader2, Sparkles } from "lucide-react";
import { notificationsApi } from "../../lib/api";
import type { AppNotification } from "../../types";

/**
 * In-app notification bell. Distinct from the Tasks feed: Tasks are
 * durable work the rep owes (backlog, due dates). Notifications are
 * signals the system noticed for the rep that decay once acknowledged.
 *
 * Producer side: app/services/notifications.py via create_notification(...)
 * Consumer side: this component.
 *
 * Polls every 45s and on tab-focus. Sub-second freshness via WebSockets
 * is not worth the infra for sales-rep cadence — 45s feels real-time.
 */

const POLL_MS = 45_000;

export function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null); // id under in-flight accept/dismiss
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  // Fetch the unread count on a background timer. Cheap COUNT(*) query
  // on the backend — gives the badge a current value without pulling
  // the full list every tick.
  const refreshCount = useCallback(async () => {
    try {
      const { unread: u } = await notificationsApi.unreadCount();
      setUnread(u);
    } catch {
      // Swallow — the badge will just be stale until the next poll. Don't
      // show an error on a background poll.
    }
  }, []);

  // Pull the full list (used when the popover opens or after an action).
  const refreshList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await notificationsApi.list({ limit: 30 });
      setItems(rows);
      const u = rows.filter((r) => !r.read_at && !r.dismissed_at).length;
      setUnread(u);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load notifications.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial count + poll.
  useEffect(() => {
    void refreshCount();
    const id = window.setInterval(refreshCount, POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshCount]);

  // Re-poll the moment the tab regains focus — common case: rep was on a
  // call, returned to the CRM, expects to see fresh signals.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshCount();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refreshCount]);

  // Click-outside to close.
  useEffect(() => {
    if (!open) return;
    const onClick = (ev: MouseEvent) => {
      const t = ev.target as Node;
      if (popoverRef.current?.contains(t)) return;
      if (buttonRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    if (next) void refreshList();
  };

  // Mark a notification read on first view. We do this lazily — only
  // when the popover is open and the row is rendered — rather than
  // bulk-reading everything on open, so the rep can still see the
  // unread state for a beat before it disappears.
  const handleMarkRead = async (n: AppNotification) => {
    if (n.read_at) return;
    setBusyId(n.id);
    try {
      const updated = await notificationsApi.markRead(n.id);
      setItems((prev) => prev.map((row) => (row.id === n.id ? updated : row)));
      setUnread((u) => Math.max(0, u - 1));
    } catch {
      // Non-fatal — read state will sync on next refresh.
    } finally {
      setBusyId(null);
    }
  };

  const handleDismiss = async (n: AppNotification) => {
    setBusyId(n.id);
    try {
      const updated = await notificationsApi.dismiss(n.id);
      setItems((prev) => prev.map((row) => (row.id === n.id ? updated : row)));
      setUnread((u) => (n.read_at ? u : Math.max(0, u - 1)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to dismiss.");
    } finally {
      setBusyId(null);
    }
  };

  const handleAccept = async (n: AppNotification) => {
    setBusyId(n.id);
    try {
      const res = await notificationsApi.accept(n.id);
      setItems((prev) => prev.map((row) => (row.id === n.id ? res.notification : row)));
      setUnread((u) => (n.read_at ? u : Math.max(0, u - 1)));
      // For meeting-booked, jump straight to the new deal so the rep
      // can fill in value / next steps while the context is hot.
      if (n.type === "meeting_booked_suggest_deal" && res.deal_id) {
        setOpen(false);
        navigate(`/pipeline?deal=${res.deal_id}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to accept.");
    } finally {
      setBusyId(null);
    }
  };

  const badgeLabel = unread > 99 ? "99+" : String(unread);
  const visibleItems = items.filter((n) => !n.dismissed_at);

  return (
    <div style={{ position: "relative" }}>
      <button
        ref={buttonRef}
        type="button"
        onClick={handleToggle}
        aria-label={unread > 0 ? `${unread} new notifications` : "Notifications"}
        title={unread > 0 ? `${unread} new notification${unread === 1 ? "" : "s"}` : "Notifications"}
        style={{
          position: "relative",
          width: 38, height: 38, borderRadius: 12,
          border: "1px solid #dbe4ef",
          background: open ? "#eef4ff" : "rgba(255,255,255,0.94)",
          color: open ? "#1d4ed8" : "#1d2f43",
          cursor: "pointer",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          boxShadow: "0 10px 24px rgba(15,23,42,0.05)",
        }}
      >
        <Bell size={16} />
        {unread > 0 ? (
          <span
            aria-hidden="true"
            style={{
              position: "absolute", top: -4, right: -4,
              minWidth: 18, height: 18, padding: "0 5px",
              borderRadius: 999,
              background: "#dc2626", color: "#fff",
              fontSize: 10, fontWeight: 800,
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              boxShadow: "0 0 0 2px #fff",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {badgeLabel}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label="Notifications"
          style={{
            position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 250,
            width: 380, maxWidth: "92vw",
            background: "#ffffff",
            border: "1px solid #dbe6f2", borderRadius: 14,
            boxShadow: "0 24px 48px rgba(14,38,66,0.18)",
            overflow: "hidden",
            display: "flex", flexDirection: "column",
          }}
        >
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "10px 14px", borderBottom: "1px solid #eef2f7",
            background: "#f8fafd",
          }}>
            <div style={{ fontSize: 13, fontWeight: 800, color: "#0f1f33" }}>Notifications</div>
            <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700 }}>
              {unread > 0 ? `${unread} unread` : "All clear"}
            </div>
          </div>

          <div style={{ maxHeight: "min(60vh, 520px)", overflowY: "auto" }}>
            {loading && items.length === 0 ? (
              <div style={{ padding: 18, fontSize: 12.5, color: "#64748b", display: "inline-flex", alignItems: "center", gap: 8 }}>
                <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /> Loading…
              </div>
            ) : visibleItems.length === 0 ? (
              <div style={{ padding: 22, textAlign: "center" }}>
                <CheckCircle2 size={26} style={{ margin: "0 auto 8px", color: "#94a3b8" }} />
                <div style={{ fontSize: 13, fontWeight: 700, color: "#334155" }}>You're all caught up</div>
                <div style={{ fontSize: 11.5, color: "#7a8ea4", marginTop: 4 }}>
                  New signals from email replies and other system events will show up here.
                </div>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 0 }}>
                {visibleItems.map((n) => (
                  <NotificationRow
                    key={n.id}
                    notification={n}
                    busy={busyId === n.id}
                    onMarkRead={() => handleMarkRead(n)}
                    onAccept={() => handleAccept(n)}
                    onDismiss={() => handleDismiss(n)}
                  />
                ))}
              </div>
            )}
          </div>

          {error ? (
            <div style={{ padding: "8px 14px", borderTop: "1px solid #fecaca", background: "#fef2f2", color: "#b91c1c", fontSize: 12 }}>
              {error}
            </div>
          ) : null}
        </div>
      ) : null}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function NotificationRow({
  notification: n,
  busy,
  onMarkRead,
  onAccept,
  onDismiss,
}: {
  notification: AppNotification;
  busy: boolean;
  onMarkRead: () => void;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  // Mark-read on first view. Tiny effect so it fires once when the row mounts.
  useEffect(() => {
    if (!n.read_at) {
      const id = window.setTimeout(() => onMarkRead(), 1200);
      return () => window.clearTimeout(id);
    }
  // We intentionally don't depend on onMarkRead — it changes every render
  // and would re-fire the timer; the effect should only run on row identity.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n.id]);

  const unread = !n.read_at;
  const accepted = !!n.accepted_at;
  const canAccept = n.type === "meeting_booked_suggest_deal" && !accepted;

  return (
    <div style={{
      padding: "12px 14px",
      borderBottom: "1px solid #eef2f7",
      background: unread ? "#f5f9ff" : "#ffffff",
      display: "grid", gap: 8,
      transition: "background-color 200ms ease",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <span style={{
          width: 26, height: 26, borderRadius: 8,
          background: unread ? "#1d4ed8" : "#cbd5e1",
          color: "#fff",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          flexShrink: 0,
        }}>
          <Sparkles size={13} />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{
            fontSize: 13, fontWeight: 800, color: "#0f1f33",
            overflow: "hidden", textOverflow: "ellipsis",
          }}>
            {n.title}
          </div>
          {n.body ? (
            <div style={{ fontSize: 12, color: "#475569", marginTop: 3, lineHeight: 1.5 }}>
              {n.body}
            </div>
          ) : null}
          <div style={{ fontSize: 10.5, color: "#94a3b8", marginTop: 4, fontWeight: 700 }}>
            {relativeTime(n.created_at)}
            {accepted ? " · accepted" : ""}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        {canAccept ? (
          <button
            type="button"
            onClick={onAccept}
            disabled={busy}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 12px", borderRadius: 9,
              border: "1px solid #16a34a", background: "#ecfdf5",
              color: "#15803d", fontSize: 11.5, fontWeight: 800,
              cursor: busy ? "wait" : "pointer",
            }}
          >
            {busy ? <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> : <CheckCircle2 size={11} />}
            Create deal
          </button>
        ) : null}
        {!n.dismissed_at ? (
          <button
            type="button"
            onClick={onDismiss}
            disabled={busy}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 12px", borderRadius: 9,
              border: "1px solid #e2e8f0", background: "#ffffff",
              color: "#475569", fontSize: 11.5, fontWeight: 700,
              cursor: busy ? "wait" : "pointer",
            }}
          >
            <X size={11} /> Dismiss
          </button>
        ) : null}
      </div>
    </div>
  );
}

// Compact relative-time formatter. Mirror of the one in ProgressCell —
// kept inline because the formats are subtly different (here we want
// "5m ago" not "5m").
function relativeTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = Date.now() - d.getTime();
  const min = Math.floor(diffMs / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

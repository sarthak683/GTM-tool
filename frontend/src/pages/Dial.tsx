import { useEffect, useMemo, useState } from "react";

/**
 * /dial?tel=+15551234567&name=Jane%20Doe
 *
 * Tiny bridge page reached only via a push-notification tap. Its sole job is
 * to hand the phone number to the OS dialer via `tel:` — which is reliable
 * from a *document* context but unreliable from `clients.openWindow()` in a
 * service worker (see sw.js for the why).
 *
 * Auto-redirects on mount, with a visible "Call" button as a fallback for
 * Android OEM browsers that gate `tel:` until the user taps once.
 */
export default function Dial() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const tel = params.get("tel") || "";
  const name = params.get("name") || "";
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tel) {
      setError("No phone number in the notification.");
      return;
    }
    // Slight delay so the document is fully parsed; some Android Chrome
    // builds drop the navigation if it fires inside the same microtask
    // as mount.
    const handle = window.setTimeout(() => {
      try {
        window.location.href = `tel:${tel}`;
      } catch (err) {
        setError("Could not open the dialer automatically — tap the button below.");
      }
    }, 50);
    return () => window.clearTimeout(handle);
  }, [tel]);

  return (
    <div style={{
      minHeight: "100dvh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: 24,
      background: "#f8f7f5",
      fontFamily: "Inter, system-ui, -apple-system, sans-serif",
    }}>
      <div style={{
        width: "100%", maxWidth: 400,
        background: "#fff", borderRadius: 18,
        border: "1px solid #e0e7ef",
        boxShadow: "0 18px 46px rgba(14,38,66,0.10)",
        padding: 28, textAlign: "center",
      }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#7a8ea4", letterSpacing: 0.4, textTransform: "uppercase", marginBottom: 12 }}>
          Calling{name ? ` ${name}` : ""}
        </div>
        <div style={{ fontSize: 24, fontWeight: 800, color: "#0f2744", marginBottom: 6, wordBreak: "break-all" }}>
          {tel || "—"}
        </div>
        <div style={{ fontSize: 13, color: "#5b6f86", marginBottom: 22, lineHeight: 1.6 }}>
          {error
            ? error
            : "Opening your phone's dialer…"}
        </div>
        {tel && (
          <a
            href={`tel:${tel}`}
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              gap: 8, width: "100%", minHeight: 52, borderRadius: 14,
              background: "linear-gradient(135deg,#0f5fb8,#174ea6)",
              color: "#fff", fontSize: 16, fontWeight: 800,
              textDecoration: "none",
              boxShadow: "0 12px 28px rgba(23,80,137,0.22)",
            }}
          >
            Call {name || tel}
          </a>
        )}
      </div>
    </div>
  );
}

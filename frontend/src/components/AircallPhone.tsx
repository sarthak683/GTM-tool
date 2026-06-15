/**
 * AircallPhone — persistent floating phone panel powered by Aircall Everywhere SDK.
 *
 * Mount once at the app root. Exposes window.__aircallDial(phoneNumber) so any
 * component can trigger a call without prop drilling.
 *
 * The panel slides in from the bottom-right when a call is triggered or when
 * the rep manually opens it. It can be minimised to a small tab.
 */
import { useEffect, useRef, useState } from "react";
import { GripVertical, Phone, X, Minus, PhoneCall, PhoneOff, PhoneMissed } from "lucide-react";
import AircallPhone from "aircall-everywhere";

// Extend window type for the global dial trigger
declare global {
  interface Window {
    __aircallDial?: (phoneNumber: string, contactName?: string) => void;
    __aircallOpen?: () => void;
  }
}

interface CallState {
  active: boolean;
  contactName?: string;
  phoneNumber?: string;
  duration: number;       // seconds elapsed
  status: "idle" | "ringing" | "answered" | "ended";
}

export default function AircallPhonePanel() {
  const panelBottom = 24;
  const [open, setOpen] = useState(false);
  const [minimised, setMinimised] = useState(false);
  const [sdkReady, setSdkReady] = useState(false);
  const [sdkUnavailable, setSdkUnavailable] = useState(false);
  const [verticalOffset, setVerticalOffset] = useState(0);
  const [callState, setCallState] = useState<CallState>({
    active: false,
    duration: 0,
    status: "idle",
  });

  const phoneRef = useRef<AircallPhone | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const dragStateRef = useRef<{ startY: number; startOffset: number } | null>(null);
  const dragMovedRef = useRef(false);
  const suppressOpenClickRef = useRef(false);

  const clampVerticalOffset = (nextOffset: number) => {
    if (typeof window === "undefined") return 0;
    const maxUp = Math.max(window.innerHeight - 160, 0);
    return Math.max(-maxUp, Math.min(0, nextOffset));
  };

  // ── Init SDK once ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (phoneRef.current) return; // already initialised

    try {
      const phone = new AircallPhone({
        domToLoadWorkspace: "#aircall-workspace",
        onLogin: () => {
          setSdkReady(true);
          setSdkUnavailable(false);
          console.log("[Aircall] Agent logged in");
        },
        onLogout: () => {
          setSdkReady(false);
          console.log("[Aircall] Agent logged out");
        },
        size: "big",
        debug: false,
      });

      // ── Call lifecycle events ────────────────────────────────────────────────
      phone.on("incoming_call", (info: any) => {
        setCallState({
          active: true,
          phoneNumber: info.from,
          contactName: info.contact?.name,
          duration: 0,
          status: "ringing",
        });
        setOpen(true);
        setMinimised(false);
      });

      phone.on("outgoing_call", (info: any) => {
        setCallState(prev => ({
          ...prev,
          active: true,
          phoneNumber: info.to,
          duration: 0,
          status: "ringing",
        }));
      });

      phone.on("outgoing_answered", () => {
        setCallState(prev => ({ ...prev, status: "answered" }));
        // Start duration timer
        if (timerRef.current) clearInterval(timerRef.current);
        timerRef.current = setInterval(() => {
          setCallState(prev => ({ ...prev, duration: prev.duration + 1 }));
        }, 1000);
      });

      phone.on("call_end_ringtone", ({ answer_status }: any) => {
        if (answer_status === "no_answer" || answer_status === "busy") {
          setCallState(prev => ({ ...prev, status: "ended", active: false }));
          if (timerRef.current) clearInterval(timerRef.current);
        }
      });

      phone.on("call_ended", ({ duration }: any) => {
        if (timerRef.current) clearInterval(timerRef.current);
        setCallState(prev => ({
          ...prev,
          active: false,
          duration: duration || prev.duration,
          status: "ended",
        }));
        // Auto-collapse after 3 seconds
        setTimeout(() => {
          setCallState({ active: false, duration: 0, status: "idle" });
          setMinimised(true);
        }, 3000);
      });

      phoneRef.current = phone;
    } catch (error) {
      console.warn("[Aircall] SDK unavailable; CRM will continue without the phone widget.", error);
      setSdkReady(false);
      setSdkUnavailable(true);
    }
  }, []);

  // ── Global dial trigger — called from any component ────────────────────────
  useEffect(() => {
    window.__aircallDial = (phoneNumber: string, contactName?: string) => {
      setOpen(true);
      setMinimised(false);
      setCallState(prev => ({ ...prev, phoneNumber, contactName, status: "ringing" }));

      // Attempt dial with retry — SDK iframe needs to be fully ready
      const attemptDial = (attemptsLeft: number) => {
        if (!phoneRef.current) return;
        phoneRef.current.send(
          "dial_number",
          { phone_number: phoneNumber },
          (success: boolean) => {
            if (success) {
              console.log("[Aircall] dial_number sent:", phoneNumber);
            } else if (attemptsLeft > 1) {
              console.warn("[Aircall] dial_number not ready, retrying…");
              setTimeout(() => attemptDial(attemptsLeft - 1), 600);
            } else {
              console.warn("[Aircall] dial_number failed after retries — panel is open, rep can dial manually");
            }
          }
        );
      };

      // Wait 500ms for iframe to become interactive, then retry up to 3 times
      setTimeout(() => attemptDial(3), 500);
    };

    window.__aircallOpen = () => {
      setOpen(true);
      setMinimised(false);
    };

    return () => {
      delete window.__aircallDial;
      delete window.__aircallOpen;
    };
  }, []);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  useEffect(() => {
    const saved = window.localStorage.getItem("crm.aircall.verticalOffset");
    if (saved) {
      const parsed = Number(saved);
      if (!Number.isNaN(parsed)) {
        setVerticalOffset(clampVerticalOffset(parsed));
      }
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem("crm.aircall.verticalOffset", String(verticalOffset));
  }, [verticalOffset]);

  useEffect(() => {
    const handleResize = () => {
      setVerticalOffset((current) => clampVerticalOffset(current));
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    const handlePointerMove = (event: PointerEvent) => {
      const drag = dragStateRef.current;
      if (!drag) return;
      const deltaY = event.clientY - drag.startY;
      if (Math.abs(deltaY) > 3) {
        dragMovedRef.current = true;
      }
      setVerticalOffset(clampVerticalOffset(drag.startOffset + deltaY));
    };

    const handlePointerUp = () => {
      if (!dragStateRef.current) return;
      suppressOpenClickRef.current = dragMovedRef.current;
      dragStateRef.current = null;
      dragMovedRef.current = false;
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, []);

  const beginDrag = (clientY: number) => {
    dragStateRef.current = { startY: clientY, startOffset: verticalOffset };
    dragMovedRef.current = false;
  };

  const formatDuration = (secs: number) => {
    const m = Math.floor(secs / 60).toString().padStart(2, "0");
    const s = (secs % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  };

  const statusColor = {
    idle: "#94a3b8",
    ringing: "#f59e0b",
    answered: "#16a34a",
    ended: "#64748b",
  }[callState.status];

  const statusLabel = callState.active
    ? callState.status === "answered"
      ? formatDuration(callState.duration)
      : callState.status === "ringing"
        ? "Ringing..."
        : "Call ended"
    : sdkUnavailable
      ? "Unavailable"
    : sdkReady
      ? "Ready for calls"
      : "Loading workspace...";

  if (sdkUnavailable) return null;

  return (
    <>
      {/* ── Aircall workspace div — ALWAYS in DOM (hidden offscreen when closed) ── */}
      {/* The SDK needs this element to exist at init time to mount the iframe */}
      <div
        id="aircall-workspace"
        style={{
          position: "fixed",
          bottom: -9999, left: -9999,
          width: 0, height: 0,
          overflow: "hidden",
          // This div is just for SDK initialization. The actual iframe gets
          // moved into view via the panel below once the SDK creates it.
        }}
      />

      {/* ── Floating trigger button (visible when panel is closed) ── */}
      {!open && (
        <button
          onClick={() => {
            if (suppressOpenClickRef.current) {
              suppressOpenClickRef.current = false;
              return;
            }
            setOpen(true);
            setMinimised(false);
          }}
          onPointerDown={(event) => beginDrag(event.clientY)}
          style={{
            position: "fixed", bottom: panelBottom, right: 24, zIndex: 900,
            width: 194,
            borderRadius: 20,
            border: "1px solid #d9e6f2",
            background: "linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(243,248,255,0.98) 100%)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 14px",
            boxShadow: "0 16px 36px rgba(15,39,68,0.10)",
            transition: "all 0.2s ease",
            textAlign: "left",
            backdropFilter: "blur(12px)",
            transform: `translateY(${verticalOffset}px)`,
            touchAction: "none",
          }}
          title="Open Aircall phone"
        >
          <span
            style={{
              width: 38,
              height: 38,
              borderRadius: 14,
              background: callState.active
                ? "linear-gradient(135deg, #0f7b4b, #16a34a)"
                : "linear-gradient(135deg, #0f2744, #175089)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              boxShadow: "inset 0 1px 0 rgba(255,255,255,0.18)",
            }}
          >
            {callState.status === "ended"
              ? <PhoneMissed size={16} color="#ffffff" />
              : callState.active
                ? <PhoneCall size={16} color="#ffffff" />
                : <Phone size={16} color="#ffffff" />
            }
          </span>
          <span style={{ minWidth: 0, display: "grid", gap: 2 }}>
            <span style={{ color: "#18324b", fontSize: 13, fontWeight: 800, lineHeight: 1.1 }}>
              Aircall
            </span>
            <span style={{ color: "#6a7f96", fontSize: 11.5, fontWeight: 600, lineHeight: 1.2 }}>
              {statusLabel}
            </span>
          </span>
          <span style={{ marginLeft: "auto", color: "#9aa8b6", display: "inline-flex", alignItems: "center" }}>
            <GripVertical size={16} />
          </span>
          {callState.active && (
            <span style={{
              position: "absolute", top: 10, right: 10,
              width: 10, height: 10, borderRadius: "50%",
              background: "#ef4444", border: "2px solid #ffffff",
              animation: "pulse 1.5s infinite",
            }} />
          )}
        </button>
      )}

      {/* ── Phone panel ── */}
      {open && (
        <div style={{
          position: "fixed", bottom: panelBottom, right: 24, zIndex: 950,
          width: minimised ? 244 : 392,
          maxWidth: "calc(100vw - 36px)",
          borderRadius: 24,
          background: "linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(247,251,255,0.98) 100%)",
          border: "1px solid #d8e4ef",
          boxShadow: "0 24px 54px rgba(14,38,66,0.14)",
          overflow: "hidden",
          transition: "width 0.2s ease, transform 0.2s ease",
          display: "flex", flexDirection: "column",
          backdropFilter: "blur(14px)",
          transform: `translateY(${verticalOffset}px)`,
        }}>
          {/* Header — z-index ensures it stays clickable above iframe */}
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "14px 16px",
            background: "linear-gradient(180deg, rgba(255,255,255,0.92) 0%, rgba(240,246,255,0.92) 100%)",
            color: "#18324b",
            position: "relative",
            zIndex: 2,
            flexShrink: 0,
            borderBottom: "1px solid #e3edf6",
          }}>
            <div
              onPointerDown={(event) => beginDrag(event.clientY)}
              style={{ display: "flex", alignItems: "center", gap: 10, cursor: "grab", flex: 1, minWidth: 0, touchAction: "none" }}
              title="Drag Aircall widget"
            >
              <div style={{
                width: 36, height: 36, borderRadius: 14,
                background: callState.active
                  ? "linear-gradient(135deg, #e8f8f0, #d8f2e5)"
                  : "linear-gradient(135deg, #eef5ff, #e6f0fb)",
                display: "flex", alignItems: "center", justifyContent: "center",
                border: "1px solid #d8e5f2",
              }}>
                {callState.status === "answered"
                  ? <PhoneCall size={15} color="#1f8f5f" />
                  : callState.status === "ended"
                    ? <PhoneOff size={15} color="#7b8ea3" />
                    : <Phone size={15} color="#175089" />
                }
              </div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 800 }}>
                  {callState.active
                    ? callState.contactName || callState.phoneNumber || "Calling…"
                    : "Aircall Phone"
                  }
                </div>
                <div style={{ fontSize: 11.5, color: "#6d8297", display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: statusColor, display: "inline-block" }} />
                  {statusLabel}
                </div>
              </div>
              <div style={{ marginLeft: "auto", color: "#95a3b2", display: "inline-flex", alignItems: "center", paddingRight: 10 }}>
                <GripVertical size={16} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                onClick={() => setMinimised(m => !m)}
                style={{ background: "#ffffff", border: "1px solid #d9e5f0", borderRadius: 10, padding: "6px 8px", cursor: "pointer", color: "#5b7087" }}
                title={minimised ? "Expand" : "Minimise"}
              >
                <Minus size={14} />
              </button>
              <button
                onClick={() => setOpen(false)}
                style={{ background: "#ffffff", border: "1px solid #d9e5f0", borderRadius: 10, padding: "6px 8px", cursor: "pointer", color: "#5b7087" }}
                title="Close"
              >
                <X size={14} />
              </button>
            </div>
          </div>

          {/* Aircall iframe container — rendered inline, below the header */}
          {!minimised && (
            <div
              ref={(el) => {
                const initDiv = document.getElementById("aircall-workspace");
                if (!initDiv) return;
                if (el) {
                  // Mount: move SDK iframe from holding div into the visible panel
                  while (initDiv.firstChild) {
                    el.appendChild(initDiv.firstChild);
                  }
                  // Store a reference so the cleanup below can find this container
                  (el as any).__aircallContainer = true;
                  (initDiv as any).__visibleContainer = el;
                } else {
                  // Unmount: return iframe children to holding div
                  const container = (initDiv as any).__visibleContainer as HTMLElement | undefined;
                  if (container) {
                    while (container.firstChild) {
                      initDiv.appendChild(container.firstChild);
                    }
                    delete (initDiv as any).__visibleContainer;
                  }
                }
              }}
              style={{
                width: "100%",
                height: 620,
                position: "relative",
                zIndex: 1,
                background: "#ffffff",
              }}
            />
          )}

          {/* Post-call status bar */}
          {callState.status === "ended" && !minimised && (
            <div style={{
              padding: "10px 16px",
              background: "#edf9f3",
              borderTop: "1px solid #cdeed9",
              fontSize: 12, color: "#166534",
              display: "flex", alignItems: "center", gap: 6,
              flexShrink: 0,
              position: "relative",
              zIndex: 2,
            }}>
              <PhoneOff size={12} />
              Call ended · {formatDuration(callState.duration)} · Auto-logged in CRM
            </div>
          )}
        </div>
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.6; transform: scale(1.2); }
        }
      `}</style>
    </>
  );
}

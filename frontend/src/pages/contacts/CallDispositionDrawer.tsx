import { useEffect, useRef, useState } from "react";
import { Building2, Phone, Clock, Mail, Link2, X, Mic, CheckCircle2, Loader2, ArrowRight } from "lucide-react";
import type { Activity, Contact } from "../../types";
import { contactsApi, activitiesApi, remindersApi, pushApi } from "../../lib/api";
import type { PreCallBrief } from "../../lib/api";
import { avatarColor, getInitials, gmailComposeUrl } from "../../lib/utils";
import { useToast } from "../../lib/ToastContext";
import {
  CALL_DISPOSITION_OPTIONS,
  deriveSequenceStatusFromCallDisposition,
  formatCallDisposition,
} from "../../lib/prospectWorkflow";
import { CallRecordingPanel, type AISuggestion, type CallRecordingPanelHandle } from "./CallRecordingPanel";
import { PreCallIntelPanel } from "./PreCallIntelPanel";
import LogLinkedInDialog from "../../components/LogLinkedInDialog";

const FOLLOWUP_DISPOSITIONS = new Set<string>([
  "interested_follow_up_required",
  "call_back_later_rescheduled",
]);

function defaultFollowupLocalString(): string {
  const target = new Date();
  target.setDate(target.getDate() + 1);
  target.setHours(10, 0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}T${pad(target.getHours())}:${pad(target.getMinutes())}`;
}

function canonicalTimezone(value?: string | null): string {
  if (!value) return "";
  return value.includes("/") ? value : value;
}

function formatTimezoneLabel(value?: string | null): string {
  if (!value) return "";
  return value.replace(/^.*\//, "").replace(/_/g, " ").toUpperCase();
}

function personaChipStyle(personaType?: string): { bg: string; fg: string; border: string; label: string } {
  const t = (personaType || "").toLowerCase();
  if (t === "champion") return { bg: "#ecfdf5", fg: "#047857", border: "#a7f3d0", label: "Champion" };
  if (t === "buyer")    return { bg: "#eff6ff", fg: "#1d4ed8", border: "#bfdbfe", label: "Buyer" };
  if (t === "evaluator")return { bg: "#f5f3ff", fg: "#6d28d9", border: "#ddd6fe", label: "Evaluator" };
  if (t === "blocker")  return { bg: "#fef2f2", fg: "#b91c1c", border: "#fecaca", label: "Blocker" };
  return { bg: "#f1f5f9", fg: "#475569", border: "#e2e8f0", label: personaType || "Unknown" };
}

interface CallDispositionDrawerProps {
  contact: Contact;
  onClose: () => void;
  onSaved?: () => void;
  /** Optional "next callable prospect" — shows the "Save & next" / "Skip to next" actions. */
  nextContact?: Contact | null;
  /** Called when the rep advances to the next contact (Save & next / Skip). */
  onAdvance?: (contact: Contact) => void;
  /** Current user — used to assign the follow-up reminder. */
  user?: { id?: string } | null;
}

export default function CallDispositionDrawer({
  contact,
  onClose,
  onSaved,
  nextContact,
  onAdvance,
  user,
}: CallDispositionDrawerProps) {
  const toast = useToast();
  const [dialCountdown, setDialCountdown] = useState<number | null>(null);
  const dialTimerRef = useRef<number | null>(null);
  const callRecordingRef = useRef<CallRecordingPanelHandle | null>(null);
  const [callDisposition, setCallDisposition] = useState("");
  const [dispoDropdownOpen, setDispoDropdownOpen] = useState(false);
  const [callNotes, setCallNotes] = useState("");
  const [currentRecordingId, setCurrentRecordingId] = useState<string | null>(null);
  const [callStatus, setCallStatus] = useState("attempted");
  const [savingDisposition, setSavingDisposition] = useState(false);
  const [sessionActivities, setSessionActivities] = useState<Array<{
    type: string;
    call_status?: string;
    call_disposition?: string;
    content?: string | null;
    created_at: string;
  }>>([]);
  const [expandedActivityIndices, setExpandedActivityIndices] = useState<Set<number>>(new Set());
  const [followupAt, setFollowupAt] = useState<string>("");
  const [precallBrief, setPrecallBrief] = useState<PreCallBrief | null>(null);
  const [precallLoading, setPrecallLoading] = useState(false);
  const [linkedinDialogOpen, setLinkedinDialogOpen] = useState(false);
  const aircallEnabled = typeof window !== "undefined" && localStorage.getItem("crm.aircall.enabled") === "true";

  const clearDialTimer = () => {
    if (dialTimerRef.current != null) {
      window.clearInterval(dialTimerRef.current);
      dialTimerRef.current = null;
    }
  };

  const performDial = (target: Contact) => {
    if (!target.phone) return;
    if (aircallEnabled && window.__aircallDial) {
      window.__aircallDial(target.phone, `${target.first_name} ${target.last_name}`.trim());
    }
    pushApi
      .ringMobile(target.id)
      .then((res) => {
        if (res.sent > 0) {
          toast.info(`Rang ${res.sent} device${res.sent === 1 ? "" : "s"}.`, "Mobile call ready");
        } else if (res.configured === 0) {
          toast.warning("Mobile push is not configured yet. The call drawer is ready here.", "Mobile ring unavailable");
        } else if (res.total === 0) {
          toast.info("No mobile PWA is registered for your user yet. Enable mobile notifications from Settings.", "Mobile not registered");
        } else {
          toast.warning("No mobile device accepted the call notification. Re-enable notifications on the phone.", "Mobile ring failed");
        }
      })
      .catch(() => {
        toast.info("Call drawer is ready here. Mobile notification could not be sent.", "Mobile ring skipped");
      });
  };

  const beginCall = () => {
    callRecordingRef.current?.startRecording();
  };

  const startDialCountdown = (target: Contact) => {
    clearDialTimer();
    let remaining = 10;
    setDialCountdown(remaining);
    dialTimerRef.current = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearDialTimer();
        setDialCountdown(null);
        beginCall();
      } else {
        setDialCountdown(remaining);
      }
    }, 1000);
  };

  const cancelDial = () => {
    clearDialTimer();
    setDialCountdown(null);
  };

  const dialNow = () => {
    clearDialTimer();
    setDialCountdown(null);
    beginCall();
  };

  const handleCallDispositionChange = (value: string) => {
    setCallDisposition(value);
    const matched = CALL_DISPOSITION_OPTIONS.find((option) => option.value === value);
    if (matched?.suggestedCallStatus) {
      setCallStatus(matched.suggestedCallStatus);
    }
    if (FOLLOWUP_DISPOSITIONS.has(value) && !followupAt) {
      setFollowupAt(defaultFollowupLocalString());
    }
  };

  const saveCallDisposition = async (opts?: { advance?: boolean }) => {
    if (!callDisposition) return;
    setSavingDisposition(true);
    try {
      const derivedSeqStatus = deriveSequenceStatusFromCallDisposition(callDisposition, contact.sequence_status);
      const nowIso = new Date().toISOString();
      const needsFollowup = FOLLOWUP_DISPOSITIONS.has(callDisposition);
      const followupLocal = needsFollowup ? (followupAt || defaultFollowupLocalString()) : "";
      const followupIso = needsFollowup
        ? (() => {
            const d = new Date(followupLocal);
            return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
          })()
        : undefined;
      if (needsFollowup && !followupIso) {
        return;
      }
      if (needsFollowup && !followupAt) {
        setFollowupAt(followupLocal);
      }
      await contactsApi.update(contact.id, {
        call_status: callStatus,
        call_disposition: callDisposition,
        call_notes: (callNotes || null) as never,
        call_last_at: nowIso,
        ...(followupIso ? { next_followup_at: followupIso } : {}),
        ...(derivedSeqStatus && derivedSeqStatus !== contact.sequence_status
          ? { sequence_status: derivedSeqStatus }
          : {}),
      });

      const dispositionLabel = formatCallDisposition(callDisposition);
      const contactLabel = `${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim();
      const activityContent = callNotes
        ? `${dispositionLabel} call with ${contactLabel}: ${callNotes}`
        : `${dispositionLabel} call with ${contactLabel}`;
      try {
        await activitiesApi.create({
          type: "call",
          source: "manual",
          content: activityContent,
          contact_id: contact.id,
          call_outcome: callStatus || undefined,
          event_metadata: {
            event_type: "manual_call_logged",
            call_disposition: callDisposition,
            call_status: callStatus,
            followup_at: followupIso,
            logged_at: nowIso,
            ...(currentRecordingId ? { recording_id: currentRecordingId } : {}),
          },
        } as Partial<Activity>);
      } catch {
        // non-fatal
      }

      if (needsFollowup && followupIso) {
        try {
          const due = new Date(followupIso);
          if (!Number.isNaN(due.getTime())) {
            await remindersApi.create({
              contact_id: contact.id,
              company_id: contact.company_id || undefined,
              note: `Follow-up call — ${formatCallDisposition(callDisposition)}${callNotes ? ` · ${callNotes}` : ""}`,
              due_at: due.toISOString(),
              assigned_to_id: user?.id || undefined,
            });
          }
        } catch {
          // reminder is best-effort
        }
      }

      setSessionActivities((prev) => [{
        type: "call",
        call_status: callStatus,
        call_disposition: callDisposition,
        content: activityContent,
        created_at: nowIso,
      }, ...prev]);
      setExpandedActivityIndices(new Set());
      if (opts?.advance && nextContact) {
        onAdvance?.(nextContact);
      } else {
        onClose();
      }
      onSaved?.();
    } catch {
      // save failure — keep drawer open
    } finally {
      setSavingDisposition(false);
    }
  };

  const handleSkip = () => {
    if (nextContact) {
      onAdvance?.(nextContact);
    } else {
      onClose();
    }
  };

  // Pre-call brief fetch.
  useEffect(() => {
    let cancelled = false;
    setPrecallLoading(true);
    contactsApi
      .getPrecallBrief(contact.id)
      .then((brief) => {
        if (!cancelled) setPrecallBrief(brief);
      })
      .catch(() => {
        if (!cancelled) setPrecallBrief(null);
      })
      .finally(() => {
        if (!cancelled) setPrecallLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contact.id]);

  // Dial + countdown on mount, mirroring the list page's openCallSidebar.
  useEffect(() => {
    performDial(contact);
    startDialCountdown(contact);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Clear a pending countdown on unmount.
  useEffect(() => () => clearDialTimer(), []);

  const outcomes: Array<{ value: string; label: string; icon: typeof Phone; color: string }> = [
    { value: "connected", label: "Connected",  icon: CheckCircle2, color: "#16a34a" },
    { value: "voicemail", label: "Voicemail",  icon: Mail,         color: "#7c3aed" },
    { value: "callback",  label: "Callback",   icon: Clock,        color: "#d97706" },
    { value: "attempted", label: "No answer",  icon: Phone,        color: "#64748b" },
  ];
  const dispoGroups: Array<{ title: string; tone: { bg: string; fg: string; border: string }; values: string[] }> = [
    {
      title: "Positive",
      tone: { bg: "#ecfdf5", fg: "#047857", border: "#a7f3d0" },
      values: ["demo_scheduled_booked", "interested_follow_up_required", "meeting_confirmed"],
    },
    {
      title: "Follow-up",
      tone: { bg: "#fffbeb", fg: "#92400e", border: "#fde68a" },
      values: ["call_back_later_rescheduled", "gatekeeper_connected_to_admin", "referral"],
    },
    {
      title: "Negative",
      tone: { bg: "#fef2f2", fg: "#b91c1c", border: "#fecaca" },
      values: ["connected_not_interested", "do_not_contact_dnc"],
    },
    {
      title: "No contact",
      tone: { bg: "#f1f5f9", fg: "#475569", border: "#e2e8f0" },
      values: ["no_answer_busy_signal", "invalid_number_wrong_number", "hang_up"],
    },
  ];
  const dispoLabel = (v: string) => CALL_DISPOSITION_OPTIONS.find((o) => o.value === v)?.label ?? v;
  const localTime = contact.timezone
    ? (() => { try { return new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: canonicalTimezone(contact.timezone) }); } catch { return null; } })()
    : null;
  const noteChips = ["Send follow-up email", "Booked meeting", "Asked to call back later", "Not the right person", "Voicemail left, no callback yet"];

  return (
    <div className="prospect-call-drawer-shell" style={{
      position: "fixed", inset: 0, zIndex: 200,
      display: "flex", justifyContent: "flex-end",
    }}>
      <div className="prospect-call-drawer-backdrop" style={{ flex: 1, background: "rgba(10,20,40,0.45)", backdropFilter: "blur(2px)" }} onClick={onClose} />

      <div className="prospect-call-drawer-panel" style={{
        width: "min(1180px, 95vw)", maxWidth: "100vw",
        background: "#ffffff",
        borderLeft: "1px solid #d5e3ef",
        boxShadow: "-24px 0 60px rgba(14,38,66,0.18)",
        display: "flex", flexDirection: "column",
        position: "relative",
      }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>

          {dialCountdown != null && (
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14,
              padding: "14px 22px",
              background: "linear-gradient(90deg, #fff7ed 0%, #fbfef4 100%)",
              borderBottom: "1px solid #e9f6d2",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
                <div style={{
                  width: 46, height: 46, borderRadius: "50%", flexShrink: 0,
                  display: "grid", placeItems: "center",
                  background: "#fff", border: "2px solid #9ace3d",
                  color: "#5fa024", fontSize: 18, fontWeight: 800,
                  boxShadow: "0 0 0 4px #e3f4c6, 0 0 18px rgba(154,206,61,0.5)",
                  animation: "ringPulse 1.3s ease-in-out infinite",
                }}>
                  {dialCountdown}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 800, color: "#3f6212" }}>Recording starts in {dialCountdown}s…</div>
                  <div style={{ fontSize: 11.5, color: "#5b7a32", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {contact.phone ? `Dialing ${contact.phone} — ` : ""}get ready, or stop before it begins.
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                <button type="button" onClick={cancelDial} style={{ height: 36, padding: "0 14px", borderRadius: 10, border: "1px solid #f3b6b6", background: "#fff", color: "#b91c1c", fontSize: 12.5, fontWeight: 800, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <X size={13} /> Stop
                </button>
                <button type="button" onClick={dialNow} style={{ height: 36, padding: "0 14px", borderRadius: 10, border: "none", background: "#9ace3d", color: "#fff", fontSize: 12.5, fontWeight: 800, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <Mic size={13} /> Start now
                </button>
              </div>
            </div>
          )}

          {/* HERO */}
          <div style={{
            padding: "16px 22px 14px",
            background: "linear-gradient(180deg, #f6faf0 0%, #ffffff 100%)",
            borderBottom: "1px solid #e8eef5",
            position: "relative",
            boxShadow: "0 6px 14px rgba(15,23,42,0.04)",
          }}>
            <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: "linear-gradient(90deg, #9ace3d, #6fae27)" }} />
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: callDisposition ? "#4d7c0f" : "#5e7290", display: "inline-flex", alignItems: "center", gap: 7 }}>
                <span style={{ width: 8, height: 8, borderRadius: 999, background: callDisposition ? "#16a34a" : "#9ace3d", boxShadow: `0 0 0 3px ${callDisposition ? "#dcfce7" : "#e3f4c6"}`, animation: "callpulse 1.6s ease-in-out infinite" }} />
                {callDisposition ? "Ready to log" : "Call in progress"}
              </span>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                style={{ width: 30, height: 30, borderRadius: 9, border: "1px solid #d5e3ef", background: "#fff", color: "#546679", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}
              >
                <X size={14} />
              </button>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-[15px] font-extrabold ${avatarColor(contact.first_name + contact.last_name)}`} style={{ boxShadow: "0 0 0 3px #fff, 0 0 0 5px #e3f4c6, 0 6px 16px rgba(154,206,61,0.28)" }}>
                {getInitials(`${contact.first_name} ${contact.last_name}`)}
              </div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 21, fontWeight: 800, color: "#0f1f33", letterSpacing: "-0.02em", lineHeight: 1.2 }}>
                  {contact.first_name} {contact.last_name}
                </div>
                {contact.title && (
                  <div style={{ fontSize: 14, color: "#4a5b73", marginTop: 3, fontWeight: 600 }}>{contact.title}</div>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
                  {contact.company_name && (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 999, background: "#f1f5f9", color: "#41526a", border: "1px solid #e2e8f0" }}>
                      <Building2 size={10} /> {contact.company_name}
                    </span>
                  )}
                  {contact.timezone && (
                    <span title={`Local time: ${localTime ?? "—"}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 999, background: "#f5f3ff", color: "#6d28d9", border: "1px solid #ddd6fe" }}>
                      <Clock size={10} /> {formatTimezoneLabel(contact.timezone)}{localTime ? ` · ${localTime}` : ""}
                    </span>
                  )}
                  {contact.persona_type && (() => {
                    const p = personaChipStyle(contact.persona_type);
                    return (
                      <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 999, background: p.bg, color: p.fg, border: `1px solid ${p.border}` }}>
                        {p.label}
                      </span>
                    );
                  })()}
                </div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
              {contact.phone && (
                <span style={{ flex: 1, display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, padding: "8px 10px", borderRadius: 10, background: "#f3fbe3", color: "#4d7c0f", border: "1px solid #cfe89a" }}>
                  <Phone size={12} /> {contact.phone}
                </span>
              )}
              {contact.email && (
                <a
                  href={gmailComposeUrl(contact.email)}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, padding: "8px 10px", borderRadius: 10, background: "#fff", color: "#41526a", border: "1px solid #dce8f4", textDecoration: "none" }}
                  title="Open in Gmail compose"
                >
                  <Mail size={12} /> Email
                </a>
              )}
              <button
                type="button"
                onClick={() => setLinkedinDialogOpen(true)}
                disabled={!contact.linkedin_url}
                title={contact.linkedin_url ? "Log a LinkedIn touch" : "Add a LinkedIn URL first to log a LinkedIn touch"}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700,
                  padding: "8px 10px", borderRadius: 10,
                  background: contact.linkedin_url ? "#f5f3ff" : "#f6f8fb",
                  color: contact.linkedin_url ? "#6d28d9" : "#9aa8b7",
                  border: `1px solid ${contact.linkedin_url ? "#ddd6fe" : "#e2e8f0"}`,
                  cursor: contact.linkedin_url ? "pointer" : "default",
                }}
              >
                <Link2 size={12} /> Log LinkedIn
              </button>
            </div>
          </div>

          {/* TWO-COLUMN BODY */}
          <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)" }}>
            <div style={{ overflowY: "auto", minHeight: 0, borderRight: "1px solid #eef2f7" }}>
              <PreCallIntelPanel
                contact={contact}
                brief={precallBrief}
                loading={precallLoading}
              />
              <CallRecordingPanel
                key={contact.id}
                ref={callRecordingRef}
                contactId={contact.id}
                onRecordingChange={setCurrentRecordingId}
                onSuggestion={(s: AISuggestion) => {
                  handleCallDispositionChange(s.disposition);
                  setCallNotes((existing) => existing.trim() ? existing : s.summary);
                }}
              />
              <div style={{ padding: "0 18px 20px" }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 8 }}>
                  Notes
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 8 }}>
                  {noteChips.map((chip) => (
                    <button
                      key={chip}
                      type="button"
                      onClick={() => setCallNotes((current) => (current ? `${current}\n${chip}` : chip))}
                      style={{
                        padding: "6px 12px", borderRadius: 999,
                        border: "1px dashed #c8d6e6", background: "#f7fafc",
                        color: "#475569", fontSize: 12.5, fontWeight: 600,
                        cursor: "pointer",
                      }}
                      title="Add to notes"
                    >
                      + {chip}
                    </button>
                  ))}
                </div>
                <textarea
                  value={callNotes}
                  onChange={(e) => setCallNotes(e.target.value)}
                  placeholder="What came up on the call? Objections, signals, next steps..."
                  rows={6}
                  style={{ width: "100%", border: "1px solid #d8e2ed", borderRadius: 12, padding: "13px 15px", fontSize: 14.5, color: "#0f1f33", background: "#fff", outline: "none", resize: "vertical", fontFamily: "inherit", lineHeight: 1.6 }}
                />
              </div>
            </div>

            <div style={{ overflowY: "auto", minHeight: 0 }}>
              <div style={{ padding: "22px 26px 28px", animation: "callRise 300ms cubic-bezier(0.22, 1, 0.36, 1) both" }}>
                <div style={{ marginBottom: 18 }}>
                  <div style={{ fontSize: 12, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 8 }}>
                    Call outcome
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 }}>
                    {outcomes.map((o) => {
                      const active = callStatus === o.value;
                      return (
                        <button
                          key={o.value}
                          type="button"
                          onClick={() => setCallStatus(o.value)}
                          style={{
                            display: "flex", alignItems: "center", gap: 9,
                            padding: "11px 14px",
                            borderRadius: 11,
                            border: `1.5px solid ${active ? o.color : "#e4ebf3"}`,
                            background: active ? `${o.color}11` : "#fff",
                            color: active ? o.color : "#3c4f68",
                            fontSize: 13.5, fontWeight: 700,
                            cursor: "pointer",
                            transition: "all 0.12s ease",
                          }}
                        >
                          <span style={{
                            display: "inline-flex", width: 26, height: 26, borderRadius: 7,
                            alignItems: "center", justifyContent: "center",
                            background: active ? o.color : "#f1f5f9",
                            color: active ? "#fff" : "#64748b",
                            flexShrink: 0,
                          }}>
                            <o.icon size={14} />
                          </span>
                          {o.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div style={{ marginBottom: 18 }}>
                  <div style={{ fontSize: 12, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 8 }}>
                    Disposition <span style={{ color: "#ef4444" }}>*</span>
                  </div>
                  <div style={{ position: "relative" }}>
                    <button
                      type="button"
                      onClick={() => setDispoDropdownOpen((v) => !v)}
                      style={{
                        width: "100%",
                        padding: "11px 14px",
                        borderRadius: 11,
                        border: callDisposition
                          ? `1.5px solid ${dispoGroups.find(g => g.values.includes(callDisposition))?.tone.fg ?? "#16a34a"}`
                          : "1.5px solid #e4ebf3",
                        background: callDisposition
                          ? dispoGroups.find(g => g.values.includes(callDisposition))?.tone.bg ?? "#f0fdf4"
                          : "#fff",
                        color: callDisposition
                          ? dispoGroups.find(g => g.values.includes(callDisposition))?.tone.fg ?? "#14532d"
                          : "#6b7c93",
                        fontSize: 13.5,
                        fontWeight: 600,
                        cursor: "pointer",
                        fontFamily: "inherit",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 8,
                        textAlign: "left",
                      }}
                    >
                      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        {callDisposition && (
                          <span style={{
                            width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                            background: dispoGroups.find(g => g.values.includes(callDisposition))?.tone.fg ?? "#16a34a",
                          }} />
                        )}
                        {callDisposition ? dispoLabel(callDisposition) : "— Pick a disposition —"}
                      </span>
                      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ flexShrink: 0, transform: dispoDropdownOpen ? "rotate(180deg)" : "none", transition: "transform 0.15s ease" }}>
                        <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    </button>

                    {dispoDropdownOpen && (
                      <>
                        <div
                          style={{ position: "fixed", inset: 0, zIndex: 98 }}
                          onClick={() => setDispoDropdownOpen(false)}
                        />
                        <div style={{
                          position: "absolute",
                          top: "calc(100% + 6px)",
                          left: 0,
                          right: 0,
                          zIndex: 99,
                          background: "#fff",
                          border: "1.5px solid #e4ebf3",
                          borderRadius: 13,
                          boxShadow: "0 12px 32px rgba(15,23,42,0.14)",
                          overflow: "hidden",
                        }}>
                          {dispoGroups.map((g, gi) => (
                            <div key={g.title}>
                              <div style={{
                                padding: "7px 14px 5px",
                                background: g.tone.bg,
                                borderTop: gi > 0 ? `1px solid ${g.tone.border}` : "none",
                                display: "flex",
                                alignItems: "center",
                                gap: 6,
                              }}>
                                <span style={{ width: 7, height: 7, borderRadius: "50%", background: g.tone.fg, flexShrink: 0 }} />
                                <span style={{ fontSize: 10.5, fontWeight: 900, color: g.tone.fg, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                                  {g.title}
                                </span>
                              </div>
                              {g.values.map((v) => {
                                const active = callDisposition === v;
                                return (
                                  <button
                                    key={v}
                                    type="button"
                                    onClick={() => { handleCallDispositionChange(v); setDispoDropdownOpen(false); }}
                                    style={{
                                      width: "100%",
                                      padding: "9px 14px 9px 28px",
                                      display: "flex",
                                      alignItems: "center",
                                      gap: 9,
                                      background: active ? g.tone.bg : "#fff",
                                      border: "none",
                                      borderLeft: `3px solid ${active ? g.tone.fg : "transparent"}`,
                                      color: active ? g.tone.fg : "#1e2d3e",
                                      fontSize: 13.5,
                                      fontWeight: active ? 700 : 500,
                                      cursor: "pointer",
                                      textAlign: "left",
                                      fontFamily: "inherit",
                                      transition: "background 0.1s ease",
                                    }}
                                    onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = g.tone.bg; }}
                                    onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "#fff"; }}
                                  >
                                    <span style={{
                                      width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                                      background: active ? g.tone.fg : "#cbd5e1",
                                    }} />
                                    {dispoLabel(v)}
                                    {active && (
                                      <svg style={{ marginLeft: "auto" }} width="13" height="13" viewBox="0 0 13 13" fill="none">
                                        <path d="M2 6.5l3.5 3.5L11 3" stroke={g.tone.fg} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                                      </svg>
                                    )}
                                  </button>
                                );
                              })}
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                  {!callDisposition && (
                    <div style={{ fontSize: 11, color: "#ef4444", marginTop: 6 }}>Pick a disposition to enable Save.</div>
                  )}
                </div>

                {FOLLOWUP_DISPOSITIONS.has(callDisposition) && (
                  <div style={{ marginBottom: 18, padding: "12px 14px", borderRadius: 12, background: "#fffbeb", border: "1px solid #fde68a" }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                      <label style={{ fontSize: 11.5, fontWeight: 800, color: "#92400e", textTransform: "uppercase", letterSpacing: "0.06em", display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <Clock size={12} /> Follow-up <span style={{ color: "#dc2626" }}>*</span>
                      </label>
                      <span style={{ fontSize: 10.5, color: "#92400e", fontWeight: 600 }}>Reminder created on save</span>
                    </div>
                    <input
                      type="datetime-local"
                      value={followupAt}
                      onChange={(e) => setFollowupAt(e.target.value)}
                      style={{
                        width: "100%", border: "1px solid #f5d77a", borderRadius: 10,
                        padding: "9px 12px", fontSize: 13, color: "#0f1f33",
                        background: "#fff", outline: "none", fontFamily: "inherit",
                      }}
                    />
                  </div>
                )}

                {(() => {
                  type DisplayAct = {
                    type: string;
                    call_disposition?: string;
                    content?: string | null;
                    created_at: string;
                    isSession: boolean;
                  };
                  const allActs: DisplayAct[] = [
                    ...sessionActivities.map((a) => ({
                      type: a.type,
                      call_disposition: a.call_disposition,
                      content: a.content,
                      created_at: a.created_at,
                      isSession: true,
                    })),
                    ...(precallBrief?.recent_activities ?? []).map((a) => ({
                      type: a.type,
                      call_disposition: undefined,
                      content: a.ai_summary || a.content || null,
                      created_at: a.created_at,
                      isSession: false,
                    })),
                  ];
                  if (allActs.length === 0) return null;

                  const fmtDate = (iso: string) => {
                    const d = new Date(iso);
                    const diffH = (Date.now() - d.getTime()) / 3_600_000;
                    if (diffH < 1) return "Just now";
                    if (diffH < 24) return `${Math.round(diffH)}h ago`;
                    const diffD = Math.floor(diffH / 24);
                    if (diffD <= 6) return `${diffD}d ago`;
                    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
                  };

                  const chStyle = (type: string) => {
                    const t = (type || "").toLowerCase();
                    if (t === "email") return { bg: "#eff6ff", fg: "#1d4ed8", border: "#bfdbfe" };
                    if (t.includes("linkedin")) return { bg: "#f0f9ff", fg: "#0369a1", border: "#bae6fd" };
                    return { bg: "#f0fdf4", fg: "#15803d", border: "#bbf7d0" };
                  };

                  const chIcon = (type: string) => {
                    const t = (type || "").toLowerCase();
                    if (t === "email") return <Mail size={11} />;
                    if (t.includes("linkedin")) return <Link2 size={11} />;
                    return <Phone size={11} />;
                  };

                  return (
                    <div style={{ marginTop: 18, paddingTop: 16, borderTop: "1px solid #e8eef5" }}>
                      <div style={{ fontSize: 12, fontWeight: 800, color: "#5e7290", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 10, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                        <span>Recent activity</span>
                        <span style={{ fontSize: 11, fontWeight: 600, color: "#94a3b8", textTransform: "none", letterSpacing: 0 }}>
                          {allActs.length} interaction{allActs.length !== 1 ? "s" : ""}
                        </span>
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        {allActs.map((act, idx) => {
                          const isExpanded = expandedActivityIndices.has(idx);
                          const ch = chStyle(act.type);
                          const tone = act.call_disposition
                            ? dispoGroups.find((g) => g.values.includes(act.call_disposition!))?.tone ?? null
                            : null;
                          const noteText = act.content || "";
                          return (
                            <div
                              key={idx}
                              onClick={() =>
                                setExpandedActivityIndices((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(idx)) next.delete(idx);
                                  else next.add(idx);
                                  return next;
                                })
                              }
                              style={{
                                border: `1px solid ${isExpanded ? "#c8d6e6" : "#e8eef5"}`,
                                borderRadius: 11,
                                background: isExpanded ? "#f8fbff" : "#fff",
                                padding: "10px 13px",
                                cursor: "pointer",
                                transition: "border-color 0.12s, background 0.12s",
                              }}
                            >
                              <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: (tone || noteText) ? 6 : 0 }}>
                                <span style={{
                                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                                  width: 22, height: 22, borderRadius: "50%",
                                  background: ch.bg, color: ch.fg, border: `1px solid ${ch.border}`,
                                  flexShrink: 0,
                                }}>
                                  {chIcon(act.type)}
                                </span>
                                <span style={{ fontSize: 11, fontWeight: 800, color: "#3c4f68", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                                  {act.type}
                                </span>
                                {act.isSession && (
                                  <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 999, background: "#f0fdf4", color: "#16a34a", border: "1px solid #bbf7d0" }}>
                                    Just saved
                                  </span>
                                )}
                                <span style={{ marginLeft: "auto", fontSize: 11, color: "#94a3b8" }}>
                                  {fmtDate(act.created_at)}
                                </span>
                                <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
                                  style={{ flexShrink: 0, transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform 0.15s" }}>
                                  <path d="M1.5 3.5l3.5 3 3.5-3" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                                </svg>
                              </div>
                              {tone && act.call_disposition && (
                                <div style={{ marginBottom: noteText ? 5 : 0 }}>
                                  <span style={{
                                    display: "inline-flex", alignItems: "center", gap: 5,
                                    fontSize: 11, fontWeight: 700,
                                    padding: "2px 8px", borderRadius: 999,
                                    background: tone.bg, color: tone.fg, border: `1px solid ${tone.border}`,
                                  }}>
                                    <span style={{ width: 5, height: 5, borderRadius: "50%", background: tone.fg, flexShrink: 0 }} />
                                    {dispoLabel(act.call_disposition)}
                                  </span>
                                </div>
                              )}
                              {noteText && (
                                <div style={{
                                  fontSize: 12.5, color: "#3d5268", lineHeight: 1.5,
                                  ...(isExpanded
                                    ? { whiteSpace: "pre-wrap" as const }
                                    : {
                                        display: "-webkit-box" as const,
                                        WebkitLineClamp: 2,
                                        WebkitBoxOrient: "vertical" as const,
                                        overflow: "hidden",
                                      }
                                  ),
                                }}>
                                  {noteText}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}
              </div>
            </div>
          </div>
        </div>

        {/* STICKY SAVE BAR */}
        <div style={{
          position: "sticky", bottom: 0, left: 0, right: 0,
          padding: "12px 18px",
          background: "rgba(255,255,255,0.97)",
          backdropFilter: "saturate(180%) blur(10px)",
          borderTop: "1px solid #e4ebf3",
          boxShadow: "0 -8px 24px rgba(15,23,42,0.06)",
        }}>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={handleSkip}
              title={nextContact ? `Skip to ${nextContact.first_name} ${nextContact.last_name}` : "Close drawer"}
              style={{
                flexShrink: 0,
                padding: "13px 16px",
                borderRadius: 12,
                border: "1.5px solid #dde6f0",
                background: "#f8fafc",
                color: "#64748b",
                fontSize: 13.5,
                fontWeight: 700,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 6,
                whiteSpace: "nowrap",
                transition: "all 0.12s ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "#f1f5f9";
                e.currentTarget.style.borderColor = "#c8d6e5";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "#f8fafc";
                e.currentTarget.style.borderColor = "#dde6f0";
              }}
            >
              {(callDisposition || callNotes) ? "Skip (don't save)" : "Skip"}
              <ArrowRight size={14} />
            </button>
            <button
              className="prospect-call-drawer-save"
              onClick={() => void saveCallDisposition()}
              disabled={!callDisposition || savingDisposition}
              style={{
                flex: nextContact && callDisposition ? "0 0 auto" : 1,
                padding: "13px 16px", borderRadius: 12,
                border: nextContact && callDisposition ? "1px solid #cdd9e6" : "none",
                background: nextContact && callDisposition ? "#fff" : (callDisposition ? "linear-gradient(135deg, #6fae27 0%, #9ace3d 100%)" : "#e8eef5"),
                color: nextContact && callDisposition ? "#41526a" : (callDisposition ? "#fff" : "#9aafbe"),
                fontSize: 14, fontWeight: 800, letterSpacing: "-0.005em",
                cursor: callDisposition ? "pointer" : "not-allowed",
                display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                opacity: savingDisposition ? 0.7 : 1,
                boxShadow: callDisposition && !nextContact ? "0 6px 16px rgba(111,174,39,0.42)" : "none",
                transition: "all 0.14s ease", whiteSpace: "nowrap",
              }}
            >
              {savingDisposition ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
              {savingDisposition ? "Saving..." : (nextContact && callDisposition ? "Save & close" : (callDisposition ? `Save: ${dispoLabel(callDisposition)}` : "Save & close"))}
            </button>
            {nextContact && callDisposition && (
              <button
                type="button"
                onClick={() => void saveCallDisposition({ advance: true })}
                disabled={savingDisposition}
                title={`Next: ${nextContact.first_name} ${nextContact.last_name}`}
                style={{
                  flex: 1, padding: "13px 0", borderRadius: 12, border: "none",
                  background: "linear-gradient(135deg, #6fae27 0%, #9ace3d 100%)",
                  color: "#fff", fontSize: 14, fontWeight: 800, letterSpacing: "-0.005em",
                  cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  opacity: savingDisposition ? 0.7 : 1,
                  boxShadow: "0 6px 16px rgba(111,174,39,0.42)", transition: "all 0.14s ease", whiteSpace: "nowrap",
                }}
              >
                Save &amp; next <ArrowRight size={15} />
              </button>
            )}
          </div>
        </div>
        <LogLinkedInDialog
          contactId={contact.id}
          contactName={`${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim() || "Prospect"}
          linkedinUrl={contact.linkedin_url}
          sequenceStatus={contact.sequence_status}
          initialStatus={contact.linkedin_status}
          open={linkedinDialogOpen}
          onClose={() => setLinkedinDialogOpen(false)}
          onLogged={() => {
            setLinkedinDialogOpen(false);
            toast.success(`LinkedIn touch logged for ${contact.first_name || "the prospect"}.`, "LinkedIn logged");
          }}
        />
        <style>{`
          @keyframes callpulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.5 } }
          @keyframes ringPulse { 0%, 100% { transform: scale(1) } 50% { transform: scale(1.08) } }
          @keyframes callRise { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: none } }
          @media (prefers-reduced-motion: reduce) { .prospect-call-drawer-panel *, .prospect-call-drawer-panel { animation-duration: 0.001ms !important; } }
        `}</style>
      </div>
    </div>
  );
}

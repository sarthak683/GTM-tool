import { useEffect, useState } from "react";
import { X, AlertTriangle, ArrowRight, Mail, PhoneCall, Link2, Sparkles } from "lucide-react";
import type { SequenceLifecycle } from "../../lib/api";
import { activitiesApi } from "../../lib/api";
import type { Activity } from "../../types";
import { formatLifecycleDate, LifecycleStepRow } from "./LifecycleStepRow";

// Pick an icon + short human label for an Activity row. Reads from
// metadata.event_type first (Instantly webhooks fill this), then falls back
// to the activity's `type` (manual call/email/note) so anything in the
// timeline is renderable, not just sequence-aware events.
function describeActivity(a: Activity): { Icon: typeof Mail; label: string; tone: { bg: string; border: string; fg: string } } {
  const meta = (a.event_metadata && typeof a.event_metadata === "object" ? a.event_metadata : {}) as Record<string, unknown>;
  const eventType = String(meta.event_type ?? "").toLowerCase();
  const type = (a.type ?? "").toLowerCase();
  switch (eventType) {
    case "email_sent":          return { Icon: Mail, label: "Email sent",     tone: { bg: "#eff6ff", border: "#bfdbfe", fg: "#1d4ed8" } };
    case "email_opened":        return { Icon: Mail, label: "Email opened",   tone: { bg: "#f0fdfa", border: "#99f6e4", fg: "#0f766e" } };
    case "email_link_clicked":  return { Icon: Mail, label: "Link clicked",   tone: { bg: "#f0f9ff", border: "#bae6fd", fg: "#0369a1" } };
    case "reply_received":      return { Icon: Mail, label: "Reply received", tone: { bg: "#faf5ff", border: "#e9d5ff", fg: "#7c3aed" } };
    case "email_bounced":       return { Icon: Mail, label: "Email bounced",  tone: { bg: "#fff7ed", border: "#fed7aa", fg: "#c2410c" } };
    default: break;
  }
  if (type === "call")   return { Icon: PhoneCall, label: a.call_outcome ? `Call · ${a.call_outcome.replace(/_/g, " ")}` : "Call logged", tone: { bg: "#f0fdf4", border: "#bbf7d0", fg: "#15803d" } };
  if (type === "linkedin") return { Icon: Link2,     label: "LinkedIn touch",   tone: { bg: "#f5f3ff", border: "#ddd6fe", fg: "#6d28d9" } };
  if (type === "email")  return { Icon: Mail,      label: "Email",            tone: { bg: "#eff6ff", border: "#bfdbfe", fg: "#1d4ed8" } };
  return { Icon: Sparkles, label: type || "Activity", tone: { bg: "#f1f5f9", border: "#cbd5e1", fg: "#475569" } };
}

export function LifecycleDrawer({
  contactId, detail, loading, onClose, onOpenOutreach,
}: {
  contactId: string | null;
  detail: SequenceLifecycle | null;
  loading: boolean;
  onClose: () => void;
  onOpenOutreach?: () => void;
}) {
  // Raw activity timeline. Always fetched so the drawer can show what
  // happened to the contact even when no OutreachSequence row exists (e.g.
  // an Instantly-tracked prospect imported before the campaign was wired up).
  // Without this, the drawer would render "No outreach sequence" next to a
  // prospect row that clearly shows engagement dots — the gap that
  // motivated this section.
  const [activities, setActivities] = useState<Activity[]>([]);
  const [activitiesLoading, setActivitiesLoading] = useState(false);

  useEffect(() => {
    if (!contactId) {
      setActivities([]);
      return;
    }
    let cancelled = false;
    setActivitiesLoading(true);
    activitiesApi
      .list(undefined, contactId)
      .then((rows) => {
        if (cancelled) return;
        // Newest first; the assembler's lifecycle steps already render
        // their own sub-events, so the activity list is the catch-all
        // chronological view.
        const sorted = [...rows].sort((a, b) => (new Date(b.created_at).getTime()) - (new Date(a.created_at).getTime()));
        setActivities(sorted);
      })
      .catch(() => { if (!cancelled) setActivities([]); })
      .finally(() => { if (!cancelled) setActivitiesLoading(false); });
    return () => { cancelled = true; };
  }, [contactId]);

  if (!contactId) return null;

  const statusChipTone = (status: string) => {
    switch (status) {
      case "in_progress": return { bg: "#eff6ff", color: "#1d4ed8", border: "#bfdbfe" };
      case "replied":     return { bg: "#faf5ff", color: "#7c3aed", border: "#e9d5ff" };
      case "booked":      return { bg: "#f0fdf4", color: "#15803d", border: "#bbf7d0" };
      case "stopped":     return { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" };
      case "stalled":     return { bg: "#fef2f2", color: "#dc2626", border: "#fecaca" };
      case "completed":   return { bg: "#f1f5f9", color: "#334155", border: "#cbd5e1" };
      case "ready":       return { bg: "#fef9e7", color: "#92400e", border: "#fde68a" };
      case "never_launched": return { bg: "#f1f5f9", color: "#64748b", border: "#cbd5e1" };
      default: return { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" };
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 220, display: "flex", justifyContent: "flex-end" }}>
      <div style={{ flex: 1, background: "rgba(10,20,40,0.35)" }} onClick={onClose} />
      <div style={{ width: 720, maxWidth: "100vw", background: "#fff", borderLeft: "1px solid #d5e3ef", boxShadow: "-24px 0 48px rgba(14,38,66,0.16)", display: "flex", flexDirection: "column", overflowY: "auto" }}>
        {/* Header */}
        <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid #e8eef5", display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, background: "#fff", zIndex: 1 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800, color: "#0f2744" }}>Sequence lifecycle</div>
            <div style={{ fontSize: 12, color: "#7a96b0" }}>
              {loading ? "Loading…" :
               detail ? `${detail.total_steps} steps · ${detail.days_since_launch != null ? `day ${detail.days_since_launch}` : "not launched"}` :
               "No data"}
            </div>
          </div>
          <button onClick={onClose} style={{ width: 30, height: 30, borderRadius: 8, border: "1px solid #d5e3ef", background: "#fff", color: "#546679", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            <X size={14} />
          </button>
        </div>

        <div style={{ padding: "16px 22px" }}>
          {loading && (
            <div style={{ fontSize: 13, color: "#7a96b0" }}>Loading lifecycle…</div>
          )}

          {!loading && detail && (
            <>
              {/* Top-line status */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
                {(() => {
                  const tone = statusChipTone(detail.status);
                  return (
                    <span style={{ padding: "4px 10px", borderRadius: 999, background: tone.bg, color: tone.color, border: `1px solid ${tone.border}`, fontSize: 11.5, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      {detail.status.replace(/_/g, " ")}
                    </span>
                  );
                })()}
                {detail.sequence?.instantly_campaign_status && (
                  <span style={{ padding: "4px 10px", borderRadius: 999, background: "#f1f5f9", color: "#475569", border: "1px solid #cbd5e1", fontSize: 11, fontWeight: 700 }}>
                    Instantly: {detail.sequence.instantly_campaign_status}
                  </span>
                )}
                {detail.launched_at && (
                  <span style={{ fontSize: 11.5, color: "#64748b" }}>
                    Launched {formatLifecycleDate(detail.launched_at)}
                  </span>
                )}
              </div>

              {/* Issues */}
              {detail.issues.length > 0 && (
                <div style={{ marginBottom: 14, display: "grid", gap: 8 }}>
                  {detail.issues.map((issue, i) => {
                    const toneBg = issue.severity === "error" ? "#fef2f2" :
                                   issue.severity === "warning" ? "#fffbeb" : "#eff6ff";
                    const toneColor = issue.severity === "error" ? "#b91c1c" :
                                      issue.severity === "warning" ? "#92400e" : "#1d4ed8";
                    const toneBorder = issue.severity === "error" ? "#fecaca" :
                                       issue.severity === "warning" ? "#fde68a" : "#bfdbfe";
                    return (
                      <div key={i} style={{ padding: "10px 12px", borderRadius: 10, background: toneBg, border: `1px solid ${toneBorder}`, color: toneColor, display: "flex", gap: 8, alignItems: "flex-start" }}>
                        <AlertTriangle size={14} style={{ marginTop: 2, flexShrink: 0 }} />
                        <div style={{ fontSize: 12.5, lineHeight: 1.5, fontWeight: 600 }}>
                          {issue.message}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Step timeline */}
              {detail.steps.length === 0 ? (
                <div style={{ fontSize: 13, color: "#7a96b0", fontStyle: "italic", marginBottom: 14 }}>
                  No steps defined on this contact's sequence plan yet.
                </div>
              ) : (
                <div>
                  {detail.steps.map((step) => (
                    <LifecycleStepRow key={step.index} step={step} />
                  ))}
                </div>
              )}

              {/* Activity timeline — always shown so engagement is visible
                  even when a contact has no formal OutreachSequence (e.g.
                  Instantly tracked their opens before a campaign was wired
                  up). For sequenced contacts this acts as the catch-all
                  reference list under the per-step cards above. */}
              <div style={{ marginTop: 18 }}>
                <div style={{
                  fontSize: 11.5, fontWeight: 800, color: "#475569",
                  textTransform: "uppercase", letterSpacing: "0.08em",
                  marginBottom: 8,
                }}>
                  Activity timeline
                </div>
                {activitiesLoading ? (
                  <div style={{ fontSize: 12.5, color: "#7a96b0", fontStyle: "italic" }}>Loading activity…</div>
                ) : activities.length === 0 ? (
                  <div style={{
                    fontSize: 12.5, color: "#7a96b0", fontStyle: "italic",
                    padding: "10px 12px", borderRadius: 10, background: "#f8fafd",
                    border: "1px dashed #d5e3ef",
                  }}>
                    No tracked activity for this prospect yet.
                  </div>
                ) : (
                  <div style={{ display: "grid", gap: 6 }}>
                    {activities.map((a) => {
                      const { Icon, label, tone } = describeActivity(a);
                      const subject = a.email_subject || null;
                      const preview = a.ai_summary || a.content || "";
                      // Truncate preview to one line; the full thing is in
                      // the contact detail page if the rep needs it.
                      const previewLine = preview ? preview.replace(/\s+/g, " ").slice(0, 140) : "";
                      return (
                        <div key={a.id} style={{
                          display: "flex", alignItems: "flex-start", gap: 10,
                          padding: "8px 10px", borderRadius: 10,
                          background: tone.bg,
                          border: `1px solid ${tone.border}`,
                        }}>
                          <div style={{
                            width: 24, height: 24, borderRadius: 7,
                            background: "#ffffff",
                            border: `1px solid ${tone.border}`,
                            display: "inline-flex", alignItems: "center", justifyContent: "center",
                            color: tone.fg, flexShrink: 0,
                          }}>
                            <Icon size={12} />
                          </div>
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                              <span style={{ fontSize: 11.5, fontWeight: 800, color: tone.fg, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                                {label}
                              </span>
                              <span style={{ fontSize: 11.5, color: "#475569", fontVariantNumeric: "tabular-nums" }}>
                                {formatLifecycleDate(a.created_at)}
                              </span>
                            </div>
                            {subject ? (
                              <div style={{ fontSize: 12.5, color: "#0f172a", fontWeight: 700, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {subject}
                              </div>
                            ) : null}
                            {previewLine ? (
                              <div style={{ fontSize: 12, color: "#475569", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {previewLine}{preview && preview.length > 140 ? "…" : ""}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {onOpenOutreach && (
                <button
                  onClick={onOpenOutreach}
                  style={{
                    marginTop: 14, width: "100%", padding: "10px 0",
                    border: "1px solid #d5e3ef", borderRadius: 10,
                    background: "#f8fafd", color: "#1d4ed8", fontSize: 13,
                    fontWeight: 700, cursor: "pointer",
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  }}
                >
                  Open Outreach Sequence <ArrowRight size={14} />
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

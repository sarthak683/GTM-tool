import { useEffect, useState, type ReactNode } from "react";
import { X, AlertTriangle, ArrowRight, Mail, PhoneCall, Link2, Sparkles, User, Clock, AtSign, Headphones, Info, ExternalLink, ChevronDown, ChevronRight } from "lucide-react";
import type { SequenceLifecycle } from "../../lib/api";
import { activitiesApi, callRecordingsApi } from "../../lib/api";
import type { Activity, CallRecording } from "../../types";
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
  if (type === "call") {
    const hasRecording = typeof meta.recording_id === "string" && meta.recording_id;
    const base = a.call_outcome ? `Call · ${a.call_outcome.replace(/_/g, " ")}` : "Call logged";
    return {
      Icon: hasRecording ? Headphones : PhoneCall,
      label: hasRecording ? `${base} · recorded` : base,
      tone: { bg: "#f0fdf4", border: "#bbf7d0", fg: "#15803d" },
    };
  }
  if (type === "linkedin") return { Icon: Link2,     label: "LinkedIn touch",   tone: { bg: "#f5f3ff", border: "#ddd6fe", fg: "#6d28d9" } };
  if (type === "email")  return { Icon: Mail,      label: "Email",            tone: { bg: "#eff6ff", border: "#bfdbfe", fg: "#1d4ed8" } };
  return { Icon: Sparkles, label: type || "Activity", tone: { bg: "#f1f5f9", border: "#cbd5e1", fg: "#475569" } };
}

function activityEventType(a: Activity): string {
  const meta = (a.event_metadata && typeof a.event_metadata === "object" ? a.event_metadata : {}) as Record<string, unknown>;
  const explicit = String(meta.event_type ?? "").toLowerCase();
  if (explicit) return explicit;
  const content = (a.content || a.ai_summary || "").toLowerCase();
  if (content.includes("opened")) return "email_opened";
  if (content.includes("positive reply")) return "reply_received";
  if (content.includes("negative reply")) return "reply_received";
  return (a.type || "").toLowerCase();
}

function formatDuration(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

function formatMetadataValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function metadataRows(meta: Record<string, unknown> | undefined): Array<[string, string]> {
  if (!meta) return [];
  const hidden = new Set([
    "event_type", "subject", "from", "to", "cc", "body", "content",
    "html", "text", "email_subject", "email_from", "email_to",
  ]);
  return Object.entries(meta)
    .filter(([key, value]) => !hidden.has(key) && value != null && value !== "")
    .slice(0, 10)
    .map(([key, value]) => [key.replace(/_/g, " "), formatMetadataValue(value)]);
}

function actorLabel(a: Activity): string | null {
  if (a.user_name) return a.user_name;
  if (a.aircall_user_name) return `${a.aircall_user_name} (Aircall)`;
  if (a.email_from) return a.email_from;
  if (a.source) {
    const s = a.source.toLowerCase();
    if (s === "instantly") return "Instantly";
    if (s === "manual") return "Manually logged";
    return a.source.replace(/_/g, " ");
  }
  return null;
}

function DetailRow({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  if (value == null || value === "" || value === false) return null;
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 12, color: "#475569", lineHeight: 1.5 }}>
      <span style={{ color: "#94a3b8", display: "inline-flex", marginTop: 1, flexShrink: 0 }}>{icon}</span>
      <span style={{ color: "#64748b", fontWeight: 800, minWidth: 68, flexShrink: 0 }}>{label}</span>
      <span style={{ color: "#1e293b", wordBreak: "break-word", overflowWrap: "anywhere" }}>{value}</span>
    </div>
  );
}

// Lazy-fetches a CallRecording linked from an Activity row's metadata
// and renders the transcript + AI suggestion in a collapsible block.
// Used inside ActivityDetailCard when meta.recording_id is set.
function RecordingDetails({ recordingId }: { recordingId: string }) {
  const [rec, setRec] = useState<CallRecording | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    callRecordingsApi
      .get(recordingId)
      .then((r) => { if (!cancelled) setRec(r); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load recording."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [recordingId]);

  if (loading) {
    return (
      <div style={{ padding: "8px 10px", borderRadius: 9, background: "#f1f5f9", border: "1px solid #e2e8f0", fontSize: 12, color: "#64748b", fontStyle: "italic" }}>
        Loading recording…
      </div>
    );
  }
  if (error || !rec) {
    return (
      <div style={{ padding: "8px 10px", borderRadius: 9, background: "#fef2f2", border: "1px solid #fecaca", fontSize: 12, color: "#b91c1c" }}>
        {error || "Recording not found."}
      </div>
    );
  }

  const durationLabel = rec.audio_duration_seconds
    ? `${Math.floor(rec.audio_duration_seconds / 60)}:${String(rec.audio_duration_seconds % 60).padStart(2, "0")}`
    : null;

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {rec.ai_disposition || rec.ai_summary ? (
        <div style={{ padding: "8px 10px", background: "#f0fdf4", borderRadius: 9, border: "1px solid #bbf7d0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10.5, fontWeight: 900, color: "#15803d", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
            <Sparkles size={11} />
            AI from call recording
            {rec.ai_confidence != null ? (
              <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums", color: "#475569", fontWeight: 800 }}>
                {Math.round((rec.ai_confidence ?? 0) * 100)}% confidence
              </span>
            ) : null}
          </div>
          {rec.ai_disposition ? (
            <div style={{ fontSize: 11.5, fontWeight: 800, color: "#15803d", marginBottom: 4 }}>
              Disposition: {rec.ai_disposition.replace(/_/g, " ")}
            </div>
          ) : null}
          {rec.ai_summary ? (
            <div style={{ fontSize: 12, color: "#1e293b", lineHeight: 1.55 }}>{rec.ai_summary}</div>
          ) : null}
        </div>
      ) : null}
      <details style={{ fontSize: 12, color: "#334155" }} open={false}>
        <summary style={{ cursor: "pointer", fontWeight: 800, color: "#334155", display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Headphones size={11} /> Transcript
          {rec.transcript ? (
            <span style={{ color: "#64748b", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
              · {rec.transcript.length.toLocaleString()} chars{durationLabel ? ` · ${durationLabel}` : ""}
            </span>
          ) : (
            <span style={{ color: "#94a3b8", fontWeight: 600, fontStyle: "italic" }}>
              — not available
            </span>
          )}
        </summary>
        {rec.transcript ? (
          <div style={{
            marginTop: 6, padding: 10, borderRadius: 8,
            background: "#f8fafc", border: "1px solid #e2e8f0",
            whiteSpace: "pre-wrap", maxHeight: 280, overflowY: "auto",
            lineHeight: 1.55,
          }}>
            {rec.transcript}
          </div>
        ) : null}
      </details>
      <div style={{ fontSize: 10.5, color: "#64748b" }}>
        Status: <strong>{rec.status}</strong>
        {rec.failure_reason ? <span> · {rec.failure_reason}</span> : null}
      </div>
    </div>
  );
}

function ActivityDetailCard({ activity }: { activity: Activity }) {
  const meta0 = (activity.event_metadata && typeof activity.event_metadata === "object" ? activity.event_metadata : {}) as Record<string, unknown>;
  const recordingId = typeof meta0.recording_id === "string" ? meta0.recording_id : null;
  // Expand by default for any activity that carries a recording — that's
  // the most interesting one in the timeline.
  const [expanded, setExpanded] = useState(activityEventType(activity) === "email_opened" || !!recordingId);
  const { Icon, label, tone } = describeActivity(activity);
  const meta = meta0;
  const actor = actorLabel(activity);
  const subject = activity.email_subject || String(meta.subject || "");
  const preview = activity.ai_summary || activity.content || "";
  const previewLine = preview ? preview.replace(/\s+/g, " ").slice(0, 160) : "";
  const rows = metadataRows(meta);
  const duration = formatDuration(activity.call_duration);
  const hasDetails = Boolean(
    activity.email_from || activity.email_to || activity.email_cc || duration || activity.recording_url ||
    activity.call_id || activity.source || rows.length || preview
  );

  return (
    <div style={{
      borderRadius: 12,
      background: "#fff",
      border: `1px solid ${tone.border}`,
      boxShadow: "0 10px 24px rgba(15, 23, 42, 0.05)",
      overflow: "hidden",
    }}>
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((v) => !v)}
        style={{
          width: "100%",
          border: "none",
          background: tone.bg,
          padding: "10px 12px",
          display: "flex",
          alignItems: "flex-start",
          gap: 10,
          textAlign: "left",
          cursor: hasDetails ? "pointer" : "default",
        }}
      >
        <div style={{
          width: 28,
          height: 28,
          borderRadius: 8,
          background: "#fff",
          border: `1px solid ${tone.border}`,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          color: tone.fg,
          flexShrink: 0,
        }}>
          <Icon size={13} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11.5, fontWeight: 900, color: tone.fg, textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {label}
            </span>
            <span style={{ fontSize: 12, color: "#334155", fontWeight: 800, fontVariantNumeric: "tabular-nums", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <Clock size={11} /> {formatLifecycleDate(activity.created_at)}
            </span>
            {actor ? (
              <span style={{ fontSize: 11.5, color: "#64748b", display: "inline-flex", alignItems: "center", gap: 4, minWidth: 0 }}>
                <User size={11} /> <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{actor}</span>
              </span>
            ) : null}
          </div>
          {subject ? (
            <div style={{ fontSize: 12.5, color: "#0f172a", fontWeight: 800, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {subject}
            </div>
          ) : null}
          {!expanded && previewLine ? (
            <div style={{ fontSize: 12, color: "#475569", marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {previewLine}{preview && preview.length > 160 ? "..." : ""}
            </div>
          ) : null}
        </div>
        {hasDetails ? (
          expanded ? <ChevronDown size={15} color="#64748b" /> : <ChevronRight size={15} color="#64748b" />
        ) : null}
      </button>
      {expanded ? (
        <div style={{ padding: "10px 12px 12px", display: "grid", gap: 8 }}>
          <div style={{ display: "grid", gap: 4 }}>
            <DetailRow icon={<AtSign size={12} />} label="From" value={activity.email_from || formatMetadataValue(meta.from)} />
            <DetailRow icon={<AtSign size={12} />} label="To" value={activity.email_to || formatMetadataValue(meta.to)} />
            <DetailRow icon={<AtSign size={12} />} label="Cc" value={activity.email_cc || formatMetadataValue(meta.cc)} />
            <DetailRow icon={<Clock size={12} />} label="Duration" value={duration} />
            <DetailRow icon={<Info size={12} />} label="Source" value={activity.source} />
            <DetailRow icon={<Info size={12} />} label="Call ID" value={activity.call_id} />
            <DetailRow
              icon={<Headphones size={12} />}
              label="Recording"
              value={activity.recording_url ? (
                <a href={activity.recording_url} target="_blank" rel="noreferrer" style={{ color: "#1d4ed8", display: "inline-flex", alignItems: "center", gap: 4, fontWeight: 800, textDecoration: "none" }}>
                  Listen <ExternalLink size={11} />
                </a>
              ) : null}
            />
            {rows.map(([key, value]) => (
              <DetailRow key={key} icon={<Info size={12} />} label={key} value={value} />
            ))}
          </div>
          {activity.ai_summary ? (
            <div style={{ padding: "8px 10px", background: "#faf5ff", borderRadius: 9, border: "1px solid #e9d5ff" }}>
              <div style={{ fontSize: 10.5, fontWeight: 900, color: "#7c3aed", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
                AI Summary
              </div>
              <div style={{ fontSize: 12, color: "#334155", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{activity.ai_summary}</div>
            </div>
          ) : null}
          {recordingId ? <RecordingDetails recordingId={recordingId} /> : null}
          {activity.content ? (
            <div style={{ padding: "10px 12px", background: "#f8fafc", borderRadius: 9, border: "1px solid #e2e8f0", maxHeight: 220, overflowY: "auto" }}>
              <div style={{ fontSize: 10.5, fontWeight: 900, color: "#475569", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>
                {activity.medium === "email" || activity.email_subject ? "Email body / notes" : "Notes"}
              </div>
              <div style={{ fontSize: 12.5, color: "#1e293b", lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{activity.content}</div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
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

  const emailOpens = activities.filter((a) => activityEventType(a) === "email_opened");
  const emailReplies = activities.filter((a) => activityEventType(a) === "reply_received");
  const emailSends = activities.filter((a) => activityEventType(a) === "email_sent");
  const calls = activities.filter((a) => (a.type || "").toLowerCase() === "call");
  const latestOpen = emailOpens[0]?.created_at || null;
  const statCard = (label: string, value: string | number, sub?: string | null) => (
    <div style={{ padding: "10px 12px", borderRadius: 12, border: "1px solid #dbe7f3", background: "#f8fbff", minWidth: 0 }}>
      <div style={{ fontSize: 10.5, color: "#64748b", fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</div>
      <div style={{ fontSize: 18, color: "#0f172a", fontWeight: 900, marginTop: 2, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {sub ? <div style={{ fontSize: 11.5, color: "#64748b", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</div> : null}
    </div>
  );

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 220, display: "flex", justifyContent: "flex-end" }}>
      <div style={{ flex: 1, background: "rgba(10,20,40,0.35)" }} onClick={onClose} />
      <div style={{ width: 860, maxWidth: "100vw", background: "#fff", borderLeft: "1px solid #d5e3ef", boxShadow: "-24px 0 48px rgba(14,38,66,0.16)", display: "flex", flexDirection: "column", overflowY: "auto" }}>
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

              {!activitiesLoading && activities.length > 0 && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10, marginBottom: 16 }}>
                  {statCard("Email opens", emailOpens.length, latestOpen ? `Last ${formatLifecycleDate(latestOpen)}` : "No opens yet")}
                  {statCard("Emails sent", emailSends.length, emailReplies.length ? `${emailReplies.length} replies` : "No replies yet")}
                  {statCard("Calls", calls.length, calls[0]?.created_at ? `Last ${formatLifecycleDate(calls[0].created_at)}` : "No calls yet")}
                  {statCard("Total activity", activities.length, "All tracked events")}
                </div>
              )}

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
                  <div style={{ display: "grid", gap: 8 }}>
                    {activities.map((a) => (
                      <ActivityDetailCard key={a.id} activity={a} />
                    ))}
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

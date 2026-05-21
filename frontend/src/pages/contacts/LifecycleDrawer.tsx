import { X, AlertTriangle, ArrowRight } from "lucide-react";
import type { SequenceLifecycle } from "../../lib/api";
import { formatLifecycleDate, LifecycleStepRow } from "./LifecycleStepRow";

export function LifecycleDrawer({
  contactId, detail, loading, onClose, onOpenOutreach,
}: {
  contactId: string | null;
  detail: SequenceLifecycle | null;
  loading: boolean;
  onClose: () => void;
  onOpenOutreach?: () => void;
}) {
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
      <div style={{ width: 520, maxWidth: "100vw", background: "#fff", borderLeft: "1px solid #d5e3ef", boxShadow: "-24px 0 48px rgba(14,38,66,0.16)", display: "flex", flexDirection: "column", overflowY: "auto" }}>
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
                <div style={{ fontSize: 13, color: "#7a96b0", fontStyle: "italic" }}>
                  No steps defined on this contact's sequence plan yet.
                </div>
              ) : (
                <div>
                  {detail.steps.map((step) => (
                    <LifecycleStepRow key={step.index} step={step} />
                  ))}
                </div>
              )}

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

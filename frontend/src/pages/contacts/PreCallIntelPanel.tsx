import React from "react";
import type { Contact } from "../../types";
import type { PreCallBrief } from "../../lib/api";

export function PreCallIntelPanel({
  contact,
  brief,
  loading,
}: {
  contact: Contact;
  brief: PreCallBrief | null;
  loading: boolean;
}) {
  const SectionHeader = ({ children }: { children: React.ReactNode }) => (
    <div style={{ fontSize: 10, fontWeight: 800, color: "#546679", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
      {children}
    </div>
  );

  const emailAgeLabel = (iso: string) => {
    const sent = new Date(iso).getTime();
    const hrs = (Date.now() - sent) / 3_600_000;
    if (hrs < 1) return "just now";
    if (hrs < 24) return `${Math.round(hrs)}h ago`;
    const days = Math.round(hrs / 24);
    return `${days}d ago`;
  };

  const signalDotColor = (type: string) => {
    switch (type) {
      case "funding": return "#f59e0b";
      case "jobs": return "#10b981";
      case "pr": return "#3b82f6";
      case "news": return "#64748b";
      case "review": return "#8b5cf6";
      case "linkedin": return "#ec4899";
      default: return "#94a3b8";
    }
  };

  const titleLine = [
    contact.title,
    brief?.company?.name,
    brief?.company?.industry,
  ].filter(Boolean).join(" · ");

  return (
    <div style={{ padding: "16px 22px", borderBottom: "1px solid #e8eef5", display: "grid", gap: 16 }}>
      {/* Identity strip: who are we calling? */}
      <div style={{ display: "grid", gap: 4 }}>
        {titleLine && (
          <div style={{ fontSize: 12, color: "#2c4a63", fontWeight: 600 }}>{titleLine}</div>
        )}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: 12, color: "#546679" }}>
          {contact.phone && (
            <span style={{ color: "#175089", fontWeight: 600 }}>
              {contact.phone}
            </span>
          )}
          {contact.timezone && <span>TZ: {contact.timezone}</span>}
          {contact.outreach_lane && (
            <span style={{ padding: "1px 8px", borderRadius: 999, background: "#eef2f7", color: "#24364b", fontWeight: 600 }}>
              {contact.outreach_lane.replace(/_/g, " ")}
            </span>
          )}
          {contact.sequence_status && (
            <span style={{ padding: "1px 8px", borderRadius: 999, background: "#eaf3ff", color: "#1a56db", fontWeight: 600 }}>
              {contact.sequence_status.replace(/_/g, " ")}
            </span>
          )}
        </div>
      </div>

      {loading && (
        <div style={{ fontSize: 12, color: "#7a96b0", fontStyle: "italic" }}>Loading pre-call brief…</div>
      )}

      {/* Last email sent — most decision-relevant signal. If the prospect
          opened it, the rep should reference it. If not, start fresh. */}
      {brief?.last_email_sent && (
        <div style={{ background: brief.last_email_sent.opened ? "#ecfdf5" : "#f8fafc", border: `1px solid ${brief.last_email_sent.opened ? "#a7f3d0" : "#dbe4ef"}`, borderRadius: 10, padding: "10px 12px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
            <SectionHeader>
              Last email · {emailAgeLabel(brief.last_email_sent.sent_at)}
            </SectionHeader>
            <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
              {brief.last_email_sent.opened && (
                <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: "#059669", color: "#fff" }}>OPENED</span>
              )}
              {brief.last_email_sent.clicked && (
                <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: "#1d4ed8", color: "#fff" }}>CLICKED</span>
              )}
            </div>
          </div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f2744", marginBottom: 4 }}>
            {brief.last_email_sent.subject}
          </div>
          {brief.last_email_sent.snippet && (
            <div style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
              {brief.last_email_sent.snippet}
            </div>
          )}
        </div>
      )}

      {/* Recent buying signals — warm the call with timely context */}
      {brief?.recent_signals && brief.recent_signals.length > 0 && (
        <div>
          <SectionHeader>Recent signals</SectionHeader>
          <div style={{ display: "grid", gap: 6 }}>
            {brief.recent_signals.map((s, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12.5, color: "#2c4a63", lineHeight: 1.5 }}>
                <span style={{ marginTop: 6, width: 6, height: 6, borderRadius: 999, background: signalDotColor(s.type), flexShrink: 0 }} />
                <span>
                  <span style={{ fontWeight: 600 }}>{s.title}</span>
                  {s.summary && <span style={{ color: "#546679" }}> — {s.summary}</span>}
                  {s.url && (
                    <>
                      {" "}
                      <a href={s.url} target="_blank" rel="noopener noreferrer" style={{ color: "#1a56db", fontSize: 11 }}>open</a>
                    </>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Conversation starter & personalization — curated human signals
          that were written during account sourcing. Highest-trust content. */}
      {(brief?.conversation_starter || brief?.personalization_notes) && (
        <div style={{ display: "grid", gap: 8 }}>
          {brief.conversation_starter && (
            <div style={{ fontSize: 13, color: "#2c4a63", background: "#f0f6ff", border: "1px solid #c8daf0", borderRadius: 10, padding: "10px 12px", lineHeight: 1.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 11, textTransform: "uppercase", color: "#546679", letterSpacing: "0.05em" }}>Conversation starter</div>
              {brief.conversation_starter}
            </div>
          )}
          {brief.personalization_notes && (
            <div style={{ fontSize: 13, color: "#2c4a63", background: "#f7fbff", border: "1px solid #dbe6f2", borderRadius: 10, padding: "10px 12px", lineHeight: 1.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 11, textTransform: "uppercase", color: "#546679", letterSpacing: "0.05em" }}>Personalization notes</div>
              {brief.personalization_notes}
            </div>
          )}
        </div>
      )}

      {/* Talking points — always shown (persona fallback if not populated)
          so the rep is never staring at an empty sidebar. */}
      {brief?.talking_points && brief.talking_points.length > 0 && (
        <div>
          <SectionHeader>Talking points</SectionHeader>
          <ol style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
            {brief.talking_points.slice(0, 4).map((pt, i) => (
              <li key={i} style={{ fontSize: 12.5, color: "#2c4a63", lineHeight: 1.5 }}>{pt}</li>
            ))}
          </ol>
        </div>
      )}

      {/* Objection playbook — collapsed by default since we don't want to
          distract the rep before the call. Available when needed. */}
      {brief?.objection_playbook && brief.objection_playbook.length > 0 && (
        <details style={{ background: "#fef9e7", border: "1px solid #fde68a", borderRadius: 10, padding: "8px 12px" }}>
          <summary style={{ cursor: "pointer", fontSize: 11, fontWeight: 800, color: "#92400e", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Objection handles ({brief.objection_playbook.length})
          </summary>
          <div style={{ marginTop: 8, display: "grid", gap: 10 }}>
            {brief.objection_playbook.map((ob, i) => (
              <div key={i}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#7c2d12", marginBottom: 3 }}>{ob.objection}</div>
                <div style={{ fontSize: 12, color: "#3d5268", lineHeight: 1.5 }}>{ob.response}</div>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Recent activity removed — shown on the right column only */}

      {!loading && !brief && (
        <div style={{ fontSize: 13, color: "#7a96b0", fontStyle: "italic" }}>
          No pre-call brief available. Check the contact has data.
        </div>
      )}
    </div>
  );
}

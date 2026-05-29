import { useEffect, useState } from "react";
import {
  Mail, Phone, Linkedin, CalendarDays, FileText, MessageSquare,
  ArrowRightLeft, Sparkles, Edit3, Link2, RefreshCw, ExternalLink,
} from "lucide-react";
import { timelineApi, type TimelineEvent } from "../lib/api";
import { formatDate } from "../lib/utils";

interface Props {
  scope: { type: "contact" | "deal"; id: string };
  limit?: number;
  emptyMessage?: string;
}

type Filter = "all" | "email" | "call" | "meeting" | "linkedin" | "notes" | "system";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "email", label: "Email" },
  { value: "call", label: "Calls" },
  { value: "meeting", label: "Meetings" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "notes", label: "Notes" },
  { value: "system", label: "System" },
];

function matchesFilter(kind: string, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "notes") return kind === "note" || kind === "comment" || kind === "import_note";
  if (filter === "system") {
    return kind === "field_change" || kind === "stage_change" || kind === "deal_created" || kind === "contact_linked";
  }
  if (filter === "call") return kind === "call";
  if (filter === "email") return kind === "email";
  if (filter === "meeting") return kind === "meeting" || kind === "transcript";
  if (filter === "linkedin") return kind === "linkedin";
  return false;
}

function iconFor(kind: string): { Icon: typeof Mail; color: string } {
  switch (kind) {
    case "email": return { Icon: Mail, color: "#ff6b35" };
    case "call": return { Icon: Phone, color: "#16a34a" };
    case "linkedin": return { Icon: Linkedin, color: "#0a66c2" };
    case "meeting": return { Icon: CalendarDays, color: "#7c3aed" };
    case "transcript": return { Icon: FileText, color: "#0891b2" };
    case "note":
    case "comment":
    case "import_note": return { Icon: MessageSquare, color: "#64748b" };
    case "field_change": return { Icon: Edit3, color: "#94a3b8" };
    case "stage_change": return { Icon: ArrowRightLeft, color: "#f59e0b" };
    case "deal_created": return { Icon: Sparkles, color: "#22c55e" };
    case "contact_linked": return { Icon: Link2, color: "#64748b" };
    default: return { Icon: MessageSquare, color: "#94a3b8" };
  }
}

export default function UnifiedTimeline({ scope, limit, emptyMessage }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const items = scope.type === "contact"
        ? await timelineApi.forContact(scope.id, limit)
        : await timelineApi.forDeal(scope.id, limit);
      setEvents(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load timeline");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.type, scope.id, limit]);

  const filtered = events.filter((e) => matchesFilter(e.kind, filter));

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14, gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {FILTERS.map((f) => {
            const active = filter === f.value;
            return (
              <button
                key={f.value}
                onClick={() => setFilter(f.value)}
                style={{
                  padding: "5px 11px", borderRadius: 999, fontSize: 12, fontWeight: 600,
                  border: active ? "1px solid #0f2744" : "1px solid #d5e3ef",
                  background: active ? "#0f2744" : "#fff",
                  color: active ? "#fff" : "#4d6178", cursor: "pointer",
                }}
              >
                {f.label}
              </button>
            );
          })}
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "5px 10px", borderRadius: 8, border: "1px solid #d5e3ef",
            background: "#fff", color: "#4d6178", fontSize: 12, fontWeight: 600,
            cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.7 : 1,
          }}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {error && (
        <div style={{ color: "#ef4444", fontSize: 13, marginBottom: 10 }}>{error}</div>
      )}

      {loading && !events.length ? (
        <p style={{ fontSize: 13, color: "#6f8399" }}>Loading timeline…</p>
      ) : filtered.length === 0 ? (
        <p style={{ fontSize: 13, color: "#6f8399" }}>
          {emptyMessage ?? (filter === "all" ? "No activity yet." : `No ${filter} events.`)}
        </p>
      ) : (
        <ol style={{ listStyle: "none", padding: 0, margin: 0, position: "relative" }}>
          <div style={{
            position: "absolute", left: 15, top: 6, bottom: 6,
            width: 2, background: "linear-gradient(to bottom, #e3eaf3, #f4f7fb)", borderRadius: 2,
          }} />
          {filtered.map((event) => {
            const { Icon, color } = iconFor(event.kind);
            const payload = event.payload || {};
            const recordingUrl = typeof payload.recording_url === "string" ? payload.recording_url : null;
            const meetingUrl = typeof payload.meeting_url === "string" ? payload.meeting_url : null;
            return (
              <li key={event.id} style={{ position: "relative", paddingLeft: 44, marginBottom: 14 }}>
                <span style={{
                  position: "absolute", left: 4, top: 6, width: 24, height: 24, borderRadius: "50%",
                  background: "#fff", border: `2px solid ${color}`,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                }}>
                  <Icon size={12} color={color} />
                </span>
                <div style={{
                  border: "1px solid #e3eaf3", borderRadius: 12, background: "#fbfdff",
                  padding: "10px 14px",
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                    <p style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#31485f" }}>
                      {event.title}
                    </p>
                    <span style={{ fontSize: 11, color: "#7a8ea4", whiteSpace: "nowrap" }}>
                      {event.occurred_at ? formatDate(event.occurred_at) : "—"}
                    </span>
                  </div>
                  {event.subtitle && (
                    <p style={{ margin: "6px 0 0", fontSize: 13, color: "#4d6178", lineHeight: 1.45 }}>
                      {event.subtitle}
                    </p>
                  )}
                  {(recordingUrl || meetingUrl) && (
                    <div style={{ marginTop: 8, display: "flex", gap: 10 }}>
                      {recordingUrl && (
                        <a
                          href={recordingUrl} target="_blank" rel="noopener noreferrer"
                          style={{ fontSize: 12, color: "#0a66c2", display: "inline-flex", alignItems: "center", gap: 4 }}
                        >
                          <ExternalLink size={11} /> Recording
                        </a>
                      )}
                      {meetingUrl && (
                        <a
                          href={meetingUrl} target="_blank" rel="noopener noreferrer"
                          style={{ fontSize: 12, color: "#7c3aed", display: "inline-flex", alignItems: "center", gap: 4 }}
                        >
                          <ExternalLink size={11} /> Meeting link
                        </a>
                      )}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

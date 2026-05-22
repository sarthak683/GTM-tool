import { useEffect, useMemo, useState } from "react";
import { analyticsApi, type OutreachAnalyticsResponse } from "../../lib/api";

const WINDOW_OPTIONS = [
  { value: 30, label: "30d" },
  { value: 60, label: "60d" },
  { value: 90, label: "90d" },
  { value: 180, label: "180d" },
];

function pct(value: number): string {
  if (!Number.isFinite(value)) return "0%";
  return `${(value * 100).toFixed(1)}%`;
}

interface FunnelStep {
  key: string;
  label: string;
  value: number;
  color: string;
}

export default function OutreachAnalyticsTab() {
  const [windowDays, setWindowDays] = useState<number>(90);
  const [data, setData] = useState<OutreachAnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    analyticsApi
      .outreach(windowDays)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load outreach analytics"))
      .finally(() => setLoading(false));
  }, [windowDays]);

  const funnel = data?.funnel;
  const funnelSteps: FunnelStep[] = useMemo(
    () => funnel
      ? [
          { key: "contacts", label: "In play", value: funnel.contacts_in_play, color: "#94a3b8" },
          { key: "sent", label: "Sent", value: funnel.sent, color: "#0f2744" },
          { key: "opened", label: "Opened", value: funnel.opened, color: "#175089" },
          { key: "clicked", label: "Clicked", value: funnel.clicked, color: "#0a66c2" },
          { key: "interested", label: "Interested", value: funnel.interested, color: "#16a34a" },
          { key: "booked", label: "Meeting booked", value: funnel.meeting_booked, color: "#7c3aed" },
        ]
      : [],
    [funnel]
  );
  const maxFunnelValue = funnelSteps.length > 0 ? Math.max(...funnelSteps.map((s) => s.value), 1) : 1;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 800, color: "#1f3144" }}>Outreach performance</h3>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "#66788d" }}>
            Sequence funnel, per-rep effectiveness, and subject-line performance.
          </p>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {WINDOW_OPTIONS.map((w) => {
            const active = w.value === windowDays;
            return (
              <button
                key={w.value}
                onClick={() => setWindowDays(w.value)}
                style={{
                  padding: "6px 14px", borderRadius: 999, fontSize: 12, fontWeight: 700,
                  border: active ? "1px solid #0f2744" : "1px solid #d5e3ef",
                  background: active ? "#0f2744" : "#fff",
                  color: active ? "#fff" : "#4d6178", cursor: "pointer",
                }}
              >
                {w.label}
              </button>
            );
          })}
        </div>
      </div>

      {error && (
        <div className="crm-panel" style={{ padding: 18, border: "1px solid #f0d2d2", background: "#fff7f7", color: "#b45454" }}>{error}</div>
      )}

      {loading && !data ? (
        <div className="crm-panel" style={{ padding: 28, color: "#6f8095" }}>Loading outreach analytics…</div>
      ) : data ? (
        <>
          {/* Headline KPIs */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
            <Kpi label="Launched sequences" value={String(data.funnel.launched_sequences)} />
            <Kpi label="Contacts in play" value={String(data.funnel.contacts_in_play)} />
            <Kpi label="Open rate" value={pct(data.funnel.open_rate)} accent="#175089" />
            <Kpi label="Reply rate" value={pct(data.funnel.reply_rate)} accent="#16a34a" />
            <Kpi label="Booking rate" value={pct(data.funnel.booking_rate)} accent="#7c3aed" />
            <Kpi label="Bounces" value={String(data.funnel.bounced)} accent="#dc2626" />
          </div>

          {/* Funnel */}
          <section className="crm-panel" style={{ padding: 20 }}>
            <h4 style={{ margin: 0, marginBottom: 14, fontSize: 14, fontWeight: 800, color: "#1f3144" }}>Funnel</h4>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {funnelSteps.map((step) => {
                const width = `${Math.max(4, (step.value / maxFunnelValue) * 100)}%`;
                return (
                  <div key={step.key} style={{ display: "grid", gridTemplateColumns: "140px 1fr 90px", alignItems: "center", gap: 12 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: "#4d6178" }}>{step.label}</span>
                    <div style={{ background: "#f3f6fb", borderRadius: 999, height: 14, overflow: "hidden" }}>
                      <div style={{ background: step.color, width, height: "100%", borderRadius: 999, transition: "width 0.2s" }} />
                    </div>
                    <span style={{ fontSize: 12, fontWeight: 700, color: "#1f3144", textAlign: "right" }}>
                      {step.value.toLocaleString()}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Per-rep leaderboard */}
          <section className="crm-panel" style={{ padding: 20 }}>
            <h4 style={{ margin: 0, marginBottom: 14, fontSize: 14, fontWeight: 800, color: "#1f3144" }}>Per-rep effectiveness</h4>
            {data.per_rep.length === 0 ? (
              <p style={{ fontSize: 13, color: "#7c86a6" }}>No rep activity in this window.</p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "#7c86a6", borderBottom: "1px solid #e7eaf5" }}>
                      <th style={{ padding: "8px 10px" }}>Rep</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Contacts</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Opened</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Interested</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Booked</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Open rate</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Reply rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.per_rep.map((row) => (
                      <tr key={row.rep_email} style={{ borderBottom: "1px solid #f0f2f8" }}>
                        <td style={{ padding: "8px 10px", fontWeight: 700, color: "#1f3144" }}>{row.rep_email}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>{row.contacts}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>{row.opened}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right", color: "#16a34a", fontWeight: 700 }}>{row.interested}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right", color: "#7c3aed", fontWeight: 700 }}>{row.booked}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>{pct(row.open_rate)}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700 }}>{pct(row.reply_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Top sequences */}
          <section className="crm-panel" style={{ padding: 20 }}>
            <h4 style={{ margin: 0, marginBottom: 14, fontSize: 14, fontWeight: 800, color: "#1f3144" }}>Top sequences</h4>
            {data.sequences.length === 0 ? (
              <p style={{ fontSize: 13, color: "#7c86a6" }}>No launched sequences in this window.</p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "#7c86a6", borderBottom: "1px solid #e7eaf5" }}>
                      <th style={{ padding: "8px 10px" }}>Subject</th>
                      <th style={{ padding: "8px 10px" }}>Persona</th>
                      <th style={{ padding: "8px 10px" }}>Status</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Contacts</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Open rate</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Reply rate</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Booked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sequences.map((row) => (
                      <tr key={row.sequence_id} style={{ borderBottom: "1px solid #f0f2f8" }}>
                        <td style={{ padding: "8px 10px", color: "#1f3144", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {row.subject || "(no subject)"}
                        </td>
                        <td style={{ padding: "8px 10px", color: "#4d6178" }}>{row.persona || "—"}</td>
                        <td style={{ padding: "8px 10px", color: "#4d6178" }}>{row.status || "—"}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>{row.contacts}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>{pct(row.open_rate)}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700 }}>{pct(row.reply_rate)}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right", color: "#7c3aed", fontWeight: 700 }}>{row.booked}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Subject-line performance */}
          <section className="crm-panel" style={{ padding: 20 }}>
            <h4 style={{ margin: 0, marginBottom: 14, fontSize: 14, fontWeight: 800, color: "#1f3144" }}>Top subject lines</h4>
            {data.subjects.length === 0 ? (
              <p style={{ fontSize: 13, color: "#7c86a6" }}>No subject-line data in this window.</p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "#7c86a6", borderBottom: "1px solid #e7eaf5" }}>
                      <th style={{ padding: "8px 10px" }}>Subject</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Sends</th>
                      <th style={{ padding: "8px 10px", textAlign: "right" }}>Distinct contacts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.subjects.map((row, i) => (
                      <tr key={`${row.subject}-${i}`} style={{ borderBottom: "1px solid #f0f2f8" }}>
                        <td style={{ padding: "8px 10px", color: "#1f3144", maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.subject}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>{row.sends}</td>
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>{row.distinct_contacts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}

function Kpi({ label, value, accent = "#0f2744" }: { label: string; value: string; accent?: string }) {
  return (
    <div className="crm-panel" style={{ padding: 16 }}>
      <div style={{ fontSize: 11, color: "#7c86a6", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 800, color: accent }}>{value}</div>
    </div>
  );
}

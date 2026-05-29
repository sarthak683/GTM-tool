/**
 * Performance Analytics tab surfaces — Scorecard / Funnel / Risk / Forecast /
 * Rankings / Targets. All six live inside the Sales Analytics page as tabs.
 *
 * Visual language matches SalesAnalytics.tsx: inline styles, warm/cool pastel
 * palette, 18–22px radii, 11px uppercase labels, generous whitespace.
 * Charts via recharts (already a dep).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  Cell,
  PolarAngleAxis,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  CheckCircle2,
  Gauge,
  Layers,
  LoaderCircle,
  Medal,
  Save,
  Target,
  TrendingDown,
  TrendingUp,
  Trophy,
  Users as UsersIcon,
} from "lucide-react";
import {
  performanceApi,
  type AnalyticsSettings,
  type DealHealthResponse,
  type ForecastResponse,
  type FunnelResponse,
  type LeaderboardResponse,
  type RepSummary,
  type ScorecardBlock,
  type ScorecardMetric,
  type ScorecardResponse,
} from "../../lib/api";
import { useAuth } from "../../lib/AuthContext";

// ── Shared visual primitives ───────────────────────────────────────────────

const PALETTE = {
  text: "#1f3144",
  muted: "#66788d",
  subtle: "#8b9db2",
  hairline: "#e3ebf4",
  panel: "#ffffff",
  tintBlue: "#eef4ff",
  tintBlueBorder: "#d7e2fb",
  tintBlueText: "#3555c4",
  tintCoral: "#fff3ec",
  tintCoralBorder: "#ffd5c3",
  tintCoralText: "#b85024",
  tintGreen: "#eafbf1",
  tintGreenBorder: "#cdecd9",
  tintGreenText: "#1f8356",
  tintAmber: "#fff6e5",
  tintAmberBorder: "#ffe1ad",
  tintAmberText: "#b07019",
  tintRed: "#fdeeee",
  tintRedBorder: "#f4cfd0",
  tintRedText: "#b94343",
};

const RAG_TINT: Record<string, { bg: string; border: string; text: string; dot: string }> = {
  green: { bg: PALETTE.tintGreen, border: PALETTE.tintGreenBorder, text: PALETTE.tintGreenText, dot: "#2b8a5d" },
  amber: { bg: PALETTE.tintAmber, border: PALETTE.tintAmberBorder, text: PALETTE.tintAmberText, dot: "#d08e22" },
  red: { bg: PALETTE.tintRed, border: PALETTE.tintRedBorder, text: PALETTE.tintRedText, dot: "#c14f4f" },
};

function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      className="crm-panel"
      style={{
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 18,
      }}
    >
      {(title || action) && (
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {title && <h2 style={{ margin: 0, fontSize: 18, fontWeight: 800, color: PALETTE.text, letterSpacing: "-0.01em" }}>{title}</h2>}
            {subtitle && <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: PALETTE.muted, maxWidth: 680 }}>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function Pill({
  tone,
  children,
}: {
  tone: "blue" | "coral" | "green" | "amber" | "red" | "neutral";
  children: React.ReactNode;
}) {
  const map = {
    blue: { bg: PALETTE.tintBlue, border: PALETTE.tintBlueBorder, color: PALETTE.tintBlueText },
    coral: { bg: PALETTE.tintCoral, border: PALETTE.tintCoralBorder, color: PALETTE.tintCoralText },
    green: { bg: PALETTE.tintGreen, border: PALETTE.tintGreenBorder, color: PALETTE.tintGreenText },
    amber: { bg: PALETTE.tintAmber, border: PALETTE.tintAmberBorder, color: PALETTE.tintAmberText },
    red: { bg: PALETTE.tintRed, border: PALETTE.tintRedBorder, color: PALETTE.tintRedText },
    neutral: { bg: "#f7f9fc", border: PALETTE.hairline, color: PALETTE.muted },
  }[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "6px 11px",
        borderRadius: 999,
        background: map.bg,
        border: `1px solid ${map.border}`,
        color: map.color,
        fontSize: 11,
        fontWeight: 800,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}
    >
      {children}
    </span>
  );
}

function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (next: T) => void;
  options: Array<{ value: T; label: string }>;
}) {
  return (
    <div
      style={{
        display: "inline-flex",
        padding: 4,
        borderRadius: 999,
        background: "#f4f7fb",
        border: `1px solid ${PALETTE.hairline}`,
        gap: 2,
      }}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            style={{
              height: 32,
              padding: "0 14px",
              borderRadius: 999,
              border: "none",
              background: active ? "#fff" : "transparent",
              color: active ? PALETTE.text : PALETTE.muted,
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
              boxShadow: active ? "0 2px 8px rgba(32,53,84,0.08)" : "none",
              transition: "background 0.12s",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function RepPicker({
  reps,
  value,
  onChange,
}: {
  reps: RepSummary[];
  value: string | undefined;
  onChange: (v: string | undefined) => void;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || undefined)}
      style={{
        height: 36,
        padding: "0 32px 0 12px",
        borderRadius: 10,
        border: `1px solid ${PALETTE.hairline}`,
        background: "#fff",
        color: PALETTE.text,
        fontSize: 13,
        fontWeight: 600,
        cursor: "pointer",
        appearance: "none",
        backgroundImage:
          "url(\"data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3e%3cpath fill='%238b9db2' d='M0 0l5 6 5-6z'/%3e%3c/svg%3e\")",
        backgroundRepeat: "no-repeat",
        backgroundPosition: "right 12px center",
      }}
    >
      <option value="">Workspace — all reps</option>
      {reps.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name} · {r.role.toUpperCase()}
        </option>
      ))}
    </select>
  );
}

function Loading() {
  return (
    <div style={{ display: "grid", placeItems: "center", padding: 60, color: PALETTE.muted, gap: 10 }}>
      <LoaderCircle size={22} className="spin" />
      <span style={{ fontSize: 13 }}>Loading…</span>
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      style={{
        padding: 16,
        borderRadius: 14,
        border: `1px solid ${PALETTE.tintRedBorder}`,
        background: PALETTE.tintRed,
        color: PALETTE.tintRedText,
        fontSize: 13,
        fontWeight: 600,
      }}
    >
      {message}
    </div>
  );
}

// ── Formatters ─────────────────────────────────────────────────────────────

function prettyStage(s: string): string {
  return s
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function fmtMoney(v: number): string {
  if (!v) return "$0";
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function fmtScorecardValue(key: string, value: number): string {
  if (["connect_rate", "reply_rate", "demo_show_up_rate", "win_rate"].includes(key)) {
    return `${(value * 100).toFixed(1)}%`;
  }
  if (key === "avg_cycle_time_days") return value ? `${value.toFixed(1)}d` : "—";
  if (key === "touches_per_won") return value ? value.toFixed(1) : "—";
  return Number.isInteger(value) ? `${value}` : value.toFixed(1);
}

// ── Scorecard tab ──────────────────────────────────────────────────────────

function ScorecardMetricCard({ m }: { m: ScorecardMetric }) {
  const rag = m.rag ?? "neutral";
  const tint = m.rag ? RAG_TINT[m.rag] : { bg: "#fff", border: PALETTE.hairline, text: PALETTE.muted, dot: PALETTE.subtle };
  const pct = m.attainment == null ? null : Math.round(m.attainment * 100);
  return (
    <div
      style={{
        padding: 16,
        borderRadius: 16,
        background: PALETTE.panel,
        border: `1px solid ${PALETTE.hairline}`,
        display: "flex",
        flexDirection: "column",
        gap: 12,
        minHeight: 118,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", color: PALETTE.subtle, textTransform: "uppercase" }}>
          {m.label}
        </span>
        {m.rag && (
          <span
            title={m.rag.toUpperCase()}
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: tint.dot,
              flexShrink: 0,
              marginTop: 4,
            }}
          />
        )}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontSize: 26, lineHeight: 1, fontWeight: 800, color: PALETTE.text }}>
          {fmtScorecardValue(m.key, m.value)}
        </span>
        {m.target != null && (
          <span style={{ fontSize: 12, color: PALETTE.subtle }}>/ {fmtScorecardValue(m.key, m.target)}</span>
        )}
      </div>
      {pct != null && (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ flex: 1, height: 5, background: "#f1f5fa", borderRadius: 999, overflow: "hidden" }}>
            <div
              style={{
                width: `${Math.min(100, pct)}%`,
                height: "100%",
                background: tint.dot,
                transition: "width 0.3s",
              }}
            />
          </div>
          <span style={{ fontSize: 11, fontWeight: 700, color: tint.text, fontVariantNumeric: "tabular-nums", minWidth: 32, textAlign: "right" }}>
            {pct}%
          </span>
        </div>
      )}
    </div>
  );
}

function ScorecardBlockPanel({ block, icon }: { block: ScorecardBlock; icon: React.ReactNode }) {
  const title = (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
      <span
        style={{
          width: 28,
          height: 28,
          borderRadius: 8,
          background: PALETTE.tintBlue,
          color: PALETTE.tintBlueText,
          display: "grid",
          placeItems: "center",
        }}
      >
        {icon}
      </span>
      {block.title}
    </span>
  );
  return (
    <Panel title={title}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        {block.metrics.map((m) => (
          <ScorecardMetricCard key={m.key} m={m} />
        ))}
      </div>
    </Panel>
  );
}

export function ScorecardTab({ reps }: { reps: RepSummary[] }) {
  const { user, isAdmin } = useAuth();
  const [repId, setRepId] = useState<string | undefined>(isAdmin ? undefined : user?.id);
  const [period, setPeriod] = useState<"week" | "month">("week");
  const [data, setData] = useState<ScorecardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    performanceApi
      .getScorecard({ rep_id: repId, period })
      .then(setData)
      .catch((e: Error) => setError(e.message ?? "Failed to load scorecard"))
      .finally(() => setLoading(false));
  }, [repId, period]);

  const attainment = data?.header.overall_attainment ?? 0;
  const attainmentPct = Math.min(150, Math.round(attainment * 100));
  const rag = data?.header.overall_rag ?? "red";
  const tint = RAG_TINT[rag] ?? RAG_TINT.red;

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel>
        <div style={{ display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ width: 170, height: 170, flexShrink: 0, position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%">
              <RadialBarChart
                innerRadius="68%"
                outerRadius="100%"
                data={[{ name: "att", value: attainmentPct, fill: tint.dot }]}
                startAngle={90}
                endAngle={-270}
              >
                <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                <RadialBar background={{ fill: "#f1f5fa" }} dataKey="value" cornerRadius={10} />
              </RadialBarChart>
            </ResponsiveContainer>
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "grid",
                placeItems: "center",
                pointerEvents: "none",
              }}
            >
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 32, fontWeight: 800, color: PALETTE.text, lineHeight: 1 }}>{attainmentPct}%</div>
                <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: "0.1em", color: PALETTE.subtle, textTransform: "uppercase", marginTop: 4 }}>
                  Attainment
                </div>
              </div>
            </div>
          </div>
          <div style={{ flex: 1, minWidth: 260, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <Pill tone="neutral">{data?.header.period_label ?? "—"}</Pill>
              {data?.header.role && <Pill tone="blue">{data.header.role.toUpperCase()}</Pill>}
              <Pill tone={rag === "green" ? "green" : rag === "amber" ? "amber" : "red"}>
                {rag === "green" ? "On track" : rag === "amber" ? "At risk" : "Off target"}
              </Pill>
            </div>
            <h2 style={{ margin: 0, fontSize: 28, fontWeight: 800, color: PALETTE.text, letterSpacing: "-0.02em" }}>
              {data?.header.rep_name ?? "—"}
            </h2>
            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.65, color: PALETTE.muted, maxWidth: 520 }}>
              Performance scorecard summarizes activity, outcomes, and efficiency against the configured
              {period === "week" ? " weekly" : " monthly"} target. RAG badges reflect attainment against role-specific targets.
            </p>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            {isAdmin && <RepPicker reps={reps} value={repId} onChange={setRepId} />}
            <SegmentedControl
              value={period}
              onChange={setPeriod}
              options={[
                { value: "week", label: "Week" },
                { value: "month", label: "Month" },
              ]}
            />
          </div>
        </div>
      </Panel>

      {error && <ErrorBanner message={error} />}
      {loading && !data && <Loading />}

      {data && (
        <>
          <ScorecardBlockPanel block={data.activity} icon={<TrendingUp size={15} />} />
          <ScorecardBlockPanel block={data.outcomes} icon={<Target size={15} />} />
          <ScorecardBlockPanel block={data.efficiency} icon={<Gauge size={15} />} />

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 18 }}>
            <Panel title="Pipeline delta" subtitle={`New deal flow during this ${period}`}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
                <StatTile label="New opportunities" value={String(data.pipeline_delta.created_count)} hint="Deals created" />
                <StatTile label="Added ACV" value={fmtMoney(data.pipeline_delta.created_value)} hint="Sum of new deal value" />
                <StatTile label="Not Fit / Lost" value={String(data.pipeline_delta.exited_count)} hint="Deals moved out" tone="red" />
              </div>
            </Panel>

            <Panel
              title="At-risk deals"
              subtitle="Open deals over the stuck-dwell threshold for their current stage."
              action={
                <span style={{ fontSize: 12, fontWeight: 700, color: PALETTE.tintRedText }}>
                  {data.at_risk_deals.length} flagged
                </span>
              }
            >
              {data.at_risk_deals.length === 0 ? (
                <EmptyState icon={<CheckCircle2 size={22} />} text="No stuck deals. 🎉" />
              ) : (
                <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column" }}>
                  {data.at_risk_deals.slice(0, 8).map((d) => (
                    <li
                      key={d.deal_id}
                      style={{
                        padding: "12px 0",
                        borderBottom: `1px solid ${PALETTE.hairline}`,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 12,
                      }}
                    >
                      <div style={{ minWidth: 0 }}>
                        <Link
                          to={`/deals/${d.deal_id}`}
                          style={{ fontSize: 14, fontWeight: 700, color: PALETTE.text, textDecoration: "none" }}
                        >
                          {d.deal_name}
                        </Link>
                        <div style={{ fontSize: 12, color: PALETTE.muted, marginTop: 2 }}>
                          {prettyStage(d.stage)} · {d.dwell_days}d in stage · target {d.threshold_days}d
                        </div>
                      </div>
                      <Pill tone="red">+{d.over_by_days}d over</Pill>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

function StatTile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "red";
}) {
  const color = tone === "red" ? PALETTE.tintRedText : PALETTE.text;
  return (
    <div
      style={{
        padding: 16,
        borderRadius: 14,
        border: `1px solid ${PALETTE.hairline}`,
        background: "#fcfdff",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <span style={{ fontSize: 11, fontWeight: 700, color: PALETTE.subtle, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </span>
      <span style={{ fontSize: 24, fontWeight: 800, color, lineHeight: 1 }}>{value}</span>
      <span style={{ fontSize: 12, color: PALETTE.muted }}>{hint}</span>
    </div>
  );
}

function EmptyState({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div
      style={{
        padding: "32px 20px",
        display: "grid",
        placeItems: "center",
        gap: 8,
        border: `1px dashed ${PALETTE.hairline}`,
        borderRadius: 14,
        background: "#fafcff",
        color: PALETTE.muted,
        fontSize: 13,
      }}
    >
      <span style={{ color: PALETTE.tintGreenText }}>{icon}</span>
      {text}
    </div>
  );
}

// ── Funnel tab ─────────────────────────────────────────────────────────────

const ACTIVE_FUNNEL_STAGES = [
  "reprospect",
  "demo_scheduled",
  "demo_done",
  "qualified_lead",
  "poc_agreed",
  "poc_wip",
  "poc_done",
  "commercial_negotiation",
  "msa_review",
  "closed_won",
];

export function FunnelTab({ reps }: { reps: RepSummary[] }) {
  const { isAdmin } = useAuth();
  const [period, setPeriod] = useState<"week" | "month" | "quarter">("month");
  const [repId, setRepId] = useState<string | undefined>(undefined);
  const [data, setData] = useState<FunnelResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    performanceApi
      .getFunnel({ period, rep_id: repId })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [period, repId]);

  const activeFunnel = useMemo(() => {
    if (!data) return [];
    return ACTIVE_FUNNEL_STAGES.map((s) => data.funnel.find((f) => f.stage === s)).filter(Boolean) as typeof data.funnel;
  }, [data]);

  const maxCount = activeFunnel.reduce((m, r) => Math.max(m, r.deal_count), 0) || 1;

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel
        title="Pipeline & Funnel"
        subtitle="Deal volume and ACV per stage, movement within the period, and stage-to-stage conversion."
        action={
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            {isAdmin && <RepPicker reps={reps} value={repId} onChange={setRepId} />}
            <SegmentedControl
              value={period}
              onChange={setPeriod}
              options={[
                { value: "week", label: "Week" },
                { value: "month", label: "Month" },
                { value: "quarter", label: "Quarter" },
              ]}
            />
          </div>
        }
      >
        {data && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}>
            <MovementTile label="Entered" value={data.movement.entered} tone="blue" />
            <MovementTile label="Advanced" value={data.movement.advanced} tone="green" />
            <MovementTile label="Regressed" value={data.movement.regressed} tone="amber" />
            <MovementTile label="Exited" value={data.movement.exited} tone="red" />
          </div>
        )}
      </Panel>

      {error && <ErrorBanner message={error} />}
      {loading && !data && <Loading />}

      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)", gap: 18 }}>
          <Panel title="Funnel by stage" subtitle={`Active deals in each stage · ${data.period_label}`}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {activeFunnel.map((row, idx) => {
                const width = (row.deal_count / maxCount) * 100;
                // Graduated color — earlier stages cooler, later warmer.
                const progress = idx / (activeFunnel.length - 1 || 1);
                const hue = 215 - progress * 70; // 215 (blue) → 145 (green)
                const color = `hsl(${hue}, 55%, 45%)`;
                return (
                  <div key={row.stage} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ width: 170, fontSize: 12, fontWeight: 700, color: PALETTE.text }}>
                      {prettyStage(row.stage)}
                    </div>
                    <div style={{ flex: 1, height: 34, background: "#f3f6fa", borderRadius: 8, position: "relative", overflow: "hidden" }}>
                      <div
                        style={{
                          width: `${width}%`,
                          height: "100%",
                          background: color,
                          borderRadius: 8,
                          transition: "width 0.4s",
                        }}
                      />
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          padding: "0 12px",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          fontSize: 12,
                          fontWeight: 700,
                        }}
                      >
                        <span style={{ color: width > 30 ? "#fff" : PALETTE.text }}>
                          {row.deal_count} deals
                        </span>
                        <span style={{ color: PALETTE.text, fontVariantNumeric: "tabular-nums" }}>
                          {fmtMoney(row.total_value)}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </Panel>

          <Panel title="Stage conversion" subtitle={`For deals that entered the stage this ${period}`}>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {data.conversion.map((row) => {
                const pct = Math.round(row.conv_rate * 100);
                const tone = pct >= 50 ? "green" : pct >= 25 ? "amber" : "red";
                const tint = RAG_TINT[tone];
                return (
                  <div
                    key={`${row.from_stage}-${row.to_stage}`}
                    style={{
                      padding: "12px 0",
                      borderBottom: `1px solid ${PALETTE.hairline}`,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 10,
                    }}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                      <span style={{ fontSize: 12, color: PALETTE.muted, fontWeight: 600 }}>
                        {prettyStage(row.from_stage)}
                      </span>
                      <span style={{ fontSize: 13, color: PALETTE.text, fontWeight: 700 }}>
                        → {prettyStage(row.to_stage)}
                      </span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <div style={{ textAlign: "right" }}>
                        <div style={{ fontSize: 10, color: PALETTE.subtle, textTransform: "uppercase", fontWeight: 700, letterSpacing: "0.05em" }}>Deals</div>
                        <div style={{ fontSize: 14, color: PALETTE.text, fontVariantNumeric: "tabular-nums", fontWeight: 700 }}>{row.deals}</div>
                      </div>
                      <div style={{ textAlign: "right" }}>
                        <div style={{ fontSize: 10, color: PALETTE.subtle, textTransform: "uppercase", fontWeight: 700, letterSpacing: "0.05em" }}>Median</div>
                        <div style={{ fontSize: 14, color: PALETTE.text, fontVariantNumeric: "tabular-nums", fontWeight: 700 }}>
                          {row.median_days != null ? `${row.median_days.toFixed(1)}d` : "—"}
                        </div>
                      </div>
                      <div
                        style={{
                          minWidth: 60,
                          padding: "8px 12px",
                          borderRadius: 10,
                          background: tint.bg,
                          border: `1px solid ${tint.border}`,
                          color: tint.text,
                          fontSize: 14,
                          fontWeight: 800,
                          textAlign: "center",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {pct}%
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            <p style={{ margin: 0, fontSize: 12, color: PALETTE.subtle }}>
              Conversion reflects deals that entered the "from" stage during this period. Historical transitions
              before the stage-history backfill are not counted.
            </p>
          </Panel>
        </div>
      )}
    </div>
  );
}

function MovementTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "blue" | "green" | "amber" | "red";
}) {
  const tint =
    tone === "blue" ? { bg: PALETTE.tintBlue, border: PALETTE.tintBlueBorder, text: PALETTE.tintBlueText } : RAG_TINT[tone === "green" ? "green" : tone === "amber" ? "amber" : "red"];
  return (
    <div
      style={{
        padding: 16,
        borderRadius: 14,
        background: tint.bg,
        border: `1px solid ${tint.border}`,
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: "0.05em", color: tint.text, textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 28, fontWeight: 800, color: PALETTE.text, marginTop: 6, lineHeight: 1 }}>{value}</div>
    </div>
  );
}

// ── Risk (Deal Health) tab ─────────────────────────────────────────────────

export function RiskTab({ reps }: { reps: RepSummary[] }) {
  const { isAdmin } = useAuth();
  const [repId, setRepId] = useState<string | undefined>(undefined);
  const [data, setData] = useState<DealHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    performanceApi
      .getDealHealth({ rep_id: repId })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [repId]);

  const byStageSorted = useMemo(
    () => (data ? Object.entries(data.by_stage).sort((a, b) => b[1] - a[1]) : []),
    [data],
  );

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel
        title="Deal Health — stuck deals"
        subtitle="Open deals currently over the configured dwell threshold for their stage. Edit thresholds in Targets."
        action={
          <div style={{ display: "flex", gap: 10 }}>
            {isAdmin && <RepPicker reps={reps} value={repId} onChange={setRepId} />}
          </div>
        }
      >
        {data && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
            <div
              style={{
                padding: 18,
                borderRadius: 16,
                background: `linear-gradient(135deg, ${PALETTE.tintRed} 0%, #fff 100%)`,
                border: `1px solid ${PALETTE.tintRedBorder}`,
              }}
            >
              <div style={{ fontSize: 11, fontWeight: 800, color: PALETTE.tintRedText, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Total stuck
              </div>
              <div style={{ fontSize: 34, fontWeight: 800, color: PALETTE.text, marginTop: 6, lineHeight: 1 }}>
                {data.total_stuck}
              </div>
              <div style={{ fontSize: 12, color: PALETTE.muted, marginTop: 6 }}>
                {data.total_stuck === 0 ? "No deals stuck." : "Needs escalation attention."}
              </div>
            </div>
            {byStageSorted.slice(0, 4).map(([stage, count]) => (
              <div
                key={stage}
                style={{
                  padding: 18,
                  borderRadius: 16,
                  background: "#fff",
                  border: `1px solid ${PALETTE.hairline}`,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 800, color: PALETTE.subtle, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  {prettyStage(stage)}
                </div>
                <div style={{ fontSize: 34, fontWeight: 800, color: PALETTE.text, marginTop: 6, lineHeight: 1 }}>{count}</div>
                <div style={{ fontSize: 12, color: PALETTE.muted, marginTop: 6 }}>
                  {count === 1 ? "deal" : "deals"} flagged
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {error && <ErrorBanner message={error} />}
      {loading && !data && <Loading />}

      {data && data.deals.length === 0 && <EmptyState icon={<CheckCircle2 size={26} />} text="No stuck deals across the workspace." />}

      {data && data.deals.length > 0 && (
        <Panel title="Stuck deals" subtitle="Sorted by days over threshold. Click a deal to open the detail view.">
          <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 0 }}>
            {data.deals.map((d, idx) => (
              <Link
                to={`/deals/${d.deal_id}`}
                key={d.deal_id}
                style={{
                  padding: "14px 4px",
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 2fr) 1fr 1fr 1fr",
                  alignItems: "center",
                  gap: 12,
                  borderTop: idx === 0 ? "none" : `1px solid ${PALETTE.hairline}`,
                  textDecoration: "none",
                  color: "inherit",
                  transition: "background 0.12s",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "#fafcff")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: PALETTE.text }}>{d.deal_name}</div>
                  <div style={{ fontSize: 12, color: PALETTE.muted, marginTop: 2 }}>{prettyStage(d.stage)}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 10, color: PALETTE.subtle, textTransform: "uppercase", fontWeight: 700 }}>In stage</div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: PALETTE.text, fontVariantNumeric: "tabular-nums" }}>{d.dwell_days}d</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 10, color: PALETTE.subtle, textTransform: "uppercase", fontWeight: 700 }}>Threshold</div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: PALETTE.subtle, fontVariantNumeric: "tabular-nums" }}>{d.threshold_days}d</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <Pill tone="red">+{d.over_by_days}d over</Pill>
                </div>
              </Link>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}

// ── Forecast tab ───────────────────────────────────────────────────────────

export function ForecastTab({ reps }: { reps: RepSummary[] }) {
  const { isAdmin } = useAuth();
  const [period, setPeriod] = useState<"month" | "quarter">("quarter");
  const [repId, setRepId] = useState<string | undefined>(undefined);
  const [quotaStr, setQuotaStr] = useState<string>("");
  const [data, setData] = useState<ForecastResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const quotaNum = quotaStr.trim() ? Number(quotaStr) : undefined;
    performanceApi
      .getForecast({ period, rep_id: repId, quota: quotaNum })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [period, repId, quotaStr]);

  const bucketColors: Record<string, string> = {
    booked: "#2b8a5d",
    commit: "#4261d6",
    best: "#d08e22",
    pipeline: "#8b9db2",
  };

  const chartData = useMemo(
    () =>
      (data?.buckets ?? []).map((b) => ({
        name: b.category.charAt(0).toUpperCase() + b.category.slice(1),
        ACV: b.acv,
        Weighted: b.weighted_acv,
        color: bucketColors[b.category] ?? PALETTE.subtle,
      })),
    [data],
  );

  const commit = data?.commit_number ?? 0;
  const best = data?.best_case_number ?? 0;
  const weighted = data?.weighted_pipeline ?? 0;
  const quota = data?.quota ?? null;
  const attainment = quota ? Math.min(1.2, commit / quota) : 0;
  const attainmentPct = Math.round(attainment * 100);

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel
        title="Forecast"
        subtitle="Commit and best-case revenue visibility against an optional quota. Deals counted by expected close date inside the period."
        action={
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            {isAdmin && <RepPicker reps={reps} value={repId} onChange={setRepId} />}
            <input
              placeholder="Quota $"
              inputMode="numeric"
              value={quotaStr}
              onChange={(e) => setQuotaStr(e.target.value)}
              style={{
                height: 36,
                width: 130,
                padding: "0 12px",
                borderRadius: 10,
                border: `1px solid ${PALETTE.hairline}`,
                background: "#fff",
                fontSize: 13,
                color: PALETTE.text,
                outline: "none",
              }}
            />
            <SegmentedControl
              value={period}
              onChange={setPeriod}
              options={[
                { value: "month", label: "Month" },
                { value: "quarter", label: "Quarter" },
              ]}
            />
          </div>
        }
      >
        {data && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
            <ForecastStat label="Commit" value={fmtMoney(commit)} hint="Booked + committed deals" color={bucketColors.commit} />
            <ForecastStat label="Best case" value={fmtMoney(best)} hint="Commit + best-case deals" color={bucketColors.best} />
            <ForecastStat label="Weighted pipeline" value={fmtMoney(weighted)} hint="Σ (ACV × stage probability)" color={PALETTE.subtle} />
          </div>
        )}
      </Panel>

      {error && <ErrorBanner message={error} />}
      {loading && !data && <Loading />}

      {data && (
        <>
          {quota != null && (
            <Panel title="Quota attainment" subtitle={`${data.period_label} · commit vs. quota`}>
              <div style={{ display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
                <div style={{ width: 160, height: 160, position: "relative", flexShrink: 0 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <RadialBarChart
                      innerRadius="68%"
                      outerRadius="100%"
                      data={[{ name: "q", value: attainmentPct, fill: attainment >= 1 ? bucketColors.booked : bucketColors.commit }]}
                      startAngle={90}
                      endAngle={-270}
                    >
                      <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                      <RadialBar background={{ fill: "#f1f5fa" }} dataKey="value" cornerRadius={10} />
                    </RadialBarChart>
                  </ResponsiveContainer>
                  <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", pointerEvents: "none" }}>
                    <div style={{ textAlign: "center" }}>
                      <div style={{ fontSize: 28, fontWeight: 800, color: PALETTE.text, lineHeight: 1 }}>{attainmentPct}%</div>
                      <div style={{ fontSize: 10, fontWeight: 800, color: PALETTE.subtle, textTransform: "uppercase", marginTop: 4, letterSpacing: "0.1em" }}>to quota</div>
                    </div>
                  </div>
                </div>
                <div style={{ flex: 1, minWidth: 220, display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: PALETTE.muted }}>
                    <span>Commit</span>
                    <span style={{ fontWeight: 800, color: PALETTE.text }}>{fmtMoney(commit)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: PALETTE.muted }}>
                    <span>Quota</span>
                    <span style={{ fontWeight: 800, color: PALETTE.text }}>{fmtMoney(quota)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: PALETTE.muted, borderTop: `1px solid ${PALETTE.hairline}`, paddingTop: 8 }}>
                    <span>Gap</span>
                    <span style={{ fontWeight: 800, color: (data.gap_to_quota ?? 0) <= 0 ? PALETTE.tintGreenText : PALETTE.tintRedText }}>
                      {fmtMoney(data.gap_to_quota ?? 0)}
                    </span>
                  </div>
                </div>
              </div>
            </Panel>
          )}

          <Panel title="Forecast breakdown" subtitle={`ACV and weighted contribution per category · ${data.period_label}`}>
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                  <XAxis dataKey="name" tickLine={false} axisLine={{ stroke: PALETTE.hairline }} tick={{ fontSize: 12, fill: PALETTE.muted }} />
                  <YAxis
                    tickLine={false}
                    axisLine={false}
                    tick={{ fontSize: 11, fill: PALETTE.subtle }}
                    tickFormatter={(v) => fmtMoney(v as number)}
                  />
                  <Tooltip
                    cursor={{ fill: "rgba(67, 97, 214, 0.05)" }}
                    contentStyle={{
                      border: `1px solid ${PALETTE.hairline}`,
                      borderRadius: 10,
                      fontSize: 12,
                      boxShadow: "0 12px 28px rgba(23,43,77,0.12)",
                    }}
                    formatter={(v: number) => fmtMoney(v)}
                  />
                  <Bar dataKey="ACV" radius={[8, 8, 0, 0]}>
                    {chartData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Bar>
                  <Bar dataKey="Weighted" radius={[8, 8, 0, 0]} fill="#dfe8f5" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", color: PALETTE.subtle, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  <th style={{ padding: "10px 8px", fontWeight: 700 }}>Category</th>
                  <th style={{ padding: "10px 8px", fontWeight: 700, textAlign: "right" }}>Deals</th>
                  <th style={{ padding: "10px 8px", fontWeight: 700, textAlign: "right" }}>ACV</th>
                  <th style={{ padding: "10px 8px", fontWeight: 700, textAlign: "right" }}>Weighted</th>
                </tr>
              </thead>
              <tbody>
                {data.buckets.map((b) => (
                  <tr key={b.category} style={{ borderTop: `1px solid ${PALETTE.hairline}` }}>
                    <td style={{ padding: "12px 8px" }}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                        <span
                          style={{
                            width: 10,
                            height: 10,
                            borderRadius: 999,
                            background: bucketColors[b.category] ?? PALETTE.subtle,
                          }}
                        />
                        <span style={{ textTransform: "capitalize", fontWeight: 700, color: PALETTE.text }}>{b.category}</span>
                      </span>
                    </td>
                    <td style={{ padding: "12px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{b.deal_count}</td>
                    <td style={{ padding: "12px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: 700 }}>{fmtMoney(b.acv)}</td>
                    <td style={{ padding: "12px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: PALETTE.muted }}>{fmtMoney(b.weighted_acv)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </>
      )}
    </div>
  );
}

function ForecastStat({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: string;
  hint: string;
  color: string;
}) {
  return (
    <div
      style={{
        padding: 18,
        borderRadius: 16,
        border: `1px solid ${PALETTE.hairline}`,
        background: "#fff",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 8, height: 8, borderRadius: 999, background: color }} />
        <span style={{ fontSize: 11, fontWeight: 800, color: PALETTE.subtle, textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</span>
      </div>
      <div style={{ fontSize: 30, fontWeight: 800, color: PALETTE.text, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 12, color: PALETTE.muted }}>{hint}</div>
    </div>
  );
}

// ── Rankings (Leaderboards) tab ────────────────────────────────────────────

const METRIC_OPTIONS = [
  { value: "calls_connected" as const, label: "Most calls connected" },
  { value: "demos_done" as const, label: "Most demos done" },
  { value: "pocs_procured" as const, label: "Most POCs procured" },
  { value: "closed_won" as const, label: "Most deals won" },
  { value: "win_rate" as const, label: "Highest win rate" },
  { value: "avg_cycle_time_days" as const, label: "Fastest cycle time" },
];

type LBMetric = (typeof METRIC_OPTIONS)[number]["value"];

export function RankingsTab() {
  const [metric, setMetric] = useState<LBMetric>("calls_connected");
  const [period, setPeriod] = useState<"week" | "month" | "quarter">("month");
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    performanceApi
      .getLeaderboard({ metric, period })
      .then(setData)
      .finally(() => setLoading(false));
  }, [metric, period]);

  const fmt = (v: number) => {
    if (metric === "win_rate") return `${(v * 100).toFixed(1)}%`;
    if (metric === "avg_cycle_time_days") return v ? `${v.toFixed(1)}d` : "—";
    return Number.isInteger(v) ? String(v) : v.toFixed(1);
  };

  const topValue = data?.entries[0]?.value ?? 0;

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel
        title="Rankings"
        subtitle="Lightweight competitive views, refreshed live. Not used for compensation."
        action={
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <select
              value={metric}
              onChange={(e) => setMetric(e.target.value as LBMetric)}
              style={{
                height: 36,
                padding: "0 32px 0 12px",
                borderRadius: 10,
                border: `1px solid ${PALETTE.hairline}`,
                background: "#fff",
                fontSize: 13,
                color: PALETTE.text,
                fontWeight: 600,
                appearance: "none",
                backgroundImage:
                  "url(\"data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3e%3cpath fill='%238b9db2' d='M0 0l5 6 5-6z'/%3e%3c/svg%3e\")",
                backgroundRepeat: "no-repeat",
                backgroundPosition: "right 12px center",
              }}
            >
              {METRIC_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <SegmentedControl
              value={period}
              onChange={setPeriod}
              options={[
                { value: "week", label: "Week" },
                { value: "month", label: "Month" },
                { value: "quarter", label: "Quarter" },
              ]}
            />
          </div>
        }
      >
        {loading && !data && <Loading />}
        {data && (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {data.entries.map((e, idx) => {
              const ratio = topValue && metric !== "avg_cycle_time_days" ? e.value / topValue : 0;
              const isTop3 = idx < 3;
              const medalColor = ["#d5a33a", "#9aa6b8", "#c1884a"][idx] ?? PALETTE.subtle;
              return (
                <div
                  key={e.rep_id}
                  style={{
                    padding: "14px 4px",
                    display: "grid",
                    gridTemplateColumns: "36px minmax(0, 2fr) 1fr minmax(160px, 1.2fr) 90px",
                    alignItems: "center",
                    gap: 14,
                    borderTop: idx === 0 ? "none" : `1px solid ${PALETTE.hairline}`,
                  }}
                >
                  <div style={{ textAlign: "center" }}>
                    {isTop3 ? (
                      <Medal size={20} color={medalColor} />
                    ) : (
                      <span style={{ fontSize: 14, fontWeight: 700, color: PALETTE.subtle, fontVariantNumeric: "tabular-nums" }}>{idx + 1}</span>
                    )}
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: PALETTE.text }}>{e.rep_name}</div>
                  <div>
                    <Pill tone={e.role === "ae" ? "blue" : "coral"}>{e.role.toUpperCase()}</Pill>
                  </div>
                  <div style={{ height: 8, background: "#f1f5fa", borderRadius: 999, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${Math.max(3, ratio * 100)}%`,
                        height: "100%",
                        background: isTop3 ? medalColor : PALETTE.tintBlueText,
                        transition: "width 0.4s",
                      }}
                    />
                  </div>
                  <div style={{ textAlign: "right", fontSize: 18, fontWeight: 800, color: PALETTE.text, fontVariantNumeric: "tabular-nums" }}>
                    {fmt(e.value)}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Panel>
    </div>
  );
}

// ── Targets (Settings) tab ─────────────────────────────────────────────────

function TargetCard({
  title,
  subtitle,
  data,
  onChange,
  step = 1,
}: {
  title: string;
  subtitle?: string;
  data: Record<string, number>;
  onChange: (next: Record<string, number>) => void;
  step?: number;
}) {
  return (
    <Panel title={title} subtitle={subtitle}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
        {Object.entries(data).map(([key, value]) => (
          <label key={key} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: PALETTE.subtle, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              {prettyStage(key)}
            </span>
            <input
              type="number"
              step={step}
              value={value}
              onChange={(e) => onChange({ ...data, [key]: Number(e.target.value) })}
              style={{
                height: 38,
                padding: "0 12px",
                borderRadius: 10,
                border: `1px solid ${PALETTE.hairline}`,
                background: "#fff",
                fontSize: 14,
                fontWeight: 600,
                color: PALETTE.text,
                outline: "none",
                fontVariantNumeric: "tabular-nums",
              }}
            />
          </label>
        ))}
      </div>
    </Panel>
  );
}

export function TargetsTab() {
  const { isAdmin } = useAuth();
  const [settings, setSettings] = useState<AnalyticsSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const savedTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    performanceApi
      .getSettings()
      .then(setSettings)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (!isAdmin) {
    return (
      <Panel
        title="Performance targets"
        subtitle="Only admins can edit weekly/monthly targets, stuck-deal thresholds, and stage probabilities."
      >
        <div style={{ padding: 24, border: `1px dashed ${PALETTE.hairline}`, borderRadius: 14, background: "#fafcff", textAlign: "center", color: PALETTE.muted, fontSize: 13 }}>
          You need admin permissions to edit these settings.
        </div>
      </Panel>
    );
  }

  if (error) return <ErrorBanner message={error} />;
  if (!settings) return <Loading />;

  async function save() {
    if (!settings) return;
    setSaving(true);
    setSaved(false);
    try {
      const next = await performanceApi.updateSettings(settings);
      setSettings(next);
      setSaved(true);
      if (savedTimeoutRef.current) window.clearTimeout(savedTimeoutRef.current);
      savedTimeoutRef.current = window.setTimeout(() => setSaved(false), 2500);
    } catch (e: any) {
      setError(e?.message ?? "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel
        title="Performance targets"
        subtitle="Edit per-role weekly and monthly targets, stuck-deal thresholds by stage, stage probabilities, and RAG bands. Changes apply to all dashboards immediately."
        action={
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {saved && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, color: PALETTE.tintGreenText }}>
                <CheckCircle2 size={14} /> Saved
              </span>
            )}
            <button
              type="button"
              onClick={save}
              disabled={saving}
              style={{
                height: 36,
                padding: "0 18px",
                borderRadius: 999,
                border: "none",
                background: PALETTE.text,
                color: "#fff",
                fontSize: 13,
                fontWeight: 700,
                cursor: saving ? "wait" : "pointer",
                opacity: saving ? 0.6 : 1,
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <Save size={14} />
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        }
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <Pill tone="blue">
            <UsersIcon size={12} /> AE + SDR roles
          </Pill>
          <Pill tone="coral">
            <Layers size={12} /> {Object.keys(settings.stuck_thresholds_days).length} stage thresholds
          </Pill>
          <Pill tone="green">
            <Trophy size={12} /> Workspace TZ {settings.workspace_timezone}
          </Pill>
        </div>
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
        <TargetCard
          title="Weekly targets — AE"
          subtitle="Activities and outcomes expected per AE per ISO week."
          data={settings.weekly_targets.ae ?? {}}
          onChange={(next) => setSettings({ ...settings, weekly_targets: { ...settings.weekly_targets, ae: next } })}
        />
        <TargetCard
          title="Weekly targets — SDR"
          subtitle="Prospecting-heavy targets for SDRs."
          data={settings.weekly_targets.sdr ?? {}}
          onChange={(next) => setSettings({ ...settings, weekly_targets: { ...settings.weekly_targets, sdr: next } })}
        />
        <TargetCard
          title="Monthly targets — AE"
          data={settings.monthly_targets.ae ?? {}}
          onChange={(next) => setSettings({ ...settings, monthly_targets: { ...settings.monthly_targets, ae: next } })}
        />
        <TargetCard
          title="Monthly targets — SDR"
          data={settings.monthly_targets.sdr ?? {}}
          onChange={(next) => setSettings({ ...settings, monthly_targets: { ...settings.monthly_targets, sdr: next } })}
        />
      </div>

      <TargetCard
        title="Stuck-deal thresholds"
        subtitle="Dwell time (days) in a stage before a deal is flagged on the Risk tab. Keep these tight — they exist to force movement."
        data={settings.stuck_thresholds_days}
        onChange={(next) => setSettings({ ...settings, stuck_thresholds_days: next })}
      />

      <TargetCard
        title="Stage probabilities"
        subtitle="Used for weighted pipeline math. Values between 0 and 1."
        data={settings.stage_probabilities}
        step={0.05}
        onChange={(next) => setSettings({ ...settings, stage_probabilities: next })}
      />

      <TargetCard
        title="RAG bands"
        subtitle="Attainment thresholds. green_min = 1.0 means ≥100% is Green; amber_min = 0.70 means 70–99% is Amber; below is Red."
        data={settings.rag_bands as unknown as Record<string, number>}
        step={0.05}
        onChange={(next) => setSettings({ ...settings, rag_bands: next as typeof settings.rag_bands })}
      />
    </div>
  );
}

// ── Tab strip + router integration ─────────────────────────────────────────

export const PERFORMANCE_TABS = [
  { key: "scorecard", label: "Scorecard", icon: Target },
  { key: "funnel", label: "Funnel", icon: Layers },
  { key: "risk", label: "Risk", icon: AlertTriangle },
  { key: "forecast", label: "Forecast", icon: TrendingUp },
  { key: "rankings", label: "Rankings", icon: Trophy },
  { key: "targets", label: "Targets", icon: Gauge },
] as const;

export type PerformanceTabKey = (typeof PERFORMANCE_TABS)[number]["key"];

export function PerformanceTabContent({ tab, reps }: { tab: PerformanceTabKey; reps: RepSummary[] }) {
  switch (tab) {
    case "scorecard":
      return <ScorecardTab reps={reps} />;
    case "funnel":
      return <FunnelTab reps={reps} />;
    case "risk":
      return <RiskTab reps={reps} />;
    case "forecast":
      return <ForecastTab reps={reps} />;
    case "rankings":
      return <RankingsTab />;
    case "targets":
      return <TargetsTab />;
  }
}

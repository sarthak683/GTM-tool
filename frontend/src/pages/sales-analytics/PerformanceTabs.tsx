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
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  PolarAngleAxis,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from "recharts";
import {
  AlertTriangle,
  BarChart3,
  Building2,
  CheckCircle2,
  Download,
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
  analyticsApi,
  performanceApi,
  type AnalyticsSettings,
  type DealHealthResponse,
  type RedAlertDeal,
  type PipelineBucketDeal,
  type PipelineBucketsResponse,
  type ForecastResponse,
  type FunnelResponse,
  type LeaderboardResponse,
  type RepSummary,
  type SalesDashboard,
  type SalesStageBucket,
  type SalesPipelineOwnerRow,
  type SalesRepActivityRow,
  type SalesRepWeeklyActivityRow,
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
  tintCoral: "#f3fbe3",
  tintCoralBorder: "#ffd5c3",
  tintCoralText: "#4d7c0f",
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

// ── Pipeline by Stage components (shared with Overview) ───────────────────

const CHART_PIPELINE = {
  raw: "#cfe89a",
  weighted: "#5a9216",
  primary: "#6fae27",
  grid: "#eef2f8",
  axis: "#7d8ea3",
};

function fmtShortCurrency(value?: number | null) {
  const n = Number(value ?? 0);
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}

function PipelineCurrencyTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload || payload.length === 0) return null;
  const dealCount = (payload[0]?.payload as { deal_count?: number } | undefined)?.deal_count;
  return (
    <div style={{ borderRadius: 14, border: "1px solid #dfe7f2", background: "rgba(255,255,255,0.96)", boxShadow: "0 18px 34px rgba(21,42,68,0.12)", padding: "12px 14px", minWidth: 190 }}>
      <p style={{ margin: 0, fontSize: 12, fontWeight: 800, color: "#1f3144" }}>{label}</p>
      <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
        {payload.map((entry) => (
          <div key={String(entry.dataKey)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, color: "#62748a" }}>
              <span style={{ width: 9, height: 9, borderRadius: 999, background: entry.color ?? CHART_PIPELINE.primary }} />
              {entry.name}
            </span>
            <span style={{ fontSize: 12, fontWeight: 800, color: "#203244" }}>{fmtShortCurrency(Number(entry.value ?? 0))}</span>
          </div>
        ))}
        {typeof dealCount === "number" && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, paddingTop: 6, marginTop: 2, borderTop: "1px solid #eef2f8" }}>
            <span style={{ fontSize: 11, color: "#8696aa" }}>Deals</span>
            <span style={{ fontSize: 12, fontWeight: 800, color: "#203244" }}>{dealCount}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function PipelineStageChart({ rows }: { rows: SalesStageBucket[] }) {
  const chartData = useMemo(
    () => rows.map((r) => ({ label: r.label, amount: r.amount, weighted: r.weighted_amount, deal_count: r.deal_count })),
    [rows],
  );
  if (rows.length === 0) return <p style={{ margin: 0, color: PALETTE.muted }}>No open pipeline yet.</p>;
  const height = rows.length * 52 + 44;
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart layout="vertical" data={chartData} margin={{ top: 8, right: 56, bottom: 0, left: 4 }} barGap={2}>
          <CartesianGrid horizontal={false} stroke={CHART_PIPELINE.grid} />
          <XAxis type="number" tick={{ fill: CHART_PIPELINE.axis, fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtShortCurrency(Number(v))} />
          <YAxis type="category" dataKey="label" tick={{ fill: "#46586d", fontSize: 12, fontWeight: 600 }} axisLine={false} tickLine={false} width={132} />
          <Tooltip content={<PipelineCurrencyTooltip />} cursor={{ fill: "rgba(111,174,39,0.06)" }} />
          <Legend verticalAlign="top" align="left" iconType="circle" wrapperStyle={{ paddingBottom: 8, fontSize: 12 }} />
          <Bar dataKey="amount" name="Open" fill={CHART_PIPELINE.raw} radius={[0, 6, 6, 0]} maxBarSize={16}>
            <LabelList dataKey="amount" position="right" formatter={(v: number) => fmtShortCurrency(Number(v))} fill="#46586d" fontSize={11} fontWeight={700} />
          </Bar>
          <Bar dataKey="weighted" name="Weighted" fill={CHART_PIPELINE.weighted} radius={[0, 6, 6, 0]} maxBarSize={16} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function PipelineOwnerChart({ rows }: { rows: SalesPipelineOwnerRow[] }) {
  // Show only AEs — filter out "Unassigned" (no user_id) and any non-person rows
  const aeRows = rows.filter((r) => r.user_id);
  if (aeRows.length === 0) return <p style={{ margin: 0, color: PALETTE.muted }}>No AE-owned pipeline yet.</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {aeRows.map((row) => (
        <div key={row.key} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 1fr) minmax(180px, 3fr) auto", gap: 12, alignItems: "center" }}>
          <div style={{ minWidth: 0 }}>
            <p style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#213547", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{row.rep_name}</p>
            <p style={{ margin: "4px 0 0", fontSize: 12, color: "#76879b" }}>{row.deal_count} deals</p>
          </div>
          <div style={{ height: 16, borderRadius: 999, background: "#eef2f8", overflow: "hidden", display: "flex" }}>
            {row.stages.map((stage) => {
              const width = row.amount > 0 ? `${(stage.amount / row.amount) * 100}%` : "0%";
              return <div key={stage.key} title={`${stage.label}: ${fmtShortCurrency(stage.amount)}`} style={{ width, background: stage.color, minWidth: stage.amount > 0 ? 8 : 0 }} />;
            })}
          </div>
          <div style={{ textAlign: "right" }}>
            <p style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "#203244" }}>{fmtShortCurrency(row.amount)}</p>
            <p style={{ margin: "4px 0 0", fontSize: 11, color: "#75869a" }}>{fmtShortCurrency(row.weighted_amount)} weighted</p>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Helpers (local to this file) ──────────────────────────────────────────

function fmtCurrency(val: number | null | undefined): string {
  if (!val) return "—";
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(0)}K`;
  return `$${val.toFixed(0)}`;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function dlCsv(filename: string, headers: string[], rows: Array<Array<string | number | null | undefined>>) {
  const esc = (v: string | number | null | undefined) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [headers, ...rows].map((r) => r.map(esc).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `${filename}.csv`;
  a.click();
}

// ── Red-alert deal modal ───────────────────────────────────────────────────

function RedAlertModal({
  label,
  deals,
  onClose,
}: {
  label: string;
  deals: RedAlertDeal[];
  onClose: () => void;
}) {
  const total = deals.reduce((s, d) => s + (d.amount ?? 0), 0);
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        background: "rgba(15,26,42,0.55)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24, backdropFilter: "blur(4px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(820px, 100%)", maxHeight: "86vh",
          background: "#fff", borderRadius: 18, overflow: "hidden",
          display: "flex", flexDirection: "column",
          boxShadow: "0 40px 80px rgba(10,22,40,0.25)",
        }}
      >
        {/* Header */}
        <div style={{ padding: "18px 22px", borderBottom: "1px solid #ebeff5", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <p style={{ margin: 0, fontSize: 11, fontWeight: 800, letterSpacing: "0.1em", color: "#c0392b", textTransform: "uppercase" }}>{label}</p>
            <h3 style={{ margin: "4px 0 0", fontSize: 20, fontWeight: 800, color: "#1d2b3a" }}>
              {deals.length} deal{deals.length === 1 ? "" : "s"}
              {total > 0 && <span style={{ marginLeft: 10, fontSize: 14, fontWeight: 600, color: "#62748a" }}>· {fmtCurrency(total)} total</span>}
            </h3>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {deals.length > 0 && (
              <button
                type="button"
                onClick={() => dlCsv(
                  label.toLowerCase().replace(/\s+/g, "-"),
                  ["Deal", "Amount", "Date in Stage", "AE", "SDR"],
                  deals.map((d) => [d.deal_name, d.amount ?? "", fmtDate(d.stage_entered_at), d.ae_name ?? "", d.sdr_name ?? ""]),
                )}
                style={{ display: "flex", alignItems: "center", gap: 5, padding: "6px 12px", borderRadius: 8, background: "#f4f6fa", border: "1px solid #e0e6ef", fontSize: 12, fontWeight: 700, color: "#3d5a80", cursor: "pointer" }}
              >
                <Download size={13} /> Export CSV
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              style={{ width: 36, height: 36, borderRadius: 10, background: "#f4f6fa", border: "1px solid #e0e6ef", color: "#5d6f84", fontSize: 18, lineHeight: 1, cursor: "pointer", display: "grid", placeItems: "center" }}
            >×</button>
          </div>
        </div>
        {/* Table */}
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#fafbfd", position: "sticky", top: 0 }}>
                {["Deal", "Amount", "Date in Stage", "AE Assigned", "SDR Assigned"].map((h, i) => (
                  <th key={h} style={{ padding: "10px 16px", textAlign: i === 1 ? "right" : "left", fontSize: 11, fontWeight: 800, color: "#8b9db2", textTransform: "uppercase", letterSpacing: "0.05em", borderBottom: "1px solid #ebeff5" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {deals.map((d, i) => (
                <tr key={d.deal_id} style={{ borderBottom: "1px solid #f0f3f8", background: i % 2 === 0 ? "#fff" : "#fafbfd" }}>
                  <td style={{ padding: "12px 16px", fontWeight: 700, color: "#1d2b3a" }}>{d.deal_name || "—"}</td>
                  <td style={{ padding: "12px 16px", textAlign: "right", fontWeight: 700, color: d.amount ? "#1d4ed8" : "#aab4c2", whiteSpace: "nowrap" }}>{fmtCurrency(d.amount)}</td>
                  <td style={{ padding: "12px 16px", color: "#62748a" }}>{fmtDate(d.stage_entered_at)}</td>
                  <td style={{ padding: "12px 16px", color: "#62748a" }}>{d.ae_name || "—"}</td>
                  <td style={{ padding: "12px 16px", color: "#62748a" }}>{d.sdr_name || "—"}</td>
                </tr>
              ))}
              {deals.length === 0 && (
                <tr><td colSpan={5} style={{ padding: 32, textAlign: "center", color: "#aab4c2" }}>No deals</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Pipeline bucket modal ──────────────────────────────────────────────────

function PipelineBucketModal({
  label,
  deals,
  onClose,
}: {
  label: string;
  deals: PipelineBucketDeal[];
  onClose: () => void;
}) {
  const total = deals.reduce((s, d) => s + (d.amount ?? 0), 0);
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 200, background: "rgba(15,26,42,0.55)", display: "flex", alignItems: "center", justifyContent: "center", padding: 24, backdropFilter: "blur(4px)" }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(820px, 100%)", maxHeight: "86vh", background: "#fff", borderRadius: 18, overflow: "hidden", display: "flex", flexDirection: "column", boxShadow: "0 40px 80px rgba(10,22,40,0.25)" }}
      >
        <div style={{ padding: "18px 22px", borderBottom: "1px solid #ebeff5", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <p style={{ margin: 0, fontSize: 11, fontWeight: 800, letterSpacing: "0.1em", color: "#1d4ed8", textTransform: "uppercase" }}>{label}</p>
            <h3 style={{ margin: "4px 0 0", fontSize: 20, fontWeight: 800, color: "#1d2b3a" }}>
              {deals.length} deal{deals.length === 1 ? "" : "s"}
              {total > 0 && <span style={{ marginLeft: 10, fontSize: 14, fontWeight: 600, color: "#62748a" }}>· {fmtCurrency(total)} total</span>}
            </h3>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {deals.length > 0 && (
              <button
                type="button"
                onClick={() => dlCsv(label.toLowerCase().replace(/\s+/g, "-"), ["Deal", "Amount", "Stage", "AE Assigned"], deals.map((d) => [d.deal_name, d.amount ?? "", d.stage, d.ae_name ?? ""]))}
                style={{ display: "flex", alignItems: "center", gap: 5, padding: "6px 12px", borderRadius: 8, background: "#f4f6fa", border: "1px solid #e0e6ef", fontSize: 12, fontWeight: 700, color: "#3d5a80", cursor: "pointer" }}
              >
                <Download size={13} /> Export CSV
              </button>
            )}
            <button type="button" onClick={onClose} style={{ width: 36, height: 36, borderRadius: 10, background: "#f4f6fa", border: "1px solid #e0e6ef", color: "#5d6f84", fontSize: 18, lineHeight: 1, cursor: "pointer", display: "grid", placeItems: "center" }}>×</button>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#fafbfd", position: "sticky", top: 0 }}>
                {["Deal Name", "Amount", "Stage", "AE Assigned"].map((h, i) => (
                  <th key={h} style={{ padding: "10px 16px", textAlign: i === 1 ? "right" : "left", fontSize: 11, fontWeight: 800, color: "#8b9db2", textTransform: "uppercase", letterSpacing: "0.05em", borderBottom: "1px solid #ebeff5" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {deals.map((d, i) => (
                <tr key={d.deal_id} style={{ borderBottom: "1px solid #f0f3f8", background: i % 2 === 0 ? "#fff" : "#fafbfd" }}>
                  <td style={{ padding: "12px 16px", fontWeight: 700, color: "#1d2b3a" }}>{d.deal_name || "—"}</td>
                  <td style={{ padding: "12px 16px", textAlign: "right", fontWeight: 700, color: d.amount ? "#1d4ed8" : "#aab4c2", whiteSpace: "nowrap" }}>{fmtCurrency(d.amount)}</td>
                  <td style={{ padding: "12px 16px", color: "#62748a" }}>{d.stage}</td>
                  <td style={{ padding: "12px 16px", color: "#62748a" }}>{d.ae_name || "—"}</td>
                </tr>
              ))}
              {deals.length === 0 && <tr><td colSpan={4} style={{ padding: 32, textAlign: "center", color: "#aab4c2" }}>No deals</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
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
  const [openBucket, setOpenBucket] = useState<{ key: string; label: string } | null>(null);
  const [pipelineData, setPipelineData] = useState<SalesDashboard | null>(null);
  const [pipelineView, setPipelineView] = useState<"stage" | "rep">("stage");
  const [pipelineBuckets, setPipelineBuckets] = useState<PipelineBucketsResponse | null>(null);
  const [openPipelineBucket, setOpenPipelineBucket] = useState<{ label: string; deals: PipelineBucketDeal[] } | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    performanceApi
      .getDealHealth({ rep_id: repId })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [repId]);

  useEffect(() => {
    analyticsApi.salesDashboard(90).then(setPipelineData).catch(() => null);
    performanceApi.getPipelineBuckets().then(setPipelineBuckets).catch(() => null);
  }, []);

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel
        title="Deal Health Red Alerts"
        subtitle="Deals stuck in stage beyond the threshold. Data sourced live from Pipeline."
        action={
          <div style={{ display: "flex", gap: 10 }}>
            {isAdmin && <RepPicker reps={reps} value={repId} onChange={setRepId} />}
          </div>
        }
      >
        {data && (() => {
          const b = data.red_alert_buckets ?? {};
          const alertBuckets = [
            { key: "demo_scheduled",     label: "Demo Scheduled",   threshold: "> 3 Weeks" },
            { key: "demo_done",          label: "Demo Done",         threshold: "> 3 Weeks" },
            { key: "qualified_lead",     label: "Converted",         threshold: "> 3 Weeks" },
            { key: "poc_agreed",         label: "PoC Agreed",        threshold: "> 4 Weeks" },
            { key: "poc_wip",            label: "PoC WIP",           threshold: "> 3 Weeks" },
            { key: "poc_done_and_later", label: "PoC Done & Later",  threshold: "> 8 Weeks" },
          ];
          return (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(175px, 1fr))", gap: 12 }}>
              {alertBuckets.map(({ key, label, threshold }) => {
                const count = b[key] ?? 0;
                const isHot = count > 0;
                return (
                  <div
                    key={key}
                    role="button"
                    tabIndex={0}
                    onClick={() => setOpenBucket({ key, label })}
                    onKeyDown={(e) => e.key === "Enter" && setOpenBucket({ key, label })}
                    style={{
                      padding: 18,
                      borderRadius: 16,
                      background: isHot
                        ? `linear-gradient(135deg, ${PALETTE.tintRed} 0%, #fff 100%)`
                        : "#fff",
                      border: `1px solid ${isHot ? PALETTE.tintRedBorder : PALETTE.hairline}`,
                      cursor: "pointer",
                      transition: "box-shadow 0.15s",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 4px 16px rgba(0,0,0,0.10)")}
                    onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "none")}
                  >
                    <div style={{ fontSize: 10, fontWeight: 800, color: isHot ? PALETTE.tintRedText : PALETTE.subtle, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                      {label}
                    </div>
                    <div style={{ fontSize: 32, fontWeight: 800, color: PALETTE.text, marginTop: 6, lineHeight: 1 }}>
                      {count}
                    </div>
                    <div style={{ fontSize: 11, color: isHot ? PALETTE.tintRedText : PALETTE.muted, marginTop: 6, fontWeight: isHot ? 700 : 400 }}>
                      {threshold} — {count === 1 ? "deal" : "deals"} flagged
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })()}
      </Panel>

      {/* Red-alert deal modal */}
      {openBucket && data && (
        <RedAlertModal
          label={openBucket.label}
          deals={(data.red_alert_deals ?? {})[openBucket.key] ?? []}
          onClose={() => setOpenBucket(null)}
        />
      )}

      {error && <ErrorBanner message={error} />}
      {loading && !data && <Loading />}

      {/* Pipeline by Stage */}
      {pipelineData && (
        <Panel
          title="Pipeline by Stage"
          subtitle="Stage composition of the current open pipeline."
          action={
            <div style={{ display: "inline-flex", borderRadius: 999, border: "1px solid #dde6f0", background: "#f8fafc", padding: 4 }}>
              {(["stage", "rep"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setPipelineView(v)}
                  style={{
                    height: 34, padding: "0 12px", borderRadius: 999, border: "none",
                    background: pipelineView === v ? "#fff" : "transparent",
                    color: pipelineView === v ? "#2948b9" : "#5d6f84",
                    fontSize: 12, fontWeight: 700, cursor: "pointer",
                    boxShadow: pipelineView === v ? "0 1px 6px rgba(32,53,84,0.08)" : "none",
                  }}
                >
                  {v === "stage" ? "By Stage" : "By Rep"}
                </button>
              ))}
            </div>
          }
        >
          {pipelineView === "stage"
            ? <PipelineStageChart rows={pipelineData.pipeline_by_stage} />
            : <PipelineOwnerChart rows={pipelineData.pipeline_by_owner} />}
        </Panel>
      )}

      {/* Overall Sales Pipeline */}
      <Panel
        title="Overall Sales Pipeline"
        subtitle="Deals in the pipeline that may need attention based on deal size and stage."
      >
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
          {/* Bucket 1: Low-value late-stage deals */}
          {(() => {
            const count = pipelineBuckets?.low_value_late_stage_count ?? 0;
            const deals = pipelineBuckets?.low_value_late_stage_deals ?? [];
            const isHot = count > 0;
            return (
              <div
                role="button"
                tabIndex={0}
                onClick={() => setOpenPipelineBucket({ label: "Late Stage Deals < $750K", deals })}
                onKeyDown={(e) => e.key === "Enter" && setOpenPipelineBucket({ label: "Late Stage Deals < $750K", deals })}
                style={{
                  padding: 20,
                  borderRadius: 16,
                  background: isHot
                    ? `linear-gradient(135deg, ${PALETTE.tintRed} 0%, #fff 100%)`
                    : "#fff",
                  border: `1px solid ${isHot ? PALETTE.tintRedBorder : PALETTE.hairline}`,
                  cursor: "pointer",
                  transition: "box-shadow 0.15s",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 4px 16px rgba(0,0,0,0.10)")}
                onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "none")}
              >
                <div style={{ fontSize: 10, fontWeight: 800, color: isHot ? PALETTE.tintRedText : PALETTE.subtle, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
                  Late Stage · PoC Agreed → Closed
                </div>
                <div style={{ fontSize: 32, fontWeight: 800, color: PALETTE.text, lineHeight: 1 }}>
                  {pipelineBuckets ? count : "—"}
                </div>
                <div style={{ fontSize: 11, color: isHot ? PALETTE.tintRedText : PALETTE.muted, marginTop: 6, fontWeight: isHot ? 700 : 400 }}>
                  Deals in late stages &lt; $750K
                </div>
              </div>
            );
          })()}

          {/* Bucket 2: Small avg deal size */}
          {(() => {
            const count = pipelineBuckets?.small_avg_count ?? 0;
            const deals = pipelineBuckets?.small_avg_deals ?? [];
            const isHot = count > 0;
            return (
              <div
                role="button"
                tabIndex={0}
                onClick={() => setOpenPipelineBucket({ label: "Deals ≤ $100K", deals })}
                onKeyDown={(e) => e.key === "Enter" && setOpenPipelineBucket({ label: "Deals ≤ $100K", deals })}
                style={{
                  padding: 20,
                  borderRadius: 16,
                  background: isHot
                    ? `linear-gradient(135deg, ${PALETTE.tintRed} 0%, #fff 100%)`
                    : "#fff",
                  border: `1px solid ${isHot ? PALETTE.tintRedBorder : PALETTE.hairline}`,
                  cursor: "pointer",
                  transition: "box-shadow 0.15s",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 4px 16px rgba(0,0,0,0.10)")}
                onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "none")}
              >
                <div style={{ fontSize: 10, fontWeight: 800, color: isHot ? PALETTE.tintRedText : PALETTE.subtle, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
                  Small Avg. Deal Size
                </div>
                <div style={{ fontSize: 32, fontWeight: 800, color: PALETTE.text, lineHeight: 1 }}>
                  {pipelineBuckets ? count : "—"}
                </div>
                <div style={{ fontSize: 11, color: isHot ? PALETTE.tintRedText : PALETTE.muted, marginTop: 6, fontWeight: isHot ? 700 : 400 }}>
                  Deals with value ≤ $100K
                </div>
              </div>
            );
          })()}
        </div>
      </Panel>

      {/* Pipeline bucket modal */}
      {openPipelineBucket && (
        <PipelineBucketModal
          label={openPipelineBucket.label}
          deals={openPipelineBucket.deals}
          onClose={() => setOpenPipelineBucket(null)}
        />
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

// ── Outreach Analysis tab ─────────────────────────────────────────────────

// Chart constants mirrored from SalesAnalytics (not exported there)
type AccountMetricKey = "emails" | "calls" | "connected_calls" | "linkedin_reachouts" | "meetings";

const ACCOUNT_ACTIVITY_METRICS: Array<{ key: AccountMetricKey; label: string; color: string; tone: string }> = [
  { key: "emails",             label: "Emails",    color: "#2f8d5d", tone: "#eefbf2" },
  { key: "calls",              label: "Calls",     color: "#445fd0", tone: "#eef3ff" },
  { key: "connected_calls",    label: "Connected", color: "#15736d", tone: "#edf9f8" },
  { key: "linkedin_reachouts", label: "LinkedIn",  color: "#0a66c2", tone: "#eef4ff" },
  { key: "meetings",           label: "Meetings",  color: "#c16a18", tone: "#fff4ea" },
];
const ACCOUNT_MIX_KEYS: AccountMetricKey[]     = ["emails", "calls", "linkedin_reachouts", "meetings"];
const ACCOUNT_CALL_KEYS: AccountMetricKey[]    = ["calls", "connected_calls"];
const ACCOUNT_ACTIVITY_META = Object.fromEntries(
  ACCOUNT_ACTIVITY_METRICS.map((m) => [m.key, m]),
) as Record<AccountMetricKey, (typeof ACCOUNT_ACTIVITY_METRICS)[number]>;

function fmtShortAmt(v?: number | null) {
  if (v == null) return "$0";
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v}`;
}

function AccountActivityTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div style={{ borderRadius: 14, border: "1px solid #dfe7f2", background: "rgba(255,255,255,0.96)", boxShadow: "0 18px 34px rgba(21,42,68,0.12)", padding: "12px 14px", minWidth: 180 }}>
      <p style={{ margin: 0, fontSize: 12, fontWeight: 800, color: "#1f3144" }}>{label}</p>
      <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
        {payload.map((entry) => (
          <div key={String(entry.dataKey)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, color: "#62748a" }}>
              <span style={{ width: 9, height: 9, borderRadius: 999, background: entry.color ?? "#4e6be6" }} />
              {entry.name}
            </span>
            <span style={{ fontSize: 12, fontWeight: 800, color: "#203244" }}>{Number(entry.value ?? 0)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TouchpointPill({
  label,
  total,
  color,
  tone,
  rows,
}: {
  label: string;
  total: number;
  color: string;
  tone: string;
  rows: { label: string; value: number | string }[];
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "14px 12px", borderRadius: 14, background: tone, border: "1px solid transparent", flex: 1, minWidth: 160 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontSize: 26, fontWeight: 800, color, lineHeight: 1 }}>{total}</span>
      </div>
      <span style={{ fontSize: 10, fontWeight: 800, color, textTransform: "uppercase", letterSpacing: "0.07em" }}>{label}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 2 }}>
        {rows.map((r) => (
          <div key={r.label} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 11, color, opacity: 0.72, fontWeight: 600 }}>{r.label}</span>
            <span style={{ fontSize: 12, fontWeight: 800, color }}>{r.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function AccountAnalysisFocus({ rows }: { rows: SalesRepWeeklyActivityRow[] }) {
  const sortedRows = useMemo(
    () => [...rows].sort((a, b) => {
      const delta = (b.totals.total ?? 0) - (a.totals.total ?? 0);
      if (delta !== 0) return delta;
      return (b.pipeline_amount ?? 0) - (a.pipeline_amount ?? 0);
    }),
    [rows],
  );

  const [selectedKey, setSelectedKey] = useState(sortedRows[0]?.key ?? "");

  useEffect(() => {
    if (!sortedRows.some((r) => r.key === selectedKey)) {
      setSelectedKey(sortedRows[0]?.key ?? "");
    }
  }, [selectedKey, sortedRows]);

  if (sortedRows.length === 0) {
    return <p style={{ margin: 0, color: PALETTE.muted }}>No outreach activity yet for this time range.</p>;
  }

  const selectedIndex = Math.max(sortedRows.findIndex((r) => r.key === selectedKey), 0);
  const selectedRow   = sortedRows[selectedIndex] ?? sortedRows[0];

  const weeklyChartData = selectedRow.weeks.map((w) => ({
    label:               w.label,
    shortLabel:          w.label.replace("Week of ", ""),
    emails:              w.emails,
    calls:               w.calls,
    connected_calls:     w.connected_calls,
    live_calls:          w.live_calls,
    linkedin_reachouts:  w.linkedin_reachouts,
    meetings:            w.meetings,
    total:               w.total,
  }));

  const callMtgBooked      = selectedRow.totals.call_meeting_booked ?? 0;
  const callFirst          = selectedRow.totals.call_first_attempt ?? 0;
  const callSecondPlus     = selectedRow.totals.call_second_plus ?? 0;
  const emailFirst         = selectedRow.totals.email_first_attempt ?? 0;
  const emailMin3          = selectedRow.totals.email_min_3_attempts ?? 0;
  const liConnRequested    = selectedRow.totals.linkedin_connection_requested ?? 0;
  const liIntroMsg         = selectedRow.totals.linkedin_intro_msg ?? 0;
  const liFollowupMsg      = selectedRow.totals.linkedin_followup_msg ?? 0;

  return (
    <div
      style={{
        borderRadius: 22,
        border: "1px solid #e7edf5",
        background: "linear-gradient(180deg, #ffffff 0%, #fbfcff 100%)",
        padding: 20,
        display: "grid",
        gap: 18,
      }}
    >
      {/* Top row — spotlight + selector */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18, alignItems: "start" }}>
        {/* Left — name + touchpoint pills */}
        <div style={{ display: "grid", gap: 14 }}>
          <div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <h3 style={{ margin: 0, fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em", color: "#1f3144" }}>{selectedRow.rep_name}</h3>
              {(selectedRow.totals.total_prospects ?? 0) > 0 && (
                <span style={{
                  display: "inline-flex", alignItems: "center", gap: 5,
                  fontSize: 12, fontWeight: 700,
                  padding: "3px 10px", borderRadius: 999,
                  background: "#f1f5f9", color: "#475569",
                  border: "1px solid #e2e8f0",
                }}>
                  <svg width="11" height="11" viewBox="0 0 11 11" fill="none" style={{ flexShrink: 0 }}>
                    <circle cx="5.5" cy="3.5" r="2" stroke="#475569" strokeWidth="1.3"/>
                    <path d="M1.5 9.5c0-2.2 1.79-4 4-4s4 1.8 4 4" stroke="#475569" strokeWidth="1.3" strokeLinecap="round"/>
                  </svg>
                  {selectedRow.totals.total_prospects} prospects
                </span>
              )}
            </div>
            <p style={{ margin: "6px 0 0", fontSize: 13, lineHeight: 1.6, color: "#687b92" }}>
              Select an account to see its full outreach breakdown — calls, emails, and LinkedIn touches side by side.
            </p>
          </div>

          {/* Touchpoint pills */}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <TouchpointPill
              label="Call Touchpoints"
              total={selectedRow.totals.calls}
              color="#445fd0"
              tone="#eef3ff"
              rows={[
                { label: "First Attempt",   value: callFirst },
                { label: "2nd+ Attempts",   value: callSecondPlus },
                { label: "Meetings Booked", value: callMtgBooked },
                { label: "Mobile Numbers",  value: selectedRow.totals.total_mobile_numbers ?? 0 },
              ]}
            />
            <TouchpointPill
              label="Email Touchpoints"
              total={selectedRow.totals.emails}
              color="#2f8d5d"
              tone="#eefbf2"
              rows={[
                { label: "First Attempt",    value: emailFirst },
                { label: "Min. 3 Attempts",  value: emailMin3 },
              ]}
            />
            <TouchpointPill
              label="LinkedIn"
              total={selectedRow.totals.linkedin_reachouts}
              color="#0a66c2"
              tone="#e8f2ff"
              rows={[
                { label: "Connection Requested", value: liConnRequested },
                { label: "Intro Msg Sent",       value: liIntroMsg },
                { label: "Followup Msg Sent",    value: liFollowupMsg },
              ]}
            />
          </div>
        </div>

        {/* Right — account selector only */}
        <div style={{ borderRadius: 18, border: "1px solid #e7edf5", background: "#f8fafc", padding: 14, display: "grid", gap: 10 }}>
          <div>
            <p style={{ margin: 0, fontSize: 11, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: "#71849a" }}>Choose Account</p>
            <p style={{ margin: "6px 0 0", fontSize: 13, lineHeight: 1.6, color: "#687b92" }}>Sorted by total weekly touches in the current window.</p>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {sortedRows.map((row, index) => {
              const selected = row.key === selectedRow.key;
              return (
                <button
                  key={row.key}
                  type="button"
                  onClick={() => setSelectedKey(row.key)}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 8, padding: "10px 12px", borderRadius: 14,
                    border:      selected ? "1px solid #b8cff7" : "1px solid #dde6f0",
                    background:  selected ? "#eef4ff" : "#fff",
                    color:       selected ? "#2948b9" : "#2a3d54",
                    fontSize: 12, fontWeight: 700, cursor: "pointer",
                    boxShadow:   selected ? "0 8px 18px rgba(66,98,197,0.12)" : "none",
                  }}
                >
                  <span style={{ display: "inline-grid", placeItems: "center", width: 22, height: 22, borderRadius: 999, background: selected ? "#dfe9ff" : "#f2f5fa", color: selected ? "#2948b9" : "#6f8095", fontSize: 11, fontWeight: 800 }}>
                    {index + 1}
                  </span>
                  <span style={{ display: "grid", textAlign: "left" }}>
                    <span style={{ lineHeight: 1.2 }}>{row.rep_name}</span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: selected ? "#5e75c8" : "#75879a" }}>{row.totals.total} touches</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Weekly Outreach Mix chart — full width */}
      <div style={{ borderRadius: 18, border: "1px solid #e7edf5", background: "#fff", padding: 18, display: "grid", gap: 12 }}>
        <div>
          <p style={{ margin: 0, fontSize: 11, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: "#71849a" }}>Weekly Outreach Mix</p>
          <p style={{ margin: "6px 0 0", fontSize: 13, lineHeight: 1.6, color: "#6f8195" }}>Stacked bars show weekly volume by channel — calls, emails, LinkedIn, and meetings — so you can spot where effort is concentrated.</p>
        </div>
        <div style={{ width: "100%", height: 320 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={weeklyChartData} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid vertical={false} stroke="#edf2f8" />
              <XAxis dataKey="shortLabel" tick={{ fill: "#7d8ea3", fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "#7d8ea3", fontSize: 11 }} axisLine={false} tickLine={false} width={34} />
              <Tooltip content={<AccountActivityTooltip />} cursor={{ fill: "rgba(78,107,230,0.05)" }} />
              <Legend verticalAlign="top" align="left" iconType="circle" wrapperStyle={{ paddingBottom: 8, fontSize: 12 }} />
              {ACCOUNT_MIX_KEYS.map((key) => {
                const m = ACCOUNT_ACTIVITY_META[key];
                return <Bar key={key} dataKey={key} name={m.label} stackId="mix" fill={m.color} radius={[6, 6, 0, 0]} maxBarSize={42} />;
              })}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export function OutreachAnalysisTab() {
  const [data, setData] = useState<SalesDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    analyticsApi
      .salesDashboard(90)
      .then(setData)
      .catch(() => null)
      .finally(() => setLoading(false));
  }, []);

  const rows: SalesRepWeeklyActivityRow[] = data?.rep_weekly_activity ?? [];

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Panel
        title="Account Analysis"
        subtitle="Outreach activity by account. Select an account from the panel to spotlight its weekly email, call, and LinkedIn mix alongside call quality trends."
        action={
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 12px", borderRadius: 999, background: "#eef4ff", border: "1px solid #d7e2fb", fontSize: 11, fontWeight: 800, color: "#3555c4", letterSpacing: "0.06em", textTransform: "uppercase" }}>
            <Building2 size={13} />
            Account view
          </span>
        }
      >
        {loading && <Loading />}
        {!loading && <AccountAnalysisFocus rows={rows} />}
      </Panel>
    </div>
  );
}

export const PERFORMANCE_TABS = [
  { key: "scorecard",          label: "Scorecard",         icon: Target },
  { key: "funnel",             label: "Funnel",            icon: Layers },
  { key: "risk",               label: "Pipeline Health",   icon: AlertTriangle },
  { key: "forecast",           label: "Forecast",          icon: TrendingUp },
  { key: "targets",            label: "Targets",           icon: Gauge },
  { key: "outreach-analysis",  label: "Outreach Analysis", icon: BarChart3 },
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
    case "targets":
      return <TargetsTab />;
    case "outreach-analysis":
      return <OutreachAnalysisTab />;
  }
}

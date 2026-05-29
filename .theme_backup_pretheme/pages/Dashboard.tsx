import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  BriefcaseBusiness,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  LoaderCircle,
  MailCheck,
  Radar,
  SearchCheck,
  ShieldAlert,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import {
  workspaceApi,
  type WorkspaceAlert,
  type WorkspaceInsightBucket,
  type WorkspaceInsightMetric,
  type WorkspaceInsightQueue,
  type WorkspaceInsights,
} from "../lib/api";

const TONE_STYLES = {
  blue: {
    badgeBg: "#eef3ff",
    badgeText: "#384eb7",
    bar: "#5663d7",
    soft: "#f7f9ff",
    border: "#d9e0fb",
  },
  green: {
    badgeBg: "#e9f7f0",
    badgeText: "#2f8f5b",
    bar: "#47b975",
    soft: "#f6fbf8",
    border: "#cfead9",
  },
  amber: {
    badgeBg: "#fff6e6",
    badgeText: "#9b6b12",
    bar: "#e7a23a",
    soft: "#fffaf2",
    border: "#f1ddaf",
  },
  red: {
    badgeBg: "#fff0f0",
    badgeText: "#c55656",
    bar: "#eb6a6a",
    soft: "#fff8f8",
    border: "#f2caca",
  },
} as const;

const SEVERITY_STYLES = {
  high: {
    bg: "#fff5f5",
    text: "#c55656",
    border: "#f2caca",
  },
  medium: {
    bg: "#fff9ef",
    text: "#9b6b12",
    border: "#f1ddaf",
  },
  low: {
    bg: "#f3f7ff",
    text: "#4b60cf",
    border: "#d7dffb",
  },
} as const;

const ALERT_TYPE_LABEL: Record<string, string> = {
  stale_deal: "Stale Deal",
  at_risk: "At Risk",
  missing_close_date: "Missing Close Date",
  deal_no_next_step: "Next Step Missing",
  no_contacts: "No Contacts",
  no_pre_brief: "No Pre-Brief",
  no_next_steps: "No Next Steps",
  hot_prospect: "Hot Prospect",
  cooling_sequence: "Cooling Sequence",
  research_blocker: "Research Blocker",
};

const METRIC_ICONS: Record<string, LucideIcon> = {
  open_pipeline: TrendingUp,
  deal_rescue: ShieldAlert,
  prospects_in_motion: Radar,
  hot_follow_up: MailCheck,
  coverage_gaps: SearchCheck,
  meeting_gaps: CalendarClock,
};

const QUEUE_ICONS: Record<string, LucideIcon> = {
  deal_rescue: ShieldAlert,
  follow_up_now: MailCheck,
  cooling_sequences: Radar,
  research_blockers: SearchCheck,
  coverage_gaps: BriefcaseBusiness,
  meeting_prep: CalendarClock,
  post_meeting_followup: AlertTriangle,
};

function formatShortCurrency(value?: number | null) {
  const amount = Number(value ?? 0);
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}M`;
  if (amount >= 1_000) return `$${Math.round(amount / 1_000)}k`;
  return `$${amount.toFixed(0)}`;
}

function formatSnapshotTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function MetricCard({ metric }: { metric: WorkspaceInsightMetric }) {
  const Icon = METRIC_ICONS[metric.key] ?? TrendingUp;
  const tone = TONE_STYLES[metric.tone];
  const content = (
    <div
      className="crm-panel"
      style={{
        padding: 20,
        minHeight: 148,
        display: "flex",
        flexDirection: "column",
        gap: 18,
        textDecoration: "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: 12,
            display: "grid",
            placeItems: "center",
            background: tone.soft,
            border: `1px solid ${tone.border}`,
            color: tone.badgeText,
            flexShrink: 0,
          }}
        >
          <Icon size={18} />
        </div>
        <span
          style={{
            padding: "4px 10px",
            borderRadius: 999,
            fontSize: 11,
            fontWeight: 700,
            background: tone.badgeBg,
            color: tone.badgeText,
            border: `1px solid ${tone.border}`,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          {metric.label}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <p style={{ margin: 0, fontSize: 34, lineHeight: 1, fontWeight: 800, color: "#212121" }}>{metric.value}</p>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "#6f7d94" }}>{metric.hint}</p>
      </div>

      {metric.link && (
        <div
          style={{
            marginTop: "auto",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12,
            fontWeight: 700,
            color: tone.badgeText,
          }}
        >
          Open
          <ArrowRight size={13} />
        </div>
      )}
    </div>
  );

  if (!metric.link) return content;

  return (
    <Link to={metric.link} style={{ textDecoration: "none" }}>
      {content}
    </Link>
  );
}

function BucketList({
  buckets,
  emptyLabel,
}: {
  buckets: WorkspaceInsightBucket[];
  emptyLabel: string;
}) {
  const maxCount = useMemo(
    () => Math.max(...buckets.map((bucket) => bucket.count), 1),
    [buckets]
  );

  if (buckets.length === 0) {
    return <p className="crm-muted" style={{ margin: 0, fontSize: 13 }}>{emptyLabel}</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {buckets.map((bucket) => {
        const tone = TONE_STYLES[bucket.tone];
        const width = `${Math.max((bucket.count / maxCount) * 100, bucket.count > 0 ? 8 : 0)}%`;
        return (
          <div key={bucket.key} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: 999,
                    background: tone.bar,
                    flexShrink: 0,
                  }}
                />
                <span style={{ fontSize: 13, fontWeight: 600, color: "#212121", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {bucket.label}
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexShrink: 0 }}>
                {bucket.amount != null && bucket.amount > 0 && (
                  <span style={{ fontSize: 12, color: "#6f7d94" }}>{formatShortCurrency(bucket.amount)}</span>
                )}
                <span style={{ fontSize: 13, fontWeight: 700, color: "#212121" }}>{bucket.count}</span>
              </div>
            </div>
            <div
              style={{
                height: 8,
                borderRadius: 999,
                background: "#eef1f7",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width,
                  background: tone.bar,
                  borderRadius: 999,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function QueueCard({ queue }: { queue: WorkspaceInsightQueue }) {
  const Icon = QUEUE_ICONS[queue.key] ?? AlertTriangle;
  const tone = TONE_STYLES[queue.tone];
  return (
    <Link
      to={queue.link}
      className="crm-panel"
      style={{
        padding: 18,
        textDecoration: "none",
        color: "inherit",
        display: "flex",
        flexDirection: "column",
        gap: 14,
        minHeight: 146,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div
          style={{
            width: 38,
            height: 38,
            borderRadius: 12,
            display: "grid",
            placeItems: "center",
            background: tone.soft,
            border: `1px solid ${tone.border}`,
            color: tone.badgeText,
            flexShrink: 0,
          }}
        >
          <Icon size={17} />
        </div>
        <span
          style={{
            padding: "3px 10px",
            borderRadius: 999,
            fontSize: 11,
            fontWeight: 700,
            color: tone.badgeText,
            background: tone.badgeBg,
            border: `1px solid ${tone.border}`,
          }}
        >
          {queue.count}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <p style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#212121" }}>{queue.label}</p>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "#6f7d94" }}>{queue.hint}</p>
      </div>
      <div
        style={{
          marginTop: "auto",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
          fontWeight: 700,
          color: tone.badgeText,
        }}
      >
        Review
        <ChevronRight size={14} />
      </div>
    </Link>
  );
}

function AlertList({ alerts }: { alerts: WorkspaceAlert[] }) {
  if (alerts.length === 0) {
    return (
      <div style={{ padding: "6px 2px 2px" }}>
        <div
          style={{
            border: "1px solid #d8e6d8",
            background: "#f6fcf7",
            borderRadius: 14,
            padding: 18,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <CheckCircle2 size={20} style={{ color: "#47b975", flexShrink: 0 }} />
          <div>
            <p style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#212121" }}>No active alerts</p>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#6f7d94" }}>
              Pipeline, prospecting, and meetings are all looking clean right now.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {alerts.map((alert) => {
        const severity = SEVERITY_STYLES[alert.severity];
        const content = (
          <div
            style={{
              padding: 16,
              borderRadius: 14,
              border: `1px solid ${severity.border}`,
              background: severity.bg,
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
            }}
          >
            <AlertTriangle size={16} style={{ color: severity.text, marginTop: 2, flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span
                  style={{
                    padding: "2px 8px",
                    borderRadius: 999,
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    color: severity.text,
                    border: `1px solid ${severity.border}`,
                    background: "white",
                  }}
                >
                  {ALERT_TYPE_LABEL[alert.type] ?? alert.type}
                </span>
                <span style={{ fontSize: 14, fontWeight: 700, color: "#212121" }}>{alert.title}</span>
              </div>
              <p style={{ margin: "8px 0 0", fontSize: 13, lineHeight: 1.6, color: "#6f7d94" }}>{alert.description}</p>
            </div>
            {alert.link && (
              <span style={{ fontSize: 12, fontWeight: 700, color: severity.text, flexShrink: 0 }}>Open</span>
            )}
          </div>
        );

        if (!alert.link) {
          return <div key={alert.id}>{content}</div>;
        }

        return (
          <Link key={alert.id} to={alert.link} style={{ textDecoration: "none" }}>
            {content}
          </Link>
        );
      })}
    </div>
  );
}

export default function Dashboard() {
  const [insights, setInsights] = useState<WorkspaceInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    workspaceApi
      .insights()
      .then(setInsights)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="crm-page">
        <div className="crm-panel" style={{ padding: 28, display: "flex", alignItems: "center", gap: 12 }}>
          <LoaderCircle size={18} className="animate-spin" style={{ color: "#384eb7" }} />
          <span className="crm-muted" style={{ fontSize: 14 }}>Loading CRM insights...</span>
        </div>
      </div>
    );
  }

  if (error || !insights) {
    return (
      <div className="crm-page">
        <div className="crm-panel" style={{ padding: 28 }}>
          <p style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#212121" }}>Unable to load CRM insights</p>
          <p className="crm-muted" style={{ margin: "8px 0 0", fontSize: 13 }}>
            {error ?? "Something went wrong while loading the insights page."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="crm-page">
      <div className="crm-panel" style={{ padding: 24 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div style={{ maxWidth: 860 }}>
            <p
              style={{
                margin: 0,
                fontSize: 11,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                color: "#7a7a7a",
              }}
            >
              CRM Insights and Alerts
            </p>
            <h1 style={{ margin: "10px 0 0", fontSize: 28, fontWeight: 700, color: "#212121" }}>
              Meaningful signal across pipeline, prospecting, and meetings
            </h1>
            <p style={{ margin: "10px 0 0", fontSize: 14, lineHeight: 1.7, color: "#6f7d94" }}>
              This page now reflects current Beacon workflows instead of legacy revenue-only charts. It highlights
              where momentum is building, where execution is blocked, and what needs intervention next.
            </p>
          </div>

          <div
            style={{
              padding: "8px 12px",
              borderRadius: 999,
              border: "1px solid #d9e0fb",
              background: "#f7f9ff",
              fontSize: 12,
              fontWeight: 600,
              color: "#384eb7",
            }}
          >
            Snapshot {formatSnapshotTime(insights.generated_at)}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {insights.metrics.map((metric) => (
          <MetricCard key={metric.key} metric={metric} />
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.35fr_1fr] gap-6">
        <div className="crm-panel" style={{ padding: 22 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, marginBottom: 18 }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "#212121" }}>Attention queues</h2>
              <p className="crm-muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
                The most useful action queues right now across the CRM.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {insights.focus_queues.map((queue) => (
              <QueueCard key={queue.key} queue={queue} />
            ))}
          </div>
        </div>

        <div className="crm-panel" style={{ padding: 22 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, marginBottom: 18 }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "#212121" }}>Priority alerts</h2>
              <p className="crm-muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
                Highest-risk or highest-opportunity signals from the live CRM state.
              </p>
            </div>
            <span
              style={{
                padding: "4px 10px",
                borderRadius: 999,
                background: "#eef3ff",
                border: "1px solid #d9e0fb",
                color: "#384eb7",
                fontSize: 11,
                fontWeight: 700,
              }}
            >
              {insights.alerts.length}
            </span>
          </div>
          <AlertList alerts={insights.alerts} />
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <div className="crm-panel" style={{ padding: 22, display: "flex", flexDirection: "column", gap: 22 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "#212121" }}>Deal execution</h2>
            <p className="crm-muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
              Where pipeline is concentrated and how risky the active book looks.
            </p>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <div>
              <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 700, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Stage mix
              </p>
              <BucketList buckets={insights.deal_stage_mix} emptyLabel="No deals tracked yet." />
            </div>

            <div
              style={{
                height: 1,
                background: "#e8edf5",
              }}
            />

            <div>
              <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 700, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Health mix
              </p>
              <BucketList buckets={insights.deal_health_mix} emptyLabel="No open deal health data yet." />
            </div>
          </div>
        </div>

        <div className="crm-panel" style={{ padding: 22, display: "flex", flexDirection: "column", gap: 22 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "#212121" }}>Prospecting and meeting readiness</h2>
            <p className="crm-muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
              Momentum from outreach, plus whether meetings are actually ready to run.
            </p>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <div>
              <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 700, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Prospect stage mix
              </p>
              <BucketList buckets={insights.prospect_stage_mix} emptyLabel="No prospect signals yet." />
            </div>

            <div
              style={{
                height: 1,
                background: "#e8edf5",
              }}
            />

            <div>
              <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 700, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Meeting readiness
              </p>
              <BucketList buckets={insights.meeting_readiness_mix} emptyLabel="No meeting readiness signals yet." />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

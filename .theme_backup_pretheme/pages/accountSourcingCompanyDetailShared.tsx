import { CSSProperties, ReactNode } from "react";
import { CheckCircle2, ExternalLink } from "lucide-react";

import type { Contact, DealStageSetting } from "../types";

export const colors = {
  bg: "#f4f7fb",
  card: "#ffffff",
  border: "#d9e1ec",
  text: "#1d2b3c",
  sub: "#55657a",
  faint: "#7f8fa5",
  primary: "#1f6feb",
  primarySoft: "#e8f0ff",
  green: "#1f8f5f",
  greenSoft: "#e8f8f0",
  violet: "#7a2dd9",
  violetSoft: "#f3eaff",
  amber: "#b56d00",
  amberSoft: "#fff4df",
  red: "#b42336",
  redSoft: "#ffecef",
};

export const ICP_STYLE: Record<string, CSSProperties> = {
  hot: { background: "#ffecef", color: "#b42336", border: "1px solid #ffd0d8" },
  warm: { background: "#fff4df", color: "#9b5a00", border: "1px solid #ffe4b0" },
  monitor: { background: "#ebf3ff", color: "#1f5ecc", border: "1px solid #d5e5ff" },
  cold: { background: "#eef2f7", color: "#5e6d83", border: "1px solid #d9e1ec" },
};

export const PERSONA_STYLE: Record<string, CSSProperties> = {
  champion: { background: colors.greenSoft, color: colors.green },
  buyer: { background: "#eaf2ff", color: "#2556c4" },
  evaluator: { background: colors.amberSoft, color: colors.amber },
  blocker: { background: colors.redSoft, color: colors.red },
  influencer: { background: "#f0e6ff", color: "#6b3fa0" },
  implementation_owner: { background: "#e6f7ff", color: "#0369a1" },
};

export const PERSONA_LABEL: Record<string, string> = {
  buyer: "Economic Buyer",
  champion: "Champion",
  evaluator: "Technical Evaluator",
  blocker: "Blocker",
  influencer: "Influencer",
  implementation_owner: "Implementation Owner",
  unknown: "Unknown",
};

export const PRIORITY_STYLE: Record<"high" | "medium" | "low", CSSProperties> = {
  high: { background: "#e8f8f0", color: "#1f8f5f" },
  medium: { background: "#fff4df", color: "#b56d00" },
  low: { background: "#eef2f7", color: "#5e6d83" },
};

export const INTEREST_STYLE: Record<"high" | "medium" | "low", CSSProperties> = {
  high: { background: "#eef5ff", color: "#1f6feb" },
  medium: { background: "#f3eaff", color: "#7a2dd9" },
  low: { background: "#ffecef", color: "#b42336" },
};

export const DISPOSITION_OPTIONS = [
  { value: "", label: "Unreviewed" },
  { value: "working", label: "Working" },
  { value: "interested", label: "Interested" },
  { value: "nurture", label: "Nurture" },
  { value: "not_interested", label: "Not Interested" },
  { value: "bad_fit", label: "Bad Fit" },
  { value: "do_not_target", label: "Do Not Target" },
];

export const OUTREACH_STATUS_OPTIONS = [
  { value: "", label: "Unknown" },
  { value: "not_started", label: "Not Started" },
  { value: "contacted", label: "Contacted" },
  { value: "replied", label: "Replied" },
  { value: "meeting_booked", label: "Meeting Booked" },
];

export const OUTREACH_LANE_OPTIONS = [
  { value: "", label: "Auto / Unset" },
  { value: "warm_intro", label: "Warm Intro" },
  { value: "event_follow_up", label: "Event Follow-up" },
  { value: "cold_operator", label: "Cold Operator" },
  { value: "cold_strategic", label: "Cold Strategic" },
];

export const FALLBACK_DEAL_STAGES: DealStageSetting[] = [
  { id: "discovery", label: "discovery", group: "active", color: "#3b82f6" },
  { id: "evaluation", label: "evaluation", group: "active", color: "#6366f1" },
  { id: "proposal", label: "proposal", group: "active", color: "#8b5cf6" },
  { id: "negotiation", label: "negotiation", group: "active", color: "#f59e0b" },
];

export function defaultDealStage(stages: DealStageSetting[]): string {
  return stages.find((stage) => stage.group === "active")?.id ?? stages[0]?.id ?? "discovery";
}

export const pageStyle: CSSProperties = {
  background: colors.bg,
  minHeight: "100%",
  padding: "30px 26px 40px",
};

export const wrapStyle: CSSProperties = {
  maxWidth: 1450,
  margin: "0 auto",
  display: "grid",
  gap: 16,
};

export const cardStyle: CSSProperties = {
  background: colors.card,
  border: `1px solid ${colors.border}`,
  borderRadius: 16,
  boxShadow: "0 6px 20px rgba(17,34,68,0.05)",
};

export function ts(value?: string): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function unwrapCache(cache: Record<string, unknown>, key: string): Record<string, unknown> | undefined {
  const e = cache[key] as Record<string, unknown> | undefined;
  if (!e) return undefined;
  return (e.data ?? e) as Record<string, unknown>;
}

export function cacheTs(cache: Record<string, unknown>, key: string): string | undefined {
  const e = cache[key] as Record<string, unknown> | undefined;
  if (typeof e?.fetched_at === "string") return e.fetched_at;
  if (typeof e?.analyzed_at === "string") return e.analyzed_at;
  return undefined;
}

export function canonicalPersona(persona?: string | null, personaType?: string | null): keyof typeof PERSONA_STYLE | "unknown" {
  const normalized = (personaType || persona || "").toLowerCase();
  if (normalized === "economic_buyer" || normalized === "buyer") return "buyer";
  if (normalized === "technical_evaluator" || normalized === "evaluator") return "evaluator";
  if (normalized === "champion") return "champion";
  if (normalized === "blocker") return "blocker";
  if (normalized === "influencer") return "influencer";
  if (normalized === "implementation_owner") return "implementation_owner";
  return "unknown";
}

export function isPriorityStakeholder(contact: Contact): boolean {
  const normalizedTitle = (contact.title || "").toLowerCase();
  const normalizedPersona = (contact.persona_type || contact.persona || "").toLowerCase();
  if (["buyer", "champion", "evaluator", "technical_evaluator", "implementation_owner"].includes(normalizedPersona)) {
    return true;
  }
  return [
    "director of engineering",
    "engineering director",
    "vp engineering",
    "head of engineering",
    "professional services",
    "implementation",
    "solutions consulting",
    "product management",
    "product manager",
    "director of product",
    "technology",
    "technical",
    "data science",
    "data",
    "enterprise applications",
  ].some((needle) => normalizedTitle.includes(needle));
}

export function MetricCard({ label, value, hint, tone = "neutral", onClick }: {
  label: string;
  value: string;
  hint: string;
  tone?: "neutral" | "primary" | "warm" | "green";
  onClick?: () => void;
}) {
  const toneStyle = {
    neutral: { bg: "#fbfdff", border: colors.border, accent: colors.sub },
    primary: { bg: "#eef5ff", border: "#cfe0fb", accent: colors.primary },
    warm: { bg: "#fff7eb", border: "#ffe0b2", accent: colors.amber },
    green: { bg: "#eefcf5", border: "#cdeedc", accent: colors.green },
  }[tone];

  return (
    <div
      onClick={onClick}
      style={{
        border: `1px solid ${toneStyle.border}`,
        background: toneStyle.bg,
        borderRadius: 14,
        padding: "14px 16px",
        cursor: onClick ? "pointer" : "default",
      }}
    >
      <div style={{ color: colors.faint, fontWeight: 800, fontSize: 11, letterSpacing: 0.5 }}>{label.toUpperCase()}</div>
      <div style={{ marginTop: 8, color: toneStyle.accent, fontWeight: 800, fontSize: 26 }}>{value}</div>
      <div style={{ marginTop: 6, color: colors.sub, fontSize: 13, lineHeight: 1.45 }}>{hint}</div>
    </div>
  );
}

export function Section({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div style={{ ...cardStyle, padding: "18px 20px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800, marginBottom: 12 }}>
        {icon}
        <span>{title}</span>
      </div>
      <div style={{ display: "grid", gap: 10 }}>{children}</div>
    </div>
  );
}

export function KV({ label, value }: { label: string; value?: ReactNode }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "160px minmax(0,1fr)", gap: 10, alignItems: "start" }}>
      <div style={{ color: colors.faint, fontWeight: 700, fontSize: 12, letterSpacing: 0.3 }}>{label.toUpperCase()}</div>
      <div style={{ color: colors.sub, lineHeight: 1.6 }}>{value}</div>
    </div>
  );
}

export function NewsSignalCard({
  item,
  borderColor,
  background,
}: {
  item: { title?: string; snippet?: string; url?: string };
  borderColor: string;
  background: string;
}) {
  const content = (
    <>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
        <div style={{ color: colors.text, fontSize: 13, fontWeight: 700 }}>{item.title}</div>
        {item.url ? <ExternalLink size={14} style={{ color: colors.primary, flexShrink: 0, marginTop: 1 }} /> : null}
      </div>
      {item.snippet ? <div style={{ color: colors.sub, fontSize: 12.5, lineHeight: 1.5, marginTop: 4 }}>{item.snippet}</div> : null}
      {item.url ? <div style={{ marginTop: 8, fontSize: 11, fontWeight: 700, color: colors.primary }}>Open source</div> : null}
    </>
  );

  const sharedStyle: CSSProperties = {
    border: `1px solid ${borderColor}`,
    background,
    borderRadius: 10,
    padding: "10px 14px",
    textDecoration: "none",
  };

  if (!item.url) {
    return <div style={sharedStyle}>{content}</div>;
  }

  return (
    <a href={item.url} target="_blank" rel="noreferrer" style={{ ...sharedStyle, display: "block" }}>
      {content}
    </a>
  );
}

export function asText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const cleaned = value
    .replace(/\s+/g, " ")
    .replace(/\s*\/\s*/g, " / ")
    .trim();
  return cleaned || undefined;
}

export function asList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => asText(String(item))).filter(Boolean) as string[];
}

export function toBriefItems(value: unknown, maxItems = 4): string[] {
  if (typeof value !== "string" || !value.trim()) return [];
  const normalized = value
    .replace(/\r/g, "")
    .replace(/(?:^|\s)(\d+)[.)]\s+/g, "\n$1. ")
    .replace(/\s+(\d+\))/g, "\n$1")
    .replace(/\s+[•-]\s+/g, "\n- ")
    .replace(/\s+(?=(?:PRIMARY|SECONDARY|TERTIARY|BACKUP|ALT(?:ERNATE)?|RISK|PROOF|ANGLE|PATH|HOOK)\s*:)/gi, "\n")
    .split(/\n+/)
    .map((item) => item.replace(/^\d+[.)]\s*/, "").replace(/^[-•]\s*/, "").trim())
    .filter(Boolean);
  return normalized.slice(0, maxItems).map((item) => item.replace(/\s+/g, " "));
}

export function clipText(value: string | undefined, maxLength: number): string | undefined {
  if (!value) return undefined;
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength).trim()}...`;
}

export function ListCard({ title, items, empty }: { title: string; items: string[]; empty?: string }) {
  return (
    <div style={{ border: `1px solid ${colors.border}`, background: "#fbfdff", borderRadius: 14, padding: "15px 16px" }}>
      <div style={{ color: colors.text, fontWeight: 800, fontSize: 13, marginBottom: 10 }}>{title}</div>
      {items.length === 0 ? (
        <div style={{ color: colors.faint, fontSize: 13 }}>{empty || "Nothing captured yet."}</div>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {items.map((item, idx) => (
            <div key={`${title}-${idx}`} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <CheckCircle2 size={14} color={colors.primary} style={{ marginTop: 2, flexShrink: 0 }} />
              <div style={{ color: colors.sub, fontSize: 13.5, lineHeight: 1.55 }}>{item}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export type EvidenceSource = {
  title: string;
  url: string;
  snippet?: string;
};

export function sourceList(value: unknown): EvidenceSource[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      title: typeof item.title === "string" && item.title.trim() ? item.title.trim() : String(item.url || "Source"),
      url: typeof item.url === "string" ? item.url : "",
      snippet: typeof item.snippet === "string" ? item.snippet : undefined,
    }))
    .filter((item) => item.url);
}

export function SourceLinks({ items }: { items: EvidenceSource[] }) {
  if (!items.length) return null;
  return (
    <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
      <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.3 }}>SOURCES</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {items.map((item) => (
          <a
            key={`${item.url}-${item.title}`}
            href={item.url}
            target="_blank"
            rel="noreferrer"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              textDecoration: "none",
              borderRadius: 999,
              border: `1px solid ${colors.border}`,
              background: "#fff",
              color: colors.primary,
              padding: "6px 10px",
              fontSize: 12,
              fontWeight: 700,
              maxWidth: "100%",
            }}
            title={item.snippet || item.title}
          >
            <ExternalLink size={12} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 220 }}>{item.title}</span>
          </a>
        ))}
      </div>
    </div>
  );
}

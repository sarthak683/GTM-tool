import { CSSProperties, ReactNode } from "react";
import { CheckCircle2 } from "lucide-react";

import type { Company, Contact } from "../types";

export const colors = {
  bg: "#f4f7fb",
  card: "#ffffff",
  border: "#d9e1ec",
  text: "#1d2b3c",
  sub: "#55657a",
  faint: "#7f8fa5",
  primary: "#1f6feb",
  primarySoft: "#eef5ff",
  green: "#1f8f5f",
  greenSoft: "#e8f8f0",
  amber: "#b56d00",
  amberSoft: "#fff4df",
  violet: "#7a2dd9",
  violetSoft: "#f3eaff",
};

export const pageStyle: CSSProperties = {
  background: "radial-gradient(circle at top right, rgba(31,111,235,0.12), transparent 28%), radial-gradient(circle at left center, rgba(181,109,0,0.10), transparent 22%), #f4f7fb",
  minHeight: "100%",
  padding: "30px 26px 40px",
};

export const wrapStyle: CSSProperties = {
  maxWidth: 1420,
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

export const heroCardStyle: CSSProperties = {
  ...cardStyle,
  padding: "24px 24px 22px",
  background: "linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(239,245,255,0.96) 58%, rgba(255,247,235,0.95) 100%)",
  borderColor: "#d7e3f3",
  boxShadow: "0 16px 40px rgba(31, 69, 120, 0.10)",
};

export function asText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function asList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item).trim()).filter(Boolean);
}

export function prettify(value?: string | null): string {
  if (!value) return "Not set";
  return value.replace(/_/g, " ");
}

export function toneForLane(value?: string | null): "primary" | "warm" | "violet" | "green" {
  if (value === "warm_intro") return "warm";
  if (value === "event_follow_up") return "violet";
  if (value === "cold_strategic") return "green";
  return "primary";
}

export function sequencePlan(contact?: Contact | null): Record<string, unknown> {
  const data = contact?.enrichment_data;
  if (!data || typeof data !== "object") return {};
  const plan = (data as Record<string, unknown>).sequence_plan;
  return plan && typeof plan === "object" ? (plan as Record<string, unknown>) : {};
}

export function uploadedRow(contact?: Contact | null): Record<string, unknown> {
  const data = contact?.enrichment_data;
  if (!data || typeof data !== "object") return {};
  const row = (data as Record<string, unknown>).raw_row;
  return row && typeof row === "object" ? (row as Record<string, unknown>) : {};
}

export function companyUploadedRow(company?: Company | null): Record<string, unknown> {
  const block = company?.enrichment_sources?.import;
  if (!block || typeof block !== "object") return {};
  const row = (block as Record<string, unknown>).raw_row;
  return row && typeof row === "object" ? (row as Record<string, unknown>) : {};
}

export function importedAnalyst(company?: Company | null): Record<string, unknown> {
  const block = company?.enrichment_sources?.import;
  if (!block || typeof block !== "object") return {};
  const analyst = (block as Record<string, unknown>).analyst;
  return analyst && typeof analyst === "object" ? (analyst as Record<string, unknown>) : {};
}

export function MetricCard({ label, value, hint, tone = "primary" }: {
  label: string;
  value: string;
  hint: string;
  tone?: "primary" | "green" | "warm" | "violet" | "danger";
}) {
  const toneStyle = {
    primary: { bg: colors.primarySoft, border: "#cfe0fb", accent: colors.primary },
    green: { bg: colors.greenSoft, border: "#cdeedc", accent: colors.green },
    warm: { bg: colors.amberSoft, border: "#ffe0b2", accent: colors.amber },
    violet: { bg: colors.violetSoft, border: "#e2d2fb", accent: colors.violet },
    danger: { bg: "#fff1f3", border: "#f6d0d7", accent: "#b42336" },
  }[tone];

  return (
    <div style={{ border: `1px solid ${toneStyle.border}`, background: `linear-gradient(180deg, ${toneStyle.bg} 0%, #ffffff 100%)`, borderRadius: 16, padding: "14px 16px", boxShadow: "0 10px 24px rgba(17,34,68,0.04)" }}>
      <div style={{ color: colors.faint, fontWeight: 800, fontSize: 11, letterSpacing: 0.45 }}>{label.toUpperCase()}</div>
      <div style={{ marginTop: 8, color: toneStyle.accent, fontWeight: 800, fontSize: 24 }}>{value}</div>
      <div style={{ marginTop: 6, color: colors.sub, fontSize: 13, lineHeight: 1.45 }}>{hint}</div>
    </div>
  );
}

export function Section({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div style={{ ...cardStyle, padding: "18px 20px", background: "linear-gradient(180deg, #ffffff 0%, #fbfdff 100%)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, color: colors.text, fontWeight: 800, marginBottom: 14 }}>
        <span style={{ width: 30, height: 30, borderRadius: 10, display: "grid", placeItems: "center", background: colors.primarySoft, color: colors.primary }}>
          {icon}
        </span>
        <span>{title}</span>
      </div>
      <div style={{ display: "grid", gap: 10 }}>{children}</div>
    </div>
  );
}

export function Chip({
  label,
  tone = "primary",
}: {
  label: string;
  tone?: "primary" | "warm" | "violet" | "green" | "neutral";
}) {
  const style = {
    primary: { bg: colors.primarySoft, color: colors.primary, border: "#d5e5ff" },
    warm: { bg: colors.amberSoft, color: colors.amber, border: "#ffe3b3" },
    violet: { bg: colors.violetSoft, color: colors.violet, border: "#eadbff" },
    green: { bg: colors.greenSoft, color: colors.green, border: "#caecd8" },
    neutral: { bg: "#f5f8fc", color: colors.sub, border: colors.border },
  }[tone];

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        borderRadius: 999,
        padding: "6px 10px",
        background: style.bg,
        color: style.color,
        border: `1px solid ${style.border}`,
        fontSize: 12,
        fontWeight: 800,
        letterSpacing: 0.15,
      }}
    >
      {label}
    </span>
  );
}

export function KV({ label, value }: { label: string; value?: ReactNode }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "150px minmax(0,1fr)", gap: 10, alignItems: "start" }}>
      <div style={{ color: colors.faint, fontWeight: 700, fontSize: 12, letterSpacing: 0.3 }}>{label.toUpperCase()}</div>
      <div style={{ color: colors.sub, lineHeight: 1.6 }}>{value}</div>
    </div>
  );
}

export function ListCard({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div style={{ border: `1px solid ${colors.border}`, background: "linear-gradient(180deg, #fbfdff 0%, #ffffff 100%)", borderRadius: 16, padding: "14px 16px" }}>
      <div style={{ color: colors.text, fontWeight: 800, fontSize: 13, marginBottom: 10 }}>{title}</div>
      {items.length === 0 ? (
        <div style={{ color: colors.faint, fontSize: 13 }}>{empty}</div>
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

export function ContactActionButton({
  icon,
  label,
  href,
  onClick,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  href?: string;
  onClick?: () => void;
  tone?: "neutral" | "primary" | "green";
}) {
  const style = tone === "green"
    ? { color: colors.green, background: colors.greenSoft, border: "#bfe8d1" }
    : tone === "primary"
      ? { color: colors.primary, background: colors.primarySoft, border: "#cfe0fb" }
      : { color: colors.sub, background: "#ffffff", border: colors.border };

  const commonStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    borderRadius: 12,
    border: `1px solid ${style.border}`,
    background: style.background,
    color: style.color,
    padding: "9px 12px",
    fontSize: 13,
    fontWeight: 700,
    textDecoration: "none",
    cursor: "pointer",
    boxShadow: "0 4px 10px rgba(17,34,68,0.04)",
  };

  if (href) {
    return (
      <a href={href} target={href.startsWith("http") ? "_blank" : undefined} rel={href.startsWith("http") ? "noreferrer" : undefined} style={commonStyle}>
        {icon}
        {label}
      </a>
    );
  }

  return (
    <button type="button" onClick={onClick} style={commonStyle}>
      {icon}
      {label}
    </button>
  );
}

export function SequenceStepCard({
  index,
  step,
}: {
  index: number;
  step: Record<string, unknown>;
}) {
  const channel = String(step.channel || "email");
  const channelTone = channel.includes("connector")
    ? { bg: colors.amberSoft, border: "#ffe3b3", text: colors.amber }
    : channel === "email"
      ? { bg: colors.primarySoft, border: "#d5e5ff", text: colors.primary }
      : { bg: colors.violetSoft, border: "#eadbff", text: colors.violet };

  return (
    <div style={{ border: `1px solid ${channelTone.border}`, background: "linear-gradient(180deg, #ffffff 0%, #fbfdff 100%)", borderRadius: 16, padding: "16px 16px 14px", boxShadow: "0 8px 22px rgba(17,34,68,0.04)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ width: 28, height: 28, borderRadius: 10, background: channelTone.bg, color: channelTone.text, display: "grid", placeItems: "center", fontSize: 12, fontWeight: 900 }}>
            {index + 1}
          </span>
          <div style={{ color: colors.text, fontWeight: 800 }}>
            Day {String(step.day_offset ?? 0)} · {channel.replace(/_/g, " ")}
          </div>
        </div>
        <Chip label={`Step ${index + 1}`} tone={channel.includes("connector") ? "warm" : "primary"} />
      </div>
      <div style={{ marginTop: 10, color: colors.sub, lineHeight: 1.65, fontSize: 13.5 }}>
        <strong style={{ color: colors.text }}>Objective:</strong> {String(step.objective || "No objective")}
      </div>
      {step.angle ? (
        <div style={{ marginTop: 6, color: colors.sub, lineHeight: 1.65, fontSize: 13.5 }}>
          <strong style={{ color: colors.text }}>Angle:</strong> {String(step.angle)}
        </div>
      ) : null}
      {step.cta ? (
        <div style={{ marginTop: 8, padding: "10px 12px", borderRadius: 12, background: channelTone.bg, color: colors.text, fontSize: 13.5, lineHeight: 1.55 }}>
          <strong>CTA:</strong> {String(step.cta)}
        </div>
      ) : null}
    </div>
  );
}

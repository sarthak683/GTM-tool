import { CSSProperties } from "react";

import type { Company } from "../types";

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

export const PRIORITY_STYLE: Record<"high" | "medium" | "low", CSSProperties> = {
  high: { background: "#e8f8f0", color: "#1f8f5f" },
  medium: { background: "#fff4df", color: "#b56d00" },
  low: { background: "#eef2f7", color: "#5e6d83" },
};

export const TIER_OPTIONS = [
  { value: "hot", label: "Hot" },
  { value: "warm", label: "Warm" },
  { value: "monitor", label: "Monitor" },
  { value: "cold", label: "Cold" },
];

export const DISPOSITION_OPTIONS = [
  { value: "__empty__", label: "Unreviewed" },
  { value: "working", label: "Working" },
  { value: "interested", label: "Interested" },
  { value: "nurture", label: "Nurture" },
  { value: "not_interested", label: "Not Interested" },
  { value: "bad_fit", label: "Bad Fit" },
  { value: "do_not_target", label: "Do Not Target" },
];

export const OUTREACH_LANE_OPTIONS = [
  { value: "__empty__", label: "Auto / Unset" },
  { value: "warm_intro", label: "Warm Intro" },
  { value: "event_follow_up", label: "Event Follow-up" },
  { value: "cold_operator", label: "Cold Operator" },
  { value: "cold_strategic", label: "Cold Strategic" },
];

export function parseSearchParamList(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export const pageStyle: CSSProperties = {
  background: colors.bg,
  minHeight: "100%",
  padding: "32px 28px 40px",
};

export const containerStyle: CSSProperties = {
  maxWidth: 1450,
  margin: "0 auto",
  display: "grid",
  gap: 18,
};

export const cardStyle: CSSProperties = {
  background: colors.card,
  border: `1px solid ${colors.border}`,
  borderRadius: 22,
  boxShadow: "0 6px 20px rgba(17,34,68,0.05)",
};

export function ts(date?: string): string {
  if (!date) return "Never";
  return new Date(date).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatBatchStage(stage?: string, status?: string): string {
  const key = (stage || status || "").toLowerCase();
  const labels: Record<string, string> = {
    upload_received: "Upload Received",
    tal_review: "Awaiting TAL Approval",
    queued: "Queued",
    research_running: "Research Running",
    processing: "Research Running",
    pending: "Queued",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return labels[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "Unknown";
}

export function getIcpAnalysis(company: Company): Record<string, unknown> | undefined {
  const cache = company.enrichment_cache;
  if (!cache || typeof cache !== "object") return undefined;
  const entry = (cache as Record<string, unknown>).icp_analysis;
  if (!entry || typeof entry !== "object") return undefined;
  const data = (entry as Record<string, unknown>).data;
  return data && typeof data === "object" ? (data as Record<string, unknown>) : (entry as Record<string, unknown>);
}

export function getSalesPlay(company: Company): Record<string, unknown> | undefined {
  const profile = company.prospecting_profile;
  if (profile && typeof profile === "object") {
    const salesPlay = (profile as Record<string, unknown>).sales_play;
    if (salesPlay && typeof salesPlay === "object") return salesPlay as Record<string, unknown>;
  }
  const cache = company.enrichment_cache;
  if (!cache || typeof cache !== "object") return undefined;
  const icpEntry = (cache as Record<string, unknown>).icp_analysis;
  if (!icpEntry || typeof icpEntry !== "object") return undefined;
  const salesPlay = (icpEntry as Record<string, unknown>).sales_play;
  return salesPlay && typeof salesPlay === "object" ? (salesPlay as Record<string, unknown>) : undefined;
}

export function asText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const cleaned = value
    .replace(/\s+/g, " ")
    .replace(/\s*\/\s*/g, " / ")
    .trim();
  return cleaned || undefined;
}

export function parseManualCompanyLines(input: string): Array<{ name: string; domain?: string }> {
  return input
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line
        .split(/[|,]/)
        .map((part) => part.trim())
        .filter(Boolean);
      const [name, domain] = parts;
      return {
        name: name || "",
        domain: domain || undefined,
      };
    })
    .filter((entry) => entry.name);
}

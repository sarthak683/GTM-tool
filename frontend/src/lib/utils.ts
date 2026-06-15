import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Company } from "../types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatCurrency(value?: number | null): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

export function isPlaceholderDomain(value?: string | null): boolean {
  const domain = (value || "").trim().toLowerCase();
  if (!domain) return false;
  return domain.endsWith(".unknown") || /^\d+$/.test(domain);
}

export function formatDomain(value?: string | null, fallback = "Domain not found"): string {
  const domain = (value || "").trim();
  if (!domain) return fallback;
  return isPlaceholderDomain(domain) ? fallback : domain;
}

/**
 * Pull the most likely customer/account name out of a meeting title.
 *
 * Mirrors `_extract_title_candidates` in app/services/tldv_sync.py so the
 * frontend shows the same candidate the backend will resolve to. Handles
 * em-dash / en-dash separators, Beacon-on-either-side ("Beacon x Acme",
 * "Acme x Beacon"), parenthetical noise, and meeting-type keywords —
 * tolerant of misspellings (matches by ignore-list, not strict regex).
 *
 * Returns the first non-Beacon candidate, falling back to the trimmed
 * title when nothing parses out cleanly. Never returns the literal title
 * for cases like "POC Kickoff Disucssion – Beacon<>Fabtech" (which used to
 * yield the meeting-name half because em-dash wasn't a split separator).
 */
export function suggestCompanyNameFromMeetingTitle(title: string | null | undefined): string {
  const raw = (title || "").trim();
  if (!raw) return "";

  // Replace all known separators with a single sentinel so split is uniform.
  // Includes em-dash (—), en-dash (–), and ASCII hyphen surrounded by spaces
  // (so we don't break hyphenated company names like "Coca-Cola").
  const SEP = "|";
  const normalized = raw
    .replace(/\[STAGING COPY\]/gi, " ")
    .replace(/🤝|<>|<->|\sx\s|\sX\s|\swith\s|\sand\s|~|\||\s-\s|\s–\s|\s—\s|:|\//g, SEP)
    .replace(/\s+/g, " ")
    .trim();

  const ignored = new Set([
    "beacon", "beacon.li", "zippy",
    "introduction", "intro", "connect", "sync", "call", "meeting",
    "demo", "walkthrough", "discussion", "disucssion", // include common typo
    "cadence", "kick off", "kickoff", "clarification", "interview",
    "next steps", "sales review", "demo recording", "recording",
    "tech deep dive", "in-person tech deep dive", "catch up",
    "poc", "pilot", "qbr", "review",
  ]);

  const candidates: string[] = [];
  for (const part of normalized.split(SEP)) {
    // Strip parenthetical noise like "(notes)" or "(reschedule)".
    let cleaned = part.replace(/\(.*?\)/g, "").replace(/\s+/g, " ").trim();
    if (!cleaned) continue;
    let lowered = cleaned.toLowerCase();
    if (ignored.has(lowered)) continue;
    // Trim "Beacon " prefix / " Beacon" suffix, then re-evaluate.
    if (lowered.startsWith("beacon ")) {
      cleaned = cleaned.slice(7).trim();
      lowered = cleaned.toLowerCase();
    }
    if (lowered.endsWith(" beacon")) {
      cleaned = cleaned.slice(0, -7).trim();
      lowered = cleaned.toLowerCase();
    }
    // Drop fragments that are entirely meeting-type words even after stripping.
    // Example: "POC Kickoff Disucssion" should not become an account name.
    const words = lowered.split(/\s+/).filter(Boolean);
    if (words.length > 0 && words.every((w) => ignored.has(w))) continue;
    if (cleaned.length < 2) continue;
    if (!ignored.has(lowered)) candidates.push(cleaned);
  }

  // Dedupe while preserving order.
  const seen = new Set<string>();
  const ordered = candidates.filter((c) => {
    const key = c.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return ordered[0] || raw;
}

export function formatDate(dateStr?: string | null): string {
  if (!dateStr) return "—";
  return new Date(dateStr).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function isValidDateValue(dateStr?: string | null): boolean {
  if (!dateStr) return false;
  return !Number.isNaN(new Date(dateStr).getTime());
}

export function formatOptionalDate(dateStr?: string | null, fallback = "No date set"): string {
  if (!dateStr) return fallback;
  return isValidDateValue(dateStr) ? formatDate(dateStr) : "Invalid date";
}

export function gmailComposeUrl(email: string, subject?: string, body?: string): string {
  const params = new URLSearchParams({
    view: "cm",
    to: email,
  });
  if (subject) params.set("su", subject);
  if (body) params.set("body", body);
  return `https://mail.google.com/mail/?${params.toString()}`;
}

export function getInitials(name: string): string {
  return name
    .split(/[\s-_]/)
    .map((n) => n[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

// Deterministic avatar color based on first character — Clay-style
const AVATAR_PALETTES = [
  "bg-violet-100 text-violet-700",
  "bg-blue-100 text-blue-700",
  "bg-emerald-100 text-emerald-700",
  "bg-orange-100 text-orange-700",
  "bg-rose-100 text-rose-700",
  "bg-teal-100 text-teal-700",
  "bg-amber-100 text-amber-700",
  "bg-cyan-100 text-cyan-700",
  "bg-pink-100 text-pink-700",
  "bg-indigo-100 text-indigo-700",
];

export function avatarColor(name: string): string {
  const code = (name?.charCodeAt(0) ?? 0) + (name?.charCodeAt(1) ?? 0);
  return AVATAR_PALETTES[code % AVATAR_PALETTES.length];
}

export type AccountPrioritySnapshot = {
  priorityScore: number;
  priorityBand: "high" | "medium" | "low";
  interestLevel: "high" | "medium" | "low";
  interestScore: number;
  committeeScore: number;
  outreachLeverage: number;
};

export function getAccountPrioritySnapshot(company: Company): AccountPrioritySnapshot {
  const cache = (company.enrichment_cache || {}) as Record<string, unknown>;
  const committeeEntry = cache.committee_coverage as { data?: { coverage_score?: number } } | undefined;
  const committee = (committeeEntry?.data ?? committeeEntry) as { coverage_score?: number } | undefined;
  const committeeScore = typeof committee?.coverage_score === "number" ? committee.coverage_score : 0;

  const intent = (company.intent_signals || {}) as Record<string, unknown>;
  const uploadedIntentScore = Number(intent.uploaded_intent_score || 0) * 10;
  const positiveSignalCount = Number(intent.positive_signal_count || 0);
  const negativeSignalCount = Number(intent.negative_signal_count || 0);

  let inferredIntent = Math.min(
    100,
    uploadedIntentScore
      + (Number(intent.hiring || 0) * 14)
      + (Number(intent.funding || 0) * 18)
      + (Number(intent.product || 0) * 10)
      + (positiveSignalCount * 5)
  );
  inferredIntent = Math.max(inferredIntent - (negativeSignalCount * 8), 0);
  const profile = (company.prospecting_profile || {}) as Record<string, unknown>;
  const warmPaths = Array.isArray(profile.warm_paths) ? profile.warm_paths as Array<Record<string, unknown>> : [];
  const strongestWarmPath = warmPaths.reduce((best, item) => Math.max(best, Number(item?.strength || 0)), 0);
  let outreachLeverage = Math.min(100, strongestWarmPath * 22);
  if ((company.recommended_outreach_lane || "").trim().toLowerCase() === "event_follow_up") {
    outreachLeverage = Math.max(outreachLeverage, 56);
  } else if ((company.recommended_outreach_lane || "").trim().toLowerCase() === "warm_intro") {
    outreachLeverage = Math.max(outreachLeverage, 72);
  }

  const disposition = (company.disposition || "").trim().toLowerCase();
  const outreachStatus = (company.outreach_status || "").trim().toLowerCase();

  let interestScore = Math.max(uploadedIntentScore, inferredIntent);
  if (disposition === "interested") interestScore = 92;
  else if (disposition === "working") interestScore = Math.max(interestScore, 68);
  else if (disposition === "nurture") interestScore = Math.min(Math.max(interestScore, 42), 60);
  else if (disposition === "not_interested") interestScore = 8;
  else if (disposition === "bad_fit" || disposition === "do_not_target") interestScore = 0;

  if (outreachStatus === "meeting_booked") interestScore = Math.max(interestScore, 90);
  else if (outreachStatus === "replied") interestScore = Math.max(interestScore, 72);
  else if (outreachStatus === "contacted") interestScore = Math.max(interestScore, 48);

  let priorityScore = Math.round(
    (Number(company.icp_score || 0) * 0.52)
      + (inferredIntent * 0.20)
      + (committeeScore * 0.13)
      + (interestScore * 0.10)
      + (outreachLeverage * 0.05)
  );

  if (disposition === "not_interested" || disposition === "bad_fit" || disposition === "do_not_target") {
    priorityScore = Math.min(priorityScore, 20);
  } else if (disposition === "interested") {
    priorityScore = Math.max(priorityScore, 78);
  }
  priorityScore = Math.max(Math.min(priorityScore, 100), 0);

  const priorityBand =
    priorityScore >= 75 ? "high" : priorityScore >= 50 ? "medium" : "low";
  const interestLevel =
    interestScore >= 75 ? "high" : interestScore >= 45 ? "medium" : "low";

  return {
    priorityScore,
    priorityBand,
    interestLevel,
    interestScore,
    committeeScore,
    outreachLeverage,
  };
}

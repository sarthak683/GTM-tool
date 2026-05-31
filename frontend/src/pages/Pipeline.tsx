import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, Building2, CalendarDays, ChevronDown, Clock3, DollarSign, Download, FileText, Filter, Globe, GripVertical, Mail, MoreHorizontal, Phone, Plus, RotateCcw, Search, Settings2, Target, TrendingUp, Trash2, Upload, UserCircle2 } from "lucide-react";
import { activitiesApi, authApi, companiesApi, contactsApi, crmImportsApi, dealsApi, settingsApi } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import type { Activity, Company, Contact, CrmImportResponse, Deal, DealStageSetting, PipelineSummarySettings, RolePermissionsSettings, User } from "../types";
import { avatarColor, formatCurrency, formatDate, getInitials } from "../lib/utils";
import DealDetailDrawer from "../components/deal/DealDetailDrawer";
import SearchableCompanySelect from "../components/SearchableCompanySelect";

type PipelineTab = "deal" | "prospect";
type ProspectStageId = "outreach" | "in_progress" | "meeting_booked" | "negative_response" | "no_response" | "not_a_fit";
type DragItem = { kind: "deal"; id: string; fromStage: string } | { kind: "prospect"; id: string; fromStage: ProspectStageId };
type PendingDealMove = { dealId: string; dealName: string; fromStage: string; targetStage: string };
type StageMeta = { id: string; label: string; group: "active" | "closed"; color?: string };
type FunnelKey = "active" | "inactive" | "tofu" | "mofu" | "bofu";
type FunnelConfig = Record<FunnelKey, string[]>;
type SummaryCardKey = "active" | "inactive" | "tofu" | "mofu" | "bofu" | "total";
type PipelineSummarySectionConfig = PipelineSummarySettings["deal"];
type CsvRow = Record<string, string | number | boolean | null | undefined>;

const DEFAULT_DEAL_STAGES: StageMeta[] = [
  { id: "reprospect", label: "REPROSPECT", group: "active", color: "#8b5cf6" },
  { id: "demo_scheduled", label: "4.DEMO SCHEDULED", group: "active", color: "#4f6ddf" },
  { id: "demo_done", label: "5.DEMO DONE", group: "active", color: "#1d4ed8" },
  { id: "qualified_lead", label: "6.QUALIFIED LEAD", group: "active", color: "#6d5efc" },
  { id: "poc_agreed", label: "7.POC AGREED", group: "active", color: "#0ea5e9" },
  { id: "poc_wip", label: "8.POC WIP", group: "active", color: "#06b6d4" },
  { id: "poc_done", label: "9.POC DONE", group: "active", color: "#14b8a6" },
  { id: "commercial_negotiation", label: "10.COMMERCIAL NEGOTIATION", group: "active", color: "#f59e0b" },
  { id: "msa_review", label: "11.WORKSHOP/MSA", group: "active", color: "#a855f7" },
  { id: "closed_won", label: "12.CLOSED WON", group: "closed", color: "#22c55e" },
  { id: "churned", label: "CHURNED", group: "closed", color: "#ef4444" },
  { id: "not_a_fit", label: "NOT FIT", group: "closed", color: "#9ca3af" },
  { id: "cold", label: "COLD", group: "closed", color: "#94a3b8" },
  { id: "closed_lost", label: "CLOSED LOST", group: "closed", color: "#7c8da4" },
  { id: "on_hold", label: "ON HOLD - REVISIT LATER", group: "closed", color: "#7c3aed" },
  { id: "nurture", label: "NURTURE - FUTURE FIT", group: "closed", color: "#2dd4bf" },
  { id: "closed", label: "CLOSED", group: "closed", color: "#64748b" },
];

const PROSPECT_STAGES: Array<StageMeta & { id: ProspectStageId }> = [
  { id: "outreach", label: "Outreach", group: "active" },
  { id: "in_progress", label: "In Progress", group: "active" },
  { id: "meeting_booked", label: "Meeting Booked", group: "active" },
  { id: "negative_response", label: "Negative Response", group: "closed" },
  { id: "no_response", label: "No Response", group: "closed" },
  { id: "not_a_fit", label: "Not a Fit", group: "closed" },
];

const STAGE_COLOR: Record<string, string> = {
  reprospect: "#8b5cf6", demo_scheduled: "#6366f1", demo_done: "#8b5cf6", qualified_lead: "#2563eb",
  poc_agreed: "#0ea5e9", poc_wip: "#06b6d4", poc_done: "#14b8a6", commercial_negotiation: "#f59e0b",
  msa_review: "#a855f7", workshop: "#f97316", closed_won: "#22c55e", closed_lost: "#94a3b8",
  not_a_fit: "#9ca3af", on_hold: "#a78bfa", nurture: "#67e8f9", churned: "#ef4444",
  outreach: "#2563eb", in_progress: "#7c3aed", meeting_booked: "#0ea5e9", negative_response: "#ef4444", no_response: "#94a3b8",
};
const DEFAULT_VISIBLE_SUMMARY_CARDS: SummaryCardKey[] = ["active", "inactive", "tofu", "mofu", "bofu", "total"];
const SUMMARY_CARD_META: Array<{ key: SummaryCardKey; label: string; tone?: "default" | "accent" | "success" }> = [
  { key: "active", label: "Active", tone: "accent" },
  { key: "inactive", label: "Inactive" },
  { key: "tofu", label: "ToFU" },
  { key: "mofu", label: "MoFU" },
  { key: "bofu", label: "BoFU", tone: "success" },
  { key: "total", label: "Total" },
];
const DEFAULT_FUNNEL: FunnelConfig = {
  active: ["reprospect", "demo_scheduled", "demo_done", "qualified_lead", "poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "msa_review"],
  inactive: ["closed_won", "churned", "not_a_fit", "cold", "closed_lost", "on_hold", "nurture", "closed"],
  tofu: ["qualified_lead", "poc_agreed"],
  mofu: ["poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop"],
  bofu: ["closed_won"],
};
const DEFAULT_PROSPECT_FUNNEL: FunnelConfig = {
  active: ["outreach", "in_progress", "meeting_booked"],
  inactive: ["negative_response", "no_response", "not_a_fit"],
  tofu: ["outreach"],
  mofu: ["in_progress"],
  bofu: ["meeting_booked"],
};
const GEO_OPTIONS = ["India", "America", "Rest of the World"] as const;
const ENGAGEMENT_SIGNAL_LEGEND = "Signal legend: Active = last touch within 3d; Watch = 4-7d; Stale = older than 7d; No signal = no captured activity yet.";

function normalizeBucketConfig(value: Partial<FunnelConfig> | undefined, defaults: FunnelConfig): FunnelConfig {
  return {
    active: Array.isArray(value?.active) ? value.active : defaults.active,
    inactive: Array.isArray(value?.inactive) ? value.inactive : defaults.inactive,
    tofu: Array.isArray(value?.tofu) ? value.tofu : defaults.tofu,
    mofu: Array.isArray(value?.mofu) ? value.mofu : defaults.mofu,
    bofu: Array.isArray(value?.bofu) ? value.bofu : defaults.bofu,
  };
}

function normalizeVisibleCards(value?: string[] | null): SummaryCardKey[] {
  const normalized = (value ?? []).filter((item): item is SummaryCardKey => DEFAULT_VISIBLE_SUMMARY_CARDS.includes(item as SummaryCardKey));
  return normalized.length ? normalized : DEFAULT_VISIBLE_SUMMARY_CARDS;
}

function normalizePipelineSummarySettings(value?: Partial<PipelineSummarySettings> | null): PipelineSummarySettings {
  return {
    deal: {
      ...normalizeBucketConfig(value?.deal, DEFAULT_FUNNEL),
      visible_cards: normalizeVisibleCards(value?.deal?.visible_cards),
    },
    prospect: {
      ...normalizeBucketConfig(value?.prospect, DEFAULT_PROSPECT_FUNNEL),
      visible_cards: normalizeVisibleCards(value?.prospect?.visible_cards),
    },
  };
}

function csvCell(value: CsvRow[string]): string {
  const raw = value == null ? "" : String(value);
  return /[",\r\n]/.test(raw) ? `"${raw.replace(/"/g, '""')}"` : raw;
}

function csvFromRows(rows: CsvRow[]): string {
  if (!rows.length) return "";
  const headers = Object.keys(rows[0]);
  return [
    headers.map(csvCell).join(","),
    ...rows.map((row) => headers.map((header) => csvCell(row[header])).join(",")),
  ].join("\r\n");
}

function downloadCsv(filename: string, rows: CsvRow[]) {
  const csv = csvFromRows(rows);
  if (!csv) return;
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function fileSafeSegment(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "pipeline";
}

// Engagement timestamps are naive-UTC (no trailing Z). Parsing them with a bare
// `new Date()` makes JS read them as LOCAL time, shifting "Active/Watch/Stale"
// and "Xh ago" by the user's UTC offset (5.5h for IST). Force UTC.
function utcMs(timestamp: string): number {
  return new Date(timestamp.endsWith("Z") ? timestamp : `${timestamp}Z`).getTime();
}

function relativeTime(timestamp?: string): string {
  if (!timestamp) return "";
  const ageMs = Date.now() - utcMs(timestamp);
  const ageHours = ageMs / (1000 * 60 * 60);
  if (ageHours < 1) return "just now";
  if (ageHours < 24) return `${Math.max(1, Math.floor(ageHours))}h ago`;
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays < 1) return "today";
  if (ageDays < 2) return "yesterday";
  return `${Math.floor(ageDays)}d ago`;
}

function engagementTone(timestamp?: string) {
  if (!timestamp) {
    return { label: "No signal", background: "#f8fafc", color: "#7a8ca1", border: "#d9e3ef", accent: "#cbd5e1" };
  }
  const ageMs = Date.now() - utcMs(timestamp);
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays <= 3) {
    return { label: "Active", background: "#ecfdf3", color: "#15803d", border: "#bbf7d0", accent: "#22c55e" };
  }
  if (ageDays <= 7) {
    return { label: "Watch", background: "#fff7ed", color: "#c2410c", border: "#fed7aa", accent: "#f59e0b" };
  }
  return { label: "Stale", background: "#fff1f2", color: "#be123c", border: "#fecdd3", accent: "#f43f5e" };
}

type EngagementSignal = NonNullable<Deal["seller_engagement_signal"]>;

function engagementSummary(signal: EngagementSignal | undefined, side: "rep" | "client") {
  if (!signal) {
    return {
      title: side === "rep" ? "No rep touch yet" : "No client touch yet",
      detail: side === "rep" ? "Waiting for outreach activity" : "Waiting for buyer activity",
      Icon: Clock3,
    };
  }

  switch (signal.type) {
    case "email":
      return {
        title: side === "rep" ? "Rep email sent" : "Client email",
        detail: signal.label,
        Icon: Mail,
      };
    case "call":
      return {
        title: side === "rep" ? "Call logged" : "Client on call",
        detail: signal.label,
        Icon: Phone,
      };
    case "meeting":
      return {
        title: side === "rep" ? "Meeting touch" : "Client meeting",
        detail: signal.label,
        Icon: CalendarDays,
      };
    case "transcript":
      return {
        title: side === "rep" ? "Meeting intel" : "Conversation captured",
        detail: signal.label,
        Icon: FileText,
      };
    case "note":
      return {
        title: "Rep note added",
        detail: signal.label,
        Icon: FileText,
      };
    default:
      return {
        title: side === "rep" ? "Rep activity" : "Client activity",
        detail: signal.label,
        Icon: Clock3,
      };
  }
}

function EngagementBadge({
  side,
  timestamp,
  signal,
  reason,
}: {
  side: "rep" | "client";
  timestamp?: string;
  signal?: EngagementSignal;
  reason?: string;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const badgeRef = useRef<HTMLDivElement | null>(null);
  const tone = engagementTone(timestamp);
  const summary = engagementSummary(signal, side);
  const Icon = summary.Icon;
  const compactReason = (reason || signal?.reason || summary.detail || "").trim();
  const line = compactReason ? `${compactReason}${timestamp ? ` · ${relativeTime(timestamp)}` : ""}` : (timestamp ? relativeTime(timestamp) : summary.detail);
  const secondary = signal?.label && signal.label !== compactReason ? signal.label : summary.detail;
  const basis = signal?.label || (side === "rep" ? "No seller-side source yet" : "No buyer-side source yet");

  const POPOVER_WIDTH = 220;
  const handleEnter = () => {
    const rect = badgeRef.current?.getBoundingClientRect();
    if (rect) {
      const left = Math.min(
        Math.max(8, rect.left),
        window.innerWidth - POPOVER_WIDTH - 8,
      );
      setCoords({ top: rect.bottom + 8, left });
    }
    setShowDetail(true);
  };

  return (
    <div
      ref={badgeRef}
      onMouseEnter={handleEnter}
      onMouseLeave={() => setShowDetail(false)}
      style={{
        minWidth: 0,
        flex: 1,
        position: "relative",
        borderRadius: 999,
        border: `1px solid ${tone.border}`,
        background: "#ffffff",
        padding: "3px 6px",
        display: "flex",
        alignItems: "center",
        gap: 5,
        cursor: "default",
      }}
    >
      <span
        style={{
          width: 12,
          height: 12,
          borderRadius: 999,
          background: "#ffffff",
          border: `1px solid ${tone.border}`,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          color: tone.color,
          flexShrink: 0,
        }}
      >
        <Icon size={8} />
      </span>
      <span style={{ fontSize: 8, fontWeight: 800, color: "#6f7f95", textTransform: "uppercase", letterSpacing: "0.05em", flexShrink: 0 }}>
        {side === "rep" ? "REP" : "CL"}
      </span>
      <span style={{ fontSize: 8, fontWeight: 700, color: tone.color, flexShrink: 0 }}>
        {tone.label}
      </span>
      <span style={{ width: 3, height: 3, borderRadius: 999, background: tone.accent, flexShrink: 0 }} />
      <span style={{ fontSize: 9, color: "#7f8ea3", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>
        {line}
      </span>
      {showDetail && coords && createPortal(
        <div
          onClick={(event) => event.stopPropagation()}
          style={{
            position: "fixed",
            top: coords.top,
            left: coords.left,
            transform: "none",
            zIndex: 9999,
            width: POPOVER_WIDTH,
            pointerEvents: "auto",
            borderRadius: 12,
            border: "1px solid #dbe6f2",
            background: "#ffffff",
            boxShadow: "0 16px 36px rgba(15, 23, 42, 0.18)",
            padding: "10px 12px",
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 800, color: "#6f7f95", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              {side === "rep" ? "Rep engagement" : "Client engagement"}
            </span>
            <span style={{ fontSize: 10, fontWeight: 700, color: tone.color }}>
              {tone.label}
            </span>
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#1f2d3d", lineHeight: 1.35 }}>
            {compactReason || summary.title}
          </div>
          {secondary && (
            <div style={{ fontSize: 11, color: "#62748a", lineHeight: 1.4 }}>
              {secondary}
            </div>
          )}
          <div style={{ fontSize: 11, color: "#33485f", lineHeight: 1.45 }}>
            <span style={{ fontWeight: 700 }}>Based on:</span> {basis}{signal?.source_label ? ` · via ${signal.source_label}` : ""}
          </div>
          {signal?.detail ? (
            <div style={{ fontSize: 11, color: "#33485f", lineHeight: 1.45 }}>
              <span style={{ fontWeight: 700 }}>What happened:</span> {signal.detail}
            </div>
          ) : null}
          <div style={{ fontSize: 10, color: "#8ca0b3", lineHeight: 1.4 }}>
            {ENGAGEMENT_SIGNAL_LEGEND}
          </div>
          {timestamp && (
            <div style={{ fontSize: 10, color: "#8ca0b3" }}>
              Last touch {relativeTime(timestamp)}
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}

function normalizeGeo(raw?: string | null): "India" | "America" | "Rest of the World" | "" {
  const value = (raw ?? "").trim().toLowerCase();
  if (!value) return "";
  if (["us", "usa", "united states", "united states of america"].includes(value)) return "America";
  if (["na", "north america", "america", "americas", "latam", "latin america", "canada", "mexico"].includes(value)) return "America";
  if (["india", "in"].includes(value)) return "India";
  if (["apac", "asia pacific", "asia-pacific", "anz", "australia", "new zealand", "singapore", "japan", "rest of world", "rest of the world", "row"].includes(value)) return "Rest of the World";
  return "Rest of the World";
}

function contactName(contact: Contact) {
  return `${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim() || contact.email || "Unnamed Prospect";
}

function prospectStage(contact: Contact): ProspectStageId {
  const text = [contact.outreach_lane, contact.sequence_status, contact.instantly_status, contact.tracking_stage, contact.tracking_summary].filter(Boolean).join(" ").toLowerCase();
  if ((contact.outreach_lane ?? "").toLowerCase() === "not_a_fit" || text.includes("not a fit")) return "not_a_fit";
  if (text.includes("meeting booked") || text.includes("meeting_booked") || text.includes("call booked") || text.includes("demo booked")) return "meeting_booked";
  if (text.includes("negative") || text.includes("not interested") || text.includes("unsubscribed") || text.includes("do not contact") || text.includes("bounce")) return "negative_response";
  if (text.includes("no response") || text.includes("sequence complete") || text.includes("completed")) return "no_response";
  if (text.includes("active") || text.includes("sent") || text.includes("replied") || text.includes("engaged") || text.includes("queued") || text.includes("in progress")) return "in_progress";
  return "outreach";
}

function prospectPatch(stage: ProspectStageId): Partial<Contact> {
  if (stage === "outreach") return { outreach_lane: "outreach", sequence_status: "draft", instantly_status: "not_started" };
  if (stage === "in_progress") return { outreach_lane: "in_progress", sequence_status: "active", instantly_status: "active" };
  if (stage === "meeting_booked") return { outreach_lane: "meeting_booked", sequence_status: "meeting_booked", instantly_status: "meeting_booked" };
  if (stage === "negative_response") return { outreach_lane: "negative_response", sequence_status: "completed", instantly_status: "negative_response" };
  if (stage === "no_response") return { outreach_lane: "no_response", sequence_status: "completed", instantly_status: "completed" };
  return { outreach_lane: "not_a_fit", sequence_status: "completed", instantly_status: "not_a_fit" };
}

function MultiSelectFilter({
  values,
  onChange,
  options,
  label,
  allLabel,
}: {
  values: string[];
  onChange: (value: string[]) => void;
  options: { value: string; label: string }[];
  label: string;
  allLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [filterText, setFilterText] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) {
        setOpen(false);
        setFilterText("");
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 40);
  }, [open]);

  const toggle = (value: string) => {
    onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  };

  const visibleOptions = filterText
    ? options.filter((option) => option.label.toLowerCase().includes(filterText.toLowerCase()))
    : options;

  const displayLabel =
    values.length === 0
      ? allLabel
      : values.length === 1
        ? options.find((option) => option.value === values[0])?.label ?? allLabel
        : `${values.length} selected`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px" }}>{label}</label>
      <div ref={ref} style={{ position: "relative" }}>
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          style={{ width: "100%", height: 38, borderRadius: 12, border: values.length ? "1.5px solid #cfe89a" : "1px solid #e2eaf2", background: values.length ? "#f3fbe3" : "#f8fafc", padding: "0 28px 0 10px", fontSize: 12, fontWeight: 600, color: "#2d4258", cursor: "pointer", outline: "none", textAlign: "left", position: "relative" }}
        >
          {displayLabel}
          {values.length > 1 && (
            <span style={{ position: "absolute", right: 28, top: "50%", transform: "translateY(-50%)", minWidth: 18, height: 18, padding: "0 6px", borderRadius: 999, background: "#9ace3d", color: "#fff", fontSize: 10, fontWeight: 800, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
              {values.length}
            </span>
          )}
          <ChevronDown size={12} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none", color: "#7a96b0" }} />
        </button>
        {open && (
          <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, right: 0, zIndex: 20, borderRadius: 14, border: "1px solid #dbe6f2", background: "#fff", boxShadow: "0 18px 36px rgba(15,23,42,0.14)", padding: 8, display: "flex", flexDirection: "column", gap: 6, maxHeight: 280 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "2px 4px 0" }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: "#6f8095", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</span>
              {values.length > 0 && (
                <button
                  type="button"
                  onClick={() => onChange([])}
                  style={{ border: "none", background: "transparent", color: "#9ace3d", fontSize: 11, fontWeight: 800, cursor: "pointer" }}
                >
                  Clear
                </button>
              )}
            </div>
            {/* Search input */}
            <div style={{ position: "relative", flexShrink: 0 }}>
              <Search size={11} style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", color: "#94a3b8", pointerEvents: "none" }} />
              <input
                ref={inputRef}
                type="text"
                placeholder={`Search ${label.toLowerCase()}…`}
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                style={{ width: "100%", height: 30, borderRadius: 7, border: "1px solid #e2eaf2", background: "#f8fafc", paddingLeft: 26, paddingRight: 8, fontSize: 11, outline: "none", boxSizing: "border-box" }}
              />
            </div>
            {/* Scrollable list */}
            <div style={{ overflowY: "auto", maxHeight: 190, display: "flex", flexDirection: "column", gap: 2 }}>
              {!filterText && (
                <button
                  type="button"
                  onClick={() => onChange([])}
                  style={{ border: "none", background: values.length === 0 ? "#f3fbe3" : "transparent", color: values.length === 0 ? "#4d7c0f" : "#4d6178", borderRadius: 8, padding: "7px 8px", textAlign: "left", fontSize: 12, fontWeight: 700, cursor: "pointer", flexShrink: 0 }}
                >
                  {allLabel}
                </button>
              )}
              {visibleOptions.length === 0 && (
                <div style={{ padding: "8px 10px", fontSize: 11, color: "#94a3b8" }}>No matches</div>
              )}
              {visibleOptions.map((option) => (
                <label key={option.value} style={{ display: "flex", alignItems: "center", gap: 8, borderRadius: 8, padding: "7px 8px", background: values.includes(option.value) ? "#fff7f2" : "transparent", color: "#2d4258", fontSize: 12, cursor: "pointer", flexShrink: 0 }}>
                  <input type="checkbox" checked={values.includes(option.value)} onChange={() => toggle(option.value)} />
                  <span>{option.label}</span>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SummaryCard({ label, value, tone = "default", action }: { label: string; value: string | number; tone?: "default" | "accent" | "success"; action?: ReactNode }) {
  const palette = tone === "accent" ? { bg: "#f3fbe3", border: "#cfe89a", value: "#4d7c0f" } : tone === "success" ? { bg: "#f0fdf4", border: "#bbf7d0", value: "#15803d" } : { bg: "#f8fafc", border: "#e8eef5", value: "#48607b" };
  return (
    <div style={{ padding: "10px 10px", borderRadius: 10, background: palette.bg, border: `1px solid ${palette.border}` }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: palette.value, lineHeight: 1 }}>{value}</div>
        {action}
      </div>
      <div style={{ fontSize: 10, color: "#7a96b0", marginTop: 3 }}>{label}</div>
    </div>
  );
}

const chip = (background: string, color: string, border: string) => ({ fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background, color, border: `1px solid ${border}` } as const);
const modalInputStyle = { height: 42, borderRadius: 12, border: "1px solid #d7e2ee", padding: "0 14px", fontSize: 14 } as const;

function FunnelSettingsModal({
  title,
  description,
  stages,
  config,
  defaultConfig,
  saving,
  onSave,
  onClose,
}: {
  title: string;
  description: string;
  stages: StageMeta[];
  config: PipelineSummarySectionConfig;
  defaultConfig: PipelineSummarySectionConfig;
  saving: boolean;
  onSave: (config: PipelineSummarySectionConfig) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<PipelineSummarySectionConfig>(config);
  const toggle = (bucket: FunnelKey, stageId: string) => setDraft((current) => ({
    ...current,
    [bucket]: current[bucket].includes(stageId) ? current[bucket].filter((item) => item !== stageId) : [...current[bucket], stageId],
  }));
  const toggleCard = (cardKey: SummaryCardKey) => setDraft((current) => ({
    ...current,
    visible_cards: current.visible_cards.includes(cardKey)
      ? current.visible_cards.filter((item) => item !== cardKey)
      : [...current.visible_cards, cardKey],
  }));

  useEffect(() => {
    setDraft(config);
  }, [config]);

  return (
    <>
      <div style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.25)", zIndex: 60 }} onClick={onClose} />
      <div style={{ position: "fixed", inset: 0, zIndex: 61, display: "grid", placeItems: "center", padding: 16 }}>
        <div style={{ width: "100%", maxWidth: 1080, background: "#fff", borderRadius: 20, border: "1px solid #dbe6f2", boxShadow: "0 20px 60px rgba(15,23,42,0.15)", padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
            <div>
              <h3 style={{ fontSize: 18, fontWeight: 700, color: "#1f2d3d" }}>{title}</h3>
              <p style={{ fontSize: 12, color: "#6b7f95", marginTop: 4 }}>{description}</p>
            </div>
            <button className="crm-button soft" onClick={onClose} disabled={saving}>Close</button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 14 }}>
            {(["active", "inactive", "tofu", "mofu", "bofu"] as FunnelKey[]).map((bucket) => (
              <div key={bucket} style={{ border: "1px solid #e8eef5", borderRadius: 14, padding: 14, background: "#fbfdff" }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#1f2d3d", marginBottom: 10, textTransform: "uppercase" }}>
                  {bucket === "inactive" ? "inactive" : bucket}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {stages.map((stage) => (
                    <label key={`${bucket}-${stage.id}`} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#41566d" }}>
                      <input type="checkbox" checked={draft[bucket].includes(stage.id)} onChange={() => toggle(bucket, stage.id)} />
                      <span>{stage.label}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 18, border: "1px solid #e8eef5", borderRadius: 14, padding: 14, background: "#fbfdff" }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#1f2d3d", marginBottom: 6 }}>Visible summary cards</div>
            <div style={{ fontSize: 12, color: "#6b7f95", marginBottom: 12 }}>
              Choose which sidebar summary cards stay visible for the team.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
              {SUMMARY_CARD_META.map((card) => (
                <label key={card.key} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#41566d" }}>
                  <input type="checkbox" checked={draft.visible_cards.includes(card.key)} onChange={() => toggleCard(card.key)} />
                  <span>{card.label}</span>
                </label>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 18 }}>
            <button className="crm-button soft" onClick={() => setDraft(defaultConfig)} disabled={saving}>Reset defaults</button>
            <button className="crm-button primary" onClick={() => onSave(draft)} disabled={saving}>{saving ? "Saving..." : "Save settings"}</button>
          </div>
        </div>
      </div>
    </>
  );
}

function CreateDealModal({ defaultStage, companies, users, stages, onClose, onCreated }: { defaultStage: string; companies: Company[]; users: User[]; stages: StageMeta[]; onClose: () => void; onCreated: (deal: Deal) => void }) {
  const [form, setForm] = useState({ name: "", company_id: "", value: "", stage: defaultStage, close_date_est: "", priority: "normal", assigned_to_id: "", geography: "", tags: "", source: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [validationErrors, setValidationErrors] = useState<{ name: boolean; source: boolean }>({ name: false, source: false });
  const [companySearch, setCompanySearch] = useState("");
  const [companyDropdownOpen, setCompanyDropdownOpen] = useState(false);
  const companyDropdownRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (!companyDropdownRef.current?.contains(event.target as Node)) {
        setCompanyDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filteredCompanies = companySearch
    ? companies.filter((c) => c.name.toLowerCase().includes(companySearch.toLowerCase()))
    : companies;

  const selectedCompanyName = companies.find((c) => c.id === form.company_id)?.name ?? "";

  const handleCreate = async () => {
    const nextValidationErrors = {
      name: !form.name.trim(),
      source: !form.source,
    };
    setValidationErrors(nextValidationErrors);
    const missingFields = [
      nextValidationErrors.name ? "Deal name" : null,
      nextValidationErrors.source ? "Deal source" : null,
    ].filter(Boolean);
    if (missingFields.length) {
      setError(`Complete required fields: ${missingFields.join(", ")}.`);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const deal = await dealsApi.create({
        name: form.name.trim(),
        pipeline_type: "deal",
        stage: form.stage,
        company_id: form.company_id || undefined,
        value: form.value ? Number(form.value) : undefined,
        close_date_est: form.close_date_est || undefined,
        priority: form.priority,
        assigned_to_id: form.assigned_to_id || undefined,
        geography: form.geography || undefined,
        source: form.source || undefined,
        tags: form.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      } as Partial<Deal>);
      onCreated(deal);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create deal");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.25)", zIndex: 40 }} onClick={onClose} />
      <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "grid", placeItems: "center", padding: 16 }}>
        <div style={{ width: "100%", maxWidth: 520, borderRadius: 20, background: "#fff", boxShadow: "0 20px 60px rgba(0,0,0,0.15)", padding: 28 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <h3 style={{ fontSize: 18, fontWeight: 700, color: "#1f2d3d" }}>New Deal</h3>
            <button onClick={onClose} style={{ color: "#7a8ea4", cursor: "pointer", background: "none", border: "none" }}>Close</button>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <input
              style={{
                ...modalInputStyle,
                border: validationErrors.name ? "1.5px solid #f59e0b" : modalInputStyle.border,
                background: validationErrors.name ? "#fffbf5" : "#fff",
              }}
              placeholder="Deal name *"
              value={form.name}
              onChange={(event) => {
                const nextName = event.target.value;
                setForm((current) => ({ ...current, name: nextName }));
                if (error) setError("");
                if (validationErrors.name && nextName.trim()) {
                  setValidationErrors((current) => ({ ...current, name: false }));
                }
              }}
            />
            {/* Searchable company combobox */}
            <div ref={companyDropdownRef} style={{ position: "relative" }}>
              <div
                onClick={() => setCompanyDropdownOpen((o) => !o)}
                style={{ ...modalInputStyle, background: "#fff", display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", userSelect: "none" }}
              >
                <span style={{ color: form.company_id ? "#1f2d3d" : "#94a3b8", fontSize: 14 }}>
                  {form.company_id ? selectedCompanyName : "Select company"}
                </span>
                <ChevronDown size={14} style={{ color: "#94a3b8", flexShrink: 0 }} />
              </div>
              {companyDropdownOpen && (
                <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 60, borderRadius: 12, border: "1px solid #dbe6f2", background: "#fff", boxShadow: "0 12px 32px rgba(15,23,42,0.14)", display: "flex", flexDirection: "column" }}>
                  <div style={{ padding: "8px 8px 4px", flexShrink: 0, position: "relative" }}>
                    <Search size={13} style={{ position: "absolute", left: 18, top: "50%", transform: "translateY(-50%)", color: "#94a3b8", pointerEvents: "none", marginTop: 2 }} />
                    <input
                      autoFocus
                      type="text"
                      placeholder="Search companies…"
                      value={companySearch}
                      onChange={(e) => setCompanySearch(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ width: "100%", height: 34, borderRadius: 8, border: "1px solid #e2eaf2", background: "#f8fafc", paddingLeft: 32, paddingRight: 10, fontSize: 13, outline: "none", boxSizing: "border-box" }}
                    />
                  </div>
                  <div style={{ overflowY: "auto", maxHeight: 220 }}>
                    <button
                      type="button"
                      onClick={() => { setForm((c) => ({ ...c, company_id: "" })); setCompanySearch(""); setCompanyDropdownOpen(false); }}
                      style={{ display: "block", width: "100%", textAlign: "left", padding: "9px 14px", fontSize: 13, border: "none", background: !form.company_id ? "#f0f6ff" : "transparent", color: !form.company_id ? "#175089" : "#4d6178", cursor: "pointer", fontWeight: 500 }}
                    >
                      No company
                    </button>
                    {filteredCompanies.length === 0 && (
                      <div style={{ padding: "10px 14px", fontSize: 12, color: "#94a3b8" }}>No matches</div>
                    )}
                    {filteredCompanies.map((company) => (
                      <button
                        key={company.id}
                        type="button"
                        onClick={() => { setForm((c) => ({ ...c, company_id: company.id })); setCompanySearch(""); setCompanyDropdownOpen(false); }}
                        style={{ display: "block", width: "100%", textAlign: "left", padding: "9px 14px", fontSize: 13, border: "none", background: form.company_id === company.id ? "#f0f6ff" : "transparent", color: form.company_id === company.id ? "#175089" : "#1f2d3d", cursor: "pointer", fontWeight: form.company_id === company.id ? 600 : 400 }}
                      >
                        {company.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <select style={{ ...modalInputStyle, background: "#fff" }} value={form.stage} onChange={(event) => setForm((current) => ({ ...current, stage: event.target.value }))}>
                {stages.map((stage) => <option key={stage.id} value={stage.id}>{stage.label}</option>)}
              </select>
              <select style={{ ...modalInputStyle, background: "#fff" }} value={form.priority} onChange={(event) => setForm((current) => ({ ...current, priority: event.target.value }))}>
                <option value="urgent">Urgent</option>
                <option value="high">High</option>
                <option value="normal">Normal</option>
                <option value="low">Low</option>
              </select>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <select style={{ ...modalInputStyle, background: "#fff" }} value={form.assigned_to_id} onChange={(event) => setForm((current) => ({ ...current, assigned_to_id: event.target.value }))}>
                <option value="">Unassigned</option>
                {users.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}
              </select>
              <select style={{ ...modalInputStyle, background: "#fff" }} value={form.geography} onChange={(event) => setForm((current) => ({ ...current, geography: event.target.value }))}>
                <option value="">Unassigned</option>
                {GEO_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <input type="number" style={modalInputStyle} placeholder="Value" value={form.value} onChange={(event) => setForm((current) => ({ ...current, value: event.target.value }))} />
              <input type="date" style={modalInputStyle} value={form.close_date_est} onChange={(event) => setForm((current) => ({ ...current, close_date_est: event.target.value }))} />
            </div>
            <input style={modalInputStyle} placeholder="Tags, comma separated" value={form.tags} onChange={(event) => setForm((current) => ({ ...current, tags: event.target.value }))} />
            <div>
              <label style={{ fontSize: 11, fontWeight: 700, color: "#5e738b", marginBottom: 5, display: "flex", alignItems: "center", gap: 4 }}>
                Deal Source <span style={{ color: "#dc2626" }}>*</span>
              </label>
              <select
                style={{ ...modalInputStyle, background: form.source ? "#fff" : "#fffbf5", border: form.source ? "1px solid #dbe6f2" : "1.5px solid #fbbf24", color: form.source ? "#1f2d3d" : "#92400e" }}
                value={form.source}
                onChange={(event) => {
                  const nextSource = event.target.value;
                  setForm((current) => ({ ...current, source: nextSource }));
                  if (error) setError("");
                  if (validationErrors.source && nextSource) {
                    setValidationErrors((current) => ({ ...current, source: false }));
                  }
                }}
              >
                <option value="">Select source (required)</option>
                <option value="inbound">Inbound</option>
                <option value="outbound">Outbound</option>
                <option value="referral">Referral</option>
                <option value="partner">Partner</option>
                <option value="event">Event</option>
                <option value="cold_call">Cold Call</option>
                <option value="linkedin">LinkedIn</option>
              </select>
            </div>
          </div>

          {error && <p style={{ fontSize: 12, color: "#b94a24", fontWeight: 600, marginTop: 12 }}>{error}</p>}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
            <button className="crm-button soft" onClick={onClose}>Cancel</button>
            <button className="crm-button primary" onClick={handleCreate} disabled={saving}>{saving ? "Creating..." : "Create deal"}</button>
          </div>
        </div>
      </div>
    </>
  );
}

function CrmImportModal({
  importing,
  result,
  error,
  onClose,
  onImport,
}: {
  importing: boolean;
  result: CrmImportResponse | null;
  error: string;
  onClose: () => void;
  onImport: () => Promise<void>;
}) {
  const statStyle = { fontSize: 12, color: "#5e738b" } as const;
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (!importing) {
      setElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [importing]);

  return (
    <>
      <div style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.25)", zIndex: 40 }} onClick={importing ? undefined : onClose} />
      <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "grid", placeItems: "center", padding: 16 }}>
        <div style={{ width: "100%", maxWidth: 560, borderRadius: 20, background: "#fff", boxShadow: "0 20px 60px rgba(0,0,0,0.15)", padding: 28 }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
            <div>
              <h3 style={{ fontSize: 18, fontWeight: 700, color: "#1f2d3d" }}>Import from CRM</h3>
              <p style={{ fontSize: 13, color: "#5e738b", marginTop: 6, lineHeight: 1.6 }}>
                This replaces the current deal pipeline data with the latest records from the ClickUp <strong>Sales CRM</strong> board.
                Users, settings, prospects, and integrations stay untouched.
              </p>
            </div>
            <button onClick={onClose} disabled={importing} style={{ color: "#7a8ea4", cursor: importing ? "not-allowed" : "pointer", background: "none", border: "none" }}>Close</button>
          </div>

          <div style={{ borderRadius: 14, border: "1px solid #dbe6f2", background: "#f8fbff", padding: 14, display: "grid", gap: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#175089" }}>What happens</div>
            <div style={statStyle}>1. Beacon clears the current deal-pipeline records.</div>
            <div style={statStyle}>2. Beacon imports every board item from ClickUp Sales CRM as deals and companies.</div>
            <div style={statStyle}>3. Reruns stay idempotent using ClickUp task ids, so the same source data is not duplicated.</div>
            <div style={{ ...statStyle, color: "#35506b" }}>
              Typical runtime is around 1-3 minutes for this CRM size. If ClickUp comments and subtasks are heavy, it can take longer.
            </div>
            {importing && (
              <div style={{ marginTop: 2, fontSize: 12, fontWeight: 700, color: "#175089" }}>
                Import running for {elapsedSeconds}s. Keep this window open while Beacon finishes the sync.
              </div>
            )}
          </div>

          {error && <div style={{ marginTop: 14, fontSize: 12, color: "#b94a24", fontWeight: 600 }}>{error}</div>}

          {result && (
            <div style={{ marginTop: 16, display: "grid", gap: 12 }}>
              <div style={{ borderRadius: 14, border: "1px solid #dbe6f2", background: "#fbfdff", padding: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#1f2d3d", marginBottom: 8 }}>Cleared</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <div style={statStyle}>Deals removed: {result.replace.deals_deleted}</div>
                  <div style={statStyle}>Activities removed: {result.replace.activities_deleted}</div>
                  <div style={statStyle}>Deal tasks removed: {result.replace.deal_tasks_deleted}</div>
                  <div style={statStyle}>Companies removed: {result.replace.companies_deleted}</div>
                </div>
              </div>
              <div style={{ borderRadius: 14, border: "1px solid #dbe6f2", background: "#fbfdff", padding: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#1f2d3d", marginBottom: 8 }}>Imported</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <div style={statStyle}>Deals seen: {result.import.top_level_tasks_seen}</div>
                  <div style={statStyle}>Subtasks seen: {result.import.subtasks_seen}</div>
                  <div style={statStyle}>Deals created: {result.import.deals_created}</div>
                  <div style={statStyle}>Deals updated: {result.import.deals_updated}</div>
                  <div style={statStyle}>Companies created: {result.import.companies_created}</div>
                  <div style={statStyle}>Companies reused: {result.import.companies_reused}</div>
                  <div style={statStyle}>Activities created: {result.import.activities_created}</div>
                  <div style={statStyle}>Tasks created: {result.import.tasks_created}</div>
                </div>
                {result.import.unmatched_assignees.length > 0 && (
                  <div style={{ marginTop: 10, fontSize: 12, color: "#7a8ea4", lineHeight: 1.5 }}>
                    Unmatched assignees: {result.import.unmatched_assignees.join(", ")}
                  </div>
                )}
              </div>
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
            <button className="crm-button soft" onClick={onClose} disabled={importing}>Cancel</button>
            <button className="crm-button primary" onClick={onImport} disabled={importing}>
              {importing ? "Importing..." : "Import from CRM"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}


function DealCard({ deal, onClick, onDragStart, onDragEnd, accountPriorityTag, selected, onToggleSelect }: { deal: Deal; onClick: () => void; onDragStart: () => void; onDragEnd: () => void; accountPriorityTag?: "P0" | "P1" | "P2" | null; selected?: boolean; onToggleSelect?: () => void }) {
  const isOverdue = deal.close_date_est && new Date(deal.close_date_est) < new Date();

  return (
    <div className="crm-hover-lift" draggable onDragStart={onDragStart} onDragEnd={onDragEnd} style={{ width: "100%", borderRadius: 14, border: selected ? "1.5px solid #9ace3d" : "1px solid #e8eef5", background: selected ? "#f7fce9" : "#fff", boxShadow: selected ? "0 0 0 3px rgba(154,206,61,0.16)" : "0 1px 4px rgba(17,34,68,0.04)", padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      {/* Header row: select + grip + name + close date */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        {onToggleSelect && (
          <input
            type="checkbox"
            checked={Boolean(selected)}
            onChange={(e) => { e.stopPropagation(); onToggleSelect(); }}
            onClick={(e) => e.stopPropagation()}
            title="Select for bulk action"
            style={{ marginTop: 2, width: 14, height: 14, accentColor: "#6fae27", cursor: "pointer", flexShrink: 0 }}
          />
        )}
        <GripVertical size={12} style={{ color: "#94a3b8", marginTop: 3, flexShrink: 0, cursor: "grab" }} />
        <button type="button" onClick={onClick} style={{ flex: 1, textAlign: "left", background: "none", border: "none", padding: 0, cursor: "pointer" }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "#1f2d3d", lineHeight: 1.35 }}>{deal.name}</span>
        </button>
        {deal.close_date_est && (
          <span style={{ fontSize: 9, fontWeight: 600, color: isOverdue ? "#dc2626" : "#7a8ca1", whiteSpace: "nowrap", flexShrink: 0, marginTop: 2 }}>
            {formatDate(deal.close_date_est)}
          </span>
        )}
      </div>

      <button type="button" onClick={onClick} style={{ background: "none", border: "none", padding: 0, cursor: "pointer", display: "flex", flexDirection: "column", gap: 8, textAlign: "left" }}>
        {deal.company_name && (
          <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: "#5e738b" }}>
            <Building2 size={11} />
            <span>{deal.company_name}</span>
          </div>
        )}
        <div style={{ fontSize: 15, fontWeight: 700, color: deal.value ? "#1f2a37" : "#b4c3d4" }}>{formatCurrency(deal.value)}</div>
        {deal.next_step && (
          <div style={{ fontSize: 11, color: "#2563eb", fontWeight: 500, lineHeight: 1.3 }}>
            {deal.next_step}
            {deal.next_step_updated_at && (
              <span style={{ color: "#94a3b8", fontWeight: 400, marginLeft: 6 }}>· {formatDate(deal.next_step_updated_at)}</span>
            )}
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
          <EngagementBadge side="rep" timestamp={deal.seller_engagement_at} signal={deal.seller_engagement_signal} reason={deal.seller_engagement_reason} />
          <EngagementBadge side="client" timestamp={deal.client_engagement_at} signal={deal.client_engagement_signal} reason={deal.client_engagement_reason} />
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {(deal.tags ?? []).slice(0, 2).map((tag) => <span key={tag} style={chip("#f8f0ff", "#6b46a0", "#e8d8f8")}>{tag}</span>)}
        </div>
      </button>

      {/* Footer: avatar + days + contacts + P-tag */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingTop: 6, borderTop: "1px solid #f0f4f8", marginTop: 2 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {deal.assigned_rep_name ? <div className={`flex items-center justify-center rounded-full text-[9px] font-bold ${avatarColor(deal.assigned_rep_name)}`} style={{ width: 20, height: 20 }}>{getInitials(deal.assigned_rep_name)}</div> : <div style={{ width: 20, height: 20, borderRadius: "50%", background: "#e8eef5" }} />}
          <div style={{ display: "flex", alignItems: "center", gap: 3, fontSize: 10, color: "#7a8ca1" }}><Clock3 size={10} /><span>{deal.days_in_stage ?? 0}d</span></div>
          {(deal.contact_count ?? 0) > 0 && <span style={{ fontSize: 10, color: "#5e738b", display: "flex", alignItems: "center", gap: 2 }}><UserCircle2 size={10} />{deal.contact_count}</span>}
        </div>
        {accountPriorityTag && (
          <span style={{
            fontSize: 9, fontWeight: 800, padding: "2px 7px", borderRadius: 5, flexShrink: 0,
            background: accountPriorityTag === "P0" ? "#fff1f2" : accountPriorityTag === "P1" ? "#fff7ed" : "#f0fdf4",
            color: accountPriorityTag === "P0" ? "#be123c" : accountPriorityTag === "P1" ? "#c2410c" : "#15803d",
            border: `1px solid ${accountPriorityTag === "P0" ? "#fecdd3" : accountPriorityTag === "P1" ? "#fed7aa" : "#bbf7d0"}`,
          }}>
            {accountPriorityTag}
          </span>
        )}
      </div>
    </div>
  );
}

function LoadingCard({ kind }: { kind: "deal" | "prospect" }) {
  return (
    <div
      aria-hidden="true"
      style={{
        width: "100%",
        borderRadius: 14,
        border: "1px solid #e8eef5",
        background: "#fff",
        boxShadow: "0 1px 4px rgba(17,34,68,0.04)",
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 9,
      }}
    >
      <div style={{ height: 12, width: kind === "deal" ? "72%" : "62%", borderRadius: 999, background: "#edf2f7" }} />
      <div style={{ height: 10, width: "44%", borderRadius: 999, background: "#f3f6fa" }} />
      <div style={{ height: 16, width: kind === "deal" ? "30%" : "52%", borderRadius: 999, background: "#edf2f7" }} />
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <div style={{ height: 18, width: 66, borderRadius: 999, background: "#f3f6fa" }} />
        <div style={{ height: 18, width: 50, borderRadius: 999, background: "#f7f9fc" }} />
      </div>
      <div style={{ height: 1, background: "#f0f4f8", marginTop: 4 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div style={{ width: 20, height: 20, borderRadius: "50%", background: "#edf2f7" }} />
          <div style={{ height: 10, width: 28, borderRadius: 999, background: "#f3f6fa" }} />
        </div>
        <div style={{ height: 10, width: 64, borderRadius: 999, background: "#f3f6fa" }} />
      </div>
    </div>
  );
}

function ProspectCard({ contact, company, onOpen, onDragStart, onDragEnd, onDelete }: { contact: Contact; company?: Company; onOpen: () => void; onDragStart: () => void; onDragEnd: () => void; onDelete?: () => void }) {
  return (
    <button type="button" className="crm-hover-lift" draggable onDragStart={onDragStart} onDragEnd={onDragEnd} onClick={onOpen} style={{ width: "100%", textAlign: "left", cursor: "pointer", borderRadius: 14, border: "1px solid #e8eef5", background: "#fff", boxShadow: "0 1px 4px rgba(17,34,68,0.04)", padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <GripVertical size={12} style={{ color: "#94a3b8", marginTop: 3, flexShrink: 0 }} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#1f2d3d", lineHeight: 1.3 }}>{contactName(contact)}</div>
          <div style={{ fontSize: 11, color: "#5e738b", marginTop: 2 }}>{contact.title || contact.persona || "Prospect"}</div>
        </div>
        {onDelete && (
          <span role="button" tabIndex={0} onClick={(e) => { e.stopPropagation(); onDelete(); }} onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); onDelete(); } }} style={{ width: 18, height: 18, borderRadius: 5, display: "grid", placeItems: "center", color: "#94a3b8", cursor: "pointer", flexShrink: 0 }} title="Delete prospect">
            <Trash2 size={11} />
          </span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: "#5e738b" }}><Building2 size={11} /><span>{contact.company_name || company?.name || "Unknown company"}</span></div>
      {contact.tracking_summary && <div style={{ fontSize: 11, color: "#2563eb", fontWeight: 500, lineHeight: 1.35 }}>{contact.tracking_summary}</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {contact.persona && <span style={chip("#eef4ff", "#24567e", "#d7e6ff")}>{contact.persona}</span>}
        {contact.tracking_label && <span style={chip("#effcf6", "#047857", "#bbf7d0")}>{contact.tracking_label}</span>}
        {typeof contact.tracking_score === "number" && <span style={chip("#fff7ed", "#c2410c", "#fed7aa")}>{contact.tracking_score}/100</span>}
      </div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingTop: 6, borderTop: "1px solid #f0f4f8", marginTop: 2 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {(contact.assigned_to_name || contact.sdr_name) ? <div className={`flex items-center justify-center rounded-full text-[9px] font-bold ${avatarColor(contact.assigned_to_name || contact.sdr_name || "")}`} style={{ width: 20, height: 20 }}>{getInitials(contact.assigned_to_name || contact.sdr_name || "RP")}</div> : <div style={{ width: 20, height: 20, borderRadius: "50%", background: "#e8eef5" }} />}
          <div style={{ fontSize: 10, color: "#7a8ca1" }}>{contact.sequence_status || contact.instantly_status || "ready"}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {contact.email
            ? <Mail size={11} color="#7a8ca1" />
            : <span onClick={(e) => { e.stopPropagation(); onOpen(); }} style={{ fontSize: 9, fontWeight: 700, color: "#4d7c0f", background: "#f3fbe3", border: "1px solid #cfe89a", borderRadius: 5, padding: "1px 5px", cursor: "pointer", lineHeight: 1.5 }}>+Email</span>
          }
          {contact.phone
            ? <Phone size={11} color="#7a8ca1" />
            : <span onClick={(e) => { e.stopPropagation(); onOpen(); }} style={{ fontSize: 9, fontWeight: 700, color: "#4d7c0f", background: "#f3fbe3", border: "1px solid #cfe89a", borderRadius: 5, padding: "1px 5px", cursor: "pointer", lineHeight: 1.5 }}>+Phone</span>
          }
          {contact.linkedin_url
            ? <Globe size={11} color="#7a8ca1" />
            : <span onClick={(e) => { e.stopPropagation(); onOpen(); }} style={{ fontSize: 9, fontWeight: 700, color: "#4d7c0f", background: "#f3fbe3", border: "1px solid #cfe89a", borderRadius: 5, padding: "1px 5px", cursor: "pointer", lineHeight: 1.5 }}>+LinkedIn</span>
          }
        </div>
      </div>
    </button>
  );
}

function BoardColumn({ stage, count, totalValue, dropActive, onAdd, onExport, onDrop, children }: { stage: StageMeta; count: number; totalValue?: number; dropActive: boolean; onAdd?: () => void; onExport?: () => void; onDrop: () => void; children: ReactNode }) {
  return (
    <div style={{ width: 286, flexShrink: 0, display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 10, padding: "0 4px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, flex: 1 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: stage.color || STAGE_COLOR[stage.id] || "#94a3b8", flexShrink: 0 }} />
            <span style={{ fontSize: 12, fontWeight: 700, color: stage.group === "closed" ? "#7a8ca1" : "#2d4258", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{stage.label}</span>
            <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 999, background: "#ecf1f7", color: "#48607b", flexShrink: 0 }}>{count}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
            {onExport && (
              <button
                type="button"
                onClick={onExport}
                disabled={count === 0}
                style={{ width: 22, height: 22, borderRadius: 7, border: "1px solid #dbe6f2", background: count === 0 ? "#f8fafc" : "#fff", cursor: count === 0 ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", color: count === 0 ? "#cbd5e1" : "#5e738b" }}
                title={`Export ${stage.label}`}
              >
                <Download size={12} />
              </button>
            )}
            {onAdd && <button onClick={onAdd} style={{ width: 22, height: 22, borderRadius: 7, border: "1px solid #dbe6f2", background: "#fff", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", color: "#7a96b0" }}><Plus size={12} /></button>}
          </div>
        </div>
        {typeof totalValue === "number" && Number.isFinite(totalValue) && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, paddingLeft: 14 }}>
            <span style={{ fontSize: 10, fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.05em" }}>Total</span>
            <span style={{ fontSize: 13, fontWeight: 800, color: totalValue > 0 ? "#4d7c0f" : "#94a3b8", letterSpacing: "0.01em" }}>
              {formatCurrency(totalValue)}
            </span>
          </div>
        )}
      </div>
      <div onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); onDrop(); }} style={{ flex: 1, minHeight: 0, maxHeight: "100%", borderRadius: 14, padding: 8, display: "flex", flexDirection: "column", gap: 8, background: dropActive ? "#f3fbe3" : stage.group === "closed" ? "#f4f6f9" : "#f9fbfe", border: dropActive ? "1px solid #9ace3d" : "1px solid #e8eef5", boxShadow: dropActive ? "0 0 0 3px rgba(154, 206, 61, 0.18)" : "none", overflowY: "auto", transition: "all 0.15s ease", scrollbarGutter: "stable" }}>
        {children}
      </div>
    </div>
  );
}

function ProspectDetailDrawer({
  contact,
  company,
  companies,
  activities,
  loading,
  onConvert,
  converting,
  stages,
  onClose,
  onUpdated,
}: {
  contact: Contact;
  company?: Company;
  companies: Company[];
  activities: Activity[];
  loading: boolean;
  onConvert?: () => Promise<void>;
  converting?: boolean;
  stages: StageMeta[];
  onClose: () => void;
  onUpdated?: () => void;
}) {
  const [currentContact, setCurrentContact] = useState(contact);
  const [editingProspect, setEditingProspect] = useState(false);
  const [editForm, setEditForm] = useState({
    first_name: contact.first_name ?? "",
    last_name: contact.last_name ?? "",
    title: contact.title ?? "",
    email: contact.email ?? "",
    phone: contact.phone ?? "",
    linkedin_url: contact.linkedin_url ?? "",
    company_id: contact.company_id ?? "",
  });
  const fullName = contactName(currentContact);
  const stage = prospectStage(currentContact);
  const stageLabel = stages.find((item) => item.id === stage)?.label ?? stage;
  const [savingContact, setSavingContact] = useState(false);
  const canConvert = stage === "meeting_booked";
  const currentCompany = currentContact.company_id
    ? companies.find((item) => item.id === currentContact.company_id) ?? company
    : undefined;
  const positiveSignals = activities.filter((item) => {
    const text = `${item.ai_summary ?? ""} ${item.content ?? ""} ${item.call_outcome ?? ""}`.toLowerCase();
    return text.includes("interested") || text.includes("positive") || text.includes("meeting booked") || text.includes("answered");
  }).length;
  const hasProspectChanges =
    editForm.first_name !== (currentContact.first_name ?? "") ||
    editForm.last_name !== (currentContact.last_name ?? "") ||
    editForm.title !== (currentContact.title ?? "") ||
    editForm.email !== (currentContact.email ?? "") ||
    editForm.phone !== (currentContact.phone ?? "") ||
    editForm.linkedin_url !== (currentContact.linkedin_url ?? "") ||
    editForm.company_id !== (currentContact.company_id ?? "");

  useEffect(() => {
    setCurrentContact(contact);
    setEditingProspect(false);
    setEditForm({
      first_name: contact.first_name ?? "",
      last_name: contact.last_name ?? "",
      title: contact.title ?? "",
      email: contact.email ?? "",
      phone: contact.phone ?? "",
      linkedin_url: contact.linkedin_url ?? "",
      company_id: contact.company_id ?? "",
    });
  }, [contact]);

  return (
    <>
      <div style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.22)", backdropFilter: "blur(3px)", zIndex: 50 }} onClick={onClose} />
      <div style={{ position: "fixed", top: 12, right: 12, bottom: 12, width: "min(760px, calc(100vw - 24px))", zIndex: 51, background: "#fff", border: "1px solid #dfe8f2", borderRadius: 22, boxShadow: "-18px 0 60px rgba(15, 23, 42, 0.16)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "22px 28px 18px", borderBottom: "1px solid #e8eef5", display: "flex", flexDirection: "column", gap: 12, background: "linear-gradient(180deg, #ffffff 0%, #fbfdff 100%)" }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 24, fontWeight: 800, color: "#1f2d3d" }}>{fullName}</div>
              <div style={{ marginTop: 6, color: "#5e738b", fontSize: 14 }}>{currentContact.title || "Prospect"} {currentCompany ? `at ${currentCompany.name}` : ""}</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {!editingProspect && (
                <button
                  type="button"
                  onClick={() => setEditingProspect(true)}
                  style={{ borderRadius: 10, border: "1px solid #c8daf0", background: "#eef5ff", color: "#175089", padding: "8px 12px", fontSize: 12, fontWeight: 800, cursor: "pointer" }}
                >
                  Edit prospect
                </button>
              )}
              <button onClick={onClose} style={{ color: "#7a96b0", cursor: "pointer", background: "none", border: "none" }}>Close</button>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <span style={{ fontSize: 12, fontWeight: 700, padding: "4px 10px", borderRadius: 999, background: "#eef4ff", color: "#175089", border: "1px solid #c8daf0" }}>{stageLabel}</span>
            {contact.tracking_label && <span style={{ fontSize: 12, fontWeight: 700, padding: "4px 10px", borderRadius: 999, background: "#effcf6", color: "#047857", border: "1px solid #bbf7d0" }}>{contact.tracking_label}</span>}
            {typeof contact.tracking_score === "number" && <span style={{ fontSize: 12, fontWeight: 700, padding: "4px 10px", borderRadius: 999, background: "#fff7ed", color: "#c2410c", border: "1px solid #fed7aa" }}>{contact.tracking_score}/100</span>}
            {canConvert && <span style={{ fontSize: 12, fontWeight: 700, padding: "4px 10px", borderRadius: 999, background: "#ecfdf5", color: "#15803d", border: "1px solid #bbf7d0" }}>Ready to convert to deal</span>}
            {canConvert && onConvert && (
              <button
                type="button"
                disabled={converting}
                onClick={onConvert}
                style={{ borderRadius: 10, border: "1px solid #6fae27", background: "#6fae27", color: "#fff", padding: "8px 12px", fontSize: 12, fontWeight: 800, cursor: converting ? "wait" : "pointer" }}
              >
                {converting ? "Converting..." : "Convert to Deal"}
              </button>
            )}
          </div>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px 28px", display: "grid", gap: 18, background: "#fcfdff" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
            <SummaryCard label="Status" value={stageLabel} tone="accent" />
            <SummaryCard label="Positive signals" value={positiveSignals} tone="success" />
            <SummaryCard label="Last signal" value={activities[0] ? formatDate(activities[0].created_at) : "No activity"} />
          </div>

          <div style={{ border: "1px solid #e8eef5", borderRadius: 16, background: "#fff", padding: 18 }}>
            <div style={{ fontSize: 15, fontWeight: 800, color: "#1f2d3d", marginBottom: 12 }}>Contact Info</div>
            {editingProspect ? (
              <div style={{ display: "grid", gap: 12 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div>
                    <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>First Name</label>
                    <input value={editForm.first_name} onChange={(e) => setEditForm((current) => ({ ...current, first_name: e.target.value }))} style={{ width: "100%", height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", fontSize: 13, outline: "none" }} />
                  </div>
                  <div>
                    <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Last Name</label>
                    <input value={editForm.last_name} onChange={(e) => setEditForm((current) => ({ ...current, last_name: e.target.value }))} style={{ width: "100%", height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", fontSize: 13, outline: "none" }} />
                  </div>
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Job Title</label>
                  <input value={editForm.title} onChange={(e) => setEditForm((current) => ({ ...current, title: e.target.value }))} style={{ width: "100%", height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", fontSize: 13, outline: "none" }} />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Company</label>
                  <SearchableCompanySelect
                    value={editForm.company_id}
                    companies={companies}
                    onChange={(companyId) => setEditForm((current) => ({ ...current, company_id: companyId ?? "" }))}
                    placeholder="Search company..."
                  />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Email</label>
                  <input value={editForm.email} onChange={(e) => setEditForm((current) => ({ ...current, email: e.target.value }))} style={{ width: "100%", height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", fontSize: 13, outline: "none" }} type="email" />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Phone</label>
                  <input value={editForm.phone} onChange={(e) => setEditForm((current) => ({ ...current, phone: e.target.value }))} style={{ width: "100%", height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", fontSize: 13, outline: "none" }} />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>LinkedIn URL</label>
                  <input value={editForm.linkedin_url} onChange={(e) => setEditForm((current) => ({ ...current, linkedin_url: e.target.value }))} style={{ width: "100%", height: 38, borderRadius: 10, border: "1px solid #dbe6f2", padding: "0 10px", fontSize: 13, outline: "none" }} />
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                  <button
                    type="button"
                    disabled={savingContact}
                    onClick={() => {
                      setEditingProspect(false);
                      setEditForm({
                        first_name: currentContact.first_name ?? "",
                        last_name: currentContact.last_name ?? "",
                        title: currentContact.title ?? "",
                        email: currentContact.email ?? "",
                        phone: currentContact.phone ?? "",
                        linkedin_url: currentContact.linkedin_url ?? "",
                        company_id: currentContact.company_id ?? "",
                      });
                    }}
                    style={{ borderRadius: 10, border: "1px solid #dbe6f2", background: "#fff", color: "#60758b", padding: "8px 14px", fontSize: 12, fontWeight: 700, cursor: savingContact ? "default" : "pointer" }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={savingContact || !hasProspectChanges}
                    onClick={async () => {
                      setSavingContact(true);
                      try {
                        const updated = await contactsApi.update(currentContact.id, {
                          first_name: editForm.first_name.trim() || undefined,
                          last_name: editForm.last_name.trim() || undefined,
                          title: editForm.title.trim() || undefined,
                          email: editForm.email.trim() || undefined,
                          phone: editForm.phone.trim() || undefined,
                          linkedin_url: editForm.linkedin_url.trim() || undefined,
                          company_id: editForm.company_id || undefined,
                        });
                        setCurrentContact(updated);
                        setEditingProspect(false);
                        onUpdated?.();
                      } finally {
                        setSavingContact(false);
                      }
                    }}
                    style={{ borderRadius: 10, border: "1px solid #6fae27", background: "#6fae27", color: "#fff", padding: "8px 14px", fontSize: 12, fontWeight: 700, cursor: savingContact || !hasProspectChanges ? "default" : "pointer", opacity: savingContact || !hasProspectChanges ? 0.7 : 1 }}
                  >
                    {savingContact ? "Saving..." : "Save changes"}
                  </button>
                </div>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 10, alignItems: "center" }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0" }}>Name</span>
                  <span style={{ fontSize: 13, color: "#1f2d3d", fontWeight: 600 }}>{fullName}</span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 10, alignItems: "center" }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0" }}>Company</span>
                  <span style={{ fontSize: 13, color: currentCompany ? "#1f2d3d" : "#94a3b8", fontWeight: 600 }}>{currentCompany?.name ?? "No company linked"}</span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 10, alignItems: "center" }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0" }}>Title</span>
                  <span style={{ fontSize: 13, color: currentContact.title ? "#1f2d3d" : "#94a3b8", fontWeight: 600 }}>{currentContact.title ?? "No title yet"}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Mail size={14} color="#6b7f95" />
                  {currentContact.email ? (
                    <>
                      <span style={{ flex: 1, fontSize: 13, color: "#1f2d3d", fontWeight: 600 }}>{currentContact.email}</span>
                      <a href={`mailto:${currentContact.email}`} style={{ color: "#1f6feb", fontSize: 12, fontWeight: 700, textDecoration: "none", whiteSpace: "nowrap" }}>Send</a>
                    </>
                  ) : (
                    <>
                      <span style={{ flex: 1, fontSize: 13, color: "#94a3b8" }}>No email yet</span>
                      <button type="button" onClick={() => setEditingProspect(true)} style={{ fontSize: 11, fontWeight: 700, color: "#4d7c0f", background: "#f3fbe3", border: "1px solid #cfe89a", borderRadius: 6, padding: "2px 8px", cursor: "pointer" }}>+ Add</button>
                    </>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Phone size={14} color="#6b7f95" />
                  {currentContact.phone ? (
                    <>
                      <span style={{ flex: 1, fontSize: 13, color: "#1f2d3d", fontWeight: 600 }}>{currentContact.phone}</span>
                      <button type="button" onClick={() => window.__aircallDial?.(currentContact.phone!, fullName || undefined)} style={{ color: "#1f8f5f", fontSize: 12, fontWeight: 700, background: "none", border: "none", cursor: "pointer", whiteSpace: "nowrap" }}>Call</button>
                    </>
                  ) : (
                    <>
                      <span style={{ flex: 1, fontSize: 13, color: "#94a3b8" }}>No phone yet</span>
                      <button type="button" onClick={() => setEditingProspect(true)} style={{ fontSize: 11, fontWeight: 700, color: "#4d7c0f", background: "#f3fbe3", border: "1px solid #cfe89a", borderRadius: 6, padding: "2px 8px", cursor: "pointer" }}>+ Add</button>
                    </>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Globe size={14} color="#6b7f95" />
                  {currentContact.linkedin_url ? (
                    <a href={currentContact.linkedin_url} target="_blank" rel="noreferrer" style={{ color: "#55657a", fontSize: 13, fontWeight: 700, textDecoration: "none" }}>LinkedIn Profile</a>
                  ) : (
                    <>
                      <span style={{ flex: 1, fontSize: 13, color: "#94a3b8" }}>No LinkedIn yet</span>
                      <button type="button" onClick={() => setEditingProspect(true)} style={{ fontSize: 11, fontWeight: 700, color: "#4d7c0f", background: "#f3fbe3", border: "1px solid #cfe89a", borderRadius: 6, padding: "2px 8px", cursor: "pointer" }}>+ Add</button>
                    </>
                  )}
                </div>
                <div style={{ marginTop: 8, paddingTop: 10, borderTop: "1px solid #f0f4f8", display: "grid", gridTemplateColumns: "120px 1fr", gap: 6, alignItems: "center" }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0" }}>Added</span>
                  <span style={{ fontSize: 12, color: "#5e738b" }}>{new Date(currentContact.created_at).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                  {(currentContact.sdr_name || currentContact.assigned_to_name) && (
                    <>
                      <span style={{ fontSize: 12, fontWeight: 700, color: "#7a96b0" }}>Uploaded by</span>
                      <span style={{ fontSize: 12, color: "#5e738b", fontWeight: 600 }}>{currentContact.sdr_name || currentContact.assigned_to_name}</span>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>

          <div style={{ border: "1px solid #e8eef5", borderRadius: 16, background: "#fff", padding: 18 }}>
            <div style={{ fontSize: 15, fontWeight: 800, color: "#1f2d3d", marginBottom: 12 }}>Prospect Activity</div>
            <div style={{ color: "#6b7f95", fontSize: 13, marginBottom: 12 }}>
              Instantly drives email status and responses, Aircall drives call outcomes and insights, and Beacon keeps the timeline automatic.
            </div>
            {loading ? (
              <div style={{ color: "#7a96b0", fontSize: 13 }}>Loading activity...</div>
            ) : activities.length === 0 ? (
              <div style={{ color: "#94a3b8", fontSize: 13 }}>No synced activity yet.</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {activities.map((activity) => {
                  const source = (activity.source || "beacon").toLowerCase();
                  const positive = `${activity.ai_summary ?? ""} ${activity.content ?? ""}`.toLowerCase().includes("replied") || `${activity.ai_summary ?? ""} ${activity.content ?? ""}`.toLowerCase().includes("interested");
                  return (
                    <div key={activity.id} style={{ border: "1px solid #e8eef5", borderRadius: 14, background: positive ? "#f0fdf4" : "#fbfdff", padding: "12px 14px" }}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                          <span style={{ fontSize: 13, fontWeight: 700, color: "#1f2d3d", textTransform: "capitalize" }}>{activity.type.replace(/_/g, " ")}</span>
                          <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: source.includes("instantly") ? "#eef4ff" : source.includes("aircall") ? "#fff7ed" : "#f5f8fc", color: source.includes("instantly") ? "#175089" : source.includes("aircall") ? "#c2410c" : "#60758b" }}>
                            {source.includes("instantly") ? "Instantly" : source.includes("aircall") ? "Aircall" : activity.source || "Beacon"}
                          </span>
                          {positive && <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: "#dcfce7", color: "#166534" }}>Client response</span>}
                        </div>
                        <span style={{ fontSize: 11, color: "#94a3b8" }}>{formatDate(activity.created_at)}</span>
                      </div>
                      {activity.call_outcome && <div style={{ marginTop: 8, fontSize: 12, color: "#475569" }}>Call outcome: {activity.call_outcome}</div>}
                      {activity.email_subject && <div style={{ marginTop: 8, fontSize: 12, fontWeight: 700, color: "#1f2d3d" }}>{activity.email_subject}</div>}
                      {activity.ai_summary && <div style={{ marginTop: 8, padding: "8px 10px", borderRadius: 10, background: positive ? "#dcfce7" : "#eef4ff", color: positive ? "#166534" : "#175089", fontSize: 12, fontWeight: 600 }}>{activity.ai_summary}</div>}
                      {!activity.ai_summary && activity.content && <div style={{ marginTop: 8, fontSize: 12, color: "#475569", lineHeight: 1.55 }}>{activity.content}</div>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function AddProspectModal({ companies, onClose, onCreated }: { companies: Company[]; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ first_name: "", last_name: "", email: "", phone: "", title: "", company_id: "", linkedin_url: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleCreate = async () => {
    if (!form.first_name.trim() && !form.last_name.trim()) {
      setError("First or last name is required");
      return;
    }
    if (!form.company_id) {
      setError("Please map this prospect to a company before adding.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await contactsApi.create({
        first_name: form.first_name.trim() || undefined,
        last_name: form.last_name.trim() || undefined,
        email: form.email.trim() || undefined,
        phone: form.phone.trim() || undefined,
        title: form.title.trim() || undefined,
        company_id: form.company_id || undefined,
        linkedin_url: form.linkedin_url.trim() || undefined,
      } as Partial<Contact>);
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create prospect");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.25)", zIndex: 40 }} onClick={onClose} />
      <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "grid", placeItems: "center", padding: 16 }}>
        <div style={{ width: "100%", maxWidth: 480, borderRadius: 20, background: "#fff", boxShadow: "0 20px 60px rgba(0,0,0,0.15)", padding: 28 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <h3 style={{ fontSize: 18, fontWeight: 700, color: "#1f2d3d" }}>Add Prospect</h3>
            <button className="crm-button soft" onClick={onClose}>Close</button>
          </div>
          {error && <div style={{ color: "#dc2626", fontSize: 13, marginBottom: 12, fontWeight: 600 }}>{error}</div>}
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>First Name</label>
                <input value={form.first_name} onChange={(e) => setForm((f) => ({ ...f, first_name: e.target.value }))} style={modalInputStyle} placeholder="Jane" />
              </div>
              <div>
                <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Last Name</label>
                <input value={form.last_name} onChange={(e) => setForm((f) => ({ ...f, last_name: e.target.value }))} style={modalInputStyle} placeholder="Smith" />
              </div>
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Email</label>
              <input value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} style={modalInputStyle} placeholder="jane@company.com" type="email" />
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Phone</label>
              <input value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} style={modalInputStyle} placeholder="+1 555 123 4567" />
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Job Title</label>
              <input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} style={modalInputStyle} placeholder="VP Engineering" />
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>Company</label>
              <SearchableCompanySelect
                value={form.company_id}
                companies={companies}
                onChange={(companyId) => setForm((f) => ({ ...f, company_id: companyId ?? "" }))}
                placeholder="Search company..."
                allowNone={false}
              />
              <div style={{ marginTop: 6, fontSize: 12, color: "#7f8fa5" }}>
                A prospect must be mapped to an existing company.
              </div>
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 700, color: "#5e738b", marginBottom: 6, display: "block" }}>LinkedIn URL</label>
              <input value={form.linkedin_url} onChange={(e) => setForm((f) => ({ ...f, linkedin_url: e.target.value }))} style={modalInputStyle} placeholder="https://linkedin.com/in/..." />
            </div>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
            <button className="crm-button soft" onClick={onClose} disabled={saving}>Cancel</button>
            <button className="crm-button primary" onClick={handleCreate} disabled={saving || !form.company_id} style={{ opacity: saving || !form.company_id ? 0.7 : 1, cursor: saving || !form.company_id ? "default" : "pointer" }}>{saving ? "Creating..." : "Add Prospect"}</button>
          </div>
        </div>
      </div>
    </>
  );
}

export default function Pipeline() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { isAdmin, user } = useAuth();
  const [tab, setTab] = useState<PipelineTab>("deal");
  const [mobileStage, setMobileStage] = useState<string>("all");
  // Bulk selection (deal tab only) — set of selected deal ids + a pending tag input.
  const [selectedDealIds, setSelectedDealIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkTag, setBulkTag] = useState("");
  const [dealBoard, setDealBoard] = useState<Record<string, Deal[]>>({});
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loadingDeals, setLoadingDeals] = useState(true);
  const [loadingProspects, setLoadingProspects] = useState(true);
  const [loadingSummarySettings, setLoadingSummarySettings] = useState(true);
  const [busyStage, setBusyStage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stageFilters, setStageFilters] = useState<string[]>([]);
  const [assigneeFilters, setAssigneeFilters] = useState<string[]>([]);
  const [geographyFilters, setGeographyFilters] = useState<string[]>([]);
  const [tagFilters, setTagFilters] = useState<string[]>([]);
  const [priorityFilters, setPriorityFilters] = useState<string[]>([]);
  const [commitFilter, setCommitFilter] = useState<string[]>([]);
  const [stalledOnly, setStalledOnly] = useState(false);
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [missingCloseDateOnly, setMissingCloseDateOnly] = useState(false);
  // Composite AE triage filter: a deal "needs attention" when it has no next
  // step, an overdue next step, no linked contact, or no human touch in 14d+.
  const [needsAttentionOnly, setNeedsAttentionOnly] = useState(false);
  const [closeMonthFilter, setCloseMonthFilter] = useState("");
  const [healthFilters, setHealthFilters] = useState<string[]>([]);
  const [closeDateFilters, setCloseDateFilters] = useState<string[]>([]);
  const [nextStepFilters, setNextStepFilters] = useState<string[]>([]);
  const [activityFilters, setActivityFilters] = useState<string[]>([]);
  const [contactFilters, setContactFilters] = useState<string[]>([]);
  const [showForecast, setShowForecast] = useState(true);
  const [createDealStage, setCreateDealStage] = useState<string | null>(null);
  const [selectedDeal, setSelectedDeal] = useState<Deal | null>(null);
  const [selectedProspect, setSelectedProspect] = useState<Contact | null>(null);
  const [prospectActivities, setProspectActivities] = useState<Activity[]>([]);
  const [loadingProspectActivities, setLoadingProspectActivities] = useState(false);
  const [migratingProspects, setMigratingProspects] = useState(false);
  const [rolePermissions, setRolePermissions] = useState<RolePermissionsSettings | null>(null);
  const [convertingProspect, setConvertingProspect] = useState(false);
  const [pendingConvertProspect, setPendingConvertProspect] = useState<Contact | null>(null);
  const [pendingDealMove, setPendingDealMove] = useState<PendingDealMove | null>(null);
  const [dragItem, setDragItem] = useState<DragItem | null>(null);
  const [showFunnelSettings, setShowFunnelSettings] = useState(false);
  const [pipelineSummaryConfig, setPipelineSummaryConfig] = useState<PipelineSummarySettings>(() =>
    normalizePipelineSummarySettings()
  );
  const [savingFunnelSettings, setSavingFunnelSettings] = useState(false);
  const [dealStages, setDealStages] = useState<StageMeta[]>(DEFAULT_DEAL_STAGES);
  const [prospectStageMeta, setProspectStageMeta] = useState<StageMeta[]>(PROSPECT_STAGES);
  const [showCrmImport, setShowCrmImport] = useState(false);
  const [importingCrm, setImportingCrm] = useState(false);
  const [crmImportResult, setCrmImportResult] = useState<CrmImportResponse | null>(null);
  const [crmImportError, setCrmImportError] = useState("");
  const [showAddProspect, setShowAddProspect] = useState(false);
  const [showSidebarMenu, setShowSidebarMenu] = useState(false);
  const prospectImportInputRef = useRef<HTMLInputElement | null>(null);

  const companyMap = useMemo(() => new Map(companies.map((company) => [company.id, company])), [companies]);
  const allDeals = useMemo(() => Object.values(dealBoard).flat(), [dealBoard]);
  const dealTags = useMemo(() => Array.from(new Set(allDeals.flatMap((deal) => deal.tags ?? []))).sort((a, b) => a.localeCompare(b)), [allDeals]);
  const effectiveDealStages = useMemo(() => {
    const configured = dealStages.length ? dealStages : DEFAULT_DEAL_STAGES;
    const seen = new Set(configured.map((stage) => stage.id));
    const extras = Object.keys(dealBoard)
      .filter((stageId) => !seen.has(stageId))
      .map((stageId) => ({
        id: stageId,
        label: stageId.replace(/_/g, " ").toUpperCase(),
        group: ["closed_won", "closed_lost", "not_a_fit", "cold", "on_hold", "nurture", "churned", "closed"].includes(stageId) ? "closed" as const : "active" as const,
        color: STAGE_COLOR[stageId] ?? "#94a3b8",
      }));
    return [...configured, ...extras];
  }, [dealBoard, dealStages]);

  useEffect(() => {
    if (searchParams.get("new") !== "deal") return;
    setTab("deal");
    setCreateDealStage(effectiveDealStages.find((stage) => stage.group === "active")?.id ?? effectiveDealStages[0]?.id ?? "reprospect");
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("new");
      return next;
    }, { replace: true });
  }, [effectiveDealStages, searchParams, setSearchParams]);

  const dealStageLabelMap = useMemo(() => new Map(effectiveDealStages.map((stage) => [stage.id, stage.label])), [effectiveDealStages]);

  const effectiveProspectStages = useMemo(() => {
    return prospectStageMeta.length ? prospectStageMeta : PROSPECT_STAGES;
  }, [prospectStageMeta]);

  const loadDealBoard = async () => {
    setLoadingDeals(true);
    try {
      const [board, dealStageSettings] = await Promise.all([
        dealsApi.board("deal"),
        settingsApi.getDealStages().catch(() => ({ stages: DEFAULT_DEAL_STAGES as DealStageSetting[] })),
      ]);
      setDealBoard(board);
      setDealStages((dealStageSettings.stages ?? DEFAULT_DEAL_STAGES).map((stage) => ({
        id: stage.id,
        label: stage.label,
        group: stage.group,
        color: stage.color,
      })));
    } finally {
      setLoadingDeals(false);
    }
  };

  const loadProspectBoard = async () => {
    setLoadingProspects(true);
    try {
      const [contactList, prospectStageSettings] = await Promise.all([
        contactsApi.list(0, 500),
        settingsApi.getProspectStages().catch(() => ({ stages: PROSPECT_STAGES as DealStageSetting[] })),
      ]);
      setContacts(contactList);
      setProspectStageMeta((prospectStageSettings.stages ?? PROSPECT_STAGES).map((stage) => ({
        id: stage.id,
        label: stage.label,
        group: stage.group,
        color: stage.color ?? STAGE_COLOR[stage.id] ?? "#94a3b8",
      })));
    } finally {
      setLoadingProspects(false);
    }
  };

  const loadSupportingData = async () => {
    setLoadingSummarySettings(true);
    try {
      const [companyList, userList, summarySettings] = await Promise.all([
        companiesApi.list(),
        authApi.listAllUsers().catch(() => []),
        settingsApi.getPipelineSummarySettings().catch(() => normalizePipelineSummarySettings()),
      ]);
      setCompanies(companyList);
      setUsers(userList);
      setPipelineSummaryConfig(normalizePipelineSummarySettings(summarySettings));
    } catch {
      // Keep the board responsive even if secondary sidebar data lags or fails.
    } finally {
      setLoadingSummarySettings(false);
    }
  };

  const loadBoard = async () => {
    void loadSupportingData();
    await Promise.all([loadDealBoard(), loadProspectBoard()]);
  };

  useEffect(() => {
    loadBoard();
  }, []);

  useEffect(() => {
    settingsApi.getRolePermissions().then(setRolePermissions).catch(() => setRolePermissions(null));
  }, []);

  useEffect(() => {
    const requestedStages = searchParams.getAll("stage").filter(Boolean);
    const requestedAssignees = searchParams.getAll("assignee").filter(Boolean);
    const stalledParam = searchParams.get("stalled");
    const overdueParam = searchParams.get("overdue");
    const missingCloseDateParam = searchParams.get("missing_close_date");
    const closeMonthParam = searchParams.get("close_month") ?? "";
    setStageFilters(requestedStages);
    setAssigneeFilters(requestedAssignees);
    setStalledOnly(stalledParam === "1" || stalledParam === "true");
    setOverdueOnly(overdueParam === "1" || overdueParam === "true");
    setMissingCloseDateOnly(missingCloseDateParam === "1" || missingCloseDateParam === "true");
    setCloseMonthFilter(closeMonthParam);
  }, [searchParams]);

  useEffect(() => {
    if (!selectedProspect) {
      setProspectActivities([]);
      setLoadingProspectActivities(false);
      return;
    }
    setLoadingProspectActivities(true);
    activitiesApi
      .list(undefined, selectedProspect.id)
      .then(setProspectActivities)
      .catch(() => setProspectActivities([]))
      .finally(() => setLoadingProspectActivities(false));
  }, [selectedProspect]);

  const filteredDealBoard = useMemo(() => {
    const next: Record<string, Deal[]> = {};
    const today = new Date();
    const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const endOfWeek = new Date(startOfToday);
    endOfWeek.setDate(endOfWeek.getDate() + 7);
    const endOfMonth = new Date(startOfToday);
    endOfMonth.setDate(endOfMonth.getDate() + 30);
    const closedStageIds = new Set(effectiveDealStages.filter((stage) => stage.group === "closed").map((stage) => stage.id));
    for (const stage of effectiveDealStages) {
      let items = dealBoard[stage.id] ?? [];
      if (search) {
        const q = search.toLowerCase();
        items = items.filter((deal) => deal.name.toLowerCase().includes(q) || (deal.company_name ?? "").toLowerCase().includes(q));
      }
      if (stageFilters.length && !stageFilters.includes(stage.id)) items = [];
      if (assigneeFilters.length) {
        items = items.filter((deal) => {
          if (!deal.assigned_to_id) return assigneeFilters.includes("unassigned");
          return assigneeFilters.includes(deal.assigned_to_id);
        });
      }
      if (geographyFilters.length) items = items.filter((deal) => {
        const geo = normalizeGeo(deal.geography);
        if (geographyFilters.includes("unassigned") && !geo) return true;
        return geo ? geographyFilters.includes(geo) : false;
      });
      if (tagFilters.length) items = items.filter((deal) => (deal.tags ?? []).some((tag) => tagFilters.includes(tag)));
      if (priorityFilters.length) items = items.filter((deal) => {
        const tag = deal.company_id ? companyMap.get(deal.company_id)?.priority_tag : null;
        return tag ? priorityFilters.includes(tag) : false;
      });
      if (healthFilters.length) items = items.filter((deal) => healthFilters.includes((deal.health || "unknown").toLowerCase()));
      if (commitFilter.length) items = items.filter((deal) => deal.commit_to_deal === true);
      if (stalledOnly) items = items.filter((deal) => (deal.days_in_stage ?? 0) >= 30);
      if (overdueOnly) {
        items = items.filter((deal) => {
          if (closedStageIds.has(deal.stage) || !deal.close_date_est) return false;
          const closeDate = new Date(deal.close_date_est);
          if (Number.isNaN(closeDate.getTime())) return false;
          return closeDate < today;
        });
      }
      if (missingCloseDateOnly) items = items.filter((deal) => !closedStageIds.has(deal.stage) && !deal.close_date_est);
      if (closeDateFilters.length) {
        items = items.filter((deal) => {
          if (closedStageIds.has(deal.stage)) return false;
          if (closeDateFilters.includes("missing") && !deal.close_date_est) return true;
          if (!deal.close_date_est) return false;
          const closeDate = new Date(deal.close_date_est);
          if (Number.isNaN(closeDate.getTime())) return false;
          return closeDateFilters.some((filter) => {
            if (filter === "overdue") return closeDate < startOfToday;
            if (filter === "this_week") return closeDate >= startOfToday && closeDate <= endOfWeek;
            if (filter === "next_30") return closeDate >= startOfToday && closeDate <= endOfMonth;
            return false;
          });
        });
      }
      if (closeMonthFilter) items = items.filter((deal) => (deal.close_date_est ?? "").slice(0, 7) === closeMonthFilter);
      if (nextStepFilters.length) {
        items = items.filter((deal) => {
          const hasNextStep = Boolean((deal.next_step || "").trim());
          return (nextStepFilters.includes("has") && hasNextStep) || (nextStepFilters.includes("missing") && !hasNextStep);
        });
      }
      if (activityFilters.length) {
        items = items.filter((deal) => {
          const lastTouch = deal.last_activity_at || deal.seller_engagement_at || deal.client_engagement_at;
          if (activityFilters.includes("none") && !lastTouch) return true;
          if (!lastTouch) return false;
          const touchDate = new Date(lastTouch);
          if (Number.isNaN(touchDate.getTime())) return false;
          const days = (Date.now() - touchDate.getTime()) / 86_400_000;
          return activityFilters.some((filter) => {
            if (filter === "7d") return days >= 7;
            if (filter === "14d") return days >= 14;
            if (filter === "30d") return days >= 30;
            return false;
          });
        });
      }
      if (contactFilters.length) {
        items = items.filter((deal) => {
          const count = deal.contact_count ?? deal.stakeholder_count ?? 0;
          return (contactFilters.includes("has") && count > 0) || (contactFilters.includes("missing") && count === 0);
        });
      }
      if (needsAttentionOnly) {
        items = items.filter((deal) => {
          if (closedStageIds.has(deal.stage)) return false;
          if (!(deal.next_step || "").trim()) return true; // no next step
          if ((deal.contact_count ?? deal.stakeholder_count ?? 0) === 0) return true; // no contact
          if (deal.next_step_due_at) {
            const d = new Date(deal.next_step_due_at.endsWith("Z") ? deal.next_step_due_at : `${deal.next_step_due_at}Z`);
            if (!Number.isNaN(d.getTime()) && d.getTime() < Date.now()) return true; // overdue next step
          }
          const lastTouch = deal.last_activity_at || deal.seller_engagement_at || deal.client_engagement_at;
          if (!lastTouch) return true; // never touched
          const t = new Date(lastTouch.endsWith("Z") ? lastTouch : `${lastTouch}Z`);
          return !Number.isNaN(t.getTime()) && (Date.now() - t.getTime()) / 86_400_000 >= 14; // stale 14d+
        });
      }
      next[stage.id] = items;
    }
    return next;
  }, [activityFilters, assigneeFilters, closeDateFilters, closeMonthFilter, commitFilter, companyMap, contactFilters, dealBoard, effectiveDealStages, geographyFilters, healthFilters, missingCloseDateOnly, needsAttentionOnly, nextStepFilters, overdueOnly, priorityFilters, search, stageFilters, stalledOnly, tagFilters]);

  const filteredProspects = useMemo(() => {
    const next: Record<ProspectStageId, Contact[]> = { outreach: [], in_progress: [], meeting_booked: [], negative_response: [], no_response: [], not_a_fit: [] };
    contacts.forEach((contact) => {
      const stage = prospectStage(contact);
      const company = contact.company_id ? companyMap.get(contact.company_id) : undefined;
      const text = `${contactName(contact)} ${contact.email ?? ""} ${contact.title ?? ""} ${contact.company_name ?? company?.name ?? ""}`.toLowerCase();
      if (search && !text.includes(search.toLowerCase())) return;
      if (stageFilters.length && !stageFilters.includes(stage)) return;
      if (assigneeFilters.length) {
        if (assigneeFilters.includes("unassigned")) {
          if (!contact.assigned_to_id && !contact.sdr_id) {
            next[stage].push(contact);
            return;
          }
        }
        if (contact.assigned_to_id && assigneeFilters.includes(contact.assigned_to_id)) {
          next[stage].push(contact);
          return;
        }
        if (contact.sdr_id && assigneeFilters.includes(contact.sdr_id)) {
          next[stage].push(contact);
          return;
        }
        return;
      }
      if (geographyFilters.length) {
        const geo = normalizeGeo(company?.region);
        if (geographyFilters.includes("unassigned") && !geo) {
          next[stage].push(contact);
          return;
        }
        if (!geo || !geographyFilters.includes(geo)) return;
      }
      next[stage].push(contact);
    });
    return next;
  }, [assigneeFilters, companyMap, contacts, geographyFilters, search, stageFilters]);

  const FORECAST_STAGES = [
    { id: "demo_scheduled", label: "Demo Scheduled" },
    { id: "demo_done", label: "Demo Done" },
    { id: "poc_agreed", label: "POC Agreed" },
    { id: "poc_wip", label: "POC WIP" },
    { id: "poc_done", label: "POC Done" },
    { id: "commercial_negotiation", label: "Commercial" },
    { id: "msa_review", label: "MSA Review" },
    { id: "closed_won", label: "Closed Won" },
  ];

  const stageForecast = useMemo(() => {
    return FORECAST_STAGES.map((stage, index) => {
      const items = dealBoard[stage.id] ?? [];
      const count = items.length;
      const value = items.reduce((sum, d) => sum + Number(d.value ?? 0), 0);
      const prevStage = index > 0 ? FORECAST_STAGES[index - 1] : null;
      const prevCount = prevStage ? (dealBoard[prevStage.id] ?? []).length : null;
      const conversionPct = prevCount != null && prevCount > 0 ? Math.round((count / prevCount) * 100) : null;
      return { ...stage, count, value, conversionPct };
    });
  }, [dealBoard]);

  const dealSummary = useMemo(() => {
    const visible = Object.values(filteredDealBoard).flat();
    const activeStageIds = new Set(pipelineSummaryConfig.deal.active);
    const inactiveStageIds = new Set(pipelineSummaryConfig.deal.inactive);
    return {
      total: visible.length,
      active: visible.filter((deal) => activeStageIds.has(deal.stage)).length,
      inactive: visible.filter((deal) => inactiveStageIds.has(deal.stage)).length,
      tofu: visible.filter((deal) => pipelineSummaryConfig.deal.tofu.includes(deal.stage)).length,
      mofu: visible.filter((deal) => pipelineSummaryConfig.deal.mofu.includes(deal.stage)).length,
      bofu: visible.filter((deal) => pipelineSummaryConfig.deal.bofu.includes(deal.stage)).length,
    };
  }, [filteredDealBoard, pipelineSummaryConfig.deal]);

  const prospectSummary = useMemo(() => {
    const activeStageIds = new Set(pipelineSummaryConfig.prospect.active);
    const closedStageIds = new Set(pipelineSummaryConfig.prospect.inactive);
    const allProspects = Object.values(filteredProspects).flat();
    return {
      total: allProspects.length,
      active: Object.entries(filteredProspects)
        .filter(([stageId]) => activeStageIds.has(stageId))
        .reduce((sum, [, items]) => sum + items.length, 0),
      inactive: Object.entries(filteredProspects)
        .filter(([stageId]) => closedStageIds.has(stageId))
        .reduce((sum, [, items]) => sum + items.length, 0),
      tofu: Array.from(new Set(pipelineSummaryConfig.prospect.tofu)).reduce(
        (sum, stageId) => sum + (filteredProspects[stageId as ProspectStageId]?.length ?? 0),
        0,
      ),
      mofu: Array.from(new Set(pipelineSummaryConfig.prospect.mofu)).reduce(
        (sum, stageId) => sum + (filteredProspects[stageId as ProspectStageId]?.length ?? 0),
        0,
      ),
      bofu: Array.from(new Set(pipelineSummaryConfig.prospect.bofu)).reduce(
        (sum, stageId) => sum + (filteredProspects[stageId as ProspectStageId]?.length ?? 0),
        0,
      ),
    };
  }, [filteredProspects, pipelineSummaryConfig.prospect]);

  const summary = tab === "deal" ? dealSummary : prospectSummary;
  const visibleSummaryCards = tab === "deal" ? pipelineSummaryConfig.deal.visible_cards : pipelineSummaryConfig.prospect.visible_cards;
  const summaryCards = SUMMARY_CARD_META.filter((card) => visibleSummaryCards.includes(card.key)).map((card) => ({
    ...card,
    value: summary[card.key],
  }));
  const currentBoardLoading = tab === "deal" ? loadingDeals : loadingProspects;
  const summaryLoading = currentBoardLoading || loadingSummarySettings;
  const summaryCardsToRender = summaryLoading
    ? SUMMARY_CARD_META.filter((card) => ["active", "tofu", "mofu", "bofu"].includes(card.key)).map((card) => ({
        ...card,
        value: "—",
      }))
    : summaryCards;
  const canImportCrm =
    isAdmin || Boolean(user && user.role !== "admin" && rolePermissions?.[user.role]?.crm_import);
  const canMigrateProspects =
    isAdmin || Boolean(user && user.role !== "admin" && rolePermissions?.[user.role]?.prospect_migration);
  const hasFilters = Boolean(search) || stageFilters.length > 0 || assigneeFilters.length > 0 || geographyFilters.length > 0 || tagFilters.length > 0 || priorityFilters.length > 0 || commitFilter.length > 0 || healthFilters.length > 0 || closeDateFilters.length > 0 || nextStepFilters.length > 0 || activityFilters.length > 0 || contactFilters.length > 0 || stalledOnly || overdueOnly || missingCloseDateOnly || needsAttentionOnly || Boolean(closeMonthFilter);
  const stages = tab === "deal" ? effectiveDealStages : effectiveProspectStages;
  const stageOptions = (tab === "deal" ? effectiveDealStages : effectiveProspectStages).map((stage) => ({ value: stage.id, label: stage.label }));
  const assigneeOptions = [{ value: "unassigned", label: "Unassigned" }, ...users.map((user) => ({ value: user.id, label: user.name }))];
  const geographyOptions = [{ value: "unassigned", label: "Unassigned" }, ...GEO_OPTIONS.map((option) => ({ value: option, label: option }))];
  const priorityOptions = [
    { value: "P0", label: "P0" },
    { value: "P1", label: "P1" },
    { value: "P2", label: "P2" },
  ];
  const commitOptions = [{ value: "committed", label: "Committed" }];
  const healthOptions = [
    { value: "green", label: "Green" },
    { value: "yellow", label: "Yellow" },
    { value: "red", label: "Red" },
  ];
  const closeDateOptions = [
    { value: "overdue", label: "Overdue" },
    { value: "this_week", label: "This week" },
    { value: "next_30", label: "Next 30 days" },
    { value: "missing", label: "No close date" },
  ];
  const nextStepOptions = [
    { value: "has", label: "Has next step" },
    { value: "missing", label: "No next step" },
  ];
  const activityOptions = [
    { value: "7d", label: "No activity 7d+" },
    { value: "14d", label: "No activity 14d+" },
    { value: "30d", label: "No activity 30d+" },
    { value: "none", label: "No activity captured" },
  ];
  const contactOptions = [
    { value: "has", label: "Has contacts" },
    { value: "missing", label: "No contacts linked" },
  ];
  const tagOptions = dealTags.map((tag) => ({ value: tag, label: tag }));
  const accentColor = tab === "deal" ? "#175089" : "#177b75";
  const accentBg = tab === "deal" ? "#f0f6ff" : "#f0faf9";
  const accentBorder = tab === "deal" ? "#b8d0f0" : "#b2e0dc";
  const activeFunnelConfig = tab === "deal" ? pipelineSummaryConfig.deal : pipelineSummaryConfig.prospect;
  const activeFunnelDefaults = tab === "deal"
    ? normalizePipelineSummarySettings({ deal: { ...DEFAULT_FUNNEL, visible_cards: DEFAULT_VISIBLE_SUMMARY_CARDS } }).deal
    : normalizePipelineSummarySettings({ prospect: { ...DEFAULT_PROSPECT_FUNNEL, visible_cards: DEFAULT_VISIBLE_SUMMARY_CARDS } }).prospect;
  const funnelModalTitle = tab === "deal" ? "Deal summary settings" : "Prospect summary settings";
  const funnelModalDescription =
    tab === "deal"
      ? "Choose which deal stages count toward ToFU, MoFU, and BoFU in the shared summary cards."
      : "Choose which prospect lanes count toward ToFU, MoFU, and BoFU in the shared summary cards.";
  const funnelModalStages = tab === "deal" ? effectiveDealStages : effectiveProspectStages;

  const dealExportRows = (items: Deal[], stage: StageMeta): CsvRow[] => items.map((deal) => {
    const company = deal.company_id ? companyMap.get(deal.company_id) : undefined;
    const lastTouch = deal.last_activity_at || deal.seller_engagement_at || deal.client_engagement_at;
    return {
      "Deal Name": deal.name,
      "Company": deal.company_name || company?.name || "",
      "Stage": stage.label,
      "Value": deal.value ?? "",
      "Owner": deal.assigned_rep_name || "",
      "Geography": deal.geography || normalizeGeo(company?.region) || "",
      "Health": deal.health || "",
      "Health Score": deal.health_score ?? "",
      "Priority": company?.priority_tag || "",
      "Commit": deal.commit_to_deal ? "Yes" : "No",
      "Close Date": deal.close_date_est ? formatDate(deal.close_date_est) : "",
      "Days In Stage": deal.days_in_stage ?? "",
      "Last Activity": lastTouch ? formatDate(lastTouch) : "",
      "Contacts": deal.contact_count ?? deal.stakeholder_count ?? 0,
      "Tags": (deal.tags ?? []).join("; "),
      "Next Step": deal.next_step || "",
      "Source": deal.source || "",
      "Deal ID": deal.id,
      "Company ID": deal.company_id || "",
    };
  });

  const prospectExportRows = (items: Contact[], stage: StageMeta): CsvRow[] => items.map((contact) => {
    const company = contact.company_id ? companyMap.get(contact.company_id) : undefined;
    return {
      "Prospect Name": contactName(contact),
      "Company": contact.company_name || company?.name || "",
      "Stage": stage.label,
      "Title": contact.title || "",
      "Persona": contact.persona || "",
      "Email": contact.email || "",
      "Phone": contact.phone || "",
      "LinkedIn": contact.linkedin_url || "",
      "AE": contact.assigned_to_name || "",
      "SDR": contact.sdr_name || "",
      "Geography": normalizeGeo(company?.region) || "",
      "Sequence Status": contact.sequence_status || "",
      "Instantly Status": contact.instantly_status || "",
      "Tracking Label": contact.tracking_label || "",
      "Tracking Score": contact.tracking_score ?? "",
      "Tracking Summary": contact.tracking_summary || "",
      "Last Activity": contact.tracking_last_activity_at ? formatDate(contact.tracking_last_activity_at) : "",
      "Contact ID": contact.id,
      "Company ID": contact.company_id || "",
    };
  });

  const exportPipelineCsv = (scope?: { stage: StageMeta }) => {
    const date = new Date().toISOString().slice(0, 10);
    const targetStages = scope?.stage ? [scope.stage] : stages;
    const rows = targetStages.flatMap((stage) => {
      if (tab === "deal") return dealExportRows(filteredDealBoard[stage.id] ?? [], stage);
      return prospectExportRows(filteredProspects[stage.id as ProspectStageId] ?? [], stage);
    });
    if (!rows.length) return;
    const scopeName = scope?.stage ? fileSafeSegment(scope.stage.label) : "visible";
    downloadCsv(`${tab === "deal" ? "pipeline-deals" : "pipeline-prospects"}-${scopeName}-${date}.csv`, rows);
  };

  // ── Bulk selection actions (deal tab) ──────────────────────────────────────
  const clearSelection = () => setSelectedDealIds(new Set());
  const toggleDealSelect = (id: string) =>
    setSelectedDealIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const runBulk = async (payload: { stage?: string; add_tags?: string[]; reassign?: boolean; assigned_to_id?: string | null }) => {
    if (selectedDealIds.size === 0 || bulkBusy) return;
    setBulkBusy(true);
    try {
      await dealsApi.bulkUpdate({ deal_ids: [...selectedDealIds], ...payload });
      await loadBoard();
      clearSelection();
      setBulkTag("");
    } catch {
      // Board stays as-is on failure; selection is preserved so the rep can retry.
    } finally {
      setBulkBusy(false);
    }
  };

  const bulkExportSelected = () => {
    const date = new Date().toISOString().slice(0, 10);
    const rows = effectiveDealStages.flatMap((stage) =>
      dealExportRows((filteredDealBoard[stage.id] ?? []).filter((d) => selectedDealIds.has(d.id)), stage),
    );
    if (!rows.length) return;
    downloadCsv(`pipeline-deals-selected-${date}.csv`, rows);
  };

  // Selection is deal-tab only; drop it when switching tabs.
  useEffect(() => { setSelectedDealIds(new Set()); }, [tab]);


  const resetFilters = () => {
    setSearch("");
    setStageFilters([]);
    setAssigneeFilters([]);
    setGeographyFilters([]);
    setTagFilters([]);
    setPriorityFilters([]);
    setCommitFilter([]);
    setHealthFilters([]);
    setCloseDateFilters([]);
    setNextStepFilters([]);
    setActivityFilters([]);
    setContactFilters([]);
    setStalledOnly(false);
    setOverdueOnly(false);
    setMissingCloseDateOnly(false);
    setNeedsAttentionOnly(false);
    setCloseMonthFilter("");
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("stage");
      next.delete("assignee");
      next.delete("stalled");
      next.delete("overdue");
      next.delete("missing_close_date");
      next.delete("close_month");
      return next;
    }, { replace: true });
  };

  const handleStageFilterChange = (values: string[]) => {
    setStageFilters(values);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("stage");
      values.forEach((value) => next.append("stage", value));
      return next;
    }, { replace: true });
  };

  const handleStalledOnlyChange = (checked: boolean) => {
    setStalledOnly(checked);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (checked) {
        next.set("stalled", "1");
      } else {
        next.delete("stalled");
      }
      return next;
    }, { replace: true });
  };

  const handleAssigneeFilterChange = (values: string[]) => {
    setAssigneeFilters(values);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("assignee");
      values.forEach((value) => next.append("assignee", value));
      return next;
    }, { replace: true });
  };

  const handleOverdueOnlyChange = (checked: boolean) => {
    setOverdueOnly(checked);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (checked) next.set("overdue", "1");
      else next.delete("overdue");
      return next;
    }, { replace: true });
  };

  const handleMissingCloseDateOnlyChange = (checked: boolean) => {
    setMissingCloseDateOnly(checked);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (checked) next.set("missing_close_date", "1");
      else next.delete("missing_close_date");
      return next;
    }, { replace: true });
  };

  const handleDealUpdated = (updated: Deal) => {
    setDealBoard((current) => {
      const next: Record<string, Deal[]> = {};
      for (const [stageId, items] of Object.entries(current)) next[stageId] = items.filter((deal) => deal.id !== updated.id);
      next[updated.stage] = [...(next[updated.stage] ?? []), updated];
      return next;
    });
    setSelectedDeal(updated);
  };

  const handleDealDeleted = (dealId: string) => {
    setDealBoard((current) => {
      const next: Record<string, Deal[]> = {};
      for (const [stageId, items] of Object.entries(current)) next[stageId] = items.filter((deal) => deal.id !== dealId);
      return next;
    });
    setSelectedDeal(null);
    if (searchParams.get("deal") === dealId) {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.delete("deal");
        return next;
      }, { replace: true });
    }
  };

  const handleDealCreated = (deal: Deal) => setDealBoard((current) => ({ ...current, [deal.stage]: [...(current[deal.stage] ?? []), deal] }));

  const handleDeleteProspect = async (contactId: string) => {
    if (!window.confirm("Delete this prospect? This cannot be undone.")) return;
    try {
      await contactsApi.delete(contactId);
      setContacts((current) => current.filter((contact) => contact.id !== contactId));
      if (selectedProspect?.id === contactId) setSelectedProspect(null);
    } catch { /* swallow */ }
  };

  const handleBulkDeleteProspects = async () => {
    if (!window.confirm("Delete ALL prospects? This cannot be undone.")) return;
    try {
      await contactsApi.bulkDelete();
      setContacts([]);
      setSelectedProspect(null);
      void loadProspectBoard();
    } catch { /* swallow */ }
  };

  const clearDragState = () => setDragItem(null);

  useEffect(() => {
    const requestedDealId = searchParams.get("deal");
    if (!requestedDealId) return;

    const existing =
      Object.values(dealBoard)
        .flat()
        .find((deal) => deal.id === requestedDealId) ?? null;

    if (existing) {
      if (!selectedDeal || selectedDeal.id !== existing.id) {
        setTab("deal");
        setSelectedDeal(existing);
      }
      return;
    }

    if (currentBoardLoading) return;

    let cancelled = false;
    void dealsApi.get(requestedDealId)
      .then((deal) => {
        if (cancelled) return;
        setTab("deal");
        setSelectedDeal(deal);
      })
      .catch(() => {
        if (cancelled) return;
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.delete("deal");
          return next;
        }, { replace: true });
      });

    return () => {
      cancelled = true;
    };
  }, [searchParams, dealBoard, currentBoardLoading, setSearchParams]);

  const handleDealDrop = async (targetStage: string) => {
    if (!dragItem || dragItem.kind !== "deal") return;
    if (dragItem.fromStage === targetStage) {
      clearDragState();
      return;
    }
    const draggedDeal = allDeals.find((deal) => deal.id === dragItem.id);
    setPendingDealMove({
      dealId: dragItem.id,
      dealName: draggedDeal?.name || "this deal",
      fromStage: dragItem.fromStage,
      targetStage,
    });
    clearDragState();
  };

  const confirmPendingDealMove = async () => {
    if (!pendingDealMove) return;
    setBusyStage(pendingDealMove.targetStage);
    try {
      await dealsApi.moveStage(pendingDealMove.dealId, pendingDealMove.targetStage);
      await loadBoard();
    } finally {
      setBusyStage(null);
      setPendingDealMove(null);
    }
  };

  const handleProspectDrop = async (targetStage: ProspectStageId) => {
    if (!dragItem || dragItem.kind !== "prospect") return;
    if (dragItem.fromStage === targetStage) {
      clearDragState();
      return;
    }
    const draggedProspect = contacts.find((contact) => contact.id === dragItem.id) ?? null;
    setBusyStage(targetStage);
    try {
      await contactsApi.update(dragItem.id, prospectPatch(targetStage));
      await loadBoard();
      if (targetStage === "meeting_booked" && draggedProspect?.company_id) {
        setPendingConvertProspect({
          ...draggedProspect,
          ...prospectPatch(targetStage),
          tracking_stage: "meeting_booked",
        } as Contact);
      }
    } finally {
      setBusyStage(null);
      clearDragState();
    }
  };

  const handleConvertProspectToDeal = async (prospect?: Contact | null) => {
    const sourceProspect = prospect ?? selectedProspect;
    if (!sourceProspect || !sourceProspect.company_id) return;
    const company = companyMap.get(sourceProspect.company_id);
    setConvertingProspect(true);
    try {
      const deal = await dealsApi.create({
        name: `${company?.name ?? sourceProspect.company_name ?? "Account"} - ${contactName(sourceProspect)}`,
        pipeline_type: "deal",
        stage: "demo_done",
        company_id: sourceProspect.company_id,
        assigned_to_id: sourceProspect.assigned_to_id || undefined,
        geography: normalizeGeo(company?.region) || undefined,
        tags: ["converted_from_prospect"],
        next_step: "Review booked meeting and align demo follow-up",
      } as Partial<Deal>);
      await dealsApi.addContact(deal.id, sourceProspect.id, "champion");
      setPendingConvertProspect(null);
      setSelectedProspect(null);
      setSelectedDeal(deal);
      await loadBoard();
    } finally {
      setConvertingProspect(false);
    }
  };

  const handleSaveFunnelSettings = async (config: PipelineSummarySectionConfig) => {
    if (!isAdmin) return;
    setSavingFunnelSettings(true);
    try {
      const nextConfig: PipelineSummarySettings =
        tab === "deal"
          ? { ...pipelineSummaryConfig, deal: config }
          : { ...pipelineSummaryConfig, prospect: config };
      const saved = await settingsApi.updatePipelineSummarySettings(nextConfig);
      setPipelineSummaryConfig(normalizePipelineSummarySettings(saved));
      setShowFunnelSettings(false);
    } finally {
      setSavingFunnelSettings(false);
    }
  };

  const handleImportFromCrm = async () => {
    setImportingCrm(true);
    setCrmImportError("");
    try {
      // Fire the import — backend queues it as a background task and returns immediately.
      const { task_id } = await crmImportsApi.importClickUpSalesCrm({ replace_existing: true });

      // Poll every 5 seconds until the task completes.
      await new Promise<void>((resolve, reject) => {
        const interval = window.setInterval(async () => {
          try {
            const status = await crmImportsApi.getImportStatus(task_id);
            if (status.status === "success") {
              window.clearInterval(interval);
              setCrmImportResult(status.result ?? null);
              await loadBoard();
              resolve();
            } else if (status.status === "failure") {
              window.clearInterval(interval);
              reject(new Error(status.error ?? "Import failed"));
            }
            // pending / running — keep polling
          } catch (pollErr) {
            window.clearInterval(interval);
            reject(pollErr);
          }
        }, 5000);
      });
    } catch (err) {
      setCrmImportError(err instanceof Error ? err.message : "Failed to import from CRM");
    } finally {
      setImportingCrm(false);
    }
  };

  const handleProspectMigration = async (file: File) => {
    setMigratingProspects(true);
    try {
      const result = await contactsApi.importCsv(file);
      await loadBoard();
      const missingMessage = result.missing_company_count
        ? `\nPlaceholder companies created: ${result.missing_company_count} (they were imported now and can be enriched or remapped later)`
        : "";
      window.alert(
        `Prospect migration complete.\nImported rows: ${result.imported_rows}\nCreated: ${result.created_count}\nUpdated: ${result.updated_count}\nSkipped: ${result.skipped_count}${missingMessage}`,
      );
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Failed to migrate prospects");
    } finally {
      setMigratingProspects(false);
    }
  };

  return (
    <>
      <div className="crm-page pipeline-page" style={{ display: "flex", flexDirection: "row", alignItems: "stretch", width: "100%", height: "100%", minHeight: 0, gap: 0, overflow: "hidden" }}>
        <div className="desktop-only pipeline-sidebar" style={{ width: 260, flexShrink: 0, display: "flex", flexDirection: "column", background: "#fff", borderRight: "1px solid #e8eef5", padding: "20px 16px", gap: 18, overflowY: "auto" }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, position: "relative" }}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 800, color: "#0f2744", marginBottom: 4 }}>Pipeline</div>
              <div style={{ fontSize: 11, color: "#7a96b0", lineHeight: 1.5 }}>Drag cards across lanes to move deals or prospects through your stages. Click any card to open the detail drawer.</div>
            </div>
            <div style={{ position: "relative", flexShrink: 0 }}>
              <button
                type="button"
                onClick={() => setShowSidebarMenu((value) => !value)}
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: 10,
                  border: "1px solid #dbe6f2",
                  background: showSidebarMenu ? "#f0f6ff" : "#fff",
                  color: showSidebarMenu ? "#175089" : "#5e738b",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: "pointer",
                }}
                title="More actions"
              >
                <MoreHorizontal size={15} />
              </button>
              {showSidebarMenu && (
                <div
                  style={{
                    position: "absolute",
                    top: 36,
                    right: 0,
                    zIndex: 20,
                    minWidth: 170,
                    borderRadius: 12,
                    border: "1px solid #dbe6f2",
                    background: "#fff",
                    boxShadow: "0 12px 28px rgba(15, 35, 75, 0.12)",
                    padding: 6,
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                  }}
                >
                  {canImportCrm && (
                    <button
                      type="button"
                      onClick={() => {
                        setShowSidebarMenu(false);
                        setCrmImportError("");
                        setCrmImportResult(null);
                        setShowCrmImport(true);
                      }}
                      style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", border: "none", background: "transparent", borderRadius: 8, padding: "9px 10px", cursor: "pointer", color: "#28435c", fontSize: 12, fontWeight: 600, textAlign: "left" }}
                    >
                      <Building2 size={14} />Import CRM
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setShowSidebarMenu(false);
                      navigate("/prospecting");
                    }}
                    style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", border: "none", background: "transparent", borderRadius: 8, padding: "9px 10px", cursor: "pointer", color: "#28435c", fontSize: 12, fontWeight: 600, textAlign: "left" }}
                  >
                    <Target size={14} />Open Prospecting
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowSidebarMenu(false);
                      setCreateDealStage(effectiveDealStages.find((stage) => stage.group === "active")?.id ?? "reprospect");
                    }}
                    style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", border: "none", background: "#f8fbff", borderRadius: 8, padding: "9px 10px", cursor: "pointer", color: "#175089", fontSize: 12, fontWeight: 700, textAlign: "left" }}
                  >
                    <Plus size={14} />New Deal
                  </button>
                </div>
              )}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2 }}>View</span>
            {[{ key: "deal" as const, label: "Deals", sub: "Sales pipeline", icon: DollarSign, active: "#175089", soft: "#eaf2ff" }].map((item) => {
              const Icon = item.icon;
              const active = tab === item.key;
              return (
                <button key={item.key} type="button" onClick={() => { setTab(item.key); resetFilters(); }} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 10, cursor: "pointer", border: active ? `1.5px solid ${item.key === "deal" ? "#b8d0f0" : "#b2e0dc"}` : "1.5px solid transparent", background: active ? (item.key === "deal" ? "#f0f6ff" : "#f0faf9") : "transparent", textAlign: "left" }}>
                  <div style={{ width: 32, height: 32, borderRadius: 8, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: active ? item.active : item.soft, color: active ? "#fff" : item.active }}><Icon size={15} /></div>
                  <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 13, fontWeight: 600, color: active ? "#0f2744" : "#4d6178" }}>{item.label}</div><div style={{ fontSize: 10, color: "#7a96b0", marginTop: 1 }}>{item.sub}</div></div>
                </button>
              );
            })}
          </div>

          <div style={{ height: 1, background: "#e8eef5" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <span style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px" }}>Funnel summary</span>
              <div style={{ fontSize: 10, color: "#a0b2c5", marginTop: 2 }}>ToFU = top, MoFU = mid, BoFU = bottom</div>
            </div>
            {isAdmin && <button type="button" onClick={() => setShowFunnelSettings(true)} style={{ border: "1px solid #dbe6f2", background: "#fff", borderRadius: 8, width: 28, height: 28, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#5e738b", cursor: "pointer" }} title={`${tab === "deal" ? "Deal" : "Prospect"} summary settings`}><Settings2 size={14} /></button>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {summaryCardsToRender.map((card) => (
              <SummaryCard key={card.key} label={card.label} value={card.value} tone={card.tone ?? "default"} />
            ))}
          </div>
          <button
            type="button"
            onClick={() => exportPipelineCsv()}
            disabled={currentBoardLoading || summary.total === 0}
            style={{
              height: 36,
              borderRadius: 10,
              border: summary.total > 0 ? `1.5px solid ${accentBorder}` : "1px solid #e2eaf2",
              background: summary.total > 0 ? accentBg : "#f8fafc",
              color: summary.total > 0 ? accentColor : "#9badbf",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              fontSize: 12,
              fontWeight: 800,
              cursor: currentBoardLoading || summary.total === 0 ? "not-allowed" : "pointer",
            }}
            title={stageFilters.length ? "Export the currently filtered stages" : "Export all visible stages"}
          >
            <Download size={14} />
            Export visible list
          </button>

          <div style={{ height: 1, background: "#e8eef5" }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px", display: "flex", alignItems: "center", gap: 4 }}><Filter size={10} />Filters</span>
              {hasFilters && <button onClick={resetFilters} style={{ fontSize: 10, color: "#dc2626", background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 3, fontWeight: 500 }}><RotateCcw size={9} />Reset</button>}
            </div>
            <div style={{ position: "relative" }}>
              <Search size={12} style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", color: "#94a3b8" }} />
              <input type="text" placeholder={`Search ${tab === "deal" ? "deals" : "prospects"}...`} value={search} onChange={(event) => setSearch(event.target.value)} style={{ width: "100%", height: 32, borderRadius: 8, border: search ? "1.5px solid #b8d0f0" : "1px solid #e2eaf2", background: search ? "#f0f6ff" : "#f8fafc", paddingLeft: 28, paddingRight: 10, fontSize: 12, outline: "none" }} />
            </div>
            {/* Mine shortcut — one-click filter to deals owned by the current
                user. Toggles the Assignee filter atomically so the rest of
                the filter row stays consistent. */}
            {user?.id && (
              (() => {
                const mineActive = assigneeFilters.length === 1 && assigneeFilters[0] === user.id;
                return (
                  <button
                    type="button"
                    onClick={() => handleAssigneeFilterChange(mineActive ? [] : [user.id!])}
                    title={mineActive ? "Showing only your deals — click to clear" : "Show only deals assigned to you"}
                    style={{
                      height: 32,
                      padding: "0 12px",
                      borderRadius: 8,
                      border: mineActive ? "1.5px solid #cfe89a" : "1px solid #e2eaf2",
                      background: mineActive ? "#f3fbe3" : "#f8fafc",
                      color: mineActive ? "#a04a1c" : "#48607b",
                      fontSize: 12,
                      fontWeight: 700,
                      cursor: "pointer",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {mineActive ? "Mine ✓" : "Mine"}
                  </button>
                );
              })()
            )}
            <MultiSelectFilter values={stageFilters} onChange={handleStageFilterChange} label="Stage" allLabel="All Stages" options={stageOptions} />
            <MultiSelectFilter values={assigneeFilters} onChange={handleAssigneeFilterChange} label="Assignee" allLabel="All Reps" options={assigneeOptions} />
            <MultiSelectFilter values={geographyFilters} onChange={setGeographyFilters} label="Geography" allLabel="All Geographies" options={geographyOptions} />
            {tab === "deal" && <MultiSelectFilter values={tagFilters} onChange={setTagFilters} label="Tags" allLabel="All Tags" options={tagOptions} />}
            {tab === "deal" && <MultiSelectFilter values={priorityFilters} onChange={setPriorityFilters} label="Priority" allLabel="All Priorities" options={priorityOptions} />}
            {tab === "deal" && <MultiSelectFilter values={healthFilters} onChange={setHealthFilters} label="Health" allLabel="All Health" options={healthOptions} />}
            {tab === "deal" && <MultiSelectFilter values={closeDateFilters} onChange={setCloseDateFilters} label="Close Date" allLabel="Any Close Date" options={closeDateOptions} />}
            {tab === "deal" && <MultiSelectFilter values={nextStepFilters} onChange={setNextStepFilters} label="Next Step" allLabel="Any Next Step" options={nextStepOptions} />}
            {tab === "deal" && <MultiSelectFilter values={activityFilters} onChange={setActivityFilters} label="Activity" allLabel="Any Activity" options={activityOptions} />}
            {tab === "deal" && <MultiSelectFilter values={contactFilters} onChange={setContactFilters} label="Contacts" allLabel="Any Contacts" options={contactOptions} />}
            {tab === "deal" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px" }}>AE Triage</label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", height: 38, padding: "0 10px", borderRadius: 12, border: needsAttentionOnly ? "1.5px solid #c4b5fd" : "1px solid #e2eaf2", background: needsAttentionOnly ? "#f5f3ff" : "#f8fafc" }}>
                  <input
                    type="checkbox"
                    checked={needsAttentionOnly}
                    onChange={(e) => setNeedsAttentionOnly(e.target.checked)}
                    style={{ accentColor: "#7c3aed", width: 14, height: 14 }}
                  />
                  <span style={{ fontSize: 12, fontWeight: 600, color: needsAttentionOnly ? "#6d28d9" : "#2d4258" }}>Needs attention (no next step / overdue / stale / no contact)</span>
                </label>
              </div>
            )}
            {tab === "deal" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px" }}>Deal Aging</label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", height: 38, padding: "0 10px", borderRadius: 12, border: stalledOnly ? "1.5px solid #fcd34d" : "1px solid #e2eaf2", background: stalledOnly ? "#fffbeb" : "#f8fafc" }}>
                  <input
                    type="checkbox"
                    checked={stalledOnly}
                    onChange={(e) => handleStalledOnlyChange(e.target.checked)}
                    style={{ accentColor: "#d97706", width: 14, height: 14 }}
                  />
                  <span style={{ fontSize: 12, fontWeight: 600, color: stalledOnly ? "#b45309" : "#2d4258" }}>Stalled only (30d+ in stage)</span>
                </label>
              </div>
            )}
            {tab === "deal" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px" }}>Close Date Hygiene</label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", height: 38, padding: "0 10px", borderRadius: 12, border: overdueOnly ? "1.5px solid #fecaca" : "1px solid #e2eaf2", background: overdueOnly ? "#fef2f2" : "#f8fafc" }}>
                  <input
                    type="checkbox"
                    checked={overdueOnly}
                    onChange={(e) => handleOverdueOnlyChange(e.target.checked)}
                    style={{ accentColor: "#dc2626", width: 14, height: 14 }}
                  />
                  <span style={{ fontSize: 12, fontWeight: 600, color: overdueOnly ? "#b91c1c" : "#2d4258" }}>Overdue close dates only</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", height: 38, padding: "0 10px", borderRadius: 12, border: missingCloseDateOnly ? "1.5px solid #fde68a" : "1px solid #e2eaf2", background: missingCloseDateOnly ? "#fffbeb" : "#f8fafc" }}>
                  <input
                    type="checkbox"
                    checked={missingCloseDateOnly}
                    onChange={(e) => handleMissingCloseDateOnlyChange(e.target.checked)}
                    style={{ accentColor: "#d97706", width: 14, height: 14 }}
                  />
                  <span style={{ fontSize: 12, fontWeight: 600, color: missingCloseDateOnly ? "#b45309" : "#2d4258" }}>Missing close date only</span>
                </label>
                {closeMonthFilter && (
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, height: 38, padding: "0 10px", borderRadius: 12, border: "1.5px solid #c7d2fe", background: "#eef2ff" }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: "#4338ca" }}>Close month: {closeMonthFilter}</span>
                    <button
                      type="button"
                      onClick={() => {
                        setCloseMonthFilter("");
                        setSearchParams((current) => {
                          const next = new URLSearchParams(current);
                          next.delete("close_month");
                          return next;
                        }, { replace: true });
                      }}
                      style={{ border: "none", background: "transparent", color: "#4338ca", fontSize: 11, fontWeight: 800, cursor: "pointer" }}
                    >
                      Clear
                    </button>
                  </div>
                )}
              </div>
            )}
            {tab === "deal" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 10, fontWeight: 600, color: "#7a96b0", textTransform: "uppercase", letterSpacing: "0.5px" }}>Commit to Deal</label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", height: 38, padding: "0 10px", borderRadius: 12, border: commitFilter.length ? "1.5px solid #bbf7d0" : "1px solid #e2eaf2", background: commitFilter.length ? "#f0fdf4" : "#f8fafc" }}>
                  <input
                    type="checkbox"
                    checked={commitFilter.length > 0}
                    onChange={(e) => setCommitFilter(e.target.checked ? ["committed"] : [])}
                    style={{ accentColor: "#22c55e", width: 14, height: 14 }}
                  />
                  <span style={{ fontSize: 12, fontWeight: 600, color: commitFilter.length ? "#15803d" : "#2d4258" }}>Committed only</span>
                </label>
              </div>
            )}
          </div>

          <div style={{ flex: 1 }} />
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0, height: "100%", overflow: "hidden" }}>
          <div className="desktop-only" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 20px", borderBottom: "1px solid #e8eef5", background: "#fff" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h2 style={{ fontSize: 16, fontWeight: 700, color: "#0f2744", margin: 0 }}>{tab === "deal" ? "Deals" : "Prospects"} Board</h2>
              <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 10px", borderRadius: 999, background: accentBg, color: accentColor, border: `1px solid ${accentBorder}` }}>{currentBoardLoading ? "Loading..." : `${summary.total} visible`}</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 11, color: "#6b7f95" }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><GripVertical size={11} />{tab === "deal" ? "Drag to move stages · Click to open deal" : "Drag to move · Move to Meeting Booked to convert"}</span>
              {busyStage && <span style={{ color: "#4d7c0f", fontWeight: 600 }}>Updating lane...</span>}
            </div>
          </div>


          <div
            className="desktop-only pipeline-board-scroll"

            style={{ flex: 1, minHeight: 0, height: 0, overflowX: "auto", overflowY: "hidden", padding: "16px 16px 8px 20px", scrollbarGutter: "stable both-edges" }}
          >
            <div className="pipeline-board-row" style={{ display: "flex", gap: 12, minWidth: "max-content", height: "100%", minHeight: 0, alignItems: "stretch" }}>
              {(tab === "deal" ? stages : effectiveProspectStages).map((stage, index) => {
                const divider = index > 0 && (tab === "deal" ? effectiveDealStages[index - 1]?.group : effectiveProspectStages[index - 1]?.group) === "active" && stage.group === "closed";
                const dealItems = filteredDealBoard[stage.id] ?? [];
                const prospectItems = filteredProspects[stage.id as ProspectStageId] ?? [];
                return (
                  <div key={stage.id} style={{ display: "flex", gap: 12, height: "100%" }}>
                    {divider && <div style={{ width: 1, background: "linear-gradient(180deg, #dbe6f2 0%, transparent 100%)", margin: "28px 2px 0", alignSelf: "stretch" }} />}
                    <BoardColumn stage={stage} count={currentBoardLoading ? 0 : tab === "deal" ? dealItems.length : prospectItems.length} totalValue={currentBoardLoading || tab !== "deal" ? undefined : dealItems.reduce((sum, deal) => { const n = Number(deal.value); return sum + (Number.isFinite(n) ? n : 0); }, 0)} dropActive={dragItem ? (dragItem.kind === "deal" ? tab === "deal" && dragItem.fromStage !== stage.id : tab === "prospect" && dragItem.fromStage !== stage.id) : false} onAdd={tab === "deal" ? () => setCreateDealStage(stage.id) : undefined} onExport={currentBoardLoading ? undefined : () => exportPipelineCsv({ stage })} onDrop={() => tab === "deal" ? handleDealDrop(stage.id) : handleProspectDrop(stage.id as ProspectStageId)}>
                      {currentBoardLoading ? (
                        Array.from({ length: stage.group === "active" ? 3 : 1 }).map((_, skeletonIndex) => (
                          <LoadingCard key={`${stage.id}-skeleton-${skeletonIndex}`} kind={tab === "deal" ? "deal" : "prospect"} />
                        ))
                      ) : tab === "deal" ? (
                        dealItems.length ? dealItems.map((deal) => <DealCard key={deal.id} deal={deal} onClick={() => {
                          setSelectedDeal(deal);
                          setSearchParams((current) => {
                            const next = new URLSearchParams(current);
                            next.set("deal", deal.id);
                            return next;
                          }, { replace: true });
                        }} onDragStart={() => setDragItem({ kind: "deal", id: deal.id, fromStage: deal.stage })} onDragEnd={clearDragState} accountPriorityTag={deal.company_id ? companyMap.get(deal.company_id)?.priority_tag : undefined} selected={selectedDealIds.has(deal.id)} onToggleSelect={() => toggleDealSelect(deal.id)} />) : <div style={{ display: "flex", height: 88, alignItems: "center", justifyContent: "center", borderRadius: 12, border: "2px dashed #dbe6f2" }}><span style={{ fontSize: 11, color: "#96a7ba" }}>No deals</span></div>
                      ) : (
                        prospectItems.length ? prospectItems.map((contact) => <ProspectCard key={contact.id} contact={contact} company={contact.company_id ? companyMap.get(contact.company_id) : undefined} onOpen={() => setSelectedProspect(contact)} onDragStart={() => setDragItem({ kind: "prospect", id: contact.id, fromStage: prospectStage(contact) })} onDragEnd={clearDragState} onDelete={isAdmin ? () => handleDeleteProspect(contact.id) : undefined} />) : <div style={{ display: "flex", height: 88, alignItems: "center", justifyContent: "center", borderRadius: 12, border: "2px dashed #dbe6f2" }}><span style={{ fontSize: 11, color: "#96a7ba" }}>No prospects</span></div>
                      )}
                    </BoardColumn>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      <div className="mobile-only" style={{ padding: "4px 2px 80px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div className="mobile-card" style={{ position: "sticky", top: 0, zIndex: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "#0f2744" }}>{tab === "deal" ? "Pipeline" : "Prospects"}</div>
              <div style={{ fontSize: 12, color: "#7a96b0" }}>{summary.total} {tab === "deal" ? "deals" : "prospects"} · {summary.active} active</div>
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              <button onClick={() => { setTab("deal"); setMobileStage("all"); }} style={{ padding: "6px 12px", borderRadius: 8, border: tab === "deal" ? "1px solid #c5d6ff" : "1px solid #dce8f4", background: tab === "deal" ? "#eef4ff" : "#fff", color: tab === "deal" ? "#3555c4" : "#55657a", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>Deals</button>
              <button onClick={() => { setTab("prospect"); setMobileStage("all"); }} style={{ padding: "6px 12px", borderRadius: 8, border: tab === "prospect" ? "1px solid #c5d6ff" : "1px solid #dce8f4", background: tab === "prospect" ? "#eef4ff" : "#fff", color: tab === "prospect" ? "#3555c4" : "#55657a", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>Prospects</button>
            </div>
          </div>
          <div className="mobile-filter-scroll">
            <span onClick={() => setMobileStage("all")} className="mobile-filter-chip" style={{ background: mobileStage === "all" ? "#eef4ff" : "#fff", color: mobileStage === "all" ? "#3555c4" : "#55657a", borderColor: mobileStage === "all" ? "#c5d6ff" : "#d5e3ef" }}>All ({summary.total})</span>
            {(tab === "deal" ? stages : effectiveProspectStages).map(s => (
              <span key={s.id} onClick={() => setMobileStage(s.id)} className="mobile-filter-chip" style={{ background: mobileStage === s.id ? "#eef4ff" : "#fff", color: mobileStage === s.id ? "#3555c4" : "#55657a", borderColor: mobileStage === s.id ? "#c5d6ff" : "#d5e3ef" }}>{s.label} ({(tab === "deal" ? filteredDealBoard[s.id]?.length ?? 0 : filteredProspects[s.id as ProspectStageId]?.length ?? 0)})</span>
            ))}
          </div>
        </div>
        {tab === "deal" ? (
          stages.filter(s => mobileStage === "all" || s.id === mobileStage).map(stage => {
            const items = filteredDealBoard[stage.id] ?? [];
            if (!items.length) return null;
            return (
              <div key={stage.id}>
                <div style={{ fontSize: 11, fontWeight: 800, textTransform: "uppercase", letterSpacing: 0.4, color: stage.color ?? "#64748b", padding: "6px 4px 4px" }}>{stage.label} ({items.length})</div>
                {items.map(deal => (
                  <div key={deal.id} className="mobile-card" onClick={() => { setSelectedDeal(deal); setSearchParams(c => { c.set("deal", deal.id); return c; }, { replace: true }); }} style={{ cursor: "pointer" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 14, fontWeight: 800, color: "#1f2d3d", marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{deal.name}</div>
                        <div style={{ fontSize: 12, color: "#71859b" }}>{deal.company_id ? companyMap.get(deal.company_id)?.name ?? "Unknown" : "No company"}</div>
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                        <span style={{ fontSize: 16, fontWeight: 800, color: "#1f2a37" }}>{formatCurrency(deal.value)}</span>
                        {(() => { const p = companyMap.get(deal.company_id!)?.priority_tag; return p ? <span style={{ padding: "2px 8px", borderRadius: 999, fontSize: 10, fontWeight: 800, background: p === "P0" ? "#fef2f2" : p === "P1" ? "#fffbeb" : "#f1f5f9", color: p === "P0" ? "#b91c1c" : p === "P1" ? "#92400e" : "#475569", border: `1px solid ${p === "P0" ? "#fecaca" : p === "P1" ? "#fde68a" : "#cbd5e1"}` }}>{p}</span> : null; })()}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            );
          })
        ) : (
          effectiveProspectStages.filter(s => mobileStage === "all" || s.id === mobileStage).map(stage => {
            const items = filteredProspects[stage.id as ProspectStageId] ?? [];
            if (!items.length) return null;
            return (
              <div key={stage.id}>
                <div style={{ fontSize: 11, fontWeight: 800, textTransform: "uppercase", letterSpacing: 0.4, color: "#64748b", padding: "6px 4px 4px" }}>{stage.label} ({items.length})</div>
                {items.map(contact => (
                  <div key={contact.id} className="mobile-card" onClick={() => setSelectedProspect(contact)} style={{ cursor: "pointer" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 14, fontWeight: 800, color: "#1f2d3d", marginBottom: 4 }}>{contact.first_name} {contact.last_name}</div>
                        <div style={{ fontSize: 12, color: "#71859b" }}>{contact.title || contact.company_name || "No title"}</div>
                        {contact.company_id && companyMap.has(contact.company_id) && <div style={{ fontSize: 11, color: "#96a7ba" }}>{companyMap.get(contact.company_id)?.name}</div>}
                      </div>
                      <span style={{ padding: "4px 10px", borderRadius: 999, fontSize: 10, fontWeight: 800, whiteSpace: "nowrap", background: "#f1f5f9", color: "#475569", border: "1px solid #cbd5e1" }}>{stage.label}</span>
                    </div>
                  </div>
                ))}
              </div>
            );
          })
        )}
      </div>

      {tab === "deal" && selectedDealIds.size > 0 && (
        <div style={{ position: "fixed", left: "50%", bottom: 20, transform: "translateX(-50%)", zIndex: 60, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "10px 14px", borderRadius: 14, background: "#0b0c0e", boxShadow: "0 18px 44px rgba(11,12,14,0.42)", border: "1px solid #23262b", maxWidth: "calc(100vw - 24px)" }}>
          <span style={{ fontSize: 13, fontWeight: 800, color: "#fff", whiteSpace: "nowrap" }}>{selectedDealIds.size} selected</span>
          <span style={{ width: 1, height: 22, background: "rgba(255,255,255,0.14)" }} />
          <select disabled={bulkBusy} value="" onChange={(e) => { if (e.target.value) runBulk({ stage: e.target.value }); }} style={{ height: 32, borderRadius: 8, border: "1px solid rgba(255,255,255,0.18)", background: "#16181c", color: "#fff", fontSize: 12.5, fontWeight: 600, padding: "0 8px", cursor: "pointer" }}>
            <option value="" disabled>Move to stage…</option>
            {effectiveDealStages.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <select disabled={bulkBusy} value="" onChange={(e) => { const v = e.target.value; if (v) runBulk({ reassign: true, assigned_to_id: v === "__unassign__" ? null : v }); }} style={{ height: 32, borderRadius: 8, border: "1px solid rgba(255,255,255,0.18)", background: "#16181c", color: "#fff", fontSize: 12.5, fontWeight: 600, padding: "0 8px", cursor: "pointer" }}>
            <option value="" disabled>Reassign to…</option>
            <option value="__unassign__">Unassigned</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <input value={bulkTag} onChange={(e) => setBulkTag(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && bulkTag.trim()) runBulk({ add_tags: [bulkTag.trim()] }); }} placeholder="Add tag…" disabled={bulkBusy} style={{ height: 32, width: 104, borderRadius: 8, border: "1px solid rgba(255,255,255,0.18)", background: "#16181c", color: "#fff", fontSize: 12.5, padding: "0 10px", outline: "none" }} />
          <button type="button" disabled={bulkBusy || !bulkTag.trim()} onClick={() => bulkTag.trim() && runBulk({ add_tags: [bulkTag.trim()] })} style={{ height: 32, borderRadius: 8, border: "none", background: "#6fae27", color: "#fff", fontSize: 12.5, fontWeight: 800, padding: "0 12px", cursor: bulkBusy || !bulkTag.trim() ? "default" : "pointer", opacity: bulkBusy || !bulkTag.trim() ? 0.5 : 1 }}>Add tag</button>
          <button type="button" disabled={bulkBusy} onClick={bulkExportSelected} style={{ height: 32, borderRadius: 8, border: "1px solid rgba(255,255,255,0.18)", background: "rgba(255,255,255,0.06)", color: "#fff", fontSize: 12.5, fontWeight: 700, padding: "0 12px", cursor: "pointer" }}>Export</button>
          <button type="button" onClick={clearSelection} style={{ height: 32, borderRadius: 8, border: "none", background: "transparent", color: "#c7cdd6", fontSize: 12.5, fontWeight: 700, padding: "0 8px", cursor: "pointer" }}>Clear</button>
          {bulkBusy && <span style={{ fontSize: 12, fontWeight: 700, color: "#9ace3d" }}>Working…</span>}
        </div>
      )}

      {createDealStage && <CreateDealModal defaultStage={createDealStage} companies={companies} users={users} onClose={() => setCreateDealStage(null)} onCreated={handleDealCreated} stages={effectiveDealStages} />}
      {selectedDeal && <DealDetailDrawer key={selectedDeal.id} deal={selectedDeal} companies={companies} users={users} stages={effectiveDealStages} onClose={() => {
        setSelectedDeal(null);
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.delete("deal");
          return next;
        }, { replace: true });
      }} onDealUpdated={handleDealUpdated} onDealDeleted={handleDealDeleted} onCompanyUpdated={(updated) => setCompanies((prev) => prev.some((c) => c.id === updated.id) ? prev.map((c) => c.id === updated.id ? updated : c) : [...prev, updated])} />}
      {selectedProspect && <ProspectDetailDrawer contact={selectedProspect} company={selectedProspect.company_id ? companyMap.get(selectedProspect.company_id) : undefined} companies={companies} activities={prospectActivities} loading={loadingProspectActivities} converting={convertingProspect} onConvert={handleConvertProspectToDeal} stages={effectiveProspectStages} onClose={() => setSelectedProspect(null)} onUpdated={loadProspectBoard} />}
      {pendingDealMove && (
        <>
          <div
            style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.22)", zIndex: 60 }}
            onClick={busyStage ? undefined : () => setPendingDealMove(null)}
          />
          <div style={{ position: "fixed", inset: 0, zIndex: 61, display: "grid", placeItems: "center", padding: 16 }}>
            <div className="crm-panel" style={{ width: "min(520px, 100%)", padding: 24, borderRadius: 18 }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: "#182042", marginBottom: 10 }}>Move this deal?</div>
              <div style={{ color: "#5e738b", fontSize: 14, lineHeight: 1.7, marginBottom: 18 }}>
                <strong style={{ color: "#182042" }}>{pendingDealMove.dealName}</strong> will move from{" "}
                <strong>{dealStageLabelMap.get(pendingDealMove.fromStage) ?? pendingDealMove.fromStage}</strong> to{" "}
                <strong>{dealStageLabelMap.get(pendingDealMove.targetStage) ?? pendingDealMove.targetStage}</strong>.
                {" "}This will update pipeline tracking for the deal.
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button
                  type="button"
                  className="crm-button soft"
                  disabled={Boolean(busyStage)}
                  onClick={() => setPendingDealMove(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="crm-button primary"
                  disabled={Boolean(busyStage)}
                  onClick={confirmPendingDealMove}
                >
                  {busyStage ? "Moving..." : "Yes, move it"}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
      {pendingConvertProspect && (
        <>
          <div style={{ position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.22)", zIndex: 60 }} onClick={() => setPendingConvertProspect(null)} />
          <div style={{ position: "fixed", inset: 0, zIndex: 61, display: "grid", placeItems: "center", padding: 16 }}>
            <div className="crm-panel" style={{ width: "min(520px, 100%)", padding: 24, borderRadius: 18 }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: "#182042", marginBottom: 10 }}>Convert this prospect to a deal?</div>
              <div style={{ color: "#5e738b", fontSize: 14, lineHeight: 1.7, marginBottom: 18 }}>
                {contactName(pendingConvertProspect)} was moved to <strong>Meeting Booked</strong>. You can create a deal now, or skip and convert later from the prospect card.
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button type="button" className="crm-button soft" onClick={() => setPendingConvertProspect(null)}>
                  Not now
                </button>
                <button type="button" className="crm-button primary" disabled={convertingProspect} onClick={() => handleConvertProspectToDeal(pendingConvertProspect)}>
                  {convertingProspect ? "Converting..." : "Convert to Deal"}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
      {showFunnelSettings && (
        <FunnelSettingsModal
          title={funnelModalTitle}
          description={funnelModalDescription}
          stages={funnelModalStages}
          config={activeFunnelConfig}
          defaultConfig={activeFunnelDefaults}
          saving={savingFunnelSettings}
          onClose={() => setShowFunnelSettings(false)}
          onSave={handleSaveFunnelSettings}
        />
      )}
      {showCrmImport && (
        <CrmImportModal
          importing={importingCrm}
          result={crmImportResult}
          error={crmImportError}
          onClose={() => {
            if (!importingCrm) setShowCrmImport(false);
          }}
          onImport={handleImportFromCrm}
        />
      )}
      {showAddProspect && (
        <AddProspectModal
          companies={companies}
          onClose={() => setShowAddProspect(false)}
          onCreated={() => { setShowAddProspect(false); loadProspectBoard(); }}
        />
      )}
    </>
  );
}

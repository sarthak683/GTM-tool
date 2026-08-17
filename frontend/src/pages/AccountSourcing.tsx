import "./account-sourcing-refresh.css";
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import {
  AlertCircle,
  Brain,
  Building2,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  Flame,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Target,
  TrendingUp,
  Upload,
  Users,
  X,
} from "lucide-react";

import { accountSourcingApi, assignmentsApi } from "../lib/api";
import { useToast } from "../lib/ToastContext";
import { ACCOUNT_STATUS_OPTIONS, accountStatusOption } from "../lib/accountStatus";
import {
  AccountSourcingHttpError,
  type AccountSourcingFilters,
  type AccountSourcingSort,
  type RecotapSummary,
  type SourcingDataHealth,
} from "../lib/api/prospecting";
import { getCachedUsers } from "../lib/cachedFetch";
import { getAccountPrioritySnapshot } from "../lib/utils";
import type { AccountSourcingSummary, Company, SourcingBatch, User } from "../types";
import AssignDropdown from "../components/AssignDropdown";
import BulkReassignUpload from "../components/BulkReassignUpload";
import MultiSelectFilter from "../components/filters/MultiSelectFilter";
import {
  cardStyle,
  colors,
  containerStyle,
  DISPOSITION_OPTIONS,
  formatBatchStage,
  ICP_STYLE,
  OUTREACH_LANE_OPTIONS,
  pageStyle,
  parseManualCompanyLines,
  parseSearchParamList,
  TIER_OPTIONS,
  ts,
} from "./accountSourcingShared";

type AccountSortKey = "recent" | "icp_desc" | "priority_desc" | "enriched_first" | "unenriched_first" | "name_asc" | "name_desc";

const ACCOUNT_SORT_OPTIONS: { value: AccountSortKey; label: string }[] = [
  { value: "recent", label: "Newest first" },
  { value: "icp_desc", label: "ICP score high to low" },
  // Priority is derived in the browser from JSONB the server does not sort on,
  // so it can only order the rows already loaded. Labelled so nobody mistakes
  // it for a whole-workspace ranking.
  { value: "priority_desc", label: "Priority (this page)" },
  { value: "enriched_first", label: "Enriched first" },
  { value: "unenriched_first", label: "Needs enrichment first" },
  { value: "name_asc", label: "Company A → Z" },
  { value: "name_desc", label: "Company Z → A" },
];

/**
 * Sort options the SERVER understands, so sorting orders the whole result set
 * rather than the 40 rows that happen to be on screen. `recent` is the
 * server's own default (created_at desc) so it sends nothing; `priority_desc`
 * has no server equivalent and stays client-side, page-scoped.
 */
const SERVER_SORT: Record<AccountSortKey, AccountSourcingSort> = {
  recent: {},
  icp_desc: { sort: "icp_score", order: "desc" },
  priority_desc: {},
  enriched_first: { sort: "enriched_at", order: "desc" },
  unenriched_first: { sort: "enriched_at", order: "asc" },
  name_asc: { sort: "name", order: "asc" },
  name_desc: { sort: "name", order: "desc" },
};

function parseAccountSort(value: string | null): AccountSortKey {
  return ACCOUNT_SORT_OPTIONS.some((option) => option.value === value) ? (value as AccountSortKey) : "recent";
}

type AccountSourcingTab = "accounts" | "imports" | "review";

function parseAccountTab(value: string | null): AccountSourcingTab {
  return value === "imports" || value === "review" ? value : "accounts";
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

// Slim KPI stat block — number + 11px label only. The longer caption lives in
// the title tooltip so the whole 8-up strip stays under ~72px tall.
function SummaryCard({
  icon,
  label,
  value,
  hint,
  tone = "neutral",
  onClick,
  active = false,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  hint: string;
  tone?: "neutral" | "primary" | "warm" | "green";
  onClick?: () => void;
  active?: boolean;
}) {
  const toneStyle = {
    neutral: { bg: "#f8fbff", accent: colors.sub, activeBorder: "#94a3b8" },
    primary: { bg: "#f3fbe3", accent: colors.primary, activeBorder: colors.primary },
    warm: { bg: "#fff7eb", accent: colors.amber, activeBorder: colors.amber },
    green: { bg: "#eefcf5", accent: colors.green, activeBorder: colors.green },
  }[tone];

  return (
    <div
      className="as-stat-block"
      title={active && onClick ? `${hint} Filtered — click to clear.` : hint}
      style={{
        border: `1px solid ${active ? toneStyle.activeBorder : "#e3e9f2"}`,
        boxShadow: active ? `0 0 0 2px ${toneStyle.bg}` : "0 1px 2px rgba(15,23,42,0.04)",
        background: active ? toneStyle.bg : "#ffffff",
        borderRadius: 10,
        padding: "9px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 3,
        minWidth: 0,
        cursor: onClick ? "pointer" : "default",
      }}
      onClick={onClick}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <span style={{ color: toneStyle.accent, display: "inline-flex", flexShrink: 0 }}>{icon}</span>
        <span style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4, textTransform: "uppercase", lineHeight: 1.25, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>{label}</span>
      </div>
      <div style={{ color: active ? toneStyle.accent : colors.text, fontSize: 19, fontWeight: 800, lineHeight: 1.15 }}>{value}</div>
    </div>
  );
}

// Shared row style for the "More ⋯" toolbar dropdown items.
const MENU_ITEM_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  border: 0,
  background: "transparent",
  color: "#142335",
  borderRadius: 8,
  padding: "9px 10px",
  fontSize: 13,
  fontWeight: 700,
  cursor: "pointer",
  textAlign: "left",
};

function UploadPanel({
  onUploaded,
  onDownloadTemplate,
}: {
  onUploaded: (batch: SourcingBatch) => void;
  onDownloadTemplate: () => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  const onFile = async (file: File) => {
    const lowerName = file.name.toLowerCase();
    if (!lowerName.endsWith(".csv") && !lowerName.endsWith(".xlsx")) {
      setError("Please upload a .csv or .xlsx file");
      return;
    }
    setUploading(true);
    setError("");
    try {
      const batch = await accountSourcingApi.upload(file);
      onUploaded(batch);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <label
        title="Upload a CSV/XLSX workbook to import accounts in bulk"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          background: uploading ? "#eef7db" : "#6fae27",
          color: uploading ? colors.sub : "#fff",
          padding: "8px 14px",
          borderRadius: 10,
          fontWeight: 700,
          fontSize: 13,
          cursor: uploading ? "wait" : "pointer",
          border: "1px solid transparent",
        }}
      >
        {uploading ? (
          <>
            <Loader2 size={14} className="animate-spin" /> Uploading…
          </>
        ) : (
          <>
            <Upload size={14} /> Import workbook
          </>
        )}
        <input
          type="file"
          accept=".csv,.xlsx"
          disabled={uploading}
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
            e.currentTarget.value = "";
          }}
        />
      </label>
      <button
        type="button"
        onClick={onDownloadTemplate}
        style={{
          border: `1px solid ${colors.border}`,
          background: "#fff",
          color: colors.sub,
          borderRadius: 10,
          padding: "8px 12px",
          fontSize: 12,
          fontWeight: 700,
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <Download size={12} /> Template
      </button>
      {error ? (
        <span
          style={{
            color: colors.red,
            fontSize: 12,
            fontWeight: 600,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <AlertCircle size={12} /> {error}
        </span>
      ) : null}
    </div>
  );
}

// Recotap journey-stage + engagement badge styling (low → high intent).
const JOURNEY_STAGE_STYLE: Record<string, { bg: string; color: string; border: string }> = {
  Unaware: { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" },
  Aware: { bg: "#eff6ff", color: "#1d4ed8", border: "#bfdbfe" },
  Consideration: { bg: "#fff7ed", color: "#c2410c", border: "#fed7aa" },
  Opportunity: { bg: "#f5f3ff", color: "#6d28d9", border: "#ddd6fe" },
  Customer: { bg: "#ecfdf5", color: "#047857", border: "#a7f3d0" },
};
const ENGAGEMENT_STYLE: Record<string, { bg: string; color: string; border: string }> = {
  Hot: { bg: "#fef2f2", color: "#b91c1c", border: "#fecaca" },
  Warm: { bg: "#fffbeb", color: "#92400e", border: "#fde68a" },
  Cold: { bg: "#eff6ff", color: "#1d4ed8", border: "#bfdbfe" },
};
const NEUTRAL_BADGE = { bg: "#f4f7fb", color: "#55657a", border: "#d9e1ec" };
const JOURNEY_STAGE_ORDER = ["Unaware", "Aware", "Consideration", "Opportunity", "Customer"];
const JOURNEY_FILTER_OPTIONS = [
  ...JOURNEY_STAGE_ORDER.map((s) => ({ value: s, label: s })),
  { value: "not_scored", label: "Not scored" },
];

// Manual account-status filter options. "unset" mirrors the backend sentinel
// for accounts with no status (parallels the Unassigned owner convention).
const ACCOUNT_STATUS_FILTER_OPTIONS = [
  ...ACCOUNT_STATUS_OPTIONS.map((option) => ({ value: option.value, label: option.label })),
  { value: "unset", label: "No status" },
];

// The Recotap buying-journey funnel — the ABM centerpiece of Account Sourcing.
// Each stage tile shows its account count, is color-coded low→high intent, and
// click-filters the list. Engagement chips + a Sync button sit in the header.
function JourneyFunnel({
  summary,
  active,
  onToggle,
  onSync,
  syncing,
}: {
  summary: RecotapSummary | null;
  active: string[];
  onToggle: (stage: string) => void;
  onSync: () => void;
  syncing: boolean;
}) {
  if (!summary) return null;
  const stages = JOURNEY_STAGE_ORDER;
  const maxCount = Math.max(1, ...stages.map((s) => summary.stages[s] ?? 0));
  const eng = summary.engagement || {};
  return (
    <div style={{ border: "1px solid #e3ebf4", borderRadius: 16, background: "linear-gradient(180deg,#fbfdff,#eff5ff)", padding: "16px 18px", marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 15, fontWeight: 800, color: "#16273d" }}>Buying Journey</span>
          <span style={{ fontSize: 10, fontWeight: 800, color: "#5b6ef5", background: "#eef0ff", border: "1px solid #dfe3ff", borderRadius: 999, padding: "3px 9px", textTransform: "uppercase", letterSpacing: "0.05em" }}>Powered by Recotap</span>
          <span style={{ fontSize: 11.5, color: "#7f8fa5" }}>{summary.scored} scored · {summary.not_scored} not scored</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {(["Hot", "Warm", "Cold"] as const).map((k) => (
            <span key={k} style={{ fontSize: 11, fontWeight: 700, color: ENGAGEMENT_STYLE[k].color, background: ENGAGEMENT_STYLE[k].bg, border: `1px solid ${ENGAGEMENT_STYLE[k].border}`, borderRadius: 999, padding: "3px 9px" }}>{k} {eng[k] ?? 0}</span>
          ))}
          <button type="button" onClick={onSync} disabled={syncing} style={{ fontSize: 11.5, fontWeight: 700, color: "#24567e", background: "#fff", border: "1px solid #cbd9ec", borderRadius: 8, padding: "5px 10px", cursor: syncing ? "default" : "pointer", opacity: syncing ? 0.6 : 1, display: "inline-flex", alignItems: "center", gap: 6 }}>
            <RefreshCw size={12} className={syncing ? "animate-spin" : undefined} />{syncing ? "Syncing…" : "Sync Recotap"}
          </button>
        </div>
      </div>
      <div className="as-journey-funnel" style={{ display: "grid", gap: 8 }}>
        {stages.map((stage, i) => {
          const count = summary.stages[stage] ?? 0;
          const s = JOURNEY_STAGE_STYLE[stage] ?? NEUTRAL_BADGE;
          const isActive = active.includes(stage);
          const pct = Math.round((count / maxCount) * 100);
          return (
            <button
              key={stage}
              type="button"
              onClick={() => onToggle(stage)}
              title={`${count} account${count === 1 ? "" : "s"} · ${stage}`}
              style={{
                textAlign: "left",
                border: `1.5px solid ${isActive ? s.color : s.border}`,
                background: isActive ? s.bg : "#fff",
                borderRadius: 12,
                padding: "10px 12px",
                cursor: "pointer",
                boxShadow: isActive ? `0 0 0 2px ${s.bg}` : "none",
                display: "flex",
                flexDirection: "column",
                gap: 8,
                minWidth: 0,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 800, color: s.color, textTransform: "uppercase", letterSpacing: "0.03em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{stage}</span>
                <span style={{ fontSize: 9.5, fontWeight: 800, color: "#9aa7b8", flexShrink: 0 }}>{i + 1}/{stages.length}</span>
              </div>
              <span style={{ fontSize: 24, fontWeight: 800, color: "#16273d", lineHeight: 1 }}>{count}</span>
              <div style={{ height: 5, borderRadius: 999, background: "#eef2f7", overflow: "hidden" }}>
                <div style={{ width: `${pct}%`, height: "100%", background: s.color, borderRadius: 999 }} />
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Stable colored avatar (initial) for the account row — Recotap-style.
const AVATAR_PALETTE = [
  ["#eef2ff", "#4f46e5"], ["#ecfdf5", "#047857"], ["#fff7ed", "#c2410c"],
  ["#eff6ff", "#1d4ed8"], ["#fdf4ff", "#a21caf"], ["#fef2f2", "#b91c1c"], ["#f0fdfa", "#0f766e"],
];
function CompanyAvatar({ name }: { name: string }) {
  const clean = (name || "?").trim();
  const initial = clean.charAt(0).toUpperCase() || "?";
  const idx = [...clean].reduce((acc, ch) => acc + ch.charCodeAt(0), 0) % AVATAR_PALETTE.length;
  const [bg, fg] = AVATAR_PALETTE[idx];
  return (
    <div style={{ width: 32, height: 32, borderRadius: 9, background: bg, color: fg, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontSize: 13, flexShrink: 0 }}>
      {initial}
    </div>
  );
}

// Column header for the Recotap-style account table (desktop only).
// Prefer a real name; if only an email exists, derive a readable display name
// (e.g. "pulkit@beacon.li" -> "Pulkit") so owner chips never render a raw email.
function prettyRepName(name?: string | null, email?: string | null): string | null {
  const n = (name || "").trim();
  if (n) return n;
  const e = (email || "").trim();
  if (e.includes("@")) {
    const local = e.split("@")[0].replace(/[._-]+/g, " ").trim();
    if (local) return local.split(" ").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  }
  return e || null;
}

// Owner chips show first name only (full name + role lives in the wrapper's
// title tooltip) so "Pravalika" never truncates inside the AE·SDR column.
function firstNameOnly(name: string | null): string | null {
  if (!name) return null;
  return name.trim().split(/\s+/)[0] || name;
}

function CompanyTableHeader({
  selectable,
  allSelected,
  onToggleAll,
}: {
  selectable?: boolean;
  allSelected?: boolean;
  onToggleAll?: () => void;
} = {}) {
  const cell: CSSProperties = { fontSize: 11, fontWeight: 800, color: "#9aa7b8", textTransform: "uppercase", letterSpacing: "0.05em" };
  return (
    <div className={`as-company-table-header${selectable ? " as-selectable" : ""}`} style={{ padding: "2px 14px 6px" }}>
      {selectable && (
        <input
          type="checkbox"
          checked={!!allSelected}
          onChange={onToggleAll}
          aria-label="Select all visible accounts"
          style={{ width: 16, height: 16, accentColor: colors.primary, cursor: "pointer", justifySelf: "center" }}
        />
      )}
      <span style={cell}>Account</span>
      <span style={cell}>Signals</span>
      <span style={{ ...cell, textAlign: "right" }}>AE · SDR</span>
    </div>
  );
}

function CompanyCard({
  company,
  onAssigned,
  selectable,
  selected,
  onToggleSelect,
}: {
  company: Company;
  onAssigned: (userId: string | null, userName: string | null) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (companyId: string) => void;
}) {
  const nav = useNavigate();

  const tier = company.icp_tier || "cold";
  const hasIcp = !!company.icp_tier;  // unscored accounts must not masquerade as "cold"
  const disposition = company.disposition || "";
  const statusOption = accountStatusOption(company.account_status);
  const rtp = company.recotap;
  const journeyStyle = rtp?.journey_stage ? (JOURNEY_STAGE_STYLE[rtp.journey_stage] ?? NEUTRAL_BADGE) : NEUTRAL_BADGE;
  const engagementStyle = rtp?.engagement ? (ENGAGEMENT_STYLE[rtp.engagement] ?? NEUTRAL_BADGE) : NEUTRAL_BADGE;
  const domainText = company.domain.endsWith(".unknown") ? "Domain unresolved" : company.domain;
  const aeName = prettyRepName(company.assigned_rep_name || company.assigned_rep, company.assigned_rep_email);
  const sdrName = prettyRepName(company.sdr_name, company.sdr_email);

  return (
    <div
      className={`as-company-card as-company-row crm-hover-lift${selectable ? " as-selectable" : ""}${selected ? " as-selected" : ""}`}
      onClick={() => nav(`/account-sourcing/${company.id}`)}
      style={{
        ...cardStyle,
        borderRadius: 12,
        boxShadow: "0 2px 8px rgba(17,34,68,0.04)",
        padding: "8px 14px",
        cursor: "pointer",
        transition: "transform 150ms cubic-bezier(0.22, 1, 0.36, 1), box-shadow 150ms ease, background 0.15s",
        ...(selected ? { background: "#f3f9ea", borderColor: "#cfe89a" } : null),
      }}
      onMouseEnter={(e) => { if (!selected) e.currentTarget.style.background = "#f8fbff"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = selected ? "#f3f9ea" : "#ffffff"; }}
    >
      {selectable && (
        <div
          className="as-col-select"
          onClick={(e) => e.stopPropagation()}
          style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
        >
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onToggleSelect?.(company.id)}
            aria-label={`Select ${company.name}`}
            style={{ width: 16, height: 16, accentColor: colors.primary, cursor: "pointer" }}
          />
        </div>
      )}
      {/* Account */}
      <div className="as-col-account" style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <CompanyAvatar name={company.name} />
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
          <div style={{ color: colors.text, fontWeight: 800, fontSize: 13.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{company.name}</div>
          <div style={{ color: colors.faint, fontSize: 11.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>
            {domainText}{company.industry ? ` · ${company.industry}` : ""}
          </div>
        </div>
      </div>

      {/* Signals — one content-driven chip cluster; shows only what exists, so
          there are no perpetually-empty columns. ICP tier always renders; the
          rest (status, disposition, Recotap journey/engagement, HQ) appear only
          when set. Wraps to a second line on dense rows. */}
      <div className="as-col-signals" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 5, minWidth: 0 }}>
        {hasIcp ? (
          <span title="ICP fit — how well this account matches our ideal customer profile (not buying intent)" style={{ ...ICP_STYLE[tier], borderRadius: 999, fontSize: 11, fontWeight: 800, padding: "2px 8px", whiteSpace: "nowrap" }}>ICP · {tier.toUpperCase()}</span>
        ) : null}
        {statusOption ? (
          // Status is the primary at-a-glance signal, so it gets a SOLID filled
          // pill (colored background + white text) rather than the soft tint used
          // by the neighboring chips — this makes it pop in a dense row. Colors
          // reuse accountStatusOption() so they stay in lockstep with the detail
          // page and analytics. (Matches the "active" status badge on the
          // company detail page.)
          <span title={`Account status · ${statusOption.label}`} style={{ background: statusOption.color, color: "#fff", borderRadius: 999, padding: "2px 9px", fontSize: 11, fontWeight: 800, letterSpacing: "0.02em", whiteSpace: "nowrap", boxShadow: `0 1px 3px ${statusOption.color}55` }}>{statusOption.label}</span>
        ) : null}
        {disposition ? (
          <span style={{ background: "#f4f7fb", color: colors.sub, border: `1px solid ${colors.border}`, borderRadius: 999, padding: "2px 8px", fontSize: 11, fontWeight: 700, whiteSpace: "nowrap" }}>{disposition}</span>
        ) : null}
        {rtp?.journey_stage ? (
          <span title="Recotap journey stage" style={{ background: journeyStyle.bg, color: journeyStyle.color, border: `1px solid ${journeyStyle.border}`, borderRadius: 999, padding: "2px 8px", fontSize: 11, fontWeight: 800, whiteSpace: "nowrap" }}>{rtp.journey_stage}</span>
        ) : null}
        {rtp?.engagement ? (
          <span title="Recotap buying intent (engagement, from account score)" style={{ background: engagementStyle.bg, color: engagementStyle.color, border: `1px solid ${engagementStyle.border}`, borderRadius: 999, padding: "2px 8px", fontSize: 11, fontWeight: 700, whiteSpace: "nowrap" }}>Intent · {rtp.engagement}</span>
        ) : null}
        {rtp?.hq_location ? (
          <span style={{ color: colors.faint, fontSize: 11.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{rtp.hq_location}</span>
        ) : null}
      </div>

      {/* Owners (AE + SDR) — first names only (full name in tooltip), never raw emails */}
      <div className="as-col-owners as-company-card-assign" style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 6 }} onClick={(e) => e.stopPropagation()}>
        <span title={aeName ? `AE · ${aeName}` : "Assign AE"} style={{ display: "inline-flex" }}>
          <AssignDropdown
            entityType="company"
            entityId={company.id}
            role="ae"
            currentAssignedId={company.assigned_to_id ?? null}
            currentAssignedName={firstNameOnly(aeName)}
            onAssigned={onAssigned}
            compact
            label="AE"
          />
        </span>
        <span title={sdrName ? `SDR · ${sdrName}` : "Assign SDR"} style={{ display: "inline-flex" }}>
          <AssignDropdown
            entityType="company"
            entityId={company.id}
            role="sdr"
            currentAssignedId={company.sdr_id ?? null}
            currentAssignedName={firstNameOnly(sdrName)}
            onAssigned={onAssigned}
            compact
            label="SDR"
          />
        </span>
        <ChevronRight size={16} color={colors.faint} style={{ flexShrink: 0 }} />
      </div>
    </div>
  );
}

// ── "Needs review" tab (admin only) ─────────────────────────────────────────
// A dense review queue over GET /account-sourcing/data-health. The report is
// strictly read-only; every fix here goes through the SAME merge / company
// update / assignment endpoints the rest of the app uses. Rows disappear (and
// their section total drops) as they are resolved.

const REVIEW_HEAD: CSSProperties = {
  fontSize: 11,
  fontWeight: 800,
  color: "#9aa7b8",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};
const REVIEW_CELL: CSSProperties = { fontSize: 12, color: colors.sub, minWidth: 0 };
const REVIEW_STRONG: CSSProperties = { fontSize: 12.5, fontWeight: 800, color: colors.text, minWidth: 0 };
const REVIEW_ACTION: CSSProperties = {
  border: `1px solid ${colors.border}`,
  background: "#fff",
  color: colors.text,
  borderRadius: 10,
  padding: "5px 9px",
  fontSize: 11,
  fontWeight: 800,
  cursor: "pointer",
  whiteSpace: "nowrap",
};
const REVIEW_ACTION_PRIMARY: CSSProperties = {
  ...REVIEW_ACTION,
  border: "1px solid #9ace3d",
  background: "#f3fbe3",
  color: "#4d7c0f",
};

function ReviewSection({
  title,
  blurb,
  total,
  shown,
  onLoadAll,
  loadingAll,
  children,
}: {
  title: string;
  blurb: string;
  total: number;
  shown: number;
  onLoadAll: () => void;
  loadingAll: boolean;
  children: ReactNode;
}) {
  return (
    <div style={{ ...cardStyle, borderRadius: 14, padding: "12px 14px", display: "grid", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 800, color: colors.text }}>{title}</span>
            <span style={{ background: "#f4f7fb", border: `1px solid ${colors.border}`, color: colors.sub, borderRadius: 999, padding: "1px 8px", fontSize: 11, fontWeight: 800 }}>
              {total}
            </span>
          </div>
          <span style={{ fontSize: 11.5, color: colors.faint, lineHeight: 1.45 }}>{blurb}</span>
        </div>
        {shown < total ? (
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11.5, fontWeight: 700, color: colors.amber }}>
              Showing {shown} of {total}
            </span>
            <button type="button" onClick={onLoadAll} disabled={loadingAll} style={{ ...REVIEW_ACTION, opacity: loadingAll ? 0.6 : 1 }}>
              {loadingAll ? "Loading…" : "Load all"}
            </button>
          </div>
        ) : null}
      </div>
      {total === 0 ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.green, fontSize: 12.5, fontWeight: 700, padding: "6px 2px" }}>
          <CheckCircle2 size={14} /> Nothing to review here.
        </div>
      ) : (
        <div style={{ display: "grid", gap: 4, overflowX: "auto" }}>{children}</div>
      )}
    </div>
  );
}

function NeedsReviewTab() {
  const [health, setHealth] = useState<SourcingDataHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [rowLimit, setRowLimit] = useState(200);
  const [busyKey, setBusyKey] = useState("");
  const nav = useNavigate();
  const toast = useToast();

  const load = useCallback(
    async (limit: number) => {
      setLoading(true);
      setLoadError("");
      try {
        setHealth(await accountSourcingApi.dataHealth(limit));
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : "Could not load the data-health report.");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(rowLimit);
  }, [load, rowLimit]);

  // Drop the rows a successful fix resolved and decrement that section's total,
  // so the queue shrinks as the admin works it without a full refetch.
  const dropRows = useCallback(
    <K extends "sdr_conflicts" | "misattached" | "domain_corrections">(
      section: K,
      match: (row: SourcingDataHealth[K]["rows"][number]) => boolean,
    ) => {
      setHealth((current) => {
        if (!current) return current;
        const kept = current[section].rows.filter((row) => !match(row));
        const removed = current[section].rows.length - kept.length;
        if (!removed) return current;
        return {
          ...current,
          [section]: { total: Math.max(0, current[section].total - removed), rows: kept },
        } as SourcingDataHealth;
      });
    },
    [],
  );

  // Every row action funnels through here so busy state, toasts and the
  // optimistic row removal behave identically across the three sections.
  const runAction = useCallback(
    async (key: string, confirmText: string, action: () => Promise<string>, onDone: () => void) => {
      if (busyKey) return;
      if (!window.confirm(confirmText)) return;
      setBusyKey(key);
      try {
        const message = await action();
        onDone();
        toast.success(message, "Fix applied");
      } catch (error) {
        // 422 alias collisions are expected here — show the server's own text.
        toast.error(error instanceof Error ? error.message : "The fix could not be applied.", "Fix failed");
      } finally {
        setBusyKey("");
      }
    },
    [busyKey, toast],
  );

  const sdrConflicts = health?.sdr_conflicts;
  const misattached = health?.misattached;
  const domainFixes = health?.domain_corrections;
  const loadingAll = loading && rowLimit > 200;

  if (loading && !health) {
    return (
      <div style={{ ...cardStyle, padding: 36, textAlign: "center" }}>
        <Loader2 className="animate-spin" color={colors.primary} />
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={{ ...cardStyle, padding: 26, textAlign: "center", color: colors.red, fontSize: 13, fontWeight: 700 }}>
        <AlertCircle size={16} style={{ marginBottom: 6 }} />
        <div>{loadError}</div>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: colors.faint }}>
          Data-quality report{health?.generated_at ? ` · generated ${ts(health.generated_at)}` : ""}. Fixes apply immediately.
        </span>
        <button type="button" onClick={() => void load(rowLimit)} disabled={loading} style={{ ...REVIEW_ACTION, opacity: loading ? 0.6 : 1, display: "inline-flex", alignItems: "center", gap: 6 }}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Re-run report
        </button>
      </div>

      {/* ── SDR conflicts ───────────────────────────────────────────────── */}
      <ReviewSection
        title="SDR conflicts"
        blurb="The account's SDR and its prospects' SDR disagree. Pick the direction that is correct — one row per account + conflicting prospect SDR."
        total={sdrConflicts?.total ?? 0}
        shown={sdrConflicts?.rows.length ?? 0}
        onLoadAll={() => setRowLimit(2000)}
        loadingAll={loadingAll}
      >
        <div className="as-review-head as-review-sdr" style={{ padding: "2px 12px 4px" }}>
          <span style={REVIEW_HEAD}>Account</span>
          <span style={REVIEW_HEAD}>Account SDR</span>
          <span style={REVIEW_HEAD}>Prospect SDR</span>
          <span style={REVIEW_HEAD}>Prospects</span>
          <span style={{ ...REVIEW_HEAD, textAlign: "right" }}>Resolve</span>
        </div>
        {(sdrConflicts?.rows ?? []).map((row) => {
          const rowKey = `sdr:${row.company_id}:${row.contact_sdr_id}`;
          const accountSdr = row.company_sdr_name || "the account SDR";
          const prospectSdr = row.contact_sdr_name || "the prospect SDR";
          return (
            <div key={rowKey} className="as-review-row as-review-sdr">
              <button
                type="button"
                onClick={() => nav(`/account-sourcing/${row.company_id}`)}
                title="Open account"
                style={{ ...REVIEW_STRONG, border: 0, background: "transparent", textAlign: "left", cursor: "pointer", padding: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              >
                {row.company_name}
              </button>
              <span style={{ ...REVIEW_CELL, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{accountSdr}</span>
              <span style={{ ...REVIEW_CELL, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{prospectSdr}</span>
              <span style={REVIEW_CELL}>{row.prospect_count}</span>
              <div className="as-review-actions" style={{ display: "flex", gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                <button
                  type="button"
                  disabled={Boolean(busyKey)}
                  style={{ ...REVIEW_ACTION_PRIMARY, opacity: busyKey ? 0.5 : 1 }}
                  title={`Change the ACCOUNT so it is owned by ${prospectSdr}`}
                  onClick={() =>
                    void runAction(
                      `${rowKey}:account`,
                      `Set ${row.company_name}'s account SDR to ${prospectSdr}?\n\nThe account moves to the prospects' SDR.`,
                      async () => {
                        await assignmentsApi.assignCompany(row.company_id, row.contact_sdr_id, "sdr");
                        return `${row.company_name} account SDR set to ${prospectSdr}.`;
                      },
                      () => dropRows("sdr_conflicts", (r) => r.company_id === row.company_id && r.contact_sdr_id === row.contact_sdr_id),
                    )
                  }
                >
                  Account → {prospectSdr}
                </button>
                <button
                  type="button"
                  disabled={Boolean(busyKey) || !row.company_sdr_id}
                  style={{ ...REVIEW_ACTION, opacity: busyKey ? 0.5 : 1 }}
                  title={`Move the ${row.prospect_count} conflicting prospect(s) to ${accountSdr}`}
                  onClick={() =>
                    void runAction(
                      `${rowKey}:prospects`,
                      `Move ${row.prospect_count} prospect(s) on ${row.company_name} from ${prospectSdr} to ${accountSdr}?\n\nAn SDR handoff resets their outreach progress.`,
                      async () => {
                        const contacts = await accountSourcingApi.getContacts(row.company_id);
                        const targets = contacts.filter((contact) => contact.sdr_id === row.contact_sdr_id);
                        for (const contact of targets) {
                          await assignmentsApi.assignContact(contact.id, row.company_sdr_id, "sdr");
                        }
                        return `${targets.length} prospect(s) on ${row.company_name} moved to ${accountSdr}.`;
                      },
                      () => dropRows("sdr_conflicts", (r) => r.company_id === row.company_id && r.contact_sdr_id === row.contact_sdr_id),
                    )
                  }
                >
                  {row.prospect_count} prospect{row.prospect_count === 1 ? "" : "s"} → {accountSdr}
                </button>
              </div>
            </div>
          );
        })}
      </ReviewSection>

      {/* ── Misattached prospects ───────────────────────────────────────── */}
      <ReviewSection
        title="Misattached prospects"
        blurb="The prospect's email domain matches neither its account's domain nor that account's dominant contact domain."
        total={misattached?.total ?? 0}
        shown={misattached?.rows.length ?? 0}
        onLoadAll={() => setRowLimit(2000)}
        loadingAll={loadingAll}
      >
        <div className="as-review-head as-review-mis" style={{ padding: "2px 12px 4px" }}>
          <span style={REVIEW_HEAD}>Prospect</span>
          <span style={REVIEW_HEAD}>Currently on</span>
          <span style={REVIEW_HEAD}>Looks like</span>
          <span style={REVIEW_HEAD}>Evidence</span>
          <span style={{ ...REVIEW_HEAD, textAlign: "right" }}>Resolve</span>
        </div>
        {(misattached?.rows ?? []).map((row) => (
          <div key={row.contact_id} className="as-review-row as-review-mis">
            <div style={{ minWidth: 0 }}>
              <div style={{ ...REVIEW_STRONG, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.contact_name || "Unnamed prospect"}</div>
              <div style={{ ...REVIEW_CELL, color: colors.faint, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.contact_email || row.contact_domain || "—"}</div>
            </div>
            <button
              type="button"
              onClick={() => nav(`/account-sourcing/${row.current_company_id}`)}
              title="Open the account this prospect sits on today"
              style={{ ...REVIEW_CELL, border: 0, background: "transparent", textAlign: "left", cursor: "pointer", padding: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {row.current_company_name || "—"}
            </button>
            <span style={{ ...REVIEW_CELL, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {row.suggested_company_name || <span style={{ color: colors.faint }}>No existing account</span>}
            </span>
            <span style={{ ...REVIEW_CELL, fontSize: 11.5, lineHeight: 1.45, whiteSpace: "normal", wordBreak: "break-word" }}>{row.evidence}</span>
            <div className="as-review-actions" style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
              {row.suggested_company_id ? (
                <button
                  type="button"
                  disabled={Boolean(busyKey)}
                  style={{ ...REVIEW_ACTION_PRIMARY, opacity: busyKey ? 0.5 : 1 }}
                  onClick={() =>
                    void runAction(
                      `mis:${row.contact_id}`,
                      `Move ${row.contact_name || "this prospect"} to ${row.suggested_company_name}?`,
                      async () => {
                        await accountSourcingApi.relinkContactCompany(row.contact_id, row.suggested_company_id as string);
                        return `${row.contact_name || "Prospect"} moved to ${row.suggested_company_name}.`;
                      },
                      () => dropRows("misattached", (r) => r.contact_id === row.contact_id),
                    )
                  }
                >
                  Move to {row.suggested_company_name}
                </button>
              ) : (
                <span style={{ fontSize: 11, fontWeight: 700, color: colors.faint, whiteSpace: "nowrap" }}>Handle manually</span>
              )}
            </div>
          </div>
        ))}
      </ReviewSection>

      {/* ── Domain corrections ──────────────────────────────────────────── */}
      <ReviewSection
        title="Domain corrections"
        blurb="The account's dominant corporate contact domain disagrees with the domain on the account record."
        total={domainFixes?.total ?? 0}
        shown={domainFixes?.rows.length ?? 0}
        onLoadAll={() => setRowLimit(2000)}
        loadingAll={loadingAll}
      >
        <div className="as-review-head as-review-dom" style={{ padding: "2px 12px 4px" }}>
          <span style={REVIEW_HEAD}>Account</span>
          <span style={REVIEW_HEAD}>Current domain</span>
          <span style={REVIEW_HEAD}>Suggested</span>
          <span style={REVIEW_HEAD}>Evidence</span>
          <span style={{ ...REVIEW_HEAD, textAlign: "right" }}>Resolve</span>
        </div>
        {(domainFixes?.rows ?? []).map((row) => (
          <div key={row.company_id} className="as-review-row as-review-dom">
            <button
              type="button"
              onClick={() => nav(`/account-sourcing/${row.company_id}`)}
              title="Open account"
              style={{ ...REVIEW_STRONG, border: 0, background: "transparent", textAlign: "left", cursor: "pointer", padding: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {row.company_name}
            </button>
            <span style={{ ...REVIEW_CELL, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.current_domain || "—"}</span>
            <span style={{ ...REVIEW_STRONG, color: "#4d7c0f", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.suggested_domain}</span>
            <span style={REVIEW_CELL}>{row.evidence_count} contact{row.evidence_count === 1 ? "" : "s"}</span>
            <div className="as-review-actions" style={{ display: "flex", gap: 6, justifyContent: "flex-end", flexWrap: "wrap", alignItems: "center" }}>
              {row.suggested_domain_taken ? (
                <>
                  <span title="Another live account already owns this domain — renaming would collide, so merge the two accounts instead." style={{ fontSize: 11, fontWeight: 800, color: colors.amber, background: colors.amberSoft, border: "1px solid #ffe4b0", borderRadius: 999, padding: "2px 8px", whiteSpace: "nowrap" }}>
                    Merge candidate
                  </span>
                  <button type="button" style={REVIEW_ACTION} onClick={() => nav(`/account-sourcing/${row.company_id}`)}>
                    Open to merge
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    disabled={Boolean(busyKey)}
                    style={{ ...REVIEW_ACTION_PRIMARY, opacity: busyKey ? 0.5 : 1 }}
                    onClick={() =>
                      void runAction(
                        `dom:${row.company_id}:primary`,
                        `Set ${row.company_name}'s primary domain to ${row.suggested_domain}?`,
                        async () => {
                          await accountSourcingApi.updateCompany(row.company_id, { domain: row.suggested_domain });
                          return `${row.company_name} now points at ${row.suggested_domain}.`;
                        },
                        () => dropRows("domain_corrections", (r) => r.company_id === row.company_id),
                      )
                    }
                  >
                    Set primary domain
                  </button>
                  <button
                    type="button"
                    disabled={Boolean(busyKey)}
                    style={{ ...REVIEW_ACTION, opacity: busyKey ? 0.5 : 1 }}
                    title="Keep the current primary domain and record the suggestion as an alias"
                    onClick={() =>
                      void runAction(
                        `dom:${row.company_id}:alias`,
                        `Add ${row.suggested_domain} as an alias domain on ${row.company_name}?`,
                        async () => {
                          // Read-modify-write: the API replaces the alias list.
                          const company = await accountSourcingApi.getCompany(row.company_id);
                          const existing = company.additional_domains ?? [];
                          if (existing.includes(row.suggested_domain)) {
                            return `${row.suggested_domain} was already an alias on ${row.company_name}.`;
                          }
                          await accountSourcingApi.updateCompany(row.company_id, {
                            additional_domains: [...existing, row.suggested_domain],
                          });
                          return `${row.suggested_domain} added as an alias on ${row.company_name}.`;
                        },
                        () => dropRows("domain_corrections", (r) => r.company_id === row.company_id),
                      )
                    }
                  >
                    Add as alias
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </ReviewSection>
    </div>
  );
}

export default function AccountSourcing() {
  const pageSize = 40;
  const [searchParams, setSearchParams] = useSearchParams();
  // Filter hydration source. URL params WIN when present (bookmarks/shared
  // links keep working); otherwise fall back to the last-saved filters in
  // localStorage so returning via the left-nav link or a detail "back" button
  // (which land on the BARE path with no query string) restores the view
  // instead of resetting everything. Computed once at mount.
  const initParams = useMemo(() => {
    const FILTER_KEYS = ["q", "pmin", "pmax", "journey", "owner", "own", "sdr", "tier", "disp", "status", "lane", "batch", "sort", "pg", "tab"];
    const hasAny = FILTER_KEYS.some((k) => searchParams.has(k));
    if (hasAny) return searchParams;
    try {
      const saved = localStorage.getItem("crm.accountSourcing.filters");
      if (saved) return new URLSearchParams(saved);
    } catch {
      /* ignore */
    }
    return searchParams;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [summary, setSummary] = useState<AccountSourcingSummary | null>(null);
  const [batches, setBatches] = useState<SourcingBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState(() => initParams.get("q") ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(() => initParams.get("q") ?? "");
  // Advanced filter — single rule "prospects {op} {value}" against the
  // contact count per account. URL-persisted as `pmin` / `pmax` so navigating
  // back into the page restores the filter. The op+value pair is derived
  // from the bounds when the modal opens.
  type ProspectOp = "gt" | "lt" | "eq" | "between";
  const initialPMin = initParams.get("pmin");
  const initialPMax = initParams.get("pmax");
  const [prospectsMin, setProspectsMin] = useState<number | undefined>(initialPMin !== null ? Number(initialPMin) : undefined);
  const [prospectsMax, setProspectsMax] = useState<number | undefined>(initialPMax !== null ? Number(initialPMax) : undefined);
  const [showAdvancedFilter, setShowAdvancedFilter] = useState(false);
  // Presentation-only UI state: the "More ⋯" toolbar dropdown and the
  // secondary filter row. The filter row auto-opens when any of the filters it
  // hosts was restored from the URL/localStorage, so active filters are never
  // hidden behind a collapsed toggle.
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  const [showMoreFilters, setShowMoreFilters] = useState<boolean>(() => Boolean(
    parseSearchParamList(initParams.get("tier")).length ||
    parseSearchParamList(initParams.get("own")).length ||
    parseSearchParamList(initParams.get("disp")).length ||
    parseSearchParamList(initParams.get("lane")).length ||
    parseSearchParamList(initParams.get("journey")).length ||
    initParams.get("pmin") !== null ||
    initParams.get("pmax") !== null,
  ));
  const [advOp, setAdvOp] = useState<ProspectOp>("gt");
  const [advValue, setAdvValue] = useState("");
  const [advValue2, setAdvValue2] = useState("");
  const [downloadingFiltered, setDownloadingFiltered] = useState(false);
  // Recotap journey-stage filter + the funnel counts that power the ABM band.
  const [journeyFilter, setJourneyFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("journey")));
  const [journeySummary, setJourneySummary] = useState<RecotapSummary | null>(null);
  const [syncingRecotap, setSyncingRecotap] = useState(false);
  const hasAdvancedFilter = prospectsMin !== undefined || prospectsMax !== undefined;
  const [ownerScope, setOwnerScope] = useState<"all" | "mine">(() => (initParams.get("owner") === "mine" ? "mine" : "all"));
  // Multi-select AE Assigned filter: matches assigned_to_id only.
  // Different from ownerScope (binary mine vs all).
  const [ownerFilter, setOwnerFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("own")));
  // Multi-select SDR Assigned filter: matches sdr_id only.
  const [sdrFilter, setSdrFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("sdr")));
  const [teamUsers, setTeamUsers] = useState<User[]>([]);
  const [tierFilter, setTierFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("tier")));
  const [dispositionFilter, setDispositionFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("disp")));
  const [statusFilter, setStatusFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("status")));
  const [laneFilter, setLaneFilter] = useState<string[]>(() => parseSearchParamList(initParams.get("lane")));
  // Import drill-in: "View accounts" on an Imports row pins batch_id here. It
  // has no dedicated control, so it surfaces as a removable chip.
  const [batchFilter, setBatchFilter] = useState<string>(() => initParams.get("batch") ?? "");
  const [sortBy, setSortBy] = useState<AccountSortKey>(() => parseAccountSort(initParams.get("sort")));
  const [page, setPage] = useState(() => parseInt(initParams.get("pg") ?? "1", 10) || 1);
  const [companyTotal, setCompanyTotal] = useState(0);
  const [companyPages, setCompanyPages] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [exportingContacts, setExportingContacts] = useState(false);
  const [exportingSelected, setExportingSelected] = useState(false);
  const [selectionExportError, setSelectionExportError] = useState<string | null>(null);
  const [resettingScope, setResettingScope] = useState<"" | "account-sourcing" | "workspace">("");
  const [activeTab, setActiveTab] = useState<AccountSourcingTab>(() => parseAccountTab(initParams.get("tab")));
  const [expandedBatchIds, setExpandedBatchIds] = useState<Set<string>>(() => new Set());
  const [dismissedBatchIds, setDismissedBatchIds] = useState<string[]>(() => {
    try {
      const raw = window.localStorage.getItem("account-sourcing-dismissed-batches");
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  });
  const [successModal, setSuccessModal] = useState<{ title: string; message: string } | null>(null);
  const [pendingBatchApproval, setPendingBatchApproval] = useState<SourcingBatch | null>(null);
  const [confirmingBatchId, setConfirmingBatchId] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState({ companiesText: "" });
  const [creatingCompany, setCreatingCompany] = useState(false);
  const [createError, setCreateError] = useState("");
  const [bulkEnriching, setBulkEnriching] = useState(false);
  const [bulkEnrichResult, setBulkEnrichResult] = useState<string | null>(null);
  const [bulkIcpRunning, setBulkIcpRunning] = useState(false);
  const [bulkIcpResult, setBulkIcpResult] = useState<string | null>(null);
  // Admin row-level multi-select → bulk-assign AE/SDR for the selected accounts.
  // Mirrors the prospect multi-select pattern in Contacts.tsx.
  const [selectedCompanyIds, setSelectedCompanyIds] = useState<Set<string>>(() => new Set());
  const [bulkAssigningAe, setBulkAssigningAe] = useState(false);
  const [bulkAssigningSdr, setBulkAssigningSdr] = useState(false);
  // Filter-wide assignment ("Assign all N matching") — mirrors the Prospecting
  // flow. expected_total makes the server 409 rather than silently assigning a
  // set that changed size under the admin's feet.
  const [showAssignAllModal, setShowAssignAllModal] = useState(false);
  const [assignAllRole, setAssignAllRole] = useState<"ae" | "sdr">("sdr");
  const [assignAllUserId, setAssignAllUserId] = useState("");
  const [assignAllBusy, setAssignAllBusy] = useState(false);
  const [assignAllConflict, setAssignAllConflict] = useState<string | null>(null);
  const { isAdmin, user } = useAuth();
  const toast = useToast();

  useEffect(() => {
    if (searchParams.get("new") !== "company") return;
    const requestedName = (searchParams.get("name") || "").trim();
    setCreateForm({ companiesText: requestedName });
    setCreateError("");
    setShowCreateModal(true);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("new");
      next.delete("name");
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    try {
      window.localStorage.setItem("account-sourcing-dismissed-batches", JSON.stringify(dismissedBatchIds));
    } catch {
      // ignore local storage issues
    }
  }, [dismissedBatchIds]);

  // Monotonic request id for load(): only the latest in-flight request is
  // allowed to write state (a filter change can fire overlapping requests).
  const loadSeqRef = useRef(0);

  /**
   * THE page's active filter state, serialized once. The accounts list, the
   * KPI summary, the CSV export and the filter-wide bulk assign all consume
   * this exact object, so those four populations agree by construction —
   * "Download filtered" used to send only the prospect-count bounds and
   * exported the whole workspace instead.
   *
   * ownerScope === "mine" uses owner_id (matches AE *or* SDR). The AE/SDR
   * dropdowns use dedicated ae_id / sdr_id so each filters its own role only.
   */
  const activeFilters = useMemo<AccountSourcingFilters>(() => ({
    q: debouncedSearch || undefined,
    ownerId: ownerScope === "mine" ? user?.id : undefined,
    aeId: ownerScope !== "mine" && ownerFilter.length ? ownerFilter : undefined,
    sdrId: sdrFilter.length ? sdrFilter : undefined,
    icpTier: tierFilter.length ? tierFilter : undefined,
    disposition: dispositionFilter.length ? dispositionFilter : undefined,
    accountStatus: statusFilter.length ? statusFilter : undefined,
    recommendedOutreachLane: laneFilter.length ? laneFilter : undefined,
    journeyStage: journeyFilter.length ? journeyFilter : undefined,
    batchId: batchFilter || undefined,
    prospectsMin,
    prospectsMax,
  }), [
    debouncedSearch, ownerScope, user?.id, ownerFilter, sdrFilter, tierFilter,
    dispositionFilter, statusFilter, laneFilter, journeyFilter, batchFilter,
    prospectsMin, prospectsMax,
  ]);

  // Server-side sort params for the current option (empty for the two
  // client-side-only options — see SERVER_SORT).
  const sortParams = SERVER_SORT[sortBy];

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    const seq = ++loadSeqRef.current;
    // Silent reloads (the in-flight batch poll) keep the table mounted
    // instead of swapping it for a spinner on every tick.
    if (!opts?.silent) setLoading(true);
    try {
      const [companyPage, companySummary, b, rtpSummary] = await Promise.all([
        accountSourcingApi.listCompaniesPaginated({
          ...activeFilters,
          ...sortParams,
          skip: (page - 1) * pageSize,
          limit: pageSize,
        }),
        // Same filters as the list → the KPI strip describes what is on screen.
        accountSourcingApi.summary(activeFilters),
        accountSourcingApi.listBatches(),
        accountSourcingApi.recotapSummary().catch(() => null),
      ]);
      // A newer request superseded this one — drop the stale response.
      if (seq !== loadSeqRef.current) return;
      setCompanies(companyPage.items);
      setCompanyTotal(companyPage.total);
      setCompanyPages(companyPage.pages);
      setSummary(companySummary);
      setBatches(b);
      setJourneySummary(rtpSummary);
    } finally {
      // Only the latest request controls the spinner; this also recovers if a
      // superseded non-silent request left loading=true behind.
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }, [activeFilters, page, sortParams]);

  // Pull live Recotap signals + (re)seed mock data, then reload so the funnel
  // and rows reflect the new data.
  const handleSyncRecotap = useCallback(async () => {
    setSyncingRecotap(true);
    try {
      await accountSourcingApi.recotapRefresh();
      await load();
    } catch {
      // surfaced via unchanged counts; no destructive failure
    } finally {
      setSyncingRecotap(false);
    }
  }, [load]);

  // Load team users once for the Owner multi-select.
  useEffect(() => {
    getCachedUsers()
      .then((users) => setTeamUsers(users.filter((u) => u.is_active)))
      .catch(() => setTeamUsers([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const latestVisibleBatch = useMemo(
    () =>
      batches.find(
        (batch) =>
          !dismissedBatchIds.includes(batch.id) &&
          ["awaiting_confirmation", "pending", "processing", "completed"].includes(batch.status)
      ) ?? null,
    [batches, dismissedBatchIds]
  );

  // Sync filter state to URL so navigating away and back restores the view
  useEffect(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      search.trim() ? next.set("q", search.trim()) : next.delete("q");
      ownerScope === "mine" ? next.set("owner", "mine") : next.delete("owner");
      ownerFilter.length ? next.set("own", ownerFilter.join(",")) : next.delete("own");
      sdrFilter.length ? next.set("sdr", sdrFilter.join(",")) : next.delete("sdr");
      tierFilter.length ? next.set("tier", tierFilter.join(",")) : next.delete("tier");
      dispositionFilter.length ? next.set("disp", dispositionFilter.join(",")) : next.delete("disp");
      statusFilter.length ? next.set("status", statusFilter.join(",")) : next.delete("status");
      laneFilter.length ? next.set("lane", laneFilter.join(",")) : next.delete("lane");
      journeyFilter.length ? next.set("journey", journeyFilter.join(",")) : next.delete("journey");
      batchFilter ? next.set("batch", batchFilter) : next.delete("batch");
      sortBy !== "recent" ? next.set("sort", sortBy) : next.delete("sort");
      page > 1 ? next.set("pg", String(page)) : next.delete("pg");
      prospectsMin !== undefined ? next.set("pmin", String(prospectsMin)) : next.delete("pmin");
      prospectsMax !== undefined ? next.set("pmax", String(prospectsMax)) : next.delete("pmax");
      activeTab !== "accounts" ? next.set("tab", activeTab) : next.delete("tab");
      // Persist the same params to localStorage so a bare-path return (left-nav
      // / detail back button) can rehydrate them via initParams. URL still wins.
      try {
        localStorage.setItem("crm.accountSourcing.filters", next.toString());
      } catch {
        /* ignore */
      }
      return next;
    }, { replace: true });
  }, [activeTab, batchFilter, laneFilter, dispositionFilter, statusFilter, journeyFilter, ownerFilter, sdrFilter, ownerScope, page, search, setSearchParams, sortBy, tierFilter, prospectsMin, prospectsMax]);

  // "Needs review" is admin-only; a stale tab=review (URL or the saved filter
  // string) must never leave a rep staring at an empty 403 tab.
  useEffect(() => {
    if (!isAdmin && activeTab === "review") setActiveTab("accounts");
  }, [isAdmin, activeTab]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  // Any change to the population OR the (now server-side) sort restarts at
  // page 1 — page 4 of the old ordering is meaningless under a new one.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, dispositionFilter, statusFilter, journeyFilter, laneFilter, ownerFilter, sdrFilter, ownerScope, tierFilter, batchFilter, sortBy, prospectsMin, prospectsMax]);

  const runReset = useCallback(async (scope: "account-sourcing" | "workspace") => {
    if (scope === "workspace") {
      const confirmation = window.prompt('Type RESET to clear workspace data (companies, contacts, deals, meetings, demos, sourcing).');
      if (confirmation !== "RESET") return;
    } else {
      const ok = window.confirm("Clear all Account Sourcing imports, sourced companies, related contacts, and batches?");
      if (!ok) return;
    }

    setResettingScope(scope);
    try {
      const result = await accountSourcingApi.resetData(scope);
      await load();
      window.alert(`${scope === "workspace" ? "Workspace" : "Account Sourcing"} cleared.\n${Object.entries(result.summary).map(([key, value]) => `${key}: ${value}`).join("\n")}`);
    } finally {
      setResettingScope("");
    }
  }, [load]);

  // Every sort except Priority is applied by the server across the WHOLE result
  // set, so the rows arrive in order and re-sorting them here would only be
  // able to reorder the current 40. Priority is computed in the browser from
  // JSONB the server does not sort on, so it stays page-scoped (and the option
  // label says so).
  const sortedCompanies = useMemo(() => {
    if (sortBy !== "priority_desc") return companies;
    return companies
      .map((company, index) => ({ company, index }))
      .sort(
        (a, b) =>
          getAccountPrioritySnapshot(b.company).priorityScore -
            getAccountPrioritySnapshot(a.company).priorityScore || a.index - b.index,
      )
      .map((item) => item.company);
  }, [companies, sortBy]);

  // Row-level multi-select (admin bulk-assign). Only admins get the controls,
  // mirroring the prospect bulk-assign gating in Contacts.tsx.
  const canSelectAccounts = isAdmin;
  const allVisibleSelected =
    sortedCompanies.length > 0 && sortedCompanies.every((c) => selectedCompanyIds.has(c.id));

  const toggleCompanySelection = useCallback((companyId: string) => {
    setSelectionExportError(null);
    setSelectedCompanyIds((current) => {
      const next = new Set(current);
      if (next.has(companyId)) next.delete(companyId);
      else next.add(companyId);
      return next;
    });
  }, []);

  const toggleVisibleCompanySelection = useCallback(() => {
    setSelectionExportError(null);
    setSelectedCompanyIds((current) => {
      if (sortedCompanies.length > 0 && sortedCompanies.every((c) => current.has(c.id))) {
        // All visible already selected → clear just the visible ones.
        const next = new Set(current);
        for (const c of sortedCompanies) next.delete(c.id);
        return next;
      }
      const next = new Set(current);
      for (const c of sortedCompanies) next.add(c.id);
      return next;
    });
  }, [sortedCompanies]);

  const clearCompanySelection = useCallback(() => {
    setSelectionExportError(null);
    setSelectedCompanyIds(new Set());
  }, []);

  // Admin: bulk-assign the selected accounts' AE or SDR. After success, clear
  // the selection and reload so the rows reflect the new owners.
  const bulkAssignSelectedCompanies = useCallback(
    async (role: "ae" | "sdr", userId: string) => {
      if (!userId || selectedCompanyIds.size === 0) return;
      const setBusy = role === "sdr" ? setBulkAssigningSdr : setBulkAssigningAe;
      setBusy(true);
      try {
        const result = await assignmentsApi.bulkAssignCompanies(Array.from(selectedCompanyIds), userId, role);
        const who = teamUsers.find((u) => u.id === userId)?.name || role.toUpperCase();
        toast.success(
          `${result.updated} account${result.updated === 1 ? "" : "s"} assigned to ${who}${result.skipped ? `, ${result.skipped} skipped` : ""}.`,
          `${role.toUpperCase()} reassigned`,
        );
        clearCompanySelection();
        await load();
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : "Failed to assign selected accounts.",
          "Assign failed",
        );
      } finally {
        setBusy(false);
      }
    },
    [selectedCompanyIds, teamUsers, toast, clearCompanySelection, load],
  );

  // Assign EVERY account matching the current filters (not just the loaded
  // page). expected_total is the number the admin was shown, so a set that
  // changed size comes back as a 409 instead of quietly assigning something
  // else — we then re-count and ask again with the new number.
  const runAssignAllMatching = useCallback(async () => {
    if (!assignAllUserId || assignAllBusy) return;
    setAssignAllBusy(true);
    try {
      const result = await accountSourcingApi.assignCompaniesByFilter(activeFilters, {
        userId: assignAllUserId,
        role: assignAllRole,
        expectedTotal: companyTotal,
      });
      const who = teamUsers.find((u) => u.id === assignAllUserId)?.name || assignAllRole.toUpperCase();
      toast.success(
        `${result.updated} account${result.updated === 1 ? "" : "s"} assigned to ${who}` +
          (result.skipped ? `, ${result.skipped} skipped (permissions)` : "") +
          (result.prospects_kept_divergent
            ? `; ${result.prospects_kept_divergent} prospect(s) left with their own rep`
            : "") +
          ".",
        `${assignAllRole.toUpperCase()} assigned to all matching`,
      );
      setShowAssignAllModal(false);
      setAssignAllConflict(null);
      clearCompanySelection();
      await load();
    } catch (error) {
      if (error instanceof AccountSourcingHttpError && error.status === 409) {
        // The matching set moved under us — refresh the count and re-ask.
        const fresh = await accountSourcingApi.countCompanies(activeFilters).catch(() => null);
        if (fresh !== null) setCompanyTotal(fresh);
        setAssignAllConflict(
          `${error.message} The count below has been refreshed${fresh !== null ? ` to ${fresh}` : ""} — confirm again to apply it.`,
        );
        return;
      }
      toast.error(error instanceof Error ? error.message : "Assignment failed.", "Assign failed");
    } finally {
      setAssignAllBusy(false);
    }
  }, [assignAllUserId, assignAllBusy, assignAllRole, activeFilters, companyTotal, teamUsers, toast, clearCompanySelection, load]);

  const hasFilters = !!(search || ownerScope === "mine" || ownerFilter.length || sdrFilter.length || tierFilter.length || dispositionFilter.length || statusFilter.length || laneFilter.length || journeyFilter.length || batchFilter || hasAdvancedFilter);
  // Count of active filters living behind the "More filters" toggle, so the
  // collapsed toggle still signals that hidden filters are narrowing the list.
  const moreFilterCount =
    tierFilter.length + ownerFilter.length + dispositionFilter.length + laneFilter.length + journeyFilter.length + (hasAdvancedFilter ? 1 : 0);
  const totalCompanies = summary?.total_companies ?? 0;
  const hotCount = summary?.hot_count ?? 0;
  const warmCount = summary?.warm_count ?? 0;
  const highPriorityCount = summary?.high_priority_count ?? 0;
  const engagedCount = summary?.engaged_count ?? 0;
  const unresolvedCount = summary?.unresolved_count ?? 0;
  const unenrichedCount = summary?.unenriched_count ?? 0;
  const researchedCount = summary?.researched_count ?? 0;
  const targetVerdictCount = summary?.target_verdict_count ?? 0;
  const watchVerdictCount = summary?.watch_verdict_count ?? 0;
  const enrichedCount = summary?.enriched_count ?? 0;
  const totalContacts = summary?.total_contacts ?? 0;
  const showingStart = companyTotal === 0 ? 0 : (page - 1) * pageSize + 1;
  const showingEnd = companyTotal === 0 ? 0 : Math.min(page * pageSize, companyTotal);
  const latestVerdictSummary = (latestVisibleBatch?.verdict_summary || {}) as Record<string, unknown>;
  const etaText =
    latestVisibleBatch?.eta_seconds && latestVisibleBatch.eta_seconds > 0
      ? `${Math.ceil(latestVisibleBatch.eta_seconds / 60)} min remaining`
      : latestVisibleBatch?.status === "completed"
        ? "Finished"
        : "Estimating...";
  const batchInFlight = Boolean(latestVisibleBatch && ["pending", "processing"].includes(latestVisibleBatch.status));
  const latestProgressMessage = latestVisibleBatch
    ? latestVisibleBatch.progress_message ||
      (latestVisibleBatch.total_rows > 0
        ? `Processed ${latestVisibleBatch.processed_rows} of ${latestVisibleBatch.total_rows} accounts`
        : "Research in progress")
    : "";
  const progressPercent = latestVisibleBatch
    ? latestVisibleBatch.status === "completed"
      ? 100
      : latestVisibleBatch.total_rows
        ? Math.min(100, Math.round((latestVisibleBatch.processed_rows / latestVisibleBatch.total_rows) * 100))
        : 0
    : 0;

  useEffect(() => {
    if (!batchInFlight) return;
    const id = window.setInterval(() => {
      // Skip hidden-tab ticks; the interval keeps running and resumes on focus.
      if (document.visibilityState === "hidden") return;
      // Silent: keep the table on screen while the batch poll refreshes data.
      void load({ silent: true });
    }, 8000);
    return () => window.clearInterval(id);
  }, [batchInFlight, load]);

  const downloadTemplate = useCallback(() => {
    const template = [
      ["Company Name", "Domain", "Industry", "AE", "SDR", "Classification", "Contact", "Title", "Email", "LinkedIn URL"],
      ["BlackLine", "blackline.com", "Finance automation", "rakesh@beacon.li", "mahesh@beacon.li", "target", "Jane Smith", "Director of Professional Services", "jane@blackline.com", "https://linkedin.com/in/janesmith"],
    ]
      .map((row) => row.join(","))
      .join("\n");
    const blob = new Blob([template], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "beacon-account-sourcing-template.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }, []);

  const handleBatchUploaded = useCallback((batch: SourcingBatch) => {
    setSuccessModal({
      title: "Upload received",
      message:
        batch.requires_confirmation
          ? "The file was uploaded. Beacon found TAL verdicts that need review before enrichment starts."
          : "The file was uploaded successfully and enrichment has started.",
    });
    if (batch.requires_confirmation) {
      setPendingBatchApproval(batch);
    }
    setDismissedBatchIds((current) => current.filter((id) => id !== batch.id));
    void load();
  }, [load]);

  // Prune selected IDs that are no longer in the loaded list (after paging or
  // filtering) so a bulk-assign never touches an account the admin can't see.
  useEffect(() => {
    setSelectedCompanyIds((current) => {
      if (current.size === 0) return current;
      const visible = new Set(companies.map((c) => c.id));
      let changed = false;
      const next = new Set<string>();
      for (const id of current) {
        if (visible.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : current;
    });
  }, [companies]);

  const handleCreateCompany = useCallback(async () => {
    const entries = parseManualCompanyLines(createForm.companiesText);
    if (!entries.length) {
      setCreateError("Add at least one company name.");
      return;
    }
    setCreatingCompany(true);
    setCreateError("");
    try {
      const createdBatches: SourcingBatch[] = [];
      for (const entry of entries) {
        const batch = await accountSourcingApi.createManualCompany({
          name: entry.name,
          domain: entry.domain,
        });
        createdBatches.push(batch);
      }
      setShowCreateModal(false);
      setCreateForm({ companiesText: "" });
      setSuccessModal({
        title: entries.length === 1 ? "Account added" : "Accounts added",
        message:
          entries.length === 1
            ? "The account was created and enrichment has started."
            : `${entries.length} accounts were created and enrichment has started for each of them.`,
      });
      setDismissedBatchIds((current) =>
        current.filter((id) => !createdBatches.some((batch) => batch.id === id))
      );
      setActiveTab("imports");
      await load();
    } catch (error: unknown) {
      setCreateError(error instanceof Error ? error.message : "Failed to create company");
    } finally {
      setCreatingCompany(false);
    }
  }, [createForm.companiesText, load]);

  // Import-status card, rendered in one of two slots: above the table while the
  // import actively needs attention (running / awaiting review), below the
  // table once completed.
  const batchNeedsAttention = Boolean(
    latestVisibleBatch && ["pending", "processing", "awaiting_confirmation"].includes(latestVisibleBatch.status),
  );
  const importStatusCard = latestVisibleBatch ? (
    <div
      style={{
        ...cardStyle,
        borderRadius: 14,
        padding: "14px 16px",
        display: "grid",
        gap: 12,
        background:
          latestVisibleBatch.status === "completed"
            ? "#f0faf4"
            : latestVisibleBatch.status === "awaiting_confirmation"
              ? "#fff8ef"
              : "#fbfdff",
        border:
          latestVisibleBatch.status === "completed"
            ? "1px solid #c8e8d8"
            : latestVisibleBatch.status === "awaiting_confirmation"
              ? "1px solid #ffd8a8"
              : `1px solid ${colors.border}`,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {latestVisibleBatch.status === "completed" ? (
              <CheckCircle2 size={16} color={colors.green} />
            ) : latestVisibleBatch.status === "awaiting_confirmation" ? (
              <AlertCircle size={16} color={colors.amber} />
            ) : (
              <Loader2 size={16} className="animate-spin" color={colors.primary} />
            )}
            <span style={{ color: colors.text, fontWeight: 800, fontSize: 14 }}>{latestVisibleBatch.filename}</span>
          </div>
          <div style={{ color: colors.sub, fontSize: 12.5 }}>
            {latestProgressMessage || "Tracking research progress"}
            {latestVisibleBatch.created_by_name ? ` • Uploaded by ${latestVisibleBatch.created_by_name}` : ""}
            {` • ${ts(latestVisibleBatch.created_at)}`}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {latestVisibleBatch.status === "awaiting_confirmation" ? (
            <>
              <button
                type="button"
                onClick={() => setPendingBatchApproval(latestVisibleBatch)}
                style={{
                  border: "1px solid #ffd29a",
                  background: "#fff2db",
                  color: colors.amber,
                  borderRadius: 10,
                  padding: "8px 12px",
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                Review TAL verdicts
              </button>
              <button
                type="button"
                onClick={() => setPendingBatchApproval(latestVisibleBatch)}
                style={{
                  border: 0,
                  background: colors.primary,
                  color: "#fff",
                  borderRadius: 10,
                  padding: "8px 12px",
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                Continue enrichment
              </button>
            </>
          ) : null}
          {latestVisibleBatch.status === "completed" ? (
            <button
              type="button"
              onClick={() => setDismissedBatchIds((current) => [...current, latestVisibleBatch.id])}
              style={{
                border: `1px solid ${colors.border}`,
                background: "#fff",
                color: colors.text,
                borderRadius: 10,
                padding: "8px 12px",
                fontSize: 12,
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              Close card
            </button>
          ) : (
            <button
              onClick={() => void load()}
              disabled={loading}
              style={{
                border: `1px solid ${colors.border}`,
                background: colors.card,
                color: colors.text,
                borderRadius: 10,
                padding: "8px 12px",
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                fontWeight: 700,
                fontSize: 12,
                cursor: loading ? "not-allowed" : "pointer",
                opacity: loading ? 0.7 : 1,
              }}
            >
              {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Refresh progress
            </button>
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
        <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "10px 12px" }}>
          <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>PROGRESS</div>
          <div style={{ marginTop: 4, color: colors.text, fontWeight: 800, fontSize: 18 }}>
            {latestVisibleBatch.processed_rows}/{latestVisibleBatch.total_rows}
          </div>
          <div style={{ marginTop: 3, color: colors.sub, fontSize: 12 }}>Accounts processed</div>
        </div>
        <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "10px 12px" }}>
          <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>CONTACTS FOUND</div>
          <div style={{ marginTop: 4, color: colors.text, fontWeight: 800, fontSize: 18 }}>
            {latestVisibleBatch.contacts_found ?? 0}
          </div>
          <div style={{ marginTop: 3, color: colors.sub, fontSize: 12 }}>Relevant stakeholders saved</div>
        </div>
        <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "10px 12px" }}>
          <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>CURRENT STEP</div>
          <div style={{ marginTop: 4, color: colors.text, fontWeight: 800, fontSize: 15 }}>
            {formatBatchStage(latestVisibleBatch.current_stage, latestVisibleBatch.status)}
          </div>
          <div style={{ marginTop: 3, color: colors.sub, fontSize: 12 }}>{etaText}</div>
        </div>
        <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "10px 12px" }}>
          <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>TAL VERDICTS</div>
          <div style={{ marginTop: 4, color: colors.text, fontWeight: 800, fontSize: 15 }}>
            {String(latestVerdictSummary.target || 0)} target / {String(latestVerdictSummary.watch || 0)} watch
          </div>
          <div style={{ marginTop: 3, color: colors.sub, fontSize: 12 }}>
            {latestVerdictSummary.message ? String(latestVerdictSummary.message) : "No uploaded verdicts"}
          </div>
        </div>
      </div>
      <div style={{ height: 8, borderRadius: 999, background: "#e5e7eb", overflow: "hidden" }}>
        <div
          style={{
            width: `${progressPercent}%`,
            height: "100%",
            background: latestVisibleBatch.status === "completed" ? colors.green : colors.primary,
          }}
        />
      </div>
    </div>
  ) : null;

  return (
    <div className="account-sourcing-page" style={pageStyle}>
      <style>{`
        @media (max-width: 768px) {
          .as-toolbar { align-items: stretch !important; }
          .as-toolbar-actions { width: 100% !important; }
          .as-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; gap: 6px !important; }
          .as-filter-bar { position: static !important; top: auto !important; padding: 10px !important; }
          .as-filter-row { flex-direction: column !important; align-items: stretch !important; gap: 8px !important; }
          .as-filter-row > * { width: 100% !important; }
          .as-company-card { flex-wrap: wrap !important; padding: 12px !important; gap: 10px !important; }
          .as-company-card-assign { display: none !important; }
        }
      `}</style>
      <div style={containerStyle}>
        {/* Compact toolbar — the topbar already renders the page title, so this
            row is just the tab switcher plus the primary actions. Secondary and
            destructive actions live in the "More ⋯" menu. */}
        <div className="as-toolbar" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 4, background: "#ffffff", border: "1px solid #e3e9f2", borderRadius: 10, padding: 3 }}>
            {[
              { id: "accounts", label: "Accounts" },
              { id: "imports", label: `Recent Imports${batches.length ? ` (${batches.length})` : ""}` },
              // Admin-only: the data-health review queue (403 for everyone else).
              ...(isAdmin ? [{ id: "review", label: "Needs Review" }] : []),
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id as AccountSourcingTab)}
                style={{
                  border: 0,
                  background: activeTab === tab.id ? "#f3fbe3" : "transparent",
                  color: activeTab === tab.id ? "#4d7c0f" : colors.sub,
                  borderRadius: 8,
                  padding: "6px 14px",
                  minHeight: 30,
                  fontSize: 13,
                  fontWeight: 800,
                  cursor: "pointer",
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="as-toolbar-actions" style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {isAdmin && (
              <button type="button" className="crm-button primary" onClick={() => setShowCreateModal(true)}>
                <Plus size={14} />
                Add Accounts
              </button>
            )}
            {isAdmin && (
              <button
                type="button"
                className="crm-button soft"
                onClick={async () => {
                  if (!window.confirm("Run ICP research for all sourced accounts? Uses web search + Claude AI — no Apollo or Hunter credits.\n\nThis may take 15-30s per company.")) return;
                  setBulkIcpRunning(true);
                  setBulkIcpResult(null);
                  try {
                    const result = await accountSourcingApi.bulkIcpResearch(false);
                    setBulkIcpResult(`Queued ${result.queued} of ${result.total} accounts for ICP research`);
                  } catch (e) {
                    setBulkIcpResult(e instanceof Error ? e.message : "Failed to queue ICP research");
                  } finally {
                    setBulkIcpRunning(false);
                  }
                }}
                disabled={bulkIcpRunning}
                style={{ opacity: bulkIcpRunning ? 0.7 : 1 }}
              >
                {bulkIcpRunning ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
                Run ICP Research
              </button>
            )}
            {isAdmin && (
              <button
                type="button"
                className="crm-button soft"
                onClick={async () => {
                  if (!window.confirm("Queue enrichment for all sourced accounts? This may take a while depending on how many companies you have.")) return;
                  setBulkEnriching(true);
                  setBulkEnrichResult(null);
                  try {
                    const result = await accountSourcingApi.bulkEnrichAll(false);
                    setBulkEnrichResult(`Queued ${result.queued} of ${result.total} accounts for enrichment`);
                  } catch (e) {
                    setBulkEnrichResult(e instanceof Error ? e.message : "Failed to queue enrichment");
                  } finally {
                    setBulkEnriching(false);
                  }
                }}
                disabled={bulkEnriching}
                style={{ opacity: bulkEnriching ? 0.7 : 1 }}
              >
                {bulkEnriching ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                Enrich All
              </button>
            )}
            <button
              type="button"
              className="crm-button soft"
              onClick={() => void load()}
              title="Refresh"
              aria-label="Refresh accounts"
              style={{ padding: "0 10px" }}
            >
              <RefreshCw size={15} />
            </button>
            <div style={{ position: "relative" }}>
              <button
                type="button"
                className="crm-button soft"
                onClick={() => setMoreMenuOpen((open) => !open)}
                aria-haspopup="menu"
                aria-expanded={moreMenuOpen}
                title="More actions"
              >
                More
                <MoreHorizontal size={15} />
              </button>
              {moreMenuOpen && (
                <>
                  <div onClick={() => setMoreMenuOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 60 }} />
                  <div
                    role="menu"
                    className="as-more-menu"
                    style={{
                      position: "absolute",
                      right: 0,
                      top: "calc(100% + 6px)",
                      zIndex: 61,
                      minWidth: 228,
                      background: "#ffffff",
                      border: "1px solid #e3e9f2",
                      borderRadius: 10,
                      boxShadow: "0 18px 44px rgba(15,23,42,0.16)",
                      padding: 6,
                      display: "grid",
                      gap: 2,
                    }}
                  >
                    <button
                      type="button"
                      role="menuitem"
                      style={MENU_ITEM_STYLE}
                      title={
                        batchFilter
                          ? "Every sourced contact from the pinned import. The account filters do not apply to the contacts export."
                          : "Every sourced contact. The account filters do not apply to the contacts export."
                      }
                      onClick={async () => {
                        setMoreMenuOpen(false);
                        setExportingContacts(true);
                        try {
                          // The contacts export takes its own (much smaller)
                          // param set server-side; batch_id is the one page
                          // filter it shares.
                          const blob = await accountSourcingApi.exportContactsCsv({ batchId: batchFilter || undefined });
                          downloadBlob(blob, `sourced-contacts-${new Date().toISOString().slice(0, 10)}.csv`);
                        } catch (e) {
                          toast.error(e instanceof Error ? e.message : "Export failed", "Export failed");
                        } finally {
                          setExportingContacts(false);
                        }
                      }}
                    >
                      {exportingContacts ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                      Export Contacts
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      style={MENU_ITEM_STYLE}
                      onClick={async () => {
                        setMoreMenuOpen(false);
                        setExporting(true);
                        try {
                          // Same filters + sort as the table on screen.
                          const blob = await accountSourcingApi.exportCsv(activeFilters, sortParams);
                          downloadBlob(blob, `sourced-companies-${new Date().toISOString().slice(0, 10)}.csv`);
                        } catch (e) {
                          toast.error(e instanceof Error ? e.message : "Export failed", "Export failed");
                        } finally {
                          setExporting(false);
                        }
                      }}
                    >
                      {exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                      {hasFilters ? "Export CSV (filtered)" : "Export CSV"}
                    </button>
                    {isAdmin && (
                      <>
                        <div style={{ height: 1, background: "#e3e9f2", margin: "4px 6px" }} />
                        <button
                          type="button"
                          role="menuitem"
                          className="as-menu-danger"
                          disabled={Boolean(resettingScope)}
                          style={{ ...MENU_ITEM_STYLE, color: colors.red, opacity: resettingScope ? 0.7 : 1, cursor: resettingScope ? "not-allowed" : "pointer" }}
                          onClick={() => {
                            setMoreMenuOpen(false);
                            void runReset("account-sourcing");
                          }}
                        >
                          {resettingScope === "account-sourcing" ? <Loader2 size={14} className="animate-spin" /> : <AlertCircle size={14} />}
                          Clear Account Sourcing
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className="as-menu-danger"
                          disabled={Boolean(resettingScope)}
                          style={{ ...MENU_ITEM_STYLE, color: colors.red, opacity: resettingScope ? 0.7 : 1, cursor: resettingScope ? "not-allowed" : "pointer" }}
                          onClick={() => {
                            setMoreMenuOpen(false);
                            void runReset("workspace");
                          }}
                        >
                          {resettingScope === "workspace" ? <Loader2 size={14} className="animate-spin" /> : <AlertCircle size={14} />}
                          Clear Workspace
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {bulkIcpResult && (
          <div style={{ borderRadius: 12, border: "1px solid #c3dfc0", background: "#edfaeb", padding: "10px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
            <span style={{ fontSize: 13, color: "#1a6b2a", fontWeight: 600 }}>{bulkIcpResult}</span>
            <button onClick={() => setBulkIcpResult(null)} style={{ background: "none", border: "none", cursor: "pointer", color: "#4a8c5a" }}><X size={14} /></button>
          </div>
        )}
        {bulkEnrichResult && (
          <div style={{ borderRadius: 12, border: "1px solid #cfe89a", background: "#f3fbe3", padding: "10px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
            <span style={{ fontSize: 13, color: "#4d7c0f", fontWeight: 600 }}>{bulkEnrichResult}</span>
            <button onClick={() => setBulkEnrichResult(null)} style={{ background: "none", border: "none", cursor: "pointer", color: "#7a96b0" }}><X size={14} /></button>
          </div>
        )}

        {(() => {
          // Toggle helpers — each card clears its own filter on a second click so
          // "click again to see all companies" works as the user expects.
          const isTierActive = (t: string) => tierFilter.length === 1 && tierFilter[0] === t;
          const isDispositionActive = (d: string) => dispositionFilter.length === 1 && dispositionFilter[0] === d;
          const clearAllFilters = () => {
            setSearch("");
            setOwnerScope("all");
            setOwnerFilter([]);
            setSdrFilter([]);
            setTierFilter([]);
            setDispositionFilter([]);
            setStatusFilter([]);
            setLaneFilter([]);
            setJourneyFilter([]);
            setBatchFilter("");
          };
          const toggleTier = (t: string) => {
            setActiveTab("accounts");
            if (isTierActive(t)) {
              clearAllFilters();
            } else {
              setTierFilter([t]);
              setDispositionFilter([]);
              setLaneFilter([]);
            }
          };
          const toggleDisposition = (d: string) => {
            setActiveTab("accounts");
            if (isDispositionActive(d)) {
              clearAllFilters();
            } else {
              setDispositionFilter([d]);
              setTierFilter([]);
              setLaneFilter([]);
            }
          };
          const toggleSourced = () => {
            // "Sourced Accounts" = show everything. Active when no filters are on
            // AND we're already on accounts tab.
            if (activeTab === "accounts" && tierFilter.length === 0 && dispositionFilter.length === 0 && laneFilter.length === 0 && !batchFilter) {
              // no-op — already showing all; clicking again leaves it as-is.
              return;
            }
            setActiveTab("accounts");
            clearAllFilters();
          };
          const toggleImportsTab = () => {
            setActiveTab(activeTab === "imports" ? "accounts" : "imports");
          };
          const sourcedActive =
            activeTab === "accounts"
            && !search.trim()
            && ownerScope === "all"
            && tierFilter.length === 0
            && dispositionFilter.length === 0
            && laneFilter.length === 0
            && !batchFilter;
          const importsActive = activeTab === "imports";

          return (
            <div className="as-summary-grid" style={{ display: "grid", gridTemplateColumns: "repeat(8, minmax(0, 1fr))", gap: 8 }}>
              <SummaryCard
                icon={<Building2 size={13} />}
                label="Sourced"
                value={String(totalCompanies)}
                hint="Total accounts currently available for enrichment and prospecting."
                tone="neutral"
                onClick={toggleSourced}
                active={sourcedActive}
              />
              <SummaryCard
                icon={<Flame size={13} />}
                label="Hot"
                value={String(hotCount)}
                hint="Accounts with the strongest ICP fit and highest near-term potential."
                tone="warm"
                onClick={() => toggleTier("hot")}
                active={isTierActive("hot")}
              />
              <SummaryCard
                icon={<TrendingUp size={13} />}
                label="Warm"
                value={String(warmCount)}
                hint="Good-fit accounts that still need stronger proof, timing, or persona clarity."
                tone="primary"
                onClick={() => toggleTier("warm")}
                active={isTierActive("warm")}
              />
              <SummaryCard
                icon={<Target size={13} />}
                label="High Priority"
                value={String(highPriorityCount)}
                hint="Accounts worth the fastest follow-up based on fit, intent, and sales feedback."
                tone="green"
                onClick={() => toggleDisposition("working")}
                active={isDispositionActive("working")}
              />
              <SummaryCard
                icon={<Users size={13} />}
                label="Engaged"
                value={String(engagedCount)}
                hint="Accounts where reps have logged active motion or positive interest."
                tone="primary"
                onClick={() => toggleDisposition("interested")}
                active={isDispositionActive("interested")}
              />
              <SummaryCard
                icon={<Target size={13} />}
                label="Researched"
                value={String(researchedCount)}
                hint="Accounts with a generated Beacon research brief already available."
                tone="green"
                onClick={toggleImportsTab}
                active={importsActive}
              />
              <SummaryCard
                icon={<Sparkles size={13} />}
                label="Target Verdicts"
                value={String(targetVerdictCount)}
                hint={`${watchVerdictCount} more accounts are currently in Watch.`}
                tone="warm"
                onClick={toggleImportsTab}
                active={importsActive}
              />
              <SummaryCard
                icon={<AlertCircle size={13} />}
                // Renamed from "Needs Review" so it can't be mistaken for the
                // Needs Review tab — this tile is about enrichment coverage.
                label="Enrichment Gaps"
                value={String(unresolvedCount + unenrichedCount)}
                hint={`${unresolvedCount} unresolved domains, ${unenrichedCount} accounts without completed enrichment.`}
                tone="warm"
                onClick={toggleImportsTab}
                active={importsActive}
              />
            </div>
          );
        })()}

        {/* Import status stays above the table ONLY while the import actively
            needs attention (running or awaiting review). Completed cards move
            below the table so the account list stays high on the page. */}
        {batchNeedsAttention ? importStatusCard : null}

        {activeTab === "accounts" ? (
          <>
            {showAdvancedFilter && (
              <div
                data-mobile-modal
                onClick={() => setShowAdvancedFilter(false)}
                style={{ position: "fixed", inset: 0, background: "rgba(15,39,68,0.45)", zIndex: 80, display: "flex", alignItems: "center", justifyContent: "center" }}
              >
                <div
                  data-mobile-modal-panel
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    background: "#fff", borderRadius: 16, width: "min(520px, 92vw)",
                    border: `1px solid ${colors.border}`, boxShadow: "0 24px 60px rgba(15,23,42,0.22)",
                    padding: 22, display: "flex", flexDirection: "column", gap: 16,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
                    <div>
                      <div style={{ fontSize: 16, fontWeight: 800, color: colors.text }}>Advanced filter</div>
                      <div style={{ fontSize: 12, color: colors.sub, marginTop: 4 }}>
                        Filter accounts by the number of prospects they have. Pick an operator and a value.
                      </div>
                    </div>
                    <button type="button" onClick={() => setShowAdvancedFilter(false)} style={{ border: "none", background: "transparent", color: colors.sub, cursor: "pointer" }}>
                      <X size={16} />
                    </button>
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: colors.sub, textTransform: "uppercase", letterSpacing: 0.4 }}>Field</span>
                      <select
                        value="prospects"
                        disabled
                        style={{ height: 36, borderRadius: 9, border: `1px solid ${colors.border}`, padding: "0 10px", fontSize: 13, color: colors.text, background: "#f7fafd" }}
                      >
                        <option value="prospects">Prospects</option>
                      </select>
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: colors.sub, textTransform: "uppercase", letterSpacing: 0.4 }}>Operator</span>
                      <select
                        value={advOp}
                        onChange={(e) => setAdvOp(e.target.value as ProspectOp)}
                        style={{ height: 36, borderRadius: 9, border: `1px solid ${colors.border}`, padding: "0 10px", fontSize: 13, color: colors.text, background: "#fff" }}
                      >
                        <option value="gt">&gt;</option>
                        <option value="lt">&lt;</option>
                        <option value="eq">=</option>
                        <option value="between">between</option>
                      </select>
                    </label>
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: advOp === "between" ? "1fr 1fr" : "1fr", gap: 10 }}>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: colors.sub, textTransform: "uppercase", letterSpacing: 0.4 }}>
                        {advOp === "between" ? "From" : "Value"}
                      </span>
                      <input
                        type="number"
                        min={0}
                        value={advValue}
                        onChange={(e) => setAdvValue(e.target.value)}
                        style={{ height: 36, borderRadius: 9, border: `1px solid ${colors.border}`, padding: "0 10px", fontSize: 13, color: colors.text, background: "#fff" }}
                      />
                    </label>
                    {advOp === "between" && (
                      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        <span style={{ fontSize: 11, fontWeight: 700, color: colors.sub, textTransform: "uppercase", letterSpacing: 0.4 }}>To</span>
                        <input
                          type="number"
                          min={0}
                          value={advValue2}
                          onChange={(e) => setAdvValue2(e.target.value)}
                          style={{ height: 36, borderRadius: 9, border: `1px solid ${colors.border}`, padding: "0 10px", fontSize: 13, color: colors.text, background: "#fff" }}
                        />
                      </label>
                    )}
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginTop: 4 }}>
                    <button
                      type="button"
                      onClick={() => {
                        setProspectsMin(undefined);
                        setProspectsMax(undefined);
                        setAdvValue("");
                        setAdvValue2("");
                        setShowAdvancedFilter(false);
                      }}
                      style={{ height: 38, padding: "0 14px", borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, fontSize: 13, fontWeight: 600, cursor: "pointer" }}
                    >
                      Clear
                    </button>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        type="button"
                        onClick={() => setShowAdvancedFilter(false)}
                        style={{ height: 38, padding: "0 14px", borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, fontSize: 13, fontWeight: 600, cursor: "pointer" }}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        disabled={!advValue || (advOp === "between" && !advValue2)}
                        onClick={() => {
                          const v = Number(advValue);
                          const v2 = Number(advValue2);
                          if (!Number.isFinite(v)) return;
                          let nextMin: number | undefined;
                          let nextMax: number | undefined;
                          if (advOp === "gt") nextMin = v + 1;
                          else if (advOp === "lt") nextMax = v - 1;
                          else if (advOp === "eq") { nextMin = v; nextMax = v; }
                          else if (advOp === "between" && Number.isFinite(v2)) {
                            nextMin = Math.min(v, v2);
                            nextMax = Math.max(v, v2);
                          }
                          setProspectsMin(nextMin);
                          setProspectsMax(nextMax);
                          setShowAdvancedFilter(false);
                        }}
                        style={{
                          height: 38, padding: "0 16px", borderRadius: 10,
                          border: "1px solid #6fae27", background: "#6fae27",
                          color: "#fff", fontSize: 13, fontWeight: 700, cursor: "pointer",
                          opacity: !advValue || (advOp === "between" && !advValue2) ? 0.6 : 1,
                        }}
                      >
                        Apply
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
            <div
              className="as-filter-bar"
              style={{
                ...cardStyle,
                borderRadius: 14,
                padding: "10px 12px",
                display: "grid",
                gap: 10,
                position: "sticky",
                top: 8,
                zIndex: 5,
              }}
            >
              {/* Row 1 — search + the most-used filters. Everything else lives
                  behind the "More filters" toggle below. */}
              <div className="as-filter-row" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div style={{ position: "relative", minWidth: 200, flex: "1 1 200px" }}>
                  <Search size={14} color={colors.faint} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search companies..."
                    style={{
                      width: "100%",
                      height: 42,
                      border: `1px solid ${colors.border}`,
                      borderRadius: 10,
                      padding: "0 12px 0 30px",
                      fontSize: 13,
                      outline: "none",
                      boxSizing: "border-box",
                    }}
                  />
                </div>
                <select
                  value={ownerScope}
                  onChange={(event) => setOwnerScope(event.target.value === "mine" ? "mine" : "all")}
                  title="View — all accounts or only mine"
                  style={{
                    height: 42,
                    borderRadius: 10,
                    border: ownerScope === "mine" ? "1.5px solid #cfe89a" : "1px solid #d9e1ec",
                    background: ownerScope === "mine" ? "#f3fbe3" : "#fff",
                    padding: "0 28px 0 12px",
                    fontSize: 13,
                    color: "#1d2b3c",
                    outline: "none",
                    minWidth: 130,
                    cursor: "pointer",
                  }}
                >
                  <option value="all">All accounts</option>
                  <option value="mine">My accounts</option>
                </select>
                {teamUsers.length > 0 && (
                  <MultiSelectFilter
                    values={sdrFilter}
                    onChange={setSdrFilter}
                    options={teamUsers.map((u) => ({ value: u.id, label: u.name || u.email }))}
                    label="SDR Assigned"
                    allLabel="SDR: All"
                    minWidth={140}
                    hideLabel
                  />
                )}
                <MultiSelectFilter
                  values={statusFilter}
                  onChange={setStatusFilter}
                  options={ACCOUNT_STATUS_FILTER_OPTIONS}
                  label="Status"
                  allLabel="Status: All"
                  minWidth={130}
                  hideLabel
                />
                <select
                  value={sortBy}
                  onChange={(event) => setSortBy(event.target.value as AccountSortKey)}
                  title="Sort the currently visible account page"
                  style={{
                    height: 42,
                    minWidth: 160,
                    borderRadius: 10,
                    border: `1px solid ${colors.border}`,
                    background: colors.card,
                    color: colors.text,
                    padding: "0 12px",
                    fontSize: 13,
                    fontWeight: 700,
                    outline: "none",
                    cursor: "pointer",
                  }}
                >
                  {ACCOUNT_SORT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setShowMoreFilters((open) => !open)}
                  aria-expanded={showMoreFilters}
                  title="Show ICP tier, AE, disposition, outreach lane, journey stage, and the prospects-count rule"
                  style={{
                    height: 42,
                    padding: "0 12px",
                    borderRadius: 10,
                    border: moreFilterCount > 0 ? "1.5px solid #cfe89a" : `1px solid ${colors.border}`,
                    background: moreFilterCount > 0 ? "#f3fbe3" : colors.card,
                    color: moreFilterCount > 0 ? "#4d7c0f" : colors.text,
                    fontSize: 13,
                    fontWeight: 700,
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  More filters{moreFilterCount > 0 ? ` (${moreFilterCount})` : ""}
                  <ChevronDown size={13} style={{ transform: showMoreFilters ? "rotate(180deg)" : "none", transition: "transform 0.15s ease" }} />
                </button>
                <span style={{ marginLeft: "auto", color: colors.sub, fontSize: 12.5, fontWeight: 700, whiteSpace: "nowrap" }}>
                  {companyTotal === 0 ? "0 shown" : `${showingStart}-${showingEnd} of ${companyTotal}`}
                </span>
                {hasFilters ? (
                  <button
                    type="button"
                    onClick={() => {
                      setSearch("");
                      setOwnerScope("all");
                      setOwnerFilter([]);
                      setSdrFilter([]);
                      setTierFilter([]);
                      setDispositionFilter([]);
                      setStatusFilter([]);
                      setLaneFilter([]);
                      setJourneyFilter([]);
                      setBatchFilter("");
                      setProspectsMin(undefined);
                      setProspectsMax(undefined);
                    }}
                    style={{
                      height: 42,
                      border: `1px solid ${colors.border}`,
                      background: colors.card,
                      color: colors.text,
                      borderRadius: 10,
                      padding: "0 12px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      fontWeight: 700,
                      cursor: "pointer",
                      fontSize: 12.5,
                    }}
                  >
                    Reset filters
                  </button>
                ) : null}
              </div>

              {/* Active-filter chips for filters with no control of their own.
                  Today that is just the import drill-in (batch_id), set by
                  "View accounts" on the Recent Imports tab. */}
              {batchFilter ? (
                <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 11, fontWeight: 800, color: colors.faint, textTransform: "uppercase", letterSpacing: 0.4 }}>Filtered by</span>
                  <span
                    title={`Only accounts imported by this batch (${batchFilter})`}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      background: "#f3fbe3",
                      border: "1px solid #cfe89a",
                      color: "#4d7c0f",
                      borderRadius: 999,
                      padding: "3px 6px 3px 10px",
                      fontSize: 11.5,
                      fontWeight: 800,
                      maxWidth: 340,
                    }}
                  >
                    <Upload size={11} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      Import · {batches.find((batch) => batch.id === batchFilter)?.filename ?? "selected batch"}
                    </span>
                    <button
                      type="button"
                      onClick={() => setBatchFilter("")}
                      aria-label="Remove import filter"
                      style={{ border: 0, background: "transparent", color: "#4d7c0f", cursor: "pointer", display: "inline-flex", padding: 2, borderRadius: 999 }}
                    >
                      <X size={11} />
                    </button>
                  </span>
                </div>
              ) : null}

              {/* Row 2 — the less-used filters, revealed on demand (auto-opens
                  when one of them is active). Every filter stays functional. */}
              {showMoreFilters && (
                <div className="as-filter-row" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", borderTop: "1px solid #eef2f8", paddingTop: 10 }}>
                  <MultiSelectFilter
                    values={tierFilter}
                    onChange={setTierFilter}
                    options={TIER_OPTIONS}
                    label="ICP Tier"
                    allLabel="ICP Tier: All"
                    minWidth={130}
                    hideLabel
                  />
                  {teamUsers.length > 0 && (
                    <MultiSelectFilter
                      values={ownerFilter}
                      onChange={setOwnerFilter}
                      options={teamUsers.map((u) => ({ value: u.id, label: u.name || u.email }))}
                      label="AE Assigned"
                      allLabel="AE: All"
                      minWidth={140}
                      hideLabel
                    />
                  )}
                  <MultiSelectFilter
                    values={dispositionFilter}
                    onChange={setDispositionFilter}
                    options={DISPOSITION_OPTIONS}
                    label="Disposition"
                    allLabel="Disposition: All"
                    minWidth={150}
                    hideLabel
                  />
                  <MultiSelectFilter
                    values={laneFilter}
                    onChange={setLaneFilter}
                    options={OUTREACH_LANE_OPTIONS}
                    label="Outreach Lane"
                    allLabel="Lane: All"
                    minWidth={140}
                    hideLabel
                  />
                  <MultiSelectFilter
                    values={journeyFilter}
                    onChange={setJourneyFilter}
                    options={JOURNEY_FILTER_OPTIONS}
                    label="Journey Stage"
                    allLabel="Journey: All"
                    minWidth={140}
                    hideLabel
                  />
                  <button
                    type="button"
                    onClick={() => {
                      // Hydrate the modal draft from the active bounds so the
                      // user sees their current rule when they open it.
                      if (prospectsMin !== undefined && prospectsMax !== undefined && prospectsMin === prospectsMax) {
                        setAdvOp("eq"); setAdvValue(String(prospectsMin)); setAdvValue2("");
                      } else if (prospectsMin !== undefined && prospectsMax !== undefined) {
                        setAdvOp("between"); setAdvValue(String(prospectsMin)); setAdvValue2(String(prospectsMax));
                      } else if (prospectsMin !== undefined) {
                        setAdvOp("gt"); setAdvValue(String(prospectsMin - 1)); setAdvValue2("");
                      } else if (prospectsMax !== undefined) {
                        setAdvOp("lt"); setAdvValue(String(prospectsMax + 1)); setAdvValue2("");
                      }
                      setShowAdvancedFilter(true);
                    }}
                    title="Filter accounts by the number of prospects they have"
                    style={{
                      height: 42, padding: "0 12px", borderRadius: 10,
                      border: hasAdvancedFilter ? "1.5px solid #cfe89a" : `1px solid ${colors.border}`,
                      background: hasAdvancedFilter ? "#f3fbe3" : colors.card,
                      color: hasAdvancedFilter ? "#4d7c0f" : colors.text,
                      fontSize: 13, fontWeight: 700, cursor: "pointer",
                      display: "inline-flex", alignItems: "center", gap: 6,
                    }}
                  >
                    Advanced Filter{hasAdvancedFilter ? " •" : ""}
                  </button>
                  {hasFilters && (
                    <button
                      type="button"
                      disabled={downloadingFiltered}
                      title={`Download all ${companyTotal} accounts matching the current filters, in the current sort order`}
                      onClick={async () => {
                        setDownloadingFiltered(true);
                        try {
                          // EVERY active filter + the current sort — the same
                          // params the list and the KPI strip were built from.
                          const blob = await accountSourcingApi.exportCsv(activeFilters, sortParams);
                          downloadBlob(blob, `sourced-companies-filtered-${new Date().toISOString().slice(0, 10)}.csv`);
                        } catch (e) {
                          toast.error(e instanceof Error ? e.message : "Export failed", "Download failed");
                        } finally {
                          setDownloadingFiltered(false);
                        }
                      }}
                      style={{
                        height: 42, padding: "0 12px", borderRadius: 10,
                        border: "1px solid #6fae27", background: "#6fae27",
                        color: "#fff", fontSize: 13, fontWeight: 700, cursor: "pointer",
                        display: "inline-flex", alignItems: "center", gap: 6,
                        opacity: downloadingFiltered ? 0.7 : 1,
                      }}
                    >
                      {downloadingFiltered ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                      Download filtered ({companyTotal})
                    </button>
                  )}
                </div>
              )}
            </div>

            {loading ? (
          <div style={{ ...cardStyle, padding: 36, textAlign: "center" }}>
            <Loader2 className="animate-spin" color={colors.primary} />
          </div>
        ) : companies.length === 0 ? (
          <div style={{ ...cardStyle, padding: 34, textAlign: "center", color: colors.faint }}>
            <Building2 size={30} style={{ marginBottom: 8 }} />
            {hasFilters ? "No companies match these filters." : "No companies sourced yet."}
          </div>
        ) : (
          <div style={{ display: "grid", gap: 6 }}>
            {/* Admin-only bar. Non-admins are deliberately excluded: account
                visibility hides unassigned accounts from them, so a
                filter-wide assign could only ever release their own slots. */}
            {canSelectAccounts && (selectedCompanyIds.size > 0 || hasFilters) && (
              <div
                className="as-selection-bar"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  flexWrap: "wrap",
                  border: "1px solid #cfe89a",
                  background: "#f7fbef",
                  borderRadius: 14,
                  padding: "10px 14px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ color: colors.text, fontSize: 13, fontWeight: 800 }}>
                    {selectedCompanyIds.size > 0
                      ? `${selectedCompanyIds.size} selected`
                      : `${companyTotal} match these filters`}
                  </span>
                  <button
                    type="button"
                    onClick={toggleVisibleCompanySelection}
                    style={{
                      height: 34,
                      border: `1px solid ${colors.border}`,
                      background: colors.card,
                      color: colors.text,
                      borderRadius: 10,
                      padding: "0 12px",
                      fontSize: 12,
                      fontWeight: 800,
                      cursor: "pointer",
                    }}
                  >
                    {allVisibleSelected ? "Clear page" : "Select visible page"}
                  </button>
                  {/* Filter-wide: acts on every matching account, not just the
                      loaded page. Confirmed against expected_total server-side. */}
                  {hasFilters && companyTotal > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        setAssignAllConflict(null);
                        setShowAssignAllModal(true);
                      }}
                      title="Assign every account matching the current filters — all pages, not just this one"
                      style={{
                        height: 34,
                        border: "1px solid #9ace3d",
                        background: "#f3fbe3",
                        color: "#4d7c0f",
                        borderRadius: 10,
                        padding: "0 12px",
                        fontSize: 12,
                        fontWeight: 800,
                        cursor: "pointer",
                      }}
                    >
                      Assign all {companyTotal} matching…
                    </button>
                  )}
                  {selectedCompanyIds.size > 0 && (
                  <button
                    type="button"
                    onClick={clearCompanySelection}
                    style={{
                      height: 34,
                      border: `1px solid ${colors.border}`,
                      background: colors.card,
                      color: colors.sub,
                      borderRadius: 10,
                      padding: "0 12px",
                      fontSize: 12,
                      fontWeight: 750,
                      cursor: "pointer",
                    }}
                  >
                    Clear
                  </button>
                  )}
                  {selectedCompanyIds.size > 0 && (
                  <button
                    type="button"
                    disabled={exportingSelected}
                    onClick={async () => {
                      setSelectionExportError(null);
                      setExportingSelected(true);
                      try {
                        // Explicit selection wins over the page filters.
                        const blob = await accountSourcingApi.exportCsv({ companyIds: Array.from(selectedCompanyIds) }, sortParams);
                        downloadBlob(blob, `sourced-companies-selected-${new Date().toISOString().slice(0, 10)}.csv`);
                      } catch (e) {
                        setSelectionExportError(e instanceof Error ? e.message : "Export failed");
                      } finally {
                        setExportingSelected(false);
                      }
                    }}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      height: 34,
                      border: "1px solid #b8d0f0",
                      background: "#eef5ff",
                      color: "#175089",
                      borderRadius: 10,
                      padding: "0 12px",
                      fontSize: 12,
                      fontWeight: 800,
                      cursor: exportingSelected ? "not-allowed" : "pointer",
                      opacity: exportingSelected ? 0.7 : 1,
                    }}
                  >
                    {exportingSelected ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                    {exportingSelected ? "Exporting…" : "Export CSV"}
                  </button>
                  )}
                </div>
                {/* The two selects act on the SELECTED rows only — hidden when
                    nothing is selected so they can't be confused with the
                    filter-wide control on the left. */}
                <div style={{ display: selectedCompanyIds.size > 0 ? "flex" : "none", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <select
                    value=""
                    disabled={bulkAssigningAe || bulkAssigningSdr}
                    onChange={(e) => { if (e.target.value) void bulkAssignSelectedCompanies("ae", e.target.value); e.currentTarget.value = ""; }}
                    title="Assign selected accounts' AE"
                    style={{
                      height: 36,
                      border: `1px solid ${colors.border}`,
                      background: colors.card,
                      color: colors.text,
                      borderRadius: 11,
                      padding: "0 12px",
                      fontSize: 13,
                      fontWeight: 800,
                      cursor: bulkAssigningAe || bulkAssigningSdr ? "not-allowed" : "pointer",
                    }}
                  >
                    <option value="">{bulkAssigningAe ? "Assigning…" : "Assign AE →"}</option>
                    {teamUsers
                      .filter((u) => ["sdr", "ae", "admin", "agency"].includes((u.role || "").toLowerCase()))
                      .map((u) => (
                        <option key={u.id} value={u.id}>{u.name || u.email} ({(u.role || "").toUpperCase()})</option>
                      ))}
                  </select>
                  <select
                    value=""
                    disabled={bulkAssigningAe || bulkAssigningSdr}
                    onChange={(e) => { if (e.target.value) void bulkAssignSelectedCompanies("sdr", e.target.value); e.currentTarget.value = ""; }}
                    title="Assign selected accounts' SDR"
                    style={{
                      height: 36,
                      border: `1px solid ${colors.border}`,
                      background: colors.card,
                      color: colors.text,
                      borderRadius: 11,
                      padding: "0 12px",
                      fontSize: 13,
                      fontWeight: 800,
                      cursor: bulkAssigningAe || bulkAssigningSdr ? "not-allowed" : "pointer",
                    }}
                  >
                    <option value="">{bulkAssigningSdr ? "Assigning…" : "Assign SDR →"}</option>
                    {teamUsers
                      .filter((u) => ["sdr", "ae", "admin", "agency"].includes((u.role || "").toLowerCase()))
                      .map((u) => (
                        <option key={u.id} value={u.id}>{u.name || u.email} ({(u.role || "").toUpperCase()})</option>
                      ))}
                  </select>
                </div>
                {selectionExportError && (
                  <div style={{ flexBasis: "100%", color: colors.red, fontSize: 12, fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
                    <AlertCircle size={13} />
                    {selectionExportError}
                  </div>
                )}
              </div>
            )}
            <CompanyTableHeader
              selectable={canSelectAccounts}
              allSelected={allVisibleSelected}
              onToggleAll={toggleVisibleCompanySelection}
            />
            {sortedCompanies.map((c) => (
              <CompanyCard
                key={c.id}
                company={c}
                onAssigned={() => load()}
                selectable={canSelectAccounts}
                selected={selectedCompanyIds.has(c.id)}
                onToggleSelect={toggleCompanySelection}
              />
            ))}
            <div style={{ ...cardStyle, padding: "14px 16px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <div style={{ color: colors.sub, fontSize: 13 }}>
                {companyTotal === 0 ? "0 shown" : `Showing ${showingStart}-${showingEnd} of ${companyTotal} sourced companies`}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  style={{
                    border: `1px solid ${colors.border}`,
                    background: page <= 1 ? "#f5f7fb" : colors.card,
                    color: page <= 1 ? colors.faint : colors.text,
                    borderRadius: 10,
                    padding: "10px 14px",
                    fontWeight: 700,
                    cursor: page <= 1 ? "not-allowed" : "pointer",
                  }}
                >
                  Previous
                </button>
                <span style={{ color: colors.sub, fontSize: 13, fontWeight: 700, minWidth: 84, textAlign: "center" }}>
                  Page {page} / {Math.max(companyPages, 1)}
                </span>
                <button
                  type="button"
                  disabled={page >= companyPages}
                  onClick={() => setPage((current) => Math.min(companyPages, current + 1))}
                  style={{
                    border: `1px solid ${colors.border}`,
                    background: page >= companyPages ? "#f5f7fb" : colors.card,
                    color: page >= companyPages ? colors.faint : colors.text,
                    borderRadius: 10,
                    padding: "10px 14px",
                    fontWeight: 700,
                    cursor: page >= companyPages ? "not-allowed" : "pointer",
                  }}
                >
                  Next
                </button>
              </div>
            </div>
          </div>
        )}

            {/* Secondary panels — intentionally BELOW the account table so the
                list is the first thing reps reach. */}
            <JourneyFunnel
              summary={journeySummary}
              active={journeyFilter}
              onToggle={(stage) => {
                setPage(1);
                setJourneyFilter((prev) => (prev.length === 1 && prev[0] === stage ? [] : [stage]));
              }}
              onSync={handleSyncRecotap}
              syncing={syncingRecotap}
            />
            {!batchNeedsAttention ? importStatusCard : null}
            {isAdmin ? (
              <div style={{ ...cardStyle, borderRadius: 14, padding: "14px 16px", display: "grid", gap: 12 }}>
                <div style={{ display: "grid", gap: 2 }}>
                  <span style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>IMPORT &amp; BULK TOOLS</span>
                  <span style={{ color: colors.sub, fontSize: 12.5 }}>Bring accounts in from a workbook, or reassign owners in bulk.</span>
                </div>
                <UploadPanel onUploaded={handleBatchUploaded} onDownloadTemplate={downloadTemplate} />
                <BulkReassignUpload onApplied={() => load()} />
              </div>
            ) : null}
          </>
        ) : activeTab === "review" ? (
          <NeedsReviewTab />
        ) : (
          <div style={{ display: "grid", gap: 12 }}>
            {batches.length === 0 ? (
              <div style={{ ...cardStyle, padding: 28, textAlign: "center", color: colors.faint }}>
                No imports yet.
              </div>
            ) : (
              batches.map((batch) => {
                const errorRows = batch.error_log ?? [];
                const expanded = expandedBatchIds.has(batch.id);
                return (
                <div key={batch.id} style={{ ...cardStyle, padding: "18px 20px", display: "grid", gap: 12 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                    <div style={{ display: "grid", gap: 6 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        {batch.status === "completed" ? <CheckCircle2 size={15} color={colors.green} /> : <Upload size={15} color={colors.primary} />}
                        <span style={{ fontWeight: 800, color: colors.text, fontSize: 16 }}>{batch.filename}</span>
                      </div>
                      <div style={{ color: colors.sub, fontSize: 13 }}>
                        {batch.created_by_name ? `Created by ${batch.created_by_name}` : "Created by Beacon"} • {ts(batch.created_at)}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {/* Drill in: pins batch_id as an active (removable) filter
                          on the accounts table, and lands in the URL. */}
                      <button
                        type="button"
                        onClick={() => {
                          setBatchFilter(batch.id);
                          setPage(1);
                          setActiveTab("accounts");
                          window.scrollTo({ top: 0, behavior: "smooth" });
                        }}
                        title={`Show only the accounts imported by ${batch.filename}`}
                        style={{ border: "1px solid #9ace3d", background: "#f3fbe3", color: "#4d7c0f", borderRadius: 10, padding: "8px 12px", fontWeight: 800, fontSize: 12.5, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}
                      >
                        <Building2 size={13} />
                        View accounts ({batch.created_companies})
                      </button>
                      <button
                        type="button"
                        onClick={async () => {
                          const blob = await accountSourcingApi.exportCsv({ batchId: batch.id });
                          downloadBlob(blob, `${batch.filename.replace(/\s+/g, "-").toLowerCase()}-companies.csv`);
                        }}
                        style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 10, padding: "8px 12px", fontWeight: 700, cursor: "pointer" }}
                      >
                        Download companies
                      </button>
                      <button
                        type="button"
                        onClick={async () => {
                          const blob = await accountSourcingApi.exportContactsCsv({ batchId: batch.id });
                          downloadBlob(blob, `${batch.filename.replace(/\s+/g, "-").toLowerCase()}-contacts.csv`);
                        }}
                        style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 10, padding: "8px 12px", fontWeight: 700, cursor: "pointer" }}
                      >
                        Download contacts
                      </button>
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
                    {[
                      { label: "Status", value: batch.status.replace(/_/g, " "), hint: batch.progress_message || "Activity tracked automatically", tone: colors.text },
                      { label: "Accounts", value: `${batch.created_companies}`, hint: `${batch.processed_rows}/${batch.total_rows} processed`, tone: colors.text },
                      { label: "Contacts", value: `${batch.contacts_found ?? 0}`, hint: "Relevant stakeholders saved", tone: colors.text },
                      { label: "Verdicts", value: `${String((batch.verdict_summary || {}).target || 0)} target`, hint: String((batch.verdict_summary || {}).message || "No uploaded verdicts"), tone: colors.text },
                      // Rows the import did not create. Previously invisible, so
                      // a half-landed import looked like a clean one.
                      { label: "Skipped", value: `${batch.skipped_rows ?? 0}`, hint: "Duplicates or rows with nothing to import", tone: (batch.skipped_rows ?? 0) > 0 ? colors.amber : colors.text },
                      { label: "Failed", value: `${batch.failed_rows ?? 0}`, hint: errorRows.length ? "See the errors below" : "Rows that errored during import", tone: (batch.failed_rows ?? 0) > 0 ? colors.red : colors.text },
                    ].map((item) => (
                      <div key={`${batch.id}-${item.label}`} style={{ border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 14px", background: "#fbfdff" }}>
                        <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>{item.label.toUpperCase()}</div>
                        <div style={{ marginTop: 6, color: item.tone, fontSize: 18, fontWeight: 800 }}>{item.value}</div>
                        <div style={{ marginTop: 4, color: colors.sub, fontSize: 12, lineHeight: 1.45 }}>{item.hint}</div>
                      </div>
                    ))}
                  </div>

                  {/* Import errors — collapsed by default, nothing rendered at
                      all when the batch landed clean. Entries wrap rather than
                      truncate: AE/SDR resolution failures list every affected
                      account and are long by design. */}
                  {errorRows.length > 0 && (
                    <div style={{ border: "1px solid #f3c9c2", borderRadius: 12, background: "#fff7f5", overflow: "hidden" }}>
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedBatchIds((current) => {
                            const next = new Set(current);
                            if (next.has(batch.id)) next.delete(batch.id);
                            else next.add(batch.id);
                            return next;
                          })
                        }
                        aria-expanded={expanded}
                        style={{
                          width: "100%",
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          border: 0,
                          background: "transparent",
                          color: colors.red,
                          padding: "10px 12px",
                          fontSize: 12.5,
                          fontWeight: 800,
                          cursor: "pointer",
                          textAlign: "left",
                        }}
                      >
                        <ChevronRight
                          size={13}
                          style={{ transform: expanded ? "rotate(90deg)" : "none", transition: "transform 0.15s ease", flexShrink: 0 }}
                        />
                        <AlertCircle size={13} style={{ flexShrink: 0 }} />
                        {errorRows.length} import {errorRows.length === 1 ? "error" : "errors"}
                        <span style={{ color: colors.sub, fontWeight: 600 }}>· {expanded ? "hide" : "show details"}</span>
                      </button>
                      {expanded && (
                        <div style={{ display: "grid", gap: 6, padding: "0 12px 12px" }}>
                          {errorRows.map((entry, index) => (
                            <div
                              key={`${batch.id}-error-${index}`}
                              style={{ border: "1px solid #f3d9d4", background: "#fff", borderRadius: 10, padding: "8px 10px", display: "grid", gap: 3 }}
                            >
                              {entry.name ? (
                                <span style={{ color: colors.text, fontSize: 12, fontWeight: 800, wordBreak: "break-word" }}>{entry.name}</span>
                              ) : null}
                              <span style={{ color: colors.sub, fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                                {entry.error || "Unknown error"}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                );
              })
            )}
          </div>
        )}

        {showAssignAllModal ? (
          <>
            <div onClick={() => setShowAssignAllModal(false)} style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.24)", zIndex: 50 }} />
            <div
              style={{
                position: "fixed",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: 460,
                maxWidth: "94vw",
                background: "#fff",
                borderRadius: 18,
                boxShadow: "0 20px 60px rgba(15,23,42,0.18)",
                padding: 24,
                zIndex: 51,
                display: "grid",
                gap: 14,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div style={{ color: colors.text, fontSize: 17, fontWeight: 800 }}>Assign all matching accounts</div>
                <button type="button" onClick={() => setShowAssignAllModal(false)} style={{ border: 0, background: "transparent", cursor: "pointer", color: colors.faint }}>
                  <X size={18} />
                </button>
              </div>
              <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6 }}>
                This assigns <strong>all {companyTotal}</strong> account{companyTotal === 1 ? "" : "s"} matching the current filters — every page, not just this one.
                Normal assignment rules apply: prospects follow the account, and an SDR handoff resets their outreach progress.
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                {(["sdr", "ae"] as const).map((role) => (
                  <button
                    key={role}
                    type="button"
                    onClick={() => setAssignAllRole(role)}
                    style={{
                      flex: 1,
                      height: 36,
                      borderRadius: 10,
                      fontSize: 12,
                      fontWeight: 800,
                      cursor: "pointer",
                      border: assignAllRole === role ? "1.5px solid #9ace3d" : `1px solid ${colors.border}`,
                      background: assignAllRole === role ? "#f3fbe3" : "#fff",
                      color: assignAllRole === role ? "#4d7c0f" : colors.sub,
                    }}
                  >
                    {role.toUpperCase()} slot
                  </button>
                ))}
              </div>
              <select
                value={assignAllUserId}
                onChange={(e) => setAssignAllUserId(e.target.value)}
                style={{ width: "100%", height: 38, border: `1px solid ${colors.border}`, borderRadius: 10, padding: "0 10px", fontSize: 13, fontWeight: 700, color: colors.text, background: "#fff" }}
              >
                <option value="">Pick a teammate…</option>
                {teamUsers
                  .filter((u) => ["sdr", "ae", "admin", "agency"].includes((u.role || "").toLowerCase()))
                  .map((teammate) => (
                    <option key={teammate.id} value={teammate.id}>
                      {teammate.name || teammate.email} ({(teammate.role || "").toUpperCase()})
                    </option>
                  ))}
              </select>
              {assignAllConflict ? (
                <div style={{ border: "1px solid #ffd8a8", background: "#fff8ef", borderRadius: 10, padding: "10px 12px", color: colors.amber, fontSize: 12, fontWeight: 700, lineHeight: 1.55, display: "flex", gap: 8 }}>
                  <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
                  <span>{assignAllConflict}</span>
                </div>
              ) : null}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button
                  type="button"
                  onClick={() => setShowAssignAllModal(false)}
                  style={{ height: 36, border: `1px solid ${colors.border}`, background: "#fff", color: colors.sub, borderRadius: 10, padding: "0 14px", fontSize: 12.5, fontWeight: 800, cursor: "pointer" }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={!assignAllUserId || assignAllBusy}
                  onClick={() => void runAssignAllMatching()}
                  style={{
                    height: 36,
                    border: 0,
                    background: !assignAllUserId || assignAllBusy ? "#c7dfa0" : colors.primary,
                    color: "#fff",
                    borderRadius: 10,
                    padding: "0 16px",
                    fontSize: 12.5,
                    fontWeight: 800,
                    cursor: !assignAllUserId || assignAllBusy ? "default" : "pointer",
                  }}
                >
                  {assignAllBusy ? "Assigning…" : `Assign ${companyTotal} account${companyTotal === 1 ? "" : "s"}`}
                </button>
              </div>
            </div>
          </>
        ) : null}

        {successModal ? (
          <>
            <div
              onClick={() => setSuccessModal(null)}
              style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.24)", zIndex: 50 }}
            />
            <div
              style={{
                position: "fixed",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: 460,
                maxWidth: "92vw",
                background: "#fff",
                borderRadius: 18,
                boxShadow: "0 20px 60px rgba(15,23,42,0.18)",
                padding: "24px 24px 20px",
                zIndex: 51,
                display: "grid",
                gap: 12,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <CheckCircle2 size={18} color={colors.green} />
                  <div style={{ color: colors.text, fontWeight: 800, fontSize: 18 }}>{successModal.title}</div>
                </div>
                <button type="button" onClick={() => setSuccessModal(null)} style={{ border: 0, background: "transparent", cursor: "pointer", color: colors.faint }}>
                  <X size={18} />
                </button>
              </div>
              <div style={{ color: colors.sub, fontSize: 14, lineHeight: 1.6 }}>{successModal.message}</div>
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button type="button" onClick={() => setSuccessModal(null)} style={{ border: 0, background: colors.primary, color: "#fff", borderRadius: 10, padding: "9px 14px", fontWeight: 700, cursor: "pointer" }}>
                  Continue
                </button>
              </div>
            </div>
          </>
        ) : null}

        {pendingBatchApproval ? (
          <>
            <div
              onClick={() => setPendingBatchApproval(null)}
              style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.24)", zIndex: 50 }}
            />
            <div
              style={{
                position: "fixed",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: 520,
                maxWidth: "94vw",
                background: "#fff",
                borderRadius: 18,
                boxShadow: "0 20px 60px rgba(15,23,42,0.18)",
                padding: "24px",
                zIndex: 51,
                display: "grid",
                gap: 14,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div>
                  <div style={{ color: colors.text, fontSize: 18, fontWeight: 800 }}>Review TAL verdicts</div>
                  <div style={{ color: colors.sub, fontSize: 13, marginTop: 4 }}>{pendingBatchApproval.filename}</div>
                </div>
                <button type="button" onClick={() => setPendingBatchApproval(null)} style={{ border: 0, background: "transparent", cursor: "pointer", color: colors.faint }}>
                  <X size={18} />
                </button>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }}>
                {[
                  { label: "Target", value: String((pendingBatchApproval.verdict_summary || {}).target || 0) },
                  { label: "Watch", value: String((pendingBatchApproval.verdict_summary || {}).watch || 0) },
                  { label: "Non-target", value: String((pendingBatchApproval.verdict_summary || {}).non_target || 0) },
                  { label: "Unknown", value: String((pendingBatchApproval.verdict_summary || {}).unknown || 0) },
                ].map((item) => (
                  <div key={item.label} style={{ border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 10px", background: "#fbfdff", textAlign: "center" }}>
                    <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>{item.label.toUpperCase()}</div>
                    <div style={{ marginTop: 6, color: colors.text, fontSize: 18, fontWeight: 800 }}>{item.value}</div>
                  </div>
                ))}
              </div>
              <div style={{ color: colors.sub, fontSize: 14, lineHeight: 1.6 }}>
                {String((pendingBatchApproval.verdict_summary || {}).message || "Some imported rows need approval before enrichment starts.")}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, flexWrap: "wrap" }}>
                <button
                  type="button"
                  disabled={confirmingBatchId === pendingBatchApproval.id}
                  onClick={async () => {
                    setConfirmingBatchId(pendingBatchApproval.id);
                    try {
                      await accountSourcingApi.cancelBatch(pendingBatchApproval.id);
                      setPendingBatchApproval(null);
                      await load();
                    } finally {
                      setConfirmingBatchId("");
                    }
                  }}
                  style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 10, padding: "9px 14px", fontWeight: 700, cursor: "pointer" }}
                >
                  Cancel enrichment
                </button>
                <button
                  type="button"
                  disabled={confirmingBatchId === pendingBatchApproval.id}
                  onClick={async () => {
                    setConfirmingBatchId(pendingBatchApproval.id);
                    try {
                      await accountSourcingApi.confirmBatch(pendingBatchApproval.id, true);
                      setPendingBatchApproval(null);
                      setSuccessModal({
                        title: "Enrichment started",
                        message: "Beacon has started enriching the approved import.",
                      });
                      await load();
                    } finally {
                      setConfirmingBatchId("");
                    }
                  }}
                  style={{ border: 0, background: colors.primary, color: "#fff", borderRadius: 10, padding: "9px 14px", fontWeight: 700, cursor: "pointer", minWidth: 160 }}
                >
                  {confirmingBatchId === pendingBatchApproval.id ? "Starting..." : "Continue enrichment"}
                </button>
              </div>
            </div>
          </>
        ) : null}

        {showCreateModal ? (
          <>
            <div onClick={() => setShowCreateModal(false)} style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.24)", zIndex: 50 }} />
            <div
              style={{
                position: "fixed",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: 500,
                maxWidth: "94vw",
                background: "#fff",
                borderRadius: 18,
                boxShadow: "0 20px 60px rgba(15,23,42,0.18)",
                padding: "24px",
                zIndex: 51,
                display: "grid",
                gap: 14,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div style={{ color: colors.text, fontSize: 18, fontWeight: 800 }}>Add accounts manually</div>
                <button type="button" onClick={() => setShowCreateModal(false)} style={{ border: 0, background: "transparent", cursor: "pointer", color: colors.faint }}>
                  <X size={18} />
                </button>
              </div>
              <div style={{ color: colors.sub, fontSize: 14, lineHeight: 1.6 }}>
                Paste one company per line. You can optionally add a website or domain after a comma or pipe. Beacon will log who created each one and when.
              </div>
              <div
                style={{
                  border: `1px solid ${colors.border}`,
                  borderRadius: 12,
                  padding: "12px 14px",
                  background: "#fbfdff",
                  display: "grid",
                  gap: 6,
                }}
              >
                <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>FORMAT</div>
                <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6 }}>
                  One per line.
                  <br />
                  `BlackLine`
                  <br />
                  `Serrala, serrala.com`
                  <br />
                  `Netcore Cloud | netcorecloud.com`
                </div>
              </div>
              <textarea
                value={createForm.companiesText}
                onChange={(e) => setCreateForm({ companiesText: e.target.value })}
                placeholder={"BlackLine\nSerrala, serrala.com\nNetcore Cloud | netcorecloud.com"}
                rows={8}
                style={{
                  border: `1px solid ${colors.border}`,
                  borderRadius: 10,
                  padding: "12px",
                  fontSize: 14,
                  color: colors.text,
                  resize: "vertical",
                  minHeight: 180,
                  lineHeight: 1.6,
                }}
              />
              {createError ? <div style={{ color: colors.red, fontSize: 13, fontWeight: 700 }}>{createError}</div> : null}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button type="button" onClick={() => setShowCreateModal(false)} style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 10, padding: "9px 14px", fontWeight: 700, cursor: "pointer" }}>
                  Close
                </button>
                <button type="button" disabled={creatingCompany} onClick={() => void handleCreateCompany()} style={{ border: 0, background: colors.primary, color: "#fff", borderRadius: 10, padding: "9px 14px", fontWeight: 700, cursor: "pointer", minWidth: 140 }}>
                  {creatingCompany ? "Creating..." : "Create & enrich"}
                </button>
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

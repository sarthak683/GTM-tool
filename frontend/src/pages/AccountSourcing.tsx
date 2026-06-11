import "./account-sourcing-refresh.css";
import { ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import {
  AlertCircle,
  Brain,
  Building2,
  CheckCircle2,
  ChevronRight,
  Download,
  Flame,
  Loader2,
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

import { accountSourcingApi } from "../lib/api";
import { ACCOUNT_STATUS_OPTIONS, accountStatusOption } from "../lib/accountStatus";
import type { RecotapSummary } from "../lib/api/prospecting";
import { getCachedUsers } from "../lib/cachedFetch";
import { getAccountPrioritySnapshot } from "../lib/utils";
import type { AccountSourcingSummary, Company, SourcingBatch, User } from "../types";
import AssignDropdown from "../components/AssignDropdown";
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
  { value: "priority_desc", label: "Priority high to low" },
  { value: "enriched_first", label: "Enriched first" },
  { value: "unenriched_first", label: "Needs enrichment first" },
  { value: "name_asc", label: "Company A → Z" },
  { value: "name_desc", label: "Company Z → A" },
];

function parseAccountSort(value: string | null): AccountSortKey {
  return ACCOUNT_SORT_OPTIONS.some((option) => option.value === value) ? (value as AccountSortKey) : "recent";
}

function SummaryCard({
  icon,
  label,
  value,
  hint,
  tone = "neutral",
  onClick,
  active = false,
  showActiveBadge = true,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  hint: string;
  tone?: "neutral" | "primary" | "warm" | "green";
  onClick?: () => void;
  active?: boolean;
  showActiveBadge?: boolean;
}) {
  const toneStyle = {
    neutral: { bg: "#f8fbff", border: colors.border, accent: colors.sub, activeBorder: "#94a3b8" },
    primary: { bg: "#f3fbe3", border: "#cfe89a", accent: colors.primary, activeBorder: colors.primary },
    warm: { bg: "#fff7eb", border: "#ffe0b2", accent: colors.amber, activeBorder: colors.amber },
    green: { bg: "#eefcf5", border: "#cdeedc", accent: colors.green, activeBorder: colors.green },
  }[tone];

  return (
    <div
      style={{
        ...cardStyle,
        padding: "18px 18px 16px",
        background: toneStyle.bg,
        // Thicker colored border + subtle ring when the card's filter is active,
        // so the rep sees which card is "on".
        borderColor: active ? toneStyle.activeBorder : toneStyle.border,
        borderWidth: active ? 2 : 1,
        boxShadow: active ? `0 0 0 3px ${toneStyle.bg}` : undefined,
        cursor: onClick ? "pointer" : "default",
        position: "relative",
      }}
      onClick={onClick}
    >
      {active && showActiveBadge && (
        <span
          style={{
            position: "absolute", top: 10, right: 12,
            fontSize: 10, fontWeight: 800, letterSpacing: 0.4,
            color: toneStyle.activeBorder, textTransform: "uppercase",
          }}
          title="Click again to clear filter"
        >
          FILTERED · CLICK TO CLEAR
        </span>
      )}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ color: toneStyle.accent }}>{icon}</div>
        <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.5 }}>{label.toUpperCase()}</div>
      </div>
      <div style={{ marginTop: 14, color: colors.text, fontSize: 28, fontWeight: 800 }}>{value}</div>
      <div style={{ marginTop: 6, color: colors.sub, fontSize: 13, lineHeight: 1.5 }}>{hint}</div>
    </div>
  );
}

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
    <div style={{ width: 38, height: 38, borderRadius: 11, background: bg, color: fg, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontSize: 15, flexShrink: 0 }}>
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

function CompanyTableHeader() {
  const cell: CSSProperties = { fontSize: 10.5, fontWeight: 800, color: "#9aa7b8", textTransform: "uppercase", letterSpacing: "0.05em" };
  return (
    <div className="as-company-table-header" style={{ padding: "2px 18px 8px" }}>
      <span style={cell}>Account</span>
      <span style={cell}>Signals</span>
      <span style={{ ...cell, textAlign: "right" }}>AE · SDR</span>
    </div>
  );
}

function CompanyCard({ company, onAssigned }: { company: Company; onAssigned: (userId: string | null, userName: string | null) => void }) {
  const nav = useNavigate();

  const tier = company.icp_tier || "cold";
  const disposition = company.disposition || "";
  const statusOption = accountStatusOption(company.account_status);
  const rtp = company.recotap;
  const journeyStyle = rtp?.journey_stage ? (JOURNEY_STAGE_STYLE[rtp.journey_stage] ?? NEUTRAL_BADGE) : NEUTRAL_BADGE;
  const engagementStyle = rtp?.engagement ? (ENGAGEMENT_STYLE[rtp.engagement] ?? NEUTRAL_BADGE) : NEUTRAL_BADGE;
  const domainText = company.domain.endsWith(".unknown") ? "Domain unresolved" : company.domain;

  return (
    <div
      className="as-company-card as-company-row crm-hover-lift"
      onClick={() => nav(`/account-sourcing/${company.id}`)}
      style={{
        ...cardStyle,
        borderRadius: 14,
        boxShadow: "0 2px 8px rgba(17,34,68,0.04)",
        padding: "11px 18px",
        cursor: "pointer",
        transition: "transform 150ms cubic-bezier(0.22, 1, 0.36, 1), box-shadow 150ms ease, background 0.15s",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "#f8fbff"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "#ffffff"; }}
    >
      {/* Account */}
      <div className="as-col-account" style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <CompanyAvatar name={company.name} />
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ color: colors.text, fontWeight: 800, fontSize: 14.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{company.name}</div>
          <div style={{ color: colors.faint, fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>
            {domainText}{company.industry ? ` · ${company.industry}` : ""}
          </div>
        </div>
      </div>

      {/* Signals — one content-driven chip cluster; shows only what exists, so
          there are no perpetually-empty columns. ICP tier always renders; the
          rest (status, disposition, Recotap journey/engagement, HQ) appear only
          when set. Wraps to a second line on dense rows. */}
      <div className="as-col-signals" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 6, minWidth: 0 }}>
        <span style={{ ...ICP_STYLE[tier], borderRadius: 999, fontSize: 10.5, fontWeight: 800, padding: "3px 9px", whiteSpace: "nowrap" }}>{tier.toUpperCase()}</span>
        {statusOption ? (
          <span style={{ background: statusOption.bg, color: statusOption.color, borderRadius: 999, padding: "3px 9px", fontSize: 10.5, fontWeight: 800, whiteSpace: "nowrap" }}>{statusOption.label}</span>
        ) : null}
        {disposition ? (
          <span style={{ background: "#f4f7fb", color: colors.sub, border: `1px solid ${colors.border}`, borderRadius: 999, padding: "3px 9px", fontSize: 10.5, fontWeight: 700, whiteSpace: "nowrap" }}>{disposition}</span>
        ) : null}
        {rtp?.journey_stage ? (
          <span title="Recotap journey stage" style={{ background: journeyStyle.bg, color: journeyStyle.color, border: `1px solid ${journeyStyle.border}`, borderRadius: 999, padding: "3px 9px", fontSize: 10.5, fontWeight: 800, whiteSpace: "nowrap" }}>{rtp.journey_stage}</span>
        ) : null}
        {rtp?.engagement ? (
          <span title="Recotap engagement" style={{ background: engagementStyle.bg, color: engagementStyle.color, border: `1px solid ${engagementStyle.border}`, borderRadius: 999, padding: "3px 9px", fontSize: 10.5, fontWeight: 700, whiteSpace: "nowrap" }}>{rtp.engagement}</span>
        ) : null}
        {rtp?.hq_location ? (
          <span style={{ color: colors.faint, fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{rtp.hq_location}</span>
        ) : null}
      </div>

      {/* Owners (AE + SDR) — names only, never raw emails */}
      <div className="as-col-owners as-company-card-assign" style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 6 }} onClick={(e) => e.stopPropagation()}>
        <AssignDropdown
          entityType="company"
          entityId={company.id}
          role="ae"
          currentAssignedId={company.assigned_to_id ?? null}
          currentAssignedName={prettyRepName(company.assigned_rep_name || company.assigned_rep, company.assigned_rep_email)}
          onAssigned={onAssigned}
          compact
          label="AE"
        />
        <AssignDropdown
          entityType="company"
          entityId={company.id}
          role="sdr"
          currentAssignedId={company.sdr_id ?? null}
          currentAssignedName={prettyRepName(company.sdr_name, company.sdr_email)}
          onAssigned={onAssigned}
          compact
          label="SDR"
        />
        <ChevronRight size={16} color={colors.faint} style={{ flexShrink: 0 }} />
      </div>
    </div>
  );
}

export default function AccountSourcing() {
  const pageSize = 40;
  const [searchParams, setSearchParams] = useSearchParams();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [summary, setSummary] = useState<AccountSourcingSummary | null>(null);
  const [batches, setBatches] = useState<SourcingBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(() => searchParams.get("q") ?? "");
  // Advanced filter — single rule "prospects {op} {value}" against the
  // contact count per account. URL-persisted as `pmin` / `pmax` so navigating
  // back into the page restores the filter. The op+value pair is derived
  // from the bounds when the modal opens.
  type ProspectOp = "gt" | "lt" | "eq" | "between";
  const initialPMin = searchParams.get("pmin");
  const initialPMax = searchParams.get("pmax");
  const [prospectsMin, setProspectsMin] = useState<number | undefined>(initialPMin !== null ? Number(initialPMin) : undefined);
  const [prospectsMax, setProspectsMax] = useState<number | undefined>(initialPMax !== null ? Number(initialPMax) : undefined);
  const [showAdvancedFilter, setShowAdvancedFilter] = useState(false);
  const [advOp, setAdvOp] = useState<ProspectOp>("gt");
  const [advValue, setAdvValue] = useState("");
  const [advValue2, setAdvValue2] = useState("");
  const [downloadingFiltered, setDownloadingFiltered] = useState(false);
  // Recotap journey-stage filter + the funnel counts that power the ABM band.
  const [journeyFilter, setJourneyFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("journey")));
  const [journeySummary, setJourneySummary] = useState<RecotapSummary | null>(null);
  const [syncingRecotap, setSyncingRecotap] = useState(false);
  const hasAdvancedFilter = prospectsMin !== undefined || prospectsMax !== undefined;
  const [ownerScope, setOwnerScope] = useState<"all" | "mine">(() => (searchParams.get("owner") === "mine" ? "mine" : "all"));
  // Multi-select Owner filter: matches assigned_to_id OR sdr_id for any
  // selected user. Different from ownerScope (binary mine vs all).
  const [ownerFilter, setOwnerFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("own")));
  const [teamUsers, setTeamUsers] = useState<User[]>([]);
  const [tierFilter, setTierFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("tier")));
  const [dispositionFilter, setDispositionFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("disp")));
  const [statusFilter, setStatusFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("status")));
  const [laneFilter, setLaneFilter] = useState<string[]>(() => parseSearchParamList(searchParams.get("lane")));
  const [sortBy, setSortBy] = useState<AccountSortKey>(() => parseAccountSort(searchParams.get("sort")));
  const [page, setPage] = useState(() => parseInt(searchParams.get("pg") ?? "1", 10) || 1);
  const [companyTotal, setCompanyTotal] = useState(0);
  const [companyPages, setCompanyPages] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [exportingContacts, setExportingContacts] = useState(false);
  const [resettingScope, setResettingScope] = useState<"" | "account-sourcing" | "workspace">("");
  const [activeTab, setActiveTab] = useState<"accounts" | "imports">("accounts");
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
  const { isAdmin, user } = useAuth();

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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // ownerScope === "mine" wins over the multi-select; otherwise the
      // multi-select drives. Both ultimately collapse to a single owner_id
      // string param the backend parses as comma-separated UUIDs.
      const effectiveOwnerId =
        ownerScope === "mine"
          ? user?.id
          : (ownerFilter.length ? ownerFilter : undefined);
      const [companyPage, companySummary, b, rtpSummary] = await Promise.all([
        accountSourcingApi.listCompaniesPaginated({
          skip: (page - 1) * pageSize,
          limit: pageSize,
          q: debouncedSearch || undefined,
          ownerId: effectiveOwnerId,
          icpTier: tierFilter.length ? tierFilter : undefined,
          disposition: dispositionFilter.length ? dispositionFilter : undefined,
          accountStatus: statusFilter.length ? statusFilter : undefined,
          recommendedOutreachLane: laneFilter.length ? laneFilter : undefined,
          journeyStage: journeyFilter.length ? journeyFilter : undefined,
          prospectsMin,
          prospectsMax,
        }),
        accountSourcingApi.summary({
          ownerId: effectiveOwnerId,
        }),
        accountSourcingApi.listBatches(),
        accountSourcingApi.recotapSummary().catch(() => null),
      ]);
      setCompanies(companyPage.items);
      setCompanyTotal(companyPage.total);
      setCompanyPages(companyPage.pages);
      setSummary(companySummary);
      setBatches(b);
      setJourneySummary(rtpSummary);
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, dispositionFilter, statusFilter, journeyFilter, laneFilter, ownerFilter, ownerScope, page, tierFilter, user?.id, prospectsMin, prospectsMax]);

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
      tierFilter.length ? next.set("tier", tierFilter.join(",")) : next.delete("tier");
      dispositionFilter.length ? next.set("disp", dispositionFilter.join(",")) : next.delete("disp");
      statusFilter.length ? next.set("status", statusFilter.join(",")) : next.delete("status");
      laneFilter.length ? next.set("lane", laneFilter.join(",")) : next.delete("lane");
      journeyFilter.length ? next.set("journey", journeyFilter.join(",")) : next.delete("journey");
      sortBy !== "recent" ? next.set("sort", sortBy) : next.delete("sort");
      page > 1 ? next.set("pg", String(page)) : next.delete("pg");
      prospectsMin !== undefined ? next.set("pmin", String(prospectsMin)) : next.delete("pmin");
      prospectsMax !== undefined ? next.set("pmax", String(prospectsMax)) : next.delete("pmax");
      return next;
    }, { replace: true });
  }, [laneFilter, dispositionFilter, statusFilter, journeyFilter, ownerFilter, ownerScope, page, search, setSearchParams, sortBy, tierFilter, prospectsMin, prospectsMax]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, dispositionFilter, statusFilter, journeyFilter, laneFilter, ownerFilter, ownerScope, tierFilter, prospectsMin, prospectsMax]);

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

  const sortedCompanies = useMemo(() => {
    const withIndex = companies.map((company, index) => ({ company, index }));
    const timestamp = (value?: string | null) => {
      const time = value ? new Date(value).getTime() : 0;
      return Number.isNaN(time) ? 0 : time;
    };
    withIndex.sort((a, b) => {
      if (sortBy === "name_asc") return a.company.name.localeCompare(b.company.name) || a.index - b.index;
      if (sortBy === "name_desc") return b.company.name.localeCompare(a.company.name) || a.index - b.index;
      if (sortBy === "icp_desc") return (b.company.icp_score ?? 0) - (a.company.icp_score ?? 0) || a.index - b.index;
      if (sortBy === "priority_desc") {
        return getAccountPrioritySnapshot(b.company).priorityScore - getAccountPrioritySnapshot(a.company).priorityScore || a.index - b.index;
      }
      if (sortBy === "enriched_first") {
        return Number(Boolean(b.company.enriched_at)) - Number(Boolean(a.company.enriched_at)) || a.index - b.index;
      }
      if (sortBy === "unenriched_first") {
        return Number(!a.company.enriched_at) - Number(!b.company.enriched_at) || a.index - b.index;
      }
      return timestamp(b.company.created_at) - timestamp(a.company.created_at) || a.index - b.index;
    });
    return withIndex.map((item) => item.company);
  }, [companies, sortBy]);

  const hasFilters = !!(search || ownerScope === "mine" || ownerFilter.length || tierFilter.length || dispositionFilter.length || statusFilter.length || laneFilter.length || journeyFilter.length);
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
      void load();
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

  return (
    <div className="account-sourcing-page" style={pageStyle}>
      <style>{`
        @media (max-width: 768px) {
          .as-hero-card { padding: 16px !important; }
          .as-hero-card h1 { font-size: 28px !important; }
          .as-hero-card p { font-size: 14px !important; }
          .as-summary-grid { grid-template-columns: 1fr 1fr !important; gap: 8px !important; }
          .as-filter-bar { position: static !important; top: auto !important; padding: 10px !important; }
          .as-filter-row { flex-direction: column !important; gap: 8px !important; }
          .as-filter-row > * { width: 100% !important; }
          .as-company-card { flex-wrap: wrap !important; padding: 12px !important; gap: 10px !important; }
          .as-company-card-desktop-chips { display: none !important; }
          .as-company-card-mobile-chips { display: flex !important; }
          .as-company-card-assign { display: none !important; }
        }
      `}</style>
      <div style={containerStyle}>
        <div
          className="as-hero-card"
          style={{
            ...cardStyle,
            padding: "26px 26px 22px",
            background: "radial-gradient(circle at top right, #eef7db 0%, transparent 28%), radial-gradient(circle at left center, #fff2ea 0%, transparent 24%), #ffffff",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <div>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 8, borderRadius: 999, padding: "6px 12px", background: "#f1f9e2", color: colors.primary, fontSize: 12, fontWeight: 800, letterSpacing: 0.4 }}>
                <Sparkles size={13} />
                GTM ENGINEERING
              </div>
              <h1 style={{ margin: "14px 0 0", color: colors.text, fontSize: 42, letterSpacing: 0.2 }}>Account Sourcing</h1>
              <p style={{ margin: "10px 0 0", color: colors.sub, fontSize: 17, lineHeight: 1.6, maxWidth: 780 }}>
                Start with company names and turn them into presentable account briefs with verdicts, timing, outreach angles, and a clean view of where to aim next.
              </p>
              <div style={{ marginTop: 18, display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap", background: "#f7faff", border: `1px solid ${colors.border}`, borderRadius: 14, padding: "8px" }}>
                {[
                  { id: "accounts", label: "Accounts" },
                  { id: "imports", label: `Recent Imports${batches.length ? ` (${batches.length})` : ""}` },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id as "accounts" | "imports")}
                    style={{
                      border: 0,
                      background: activeTab === tab.id ? "#f1f9e2" : "transparent",
                      color: activeTab === tab.id ? colors.primary : colors.sub,
                      borderRadius: 10,
                      padding: "10px 14px",
                      fontWeight: 800,
                      cursor: "pointer",
                    }}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ display: "inline-flex", gap: 10, flexWrap: "wrap" }}>
              <button
                onClick={() => setShowCreateModal(true)}
                style={{
                  border: 0,
                  background: "#6fae27",
                  color: "#fff",
                  borderRadius: 12,
                  padding: "10px 14px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                <Plus size={15} />
                Add Accounts
              </button>
              {isAdmin && (
                <>
                  <button
                    onClick={() => void runReset("account-sourcing")}
                    disabled={Boolean(resettingScope)}
                    style={{
                      border: "1px solid #f0c2c8",
                      background: "#fff6f7",
                      color: colors.red,
                      borderRadius: 12,
                      padding: "10px 14px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                      fontWeight: 700,
                      cursor: resettingScope ? "not-allowed" : "pointer",
                      opacity: resettingScope ? 0.7 : 1,
                    }}
                  >
                    {resettingScope === "account-sourcing" ? <Loader2 size={15} className="animate-spin" /> : <AlertCircle size={15} />}
                    Clear Account Sourcing
                  </button>
                  <button
                    onClick={() => void runReset("workspace")}
                    disabled={Boolean(resettingScope)}
                    style={{
                      border: "1px solid #f5d4d8",
                      background: "#fffafb",
                      color: colors.red,
                      borderRadius: 12,
                      padding: "10px 14px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                      fontWeight: 700,
                      cursor: resettingScope ? "not-allowed" : "pointer",
                      opacity: resettingScope ? 0.7 : 1,
                    }}
                  >
                    {resettingScope === "workspace" ? <Loader2 size={15} className="animate-spin" /> : <AlertCircle size={15} />}
                    Clear Workspace
                  </button>
                </>
              )}
              <button
                onClick={async () => {
                  setExportingContacts(true);
                  try {
                    const blob = await accountSourcingApi.exportContactsCsv();
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement("a");
                    anchor.href = url;
                    anchor.download = `sourced-contacts-${new Date().toISOString().slice(0, 10)}.csv`;
                    anchor.click();
                    URL.revokeObjectURL(url);
                  } finally {
                    setExportingContacts(false);
                  }
                }}
                style={{
                  border: `1px solid ${colors.border}`,
                  background: colors.card,
                  color: colors.text,
                  borderRadius: 12,
                  padding: "10px 14px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                {exportingContacts ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                Export Contacts
              </button>
              <button
                onClick={async () => {
                  setExporting(true);
                  try {
                    const blob = await accountSourcingApi.exportCsv();
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement("a");
                    anchor.href = url;
                    anchor.download = `sourced-companies-${new Date().toISOString().slice(0, 10)}.csv`;
                    anchor.click();
                    URL.revokeObjectURL(url);
                  } finally {
                    setExporting(false);
                  }
                }}
                style={{
                  border: `1px solid ${colors.border}`,
                  background: colors.card,
                  color: colors.text,
                  borderRadius: 12,
                  padding: "10px 14px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                {exporting ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                Export CSV
              </button>
              {isAdmin && (
                <>
                  <button
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
                    style={{
                      border: `1px solid #c3dfc0`,
                      background: "#edfaeb",
                      color: "#1a6b2a",
                      borderRadius: 12,
                      padding: "10px 14px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                      fontWeight: 700,
                      cursor: bulkIcpRunning ? "not-allowed" : "pointer",
                      opacity: bulkIcpRunning ? 0.7 : 1,
                    }}
                  >
                    {bulkIcpRunning ? <Loader2 size={15} className="animate-spin" /> : <Brain size={15} />}
                    Run ICP Research
                  </button>
                  <button
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
                    style={{
                      border: `1px solid #cfe89a`,
                      background: "#f3fbe3",
                      color: "#4d7c0f",
                      borderRadius: 12,
                      padding: "10px 14px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 8,
                      fontWeight: 700,
                      cursor: bulkEnriching ? "not-allowed" : "pointer",
                      opacity: bulkEnriching ? 0.7 : 1,
                    }}
                  >
                    {bulkEnriching ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                    Enrich All Accounts
                  </button>
                </>
              )}
              <button
                onClick={load}
                style={{
                  border: `1px solid ${colors.border}`,
                  background: colors.card,
                  color: colors.text,
                  borderRadius: 12,
                  padding: "10px 14px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                <RefreshCw size={15} /> Refresh
              </button>
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
            setTierFilter([]);
            setDispositionFilter([]);
            setStatusFilter([]);
            setLaneFilter([]);
            setJourneyFilter([]);
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
            if (activeTab === "accounts" && tierFilter.length === 0 && dispositionFilter.length === 0 && laneFilter.length === 0) {
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
            && laneFilter.length === 0;
          const importsActive = activeTab === "imports";

          return (
            <>
              <div className="as-summary-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
                <SummaryCard
                  icon={<Building2 size={18} />}
                  label="Sourced Accounts"
                  value={String(totalCompanies)}
                  hint="Total accounts currently available for enrichment and prospecting."
                  tone="neutral"
                  onClick={toggleSourced}
                  active={sourcedActive}
                  showActiveBadge={false}
                />
                <SummaryCard
                  icon={<Flame size={18} />}
                  label="Hot Accounts"
                  value={String(hotCount)}
                  hint="Accounts with the strongest ICP fit and highest near-term potential."
                  tone="warm"
                  onClick={() => toggleTier("hot")}
                  active={isTierActive("hot")}
                />
                <SummaryCard
                  icon={<TrendingUp size={18} />}
                  label="Warm Accounts"
                  value={String(warmCount)}
                  hint="Good-fit accounts that still need stronger proof, timing, or persona clarity."
                  tone="primary"
                  onClick={() => toggleTier("warm")}
                  active={isTierActive("warm")}
                />
                <SummaryCard
                  icon={<Target size={18} />}
                  label="High Priority"
                  value={String(highPriorityCount)}
                  hint="Accounts worth the fastest follow-up based on fit, intent, and sales feedback."
                  tone="green"
                  onClick={() => toggleDisposition("working")}
                  active={isDispositionActive("working")}
                />
              </div>

              <div className="as-summary-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
                <SummaryCard
                  icon={<Users size={18} />}
                  label="Engaged Accounts"
                  value={String(engagedCount)}
                  hint="Accounts where reps have logged active motion or positive interest."
                  tone="primary"
                  onClick={() => toggleDisposition("interested")}
                  active={isDispositionActive("interested")}
                />
                <SummaryCard
                  icon={<Target size={18} />}
                  label="Research Complete"
                  value={String(researchedCount)}
                  hint="Accounts with a generated Beacon research brief already available."
                  tone="green"
                  onClick={toggleImportsTab}
                  active={importsActive}
                  showActiveBadge={false}
                />
                <SummaryCard
                  icon={<Sparkles size={18} />}
                  label="Target Verdicts"
                  value={String(targetVerdictCount)}
                  hint={`${watchVerdictCount} more accounts are currently in Watch.`}
                  tone="warm"
                  onClick={toggleImportsTab}
                  active={importsActive}
                  showActiveBadge={false}
                />
                <SummaryCard
                  icon={<AlertCircle size={18} />}
                  label="Needs Review"
                  value={String(unresolvedCount + unenrichedCount)}
                  hint={`${unresolvedCount} unresolved domains, ${unenrichedCount} accounts without completed enrichment.`}
                  tone="warm"
                  onClick={toggleImportsTab}
                  active={importsActive}
                  showActiveBadge={false}
                />
              </div>
            </>
          );
        })()}

        {isAdmin && activeTab === "accounts" ? (
          <UploadPanel onUploaded={handleBatchUploaded} onDownloadTemplate={downloadTemplate} />
        ) : null}

        {latestVisibleBatch ? (
          <div
            style={{
              ...cardStyle,
              padding: "16px 18px",
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
                  <span style={{ color: colors.text, fontWeight: 800, fontSize: 15 }}>{latestVisibleBatch.filename}</span>
                </div>
                <div style={{ color: colors.sub, fontSize: 13 }}>
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
                    onClick={load}
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
              <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 14px" }}>
                <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>PROGRESS</div>
                <div style={{ marginTop: 6, color: colors.text, fontWeight: 800, fontSize: 20 }}>
                  {latestVisibleBatch.processed_rows}/{latestVisibleBatch.total_rows}
                </div>
                <div style={{ marginTop: 4, color: colors.sub, fontSize: 12 }}>Accounts processed</div>
              </div>
              <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 14px" }}>
                <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>CONTACTS FOUND</div>
                <div style={{ marginTop: 6, color: colors.text, fontWeight: 800, fontSize: 20 }}>
                  {latestVisibleBatch.contacts_found ?? 0}
                </div>
                <div style={{ marginTop: 4, color: colors.sub, fontSize: 12 }}>Relevant stakeholders saved</div>
              </div>
              <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 14px" }}>
                <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>CURRENT STEP</div>
                <div style={{ marginTop: 6, color: colors.text, fontWeight: 800, fontSize: 16 }}>
                  {formatBatchStage(latestVisibleBatch.current_stage, latestVisibleBatch.status)}
                </div>
                <div style={{ marginTop: 4, color: colors.sub, fontSize: 12 }}>{etaText}</div>
              </div>
              <div style={{ background: "#fff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 14px" }}>
                <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>TAL VERDICTS</div>
                <div style={{ marginTop: 6, color: colors.text, fontWeight: 800, fontSize: 18 }}>
                  {String(latestVerdictSummary.target || 0)} target / {String(latestVerdictSummary.watch || 0)} watch
                </div>
                <div style={{ marginTop: 4, color: colors.sub, fontSize: 12 }}>
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
        ) : null}

        {activeTab === "accounts" ? (
          <>
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
                padding: "14px 16px",
                display: "grid",
                gap: 12,
                position: "sticky",
                top: 16,
                zIndex: 5,
              }}
            >
          <div className="as-filter-row" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ position: "relative", minWidth: 260, flex: 1 }}>
              <Search size={14} color={colors.faint} style={{ position: "absolute", left: 10, top: 11 }} />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search companies..."
                style={{
                  width: "100%",
                  border: `1px solid ${colors.border}`,
                  borderRadius: 10,
                  padding: "10px 12px 10px 30px",
                  fontSize: 14,
                  outline: "none",
                }}
              />
            </div>
            <div style={{ color: colors.sub, fontSize: 14, display: "flex", gap: 20, flexWrap: "wrap", alignItems: "center" }}>
              <span>{totalCompanies} companies sourced</span>
              <span>{highPriorityCount} high-priority</span>
              <span>{researchedCount} researched</span>
              <span>{targetVerdictCount} target verdicts</span>
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
                  height: 36, padding: "0 12px", borderRadius: 10,
                  border: hasAdvancedFilter ? "1.5px solid #ffb995" : `1px solid ${colors.border}`,
                  background: hasAdvancedFilter ? "#f3fbe3" : colors.card,
                  color: hasAdvancedFilter ? "#4d7c0f" : colors.text,
                  fontSize: 13, fontWeight: 700, cursor: "pointer",
                  display: "inline-flex", alignItems: "center", gap: 6,
                }}
              >
                Advanced Filter{hasAdvancedFilter ? " •" : ""}
              </button>
              {hasAdvancedFilter && (
                <button
                  type="button"
                  disabled={downloadingFiltered}
                  onClick={async () => {
                    setDownloadingFiltered(true);
                    try {
                      const blob = await accountSourcingApi.exportCsv({
                        prospectsMin,
                        prospectsMax,
                      });
                      const url = URL.createObjectURL(blob);
                      const anchor = document.createElement("a");
                      anchor.href = url;
                      anchor.download = `sourced-companies-filtered-${new Date().toISOString().slice(0, 10)}.csv`;
                      anchor.click();
                      URL.revokeObjectURL(url);
                    } finally {
                      setDownloadingFiltered(false);
                    }
                  }}
                  style={{
                    height: 36, padding: "0 12px", borderRadius: 10,
                    border: "1px solid #6fae27", background: "#6fae27",
                    color: "#fff", fontSize: 13, fontWeight: 700, cursor: "pointer",
                    display: "inline-flex", alignItems: "center", gap: 6,
                    opacity: downloadingFiltered ? 0.7 : 1,
                  }}
                >
                  {downloadingFiltered ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                  Download filtered
                </button>
              )}
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <MultiSelectFilter
                values={tierFilter}
                onChange={setTierFilter}
                options={TIER_OPTIONS}
                label="ICP Tier"
                allLabel="All ICP tiers"
                minWidth={130}
              />
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 10, fontWeight: 700, color: "#7f8fa5", textTransform: "uppercase", letterSpacing: 0.5 }}>View</label>
                <select
                  value={ownerScope}
                  onChange={(event) => setOwnerScope(event.target.value === "mine" ? "mine" : "all")}
                  style={{
                    height: 42,
                    borderRadius: 12,
                    border: ownerScope === "mine" ? "1.5px solid #cfe89a" : "1px solid #d9e1ec",
                    background: ownerScope === "mine" ? "#f3fbe3" : "#fff",
                    padding: "0 32px 0 12px",
                    fontSize: 13,
                    color: "#1d2b3c",
                    outline: "none",
                    minWidth: 150,
                    cursor: "pointer",
                  }}
                >
                  <option value="all">All accounts</option>
                  <option value="mine">My accounts</option>
                </select>
              </div>
              {teamUsers.length > 0 && (
                <MultiSelectFilter
                  values={ownerFilter}
                  onChange={setOwnerFilter}
                  options={[
                    // Sentinel for "no owner" — backend maps "__unassigned__" to
                    // assigned_to_id IS NULL AND sdr_id IS NULL so reps can
                    // surface accounts that slipped through with no owner.
                    { value: "__unassigned__", label: "Unassigned" },
                    ...teamUsers.map((u) => ({ value: u.id, label: u.name || u.email })),
                  ]}
                  label="Owner"
                  allLabel="Owner: All"
                  minWidth={170}
                />
              )}
              <MultiSelectFilter
                values={dispositionFilter}
                onChange={setDispositionFilter}
                options={DISPOSITION_OPTIONS}
                label="Disposition"
                allLabel="All dispositions"
                minWidth={150}
              />
              <MultiSelectFilter
                values={statusFilter}
                onChange={setStatusFilter}
                options={ACCOUNT_STATUS_FILTER_OPTIONS}
                label="Status"
                allLabel="All statuses"
                minWidth={150}
              />
              <MultiSelectFilter
                values={laneFilter}
                onChange={setLaneFilter}
                options={OUTREACH_LANE_OPTIONS}
                label="Outreach Lane"
                allLabel="All lanes"
                minWidth={170}
              />
              <MultiSelectFilter
                values={journeyFilter}
                onChange={setJourneyFilter}
                options={JOURNEY_FILTER_OPTIONS}
                label="Journey Stage"
                allLabel="All journey stages"
                minWidth={175}
              />
              <select
                value={sortBy}
                onChange={(event) => setSortBy(event.target.value as AccountSortKey)}
                title="Sort the currently visible account page"
                style={{
                  height: 38,
                  minWidth: 190,
                  borderRadius: 10,
                  border: `1px solid ${colors.border}`,
                  background: colors.card,
                  color: colors.text,
                  padding: "0 12px",
                  fontSize: 13,
                  fontWeight: 700,
                  outline: "none",
                }}
              >
                {ACCOUNT_SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ color: colors.sub, fontSize: 13, fontWeight: 700 }}>
                {companyTotal === 0 ? "0 shown" : `${showingStart}-${showingEnd} of ${companyTotal}`}
              </span>
              <span style={{ color: colors.faint, fontSize: 12 }}>Page {page} of {Math.max(companyPages, 1)}</span>
              {hasFilters ? (
                <button
                  type="button"
                  onClick={() => {
                    setSearch("");
                    setOwnerScope("all");
                    setOwnerFilter([]);
                    setTierFilter([]);
                    setDispositionFilter([]);
                    setStatusFilter([]);
                    setLaneFilter([]);
                    setJourneyFilter([]);
                  }}
                  style={{
                    border: `1px solid ${colors.border}`,
                    background: colors.card,
                    color: colors.text,
                    borderRadius: 10,
                    padding: "10px 14px",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    fontWeight: 700,
                    cursor: "pointer",
                    fontSize: 13,
                  }}
                >
                  Reset filters
                </button>
              ) : null}
            </div>
          </div>
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
          <div style={{ display: "grid", gap: 8 }}>
            <CompanyTableHeader />
            {sortedCompanies.map((c) => (
              <CompanyCard key={c.id} company={c} onAssigned={() => load()} />
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
          </>
        ) : (
          <div style={{ display: "grid", gap: 12 }}>
            {batches.length === 0 ? (
              <div style={{ ...cardStyle, padding: 28, textAlign: "center", color: colors.faint }}>
                No imports yet.
              </div>
            ) : (
              batches.map((batch) => (
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
                      <button
                        type="button"
                        onClick={async () => {
                          const blob = await accountSourcingApi.exportCsv({ batchId: batch.id });
                          const url = URL.createObjectURL(blob);
                          const anchor = document.createElement("a");
                          anchor.href = url;
                          anchor.download = `${batch.filename.replace(/\s+/g, "-").toLowerCase()}-companies.csv`;
                          anchor.click();
                          URL.revokeObjectURL(url);
                        }}
                        style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 10, padding: "8px 12px", fontWeight: 700, cursor: "pointer" }}
                      >
                        Download companies
                      </button>
                      <button
                        type="button"
                        onClick={async () => {
                          const blob = await accountSourcingApi.exportContactsCsv({ batchId: batch.id });
                          const url = URL.createObjectURL(blob);
                          const anchor = document.createElement("a");
                          anchor.href = url;
                          anchor.download = `${batch.filename.replace(/\s+/g, "-").toLowerCase()}-contacts.csv`;
                          anchor.click();
                          URL.revokeObjectURL(url);
                        }}
                        style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 10, padding: "8px 12px", fontWeight: 700, cursor: "pointer" }}
                      >
                        Download contacts
                      </button>
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
                    {[
                      { label: "Status", value: batch.status.replace(/_/g, " "), hint: batch.progress_message || "Activity tracked automatically" },
                      { label: "Accounts", value: `${batch.created_companies}`, hint: `${batch.processed_rows}/${batch.total_rows} processed` },
                      { label: "Contacts", value: `${batch.contacts_found ?? 0}`, hint: "Relevant stakeholders saved" },
                      { label: "Verdicts", value: `${String((batch.verdict_summary || {}).target || 0)} target`, hint: String((batch.verdict_summary || {}).message || "No uploaded verdicts") },
                    ].map((item) => (
                      <div key={`${batch.id}-${item.label}`} style={{ border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 14px", background: "#fbfdff" }}>
                        <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>{item.label.toUpperCase()}</div>
                        <div style={{ marginTop: 6, color: colors.text, fontSize: 18, fontWeight: 800 }}>{item.value}</div>
                        <div style={{ marginTop: 4, color: colors.sub, fontSize: 12, lineHeight: 1.45 }}>{item.hint}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

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

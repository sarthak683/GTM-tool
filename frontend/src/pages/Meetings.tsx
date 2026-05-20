import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarDays, Check, ChevronDown, Filter, Plus, Search, X } from "lucide-react";
import { authApi, companiesApi, contactsApi, dealsApi, meetingsApi } from "../lib/api";
import TldvRecordingLink from "../components/meetings/TldvRecordingLink";
import { useAuth } from "../lib/AuthContext";
import type { Company, Contact, Deal, Meeting, User } from "../types";
import { formatOptionalDate, isValidDateValue } from "../lib/utils";

const MEETING_TYPES = ["discovery", "demo", "poc", "qbr", "other"];
const PAGE_SIZE = 25;
const DEVELOPER_EMAILS = new Set(["sarthak@beacon.li"]);

function isDeveloperUser(user?: Pick<User, "email" | "name"> | null) {
  if (!user) return false;
  const email = (user.email || "").trim().toLowerCase();
  const name = (user.name || "").trim().toLowerCase();
  return DEVELOPER_EMAILS.has(email) || name === "sarthak aitha";
}

function meetingTiming(meeting: Meeting): "upcoming" | "overdue" | "completed" | "cancelled" {
  if (meeting.status === "completed") return "completed";
  if (meeting.status === "cancelled") return "cancelled";
  if (!meeting.scheduled_at || !isValidDateValue(meeting.scheduled_at)) return "upcoming";
  return new Date(meeting.scheduled_at).getTime() < Date.now() ? "overdue" : "upcoming";
}

function timingLabel(meeting: Meeting): string {
  const timing = meetingTiming(meeting);
  if (timing === "completed") return "Completed";
  if (timing === "cancelled") return "Cancelled";
  if (!meeting.scheduled_at) return "No date set";
  if (!isValidDateValue(meeting.scheduled_at)) return "Invalid date";
  const diffHours = Math.round((new Date(meeting.scheduled_at).getTime() - Date.now()) / 3_600_000);
  if (timing === "overdue") {
    const overdueHours = Math.abs(diffHours);
    return overdueHours >= 24 ? `${Math.round(overdueHours / 24)}d overdue` : `${Math.max(overdueHours, 1)}h overdue`;
  }
  if (diffHours < 1) return "Starts soon";
  if (diffHours < 24) return `${diffHours}h away`;
  return `${Math.round(diffHours / 24)}d away`;
}

function timingTone(meeting: Meeting) {
  const timing = meetingTiming(meeting);
  if (timing === "overdue") return { bg: "#fdecec", color: "#b42336", border: "#f5c2c2" };
  if (timing === "completed") return { bg: "#ecf8f0", color: "#15803d", border: "#c7e8d3" };
  if (timing === "cancelled") return { bg: "#f1f5f9", color: "#64748b", border: "#e2e8f0" };
  return { bg: "#eef4ff", color: "#2948b9", border: "#c5d6ff" };
}

const styles: Record<string, CSSProperties> = {
  page: {
    display: "flex",
    flexDirection: "column",
    gap: 24,
    padding: "8px 2px 18px",
  },
  panel: {
    background: "#ffffff",
    border: "1px solid #d9e1ec",
    borderRadius: 22,
    boxShadow: "0 14px 36px rgba(15,23,42,0.05)",
  },
  toolbar: {
    padding: 18,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    flexWrap: "wrap",
  },
  chip: {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    padding: "7px 12px",
    borderRadius: 999,
    border: "1px solid #d8e4ef",
    background: "#f8fbff",
    color: "#38526b",
    fontSize: 12,
    fontWeight: 700,
  },
  buttonPrimary: {
    border: "none",
    background: "#1f6feb",
    color: "white",
    borderRadius: 12,
    padding: "10px 16px",
    fontSize: 13,
    fontWeight: 700,
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    cursor: "pointer",
    minHeight: 44,
  },
  table: {
    width: "100%",
    minWidth: 920,
    borderCollapse: "collapse",
  },
  th: {
    textAlign: "left",
    padding: "14px 20px",
    fontSize: 11,
    color: "#7f8fa5",
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    borderBottom: "1px solid #e8eef5",
    background: "#f9fbfe",
  },
  td: {
    padding: "14px 20px",
    borderBottom: "1px solid #edf2f8",
    fontSize: 13,
    color: "#30485f",
  },
  statusChip: {
    display: "inline-flex",
    alignItems: "center",
    padding: "4px 8px",
    borderRadius: 999,
    border: "1px solid #d7e2ee",
    background: "#f8fbff",
    fontSize: 11,
    fontWeight: 700,
    textTransform: "capitalize",
    color: "#4f657e",
  },
  modalOverlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(16, 24, 32, 0.3)",
    zIndex: 40,
  },
  modalWrap: {
    position: "fixed",
    inset: 0,
    zIndex: 50,
    display: "grid",
    placeItems: "center",
    padding: 16,
  },
  modal: {
    width: "100%",
    maxWidth: 760,
    background: "#ffffff",
    border: "1px solid #d9e1ec",
    borderRadius: 22,
    boxShadow: "0 18px 54px rgba(20, 46, 72, 0.2)",
    padding: 24,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  input: {
    height: 42,
    borderRadius: 12,
    border: "1px solid #d7e2ee",
    padding: "0 12px",
    fontSize: 14,
    color: "#25384d",
    width: "100%",
    boxSizing: "border-box",
  },
  secondaryButton: {
    border: "1px solid #d9e5f0",
    background: "#f5f9ff",
    color: "#45607a",
    borderRadius: 10,
    padding: "9px 14px",
    fontSize: 13,
    fontWeight: 700,
    cursor: "pointer",
  },
};

function MultiSelectDropdown({
  options,
  selected,
  onChange,
  placeholder,
}: {
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
  placeholder: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filtered = useMemo(
    () => query.trim() ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase())) : options,
    [options, query],
  );

  function toggle(value: string) {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  }

  const displayText =
    selected.length === 0 ? placeholder
    : selected.length === 1 ? (options.find((o) => o.value === selected[0])?.label ?? placeholder)
    : `${selected.length} selected`;

  const isActive = selected.length > 0;

  return (
    <div style={{ position: "relative", zIndex: open ? 200 : 1 }} ref={ref}>
      <button
        type="button"
        onClick={() => { setOpen((o) => !o); setQuery(""); }}
        style={{
          height: 36,
          borderRadius: 8,
          border: isActive ? "1px solid #b8cff7" : "1px solid #d7e2ee",
          background: isActive ? "#eef4ff" : "#fff",
          color: isActive ? "#2948b9" : "#25384d",
          fontSize: 13,
          fontWeight: 600,
          padding: "0 10px",
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis" }}>{displayText}</span>
        {isActive && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); onChange([]); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); onChange([]); } }}
            style={{ display: "flex", alignItems: "center", color: "#5878be" }}
          >
            <X size={12} />
          </span>
        )}
        <ChevronDown size={12} style={{ color: "#7a8ca0", transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s", flexShrink: 0 }} />
      </button>
      {open && (
        <div style={{
          position: "absolute",
          top: "calc(100% + 4px)",
          left: 0,
          zIndex: 1000,
          minWidth: 200,
          background: "#fff",
          border: "1px solid #dde8f4",
          borderRadius: 12,
          boxShadow: "0 8px 28px rgba(20,50,80,0.12)",
          overflow: "hidden",
        }}>
          <div style={{ padding: "6px 8px", borderBottom: "1px solid #edf2f8", display: "flex", alignItems: "center", gap: 6 }}>
            <Search size={12} style={{ color: "#94a8be", flexShrink: 0 }} />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search..."
              style={{ flex: 1, border: "none", outline: "none", fontSize: 13, color: "#203244", background: "transparent" }}
            />
          </div>
          <div style={{ maxHeight: 200, overflowY: "auto" }}>
            {filtered.length === 0 ? (
              <p style={{ margin: 0, padding: "10px 12px", fontSize: 12, color: "#94a8be" }}>No results</p>
            ) : filtered.map((opt) => {
              const isSel = selected.includes(opt.value);
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggle(opt.value)}
                  style={{
                    width: "100%", padding: "9px 12px", display: "flex", alignItems: "center", gap: 8,
                    border: "none", background: isSel ? "#f0f5ff" : "transparent", cursor: "pointer",
                    textAlign: "left", fontSize: 13, fontWeight: isSel ? 700 : 500, color: isSel ? "#2948b9" : "#2e4260",
                  }}
                >
                  <span style={{
                    width: 16, height: 16, borderRadius: 5, border: isSel ? "none" : "1.5px solid #c8d8ea",
                    background: isSel ? "#3f5fd4" : "#fff", display: "grid", placeItems: "center", flexShrink: 0,
                  }}>
                    {isSel && <Check size={10} style={{ color: "#fff" }} />}
                  </span>
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Meetings() {
  const { isAdmin, user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [availableContacts, setAvailableContacts] = useState<Contact[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>(["scheduled"]);
  const [typeFilter, setTypeFilter] = useState<string[]>([]);
  // Default to "my upcoming" — current user's scheduled meetings. Users can
  // still clear the filter to see everything. If the default ("me") returns
  // zero results, we auto-fall-back to all reps once per mount so users
  // without assigned meetings don't see a blank page.
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>(user?.id ? [user.id] : []);
  const [autoFallbackApplied, setAutoFallbackApplied] = useState(false);
  // Internal-only meetings (every attendee from an internal domain, e.g. beacon.li)
  // are hidden by default.  Toggle in the filter bar to include them.
  const [showInternal, setShowInternal] = useState<boolean>(false);
  // Advanced filter disclosure: most reps only need search/status/assignee/internal.
  // Type, link-state, and sync-age are hidden behind a "More filters" button.
  const [showAdvancedFilters, setShowAdvancedFilters] = useState<boolean>(false);
  const [linkFilter, setLinkFilter] = useState<string[]>([]);
  // Text search across title, linked company name, and attendee list.
  // Debounced via a separate committed value so we don't hit the API on
  // every keystroke.
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  // "Recently synced" shortcut — values are hours-ago windows. "" = off.
  const [recentSyncHours, setRecentSyncHours] = useState<"" | "1" | "24" | "168">("");
  const [page, setPage] = useState(1);
  const [totalMeetings, setTotalMeetings] = useState(0);
  const [meetingPages, setMeetingPages] = useState(1);
  const [form, setForm] = useState({
    title: "",
    company_id: "",
    deal_id: "",
    meeting_type: "discovery",
    scheduled_at: "",
    attendee_ids: [] as string[],
  });

  useEffect(() => {
    if (!user?.id) return;
    setAssigneeFilter((current) => (current.length === 0 ? [user.id] : current));
  }, [user?.id]);

  useEffect(() => {
    if (searchParams.get("new") !== "meeting") return;
    setShowModal(true);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("new");
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  const loadData = async () => {
    setLoading(true);
    try {
      // Translate "recently synced" shortcut into an ISO timestamp the
      // backend can compare against Meeting.synced_at.
      const syncedAfterIso = recentSyncHours
        ? new Date(Date.now() - parseInt(recentSyncHours, 10) * 3600_000).toISOString()
        : undefined;
      const apiStatusFilter = statusFilter.includes("overdue")
        ? Array.from(new Set(statusFilter.filter((status) => status !== "overdue").concat(["scheduled"])))
        : statusFilter;
      const temporalStatusFilter = Array.from(new Set(statusFilter.flatMap((status) => {
        if (status === "scheduled") return ["upcoming"];
        if (status === "overdue") return ["overdue"];
        return [];
      })));

      const pageResp = await meetingsApi.listPaginated({
        skip: (page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
        status: apiStatusFilter,
        temporalStatus: temporalStatusFilter,
        meetingType: typeFilter,
        assigneeId: assigneeFilter,
        linkState: linkFilter,
        q: debouncedSearch || undefined,
        syncedAfter: syncedAfterIso,
        internalScope: showInternal ? "only" : "exclude",
      });

      const ms = pageResp.items;
      setMeetings(ms);
      setTotalMeetings(pageResp.total);
      setMeetingPages(pageResp.pages);

      // Empty-set fallback: if the default "my upcoming" scope returned
      // zero meetings, broaden to all reps so the user sees something.
      // Only auto-applies once per mount; users who explicitly re-filter
      // to themselves later keep that choice.
      if (
        !autoFallbackApplied
        && pageResp.total === 0
        && assigneeFilter.length === 1
        && user?.id
        && assigneeFilter[0] === user.id
        && !debouncedSearch
        && statusFilter.length === 1
        && statusFilter[0] === "scheduled"
      ) {
        setAutoFallbackApplied(true);
        setAssigneeFilter([]);
        return;
      }

      const companyIds = Array.from(new Set(ms.map((meeting) => meeting.company_id).filter(Boolean))) as string[];
      const dealIds = Array.from(new Set(ms.map((meeting) => meeting.deal_id).filter(Boolean))) as string[];

      // Load the team roster unconditionally so the "Added by" column can
      // resolve synced_by_user_id → user name for every rep, not just admins.
      // The non-admin `listUsers` endpoint returns the public subset.
      const rosterPromise: Promise<User[]> = isAdmin
        ? authApi.listAllUsers().catch(() => [])
        : authApi.listUsers().catch(() => []);
      const [companyResults, dealResults, us] = await Promise.all([
        Promise.all(companyIds.map((id) => companiesApi.get(id).catch(() => null))),
        Promise.all(dealIds.map((id) => dealsApi.get(id).catch(() => null))),
        rosterPromise,
      ]);

      setCompanies(companyResults.filter((item): item is Company => Boolean(item)));
      setDeals(dealResults.filter((item): item is Deal => Boolean(item)));
      setUsers(us);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [page, statusFilter, typeFilter, assigneeFilter, linkFilter, debouncedSearch, recentSyncHours, showInternal]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, typeFilter, assigneeFilter, linkFilter, debouncedSearch, recentSyncHours, showInternal]);

  // Debounce the search input 250ms so typing doesn't hammer the API.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchInput.trim()), 250);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    if (!showModal || !form.company_id) {
      setAvailableContacts([]);
      return;
    }
    contactsApi.list(0, 50, form.company_id).then(setAvailableContacts).catch(() => setAvailableContacts([]));
  }, [showModal, form.company_id]);

  const companyName = useMemo(() => Object.fromEntries(companies.map((c) => [c.id, c.name])), [companies]);
  const companyDeals = useMemo(() => {
    return deals.filter((d) => !form.company_id || d.company_id === form.company_id);
  }, [deals, form.company_id]);

  const hideDeveloper = isDeveloperUser(user);
  const visibleUsers = useMemo(
    () => (hideDeveloper ? users.filter((teamUser) => !isDeveloperUser(teamUser)) : users),
    [hideDeveloper, users],
  );

  const hasFilters = statusFilter.length > 0 || typeFilter.length > 0 || assigneeFilter.length > 0 || linkFilter.length > 0 || debouncedSearch.length > 0 || !!recentSyncHours || showInternal;
  const hasAdvancedFilters = typeFilter.length > 0 || linkFilter.length > 0 || !!recentSyncHours;

  const handleCreate = async () => {
    if (!form.title.trim()) {
      setError("Meeting title is required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await meetingsApi.create({
        title: form.title.trim(),
        company_id: form.company_id || undefined,
        deal_id: form.deal_id || undefined,
        meeting_type: form.meeting_type,
        scheduled_at: form.scheduled_at ? new Date(form.scheduled_at).toISOString() : undefined,
        attendees: availableContacts
          .filter((contact) => form.attendee_ids.includes(contact.id))
          .map((contact) => ({
            contact_id: contact.id,
            name: `${contact.first_name} ${contact.last_name}`.trim(),
            title: contact.title,
            email: contact.email,
          })),
      });
      setShowModal(false);
      setForm({ title: "", company_id: "", deal_id: "", meeting_type: "discovery", scheduled_at: "", attendee_ids: [] });
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create meeting");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={styles.page}>
      <style>{`
        @media (max-width: 768px) {
          .meetings-page-toolbar {
            flex-direction: column !important;
            gap: 10px !important;
            padding: 12px !important;
          }
          .meetings-filter-bar {
            flex-direction: column !important;
            gap: 8px !important;
            padding: 10px !important;
          }
          .meetings-filter-input {
            min-width: 0 !important;
            max-width: none !important;
            flex: 1 1 auto !important;
          }
          .meetings-table-wrap {
            overflow-x: auto !important;
            -webkit-overflow-scrolling: touch !important;
          }
        }
      `}</style>
      <div className="meetings-page-toolbar" style={{ ...styles.panel, ...styles.toolbar }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={styles.chip}>
              <span style={{ fontWeight: 800 }}>{totalMeetings}</span>
              Meetings
            </span>
            {hasFilters && (
              <span style={{ ...styles.chip, background: "#fff7ed", color: "#b94a20", borderColor: "#ffd3be" }}>
                {totalMeetings} shown
              </span>
            )}
          </div>
          <p style={{ margin: 0, fontSize: 12, color: "#7a8ea4", maxWidth: 560 }}>
            Log discovery calls, demos, and QBRs here. Beacon sends a pre-meeting intel brief to the assigned rep 12 hours before each scheduled meeting — covering account context, prior meeting notes, and email threads.
          </p>
        </div>
        <button style={styles.buttonPrimary} onClick={() => setShowModal(true)}>
          <Plus size={14} />
          New Meeting
        </button>
      </div>

      {/* Filter bar — primary controls always visible, advanced filters hidden behind
          a "More filters" disclosure.  Most reps only need search / status / assignee
          / show-internal; keeping the bar dense was visual noise we lived with too
          long. */}
      <div className="meetings-filter-bar" style={{ ...styles.panel, position: "relative", zIndex: 20, overflow: "visible", padding: "14px 18px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          {/* Free-text search: matches meeting title, linked company name,
              and any text inside the attendees JSON (names + emails). */}
          <div className="meetings-filter-input" style={{ position: "relative", minWidth: 280, flex: "1 1 280px", maxWidth: 380 }}>
            <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#7a8ea4" }} />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search title, company, attendee…"
              style={{
                width: "100%",
                boxSizing: "border-box",
                height: 36,
                padding: "0 32px 0 30px",
                borderRadius: 10,
                border: "1px solid #d5e3ef",
                fontSize: 13,
                color: "#0f2744",
                background: "#fff",
                outline: "none",
              }}
            />
            {searchInput && (
              <button
                type="button"
                onClick={() => setSearchInput("")}
                aria-label="Clear search"
                style={{
                  position: "absolute",
                  right: 6,
                  top: "50%",
                  transform: "translateY(-50%)",
                  border: "none",
                  background: "transparent",
                  color: "#7a8ea4",
                  cursor: "pointer",
                  padding: 2,
                  display: "inline-flex",
                }}
              >
                <X size={14} />
              </button>
            )}
          </div>
          <MultiSelectDropdown
            options={[
              { value: "scheduled", label: "Upcoming" },
              { value: "overdue", label: "Overdue" },
              { value: "completed", label: "Completed" },
              { value: "cancelled", label: "Cancelled" },
            ]}
            selected={statusFilter}
            onChange={setStatusFilter}
            placeholder="All statuses"
          />
          {isAdmin && visibleUsers.length > 0 && (
            <MultiSelectDropdown
              options={visibleUsers.map((u) => ({ value: u.id, label: u.name }))}
              selected={assigneeFilter}
              onChange={setAssigneeFilter}
              placeholder="All reps"
            />
          )}
          <button
            type="button"
            onClick={() => setShowInternal((v) => !v)}
            title={showInternal ? "Showing only internal meetings; click to return to customer meetings" : "Show only internal meetings"}
            style={{
              height: 36,
              padding: "0 12px",
              borderRadius: 8,
              border: showInternal ? "1px solid #c5b1ff" : "1px solid #d5e3ef",
              background: showInternal ? "#efebff" : "#fff",
              color: showInternal ? "#5b3bd4" : "#55657a",
              fontSize: 12.5,
              fontWeight: 700,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span style={{ width: 8, height: 8, borderRadius: 999, background: showInternal ? "#7c3aed" : "#b8c4d4" }} />
            {showInternal ? "Internal only" : "Internal"}
          </button>

          <div style={{ flex: 1 }} />

          <button
            type="button"
            onClick={() => setShowAdvancedFilters((v) => !v)}
            style={{
              height: 36,
              padding: "0 12px",
              borderRadius: 8,
              border: hasAdvancedFilters ? "1px solid #c5d6ff" : "1px solid #d5e3ef",
              background: hasAdvancedFilters ? "#eef4ff" : "#fff",
              color: hasAdvancedFilters ? "#3555c4" : "#55657a",
              fontSize: 12.5,
              fontWeight: 700,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <Filter size={13} />
            {showAdvancedFilters ? "Hide filters" : hasAdvancedFilters ? `Filters (${(typeFilter.length ? 1 : 0) + (linkFilter.length ? 1 : 0) + (recentSyncHours ? 1 : 0)})` : "More filters"}
          </button>
          {hasFilters && (
            <button
              type="button"
              onClick={() => { setStatusFilter([]); setTypeFilter([]); setAssigneeFilter([]); setLinkFilter([]); setRecentSyncHours(""); setSearchInput(""); setShowInternal(false); }}
              style={{ height: 36, padding: "0 10px", borderRadius: 8, border: "1px solid #ffd0d8", background: "#fff5f7", color: "#c55656", fontSize: 12, fontWeight: 700, cursor: "pointer" }}
            >
              Reset
            </button>
          )}
        </div>

        {/* Advanced filter row — hidden by default; kept in DOM so state is
            preserved across toggles.  Displayed as a subtle second tier so it
            reads as a disclosure, not a primary control. */}
        {showAdvancedFilters && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", paddingTop: 10, borderTop: "1px dashed #e2eaf3" }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: "#7a8ea4", textTransform: "uppercase", letterSpacing: "0.06em" }}>Advanced</span>
            <MultiSelectDropdown
              options={MEETING_TYPES.map((t) => ({ value: t, label: t.replace(/_/g, " ") }))}
              selected={typeFilter}
              onChange={setTypeFilter}
              placeholder="All types"
            />
            <MultiSelectDropdown
              options={[
                { value: "linked", label: "Linked" },
                { value: "needs_review", label: "Needs review" },
              ]}
              selected={linkFilter}
              onChange={setLinkFilter}
              placeholder="All links"
            />
            <select
              value={recentSyncHours}
              onChange={(e) => setRecentSyncHours(e.target.value as "" | "1" | "24" | "168")}
              style={{
                height: 36,
                padding: "0 28px 0 10px",
                borderRadius: 8,
                border: "1px solid #d5e3ef",
                fontSize: 12.5,
                fontWeight: 600,
                color: recentSyncHours ? "#175089" : "#55657a",
                background: recentSyncHours ? "#eef5ff" : "#fff",
                cursor: "pointer",
                outline: "none",
              }}
            >
              <option value="">Any import time</option>
              <option value="1">Imported in last hour</option>
              <option value="24">Imported in last 24h</option>
              <option value="168">Imported in last 7d</option>
            </select>
          </div>
        )}
      </div>

      {loading ? (
        <div style={{ ...styles.panel, padding: "46px 20px", textAlign: "center", color: "#7a8ea4", fontSize: 14 }}>
          Loading meetings...
        </div>
      ) : (
        <div style={{ ...styles.panel, overflow: "hidden" }}>
          <div className="meetings-table-wrap" style={{ overflowX: "auto" }}>
            <table className="crm-table" style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Title</th>
                  <th style={styles.th}>Company</th>
                  <th style={styles.th}>Type</th>
                  <th style={styles.th}>Scheduled</th>
                  <th style={styles.th}>Status</th>
                  <th style={styles.th}>Prep</th>
                  <th style={styles.th}>Added by</th>
                  <th style={styles.th}>Score</th>
                </tr>
              </thead>
              <tbody>
                {meetings.map((m) => {
                  const timingToneStyle = timingTone(m);
                  const hasBrief = Boolean(m.research_data || m.pre_brief || m.demo_strategy);
                  const prepBlocked = !m.company_id || !m.deal_id;
                  const needsReviewReason =
                    !m.company_id && !m.deal_id
                      ? "Missing company and deal link. Open the meeting and use Re-link before trusting prep."
                      : !m.company_id
                        ? "Missing company link. Open the meeting and use Re-link."
                        : !m.deal_id
                          ? "Missing deal link. Open the meeting and use Re-link."
                          : "";
                  const invalidSchedule = Boolean(m.scheduled_at && !isValidDateValue(m.scheduled_at));
                  return (
                  <tr key={m.id} style={{ borderLeft: meetingTiming(m) === "overdue" ? "3px solid #dc2626" : "3px solid transparent" }}>
                    <td data-label="Title" style={styles.td}>
                      <div style={{ display: "grid", gap: 6 }}>
                        <Link
                          to={`/meetings/${m.id}`}
                          style={{ fontWeight: 700, color: "#24364b", textDecoration: "none" }}
                        >
                          {m.title}
                        </Link>
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                          {m.is_internal && (
                            <span
                              title="Every attendee is from an internal domain. Internal meetings are hidden by default; the Internal filter shows only them."
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                padding: "3px 8px",
                                borderRadius: 999,
                                border: "1px solid #d7ccff",
                                background: "#f1ecff",
                                color: "#5b3bd4",
                                fontSize: 10,
                                fontWeight: 700,
                                textTransform: "uppercase",
                                letterSpacing: "0.05em",
                              }}
                            >
                              Internal
                            </span>
                          )}
                          {!m.is_internal && (!m.company_id || !m.deal_id) && (
                            <span
                              title={needsReviewReason}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                padding: "3px 8px",
                                borderRadius: 999,
                                border: "1px solid #ffd8b4",
                                background: "#fff6ec",
                                color: "#b25a1d",
                                fontSize: 10,
                                fontWeight: 700,
                                textTransform: "uppercase",
                                letterSpacing: "0.05em",
                              }}
                            >
                              Needs review
                            </span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td data-label="Company" style={styles.td}>
                      {m.company_id ? (
                        <span style={{ fontWeight: 600, color: "#1f3144" }}>{companyName[m.company_id] ?? "—"}</span>
                      ) : m.is_internal ? (
                        <span style={{ color: "#8a95a6", fontSize: 12.5 }}>—</span>
                      ) : (
                        <Link
                          to={`/meetings/${m.id}`}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            padding: "4px 9px",
                            borderRadius: 8,
                            border: "1px solid #ffd8b4",
                            background: "#fff6ec",
                            color: "#b25a1d",
                            fontSize: 11.5,
                            fontWeight: 700,
                            textDecoration: "none",
                          }}
                          title="Open meeting to link it to a company"
                        >
                          + Link company
                        </Link>
                      )}
                    </td>
                    <td data-label="Type" style={{ ...styles.td, textTransform: "capitalize" }}>{m.meeting_type.replace(/_/g, " ")}</td>
                    <td data-label="Added by" style={styles.td}>
                      {(() => {
                        // Map the meeting's sync metadata to a compact
                        // "who added this" cell: source label, adder name
                        // (resolved from users list), and a relative time.
                        const source = (m.external_source || "manual").toLowerCase();
                        const sourceLabel: Record<string, string> = {
                          google_calendar: "Google Calendar",
                          manual: "Manual entry",
                          tldv: "tl;dv",
                          fireflies: "Fireflies",
                        };
                        const sourceTone: Record<string, { bg: string; fg: string; border: string }> = {
                          google_calendar: { bg: "#eef5ff", fg: "#1d4ed8", border: "#bfdbfe" },
                          manual:          { bg: "#f1f5f9", fg: "#475569", border: "#cbd5e1" },
                          tldv:            { bg: "#faf5ff", fg: "#7c3aed", border: "#e9d5ff" },
                          fireflies:       { bg: "#ecfdf5", fg: "#047857", border: "#a7f3d0" },
                        };
                        const tone = sourceTone[source] ?? sourceTone.manual;
                        const label = sourceLabel[source] ?? source;
                        const syncedByUser = m.synced_by_user_id
                          ? users.find((u) => u.id === m.synced_by_user_id)
                          : undefined;
                        const adder = syncedByUser?.name;
                        const adderEmail = syncedByUser?.email;
                        const when = m.synced_at ? new Date(m.synced_at) : null;
                        const whenLabel = when
                          ? (() => {
                              const diffMs = Date.now() - when.getTime();
                              const mins = Math.max(0, Math.round(diffMs / 60_000));
                              if (mins < 60) return mins <= 1 ? "just now" : `${mins}m ago`;
                              const hrs = Math.round(mins / 60);
                              if (hrs < 24) return `${hrs}h ago`;
                              const days = Math.round(hrs / 24);
                              return `${days}d ago`;
                            })()
                          : null;
                        return (
                          <div style={{ display: "grid", gap: 3 }}>
                            <span style={{ display: "inline-flex", alignItems: "center", width: "fit-content", padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 700, background: tone.bg, color: tone.fg, border: `1px solid ${tone.border}` }}>
                              {label}
                            </span>
                            {adder && (
                              <span style={{ fontSize: 11.5, color: "#546679", lineHeight: 1.45 }}>
                                by {adder}
                                {adderEmail ? (
                                  <span style={{ color: "#8aa0b6" }}> · {adderEmail}</span>
                                ) : null}
                              </span>
                            )}
                            {whenLabel && (
                              <span style={{ fontSize: 11, color: "#94a3b8" }}>{whenLabel}</span>
                            )}
                          </div>
                        );
                      })()}
                    </td>
                    <td data-label="Score" style={styles.td}>{m.meeting_score ?? "-"}</td>
                  </tr>
                );
                })}
                {meetings.length === 0 && (
                  <tr>
                    <td colSpan={8} style={{ ...styles.td, textAlign: "center", color: "#7a8ea4", padding: "48px 12px" }}>
                      <div style={{ fontSize: 14, fontWeight: 700, color: "#25384d", marginBottom: 6 }}>
                        {totalMeetings === 0 ? "No meetings scheduled" : "No meetings match these filters"}
                      </div>
                      <div style={{ fontSize: 12, color: "#7a8ea4", maxWidth: 420, margin: "0 auto" }}>
                        {totalMeetings === 0
                          ? "Create a meeting and link it to a deal. Beacon will automatically generate a pre-meeting intel brief and send it to the assigned rep 12 hours before the call."
                          : "Try adjusting the filters above to see more results."}
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {meetingPages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <p style={{ margin: 0, fontSize: 12, color: "#7a8ea4" }}>
            Page {page} of {meetingPages}
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              style={{ height: 36, padding: "0 12px", borderRadius: 10, border: "1px solid #d7e2ee", background: page <= 1 ? "#f7f9fc" : "#fff", color: page <= 1 ? "#94a8be" : "#25384d", cursor: page <= 1 ? "not-allowed" : "pointer", fontSize: 12, fontWeight: 700 }}
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page >= meetingPages}
              onClick={() => setPage((current) => Math.min(meetingPages, current + 1))}
              style={{ height: 36, padding: "0 12px", borderRadius: 10, border: "1px solid #d7e2ee", background: page >= meetingPages ? "#f7f9fc" : "#fff", color: page >= meetingPages ? "#94a8be" : "#25384d", cursor: page >= meetingPages ? "not-allowed" : "pointer", fontSize: 12, fontWeight: 700 }}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {showModal && (
        <>
          <div style={styles.modalOverlay} onClick={() => setShowModal(false)} />
          <div style={styles.modalWrap}>
            <div style={styles.modal}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <h3 style={{ margin: 0, fontSize: 18, fontWeight: 800, color: "#25384d" }}>Create Meeting</h3>
                <button
                  onClick={() => setShowModal(false)}
                  style={{ background: "transparent", border: "none", color: "#7a8ea4", cursor: "pointer" }}
                >
                  <X size={18} />
                </button>
              </div>

              <div style={{ display: "grid", gap: 10 }}>
                <input
                  style={styles.input}
                  placeholder="Meeting title"
                  value={form.title}
                  onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                />

                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
                  <select
                    style={styles.input}
                    value={form.company_id}
                    onChange={(e) => setForm((f) => ({ ...f, company_id: e.target.value, deal_id: "", attendee_ids: [] }))}
                  >
                    <option value="">Select company (optional)</option>
                    {companies.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                  <select
                    style={styles.input}
                    value={form.deal_id}
                    onChange={(e) => setForm((f) => ({ ...f, deal_id: e.target.value }))}
                  >
                    <option value="">Select deal (optional)</option>
                    {companyDeals.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
                  <select
                    style={styles.input}
                    value={form.meeting_type}
                    onChange={(e) => setForm((f) => ({ ...f, meeting_type: e.target.value }))}
                  >
                    {MEETING_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  <label
                    style={{
                      ...styles.input,
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      color: "#6f8399",
                    }}
                  >
                    <CalendarDays size={14} />
                    <input
                      type="datetime-local"
                      style={{ border: "none", outline: "none", width: "100%", color: "#25384d", background: "transparent" }}
                      value={form.scheduled_at}
                      onChange={(e) => setForm((f) => ({ ...f, scheduled_at: e.target.value }))}
                    />
                  </label>
                </div>

                {form.company_id && (
                  <div style={{ border: "1px solid #d7e2ee", borderRadius: 12, padding: 12, display: "grid", gap: 8 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                      <p style={{ margin: 0, fontSize: 12, fontWeight: 800, color: "#6f8399", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                        Attendees
                      </p>
                      <span style={{ fontSize: 12, color: "#7a8ea4" }}>
                        {form.attendee_ids.length} selected
                      </span>
                    </div>
                    {availableContacts.length === 0 ? (
                      <p style={{ margin: 0, fontSize: 13, color: "#7a8ea4" }}>
                        No discovered contacts yet for this company. You can still create the meeting now and add contacts later.
                      </p>
                    ) : (
                      <div style={{ display: "grid", gap: 8, maxHeight: 180, overflowY: "auto" }}>
                        {availableContacts.map((contact) => {
                          const checked = form.attendee_ids.includes(contact.id);
                          return (
                            <label key={contact.id} style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer" }}>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => setForm((f) => ({
                                  ...f,
                                  attendee_ids: checked
                                    ? f.attendee_ids.filter((id) => id !== contact.id)
                                    : [...f.attendee_ids, contact.id],
                                }))}
                              />
                              <div>
                                <div style={{ fontSize: 13, fontWeight: 700, color: "#25384d" }}>
                                  {contact.first_name} {contact.last_name}
                                </div>
                                <div style={{ fontSize: 12, color: "#6f8399" }}>
                                  {contact.title || contact.persona || contact.persona_type || "Stakeholder"}
                                </div>
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {error && <p style={{ margin: 0, fontSize: 12, color: "#b94a24", fontWeight: 700 }}>{error}</p>}

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button style={styles.secondaryButton} onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button style={styles.buttonPrimary} onClick={handleCreate} disabled={saving}>
                  {saving ? "Creating..." : "Create Meeting"}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";

import type { DateRangeValue } from "../../components/filters/DateRangeFilter";
import { canonicalTimezone } from "./timezones";
import { parseSearchParamList } from "./formatters";
import { reduceViewMode, type ViewMode } from "../../lib/viewMode";


export type ProspectSortKey = "recent" | "name_asc" | "name_desc" | "company_asc" | "company_desc";

export const PROSPECT_SORT_OPTIONS: Array<{ value: ProspectSortKey; label: string }> = [
  { value: "recent", label: "Newest first" },
  { value: "name_asc", label: "Name A → Z" },
  { value: "name_desc", label: "Name Z → A" },
  { value: "company_asc", label: "Company A → Z" },
  { value: "company_desc", label: "Company Z → A" },
];

export function sortToApi(s: ProspectSortKey): { sortBy?: "name" | "company"; sortDir?: "asc" | "desc" } {
  if (s === "name_asc") return { sortBy: "name", sortDir: "asc" };
  if (s === "name_desc") return { sortBy: "name", sortDir: "desc" };
  if (s === "company_asc") return { sortBy: "company", sortDir: "asc" };
  if (s === "company_desc") return { sortBy: "company", sortDir: "desc" };
  return {};
}

export interface ProspectFilters {
  viewMode: ViewMode;
  search: string;
  searchScope: string;
  searchMatch: "contains" | "exact";
  prospectSort: ProspectSortKey;
  personaFilter: string[];
  sequenceFilter: string[];
  accountStatusFilter: string[];
  callDispositionFilter: string[];
  linkedinStatusFilter: string[];
  callOutcomeColorFilter: string[];
  emailOutcomeColorFilter: string[];
  callAttemptsBucketFilter: string[];
  followupCountMin: number | null;
  followupCountMax: number | null;
  nextFollowupRange: DateRangeValue;
  callLastRange: DateRangeValue;
  emailFilter: string[];
  ownerScope: "all" | "mine";
  aeFilter: string[];
  sdrFilter: string[];
  ownerFilter: string[];
  timezoneFilter: string[];
  // Last Touch filter: which channel (call/email/linkedin) + which rep(s) did
  // the MOST RECENT activity of that channel. Both must be set to filter — a
  // rep alone with no channel picked is ambiguous (which of the 3 columns?).
  lastTouchType: "" | "call" | "email" | "linkedin";
  lastTouchRepFilter: string[];
  companyFilter: string;
  page: number;
}

interface FilterSpecEntry<T> {
  params: readonly string[];
  read: (params: URLSearchParams) => T;
  write: (params: URLSearchParams, value: T) => void;
}

const setOrDelete = (params: URLSearchParams, key: string, value: string | null) => {
  if (value) params.set(key, value);
  else params.delete(key);
};

const scalar = <T extends string>(
  param: string,
  defaultValue: T,
  parse?: (raw: string) => T,
  persist?: (value: T) => boolean,
): FilterSpecEntry<T> => ({
  params: [param],
  read: (params) => {
    const raw = params.get(param);
    return raw == null ? defaultValue : parse ? parse(raw) : raw as T;
  },
  write: (params, value) => setOrDelete(
    params,
    param,
    (persist ? persist(value) : value !== defaultValue) ? value : null,
  ),
});

const list = (param: string, map?: (value: string) => string): FilterSpecEntry<string[]> => ({
  params: [param],
  read: (params) => parseSearchParamList(params.get(param)).map(map ?? ((value) => value)),
  write: (params, value) => setOrDelete(params, param, value.length ? value.join(",") : null),
});

const count = (param: string): FilterSpecEntry<number | null> => ({
  params: [param],
  read: (params) => {
    const raw = params.get(param);
    if (raw == null || raw.trim() === "") return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
  },
  write: (params, value) => setOrDelete(params, param, value == null ? null : String(value)),
});

const range = (fromParam: string, toParam: string): FilterSpecEntry<DateRangeValue> => ({
  params: [fromParam, toParam],
  read: (params) => ({ from: params.get(fromParam) ?? "", to: params.get(toParam) ?? "" }),
  write: (params, value) => {
    setOrDelete(params, fromParam, value.from || null);
    setOrDelete(params, toParam, value.to || null);
  },
});

// One declarative row per filter. Adding a filter becomes one line here —
// not a useState plus a hydration branch plus a serialiser branch plus a
// clause in the "is anything active?" test.
export const FILTER_SPEC: { [K in keyof ProspectFilters]: FilterSpecEntry<ProspectFilters[K]> } = {
  // The current table remains the default. `?view=board` is the only alternate
  // so existing bookmarks and bare-path navigation keep today's layout.
  viewMode: scalar<ViewMode>("view", "table", (raw) =>
    reduceViewMode("table", { type: "hydrate", value: raw, defaultMode: "table" })),
  search: {
    params: ["q"],
    read: (params) => params.get("q") ?? "",
    write: (params, value) => setOrDelete(params, "q", value.trim() || null),
  },
  searchScope: scalar<string>("qf", "all"),
  // Match mode for scoped/bulk search: "contains" treats each pasted entry as
  // a substring; "exact" requires whole-cell equality (case-insensitive).
  // URL-persisted as `qm`; only sent when scope !== "all".
  searchMatch: scalar("qm", "contains", (raw) => raw === "exact" ? "exact" : "contains"),
  // Explicit sort. Server-side so it covers the full dataset, not just the
  // visible page. URL-persisted as `sb`/`sd` so deep-linked alphabetised
  // views survive navigation.
  prospectSort: scalar("sb", "recent", (raw) =>
    PROSPECT_SORT_OPTIONS.some((option) => option.value === raw) ? raw as ProspectSortKey : "recent"),
  // Layout reverted to the pre-97450f2 toolbar (no "More ⋯" overflow menu), but
  // the filter still restores from the URL — that is the shareable-filter-link
  // behaviour from d31d73d, which is function rather than layout.
  personaFilter: list("pe"),
  sequenceFilter: list("seq"),
  // ACCOUNT-status filter. Server rule: with nothing selected, prospects of
  // disabled (not_a_fit/dnd) accounts are hidden; explicitly selecting a
  // disabled status shows them (reviewing parked accounts is legitimate).
  accountStatusFilter: list("acct"),
  callDispositionFilter: list("call"),
  linkedinStatusFilter: list("li"),
  // Progress-dot color filters. Map 1:1 to the dot colors rendered by
  // `ProgressCell`. URL keys: `cc` (call color), `ec` (email color), `ca`
  // (call attempts bucket). The backend translates colors to disposition /
  // sequence_status / count buckets — see app/repositories/contact.py.
  callOutcomeColorFilter: list("cc"),
  emailOutcomeColorFilter: list("ec"),
  callAttemptsBucketFilter: list("ca"),
  // Follow-up count range (calls logged). URL keys: `fcmin` / `fcmax`. Either
  // bound may be null (open-ended). Backend maps these to call_attempt_min/max.
  followupCountMin: count("fcmin"),
  followupCountMax: count("fcmax"),
  // Date-range filters. `nextFollowupRange` filters on the rep-scheduled
  // callback (next_followup_at); `callLastRange` on the last call (call_last_at).
  // Values are `YYYY-MM-DD`; "" means unbounded. URL keys: nfa/nfb, cla/clb.
  nextFollowupRange: range("nfa", "nfb"),
  callLastRange: range("cla", "clb"),
  // Email-state filter (has/missing/verified/unverified). Server-side via
  // ContactFilters.email_state; URL key `em`.
  emailFilter: list("em"),
  // SDRs only ever see their own prospects — force "mine" on load regardless
  // of any persisted ?owner= param.
  ownerScope: scalar("owner", "all", (raw) => raw === "mine" ? "mine" : "all"),
  aeFilter: list("ae"),
  sdrFilter: list("sdr"),
  // Owner filter — multi-select that matches AE OR SDR ownership for any
  // selected user. Different from ownerScope (binary "mine vs all") and from
  // aeFilter/sdrFilter (role-specific). Sent to backend via owner_id +
  // scope_any_match=true so a single user_id matches contacts they own as
  // either AE or SDR.
  ownerFilter: list("own"),
  // Timezone filter values are canonical IANA zones. Legacy short labels in
  // saved URLs are normalized on load, while API requests expand each calling
  // region to every compatible canonical zone and historic abbreviation.
  timezoneFilter: list("tz", canonicalTimezone),
  // Last Touch filter. `ltt` picks the channel, `ltr` the rep(s); the query is
  // only sent when both are set (see Contacts.tsx). Server side lives in
  // ContactFilters.last_touch_type / last_touch_rep_id.
  lastTouchType: scalar<"" | "call" | "email" | "linkedin">("ltt", "", (raw) =>
    raw === "call" || raw === "email" || raw === "linkedin" ? raw : ""),
  lastTouchRepFilter: list("ltr"),
  // Company filter — optional narrowing to a single company's prospects.
  // Backend's contacts list already accepts `company_id`; this just wires a
  // dropdown to it. Value is a single company UUID (or "" for all).
  companyFilter: scalar<string>("co", ""),
  page: {
    params: ["pg"],
    read: (params) => Math.max(1, Number.parseInt(params.get("pg") ?? "1", 10) || 1),
    write: (params, value) => setOrDelete(params, "pg", value > 1 ? String(value) : null),
  },
};

const FILTER_PARAM_KEYS = new Set(
  Object.values(FILTER_SPEC).flatMap((entry) => entry.params),
);

function hydrate(params: URLSearchParams, isSdrLocked: boolean): ProspectFilters {
  const values = {} as ProspectFilters;
  for (const key of Object.keys(FILTER_SPEC) as Array<keyof ProspectFilters>) {
    const entry = FILTER_SPEC[key] as FilterSpecEntry<ProspectFilters[typeof key]>;
    (values as Record<keyof ProspectFilters, ProspectFilters[keyof ProspectFilters]>)[key] = entry.read(params);
  }
  if (isSdrLocked) values.ownerScope = "mine";
  return values;
}

function initialParams(searchParams: URLSearchParams): URLSearchParams {
  // Filter hydration source. URL params WIN when present (bookmarks/shared
  // links keep working); otherwise fall back to the last-saved filters in
  // localStorage so returning via the left-nav link or a detail "back" button
  // (which land on the BARE path with no query string) restores the view
  // instead of resetting everything. Computed once at mount.
  if ([...FILTER_PARAM_KEYS].some((key) => searchParams.has(key))) {
    return new URLSearchParams(searchParams);
  }
  try {
    const saved = localStorage.getItem("crm.prospecting.filters");
    if (saved) return new URLSearchParams(saved);
  } catch {
    /* ignore */
  }
  return new URLSearchParams(searchParams);
}

export function useProspectFilters({
  searchParams,
  isSdrLocked,
}: {
  searchParams: URLSearchParams;
  isSdrLocked: boolean;
}) {
  const [filters, setFilters] = useState<ProspectFilters>(() =>
    hydrate(initialParams(searchParams), isSdrLocked),
  );

  // View mode must follow browser back/forward even while this page remains
  // mounted. Other filter state is intentionally local-first and writes to the
  // URL below; this one field is safe to hydrate independently without
  // clobbering an in-progress search edit.
  useEffect(() => {
    const nextView = FILTER_SPEC.viewMode.read(initialParams(searchParams));
    setFilters((current) => current.viewMode === nextView
      ? current
      : { ...current, viewMode: nextView });
  }, [searchParams]);

  const makeSetter = useCallback(<K extends keyof ProspectFilters>(key: K) => {
    const setter: Dispatch<SetStateAction<ProspectFilters[K]>> = (next) => {
      setFilters((current) => ({
        ...current,
        [key]: typeof next === "function"
          ? (next as (value: ProspectFilters[K]) => ProspectFilters[K])(current[key])
          : next,
      }));
    };
    return setter;
  }, []);

  const setters = useMemo(() => ({
    setViewMode: makeSetter("viewMode"),
    setSearch: makeSetter("search"),
    setSearchScope: makeSetter("searchScope"),
    setSearchMatch: makeSetter("searchMatch"),
    setProspectSort: makeSetter("prospectSort"),
    setPersonaFilter: makeSetter("personaFilter"),
    setSequenceFilter: makeSetter("sequenceFilter"),
    setAccountStatusFilter: makeSetter("accountStatusFilter"),
    setCallDispositionFilter: makeSetter("callDispositionFilter"),
    setLinkedinStatusFilter: makeSetter("linkedinStatusFilter"),
    setCallOutcomeColorFilter: makeSetter("callOutcomeColorFilter"),
    setEmailOutcomeColorFilter: makeSetter("emailOutcomeColorFilter"),
    setCallAttemptsBucketFilter: makeSetter("callAttemptsBucketFilter"),
    setFollowupCountMin: makeSetter("followupCountMin"),
    setFollowupCountMax: makeSetter("followupCountMax"),
    setNextFollowupRange: makeSetter("nextFollowupRange"),
    setCallLastRange: makeSetter("callLastRange"),
    setEmailFilter: makeSetter("emailFilter"),
    setOwnerScope: makeSetter("ownerScope"),
    setAeFilter: makeSetter("aeFilter"),
    setSdrFilter: makeSetter("sdrFilter"),
    setOwnerFilter: makeSetter("ownerFilter"),
    setTimezoneFilter: makeSetter("timezoneFilter"),
    setLastTouchType: makeSetter("lastTouchType"),
    setLastTouchRepFilter: makeSetter("lastTouchRepFilter"),
    setCompanyFilter: makeSetter("companyFilter"),
    setPage: makeSetter("page"),
  }), [makeSetter]);

  const reset = useCallback(() => {
    setFilters((current) => ({
      ...hydrate(new URLSearchParams(), isSdrLocked),
      // Reset filters without surprising the user by changing layout.
      viewMode: current.viewMode,
    }));
  }, [isSdrLocked]);

  const applyParams = useCallback((params: URLSearchParams | string) => {
    const next = typeof params === "string" ? new URLSearchParams(params) : params;
    setFilters(hydrate(next, isSdrLocked));
  }, [isSdrLocked]);

  const toParams = useCallback((base?: URLSearchParams) => {
    const params = new URLSearchParams(base);
    for (const key of Object.keys(FILTER_SPEC) as Array<keyof ProspectFilters>) {
      const entry = FILTER_SPEC[key] as FilterSpecEntry<ProspectFilters[typeof key]>;
      entry.write(params, filters[key]);
    }
    if (filters.searchScope === "all") params.delete("qm");
    return params;
  }, [filters]);

  const hasActiveFilters = Boolean(
    (!isSdrLocked && filters.ownerScope === "mine") ||
    filters.sequenceFilter.length ||
    filters.accountStatusFilter.length ||
    filters.callDispositionFilter.length ||
    filters.linkedinStatusFilter.length ||
    filters.personaFilter.length ||
    filters.emailFilter.length ||
    filters.callOutcomeColorFilter.length ||
    filters.emailOutcomeColorFilter.length ||
    filters.callAttemptsBucketFilter.length ||
    filters.followupCountMin != null ||
    filters.followupCountMax != null ||
    filters.nextFollowupRange.from || filters.nextFollowupRange.to ||
    filters.callLastRange.from || filters.callLastRange.to ||
    filters.aeFilter.length ||
    filters.sdrFilter.length ||
    filters.ownerFilter.length ||
    filters.timezoneFilter.length ||
    (filters.lastTouchType && filters.lastTouchRepFilter.length) ||
    filters.companyFilter ||
    filters.search
  );

  // Auto-open the filter card on mount when the URL already carries active
  // filters — otherwise users would have to hunt for the toggle to discover
  // why the list is narrowed.
  //
  // `owner=mine` is deliberately NOT in this list. It is the DEFAULT landing
  // view ("My prospects"), not something the rep chose, so counting it here
  // auto-expanded all five filter rows on every plain visit to /contacts and
  // pushed the first prospect to y=862 of a 900px viewport — zero rows
  // visible before scrolling. Measured 2026-08-18.
  const initiallyShowFilters = Boolean(
    filters.sequenceFilter.length || filters.accountStatusFilter.length ||
    filters.callDispositionFilter.length || filters.aeFilter.length ||
    filters.sdrFilter.length || filters.ownerFilter.length ||
    filters.timezoneFilter.length || filters.companyFilter ||
    filters.personaFilter.length || filters.emailFilter.length ||
    filters.callOutcomeColorFilter.length || filters.emailOutcomeColorFilter.length ||
    filters.callAttemptsBucketFilter.length || filters.followupCountMin != null ||
    filters.followupCountMax != null || filters.nextFollowupRange.from ||
    filters.nextFollowupRange.to || filters.callLastRange.from || filters.callLastRange.to ||
    (filters.lastTouchType && filters.lastTouchRepFilter.length)
  );

  return {
    ...filters,
    ...setters,
    reset,
    applyParams,
    toParams,
    hasActiveFilters,
    initiallyShowFilters,
  };
}

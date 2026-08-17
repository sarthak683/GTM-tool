import type {
  AccountSourcingSummary,
  Company,
  Contact,
  OutreachSequence,
  OutreachStep,
  Signal,
  SourcingBatch,
} from "../../types";
import { BASE, getAuthHeaders, request, requestList, requestPaginated } from "./core";

export interface RecotapSummary {
  stages: Record<string, number>;
  engagement: Record<string, number>;
  scored: number;
  not_scored: number;
  total: number;
}

export const enrichmentApi = {
  triggerCompany: (companyId: string) =>
    request<{ status: string; task_id: string; message: string }>(
      `/api/v1/enrichment/company/${companyId}`,
      { method: "POST" }
    ),
  taskStatus: (taskId: string) =>
    request<{ task_id: string; status: string; result: unknown }>(
      `/api/v1/enrichment/task/${taskId}`
    ),
};

export const outreachApi = {
  generate: (contactId: string) =>
    request<OutreachSequence>(`/api/v1/outreach/generate/${contactId}`, {
      method: "POST",
    }),
  getSequence: (contactId: string) =>
    request<OutreachSequence>(`/api/v1/outreach/sequences/${contactId}`),
  getSequenceOptional: (contactId: string) =>
    request<OutreachSequence | null>(`/api/v1/outreach/contacts/${contactId}/sequence`),
  listInstantlyCampaigns: () =>
    request<{ campaigns: { id: string; name: string; status?: number | string | null }[] }>(
      `/api/v1/outreach/instantly/campaigns`,
    ),
  bulkAddToInstantlyCampaign: (contactIds: string[], campaignId: string) =>
    request<{ campaign_id: string; requested: number; enrolled: number; skipped_no_email: number }>(
      `/api/v1/outreach/instantly/bulk-add`,
      { method: "POST", body: JSON.stringify({ contact_ids: contactIds, campaign_id: campaignId }) },
    ),
  bulkGenerate: (companyId: string, personaFilter?: string) => {
    const params = personaFilter ? `?persona_filter=${personaFilter}` : "";
    return request<{ generated: number; skipped_existing: number; sequences: unknown[] }>(
      `/api/v1/outreach/bulk/${companyId}${params}`,
      { method: "POST" }
    );
  },
  getCompanySequences: (companyId: string) =>
    request<
      { sequence_id: string; contact_id: string; contact_name: string; title?: string; persona?: string; status: string; subject_1?: string; email_1_preview?: string }[]
    >(`/api/v1/outreach/company/${companyId}`),
  updateSequence: (sequenceId: string, fields: Partial<Record<"email_1"|"email_2"|"email_3"|"subject_1"|"subject_2"|"subject_3"|"linkedin_message"|"status", string>>) =>
    request<OutreachSequence>(`/api/v1/outreach/sequences/${sequenceId}`, {
      method: "PATCH",
      body: JSON.stringify(fields),
    }),
  getSteps: (sequenceId: string) =>
    request<OutreachStep[]>(`/api/v1/outreach/sequences/${sequenceId}/steps`),
  addStep: (sequenceId: string, step: Pick<OutreachStep, "step_number" | "channel" | "subject" | "body" | "delay_value" | "delay_unit"> & { variants?: Record<string, unknown> | Array<Record<string, unknown>> | null }) =>
    request<OutreachStep>(`/api/v1/outreach/sequences/${sequenceId}/steps`, {
      method: "POST",
      body: JSON.stringify(step),
    }),
  updateStep: (stepId: string, fields: Partial<Pick<OutreachStep, "channel" | "subject" | "body" | "delay_value" | "delay_unit" | "status" | "variants">>) =>
    request<OutreachStep>(`/api/v1/outreach/steps/${stepId}`, {
      method: "PATCH",
      body: JSON.stringify(fields),
    }),
  deleteStep: (stepId: string) =>
    request<{ status: string; step_id: string }>(`/api/v1/outreach/steps/${stepId}`, {
      method: "DELETE",
    }),
  launch: (sequenceId: string, sendingAccount: string, campaignName?: string) =>
    request<{
      status: string;
      sequence_id: string;
      instantly_campaign_id: string;
      contact_email: string;
      steps_count: number;
      campaign_name: string;
    }>(`/api/v1/outreach/launch/${sequenceId}`, {
      method: "POST",
      body: JSON.stringify({ sending_account: sendingAccount, campaign_name: campaignName }),
    }),
  getReplies: (sequenceId: string) =>
    request<{ sequence_id: string; replies: Array<{ id?: string; subject?: unknown; body?: unknown; from_email?: string; created_at?: string; timestamp?: string }> }>(
      `/api/v1/outreach/replies/${sequenceId}`
    ),
  launchContactsCampaign: (
    contactIds: string[],
    sendingAccount: string,
    options?: { templateSequenceId?: string; campaignName?: string }
  ) =>
    request<{
      status: string;
      instantly_campaign_id: string;
      campaign_name: string;
      selected_contacts: number;
      launched_pairs: number;
      skipped_no_email: number;
      skipped_already_launched: number;
      sequences_generated: number;
      pushed: number;
      failed: number;
      results: Array<{ contact_id: string; email: string; status: string; error?: string }>;
    }>(`/api/v1/outreach/launch-contacts`, {
      method: "POST",
      body: JSON.stringify({
        contact_ids: contactIds,
        sending_account: sendingAccount,
        template_sequence_id: options?.templateSequenceId,
        campaign_name: options?.campaignName,
      }),
    }),
  launchCompanyCampaign: (companyId: string, sendingAccount: string, campaignName?: string) =>
    request<{
      status: string;
      company_id: string;
      company_name: string;
      instantly_campaign_id: string;
      campaign_name: string;
      total_sequences: number;
      pushed: number;
      failed: number;
      results: Array<{ contact_id: string; email: string; status: string; error?: string }>;
    }>(`/api/v1/outreach/launch-company/${companyId}`, {
      method: "POST",
      body: JSON.stringify({ sending_account: sendingAccount, campaign_name: campaignName }),
    }),
  syncCampaign: (sequenceId: string) =>
    request<{
      sequence_id: string;
      campaign_id: string;
      campaign_status?: string;
      leads_synced: number;
      leads_skipped: number;
      analytics?: Record<string, unknown>;
    }>(`/api/v1/outreach/sync-campaign/${sequenceId}`, {
      method: "POST",
    }),
  pause: (sequenceId: string) =>
    request<{ status: string; sequence_id: string; campaign_id: string }>(
      `/api/v1/outreach/pause/${sequenceId}`,
      { method: "POST" }
    ),
  resume: (sequenceId: string) =>
    request<{ status: string; sequence_id: string; campaign_id: string }>(
      `/api/v1/outreach/resume/${sequenceId}`,
      { method: "POST" }
    ),
};

export const intelligenceApi = {
  getAccountBrief: (companyId: string) =>
    request<{
      company_id: string;
      company_name: string;
      domain: string;
      scraped: { title?: string; description?: string; body_text?: string; about_text?: string; error?: string };
      news_signals: { title: string; url?: string }[];
      tech_stack: Record<string, string>;
      brief: string | null;
    }>(`/api/v1/intelligence/${companyId}`),
};

export const sendApi = {
  sendEmail: (sequenceId: string, emailNumber: 1 | 2 | 3, toEmail?: string) =>
    request<{ sequence_id: string; email_number: number; to: string; subject?: string; resend_id?: string; status: string }>(
      `/api/v1/outreach/send/${sequenceId}`,
      {
        method: "POST",
        body: JSON.stringify({ email_number: emailNumber, to_email: toEmail ?? "" }),
      }
    ),
};

export const signalsApi = {
  getCompanySignals: (companyId: string) =>
    request<Signal[]>(`/api/v1/signals/company/${companyId}`),
  refreshCompanySignals: (companyId: string) =>
    request<{ company_id: string; signals_created: number }>(
      `/api/v1/signals/company/${companyId}/refresh`,
      { method: "POST" }
    ),
};

export interface ProspectingBatch {
  batch_id: string;
  created_at: string;
  total: number;
  created: number;
  skipped: number;
  failed: number;
  companies: Array<{
    domain: string;
    company_id: string;
    task_id: string;
    status: string;
  }>;
  skipped_names?: string[];
  skipped_domains?: string[];
  failed_rows: Array<{ name?: string; domain?: string; error: string }>;
  completed_enrichments?: number;
}

export const prospectingApi = {
  bulkUpload: async (file: File): Promise<ProspectingBatch> => {
    const form = new FormData();
    form.append("file", file);

    const res = await fetch(`${BASE}/api/v1/prospecting/bulk`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: form,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? "Bulk upload failed");
    }

    return res.json();
  },

  status: async (batchId: string): Promise<ProspectingBatch> => {
    return request<ProspectingBatch>(`/api/v1/prospecting/status/${batchId}`);
  },
};

export type DemoStatus = "draft" | "generating" | "ready" | "error";

export type CustomDemo = {
  id: string;
  title: string;
  client_name: string | null;
  client_domain: string | null;
  creation_path: "file_upload" | "editor" | "brief";
  source_filename: string | null;
  status: DemoStatus;
  error_message: string | null;
  brand_data: Record<string, string> | null;
  created_at: string;
  updated_at: string;
};

export type SceneIn = {
  scene_title: string;
  beacon_steps: string[];
  client_screen: string;
  reveal_description: string;
};

export type DemoBriefIn = {
  title: string;
  client_name?: string;
  client_domain?: string;
  company_id?: string;
  deal_id?: string;
  industry?: string;
  company_summary: string;
  audience?: string;
  business_objectives: string[];
  demo_objectives: string[];
  workflow_overview: string;
  key_capabilities: string[];
  scenes_outline: string[];
  success_metrics: string[];
  constraints: string[];
  additional_context?: string;
};

export const customDemoApi = {
  list: () => request<CustomDemo[]>("/api/v1/custom-demos/"),

  generateFromFile: (
    file: File,
    title: string,
    clientName: string,
    clientDomain: string,
    companyId?: string,
    dealId?: string,
  ) => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    form.append("client_name", clientName);
    form.append("client_domain", clientDomain);
    if (companyId) form.append("company_id", companyId);
    if (dealId) form.append("deal_id", dealId);
    return fetch(`${BASE}/api/v1/custom-demos/generate-from-file`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: form,
    }).then(async (r) => {
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(err.detail ?? "Upload failed");
      }
      return r.json() as Promise<CustomDemo>;
    });
  },

  generateFromEditor: (payload: {
    title: string;
    client_name?: string;
    client_domain?: string;
    company_id?: string;
    deal_id?: string;
    scenes: SceneIn[];
  }) =>
    request<CustomDemo>("/api/v1/custom-demos/generate-from-editor", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  generateFromBrief: (payload: DemoBriefIn) =>
    request<CustomDemo>("/api/v1/custom-demos/generate-from-brief", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  status: (id: string) =>
    request<{ id: string; status: DemoStatus; error_message: string | null }>(
      `/api/v1/custom-demos/${id}/status`
    ),

  revise: (id: string, instruction: string) =>
    request<CustomDemo>(`/api/v1/custom-demos/${id}/revise`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),

  delete: (id: string) =>
    request<void>(`/api/v1/custom-demos/${id}`, { method: "DELETE" }),

  htmlUrl: (id: string) => `${BASE}/api/v1/custom-demos/${id}/html`,
};

/**
 * Every filter the Account Sourcing accounts list understands — the frontend
 * twin of the backend's `CompanySourcingFilters` dependency.
 *
 * The list, the summary (KPI strip), the CSV export and the filter-wide
 * bulk-assign all serialize through `buildAccountSourcingQuery`, so the four
 * populations agree by construction. Before this existed, "Download filtered"
 * sent only the prospect-count bounds and quietly exported the whole
 * workspace.
 */
export interface AccountSourcingFilters {
  q?: string;
  icpTier?: string[];
  disposition?: string[];
  /** `unset` = no status. Passing not_a_fit/dnd lifts the disabled-account hiding. */
  accountStatus?: string[];
  recommendedOutreachLane?: string[];
  assignedRep?: string;
  assignedRepEmail?: string;
  /** Matches AE **or** SDR ownership. `__unassigned__` = neither slot set. */
  ownerId?: string | string[];
  aeId?: string | string[];
  sdrId?: string | string[];
  /** `not_scored` = no Recotap journey stage. */
  journeyStage?: string[];
  batchId?: string;
  prospectsMin?: number;
  prospectsMax?: number;
  companyIds?: string[];
}

/** Server-side sort keys accepted by the list + export (anything else → 422). */
export type AccountSourcingSortKey =
  | "created_at"
  | "name"
  | "icp_score"
  | "prospect_count"
  | "enriched_at";

export interface AccountSourcingSort {
  sort?: AccountSourcingSortKey;
  /** Omitted → desc, except `name` → asc. */
  order?: "asc" | "desc";
}

function appendMulti(search: URLSearchParams, key: string, value?: string | string[]) {
  const joined = Array.isArray(value) ? value.join(",") : value;
  if (joined) search.set(key, joined);
}

/** Serialize the page's active filter state into the shared query params. */
export function buildAccountSourcingQuery(
  filters?: AccountSourcingFilters,
  sort?: AccountSourcingSort,
): URLSearchParams {
  const search = new URLSearchParams();
  if (filters?.q) search.set("q", filters.q);
  appendMulti(search, "icp_tier", filters?.icpTier);
  appendMulti(search, "disposition", filters?.disposition);
  appendMulti(search, "account_status", filters?.accountStatus);
  appendMulti(search, "recommended_outreach_lane", filters?.recommendedOutreachLane);
  if (filters?.assignedRep) search.set("assigned_rep", filters.assignedRep);
  if (filters?.assignedRepEmail) search.set("assigned_rep_email", filters.assignedRepEmail);
  appendMulti(search, "owner_id", filters?.ownerId);
  appendMulti(search, "ae_id", filters?.aeId);
  appendMulti(search, "sdr_id", filters?.sdrId);
  appendMulti(search, "journey_stage", filters?.journeyStage);
  if (filters?.batchId) search.set("batch_id", filters.batchId);
  if (filters?.prospectsMin !== undefined) search.set("prospects_min", String(filters.prospectsMin));
  if (filters?.prospectsMax !== undefined) search.set("prospects_max", String(filters.prospectsMax));
  appendMulti(search, "company_ids", filters?.companyIds);
  if (sort?.sort) search.set("sort", sort.sort);
  if (sort?.order) search.set("order", sort.order);
  return search;
}

/** Error carrying the HTTP status, so callers can branch on 409 vs 422. */
export class AccountSourcingHttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AccountSourcingHttpError";
    this.status = status;
  }
}

async function requestWithStatus<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err?.detail;
    const message =
      typeof detail === "string" && detail
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => (typeof d === "string" ? d : d?.msg)).filter(Boolean).join("; ")
          : res.statusText || "Request failed";
    throw new AccountSourcingHttpError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface FilterAssignResult {
  matched: number;
  updated: number;
  skipped: number;
  user_id: string | null;
  role: "ae" | "sdr";
  /** Prospects held by a third rep — deliberately left where they were. */
  prospects_kept_divergent: number;
}

// ── Admin data-health (Needs review) ────────────────────────────────────────

export interface SdrConflictRow {
  company_id: string;
  company_name: string;
  company_sdr_id: string;
  company_sdr_name: string | null;
  contact_sdr_id: string;
  contact_sdr_name: string | null;
  prospect_count: number;
}

/**
 * Why a prospect's email domain does not match its account.
 *
 * `alias_gap`   — NO live account owns that domain. Nothing is misfiled: the
 *                 prospect is an acquisition or alternate brand of the account
 *                 it already sits on, and the ACCOUNT is missing the domain as
 *                 an alias. Re-linking these would scatter accounts that were
 *                 correctly consolidated.
 * `misattached` — another live account already claims the domain, so the
 *                 prospect is genuinely ambiguous and may belong there.
 */
export type MisattachedCause = "alias_gap" | "misattached";

export interface MisattachedProspectRow {
  contact_id: string;
  contact_name: string;
  contact_email: string | null;
  contact_domain: string | null;
  contact_sdr_name: string | null;
  current_company_id: string;
  current_company_name: string | null;
  current_company_domain: string | null;
  company_dominant_domain: string | null;
  dominant_contact_count: number;
  /** null = no existing home for this prospect; do NOT offer a re-link. */
  suggested_company_id: string | null;
  suggested_company_name: string | null;
  suggested_company_domain: string | null;
  /** The live account that owns `contact_domain` today (primary OR alias).
   *  Set without a `suggested_company_id` when the domain is claimed only as
   *  another account's alias — adding it here would 422. */
  domain_owner_id: string | null;
  domain_owner_name: string | null;
  likely_cause: MisattachedCause;
  /** `add_alias` for alias gaps, `relink` for true misattachments. */
  recommended_action: "add_alias" | "relink";
  /** Cause-specific wording — an alias gap must NOT read as an error. */
  evidence: string;
}

/** Misattached candidates carry per-cause subtotals over the FULL set, so the
 *  two UI sections can show honest totals even when `rows` is limit-capped. */
export interface MisattachedSection extends DataHealthSection<MisattachedProspectRow> {
  alias_gap_total: number;
  misattached_total: number;
}

export interface DomainCorrectionRow {
  company_id: string;
  company_name: string;
  current_domain: string | null;
  suggested_domain: string;
  evidence_count: number;
  /** true = another live account already owns it → merge, don't rename. */
  suggested_domain_taken: boolean;
}

export interface DataHealthSection<T> {
  total: number;
  rows: T[];
}

export interface SourcingDataHealth {
  generated_at: string;
  sdr_conflicts: DataHealthSection<SdrConflictRow>;
  misattached: MisattachedSection;
  domain_corrections: DataHealthSection<DomainCorrectionRow>;
}

export const accountSourcingApi = {
  upload: async (file: File): Promise<SourcingBatch> => {
    const form = new FormData();
    form.append("file", file);

    const res = await fetch(`${BASE}/api/v1/account-sourcing/upload`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? "Upload failed");
    }
    return res.json();
  },

  // The endpoint pages through the shared Pagination dep (default 50, max 2000),
  // newest first. Send the limit explicitly so the caller knows which cap it got
  // and can tell a full page from a short one.
  listBatches: (limit = 50) =>
    requestList<SourcingBatch>(`/api/v1/account-sourcing/batches?limit=${limit}`),

  batchStatus: (batchId: string) =>
    request<SourcingBatch>(`/api/v1/account-sourcing/batches/${batchId}`),

  confirmBatch: (batchId: string, force = true) =>
    request<SourcingBatch>(`/api/v1/account-sourcing/batches/${batchId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),

  cancelBatch: (batchId: string) =>
    request<SourcingBatch>(`/api/v1/account-sourcing/batches/${batchId}/cancel`, {
      method: "POST",
    }),

  batchCompanies: (batchId: string) =>
    requestList<Company>(`/api/v1/account-sourcing/batches/${batchId}/companies`),

  listCompanies: (skip = 0, limit = 200, assignedRepEmail?: string) =>
    requestList<Company>(`/api/v1/account-sourcing/companies?skip=${skip}&limit=${limit}${assignedRepEmail ? `&assigned_rep_email=${encodeURIComponent(assignedRepEmail)}` : ""}`),

  listCompaniesPaginated: (
    params?: AccountSourcingFilters & AccountSourcingSort & { skip?: number; limit?: number },
  ) => {
    const search = buildAccountSourcingQuery(params, params);
    search.set("skip", String(params?.skip ?? 0));
    search.set("limit", String(params?.limit ?? 50));
    return requestPaginated<Company>(`/api/v1/account-sourcing/companies?${search}`);
  },

  /** Count-only probe over the SAME population as the list — used to re-confirm
   *  a filter-wide assignment after the server reports a 409 size mismatch. */
  countCompanies: async (filters?: AccountSourcingFilters) => {
    const search = buildAccountSourcingQuery(filters);
    search.set("skip", "0");
    search.set("limit", "1");
    const page = await requestPaginated<Company>(`/api/v1/account-sourcing/companies?${search}`);
    return page.total;
  },

  /** Admin-only data-quality report backing the "Needs review" tab.
   *  `limit` caps rows PER SECTION; each section's `total` is the full count. */
  dataHealth: (limit?: number) =>
    request<SourcingDataHealth>(
      `/api/v1/account-sourcing/data-health${limit ? `?limit=${limit}` : ""}`,
    ),

  /** Assign every account matching `filters`. `expectedTotal` is the count the
   *  user was shown — the server 409s instead of assigning a different set. */
  assignCompaniesByFilter: (
    filters: AccountSourcingFilters,
    body: { userId: string | null; role: "ae" | "sdr"; expectedTotal?: number },
  ) =>
    requestWithStatus<FilterAssignResult>(
      `/api/v1/assignments/companies/by-filter?${buildAccountSourcingQuery(filters)}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          user_id: body.userId,
          role: body.role,
          ...(body.expectedTotal !== undefined ? { expected_total: body.expectedTotal } : {}),
        }),
      },
    ),

  /** Re-home a misattached prospect onto the account it actually belongs to. */
  relinkContactCompany: (contactId: string, companyId: string) =>
    request<Contact>(`/api/v1/contacts/${contactId}`, {
      method: "PUT",
      body: JSON.stringify({ company_id: companyId }),
    }),

  /** Record ONE extra domain as an alias of an account.
   *
   *  The company PUT REPLACES `additional_domains` wholesale, so this has to be
   *  a read-modify-write — sending just the new domain would wipe the existing
   *  aliases. A cross-account collision comes back as 422 and `request` throws
   *  with the server's own message (which names the colliding account).
   *
   *  Resolves `false` when the domain was already an alias (nothing written). */
  addAliasDomain: async (companyId: string, domain: string): Promise<boolean> => {
    const path = `/api/v1/account-sourcing/companies/${companyId}`;
    const company = await request<Company>(path);
    const existing = company.additional_domains ?? [];
    if (existing.includes(domain)) return false;
    await request<Company>(path, {
      method: "PUT",
      body: JSON.stringify({ additional_domains: [...existing, domain] }),
    });
    return true;
  },

  /** Buying-journey funnel counts over the SAME filtered, visibility-scoped
   *  population as the list. Pass the page's active filters — without them the
   *  funnel showed workspace-wide numbers to reps who own a handful of
   *  accounts. `journey_stage` is ignored server-side (facet-count semantics:
   *  the tiles ARE that filter), so it is safe to pass the whole object. */
  recotapSummary: (params?: AccountSourcingFilters) => {
    const search = buildAccountSourcingQuery(params);
    return request<RecotapSummary>(
      `/api/v1/account-sourcing/recotap/summary${search.toString() ? `?${search}` : ""}`,
    );
  },

  recotapRefresh: () =>
    request<{ pull: { pulled: number; configured: number }; seed: { seeded: number } }>(
      "/api/v1/account-sourcing/recotap/refresh",
      { method: "POST" },
    ),

  /** KPI-strip counts over the SAME filtered population as the list. */
  summary: (params?: AccountSourcingFilters) => {
    const search = buildAccountSourcingQuery(params);
    return request<AccountSourcingSummary>(
      `/api/v1/account-sourcing/summary${search.toString() ? `?${search.toString()}` : ""}`
    );
  },

  getCompany: (companyId: string) =>
    request<Company>(`/api/v1/account-sourcing/companies/${companyId}`),

  createManualCompany: (data: { name: string; domain?: string }) =>
    request<SourcingBatch>("/api/v1/account-sourcing/companies/manual", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateCompany: (companyId: string, data: Record<string, unknown>) =>
    request<Company>(`/api/v1/account-sourcing/companies/${companyId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  // Merge sourceCompanyId INTO companyId (the survivor). Admin-only; the
  // source is soft-deleted and its domains become alias domains here.
  mergeCompany: (companyId: string, sourceCompanyId: string) =>
    request<{
      merged_into: string;
      source_company_id: string;
      moved: Record<string, number>;
      alias_domains: string[];
    }>(`/api/v1/account-sourcing/companies/${companyId}/merge`, {
      method: "POST",
      body: JSON.stringify({ source_company_id: sourceCompanyId }),
    }),

  reEnrichCompany: (companyId: string) =>
    request<{ company_id: string; task_id: string; status: string; message: string }>(
      `/api/v1/account-sourcing/companies/${companyId}/re-enrich`,
      { method: "POST" }
    ),

  bulkEnrichAll: (unenrichedOnly = false) =>
    request<{ queued: number; total: number; unenriched_only: boolean; message: string }>(
      `/api/v1/account-sourcing/companies/bulk-enrich?unenriched_only=${unenrichedOnly}`,
      { method: "POST" }
    ),

  bulkIcpResearch: (unenrichedOnly = false) =>
    request<{ queued: number; total: number; unenriched_only: boolean; message: string }>(
      `/api/v1/account-sourcing/companies/bulk-icp-research?unenriched_only=${unenrichedOnly}`,
      { method: "POST" }
    ),

  icpResearch: (companyId: string) =>
    request<{ company_id: string; task_id: string; status: string; message: string }>(
      `/api/v1/account-sourcing/companies/${companyId}/icp-research`,
      { method: "POST" }
    ),

  getContacts: (companyId: string) =>
    requestList<Contact>(`/api/v1/account-sourcing/companies/${companyId}/contacts`),

  getContact: (contactId: string) =>
    request<Contact>(`/api/v1/account-sourcing/contacts/${contactId}`),

  updateContact: (contactId: string, data: Record<string, unknown>) =>
    request<Contact>(`/api/v1/account-sourcing/contacts/${contactId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  reEnrichContact: (contactId: string) =>
    request<{ contact_id: string; task_id: string; status: string; message: string }>(
      `/api/v1/account-sourcing/contacts/${contactId}/re-enrich`,
      { method: "POST" }
    ),

  pushToInstantly: (companyId: string, campaignId = "default") =>
    request<{ company_id: string; contacts_pushed: number; results: unknown[] }>(
      `/api/v1/account-sourcing/companies/${companyId}/push-instantly?campaign_id=${campaignId}`,
      { method: "POST" }
    ),

  addCompanyNote: (companyId: string, body: string) =>
    request<{ activity_log: unknown[] }>(
      `/api/v1/account-sourcing/companies/${companyId}/notes`,
      { method: "POST", body: JSON.stringify({ body }) }
    ),

  addContactNote: (contactId: string, body: string) =>
    request<{ notes_log: unknown[] }>(
      `/api/v1/account-sourcing/contacts/${contactId}/notes`,
      { method: "POST", body: JSON.stringify({ body }) }
    ),

  /** CSV of EXACTLY the filtered + sorted population the accounts table shows. */
  exportCsv: async (params?: AccountSourcingFilters, sort?: AccountSourcingSort) => {
    const qs = buildAccountSourcingQuery(params, sort).toString();
    const res = await fetch(`${BASE}/api/v1/account-sourcing/export${qs ? `?${qs}` : ""}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? "Export failed");
    }
    return res.blob();
  },

  exportContactsCsv: async (params?: { assignedRepEmail?: string; batchId?: string; contactIds?: string[] }) => {
    const search = new URLSearchParams();
    if (params?.assignedRepEmail) search.set("assigned_rep_email", params.assignedRepEmail);
    if (params?.batchId) search.set("batch_id", params.batchId);
    if (params?.contactIds?.length) search.set("contact_ids", params.contactIds.join(","));
    const qs = search.toString();
    const res = await fetch(`${BASE}/api/v1/account-sourcing/export-contacts${qs ? `?${qs}` : ""}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? "Contact export failed");
    }
    return res.blob();
  },

  resetData: (scope: "account-sourcing" | "prospecting" | "workspace") =>
    request<{ scope: string; summary: Record<string, number> }>(
      `/api/v1/account-sourcing/reset/${scope}`,
      { method: "POST" }
    ),
};

import type {
  AccountSourcingSummary,
  Company,
  Contact,
  Deal,
  Activity,
  TaskComment,
  TaskItem,
  TaskWorkspaceItem,
  AssignmentUpdate,
  OutreachSequence,
  OutreachStep,
  Signal,
  Meeting,
  Battlecard,
  Paginated,
  SourcingBatch,
  SalesResource,
  GlobalSearchResponse,
  User,
  AngelInvestor,
  AngelMapping,
  ExecutionTrackerItem,
  ExecutionTrackerSummary,
  Reminder,
  GmailSyncSettings,
  DealStageSettings,
  ProspectStageSettings,
  OutreachContentSettings,
  PipelineSummarySettings,
  PreMeetingAutomationSettings,
  ProspectImportResponse,
  RolePermissionsSettings,
  CrmImportResponse,
  ClickUpCrmSettings,
  SyncScheduleSettings,
} from "../types";

/**
 * Fetch a paginated list endpoint and unwrap .items for backward compat.
 * Pages that need total/pages can call requestPaginated() directly.
 */
async function requestList<T>(path: string): Promise<T[]> {
  const res = await request<Paginated<T> | T[]>(path);
  if (Array.isArray(res)) return res;
  return res.items ?? [];
}

async function requestPaginated<T>(path: string): Promise<Paginated<T>> {
  const res = await request<Paginated<T> | T[]>(path);
  if (Array.isArray(res)) {
    // Some older endpoints still return a bare array. Normalize them so page
    // components can consume one shape while the backend gradually converges.
    return {
      items: res,
      total: res.length,
      page: 1,
      size: res.length,
      pages: 1,
    };
  }
  return res;
}

const BASE = import.meta.env.VITE_API_URL ?? "";

const ISO_DATETIME_NO_TZ = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;

function normalizeUtcDateStrings<T>(value: T): T {
  if (value == null) return value;
  if (Array.isArray(value)) {
    return value.map((item) => normalizeUtcDateStrings(item)) as T;
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const normalized: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(obj)) {
      normalized[key] = normalizeUtcDateStrings(item);
    }
    return normalized as T;
  }
  if (typeof value === "string" && ISO_DATETIME_NO_TZ.test(value)) {
    // Backend stores many timestamps as UTC without an explicit offset.
    // Appending Z ensures browsers interpret these as UTC before local display.
    return `${value}Z` as T;
  }
  return value;
}

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem("beacon_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...getAuthHeaders(), ...options?.headers },
    ...options,
  });
  if (res.status === 401) {
    // Any expired/invalid token should force a clean login flow so pages do not
    // keep retrying unauthorized requests with stale browser state.
    localStorage.removeItem("beacon_token");
    window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Request failed");
  }
  if (res.status === 204) return undefined as T;
  const payload = await res.json();
  return normalizeUtcDateStrings(payload) as T;
}

export const companiesApi = {
  list: (skip = 0, limit = 1000) =>
    requestList<Company>(`/api/v1/companies/?skip=${skip}&limit=${limit}`),
  listPaginated: (skip = 0, limit = 50) =>
    requestPaginated<Company>(`/api/v1/companies/?skip=${skip}&limit=${limit}`),
  get: (id: string) => request<Company>(`/api/v1/companies/${id}`),
  getDeals: (id: string) => request<Deal[]>(`/api/v1/companies/${id}/deals`),
  create: (data: Partial<Company>) =>
    request<Company>("/api/v1/companies/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<Company>) =>
    request<Company>(`/api/v1/companies/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  patch: (id: string, data: Partial<Company>) =>
    request<Company>(`/api/v1/companies/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    request<void>(`/api/v1/companies/${id}`, { method: "DELETE" }),
  checkDuplicates: (names: string[], domains: string[]) =>
    request<{ duplicate_names: string[]; duplicate_domains: string[] }>(
      "/api/v1/companies/check-duplicates",
      { method: "POST", body: JSON.stringify({ names, domains }) }
    ),
};

export const contactsApi = {
  list: (skip = 0, limit = 200, companyId?: string) => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
    if (companyId) params.set("company_id", companyId);
    // Returns Contact[] with company_name populated — no second API call needed
    return requestList<Contact>(`/api/v1/contacts/?${params}`);
  },
  listPaginated: (skip = 0, limit = 50, companyId?: string) => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
    if (companyId) params.set("company_id", companyId);
    return requestPaginated<Contact>(`/api/v1/contacts/?${params}`);
  },
  searchPaginated: (params: {
    skip?: number;
    limit?: number;
    companyId?: string;
    q?: string;
    persona?: string[];
    sequenceStatus?: string[];
    callDisposition?: string[];
    emailState?: string[];
    aeId?: string[];
    sdrId?: string[];
    prospectOnly?: boolean;
  }) => {
    const search = new URLSearchParams({
      skip: String(params.skip ?? 0),
      limit: String(params.limit ?? 50),
    });
    if (params.companyId) search.set("company_id", params.companyId);
    if (params.q) search.set("q", params.q);
    if (params.persona?.length) search.set("persona", params.persona.join(","));
    if (params.sequenceStatus?.length) search.set("sequence_status", params.sequenceStatus.join(","));
    if (params.callDisposition?.length) search.set("call_disposition", params.callDisposition.join(","));
    if (params.emailState?.length) search.set("email_state", params.emailState.join(","));
    if (params.aeId?.length) search.set("ae_id", params.aeId.join(","));
    if (params.sdrId?.length) search.set("sdr_id", params.sdrId.join(","));
    if (params.prospectOnly) search.set("prospect_only", "true");
    return requestPaginated<Contact>(`/api/v1/contacts/?${search}`);
  },
  get: (id: string) => request<Contact>(`/api/v1/contacts/${id}`),
  enrich: (id: string) =>
    request<{ contact_id: string; status: string; fields_updated: string[]; contact: Contact }>(
      `/api/v1/contacts/${id}/enrich`,
      { method: "POST" }
    ),
  getBrief: (id: string) =>
    request<{
      contact_id: string;
      contact_name: string;
      title?: string;
      linkedin_url?: string;
      brief: string | null;
      scraped?: { headline?: string; summary?: string; error?: string };
    }>(`/api/v1/contacts/${id}/brief`),
  create: (data: Partial<Contact>) =>
    request<Contact>("/api/v1/contacts/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<Contact>) =>
    request<Contact>(`/api/v1/contacts/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    request<void>(`/api/v1/contacts/${id}`, { method: "DELETE" }),
  bulkDelete: () =>
    request<void>("/api/v1/contacts/bulk", { method: "DELETE" }),
  discover: (companyId: string) =>
    request<Contact[]>(`/api/v1/contacts/discover/${companyId}`, { method: "POST" }),
  importCsv: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/api/v1/contacts/import-csv`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: form,
    }).then(async (r) => {
      if (r.status === 401) {
        localStorage.removeItem("beacon_token");
        window.location.href = "/login";
        throw new Error("Session expired");
      }
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(err.detail ?? "Upload failed");
      }
      return r.json() as Promise<ProspectImportResponse>;
    });
  },
};

export const dealsApi = {
  list: (skip = 0, limit = 200, companyId?: string, stage?: string) => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
    if (companyId) params.set("company_id", companyId);
    if (stage) params.set("stage", stage);
    return requestList<Deal>(`/api/v1/deals/?${params}`);
  },
  board: (pipelineType = "deal") =>
    request<Record<string, Deal[]>>(`/api/v1/deals/board?pipeline_type=${pipelineType}`),
  get: (id: string) => request<Deal>(`/api/v1/deals/${id}`),
  create: (data: Partial<Deal>) =>
    request<Deal>("/api/v1/deals/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<Deal>) =>
    request<Deal>(`/api/v1/deals/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  patch: (id: string, data: Partial<Deal>) =>
    request<Deal>(`/api/v1/deals/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  autoFillMeddpicc: (id: string) =>
    request<Deal>(`/api/v1/deals/${id}/meddpicc/auto-fill`, {
      method: "POST",
    }),
  moveStage: (dealId: string, stage: string) =>
    request<Deal>(`/api/v1/deals/${dealId}/stage`, {
      method: "PATCH",
      body: JSON.stringify({ stage }),
    }),
  delete: (id: string) =>
    request<void>(`/api/v1/deals/${id}`, { method: "DELETE" }),
  // Deal contacts
  getContacts: (dealId: string) =>
    request<import("../types").DealContact[]>(`/api/v1/deals/${dealId}/contacts`),
  addContact: (dealId: string, contactId: string, role?: string) =>
    request<import("../types").DealContact>(`/api/v1/deals/${dealId}/contacts`, {
      method: "POST",
      body: JSON.stringify({ contact_id: contactId, role }),
    }),
  removeContact: (dealId: string, contactId: string) =>
    request<void>(`/api/v1/deals/${dealId}/contacts/${contactId}`, { method: "DELETE" }),
  // Deal activities
  getActivities: (dealId: string) =>
    request<Activity[]>(`/api/v1/deals/${dealId}/activities`),
  addComment: (dealId: string, body: string) =>
    request<Activity>(`/api/v1/deals/${dealId}/activities`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),
};

export const crmImportsApi = {
  importClickUpSalesCrm: (data?: {
    replace_existing?: boolean;
    limit?: number;
    cache_dir?: string;
    skip_comments?: boolean;
    skip_subtasks?: boolean;
  }) =>
    request<{ status: string; task_id: string; message: string }>("/api/v1/crm-imports/clickup-sales-crm", {
      method: "POST",
      body: JSON.stringify(data ?? { replace_existing: true }),
    }),

  getImportStatus: (taskId: string) =>
    request<{ task_id: string; status: string; result?: CrmImportResponse; error?: string }>(
      `/api/v1/crm-imports/status/${taskId}`
    ),
};

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
    request<{ sequence_id: string; replies: Array<{ id?: string; subject?: string; body?: string; from_email?: string; created_at?: string; timestamp?: string }> }>(
      `/api/v1/outreach/replies/${sequenceId}`
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

export const activitiesApi = {
  list: (dealId?: string, contactId?: string) => {
    const params = new URLSearchParams();
    if (dealId) params.set("deal_id", dealId);
    if (contactId) params.set("contact_id", contactId);
    return requestList<Activity>(`/api/v1/activities/?${params}`);
  },
  create: (data: Partial<Activity>) =>
    request<Activity>("/api/v1/activities/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

export const tasksApi = {
  list: (entityType: "company" | "contact" | "deal", entityId: string, includeClosed = true) =>
    request<TaskItem[]>(`/api/v1/tasks/?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}&include_closed=${includeClosed ? "true" : "false"}`),
  workspace: (params?: {
    includeClosed?: boolean;
    taskType?: "manual" | "system";
    entityType?: "company" | "contact" | "deal";
    scope?: "mine" | "team";
  }) => {
    const search = new URLSearchParams();
    search.set("include_closed", params?.includeClosed ? "true" : "false");
    if (params?.taskType) search.set("task_type", params.taskType);
    if (params?.entityType) search.set("entity_type", params.entityType);
    if (params?.scope) search.set("scope", params.scope);
    return request<TaskWorkspaceItem[]>(`/api/v1/tasks/workspace?${search}`);
  },
  create: (data: {
    entity_type: "company" | "contact" | "deal";
    entity_id: string;
    title: string;
    description?: string;
    priority?: "low" | "medium" | "high";
    due_at?: string;
    assigned_role?: "admin" | "ae" | "sdr";
    assigned_to_id?: string;
  }) =>
    request<TaskItem>("/api/v1/tasks/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: {
    title?: string;
    description?: string;
    priority?: "low" | "medium" | "high";
    due_at?: string | null;
    status?: "open" | "completed" | "dismissed";
    assigned_role?: "admin" | "ae" | "sdr" | null;
    assigned_to_id?: string | null;
  }) =>
    request<TaskItem>(`/api/v1/tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  addComment: (id: string, body: string) =>
    request<TaskComment>(`/api/v1/tasks/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),
  accept: (id: string) =>
    request<TaskItem>(`/api/v1/tasks/${id}/accept`, {
      method: "POST",
    }),
  remove: (id: string) =>
    request<void>(`/api/v1/tasks/${id}`, {
      method: "DELETE",
    }),
  countOpen: () =>
    request<{ open: number }>("/api/v1/tasks/count"),
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

export const meetingsApi = {
  list: (skip = 0, limit = 50, companyId?: string, dealId?: string, status?: string | string[]) => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
    if (companyId) params.set("company_id", companyId);
    if (dealId) params.set("deal_id", dealId);
    if (Array.isArray(status)) {
      for (const value of status) params.append("status", value);
    } else if (status) {
      params.set("status", status);
    }
    return requestList<Meeting>(`/api/v1/meetings/?${params}`);
  },
  listPaginated: (params: {
    skip?: number;
    limit?: number;
    companyId?: string;
    dealId?: string;
    status?: string[];
    meetingType?: string[];
    assigneeId?: string[];
    linkState?: string[];
    hasIntel?: boolean;
    order?: "asc" | "desc";
  }) => {
    const search = new URLSearchParams({
      skip: String(params.skip ?? 0),
      limit: String(params.limit ?? 50),
    });
    if (params.companyId) search.set("company_id", params.companyId);
    if (params.dealId) search.set("deal_id", params.dealId);
    for (const value of params.status ?? []) search.append("status", value);
    for (const value of params.meetingType ?? []) search.append("meeting_type", value);
    for (const value of params.assigneeId ?? []) search.append("assignee_id", value);
    for (const value of params.linkState ?? []) search.append("link_state", value);
    if (params.hasIntel !== undefined) search.set("has_intel", params.hasIntel ? "true" : "false");
    if (params.order) search.set("order", params.order);
    return requestPaginated<Meeting>(`/api/v1/meetings/?${search.toString()}`);
  },
  get: (id: string) => request<Meeting>(`/api/v1/meetings/${id}`),
  create: (data: Partial<Meeting>) =>
    request<Meeting>("/api/v1/meetings/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<Meeting>) =>
    request<Meeting>(`/api/v1/meetings/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  delete: (id: string) => request<void>(`/api/v1/meetings/${id}`, { method: "DELETE" }),
  generatePreBrief: (id: string) =>
    request<{ meeting_id: string; pre_brief: string }>(`/api/v1/meetings/${id}/pre-brief`, {
      method: "POST",
    }),
  runIntelligence: (id: string) =>
    request<{ meeting_id: string; research_data: unknown; demo_strategy: string }>(
      `/api/v1/meetings/${id}/intelligence`,
      { method: "POST" }
    ),
  generateDemoStrategy: (id: string) =>
    request<{ meeting_id: string; demo_strategy: string }>(
      `/api/v1/meetings/${id}/demo-strategy`,
      { method: "POST" }
    ),
  postScore: (id: string, rawNotes: string) =>
    request<{
      meeting_id: string;
      meeting_score?: number;
      what_went_right?: string;
      what_went_wrong?: string;
      next_steps?: string;
      mom_draft?: string;
    }>(`/api/v1/meetings/${id}/post-score`, {
      method: "POST",
      body: JSON.stringify({ raw_notes: rawNotes }),
    }),
  getResearchGaps: (id: string) =>
    request<{ gaps: Array<{ key: string; label: string }>; count: number }>(
      `/api/v1/meetings/${id}/research-gaps`
    ),
  researchMore: (id: string) =>
    request<{ filled: string[]; gaps_detected: string[]; message: string }>(
      `/api/v1/meetings/${id}/research-more`,
      { method: "POST" }
    ),
};

export const battlecardsApi = {
  list: (category?: string) => {
    const qs = category ? `?category=${encodeURIComponent(category)}` : "";
    return request<Battlecard[]>(`/api/v1/battlecards/${qs}`);
  },
  search: (query: string) =>
    request<Battlecard[]>(`/api/v1/battlecards/search?q=${encodeURIComponent(query)}`),
  get: (id: string) => request<Battlecard>(`/api/v1/battlecards/${id}`),
  create: (data: Partial<Battlecard>) =>
    request<Battlecard>("/api/v1/battlecards/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<Battlecard>) =>
    request<Battlecard>(`/api/v1/battlecards/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  delete: (id: string) => request<void>(`/api/v1/battlecards/${id}`, { method: "DELETE" }),
  seed: () => request<{ seeded: number; message: string }>("/api/v1/battlecards/seed", { method: "POST" }),
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

    // File uploads skip request() because the browser must set the multipart
    // boundary header automatically.
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

// ── Custom Demo ───────────────────────────────────────────────────────────────

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
    // Same multipart caveat as prospecting uploads: no JSON content-type header.
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

// ── Account Sourcing ──────────────────────────────────────────────────────────

export const accountSourcingApi = {
  upload: async (file: File): Promise<SourcingBatch> => {
    const form = new FormData();
    form.append("file", file);
    // The backend kicks off enrichment work from this upload, so callers only
    // receive the batch record and poll for progress afterward.
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

  listBatches: () =>
    requestList<SourcingBatch>("/api/v1/account-sourcing/batches"),

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

  listCompaniesPaginated: (params?: {
    skip?: number;
    limit?: number;
    q?: string;
    icpTier?: string[];
    disposition?: string[];
    recommendedOutreachLane?: string[];
    assignedRepEmail?: string;
  }) => {
    const search = new URLSearchParams({
      skip: String(params?.skip ?? 0),
      limit: String(params?.limit ?? 50),
    });
    if (params?.q) search.set("q", params.q);
    if (params?.icpTier?.length) search.set("icp_tier", params.icpTier.join(","));
    if (params?.disposition?.length) search.set("disposition", params.disposition.join(","));
    if (params?.recommendedOutreachLane?.length) search.set("recommended_outreach_lane", params.recommendedOutreachLane.join(","));
    if (params?.assignedRepEmail) search.set("assigned_rep_email", params.assignedRepEmail);
    return requestPaginated<Company>(`/api/v1/account-sourcing/companies?${search}`);
  },

  summary: (assignedRepEmail?: string) =>
    request<AccountSourcingSummary>(
      `/api/v1/account-sourcing/summary${assignedRepEmail ? `?assigned_rep_email=${encodeURIComponent(assignedRepEmail)}` : ""}`
    ),

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

  exportCsv: async (params?: { assignedRep?: string; assignedRepEmail?: string; disposition?: string; batchId?: string }) => {
    const search = new URLSearchParams();
    if (params?.assignedRep) search.set("assigned_rep", params.assignedRep);
    if (params?.assignedRepEmail) search.set("assigned_rep_email", params.assignedRepEmail);
    if (params?.disposition) search.set("disposition", params.disposition);
    if (params?.batchId) search.set("batch_id", params.batchId);
    const qs = search.toString();
    const res = await fetch(`${BASE}/api/v1/account-sourcing/export${qs ? `?${qs}` : ""}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? "Export failed");
    }
    return res.blob();
  },

  exportContactsCsv: async (params?: { assignedRepEmail?: string; batchId?: string }) => {
    const search = new URLSearchParams();
    if (params?.assignedRepEmail) search.set("assigned_rep_email", params.assignedRepEmail);
    if (params?.batchId) search.set("batch_id", params.batchId);
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

// ── Workspace ────────────────────────────────────────────────────────────────

export type WorkspaceSummary = {
  open_deals: number;
  total_companies: number;
  total_contacts: number;
  scheduled_meetings: number;
  alerts_count: number;
};

export type WorkspaceAlert = {
  id: string;
  type: string;
  severity: "high" | "medium" | "low";
  title: string;
  description: string;
  entity_id?: string;
  entity_name?: string;
  entity_type?: string;
  link?: string;
  created_at: string;
};

export type WorkspaceInsightTone = "blue" | "green" | "amber" | "red";

export type WorkspaceInsightMetric = {
  key: string;
  label: string;
  value: string;
  hint: string;
  tone: WorkspaceInsightTone;
  link?: string;
};

export type WorkspaceInsightBucket = {
  key: string;
  label: string;
  count: number;
  amount?: number | null;
  tone: WorkspaceInsightTone;
};

export type WorkspaceInsightQueue = {
  key: string;
  label: string;
  count: number;
  hint: string;
  tone: WorkspaceInsightTone;
  link: string;
};

export type WorkspaceInsights = {
  generated_at: string;
  metrics: WorkspaceInsightMetric[];
  deal_stage_mix: WorkspaceInsightBucket[];
  deal_health_mix: WorkspaceInsightBucket[];
  prospect_stage_mix: WorkspaceInsightBucket[];
  meeting_readiness_mix: WorkspaceInsightBucket[];
  focus_queues: WorkspaceInsightQueue[];
  alerts: WorkspaceAlert[];
};

export type StageStatus = {
  stage: string;
  status: "ready" | "needs_action" | "blocked";
  count: number;
  blockers: string[];
  actions: string[];
};

export const workspaceApi = {
  summary: () =>
    request<WorkspaceSummary>("/api/v1/workspace/summary"),
  alerts: () =>
    request<WorkspaceAlert[]>("/api/v1/workspace/alerts"),
  insights: () =>
    request<WorkspaceInsights>("/api/v1/workspace/insights"),
  stageStatus: (stage: string) =>
    request<StageStatus>(`/api/v1/workspace/stages/${stage}`),
};

export type MilestoneDealRow = {
  milestone_key: string;
  deal_name: string | null;
  company_name: string | null;
  reached_at: string;
  close_date_est: string | null;
  deal_value: number | null;
};

export type SalesDashboardSummary = {
  pipeline_amount: number;
  weighted_pipeline_amount: number;
  forecast_amount: number;
  active_deals: number;
  average_deal_size: number;
  overdue_close_count: number;
  missing_close_date_count: number;
  stale_deal_count: number;
  demo_done_count: number;
  poc_agreed_count: number;
  poc_done_count: number;
  closed_won_count: number;
  closed_won_value: number;
  milestone_deals: MilestoneDealRow[];
};

export type SalesRepActivityRow = {
  key: string;
  user_id?: string | null;
  rep_name: string;
  calls: number;
  emails: number;
  meetings: number;
  total: number;
  active_deals: number;
  pipeline_amount: number;
};

export type SalesStageBucket = {
  key: string;
  label: string;
  color: string;
  deal_count: number;
  amount: number;
  weighted_amount: number;
};

export type SalesPipelineOwnerRow = {
  key: string;
  user_id?: string | null;
  rep_name: string;
  deal_count: number;
  amount: number;
  weighted_amount: number;
  stages: SalesStageBucket[];
};

export type SalesVelocityRow = {
  key: string;
  label: string;
  color: string;
  deal_count: number;
  average_days_in_stage: number;
  stale_deals: number;
};

export type SalesForecastRow = {
  key: string;
  label: string;
  deal_count: number;
  amount: number;
  weighted_amount: number;
};

export type SalesFunnelStep = {
  key: string;
  label: string;
  count: number;
  conversion_from_previous?: number | null;
};

export type MonthlyUniqueFunnelRow = {
  month_key: string;
  label: string;
  demo_done: number;
  poc_agreed: number;
  poc_wip: number;
  poc_done: number;
  closed_won: number;
};

export type SalesQuotaState = {
  configured: boolean;
  title: string;
  message: string;
};

export type SalesDashboard = {
  generated_at: string;
  window_days: number;
  from_date?: string | null;
  to_date?: string | null;
  summary: SalesDashboardSummary;
  highlights: string[];
  rep_activity: SalesRepActivityRow[];
  pipeline_by_stage: SalesStageBucket[];
  pipeline_by_owner: SalesPipelineOwnerRow[];
  velocity_by_stage: SalesVelocityRow[];
  forecast_by_month: SalesForecastRow[];
  conversion_funnel: SalesFunnelStep[];
  monthly_unique_funnel: MonthlyUniqueFunnelRow[];
  quota: SalesQuotaState;
};

export const analyticsApi = {
  salesDashboard: (windowDays = 90, repIds: string[] = [], geographies: string[] = [], fromDate?: string, toDate?: string) => {
    const params = new URLSearchParams({ window_days: String(windowDays) });
    for (const id of repIds) params.append("rep_id", id);
    for (const g of geographies) params.append("geography", g);
    if (fromDate) params.set("from_date", fromDate);
    if (toDate) params.set("to_date", toDate);
    return request<SalesDashboard>(`/api/v1/analytics/sales-dashboard?${params.toString()}`);
  },
  monthlyFunnelSummary: (months = 12) =>
    request<MonthlyUniqueFunnelRow[]>(`/api/v1/analytics/monthly-funnel-summary?months=${months}`),
};

export const globalSearchApi = {
  search: (query: string) =>
    request<GlobalSearchResponse>(`/api/v1/search/global?q=${encodeURIComponent(query)}`),
};

// ── Knowledge Base / Sales Resources ────────────────────────────────────────

export const resourcesApi = {
  list: (skip = 0, limit = 50, category?: string, module?: string, q?: string) => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
    if (category) params.set("category", category);
    if (module) params.set("module", module);
    if (q) params.set("q", q);
    return requestPaginated<SalesResource>(`/api/v1/resources?${params}`);
  },
  get: (id: string) => request<SalesResource>(`/api/v1/resources/${id}`),
  create: (data: {
    title: string;
    category: string;
    description?: string;
    content: string;
    tags?: string[];
    modules?: string[];
  }) =>
    request<SalesResource>("/api/v1/resources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  upload: (file: File, meta: {
    title: string;
    category: string;
    description?: string;
    tags?: string[];
    modules?: string[];
  }) => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", meta.title);
    form.append("category", meta.category);
    if (meta.description) form.append("description", meta.description);
    form.append("tags", JSON.stringify(meta.tags ?? []));
    form.append("modules", JSON.stringify(meta.modules ?? []));
    return request<SalesResource>("/api/v1/resources/upload", {
      method: "POST",
      body: form,
    });
  },
  update: (id: string, data: Partial<{
    title: string;
    category: string;
    description: string;
    content: string;
    tags: string[];
    modules: string[];
    is_active: boolean;
  }>) =>
    request<SalesResource>(`/api/v1/resources/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  delete: (id: string) =>
    request<void>(`/api/v1/resources/${id}`, { method: "DELETE" }),
  options: () =>
    request<{ categories: string[]; modules: string[] }>("/api/v1/resources/meta/options"),
};

// ── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  me: () => request<User>("/api/v1/auth/me"),
  googleLoginUrl: () => `${BASE}/api/v1/auth/google/login`,
  listAllUsers: () => request<User[]>("/api/v1/auth/users/all"),
  listUsers: () => request<User[]>("/api/v1/auth/users"),
  updateUser: (userId: string, data: { name?: string; role?: string; is_active?: boolean }) =>
    request<User>(`/api/v1/auth/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  seedUsers: (users: { email: string; name: string; role?: string }[]) =>
    request<{ created: number; skipped: number; users: User[] }>("/api/v1/auth/users/seed", {
      method: "POST",
      body: JSON.stringify({ users }),
    }),
  deleteUser: (userId: string) =>
    request<{ status: string; user_id: string }>(`/api/v1/auth/users/${userId}`, {
      method: "DELETE",
    }),
};

// ── Assignments ──────────────────────────────────────────────────────────────

export const angelMappingApi = {
  // Angel Investors
  listInvestors: () =>
    request<AngelInvestor[]>("/api/v1/angel-mapping/investors?limit=500"),
  getInvestor: (id: string) =>
    request<AngelInvestor>(`/api/v1/angel-mapping/investors/${id}`),
  createInvestor: (data: Partial<AngelInvestor>) =>
    request<AngelInvestor>("/api/v1/angel-mapping/investors", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateInvestor: (id: string, data: Partial<AngelInvestor>) =>
    request<AngelInvestor>(`/api/v1/angel-mapping/investors/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteInvestor: (id: string) =>
    request<void>(`/api/v1/angel-mapping/investors/${id}`, { method: "DELETE" }),

  // Angel Mappings
  listMappings: (params?: {
    contact_id?: string;
    company_id?: string;
    angel_investor_id?: string;
    min_strength?: number;
  }) => {
    const qs = new URLSearchParams();
    qs.set("limit", "500");
    if (params?.contact_id) qs.set("contact_id", params.contact_id);
    if (params?.company_id) qs.set("company_id", params.company_id);
    if (params?.angel_investor_id) qs.set("angel_investor_id", params.angel_investor_id);
    if (params?.min_strength) qs.set("min_strength", String(params.min_strength));
    return request<AngelMapping[]>(`/api/v1/angel-mapping/mappings?${qs}`);
  },
  createMapping: (data: {
    contact_id: string;
    company_id?: string;
    angel_investor_id: string;
    strength: number;
    rank: number;
    connection_path?: string;
    why_it_works?: string;
    recommended_strategy?: string;
  }) =>
    request<AngelMapping>("/api/v1/angel-mapping/mappings", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateMapping: (id: string, data: Partial<AngelMapping>) =>
    request<AngelMapping>(`/api/v1/angel-mapping/mappings/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteMapping: (id: string) =>
    request<void>(`/api/v1/angel-mapping/mappings/${id}`, { method: "DELETE" }),

  // Bulk Import
  bulkImport: (rows: Array<Record<string, unknown>>) =>
    request<{
      investors_created: number;
      mappings_created: number;
      companies_updated: number;
      errors: string[];
    }>("/api/v1/angel-mapping/import", {
      method: "POST",
      body: JSON.stringify({ rows }),
    }),
};

export const assignmentsApi = {
  assignCompany: (companyId: string, userId: string | null, role: "ae" | "sdr" = "ae") =>
    request<Company>(`/api/v1/assignments/company/${companyId}`, {
      method: "PATCH",
      body: JSON.stringify({ user_id: userId, role }),
    }),
  assignContact: (contactId: string, userId: string | null, role: "ae" | "sdr" = "ae") =>
    request<Contact>(`/api/v1/assignments/contact/${contactId}`, {
      method: "PATCH",
      body: JSON.stringify({ user_id: userId, role }),
    }),
  bulkAssignCompanies: (ids: string[], userId: string | null) =>
    request<{ updated: number; user_id: string | null }>("/api/v1/assignments/bulk-companies", {
      method: "PATCH",
      body: JSON.stringify({ ids, user_id: userId }),
    }),
  bulkAssignContacts: (ids: string[], userId: string | null) =>
    request<{ updated: number; user_id: string | null }>("/api/v1/assignments/bulk-contacts", {
      method: "PATCH",
      body: JSON.stringify({ ids, user_id: userId }),
    }),
};

export const executionTrackerApi = {
  listItems: (params?: {
    skip?: number;
    limit?: number;
    assigneeId?: string;
    entityType?: "company" | "contact" | "deal";
    progressState?: string;
    needsUpdateOnly?: boolean;
    q?: string;
  }) => {
    const search = new URLSearchParams({
      skip: String(params?.skip ?? 0),
      limit: String(params?.limit ?? 25),
    });
    if (params?.assigneeId) search.set("assignee_id", params.assigneeId);
    if (params?.entityType) search.set("entity_type", params.entityType);
    if (params?.progressState) search.set("progress_state", params.progressState);
    if (params?.needsUpdateOnly) search.set("needs_update_only", "true");
    if (params?.q) search.set("q", params.q);
    return requestPaginated<ExecutionTrackerItem>(`/api/v1/execution-tracker/items?${search}`);
  },
  summary: (params?: {
    assigneeId?: string;
    entityType?: "company" | "contact" | "deal";
    progressState?: string;
    needsUpdateOnly?: boolean;
    q?: string;
  }) => {
    const search = new URLSearchParams();
    if (params?.assigneeId) search.set("assignee_id", params.assigneeId);
    if (params?.entityType) search.set("entity_type", params.entityType);
    if (params?.progressState) search.set("progress_state", params.progressState);
    if (params?.needsUpdateOnly) search.set("needs_update_only", "true");
    if (params?.q) search.set("q", params.q);
    return request<ExecutionTrackerSummary>(`/api/v1/execution-tracker/summary${search.toString() ? `?${search}` : ""}`);
  },
  getUpdates: (entityType: string, entityId: string, assignmentRole: string) =>
    request<AssignmentUpdate[]>(
      `/api/v1/execution-tracker/items/${entityType}/${entityId}/updates?assignment_role=${encodeURIComponent(assignmentRole)}`
    ),
  createUpdate: (data: {
    entity_type: "company" | "contact" | "deal";
    entity_id: string;
    assignment_role: "owner" | "ae" | "sdr";
    progress_state: string;
    confidence: string;
    buyer_signal: string;
    blocker_type: string;
    last_touch_type: string;
    summary: string;
    next_step: string;
    next_step_due_date?: string;
    blocker_detail?: string;
  }) =>
    request<AssignmentUpdate>("/api/v1/execution-tracker/updates", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

export const settingsApi = {
  getOutreach: () =>
    request<{ step_delays: number[]; steps_count: number; steps: Array<{ step_number: number; day: number; channel: "email" | "call" | "linkedin" }> }>("/api/v1/settings/outreach"),
  updateOutreach: (steps: Array<{ step_number: number; day: number; channel: "email" | "call" | "linkedin" }>) =>
    request<{ step_delays: number[]; steps_count: number; steps: Array<{ step_number: number; day: number; channel: "email" | "call" | "linkedin" }> }>("/api/v1/settings/outreach", {
      method: "PATCH",
      body: JSON.stringify({ steps }),
    }),
  getOutreachContent: () =>
    request<OutreachContentSettings>("/api/v1/settings/outreach-content"),
  updateOutreachContent: (config: OutreachContentSettings) =>
    request<OutreachContentSettings>("/api/v1/settings/outreach-content", {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  getPipelineSummarySettings: () =>
    request<PipelineSummarySettings>("/api/v1/settings/pipeline-summary"),
  updatePipelineSummarySettings: (config: PipelineSummarySettings) =>
    request<PipelineSummarySettings>("/api/v1/settings/pipeline-summary", {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  getDealStages: () =>
    request<DealStageSettings>("/api/v1/settings/deal-stages"),
  updateDealStages: (config: DealStageSettings) =>
    request<DealStageSettings>("/api/v1/settings/deal-stages", {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  getProspectStages: () =>
    request<ProspectStageSettings>("/api/v1/settings/prospect-stages"),
  updateProspectStages: (config: ProspectStageSettings) =>
    request<ProspectStageSettings>("/api/v1/settings/prospect-stages", {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  getClickUpCrmSettings: () =>
    request<ClickUpCrmSettings>("/api/v1/settings/clickup-crm"),
  updateClickUpCrmSettings: (config: ClickUpCrmSettings) =>
    request<ClickUpCrmSettings>("/api/v1/settings/clickup-crm", {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  getRolePermissions: () =>
    request<RolePermissionsSettings>("/api/v1/settings/role-permissions"),
  updateRolePermissions: (config: RolePermissionsSettings) =>
    request<RolePermissionsSettings>("/api/v1/settings/role-permissions", {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  getPreMeetingAutomation: () =>
    request<PreMeetingAutomationSettings>("/api/v1/settings/pre-meeting-automation"),
  updatePreMeetingAutomation: (config: PreMeetingAutomationSettings) =>
    request<PreMeetingAutomationSettings>("/api/v1/settings/pre-meeting-automation", {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  runPreMeetingAutomationNow: () =>
    request<{ checked: number; generated: number; emailed: number; skipped: number }>("/api/v1/settings/pre-meeting-automation/run-now", {
      method: "POST",
    }),
  getZippySystemPrompt: () =>
    request<{ prompt: string; is_default: boolean }>("/api/v1/settings/zippy-system-prompt"),
  updateZippySystemPrompt: (prompt: string) =>
    request<{ prompt: string; is_default: boolean }>("/api/v1/settings/zippy-system-prompt", {
      method: "PATCH",
      body: JSON.stringify({ prompt }),
    }),
  getGmailSync: () =>
    request<GmailSyncSettings>("/api/v1/settings/email-sync"),
  updateGmailInbox: (inbox: string) =>
    request<GmailSyncSettings>("/api/v1/settings/email-sync", {
      method: "PATCH",
      body: JSON.stringify({ inbox }),
    }),
  getGmailConnectUrl: () =>
    request<{ url: string }>("/api/v1/settings/email-sync/google/connect-url"),
  disconnectGmail: () =>
    request<{ status: string }>("/api/v1/settings/email-sync/google", {
      method: "DELETE",
    }),
  triggerEmailSync: () =>
    request<{ status: string; task_id?: string; message?: string }>("/api/v1/email-sync/trigger", {
      method: "POST",
    }),
  getSyncSchedule: () =>
    request<SyncScheduleSettings>("/api/v1/settings/sync-schedule"),
  updateSyncSchedule: (data: Partial<SyncScheduleSettings>) =>
    request<SyncScheduleSettings>("/api/v1/settings/sync-schedule", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  triggerTldvSync: () =>
    request<{ status: string }>("/api/v1/settings/sync-schedule/tldv-now", {
      method: "POST",
    }),
  stopTldvSync: () =>
    request<{ status: string; tldv_sync_enabled: boolean }>("/api/v1/settings/sync-schedule/tldv-stop", {
      method: "POST",
    }),
};

export const aircallApi = {
  getConfig: () =>
    request<{
      configured: boolean;
      numbers: { id: number; digits: string; name: string }[];
      users: { id: number; name: string; email: string }[];
      default_number: { id: number; digits: string; name: string } | null;
    }>("/api/v1/aircall/config"),
  getUserByEmail: (email: string) =>
    request<{ found: boolean; aircall_user_id?: number; name?: string; availability?: string }>(
      `/api/v1/aircall/user-by-email?email=${encodeURIComponent(email)}`
    ),
  getAvailabilities: () =>
    request<{ id: number; name: string; availability_status: string }[]>("/api/v1/aircall/availabilities"),
  initiateCall: (to: string, user_id: number, number_id: number) =>
    request<{ success: boolean }>("/api/v1/aircall/call", {
      method: "POST",
      body: JSON.stringify({ to, user_id, number_id }),
    }),
  registerWebhook: () =>
    request<{ status: string }>("/api/v1/aircall/register-webhook", { method: "POST" }),
};

// ── Reminders ───────────────────────────────────────────────────────────────

export const remindersApi = {
  list: (params?: { contact_id?: string; company_id?: string; status?: string; assigned_to_id?: string }) => {
    const search = new URLSearchParams();
    if (params?.contact_id) search.set("contact_id", params.contact_id);
    if (params?.company_id) search.set("company_id", params.company_id);
    if (params?.status) search.set("status", params.status);
    if (params?.assigned_to_id) search.set("assigned_to_id", params.assigned_to_id);
    return request<Reminder[]>(`/api/v1/reminders/?${search}`);
  },
  create: (data: { contact_id: string; company_id?: string; note: string; due_at: string; assigned_to_id?: string }) =>
    request<Reminder>("/api/v1/reminders/", { method: "POST", body: JSON.stringify(data) }),
  update: (id: string, data: Partial<Reminder>) =>
    request<Reminder>(`/api/v1/reminders/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  delete: (id: string) =>
    request<void>(`/api/v1/reminders/${id}`, { method: "DELETE" }),
};

// ── Personal Email Sync ──────────────────────────────────────────────────────

export interface PersonalEmailStatus {
  connected: boolean;
  email_address?: string;
  last_sync_epoch?: number;
  backfill_completed: boolean;
  last_error?: string;
}

export interface PersonalEmailThread {
  thread_id: string;
  subject: string;
  message_count: number;
  latest_at: string;
  synced_by_email: string;
  messages: {
    id: string;
    message_id: string;
    subject: string;
    from_addr: string;
    to_addrs: string;
    cc_addrs: string;
    body_preview: string;
    ai_summary?: string;
    intent_detected?: string;
    synced_by_email: string;
    created_at: string;
  }[];
}

export const personalEmailSyncApi = {
  getStatus: () =>
    request<PersonalEmailStatus>("/api/v1/personal-email-sync/status"),
  getConnectUrl: () =>
    request<{ url: string }>("/api/v1/personal-email-sync/connect"),
  trigger: () =>
    request<{ status: string; task_id: string; email_address: string }>(
      "/api/v1/personal-email-sync/trigger",
      { method: "POST" }
    ),
  disconnect: () =>
    request<{ status: string; email_address?: string }>(
      "/api/v1/personal-email-sync/disconnect",
      { method: "POST" }
    ),
  getThreadsForDeal: (dealId: string) =>
    request<{ deal_id: string; threads: PersonalEmailThread[]; total: number }>(
      `/api/v1/personal-email-sync/threads/${dealId}`
    ),
};

// ── Google Drive folder picker ──────────────────────────────────────────────

export interface DriveFolder {
  id: string;
  name: string;
  parents: string[];
  modified_time?: string;
  owned_by_me: boolean;
  shared: boolean;
  drive_id?: string;
}

export interface DriveFolderList {
  folders: DriveFolder[];
  parent_id?: string;
}

export interface SelectedDriveFolder {
  folder_id?: string;
  folder_name?: string;
  is_admin_folder: boolean;
  owner_email?: string;
}

export const driveApi = {
  listFolders: (parentId?: string) => {
    const qs = parentId ? `?parent_id=${encodeURIComponent(parentId)}` : "";
    return request<DriveFolderList>(`/api/v1/drive/folders${qs}`);
  },
  searchFolders: (q: string) =>
    request<DriveFolderList>(`/api/v1/drive/folders/search?q=${encodeURIComponent(q)}`),
  selectFolder: (folderId: string, folderName?: string) =>
    request<SelectedDriveFolder>(`/api/v1/drive/folder/select`, {
      method: "POST",
      body: JSON.stringify({ folder_id: folderId, folder_name: folderName }),
    }),
  selectAdminFolder: (folderId: string, folderName?: string) =>
    request<SelectedDriveFolder>(`/api/v1/drive/folder/select-admin`, {
      method: "POST",
      body: JSON.stringify({ folder_id: folderId, folder_name: folderName }),
    }),
  getCurrentFolder: () =>
    request<SelectedDriveFolder>(`/api/v1/drive/folder/current`),
  getAdminFolder: () =>
    request<SelectedDriveFolder>(`/api/v1/drive/folder/admin`),
  clearFolder: () =>
    request<SelectedDriveFolder>(`/api/v1/drive/folder/clear`, { method: "POST" }),
};

// ── Zippy (RAG Copilot) ──────────────────────────────────────────────────────

export interface ZippyCitation {
  source_id: string;
  source_name: string;
  source_type: string;
  drive_url: string;
  mime_type: string;
  chunk_index: number;
  score: number;
  snippet: string;
}

export interface ZippyArtifact {
  type: string;
  filename: string;
  url: string;              // relative path, prefix with API_BASE
  drive_url?: string;       // absolute Google Docs URL when uploaded
  drive_file_id?: string;
  summary: string;
  created_at: string;
}

export interface ZippyMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  citations?: ZippyCitation[] | null;
  artifacts?: ZippyArtifact[] | null;
  created_at: string;
}

export interface ZippyConversationSummary {
  id: string;
  title: string;
  summary: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ZippyConversationDetail {
  id: string;
  title: string;
  summary: string | null;
  messages: ZippyMessage[];
  created_at: string;
  updated_at: string;
}

export interface ZippySendResponse {
  conversation_id: string;
  message: ZippyMessage;
}

export const zippyApi = {
  send: (payload: {
    message: string;
    conversation_id?: string | null;
    source_ids?: string[] | null;
    image_base64?: string;
    image_media_type?: string;
  }) =>
    request<ZippySendResponse>(`/api/v1/zippy/send`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listConversations: (limit = 30) =>
    request<ZippyConversationSummary[]>(
      `/api/v1/zippy/conversations?limit=${limit}`,
    ),
  getConversation: (id: string) =>
    request<ZippyConversationDetail>(`/api/v1/zippy/conversations/${id}`),
  archive: (id: string, archived = true) =>
    request<{ id: string; is_archived: boolean }>(
      `/api/v1/zippy/conversations/${id}/archive`,
      {
        method: "POST",
        body: JSON.stringify({ is_archived: archived }),
      },
    ),
};

// ── Knowledge / Drive index ──────────────────────────────────────────────────

export interface IndexedFile {
  id: string;
  drive_file_id: string;
  name: string;
  mime_type: string;
  web_view_link: string;
  size_bytes: number | null;
  qdrant_chunk_count: number;
  last_indexed_at: string | null;
  last_error: string | null;
  is_admin: boolean;
}

export interface IndexReport {
  folder_id: string;
  folder_name: string;
  scope: "admin" | "user";
  files_scanned: number;
  files_indexed: number;
  files_skipped_unchanged: number;
  files_skipped_unsupported: number;
  files_failed: number;
  chunks_written: number;
  errors: string[];
}

export interface ReindexResponse {
  ok: boolean;
  report: IndexReport | Record<string, unknown>;
}

export interface IndexStatus {
  folder_id: string | null;
  folder_name: string | null;
  is_admin_folder: boolean;
  total_files: number;
  successful: number;
  failed: number;
  skipped?: number;
  total_chunks: number;
  files: IndexedFile[];
}

export const knowledgeApi = {
  status: (scope: "user" | "admin" = "user") =>
    request<IndexStatus>(`/api/v1/knowledge/status?scope=${scope}`),
  reindex: (force = false) =>
    request<ReindexResponse>(`/api/v1/knowledge/reindex?force=${force}`, {
      method: "POST",
    }),
  reindexAdmin: (force = false) =>
    request<ReindexResponse>(`/api/v1/knowledge/reindex-admin?force=${force}`, {
      method: "POST",
    }),
  reset: () =>
    request<ReindexResponse>(`/api/v1/knowledge/reset`, { method: "POST" }),
  resetAdmin: () =>
    request<ReindexResponse>(`/api/v1/knowledge/reset-admin`, {
      method: "POST",
    }),
};

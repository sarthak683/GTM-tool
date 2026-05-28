import type {
  Activity,
  Battlecard,
  CallRecording,
  Company,
  Contact,
  CrmImportResponse,
  Deal,
  Meeting,
  MeetingPrepMonitor,
  ProspectImportResponse,
} from "../../types";
import { BASE, getAuthHeaders, request, requestList, requestPaginated } from "./core";

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
    qField?: string;
    qMatch?: "exact" | "contains";
    persona?: string[];
    sequenceStatus?: string[];
    callDisposition?: string[];
    emailState?: string[];
    // Outcome-color filters wired to the prospect-row progress dots.
    // Backend maps each color to a set of dispositions / sequence states
    // (see app/repositories/contact.py). Multi-value = OR.
    callOutcomeColor?: string[];
    emailOutcomeColor?: string[];
    // Call attempts bucket — values: "0" | "1" | "2" | "3" | "4plus".
    callAttemptsBucket?: string[];
    aeId?: string[];
    sdrId?: string[];
    ownerId?: string | string[];
    scopeAnyMatch?: boolean;
    prospectOnly?: boolean;
    timezone?: string[];
    sortBy?: "name" | "first_name" | "last_name" | "company" | "email" | "title" | "created_at";
    sortDir?: "asc" | "desc";
  }) => {
    const search = new URLSearchParams({
      skip: String(params.skip ?? 0),
      limit: String(params.limit ?? 50),
    });
    if (params.companyId) search.set("company_id", params.companyId);
    if (params.q) search.set("q", params.q);
    if (params.qField && params.qField !== "all") search.set("q_field", params.qField);
    if (params.qMatch && params.qField && params.qField !== "all") search.set("q_match", params.qMatch);
    if (params.persona?.length) search.set("persona", params.persona.join(","));
    if (params.sequenceStatus?.length) search.set("sequence_status", params.sequenceStatus.join(","));
    if (params.callDisposition?.length) search.set("call_disposition", params.callDisposition.join(","));
    if (params.emailState?.length) search.set("email_state", params.emailState.join(","));
    if (params.callOutcomeColor?.length) search.set("call_outcome_color", params.callOutcomeColor.join(","));
    if (params.emailOutcomeColor?.length) search.set("email_outcome_color", params.emailOutcomeColor.join(","));
    if (params.callAttemptsBucket?.length) search.set("call_attempts_bucket", params.callAttemptsBucket.join(","));
    if (params.aeId?.length) search.set("ae_id", params.aeId.join(","));
    if (params.sdrId?.length) search.set("sdr_id", params.sdrId.join(","));
    if (params.ownerId) {
      const ownerValue = Array.isArray(params.ownerId) ? params.ownerId.join(",") : params.ownerId;
      if (ownerValue) search.set("owner_id", ownerValue);
    }
    if (params.scopeAnyMatch) search.set("scope_any_match", "true");
    if (params.prospectOnly) search.set("prospect_only", "true");
    if (params.timezone?.length) search.set("timezone", params.timezone.join(","));
    if (params.sortBy) search.set("sort_by", params.sortBy);
    if (params.sortDir) search.set("sort_dir", params.sortDir);
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
  getPrecallBrief: (id: string) =>
    request<PreCallBrief>(`/api/v1/contacts/${id}/precall-brief`),
  getSequenceLifecycle: (id: string) =>
    request<SequenceLifecycle>(`/api/v1/contacts/${id}/sequence-lifecycle`),
  getLifecycleSummaries: (contactIds: string[]) =>
    request<{ summaries: Record<string, LifecycleSummary> }>(
      "/api/v1/contacts/sequence-lifecycle/summaries",
      {
        method: "POST",
        body: JSON.stringify({ contact_ids: contactIds }),
      }
    ),
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
  importCsv: (
    file: File,
    autoCreateCompanies = false,
    onProgress?: (phase: "uploading" | "processing", percent: number) => void,
  ) => {
    return new Promise<ProspectImportResponse>((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      if (autoCreateCompanies) {
        form.append("auto_create_companies", "true");
      }

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/api/v1/contacts/import-csv`);
      const headers = getAuthHeaders();
      for (const [k, v] of Object.entries(headers)) {
        xhr.setRequestHeader(k, v);
      }

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          const pct = Math.min(100, Math.round((e.loaded / e.total) * 100));
          onProgress("uploading", pct);
        }
      };
      xhr.upload.onload = () => {
        if (onProgress) onProgress("processing", 100);
      };

      xhr.onload = () => {
        if (xhr.status === 401) {
          localStorage.removeItem("beacon_token");
          window.location.href = "/login";
          reject(new Error("Session expired"));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as ProspectImportResponse);
          } catch {
            reject(new Error("Server returned an invalid response."));
          }
          return;
        }
        let detail = xhr.statusText || "Upload failed";
        try {
          const body = JSON.parse(xhr.responseText) as { detail?: string };
          if (body.detail) detail = body.detail;
        } catch {
        }
        reject(new Error(detail));
      };
      xhr.onerror = () => reject(new Error("Network error during upload."));
      xhr.onabort = () => reject(new Error("Upload aborted."));

      xhr.send(form);
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
  getContacts: (dealId: string) =>
    request<import("../../types").DealContact[]>(`/api/v1/deals/${dealId}/contacts`),
  addContact: (dealId: string, contactId: string, role?: string) =>
    request<import("../../types").DealContact>(`/api/v1/deals/${dealId}/contacts`, {
      method: "POST",
      body: JSON.stringify({ contact_id: contactId, role }),
    }),
  removeContact: (dealId: string, contactId: string) =>
    request<void>(`/api/v1/deals/${dealId}/contacts/${contactId}`, { method: "DELETE" }),
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

// Browser-mic call recording flow. `upload` POSTs a multipart blob and
// returns the freshly-created CallRecording row in `uploaded` status.
// The frontend then polls `get` every few seconds until status is
// `ready` (transcript + AI disposition populated) or `failed`.
export const callRecordingsApi = {
  upload: async (params: {
    audio: Blob;
    contactId: string;
    consentAcknowledgedAt: string; // ISO timestamp
    durationSeconds?: number;
  }): Promise<CallRecording> => {
    const form = new FormData();
    // Hand the browser a filename so multipart bookkeeping is clean —
    // the backend ignores the name itself.
    const file = new File([params.audio], `call-${Date.now()}.webm`, {
      type: params.audio.type || "audio/webm",
    });
    form.append("audio", file);
    form.append("contact_id", params.contactId);
    form.append("consent_acknowledged_at", params.consentAcknowledgedAt);
    if (params.durationSeconds != null) {
      form.append("duration_seconds", String(params.durationSeconds));
    }
    // request() forces Content-Type: application/json; for multipart we
    // need fetch directly so the browser sets the multipart boundary.
    const res = await fetch(`${BASE}/api/v1/calls/recordings/`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: form,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Recording upload failed (HTTP ${res.status}): ${text.slice(0, 200)}`);
    }
    return res.json();
  },
  get: (id: string) => request<CallRecording>(`/api/v1/calls/recordings/${id}`),
  listForContact: (contactId: string, limit = 50) =>
    request<CallRecording[]>(`/api/v1/calls/recordings/?contact_id=${contactId}&limit=${limit}`),
  patch: (id: string, payload: { transcript?: string; ai_disposition?: string; ai_summary?: string }) =>
    request<CallRecording>(`/api/v1/calls/recordings/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  retry: (id: string) =>
    request<CallRecording>(`/api/v1/calls/recordings/${id}/retry`, { method: "POST" }),
};

export const activitiesApi = {
  list: (dealId?: string, contactId?: string) => {
    const params = new URLSearchParams();
    if (dealId) params.set("deal_id", dealId);
    if (contactId) params.set("contact_id", contactId);
    return requestList<Activity>(`/api/v1/activities/?${params}`);
  },
  myCountSince: async (sinceIso: string, type?: string) => {
    const params = new URLSearchParams({
      created_by_me: "true",
      since: sinceIso,
      limit: "1",
    });
    if (type) params.set("type", type);
    const res = await requestPaginated<Activity>(`/api/v1/activities/?${params}`);
    return res.total ?? 0;
  },
  myCallsToday: async () => {
    const res = await request<{ total: number }>("/api/v1/activities/me/calls-today");
    return res.total ?? 0;
  },
  create: (data: Partial<Activity>) =>
    request<Activity>("/api/v1/activities/", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<Activity>) =>
    request<Activity>(`/api/v1/activities/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
};

export interface TimelineEvent {
  id: string;
  kind: string;
  occurred_at: string | null;
  title: string;
  subtitle: string | null;
  actor_user_id: string | null;
  deal_id: string | null;
  contact_id: string | null;
  payload: Record<string, unknown>;
}

export const timelineApi = {
  forContact: async (contactId: string, limit = 100) => {
    const res = await request<{ items: TimelineEvent[] }>(
      `/api/v1/contacts/${contactId}/timeline?limit=${limit}`
    );
    return res.items ?? [];
  },
  forDeal: async (dealId: string, limit = 150) => {
    const res = await request<{ items: TimelineEvent[] }>(
      `/api/v1/deals/${dealId}/timeline?limit=${limit}`
    );
    return res.items ?? [];
  },
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
    temporalStatus?: string[];
    meetingType?: string[];
    assigneeId?: string[];
    linkState?: string[];
    hasIntel?: boolean;
    order?: "asc" | "desc";
    q?: string;
    syncedAfter?: string;
    includeInternal?: boolean;
    internalScope?: "exclude" | "include" | "only";
  }) => {
    const search = new URLSearchParams({
      skip: String(params.skip ?? 0),
      limit: String(params.limit ?? 50),
    });
    if (params.companyId) search.set("company_id", params.companyId);
    if (params.dealId) search.set("deal_id", params.dealId);
    for (const value of params.status ?? []) search.append("status", value);
    for (const value of params.temporalStatus ?? []) search.append("temporal_status", value);
    for (const value of params.meetingType ?? []) search.append("meeting_type", value);
    for (const value of params.assigneeId ?? []) search.append("assignee_id", value);
    for (const value of params.linkState ?? []) search.append("link_state", value);
    if (params.hasIntel !== undefined) search.set("has_intel", params.hasIntel ? "true" : "false");
    if (params.order) search.set("order", params.order);
    const qTrimmed = (params.q ?? "").trim();
    if (qTrimmed) search.set("q", qTrimmed);
    if (params.syncedAfter) search.set("synced_after", params.syncedAfter);
    if (params.includeInternal) search.set("include_internal", "true");
    if (params.internalScope) search.set("internal_scope", params.internalScope);
    return requestPaginated<Meeting>(`/api/v1/meetings/?${search.toString()}`);
  },
  get: (id: string) => request<Meeting>(`/api/v1/meetings/${id}`),
  prepMonitor: (windowHours = 24) =>
    request<MeetingPrepMonitor>(`/api/v1/meetings/prep-monitor?window_hours=${windowHours}`),
  getRecordingUrl: (id: string) =>
    request<{ url: string }>(`/api/v1/meetings/${id}/recording-url`),
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

export type LifecycleStepState =
  | "upcoming"
  | "overdue"
  | "sent"
  | "opened"
  | "clicked"
  | "replied"
  | "done"
  | "skipped"
  | "failed";

export type LifecycleStatus =
  | "never_launched"
  | "ready"
  | "in_progress"
  | "replied"
  | "booked"
  | "stopped"
  | "stalled"
  | "completed";

export interface LifecycleEvent {
  activity_id?: string | null;
  created_at?: string | null;
  source?: string | null;
  external_source?: string | null;
  external_source_id?: string | null;
  medium?: string | null;
  content?: string | null;
  ai_summary?: string | null;
  email_subject?: string | null;
  email_from?: string | null;
  email_to?: string | null;
  email_cc?: string | null;
  call_duration_seconds?: number | null;
  recording_url?: string | null;
  aircall_user_name?: string | null;
  call_id?: string | null;
  created_by_id?: string | null;
  created_by_name?: string | null;
  event_type?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface LifecycleStep {
  index: number;
  channel: "email" | "call" | "linkedin";
  day_offset: number;
  objective?: string | null;
  subject?: string | null;
  state: LifecycleStepState;
  due_at: string;
  fired_at?: string | null;
  opened_at?: string | null;
  clicked_at?: string | null;
  replied_at?: string | null;
  bounced_at?: string | null;
  // Per-step engagement counts. Counted from Activity rows within this
  // step's window so the drawer's "Opened N times" label always matches
  // the timeline below — never the contact-wide aggregate.
  open_count?: number;
  click_count?: number;
  call_outcome?: string | null;
  note?: string | null;
  skip_reason?: string | null;
  // Rich per-event payloads (populated when the event has fired).
  send_event?: LifecycleEvent | null;
  open_event?: LifecycleEvent | null;
  click_event?: LifecycleEvent | null;
  open_events?: LifecycleEvent[];
  click_events?: LifecycleEvent[];
  reply_event?: LifecycleEvent | null;
  bounce_event?: LifecycleEvent | null;
  call_event?: LifecycleEvent | null;
  linkedin_event?: LifecycleEvent | null;
}

export interface LifecycleIssue {
  severity: "info" | "warning" | "error";
  code: string;
  step_index?: number;
  message: string;
}

export interface SequenceLifecycle {
  contact_id: string;
  status: LifecycleStatus;
  sequence?: {
    id: string;
    status?: string | null;
    instantly_campaign_id?: string | null;
    instantly_campaign_status?: string | null;
  } | null;
  launched_at?: string | null;
  days_since_launch?: number | null;
  current_step_index?: number | null;
  total_steps: number;
  steps: LifecycleStep[];
  issues: LifecycleIssue[];
}

export interface LifecycleSummary {
  status: LifecycleStatus;
  done_count: number;
  total_steps: number;
  overdue_count: number;
  current_channel?: "email" | "call" | "linkedin" | null;
  current_step_index?: number | null;
  days_since_launch?: number | null;
  has_issues: boolean;
}

export interface PreCallBrief {
  contact: {
    id: string;
    name: string;
    title?: string | null;
    email?: string | null;
    phone?: string | null;
    linkedin_url?: string | null;
    persona?: string | null;
    persona_type?: string | null;
    timezone?: string | null;
    sequence_status?: string | null;
    call_status?: string | null;
    call_disposition?: string | null;
    linkedin_status?: string | null;
  };
  company: {
    id?: string | null;
    name?: string | null;
    domain?: string | null;
    industry?: string | null;
    employees?: number | null;
  } | null;
  conversation_starter?: string | null;
  personalization_notes?: string | null;
  talking_points: string[];
  objection_playbook: Array<{ objection: string; response: string }>;
  last_email_sent: {
    subject: string;
    sent_at: string;
    snippet?: string | null;
    opened: boolean;
    clicked: boolean;
  } | null;
  recent_activities: Array<{
    type: string;
    medium?: string | null;
    source?: string | null;
    content?: string | null;
    ai_summary?: string | null;
    created_at: string;
  }>;
  recent_signals: Array<{
    type: string;
    title: string;
    summary?: string | null;
    url?: string | null;
    published_at?: string | null;
  }>;
  sequence: {
    id: string;
    status: string;
    subject_1?: string | null;
    email_1_snippet?: string | null;
    linkedin_message?: string | null;
    instantly_campaign_status?: string | null;
  } | null;
}

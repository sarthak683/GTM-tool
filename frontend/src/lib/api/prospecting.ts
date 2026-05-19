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
    ownerId?: string | string[];
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
    if (params?.ownerId) {
      const ownerValue = Array.isArray(params.ownerId) ? params.ownerId.join(",") : params.ownerId;
      if (ownerValue) search.set("owner_id", ownerValue);
    }
    return requestPaginated<Company>(`/api/v1/account-sourcing/companies?${search}`);
  },

  summary: (params?: { assignedRepEmail?: string; ownerId?: string | string[] }) => {
    const search = new URLSearchParams();
    if (params?.assignedRepEmail) search.set("assigned_rep_email", params.assignedRepEmail);
    if (params?.ownerId) {
      const ownerValue = Array.isArray(params.ownerId) ? params.ownerId.join(",") : params.ownerId;
      if (ownerValue) search.set("owner_id", ownerValue);
    }
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

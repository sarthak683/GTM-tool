import { BASE, getAuthHeaders, request } from "./core";

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

export const remindersApi = {
  list: (params?: { contact_id?: string; company_id?: string; status?: string; assigned_to_id?: string }) => {
    const search = new URLSearchParams();
    if (params?.contact_id) search.set("contact_id", params.contact_id);
    if (params?.company_id) search.set("company_id", params.company_id);
    if (params?.status) search.set("status", params.status);
    if (params?.assigned_to_id) search.set("assigned_to_id", params.assigned_to_id);
    return request<import("../../types").Reminder[]>(`/api/v1/reminders/?${search}`);
  },
  create: (data: { contact_id: string; company_id?: string; note: string; due_at: string; assigned_to_id?: string }) =>
    request<import("../../types").Reminder>("/api/v1/reminders/", { method: "POST", body: JSON.stringify(data) }),
  update: (id: string, data: Partial<import("../../types").Reminder>) =>
    request<import("../../types").Reminder>(`/api/v1/reminders/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  delete: (id: string) =>
    request<void>(`/api/v1/reminders/${id}`, { method: "DELETE" }),
};

export interface PersonalEmailStatus {
  connected: boolean;
  email_address?: string;
  last_sync_epoch?: number;
  backfill_completed: boolean;
  last_error?: string;
  has_calendar_scope?: boolean;
  has_drive_scope?: boolean;
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
  url: string;
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

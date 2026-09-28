import { request } from "./core";

export type SequenceStepType = "email" | "call" | "linkedin";
export type SequenceEmailSendVia = "personal" | "instantly";
export type SequenceLinkedinCategory = "dm" | "follow_up" | "inmail";

export type SequenceStep = {
  id: string;
  sequence_id: string;
  order: number;
  type: SequenceStepType;
  delay_minutes: number;
  send_via?: SequenceEmailSendVia | null;
  instantly_campaign_id?: string | null;
  linkedin_category?: SequenceLinkedinCategory | null;
};

export type SequenceStepDraft = {
  type: SequenceStepType;
  delay_minutes: number;
  send_via?: SequenceEmailSendVia | null;
  instantly_campaign_id?: string | null;
  linkedin_category?: SequenceLinkedinCategory | null;
};

export type Sequence = {
  id: string;
  name: string;
  shared: boolean;
  owner_id: string;
  owner_name?: string | null;
  step_count: number;
  active_enrollment_count: number;
  created_at: string;
  updated_at: string;
};

export type Enrollment = {
  id: string;
  contact_id: string;
  sequence_id: string;
  current_step: number;
  status: "active" | "completed" | "removed";
  enrolled_by: string;
  enrolled_at: string;
  completed_at?: string | null;
  sequence_name?: string | null;
  total_steps: number;
};

export const sequencesApi = {
  list: (scope?: "mine" | "team") => {
    const qs = scope ? `?scope=${scope}` : "";
    return request<Sequence[]>(`/api/v1/sequences${qs}`);
  },
  get: (id: string) => request<Sequence>(`/api/v1/sequences/${id}`),
  getSteps: (id: string) => request<SequenceStep[]>(`/api/v1/sequences/${id}/steps`),
  create: (data: { name: string; shared: boolean; steps: SequenceStepDraft[] }) =>
    request<Sequence>("/api/v1/sequences", { method: "POST", body: JSON.stringify(data) }),
  update: (id: string, data: { name: string; shared: boolean; steps: SequenceStepDraft[] }) =>
    request<Sequence>(`/api/v1/sequences/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  remove: (id: string) => request<void>(`/api/v1/sequences/${id}`, { method: "DELETE" }),
  enroll: (contactId: string, sequenceId: string) =>
    request<Enrollment>("/api/v1/sequences/enroll", {
      method: "POST",
      body: JSON.stringify({ contact_id: contactId, sequence_id: sequenceId }),
    }),
  getContactEnrollment: (contactId: string) =>
    request<Enrollment | null>(`/api/v1/sequences/contacts/${contactId}/enrollment`),
  unenroll: (enrollmentId: string) =>
    request<void>(`/api/v1/sequences/enrollments/${enrollmentId}`, { method: "DELETE" }),
  // Instantly email step's one-click action — adds the contact as a lead to
  // the step's campaign now; the task itself completes later, whenever the
  // existing Instantly webhook confirms the send (not on this click).
  addTaskToInstantly: (taskId: string) =>
    request<{ added: boolean }>(`/api/v1/sequences/tasks/${taskId}/instantly-add`, { method: "POST" }),
};

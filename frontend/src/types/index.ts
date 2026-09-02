import type { components } from "./generated";

type GeneratedCompany = components["schemas"]["CompanyRead"];

export type Company = Omit<
  GeneratedCompany,
  | "additional_domains"
  | "tech_stack"
  | "enrichment_sources"
  | "intent_signals"
  | "enrichment_cache"
  | "priority_tag"
  | "prospecting_profile"
  | "outreach_plan"
  | "recotap"
> & {
  // Alias domains (rebrands/merged accounts) — all matching honors these.
  additional_domains?: string[] | null;
  tech_stack?: Record<string, unknown> | null;
  enrichment_sources?: Record<string, unknown> | null;
  intent_signals?: Record<string, unknown> | null;
  enrichment_cache?: Record<string, unknown> | null;
  priority_tag?: "P0" | "P1" | "P2" | null;
  prospecting_profile?: Record<string, unknown> | null;
  outreach_plan?: Record<string, unknown> | null;
  // Recotap ABM signals, joined by domain (Account Sourcing only).
  recotap?: RecotapSignals | null;
};

export interface RecotapSignals {
  domain: string;
  name?: string | null;
  rtp_aid?: string | null;
  /** Effective stage: the CRM-derived one when a live deal gives us one, else
   *  Recotap's. `journey_stage_source` says which, so a badge reading "Powered
   *  by Recotap" can stop taking credit for Beacon's own deal stages. */
  journey_stage?: string | null;
  journey_stage_source?: "crm" | "recotap" | null;
  recotap_journey_stage?: string | null;
  crm_journey_stage?: string | null;
  /** rtp_account_score. Recotap documents 0-100 but sends values above it; 0
   *  means "not scored yet", which is why `engagement` is null rather than
   *  "Cold" for those accounts. */
  score?: number | null;
  engagement?: string | null;
  icp_fit?: string | null;
  advertising_activity_score?: number | null;
  website_intent_score?: number | null;
  g2_intent_score?: number | null;
  bombora_intent_score?: number | null;
  hq_location?: string | null;
  last_account_date?: string | null;
  source?: string | null;
}

type GeneratedContact = components["schemas"]["ContactRead"];

export type Contact = Omit<
  GeneratedContact,
  "additional_phones" | "enrichment_data" | "warm_intro_path" | "talking_points"
> & {
  additional_phones?: { number: string; label?: string }[] | null;
  enrichment_data?: Record<string, unknown> | null;
  warm_intro_path?: Record<string, unknown> | null;
  talking_points?: string[] | null;
};

export interface SourcingBatch {
  id: string;
  filename: string;
  status: string; // pending | awaiting_confirmation | processing | completed | failed | cancelled
  total_rows: number;
  processed_rows: number;
  created_companies: number;
  skipped_rows: number;
  failed_rows: number;
  created_by_id?: string;
  created_by_name?: string;
  created_by_email?: string;
  meta?: Record<string, unknown>;
  error_log?: Array<{ name?: string; error?: string }>;
  current_stage?: string;
  progress_message?: string;
  eta_seconds?: number | null;
  contacts_found?: number | null;
  verdict_summary?: Record<string, unknown>;
  requires_confirmation?: boolean;
  auto_started?: boolean;
  created_at: string;
  updated_at: string;
}

export interface AccountSourcingSummary {
  total_companies: number;
  hot_count: number;
  warm_count: number;
  high_priority_count: number;
  engaged_count: number;
  unresolved_count: number;
  unenriched_count: number;
  researched_count: number;
  target_verdict_count: number;
  watch_verdict_count: number;
  enriched_count: number;
  total_contacts: number;
}

/** Standard paginated list wrapper returned by all GET list endpoints. */
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

type GeneratedDeal = components["schemas"]["DealRead"];
type DealEngagementSignal = {
  type: string;
  source: string;
  label: string;
  reason?: string;
  source_label?: string;
  detail?: string;
};

export type Deal = Omit<
  GeneratedDeal,
  | "qualification"
  | "priority_tag"
  | "seller_engagement_signal"
  | "client_engagement_signal"
  | "flags"
  | "forecast_category"
> & {
  // The generated schema in the local WIP predates this newly merged field.
  close_date?: string | null;
  qualification?: DealQualification | null;
  priority_tag?: "P0" | "P1" | "P2" | null;
  seller_engagement_signal?: DealEngagementSignal | null;
  client_engagement_signal?: DealEngagementSignal | null;
  flags?: Record<string, "green" | "yellow" | "red"> | null;
  forecast_category?: "commit" | "best_case" | "pipeline" | null;
};

export interface MeddpiccAiDimension {
  level: number;
  confidence?: "low" | "medium" | "high";
  reason?: string;
}

export interface MeddpiccFieldContact {
  name?: string;
  email?: string;
  title?: string;
  persona_type?: string;
}

export interface MeddpiccFieldDetail {
  summary?: string;
  evidence?: string;
  notes?: string;
  change_reason?: "empty_field" | "material_refinement" | "contradiction";
  updated_at?: string;
  target_score?: number;
  evidence_activity_id?: string;
  contact?: MeddpiccFieldContact | null;
  tags?: string[];
  entities?: string[];
}

export interface DealQualification {
  meddpicc?: Record<string, number>;
  meddpicc_details?: Record<string, MeddpiccFieldDetail>;
  meddpicc_ai?: {
    generated_at?: string;
    generator?: string;
    dimensions?: Record<string, MeddpiccAiDimension>;
    signals_used?: {
      contacts?: number;
      activities?: number;
    };
  };
  [key: string]: unknown;
}

export interface DealContact {
  deal_id: string;
  contact_id: string;
  role?: string;
  added_at: string;
  first_name?: string;
  last_name?: string;
  email?: string;
  title?: string;
  persona?: string;
}

export interface OutreachSequence {
  id: string;
  contact_id: string;
  company_id: string;
  persona?: string;
  status: "draft" | "approved" | "launched" | "replied" | "completed" | "paused" | "sent" | "skipped" | "meeting_booked";
  email_1?: string;
  email_2?: string;
  email_3?: string;
  linkedin_message?: string;
  subject_1?: string;
  subject_2?: string;
  subject_3?: string;
  instantly_campaign_id?: string;
  instantly_campaign_status?: string;
  generation_context?: Record<string, unknown>;
  generated_at?: string;
  launched_at?: string;
  created_at: string;
  updated_at: string;
}

export interface OutreachStep {
  id: string;
  sequence_id: string;
  step_number: number;
  channel?: "email" | "call" | "linkedin";
  subject?: string;
  body: string;
  delay_value: number;
  delay_unit: string;
  variants?: Record<string, unknown> | Array<Record<string, unknown>> | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Activity {
  id: string;
  deal_id?: string;
  contact_id?: string;
  type: string;
  source?: string;
  medium?: string; // email, call, linkedin, whatsapp, in_person, sms, other
  content?: string;
  ai_summary?: string;
  event_metadata?: Record<string, unknown>;
  created_at: string;
  created_by_id?: string;
  user_name?: string;
  call_id?: string;
  call_duration?: number;
  call_outcome?: string;
  recording_url?: string;
  aircall_user_name?: string;
  email_message_id?: string;
  email_subject?: string;
  email_from?: string;
  email_to?: string;
  email_cc?: string;
}

// System-generated bell notification. Distinct from Tasks: signals that
// decay on read, not durable work. See app/services/notifications.py.
export type NotificationType =
  | "meeting_booked_suggest_deal"
  | "records_added"
  | "next_step_due"
  | "prospect_followup_due";

export interface AppNotification {
  id: string;
  user_id: string;
  type: NotificationType | string; // string fallback so unknown future types still render
  title: string;
  body?: string;
  action_payload?: Record<string, unknown>;
  dedup_key?: string;
  read_at?: string;
  dismissed_at?: string;
  accepted_at?: string;
  created_at: string;
  updated_at: string;
}

// In-browser call recording (manual call on phone speakerphone, laptop
// mic captures both sides). Audio is not persisted server-side — see
// app/tasks/transcribe_call.py. Only the transcript and AI-classified
// disposition are stored long-term.
export type CallRecordingStatus =
  | "uploaded"
  | "transcribing"
  | "classifying"
  | "ready"
  | "failed";

export interface CallRecording {
  id: string;
  contact_id?: string;
  deal_id?: string;
  created_by_id?: string;
  status: CallRecordingStatus;
  consent_acknowledged_at?: string;
  audio_duration_seconds?: number;
  audio_size_bytes?: number;
  transcript?: string;
  ai_disposition?: string;
  ai_confidence?: number;
  ai_summary?: string;
  failure_reason?: string;
  created_at: string;
  updated_at: string;
}

export interface TaskComment {
  id: string;
  task_id: string;
  body: string;
  created_by_id?: string;
  created_by_name?: string;
  created_at: string;
}

export interface TaskItem {
  id: string;
  entity_type: "company" | "contact" | "deal";
  entity_id: string;
  task_type: "manual" | "system";
  title: string;
  description?: string;
  status: "open" | "completed" | "dismissed";
  priority: "low" | "medium" | "high";
  source?: string;
  recommended_action?: string;
  due_at?: string;
  action_payload?: Record<string, unknown>;
  system_key?: string;
  task_track?: "sales_ai" | "critical" | "hygiene" | "manual" | null;
  created_by_id?: string;
  created_by_name?: string;
  assigned_role?: "admin" | "ae" | "sdr";
  assigned_to_id?: string;
  assigned_to_name?: string;
  accepted_at?: string;
  completed_at?: string;
  created_at: string;
  updated_at: string;
  comments: TaskComment[];
}

export interface TaskWorkspaceItem extends TaskItem {
  entity_name: string;
  entity_subtitle?: string;
  entity_link: string;
}

export interface CrmImportResponse {
  replace: {
    deals_deleted: number;
    deal_contacts_deleted: number;
    deal_tasks_deleted: number;
    activities_deleted: number;
    companies_deleted: number;
  };
  import: {
    top_level_tasks_seen: number;
    subtasks_seen: number;
    companies_created: number;
    companies_reused: number;
    deals_created: number;
    deals_updated: number;
    tasks_created: number;
    tasks_updated: number;
    activities_created: number;
    activities_reused: number;
    unmatched_assignees: string[];
    fields_loaded: number;
  };
}

export interface ProspectImportMissingCompany {
  name: string;
  domain?: string;
  contacts_count: number;
}

export interface ProspectImportCreatedCompany {
  id: string;
  name: string;
  domain?: string;
  contacts_count: number;
}

export interface ProspectImportResponse {
  imported_rows: number;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  warning_count?: number;
  missing_company_count: number;
  missing_companies: ProspectImportMissingCompany[];
  created_company_count?: number;
  created_companies?: ProspectImportCreatedCompany[];
  message: string;
}

export interface Reminder {
  id: string;
  contact_id: string;
  company_id?: string;
  created_by_id?: string;
  assigned_to_id?: string;
  note: string;
  due_at: string;
  status: "pending" | "completed" | "dismissed";
  created_at: string;
  completed_at?: string;
  contact_name?: string;
  company_name?: string;
  assigned_to_name?: string;
}

export interface AssignmentUpdate {
  id: string;
  entity_type: "company" | "contact" | "deal";
  entity_id: string;
  assignment_role: "owner" | "ae" | "sdr";
  assignee_id?: string;
  created_by_id?: string;
  entity_name_snapshot?: string;
  company_name_snapshot?: string;
  assignee_name_snapshot?: string;
  assignee_email_snapshot?: string;
  progress_state: "new" | "working" | "waiting_on_buyer" | "meeting_booked" | "qualified" | "deal_created" | "blocked" | "closed";
  confidence: "low" | "medium" | "high";
  buyer_signal: "none" | "replied" | "interested" | "champion_identified" | "meeting_requested" | "commercial_discussion" | "verbal_yes";
  blocker_type: "none" | "no_response" | "wrong_person" | "timing" | "budget" | "competition" | "internal_dependency" | "legal_security" | "other";
  last_touch_type: "none" | "email" | "call" | "linkedin" | "meeting" | "research" | "internal";
  summary: string;
  next_step: string;
  next_step_due_date?: string;
  blocker_detail?: string;
  created_by_name?: string;
  created_at: string;
}

export interface ExecutionTrackerItem {
  entity_type: "company" | "contact" | "deal";
  entity_id: string;
  entity_name: string;
  entity_subtitle?: string;
  entity_link: string;
  company_name?: string;
  assignee_id: string;
  assignee_name?: string;
  assignment_role: "owner" | "ae" | "sdr";
  system_status?: string;
  entity_updated_at: string;
  needs_update: boolean;
  next_step_overdue: boolean;
  latest_update?: AssignmentUpdate | null;
}

export interface ExecutionTrackerSummary {
  total_items: number;
  no_update_items: number;
  needs_update_items: number;
  blocked_items: number;
  overdue_next_steps: number;
  positive_momentum_items: number;
}

export interface Signal {
  id: string;
  company_id: string;
  signal_type: string;
  source: string;
  title: string;
  url?: string;
  summary?: string;
  published_at?: string;
  relevance_score?: number;
  created_at: string;
}

export interface Meeting {
  id: string;
  title: string;
  company_id?: string;
  deal_id?: string;
  owner_user_id?: string;
  external_source?: string;
  external_source_id?: string;
  synced_by_user_id?: string;
  synced_at?: string;
  scheduled_at?: string;
  status: string;
  meeting_type: string;
  meeting_url?: string;
  recording_url?: string;
  pre_brief?: string;
  demo_strategy?: string;
  research_data?: unknown;
  attendees?: unknown;
  raw_notes?: string;
  ai_summary?: string;
  mom_draft?: string;
  meeting_score?: number;
  intel_email_sent_at?: string;
  what_went_right?: string;
  what_went_wrong?: string;
  next_steps?: string;
  manually_linked?: boolean;
  is_internal?: boolean;
  /** Sales Lifecycle SOP stage 04 — the AE classifies each client call from the
   *  invite's attendee list, and the level sets how the call is run. */
  call_level?: CallLevel | null;
  /** "manual" means an AE decided at the prep call; sync never overwrites it. */
  call_level_source?: "auto" | "manual" | null;
  call_level_set_by_id?: string | null;
  call_level_set_at?: string | null;
  /** Computed per request from the CURRENT attendee list — detail responses
   *  only, so it is absent on list rows. */
  call_level_suggestion?: CallLevelSuggestion | null;
  created_at: string;
  updated_at: string;
}

export type CallLevel = "L1" | "L2" | "L3";

export interface CallLevelSuggestion {
  level: CallLevel | null;
  /** "low" means the attendee titles could not all be read, so an SVP+ may be
   *  present and the level could actually be higher. Only 3.4% of attendees
   *  carry a title, so this is the common case — show it, don't hide it. */
  confidence: "high" | "low";
  rationale: string;
  external_count: number;
  titles_known: number;
  senior_attendees: string[];
}

export interface SalesResource {
  id: string;
  title: string;
  category: string;
  description?: string;
  content: string;
  filename?: string;
  file_size?: number;
  tags: string[];
  modules: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface GlobalSearchItem {
  id: string;
  kind: string;
  title: string;
  subtitle?: string;
  meta?: string;
  link: string;
}

export interface GlobalSearchSection {
  key: string;
  label: string;
  items: GlobalSearchItem[];
}

export interface GlobalSearchResponse {
  query: string;
  sections: GlobalSearchSection[];
}

export type User = Omit<components["schemas"]["UserRead"], "role"> & {
  role: "superadmin" | "admin" | "ae" | "sdr" | "marketing";
};

export type Task = components["schemas"]["TaskRead"];

export interface GmailSyncSettings {
  configured: boolean;
  inbox?: string;
  connected_email?: string;
  connected_at?: string;
  interval_seconds: number;
  last_sync_epoch?: number | null;
  last_error?: string | null;
}

export interface ReportSenderSettings {
  configured: boolean;
  sender_email?: string;
  connected_email?: string;
  connected_at?: string;
  last_error?: string | null;
  has_send_scope: boolean;
}

export interface SalesReportSettings {
  enabled: boolean;
  recipients: string[];
  send_timezone: string;
  send_hour: number;
  send_minute: number;
  cutoff_timezone: string;
  cutoff_hour: number;
  report_label_timezone: string;
  send_days: string[];
  weekly_report_day: string;
  skip_weekends: boolean;
  nonprod_scheduled_enabled: boolean;
  nonprod_recipients: string[];
  last_scheduled_send_key?: string | null;
  last_scheduled_send_at?: string | null;
}

export interface WeeklyDigestSettings {
  enabled: boolean;
  recipients: string[];
  send_timezone: string;
  send_hour: number;
  send_minute: number;
  send_days: string[];
  nonprod_scheduled_enabled: boolean;
  nonprod_recipients: string[];
  last_scheduled_send_key?: string | null;
  last_scheduled_send_at?: string | null;
}

export interface SalesReportRunResult {
  report_type: "daily" | "weekly" | "month_to_date" | "prior_quarter" | "custom";
  report_date: string;
  period_start: string;
  period_end: string;
  subject: string;
  recipients: string[];
  rows: Array<{
    rep_name: string;
    calls: number;
    connected_calls: number;
    meetings_booked_calls: number;
  }>;
  send_results?: Array<Record<string, unknown>>;
}

export interface SalesAnalyticsRosterSettings {
  user_ids: string[];
  default_emails: string[];
}

export interface DealStageSetting {
  id: string;
  label: string;
  group: "active" | "closed";
  color: string;
}

export interface DealStageSettings {
  stages: DealStageSetting[];
}

export interface ProspectStageSettings {
  stages: DealStageSetting[];
}

export interface OutreachTemplateStep {
  step_number: number;
  channel: "email" | "call" | "linkedin";
  label: string;
  goal: string;
  subject_hint?: string | null;
  body_template?: string | null;
  prompt_hint?: string | null;
}

export interface OutreachContentSettings {
  general_prompt: string;
  linkedin_prompt: string;
  step_templates: OutreachTemplateStep[];
}

export interface StageBucketSettings {
  active: string[];
  inactive: string[];
  tofu: string[];
  mofu: string[];
  bofu: string[];
  visible_cards: string[];
}

export interface PipelineSummarySettings {
  deal: StageBucketSettings;
  prospect: StageBucketSettings;
}

export interface RolePermissionFlags {
  crm_import: boolean;
  prospect_migration: boolean;
  manage_team: boolean;
  run_pre_meeting_intel: boolean;
  manage_reports: boolean;
}

export interface RolePermissionsSettings {
  ae: RolePermissionFlags;
  sdr: RolePermissionFlags;
  marketing: RolePermissionFlags;
}

export interface PreMeetingAutomationSettings {
  enabled: boolean;
  send_mode: "hours_before" | "daily_time";
  send_time: string; // "HH:MM" 24h, in `timezone`
  timezone: string; // IANA tz, e.g. "America/New_York"
  send_hours_before: number;
  generate_hours_before: number;
  auto_generate_if_missing: boolean;
}

export interface MeetingPrepMonitor {
  window_hours: number;
  upcoming_count: number;
  no_company_count: number;
  no_deal_count: number;
  no_intel_count: number;
  no_recipient_count: number;
  unlinked: Meeting[];
}

export interface SyncScheduleSettings {
  tldv_sync_enabled: boolean;
  tldv_sync_interval_minutes: number;
  tldv_page_size: number;
  tldv_max_pages: number;
  tldv_last_synced_at?: string | null;
  email_sync_interval_seconds: number;
  deal_health_hour: number;
  /** When on, emails track only via zippy+<deal-alias> CC; bulk inbox sync pauses. */
  zippy_only_email_sync: boolean;
}

export interface ClickUpCrmSettings {
  team_id?: string | null;
  space_id?: string | null;
  deals_list_id?: string | null;
}

export interface AngelInvestor {
  id: string;
  name: string;
  current_role?: string;
  current_company?: string;
  linkedin_url?: string;
  career_history?: string;
  networks?: string;
  pe_vc_connections?: string;
  sectors?: string;
  notes?: string;
  created_at: string;
  updated_at: string;
}

export interface AngelMapping {
  id: string;
  contact_id: string;
  company_id?: string;
  angel_investor_id: string;
  strength: number;
  rank: number;
  connection_path?: string;
  why_it_works?: string;
  recommended_strategy?: string;
  // Joined fields
  contact_name?: string;
  contact_title?: string;
  contact_linkedin?: string;
  company_name?: string;
  angel_name?: string;
  angel_current_role?: string;
  angel_current_company?: string;
  created_at: string;
  updated_at: string;
}

export interface Battlecard {
  id: string;
  category: string;
  title: string;
  trigger: string;
  response: string;
  competitor?: string;
  tags?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type DataRoomCategory = "documentation" | "decks" | "videos" | "demo_recordings" | "post_poc_collaterals";

export interface DataRoomItem {
  id: string;
  category: DataRoomCategory;
  title: string;
  embed_url: string;
  thumbnail_url?: string | null;
  created_by_id?: string | null;
  created_at: string;
  updated_at: string;
}

// Bulk account reassignment from an uploaded CSV/XLSX.
// `status` mirrors the backend's RowStatus: "ok" is the only one that writes.
export type AssignmentUploadRowStatus =
  | "ok"
  | "no_change"
  | "not_found"
  | "ambiguous"
  | "unknown_rep"
  | "no_identifier";

export interface AssignmentUploadRow {
  row_number: number;
  identifier: string;
  status: AssignmentUploadRowStatus;
  message: string;
  company_id: string | null;
  company_name: string | null;
  company_domain: string | null;
  current_ae: string | null;
  current_sdr: string | null;
  new_ae: string | null;
  new_sdr: string | null;
  ae_changes: boolean;
  sdr_changes: boolean;
}

export interface AssignmentUploadResult {
  dry_run: boolean;
  filename: string;
  summary: {
    total: number;
    will_change: number;
    no_change: number;
    not_found: number;
    ambiguous: number;
    unknown_rep: number;
    no_identifier: number;
  };
  rows: AssignmentUploadRow[];
  applied: {
    ae_changed: number;
    sdr_changed: number;
    contacts_touched: number;
  } | null;
}

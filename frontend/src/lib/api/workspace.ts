import type {
  AngelInvestor,
  AngelMapping,
  AssignmentUpdate,
  ClickUpCrmSettings,
  Company,
  Contact,
  DealStageSettings,
  ExecutionTrackerItem,
  ExecutionTrackerSummary,
  GmailSyncSettings,
  GlobalSearchResponse,
  OutreachContentSettings,
  PipelineSummarySettings,
  PreMeetingAutomationSettings,
  ProspectStageSettings,
  ReportSenderSettings,
  RolePermissionsSettings,
  SalesReportSettings,
  SalesResource,
  SyncScheduleSettings,
  TaskComment,
  TaskItem,
  TaskWorkspaceItem,
  User,
} from "../../types";
import { BASE, getAuthHeaders, normalizeUtcDateStrings, request, requestList, requestPaginated } from "./core";

export const tasksApi = {
  listDetailed: async (
    entityType: "company" | "contact" | "deal",
    entityId: string,
    includeClosed = true,
    refreshMode: "auto" | "force" | "none" = "auto",
  ) => {
    const res = await fetch(
      `${BASE}/api/v1/tasks/?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}&include_closed=${includeClosed ? "true" : "false"}&refresh_mode=${refreshMode}`,
      {
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      },
    );
    if (res.status === 401) {
      localStorage.removeItem("beacon_token");
      window.location.href = "/login";
      throw new Error("Session expired");
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? "Request failed");
    }
    const payload = normalizeUtcDateStrings(await res.json()) as TaskItem[];
    return {
      items: payload,
      refreshMode: res.headers.get("X-Beacon-Refresh-Mode") ?? "skipped",
    };
  },
  list: async (
    entityType: "company" | "contact" | "deal",
    entityId: string,
    includeClosed = true,
    refreshMode: "auto" | "force" | "none" = "auto",
  ) => {
    const result = await tasksApi.listDetailed(entityType, entityId, includeClosed, refreshMode);
    return result.items;
  },
  workspace: (params?: {
    includeClosed?: boolean;
    taskType?: "manual" | "system";
    entityType?: "company" | "contact" | "deal";
    dealId?: string;
    scope?: "mine" | "team";
  }) => {
    const search = new URLSearchParams();
    search.set("include_closed", params?.includeClosed ? "true" : "false");
    if (params?.taskType) search.set("task_type", params.taskType);
    if (params?.entityType) search.set("entity_type", params.entityType);
    if (params?.dealId) search.set("deal_id", params.dealId);
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

export type ScorecardMetric = {
  key: string;
  label: string;
  value: number;
  target: number | null;
  attainment: number | null;
  rag: "green" | "amber" | "red" | null;
};

export type ScorecardBlock = {
  title: string;
  metrics: ScorecardMetric[];
};

export type ScorecardResponse = {
  header: {
    rep_id: string | null;
    rep_name: string | null;
    role: string | null;
    period_label: string;
    period_start: string;
    period_end: string;
    overall_attainment: number;
    overall_rag: "green" | "amber" | "red";
  };
  activity: ScorecardBlock;
  outcomes: ScorecardBlock;
  efficiency: ScorecardBlock;
  pipeline_delta: { created_count: number; created_value: number; exited_count: number };
  at_risk_deals: Array<{
    deal_id: string;
    deal_name: string;
    stage: string;
    dwell_days: number;
    threshold_days: number;
    over_by_days: number;
  }>;
};

export type RepSummary = { id: string; name: string; email: string; role: string };
export type PodSummary = {
  key: string;
  label: string;
  rep_ids: string[];      // all members — drives the rep filter
  ae_rep_ids: string[];   // deal-owning AEs — the pipeline/forecast subset
  reps: { id: string; name: string; email: string; role: string; is_ae: boolean }[];
};

export const performanceApi = {
  getScorecard: (params: { rep_id?: string; period?: "week" | "month"; anchor?: string }) => {
    const qs = new URLSearchParams();
    if (params.rep_id) qs.set("rep_id", params.rep_id);
    if (params.period) qs.set("period", params.period);
    if (params.anchor) qs.set("anchor", params.anchor);
    const tail = qs.toString();
    return request<ScorecardResponse>(`/api/v1/performance/scorecard${tail ? `?${tail}` : ""}`);
  },
  listReps: () => request<RepSummary[]>("/api/v1/performance/reps"),
  getPods: () => request<PodSummary[]>("/api/v1/performance/pods"),
  getFunnel: (params: { period?: "week" | "month" | "quarter"; anchor?: string; rep_id?: string }) => {
    const qs = new URLSearchParams();
    if (params.period) qs.set("period", params.period);
    if (params.anchor) qs.set("anchor", params.anchor);
    if (params.rep_id) qs.set("rep_id", params.rep_id);
    const tail = qs.toString();
    return request<FunnelResponse>(`/api/v1/performance/funnel${tail ? `?${tail}` : ""}`);
  },
  getDealHealth: (params: { rep_id?: string }) => {
    const qs = new URLSearchParams();
    if (params.rep_id) qs.set("rep_id", params.rep_id);
    const tail = qs.toString();
    return request<DealHealthResponse>(`/api/v1/performance/deal-health${tail ? `?${tail}` : ""}`);
  },
  getPipelineBuckets: () => request<PipelineBucketsResponse>("/api/v1/performance/pipeline-buckets"),
  getForecast: (params: {
    period?: "month" | "quarter";
    anchor?: string;
    rep_id?: string;
    quota?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.period) qs.set("period", params.period);
    if (params.anchor) qs.set("anchor", params.anchor);
    if (params.rep_id) qs.set("rep_id", params.rep_id);
    if (params.quota != null) qs.set("quota", String(params.quota));
    const tail = qs.toString();
    return request<ForecastResponse>(`/api/v1/performance/forecast${tail ? `?${tail}` : ""}`);
  },
  getSettings: () => request<AnalyticsSettings>("/api/v1/performance/settings"),
  updateSettings: (patch: Partial<AnalyticsSettings>) =>
    request<AnalyticsSettings>("/api/v1/performance/settings", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  getLeaderboard: (params: {
    metric: "calls_connected" | "demos_done" | "pocs_procured" | "closed_won" | "win_rate" | "avg_cycle_time_days";
    period?: "week" | "month" | "quarter";
  }) => {
    const qs = new URLSearchParams();
    qs.set("metric", params.metric);
    if (params.period) qs.set("period", params.period);
    return request<LeaderboardResponse>(`/api/v1/performance/leaderboards?${qs.toString()}`);
  },
};

export type RedAlertDeal = {
  deal_id: string;
  deal_name: string;
  amount?: number | null;
  stage_entered_at?: string | null;
  ae_name?: string | null;
  sdr_name?: string | null;
};

export type DealHealthResponse = {
  total_stuck: number;
  by_stage: Record<string, number>;
  deals: Array<{
    deal_id: string;
    deal_name: string;
    stage: string;
    dwell_days: number;
    threshold_days: number;
    over_by_days: number;
  }>;
  red_alert_buckets: Record<string, number>;
  red_alert_deals: Record<string, RedAlertDeal[]>;
};

export type PipelineBucketDeal = {
  deal_id: string;
  deal_name: string;
  amount?: number | null;
  stage: string;
  ae_name?: string | null;
};

export type PipelineBucketsResponse = {
  low_value_late_stage_count: number;
  low_value_late_stage_deals: PipelineBucketDeal[];
  small_avg_count: number;
  small_avg_deals: PipelineBucketDeal[];
};

export type ForecastResponse = {
  period_label: string;
  quota: number | null;
  commit_number: number;
  best_case_number: number;
  weighted_pipeline: number;
  gap_to_quota: number | null;
  buckets: Array<{
    category: string;
    deal_count: number;
    acv: number;
    weighted_acv: number;
  }>;
};

export type LeaderboardResponse = {
  metric: string;
  period_label: string;
  entries: Array<{ rep_id: string; rep_name: string; role: string; value: number }>;
};

export type AnalyticsSettings = {
  weekly_targets: Record<string, Record<string, number>>;
  monthly_targets: Record<string, Record<string, number>>;
  rag_bands: { green_min: number; amber_min: number };
  stuck_thresholds_days: Record<string, number>;
  stage_probabilities: Record<string, number>;
  conversion_transitions: Array<{ from: string; to: string }>;
  workspace_timezone: string;
  email_reply_lookback_days: number;
};

export type FunnelResponse = {
  period_label: string;
  period_start: string;
  period_end: string;
  funnel: Array<{ stage: string; deal_count: number; total_value: number }>;
  conversion: Array<{
    from_stage: string;
    to_stage: string;
    deals: number;
    conv_rate: number;
    median_days: number | null;
  }>;
  movement: { advanced: number; regressed: number; exited: number; entered: number };
};

export type MilestoneDealRow = {
  milestone_key: string;
  deal_name: string | null;
  company_name: string | null;
  reached_at: string;
  close_date_est: string | null;
  deal_value: number | null;
  assigned_ae: string | null;
  assigned_sdr: string | null;
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
  demo_scheduled_count?: number;
  qualified_lead_count?: number;
  demo_done_count: number;
  poc_agreed_count: number;
  poc_wip_count?: number;
  poc_done_count: number;
  commercial_negotiation_count?: number;
  workshop_msa_count?: number;
  closed_won_count: number;
  closed_won_value: number;
  milestone_deals: MilestoneDealRow[];
  // Previous equal-length window — for period-over-period trend deltas.
  prev_demo_scheduled_count?: number;
  prev_qualified_lead_count?: number;
  prev_demo_done_count?: number;
  prev_poc_agreed_count?: number;
  prev_poc_wip_count?: number;
  prev_poc_done_count?: number;
  prev_commercial_negotiation_count?: number;
  prev_workshop_msa_count?: number;
  prev_closed_won_count?: number;
  prev_closed_won_value?: number;
};

export type SalesRepActivityRow = {
  key: string;
  user_id?: string | null;
  rep_name: string;
  role?: string | null;
  calls: number;
  connected_calls: number;
  live_calls: number;
  emails: number;
  email_opens: number;
  email_replies: number;
  linkedin_reachouts: number;
  linkedin_accepted?: number;
  linkedin_meeting_booked?: number;
  call_meeting_booked?: number;
  meetings: number;
  total: number;
  meetings_next_1w?: number;
  meetings_next_2w?: number;
  meetings_beyond_2w?: number;
  direct_sql?: number;
  active_deals: number;
  pipeline_amount: number;
  demos_scheduled: number;
  demos_done: number;
  demos_converted: number;
  ae_demos_scheduled: number;
  ae_demos_done: number;
  ae_demos_converted: number;
  // Call touchpoint breakdown
  call_first_attempt?: number;
  call_second_plus?: number;
  // Email touchpoint breakdown
  email_first_attempt?: number;
  email_min_3_attempts?: number;
  // LinkedIn touchpoint breakdown
  linkedin_connection_requested?: number;
  linkedin_intro_msg?: number;
  linkedin_followup_msg?: number;
  // Prospect / contact coverage
  total_prospects?: number;
  total_mobile_numbers?: number;
};

export type SalesRepActivityWeekRow = {
  week_key: string;
  label: string;
  week_start: string;
  week_end: string;
  emails: number;
  calls: number;
  connected_calls: number;
  live_calls: number;
  linkedin_reachouts: number;
  meetings: number;
  total: number;
};

export type SalesRepWeeklyActivityRow = {
  key: string;
  user_id?: string | null;
  rep_name: string;
  active_deals: number;
  pipeline_amount: number;
  totals: SalesRepActivityRow;
  weeks: SalesRepActivityWeekRow[];
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

export type SalesHighlightDrilldown = {
  entity_type: "deal";
  stage_key?: string | null;
  rep_user_id?: string | null;
  stalled_only?: boolean;
  overdue_close_date?: boolean;
  missing_close_date?: boolean;
  close_month?: string | null;
};

export type SalesHighlight = {
  key: string;
  message: string;
  title?: string | null;
  subtitle?: string | null;
  drilldown?: SalesHighlightDrilldown | null;
};

export type SalesDashboard = {
  generated_at: string;
  window_days: number;
  from_date?: string | null;
  to_date?: string | null;
  summary: SalesDashboardSummary;
  highlights: Array<SalesHighlight | string>;
  rep_activity: SalesRepActivityRow[];
  rep_weekly_activity: SalesRepWeeklyActivityRow[];
  pipeline_by_stage: SalesStageBucket[];
  pipeline_by_owner: SalesPipelineOwnerRow[];
  velocity_by_stage: SalesVelocityRow[];
  forecast_by_month: SalesForecastRow[];
  forecast_by_week?: SalesForecastRow[];
  forecast_buckets?: SalesForecastRow[];
  forecast_granularity?: "week" | "month";
  conversion_funnel: SalesFunnelStep[];
  monthly_unique_funnel: MonthlyUniqueFunnelRow[];
  accounts_by_status?: SalesAccountStatusRow[];
  quota: SalesQuotaState;
};

export type SalesAccountStatusRow = {
  key: string;
  label: string;
  count: number;
};

export type SalesActivityDrilldownRow = {
  id: string;
  kind: "activity" | "meeting";
  activity_type: string;
  occurred_at: string;
  rep_user_id?: string | null;
  rep_name: string;
  source?: string | null;
  source_label?: string | null;
  subject?: string | null;
  direction?: string | null;
  from_email?: string | null;
  to_email?: string | null;
  call_outcome?: string | null;
  call_duration?: number | null;
  contact_name?: string | null;
  contact_email?: string | null;
  company_name?: string | null;
  deal_name?: string | null;
  deal_id?: string | null;
  company_id?: string | null;
  email_body?: string | null;  // full body for expand-in-drilldown (1.2)
};

export type SalesActivityDrilldown = {
  generated_at: string;
  metric: string;
  window_days: number;
  from_date?: string | null;
  to_date?: string | null;
  rep_user_id?: string | null;
  rep_name?: string | null;
  returned_count: number;
  has_more: boolean;
  limit: number;
  offset: number;
  rows: SalesActivityDrilldownRow[];
};

export const analyticsApi = {
  salesDashboard: (
    windowDays = 90,
    repIds: string[] = [],
    geographies: string[] = [],
    fromDate?: string,
    toDate?: string,
    forecastGranularity?: "week" | "month",
  ) => {
    const params = new URLSearchParams({ window_days: String(windowDays) });
    for (const id of repIds) params.append("rep_id", id);
    for (const g of geographies) params.append("geography", g);
    if (fromDate) params.set("from_date", fromDate);
    if (toDate) params.set("to_date", toDate);
    if (forecastGranularity) params.set("forecast_granularity", forecastGranularity);
    return request<SalesDashboard>(`/api/v1/analytics/sales-dashboard?${params.toString()}`);
  },
  salesActivityDrilldown: (
    metric: string,
    windowDays = 90,
    repId?: string | null,
    geographies: string[] = [],
    fromDate?: string,
    toDate?: string,
    limit = 50,
    offset = 0,
  ) => {
    const params = new URLSearchParams({ metric, window_days: String(windowDays) });
    if (repId) params.set("rep_id", repId);
    for (const g of geographies) params.append("geography", g);
    if (fromDate) params.set("from_date", fromDate);
    if (toDate) params.set("to_date", toDate);
    params.set("limit", String(limit));
    params.set("offset", String(offset));
    return request<SalesActivityDrilldown>(`/api/v1/analytics/sales-activity-drilldown?${params.toString()}`);
  },
  monthlyFunnelSummary: (months = 12) =>
    request<MonthlyUniqueFunnelRow[]>(`/api/v1/analytics/monthly-funnel-summary?months=${months}`),
};

// ── Global search ─────────────────────────────────────────────────────────────

export const globalSearchApi = {
  search: (query: string) =>
    request<GlobalSearchResponse>(`/api/v1/search/global?q=${encodeURIComponent(query)}`),
};

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

export const authApi = {
  me: () => request<User>("/api/v1/auth/me"),
  // Resolve /me against an explicit token (used to rehydrate the real
  // superadmin identity while an impersonation token is the active one).
  meWithToken: (token: string) =>
    request<User>("/api/v1/auth/me", { headers: { Authorization: `Bearer ${token}` } }),
  // Superadmin-only: returns a read-only token + the target user record.
  impersonate: (userId: string) =>
    request<{ token: string; user: User }>("/api/v1/auth/impersonate", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
  googleLoginUrl: () => `${BASE}/api/v1/auth/google/login`,
  listAllUsers: () => request<User[]>("/api/v1/auth/users/all"),
  listUsers: () => request<User[]>("/api/v1/auth/users"),
  createUser: (data: { email: string; name: string; role: string }) =>
    request<User>("/api/v1/auth/users", {
      method: "POST",
      body: JSON.stringify(data),
    }),
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

export const angelMappingApi = {
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
  bulkAssignCompanies: (ids: string[], userId: string | null, role: "ae" | "sdr" = "ae") =>
    request<{ updated: number; skipped?: number; user_id: string | null; role?: "ae" | "sdr" }>("/api/v1/assignments/bulk-companies", {
      method: "PATCH",
      body: JSON.stringify({ ids, user_id: userId, role }),
    }),
  bulkAssignContacts: (ids: string[], userId: string | null, role: "ae" | "sdr" = "ae") =>
    request<{ updated: number; skipped?: number; user_id: string | null; role?: "ae" | "sdr" }>("/api/v1/assignments/bulk-contacts", {
      method: "PATCH",
      body: JSON.stringify({ ids, user_id: userId, role }),
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

export interface JobHealthRow {
  beat_name: string;
  task: string;
  schedule: string;
  last_run_at: string | null;
  last_success_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_duration_ms: number | null;
  runs_total: number;
  failures_total: number;
  staleness: "ok" | "stale" | "failing" | "unknown";
}
export interface JobHealthResponse {
  jobs: JobHealthRow[];
  as_of: string;
}

export const settingsApi = {
  // Admin-only: scheduled-job health for the System Health panel.
  getJobHealth: () => request<JobHealthResponse>("/api/v1/admin/job-health"),
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
  getReportSender: () =>
    request<ReportSenderSettings>("/api/v1/settings/report-sender"),
  updateReportSender: (sender_email: string) =>
    request<ReportSenderSettings>("/api/v1/settings/report-sender", {
      method: "PATCH",
      body: JSON.stringify({ sender_email }),
    }),
  getReportSenderConnectUrl: () =>
    request<{ url: string }>("/api/v1/settings/report-sender/google/connect-url"),
  disconnectReportSender: () =>
    request<{ status: string }>("/api/v1/settings/report-sender/google", {
      method: "DELETE",
    }),
  getSalesReportSettings: () =>
    request<SalesReportSettings>("/api/v1/settings/sales-report"),
  updateSalesReportSettings: (data: Partial<SalesReportSettings>) =>
    request<SalesReportSettings>("/api/v1/settings/sales-report", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  sendSalesReportTest: () =>
    request<{ report_date: string; report_type: string; recipients: string[]; send_results?: Array<Record<string, unknown>> }>("/api/v1/sales-reports/us-pod-call-report/send", {
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
  getProspectVisibility: () =>
    request<{ user_ids: string[] }>("/api/v1/settings/prospect-visibility"),
  updateProspectVisibility: (userIds: string[]) =>
    request<{ user_ids: string[] }>("/api/v1/settings/prospect-visibility", {
      method: "PUT",
      body: JSON.stringify({ user_ids: userIds }),
    }),
  getZippySystemPrompt: () =>
    request<{ prompt: string; is_default: boolean }>("/api/v1/settings/zippy-system-prompt"),
  updateZippySystemPrompt: (prompt: string) =>
    request<{ prompt: string; is_default: boolean }>("/api/v1/settings/zippy-system-prompt", {
      method: "PATCH",
      body: JSON.stringify({ prompt }),
    }),
};

// ── Workspace insights (Sales Workspace / Dashboard) ────────────────────────

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

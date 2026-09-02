import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CalendarDays,
  CheckCircle2,
  GripVertical,
  Link2,
  Mail,
  Plus,
  RefreshCw,
  Shield,
  Sparkles,
  Target,
  Trash2,
  Unplug,
  Users,
  Clock,
  Wand2,
  Bot,
  PhoneCall,
  Loader2,
} from "lucide-react";
import { settingsApi, personalEmailSyncApi, driveApi, authApi, pushApi } from "../lib/api";
import { trashApi, type CompanyTrashRow, type DealTrashRow } from "../lib/api/trash";
import { getCachedGmailSync, getCachedRolePermissions, invalidateGmailSyncCache, invalidateRolePermissionsCache } from "../lib/cachedFetch";
import { disablePush, enablePush, getSubscriptionState, type PushSubscriptionState } from "../lib/push";
import type { DriveFolder, PersonalEmailStatus, SelectedDriveFolder, JobHealthRow } from "../lib/api";
import { DriveFolderPicker } from "../components/DriveFolderPicker";
import { KnowledgeSourcePanel } from "../components/zippy/KnowledgeSourcePanel";
import { useAuth } from "../lib/AuthContext";
import { useToast } from "../lib/ToastContext";
import type {
  ClickUpCrmSettings,
  DealStageSettings,
  ProspectStageSettings,
  GmailSyncSettings,
  ReportSenderSettings,
  SalesReportSettings,
  SalesReportRunResult,
  SalesAnalyticsRosterSettings,
  OutreachContentSettings,
  OutreachTemplateStep,
  PreMeetingAutomationSettings,
  RolePermissionsSettings,
  SyncScheduleSettings,
  WeeklyDigestSettings,
} from "../types";

type SettingsTab = "email-sync" | "outreach-ai" | "pipeline" | "permissions" | "pre-meeting" | "reports" | "sync-schedule" | "zippy" | "zippy-prompt" | "notifications" | "system-health" | "trash";

/** Trash timestamps as a short local date. The API layer already appends "Z"
 *  to naive UTC datetimes, but re-check here so a raw value can't silently
 *  render in the wrong timezone. */
function fmtTrashDate(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// Curated IANA timezones for the pre-meeting daily-send picker. The backend
// validates against the full zoneinfo database, so any value here is accepted;
// a non-listed stored value is appended at render time so it stays selectable.
const PRE_MEETING_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

const REPORT_DAYS = [
  { key: "mon", label: "Mon" },
  { key: "tue", label: "Tue" },
  { key: "wed", label: "Wed" },
  { key: "thu", label: "Thu" },
  { key: "fri", label: "Fri" },
  { key: "sat", label: "Sat" },
  { key: "sun", label: "Sun" },
];

function ReportDaySelector({
  selectedDays,
  disabled,
  onToggle,
}: {
  selectedDays: string[];
  disabled: boolean;
  onToggle: (day: string) => void;
}) {
  return (
    <fieldset style={{ display: "grid", gap: 8, border: 0, padding: 0, margin: 0 }}>
      <legend style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", marginBottom: 6 }}>Send days</legend>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(72px, 1fr))", gap: 8 }}>
        {REPORT_DAYS.map(({ key, label }) => {
          const checked = selectedDays.includes(key);
          return (
            <label
              key={key}
              style={{
                minHeight: 36,
                border: `1px solid ${checked ? "#9ace3d" : "#e3e9f2"}`,
                borderRadius: 8,
                background: checked ? "#f3fbe3" : "#fff",
                color: checked ? "#4d7c0f" : "#68788d",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                fontSize: 12,
                fontWeight: 700,
                cursor: disabled ? "default" : "pointer",
                opacity: disabled ? 0.65 : 1,
              }}
            >
              <input type="checkbox" checked={checked} disabled={disabled} onChange={() => onToggle(key)} />
              {label}
            </label>
          );
        })}
      </div>
      <span className="crm-muted" style={{ fontSize: 12 }}>Monday through Saturday are on by default. Sunday is off.</span>
    </fieldset>
  );
}

function formatTimestamp(epoch?: number | null) {
  if (!epoch) return "Never";
  return new Date(epoch * 1000).toLocaleString();
}

function formatDate(value?: string | null) {
  if (!value) return "Not connected";
  return new Date(value).toLocaleString();
}

function buildCcPattern(inbox?: string | null) {
  if (!inbox || !inbox.includes("@")) return "zippy+deal-name@beacon.li";
  const [local, domain] = inbox.split("@");
  return `${local}+deal-name@${domain}`;
}

function createTemplate(stepNumber: number): OutreachTemplateStep {
  return {
    step_number: stepNumber,
    channel: "email",
    label: `Step ${stepNumber}`,
    goal: "",
    subject_hint: "",
    body_template: "",
    prompt_hint: "",
  };
}

function slugifyStageId(label: string) {
  return label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "new_stage";
}

export default function SettingsPage() {
  const { isAdmin, user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<SettingsTab>("email-sync");
  const [jobHealth, setJobHealth] = useState<JobHealthRow[] | null>(null);
  const [jobHealthLoading, setJobHealthLoading] = useState(false);
  const [jobHealthError, setJobHealthError] = useState<string | null>(null);
  // Trash tab: soft-deleted accounts + deals, loaded lazily when the tab opens
  // (same pattern as System Health above). `null` = not fetched yet, which is
  // what keeps the empty state from flashing before the first load.
  const [trashCompanies, setTrashCompanies] = useState<CompanyTrashRow[] | null>(null);
  const [trashDeals, setTrashDeals] = useState<DealTrashRow[] | null>(null);
  const [trashLoading, setTrashLoading] = useState(false);
  const [trashError, setTrashError] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  // Web Push state for the Notifications tab. Drives the opt-in toggle so the
  // rep can register their *current* browser (typically their mobile PWA) to
  // receive "tap to call" notifications when their desktop clicks Call.
  const [pushState, setPushState] = useState<PushSubscriptionState | null>(null);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushTesting, setPushTesting] = useState(false);
  const [gmail, setGmail] = useState<GmailSyncSettings | null>(null);
  const [reportSender, setReportSender] = useState<ReportSenderSettings | null>(null);
  const [inbox, setInbox] = useState("zippy@beacon.li");
  const [reportSenderEmail, setReportSenderEmail] = useState("sarthak@beacon.li");
  const [outreachContent, setOutreachContent] = useState<OutreachContentSettings | null>(null);
  const [dealStages, setDealStages] = useState<DealStageSettings | null>(null);
  // Stage ids as they exist ON THE SERVER. Ids in this set are immutable
  // (deals reference them); ids outside it are drafts added this session and
  // get re-derived from their final label at save time — this is what stops
  // new stages from keeping their "new_stage_18"-style placeholder id forever.
  const savedDealStageIdsRef = useRef<Set<string>>(new Set());
  const [prospectStages, setProspectStages] = useState<ProspectStageSettings | null>(null);
  const [savingProspectStages, setSavingProspectStages] = useState(false);
  const [clickupCrmSettings, setClickupCrmSettings] = useState<ClickUpCrmSettings | null>(null);
  const [rolePermissions, setRolePermissions] = useState<RolePermissionsSettings | null>(null);
  const [preMeetingSettings, setPreMeetingSettings] = useState<PreMeetingAutomationSettings | null>(null);
  const [syncSchedule, setSyncSchedule] = useState<SyncScheduleSettings | null>(null);
  const [savingSyncSchedule, setSavingSyncSchedule] = useState(false);
  const [allUsers, setAllUsers] = useState<Array<{ id: string; name?: string | null; email?: string | null; role: string }>>([]);
  const [prospectViewAll, setProspectViewAll] = useState<string[]>([]);
  const [savingProspectVis, setSavingProspectVis] = useState(false);
  const [salesAnalyticsRoster, setSalesAnalyticsRoster] = useState<SalesAnalyticsRosterSettings | null>(null);
  const [savingSalesAnalyticsRoster, setSavingSalesAnalyticsRoster] = useState(false);
  const [salesReportSettings, setSalesReportSettings] = useState<SalesReportSettings | null>(null);
  const [savingSalesReportSettings, setSavingSalesReportSettings] = useState(false);
  const [indiaSalesReportSettings, setIndiaSalesReportSettings] = useState<SalesReportSettings | null>(null);
  const [savingIndiaSalesReportSettings, setSavingIndiaSalesReportSettings] = useState(false);
  const [sendingSalesReportTest, setSendingSalesReportTest] = useState(false);
  const [weeklyDigestSettings, setWeeklyDigestSettings] = useState<WeeklyDigestSettings | null>(null);
  const [savingWeeklyDigestSettings, setSavingWeeklyDigestSettings] = useState(false);
  const [sendingWeeklyDigestTest, setSendingWeeklyDigestTest] = useState(false);
  const [reportRunType, setReportRunType] = useState<"month_to_date" | "prior_quarter" | "custom">("month_to_date");
  const [reportAsOfDate, setReportAsOfDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [customReportStart, setCustomReportStart] = useState("");
  const [customReportEnd, setCustomReportEnd] = useState("");
  const [reportRecipient, setReportRecipient] = useState("");
  const [reportPreview, setReportPreview] = useState<SalesReportRunResult | null>(null);
  const [loadingReportPreview, setLoadingReportPreview] = useState(false);
  const [sendingPeriodReport, setSendingPeriodReport] = useState(false);
  // Zippy global system prompt (admin only)
  const [zippyPrompt, setZippyPrompt] = useState<string>("");
  const [zippyPromptIsDefault, setZippyPromptIsDefault] = useState<boolean>(true);
  const [zippyPromptLoading, setZippyPromptLoading] = useState(false);
  const [savingZippyPrompt, setSavingZippyPrompt] = useState(false);
  const [triggeringTldv, setTriggeringTldv] = useState(false);
  const [stoppingTldv, setStoppingTldv] = useState(false);
  const [outreachStepDelays, setOutreachStepDelays] = useState<number[]>([]);
  const [outreachTimingSteps, setOutreachTimingSteps] = useState<Array<{ step_number: number; day: number; channel: "email" | "call" | "linkedin" }>>([]);
  const [loading, setLoading] = useState(true);
  const [savingInbox, setSavingInbox] = useState(false);
  const [savingReportSender, setSavingReportSender] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectingReportSender, setConnectingReportSender] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectingReportSender, setDisconnectingReportSender] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [savingOutreach, setSavingOutreach] = useState(false);
  const [savingStages, setSavingStages] = useState(false);
  const [savingClickUpCrm, setSavingClickUpCrm] = useState(false);
  const [savingPermissions, setSavingPermissions] = useState(false);
  const [savingPreMeeting, setSavingPreMeeting] = useState(false);
  const [runningPreMeeting, setRunningPreMeeting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Personal email sync
  const [personalEmail, setPersonalEmail] = useState<PersonalEmailStatus | null>(null);
  const [connectingPersonal, setConnectingPersonal] = useState(false);
  const [disconnectingPersonal, setDisconnectingPersonal] = useState(false);
  const [syncingPersonal, setSyncingPersonal] = useState(false);
  const [monitorPersonalSync, setMonitorPersonalSync] = useState(false);
  const [personalSyncBaseline, setPersonalSyncBaseline] = useState<number | null>(null);
  const [userDriveFolder, setUserDriveFolder] = useState<SelectedDriveFolder | null>(null);
  const [adminDriveFolder, setAdminDriveFolder] = useState<SelectedDriveFolder | null>(null);
  const [driveLoading, setDriveLoading] = useState(false);
  const [drivePickerMode, setDrivePickerMode] = useState<"user" | "admin" | null>(null);
  const [driveMessage, setDriveMessage] = useState<string | null>(null);

  const statusTone = useMemo(() => {
    if (!gmail) return { bg: "#eef2ff", color: "#4b56c7", label: "Loading" };
    if (gmail.configured) return { bg: "#e8f8ee", color: "#217a49", label: "Connected" };
    if (gmail.inbox) return { bg: "#fff6df", color: "#a26a00", label: "Needs connect" };
    return { bg: "#f3f5fc", color: "#66748f", label: "Not set up" };
  }, [gmail]);

  const ccPattern = useMemo(() => buildCcPattern(gmail?.inbox || inbox), [gmail?.inbox, inbox]);
  const extraTemplateCount = Math.max((outreachContent?.step_templates.length ?? 0) - outreachStepDelays.length, 0);
  const canManageReports = isAdmin || Boolean(
    user && (user.role === "ae" || user.role === "sdr" || user.role === "marketing") &&
      rolePermissions?.[user.role]?.manage_reports,
  );

  const loadSettings = async () => {
    setLoading(true);
    setError(null);
    try {
      const [gmailData, reportSenderData, salesReportData, indiaSalesReportData, weeklyDigestData, outreachContentData, outreachTiming, dealStageData, prospectStageData, clickupCrmData, rolePermissionData, preMeetingData, syncScheduleData, personalEmailData] = await Promise.all([
        getCachedGmailSync(),
        settingsApi.getReportSender().catch(() => null),
        settingsApi.getSalesReportSettings().catch(() => null),
        settingsApi.getIndiaSalesReportSettings().catch(() => null),
        settingsApi.getWeeklyDigestSettings().catch(() => null),
        settingsApi.getOutreachContent(),
        settingsApi.getOutreach(),
        settingsApi.getDealStages(),
        settingsApi.getProspectStages().catch(() => null),
        settingsApi.getClickUpCrmSettings(),
        getCachedRolePermissions(),
        settingsApi.getPreMeetingAutomation(),
        settingsApi.getSyncSchedule().catch(() => null),
        personalEmailSyncApi.getStatus().catch(() => null),
      ]);
      setGmail(gmailData);
      setInbox(gmailData.inbox || "zippy@beacon.li");
      if (reportSenderData) {
        setReportSender(reportSenderData);
        setReportSenderEmail(reportSenderData.sender_email || "sarthak@beacon.li");
      }
      if (salesReportData) setSalesReportSettings(salesReportData);
      if (indiaSalesReportData) setIndiaSalesReportSettings(indiaSalesReportData);
      if (weeklyDigestData) setWeeklyDigestSettings(weeklyDigestData);
      if (personalEmailData) setPersonalEmail(personalEmailData);
      setOutreachContent(outreachContentData);
      setOutreachStepDelays(outreachTiming.step_delays);
      setOutreachTimingSteps(outreachTiming.steps);
      setDealStages(dealStageData);
      savedDealStageIdsRef.current = new Set((dealStageData?.stages ?? []).map((stage) => stage.id));
      setProspectStages(prospectStageData);
      setClickupCrmSettings(clickupCrmData);
      setRolePermissions(rolePermissionData);
      setPreMeetingSettings(preMeetingData);
      if (syncScheduleData) setSyncSchedule(syncScheduleData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  };

  const refreshPersonalEmailStatus = async () => {
    const status = await personalEmailSyncApi.getStatus();
    setPersonalEmail(status);
    return status;
  };

  const loadDriveFolders = async () => {
    setDriveLoading(true);
    try {
      const [userFolder, adminFolder] = await Promise.all([
        driveApi.getCurrentFolder().catch(() => null),
        driveApi.getAdminFolder().catch(() => null),
      ]);
      setUserDriveFolder(userFolder);
      setAdminDriveFolder(adminFolder);
    } finally {
      setDriveLoading(false);
    }
  };

  useEffect(() => {
    // Only load Drive folder state once the user has a personal connection
    // (because the scope lives on that connection).
    if (personalEmail?.connected) {
      void loadDriveFolders();
    } else {
      setUserDriveFolder(null);
      setAdminDriveFolder(null);
    }
  }, [personalEmail?.connected]);

  const handlePickUserFolder = async (folder: DriveFolder) => {
    try {
      const saved = await driveApi.selectFolder(folder.id, folder.name);
      setUserDriveFolder(saved);
      setDriveMessage(`Your personal Drive folder is now "${saved.folder_name}".`);
      toast.success(saved.folder_name || folder.name, "Drive folder saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save Drive folder");
    }
  };

  const handlePickAdminFolder = async (folder: DriveFolder) => {
    try {
      const saved = await driveApi.selectAdminFolder(folder.id, folder.name);
      setAdminDriveFolder(saved);
      setDriveMessage(`Workspace-wide Drive folder is now "${saved.folder_name}".`);
      toast.success(saved.folder_name || folder.name, "Workspace folder saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save workspace Drive folder");
    }
  };

  const handleClearUserFolder = async () => {
    try {
      await driveApi.clearFolder();
      await loadDriveFolders();
      setDriveMessage("Your personal Drive folder has been cleared.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear Drive folder");
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  useEffect(() => {
    if (!message) return;
    toast.success(message, "Done");
  }, [message]);

  useEffect(() => {
    if (!error) return;
    toast.error(error, "Something needs attention");
  }, [error]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const gmailStatus = params.get("gmail");
    const gmailConnected = params.get("gmail_connected");
    const reportSenderStatus = params.get("report_sender");
    const connectedEmail = params.get("email");
    if (gmailStatus === "connected") {
      setMessage("Gmail connected successfully. Beacon will keep syncing zippy@beacon.li automatically from here.");
      loadSettings();
    } else if (gmailStatus === "error") {
      setError("Gmail connection failed. Please try again.");
    }
    if (gmailConnected === "1") {
      setMessage(`Personal Gmail connected${connectedEmail ? ` (${connectedEmail})` : ""}. Your inbox is being scanned now — activities and contacts will appear shortly.`);
      setMonitorPersonalSync(true);
      setPersonalSyncBaseline(null);
      loadSettings();
    }
    if (reportSenderStatus === "connected") {
      setMessage(`Report sender connected${connectedEmail ? ` (${connectedEmail})` : ""}. Beacon can now send scheduled reports from this Gmail account.`);
      loadSettings();
    } else if (reportSenderStatus === "error") {
      setError("Report sender Gmail connection failed. Please try again.");
      loadSettings();
    }
    if (gmailStatus || gmailConnected || reportSenderStatus) {
      params.delete("gmail");
      params.delete("gmail_connected");
      params.delete("report_sender");
      params.delete("email");
      const query = params.toString();
      window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
    }
  }, []);

  useEffect(() => {
    if (!personalEmail?.connected || !monitorPersonalSync) return;

    let cancelled = false;
    const pollStatus = async () => {
      try {
        const status = await refreshPersonalEmailStatus();
        if (cancelled) return;

        if (status.last_error) {
          setMonitorPersonalSync(false);
          setPersonalSyncBaseline(null);
          return;
        }

        const syncAdvanced =
          typeof status.last_sync_epoch === "number" &&
          (personalSyncBaseline == null || status.last_sync_epoch !== personalSyncBaseline);

        if (status.backfill_completed && (syncAdvanced || personalSyncBaseline == null)) {
          setMonitorPersonalSync(false);
          setPersonalSyncBaseline(null);
          setMessage(
            personalSyncBaseline == null
              ? "Initial inbox scan is complete. Your email activity and meetings are ready."
              : "Personal inbox sync finished. Refresh any deal or meeting view to see the latest activity.",
          );
        }
      } catch {
        if (!cancelled) {
          setMonitorPersonalSync(false);
          setPersonalSyncBaseline(null);
        }
      }
    };

    void pollStatus();
    const timer = window.setInterval(() => {
      void pollStatus();
    }, 10000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [monitorPersonalSync, personalEmail?.connected, personalSyncBaseline]);

  const handleSaveInbox = async () => {
    setSavingInbox(true);
    setError(null);
    setMessage(null);
    try {
      const data = await settingsApi.updateGmailInbox(inbox.trim());
      invalidateGmailSyncCache();
      setGmail(data);
      setMessage("Shared mailbox saved. Next step: connect Gmail once as an admin.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save inbox");
    } finally {
      setSavingInbox(false);
    }
  };

  const handleConnect = async () => {
    setConnecting(true);
    setError(null);
    setMessage(null);
    try {
      if (!gmail?.inbox || gmail.inbox !== inbox.trim()) {
        await settingsApi.updateGmailInbox(inbox.trim());
        invalidateGmailSyncCache();
      }
      const result = await settingsApi.getGmailConnectUrl();
      window.location.assign(result.url);
    } catch (err) {
      setConnecting(false);
      setError(err instanceof Error ? err.message : "Failed to start Gmail connect");
    }
  };

  const handleDisconnect = async () => {
    setDisconnecting(true);
    setError(null);
    setMessage(null);
    try {
      await settingsApi.disconnectGmail();
      invalidateGmailSyncCache();
      await loadSettings();
      setMessage("Gmail disconnected. Sync is paused until an admin reconnects it.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect Gmail");
    } finally {
      setDisconnecting(false);
    }
  };

  const handleSyncNow = async () => {
    setSyncing(true);
    setError(null);
    setMessage(null);
    try {
      const result = await settingsApi.triggerEmailSync();
      setMessage(
        result.status === "queued"
          ? "Shared inbox sync queued. Beacon is checking for new emails in the background."
          : (result.message ?? "Sync request completed."),
      );
      await loadSettings();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to trigger sync");
    } finally {
      setSyncing(false);
    }
  };

  const handleSaveReportSender = async () => {
    if (!reportSenderEmail.trim()) {
      setError("Enter a report sender email first.");
      return;
    }
    setSavingReportSender(true);
    setError(null);
    setMessage(null);
    try {
      const data = await settingsApi.updateReportSender(reportSenderEmail.trim());
      setReportSender(data);
      setReportSenderEmail(data.sender_email || reportSenderEmail.trim());
      setMessage("Report sender saved. Connect Gmail to grant send permission for reports.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save report sender");
    } finally {
      setSavingReportSender(false);
    }
  };

  const handleConnectReportSender = async () => {
    setConnectingReportSender(true);
    setError(null);
    setMessage(null);
    try {
      if (!reportSender?.sender_email || reportSender.sender_email !== reportSenderEmail.trim()) {
        const data = await settingsApi.updateReportSender(reportSenderEmail.trim());
        setReportSender(data);
      }
      const result = await settingsApi.getReportSenderConnectUrl();
      window.location.assign(result.url);
    } catch (err) {
      setConnectingReportSender(false);
      setError(err instanceof Error ? err.message : "Failed to start report sender Gmail connect");
    }
  };

  const handleDisconnectReportSender = async () => {
    setDisconnectingReportSender(true);
    setError(null);
    setMessage(null);
    try {
      await settingsApi.disconnectReportSender();
      const data = await settingsApi.getReportSender();
      setReportSender(data);
      setMessage("Report sender disconnected. Scheduled report emails are paused until an admin reconnects it.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect report sender");
    } finally {
      setDisconnectingReportSender(false);
    }
  };

  const handleConnectPersonalEmail = async () => {
    setConnectingPersonal(true);
    setError(null);
    setMessage(null);
    try {
      const result = await personalEmailSyncApi.getConnectUrl();
      window.location.assign(result.url);
    } catch (err) {
      setConnectingPersonal(false);
      setError(err instanceof Error ? err.message : "Failed to start personal Gmail connect");
    }
  };

  const handleDisconnectPersonalEmail = async () => {
    setDisconnectingPersonal(true);
    setError(null);
    setMessage(null);
    try {
      await personalEmailSyncApi.disconnect();
      await loadSettings();
      setMessage("Personal Gmail disconnected. Your past synced activities remain in the CRM.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect personal Gmail");
    } finally {
      setDisconnectingPersonal(false);
    }
  };

  const handleSyncPersonalNow = async () => {
    setSyncingPersonal(true);
    setError(null);
    setMessage(null);
    try {
      const result = await personalEmailSyncApi.trigger();
      setPersonalSyncBaseline(personalEmail?.last_sync_epoch ?? null);
      setMonitorPersonalSync(true);
      setMessage(
        result.status === "queued"
          ? `Sync started for ${result.email_address}. Beacon is checking recent emails and calendar events now.`
          : "Sync request sent.",
      );
      await refreshPersonalEmailStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to trigger personal email sync");
    } finally {
      setSyncingPersonal(false);
    }
  };

  const handleStopTldvSync = async () => {
    setStoppingTldv(true);
    setError(null);
    setMessage(null);
    try {
      await settingsApi.stopTldvSync();
      await loadSettings();
      setMessage("tl;dv sync stop requested. Current run will stop between meetings and future scheduled runs are disabled.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop tl;dv sync");
    } finally {
      setStoppingTldv(false);
    }
  };

  const personalSyncStatusCopy = useMemo(() => {
    if (personalEmail?.last_error) {
      return {
        tone: "warning" as const,
        title: "Beacon needs you to reconnect this inbox",
        body: `${personalEmail.last_error?.includes("unauthorized_client") || personalEmail.last_error?.includes("invalid_grant") ? "Your Gmail authorisation has expired or been revoked." : "An error occurred while syncing your inbox."} Reconnect Gmail to resume email and calendar sync.`,
      };
    }
    if (!personalEmail?.connected) {
      return {
        tone: "info" as const,
        title: "Connect once, then Beacon keeps watching in the background",
        body: "After you connect Gmail, Beacon will keep checking your inbox and upcoming calendar events without you staying on this page.",
      };
    }
    if (personalEmail.has_calendar_scope === false) {
      return {
        tone: "warning" as const,
        title: "Calendar access needs reconnect",
        body: "Your Gmail sync is connected, but Google Calendar permission is missing. Reconnect once so Beacon can create upcoming customer meetings automatically.",
      };
    }
    if (monitorPersonalSync && !personalEmail.backfill_completed) {
      return {
        tone: "info" as const,
        title: "Initial inbox scan is running",
        body: "Beacon is scanning recent inbox history and upcoming meetings. This first pass can take a few minutes, and you can keep using the app while it works.",
      };
    }
    if (monitorPersonalSync) {
      return {
        tone: "info" as const,
        title: "Fresh sync is running",
        body: "Beacon is checking for any new messages and calendar changes right now. You can leave this page and come back.",
      };
    }
    if (!personalEmail.backfill_completed) {
      return {
        tone: "warning" as const,
        title: "Initial sync is still catching up",
        body: "Beacon has the connection, but the first historical pass is not done yet. New activities and meetings will keep appearing as it catches up.",
      };
    }
    return {
      tone: "info" as const,
      title: "Everything is connected",
      body: "Beacon automatically checks your inbox and calendar every 10 minutes. Use Sync now anytime you want it to check immediately.",
    };
  }, [monitorPersonalSync, personalEmail]);

  const updateOutreachField = (field: keyof OutreachContentSettings, value: string) => {
    setOutreachContent((current) => {
      if (!current) return current;
      return { ...current, [field]: value };
    });
  };

  const updateTemplate = (index: number, field: keyof OutreachTemplateStep, value: string) => {
    setOutreachContent((current) => {
      if (!current) return current;
      const nextTemplates = current.step_templates.map((template, templateIndex) =>
        templateIndex === index ? { ...template, [field]: value } : template,
      );
      return { ...current, step_templates: nextTemplates };
    });
  };

  const handleAddTemplate = () => {
    setOutreachContent((current) => {
      if (!current) return current;
      return {
        ...current,
        step_templates: [...current.step_templates, createTemplate(current.step_templates.length + 1)],
      };
    });
  };

  const handleRemoveTemplate = (index: number) => {
    setOutreachContent((current) => {
      if (!current || current.step_templates.length <= 1) return current;
      const nextTemplates = current.step_templates
        .filter((_, templateIndex) => templateIndex !== index)
        .map((template, templateIndex) => ({
          ...template,
          step_number: templateIndex + 1,
          label: template.label?.trim() ? template.label : `Step ${templateIndex + 1}`,
        }));
      return { ...current, step_templates: nextTemplates };
    });
  };

  const handleSaveOutreach = async () => {
    if (!outreachContent) return;
    setSavingOutreach(true);
    setError(null);
    setMessage(null);
    try {
      const payload: OutreachContentSettings = {
        general_prompt: outreachContent.general_prompt.trim(),
        linkedin_prompt: outreachContent.linkedin_prompt.trim(),
        step_templates: outreachContent.step_templates.map((template, index) => ({
          step_number: index + 1,
          channel: template.channel,
          label: template.label.trim() || `Step ${index + 1}`,
          goal: template.goal.trim(),
          subject_hint: template.subject_hint?.trim() || null,
          body_template: template.body_template?.trim() || null,
          prompt_hint: template.prompt_hint?.trim() || null,
        })),
      };
      const saved = await settingsApi.updateOutreachContent(payload);
      setOutreachContent(saved);
      setMessage("Outreach AI settings saved. New outreach generation will use this shared playbook.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save outreach settings");
    } finally {
      setSavingOutreach(false);
    }
  };

  const updateStage = (index: number, field: "label" | "group" | "color", value: string) => {
    setDealStages((current) => {
      if (!current) return current;
      const nextStages = current.stages.map((stage, stageIndex) =>
        stageIndex === index ? { ...stage, [field]: value } : stage
      );
      return { stages: nextStages };
    });
  };

  const moveStage = (index: number, direction: -1 | 1) => {
    setDealStages((current) => {
      if (!current) return current;
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= current.stages.length) return current;
      const nextStages = [...current.stages];
      const [item] = nextStages.splice(index, 1);
      nextStages.splice(nextIndex, 0, item);
      return { stages: nextStages };
    });
  };

  const addStage = () => {
    setDealStages((current) => {
      const existing = current?.stages ?? [];
      const baseLabel = `New Stage ${existing.length + 1}`;
      let nextId = slugifyStageId(baseLabel);
      let suffix = 2;
      while (existing.some((stage) => stage.id === nextId)) {
        nextId = `${slugifyStageId(baseLabel)}_${suffix}`;
        suffix += 1;
      }
      return {
        stages: [...existing, { id: nextId, label: baseLabel, group: "active", color: "#64748b" }],
      };
    });
  };

  const removeStage = (index: number) => {
    setDealStages((current) => {
      if (!current || current.stages.length <= 1) return current;
      return { stages: current.stages.filter((_, stageIndex) => stageIndex !== index) };
    });
  };

  const handleSaveStages = async () => {
    if (!dealStages) return;
    setSavingStages(true);
    setError(null);
    setMessage(null);
    try {
      const usedIds = new Set<string>();
      const normalized = {
        stages: dealStages.stages.map((stage, index) => {
          const label = stage.label.trim() || `Stage ${index + 1}`;
          // Server-known ids are immutable (deals reference them). A DRAFT
          // stage added this session gets its final id from its final LABEL,
          // so "Marketing Lead (MQL)" becomes marketing_lead_mql — not the
          // opaque new_stage_18 placeholder it was born with.
          let id = stage.id || slugifyStageId(label);
          if (!savedDealStageIdsRef.current.has(id)) {
            id = slugifyStageId(label);
          }
          let candidate = id;
          let suffix = 2;
          while (usedIds.has(candidate)) {
            candidate = `${id}_${suffix}`;
            suffix += 1;
          }
          usedIds.add(candidate);
          return {
            id: candidate,
            label,
            group: (stage.group === "closed" ? "closed" : "active") as "closed" | "active",
            color: stage.color || "#64748b",
          };
        }),
      };
      const saved = await settingsApi.updateDealStages(normalized);
      setDealStages(saved);
      savedDealStageIdsRef.current = new Set(saved.stages.map((stage) => stage.id));
      setMessage("Pipeline lanes saved. The deal board now follows this shared lane configuration.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save deal stages");
    } finally {
      setSavingStages(false);
    }
  };

  /* ── Prospect stage CRUD (mirrors deal stage handlers above) ── */
  const updateProspectStage = (index: number, field: "label" | "group" | "color", value: string) => {
    setProspectStages((current) => {
      if (!current) return current;
      const nextStages = current.stages.map((stage, i) =>
        i === index ? { ...stage, [field]: value } : stage
      );
      return { stages: nextStages };
    });
  };

  const moveProspectStage = (index: number, direction: -1 | 1) => {
    setProspectStages((current) => {
      if (!current) return current;
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= current.stages.length) return current;
      const nextStages = [...current.stages];
      const [item] = nextStages.splice(index, 1);
      nextStages.splice(nextIndex, 0, item);
      return { stages: nextStages };
    });
  };

  const addProspectStage = () => {
    setProspectStages((current) => {
      const existing = current?.stages ?? [];
      const baseLabel = `New Stage ${existing.length + 1}`;
      let nextId = slugifyStageId(baseLabel);
      let suffix = 2;
      while (existing.some((stage) => stage.id === nextId)) {
        nextId = `${slugifyStageId(baseLabel)}_${suffix}`;
        suffix += 1;
      }
      return {
        stages: [...existing, { id: nextId, label: baseLabel, group: "active", color: "#64748b" }],
      };
    });
  };

  const removeProspectStage = (index: number) => {
    setProspectStages((current) => {
      if (!current || current.stages.length <= 1) return current;
      return { stages: current.stages.filter((_, i) => i !== index) };
    });
  };

  const handleSaveProspectStages = async () => {
    if (!prospectStages) return;
    setSavingProspectStages(true);
    setError(null);
    setMessage(null);
    try {
      const normalized = {
        stages: prospectStages.stages.map((stage, index) => {
          const label = stage.label.trim() || `Stage ${index + 1}`;
          return {
            id: stage.id || slugifyStageId(label),
            label,
            group: (stage.group === "closed" ? "closed" : "active") as "closed" | "active",
            color: stage.color || "#64748b",
          };
        }),
      };
      const saved = await settingsApi.updateProspectStages(normalized);
      setProspectStages(saved);
      setMessage("Prospect lanes saved. The prospect board now follows this shared lane configuration.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save prospect stages");
    } finally {
      setSavingProspectStages(false);
    }
  };

  const updateClickUpCrmField = (field: keyof ClickUpCrmSettings, value: string) => {
    setClickupCrmSettings((current) => {
      if (!current) return current;
      return { ...current, [field]: value };
    });
  };

  const handleSaveClickUpCrm = async () => {
    if (!clickupCrmSettings) return;
    setSavingClickUpCrm(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await settingsApi.updateClickUpCrmSettings({
        team_id: clickupCrmSettings.team_id?.trim() || null,
        space_id: clickupCrmSettings.space_id?.trim() || null,
        deals_list_id: clickupCrmSettings.deals_list_id?.trim() || null,
      });
      setClickupCrmSettings(saved);
      setMessage("ClickUp CRM source settings saved. Beacon imports will use these IDs, falling back to env defaults when fields are blank.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save ClickUp CRM settings");
    } finally {
      setSavingClickUpCrm(false);
    }
  };

  const updateRolePermission = (
    role: keyof RolePermissionsSettings,
    key: keyof RolePermissionsSettings["ae"],
    value: boolean,
  ) => {
    setRolePermissions((current) => {
      if (!current) return current;
      return {
        ...current,
        [role]: {
          ...current[role],
          [key]: value,
        },
      };
    });
  };

  const handleSavePermissions = async () => {
    if (!rolePermissions) return;
    setSavingPermissions(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await settingsApi.updateRolePermissions(rolePermissions);
      invalidateRolePermissionsCache();
      setRolePermissions(saved);
      setMessage("Role permissions saved. Beacon will now enforce these rules across shared workflows.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save role permissions");
    } finally {
      setSavingPermissions(false);
    }
  };

  const updatePreMeetingField = <K extends keyof PreMeetingAutomationSettings>(field: K, value: PreMeetingAutomationSettings[K]) => {
    setPreMeetingSettings((current) => {
      if (!current) return current;
      return { ...current, [field]: value };
    });
  };

  const handleSavePreMeeting = async () => {
    if (!preMeetingSettings) return;
    setSavingPreMeeting(true);
    setError(null);
    setMessage(null);
    try {
      const payload: PreMeetingAutomationSettings = {
        ...preMeetingSettings,
        send_hours_before: Math.max(1, Math.min(168, Number(preMeetingSettings.send_hours_before) || 12)),
        generate_hours_before: Math.max(
          Math.max(1, Math.min(168, Number(preMeetingSettings.send_hours_before) || 12)),
          Math.min(168, Number(preMeetingSettings.generate_hours_before) || 48),
        ),
      };
      const saved = await settingsApi.updatePreMeetingAutomation(payload);
      setPreMeetingSettings(saved);
      setMessage("Pre-meeting automation saved. Beacon will generate and send prep intel using this schedule.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save pre-meeting automation");
    } finally {
      setSavingPreMeeting(false);
    }
  };

  const handleRunPreMeetingNow = async () => {
    setRunningPreMeeting(true);
    setError(null);
    setMessage(null);
    try {
      const result = await settingsApi.runPreMeetingAutomationNow();
      setMessage(
        `Pre-meeting automation checked ${result.checked} meeting${result.checked === 1 ? "" : "s"}, generated intel for ${result.generated}, emailed ${result.emailed}, and skipped ${result.skipped}.`,
      );
      await loadSettings();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run pre-meeting automation");
    } finally {
      setRunningPreMeeting(false);
    }
  };

  const updateSyncField = (field: keyof SyncScheduleSettings, value: number | boolean) => {
    if (!syncSchedule) return;
    setSyncSchedule({ ...syncSchedule, [field]: value });
  };

  const handleSaveSyncSchedule = async () => {
    if (!syncSchedule) return;
    setSavingSyncSchedule(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await settingsApi.updateSyncSchedule(syncSchedule);
      setSyncSchedule(updated);
      setMessage("Sync schedule saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save sync schedule");
    } finally {
      setSavingSyncSchedule(false);
    }
  };

  const handleToggleZippyOnly = async (next: boolean) => {
    setSavingSyncSchedule(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await settingsApi.updateSyncSchedule({ zippy_only_email_sync: next });
      setSyncSchedule(updated);
      setMessage(
        next
          ? "Zippy-only email tracking is ON — only zippy+<deal> CC'd emails are tracked; bulk inbox sync paused."
          : "Zippy-only email tracking is OFF — normal email sync resumed.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update email tracking mode");
    } finally {
      setSavingSyncSchedule(false);
    }
  };

  const updateSalesReportField = <K extends keyof SalesReportSettings>(field: K, value: SalesReportSettings[K]) => {
    if (!salesReportSettings) return;
    setSalesReportSettings({ ...salesReportSettings, [field]: value });
  };

  const updateSalesReportList = (field: "recipients" | "nonprod_recipients", value: string) => {
    updateSalesReportField(
      field,
      value.split(",").map((item) => item.trim()).filter(Boolean) as SalesReportSettings[typeof field],
    );
  };

  const toggleSalesReportDay = (day: string) => {
    if (!salesReportSettings) return;
    const current = new Set(salesReportSettings.send_days);
    if (current.has(day)) current.delete(day);
    else current.add(day);
    const ordered = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].filter((item) => current.has(item));
    setSalesReportSettings({ ...salesReportSettings, send_days: ordered });
  };

  const toggleIndiaSalesReportDay = (day: string) => {
    if (!indiaSalesReportSettings) return;
    const current = new Set(indiaSalesReportSettings.send_days);
    if (current.has(day)) current.delete(day);
    else current.add(day);
    const ordered = REPORT_DAYS.map(({ key }) => key).filter((item) => current.has(item));
    setIndiaSalesReportSettings({ ...indiaSalesReportSettings, send_days: ordered });
  };

  const handleSaveSalesReportSettings = async () => {
    if (!salesReportSettings) return;
    setSavingSalesReportSettings(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await settingsApi.updateSalesReportSettings(salesReportSettings);
      setSalesReportSettings(updated);
      setMessage("Sales report settings saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save sales report settings");
    } finally {
      setSavingSalesReportSettings(false);
    }
  };

  const handleSendSalesReportTest = async () => {
    setSendingSalesReportTest(true);
    setError(null);
    setMessage(null);
    try {
      const result = await settingsApi.sendSalesReportTest();
      const recipients = result.recipients?.join(", ") || "configured recipients";
      setMessage(`Test report sent to ${recipients}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send test report");
    } finally {
      setSendingSalesReportTest(false);
    }
  };

  const reportRunParams = () => {
    if (reportRunType === "custom") {
      if (!customReportStart || !customReportEnd) throw new Error("Choose both dates for a custom report.");
      return { reportType: reportRunType, periodStart: customReportStart, periodEnd: customReportEnd } as const;
    }
    return { reportType: reportRunType, date: reportAsOfDate || undefined } as const;
  };

  const handlePreviewPeriodReport = async () => {
    setLoadingReportPreview(true);
    setError(null);
    setMessage(null);
    try {
      setReportPreview(await settingsApi.previewUsPodCallReport(reportRunParams()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to build the report preview");
    } finally {
      setLoadingReportPreview(false);
    }
  };

  const handleSendPeriodReport = async () => {
    if (!reportRecipient.includes("@")) {
      setError("Enter one email address for the report delivery.");
      return;
    }
    setSendingPeriodReport(true);
    setError(null);
    setMessage(null);
    try {
      const result = await settingsApi.sendUsPodCallReport({ ...reportRunParams(), recipient: reportRecipient.trim() });
      setReportPreview(result);
      setMessage(`Report delivery requested for ${result.recipients.join(", ") || reportRecipient.trim()}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send the report");
    } finally {
      setSendingPeriodReport(false);
    }
  };

  useEffect(() => {
    if (!reportRecipient && user?.email) setReportRecipient(user.email);
  }, [reportRecipient, user?.email]);

  const handleSaveIndiaSalesReportSettings = async () => {
    if (!indiaSalesReportSettings) return;
    setSavingIndiaSalesReportSettings(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await settingsApi.updateIndiaSalesReportSettings({
        enabled: indiaSalesReportSettings.enabled,
        send_days: indiaSalesReportSettings.send_days,
      });
      setIndiaSalesReportSettings(updated);
      setMessage("India pod report schedule saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save India pod report schedule");
    } finally {
      setSavingIndiaSalesReportSettings(false);
    }
  };

  const updateWeeklyDigestField = <K extends keyof WeeklyDigestSettings>(field: K, value: WeeklyDigestSettings[K]) => {
    if (!weeklyDigestSettings) return;
    setWeeklyDigestSettings({ ...weeklyDigestSettings, [field]: value });
  };

  const updateWeeklyDigestList = (field: "recipients" | "nonprod_recipients", value: string) => {
    updateWeeklyDigestField(
      field,
      value.split(",").map((item) => item.trim()).filter(Boolean) as WeeklyDigestSettings[typeof field],
    );
  };

  const handleSaveWeeklyDigestSettings = async () => {
    if (!weeklyDigestSettings) return;
    setSavingWeeklyDigestSettings(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await settingsApi.updateWeeklyDigestSettings(weeklyDigestSettings);
      setWeeklyDigestSettings(updated);
      setMessage("Weekly digest settings saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save weekly digest settings");
    } finally {
      setSavingWeeklyDigestSettings(false);
    }
  };

  const handleSendWeeklyDigestTest = async () => {
    setSendingWeeklyDigestTest(true);
    setError(null);
    setMessage(null);
    try {
      const result = await settingsApi.sendWeeklyDigestTest();
      const recipients = result.recipients?.join(", ") || "configured recipients";
      const results = result.send_results ?? [];
      const sentCount = results.filter((r) => r.status === "sent").length;
      if (results.length > 0 && sentCount === 0) {
        const firstError = (results[0]?.error as string) || "Report sender Gmail account is not connected.";
        setError(`Test digest was not sent: ${firstError}`);
      } else if (sentCount < results.length) {
        setMessage(`Test digest sent to ${sentCount}/${results.length} recipient(s) (${recipients}) — some failed, see logs`);
      } else {
        setMessage(`Test digest sent to ${recipients}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send test digest");
    } finally {
      setSendingWeeklyDigestTest(false);
    }
  };

  const loadZippyPrompt = async () => {
    if (!isAdmin) return;
    setZippyPromptLoading(true);
    setError(null);
    try {
      const res = await settingsApi.getZippySystemPrompt();
      setZippyPrompt(res.prompt);
      setZippyPromptIsDefault(res.is_default);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Zippy prompt");
    } finally {
      setZippyPromptLoading(false);
    }
  };

  const handleSaveZippyPrompt = async () => {
    if (!isAdmin) return;
    setSavingZippyPrompt(true);
    setError(null);
    setMessage(null);
    try {
      const res = await settingsApi.updateZippySystemPrompt(zippyPrompt);
      setZippyPrompt(res.prompt);
      setZippyPromptIsDefault(res.is_default);
      setMessage(res.is_default ? "Reset to default prompt" : "Zippy prompt saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save Zippy prompt");
    } finally {
      setSavingZippyPrompt(false);
    }
  };

  const handleResetZippyPrompt = async () => {
    if (!isAdmin) return;
    if (!confirm("Reset Zippy's prompt to the built-in default? Your edits will be lost.")) return;
    setSavingZippyPrompt(true);
    setError(null);
    setMessage(null);
    try {
      const res = await settingsApi.updateZippySystemPrompt("");
      setZippyPrompt(res.prompt);
      setZippyPromptIsDefault(res.is_default);
      setMessage("Reset to default prompt");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reset Zippy prompt");
    } finally {
      setSavingZippyPrompt(false);
    }
  };

  // Lazy-load the prompt only when the tab is opened (admin only).
  useEffect(() => {
    if (activeTab === "zippy-prompt" && isAdmin && !zippyPrompt && !zippyPromptLoading) {
      loadZippyPrompt();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, isAdmin]);

  // Lazy-load users + person-level settings when the Permissions tab opens.
  useEffect(() => {
    if (activeTab === "permissions" && isAdmin && (allUsers.length === 0 || !salesAnalyticsRoster)) {
      Promise.all([
        authApi.listUsers().catch(() => []),
        settingsApi.getProspectVisibility().catch(() => ({ user_ids: [] as string[] })),
        settingsApi.getSalesAnalyticsRoster().catch(() => ({ user_ids: [] as string[], default_emails: [] as string[] })),
      ]).then(([users, prospectVis, roster]) => {
        setAllUsers(users as Array<{ id: string; name?: string | null; email?: string | null; role: string }>);
        setProspectViewAll(prospectVis.user_ids || []);
        setSalesAnalyticsRoster(roster);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, isAdmin, salesAnalyticsRoster]);

  const toggleProspectViewAll = async (userId: string, checked: boolean) => {
    const prev = prospectViewAll;
    const next = checked ? Array.from(new Set([...prev, userId])) : prev.filter((id) => id !== userId);
    setProspectViewAll(next); // optimistic
    setSavingProspectVis(true);
    setError(null);
    try {
      const res = await settingsApi.updateProspectVisibility(next);
      setProspectViewAll(res.user_ids || []);
    } catch (err) {
      setProspectViewAll(prev); // revert on failure
      setError(err instanceof Error ? err.message : "Failed to update prospect visibility");
    } finally {
      setSavingProspectVis(false);
    }
  };

  const toggleSalesAnalyticsRoster = async (userId: string, checked: boolean) => {
    const prev = salesAnalyticsRoster ?? { user_ids: [], default_emails: [] };
    const nextIds = checked
      ? Array.from(new Set([...(prev.user_ids || []), userId]))
      : (prev.user_ids || []).filter((id) => id !== userId);
    setSalesAnalyticsRoster({ ...prev, user_ids: nextIds });
    setSavingSalesAnalyticsRoster(true);
    setError(null);
    try {
      const res = await settingsApi.updateSalesAnalyticsRoster(nextIds);
      setSalesAnalyticsRoster(res);
      setMessage("Sales Analytics roster saved");
    } catch (err) {
      setSalesAnalyticsRoster(prev);
      setError(err instanceof Error ? err.message : "Failed to update Sales Analytics roster");
    } finally {
      setSavingSalesAnalyticsRoster(false);
    }
  };

  const loadJobHealth = () => {
    setJobHealthLoading(true);
    setJobHealthError(null);
    settingsApi
      .getJobHealth()
      .then((res) => setJobHealth(res.jobs))
      .catch((e) => setJobHealthError(e instanceof Error ? e.message : "Failed to load job health"))
      .finally(() => setJobHealthLoading(false));
  };

  // Load scheduled-job health whenever the admin opens the System Health tab.
  useEffect(() => {
    if (activeTab === "system-health" && isAdmin) loadJobHealth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, isAdmin]);

  const loadTrash = () => {
    setTrashLoading(true);
    setTrashError(null);
    Promise.all([trashApi.listCompanies(50), trashApi.listDeals(50)])
      .then(([companies, deals]) => {
        setTrashCompanies(companies);
        setTrashDeals(deals);
      })
      .catch((e) => setTrashError(e instanceof Error ? e.message : "Failed to load trash"))
      .finally(() => setTrashLoading(false));
  };

  // Lazy: only fetched when an admin actually opens Trash.
  useEffect(() => {
    if (activeTab === "trash" && isAdmin) loadTrash();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, isAdmin]);

  const restoreCompany = async (row: CompanyTrashRow) => {
    // Spell out what restore actually does. A merged loser is the sharp edge:
    // its data stayed on the winner, so restoring gives back an empty shell —
    // say so before the click, not after.
    const note = row.merged_into_name
      ? `\n\nThis account was MERGED into ${row.merged_into_name}. Restoring does NOT un-merge it: its contacts, deals, and activity stay on ${row.merged_into_name}, so "${row.name}" comes back as an empty account.`
      : `\n\nComes back: the account, its ${row.prospect_count} prospect${row.prospect_count === 1 ? "" : "s"}, and the deals deleted with it.\nStays as-is: tasks that were dismissed when it was deleted.`;
    if (!confirm(`Restore ${row.name}?${note}`)) return;
    setRestoringId(row.id);
    try {
      const res = await trashApi.restoreCompany(row.id);
      setTrashCompanies((prev) => (prev ?? []).filter((c) => c.id !== row.id));
      // A company restore un-deletes its deals too, so the deal table is now
      // stale. Refetch rather than guessing which rows went — deal names alone
      // can't identify which account they belonged to.
      if (res.deals_restored > 0) loadTrash();
      toast.success(
        res.deals_restored > 0
          ? `${res.name} restored with ${res.deals_restored} deal${res.deals_restored === 1 ? "" : "s"}.`
          : `${res.name} restored.`,
        "Account restored",
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Restore failed", "Could not restore");
    } finally {
      setRestoringId(null);
    }
  };

  const restoreDeal = async (row: DealTrashRow) => {
    if (
      !confirm(
        `Restore ${row.name}?\n\nComes back: the deal, with its activity, stage history, and stakeholders.\nStays as-is: tasks that were dismissed when it was deleted.`,
      )
    )
      return;
    setRestoringId(row.id);
    try {
      const res = await trashApi.restoreDeal(row.id);
      setTrashDeals((prev) => (prev ?? []).filter((d) => d.id !== row.id));
      toast.success(`${res.name} is back on the pipeline.`, "Deal restored");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Restore failed", "Could not restore");
    } finally {
      setRestoringId(null);
    }
  };

  // Refresh push subscription state whenever the Notifications tab is
  // opened — covers the case where the user revoked permission in
  // browser settings between visits.
  useEffect(() => {
    if (activeTab !== "notifications") return;
    let cancelled = false;
    getSubscriptionState()
      .then((state) => { if (!cancelled) setPushState(state); })
      .catch(() => { if (!cancelled) setPushState(null); });
    return () => { cancelled = true; };
  }, [activeTab]);

  const sendTestNotification = async () => {
    setPushTesting(true);
    try {
      const r = await pushApi.sendTest();
      if (r.total === 0) {
        toast.error("No devices registered for this account. Tap Enable on the device you want notified.", "No devices");
      } else if (r.sent > 0) {
        // Delivery past the push service is invisible to the server, so say
        // what we actually know and where to look if nothing shows up.
        toast.success(
          `Sent to ${r.sent} device${r.sent > 1 ? "s" : ""}. If nothing appears, check notification permission for the installed Beacon app and turn off Focus/Do Not Disturb.`,
          "Test sent",
        );
      } else if (r.removed > 0) {
        toast.error("That device's subscription had expired and was removed. Tap Enable on the device again.", "Subscription expired");
      } else {
        toast.error("Could not send to any registered device.", "Test failed");
      }
      setPushState(await getSubscriptionState());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Test notification failed.", "Test failed");
    } finally {
      setPushTesting(false);
    }
  };

  const togglePushNotifications = async () => {
    setPushBusy(true);
    try {
      const wasOn = !!pushState?.subscribed;
      const result = wasOn ? await disablePush() : await enablePush();
      if (!result.ok) {
        toast.error(result.reason || "Could not change notification setting.", "Notification error");
      } else {
        toast.success(wasOn ? "Mobile call notifications disabled." : "This device is registered for call notifications.", "Saved");
      }
      const next = await getSubscriptionState();
      setPushState(next);
    } catch (err) {
      // Without this the toggle just stopped spinning and said nothing when
      // the browser threw (common on iOS and when a subscription is stale),
      // which reads to the user as "notifications are silently broken".
      toast.error(
        err instanceof Error ? err.message : "Could not change notification setting.",
        "Notification error",
      );
    } finally {
      setPushBusy(false);
    }
  };

  const handleTriggerTldvSync = async () => {
    setTriggeringTldv(true);
    setError(null);
    setMessage(null);
    try {
      await settingsApi.triggerTldvSync();
      setMessage("TLDV sync triggered — check worker logs for progress");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to trigger TLDV sync");
    } finally {
      setTriggeringTldv(false);
    }
  };

  const tabButton = (id: SettingsTab, label: string, icon: ReactNode) => (
    <button
      key={id}
      type="button"
      onClick={() => setActiveTab(id)}
      className={`crm-button ${activeTab === id ? "primary" : "soft"} settings-nav-button`}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <div className="crm-page" style={{ maxWidth: 1160, display: "grid", gap: 16, paddingBottom: 64 }}>
      <div className="settings-layout">
          <aside className="crm-panel settings-nav-panel" style={{ boxShadow: "none" }}>
            <div className="settings-nav-list">
              {tabButton("email-sync", "Email Sync", <Mail size={15} />)}
              {tabButton("outreach-ai", "Outreach AI", <Sparkles size={15} />)}
              {tabButton("pipeline", "Pipeline", <GripVertical size={15} />)}
              {tabButton("permissions", "Permissions", <Users size={15} />)}
              {tabButton("pre-meeting", "Pre-Meeting", <Shield size={15} />)}
              {tabButton("reports", "Reports", <CalendarDays size={15} />)}
              {tabButton("sync-schedule", "Sync Schedule", <Clock size={15} />)}
              {tabButton("notifications", "Notifications", <PhoneCall size={15} />)}
              {tabButton("zippy", "Zippy", <Bot size={15} />)}
              {isAdmin && tabButton("zippy-prompt", "System Prompt", <Shield size={15} />)}
              {isAdmin && tabButton("system-health", "System Health", <RefreshCw size={15} />)}
              {isAdmin && tabButton("trash", "Trash", <Trash2 size={15} />)}
            </div>
          </aside>

          <div style={{ display: "grid", gap: 14, minWidth: 0 }}>

        {message && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", borderRadius: 12, background: "#eaf8ef", border: "1px solid #cbe8d5", color: "#1f7a47" }}>
            <CheckCircle2 size={18} />
            <span>{message}</span>
          </div>
        )}

        {(error || gmail?.last_error) && activeTab === "email-sync" && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", borderRadius: 12, background: "#fff4e6", border: "1px solid #f0d4ac", color: "#a46206" }}>
            <AlertTriangle size={18} />
            <span>{error || gmail?.last_error}</span>
          </div>
        )}

        {error && activeTab !== "email-sync" && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", borderRadius: 12, background: "#fff4e6", border: "1px solid #f0d4ac", color: "#a46206" }}>
            <AlertTriangle size={18} />
            <span>{error}</span>
          </div>
        )}

        {activeTab === "email-sync" ? (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#eef2ff", color: "#4958d8", borderColor: "#d8def8" }}>
                  <Mail size={14} />
                  Email Sync
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Shared inbox tracking</h3>
                <p className="crm-muted" style={{ maxWidth: 700, lineHeight: 1.7 }}>
                  Connect one shared mailbox once as an admin, then Beacon will keep pulling CC'd customer emails into deal activity automatically.
                  Reps should CC <strong>{ccPattern}</strong> on customer threads so Beacon can map the email straight to the right deal.
                </p>
              </div>
              <div style={{ padding: "6px 14px", borderRadius: 999, background: statusTone.bg, color: statusTone.color, fontSize: 12, fontWeight: 700, minWidth: 120, textAlign: "center" }}>
                {statusTone.label}
              </div>
            </div>

            {isAdmin && (
              <div className="crm-panel" style={{ padding: 16, borderRadius: 14, boxShadow: "none", border: "1px solid #d8def8", background: "#f7f9ff" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                  <div style={{ maxWidth: 660 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 4 }}>
                      Zippy-only email tracking
                    </div>
                    <p className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6, margin: 0 }}>
                      When ON, Beacon tracks an email <strong>only</strong> if it's CC'd to an alias like <strong>{ccPattern}</strong>.
                      The broad contact-match fallback and per-rep inbox bulk sync pause — existing activity is untouched, and turning
                      this OFF resumes normal sync immediately.
                    </p>
                  </div>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 10, cursor: syncSchedule ? "pointer" : "not-allowed" }}>
                    <input
                      type="checkbox"
                      checked={Boolean(syncSchedule?.zippy_only_email_sync)}
                      disabled={!syncSchedule || savingSyncSchedule}
                      onChange={(event) => void handleToggleZippyOnly(event.target.checked)}
                      style={{ width: 18, height: 18 }}
                    />
                    <span style={{ fontWeight: 700, color: syncSchedule?.zippy_only_email_sync ? "#1f7a47" : "#68788d" }}>
                      {syncSchedule?.zippy_only_email_sync ? "ON" : "OFF"}
                    </span>
                  </label>
                </div>
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(320px, 0.75fr)", gap: 14 }}>
              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none" }}>
                <div style={{ display: "grid", gap: 14 }}>
                  <div>
                    <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                      Shared mailbox
                    </div>
                    <input
                      value={inbox}
                      onChange={(event) => setInbox(event.target.value)}
                      placeholder="zippy@beacon.li"
                      style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                      disabled={!isAdmin}
                    />
                    <p className="crm-muted" style={{ marginTop: 6, fontSize: 12 }}>
                      This is the base mailbox Beacon watches. Reps should CC aliases like <strong>{ccPattern}</strong>.
                    </p>
                  </div>

                  {isAdmin ? (
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      <button className="crm-button soft" onClick={handleSaveInbox} disabled={savingInbox}>
                        {savingInbox ? <RefreshCw size={15} className="animate-spin" /> : <Shield size={15} />}
                        Save inbox
                      </button>
                      <button className="crm-button primary" onClick={handleConnect} disabled={connecting}>
                        {connecting ? <RefreshCw size={15} className="animate-spin" /> : <Link2 size={15} />}
                        Connect Gmail
                      </button>
                      <button className="crm-button soft" onClick={handleDisconnect} disabled={disconnecting || !gmail?.configured} style={{ marginLeft: 6, color: "#b42336", borderColor: "#f0c1c8", background: "#fff" }}>
                        {disconnecting ? <RefreshCw size={15} className="animate-spin" /> : <Unplug size={15} />}
                        Disconnect
                      </button>
                      <button className="crm-button soft" onClick={handleSyncNow} disabled={syncing || !gmail?.configured}>
                        {syncing ? <RefreshCw size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                        Sync now
                      </button>
                    </div>
                  ) : (
                    <p className="crm-muted" style={{ fontSize: 12 }}>
                      Only admins can change the inbox connection. Everyone can view sync status here.
                    </p>
                  )}
                </div>
              </div>

              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 14 }}>
                <div>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                    Connection status
                  </div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: "#142335" }}>
                    {loading ? "Loading..." : (gmail?.configured ? "Auto-sync active" : "Needs setup")}
                  </div>
                </div>
                <div style={{ display: "grid", gap: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Connected mailbox</span>
                    <strong style={{ color: "#142335" }}>{gmail?.connected_email || "Not connected"}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Connected at</span>
                    <strong style={{ color: "#142335" }}>{formatDate(gmail?.connected_at)}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Last sync</span>
                    <strong style={{ color: "#142335" }}>{formatTimestamp(gmail?.last_sync_epoch)}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Polling interval</span>
                    <strong style={{ color: "#142335" }}>{gmail ? `${Math.round(gmail.interval_seconds / 60)} min` : "--"}</strong>
                  </div>
                </div>
              </div>
            </div>

            <section className="crm-panel" style={{ padding: 18, display: "grid", gap: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
                <div>
                  <div className="crm-chip" style={{ marginBottom: 10, background: "#fff7ed", color: "#c2410c", borderColor: "#fed7aa" }}>
                    <Mail size={13} />
                    Report Sender
                  </div>
                  <h3 style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 4 }}>
                    Daily report Gmail sender
                  </h3>
                  <p className="crm-muted" style={{ maxWidth: 720, lineHeight: 1.6, fontSize: 13 }}>
                    Beacon sends the US pod daily call report from this mailbox. Use your email now, then switch to a dedicated reports mailbox later without code changes.
                  </p>
                </div>
                <div style={{
                  padding: "6px 12px",
                  borderRadius: 999,
                  background: reportSender?.configured ? "#e8f8ee" : reportSender?.sender_email ? "#fff6df" : "#f3f5fc",
                  color: reportSender?.configured ? "#217a49" : reportSender?.sender_email ? "#a26a00" : "#66748f",
                  fontSize: 12,
                  fontWeight: 700,
                }}>
                  {reportSender?.configured ? "Ready to send" : reportSender?.sender_email ? "Needs Gmail connect" : "Not set up"}
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(320px, 0.8fr)", gap: 16 }}>
                <div style={{ display: "grid", gap: 12 }}>
                  <div>
                    <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                      Sender mailbox
                    </div>
                    <input
                      value={reportSenderEmail}
                      onChange={(event) => setReportSenderEmail(event.target.value)}
                      placeholder="sarthak@beacon.li"
                      style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                      disabled={!isAdmin}
                    />
                  </div>
                  {isAdmin ? (
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      <button className="crm-button soft" onClick={handleSaveReportSender} disabled={savingReportSender}>
                        {savingReportSender ? <RefreshCw size={15} className="animate-spin" /> : <Shield size={15} />}
                        Save sender
                      </button>
                      <button className="crm-button primary" onClick={handleConnectReportSender} disabled={connectingReportSender}>
                        {connectingReportSender ? <RefreshCw size={15} className="animate-spin" /> : <Link2 size={15} />}
                        Connect Gmail send
                      </button>
                      <button className="crm-button soft" onClick={handleDisconnectReportSender} disabled={disconnectingReportSender || !reportSender?.connected_email} style={{ marginLeft: 6, color: "#b42336", borderColor: "#f0c1c8", background: "#fff" }}>
                        {disconnectingReportSender ? <RefreshCw size={15} className="animate-spin" /> : <Unplug size={15} />}
                        Disconnect sender
                      </button>
                    </div>
                  ) : (
                    <p className="crm-muted" style={{ fontSize: 12 }}>Only admins can change the report sender.</p>
                  )}
                  {reportSender?.last_error && (
                    <div style={{ padding: "10px 12px", borderRadius: 10, background: "#fff4e6", border: "1px solid #f0d4ac", color: "#a46206", fontSize: 13 }}>
                      {reportSender.last_error}
                    </div>
                  )}
                </div>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Configured sender</span>
                    <strong style={{ color: "#142335" }}>{reportSender?.sender_email || "Not set"}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Connected Gmail</span>
                    <strong style={{ color: "#142335" }}>{reportSender?.connected_email || "Not connected"}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Send permission</span>
                    <strong style={{ color: reportSender?.has_send_scope ? "#217a49" : "#a26a00" }}>{reportSender?.has_send_scope ? "Granted" : "Missing"}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span className="crm-muted">Connected at</span>
                    <strong style={{ color: "#142335" }}>{formatDate(reportSender?.connected_at)}</strong>
                  </div>
                </div>
              </div>
            </section>

            <section className="crm-panel" style={{ padding: 18 }}>
              <h3 style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 10 }}>How it works</h3>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 14 }}>
                {[
                  {
                    title: "1. Admin connects once",
                    body: "Connect zippy@beacon.li through Google OAuth. Beacon stores the refresh token and keeps the connection alive.",
                  },
                  {
                    title: "2. Reps CC the shared inbox",
                    body: `Customer emails stay in normal rep workflows. The only habit change is CC'ing an alias like ${ccPattern} so Beacon can map the thread to the right deal.`,
                  },
                  {
                    title: "3. Beacon logs activity automatically",
                    body: "Synced emails land in deal activity with AI summaries, so the CRM stays current without reps rewriting notes.",
                  },
                ].map((item) => (
                  <div key={item.title} style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff" }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 8 }}>{item.title}</div>
                    <p className="crm-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>{item.body}</p>
                  </div>
                ))}
              </div>
            </section>

            {/* ── Personal Gmail Sync ─────────���───────────────────────── */}
            <section className="crm-panel" style={{ padding: 18, display: "grid", gap: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
                <div>
                  <div className="crm-chip" style={{ marginBottom: 10, background: "#f0f4ff", color: "#3b4dc8", borderColor: "#d4dcf8" }}>
                    <Mail size={13} />
                    Personal Inbox + Calendar Sync
                  </div>
                  <h3 style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 4 }}>
                    Connect your personal Gmail &amp; Calendar
                  </h3>
                  <p className="crm-muted" style={{ maxWidth: 600, lineHeight: 1.6, fontSize: 13 }}>
                    Beacon scans your past emails and upcoming calendar events. Emails are matched to deals and
                    contacts. Calendar events with external attendees are auto-created as meetings — complete with
                    scheduled time, Meet link, and pre-meeting intel 12 hours before the call.
                  </p>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  <button
                    className="crm-button primary"
                    onClick={handleConnectPersonalEmail}
                    disabled={connectingPersonal}
                  >
                    {connectingPersonal ? <RefreshCw size={15} className="animate-spin" /> : <Link2 size={15} />}
                    {personalEmail?.connected ? "Reconnect Gmail" : "Connect my Gmail"}
                  </button>
                  {personalEmail?.connected && (
                    <button
                      className="crm-button soft"
                      onClick={handleSyncPersonalNow}
                      disabled={syncingPersonal}
                    >
                      {syncingPersonal ? <RefreshCw size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                      Sync now
                    </button>
                  )}
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
                <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                  <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                    Status
                  </div>
                  <div style={{
                    display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 12px",
                    borderRadius: 20, fontSize: 13, fontWeight: 700,
                    background: !personalEmail?.connected ? "#f3f5fc" : monitorPersonalSync ? "#eef5ff" : personalEmail.has_calendar_scope === false ? "#fff4e6" : "#e8f8ee",
                    color: !personalEmail?.connected ? "#66748f" : monitorPersonalSync ? "#3b4dc8" : personalEmail.has_calendar_scope === false ? "#a46206" : "#217a49",
                  }}>
                    {!personalEmail?.connected ? "Not connected" : monitorPersonalSync ? "Syncing…" : personalEmail.has_calendar_scope === false ? "Email connected" : "Connected"}
                  </div>
                </div>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                  <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                    Connected email
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>
                    {personalEmail?.email_address || "—"}
                  </div>
                </div>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                  <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                    Last synced
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>
                    {formatTimestamp(personalEmail?.last_sync_epoch)}
                  </div>
                </div>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                  <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                    Historical backfill
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: personalEmail?.backfill_completed ? "#217a49" : "#a26a00" }}>
                    {personalEmail?.backfill_completed ? "Complete" : personalEmail?.connected ? "In progress…" : "—"}
                  </div>
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  padding: "12px 14px",
                  borderRadius: 10,
                  background: personalSyncStatusCopy.tone === "warning" ? "#fff4e6" : "#f0f6ff",
                  border: personalSyncStatusCopy.tone === "warning" ? "1px solid #f0d4ac" : "1px solid #c8daf8",
                  color: personalSyncStatusCopy.tone === "warning" ? "#a46206" : "#1a4fa8",
                  fontSize: 13,
                }}
              >
                {personalSyncStatusCopy.tone === "warning" ? (
                  <AlertTriangle size={16} style={{ marginTop: 1, flexShrink: 0 }} />
                ) : monitorPersonalSync ? (
                  <RefreshCw size={16} className="animate-spin" style={{ marginTop: 1, flexShrink: 0 }} />
                ) : (
                  <CalendarDays size={16} style={{ marginTop: 1, flexShrink: 0 }} />
                )}
                <span>
                  <strong>{personalSyncStatusCopy.title}.</strong> {personalSyncStatusCopy.body}
                  {personalEmail?.connected && !personalEmail?.last_error ? (
                    <>
                      {" "}Beacon also checks your upcoming Google Calendar events every 10 minutes and auto-creates meetings for customer calls. If you connected before calendar access was added,{" "}
                      <button
                        onClick={handleConnectPersonalEmail}
                        style={{ background: "none", border: "none", color: "inherit", fontWeight: 700, textDecoration: "underline", cursor: "pointer", padding: 0, fontSize: 13 }}
                      >
                        reconnect once to refresh permissions
                      </button>
                      .
                    </>
                  ) : null}
                </span>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
                {[
                  { title: "Conversation mapping", body: "Matches emails to deals via contact address, company domain, or AI classification." },
                  { title: "Auto-create contacts", body: "New stakeholders found in email threads are added to the CRM and linked to the deal." },
                  { title: "AI task generation", body: "Detects key moments — POC agreed, pricing asked, meeting requested — and creates tasks automatically." },
                  { title: "Historical backfill", body: "On first connect, scans the last 90 days of your inbox to surface past conversations." },
                ].map((item) => (
                  <div key={item.title} style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#fff" }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 6 }}>{item.title}</div>
                    <p className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>{item.body}</p>
                  </div>
                ))}
              </div>
            </section>

            {/* ── Knowledge Source (merged: folder picker + Zippy index) ──── */}
            <KnowledgeSourcePanel
              isAdmin={isAdmin}
              connected={!!personalEmail?.connected}
              driveLoading={driveLoading}
              userFolder={userDriveFolder}
              adminFolder={adminDriveFolder}
              driveMessage={driveMessage}
              onOpenPicker={(scope) => setDrivePickerMode(scope)}
              onClearUser={handleClearUserFolder}
            />
          </>
        ) : activeTab === "outreach-ai" ? (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#f5efff", color: "#6f46d9", borderColor: "#e4d8ff" }}>
                  <Wand2 size={14} />
                  Outreach AI
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Shared outreach playbook</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  This is the shared writing system Beacon uses when it generates outreach. Sequence timing controls <strong>when</strong> each touch
                  goes out, and these prompts plus templates control <strong>how</strong> each touch sounds.
                </p>
              </div>
              <div className="crm-panel" style={{ padding: 16, borderRadius: 14, boxShadow: "none", minWidth: 300 }}>
                <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 8 }}>
                  Current sequence timing
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  {outreachTimingSteps.map((step) => (
                    <span key={`${step.step_number}-${step.channel}-${step.day}`} className="crm-chip" style={{ background: "#eef2ff", color: "#4958d8", borderColor: "#d8def8" }}>
                      Step {step.step_number}: {step.channel === "linkedin" ? "LinkedIn" : step.channel === "call" ? "Call" : "Email"} · Day {step.day}
                    </span>
                  ))}
                </div>
                <p className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                  Timing now supports mixed touches too, so the shared playbook can combine email, LinkedIn, and call steps without needing separate workflows.
                </p>
              </div>
            </div>

            <div style={{ display: "grid", gap: 14 }}>
              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
                <div>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                    General AI prompt
                  </div>
                  <textarea
                    value={outreachContent?.general_prompt ?? ""}
                    onChange={(event) => updateOutreachField("general_prompt", event.target.value)}
                    disabled={!outreachContent}
                    rows={5}
                    placeholder="Tell Beacon the shared writing rules, tone, and constraints to follow across all emails."
                    style={{ width: "100%", resize: "vertical", minHeight: 120 }}
                  />
                  <p className="crm-muted" style={{ marginTop: 6, fontSize: 12 }}>
                    Use this for shared rules like tone, CTA style, banned phrasing, compliance guardrails, or how personalized you want the emails to feel.
                  </p>
                </div>

                <div>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                    LinkedIn prompt
                  </div>
                  <textarea
                    value={outreachContent?.linkedin_prompt ?? ""}
                    onChange={(event) => updateOutreachField("linkedin_prompt", event.target.value)}
                    disabled={!outreachContent}
                    rows={3}
                    placeholder="Guide how Beacon should write LinkedIn connection notes."
                    style={{ width: "100%", resize: "vertical", minHeight: 90 }}
                  />
                </div>
              </div>

              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Touch templates</div>
                    <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                      Each touch has a goal, optional subject hint, writing cue, and reference template. Beacon will adapt these to the actual contact instead of copying them verbatim.
                    </p>
                  </div>
                  <button className="crm-button soft" type="button" onClick={handleAddTemplate} disabled={!outreachContent || outreachContent.step_templates.length >= 20}>
                    <Plus size={15} />
                    Add step template
                  </button>
                </div>

                {extraTemplateCount > 0 && (
                  <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", borderRadius: 12, background: "#fff8ea", border: "1px solid #f3ddb0", color: "#a26a00" }}>
                    <AlertTriangle size={18} />
                    <span>
                      You have {extraTemplateCount} template{extraTemplateCount === 1 ? "" : "s"} beyond the current sequence timing. Beacon will only use them after you add more touches in timing settings.
                    </span>
                  </div>
                )}

                <div style={{ display: "grid", gap: 14 }}>
                  {(outreachContent?.step_templates ?? []).map((template, index) => (
                    <div key={template.step_number} className="crm-panel" style={{ padding: 16, borderRadius: 14, boxShadow: "none", display: "grid", gap: 12 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                          <span className="crm-chip" style={{ background: "#eef2ff", color: "#4958d8", borderColor: "#d8def8" }}>
                            Step {index + 1}
                          </span>
                          {outreachTimingSteps[index] && (
                            <span className="crm-chip" style={{ background: "#f7f8fc", color: "#5b6685", borderColor: "#e3e9f2" }}>
                              {outreachTimingSteps[index].channel === "linkedin" ? "LinkedIn" : outreachTimingSteps[index].channel === "call" ? "Call" : "Email"} · Day {outreachTimingSteps[index].day}
                            </span>
                          )}
                        </div>
                        <button className="crm-button soft" type="button" onClick={() => handleRemoveTemplate(index)} disabled={(outreachContent?.step_templates.length ?? 0) <= 1} style={{ color: "#b42336", borderColor: "#f0c1c8", background: "#fff" }}>
                          <Trash2 size={15} />
                          Remove
                        </button>
                      </div>

                      <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 0.55fr) minmax(0, 1fr)", gap: 14 }}>
                        <div>
                          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                            Channel
                          </div>
                          <select
                            value={template.channel}
                            onChange={(event) => updateTemplate(index, "channel", event.target.value)}
                            disabled={!outreachContent}
                            style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                          >
                            <option value="email">Email</option>
                            <option value="call">Call</option>
                            <option value="linkedin">LinkedIn</option>
                          </select>
                        </div>
                        <div>
                          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                            Template label
                          </div>
                          <input
                            value={template.label}
                            onChange={(event) => updateTemplate(index, "label", event.target.value)}
                            disabled={!outreachContent}
                            placeholder="Initial email"
                            style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                          />
                        </div>
                        <div>
                          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                            Goal
                          </div>
                          <input
                            value={template.goal}
                            onChange={(event) => updateTemplate(index, "goal", event.target.value)}
                            disabled={!outreachContent}
                            placeholder="What should this touch accomplish?"
                            style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                          />
                        </div>
                      </div>

                      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 0.95fr) minmax(0, 1.05fr)", gap: 14 }}>
                        <div>
                          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                            Subject hint
                          </div>
                          <input
                            value={template.subject_hint ?? ""}
                            onChange={(event) => updateTemplate(index, "subject_hint", event.target.value)}
                            disabled={!outreachContent}
                            placeholder="Quick question about {{company_name}}"
                            style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                          />
                        </div>
                        <div>
                          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                            Prompt hint
                          </div>
                          <input
                            value={template.prompt_hint ?? ""}
                            onChange={(event) => updateTemplate(index, "prompt_hint", event.target.value)}
                            disabled={!outreachContent}
                            placeholder="Tell Beacon how this touch should feel."
                            style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                          />
                        </div>
                      </div>

                      <div>
                        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                          Reference template
                        </div>
                        <textarea
                          value={template.body_template ?? ""}
                          onChange={(event) => updateTemplate(index, "body_template", event.target.value)}
                          disabled={!outreachContent}
                          rows={6}
                          placeholder="Use placeholders like {{first_name}} and {{company_name}} if you want to give Beacon a reusable pattern."
                          style={{ width: "100%", resize: "vertical", minHeight: 150 }}
                        />
                        <p className="crm-muted" style={{ marginTop: 6, fontSize: 12 }}>
                          Supported placeholders include <strong>{"{{first_name}}"}</strong> and <strong>{"{{company_name}}"}</strong>. Beacon treats this as a reference pattern, not a hard-coded script.
                        </p>
                      </div>
                    </div>
                  ))}
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <p className="crm-muted" style={{ fontSize: 12 }}>
                    These settings apply to new outreach generation and regeneration. Existing launched sequences keep their current copy.
                  </p>
                  <button className="crm-button primary" type="button" onClick={handleSaveOutreach} disabled={savingOutreach || !outreachContent}>
                    {savingOutreach ? <RefreshCw size={15} className="animate-spin" /> : <Sparkles size={15} />}
                    Save outreach settings
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : activeTab === "permissions" ? (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#f4efff", color: "#6f46d9", borderColor: "#e4d8ff" }}>
                  <Users size={14} />
                  Permissions
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Role permissions</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Admins always keep full access. Use these switches to decide what <strong>AEs</strong> and <strong>SDRs</strong> can do in Beacon without making them admins.
                </p>
              </div>
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              {([
                {
                  key: "crm_import" as const,
                  label: "Import from CRM",
                  help: "Lets this role replace the deal pipeline from the ClickUp Sales CRM board.",
                },
                {
                  key: "prospect_migration" as const,
                  label: "Migrate prospects",
                  help: "Lets this role upload and migrate prospect spreadsheets or CSV files.",
                },
                {
                  key: "manage_team" as const,
                  label: "Manage team roles",
                  help: "Lets this role change teammate roles and activation status from Team Management.",
                },
                {
                  key: "run_pre_meeting_intel" as const,
                  label: "Run pre-meeting intel",
                  help: "Lets this role trigger meeting research, pre-briefs, and demo strategy generation manually.",
                },
                {
                  key: "manage_reports" as const,
                  label: "Manage call reports",
                  help: "Lets this role preview, email, and update call-report schedules without granting full admin access.",
                },
              ]).map((permission) => (
                <div key={permission.key} style={{ border: "1px solid #e3e9f2", borderRadius: 14, overflow: "hidden", background: "#fff" }}>
                  <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 1.25fr) repeat(auto-fit, minmax(150px, 1fr))", gap: 14, padding: 16, alignItems: "center" }}>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 6 }}>{permission.label}</div>
                      <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>{permission.help}</div>
                    </div>
                    {(["ae", "sdr", "marketing"] as const).map((role) => (
                      <label key={role} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, border: "1px solid #e3e9f2", borderRadius: 10, padding: "10px 12px", background: "#fbfdff" }}>
                        <div>
                          <div style={{ fontSize: 11, fontWeight: 700, color: "#142335", textTransform: "uppercase", letterSpacing: "0.08em" }}>{role}</div>
                          <div className="crm-muted" style={{ fontSize: 12 }}>{rolePermissions?.[role]?.[permission.key] ? "Allowed" : "Blocked"}</div>
                        </div>
                        <input
                          type="checkbox"
                          checked={Boolean(rolePermissions?.[role]?.[permission.key])}
                          onChange={(event) => updateRolePermission(role, permission.key, event.target.checked)}
                          disabled={!isAdmin || !rolePermissions}
                        />
                      </label>
                    ))}
                  </div>
                </div>
              ))}

              {isAdmin ? (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <p className="crm-muted" style={{ fontSize: 12 }}>
                    Admins always keep full access. These switches only control what AEs and SDRs can do.
                  </p>
                  <button className="crm-button primary" type="button" onClick={handleSavePermissions} disabled={savingPermissions || !rolePermissions}>
                    {savingPermissions ? <RefreshCw size={15} className="animate-spin" /> : <Users size={15} />}
                    Save role permissions
                  </button>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>
                  Only admins can change workspace permissions. Everyone else can review the current guardrails here.
                </p>
              )}
            </div>

            {isAdmin && (
              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 14 }}>
                <div>
                  <h3 style={{ fontSize: 13, fontWeight: 700, color: "#142335", margin: 0 }}>Prospect visibility</h3>
                  <p className="crm-muted" style={{ maxWidth: 760, fontSize: 13, lineHeight: 1.7, marginTop: 6 }}>
                    By default everyone sees only <strong>their own</strong> prospects plus <strong>unassigned</strong> ones; admins see all.
                    Turn someone on here to let them see <strong>every</strong> prospect.
                  </p>
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  {allUsers.filter((u) => u.role !== "admin" && u.role !== "superadmin").map((u) => {
                    const on = prospectViewAll.includes(u.id);
                    return (
                      <label key={u.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, border: "1px solid #e3e9f2", borderRadius: 10, padding: "8px 12px", background: "#fbfdff" }}>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>{u.name || u.email || u.id}</div>
                          <div className="crm-muted" style={{ fontSize: 12 }}>
                            {(u.role || "").toUpperCase()} · {on ? "Sees all prospects" : "Own + unassigned"}
                          </div>
                        </div>
                        <input
                          type="checkbox"
                          checked={on}
                          disabled={savingProspectVis}
                          onChange={(event) => void toggleProspectViewAll(u.id, event.target.checked)}
                        />
                      </label>
                    );
                  })}
                  {allUsers.filter((u) => u.role !== "admin" && u.role !== "superadmin").length === 0 && (
                    <p className="crm-muted" style={{ fontSize: 12 }}>No non-admin teammates to configure yet.</p>
                  )}
                </div>
              </div>
            )}

            {isAdmin && (
              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 14 }}>
                <div>
                  <h3 style={{ fontSize: 13, fontWeight: 700, color: "#142335", margin: 0 }}>Pipeline visibility</h3>
                  <p className="crm-muted" style={{ maxWidth: 760, fontSize: 13, lineHeight: 1.7, marginTop: 6 }}>
                    Every active teammate can see every live deal in the pipeline. This is workspace-wide, so there is no individual access toggle to maintain.
                  </p>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, border: "1px solid #cdeedc", borderRadius: 10, padding: "10px 12px", background: "#eefaf2", color: "#1f8f5f", fontSize: 12, fontWeight: 800 }}>
                  <CheckCircle2 size={15} /> Workspace-wide access enabled
                </div>
              </div>
            )}

            {isAdmin && (
              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 14 }}>
                <div>
                  <h3 style={{ fontSize: 13, fontWeight: 700, color: "#142335", margin: 0 }}>Sales Analytics roster</h3>
                  <p className="crm-muted" style={{ maxWidth: 760, fontSize: 13, lineHeight: 1.7, marginTop: 6 }}>
                    AEs and SDRs appear automatically. Turn someone on here when they should also show in Sales Analytics activity rows and drilldowns.
                  </p>
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  {allUsers.map((u) => {
                    const role = (u.role || "").toLowerCase();
                    const autoIncluded = role === "ae" || role === "sdr";
                    const explicitlyIncluded = Boolean(salesAnalyticsRoster?.user_ids?.includes(u.id));
                    const on = autoIncluded || explicitlyIncluded;
                    return (
                      <label key={u.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, border: "1px solid #e3e9f2", borderRadius: 10, padding: "8px 12px", background: "#fbfdff" }}>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>{u.name || u.email || u.id}</div>
                          <div className="crm-muted" style={{ fontSize: 12 }}>
                            {(u.role || "").toUpperCase()} · {autoIncluded ? "Included by role" : on ? "Included in Sales Analytics" : "Hidden from Sales Analytics"}
                          </div>
                        </div>
                        <input
                          type="checkbox"
                          checked={on}
                          disabled={autoIncluded || savingSalesAnalyticsRoster || !salesAnalyticsRoster}
                          onChange={(event) => void toggleSalesAnalyticsRoster(u.id, event.target.checked)}
                        />
                      </label>
                    );
                  })}
                  {allUsers.length === 0 && (
                    <p className="crm-muted" style={{ fontSize: 12 }}>No teammates to configure yet.</p>
                  )}
                </div>
              </div>
            )}
          </div>
        ) : activeTab === "reports" ? (
          <div style={{ display: "grid", gap: 14 }}>
            <div>
              <div className="crm-chip" style={{ marginBottom: 12, background: "#eef8ff", color: "#145d97", borderColor: "#d7ebfb" }}>
                <CalendarDays size={14} />
                Reports
              </div>
              <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Call-report workspace</h3>
              <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                Preview a period, set each pod’s schedule, and keep non-production delivery safely restricted. Sunday is disabled by default for both schedules.
              </p>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 10 }}>
              {[
                { label: "US schedule", value: salesReportSettings?.enabled ? "On" : "Off", help: `${salesReportSettings?.send_hour ?? 7}:${String(salesReportSettings?.send_minute ?? 0).padStart(2, "0")} ${salesReportSettings?.send_timezone ?? "Asia/Kolkata"}` },
                { label: "India schedule", value: indiaSalesReportSettings?.enabled ? "On" : "Off", help: `${(indiaSalesReportSettings?.send_days ?? []).length || 0} send days selected` },
                { label: "Report access", value: canManageReports ? "Can manage" : "View only", help: canManageReports ? "Preview, delivery, and schedule controls are available." : "An admin can grant access in Permissions." },
              ].map((item) => (
                <div key={item.label} style={{ border: "1px solid #dce7f3", borderRadius: 12, background: "#fbfdff", padding: "12px 14px" }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "#68788d", textTransform: "uppercase", letterSpacing: "0.07em" }}>{item.label}</div>
                  <div style={{ fontSize: 16, fontWeight: 800, color: item.value === "Off" || item.value === "View only" ? "#9a5c10" : "#16744b", margin: "4px 0" }}>{item.value}</div>
                  <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.45 }}>{item.help}</div>
                </div>
              ))}
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              <div>
                <h4 style={{ fontSize: 13, fontWeight: 700, color: "#142335", margin: 0 }}>Run a historical US pod report</h4>
                <p className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6, margin: "4px 0 0" }}>
                  Build a preview before emailing it. Month-to-date ends on the selected business date; the prior-period preset covers the three complete months immediately before it.
                </p>
              </div>

              <div className="report-settings-grid-three">
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Report period</span>
                  <select value={reportRunType} onChange={(event) => { setReportRunType(event.target.value as typeof reportRunType); setReportPreview(null); }} disabled={!canManageReports} style={{ height: 36, padding: "0 10px", fontSize: 13 }}>
                    <option value="month_to_date">Month to date</option>
                    <option value="prior_quarter">Prior 3 full months</option>
                    <option value="custom">Custom date range</option>
                  </select>
                </label>
                {reportRunType === "custom" ? (
                  <>
                    <label style={{ display: "grid", gap: 8 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Start date</span>
                      <input type="date" value={customReportStart} onChange={(event) => { setCustomReportStart(event.target.value); setReportPreview(null); }} disabled={!canManageReports} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                    </label>
                    <label style={{ display: "grid", gap: 8 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>End date</span>
                      <input type="date" value={customReportEnd} onChange={(event) => { setCustomReportEnd(event.target.value); setReportPreview(null); }} disabled={!canManageReports} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                    </label>
                  </>
                ) : (
                  <label style={{ display: "grid", gap: 8 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>As-of business date</span>
                    <input type="date" value={reportAsOfDate} onChange={(event) => { setReportAsOfDate(event.target.value); setReportPreview(null); }} disabled={!canManageReports} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                    <span className="crm-muted" style={{ fontSize: 12 }}>Used to resolve the selected period.</span>
                  </label>
                )}
              </div>

              <div className="report-settings-grid-two">
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Email one recipient</span>
                  <input type="email" value={reportRecipient} onChange={(event) => setReportRecipient(event.target.value)} disabled={!canManageReports} placeholder="name@beacon.li" style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>In staging, delivery remains limited to the non-production recipient allowlist.</span>
                </label>
                <div style={{ display: "flex", alignItems: "end", gap: 10, flexWrap: "wrap" }}>
                  <button className="crm-button soft" type="button" onClick={handlePreviewPeriodReport} disabled={!canManageReports || loadingReportPreview}>
                    {loadingReportPreview ? <RefreshCw size={15} className="animate-spin" /> : <CalendarDays size={15} />}
                    Preview report
                  </button>
                  <button className="crm-button primary" type="button" onClick={handleSendPeriodReport} disabled={!canManageReports || sendingPeriodReport}>
                    {sendingPeriodReport ? <RefreshCw size={15} className="animate-spin" /> : <Mail size={15} />}
                    Email report
                  </button>
                </div>
              </div>

              {reportPreview && (
                <div style={{ border: "1px solid #d8e7f8", borderRadius: 12, padding: 14, background: "#f7fbff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>{reportPreview.subject}</div>
                  <div className="crm-muted" style={{ fontSize: 12 }}>{reportPreview.period_start} through {reportPreview.period_end} · {reportPreview.rows.length} reps</div>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", minWidth: 420, borderCollapse: "collapse", fontSize: 12 }}>
                      <thead><tr style={{ textAlign: "left", color: "#68788d" }}><th style={{ padding: "6px 0" }}>Rep</th><th>Calls</th><th>Connected</th><th>Meetings</th></tr></thead>
                      <tbody>{reportPreview.rows.map((row) => <tr key={row.rep_name} style={{ borderTop: "1px solid #e3e9f2", color: "#142335" }}><td style={{ padding: "7px 0", fontWeight: 700 }}>{row.rep_name}</td><td>{row.calls}</td><td>{row.connected_calls}</td><td>{row.meetings_booked_calls}</td></tr>)}</tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              <h4 style={{ fontSize: 13, fontWeight: 700, color: "#142335", margin: 0 }}>US pod report</h4>
              <div style={{ border: "1px solid #d8e7f8", borderRadius: 14, padding: 16, background: "#f7fbff", color: "#34516d", fontSize: 13, lineHeight: 1.7 }}>
                The normal setup is <strong>7:00 AM Asia/Kolkata</strong> with a <strong>6:00 AM Asia/Kolkata</strong> cutoff. That means the report is sent after the US team's working day ends. In staging, scheduled and test sends are restricted to the non-production allowlist below, so production recipients do not get test emails.
              </div>

              <label style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Enable scheduled call reports</div>
                  <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>When off, manual preview/send endpoints still work, but the scheduled report will skip.</div>
                </div>
                <input
                  type="checkbox"
                  checked={Boolean(salesReportSettings?.enabled)}
                  disabled={!canManageReports || !salesReportSettings}
                  onChange={(event) => updateSalesReportField("enabled", event.target.checked)}
                />
              </label>

              <div className="report-settings-grid-three">
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Send timezone</span>
                  <input value={salesReportSettings?.send_timezone ?? "Asia/Kolkata"} onChange={(e) => updateSalesReportField("send_timezone", e.target.value)} disabled={!canManageReports || !salesReportSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>IANA timezone used for the send clock, e.g. Asia/Kolkata or America/Chicago.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Send hour</span>
                  <input type="number" min={0} max={23} value={salesReportSettings?.send_hour ?? 7} onChange={(e) => updateSalesReportField("send_hour", Number(e.target.value))} disabled={!canManageReports || !salesReportSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>24-hour local hour in the send timezone. Use 7 for 7 AM.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Send minute</span>
                  <input type="number" min={0} max={59} value={salesReportSettings?.send_minute ?? 0} onChange={(e) => updateSalesReportField("send_minute", Number(e.target.value))} disabled={!canManageReports || !salesReportSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Minute after the hour. Use 0 for exactly 7:00.</span>
                </label>
              </div>

              <div className="report-settings-grid-three">
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Business-day cutoff timezone</span>
                  <input value={salesReportSettings?.cutoff_timezone ?? "Asia/Kolkata"} onChange={(e) => updateSalesReportField("cutoff_timezone", e.target.value)} disabled={!canManageReports || !salesReportSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Timezone used to decide which calls belong to a sales day.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Cutoff hour</span>
                  <input type="number" min={0} max={23} value={salesReportSettings?.cutoff_hour ?? 6} onChange={(e) => updateSalesReportField("cutoff_hour", Number(e.target.value))} disabled={!canManageReports || !salesReportSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Hour when the sales day ends. Use 6 for 6 AM IST.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Report label timezone</span>
                  <input value={salesReportSettings?.report_label_timezone ?? "America/Chicago"} onChange={(e) => updateSalesReportField("report_label_timezone", e.target.value)} disabled={!canManageReports || !salesReportSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Timezone used for the date printed in the report subject/title.</span>
                </label>
              </div>

              <ReportDaySelector
                selectedDays={salesReportSettings?.send_days ?? ["mon", "tue", "wed", "thu", "fri", "sat"]}
                disabled={!canManageReports || !salesReportSettings}
                onToggle={toggleSalesReportDay}
              />

              <div className="report-settings-grid-two">
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Weekly report day</span>
                  <select value={salesReportSettings?.weekly_report_day ?? "fri"} onChange={(e) => updateSalesReportField("weekly_report_day", e.target.value)} disabled={!canManageReports || !salesReportSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }}>
                    {["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map((day) => <option key={day} value={day}>{day.toUpperCase()}</option>)}
                  </select>
                  <span className="crm-muted" style={{ fontSize: 12 }}>On this send day, Beacon sends the weekly report instead of the daily report.</span>
                </label>
                <label style={{ display: "flex", gap: 10, alignItems: "center", border: "1px solid #e3e9f2", borderRadius: 14, padding: 14 }}>
                  <input type="checkbox" checked={Boolean(salesReportSettings?.skip_weekends)} onChange={(e) => updateSalesReportField("skip_weekends", e.target.checked)} disabled={!canManageReports || !salesReportSettings} />
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Skip weekend report periods</span>
                </label>
              </div>

              <label style={{ display: "grid", gap: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Production recipients</span>
                <textarea value={(salesReportSettings?.recipients ?? []).join(", ")} onChange={(e) => updateSalesReportList("recipients", e.target.value)} disabled={!canManageReports || !salesReportSettings} rows={3} style={{ padding: 12, resize: "vertical" }} />
                <span className="crm-muted" style={{ fontSize: 12 }}>Comma-separated emails for production scheduled reports. Staging does not send to this full list.</span>
              </label>

              <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fbfcff", display: "grid", gap: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Staging safety</div>
                <label style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <input type="checkbox" checked={Boolean(salesReportSettings?.nonprod_scheduled_enabled)} onChange={(e) => updateSalesReportField("nonprod_scheduled_enabled", e.target.checked)} disabled={!canManageReports || !salesReportSettings} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Allow scheduled sends in non-production. Recipients are still restricted to the allowlist below.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Non-production allowed recipients</span>
                  <textarea value={(salesReportSettings?.nonprod_recipients ?? []).join(", ")} onChange={(e) => updateSalesReportList("nonprod_recipients", e.target.value)} disabled={!canManageReports || !salesReportSettings} rows={2} style={{ padding: 12, resize: "vertical" }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Only these addresses can receive staging report emails. Keep this as your email while testing.</span>
                </label>
              </div>

              {salesReportSettings?.last_scheduled_send_at && (
                <div className="crm-muted" style={{ fontSize: 12 }}>
                  Last scheduled send: {new Date(salesReportSettings.last_scheduled_send_at).toLocaleString()} ({salesReportSettings.last_scheduled_send_key})
                </div>
              )}

              {canManageReports ? (
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                  <button className="crm-button soft" type="button" onClick={handleSendSalesReportTest} disabled={sendingSalesReportTest || !salesReportSettings}>
                    {sendingSalesReportTest ? <RefreshCw size={15} className="animate-spin" /> : <Mail size={15} />}
                    Send test report
                  </button>
                  <button className="crm-button primary" type="button" onClick={handleSaveSalesReportSettings} disabled={savingSalesReportSettings || !salesReportSettings}>
                    {savingSalesReportSettings ? <RefreshCw size={15} className="animate-spin" /> : <CalendarDays size={15} />}
                    Save report settings
                  </button>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>An admin can grant report management from Settings → Permissions.</p>
              )}
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, flexWrap: "wrap" }}>
                <div>
                  <h4 style={{ fontSize: 13, fontWeight: 700, color: "#142335", margin: 0 }}>India pod report</h4>
                  <p className="crm-muted" style={{ fontSize: 12, margin: "4px 0 0" }}>Configure the India pod daily report schedule independently.</p>
                </div>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 700, color: "#142335" }}>
                  <input
                    type="checkbox"
                    checked={Boolean(indiaSalesReportSettings?.enabled)}
                    disabled={!canManageReports || !indiaSalesReportSettings}
                    onChange={(event) => indiaSalesReportSettings && setIndiaSalesReportSettings({ ...indiaSalesReportSettings, enabled: event.target.checked })}
                  />
                  Scheduled reports enabled
                </label>
              </div>

              <ReportDaySelector
                selectedDays={indiaSalesReportSettings?.send_days ?? ["mon", "tue", "wed", "thu", "fri", "sat"]}
                disabled={!canManageReports || !indiaSalesReportSettings}
                onToggle={toggleIndiaSalesReportDay}
              />

              {canManageReports ? (
                <div style={{ display: "flex", justifyContent: "flex-end" }}>
                  <button
                    className="crm-button primary"
                    type="button"
                    onClick={handleSaveIndiaSalesReportSettings}
                    disabled={savingIndiaSalesReportSettings || !indiaSalesReportSettings}
                  >
                    {savingIndiaSalesReportSettings ? <RefreshCw size={15} className="animate-spin" /> : <CalendarDays size={15} />}
                    Save India schedule
                  </button>
                </div>
              ) : null}
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, flexWrap: "wrap" }}>
                <div>
                  <h4 style={{ fontSize: 13, fontWeight: 700, color: "#142335", margin: 0 }}>Weekly CRM digest</h4>
                  <p className="crm-muted" style={{ fontSize: 12, margin: "4px 0 0" }}>
                    One email every Monday summarizing the prior week's pipeline stage changes, accounts marked DND / Not a Fit / Reach Out Later, prospects marked DND, and new accounts added via Recent Imports. Every recipient gets the identical full digest — no per-rep filtering, no row limits.
                  </p>
                </div>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 700, color: "#142335" }}>
                  <input
                    type="checkbox"
                    checked={Boolean(weeklyDigestSettings?.enabled)}
                    disabled={!isAdmin || !weeklyDigestSettings}
                    onChange={(event) => updateWeeklyDigestField("enabled", event.target.checked)}
                  />
                  Scheduled digest enabled
                </label>
              </div>

              <div className="report-settings-grid-three">
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Send timezone</span>
                  <input value={weeklyDigestSettings?.send_timezone ?? "Asia/Kolkata"} onChange={(e) => updateWeeklyDigestField("send_timezone", e.target.value)} disabled={!isAdmin || !weeklyDigestSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>IANA timezone used for the Monday send clock.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Send hour</span>
                  <input type="number" min={0} max={23} value={weeklyDigestSettings?.send_hour ?? 9} onChange={(e) => updateWeeklyDigestField("send_hour", Number(e.target.value))} disabled={!isAdmin || !weeklyDigestSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>24-hour local hour in the send timezone. Use 9 for 9 AM.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Send minute</span>
                  <input type="number" min={0} max={59} value={weeklyDigestSettings?.send_minute ?? 0} onChange={(e) => updateWeeklyDigestField("send_minute", Number(e.target.value))} disabled={!isAdmin || !weeklyDigestSettings} style={{ height: 36, padding: "0 10px", fontSize: 13 }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Minute after the hour. Use 0 for exactly 9:00.</span>
                </label>
              </div>

              <ReportDaySelector
                selectedDays={weeklyDigestSettings?.send_days ?? ["mon"]}
                disabled={!isAdmin || !weeklyDigestSettings}
                onToggle={(day) => {
                  if (!weeklyDigestSettings) return;
                  const current = new Set(weeklyDigestSettings.send_days);
                  if (current.has(day)) current.delete(day);
                  else current.add(day);
                  const ordered = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].filter((item) => current.has(item));
                  setWeeklyDigestSettings({ ...weeklyDigestSettings, send_days: ordered });
                }}
              />

              <label style={{ display: "grid", gap: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Production recipients</span>
                <textarea value={(weeklyDigestSettings?.recipients ?? []).join(", ")} onChange={(e) => updateWeeklyDigestList("recipients", e.target.value)} disabled={!isAdmin || !weeklyDigestSettings} rows={3} style={{ padding: 12, resize: "vertical" }} />
                <span className="crm-muted" style={{ fontSize: 12 }}>Comma-separated emails. Every recipient receives the identical full digest.</span>
              </label>

              <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fbfcff", display: "grid", gap: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Staging safety</div>
                <label style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <input type="checkbox" checked={Boolean(weeklyDigestSettings?.nonprod_scheduled_enabled)} onChange={(e) => updateWeeklyDigestField("nonprod_scheduled_enabled", e.target.checked)} disabled={!isAdmin || !weeklyDigestSettings} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Allow scheduled sends in non-production. Recipients are still restricted to the allowlist below.</span>
                </label>
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Non-production allowed recipients</span>
                  <textarea value={(weeklyDigestSettings?.nonprod_recipients ?? []).join(", ")} onChange={(e) => updateWeeklyDigestList("nonprod_recipients", e.target.value)} disabled={!isAdmin || !weeklyDigestSettings} rows={2} style={{ padding: 12, resize: "vertical" }} />
                  <span className="crm-muted" style={{ fontSize: 12 }}>Only these addresses can receive staging digest emails. Keep this as your email while testing.</span>
                </label>
              </div>

              {weeklyDigestSettings?.last_scheduled_send_at && (
                <div className="crm-muted" style={{ fontSize: 12 }}>
                  Last scheduled send: {new Date(weeklyDigestSettings.last_scheduled_send_at).toLocaleString()} ({weeklyDigestSettings.last_scheduled_send_key})
                </div>
              )}

              {isAdmin ? (
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                  <button className="crm-button soft" type="button" onClick={handleSendWeeklyDigestTest} disabled={sendingWeeklyDigestTest || !weeklyDigestSettings}>
                    {sendingWeeklyDigestTest ? <RefreshCw size={15} className="animate-spin" /> : <Mail size={15} />}
                    Send test digest
                  </button>
                  <button className="crm-button primary" type="button" onClick={handleSaveWeeklyDigestSettings} disabled={savingWeeklyDigestSettings || !weeklyDigestSettings}>
                    {savingWeeklyDigestSettings ? <RefreshCw size={15} className="animate-spin" /> : <CalendarDays size={15} />}
                    Save digest settings
                  </button>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>Only admins can change digest settings.</p>
              )}
            </div>
          </div>
        ) : activeTab === "sync-schedule" ? (
          <div style={{ display: "grid", gap: 14 }}>
            <div>
              <div className="crm-chip" style={{ marginBottom: 12, background: "#fef3e2", color: "#9a5c10", borderColor: "#fcd9a8" }}>
                <Clock size={14} />
                Sync Schedule
              </div>
              <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Background sync configuration</h3>
              <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                Control how often Beacon runs background sync jobs — tl;dv meeting import, email ingestion, and deal health recalculation.
              </p>
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              {/* TLDV section */}
              <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>tl;dv Meeting Sync</div>

              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 14 }}>
                <label style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Enable tl;dv sync</div>
                    <input
                      type="checkbox"
                      checked={Boolean(syncSchedule?.tldv_sync_enabled)}
                      onChange={(e) => updateSyncField("tldv_sync_enabled", e.target.checked)}
                      disabled={!isAdmin || !syncSchedule}
                    />
                  </div>
                  <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    When disabled, the tl;dv sync task will skip execution entirely.
                  </div>
                  {syncSchedule?.tldv_last_synced_at ? (
                    <div style={{ fontSize: 12, color: "#1f8f5f", background: "#e8f8f0", borderRadius: 8, padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: 5 }}>
                      Last synced: {new Date(syncSchedule.tldv_last_synced_at).toLocaleString()}
                    </div>
                  ) : (
                    <div style={{ fontSize: 12, color: "#7f8fa5" }}>No sync run yet</div>
                  )}
                </label>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Sync interval (minutes)</div>
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={syncSchedule?.tldv_sync_interval_minutes ?? 5}
                    onChange={(e) => updateSyncField("tldv_sync_interval_minutes", Number(e.target.value))}
                    disabled={!isAdmin || !syncSchedule}
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                  <div className="crm-muted" style={{ fontSize: 12 }}>How often to check for new meetings (1–60 min). Default: <strong>5</strong>. Only new meetings since the last run are fetched — very low API cost.</div>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 14 }}>
                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Page size</div>
                  <input
                    type="number"
                    min={5}
                    max={50}
                    value={syncSchedule?.tldv_page_size ?? 10}
                    onChange={(e) => updateSyncField("tldv_page_size", Number(e.target.value))}
                    disabled={!isAdmin || !syncSchedule}
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                  <div className="crm-muted" style={{ fontSize: 12 }}>Meetings per API page (5–50). Default: <strong>10</strong>. With incremental sync, 1–2 pages is enough per run.</div>
                </div>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Max pages per run</div>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={syncSchedule?.tldv_max_pages ?? 2}
                    onChange={(e) => updateSyncField("tldv_max_pages", Number(e.target.value))}
                    disabled={!isAdmin || !syncSchedule}
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                  <div className="crm-muted" style={{ fontSize: 12 }}>Max pages to fetch per run (1–10). Default: <strong>2</strong>. Incremental runs stop early when they reach already-synced meetings.</div>
                </div>
              </div>

              {/* Divider */}
              <hr style={{ border: "none", borderTop: "1px solid #e3e9f2", margin: "4px 0" }} />

              {/* Other sync settings */}
              <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Other Sync Jobs</div>

              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 14 }}>
                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Email sync interval (seconds)</div>
                  <input
                    type="number"
                    min={60}
                    max={3600}
                    value={syncSchedule?.email_sync_interval_seconds ?? 180}
                    onChange={(e) => updateSyncField("email_sync_interval_seconds", Number(e.target.value))}
                    disabled={!isAdmin || !syncSchedule}
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                  <div className="crm-muted" style={{ fontSize: 12 }}>How often Beacon checks for new emails (60–3600s). Default: <strong>180</strong></div>
                </div>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Deal health hour (UTC)</div>
                  <input
                    type="number"
                    min={0}
                    max={23}
                    value={syncSchedule?.deal_health_hour ?? 2}
                    onChange={(e) => updateSyncField("deal_health_hour", Number(e.target.value))}
                    disabled={!isAdmin || !syncSchedule}
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                  <div className="crm-muted" style={{ fontSize: 12 }}>Hour of the day (0–23 UTC) for deal health recalc. Default: <strong>2</strong></div>
                </div>
              </div>

              {/* Actions */}
              {isAdmin ? (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <div style={{ display: "inline-flex", gap: 10, flexWrap: "wrap" }}>
                    <button className="crm-button soft" type="button" onClick={handleTriggerTldvSync} disabled={triggeringTldv || stoppingTldv}>
                      {triggeringTldv ? <RefreshCw size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                      Sync tl;dv now
                    </button>
                    <button className="crm-button soft" type="button" onClick={handleStopTldvSync} disabled={stoppingTldv || triggeringTldv}>
                      {stoppingTldv ? <RefreshCw size={15} className="animate-spin" /> : <AlertTriangle size={15} />}
                      Stop tl;dv sync
                    </button>
                  </div>
                  <button className="crm-button primary" type="button" onClick={handleSaveSyncSchedule} disabled={savingSyncSchedule || !syncSchedule}>
                    {savingSyncSchedule ? <RefreshCw size={15} className="animate-spin" /> : <Clock size={15} />}
                    Save sync schedule
                  </button>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>
                  Only admins can change sync schedule settings.
                </p>
              )}
            </div>
          </div>
        ) : activeTab === "zippy" ? (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#f3f0ff", color: "#6b3fc7", borderColor: "#e0d4f8" }}>
                  <Bot size={14} />
                  Zippy AI Assistant
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Your knowledge-powered copilot</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Zippy answers questions from your Drive files, generates MOMs, NDAs, ROI analyses, PoC documents, proposals, and LinkedIn drafts.
                  Connect your personal Gmail below to give Zippy access to your Drive files for RAG (retrieval-augmented generation).
                </p>
              </div>
              <div style={{ padding: "6px 14px", borderRadius: 999, background: "#f3f0ff", color: "#6b3fc7", fontSize: 12, fontWeight: 700, minWidth: 120, textAlign: "center" }}>
                {personalEmail?.connected ? "Connected" : "Setup needed"}
              </div>
            </div>

            {/* ── Personal email connection ──── */}
            <section className="crm-panel" style={{ padding: 18, display: "grid", gap: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
                <div>
                  <div className="crm-chip" style={{ marginBottom: 10, background: "#fff7ed", color: "#c2410c", borderColor: "#fed7aa" }}>
                    <Mail size={13} />
                    Drive Access
                  </div>
                  <h3 style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 4 }}>
                    Connect your Gmail to enable Drive
                  </h3>
                  <p className="crm-muted" style={{ maxWidth: 720, lineHeight: 1.6, fontSize: 13 }}>
                    Zippy reads your selected Drive folder to answer questions. Connecting Gmail grants <code>drive.readonly</code> + <code>drive.file</code> scopes so Zippy can search your files and upload generated documents.
                  </p>
                </div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-start" }}>
                  {personalEmail?.connected ? (
                    <>
                      <button className="crm-button soft" onClick={handleDisconnectPersonalEmail} disabled={disconnectingPersonal} style={{ color: "#b42336", borderColor: "#f0c1c8", background: "#fff" }}>
                        {disconnectingPersonal ? <RefreshCw size={15} className="animate-spin" /> : <Unplug size={15} />}
                        Disconnect
                      </button>
                      <button className="crm-button soft" onClick={handleSyncPersonalNow} disabled={syncingPersonal}>
                        {syncingPersonal ? <RefreshCw size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                        Sync now
                      </button>
                    </>
                  ) : (
                    <button className="crm-button primary" onClick={handleConnectPersonalEmail} disabled={connectingPersonal}>
                      {connectingPersonal ? <RefreshCw size={15} className="animate-spin" /> : <Link2 size={15} />}
                      Connect my Gmail
                    </button>
                  )}
                </div>
              </div>

              {personalEmail?.connected && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
                  <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                    <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                      Connected email
                    </div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>
                      {personalEmail?.email_address || "—"}
                    </div>
                  </div>
                  <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                    <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                      Last synced
                    </div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>
                      {formatTimestamp(personalEmail?.last_sync_epoch)}
                    </div>
                  </div>
                  <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                    <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                      Calendar scope
                    </div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: personalEmail?.has_calendar_scope ? "#217a49" : "#a26a00" }}>
                      {personalEmail?.has_calendar_scope ? "Granted" : "Missing"}
                    </div>
                  </div>
                  <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                    <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                      Drive scope
                    </div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: personalEmail?.has_drive_scope ? "#217a49" : "#a26a00" }}>
                      {personalEmail?.has_drive_scope ? "Granted" : "Missing"}
                    </div>
                  </div>
                  <div style={{ border: "1px solid #e3e9f2", borderRadius: 12, padding: 16, background: "#f8faff" }}>
                    <div style={{ fontSize: 11, color: "#68788d", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                      Send scope
                    </div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: personalEmail?.has_send_scope ? "#217a49" : "#a26a00" }}>
                      {personalEmail?.has_send_scope ? "Granted" : "Missing"}
                    </div>
                  </div>
                </div>
              )}

              {personalEmail?.connected && personalEmail.has_send_scope === false && (
                <div style={{ marginTop: 12, padding: "12px 14px", borderRadius: 10, background: "#fff4e6", border: "1px solid #f5d199", color: "#7a4b00", fontSize: 13 }}>
                  <strong>Reply from CRM is disabled.</strong> Your Gmail connection is missing the send permission. Reconnect Gmail above to unlock the "Reply" button on deal email threads.
                </div>
              )}

              {!personalEmail?.connected && (
                <div style={{ padding: "12px 14px", borderRadius: 10, background: "#f0f6ff", border: "1px solid #c8daf8", color: "#1a4fa8", fontSize: 13 }}>
                  <strong>No Gmail connected yet.</strong> Click "Connect my Gmail" above to grant Zippy access to your Drive. You can also connect from <button onClick={() => setActiveTab("email-sync")} style={{ background: "none", border: "none", color: "#1a4fa8", fontWeight: 700, textDecoration: "underline", cursor: "pointer", padding: 0, fontSize: 13 }}>Email Sync settings</button>.
                </div>
              )}
            </section>

            {/* ── Knowledge Source (Drive folder picker + Zippy index) ──── */}
            <KnowledgeSourcePanel
              isAdmin={isAdmin}
              connected={!!personalEmail?.connected}
              driveLoading={driveLoading}
              userFolder={userDriveFolder}
              adminFolder={adminDriveFolder}
              driveMessage={driveMessage}
              onOpenPicker={(scope) => setDrivePickerMode(scope)}
              onClearUser={handleClearUserFolder}
            />
          </>
        ) : activeTab === "notifications" ? (
          <section style={{ display: "grid", gap: 14 }}>
            <div>
              <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Mobile call notifications</h3>
              <p className="crm-muted" style={{ maxWidth: 720, lineHeight: 1.6 }}>
                When you click <strong>Call</strong> on a prospect from your desktop, Beacon can push a notification to this device. Tapping the notification opens your phone's dialer with the prospect's number pre-filled — no copy-paste, no hunting for it.
              </p>
            </div>

            {/* On desktop the enable toggle just confuses people — the feature
                rings your PHONE, not the desktop. So on desktop we show a
                pointer to mobile and hide the actual toggle; the real
                enable/disable control is mobile-only below. */}
            <div className="desktop-only">
              <div className="crm-panel" style={{ padding: 16 }}>
                <p className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6, margin: 0 }}>
                  📱 Call notifications ring your <strong>phone</strong>. Open Beacon on your mobile (add it to the Home Screen on iOS 16.4+ / Chrome on Android), then come to this screen there to enable them. There's nothing to turn on here on desktop.
                </p>
              </div>
            </div>

            {/* Status panel — what does this browser actually support / have?
                Mobile-only: the enable toggle belongs where the feature works. */}
            <div className="mobile-only">
            <div className="crm-panel" style={{ padding: 16, display: "grid", gap: 12 }}>
              {/* Per-device toggle row. The toggle itself is the source of
                  truth; the chips below explain what's blocking when it
                  can't enable (no SW support, server has no VAPID keys,
                  notification permission denied, etc.). */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>
                    {pushState?.subscribed ? "Notifications enabled on this device" : "Enable notifications on this device"}
                  </div>
                  <div className="crm-muted" style={{ fontSize: 12, marginTop: 4 }}>
                    {pushState
                      ? (pushState.supported
                          ? (pushState.configured
                              ? (pushState.permission === "denied"
                                  ? "Permission was denied. Re-enable notifications for this site in your browser settings, then try again."
                                  : (pushState.subscribed
                                      ? "Your desktop calls will ring this device."
                                      : "Tap to enable — your browser will ask for permission."))
                              : "Server hasn't been configured with VAPID keys yet. Ask an admin to set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY.")
                          : "This browser does not support Web Push. Try Chrome on Android or install the PWA on iOS 16.4+.")
                      : "Checking…"}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={pushBusy || !pushState?.supported || !pushState?.configured}
                  onClick={togglePushNotifications}
                  className={`crm-button ${pushState?.subscribed ? "soft" : "primary"}`}
                  style={{
                    minWidth: 120,
                    cursor: (pushBusy || !pushState?.supported || !pushState?.configured) ? "not-allowed" : "pointer",
                    opacity: (!pushState?.supported || !pushState?.configured) ? 0.55 : 1,
                  }}
                >
                  {pushBusy ? <Loader2 size={15} className="animate-spin" /> : <PhoneCall size={15} />}
                  {pushState?.subscribed ? "Disable" : "Enable"}
                </button>
                {pushState?.subscribed && (
                  <button
                    type="button"
                    disabled={pushTesting}
                    onClick={sendTestNotification}
                    className="crm-button soft"
                    style={{ minWidth: 120, cursor: pushTesting ? "not-allowed" : "pointer" }}
                  >
                    {pushTesting ? <Loader2 size={15} className="animate-spin" /> : <PhoneCall size={15} />}
                    Send test
                  </button>
                )}
              </div>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                <span className="crm-chip" style={{ background: pushState?.supported ? "#eaf8ef" : "#fbeaea", color: pushState?.supported ? "#1f7a47" : "#9b2226", borderColor: pushState?.supported ? "#cbe8d5" : "#f3c0c0" }}>
                  Browser support: {pushState?.supported ? "Yes" : "No"}
                </span>
                <span className="crm-chip" style={{ background: pushState?.configured ? "#eaf8ef" : "#fff4e0", color: pushState?.configured ? "#1f7a47" : "#925b00", borderColor: pushState?.configured ? "#cbe8d5" : "#f0d9a8" }}>
                  Server configured: {pushState?.configured ? "Yes" : "No"}
                </span>
                <span className="crm-chip" style={{ background: pushState?.permission === "granted" ? "#eaf8ef" : "#fbeaea", color: pushState?.permission === "granted" ? "#1f7a47" : "#9b2226", borderColor: pushState?.permission === "granted" ? "#cbe8d5" : "#f3c0c0" }}>
                  Permission: {pushState?.permission ?? "—"}
                </span>
              </div>

              <p className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6, margin: 0 }}>
                <strong>iOS users:</strong> Web Push only works once you've added Beacon to your Home Screen (Share → Add to Home Screen) and opened it from there. Regular Safari tabs can't receive push.
              </p>
            </div>
            </div>{/* end mobile-only */}
          </section>
        ) : activeTab === "zippy-prompt" ? (
          isAdmin ? (
            <div style={{ display: "grid", gap: 14 }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#f3eaff", color: "#5b2ea3", borderColor: "#e0d0fb" }}>
                  <Bot size={14} />
                  Zippy Prompt
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Global system prompt</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  This is the exact system prompt Zippy runs under for every conversation. Edits take effect on the next user turn — no redeploy needed.
                  Leave it blank and save to reset to the built-in default. <strong>Admin-only.</strong>
                </p>
              </div>

              <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 14 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                  <div style={{ fontSize: 13, color: "#5b6685" }}>
                    Status:{" "}
                    <strong style={{ color: zippyPromptIsDefault ? "#a26a00" : "#1f7a47" }}>
                      {zippyPromptIsDefault ? "Using built-in default" : "Custom override active"}
                    </strong>
                  </div>
                  <div style={{ fontSize: 12, color: "#7f8fa5" }}>
                    {zippyPrompt.length.toLocaleString()} chars
                  </div>
                </div>

                <textarea
                  value={zippyPrompt}
                  onChange={(e) => setZippyPrompt(e.target.value)}
                  disabled={zippyPromptLoading || savingZippyPrompt}
                  spellCheck={false}
                  style={{
                    width: "100%",
                    minHeight: 460,
                    padding: 14,
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                    fontSize: 13,
                    lineHeight: 1.55,
                    border: "1px solid #e3e9f2",
                    borderRadius: 12,
                    background: "#fafbfe",
                    color: "#142335",
                    resize: "vertical",
                  }}
                />

                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="crm-button primary"
                    onClick={handleSaveZippyPrompt}
                    disabled={savingZippyPrompt || zippyPromptLoading}
                  >
                    <CheckCircle2 size={15} />
                    {savingZippyPrompt ? "Saving…" : "Save prompt"}
                  </button>
                  <button
                    type="button"
                    className="crm-button soft"
                    onClick={handleResetZippyPrompt}
                    disabled={savingZippyPrompt || zippyPromptLoading || zippyPromptIsDefault}
                    title={zippyPromptIsDefault ? "Already on the default" : "Reset to built-in default"}
                  >
                    <RefreshCw size={15} />
                    Reset to default
                  </button>
                  <button
                    type="button"
                    className="crm-button soft"
                    onClick={loadZippyPrompt}
                    disabled={zippyPromptLoading || savingZippyPrompt}
                  >
                    Reload
                  </button>
                </div>

                <p className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                  Tip: Zippy loads this prompt once per user turn, so a change is live as soon as you click Save. The built-in default is the fallback when this field is empty — saving an empty prompt reverts to it.
                </p>
              </div>
            </div>
          ) : (
            <p className="crm-muted" style={{ fontSize: 12 }}>
              Admin access required to view or edit Zippy's system prompt.
            </p>
          )
        ) : activeTab === "pre-meeting" ? (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#eef8ff", color: "#145d97", borderColor: "#d7ebfb" }}>
                  <Shield size={14} />
                  Pre-Meeting
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Pre-meeting automation</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Beacon watches scheduled meetings already in the CRM. Before the meeting starts, it can generate missing research, build the prep page, and email the meeting intel link to the assigned team.
                </p>
              </div>
              <div className="crm-panel" style={{ padding: 16, borderRadius: 14, boxShadow: "none", minWidth: 300 }}>
                <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>
                  Delivery flow
                </div>
                <p className="crm-muted" style={{ fontSize: 13, lineHeight: 1.7, marginBottom: 0 }}>
                  Email includes the Beacon meeting prep page link and is sent to the deal owner plus linked AE / SDR teammates when Beacon finds them.
                </p>
              </div>
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) repeat(2, minmax(220px, 0.35fr))", gap: 16 }}>
                <label style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Enable automatic pre-meeting sends</div>
                    <input
                      type="checkbox"
                      checked={Boolean(preMeetingSettings?.enabled)}
                      onChange={(event) => updatePreMeetingField("enabled", event.target.checked)}
                      disabled={!isAdmin || !preMeetingSettings}
                    />
                  </div>
                  <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    When enabled, Beacon checks scheduled meetings in the background and sends prep intel automatically before the meeting.
                  </div>
                </label>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Send window</div>
                  <input
                    type="number"
                    min={1}
                    max={168}
                    value={preMeetingSettings?.send_hours_before ?? 12}
                    onChange={(event) => updatePreMeetingField("send_hours_before", Number(event.target.value))}
                    disabled={!isAdmin || !preMeetingSettings}
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                  <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    Default is <strong>12 hours</strong> before the scheduled meeting start. Use 1-168 hours.
                  </div>
                </div>

                <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Prep generation window</div>
                  <input
                    type="number"
                    min={1}
                    max={168}
                    value={preMeetingSettings?.generate_hours_before ?? 48}
                    onChange={(event) => updatePreMeetingField("generate_hours_before", Number(event.target.value))}
                    disabled={!isAdmin || !preMeetingSettings}
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                  <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    Generate missing prep earlier, then send at the send window. Must be at least the send window.
                  </div>
                </div>
              </div>

              <div style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>When to send</div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  {([
                    { key: "hours_before", label: "Hours before each meeting", desc: "Send each brief a set number of hours ahead of that meeting." },
                    { key: "daily_time", label: "Daily at a fixed time", desc: "Send all upcoming briefs once a day at a chosen time." },
                  ] as const).map((opt) => {
                    const active = (preMeetingSettings?.send_mode ?? "hours_before") === opt.key;
                    return (
                      <button
                        key={opt.key}
                        type="button"
                        disabled={!isAdmin || !preMeetingSettings}
                        onClick={() => updatePreMeetingField("send_mode", opt.key)}
                        style={{
                          flex: "1 1 240px", textAlign: "left", borderRadius: 12, padding: 14, display: "grid", gap: 6,
                          border: active ? "2px solid #9ace3d" : "1px solid #e3e9f2",
                          background: active ? "#f3fbe3" : "#fff",
                          cursor: isAdmin && preMeetingSettings ? "pointer" : "default",
                        }}
                      >
                        <span style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>{opt.label}</span>
                        <span className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>{opt.desc}</span>
                      </button>
                    );
                  })}
                </div>
                {preMeetingSettings?.send_mode === "daily_time" && (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 14, marginTop: 2 }}>
                    <div style={{ display: "grid", gap: 6 }}>
                      <label style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Send time</label>
                      <input
                        type="time"
                        value={preMeetingSettings?.send_time ?? "07:00"}
                        onChange={(event) => updatePreMeetingField("send_time", event.target.value)}
                        disabled={!isAdmin}
                        style={{ height: 36, padding: "0 12px", fontSize: 13 }}
                      />
                      <span className="crm-muted" style={{ fontSize: 12 }}>Briefs go out once a day at this time.</span>
                    </div>
                    <div style={{ display: "grid", gap: 6 }}>
                      <label style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>Timezone</label>
                      <select
                        value={preMeetingSettings?.timezone ?? "UTC"}
                        onChange={(event) => updatePreMeetingField("timezone", event.target.value)}
                        disabled={!isAdmin}
                        style={{ height: 36, padding: "0 12px", fontSize: 13 }}
                      >
                        {PRE_MEETING_TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                        {preMeetingSettings?.timezone && !PRE_MEETING_TIMEZONES.includes(preMeetingSettings.timezone) && (
                          <option value={preMeetingSettings.timezone}>{preMeetingSettings.timezone}</option>
                        )}
                      </select>
                      <span className="crm-muted" style={{ fontSize: 12 }}>Send time is interpreted in this timezone.</span>
                    </div>
                  </div>
                )}
                <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                  {preMeetingSettings?.send_mode === "daily_time"
                    ? "At the send time, Beacon emails briefs for meetings starting within the “Send window” hours above (it doubles as the daily look-ahead)."
                    : "Each brief is sent the “Send window” hours before its meeting starts."}
                </div>
              </div>

              <label style={{ border: "1px solid #e3e9f2", borderRadius: 14, padding: 16, background: "#fff", display: "grid", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#142335" }}>Generate missing research automatically</div>
                  <input
                    type="checkbox"
                    checked={Boolean(preMeetingSettings?.auto_generate_if_missing)}
                    onChange={(event) => updatePreMeetingField("auto_generate_if_missing", event.target.checked)}
                    disabled={!isAdmin || !preMeetingSettings}
                  />
                </div>
                <div className="crm-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                  If the account has no fresh meeting research yet, Beacon will run account research and demo-strategy generation before sending the prep email instead of waiting for a rep to do it manually.
                </div>
              </label>

              {isAdmin ? (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <p className="crm-muted" style={{ fontSize: 12 }}>
                    This automation runs off scheduled meeting records already in Beacon. Calendar ingestion can feed those records later without changing this workflow.
                  </p>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <button className="crm-button soft" type="button" onClick={handleRunPreMeetingNow} disabled={runningPreMeeting}>
                      {runningPreMeeting ? <RefreshCw size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                      Run now
                    </button>
                    <button className="crm-button primary" type="button" onClick={handleSavePreMeeting} disabled={savingPreMeeting || !preMeetingSettings}>
                      {savingPreMeeting ? <RefreshCw size={15} className="animate-spin" /> : <Shield size={15} />}
                      Save pre-meeting settings
                    </button>
                  </div>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>
                  Only admins can change pre-meeting automation. Everyone else can review the current timing and behavior here.
                </p>
              )}
            </div>
          </div>
        ) : activeTab === "trash" ? (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#f3fbe3", color: "#4d7c0f", borderColor: "#dcefbb" }}>
                  <Trash2 size={14} />
                  Trash
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Deleted accounts and deals</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Deleting an account or a deal only hides it — every contact, activity, and stage-history row survives so past
                  scorecards never rewrite. This is where those rows live, and Restore brings them back.
                </p>
              </div>
              <button className="crm-button soft" type="button" onClick={loadTrash} disabled={trashLoading}>
                <RefreshCw size={15} className={trashLoading ? "animate-spin" : undefined} />
                Refresh
              </button>
            </div>

            {trashError ? (
              <div style={{ border: "1px solid #f3c7cd", background: "#fdecec", color: "#b42336", borderRadius: 12, padding: "12px 14px", fontSize: 13 }}>{trashError}</div>
            ) : trashLoading && !trashCompanies && !trashDeals ? (
              <div className="crm-muted" style={{ padding: 16, fontSize: 13 }}>Loading trash…</div>
            ) : (
              <>
                {/* ── Companies ─────────────────────────────────────────── */}
                <div style={{ display: "grid", gap: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>
                    Accounts
                    {trashCompanies?.length ? <span style={{ color: "#68788d", fontWeight: 600 }}> · {trashCompanies.length}</span> : null}
                  </div>
                  {!trashCompanies || trashCompanies.length === 0 ? (
                    <div className="crm-muted" style={{ padding: "14px 16px", fontSize: 13, border: "1px solid #e3e9f2", borderRadius: 14, background: "#fff" }}>Trash is empty.</div>
                  ) : (
                    <div style={{ overflowX: "auto", border: "1px solid #e3e9f2", borderRadius: 14, background: "#fff" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                        <thead>
                          <tr style={{ textAlign: "left", color: "#68788d", background: "#f7f9fc" }}>
                            {["Account", "Prospects", "Deleted", ""].map((h, i) => (
                              <th key={h || `sp-${i}`} style={{ padding: "10px 14px", fontWeight: 700, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.07em", borderBottom: "1px solid #e3e9f2" }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {trashCompanies.map((c) => (
                            <tr key={c.id} style={{ borderTop: "1px solid #eef1f6" }}>
                              <td style={{ padding: "11px 14px" }}>
                                <div style={{ fontWeight: 700, color: "#25384d" }}>{c.name}</div>
                                {c.domain ? <div style={{ fontSize: 11, color: "#9fb0c0" }}>{c.domain}</div> : null}
                                {c.merged_into_name ? (
                                  <div style={{ fontSize: 11, color: "#4d7c0f", marginTop: 3, fontWeight: 600 }}>
                                    merged into {c.merged_into_name}
                                  </div>
                                ) : null}
                              </td>
                              <td style={{ padding: "11px 14px", color: "#5b6b7d", whiteSpace: "nowrap" }}>{c.prospect_count}</td>
                              <td style={{ padding: "11px 14px", color: "#5b6b7d", whiteSpace: "nowrap" }}>{fmtTrashDate(c.deleted_at)}</td>
                              <td style={{ padding: "11px 14px", textAlign: "right" }}>
                                <button
                                  type="button"
                                  className="crm-button soft"
                                  onClick={() => restoreCompany(c)}
                                  disabled={restoringId === c.id}
                                  style={{ fontSize: 12, padding: "5px 12px", whiteSpace: "nowrap" }}
                                >
                                  {restoringId === c.id ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                                  Restore
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* ── Deals ─────────────────────────────────────────────── */}
                <div style={{ display: "grid", gap: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#142335" }}>
                    Deals
                    {trashDeals?.length ? <span style={{ color: "#68788d", fontWeight: 600 }}> · {trashDeals.length}</span> : null}
                  </div>
                  {!trashDeals || trashDeals.length === 0 ? (
                    <div className="crm-muted" style={{ padding: "14px 16px", fontSize: 13, border: "1px solid #e3e9f2", borderRadius: 14, background: "#fff" }}>Trash is empty.</div>
                  ) : (
                    <div style={{ overflowX: "auto", border: "1px solid #e3e9f2", borderRadius: 14, background: "#fff" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                        <thead>
                          <tr style={{ textAlign: "left", color: "#68788d", background: "#f7f9fc" }}>
                            {["Deal", "Stage", "Value", "Deleted", ""].map((h, i) => (
                              <th key={h || `sp-${i}`} style={{ padding: "10px 14px", fontWeight: 700, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.07em", borderBottom: "1px solid #e3e9f2" }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {trashDeals.map((d) => (
                            <tr key={d.id} style={{ borderTop: "1px solid #eef1f6" }}>
                              <td style={{ padding: "11px 14px" }}>
                                <div style={{ fontWeight: 700, color: "#25384d" }}>{d.name}</div>
                                {d.company_name ? <div style={{ fontSize: 11, color: "#9fb0c0" }}>{d.company_name}</div> : null}
                              </td>
                              <td style={{ padding: "11px 14px", color: "#5b6b7d", whiteSpace: "nowrap" }}>{d.stage ?? "—"}</td>
                              <td style={{ padding: "11px 14px", color: "#5b6b7d", whiteSpace: "nowrap" }}>
                                {d.amount == null ? "—" : `$${Math.round(d.amount).toLocaleString()}`}
                              </td>
                              <td style={{ padding: "11px 14px", color: "#5b6b7d", whiteSpace: "nowrap" }}>{fmtTrashDate(d.deleted_at)}</td>
                              <td style={{ padding: "11px 14px", textAlign: "right" }}>
                                <button
                                  type="button"
                                  className="crm-button soft"
                                  onClick={() => restoreDeal(d)}
                                  disabled={restoringId === d.id}
                                  style={{ fontSize: 12, padding: "5px 12px", whiteSpace: "nowrap" }}
                                >
                                  {restoringId === d.id ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                                  Restore
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <p className="crm-muted" style={{ fontSize: 11, lineHeight: 1.7, maxWidth: 760 }}>
                  Restoring an account also restores the deals that were deleted with it. Tasks dismissed at delete time stay
                  dismissed — re-open the ones that still matter from the task center. Restoring a merged account does not
                  un-merge it: its records stay on the account it was merged into.
                </p>
              </>
            )}
          </div>
        ) : activeTab === "system-health" ? (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#eef5ff", color: "#175089", borderColor: "#d8e6fb" }}>
                  <RefreshCw size={14} />
                  System Health
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Scheduled jobs</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Last run and status for every scheduled background job. A red or amber badge means a job failed or hasn't run on time — catch a silent scheduler problem here before it affects reports, syncs, or reminders.
                </p>
              </div>
              <button className="crm-button soft" type="button" onClick={loadJobHealth} disabled={jobHealthLoading}>
                <RefreshCw size={15} className={jobHealthLoading ? "animate-spin" : undefined} />
                Refresh
              </button>
            </div>
            {jobHealthError ? (
              <div style={{ border: "1px solid #f3c7cd", background: "#fdecec", color: "#b42336", borderRadius: 12, padding: "12px 14px", fontSize: 13 }}>{jobHealthError}</div>
            ) : jobHealthLoading && !jobHealth ? (
              <div className="crm-muted" style={{ padding: 16, fontSize: 13 }}>Loading job health…</div>
            ) : !jobHealth || jobHealth.length === 0 ? (
              <div className="crm-muted" style={{ padding: 16, fontSize: 13 }}>No scheduled-job data recorded yet. Jobs appear here after their next run.</div>
            ) : (
              <div style={{ overflowX: "auto", border: "1px solid #e3e9f2", borderRadius: 14, background: "#fff" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "#68788d", background: "#f7f9fc" }}>
                      {["Job", "Schedule", "Last run", "Status", "Runs"].map((h) => (
                        <th key={h} style={{ padding: "10px 14px", fontWeight: 700, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.07em", borderBottom: "1px solid #e3e9f2" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {jobHealth.map((j) => {
                      const tone =
                        j.staleness === "failing" ? { bg: "#fdecec", fg: "#b42336", label: "Failing" }
                        : j.staleness === "stale" ? { bg: "#fff5e6", fg: "#a8650a", label: "Stale" }
                        // Idle reads as blue, not green: the job is healthy but
                        // producing nothing, which is a state someone should
                        // look at rather than scroll past.
                        : j.staleness === "idle" ? { bg: "#eef5ff", fg: "#175089", label: "Idle" }
                        : j.staleness === "ok" ? { bg: "#eafbf0", fg: "#1f8f5f", label: "OK" }
                        : { bg: "#eef1f6", fg: "#6b7794", label: "No data" };
                      const fmt = (v: string | null) =>
                        v ? new Date(v.endsWith("Z") ? v : `${v}Z`).toLocaleString() : "—";
                      const lastRun = fmt(j.last_run_at);
                      return (
                        <tr key={j.beat_name} style={{ borderTop: "1px solid #eef1f6" }}>
                          <td style={{ padding: "11px 14px" }}>
                            <div style={{ fontWeight: 700, color: "#25384d" }}>{j.beat_name}</div>
                            <div style={{ fontSize: 11, color: "#9fb0c0" }}>{j.task}</div>
                          </td>
                          <td style={{ padding: "11px 14px", color: "#5b6b7d", whiteSpace: "nowrap" }}>{j.schedule}</td>
                          <td style={{ padding: "11px 14px", color: "#5b6b7d", whiteSpace: "nowrap" }}>
                            {lastRun}
                            {/* "Last run" alone can't tell a working job from one
                                that runs every 3 minutes and does nothing, which
                                is how several integrations stayed dead for months
                                behind a green badge. Show when it last did work. */}
                            <div style={{ fontSize: 11, color: "#9fb0c0", marginTop: 2 }}>
                              did work: {fmt(j.last_effective_at)}
                            </div>
                          </td>
                          <td style={{ padding: "11px 14px" }}>
                            <span style={{ background: tone.bg, color: tone.fg, padding: "3px 9px", borderRadius: 999, fontWeight: 700, fontSize: 11 }}>{tone.label}</span>
                            {j.last_error ? <div style={{ fontSize: 11, color: "#b42336", marginTop: 4, maxWidth: 320, lineHeight: 1.4 }}>{j.last_error}</div> : null}
                            {!j.last_error && j.last_skip_reason ? (
                              <div style={{ fontSize: 11, color: "#175089", marginTop: 4, maxWidth: 320, lineHeight: 1.4 }}>
                                skipped: {j.last_skip_reason}
                              </div>
                            ) : null}
                          </td>
                          <td style={{ padding: "11px 14px", color: "#5b6b7d" }}>
                            {j.runs_total}
                            {j.failures_total > 0 ? <span style={{ color: "#b42336", fontWeight: 600 }}> · {j.failures_total} failed</span> : null}
                            {j.skips_total > 0 ? <span style={{ color: "#175089", fontWeight: 600 }}> · {j.skips_total} skipped</span> : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#eef5ff", color: "#175089", borderColor: "#d8e6fb" }}>
                  <GripVertical size={14} />
                  Pipeline
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Deal lanes</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Control the shared deal board lanes here. Admins can rename, reorder, add, or remove lanes, and the Pipeline page will use this exact layout.
                </p>
              </div>
              {isAdmin && (
                <button className="crm-button soft" type="button" onClick={addStage} disabled={!dealStages}>
                  <Plus size={15} />
                  Add lane
                </button>
              )}
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 8 }}>
              {(dealStages?.stages ?? []).map((stage, index) => (
                <div key={stage.id} style={{ border: "1px solid #e3e9f2", borderRadius: 10, padding: "5px 10px", background: "#fff", minHeight: 48, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ width: 18, textAlign: "center", fontSize: 11, fontWeight: 700, color: "#68788d", flexShrink: 0 }}>{index + 1}</span>
                  <input
                    type="color"
                    value={stage.color}
                    onChange={(event) => updateStage(index, "color", event.target.value)}
                    disabled={!isAdmin}
                    title={`Lane color ${stage.color}`}
                    aria-label={`Lane ${index + 1} color`}
                    style={{ width: 36, height: 32, border: "1px solid #e3e9f2", borderRadius: 8, background: "#fff", padding: 2, flexShrink: 0 }}
                  />
                  <input
                    value={stage.label}
                    onChange={(event) => updateStage(index, "label", event.target.value)}
                    disabled={!isAdmin}
                    placeholder="Lane name"
                    aria-label={`Lane ${index + 1} name`}
                    style={{ flex: "1 1 200px", minWidth: 160, height: 36, padding: "0 12px", fontSize: 13, fontWeight: 600 }}
                  />
                  <span title={`Stage id: ${stage.id}`} style={{ fontSize: 11, fontWeight: 600, color: "#68788d", maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{stage.id}</span>
                  <select
                    value={stage.group}
                    onChange={(event) => updateStage(index, "group", event.target.value)}
                    disabled={!isAdmin}
                    aria-label={`Lane ${index + 1} group`}
                    style={{ width: 110, height: 36, padding: "0 8px", fontSize: 13, flexShrink: 0 }}
                  >
                    <option value="active">Active</option>
                    <option value="closed">Closed</option>
                  </select>
                  {isAdmin && (
                    <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                      <button className="crm-button soft" type="button" title="Move up" aria-label={`Move lane ${index + 1} up`} onClick={() => moveStage(index, -1)} disabled={index === 0} style={{ minHeight: 32, padding: "0 9px" }}><ArrowUp size={14} /></button>
                      <button className="crm-button soft" type="button" title="Move down" aria-label={`Move lane ${index + 1} down`} onClick={() => moveStage(index, 1)} disabled={index === (dealStages?.stages.length ?? 0) - 1} style={{ minHeight: 32, padding: "0 9px" }}><ArrowDown size={14} /></button>
                      <button className="crm-button soft" type="button" title="Delete lane" aria-label={`Delete lane ${index + 1}`} onClick={() => removeStage(index)} disabled={(dealStages?.stages.length ?? 0) <= 1} style={{ minHeight: 32, padding: "0 9px", marginLeft: 6, color: "#b42336", borderColor: "#f0c1c8", background: "#fff" }}><Trash2 size={14} /></button>
                    </div>
                  )}
                </div>
              ))}

              {isAdmin ? (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <p className="crm-muted" style={{ fontSize: 12 }}>
                    These lanes define the shared deal board order and names across Beacon, including the ClickUp CRM import flow.
                  </p>
                  <button className="crm-button primary" type="button" onClick={handleSaveStages} disabled={savingStages || !dealStages}>
                    {savingStages ? <RefreshCw size={15} className="animate-spin" /> : <Shield size={15} />}
                    Save pipeline lanes
                  </button>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>
                  Only admins can update the shared deal lanes. Everyone else sees the same board layout in Pipeline.
                </p>
              )}
            </div>

            {/* ── Prospect lanes editor ── */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap", marginTop: 28 }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#f0fdf4", color: "#15803d", borderColor: "#bbf7d0" }}>
                  <Target size={14} />
                  Prospecting
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: "#142335", marginBottom: 4 }}>Prospect lanes</h3>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Control the shared prospect board lanes here. The Pipeline prospect tab will use this exact layout for sorting contacts into stages.
                </p>
              </div>
              {isAdmin && (
                <button className="crm-button soft" type="button" onClick={addProspectStage} disabled={!prospectStages}>
                  <Plus size={15} />
                  Add lane
                </button>
              )}
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 8 }}>
              {(prospectStages?.stages ?? []).map((stage, index) => (
                <div key={stage.id} style={{ border: "1px solid #e3e9f2", borderRadius: 10, padding: "5px 10px", background: "#fff", minHeight: 48, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ width: 18, textAlign: "center", fontSize: 11, fontWeight: 700, color: "#68788d", flexShrink: 0 }}>{index + 1}</span>
                  <input
                    type="color"
                    value={stage.color}
                    onChange={(event) => updateProspectStage(index, "color", event.target.value)}
                    disabled={!isAdmin}
                    title={`Lane color ${stage.color}`}
                    aria-label={`Prospect lane ${index + 1} color`}
                    style={{ width: 36, height: 32, border: "1px solid #e3e9f2", borderRadius: 8, background: "#fff", padding: 2, flexShrink: 0 }}
                  />
                  <input
                    value={stage.label}
                    onChange={(event) => updateProspectStage(index, "label", event.target.value)}
                    disabled={!isAdmin}
                    placeholder="Lane name"
                    aria-label={`Prospect lane ${index + 1} name`}
                    style={{ flex: "1 1 200px", minWidth: 160, height: 36, padding: "0 12px", fontSize: 13, fontWeight: 600 }}
                  />
                  <span title={`Stage id: ${stage.id}`} style={{ fontSize: 11, fontWeight: 600, color: "#68788d", maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{stage.id}</span>
                  <select
                    value={stage.group}
                    onChange={(event) => updateProspectStage(index, "group", event.target.value)}
                    disabled={!isAdmin}
                    aria-label={`Prospect lane ${index + 1} group`}
                    style={{ width: 110, height: 36, padding: "0 8px", fontSize: 13, flexShrink: 0 }}
                  >
                    <option value="active">Active</option>
                    <option value="closed">Closed</option>
                  </select>
                  {isAdmin && (
                    <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                      <button className="crm-button soft" type="button" title="Move up" aria-label={`Move prospect lane ${index + 1} up`} onClick={() => moveProspectStage(index, -1)} disabled={index === 0} style={{ minHeight: 32, padding: "0 9px" }}><ArrowUp size={14} /></button>
                      <button className="crm-button soft" type="button" title="Move down" aria-label={`Move prospect lane ${index + 1} down`} onClick={() => moveProspectStage(index, 1)} disabled={index === (prospectStages?.stages.length ?? 0) - 1} style={{ minHeight: 32, padding: "0 9px" }}><ArrowDown size={14} /></button>
                      <button className="crm-button soft" type="button" title="Delete lane" aria-label={`Delete prospect lane ${index + 1}`} onClick={() => removeProspectStage(index)} disabled={(prospectStages?.stages.length ?? 0) <= 1} style={{ minHeight: 32, padding: "0 9px", marginLeft: 6, color: "#b42336", borderColor: "#f0c1c8", background: "#fff" }}><Trash2 size={14} /></button>
                    </div>
                  )}
                </div>
              ))}

              {isAdmin ? (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <p className="crm-muted" style={{ fontSize: 12 }}>
                    These lanes define the shared prospect board order and names across Beacon.
                  </p>
                  <button className="crm-button primary" type="button" onClick={handleSaveProspectStages} disabled={savingProspectStages || !prospectStages}>
                    {savingProspectStages ? <RefreshCw size={15} className="animate-spin" /> : <Shield size={15} />}
                    Save prospect lanes
                  </button>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>
                  Only admins can update the shared prospect lanes. Everyone else sees the same board layout in Pipeline.
                </p>
              )}
            </div>

            <div className="crm-panel" style={{ padding: 18, borderRadius: 14, boxShadow: "none", display: "grid", gap: 16 }}>
              <div>
                <div className="crm-chip" style={{ marginBottom: 12, background: "#f7f8fc", color: "#5b6685", borderColor: "#e3e9f2" }}>
                  <Link2 size={14} />
                  ClickUp CRM import
                </div>
                <h4 style={{ fontSize: 13, fontWeight: 700, color: "#142335", marginBottom: 4 }}>ClickUp source IDs</h4>
                <p className="crm-muted" style={{ maxWidth: 760, lineHeight: 1.7 }}>
                  Beacon still uses the ClickUp API token from env, but admins can override the Sales CRM workspace IDs here instead of hardcoding them in deployment.
                </p>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 14 }}>
                <div>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>Team ID</div>
                  <input
                    value={clickupCrmSettings?.team_id ?? ""}
                    onChange={(event) => updateClickUpCrmField("team_id", event.target.value)}
                    disabled={!isAdmin}
                    placeholder="9016838025"
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                </div>
                <div>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>Space ID</div>
                  <input
                    value={clickupCrmSettings?.space_id ?? ""}
                    onChange={(event) => updateClickUpCrmField("space_id", event.target.value)}
                    disabled={!isAdmin}
                    placeholder="90166384157"
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                </div>
                <div>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#68788d", fontWeight: 700, marginBottom: 6 }}>Deals List ID</div>
                  <input
                    value={clickupCrmSettings?.deals_list_id ?? ""}
                    onChange={(event) => updateClickUpCrmField("deals_list_id", event.target.value)}
                    disabled={!isAdmin}
                    placeholder="901613645185"
                    style={{ width: "100%", height: 36, padding: "0 12px", fontSize: 13 }}
                  />
                </div>
              </div>

              {isAdmin ? (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <p className="crm-muted" style={{ fontSize: 12 }}>
                    Leave a field blank to fall back to the current env default. This only changes which ClickUp Sales CRM board Beacon imports from.
                  </p>
                  <button className="crm-button primary" type="button" onClick={handleSaveClickUpCrm} disabled={savingClickUpCrm || !clickupCrmSettings}>
                    {savingClickUpCrm ? <RefreshCw size={15} className="animate-spin" /> : <Shield size={15} />}
                    Save ClickUp source
                  </button>
                </div>
              ) : (
                <p className="crm-muted" style={{ fontSize: 12 }}>
                  Only admins can change the ClickUp import source. Everyone else uses the shared Sales CRM configuration.
                </p>
              )}
            </div>
          </div>
        )}
          </div>
      </div>

      <DriveFolderPicker
        open={drivePickerMode !== null}
        onClose={() => setDrivePickerMode(null)}
        onPick={drivePickerMode === "admin" ? handlePickAdminFolder : handlePickUserFolder}
        title={drivePickerMode === "admin" ? "Select a workspace Drive folder" : "Select your personal Drive folder"}
        description={
          drivePickerMode === "admin"
            ? "This folder will be visible to every user in the workspace."
            : "Only you will see files from this folder."
        }
      />
    </div>
  );
}

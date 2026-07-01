import { CSSProperties, memo, useEffect, useMemo, useState } from "react";
import { outreachApi } from "../../lib/api";
import type { Contact, OutreachSequence, OutreachStep } from "../../types";
import {
  X, Sparkles, Copy, CheckCheck, Linkedin, Mail, RefreshCw,
  Send, Rocket, CheckCircle, Clock, MessageSquare, ExternalLink, ChevronDown, ChevronUp,
  Pencil, Check, Plus, Trash2, Phone, Settings2, Play, Pause,
} from "lucide-react";
import { avatarColor, getInitials } from "../../lib/utils";

interface Props {
  contact: Contact | null;
  onClose: () => void;
  mode?: "drawer" | "inline";
}

// Soft cap on cadence length. 6 was the original default for a 3-email +
// intermixed call/LinkedIn sequence; bumped to 12 so multi-touch cadences
// (e.g. 4 emails + 3 calls + 3 LinkedIn touches + 2 nudges) fit without
// hitting a ceiling mid-authoring. Backend caps at 20 (settings.py).
const MAX_SEQUENCE_STEPS = 20;
type StepChannel = "email" | "call" | "linkedin";
type TabKey = `step_${number}`;
type OutreachReply = {
  id?: string;
  subject?: unknown;
  body?: unknown;
  from_email?: string;
  timestamp?: string;
  created_at?: string;
};

function stepTabKey(stepNumber: number): `step_${number}` {
  return `step_${stepNumber}`;
}

function getStepNumberFromTab(tab: TabKey): number | null {
  const raw = tab.replace("step_", "");
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function getStepChannel(step?: Pick<OutreachStep, "channel"> | null): StepChannel {
  const value = String(step?.channel || "email").trim().toLowerCase();
  return value === "call" || value === "linkedin" ? value : "email";
}

function getStepLabel(step: Pick<OutreachStep, "step_number" | "channel">, index: number): string {
  const channel = getStepChannel(step);
  if (channel === "call") return `Call ${index + 1}`;
  if (channel === "linkedin") return `LinkedIn ${index + 1}`;
  if (index === 0) return "Email 1";
  if (index === 1) return "Follow-up";
  if (index === 2) return "Final";
  return `Email ${step.step_number}`;
}

function stripHtml(value: string): string {
  return value
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .trim();
}

function replyText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return stripHtml(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => replyText(item)).filter(Boolean).join("\n");
  }
  if (typeof value !== "object") return "";

  const candidates = [
    "text",
    "plain_text",
    "body_text",
    "body",
    "content",
    "message",
    "reply_text",
    "html",
    "body_html",
    "snippet",
    "preview",
  ];

  for (const key of candidates) {
    const extracted = replyText((value as Record<string, unknown>)[key]);
    if (extracted) return extracted;
  }

  return "";
}

const PERSONA_LABEL: Record<string, string> = {
  economic_buyer: "Economic Buyer",
  champion: "Champion",
  technical_evaluator: "Technical Evaluator",
  unknown: "Unknown",
};

const PERSONA_STYLE: Record<string, CSSProperties> = {
  economic_buyer: { color: "#7b3a1d", background: "#ffe8de", border: "1px solid #ffc8b4" },
  champion: { color: "#1b6f53", background: "#e4fbf3", border: "1px solid #b8efd8" },
  technical_evaluator: { color: "#24567e", background: "#eaf4ff", border: "1px solid #c9e0f8" },
  unknown: { color: "#546679", background: "#edf3f9", border: "1px solid #d7e1eb" },
};

const palette = {
  panelBg: "#ffffff",
  line: "#dbe4ee",
  text: "#24384d",
  sub: "#5d748b",
  muted: "#7f93a8",
  soft: "#f4f8fc",
  accent: "#9ace3d",
  accentDark: "#e05725",
  accentSoft: "#f3fbe3",
  green: "#1f8f5f",
  greenSoft: "#e8f8f0",
  greenBorder: "#bde8d1",
  blue: "#24567e",
  blueSoft: "#eaf4ff",
  blueBorder: "#c9e0f8",
};

const DEFAULT_SENDING_ACCOUNT = "";

function OutreachDrawer({ contact, onClose, mode = "drawer" }: Props) {
  const isOpen = !!contact;
  const isInline = mode === "inline";

  const [seq, setSeq] = useState<OutreachSequence | null>(null);
  const [steps, setSteps] = useState<OutreachStep[]>([]);
  const [tab, setTab] = useState<TabKey>(stepTabKey(1));
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  // Launch state
  const [sendingAccount, setSendingAccount] = useState(DEFAULT_SENDING_ACCOUNT);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState("");
  const [sequenceApproved, setSequenceApproved] = useState(false);

  // Inline editing state
  const [editing, setEditing] = useState(false);
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveOk, setSaveOk] = useState(false);
  const [savingTiming, setSavingTiming] = useState(false);
  const [timingError, setTimingError] = useState("");
  const [timingOk, setTimingOk] = useState(false);
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);

  // Replies state
  const [replies, setReplies] = useState<OutreachReply[]>([]);
  const [showReplies, setShowReplies] = useState(false);
  const [loadingReplies, setLoadingReplies] = useState(false);

  // Campaign actions
  const [pausing, setPausing] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [campaignStats, setCampaignStats] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (!contact) {
      setSeq(null);
      setSteps([]);
      setError("");
      setLaunchError("");
      setSequenceApproved(false);
      setReplies([]);
      setShowAdvancedSettings(false);
      return;
    }

    // Cancel stale loads: switching prospects quickly would otherwise let the
    // previous contact's response land last and show the wrong sequence.
    let cancelled = false;
    setLoading(true);
    setTab(stepTabKey(1));
    setShowAdvancedSettings(false);
    setSequenceApproved(false);

    outreachApi
      .getSequenceOptional(contact.id)
      .then((s) => {
        if (cancelled) return;
        if (!s) {
          setSeq(null);
          setSteps([]);
          setError("");
          return;
        }
        setSeq(s);
        setError("");
        void loadSteps(s.id, s);
        // Auto-load replies if already launched
        if (s.instantly_campaign_id) {
          loadReplies(s.id);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setSeq(null);
        setSteps([]);
        setError("");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [contact?.id]);

  const loadSteps = async (sequenceId: string, currentSeq?: OutreachSequence | null) => {
    try {
      const loaded = await outreachApi.getSteps(sequenceId);
      if (loaded.length > 0) {
        setSteps(loaded);
        return;
      }
    } catch {
      // Fall back to legacy timing if step records are not available yet.
    }

    const source = currentSeq ?? seq;
    if (!source) {
      setSteps([]);
      return;
    }

    const fallback: OutreachStep[] = [
      {
        id: `${source.id}-1`,
        sequence_id: source.id,
        step_number: 1,
        channel: "email" as const,
        subject: source.subject_1,
        body: source.email_1 ?? "",
        delay_value: 0,
        delay_unit: "days",
        variants: null,
        status: "draft",
        created_at: source.created_at,
        updated_at: source.updated_at,
      },
      {
        id: `${source.id}-2`,
        sequence_id: source.id,
        step_number: 2,
        channel: "email" as const,
        subject: source.subject_2,
        body: source.email_2 ?? "",
        delay_value: 3,
        delay_unit: "days",
        variants: null,
        status: "draft",
        created_at: source.created_at,
        updated_at: source.updated_at,
      },
      {
        id: `${source.id}-3`,
        sequence_id: source.id,
        step_number: 3,
        channel: "email" as const,
        subject: source.subject_3,
        body: source.email_3 ?? "",
        delay_value: 7,
        delay_unit: "days",
        variants: null,
        status: "draft",
        created_at: source.created_at,
        updated_at: source.updated_at,
      },
    ].filter((step) => !!step.body);

    setSteps(fallback);
  };

  const loadReplies = async (sequenceId: string) => {
    setLoadingReplies(true);
    try {
      const result = await outreachApi.getReplies(sequenceId);
      setReplies(result.replies ?? []);
    } catch {
      // Non-fatal
    } finally {
      setLoadingReplies(false);
    }
  };

  const handleGenerate = async () => {
    if (!contact) return;
    setGenerating(true);
    setError("");
    try {
      const result = await outreachApi.generate(contact.id);
      setSeq(result);
      await loadSteps(result.id, result);
      setTab(stepTabKey(1));
      setSequenceApproved(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  const handleCallContact = () => {
    if (!contact?.phone) return;
    window.__aircallDial?.(contact.phone, `${contact.first_name} ${contact.last_name}`);
  };

  const handleLaunch = async () => {
    if (!seq || !sendingAccount.trim()) return;
    if (!sequenceApproved) {
      setLaunchError("Review and approve the sequence preview before launch.");
      return;
    }
    setLaunching(true);
    setLaunchError("");
    try {
      await outreachApi.launch(seq.id, sendingAccount.trim());
      // Reload the sequence to get updated campaign ID and status
      const updated = await outreachApi.getSequence(contact!.id);
      setSeq(updated);
      await loadSteps(updated.id, updated);
      // Load replies (will be empty initially but sets up the section)
      if (updated.instantly_campaign_id) {
        await loadReplies(updated.id);
      }
    } catch (e: unknown) {
      setLaunchError(e instanceof Error ? e.message : "Launch failed");
    } finally {
      setLaunching(false);
    }
  };

  const handlePause = async () => {
    if (!seq) return;
    setPausing(true);
    try {
      await outreachApi.pause(seq.id);
      const updated = await outreachApi.getSequence(contact!.id);
      setSeq(updated);
    } catch (e: unknown) {
      setLaunchError(e instanceof Error ? e.message : "Pause failed");
    } finally {
      setPausing(false);
    }
  };

  const handleResume = async () => {
    if (!seq) return;
    setResuming(true);
    try {
      await outreachApi.resume(seq.id);
      const updated = await outreachApi.getSequence(contact!.id);
      setSeq(updated);
    } catch (e: unknown) {
      setLaunchError(e instanceof Error ? e.message : "Resume failed");
    } finally {
      setResuming(false);
    }
  };

  const handleSync = async () => {
    if (!seq) return;
    setSyncing(true);
    setLaunchError("");
    try {
      const result = await outreachApi.syncCampaign(seq.id);
      setCampaignStats(result.analytics ?? null);
      const updated = await outreachApi.getSequence(contact!.id);
      setSeq(updated);
    } catch (e: unknown) {
      setLaunchError(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const sortedSteps = useMemo(
    () => [...steps].sort((a, b) => a.step_number - b.step_number),
    [steps]
  );
  const currentStepNumber = getStepNumberFromTab(tab);
  const currentStep = currentStepNumber == null
    ? null
    : sortedSteps.find((step) => step.step_number === currentStepNumber) ?? null;
  const currentChannel = getStepChannel(currentStep);

  const subject = useMemo(() => {
    if (currentChannel !== "email") return null;
    return currentStep?.subject ?? null;
  }, [currentChannel, currentStep]);

  const body = useMemo(() => {
    if (!seq) return "";
    const text = currentStep?.body ?? "";
    return text.replace(/^Subject:.*\n\n?/i, "").trim();
  }, [currentStep, seq]);

  const tabDayLabel = currentStep ? `Day ${currentStep.delay_value}` : "";
  const visibleTimingSteps = sortedSteps;

  const handleDelayChange = (stepId: string, rawValue: string) => {
    const nextValue = Math.max(0, Number.parseInt(rawValue || "0", 10) || 0);

    setSteps((current) =>
      current.map((step) =>
        step.id === stepId
          ? { ...step, delay_value: nextValue }
          : step
      )
    );
    setTimingError("");
    setTimingOk(false);
    setSequenceApproved(false);
  };

  const handleChannelChange = (stepId: string, rawValue: string) => {
    const nextChannel: StepChannel =
      rawValue === "call" || rawValue === "linkedin" ? rawValue : "email";
    setSteps((current) =>
      current.map((step) =>
        step.id === stepId
          ? {
              ...step,
              channel: nextChannel,
              subject: nextChannel === "email" ? (step.subject ?? "") : undefined,
              body:
                nextChannel === "call"
                  ? (step.body || "Call this prospect and use the recent outreach context before logging a disposition.")
                  : nextChannel === "linkedin"
                    ? (step.body || seq?.linkedin_message || "Send a short LinkedIn touch tailored to this prospect's role.")
                    : step.body,
            }
          : step
      )
    );
    setTimingError("");
    setTimingOk(false);
    setSequenceApproved(false);
  };

  const handleSaveTiming = async () => {
    if (!seq) return;
    const editableSteps = visibleTimingSteps;

    if (editableSteps.length === 0) return;

    for (let i = 1; i < editableSteps.length; i += 1) {
      if (editableSteps[i].delay_value < editableSteps[i - 1].delay_value) {
        setTimingError("Each next step must be on the same day or later than the previous one.");
        return;
      }
    }

    setSavingTiming(true);
    setTimingError("");
    try {
      const updatedSteps = await Promise.all(
        editableSteps.map((step) =>
          outreachApi.updateStep(step.id, {
            delay_value: step.delay_value,
            channel: getStepChannel(step),
            subject: getStepChannel(step) === "email" ? (step.subject ?? "") : undefined,
            body: step.body,
          })
        )
      );
      setSteps((current) =>
        current.map((step) => updatedSteps.find((item) => item.id === step.id) ?? step)
      );
      setSequenceApproved(false);
      setTimingOk(true);
      setTimeout(() => setTimingOk(false), 2500);
    } catch (e: unknown) {
      setTimingError(e instanceof Error ? e.message : "Could not save sequence timing");
    } finally {
      setSavingTiming(false);
    }
  };

  const handleCopy = async () => {
    const full = subject ? `Subject: ${subject}\n\n${body}` : body;
    if (!full) return;
    await navigator.clipboard.writeText(full);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleStartEdit = () => {
    setEditSubject(subject ?? "");
    setEditBody(body);
    setEditing(true);
    setSaveOk(false);
  };

  const handleSave = async () => {
    if (!seq || !currentStep) return;
    setSaving(true);
    try {
      await outreachApi.updateStep(currentStep.id, {
        channel: currentChannel,
        subject: currentChannel === "email" ? editSubject : undefined,
        body: editBody,
      });
      setSteps((current) =>
        current.map((step) =>
          step.id === currentStep.id
            ? { ...step, channel: currentChannel, subject: currentChannel === "email" ? editSubject : undefined, body: editBody }
            : step
        )
      );
      setSequenceApproved(false);

      if (currentChannel === "email" && currentStep.step_number <= 3) {
        const suffix = String(currentStep.step_number) as "1" | "2" | "3";
        const updated = await outreachApi.updateSequence(seq.id, {
          [`subject_${suffix}`]: editSubject,
          [`email_${suffix}`]: editBody,
        } as Partial<Record<"email_1" | "email_2" | "email_3" | "subject_1" | "subject_2" | "subject_3", string>>);
        setSeq(updated);
      }

      setEditing(false);
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2500);
    } catch {
      // keep editing open on error
    } finally {
      setSaving(false);
    }
  };

  const handleTabChange = (key: TabKey) => {
    setTab(key);
    setEditing(false);
    setSaveOk(false);
  };

  const isLaunched = !!(seq?.instantly_campaign_id || seq?.launched_at);
  const canEditTiming = !!seq && !isLaunched;
  const launchDisabled = launching || !sendingAccount.trim() || !contact?.email?.trim() || !sequenceApproved;

  const getStepTabStyle = (kind: StepChannel | "other", active: boolean): CSSProperties => {
    const tone = kind === "email"
      ? { bg: "#eaf4ff", border: "#c9e0f8", text: palette.blue }
      : kind === "call"
        ? { bg: "#fff4df", border: "#ffe0b2", text: "#b56d00" }
        : kind === "linkedin"
          ? { bg: "#eef1ff", border: "#d7ddff", text: "#4f46e5" }
          : { bg: "#f4f8fc", border: palette.line, text: palette.sub };

    return {
      border: `1px solid ${active ? tone.border : palette.line}`,
      borderRadius: 12,
      padding: "12px 10px",
      cursor: "pointer",
      fontWeight: 700,
      fontSize: 13,
      color: active ? tone.text : palette.sub,
      background: active ? tone.bg : `linear-gradient(180deg, ${tone.bg} 0%, #ffffff 100%)`,
      boxShadow: active ? "0 8px 18px rgba(17,34,68,0.08)" : "none",
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      gap: 6,
      flexDirection: "column",
      minHeight: 72,
    };
  };

  const handleAddTimingStep = async () => {
    if (!seq || !canEditTiming || visibleTimingSteps.length >= MAX_SEQUENCE_STEPS) return;
    const last = visibleTimingSteps[visibleTimingSteps.length - 1];
    if (!last) return;

    setSavingTiming(true);
    setTimingError("");
    try {
      const created = await outreachApi.addStep(seq.id, {
        step_number: last.step_number + 1,
        channel: "email",
        subject: last.subject ?? "",
        body: last.body,
        delay_value: last.delay_value + 3,
        delay_unit: last.delay_unit || "days",
        variants: null,
      });
      setSteps((current) => [...current, created]);
      setTab(stepTabKey(created.step_number));
      setSequenceApproved(false);
      setTimingOk(true);
      setTimeout(() => setTimingOk(false), 2500);
    } catch (e: unknown) {
      setTimingError(e instanceof Error ? e.message : "Could not add sequence step");
    } finally {
      setSavingTiming(false);
    }
  };

  const handleRemoveTimingStep = async (stepId: string) => {
    if (!canEditTiming || visibleTimingSteps.length <= 1) return;
    setSavingTiming(true);
    setTimingError("");
    try {
      await outreachApi.deleteStep(stepId);
      const remaining = visibleTimingSteps.filter((step) => step.id !== stepId);
      setSteps((current) => current.filter((step) => step.id !== stepId));
      setSequenceApproved(false);
      if (currentStep?.id === stepId && remaining.length > 0) {
        setTab(stepTabKey(remaining[remaining.length - 1].step_number));
      }
      setTimingOk(true);
      setTimeout(() => setTimingOk(false), 2500);
    } catch (e: unknown) {
      setTimingError(e instanceof Error ? e.message : "Could not remove sequence step");
    } finally {
      setSavingTiming(false);
    }
  };

  const content = (
    <>
        {/* Header */}
        <header style={{
          padding: "18px 22px",
          borderBottom: `1px solid ${palette.line}`,
          display: "flex", alignItems: "flex-start",
          justifyContent: "space-between", gap: 12,
        }}>
          <div style={{ display: "flex", gap: 12, minWidth: 0 }}>
            {contact && (
              <div
                className={avatarColor(contact.first_name + contact.last_name)}
                style={{
                  width: 42, height: 42, borderRadius: "999px",
                  display: "inline-flex", alignItems: "center",
                  justifyContent: "center", fontSize: 13, fontWeight: 800, flexShrink: 0,
                }}
              >
                {getInitials(`${contact.first_name} ${contact.last_name}`)}
              </div>
            )}
            <div style={{ minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <h2 style={{ margin: 0, fontSize: 22, color: palette.text, fontWeight: 800 }}>
                  {contact ? `${contact.first_name} ${contact.last_name}` : "Outreach"}
                </h2>
                {contact?.persona && (
                  <span style={{
                    ...(PERSONA_STYLE[contact.persona] ?? PERSONA_STYLE.unknown),
                    borderRadius: 999, padding: "3px 9px", fontSize: 11, fontWeight: 700,
                  }}>
                    {PERSONA_LABEL[contact.persona] ?? contact.persona}
                  </span>
                )}
                {isLaunched && (
                  <span style={{
                    borderRadius: 999, padding: "3px 9px", fontSize: 11, fontWeight: 700,
                    color: palette.green, background: palette.greenSoft, border: `1px solid ${palette.greenBorder}`,
                    display: "inline-flex", alignItems: "center", gap: 4,
                  }}>
                    <CheckCircle size={10} /> Live in Instantly
                  </span>
                )}
              </div>
              <p style={{ margin: "4px 0 0", color: palette.sub, fontSize: 14, lineHeight: 1.4 }}>
                {contact?.title ?? ""}
                {contact?.email ? ` · ${contact.email}` : ""}
              </p>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                {contact?.email && (
                  <a
                    href={`https://mail.google.com/mail/?view=cm&to=${encodeURIComponent(contact.email)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      ...ghostBtn,
                      textDecoration: "none",
                      fontSize: 12,
                      padding: "7px 11px",
                    }}
                  >
                    <Mail size={12} /> Email
                  </a>
                )}
                {contact?.phone && (
                  <button
                    onClick={handleCallContact}
                    style={{
                      ...ghostBtn,
                      fontSize: 12,
                      padding: "7px 11px",
                      color: palette.green,
                      borderColor: palette.greenBorder,
                      background: "#f4fbf7",
                    }}
                    title={`Call ${contact.phone} in Aircall`}
                  >
                    <Phone size={12} /> {contact.phone}
                  </button>
                )}
                {contact?.linkedin_url && (
                  <a
                    href={contact.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      ...ghostBtn,
                      textDecoration: "none",
                      fontSize: 12,
                      padding: "7px 11px",
                    }}
                  >
                    <Linkedin size={12} /> LinkedIn
                  </a>
                )}
              </div>
            </div>
          </div>
          {!isInline ? (
            <button onClick={onClose} style={{ border: 0, background: "transparent", color: palette.muted, cursor: "pointer", padding: 2 }}>
              <X size={18} />
            </button>
          ) : null}
        </header>

        {/* Body */}
        <div style={{ overflowY: "auto", padding: "20px 22px 32px", background: "#fbfdff" }}>
          {loading ? (
            <div style={{ height: 180, display: "grid", placeItems: "center", color: palette.sub }}>Loading...</div>
          ) : !seq ? (
            // ── Empty state ───────────────────────────────────────────────────
            <div style={{ ...panel, minHeight: 220, display: "grid", placeItems: "center", textAlign: "center", gap: 14 }}>
              <Mail size={28} color={palette.accent} />
              <p style={{ margin: 0, color: palette.sub, lineHeight: 1.6, fontSize: 15 }}>
                No outreach sequence yet. Generate one with AI.
              </p>
              <button onClick={handleGenerate} disabled={generating} style={generating ? { ...primaryBtn, ...disabledBtn } : primaryBtn}>
                {generating ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {generating ? "Generating..." : "Generate Sequence"}
              </button>
              {error && <p style={{ margin: 0, color: "#b42336", fontSize: 13 }}>{error}</p>}
            </div>
          ) : (
            <div style={{ display: "grid", gap: 14 }}>

              {/* ── Status bar ─────────────────────────────────────────────── */}
              <div style={{ ...panel, padding: "12px 14px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <div style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <StatusChip status={seq.status} />
                  {seq.generated_at && (
                    <span style={{ color: palette.muted, fontSize: 12 }}>
                      Generated {new Date(seq.generated_at).toLocaleDateString()}
                    </span>
                  )}
                  {seq.launched_at && (
                    <span style={{ color: palette.muted, fontSize: 12 }}>
                      · Launched {new Date(seq.launched_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
                <button
                  onClick={handleGenerate}
                  disabled={generating || isLaunched}
                  style={(generating || isLaunched) ? { ...ghostBtn, ...disabledBtn } : ghostBtn}
                  title={isLaunched ? "Cannot regenerate after launch" : undefined}
                >
                  <RefreshCw size={13} className={generating ? "animate-spin" : ""} /> Regenerate
                </button>
              </div>

              {/* ── Step timeline ───────────────────────────────────────────── */}
              <StepTimeline seq={seq} steps={steps} />

              {/* ── Externally-launched campaign notice ──────────────────────── */}
              {isLaunched && visibleTimingSteps.length === 0 && (
                <div style={{ ...panel, padding: "16px 18px", borderLeft: `3px solid ${palette.blue}`, background: palette.blueSoft }}>
                  <div style={{ fontWeight: 700, color: palette.blue, fontSize: 13, marginBottom: 6 }}>
                    Campaign managed in Instantly
                  </div>
                  <div style={{ fontSize: 12.5, color: palette.sub, lineHeight: 1.5 }}>
                    This campaign was started directly in Instantly. Email content and sequence steps are managed there. 
                    Replies, opens, and clicks will still sync to the CRM automatically.
                  </div>
                  <div style={{ marginTop: 10, fontSize: 11, color: palette.muted, fontFamily: "monospace" }}>
                    Campaign ID: {seq.instantly_campaign_id}
                  </div>
                </div>
              )}

              {/* ── Email tabs ──────────────────────────────────────────────── */}
              {visibleTimingSteps.length > 0 && (
              <div style={{ ...panel, padding: 8, display: "grid", gridTemplateColumns: `repeat(${Math.max(visibleTimingSteps.length, 2)}, minmax(0, 1fr))`, gap: 8 }}>
                {visibleTimingSteps.map((step, index) => {
                  const stepKey = stepTabKey(step.step_number);
                  const channel = getStepChannel(step);
                  const label = getStepLabel(step, index);
                  const Icon = channel === "linkedin" ? Linkedin : channel === "call" ? Phone : Mail;
                  return (
                  <button
                    key={step.id}
                    onClick={() => handleTabChange(stepKey)}
                    style={getStepTabStyle(channel, tab === stepKey)}
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                      <Icon size={13} />
                      {label}
                    </span>
                    <span style={{ fontSize: 10, fontWeight: 600, color: palette.muted }}>
                      Day {step.delay_value}
                    </span>
                  </button>
                )})}
              </div>
              )}

              <div style={{ ...panel, padding: "12px 14px" }}>
                <button
                  type="button"
                  onClick={() => setShowAdvancedSettings((current) => !current)}
                  style={{
                    width: "100%",
                    border: 0,
                    background: "transparent",
                    padding: 0,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 12,
                    color: palette.text,
                  }}
                >
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 14 }}>
                    <Settings2 size={14} color={palette.blue} />
                    Advanced settings
                  </span>
                  {showAdvancedSettings ? <ChevronUp size={14} color={palette.muted} /> : <ChevronDown size={14} color={palette.muted} />}
                </button>
                <div style={{ marginTop: 6, color: palette.muted, fontSize: 12, lineHeight: 1.5 }}>
                  Configure prospect-only timing overrides and extra touches before launch.
                </div>
              </div>

              {showAdvancedSettings && (
                <div style={{ ...panel, padding: "14px 16px" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                    <div>
                      <div style={{ fontWeight: 700, color: palette.text, fontSize: 14 }}>Sequence timing override</div>
                      <div style={{ marginTop: 4, color: palette.muted, fontSize: 12 }}>
                        Set timing just for this prospect before the sequence starts. You can add or remove touches here.
                      </div>
                    </div>
                    {!canEditTiming && (
                      <span style={{ fontSize: 12, color: palette.green, fontWeight: 700 }}>
                        Locked after launch
                      </span>
                    )}
                  </div>
                  <div style={{ marginTop: 14, display: "grid", gridTemplateColumns: `repeat(${Math.min(Math.max(visibleTimingSteps.length, 1), 3)}, minmax(0, 1fr))`, gap: 10 }}>
                    {visibleTimingSteps.map((step, index) => (
                      <label key={step.id} style={{ display: "grid", gap: 6 }}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: palette.sub }}>
                          Step {index + 1}
                        </span>
                        <select
                          value={getStepChannel(step)}
                          disabled={!canEditTiming}
                          onChange={(e) => handleChannelChange(step.id, e.target.value)}
                          style={{
                            width: "100%",
                            boxSizing: "border-box",
                            border: `1px solid ${palette.line}`,
                            borderRadius: 8,
                            padding: "9px 12px",
                            fontSize: 13,
                            color: palette.text,
                            fontFamily: "inherit",
                            outline: "none",
                            background: canEditTiming ? "#fff" : "#f4f8fc",
                          }}
                        >
                          <option value="email">Email</option>
                          <option value="call">Call</option>
                          <option value="linkedin">LinkedIn</option>
                        </select>
                        <input
                          type="number"
                          min={0}
                          value={step.delay_value}
                          disabled={!canEditTiming}
                          onChange={(e) => handleDelayChange(step.id, e.target.value)}
                          style={{
                            width: "100%",
                            boxSizing: "border-box",
                            border: `1px solid ${palette.line}`,
                            borderRadius: 8,
                            padding: "9px 12px",
                            fontSize: 14,
                            color: palette.text,
                            fontFamily: "inherit",
                            outline: "none",
                            background: canEditTiming ? "#fff" : "#f4f8fc",
                          }}
                        />
                        <span style={{ fontSize: 11, color: palette.muted }}>
                          {getStepChannel(step) === "call" ? "Call on" : getStepChannel(step) === "linkedin" ? "Touch on LinkedIn on" : "Send on"} Day {step.delay_value}
                        </span>
                        {canEditTiming && visibleTimingSteps.length > 1 && index === visibleTimingSteps.length - 1 && (
                          <button
                            onClick={() => void handleRemoveTimingStep(step.id)}
                            type="button"
                            style={{ ...copyBtn, justifyContent: "center" }}
                          >
                            <Trash2 size={12} />
                            Remove last step
                          </button>
                        )}
                      </label>
                    ))}
                  </div>
                  <div style={{ marginTop: 12, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                    <div style={{ fontSize: 12, color: palette.blue }}>
                      {tabDayLabel ? `This touch is currently set for ${tabDayLabel}. Changes here affect only this prospect.` : "Changes here affect only this prospect."}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      {timingError && <span style={{ fontSize: 12, color: "#b42336" }}>{timingError}</span>}
                      {timingOk && <span style={{ fontSize: 12, color: palette.green, fontWeight: 700 }}>Timing saved</span>}
                      {canEditTiming && visibleTimingSteps.length < MAX_SEQUENCE_STEPS && (
                        <button onClick={() => void handleAddTimingStep()} style={ghostBtn}>
                          <Plus size={13} />
                          Add step
                        </button>
                      )}
                      <button
                        onClick={handleSaveTiming}
                        disabled={!canEditTiming || savingTiming}
                        style={{
                          ...ghostBtn,
                          opacity: canEditTiming ? 1 : 0.6,
                          cursor: canEditTiming ? "pointer" : "not-allowed",
                        }}
                      >
                        {savingTiming ? <RefreshCw size={13} className="animate-spin" /> : <Check size={13} />}
                        {savingTiming ? "Saving..." : "Save timing"}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Subject */}
              {currentChannel === "email" && (subject || editing) && (
                <div style={{ ...panel, padding: "12px 14px" }}>
                  <div style={{ color: palette.muted, fontSize: 11, letterSpacing: 0.4, fontWeight: 700, marginBottom: 6 }}>SUBJECT</div>
                  {editing ? (
                    <input
                      value={editSubject}
                      onChange={(e) => setEditSubject(e.target.value)}
                      style={{ width: "100%", boxSizing: "border-box", border: `1px solid ${palette.accent}`, borderRadius: 8, padding: "7px 10px", fontSize: 15, fontWeight: 700, color: palette.text, fontFamily: "inherit", outline: "none" }}
                    />
                  ) : (
                    <div style={{ color: palette.text, fontWeight: 800, fontSize: 17, lineHeight: 1.4 }}>{subject}</div>
                  )}
                </div>
              )}

              {/* Body */}
              <div style={{ ...panel, padding: "14px 14px 16px", position: "relative" }}>
                {editing ? (
                  <textarea
                    value={editBody}
                    onChange={(e) => setEditBody(e.target.value)}
                    rows={12}
                    style={{ width: "100%", boxSizing: "border-box", border: `1px solid ${palette.accent}`, borderRadius: 8, padding: "10px", fontSize: 15, color: palette.text, fontFamily: "inherit", lineHeight: 1.65, resize: "vertical", outline: "none" }}
                  />
                ) : body ? (
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "inherit", color: palette.text, lineHeight: 1.65, fontSize: 16 }}>
                    {body}
                  </pre>
                ) : (
                  <div style={{ color: palette.muted, fontStyle: "italic" }}>No content for this touch.</div>
                )}

                {/* Edit / Save / Copy controls */}
                <div style={{ position: "absolute", top: 12, right: 12, display: "flex", gap: 6 }}>
                  {!isLaunched && !editing && body && (
                    <button onClick={handleStartEdit} style={copyBtn}>
                      <Pencil size={12} /> Edit
                    </button>
                  )}
                  {editing && (
                    <>
                      <button onClick={() => setEditing(false)} style={copyBtn}>Cancel</button>
                      <button onClick={handleSave} disabled={saving} style={{ ...copyBtn, color: palette.green, borderColor: palette.greenBorder }}>
                        {saving ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
                        {saving ? "Saving..." : "Save"}
                      </button>
                    </>
                  )}
                  {!editing && body && saveOk && (
                    <span style={{ ...copyBtn, color: palette.green, borderColor: palette.greenBorder, cursor: "default" }}>
                      <Check size={12} /> Saved
                    </span>
                  )}
                  {!editing && body && (
                    <button onClick={handleCopy} style={copyBtn}>
                      {copied ? <CheckCheck size={13} color={palette.green} /> : <Copy size={13} />}
                      {copied ? "Copied" : "Copy"}
                    </button>
                  )}
                </div>
              </div>

              {/* LinkedIn profile link */}
              {currentChannel === "linkedin" && contact?.linkedin_url && (
                <a
                  href={contact.linkedin_url}
                  target="_blank" rel="noopener noreferrer"
                  style={{ ...panel, padding: "10px 12px", color: palette.accent, fontWeight: 700, fontSize: 13, textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 8, justifySelf: "start" }}
                >
                  <Linkedin size={13} /> Open LinkedIn profile
                </a>
              )}

              {/* ── Launch section ──────────────────────────────────────────── */}
              {isLaunched ? (
                <div style={{ ...panel, padding: "14px 16px", borderLeft: `3px solid ${palette.green}` }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <CheckCircle size={15} color={palette.green} />
                      <span style={{ fontWeight: 700, color: palette.green, fontSize: 14 }}>
                        Live in Instantly
                        {seq.instantly_campaign_status && (
                          <span style={{ marginLeft: 8, fontSize: 11, padding: "2px 7px", borderRadius: 999, background: seq.instantly_campaign_status === "paused" ? "#fff3cd" : "#e8f8f0", color: seq.instantly_campaign_status === "paused" ? "#856404" : palette.green, fontWeight: 600 }}>
                            {seq.instantly_campaign_status}
                          </span>
                        )}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <button
                        onClick={handleSync}
                        disabled={syncing}
                        style={{ ...ghostBtn, fontSize: 12, gap: 4 }}
                        title="Pull latest stats from Instantly"
                      >
                        <RefreshCw size={11} className={syncing ? "animate-spin" : ""} /> Sync
                      </button>
                      {seq.instantly_campaign_status === "paused" ? (
                        <button
                          onClick={handleResume}
                          disabled={resuming}
                          style={{ ...ghostBtn, fontSize: 12, gap: 4, color: palette.green, ...(resuming ? disabledBtn : {}) }}
                        >
                          {resuming ? <RefreshCw size={11} className="animate-spin" /> : <Play size={11} />} Resume
                        </button>
                      ) : (
                        <button
                          onClick={handlePause}
                          disabled={pausing}
                          style={{ ...ghostBtn, fontSize: 12, gap: 4, color: "#b45309", ...(pausing ? disabledBtn : {}) }}
                        >
                          {pausing ? <RefreshCw size={11} className="animate-spin" /> : <Pause size={11} />} Pause
                        </button>
                      )}
                      <a
                        href="https://app.instantly.ai/app/campaigns"
                        target="_blank" rel="noopener noreferrer"
                        style={{ ...ghostBtn, textDecoration: "none", fontSize: 12 }}
                      >
                        <ExternalLink size={12} /> View
                      </a>
                    </div>
                  </div>
                  {seq.instantly_campaign_id && (
                    <div style={{ marginTop: 8, fontSize: 11, color: palette.muted, fontFamily: "monospace" }}>
                      Campaign ID: {seq.instantly_campaign_id}
                    </div>
                  )}
                  {campaignStats && (
                    <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 8, background: palette.soft, display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, color: palette.sub }}>
                      {typeof campaignStats.emails_sent_count === "number" && <span><strong>{campaignStats.emails_sent_count}</strong> sent</span>}
                      {typeof campaignStats.open_count === "number" && <span><strong>{campaignStats.open_count}</strong> opens</span>}
                      {typeof campaignStats.reply_count === "number" && <span><strong>{campaignStats.reply_count}</strong> replies</span>}
                      {typeof campaignStats.bounced_count === "number" && <span><strong>{campaignStats.bounced_count}</strong> bounced</span>}
                    </div>
                  )}
                  {launchError && (
                    <p style={{ margin: "8px 0 0", color: "#b42336", fontSize: 12 }}>{launchError}</p>
                  )}
                </div>
              ) : (
                  // Not launched — always show sending account + launch button
                  <div style={{ ...panel, padding: "16px 16px" }}>
                    <div style={{ display: "grid", gap: 12 }}>
                      <div style={{ fontWeight: 700, color: palette.text, fontSize: 14, display: "flex", alignItems: "center", gap: 7 }}>
                        <Rocket size={14} color={palette.accent} /> Launch to Instantly
                      </div>

                      {/* Sending account — always visible */}
                      <div>
                        <label style={{ fontSize: 12, fontWeight: 700, color: palette.sub, display: "block", marginBottom: 5 }}>
                          SENDING FROM
                        </label>
                        <input
                          type="email"
                          value={sendingAccount}
                          onChange={(e) => setSendingAccount(e.target.value)}
                          placeholder="you@company.com"
                          style={{
                            width: "100%", boxSizing: "border-box",
                            border: `1px solid ${palette.line}`, borderRadius: 8,
                            padding: "9px 12px", fontSize: 14, color: palette.text,
                            fontFamily: "inherit", outline: "none",
                          }}
                        />
                        <p style={{ margin: "5px 0 0", fontSize: 11, color: palette.muted }}>
                          Your connected sending email in Instantly
                        </p>
                      </div>

                      {/* Info note */}
                      <div style={{
                        padding: "8px 10px", borderRadius: 8,
                        background: palette.blueSoft, border: `1px solid ${palette.blueBorder}`,
                        fontSize: 12, color: palette.blue,
                      }}>
                        Sends {visibleTimingSteps.map((step) => `Day ${step.delay_value}`).join(" -> ")} · Auto-stops on reply · Opens & clicks tracked
                      </div>

                      <label style={{
                        display: "flex", alignItems: "flex-start", gap: 9,
                        padding: "10px 11px", borderRadius: 10,
                        background: sequenceApproved ? palette.greenSoft : "#fffaf6",
                        border: `1px solid ${sequenceApproved ? palette.greenBorder : "#ffd9c5"}`,
                        color: sequenceApproved ? palette.green : "#a45125",
                        fontSize: 12.5, lineHeight: 1.45, cursor: "pointer",
                      }}>
                        <input
                          type="checkbox"
                          checked={sequenceApproved}
                          onChange={(event) => {
                            setSequenceApproved(event.target.checked);
                            if (event.target.checked) setLaunchError("");
                          }}
                          style={{ marginTop: 2, accentColor: palette.green }}
                        />
                        <span>
                          I reviewed the email copy, timing, and sending account. Ready to launch.
                        </span>
                      </label>

                      {launchError && (
                        <p style={{ margin: 0, color: "#b42336", fontSize: 13 }}>{launchError}</p>
                      )}

                      {!contact?.email?.trim() && (
                        <p style={{ margin: 0, color: "#b42336", fontSize: 13 }}>
                          This contact has no email on file — add one before launching the sequence.
                        </p>
                      )}

                      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                        <button
                          onClick={handleLaunch}
                          disabled={launchDisabled}
                          style={launchDisabled ? { ...launchBtn, ...disabledBtn } : launchBtn}
                          title={!contact?.email?.trim() ? "Add an email to this contact before launching" : !sequenceApproved ? "Review and approve the sequence preview first" : undefined}
                        >
                          {launching ? <RefreshCw size={13} className="animate-spin" /> : <Rocket size={13} />}
                          {launching ? "Launching..." : "Launch Sequence"}
                        </button>
                      </div>
                    </div>
                  </div>
                )
              }

              {/* ── Replies section ─────────────────────────────────────────── */}
              {isLaunched && (
                <div style={{ ...panel, padding: "12px 14px" }}>
                  <button
                    onClick={() => {
                      setShowReplies(!showReplies);
                      if (!showReplies && seq.id) loadReplies(seq.id);
                    }}
                    style={{ width: "100%", border: 0, background: "transparent", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between", padding: 0 }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 7, fontWeight: 700, color: palette.text, fontSize: 14 }}>
                      <MessageSquare size={14} color={palette.accent} />
                      Replies
                      {replies.length > 0 && (
                        <span style={{ background: palette.accent, color: "#fff", borderRadius: 999, padding: "1px 7px", fontSize: 11, fontWeight: 800 }}>
                          {replies.length}
                        </span>
                      )}
                    </span>
                    {showReplies ? <ChevronUp size={14} color={palette.muted} /> : <ChevronDown size={14} color={palette.muted} />}
                  </button>

                  {showReplies && (
                    <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
                      {loadingReplies ? (
                        <div style={{ color: palette.muted, fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
                          <RefreshCw size={12} className="animate-spin" /> Loading replies...
                        </div>
                      ) : replies.length === 0 ? (
                        <div style={{ color: palette.muted, fontSize: 13, padding: "10px 0" }}>
                          No replies yet. Instantly will notify us when a reply comes in.
                        </div>
                      ) : (
                        replies.map((reply, i) => {
                          const subject = replyText(reply.subject);
                          const body = replyText(reply.body);
                          return (
                            <div key={reply.id ?? i} style={{ padding: "10px 12px", borderRadius: 8, border: `1px solid ${palette.greenBorder}`, background: palette.greenSoft }}>
                              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
                                <span style={{ fontWeight: 700, fontSize: 13, color: palette.text }}>
                                  {reply.from_email ?? "Prospect"}
                                </span>
                                <span style={{ fontSize: 11, color: palette.muted }}>
                                  {reply.created_at ? new Date(reply.created_at).toLocaleDateString() : reply.timestamp ?? ""}
                                </span>
                              </div>
                              {subject && (
                                <div style={{ fontSize: 12, color: palette.sub, marginBottom: 4 }}>Re: {subject}</div>
                              )}
                              {body && (
                                <div style={{ fontSize: 13, color: palette.text, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                                  {body.slice(0, 500) + (body.length > 500 ? "..." : "")}
                                </div>
                              )}
                            </div>
                          );
                        })
                      )}
                    </div>
                  )}
                </div>
              )}

            </div>
          )}
        </div>
    </>
  );

  if (isInline) {
    if (!contact) return null;
    return (
      <div
        style={{
          ...panel,
          overflow: "hidden",
          background: palette.panelBg,
          boxShadow: "0 12px 26px rgba(17,34,68,0.07)",
        }}
      >
        {content}
      </div>
    );
  }

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, zIndex: 40,
          background: "rgba(16, 24, 40, 0.24)",
          backdropFilter: "blur(2px)",
          opacity: isOpen ? 1 : 0,
          pointerEvents: isOpen ? "auto" : "none",
          transition: "opacity 220ms ease",
        }}
      />

      <aside
        style={{
          position: "fixed", top: 0, right: 0, zIndex: 50,
          height: "100%", width: "min(800px, 100%)",
          background: palette.panelBg,
          borderLeft: `1px solid ${palette.line}`,
          boxShadow: "-16px 0 40px rgba(10, 21, 42, 0.18)",
          transform: isOpen ? "translateX(0)" : "translateX(100%)",
          transition: "transform 260ms ease",
          display: "grid",
          gridTemplateRows: "auto 1fr",
        }}
      >
        {content}
      </aside>
    </>
  );
}

export default memo(OutreachDrawer);

// ── Step Timeline ─────────────────────────────────────────────────────────────

function StepTimeline({ seq, steps }: { seq: OutreachSequence; steps: OutreachStep[] }) {
  const timeline = [...steps]
    .sort((a, b) => a.step_number - b.step_number)
    .map((step, index) => ({
      label: getStepLabel(step, index),
      channel: getStepChannel(step),
      day: `Day ${step.delay_value}`,
      body: step.body,
    }));

  const isLaunched = !!(seq.instantly_campaign_id || seq.launched_at);
  const isReplied = seq.status === "replied" || seq.status === "meeting_booked";

  return (
    <div style={{ ...panel, padding: "12px 14px" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: palette.muted, letterSpacing: 0.4, marginBottom: 10 }}>
        SEQUENCE STEPS
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
        {timeline.map((step, i) => {
          const isReady = !!step.body;
          const isSent = isLaunched && !isReplied;
          const isDone = isReplied;

          return (
            <div key={i} style={{ display: "flex", alignItems: "center", flex: 1 }}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 5, flex: 1 }}>
                <div style={{
                  width: 28, height: 28, borderRadius: "50%",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 11, fontWeight: 800,
                  background: isDone ? palette.greenSoft : isSent ? palette.accentSoft : isReady ? (step.channel === "linkedin" ? "#eef1ff" : step.channel === "call" ? "#fff4df" : "#f0f4fa") : "#f5f5f5",
                  border: `2px solid ${isDone ? palette.greenBorder : isSent ? "#ffc8b4" : isReady ? (step.channel === "linkedin" ? "#d7ddff" : step.channel === "call" ? "#ffe0b2" : "#c5d4e8") : "#e0e0e0"}`,
                  color: isDone ? palette.green : isSent ? palette.accent : isReady ? (step.channel === "linkedin" ? "#4f46e5" : step.channel === "call" ? "#b56d00" : palette.sub) : palette.muted,
                }}>
                  {isDone ? <CheckCircle size={13} /> : isSent ? <Send size={11} /> : step.channel === "linkedin" ? <Linkedin size={11} /> : step.channel === "call" ? <Phone size={11} /> : <Mail size={11} />}
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: palette.text }}>{step.label}</div>
                  <div style={{ fontSize: 10, color: palette.muted }}>{step.day}</div>
                </div>
              </div>
              {i < timeline.length - 1 && (
                <div style={{
                  height: 2, width: 24, flexShrink: 0, marginBottom: 20,
                  background: isSent || isDone ? palette.accent : palette.line,
                }} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusChip({ status }: { status: string }) {
  const style: CSSProperties =
    status === "launched"
      ? { color: palette.green, background: palette.greenSoft, border: `1px solid ${palette.greenBorder}` }
      : status === "replied" || status === "meeting_booked"
      ? { color: "#6b3fa0", background: "#f3eeff", border: "1px solid #d4b8f8" }
      : status === "approved" || status === "sent"
      ? { color: palette.blue, background: palette.blueSoft, border: `1px solid ${palette.blueBorder}` }
      : { color: "#546679", background: "#edf3f9", border: "1px solid #d7e1eb" };

  return (
    <span style={{ ...style, borderRadius: 999, padding: "4px 10px", fontSize: 11, fontWeight: 700, textTransform: "capitalize" }}>
      {status.replace("_", " ")}
    </span>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const panel: CSSProperties = {
  background: "#ffffff",
  border: `1px solid ${palette.line}`,
  borderRadius: 12,
  boxShadow: "0 2px 6px rgba(17, 34, 68, 0.05)",
};

const primaryBtn: CSSProperties = {
  border: 0, borderRadius: 10,
  background: palette.accent, color: "#fff",
  fontSize: 13, fontWeight: 700, cursor: "pointer",
  padding: "9px 12px", display: "inline-flex", alignItems: "center", gap: 7,
};

const ghostBtn: CSSProperties = {
  border: `1px solid ${palette.line}`, borderRadius: 10,
  background: "#fff", color: palette.sub,
  fontSize: 13, fontWeight: 700, cursor: "pointer",
  padding: "8px 10px", display: "inline-flex", alignItems: "center", gap: 7,
};

const copyBtn: CSSProperties = {
  border: `1px solid ${palette.line}`, borderRadius: 8,
  background: "#fff", color: palette.sub,
  fontSize: 12, fontWeight: 700, cursor: "pointer",
  padding: "6px 9px", display: "inline-flex", alignItems: "center", gap: 6,
};

const disabledBtn: CSSProperties = {
  cursor: "not-allowed",
  opacity: 0.55,
};

const launchBtn: CSSProperties = {
  border: 0, borderRadius: 10,
  background: palette.accent, color: "#fff",
  fontSize: 13, fontWeight: 700, cursor: "pointer",
  padding: "9px 14px", display: "inline-flex", alignItems: "center", gap: 7,
  whiteSpace: "nowrap",
};

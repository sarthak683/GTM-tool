import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, BrainCircuit, ExternalLink, RefreshCw, Sparkles,
  Users, Newspaper, BookOpen, Shield, ChevronDown, ChevronUp,
  Building2, TrendingUp, Lightbulb, Search, PlayCircle,
  Swords, MessageSquareWarning, ArrowRight, Briefcase, Zap,
  Globe, Target, Mail, UserPlus, FileText, Crosshair, Trash2,
  Link2, Link2Off, Plus, X, Save,
  ClipboardList,
} from "lucide-react";
import { ZippyDocDropdown } from "../components/zippy/ZippyDocDropdown";
import { companiesApi, contactsApi, dealsApi, intelligenceApi, meetingsApi, signalsApi } from "../lib/api";
import type { Company, Contact, Deal, Meeting, Signal } from "../types";
import { formatCurrency, formatDate, avatarColor, getInitials } from "../lib/utils";

// ── Styles ───────────────────────────────────────────────────────────────────

const SIGNAL_DOT: Record<string, string> = {
  funding: "#f59e0b", pr: "#3b82f6", jobs: "#10b981",
  review: "#8b5cf6", linkedin: "#ec4899", news: "#64748b",
};

const PERSONA_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  economic_buyer:      { color: "#7b3a1d", bg: "#ffe8de", border: "#ffc8b4" },
  champion:            { color: "#1b6f53", bg: "#e4fbf3", border: "#b8efd8" },
  technical_evaluator: { color: "#24567e", bg: "#eaf4ff", border: "#c9e0f8" },
  unknown:             { color: "#546679", bg: "#edf3f9", border: "#d7e1eb" },
};

const PERSONA_LABEL: Record<string, string> = {
  economic_buyer: "Buyer", champion: "Champion",
  technical_evaluator: "Tech Eval", unknown: "Unknown",
};

function canonicalPersona(persona?: string | null, personaType?: string | null): keyof typeof PERSONA_STYLE {
  const normalized = (persona || personaType || "").toLowerCase();
  if (normalized === "buyer" || normalized === "economic_buyer") return "economic_buyer";
  if (normalized === "champion") return "champion";
  if (normalized === "evaluator" || normalized === "technical_evaluator") return "technical_evaluator";
  return "unknown";
}

// ── Types ────────────────────────────────────────────────────────────────────

interface CompanyBackground {
  title?: string;
  description?: string;
  extract?: string;
  url?: string;
  founded?: string;
}

interface IntentSignals {
  hiring?: Array<{ title: string; snippet: string }>;
  funding?: Array<{ title: string; snippet: string }>;
  product?: Array<{ title: string; snippet: string }>;
}

interface GoogleNewsItem {
  title: string;
  url: string;
  published?: string;
  source?: string;
}

interface HunterContact {
  email: string;
  first_name: string;
  last_name: string;
  title?: string;
  linkedin_url?: string;
  confidence: number;
}

interface HunterContacts {
  domain: string;
  pattern?: string;
  emails_found?: number;
  contacts: HunterContact[];
  organization?: string;
}

interface WhyNowSignal {
  title: string;
  detail: string;
  source: string;
  url?: string;
}

interface StakeholderCard {
  contact_id?: string;
  name: string;
  title?: string;
  email?: string;
  linkedin_url?: string;
  persona?: string;
  role?: string;
  role_label?: string;
  status?: "attending" | "recommended" | "discovered";
  priority?: "high" | "medium" | "low";
  likely_focus?: string;
  talk_track?: string;
  questions_to_ask?: string[];
}

interface CommitteeCoverage {
  coverage_score?: number;
  discovered_roles?: Array<{ role: string; label: string }>;
  attending_roles?: Array<{ role: string; label: string }>;
  missing_roles?: Array<{ role: string; label: string }>;
  meeting_gaps?: Array<{ role: string; label: string }>;
}

interface AttendeeIntelligence {
  has_explicit_attendees?: boolean;
  stakeholder_cards?: StakeholderCard[];
  committee_coverage?: CommitteeCoverage;
}

interface CompanySnapshot {
  icp_score?: number | null;
  icp_tier?: string | null;
  industry?: string | null;
  employee_count?: number | null;
  funding_stage?: string | null;
  pain_points?: string[];
  talking_points?: string[];
  beacon_angle?: string | null;
  conversation_starter?: string | null;
  why_now_summary?: string | null;
  recommended_approach?: string | null;
}

interface WebResearch {
  company_snapshot?: CompanySnapshot | null;
  company_background?: CompanyBackground | null;
  website_analysis?: Record<string, string> | null;
  recent_news?: Array<{ title: string; url: string; snippet: string }>;
  milestones?: Array<{ title: string; url: string; snippet: string }>;
  intent_signals?: IntentSignals | null;
  google_news?: GoogleNewsItem[];
  hunter_contacts?: HunterContacts | null;
  hunter_company?: Record<string, unknown> | null;
  competitive_landscape?: Array<{ title: string; url: string; snippet: string }>;
  attendee_intelligence?: AttendeeIntelligence | null;
  why_now_signals?: WhyNowSignal[];
  meeting_recommendations?: string[];
  relevant_battlecards?: Array<{ id: string; title: string; category: string; summary: string }>;
  executive_briefing?: string | null;
}

// ── Demo strategy section config ─────────────────────────────────────────────

const STORY_SECTIONS = [
  { icon: Lightbulb,            color: "#ff6b35", bg: "#fff8f5", border: "#ffd5be", label: "Opening Hook" },
  { icon: Search,               color: "#2563eb", bg: "#eff6ff", border: "#bfdbfe", label: "Discovery Question" },
  { icon: PlayCircle,           color: "#7c3aed", bg: "#f5f3ff", border: "#ddd6fe", label: "Story Lineup" },
  { icon: Swords,               color: "#0f766e", bg: "#f0fdfa", border: "#99f6e4", label: "Key Differentiation" },
  { icon: MessageSquareWarning, color: "#b91c1c", bg: "#fef2f2", border: "#fecaca", label: "Objection Handling" },
  { icon: ArrowRight,           color: "#15803d", bg: "#f0fdf4", border: "#bbf7d0", label: "Next Step" },
];

/** Parse GPT-4o numbered output into { title, body }[] sections */
function parseDemoSections(text: string): { title: string; body: string }[] {
  // Split on lines starting with a digit+dot (1. 2. etc.)
  const chunks = text.split(/\n(?=\d+\.\s)/);
  return chunks.map((chunk) => {
    const firstNewline = chunk.indexOf("\n");
    const heading = firstNewline === -1 ? chunk : chunk.slice(0, firstNewline);
    const body = firstNewline === -1 ? "" : chunk.slice(firstNewline + 1).trim();
    // Strip leading "1. " and markdown bold markers from the heading
    const title = heading.replace(/^\d+\.\s*/, "").replace(/\*\*/g, "").replace(/:$/, "").trim();
    return { title, body };
  }).filter((s) => s.title);
}

function DemoStrategyCards({ strategy, onRegenerate, regenerating }: {
  strategy: string;
  onRegenerate: () => void;
  regenerating: boolean;
}) {
  const sections = parseDemoSections(strategy);

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        {sections.map((sec, i) => {
          const cfg = STORY_SECTIONS[i] ?? STORY_SECTIONS[0];
          const Icon = cfg.icon;
          return (
            <div
              key={i}
              className="rounded-xl border p-4"
              style={{ background: cfg.bg, borderColor: cfg.border }}
            >
              <div className="flex items-center gap-2 mb-2">
                <Icon size={14} style={{ color: cfg.color }} />
                <span className="text-[11px] font-bold uppercase tracking-wide" style={{ color: cfg.color }}>
                  {cfg.label}
                </span>
              </div>
              <p className="text-[12px] font-semibold text-[#2b3f55] mb-1">{sec.title}</p>
              <div className="text-[13px] text-[#3d5268] leading-relaxed whitespace-pre-wrap">
                {sec.body.replace(/\*\*/g, "").replace(/^\s*[-–]\s*/gm, "• ")}
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex justify-end pt-1">
        <button className="crm-button soft text-[12px]" onClick={onRegenerate} disabled={regenerating}>
          {regenerating ? <RefreshCw size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {regenerating ? "Regenerating…" : "Regenerate Story"}
        </button>
      </div>
    </div>
  );
}

// ── Collapsible section wrapper ───────────────────────────────────────────────

function Section({ title, icon, children, defaultOpen = true, badge }: {
  title: string; icon: React.ReactNode; children: React.ReactNode;
  defaultOpen?: boolean; badge?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="crm-panel overflow-hidden">
      <button className="flex items-center justify-between w-full px-6 py-4 text-left" style={{ padding: "16px 22px" }} onClick={() => setOpen(!open)}>
        <div className="flex items-center gap-2">
          {icon}
          <span className="text-[15px] font-bold text-[#2b3f55]">{title}</span>
          {badge && <span className="crm-chip text-[11px]">{badge}</span>}
        </div>
        {open ? <ChevronUp size={14} className="text-[#7a8ea4]" /> : <ChevronDown size={14} className="text-[#7a8ea4]" />}
      </button>
      {open && <div className="px-6 pb-6" style={{ padding: "0 22px 22px" }}>{children}</div>}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [meeting, setMeeting]     = useState<Meeting | null>(null);
  const [company, setCompany]     = useState<Company | null>(null);
  const [contacts, setContacts]   = useState<Contact[]>([]);
  const [signals, setSignals]     = useState<Signal[]>([]);
  const [accountBrief, setAccountBrief] = useState<string | null>(null);
  const [loadingBrief, setLoadingBrief] = useState(false);

  const [loading, setLoading]         = useState(true);
  const [running, setRunning]         = useState(false);
  const [researchingMore, setResearchingMore] = useState(false);
  const [generatingStory, setGeneratingStory] = useState(false);
  const [scoring, setScoring]         = useState(false);
  const [rawNotes, setRawNotes]       = useState("");
  const [error, setError]             = useState("");
  const [statusMsg, setStatusMsg]     = useState("");
  const [researchGaps, setResearchGaps] = useState<Array<{ key: string; label: string }>>([]);

  // ── Manual linking state ────────────────────────────────────────────────────
  const [showLinkPanel, setShowLinkPanel]   = useState(false);
  const [allCompanies, setAllCompanies]     = useState<Company[]>([]);
  const [allDeals, setAllDeals]             = useState<Deal[]>([]);
  const [allContacts, setAllContacts]       = useState<Contact[]>([]);
  const [linkCompanyId, setLinkCompanyId]   = useState<string>("");
  const [linkDealId, setLinkDealId]         = useState<string>("");
  const [linkSaving, setLinkSaving]         = useState(false);
  // attendee editor
  const [editingAttendees, setEditingAttendees] = useState(false);
  const [attendeeList, setAttendeeList]         = useState<Array<{ contact_id: string; name: string; title?: string; email?: string; role?: string }>>([]);
  const [attendeeSaving, setAttendeeSaving]     = useState(false);

  // ── Load all existing DB data on mount ──────────────────────────────────────
  const loadAll = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const m = await meetingsApi.get(id);
      setMeeting(m);
      setRawNotes(m.raw_notes ?? "");

      if (m.company_id) {
        const [c, cts, sig] = await Promise.all([
          companiesApi.get(m.company_id),
          contactsApi.list(0, 50, m.company_id),
          signalsApi.getCompanySignals(m.company_id),
        ]);
        setCompany(c);
        setContacts(cts);
        setSignals(sig.slice(0, 8));
      }
      // Load research gaps in background — doesn't block page render
      meetingsApi.getResearchGaps(m.id).then(g => setResearchGaps(g.gaps)).catch(() => {});
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(); }, [id]);

  // Web research + demo strategy saved in meeting.research_data / meeting.demo_strategy
  const webResearch = useMemo((): WebResearch => (meeting?.research_data as WebResearch) ?? {}, [meeting]);
  // True once intel has been run at all — regardless of whether web results came back
  const intelWasRun = !!meeting?.research_data;
  const hasWikipedia = !!webResearch.company_background;
  const hasNews = (webResearch.recent_news?.length ?? 0) > 0;
  const hasMilestones = (webResearch.milestones?.length ?? 0) > 0;
  const hasIntentSignals = !!(
    (webResearch.intent_signals?.hiring?.length) ||
    (webResearch.intent_signals?.funding?.length) ||
    (webResearch.intent_signals?.product?.length)
  );
  const hasGoogleNews = (webResearch.google_news?.length ?? 0) > 0;
  const hasHunterContacts = (webResearch.hunter_contacts?.contacts?.length ?? 0) > 0;
  const hasWebsiteAnalysis = !!webResearch.website_analysis && Object.keys(webResearch.website_analysis).length > 0;
  const hasCompetitors = (webResearch.competitive_landscape?.length ?? 0) > 0;
  const hasExecutiveBriefing = !!webResearch.executive_briefing;
  const stakeholderCards = webResearch.attendee_intelligence?.stakeholder_cards ?? [];
  const committeeCoverage = webResearch.attendee_intelligence?.committee_coverage;
  const whyNowSignals = webResearch.why_now_signals ?? [];
  const meetingRecommendations = webResearch.meeting_recommendations ?? [];
  const hasMeetingReadiness = stakeholderCards.length > 0 || whyNowSignals.length > 0 || meetingRecommendations.length > 0;
  const readinessStats = [
    {
      label: "Committee Coverage",
      value: `${committeeCoverage?.coverage_score ?? 0}%`,
      hint: "Buying group mapped",
      tone: (committeeCoverage?.coverage_score ?? 0) >= 75 ? "green" : "blue",
    },
    {
      label: "Stakeholders",
      value: String(stakeholderCards.length || contacts.length),
      hint: "People in motion",
      tone: "blue",
    },
    {
      label: "Why Now",
      value: String(whyNowSignals.length),
      hint: "Timing signals found",
      tone: whyNowSignals.length > 0 ? "orange" : "neutral",
    },
    {
      label: "Signals",
      value: String(signals.length),
      hint: "CRM and web alerts",
      tone: signals.length > 0 ? "orange" : "neutral",
    },
  ] as const;

  // ── Fetch account brief from intelligence endpoint ──────────────────────────
  const handleGetAccountBrief = async () => {
    if (!company) return;
    setLoadingBrief(true);
    try {
      const r = await intelligenceApi.getAccountBrief(company.id);
      setAccountBrief(r.brief ?? "No brief generated.");
    } catch {
      setAccountBrief("Failed to generate brief — check API.");
    } finally {
      setLoadingBrief(false);
    }
  };

  // ── Run web research only (Wikipedia + DDG news/milestones) ─────────────────
  const handleRunIntelligence = async () => {
    if (!id) return;
    setRunning(true);
    setStatusMsg("Running full intel: website, news, Hunter, signals, competitors…");
    setError("");
    try {
      await meetingsApi.runIntelligence(id);
      await loadAll();
      setStatusMsg("Web intel saved ✓");
      setTimeout(() => setStatusMsg(""), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Intelligence run failed");
      setStatusMsg("");
    } finally {
      setRunning(false);
    }
  };

  // ── Research More: fill gaps in enrichment_cache ────────────────────────────
  const handleResearchMore = async () => {
    if (!id) return;
    setResearchingMore(true);
    setError("");
    setStatusMsg(`Researching ${researchGaps.length} gaps…`);
    try {
      const result = await meetingsApi.researchMore(id);
      await loadAll();
      const filledCount = result.filled?.length ?? 0;
      setStatusMsg(filledCount > 0 ? `Filled ${filledCount} gap${filledCount !== 1 ? "s" : ""} ✓` : "Nothing new found");
      setTimeout(() => setStatusMsg(""), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Research more failed");
      setStatusMsg("");
    } finally {
      setResearchingMore(false);
    }
  };

  // ── Generate GPT-4o demo strategy (reads cached research_data) ──────────────
  const handleGenerateDemoStrategy = async () => {
    if (!id) return;
    setGeneratingStory(true);
    setError("");
    try {
      await meetingsApi.generateDemoStrategy(id);
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Demo strategy generation failed");
    } finally {
      setGeneratingStory(false);
    }
  };

  // ── Post-debrief scoring ─────────────────────────────────────────────────────
  const handlePostScore = async () => {
    if (!id) return;
    if (!rawNotes.trim()) { setError("Paste raw meeting notes before scoring."); return; }
    setScoring(true); setError("");
    try {
      await meetingsApi.postScore(id, rawNotes);
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to score meeting");
    } finally {
      setScoring(false);
    }
  };

  // ── Open link panel: load companies + deals once ────────────────────────────
  const handleOpenLinkPanel = async () => {
    setLinkCompanyId(meeting?.company_id ?? "");
    setLinkDealId(meeting?.deal_id ?? "");
    if (allCompanies.length === 0) {
      const [cs, ds] = await Promise.all([companiesApi.list(), dealsApi.list(0, 300)]);
      setAllCompanies(cs);
      setAllDeals(ds);
    }
    setShowLinkPanel(true);
  };

  const handleSaveLink = async () => {
    if (!id) return;
    setLinkSaving(true);
    try {
      const payload: Record<string, string | null> = {};
      if (linkCompanyId !== (meeting?.company_id ?? "")) payload.company_id = linkCompanyId || null;
      if (linkDealId !== (meeting?.deal_id ?? "")) payload.deal_id = linkDealId || null;
      if (Object.keys(payload).length > 0) {
        await meetingsApi.update(id, payload as any);
        await loadAll();
      }
      setShowLinkPanel(false);
    } finally {
      setLinkSaving(false);
    }
  };

  // ── Open attendee editor: load contacts for linked company ───────────────────
  const handleOpenAttendeeEditor = async () => {
    const existing = Array.isArray(meeting?.attendees) ? meeting!.attendees as any[] : [];
    setAttendeeList(existing.map((a: any) => ({
      contact_id: a.contact_id ?? "",
      name: a.name ?? "",
      title: a.title ?? "",
      email: a.email ?? "",
      role: a.role ?? "attendee",
    })));
    if (meeting?.company_id && allContacts.length === 0) {
      const cs = await contactsApi.list(0, 100, meeting.company_id);
      setAllContacts(cs);
    }
    setEditingAttendees(true);
  };

  const handleAddAttendee = (contact: Contact) => {
    if (attendeeList.some(a => a.contact_id === contact.id)) return;
    setAttendeeList(prev => [...prev, {
      contact_id: contact.id,
      name: `${contact.first_name ?? ""} ${contact.last_name ?? ""}`.trim(),
      title: contact.title ?? "",
      email: contact.email ?? "",
      role: "attendee",
    }]);
  };

  const handleRemoveAttendee = (contactId: string) => {
    setAttendeeList(prev => prev.filter(a => a.contact_id !== contactId));
  };

  const handleSaveAttendees = async () => {
    if (!id) return;
    setAttendeeSaving(true);
    try {
      await meetingsApi.update(id, { attendees: attendeeList } as any);
      await loadAll();
      setEditingAttendees(false);
    } finally {
      setAttendeeSaving(false);
    }
  };

  if (loading) return <div className="crm-panel p-14 text-center crm-muted">Loading meeting workspace...</div>;
  if (!meeting) return <div className="crm-panel p-14 text-center crm-muted">Meeting not found.</div>;

  const techStack = company?.tech_stack as Record<string, string> | null;

  // ── Parse enrichment_cache directly — shown immediately, no intel run needed ──
  const ec = (company?.enrichment_cache ?? {}) as Record<string, any>;
  const _unwrapEc = (key: string) => {
    const entry = ec[key];
    if (entry && typeof entry === "object" && "data" in entry) return entry.data;
    return entry ?? null;
  };
  // icp_analysis cache shape: { data: { conversation_starter, beacon_angle, ... }, analyzed_at, ... }
  // _unwrapEc already pulls .data out, so cachedIcp IS the ICP fields object directly.
  const icpData = _unwrapEc("icp_analysis") as Record<string, any> | null;
  const cachedAiSummary = _unwrapEc("ai_summary") as Record<string, any> | null;
  const cachedIntent = _unwrapEc("intent_signals") as Record<string, any> | null;
  const cachedHunterContacts = _unwrapEc("hunter_contacts") as any;
  const cachedCompetitive = ec["competitive_landscape_v2"] as any[] | null;
  const cachedHunterCompany = _unwrapEc("hunter_company") as Record<string, any> | null;
  const cachedGoogleNews = _unwrapEc("google_news") as Array<{ title: string; url: string; published?: string; source?: string }> | null;

  const icpPainPoints: string[] = icpData?.pain_points ?? cachedAiSummary?.pain_points ?? [];
  const icpTalkingPoints: string[] = cachedAiSummary?.talking_points ?? [];
  const icpBeaconAngle: string | null = icpData?.beacon_angle ?? null;
  const icpConversationStarter: string | null = icpData?.conversation_starter ?? null;
  const icpWhyNow: string | null = icpData?.why_now ?? null;
  const icpRecommendedApproach: string | null = icpData?.recommended_outreach_strategy ?? null;
  const icpHiringSignals: string[] = (cachedIntent?.ps_hiring ?? [])
    .filter((s: any) => typeof s === "object" && s?.title)
    .map((s: any) => s.title as string)
    .slice(0, 8);
  const icpFunding: string | null = (cachedIntent?.funding ?? [])[0]?.snippet ?? null;
  const icpCompetitors: Array<{ name: string; summary?: string }> = (cachedCompetitive ?? [])
    .filter((c: any) => typeof c === "object")
    .map((c: any) => ({ name: c.name ?? c.competitor ?? "", summary: c.summary ?? "" }));
  // hunter_contacts.data may be a flat array OR {contacts:[]} shape — handle both
  const icpHunterContacts: Array<{ first_name?: string; last_name?: string; title?: string; email?: string; linkedin_url?: string; seniority?: string; department?: string; phone_number?: string }> =
    Array.isArray(cachedHunterContacts) ? cachedHunterContacts.filter((c: any) => typeof c === "object") :
    (Array.isArray(cachedHunterContacts?.contacts) ? cachedHunterContacts.contacts.filter((c: any) => typeof c === "object") : []);

  const hasIcpData = !!(icpBeaconAngle || icpConversationStarter || icpPainPoints.length || icpTalkingPoints.length);

  return (
    <div className="meeting-detail-page" style={{ display: "flex", flexDirection: "column", gap: 24 }}>

      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {meeting.deal_id ? (
            <button className="crm-button soft" onClick={() => navigate(`/pipeline?deal=${meeting.deal_id}`)}>
              <ArrowLeft size={14} /> Back to Deal
            </button>
          ) : (
            <button className="crm-button soft" onClick={() => navigate("/meetings")}>
              <ArrowLeft size={14} /> Back to Meetings
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="crm-chip capitalize">{meeting.status} · {meeting.meeting_type}</span>
          <button
            className="crm-button soft text-[#c0392b] border-[#fcc] hover:bg-[#fff5f5]"
            onClick={async () => {
              if (!window.confirm(`Delete meeting "${meeting.title}"? This cannot be undone.`)) return;
              await meetingsApi.delete(id!);
              navigate(meeting.deal_id ? `/pipeline?deal=${meeting.deal_id}` : "/meetings");
            }}
          >
            <Trash2 size={14} />
            Delete
          </button>
        </div>
      </div>

      {/* ── Hero ── */}
      <div className="crm-panel p-8" style={{ padding: 32 }}>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h2 className="text-[28px] font-extrabold text-[#1f2d3d]">{meeting.title}</h2>
            <p className="text-[14px] text-[#647a91] mt-2 flex items-center gap-2 flex-wrap">
              {company ? (
                <>
                  <Link to={`/account-sourcing/${company.id}`} className="font-semibold text-[#24364b] hover:text-[#ff6b35]">
                    {company.name}
                  </Link>
                  ·
                  <a href={`https://${company.domain}`} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 hover:text-[#ff6b35]">
                    {company.domain} <ExternalLink size={12} />
                  </a>
                </>
              ) : <span className="text-[#f59e0b] font-semibold">No company linked</span>}
              {meeting.scheduled_at && <span>· {formatDate(meeting.scheduled_at)}</span>}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {statusMsg && <span className="text-[12px] text-[#ff6b35] font-semibold">{statusMsg}</span>}
            <button className="crm-button soft" onClick={handleOpenAttendeeEditor}>
              <Users size={14} /> Manage Attendees
            </button>
            <button className="crm-button soft" onClick={handleOpenLinkPanel}>
              <Link2 size={14} /> {company ? "Re-link" : "Link Company / Deal"}
            </button>
            <button className="crm-button soft" onClick={handleRunIntelligence} disabled={running}>
              {running ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />}
              {running ? "Searching…" : intelWasRun ? "Re-run Web Intel" : "Run Web Intel"}
            </button>
            <ZippyDocDropdown
              items={[
                {
                  label: "MOM",
                  icon: <FileText size={14} />,
                  message: `Create a MOM for the ${company?.name ?? meeting.title} meeting${meeting.scheduled_at ? ` on ${formatDate(meeting.scheduled_at)}` : ""}. Meeting type: ${meeting.meeting_type ?? "not specified"}.`,
                },
                {
                  label: "PoC Kickoff",
                  icon: <ClipboardList size={14} />,
                  message: `Create a PoC Kickoff document for ${company?.name ?? meeting.title}${meeting.scheduled_at ? `. Meeting date: ${formatDate(meeting.scheduled_at)}` : ""}.`,
                },
                {
                  label: "Business Proposal",
                  icon: <FileText size={14} />,
                  message: `Create a business proposal for ${company?.name ?? meeting.title}.`,
                  separatorBefore: true,
                },
              ]}
            />
          </div>
        </div>

        {/* ── Link Company / Deal panel ── */}
        {showLinkPanel && (
          <div style={{ marginTop: 20, padding: "16px 18px", borderRadius: 14, border: "1px solid #d5e5ff", background: "#f3f8ff" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <span style={{ fontSize: 13, fontWeight: 800, color: "#24364b", display: "flex", alignItems: "center", gap: 6 }}>
                <Link2 size={14} style={{ color: "#1f6feb" }} /> Link Company &amp; Deal
              </span>
              <button type="button" onClick={() => setShowLinkPanel(false)} style={{ background: "none", border: "none", cursor: "pointer", color: "#7a8ea4" }}>
                <X size={14} />
              </button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 700, color: "#546679", textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: 5 }}>Company</label>
                <select
                  value={linkCompanyId}
                  onChange={(e) => setLinkCompanyId(e.target.value)}
                  style={{ width: "100%", height: 36, borderRadius: 9, border: "1px solid #c8d8ee", padding: "0 10px", fontSize: 13, color: "#24364b", background: "#fff" }}
                >
                  <option value="">— No company —</option>
                  {allCompanies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 700, color: "#546679", textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: 5 }}>Deal</label>
                <select
                  value={linkDealId}
                  onChange={(e) => setLinkDealId(e.target.value)}
                  style={{ width: "100%", height: 36, borderRadius: 9, border: "1px solid #c8d8ee", padding: "0 10px", fontSize: 13, color: "#24364b", background: "#fff" }}
                >
                  <option value="">— No deal —</option>
                  {allDeals.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button
                type="button"
                disabled={linkSaving}
                onClick={handleSaveLink}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 9, background: "#1f6feb", border: "1px solid #1f6feb", color: "#fff", fontSize: 13, fontWeight: 700, cursor: "pointer" }}
              >
                <Save size={13} /> {linkSaving ? "Saving…" : "Save"}
              </button>
              <button type="button" onClick={() => setShowLinkPanel(false)}
                style={{ padding: "7px 12px", borderRadius: 9, border: "1px solid #d9e1ec", background: "#fff", color: "#546679", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
                Cancel
              </button>
              {(meeting.company_id || meeting.deal_id) && (
                <button type="button"
                  onClick={async () => {
                    setLinkCompanyId("");
                    setLinkDealId("");
                    await meetingsApi.update(id!, { company_id: null, deal_id: null } as any);
                    await loadAll();
                    setShowLinkPanel(false);
                  }}
                  style={{ display: "inline-flex", alignItems: "center", gap: 5, marginLeft: "auto", padding: "7px 12px", borderRadius: 9, border: "1px solid #fcc", background: "#fff5f5", color: "#c0392b", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                  <Link2Off size={12} /> Unlink all
                </button>
              )}
            </div>
          </div>
        )}

        {/* ── Attendee editor panel ── */}
        {editingAttendees && (
          <div style={{ marginTop: 20, padding: "16px 18px", borderRadius: 14, border: "1px solid #d3f0e2", background: "#f0fdf4" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <span style={{ fontSize: 13, fontWeight: 800, color: "#1a3d2b", display: "flex", alignItems: "center", gap: 6 }}>
                <Users size={14} style={{ color: "#16a34a" }} /> Meeting Attendees
              </span>
              <button type="button" onClick={() => setEditingAttendees(false)} style={{ background: "none", border: "none", cursor: "pointer", color: "#7a8ea4" }}>
                <X size={14} />
              </button>
            </div>

            {/* Current attendee list */}
            {attendeeList.length > 0 ? (
              <div style={{ display: "grid", gap: 6, marginBottom: 12 }}>
                {attendeeList.map((a) => (
                  <div key={a.contact_id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderRadius: 10, background: "#fff", border: "1px solid #c3e6cb" }}>
                    <div>
                      <span style={{ fontSize: 13, fontWeight: 700, color: "#1a3d2b" }}>{a.name}</span>
                      {a.title && <span style={{ fontSize: 12, color: "#4a7a5a", marginLeft: 8 }}>{a.title}</span>}
                      {a.email && <span style={{ fontSize: 11, color: "#7aad8a", marginLeft: 8 }}>{a.email}</span>}
                    </div>
                    <button type="button" onClick={() => handleRemoveAttendee(a.contact_id)}
                      style={{ background: "none", border: "none", cursor: "pointer", color: "#e05050", padding: 4 }}>
                      <X size={13} />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ fontSize: 13, color: "#4a7a5a", marginBottom: 12 }}>No attendees added yet.</p>
            )}

            {/* Add from linked company's contacts */}
            {allContacts.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: "#4a7a5a", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                  Add from {company?.name ?? "linked company"}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {allContacts
                    .filter(c => !attendeeList.some(a => a.contact_id === c.id))
                    .map(c => (
                      <button key={c.id} type="button" onClick={() => handleAddAttendee(c)}
                        style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "5px 10px", borderRadius: 8, border: "1px solid #a8d5b5", background: "#fff", color: "#1a5c34", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
                        <Plus size={11} />
                        {c.first_name} {c.last_name}
                        {c.title && <span style={{ color: "#7aad8a", fontWeight: 400 }}>· {c.title}</span>}
                      </button>
                    ))}
                </div>
              </div>
            )}

            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" disabled={attendeeSaving} onClick={handleSaveAttendees}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 14px", borderRadius: 9, background: "#16a34a", border: "1px solid #16a34a", color: "#fff", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
                <Save size={13} /> {attendeeSaving ? "Saving…" : "Save Attendees"}
              </button>
              <button type="button" onClick={() => setEditingAttendees(false)}
                style={{ padding: "7px 12px", borderRadius: 9, border: "1px solid #c3e6cb", background: "#fff", color: "#4a7a5a", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-4" style={{ gap: 12 }}>
        {readinessStats.map((item) => {
          const tone = item.tone === "green"
            ? { bg: "#eefcf5", border: "#cdeedc", accent: "#1b6f53" }
            : item.tone === "orange"
              ? { bg: "#fff7ef", border: "#ffd9be", accent: "#b05a2a" }
              : item.tone === "blue"
                ? { bg: "#f3f8ff", border: "#d5e5ff", accent: "#24567e" }
                : { bg: "#fbfdff", border: "#e3eaf3", accent: "#6f8399" };
          return (
            <div
              key={item.label}
              className="crm-panel"
              style={{ padding: "16px 18px", background: tone.bg, borderColor: tone.border }}
            >
              <p className="text-[11px] uppercase tracking-wide font-semibold" style={{ color: "#7d8fa3" }}>{item.label}</p>
              <p className="text-[25px] font-extrabold mt-2" style={{ color: tone.accent }}>{item.value}</p>
              <p className="text-[12px] text-[#6f8399] mt-1">{item.hint}</p>
            </div>
          );
        })}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 0a — Pre-meeting prep (company facts + ICP research, always shown)
      ══════════════════════════════════════════════════════════════════════ */}
      {company && (
        <Section title="Meeting Prep" icon={<Briefcase size={15} className="text-[#1f6feb]" />} defaultOpen={true}>
          <div style={{ display: "grid", gap: 20 }}>

            {/* ── Company snapshot ── */}
            <div style={{ display: "grid", gap: 12 }}>
              <p style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#7d8fa3" }}>Company</p>

              {/* Facts row */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {[
                  { label: "HQ", value: company.headquarters },
                  { label: "Region", value: company.region },
                  { label: "Industry", value: company.industry },
                  { label: "Vertical", value: company.vertical },
                  { label: "Employees", value: company.employee_count?.toLocaleString() },
                  { label: "Funding", value: company.funding_stage },
                  { label: "ARR", value: company.arr_estimate ? `$${(company.arr_estimate / 1_000_000).toFixed(1)}M` : null },
                  { label: "ICP", value: company.icp_score != null ? `${company.icp_score} · ${company.icp_tier ?? ""}` : null },
                  { label: "Ownership", value: company.ownership_stage },
                ].filter(f => f.value).map(f => (
                  <div key={f.label} style={{ padding: "6px 12px", borderRadius: 10, background: "#f4f7fb", border: "1px solid #e3eaf3" }}>
                    <span style={{ fontSize: 10, color: "#8fa3ba", fontWeight: 700, textTransform: "uppercase", marginRight: 5 }}>{f.label}</span>
                    <span style={{ fontSize: 12, color: "#2b3f55", fontWeight: 600 }}>{f.value}</span>
                  </div>
                ))}
              </div>

              {/* Description */}
              {company.description && (
                <p style={{ fontSize: 13, color: "#3d5268", lineHeight: 1.7, padding: "12px 16px", background: "#f8fafc", borderRadius: 12, border: "1px solid #e8edf5", margin: 0 }}>
                  {company.description}
                </p>
              )}

              {/* Tech stack */}
              {techStack && Object.keys(techStack).length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {Object.entries(techStack).map(([cat, tool]) => (
                    <span key={cat} style={{ fontSize: 11, padding: "3px 10px", borderRadius: 999, background: "#eef2ff", border: "1px solid #c7d2fe", color: "#3730a3", fontWeight: 600 }}>
                      {cat}: {String(tool)}
                    </span>
                  ))}
                </div>
              )}

              {/* Investors */}
              {(company.pe_investors || company.vc_investors || company.strategic_investors) && (
                <div style={{ fontSize: 12, color: "#546679", padding: "10px 14px", background: "#f8fafc", borderRadius: 10, border: "1px solid #e3eaf3" }}>
                  {company.pe_investors && <p style={{ margin: "0 0 2px" }}><span style={{ fontWeight: 700, color: "#2b3f55" }}>PE:</span> {company.pe_investors}</p>}
                  {company.vc_investors && <p style={{ margin: "0 0 2px" }}><span style={{ fontWeight: 700, color: "#2b3f55" }}>VC:</span> {company.vc_investors}</p>}
                  {company.strategic_investors && <p style={{ margin: 0 }}><span style={{ fontWeight: 700, color: "#2b3f55" }}>Strategic:</span> {company.strategic_investors}</p>}
                </div>
              )}
            </div>

            {/* ── Divider ── */}
            {(company.beacon_angle || company.why_now || company.account_thesis || hasIcpData) && (
              <div style={{ borderTop: "1px solid #e8edf5" }} />
            )}

            {/* ── Sales prep from ICP research (direct company fields first) ── */}
            {(company.beacon_angle || company.why_now || company.account_thesis || icpConversationStarter) && (
              <div style={{ display: "grid", gap: 12 }}>
                <p style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#7d8fa3", margin: 0 }}>Sales Prep</p>

                {/* Open with + Beacon angle */}
                <div style={{ display: "grid", gridTemplateColumns: icpConversationStarter && (company.beacon_angle || company.why_now) ? "1fr 1fr" : "1fr", gap: 12 }}>
                  {icpConversationStarter && (
                    <div style={{ padding: "14px 16px", borderRadius: 12, background: "linear-gradient(135deg, #f0f7ff, #e8f2ff)", border: "1px solid #c7dcf8" }}>
                      <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#1d4ed8", marginBottom: 6 }}>Open With This</p>
                      <p style={{ fontSize: 13, color: "#1e3a5f", lineHeight: 1.65, fontStyle: "italic", margin: 0 }}>"{icpConversationStarter}"</p>
                    </div>
                  )}
                  {(company.beacon_angle || company.why_now) && (
                    <div style={{ padding: "14px 16px", borderRadius: 12, background: "linear-gradient(135deg, #fff8f4, #fff3ec)", border: "1px solid #ffd5be" }}>
                      <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#c2410c", marginBottom: 6 }}>Beacon Angle</p>
                      <p style={{ fontSize: 13, color: "#431407", lineHeight: 1.65, margin: 0 }}>{company.beacon_angle || icpBeaconAngle}</p>
                    </div>
                  )}
                </div>

                {/* Why now + Account thesis */}
                {(company.why_now || company.account_thesis) && (
                  <div style={{ display: "grid", gridTemplateColumns: company.why_now && company.account_thesis ? "1fr 1fr" : "1fr", gap: 12 }}>
                    {company.why_now && (
                      <div style={{ padding: "14px 16px", borderRadius: 12, background: "linear-gradient(135deg, #fffbeb, #fef9e7)", border: "1px solid #fde68a" }}>
                        <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#92400e", marginBottom: 6 }}>Why Now</p>
                        <p style={{ fontSize: 13, color: "#78350f", lineHeight: 1.65, margin: 0 }}>{company.why_now}</p>
                      </div>
                    )}
                    {company.account_thesis && (
                      <div style={{ padding: "14px 16px", borderRadius: 12, background: "linear-gradient(135deg, #f5f3ff, #ede9fe)", border: "1px solid #ddd6fe" }}>
                        <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#6d28d9", marginBottom: 6 }}>Account Thesis</p>
                        <p style={{ fontSize: 13, color: "#4c1d95", lineHeight: 1.65, margin: 0 }}>{company.account_thesis}</p>
                      </div>
                    )}
                  </div>
                )}

                {/* Pain points */}
                {icpPainPoints.length > 0 && (
                  <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fafafa", border: "1px solid #e8edf5" }}>
                    <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#b91c1c", marginBottom: 8 }}>Pain Points to Address</p>
                    <div style={{ display: "grid", gap: 5 }}>
                      {icpPainPoints.map((p, i) => (
                        <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                          <span style={{ color: "#ef4444", fontWeight: 700, flexShrink: 0, marginTop: 1 }}>·</span>
                          <p style={{ fontSize: 13, color: "#374151", lineHeight: 1.6, margin: 0 }}>{p}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Talking points */}
                {icpTalkingPoints.length > 0 && (
                  <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fafafa", border: "1px solid #e8edf5" }}>
                    <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#15803d", marginBottom: 8 }}>Talking Points</p>
                    <div style={{ display: "grid", gap: 5 }}>
                      {icpTalkingPoints.map((t, i) => (
                        <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                          <span style={{ color: "#22c55e", fontWeight: 700, flexShrink: 0, marginTop: 1 }}>✓</span>
                          <p style={{ fontSize: 13, color: "#374151", lineHeight: 1.6, margin: 0 }}>{t}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Intent signals row ── */}
            {(icpHiringSignals.length > 0 || icpFunding || icpCompetitors.length > 0) && (
              <>
                <div style={{ borderTop: "1px solid #e8edf5" }} />
                <div style={{ display: "grid", gap: 12 }}>
                  <p style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#7d8fa3", margin: 0 }}>Signals</p>

                  <div style={{ display: "grid", gridTemplateColumns: icpCompetitors.length > 0 && icpHiringSignals.length > 0 ? "1fr 1fr" : "1fr", gap: 12 }}>
                    {/* Hiring */}
                    {icpHiringSignals.length > 0 && (
                      <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fffbeb", border: "1px solid #fde68a" }}>
                        <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#92400e", marginBottom: 8 }}>
                          Hiring {icpHiringSignals.length} roles — buying signal
                        </p>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                          {icpHiringSignals.map((r, i) => (
                            <span key={i} style={{ fontSize: 11, padding: "2px 9px", borderRadius: 999, background: "#fff", border: "1px solid #fde68a", color: "#92400e", fontWeight: 600 }}>{r}</span>
                          ))}
                        </div>
                        {icpFunding && <p style={{ fontSize: 12, color: "#78350f", marginTop: 8, marginBottom: 0 }}><span style={{ fontWeight: 700 }}>Funding:</span> {icpFunding}</p>}
                      </div>
                    )}

                    {/* Competitors */}
                    {icpCompetitors.length > 0 && (
                      <div style={{ padding: "14px 16px", borderRadius: 12, background: "#fff5f5", border: "1px solid #fecaca" }}>
                        <p style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", color: "#b91c1c", marginBottom: 8 }}>In Evaluation</p>
                        <div style={{ display: "grid", gap: 6 }}>
                          {icpCompetitors.slice(0, 4).map((c, i) => (
                            <div key={i} style={{ paddingLeft: 8, borderLeft: "2px solid #fca5a5" }}>
                              <p style={{ fontSize: 12, fontWeight: 700, color: "#991b1b", margin: 0 }}>{c.name}</p>
                              {c.summary && <p style={{ fontSize: 11, color: "#7f1d1d", lineHeight: 1.4, margin: "2px 0 0" }}>{c.summary.slice(0, 100)}{c.summary.length > 100 ? "…" : ""}</p>}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}

            {/* ── Hunter contacts ── */}
            {icpHunterContacts.length > 0 && (
              <>
                <div style={{ borderTop: "1px solid #e8edf5" }} />
                <div style={{ display: "grid", gap: 10 }}>
                  <p style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#7d8fa3", margin: 0 }}>Contacts Found ({icpHunterContacts.length})</p>
                  <div style={{ display: "grid", gap: 6 }}>
                    {icpHunterContacts.slice(0, 6).map((c, i) => (
                      <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", borderRadius: 10, background: "#f8fafc", border: "1px solid #e8edf5" }}>
                        <div style={{ width: 34, height: 34, borderRadius: "50%", background: "#e0e7ff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, color: "#3730a3", flexShrink: 0 }}>
                          {(c.first_name?.[0] ?? "") + (c.last_name?.[0] ?? "")}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                            <p style={{ fontSize: 13, fontWeight: 700, color: "#1e3a5f", margin: 0 }}>{c.first_name} {c.last_name}</p>
                            {c.seniority && (
                              <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 999, background: "#ede9fe", border: "1px solid #ddd6fe", color: "#5b21b6", fontWeight: 700, textTransform: "capitalize" }}>{c.seniority}</span>
                            )}
                            {c.department && (
                              <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 999, background: "#f0fdf4", border: "1px solid #bbf7d0", color: "#15803d", fontWeight: 700, textTransform: "capitalize" }}>{c.department}</span>
                            )}
                          </div>
                          {c.title && <p style={{ fontSize: 11, color: "#64748b", margin: "2px 0 0" }}>{c.title}</p>}
                          {c.email && <p style={{ fontSize: 11, color: "#94a3b8", margin: "1px 0 0" }}>{c.email}</p>}
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, flexShrink: 0, alignItems: "flex-end" }}>
                          {c.linkedin_url && (
                            <a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 11, color: "#0077b5", fontWeight: 600, textDecoration: "none", display: "flex", alignItems: "center", gap: 3 }}>
                              LinkedIn <ExternalLink size={10} />
                            </a>
                          )}
                          {c.phone_number && (
                            <span style={{ fontSize: 11, color: "#64748b" }}>{String(c.phone_number)}</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}

            {/* ── Hunter company firmographics from cache ── */}
            {cachedHunterCompany && (
              <>
                <div style={{ borderTop: "1px solid #e8edf5" }} />
                <div style={{ display: "grid", gap: 10 }}>
                  <p style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#7d8fa3", margin: 0 }}>Firmographics</p>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {[
                      { label: "Size", value: cachedHunterCompany.size },
                      { label: "Revenue", value: cachedHunterCompany.annual_revenue ? `$${(Number(cachedHunterCompany.annual_revenue) / 1_000_000).toFixed(1)}M` : null },
                      { label: "Country", value: cachedHunterCompany.country },
                      { label: "Founded", value: cachedHunterCompany.founded_year },
                      { label: "Type", value: cachedHunterCompany.type },
                    ].filter(f => f.value).map(f => (
                      <div key={f.label} style={{ padding: "5px 11px", borderRadius: 10, background: "#f0f7ff", border: "1px solid #c7dcf8" }}>
                        <span style={{ fontSize: 10, color: "#5a7a99", fontWeight: 700, textTransform: "uppercase", marginRight: 5 }}>{f.label}</span>
                        <span style={{ fontSize: 12, color: "#1e3a5f", fontWeight: 600 }}>{String(f.value)}</span>
                      </div>
                    ))}
                  </div>
                  {/* Technologies from Hunter */}
                  {Array.isArray(cachedHunterCompany.technologies) && cachedHunterCompany.technologies.length > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                      {(cachedHunterCompany.technologies as string[]).slice(0, 10).map((t, i) => (
                        <span key={i} style={{ fontSize: 11, padding: "2px 9px", borderRadius: 999, background: "#f5f3ff", border: "1px solid #ddd6fe", color: "#5b21b6", fontWeight: 600 }}>{t}</span>
                      ))}
                    </div>
                  )}
                  {/* Social links */}
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    {cachedHunterCompany.linkedin_url && (
                      <a href={String(cachedHunterCompany.linkedin_url)} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: "#0077b5", fontWeight: 600, textDecoration: "none", display: "flex", alignItems: "center", gap: 3 }}>
                        LinkedIn <ExternalLink size={10} />
                      </a>
                    )}
                    {cachedHunterCompany.twitter && (
                      <a href={`https://twitter.com/${cachedHunterCompany.twitter}`} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: "#1d9bf0", fontWeight: 600, textDecoration: "none", display: "flex", alignItems: "center", gap: 3 }}>
                        Twitter <ExternalLink size={10} />
                      </a>
                    )}
                  </div>
                </div>
              </>
            )}

            {/* ── Cached Google News ── */}
            {cachedGoogleNews && cachedGoogleNews.length > 0 && (
              <>
                <div style={{ borderTop: "1px solid #e8edf5" }} />
                <div style={{ display: "grid", gap: 8 }}>
                  <p style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#7d8fa3", margin: 0 }}>Recent News</p>
                  {cachedGoogleNews.slice(0, 4).map((item, i) => (
                    <a key={i} href={item.url} target="_blank" rel="noopener noreferrer"
                      style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "8px 12px", borderRadius: 10, background: "#f8fafc", border: "1px solid #e8edf5", textDecoration: "none" }}>
                      <Newspaper size={13} style={{ color: "#7d8fa3", marginTop: 2, flexShrink: 0 }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <p style={{ fontSize: 13, fontWeight: 600, color: "#1e3a5f", margin: 0, lineHeight: 1.4 }}>{item.title}</p>
                        {(item.source || item.published) && (
                          <p style={{ fontSize: 11, color: "#94a3b8", margin: "2px 0 0" }}>
                            {item.source}{item.source && item.published ? " · " : ""}{item.published ? new Date(item.published).toLocaleDateString() : ""}
                          </p>
                        )}
                      </div>
                      <ExternalLink size={10} style={{ color: "#94a3b8", flexShrink: 0, marginTop: 3 }} />
                    </a>
                  ))}
                </div>
              </>
            )}

            {/* ── Research More CTA ── */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "12px 16px", borderRadius: 12, background: "#f4f7fb", border: "1px dashed #c9daf0" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ fontSize: 12, color: "#5a7a99", margin: 0, lineHeight: 1.5 }}>
                  {intelWasRun
                    ? "Full intel was run — scroll down for the AI executive briefing, live news, and stakeholder talk tracks."
                    : researchGaps.length > 0
                      ? `${researchGaps.length} data gap${researchGaps.length !== 1 ? "s" : ""} found — fetch missing firmographics, contacts, news, and more.`
                      : "All available data has been fetched. Run full Web Intel for an AI executive briefing."}
                </p>
                {researchGaps.length > 0 && !researchingMore && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 6 }}>
                    {researchGaps.map(g => (
                      <span key={g.key} style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "#fff3cd", border: "1px solid #fde68a", color: "#92400e", fontWeight: 600 }}>{g.label}</span>
                    ))}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                {researchGaps.length > 0 && (
                  <button className="crm-button" onClick={handleResearchMore} disabled={researchingMore || running}>
                    {researchingMore ? <RefreshCw size={13} className="animate-spin" /> : <Search size={13} />}
                    {researchingMore ? "Researching…" : `Research More (${researchGaps.length})`}
                  </button>
                )}
                {!intelWasRun && (
                  <button className="crm-button soft" onClick={handleRunIntelligence} disabled={running || researchingMore}>
                    {running ? <RefreshCw size={13} className="animate-spin" /> : <Sparkles size={13} />}
                    {running ? "Running…" : "Full Web Intel"}
                  </button>
                )}
              </div>
            </div>

          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 0 — Executive Briefing (GPT-4o synthesis of all intel)
      ══════════════════════════════════════════════════════════════════════ */}
      {hasExecutiveBriefing && (
        <Section title="Executive Briefing" icon={<Briefcase size={15} className="text-[#ff6b35]" />} badge="AI">
          <div className="rounded-xl border border-[#ffd5be] bg-[#fff8f5] p-5">
            <div className="prose prose-sm max-w-none text-[14px] text-[#2d4258] leading-relaxed whitespace-pre-wrap">
              {webResearch.executive_briefing!.split(/\n(?=##\s)/).map((block, i) => {
                const lines = block.trim().split("\n");
                const heading = lines[0]?.replace(/^##\s*/, "");
                const body = lines.slice(1).join("\n").trim();
                return (
                  <div key={i} className={i > 0 ? "mt-4" : ""}>
                    {heading && (
                      <p className="text-[12px] uppercase tracking-wide font-bold text-[#b05a2a] mb-1.5">{heading}</p>
                    )}
                    <div className="text-[13px] text-[#3d5268] leading-relaxed">
                      {body.split("\n").map((line, j) => (
                        <p key={j} className={line.startsWith("-") || line.startsWith("•") ? "pl-3" : ""}>{line}</p>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Section>
      )}

      {hasMeetingReadiness && (
        <Section title="Meeting Readiness" icon={<Target size={15} className="text-[#2563eb]" />} badge="Prep">
          <div className="space-y-5" style={{ rowGap: 18, display: "grid" }}>
            {committeeCoverage && (
              <div className="rounded-xl border border-[#dbe7f5] bg-[#f7fbff] p-4">
                <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-[#6e88a5] font-semibold">Committee Coverage</p>
                    <p className="text-[14px] font-bold text-[#24364b] mt-1">
                      {committeeCoverage.coverage_score ?? 0}% of the core buying group is covered
                    </p>
                  </div>
                  {typeof committeeCoverage.coverage_score === "number" && (
                    <span className="crm-chip tabular">{committeeCoverage.coverage_score}%</span>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  {(committeeCoverage.discovered_roles ?? []).map((item) => (
                    <span key={item.role} className="inline-flex items-center rounded-full border border-[#c7daf0] bg-white px-3 py-1 text-[11px] font-semibold text-[#2d5f8e]">
                      {item.label}
                    </span>
                  ))}
                  {(committeeCoverage.meeting_gaps ?? []).map((item) => (
                    <span key={item.role} className="inline-flex items-center rounded-full border border-[#ffd6c7] bg-[#fff5f0] px-3 py-1 text-[11px] font-semibold text-[#b4532a]">
                      Missing: {item.label}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {whyNowSignals.length > 0 && (
              <div>
                <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-2">Why Now</p>
                <div className="grid gap-3 md:grid-cols-2" style={{ gap: 12 }}>
                  {whyNowSignals.map((item, i) => (
                    <div key={`${item.title}-${i}`} className="rounded-xl border border-[#e3eaf3] bg-white px-4 py-3">
                      <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold">{item.title}</p>
                      <p className="text-[13px] text-[#2d4258] leading-relaxed mt-1">{item.detail}</p>
                      {item.url && (
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-[#4a7fa5] hover:underline"
                        >
                          Source <ExternalLink size={10} />
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {stakeholderCards.length > 0 && (
              <div>
                <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
                  <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold">Stakeholder Focus</p>
                  <span className="text-[11px] text-[#7d8fa3]">
                    {webResearch.attendee_intelligence?.has_explicit_attendees
                      ? "Using saved meeting attendees plus suggested gaps"
                      : "No attendees saved yet, using best-fit stakeholder recommendations"}
                  </span>
                </div>
                <div className="grid gap-3 md:grid-cols-2" style={{ gap: 12 }}>
                  {stakeholderCards.map((card, i) => {
                    const personaKey = canonicalPersona(card.persona);
                    const ps = PERSONA_STYLE[personaKey];
                    return (
                      <div key={`${card.name}-${i}`} className="rounded-xl border border-[#e1e8f1] bg-white p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="flex items-center gap-2 flex-wrap">
                              <p className="text-[14px] font-bold text-[#24364b]">{card.name}</p>
                              <span
                                className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold"
                                style={{ color: ps.color, background: ps.bg, border: `1px solid ${ps.border}` }}
                              >
                                {PERSONA_LABEL[personaKey]}
                              </span>
                              {card.status && (
                                <span className="inline-flex items-center px-2 py-0.5 rounded-full border border-[#d7e2ee] bg-[#f8fbff] text-[10px] font-bold text-[#61788f] capitalize">
                                  {card.status}
                                </span>
                              )}
                            </div>
                            <p className="text-[12px] text-[#6f8399] mt-1">{card.title ?? card.role_label ?? "Stakeholder"}</p>
                          </div>
                          {card.linkedin_url && (
                            <a href={card.linkedin_url} target="_blank" rel="noopener noreferrer" className="text-[11px] text-[#0077b5] font-semibold hover:underline shrink-0">
                              LinkedIn <ExternalLink size={10} />
                            </a>
                          )}
                        </div>
                        {card.likely_focus && (
                          <p className="text-[12px] text-[#30465d] leading-relaxed mt-3">
                            <span className="font-bold text-[#24364b]">Focus:</span> {card.likely_focus}
                          </p>
                        )}
                        {card.talk_track && (
                          <p className="text-[12px] text-[#30465d] leading-relaxed mt-2">
                            <span className="font-bold text-[#24364b]">Talk track:</span> {card.talk_track}
                          </p>
                        )}
                        {(card.questions_to_ask?.length ?? 0) > 0 && (
                          <div className="mt-3">
                            <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-1">Questions To Ask</p>
                            <div className="space-y-1">
                              {card.questions_to_ask!.map((question, idx) => (
                                <p key={idx} className="text-[12px] text-[#41556b] leading-relaxed">• {question}</p>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {meetingRecommendations.length > 0 && (
              <div className="rounded-xl border border-[#ffe0cf] bg-[#fff8f4] p-4">
                <p className="text-[11px] uppercase tracking-wide text-[#b05a2a] font-semibold mb-2">Recommended Meeting Plan</p>
                <div className="space-y-1.5">
                  {meetingRecommendations.map((item, i) => (
                    <p key={i} className="text-[13px] text-[#3d5268] leading-relaxed">• {item}</p>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 1 — Account Intelligence (existing DB data, shown immediately)
      ══════════════════════════════════════════════════════════════════════ */}
      <Section title="Account Intelligence" icon={<Building2 size={15} className="text-[#ff6b35]" />}>
        {!company ? (
          <p className="text-[13px] text-[#6f8399]">No company linked to this meeting.</p>
        ) : (
          <div className="space-y-5" style={{ rowGap: 18, display: "grid" }}>

            {/* Company facts grid — from DB, instant */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3" style={{ gap: 12 }}>
              {[
                { label: "Industry",      value: company.industry },
                { label: "Employees",     value: company.employee_count?.toLocaleString() },
                { label: "ARR Estimate",  value: formatCurrency(company.arr_estimate) },
                { label: "Funding Stage", value: company.funding_stage },
                { label: "ICP Score",     value: company.icp_score != null ? `${company.icp_score} (${company.icp_tier})` : null },
                { label: "DAP Tool",      value: company.has_dap ? (company.dap_tool ?? "Yes — unknown tool") : "No DAP detected" },
              ].filter(i => i.value).map(item => (
                <div key={item.label} className="rounded-xl border border-[#e3eaf3] bg-[#f9fbfe] px-4 py-3" style={{ padding: "13px 15px" }}>
                  <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold">{item.label}</p>
                  <p className="text-[13px] font-bold text-[#2b3f55] mt-1 capitalize">{item.value}</p>
                </div>
              ))}
            </div>

            {/* ICP Research snapshot — pain points, talking points, beacon angle from enrichment_cache */}
            {webResearch.company_snapshot && (webResearch.company_snapshot.pain_points?.length || webResearch.company_snapshot.beacon_angle || webResearch.company_snapshot.conversation_starter || webResearch.company_snapshot.talking_points?.length) && (
              <div className="rounded-xl border border-[#e0edff] bg-[#f5f9ff] p-4" style={{ display: "grid", gap: 12 }}>
                <p className="text-[11px] uppercase tracking-wide text-[#24567e] font-semibold">From ICP Research</p>
                {webResearch.company_snapshot.conversation_starter && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-1">Conversation Starter</p>
                    <p className="text-[13px] text-[#2d4258] leading-relaxed italic">"{webResearch.company_snapshot.conversation_starter}"</p>
                  </div>
                )}
                {webResearch.company_snapshot.beacon_angle && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-1">Beacon Angle</p>
                    <p className="text-[13px] text-[#2d4258] leading-relaxed">{webResearch.company_snapshot.beacon_angle}</p>
                  </div>
                )}
                {webResearch.company_snapshot.pain_points && webResearch.company_snapshot.pain_points.length > 0 && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-1">Pain Points</p>
                    <div style={{ display: "grid", gap: 4 }}>
                      {webResearch.company_snapshot.pain_points.map((p, i) => (
                        <p key={i} className="text-[13px] text-[#3d5268] leading-relaxed">• {p}</p>
                      ))}
                    </div>
                  </div>
                )}
                {webResearch.company_snapshot.talking_points && webResearch.company_snapshot.talking_points.length > 0 && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-1">Talking Points</p>
                    <div style={{ display: "grid", gap: 4 }}>
                      {webResearch.company_snapshot.talking_points.map((t, i) => (
                        <p key={i} className="text-[13px] text-[#3d5268] leading-relaxed">• {t}</p>
                      ))}
                    </div>
                  </div>
                )}
                {webResearch.company_snapshot.why_now_summary && (
                  <div>
                    <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-1">Why Now</p>
                    <p className="text-[13px] text-[#2d4258] leading-relaxed">{webResearch.company_snapshot.why_now_summary}</p>
                  </div>
                )}
              </div>
            )}

            {/* Tech stack — from DB */}
            {techStack && Object.keys(techStack).length > 0 && (
              <div>
                <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-2">Tech Stack</p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(techStack).map(([name, tool]) => (
                    <span key={name} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-[#d8e3ee] bg-white text-[12px]">
                      <span className="text-[#7a8ea4] capitalize">{name}:</span>
                      <span className="font-semibold text-[#2e4358]">{tool}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Company background — scraped from own website + GPT-4o */}
            {webResearch.company_background && (
              <div className="rounded-xl border border-[#dce6f0] bg-[#f8fbff] p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold">Company Background</p>
                  {webResearch.company_background.url && (
                    <a href={webResearch.company_background.url} target="_blank" rel="noopener noreferrer"
                      className="text-[11px] text-[#4a7fa5] hover:underline flex items-center gap-1">
                      Source <ExternalLink size={10} />
                    </a>
                  )}
                </div>
                {webResearch.company_background.description && (
                  <p className="text-[12px] text-[#546679] mb-1">{webResearch.company_background.description}</p>
                )}
                <p className="text-[14px] text-[#2d4258] leading-relaxed">{webResearch.company_background.extract}</p>
                {webResearch.company_background.founded && (
                  <span className="inline-flex items-center mt-2 px-2 py-0.5 rounded-full bg-[#edf5ff] border border-[#c9e0f8] text-[11px] text-[#24567e] font-semibold">
                    Founded {webResearch.company_background.founded}
                  </span>
                )}
              </div>
            )}

            {/* Recent news — from Run Intel */}
            {(webResearch.recent_news?.length ?? 0) > 0 && (
              <div>
                <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-2">
                  Recent News & Signals <span className="text-[#ff6b35] normal-case font-normal">(web research)</span>
                </p>
                <div className="space-y-2" style={{ rowGap: 10, display: "grid" }}>
                  {webResearch.recent_news!.map((item, i) => (
                    <a key={i} href={item.url} target="_blank" rel="noopener noreferrer"
                      className="flex items-start gap-3 rounded-xl border border-[#e3eaf3] bg-white px-4 py-3 hover:border-[#ff6b35] transition-colors">
                      <Newspaper size={13} className="text-[#9eb0c3] mt-0.5 shrink-0" />
                      <div className="min-w-0">
                        <p className="text-[13px] font-semibold text-[#24364b]">{item.title}</p>
                        {item.snippet && <p className="text-[12px] text-[#6f8399] mt-0.5 line-clamp-2">{item.snippet}</p>}
                      </div>
                      <ExternalLink size={11} className="text-[#9eb0c3] shrink-0 mt-0.5" />
                    </a>
                  ))}
                </div>
              </div>
            )}

            {/* Milestones — from Run Intel */}
            {hasMilestones && (
              <div>
                <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-2">
                  Company Milestones <span className="text-[#ff6b35] normal-case font-normal">(web research)</span>
                </p>
                <div className="space-y-2" style={{ rowGap: 10, display: "grid" }}>
                  {webResearch.milestones!.map((item, i) => (
                    <a key={i} href={item.url} target="_blank" rel="noopener noreferrer"
                      className="flex items-start gap-3 rounded-xl border border-[#e3eaf3] bg-white px-4 py-3 hover:border-[#ff6b35] transition-colors">
                      <TrendingUp size={13} className="text-[#9eb0c3] mt-0.5 shrink-0" />
                      <div className="min-w-0">
                        <p className="text-[13px] font-semibold text-[#24364b]">{item.title}</p>
                        {item.snippet && <p className="text-[12px] text-[#6f8399] mt-0.5 line-clamp-2">{item.snippet}</p>}
                      </div>
                      <ExternalLink size={11} className="text-[#9eb0c3] shrink-0 mt-0.5" />
                    </a>
                  ))}
                </div>
              </div>
            )}

            {/* Web intel state: not run / run but no results / has results (already rendered above) */}
            {!intelWasRun && (
              <div className="rounded-xl border border-dashed border-[#c9daf0] bg-[#f5f9ff] px-4 py-3 flex items-center justify-between gap-3">
                <p className="text-[13px] text-[#5a7a99]">
                  Click <strong>Run Web Intel</strong> to gather company background, news, intent signals, Hunter contacts, competitors, and an AI executive briefing.
                </p>
                <button className="crm-button soft shrink-0" onClick={handleRunIntelligence} disabled={running}>
                  <Sparkles size={13} /> Run Web Intel
                </button>
              </div>
            )}
            {intelWasRun && !hasWikipedia && !hasNews && !hasMilestones && (
              <p className="text-[13px] text-[#8fa5bc]">
                No public web results found for <strong>{company.name}</strong>. This can happen due to search rate limits or if the company has limited public coverage.
                Try clicking <strong>Re-run Web Intel</strong> to search again.
              </p>
            )}
          </div>
        )}
      </Section>

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 2 — Stakeholders (existing contacts, shown immediately)
      ══════════════════════════════════════════════════════════════════════ */}
      <Section
        title="Stakeholders / Attendees"
        icon={<Users size={15} className="text-[#4a627c]" />}
        badge={contacts.length > 0 ? String(contacts.length) : undefined}
      >
        {contacts.length === 0 ? (
          <div className="space-y-2">
            <p className="text-[13px] text-[#6f8399]">No contacts found for this company.</p>
            {company && (
              <p className="text-[12px] text-[#8fa5bc]">
                Go to <Link to={`/account-sourcing/${company.id}`} className="font-semibold text-[#4a7fa5] hover:underline">{company.name}</Link> and click "Find Contacts" to discover stakeholders.
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-3" style={{ rowGap: 10, display: "grid" }}>
            {contacts.map((c) => {
              const personaKey = canonicalPersona(c.persona, c.persona_type);
              const ps = PERSONA_STYLE[personaKey] ?? PERSONA_STYLE.unknown;
              return (
                <div key={c.id} className="flex items-center gap-4 rounded-xl border border-[#e0e8f1] bg-white px-4 py-3" style={{ padding: "12px 14px", gap: 12 }}>
                  <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[12px] font-extrabold ${avatarColor(`${c.first_name}${c.last_name}`)}`}>
                    {getInitials(`${c.first_name} ${c.last_name}`)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-[14px] font-bold text-[#26384e]">{c.first_name} {c.last_name}</p>
                      {(c.persona || c.persona_type) && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold"
                          style={{ color: ps.color, background: ps.bg, border: `1px solid ${ps.border}` }}>
                          {PERSONA_LABEL[personaKey] ?? personaKey}
                        </span>
                      )}
                    </div>
                    <p className="text-[12px] text-[#7b8fa4] mt-0.5">{c.title ?? c.seniority ?? "—"}</p>
                    {c.email && <p className="text-[12px] text-[#4a7fa5] mt-0.5">{c.email}</p>}
                  </div>
                  {c.linkedin_url && (
                    <a href={c.linkedin_url} target="_blank" rel="noopener noreferrer"
                      className="text-[11px] text-[#0077b5] font-semibold hover:underline shrink-0 flex items-center gap-1">
                      LinkedIn <ExternalLink size={10} />
                    </a>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 2b — Hunter.io Discovered Contacts
      ══════════════════════════════════════════════════════════════════════ */}
      {hasHunterContacts && (
        <Section
          title="Hunter.io Discovered Contacts"
          icon={<UserPlus size={15} className="text-[#0d9488]" />}
          badge={`${webResearch.hunter_contacts!.contacts.length} found`}
          defaultOpen={false}
        >
          <div className="space-y-1 mb-3">
            {webResearch.hunter_contacts!.pattern && (
              <p className="text-[12px] text-[#6f8399]">
                Email pattern: <span className="font-mono font-semibold text-[#2b3f55]">{webResearch.hunter_contacts!.pattern}@{webResearch.hunter_contacts!.domain}</span>
              </p>
            )}
          </div>
          <div className="space-y-2" style={{ rowGap: 8, display: "grid" }}>
            {webResearch.hunter_contacts!.contacts.map((hc, i) => (
              <div key={i} className="flex items-center gap-3 rounded-xl border border-[#d5ebe6] bg-[#f0fdf9] px-4 py-3" style={{ padding: "10px 14px", gap: 10 }}>
                <Mail size={14} className="text-[#0d9488] shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-bold text-[#26384e]">{hc.first_name} {hc.last_name}</p>
                  {hc.title && <p className="text-[12px] text-[#7b8fa4]">{hc.title}</p>}
                  <p className="text-[12px] text-[#4a7fa5]">{hc.email}</p>
                </div>
                <span className="text-[11px] font-semibold text-[#0d9488] bg-[#d5ebe6] px-2 py-0.5 rounded-full">{hc.confidence}%</span>
                {hc.linkedin_url && (
                  <a href={hc.linkedin_url} target="_blank" rel="noopener noreferrer"
                    className="text-[11px] text-[#0077b5] font-semibold hover:underline shrink-0">
                    LinkedIn <ExternalLink size={10} />
                  </a>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 2c — Intent Signals (hiring, funding, product/growth)
      ══════════════════════════════════════════════════════════════════════ */}
      {hasIntentSignals && (
        <Section title="Buying Intent Signals" icon={<Zap size={15} className="text-[#f59e0b]" />}>
          <div className="grid gap-4 md:grid-cols-3" style={{ gap: 14 }}>
            {([
              { key: "hiring" as const, label: "Hiring Signals", icon: Users, color: "#10b981", bg: "#ecfdf5", border: "#a7f3d0", hint: "Expanding team = budget available" },
              { key: "funding" as const, label: "Funding Signals", icon: TrendingUp, color: "#f59e0b", bg: "#fffbeb", border: "#fde68a", hint: "Fresh capital = buying power" },
              { key: "product" as const, label: "Growth Signals", icon: Zap, color: "#8b5cf6", bg: "#f5f3ff", border: "#ddd6fe", hint: "Expansion = new tool needs" },
            ]).map(({ key, label, icon: Icon, color, bg, border, hint }) => {
              const items = webResearch.intent_signals?.[key] ?? [];
              if (items.length === 0) return null;
              return (
                <div key={key} className="rounded-xl border p-4" style={{ background: bg, borderColor: border }}>
                  <div className="flex items-center gap-2 mb-2">
                    <Icon size={14} style={{ color }} />
                    <span className="text-[11px] font-bold uppercase tracking-wide" style={{ color }}>{label}</span>
                  </div>
                  <p className="text-[10px] text-[#8fa5bc] mb-2">{hint}</p>
                  <div className="space-y-2">
                    {items.map((s, i) => (
                      <div key={i} className="text-[12px] text-[#3d5268]">
                        <p className="font-semibold">{s.title}</p>
                        {s.snippet && <p className="text-[11px] text-[#6f8399] mt-0.5 line-clamp-2">{s.snippet}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 2d — Google News Headlines
      ══════════════════════════════════════════════════════════════════════ */}
      {hasGoogleNews && (
        <Section title="Google News Headlines" icon={<Globe size={15} className="text-[#3b82f6]" />}
          badge={String(webResearch.google_news!.length)} defaultOpen={false}>
          <div className="space-y-2" style={{ rowGap: 8, display: "grid" }}>
            {webResearch.google_news!.map((item, i) => (
              <a key={i} href={item.url} target="_blank" rel="noopener noreferrer"
                className="flex items-start gap-3 rounded-xl border border-[#e3eaf3] bg-white px-4 py-3 hover:border-[#3b82f6] transition-colors">
                <Newspaper size={13} className="text-[#9eb0c3] mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-semibold text-[#24364b]">{item.title}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    {item.source && <span className="text-[11px] font-semibold text-[#3b82f6]">{item.source}</span>}
                    {item.published && <span className="text-[11px] text-[#8fa5bc]">{item.published}</span>}
                  </div>
                </div>
                <ExternalLink size={11} className="text-[#9eb0c3] shrink-0 mt-0.5" />
              </a>
            ))}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 2e — Website Deep Analysis
      ══════════════════════════════════════════════════════════════════════ */}
      {hasWebsiteAnalysis && (
        <Section title="Website Deep Analysis" icon={<FileText size={15} className="text-[#7c3aed]" />} defaultOpen={false}>
          <div className="grid gap-3 md:grid-cols-2" style={{ gap: 10 }}>
            {Object.entries(webResearch.website_analysis!).map(([key, value]) => {
              const label = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
              return (
                <div key={key} className="rounded-xl border border-[#e3e8f0] bg-[#f9fbfe] px-4 py-3" style={{ padding: "12px 15px" }}>
                  <p className="text-[11px] uppercase tracking-wide text-[#7d8fa3] font-semibold mb-1">{label}</p>
                  <p className="text-[13px] text-[#2d4258] leading-relaxed">{value}</p>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 2f — Competitive Landscape
      ══════════════════════════════════════════════════════════════════════ */}
      {hasCompetitors && (
        <Section title="Competitive Landscape" icon={<Crosshair size={15} className="text-[#ef4444]" />} defaultOpen={false}>
          <div className="space-y-2" style={{ rowGap: 8, display: "grid" }}>
            {webResearch.competitive_landscape!.map((item, i) => (
              <a key={i} href={item.url} target="_blank" rel="noopener noreferrer"
                className="flex items-start gap-3 rounded-xl border border-[#e3eaf3] bg-white px-4 py-3 hover:border-[#ef4444] transition-colors">
                <Target size={13} className="text-[#f87171] mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold text-[#24364b]">{item.title}</p>
                  {item.snippet && <p className="text-[12px] text-[#6f8399] mt-0.5 line-clamp-2">{item.snippet}</p>}
                </div>
                <ExternalLink size={11} className="text-[#9eb0c3] shrink-0 mt-0.5" />
              </a>
            ))}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 3 — Demo Strategy & Story Lineup (GPT-4o, on-demand)
      ══════════════════════════════════════════════════════════════════════ */}
      <Section title="Demo Strategy & Story Lineup" icon={<Sparkles size={15} className="text-[#ff6b35]" />}>
        {meeting.demo_strategy ? (
          <DemoStrategyCards strategy={meeting.demo_strategy} onRegenerate={handleGenerateDemoStrategy} regenerating={generatingStory} />
        ) : (
          <div className="rounded-xl border border-dashed border-[#ffd5be] bg-[#fff8f5] px-5 py-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <p className="text-[14px] font-bold text-[#8f3a14]">No story lineup yet</p>
              <p className="text-[13px] text-[#b05a2a] mt-0.5">
                GPT-4o will read your account intel and build a tailored demo playbook — opening hook, discovery question, 3 story chapters, objection handling, and next step.
              </p>
            </div>
            <button className="crm-button primary shrink-0" onClick={handleGenerateDemoStrategy} disabled={generatingStory}>
              {generatingStory ? <RefreshCw size={13} className="animate-spin" /> : <Sparkles size={13} />}
              {generatingStory ? "Building story…" : "Create Story & Pre-Intel"}
            </button>
          </div>
        )}
      </Section>

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 4 — Company Signals (from DB, shown immediately)
      ══════════════════════════════════════════════════════════════════════ */}
      {signals.length > 0 && (
        <Section title="Company Signals" icon={<TrendingUp size={15} className="text-[#4a7fa5]" />}
          badge={String(signals.length)} defaultOpen={false}>
          <div className="space-y-3">
            {signals.map((s) => (
              <div key={s.id} className="rounded-xl border border-[#e3eaf3] bg-[#fbfdff] px-4 py-3">
                <div className="flex items-center gap-2 text-[12px] text-[#6f8399] capitalize mb-1">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: SIGNAL_DOT[s.signal_type] ?? "#94a3b8" }} />
                  {s.signal_type}
                </div>
                <p className="text-[14px] font-semibold text-[#2b3f55]">{s.title}</p>
                {s.summary && <p className="text-[13px] text-[#647a91] mt-1">{s.summary}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 5 — Relevant Battlecards (from Run Intel)
      ══════════════════════════════════════════════════════════════════════ */}
      {(webResearch.relevant_battlecards?.length ?? 0) > 0 && (
        <Section title="Relevant Battlecards" icon={<Shield size={15} className="text-[#4a627c]" />} defaultOpen={false}>
          <div className="space-y-3">
            {webResearch.relevant_battlecards!.map((bc) => (
              <div key={bc.id} className="rounded-xl border border-[#e3eaf3] bg-[#fbfdff] px-4 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="inline-flex px-2 py-0.5 rounded-full bg-[#fff3e8] border border-[#ffd9be] text-[10px] font-bold text-[#b05a1a] capitalize">
                    {bc.category}
                  </span>
                  <p className="text-[14px] font-bold text-[#2b3f55]">{bc.title}</p>
                </div>
                <p className="text-[13px] text-[#4d6178] leading-relaxed">{bc.summary}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 6 — AI Account Brief (on-demand account planning brief)
      ══════════════════════════════════════════════════════════════════════ */}
      <Section title="AI Account Brief" icon={<BrainCircuit size={15} className="text-[#ff6b35]" />} defaultOpen={false}>
        <div className="flex items-center justify-between mb-3">
          <p className="text-[13px] text-[#6f8399]">
            Builds a quick account-planning brief from company context, signals, stakeholders, and website research.
          </p>
          <button className="crm-button soft" onClick={handleGetAccountBrief} disabled={loadingBrief || !company}>
            {loadingBrief ? <RefreshCw size={13} className="animate-spin" /> : <BrainCircuit size={13} />}
            {loadingBrief ? "Generating…" : "Generate Brief"}
          </button>
        </div>
        {accountBrief ? (
          <div className="rounded-xl border border-[#dce6f0] bg-[#f8fbff] p-4 space-y-2">
            {accountBrief.split("\n").filter(Boolean).map((line, i) => (
              <p key={i} className="text-[14px] text-[#2d4258] leading-relaxed">{line}</p>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#6f8399]">Not generated yet.</p>
        )}
      </Section>

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 7 — Post-Debrief & Scoring
      ══════════════════════════════════════════════════════════════════════ */}
      <Section title="Post-Debrief & Scoring" icon={<BookOpen size={15} className="text-[#4a627c]" />} defaultOpen={false}>
        <textarea
          className="w-full min-h-40 rounded-xl border border-[#d7e2ee] bg-white p-4 text-[14px] text-[#223145] outline-none focus:border-[#c2d3e5] mb-3"
          placeholder="Paste raw meeting notes here — AI will score the call, identify wins/losses, and draft the MoM email."
          value={rawNotes}
          onChange={(e) => setRawNotes(e.target.value)}
        />
        <div className="flex justify-end">
          <button className="crm-button primary" onClick={handlePostScore} disabled={scoring}>
            {scoring ? "Scoring…" : "Score Meeting & Draft MoM"}
          </button>
        </div>
        {(meeting.meeting_score != null || meeting.what_went_right || meeting.mom_draft) && (
          <div className="rounded-xl border border-[#dce6f0] bg-[#f8fbff] p-5 space-y-3 mt-4">
            {meeting.meeting_score != null && (
              <p className="text-[14px]"><span className="font-bold">Score:</span>{" "}
                <span className="tabular font-extrabold text-[#ff6b35]">{meeting.meeting_score}/100</span>
              </p>
            )}
            {meeting.what_went_right && (
              <p className="text-[14px]"><span className="font-bold text-[#1b6f53]">✓ What went right:</span> {meeting.what_went_right}</p>
            )}
            {meeting.what_went_wrong && (
              <p className="text-[14px]"><span className="font-bold text-[#8f2f11]">✗ What went wrong:</span> {meeting.what_went_wrong}</p>
            )}
            {meeting.next_steps && (
              <p className="text-[14px]"><span className="font-bold">→ Next steps:</span> {meeting.next_steps}</p>
            )}
            {meeting.mom_draft && (
              <div>
                <p className="text-[13px] font-bold uppercase tracking-wide text-[#7a8ea4] mb-2">MoM Email Draft</p>
                <pre className="whitespace-pre-wrap text-[13px] text-[#2d4258] font-sans leading-relaxed">{meeting.mom_draft}</pre>
              </div>
            )}
          </div>
        )}
      </Section>

      {error && <p className="text-[12px] text-[#b94a24] font-semibold px-1">{error}</p>}
    </div>
  );
}

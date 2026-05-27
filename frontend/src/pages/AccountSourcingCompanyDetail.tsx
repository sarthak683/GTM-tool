import "./account-sourcing-refresh.css";
import { CSSProperties, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Brain,
  Building2,
  CheckCircle2,
  Clock,
  ExternalLink,
  Globe,
  Loader2,
  Mail,
  MessageSquare,
  Phone,
  RefreshCw,
  Rocket,
  AlertTriangle,
  Newspaper,
  Radar,
  Target,
  TrendingUp,
  Users,
  X,
} from "lucide-react";

import { accountSourcingApi, companiesApi, contactsApi, dealsApi, outreachApi, settingsApi } from "../lib/api";
import { Plus, Trash2, UserPlus } from "lucide-react";
import { useAuth } from "../lib/AuthContext";
import { useToast } from "../lib/ToastContext";
import {
  getProspectTrackingScore,
  getProspectTrackingStage,
  getProspectTrackingSummary,
  getProspectTrackingTone,
} from "../lib/prospectTracking";
import type { Company, Contact, DealStageSetting } from "../types";
import { formatDate, formatDomain, getAccountPrioritySnapshot, gmailComposeUrl } from "../lib/utils";
import AssignDropdown from "../components/AssignDropdown";
import ProvenanceBar from "../components/ProvenanceBar";
import TaskCenterModal from "../components/tasks/TaskCenterModal";
import {
  asList,
  asText,
  cacheTs,
  canonicalPersona,
  cardStyle,
  clipText,
  colors,
  defaultDealStage,
  DISPOSITION_OPTIONS,
  FALLBACK_DEAL_STAGES,
  ICP_STYLE,
  INTEREST_STYLE,
  isPriorityStakeholder,
  KV,
  ListCard,
  MetricCard,
  NewsSignalCard,
  OUTREACH_LANE_OPTIONS,
  OUTREACH_STATUS_OPTIONS,
  PERSONA_LABEL,
  PERSONA_STYLE,
  pageStyle,
  PRIORITY_STYLE,
  Section,
  SourceLinks,
  sourceList,
  toBriefItems,
  ts,
  unwrapCache,
  wrapStyle,
} from "./accountSourcingCompanyDetailShared";

function ContactItem({ contact, onChanged }: { contact: Contact; onChanged: () => void }) {
  const [re, setRe] = useState(false);

  const persona = canonicalPersona(contact.persona, contact.persona_type);
  const talkingPoints = Array.isArray(contact.talking_points) ? contact.talking_points : [];
  const warmPath = (contact.warm_intro_path || {}) as Record<string, unknown>;
  const enrichData = (contact.enrichment_data || {}) as Record<string, unknown>;
  const emailConfidence = typeof enrichData.confidence === "number" ? enrichData.confidence : null;
  const trackingTone = getProspectTrackingTone(contact);

  return (
    <div style={{ border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 14px", background: "#fbfdff" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <div>
          <Link
            to={`/account-sourcing/contacts/${contact.id}`}
            style={{ color: colors.text, fontWeight: 800, fontSize: 15, textDecoration: "none" }}
          >
            {contact.first_name} {contact.last_name}
          </Link>
          <div style={{ color: colors.sub, marginTop: 3 }}>{contact.title || "No title"}</div>
          <div style={{ color: colors.faint, marginTop: 4, fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
            {contact.email || "No email"}
            {contact.email_verified && (
              <span style={{ background: "#eefcf5", color: colors.green, borderRadius: 999, fontSize: 10, padding: "2px 6px", fontWeight: 700 }}>verified</span>
            )}
            {emailConfidence !== null && (
              <span style={{
                background: emailConfidence >= 80 ? "#eefcf5" : emailConfidence >= 50 ? "#fff4df" : "#ffecef",
                color: emailConfidence >= 80 ? colors.green : emailConfidence >= 50 ? colors.amber : colors.red,
                borderRadius: 999, fontSize: 10, padding: "2px 6px", fontWeight: 700,
              }}>
                {emailConfidence}% conf
              </span>
            )}
          </div>
          <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <span
              style={{
                background: trackingTone.background,
                color: trackingTone.color,
                borderRadius: 999,
                border: `1px solid ${trackingTone.border}`,
                fontSize: 11,
                padding: "4px 8px",
                fontWeight: 800,
              }}
            >
              {getProspectTrackingStage(contact)}
            </span>
            {contact.outreach_lane ? (
              <span style={{ background: "#eef5ff", color: colors.primary, borderRadius: 999, fontSize: 11, padding: "4px 8px", fontWeight: 700 }}>
                {contact.outreach_lane.replace(/_/g, " ")}
              </span>
            ) : null}
            {contact.sequence_status ? (
              <span style={{ background: "#f3eaff", color: colors.violet, borderRadius: 999, fontSize: 11, padding: "4px 8px", fontWeight: 700 }}>
                {contact.sequence_status.replace(/_/g, " ")}
              </span>
            ) : null}
            {contact.assigned_rep_email ? (
              <span style={{ background: "#f7f9fc", color: colors.sub, borderRadius: 999, fontSize: 11, padding: "4px 8px", fontWeight: 700 }}>
                {contact.assigned_rep_email}
              </span>
            ) : null}
          </div>
          <div
            style={{
              marginTop: 10,
              borderRadius: 12,
              border: `1px solid ${trackingTone.border}`,
              background: trackingTone.soft,
              padding: "10px 12px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
              <span style={{ color: trackingTone.color, fontSize: 12, fontWeight: 800 }}>Automated progress</span>
              <span style={{ color: trackingTone.color, fontSize: 12, fontWeight: 900 }}>{getProspectTrackingScore(contact)}</span>
            </div>
            <div style={{ marginTop: 5, color: colors.sub, fontSize: 12.5, lineHeight: 1.55 }}>
              {getProspectTrackingSummary(contact)}
            </div>
          </div>
          {contact.conversation_starter ? (
            <div style={{ marginTop: 10, color: colors.sub, fontSize: 13, lineHeight: 1.55 }}>
              <strong style={{ color: colors.text }}>Starter:</strong> {contact.conversation_starter}
            </div>
          ) : null}
          {warmPath.connection_path ? (
            <div style={{ marginTop: 8, color: colors.sub, fontSize: 13, lineHeight: 1.55 }}>
              <strong style={{ color: colors.text }}>Warm Path:</strong> {String(warmPath.connection_path)}
            </div>
          ) : null}
          {talkingPoints.length > 0 ? (
            <div style={{ marginTop: 8, color: colors.sub, fontSize: 13, lineHeight: 1.55 }}>
              <strong style={{ color: colors.text }}>Talking Points:</strong> {talkingPoints.slice(0, 2).join(" | ")}
            </div>
          ) : null}
        </div>

        <div style={{ display: "grid", justifyItems: "end", alignContent: "start", gap: 8 }}>
          {persona !== "unknown" ? (
            <span style={{ ...PERSONA_STYLE[persona], borderRadius: 999, fontSize: 11, padding: "4px 9px", fontWeight: 700 }}>
              {PERSONA_LABEL[persona]}
            </span>
          ) : null}

          <div style={{ display: "inline-flex", gap: 10 }}>
            {contact.email ? (
              <a
                href={gmailComposeUrl(contact.email)}
                target="_blank"
                rel="noreferrer"
                title={`Email ${contact.email} in Gmail`}
              >
                <Mail size={14} />
              </a>
            ) : null}
            {contact.linkedin_url ? <a href={contact.linkedin_url} target="_blank" rel="noreferrer"><span><Globe size={14} /></span></a> : null}
            {contact.phone ? (
              <button
                type="button"
                onClick={() => window.__aircallDial?.(contact.phone!, `${contact.first_name} ${contact.last_name}`)}
                style={{ background: "none", border: "none", padding: 0, color: "inherit", cursor: "pointer" }}
                title={`Call ${contact.phone} in Aircall`}
              >
                <Phone size={14} />
              </button>
            ) : null}
          </div>

          <div style={{ color: colors.faint, fontSize: 12 }}>Enriched: {ts(contact.enriched_at)}</div>
          <div style={{ display: "grid", gap: 6, justifyItems: "end" }}>
            <div style={{ color: colors.faint, fontSize: 11, fontWeight: 700 }}>AE</div>
            <AssignDropdown
              entityType="contact"
              entityId={contact.id}
              currentAssignedId={contact.assigned_to_id}
              currentAssignedName={contact.assigned_to_name || contact.assigned_rep_email}
              onAssigned={() => onChanged()}
              compact
              role="ae"
              label="Assign AE"
            />
            <div style={{ color: colors.faint, fontSize: 11, fontWeight: 700 }}>SDR</div>
            <AssignDropdown
              entityType="contact"
              entityId={contact.id}
              currentAssignedId={contact.sdr_id}
              currentAssignedName={contact.sdr_name}
              onAssigned={() => onChanged()}
              compact
              role="sdr"
              label="Assign SDR"
            />
          </div>
          <div style={{ display: "inline-flex", gap: 8 }}>
            <Link
              to={`/account-sourcing/contacts/${contact.id}`}
              style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.primary, borderRadius: 10, padding: "6px 9px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6, textDecoration: "none" }}
              title="Open full prospect card"
            >
              <ExternalLink size={12} /> Open prospect
            </Link>
            <button
              onClick={async () => {
                setRe(true);
                try {
                  await accountSourcingApi.reEnrichContact(contact.id);
                  onChanged();
                } finally {
                  setTimeout(() => setRe(false), 2500);
                }
              }}
              style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 10, padding: "6px 9px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              {re ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Re-enrich
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AccountSourcingCompanyDetail() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const { isAdmin } = useAuth();
  const toast = useToast();

  const [company, setCompany] = useState<Company | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);

  const [re, setRe] = useState(false);
  const [icpResearching, setIcpResearching] = useState(false);
  const [researchStatus, setResearchStatus] = useState<{
    tone: "idle" | "running" | "success" | "warning" | "error";
    message: string;
  }>({ tone: "idle", message: "" });
  const [showAddStakeholder, setShowAddStakeholder] = useState(false);
  // F8 — bulk add-to-sequence selection state
  const [selectedContactIds, setSelectedContactIds] = useState<Set<string>>(new Set());
  const [bulkLaunchOpen, setBulkLaunchOpen] = useState(false);
  const [bulkLaunchAccount, setBulkLaunchAccount] = useState("");
  const [bulkLaunchName, setBulkLaunchName] = useState("");
  const [bulkLaunching, setBulkLaunching] = useState(false);
  const [bulkLaunchError, setBulkLaunchError] = useState("");

  const toggleContactSelected = useCallback((cid: string) => {
    setSelectedContactIds((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid); else next.add(cid);
      return next;
    });
  }, []);
  const [showDealModal, setShowDealModal] = useState(false);
  const [showActivityModal, setShowActivityModal] = useState(false);
  const [showTasksModal, setShowTasksModal] = useState(false);
  const [noteInput, setNoteInput] = useState("");
  const [noteSaving, setNoteSaving] = useState(false);
  const [editingDomain, setEditingDomain] = useState(false);
  const [domainInput, setDomainInput] = useState("");
  const [domainSaving, setDomainSaving] = useState(false);
  const [creatingDeal, setCreatingDeal] = useState(false);
  const [dealError, setDealError] = useState("");
  const [existingCompanyDeals, setExistingCompanyDeals] = useState<Array<{ id: string; name: string; stage?: string }>>([]);
  const [dealStages, setDealStages] = useState<DealStageSetting[]>([]);
  const [dealForm, setDealForm] = useState({
    name: "",
    value: "",
    stage: "discovery",
    close_date_est: "",
  });
  const availableDealStages = useMemo(
    () => (dealStages.length ? dealStages : FALLBACK_DEAL_STAGES),
    [dealStages]
  );

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const [c, ct] = await Promise.all([
        accountSourcingApi.getCompany(id),
        accountSourcingApi.getContacts(id),
      ]);
      setCompany(c);
      setContacts(ct);
    } finally {
      setLoading(false);
    }
  }, [id]);

  const refreshUntilSettled = useCallback(async (options?: {
    attempts?: number;
    delayMs?: number;
    stopWhen?: (company: Company) => boolean;
    onProgress?: (state: { attempt: number; attempts: number; company: Company }) => void;
  }) => {
    if (!id) return;
    const attempts = options?.attempts ?? 18;
    const delayMs = options?.delayMs ?? 5000;
    for (let i = 0; i < attempts; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      const [c, ct] = await Promise.all([
        accountSourcingApi.getCompany(id),
        accountSourcingApi.getContacts(id),
      ]);
      setCompany(c);
      setContacts(ct);
      options?.onProgress?.({ attempt: i + 1, attempts, company: c });
      if (options?.stopWhen?.(c)) {
        return true;
      }
    }
    return false;
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const cache = useMemo(() => (company?.enrichment_cache || {}) as Record<string, unknown>, [company]);
  const prospectingProfile = (company?.prospecting_profile || {}) as Record<string, unknown>;
  const outreachPlan = (company?.outreach_plan || {}) as Record<string, unknown>;
  const ai = unwrapCache(cache, "ai_summary");
  const web = unwrapCache(cache, "web_scrape");
  const apollo = unwrapCache(cache, "apollo_company");
  const committee = unwrapCache(cache, "committee_coverage");
  const committeeScore = typeof committee?.coverage_score === "number" ? committee.coverage_score : 0;
  const missingRoles = Array.isArray(committee?.missing_roles)
    ? (committee.missing_roles as Array<{ label?: string; why?: string }>)
    : [];
  const bestContacts = Array.isArray(committee?.best_contacts)
    ? (committee.best_contacts as Array<{ name?: string; title?: string; label?: string }>)
    : [];
  const fitReasoning = asText(ai?.icp_fit_reasoning);
  const intentSummary = asText(ai?.intent_signals_summary);
  const companySummary = asText(ai?.description) || company?.description || undefined;
  const websiteText = asText(web?.text);
  const warmPaths = Array.isArray(prospectingProfile.warm_paths)
    ? prospectingProfile.warm_paths as Array<Record<string, unknown>>
    : [];
  const sequenceFamily = asText(outreachPlan.sequence_family);
  const priority = company ? getAccountPrioritySnapshot(company) : null;

  // Intent signals — hiring, funding, product news from Serper
  const intentSignals = useMemo(() => {
    const entry = cache.intent_signals as Record<string, unknown> | undefined;
    if (!entry) return null;
    return (entry.data ?? entry) as Record<string, unknown>;
  }, [cache]);
  const hiringNews = useMemo(() => {
    const items = intentSignals?.hiring;
    return Array.isArray(items) ? items as Array<{ title?: string; snippet?: string; url?: string }> : [];
  }, [intentSignals]);
  const fundingNews = useMemo(() => {
    const items = intentSignals?.funding;
    return Array.isArray(items) ? items as Array<{ title?: string; snippet?: string; url?: string }> : [];
  }, [intentSignals]);
  const productNews = useMemo(() => {
    const items = intentSignals?.product;
    return Array.isArray(items) ? items as Array<{ title?: string; snippet?: string; url?: string }> : [];
  }, [intentSignals]);
  const allNewsItems = useMemo(() => {
    return [...hiringNews, ...fundingNews, ...productNews].filter((n) => n.title);
  }, [hiringNews, fundingNews, productNews]);

  // ICP Intelligence Pipeline data
  const icpAnalysis = useMemo(() => {
    const entry = cache.icp_analysis as Record<string, unknown> | undefined;
    if (!entry) return null;
    const data = (entry.data ?? entry) as Record<string, unknown>;
    return data;
  }, [cache]);
  const researchSources = useMemo(() => {
    const sources = icpAnalysis?.sources;
    return sources && typeof sources === "object" ? (sources as Record<string, unknown>) : {};
  }, [icpAnalysis]);
  const icpAnalyzedAt = useMemo(() => {
    const entry = cache.icp_analysis as Record<string, unknown> | undefined;
    return typeof entry?.analyzed_at === "string" ? entry.analyzed_at : undefined;
  }, [cache]);
  const icpPersonas = useMemo(() => {
    const personas = prospectingProfile.icp_personas;
    return Array.isArray(personas) ? personas as Array<Record<string, string>> : [];
  }, [prospectingProfile]);
  const icpOpenGaps = useMemo(() => {
    const gaps = prospectingProfile.open_gaps;
    return Array.isArray(gaps) ? gaps.map(String) : [];
  }, [prospectingProfile]);
  const icpCommitteeCoverage = asText(prospectingProfile.committee_coverage);
  const salesPlay = useMemo(() => {
    const play = prospectingProfile.sales_play;
    return play && typeof play === "object" ? (play as Record<string, unknown>) : undefined;
  }, [prospectingProfile]);
  const entryPersona = salesPlay?.best_persona && typeof salesPlay.best_persona === "object"
    ? (salesPlay.best_persona as Record<string, unknown>)
    : undefined;
  const entryPersonaTitle = asText(entryPersona?.title);
  const entryPersonaRelevance = asText(entryPersona?.relevance);
  const whyNowItems = toBriefItems(icpAnalysis?.why_now ?? company?.why_now, 3);
  const outreachItems = toBriefItems(icpAnalysis?.recommended_outreach_strategy, 4);
  const starterItems = toBriefItems(icpAnalysis?.conversation_starter, 2);
  const nextStepItems = toBriefItems(icpAnalysis?.next_steps, 4);
  const fitReasonItems = toBriefItems(icpAnalysis?.icp_why ?? fitReasoning, 3);
  const intentReasonItems = toBriefItems(icpAnalysis?.intent_why ?? intentSummary, 3);
  const beaconCareItems = toBriefItems(icpAnalysis?.account_thesis, 2);
  const engageItems = toBriefItems(icpAnalysis?.beacon_angle, 3);
  const positiveSignalCards = [
    { title: "Hiring", value: clipText(asText(icpAnalysis?.ps_impl_hiring), 220) },
    { title: "Leadership", value: clipText(asText(icpAnalysis?.leadership_org_moves), 220) },
    { title: "Expansion", value: clipText(asText(icpAnalysis?.pr_funding_expansion), 220) },
    { title: "Events", value: clipText(asText(icpAnalysis?.events_thought_leadership), 220) },
    { title: "Proof", value: clipText(asText(icpAnalysis?.reviews_case_studies), 220) },
  ].filter((item) => item.value && item.value.toLowerCase() !== "none observed" && item.value.toLowerCase() !== "not analyzed") as Array<{ title: string; value: string }>;
  const negativeSignalCards = [
    { title: "AI Overlap", value: clipText(asText(icpAnalysis?.internal_ai_overlap), 220) },
    { title: "Constraints", value: clipText(asText(icpAnalysis?.strategic_constraints), 220) },
    { title: "Contraction", value: clipText(asText(icpAnalysis?.ps_cs_contraction), 220) },
    { title: "Build vs Buy", value: clipText(asText(icpAnalysis?.build_vs_buy), 220) },
    { title: "AI Acquisition", value: clipText(asText(icpAnalysis?.ai_acquisition), 220) },
  ].filter((item) => item.value && item.value.toLowerCase() !== "none observed" && item.value.toLowerCase() !== "not analyzed") as Array<{ title: string; value: string }>;
  const implCycle = useMemo(() => {
    const raw = icpAnalysis?.implementation_cycle;
    if (!raw || typeof raw !== "object") return null;
    const cycle = raw as Record<string, unknown>;
    // Only show if at least one field has real content
    const hasContent = ["enterprise", "mid_market", "minimum", "key_drivers", "evidence"]
      .some((k) => { const v = cycle[k]; return typeof v === "string" && v.length > 2; });
    return hasContent ? cycle : null;
  }, [icpAnalysis]);
  const researchQuality = useMemo(() => {
    const entry = cache.research_quality as Record<string, unknown> | undefined;
    if (!entry) return undefined;
    return (entry.data ?? entry) as Record<string, unknown>;
  }, [cache]);
  const evidenceLevel = asText(researchQuality?.evidence_level);
  const evidenceScore = typeof researchQuality?.evidence_score === "number" ? researchQuality.evidence_score : undefined;
  const fitSources = sourceList(researchSources.icp_why);
  const intentSources = sourceList(researchSources.intent_why);
  const thesisSources = sourceList(researchSources.account_thesis);
  const whyNowSources = sourceList(researchSources.why_now);
  const angleSources = sourceList(researchSources.beacon_angle);
  const engageSources = sourceList(researchSources.recommended_outreach_strategy);
  const hookSources = sourceList(researchSources.conversation_starter);
  const hiringSources = sourceList(researchSources.ps_impl_hiring);
  const leadershipSources = sourceList(researchSources.leadership_org_moves);
  const relevantContacts = useMemo(() => {
    const filtered = contacts.filter(isPriorityStakeholder);
    return filtered.length ? filtered : contacts;
  }, [contacts]);
  const contactMomentum = useMemo(() => {
    if (!relevantContacts.length) return null;
    const ranked = [...relevantContacts].sort((a, b) => (b.tracking_score || 0) - (a.tracking_score || 0));
    const best = ranked[0];
    const goodCount = relevantContacts.filter((contact) => contact.tracking_label === "good").length;
    const blockedCount = relevantContacts.filter((contact) => contact.tracking_label === "blocked").length;
    return {
      best,
      goodCount,
      blockedCount,
      hint:
        goodCount > 0
          ? `${goodCount} stakeholder${goodCount === 1 ? "" : "s"} showing positive momentum.`
          : blockedCount === relevantContacts.length
            ? "All visible stakeholders are currently blocked."
            : "No stakeholder has a strong signal yet.",
    };
  }, [relevantContacts]);
  const fundingSources = sourceList(researchSources.pr_funding_expansion);
  const eventsSources = sourceList(researchSources.events_thought_leadership);
  const reviewsSources = sourceList(researchSources.reviews_case_studies);
  const aiOverlapSources = sourceList(researchSources.internal_ai_overlap);
  const strategicSources = sourceList(researchSources.strategic_constraints);
  const revenueSources = sourceList(researchSources.revenue_funding);
  const committeeCoverageSources = sourceList(researchSources.committee_coverage);
  const nextStepSources = sourceList(researchSources.next_steps);
  const activityEntries = useMemo(() => {
    const entries = cache.activity_log;
    return Array.isArray(entries) ? [...entries].reverse().slice(0, 8) as Array<Record<string, unknown>> : [];
  }, [cache]);
  const competitorCards = useMemo(() => {
    const items = cache.competitive_landscape_v2;
    return Array.isArray(items) ? items as Array<Record<string, unknown>> : [];
  }, [cache]);
  const hasPartialResearchWithoutFullEnrichment = Boolean(company && !company.enriched_at && competitorCards.length > 0);
  const detailMetaItems = [
    company?.domain ? (company.domain.endsWith(".unknown") ? formatDomain(company.domain) : company.domain) : undefined,
    asText(company?.industry),
    asText(company?.funding_stage),
    company?.employee_count ? `${company.employee_count.toLocaleString()} employees` : undefined,
    typeof icpAnalysis?.category === "string" ? asText(icpAnalysis.category) : undefined,
    company?.headquarters ? `📍 ${company.headquarters}` : undefined,
    company?.region ? `🌐 ${company.region}` : undefined,
  ].filter(Boolean) as string[];

  useEffect(() => {
    settingsApi
      .getDealStages()
      .then((config) => setDealStages(config.stages ?? []))
      .catch(() => setDealStages([]));
  }, []);

  useEffect(() => {
    setDealForm((current) => {
      if (availableDealStages.some((stage) => stage.id === current.stage)) return current;
      return { ...current, stage: defaultDealStage(availableDealStages) };
    });
  }, [availableDealStages]);

  useEffect(() => {
    if (!showDealModal || !company) return;
    setDealError("");
    dealsApi
      .list(0, 25, company.id)
      .then((rows) => {
        const deals = rows.map((deal) => ({ id: deal.id, name: deal.name, stage: deal.stage }));
        setExistingCompanyDeals(deals);
        setDealForm((current) => {
          if (current.name.trim()) return current;
          const suffix = deals.length === 0 ? "" : String(deals.length + 1);
          return {
            ...current,
            name: `${company.name}${suffix}`,
            stage: availableDealStages.some((stage) => stage.id === current.stage)
              ? current.stage
              : defaultDealStage(availableDealStages),
          };
        });
      })
      .catch(() => {
        setExistingCompanyDeals([]);
        setDealForm((current) => ({
          ...current,
          name: current.name.trim() ? current.name : company.name,
          stage: availableDealStages.some((stage) => stage.id === current.stage)
            ? current.stage
            : defaultDealStage(availableDealStages),
        }));
      });
  }, [showDealModal, company, availableDealStages]);

  if (loading) {
    return (
      <div style={pageStyle}>
        <div style={{ ...wrapStyle, ...cardStyle, padding: 28, textAlign: "center" }}>
          <Loader2 className="animate-spin" color={colors.primary} />
        </div>
      </div>
    );
  }

  if (!company) {
    return (
      <div style={pageStyle}>
        <div style={{ ...wrapStyle, ...cardStyle, padding: 28, textAlign: "center", color: colors.faint }}>
          Company not found.
        </div>
      </div>
    );
  }

  const handleCreateDeal = async () => {
    if (!company) return;
    if (!dealForm.name.trim()) {
      setDealError("Deal name is required.");
      return;
    }
    setCreatingDeal(true);
    setDealError("");
    try {
      const normalizedRequestedName = dealForm.name.trim().toLowerCase();
      const duplicate = existingCompanyDeals.find(
        (deal) => deal.name.trim().toLowerCase() === normalizedRequestedName
      );
      if (duplicate) {
        toast.info(`Opened "${duplicate.name}" instead of creating a duplicate deal.`, "Deal already exists");
        setShowDealModal(false);
        nav(`/deals/${duplicate.id}`);
        return;
      }
      const createdDeal = await dealsApi.create({
        company_id: company.id,
        name: dealForm.name.trim(),
        stage: dealForm.stage,
        value: dealForm.value ? Number(dealForm.value) : undefined,
        close_date_est: dealForm.close_date_est || undefined,
      });
      setShowDealModal(false);
      setDealForm({ name: "", value: "", stage: defaultDealStage(availableDealStages), close_date_est: "" });
      toast.success(`${createdDeal.name} was created and linked to ${company.name}.`, "Deal created");
      nav(`/deals/${createdDeal.id}`);
    } catch (error) {
      setDealError(error instanceof Error ? error.message : "Failed to create deal");
    } finally {
      setCreatingDeal(false);
    }
  };

  const scrollToContacts = () => {
    document.getElementById("stakeholders-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const handleSaveDomain = async () => {
    if (!company) return;
    const trimmed = domainInput.trim().replace(/^https?:\/\//, "").replace(/\/$/, "");
    if (!trimmed) return;
    setDomainSaving(true);
    try {
      await accountSourcingApi.updateCompany(company.id, { domain: trimmed });
      await load();
      setEditingDomain(false);
    } finally {
      setDomainSaving(false);
    }
  };

  const researchFingerprint = `${company.enriched_at || ""}|${company.icp_score ?? ""}|${company.domain}|${cacheTs(cache, "icp_analysis") || ""}|${cacheTs(cache, "research_quality") || ""}`;

  const tier = company.icp_tier || "cold";

  return (
    <div className="account-sourcing-detail-page ascd-page" style={pageStyle}>
      <style>{`
        @media (max-width: 768px) {
          .ascd-hero { padding: 14px !important; }
          .ascd-hero h1 { font-size: 24px !important; }
          .ascd-hero-grid { grid-template-columns: 1fr !important; gap: 12px !important; }
          .ascd-main-grid { grid-template-columns: 1fr !important; gap: 10px !important; }
          .ascd-section { padding: 12px !important; border-radius: 14px !important; }
          .ascd-page { padding: 8px 6px 80px !important; }
          .ascd-wrap { padding: 8px 2px 0 !important; gap: 10px !important; }
        }
      `}</style>
      <div className="ascd-wrap" style={wrapStyle}>
        <button
          onClick={() => nav("/account-sourcing")}
          style={{
            justifySelf: "start",
            border: 0,
            background: "transparent",
            color: colors.sub,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontWeight: 700,
            cursor: "pointer",
            padding: 0,
          }}
        >
          <ArrowLeft size={14} /> Back to Account Sourcing
        </button>

        <div
          className="ascd-hero"
          style={{
            ...cardStyle,
            padding: "24px 26px",
            background: "linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(242,247,255,0.98) 60%, rgba(255,244,236,0.98) 100%)",
          }}
        >
          <div className="ascd-hero-grid" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(250px, 290px)", gap: 20, alignItems: "start" }}>
            <div style={{ minWidth: 0, maxWidth: 980 }}>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 8, borderRadius: 999, padding: "6px 12px", background: "#eef5ff", color: colors.primary, fontSize: 12, fontWeight: 800, letterSpacing: 0.4 }}>
                <Brain size={13} />
                ACCOUNT INTELLIGENCE
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
                <Building2 size={18} color="#8b98ad" />
                <h1 style={{ margin: 0, color: colors.text, fontSize: 34, lineHeight: 1.1 }}>{company.name}</h1>
                <span style={{ ...ICP_STYLE[tier], borderRadius: 999, padding: "5px 10px", fontSize: 12, fontWeight: 800 }}>
                  {tier.toUpperCase()} · {company.icp_score ?? 0}/100
                </span>
                {priority ? (
                  <>
                    <span style={{ ...PRIORITY_STYLE[priority.priorityBand], borderRadius: 999, padding: "5px 10px", fontSize: 12, fontWeight: 800 }}>
                      Priority {priority.priorityBand} · {priority.priorityScore}
                    </span>
                    <span style={{ ...INTEREST_STYLE[priority.interestLevel], borderRadius: 999, padding: "5px 10px", fontSize: 12, fontWeight: 800 }}>
                      Interest {priority.interestLevel}
                    </span>
                  </>
                ) : null}
              </div>
              <ProvenanceBar
                source={(() => {
                  const es = company.enrichment_sources as Record<string, unknown> | null | undefined;
                  if (!es) return null;
                  // Prefer a top-level "source" marker; fall back to known import keys.
                  if (typeof es.source === "string") return es.source;
                  if (es.prospect_import_placeholder) return "prospect_csv_upload";
                  if (es.clickup_import) return "clickup_import";
                  if (es.account_sourcing) return "account_sourcing";
                  if (es.import) return "upload";
                  return null;
                })()}
                uploadedBy={(() => {
                  const es = company.enrichment_sources as Record<string, unknown> | null | undefined;
                  const placeholder = es?.prospect_import_placeholder as Record<string, unknown> | undefined;
                  return (placeholder?.uploaded_by as string | undefined) ?? null;
                })()}
                createdAt={company.created_at}
                updatedAt={company.updated_at}
                enrichedAt={company.enriched_at}
              />

              <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                {detailMetaItems.map((item) => (
                  <span
                    key={item}
                    style={{
                      borderRadius: 999,
                      padding: "6px 10px",
                      background: item.startsWith("Domain unresolved") ? "#fff4df" : "#f4f7fb",
                      border: `1px solid ${item.startsWith("Domain unresolved") ? "#ffe0b2" : colors.border}`,
                      color: item.startsWith("Domain unresolved") ? colors.amber : colors.sub,
                      fontSize: 12,
                      fontWeight: 600,
                      lineHeight: 1.2,
                    }}
                  >
                    {item}
                  </span>
                ))}
                {editingDomain ? (
                  <div
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "4px 6px",
                      borderRadius: 999,
                      border: `1px solid ${colors.border}`,
                      background: "#fff",
                    }}
                  >
                    <input
                      autoFocus
                      value={domainInput}
                      onChange={(e) => setDomainInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveDomain();
                        if (e.key === "Escape") setEditingDomain(false);
                      }}
                      placeholder="e.g. gainsight.com"
                      style={{
                        height: 30,
                        minWidth: 220,
                        borderRadius: 8,
                        border: `1px solid ${colors.primary}`,
                        padding: "0 10px",
                        fontSize: 13,
                        color: colors.text,
                        outline: "none",
                      }}
                    />
                    <button
                      type="button"
                      disabled={domainSaving}
                      onClick={handleSaveDomain}
                      style={{
                        height: 30,
                        padding: "0 10px",
                        borderRadius: 8,
                        border: `1px solid ${colors.primary}`,
                        background: colors.primary,
                        color: "#fff",
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: domainSaving ? "wait" : "pointer",
                      }}
                    >
                      {domainSaving ? "Saving..." : "Save"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingDomain(false)}
                      style={{
                        height: 30,
                        padding: "0 10px",
                        borderRadius: 8,
                        border: `1px solid ${colors.border}`,
                        background: "#fff",
                        color: colors.faint,
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setDomainInput(company.domain || "");
                      setEditingDomain(true);
                    }}
                    style={{
                      borderRadius: 999,
                      border: `1px solid ${company.domain.endsWith(".unknown") ? "#ffd7a0" : colors.border}`,
                      background: company.domain.endsWith(".unknown") ? "#fff7eb" : "#f7f9fc",
                      color: company.domain.endsWith(".unknown") ? colors.amber : colors.primary,
                      padding: "6px 10px",
                      fontSize: 12,
                      fontWeight: 700,
                      cursor: "pointer",
                    }}
                  >
                    Edit domain
                  </button>
                )}
                {!company.domain.endsWith(".unknown") ? (
                  <a href={`https://${company.domain}`} target="_blank" rel="noreferrer" style={{ color: colors.primary, textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 2px" }}>
                    <Globe size={13} /> Visit site <ExternalLink size={11} />
                  </a>
                ) : null}
              </div>

              <p style={{ marginTop: 14, marginBottom: 0, color: colors.sub, lineHeight: 1.75, fontSize: 15, maxWidth: 920 }}>
                {companySummary || "This account is in sourcing. Use the sections below to quickly decide fit, identify missing stakeholders, and shape the next prospecting move."}
              </p>

              {(company.domain.endsWith(".unknown") || !company.enriched_at) ? (
                <div style={{ marginTop: 14, border: `1px solid #ffe0b2`, background: "#fff9f0", borderRadius: 12, padding: "10px 12px", color: colors.amber, fontSize: 13, lineHeight: 1.55, display: "flex", gap: 12, justifyContent: "space-between", alignItems: "center", flexWrap: "wrap" }}>
                  <span>
                    {company.domain.endsWith(".unknown")
                      ? "This account still has an unresolved domain placeholder, so some web and paid enrichment may be incomplete."
                      : hasPartialResearchWithoutFullEnrichment
                        ? "Partial account intelligence is available below, but full enrichment has not completed yet."
                        : "This account has not completed enrichment yet."}
                  </span>
                  {company.domain.endsWith(".unknown") ? (
                    <button
                      type="button"
                      onClick={() => {
                        setDomainInput(company.domain || "");
                        setEditingDomain(true);
                      }}
                      style={{
                        borderRadius: 999,
                        border: "1px solid #ffd7a0",
                        background: "#fff",
                        color: colors.amber,
                        padding: "6px 10px",
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: "pointer",
                        whiteSpace: "nowrap",
                      }}
                    >
                      Fix domain
                    </button>
                  ) : null}
                </div>
              ) : null}

              {researchStatus.tone !== "idle" ? (
                <div
                  style={{
                    marginTop: 12,
                    borderRadius: 12,
                    padding: "10px 12px",
                    border:
                      researchStatus.tone === "running"
                        ? "1px solid #c8daf8"
                        : researchStatus.tone === "success"
                          ? "1px solid #b7e6c5"
                          : researchStatus.tone === "warning"
                            ? "1px solid #f0d4ac"
                            : "1px solid #f3c3ba",
                    background:
                      researchStatus.tone === "running"
                        ? "#f0f6ff"
                        : researchStatus.tone === "success"
                          ? "#f0fbf4"
                          : researchStatus.tone === "warning"
                            ? "#fff7eb"
                            : "#fff4f2",
                    color:
                      researchStatus.tone === "running"
                        ? "#1a4fa8"
                        : researchStatus.tone === "success"
                          ? "#1f7a47"
                          : researchStatus.tone === "warning"
                            ? "#a46206"
                            : "#c53030",
                    fontSize: 13,
                    fontWeight: 600,
                    lineHeight: 1.55,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  {researchStatus.tone === "running" ? <Loader2 size={14} className="animate-spin" /> : null}
                  <span>{researchStatus.message}</span>
                </div>
              ) : null}
            </div>

            <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
              <div style={{ display: "grid", gap: 8 }}>
              <button
                type="button"
                onClick={() => setShowTasksModal(true)}
                style={{ width: "100%", border: `1px solid #d5e5ff`, background: "#eef5ff", color: colors.primary, borderRadius: 12, padding: "10px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center", whiteSpace: "nowrap" }}
              >
                <CheckCircle2 size={13} /> Tasks
              </button>
              <button
                type="button"
                onClick={() => setShowActivityModal(true)}
                style={{ width: "100%", border: `1px solid ${colors.border}`, background: "#ffffff", color: colors.text, borderRadius: 12, padding: "10px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center", whiteSpace: "nowrap" }}
              >
                <MessageSquare size={13} /> Account Activity {activityEntries.length > 0 ? `(${activityEntries.length})` : ""}
              </button>
              <button
                onClick={async () => {
                  setIcpResearching(true);
                  let progressToastShown = false;
                  try {
                    setResearchStatus({
                      tone: "running",
                      message: "ICP research started. Beacon is refreshing account fit, signals, and recommendations in the background.",
                    });
                    toast.info(
                      "Beacon started ICP research for this account. You can keep using the app while it refreshes.",
                      "Research started",
                    );
                    await accountSourcingApi.icpResearch(company.id);
                    const settled = await refreshUntilSettled({
                      stopWhen: (next) =>
                        `${next.enriched_at || ""}|${next.icp_score ?? ""}|${next.domain}|${cacheTs((next.enrichment_cache || {}) as Record<string, unknown>, "icp_analysis") || ""}|${cacheTs((next.enrichment_cache || {}) as Record<string, unknown>, "research_quality") || ""}` !== researchFingerprint ||
                        !next.domain.endsWith(".unknown"),
                      onProgress: ({ attempt, attempts }) => {
                        setResearchStatus({
                          tone: "running",
                          message:
                            attempt === 1
                              ? "Beacon is collecting fresh account data and checking for updated ICP signals."
                              : `Beacon is still researching this account and checking for fresh results (${attempt}/${attempts}).`,
                        });
                        if (!progressToastShown && attempt >= 2) {
                          progressToastShown = true;
                          toast.info(
                            "Beacon is still researching this account in the background. You can keep working while we refresh the latest signals.",
                            "Research in progress",
                          );
                        }
                      },
                    });
                    if (settled) {
                      setResearchStatus({
                        tone: "success",
                        message: "ICP research finished. The latest account intelligence is now loaded.",
                      });
                      toast.success(
                        "Beacon finished refreshing ICP research for this account.",
                        "Research complete",
                      );
                    } else {
                      setResearchStatus({
                        tone: "warning",
                        message: "Research is still running in the background. We’ll keep the current view until fresh results land.",
                      });
                      toast.warning(
                        "ICP research is still running. Give it another minute and refresh if needed.",
                        "Still working",
                      );
                    }
                  } catch (error) {
                    setResearchStatus({
                      tone: "error",
                      message: "Beacon couldn’t start or finish ICP research right now. Please try again once.",
                    });
                    toast.error(
                      error instanceof Error ? error.message : "Unable to refresh ICP research right now.",
                      "Research failed",
                    );
                  } finally {
                    setIcpResearching(false);
                    load();
                  }
                }}
                style={{ width: "100%", border: `1px solid #d0e8d0`, background: "#eef8ef", color: "#1f8f5f", borderRadius: 12, padding: "10px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center", whiteSpace: "nowrap" }}
              >
                {icpResearching ? <Loader2 size={13} className="animate-spin" /> : <Brain size={13} />} Refresh ICP Research
              </button>

              <button
                onClick={async () => {
                  setRe(true);
                  try {
                    await accountSourcingApi.reEnrichCompany(company.id);
                    const currentEnrichedAt = company.enriched_at;
                    await refreshUntilSettled({
                      stopWhen: (next) =>
                        next.enriched_at !== currentEnrichedAt ||
                        !next.domain.endsWith(".unknown"),
                    });
                  } finally {
                    setRe(false);
                    load();
                  }
                }}
                style={{ width: "100%", border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 12, padding: "10px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center", whiteSpace: "nowrap" }}
              >
                {re ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Re-enrich
              </button>

              <button
                type="button"
                onClick={() => setShowDealModal(true)}
                style={{ width: "100%", border: `1px solid #cde5ff`, background: "#eff7ff", color: "#1f5ecc", borderRadius: 12, padding: "10px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center", whiteSpace: "nowrap" }}
              >
                <Plus size={13} /> Add to Deal
              </button>

              <button
                type="button"
                onClick={async () => {
                  try {
                    const account = prompt("Your connected sending email in Instantly:", "");
                    if (!account) return;
                    const name = prompt("Campaign name (optional):", `${company.name} Outreach`);
                    const { outreachApi } = await import("../lib/api");
                    const result = await outreachApi.launchCompanyCampaign(company.id, account.trim(), (name || "").trim() || undefined);
                    alert(`Campaign launched!\n${result.pushed} prospects added.\nFailed: ${result.failed}\nCampaign ID: ${result.instantly_campaign_id}`);
                    load();
                  } catch (err: unknown) {
                    alert(`Launch failed: ${err instanceof Error ? err.message : String(err)}`);
                  }
                }}
                style={{ width: "100%", border: "1px solid #ffd4b8", background: "#fdf5f0", color: "#d15b20", borderRadius: 12, padding: "10px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center", whiteSpace: "nowrap" }}
              >
                <Rocket size={13} /> Launch to Instantly
              </button>

              {isAdmin && (
                <button
                  type="button"
                  onClick={async () => {
                    if (!window.confirm(`Delete account "${company.name}" and all its data? This cannot be undone.`)) return;
                    try {
                      await companiesApi.delete(company.id);
                      nav("/account-sourcing");
                    } catch { /* swallow */ }
                  }}
                  style={{ width: "100%", border: "1px solid #fecaca", background: "#fff5f5", color: "#dc2626", borderRadius: 12, padding: "10px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center", whiteSpace: "nowrap" }}
                >
                  <Trash2 size={13} /> Delete Account
                </button>
              )}
              </div>

              <div style={{ ...cardStyle, padding: "12px 14px", display: "grid", gap: 10 }}>
                <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.4 }}>ACCOUNT TEAM</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
                  <div style={{ display: "grid", gap: 6 }}>
                    <div style={{ color: colors.faint, fontSize: 11, fontWeight: 700, letterSpacing: 0.35 }}>ACCOUNT AE</div>
                    <AssignDropdown
                      entityType="company"
                      entityId={company.id}
                      currentAssignedId={company.assigned_to_id}
                      currentAssignedName={company.assigned_to_name || company.assigned_rep_name || company.assigned_rep_email || company.assigned_rep}
                      onAssigned={() => load()}
                      role="ae"
                      label="Assign AE"
                    />
                  </div>
                  <div style={{ display: "grid", gap: 6 }}>
                    <div style={{ color: colors.faint, fontSize: 11, fontWeight: 700, letterSpacing: 0.35 }}>ACCOUNT SDR</div>
                    <AssignDropdown
                      entityType="company"
                      entityId={company.id}
                      currentAssignedId={company.sdr_id}
                      currentAssignedName={company.sdr_name || company.sdr_email}
                      onAssigned={() => load()}
                      role="sdr"
                      label="Assign SDR"
                    />
                  </div>
                </div>
                <div style={{ color: colors.sub, fontSize: 12.5, lineHeight: 1.55 }}>
                  Account-level AE and SDR show up as the default team on prospects, and individual stakeholders can still be adjusted later.
                </div>
              </div>
            </div>
          </div>

          <div style={{ marginTop: 16, paddingTop: 14, borderTop: `1px solid ${colors.border}`, display: "flex", gap: 16, flexWrap: "wrap", color: colors.faint, fontSize: 13 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><Clock size={12} /> Enriched: {ts(company.enriched_at)}</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><Clock size={12} /> Created: {formatDate(company.created_at)}</span>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
          <MetricCard
            label="ICP Score"
            value={`${company.icp_score ?? 0}`}
            hint="Current fit score based on firmographics, signals, and detected tooling."
            tone={tier === "hot" ? "warm" : "primary"}
          />
          <MetricCard
            label="TAL Verdict"
            value={typeof icpAnalysis?.classification === "string" ? String(icpAnalysis.classification) : "N/A"}
            hint={typeof icpAnalysis?.fit_type === "string" ? String(icpAnalysis.fit_type) : "Verdict pending"}
            tone={String(icpAnalysis?.classification || "").toLowerCase() === "target" ? "green" : "primary"}
          />
          <MetricCard
            label="Research Quality"
            value={evidenceLevel || "Pending"}
            hint={typeof evidenceScore === "number" ? `Evidence score ${evidenceScore}` : "Waiting for enough live evidence."}
            tone={evidenceLevel === "strong" ? "green" : evidenceLevel === "partial" ? "primary" : "warm"}
          />
          <MetricCard
            label="Best Entry"
            value={entryPersonaTitle || "Need mapping"}
            hint={entryPersonaRelevance || "Lead with the operator most likely to own rollout pain."}
            tone="primary"
          />
          <MetricCard
            label="Priority"
            value={priority ? `${priority.priorityScore}` : "0"}
            hint={priority ? `${priority.priorityBand} priority based on fit, timing, evidence, and sales feedback.` : "Priority unavailable"}
            tone={priority?.priorityBand === "high" ? "green" : "primary"}
          />
          <MetricCard
            label="Contacts"
            value={String(relevantContacts.length)}
            hint="Relevant stakeholders ready for prospecting and meeting prep."
            tone="neutral"
            onClick={scrollToContacts}
          />
        </div>

        <div style={{ display: "grid", gap: 14 }}>
            <Section title="Account Thesis" icon={<Target size={15} color={colors.primary} />}>
              {toBriefItems(icpAnalysis?.core_focus, 3).length > 0 && (
                <div style={{ background: "#f8fbff", border: `1px solid ${colors.border}`, borderRadius: 12, padding: "12px 16px" }}>
                  <div style={{ color: colors.faint, fontWeight: 800, fontSize: 11, letterSpacing: 0.4, marginBottom: 6 }}>WHAT THEY DO & WHY IMPLEMENTATIONS ARE COMPLEX</div>
                  <div style={{ color: colors.sub, fontSize: 13.5, lineHeight: 1.65 }}>
                    {toBriefItems(icpAnalysis?.core_focus, 3).join(" ")}
                  </div>
                </div>
              )}

              <div>
                <div style={{ color: colors.text, fontWeight: 800, marginBottom: 8 }}>Why this account fits Beacon</div>
                <ListCard title="Fit Summary" items={fitReasonItems} empty="No fit reasoning available yet." />
                <SourceLinks items={fitSources} />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
                <div>
                  <ListCard title="Intent Summary" items={intentReasonItems.length ? intentReasonItems : intentSummary ? [intentSummary] : []} empty="No intent summary available." />
                  <SourceLinks items={intentSources} />
                </div>
              </div>
            </Section>

            {icpAnalysis ? (
              <Section title="Sales Brief" icon={<Brain size={15} color="#7a2dd9" />}>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
                  {icpAnalyzedAt && (
                    <span style={{ fontSize: 11, color: colors.faint, display: "inline-flex", alignItems: "center", gap: 4 }}>
                      <Clock size={11} /> Analyzed: {ts(icpAnalyzedAt)}
                    </span>
                  )}
                  {typeof icpAnalysis.confidence === "string" && (
                    <span style={{
                      fontSize: 11, fontWeight: 700, borderRadius: 999, padding: "2px 8px",
                      background: icpAnalysis.confidence === "high" ? colors.greenSoft : icpAnalysis.confidence === "medium" ? colors.amberSoft : colors.redSoft,
                      color: icpAnalysis.confidence === "high" ? colors.green : icpAnalysis.confidence === "medium" ? colors.amber : colors.red,
                    }}>
                      {String(icpAnalysis.confidence).toUpperCase()} CONFIDENCE
                    </span>
                  )}
                  {typeof icpAnalysis._source === "string" && icpAnalysis._source === "claude_icp_pipeline" && (
                    <span style={{ fontSize: 11, fontWeight: 700, borderRadius: 999, padding: "2px 8px", background: "#f3eaff", color: "#7a2dd9" }}>
                      AI-RESEARCHED
                    </span>
                  )}
                  {evidenceLevel && (
                    <span style={{ fontSize: 11, fontWeight: 700, borderRadius: 999, padding: "2px 8px", background: "#eef5ff", color: colors.primary }}>
                      {evidenceLevel.toUpperCase()} EVIDENCE
                    </span>
                  )}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
                  <MetricCard
                    label="ICP Fit"
                    value={typeof icpAnalysis.icp_fit_score === "number" ? `${icpAnalysis.icp_fit_score}/10` : "N/A"}
                    hint={typeof icpAnalysis.classification === "string" ? String(icpAnalysis.classification) : "TAL classification"}
                    tone={Number(icpAnalysis.icp_fit_score) >= 7 ? "green" : Number(icpAnalysis.icp_fit_score) >= 4 ? "primary" : "warm"}
                  />
                  <MetricCard
                    label="Intent"
                    value={typeof icpAnalysis.intent_score === "number" ? `${icpAnalysis.intent_score}/10` : "N/A"}
                    hint={typeof icpAnalysis.fit_type === "string" ? String(icpAnalysis.fit_type) : "Fit type"}
                    tone={Number(icpAnalysis.intent_score) >= 7 ? "green" : Number(icpAnalysis.intent_score) >= 4 ? "primary" : "warm"}
                  />
                  <MetricCard
                    label="Financial"
                    value={icpAnalysis.financial_capacity_met ? "Met" : "Not Met"}
                    hint={typeof icpAnalysis.revenue_funding === "string" ? String(icpAnalysis.revenue_funding).slice(0, 80) : "150K+ ACV capacity"}
                    tone={icpAnalysis.financial_capacity_met ? "green" : "warm"}
                  />
                </div>
                <SourceLinks items={revenueSources} />

                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
                  <div>
                    <ListCard title="Why Beacon Should Care" items={beaconCareItems} empty="No account thesis generated yet." />
                    <SourceLinks items={thesisSources} />
                  </div>
                  <div>
                    <ListCard title="Why Now" items={whyNowItems} empty="No timing trigger captured yet." />
                    <SourceLinks items={whyNowSources} />
                  </div>
                  <div>
                    <ListCard title="How To Engage" items={engageItems} empty="No Beacon angle generated yet." />
                    <SourceLinks items={angleSources.length ? angleSources : engageSources} />
                  </div>
                  <div>
                    <ListCard title="What To Say First" items={starterItems} empty="No opening hook captured yet." />
                    <SourceLinks items={hookSources} />
                  </div>
                </div>

                <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, color: colors.green, fontWeight: 700, fontSize: 13, marginBottom: 8 }}>
                    <Radar size={14} /> Positive Signals
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
                    {positiveSignalCards.map((item) => (
                      <div key={item.title} style={{ background: "#f0faf4", border: "1px solid #d0e8d0", borderRadius: 10, padding: "10px 14px" }}>
                        <div style={{ color: colors.green, fontSize: 11, fontWeight: 700, letterSpacing: 0.3 }}>{item.title.toUpperCase()}</div>
                        <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6, marginTop: 4 }}>{item.value}</div>
                        <SourceLinks
                          items={
                            item.title === "Hiring" ? hiringSources :
                            item.title === "Leadership" ? leadershipSources :
                            item.title === "Expansion" ? fundingSources :
                            item.title === "Events" ? eventsSources :
                            reviewsSources
                          }
                        />
                      </div>
                    ))}
                  </div>
                </div>

                <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, color: colors.red, fontWeight: 700, fontSize: 13, marginBottom: 8 }}>
                    <AlertTriangle size={14} /> Negative Signals
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
                    {negativeSignalCards.map((item) => (
                      <div key={item.title} style={{ background: "#fff5f5", border: "1px solid #ffd0d8", borderRadius: 10, padding: "10px 14px" }}>
                        <div style={{ color: colors.red, fontSize: 11, fontWeight: 700, letterSpacing: 0.3 }}>{item.title.toUpperCase()}</div>
                        <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6, marginTop: 4 }}>{item.value}</div>
                        <SourceLinks items={item.title === "AI Overlap" ? aiOverlapSources : strategicSources} />
                      </div>
                    ))}
                    {negativeSignalCards.length === 0 && (
                      <div style={{ color: colors.green, fontSize: 13 }}>No negative signals detected.</div>
                    )}
                  </div>
                </div>

                {/* Implementation Cycle Duration */}
                {implCycle && (
                  <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, color: colors.violet, fontWeight: 700, fontSize: 13, marginBottom: 8 }}>
                      <Clock size={14} /> Implementation Cycle
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 8 }}>
                      {typeof implCycle.enterprise === "string" && implCycle.enterprise && (
                        <div style={{ background: "#f3eaff", border: "1px solid #e0d0f5", borderRadius: 10, padding: "10px 14px" }}>
                          <div style={{ color: colors.violet, fontSize: 11, fontWeight: 700, letterSpacing: 0.3 }}>ENTERPRISE</div>
                          <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6, marginTop: 4 }}>{implCycle.enterprise}</div>
                        </div>
                      )}
                      {typeof implCycle.mid_market === "string" && implCycle.mid_market && (
                        <div style={{ background: "#eef5ff", border: "1px solid #d5e5ff", borderRadius: 10, padding: "10px 14px" }}>
                          <div style={{ color: colors.primary, fontSize: 11, fontWeight: 700, letterSpacing: 0.3 }}>MID-MARKET</div>
                          <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6, marginTop: 4 }}>{implCycle.mid_market}</div>
                        </div>
                      )}
                      {typeof implCycle.minimum === "string" && implCycle.minimum && (
                        <div style={{ background: "#e8f8f0", border: "1px solid #c8e8d8", borderRadius: 10, padding: "10px 14px" }}>
                          <div style={{ color: colors.green, fontSize: 11, fontWeight: 700, letterSpacing: 0.3 }}>MINIMUM</div>
                          <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6, marginTop: 4 }}>{implCycle.minimum}</div>
                        </div>
                      )}
                    </div>
                    {typeof implCycle.key_drivers === "string" && implCycle.key_drivers && (
                      <div style={{ marginTop: 8, background: "#f8f5ff", border: `1px solid ${colors.border}`, borderRadius: 10, padding: "10px 14px" }}>
                        <div style={{ color: colors.faint, fontSize: 11, fontWeight: 700, letterSpacing: 0.3, marginBottom: 4 }}>KEY DRIVERS</div>
                        <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.6 }}>{implCycle.key_drivers}</div>
                      </div>
                    )}
                    {typeof implCycle.evidence === "string" && implCycle.evidence && (
                      <div style={{ marginTop: 6, color: colors.faint, fontSize: 12, fontStyle: "italic", lineHeight: 1.5 }}>
                        {implCycle.evidence}
                      </div>
                    )}
                  </div>
                )}

                {/* Sales Strategy */}
                <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, color: colors.text, fontWeight: 700, fontSize: 13, marginBottom: 8 }}>
                    <Target size={14} /> Action Plan
                  </div>
                  <div style={{ display: "grid", gap: 10 }}>
                    <ListCard title="Outreach Strategy" items={outreachItems} empty="No outreach strategy captured yet." />
                  </div>
                </div>
              </Section>
            ) : null}

            {allNewsItems.length > 0 && (
              <Section title="Recent News & Signals" icon={<Newspaper size={15} color={colors.primary} />}>
                <div style={{ display: "grid", gap: 8 }}>
                  {hiringNews.length > 0 && (
                    <div>
                      <div style={{ color: colors.green, fontSize: 11, fontWeight: 700, letterSpacing: 0.3, marginBottom: 6 }}>HIRING SIGNALS</div>
                      <div style={{ display: "grid", gap: 6 }}>
                        {hiringNews.map((item, idx) => (
                          <NewsSignalCard key={`h-${idx}`} item={item} borderColor="#d0e8d0" background="#f0faf4" />
                        ))}
                      </div>
                    </div>
                  )}
                  {fundingNews.length > 0 && (
                    <div>
                      <div style={{ color: colors.primary, fontSize: 11, fontWeight: 700, letterSpacing: 0.3, marginBottom: 6 }}>FUNDING & INVESTMENT</div>
                      <div style={{ display: "grid", gap: 6 }}>
                        {fundingNews.map((item, idx) => (
                          <NewsSignalCard key={`f-${idx}`} item={item} borderColor="#cde5ff" background="#eff7ff" />
                        ))}
                      </div>
                    </div>
                  )}
                  {productNews.length > 0 && (
                    <div>
                      <div style={{ color: colors.violet, fontSize: 11, fontWeight: 700, letterSpacing: 0.3, marginBottom: 6 }}>PRODUCT & PARTNERSHIPS</div>
                      <div style={{ display: "grid", gap: 6 }}>
                        {productNews.map((item, idx) => (
                          <NewsSignalCard key={`p-${idx}`} item={item} borderColor="#e0d5f5" background="#f8f4ff" />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </Section>
            )}

            <Section title="Competitive Landscape" icon={<Radar size={15} color={colors.primary} />}>
              {competitorCards.length === 0 ? (
                <div style={{ color: colors.faint }}>No competitor view has been captured yet.</div>
              ) : (
                <div style={{ display: "grid", gap: 10 }}>
                  {hasPartialResearchWithoutFullEnrichment && (
                    <div style={{ border: "1px solid #ffe0b2", background: "#fff9f0", color: colors.amber, borderRadius: 12, padding: "10px 12px", fontSize: 12.5, lineHeight: 1.55 }}>
                      Partial research is present from import or cached intelligence. Run full enrichment before treating this as a completed account profile.
                    </div>
                  )}
                  {competitorCards.map((item, idx) => (
                    <div key={`competitor-${idx}`} style={{ border: `1px solid ${colors.border}`, background: "#fbfdff", borderRadius: 14, padding: "14px 16px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                        <div style={{ color: colors.text, fontWeight: 800 }}>{String(item.name || `Competitor ${idx + 1}`)}</div>
                        {item.website ? (
                          <a href={String(item.website)} target="_blank" rel="noreferrer" style={{ color: colors.primary, fontSize: 12, fontWeight: 700, textDecoration: "none" }}>
                            Visit site <ExternalLink size={11} style={{ display: "inline" }} />
                          </a>
                        ) : null}
                      </div>
                      {item.summary ? <div style={{ marginTop: 8, color: colors.sub, fontSize: 13.5, lineHeight: 1.6 }}>{String(item.summary)}</div> : null}
                      {item.pitch_angle ? (
                        <div style={{ marginTop: 10, borderRadius: 10, background: "#eef5ff", border: "1px solid #d5e5ff", padding: "10px 12px" }}>
                          <div style={{ color: colors.primary, fontSize: 11, fontWeight: 800, letterSpacing: 0.3 }}>PITCH ANGLE</div>
                          <div style={{ marginTop: 4, color: colors.sub, fontSize: 13, lineHeight: 1.6 }}>{String(item.pitch_angle)}</div>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </Section>

            <div id="stakeholders-section">
            <Section title={`Stakeholders (${relevantContacts.length})`} icon={<Users size={15} color={colors.primary} />}>
              <div
                style={{
                  marginBottom: 12,
                  borderRadius: 12,
                  border: "1px solid #f5ddaa",
                  background: "#fff8e8",
                  padding: "10px 12px",
                  color: "#6c5a2f",
                  fontSize: 12.5,
                  lineHeight: 1.6,
                }}
              >
                Company research is temporarily not pulling contacts automatically. Upload prospects from the Prospecting page or add stakeholders here when you have them.
              </div>
              {/* Add Stakeholder button */}
              <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
                <button
                  onClick={() => setShowAddStakeholder(true)}
                  style={{
                    display: "flex", alignItems: "center", gap: 6,
                    padding: "7px 14px", borderRadius: 10, fontSize: 12, fontWeight: 700,
                    background: colors.primary, color: "#fff", border: "none", cursor: "pointer",
                  }}
                >
                  <UserPlus size={13} /> Add Stakeholder
                </button>
              </div>

              {/* Buying Committee grouped view */}
              {relevantContacts.length > 0 && (() => {
                const grouped: Record<string, Contact[]> = {};
                for (const c of relevantContacts) {
                  const role = canonicalPersona(c.persona, c.persona_type);
                  (grouped[role] ??= []).push(c);
                }
                const roleOrder = ["champion", "buyer", "evaluator", "influencer", "implementation_owner", "blocker", "unknown"];
                const sortedRoles = roleOrder.filter((r) => grouped[r]?.length);
                if (sortedRoles.length > 1 || (sortedRoles.length === 1 && sortedRoles[0] !== "unknown")) {
                  return (
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ color: colors.faint, fontWeight: 700, fontSize: 12, letterSpacing: 0.3, marginBottom: 8 }}>BUYING COMMITTEE</div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                        {sortedRoles.map((role) => (
                          <span key={role} style={{
                            ...PERSONA_STYLE[role] ?? { background: "#f1f5f9", color: "#64748b" },
                            borderRadius: 999, fontSize: 11, padding: "4px 10px", fontWeight: 700,
                          }}>
                            {PERSONA_LABEL[role] ?? role} ({grouped[role].length})
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                }
                return null;
              })()}

              {icpPersonas.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ color: colors.faint, fontWeight: 700, fontSize: 12, letterSpacing: 0.3, marginBottom: 8 }}>KEY PERSONAS</div>
                  <div style={{ display: "grid", gap: 8 }}>
                    {icpPersonas.map((p, i) => (
                      <div key={i} style={{ background: "#fbfdff", border: `1px solid ${colors.border}`, borderRadius: 10, padding: "10px 14px" }}>
                        <div style={{ color: colors.text, fontSize: 13, fontWeight: 700 }}>{p.title || "Unknown"}</div>
                        {p.name && <div style={{ color: colors.primary, fontSize: 12 }}>{p.name}</div>}
                        {p.relevance && <div style={{ color: colors.faint, fontSize: 12, marginTop: 2 }}>{p.relevance}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {relevantContacts.length === 0 && icpPersonas.length === 0 ? (
                <div style={{ color: colors.faint }}>No prospects have been added to this account yet.</div>
              ) : relevantContacts.length > 0 ? (
                <>
                  {/* F8 selection toolbar */}
                  <div style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    gap: 10, padding: "8px 12px", marginBottom: 8,
                    border: `1px solid ${colors.border}`, borderRadius: 10, background: "#f7fafd",
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, color: colors.sub, cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={relevantContacts.length > 0 && relevantContacts.every((c) => selectedContactIds.has(c.id))}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedContactIds(new Set(relevantContacts.map((c) => c.id)));
                            } else {
                              setSelectedContactIds(new Set());
                            }
                          }}
                        />
                        Select all
                      </label>
                      <span style={{ fontSize: 12, color: colors.sub }}>
                        {selectedContactIds.size} selected
                      </span>
                    </div>
                    <button
                      onClick={() => { setBulkLaunchOpen(true); setBulkLaunchError(""); }}
                      disabled={selectedContactIds.size === 0}
                      style={{
                        padding: "6px 12px", borderRadius: 8, fontSize: 12, fontWeight: 700,
                        background: selectedContactIds.size === 0 ? "#e8eef5" : colors.primary,
                        color: selectedContactIds.size === 0 ? "#9aafbe" : "#fff",
                        border: "none", cursor: selectedContactIds.size === 0 ? "not-allowed" : "pointer",
                      }}
                    >
                      Add {selectedContactIds.size || ""} to sequence
                    </button>
                  </div>
                  <div style={{ display: "grid", gap: 10 }}>
                    {relevantContacts.map((c) => (
                      <div key={c.id} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                        <input
                          type="checkbox"
                          checked={selectedContactIds.has(c.id)}
                          onChange={() => toggleContactSelected(c.id)}
                          style={{ marginTop: 16 }}
                          aria-label={`Select ${c.first_name} ${c.last_name}`}
                        />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <ContactItem contact={c} onChanged={load} />
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </Section>
            </div>

            <Section title="Website Research" icon={<Globe size={15} color={colors.primary} />}>
              {websiteText ? (
                <div style={{ border: `1px solid ${colors.border}`, background: "#fbfdff", borderRadius: 14, padding: "16px 18px", maxHeight: 420, overflow: "auto" }}>
                  <div style={{ color: colors.sub, lineHeight: 1.75, fontSize: 14, whiteSpace: "pre-wrap" }}>{websiteText.slice(0, 4000)}</div>
                </div>
              ) : (
                <div style={{ color: colors.faint }}>No website scrape content available.</div>
              )}
            </Section>
            <Section title="Data Freshness" icon={<TrendingUp size={15} color={colors.primary} />}>
              {[
                ["Website Scrape", cacheTs(cache, "web_scrape")],
                ["Intent Signals", cacheTs(cache, "intent_signals")],
                ["Apollo Company", cacheTs(cache, "apollo_company")],
                ["Apollo Contacts", cacheTs(cache, "apollo_contacts")],
                ["Committee Coverage", cacheTs(cache, "committee_coverage")],
                ["AI Summary", cacheTs(cache, "ai_summary") || cacheTs(cache, "icp_analysis")],
              ].map(([name, t]) => (
                <div key={String(name)} style={{ display: "flex", justifyContent: "space-between", gap: 8, color: colors.sub }}>
                  <span style={{ fontWeight: 700 }}>{name}</span>
                  <span style={{ color: colors.faint, fontSize: 13 }}>{t ? ts(String(t)) : "-"}</span>
                </div>
              ))}

              {apollo ? (
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${colors.border}` }}>
                  <div style={{ fontWeight: 800, color: colors.text, marginBottom: 6 }}>Apollo Snapshot</div>
                  {Object.entries(apollo).slice(0, 8).map(([k, v]) => (
                    <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 8, color: colors.sub, fontSize: 13, marginBottom: 3 }}>
                      <span>{k}</span>
                      <span>{String(v)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </Section>
        </div>
      </div>

      <TaskCenterModal
        isOpen={showTasksModal}
        onClose={() => setShowTasksModal(false)}
        entityType="company"
        entityId={company.id}
        entityLabel={company.name}
        onChanged={() => {
          void load();
        }}
      />

      {showActivityModal && (
        <>
          <div onClick={() => setShowActivityModal(false)} style={{
            position: "fixed", inset: 0, background: "rgba(15,23,42,0.28)",
            backdropFilter: "blur(3px)", zIndex: 100,
          }} />
          <div style={{
            position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
            zIndex: 101, width: "min(860px, 92vw)", maxHeight: "82vh", background: "#fff",
            borderRadius: 18, boxShadow: "0 20px 60px rgba(15,23,42,0.18)",
            display: "grid", gridTemplateRows: "auto minmax(0,1fr)", overflow: "hidden",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 14, padding: "20px 22px 16px", borderBottom: `1px solid ${colors.border}` }}>
              <div style={{ display: "grid", gap: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontWeight: 800 }}>
                  <MessageSquare size={16} color={colors.primary} />
                  <span>Account Activity</span>
                </div>
                <div style={{ color: colors.sub, fontSize: 13, lineHeight: 1.55 }}>
                  Review assignment changes, enrichment updates, and sourcing actions without losing your place on the company page.
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowActivityModal(false)}
                style={{
                  width: 34, height: 34, borderRadius: 10, border: `1px solid ${colors.border}`,
                  background: "#fff", color: colors.sub, display: "grid", placeItems: "center", cursor: "pointer",
                }}
              >
                <X size={16} />
              </button>
            </div>
            <div style={{ padding: "14px 18px 0", borderBottom: `1px solid ${colors.border}`, background: "#fff" }}>
              <div style={{ display: "flex", gap: 8 }}>
                <textarea
                  value={noteInput}
                  onChange={(e) => setNoteInput(e.target.value)}
                  placeholder="Add a note..."
                  rows={2}
                  style={{
                    flex: 1, resize: "vertical", border: `1px solid ${colors.border}`,
                    borderRadius: 10, padding: "9px 12px", fontSize: 13, color: colors.text,
                    fontFamily: "inherit", outline: "none",
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault();
                      if (!noteInput.trim() || noteSaving || !company) return;
                      setNoteSaving(true);
                      accountSourcingApi.addCompanyNote(company.id, noteInput.trim()).then((res) => {
                          setCompany((prev) => prev ? { ...prev, enrichment_cache: { ...(prev.enrichment_cache || {}), activity_log: res.activity_log } } : prev);
                        setNoteInput("");
                      }).catch(() => {}).finally(() => setNoteSaving(false));
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={!noteInput.trim() || noteSaving}
                  onClick={async () => {
                    if (!noteInput.trim() || noteSaving || !company) return;
                    setNoteSaving(true);
                    try {
                      const res = await accountSourcingApi.addCompanyNote(company.id, noteInput.trim());
                      setCompany((prev) => prev ? { ...prev, enrichment_cache: { ...(prev.enrichment_cache || {}), activity_log: res.activity_log } } : prev);
                      setNoteInput("");
                    } catch { /* ignore */ } finally { setNoteSaving(false); }
                  }}
                  style={{
                    padding: "0 16px", borderRadius: 10, border: "none", cursor: noteInput.trim() ? "pointer" : "not-allowed",
                    background: noteInput.trim() ? colors.primary : colors.border,
                    color: noteInput.trim() ? "#fff" : colors.faint,
                    fontWeight: 700, fontSize: 13, alignSelf: "flex-end", height: 38, flexShrink: 0,
                  }}
                >
                  {noteSaving ? "..." : "Save"}
                </button>
              </div>
              <div style={{ color: colors.faint, fontSize: 11, padding: "4px 2px 10px" }}>⌘↵ or Ctrl+↵ to save quickly</div>
            </div>
            <div style={{ overflowY: "auto", padding: 18, background: "#fbfdff", display: "grid", gap: 10 }}>
              {activityEntries.length === 0 ? (
                <div style={{ color: colors.faint, padding: "8px 4px" }}>No account activity captured yet.</div>
              ) : (
                [...activityEntries].reverse().map((entry, idx) => {
                  const isNote = String(entry.action) === "note";
                  return (
                    <div key={`activity-${idx}`} style={{
                      border: `1px solid ${isNote ? "#d0e8ff" : colors.border}`,
                      background: isNote ? "#f0f7ff" : "#ffffff",
                      borderRadius: 14, padding: "12px 14px",
                    }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                        <div style={{ color: colors.text, fontWeight: isNote ? 500 : 700, fontSize: isNote ? 13.5 : 13, lineHeight: 1.55 }}>
                          {isNote ? String(entry.message || "") : `${String(entry.message || entry.action || "Update")}`}
                        </div>
                        <div style={{ color: colors.faint, fontSize: 12 }}>{typeof entry.at === "string" ? ts(String(entry.at)) : "-"}</div>
                      </div>
                      <div style={{ color: colors.sub, fontSize: 12.5, marginTop: 4 }}>
                        {entry.actor_name ? `${isNote ? "Note by" : "By"} ${String(entry.actor_name)}` : "By system"}
                        {entry.actor_email ? ` • ${String(entry.actor_email)}` : ""}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </>
      )}

      {/* ── Add Stakeholder Modal ──────────────────────────────────── */}
      {showDealModal && (
        <>
          <div onClick={() => setShowDealModal(false)} style={{
            position: "fixed", inset: 0, background: "rgba(15,23,42,0.22)",
            backdropFilter: "blur(3px)", zIndex: 100,
          }} />
          <div style={{
            position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
            zIndex: 101, width: 460, maxWidth: "90vw", background: "#fff",
            borderRadius: 18, boxShadow: "0 20px 60px rgba(15,23,42,0.18)",
            padding: "26px 26px 22px", display: "grid", gap: 14,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ fontSize: 17, fontWeight: 700, color: colors.text, margin: 0 }}>Add to Deal</h3>
              <button onClick={() => setShowDealModal(false)} style={{ background: "none", border: "none", cursor: "pointer", color: colors.faint, fontSize: 18 }}>x</button>
            </div>
            <input
              value={dealForm.name}
              onChange={(e) => setDealForm((current) => ({ ...current, name: e.target.value }))}
              placeholder="Deal name"
              style={{ border: `1px solid ${colors.border}`, borderRadius: 10, padding: "11px 12px", fontSize: 13, color: colors.text }}
            />
            {existingCompanyDeals.length ? (
              <div style={{ display: "grid", gap: 8, padding: "10px 12px", borderRadius: 12, background: "#f8fbff", border: `1px solid ${colors.border}` }}>
                <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.35 }}>
                  EXISTING DEALS
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {existingCompanyDeals.map((deal) => (
                    <button
                      key={deal.id}
                      type="button"
                      onClick={() => nav(`/deals/${deal.id}`)}
                      style={{
                        borderRadius: 999,
                        border: "1px solid #dce8f4",
                        background: "#fff",
                        color: "#1f5ecc",
                        padding: "6px 10px",
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      {deal.name}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <input
                value={dealForm.value}
                onChange={(e) => setDealForm((current) => ({ ...current, value: e.target.value }))}
                placeholder="Value (optional)"
                style={{ border: `1px solid ${colors.border}`, borderRadius: 10, padding: "11px 12px", fontSize: 13, color: colors.text }}
              />
              <input
                type="date"
                value={dealForm.close_date_est}
                onChange={(e) => setDealForm((current) => ({ ...current, close_date_est: e.target.value }))}
                style={{ border: `1px solid ${colors.border}`, borderRadius: 10, padding: "11px 12px", fontSize: 13, color: colors.text }}
              />
            </div>
            <select
              value={dealForm.stage}
              onChange={(e) => setDealForm((current) => ({ ...current, stage: e.target.value }))}
              style={{ border: `1px solid ${colors.border}`, borderRadius: 10, padding: "11px 12px", fontSize: 13, color: colors.text, background: "#fff" }}
            >
              {availableDealStages.map((stage) => (
                <option key={stage.id} value={stage.id}>{stage.label}</option>
              ))}
            </select>
            {dealError ? <div style={{ color: colors.red, fontSize: 12, fontWeight: 700 }}>{dealError}</div> : null}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button onClick={() => setShowDealModal(false)} style={{
                padding: "9px 18px", borderRadius: 10, fontSize: 13, fontWeight: 600,
                background: "#f1f5f9", color: colors.sub, border: "none", cursor: "pointer",
              }}>Cancel</button>
              <button onClick={() => void handleCreateDeal()} disabled={creatingDeal} style={{
                padding: "9px 18px", borderRadius: 10, fontSize: 13, fontWeight: 700,
                background: colors.primary, color: "#fff", border: "none", cursor: "pointer",
                opacity: creatingDeal ? 0.6 : 1,
              }}>
                {creatingDeal ? "Creating..." : "Create deal"}
              </button>
            </div>
          </div>
        </>
      )}
      {showAddStakeholder && (
        <AddStakeholderModal
          companyId={company.id}
          onClose={() => setShowAddStakeholder(false)}
          onCreated={() => { setShowAddStakeholder(false); load(); }}
        />
      )}
      {bulkLaunchOpen && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 9000,
            background: "rgba(15, 39, 68, 0.4)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
          onClick={() => !bulkLaunching && setBulkLaunchOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ width: 480, background: "#fff", borderRadius: 16, padding: "22px 24px", maxWidth: "92vw" }}
          >
            <h3 style={{ margin: 0, marginBottom: 6, fontSize: 16, fontWeight: 700, color: colors.text }}>
              Add {selectedContactIds.size} prospects to sequence
            </h3>
            <p style={{ margin: 0, marginBottom: 14, fontSize: 13, color: colors.sub }}>
              We'll create or reuse each contact's sequence, then launch a single Instantly campaign with them all.
              Contacts missing an email will be skipped.
            </p>
            <label style={{ display: "block", fontSize: 12, fontWeight: 700, color: colors.sub, marginBottom: 6 }}>
              Sending account email *
            </label>
            <input
              type="email"
              placeholder="rep@beacon.li"
              value={bulkLaunchAccount}
              onChange={(e) => setBulkLaunchAccount(e.target.value)}
              style={{ width: "100%", border: `1px solid ${colors.border}`, borderRadius: 10, padding: "9px 12px", fontSize: 13, marginBottom: 12 }}
            />
            <label style={{ display: "block", fontSize: 12, fontWeight: 700, color: colors.sub, marginBottom: 6 }}>
              Campaign name (optional)
            </label>
            <input
              type="text"
              placeholder={`Bulk · ${selectedContactIds.size} prospects`}
              value={bulkLaunchName}
              onChange={(e) => setBulkLaunchName(e.target.value)}
              style={{ width: "100%", border: `1px solid ${colors.border}`, borderRadius: 10, padding: "9px 12px", fontSize: 13, marginBottom: 14 }}
            />
            {bulkLaunchError && (
              <div style={{ color: colors.red, fontSize: 12, marginBottom: 10 }}>{bulkLaunchError}</div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button
                onClick={() => setBulkLaunchOpen(false)}
                disabled={bulkLaunching}
                style={{
                  padding: "9px 18px", borderRadius: 10, fontSize: 13, fontWeight: 600,
                  background: "#f1f5f9", color: colors.sub, border: "none",
                  cursor: bulkLaunching ? "not-allowed" : "pointer",
                }}
              >
                Cancel
              </button>
              <button
                disabled={bulkLaunching || !bulkLaunchAccount.trim()}
                onClick={async () => {
                  setBulkLaunching(true);
                  setBulkLaunchError("");
                  try {
                    const result = await outreachApi.launchContactsCampaign(
                      Array.from(selectedContactIds),
                      bulkLaunchAccount.trim(),
                      { campaignName: bulkLaunchName.trim() || undefined }
                    );
                    setBulkLaunchOpen(false);
                    setSelectedContactIds(new Set());
                    setBulkLaunchAccount("");
                    setBulkLaunchName("");
                    toast.success(
                      `${result.pushed} pushed · ${result.failed} failed · ${result.skipped_no_email} no-email · ${result.skipped_already_launched} already launched`
                    );
                    await load();
                  } catch (e) {
                    setBulkLaunchError(e instanceof Error ? e.message : "Launch failed");
                  } finally {
                    setBulkLaunching(false);
                  }
                }}
                style={{
                  padding: "9px 18px", borderRadius: 10, fontSize: 13, fontWeight: 700,
                  background: bulkLaunching || !bulkLaunchAccount.trim() ? "#e8eef5" : colors.primary,
                  color: bulkLaunching || !bulkLaunchAccount.trim() ? "#9aafbe" : "#fff",
                  border: "none", cursor: bulkLaunching || !bulkLaunchAccount.trim() ? "not-allowed" : "pointer",
                }}
              >
                {bulkLaunching ? "Launching…" : "Launch campaign"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Add Stakeholder Modal ───────────────────────────────────────────────────

function AddStakeholderModal({
  companyId,
  onClose,
  onCreated,
}: {
  companyId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    title: "",
    email: "",
    phone: "",
    linkedin_url: "",
    persona_type: "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    if (!form.first_name.trim() || !form.last_name.trim()) {
      setError("First and last name are required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await contactsApi.create({
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        title: form.title.trim() || undefined,
        email: form.email.trim() || undefined,
        phone: form.phone.trim() || undefined,
        linkedin_url: form.linkedin_url.trim() || undefined,
        persona_type: form.persona_type || undefined,
        company_id: companyId,
      } as Parameters<typeof contactsApi.create>[0]);
      onCreated();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create contact");
    } finally {
      setSaving(false);
    }
  };

  const fieldStyle: CSSProperties = {
    width: "100%", padding: "9px 12px", borderRadius: 10, fontSize: 13,
    border: `1px solid ${colors.border}`, outline: "none", fontFamily: "inherit",
  };

  return (
    <>
      <div onClick={onClose} style={{
        position: "fixed", inset: 0, background: "rgba(15,23,42,0.22)",
        backdropFilter: "blur(3px)", zIndex: 100,
      }} />
      <div style={{
        position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
        zIndex: 101, width: 480, maxWidth: "90vw", background: "#fff",
        borderRadius: 18, boxShadow: "0 20px 60px rgba(15,23,42,0.18)",
        padding: "28px 28px 22px", display: "flex", flexDirection: "column", gap: 16,
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ fontSize: 17, fontWeight: 700, color: colors.text, margin: 0 }}>Add Stakeholder</h3>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: colors.faint, fontSize: 18 }}>x</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: colors.faint, marginBottom: 4, display: "block" }}>First Name *</label>
            <input style={fieldStyle} value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} placeholder="Jane" />
          </div>
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: colors.faint, marginBottom: 4, display: "block" }}>Last Name *</label>
            <input style={fieldStyle} value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} placeholder="Smith" />
          </div>
        </div>

        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: colors.faint, marginBottom: 4, display: "block" }}>Job Title</label>
          <input style={fieldStyle} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="VP of Engineering" />
        </div>

        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: colors.faint, marginBottom: 4, display: "block" }}>Buying Committee Role</label>
          <select style={{ ...fieldStyle, appearance: "auto" }} value={form.persona_type} onChange={(e) => setForm({ ...form, persona_type: e.target.value })}>
            <option value="">Select role...</option>
            <option value="champion">Champion</option>
            <option value="buyer">Economic Buyer</option>
            <option value="evaluator">Technical Evaluator</option>
            <option value="influencer">Influencer</option>
            <option value="implementation_owner">Implementation Owner</option>
            <option value="blocker">Blocker</option>
          </select>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: colors.faint, marginBottom: 4, display: "block" }}>Email</label>
            <input style={fieldStyle} type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="jane@company.com" />
          </div>
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: colors.faint, marginBottom: 4, display: "block" }}>Phone</label>
            <input style={fieldStyle} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+1 555 0100" />
          </div>
        </div>

        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: colors.faint, marginBottom: 4, display: "block" }}>LinkedIn URL</label>
          <input style={fieldStyle} value={form.linkedin_url} onChange={(e) => setForm({ ...form, linkedin_url: e.target.value })} placeholder="https://linkedin.com/in/janesmith" />
        </div>

        {error && <div style={{ color: colors.red, fontSize: 12, fontWeight: 600 }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 4 }}>
          <button onClick={onClose} style={{
            padding: "9px 18px", borderRadius: 10, fontSize: 13, fontWeight: 600,
            background: "#f1f5f9", color: colors.sub, border: "none", cursor: "pointer",
          }}>Cancel</button>
          <button onClick={handleSave} disabled={saving} style={{
            padding: "9px 18px", borderRadius: 10, fontSize: 13, fontWeight: 700,
            background: colors.primary, color: "#fff", border: "none", cursor: "pointer",
            opacity: saving ? 0.6 : 1,
          }}>
            {saving ? "Saving..." : "Add Stakeholder"}
          </button>
        </div>
      </div>
    </>
  );
}

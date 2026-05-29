import "./prospect-detail-refresh.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  ExternalLink,
  Globe,
  Loader2,
  Mail,
  PenLine,
  Phone,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  UserRound,
} from "lucide-react";

import { accountSourcingApi, activitiesApi, companiesApi, contactsApi, dealsApi } from "../lib/api";
import OutreachDrawer from "../components/outreach/OutreachDrawer";
import AssignDropdown from "../components/AssignDropdown";
import TaskCenterModal from "../components/tasks/TaskCenterModal";
import ProvenanceBar from "../components/ProvenanceBar";
import {
  getProspectTrackingScore,
  getProspectTrackingStage,
  getProspectTrackingSummary,
  getProspectTrackingTone,
} from "../lib/prospectTracking";
import { avatarColor, formatDate, formatDomain, getAccountPrioritySnapshot, getInitials, gmailComposeUrl, isPlaceholderDomain } from "../lib/utils";
import type { Activity, Company, Contact, Deal } from "../types";
import { MessageSquare } from "lucide-react";
import {
  asList,
  asText,
  cardStyle,
  Chip,
  colors,
  companyUploadedRow,
  ContactActionButton,
  heroCardStyle,
  importedAnalyst,
  KV,
  ListCard,
  MetricCard,
  pageStyle,
  prettify,
  Section,
  sequencePlan,
  SequenceStepCard,
  toneForLane,
  uploadedRow,
  wrapStyle,
} from "./accountSourcingContactDetailShared";

export default function AccountSourcingContactDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [contact, setContact] = useState<Contact | null>(null);
  const [company, setCompany] = useState<Company | null>(null);
  const [loading, setLoading] = useState(true);
  const [reEnriching, setReEnriching] = useState(false);
  const [reEnrichStatus, setReEnrichStatus] = useState<"idle" | "success" | "error">("idle");
  const [companyEnriching, setCompanyEnriching] = useState(false);
  const [convertingDeal, setConvertingDeal] = useState(false);
  const [commsLog, setCommsLog] = useState<Activity[]>([]);
  const [showTasksModal, setShowTasksModal] = useState(false);
  const [showEngagementTimeline, setShowEngagementTimeline] = useState(false);
  const [noteInput, setNoteInput] = useState("");
  const [noteSaving, setNoteSaving] = useState(false);
  const [editingDomain, setEditingDomain] = useState(false);
  const [domainInput, setDomainInput] = useState("");
  const [domainSaving, setDomainSaving] = useState(false);
  const [editingEmail, setEditingEmail] = useState(false);
  const [emailInput, setEmailInput] = useState("");
  const [emailSaving, setEmailSaving] = useState(false);
  const [editingPhone, setEditingPhone] = useState(false);
  const [phoneInput, setPhoneInput] = useState("");
  const [phoneSaving, setPhoneSaving] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      let loadedContact: Contact;
      try {
        loadedContact = await accountSourcingApi.getContact(id);
      } catch {
        loadedContact = await contactsApi.get(id);
      }

      setContact(loadedContact);

      if (loadedContact.company_id) {
        try {
          const loadedCompany = await accountSourcingApi.getCompany(loadedContact.company_id);
          setCompany(loadedCompany);
        } catch {
          const loadedCompany = await companiesApi.get(loadedContact.company_id);
          setCompany(loadedCompany);
        }
      } else {
        setCompany(null);
      }
      // Load engagement events captured from connected tools.
      activitiesApi.list(undefined, id).then(setCommsLog).catch(() => {});
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const plan = useMemo(() => sequencePlan(contact), [contact]);
  const rawRow = useMemo(() => uploadedRow(contact), [contact]);
  const fallbackRawRow = useMemo(() => companyUploadedRow(company), [company]);
  const analyst = useMemo(() => importedAnalyst(company), [company]);
  const priority = company ? getAccountPrioritySnapshot(company) : null;
  const companyProfile = company?.prospecting_profile && typeof company.prospecting_profile === "object"
    ? (company.prospecting_profile as Record<string, unknown>)
    : {};
  const warmPath = contact?.warm_intro_path && typeof contact.warm_intro_path === "object"
    ? (contact.warm_intro_path as Record<string, unknown>)
    : {};
  const companyWarmPaths = Array.isArray(companyProfile.warm_paths)
    ? (companyProfile.warm_paths as Array<Record<string, unknown>>)
    : [];
  const displayWarmPath = Object.keys(warmPath).length > 0 ? warmPath : (companyWarmPaths[0] || {});
  const displayRawRow = Object.keys(rawRow).length > 0 ? rawRow : fallbackRawRow;
  const planSteps = Array.isArray(plan.steps) ? (plan.steps as Array<Record<string, unknown>>) : [];
  const planHooks = asList(plan.personalization_hooks);
  const companyPlan = company?.outreach_plan && typeof company.outreach_plan === "object"
    ? (company.outreach_plan as Record<string, unknown>)
    : {};
  const companyCache = company?.enrichment_cache && typeof company.enrichment_cache === "object"
    ? (company.enrichment_cache as Record<string, unknown>)
    : {};
  const aiEntry = companyCache.ai_summary && typeof companyCache.ai_summary === "object"
    ? ((companyCache.ai_summary as Record<string, unknown>).data ?? companyCache.ai_summary)
    : {};
  const aiSummary = aiEntry && typeof aiEntry === "object" ? (aiEntry as Record<string, unknown>) : {};
  const trackingTone = getProspectTrackingTone(contact);
  const suggestedCompanyName = asText(displayRawRow["company name"]) || asText(displayRawRow.company) || contact?.company_name;
  const suggestedCompanyDomain = asText(displayRawRow.domain) || asText(displayRawRow.website) || asText(displayRawRow.url);
  const companyNeedsEnrichment = Boolean(
    company && (!company.enriched_at || (company.domain || "").endsWith(".unknown"))
  );
  const canCreateCompanyFromProspect = !company && Boolean(suggestedCompanyName);

  if (loading) {
    return (
      <div style={pageStyle}>
        <div style={{ ...wrapStyle, ...cardStyle, padding: 28, textAlign: "center" }}>
          <Loader2 className="animate-spin" color={colors.primary} />
        </div>
      </div>
    );
  }

  if (!contact) {
    return (
      <div style={pageStyle}>
        <div style={{ ...wrapStyle, ...cardStyle, padding: 28, textAlign: "center", color: colors.faint }}>
          Prospect not found.
        </div>
      </div>
    );
  }

  const fullName = `${contact.first_name} ${contact.last_name}`.trim();
  const canConvertToDeal = [contact.sequence_status, contact.instantly_status, contact.tracking_stage, contact.tracking_summary]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes("meeting_booked")
    || [contact.sequence_status, contact.instantly_status, contact.tracking_stage, contact.tracking_summary]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes("meeting booked");

  const handleConvertToDeal = async () => {
    if (!company) return;
    setConvertingDeal(true);
    try {
      const deal = await dealsApi.create({
        name: `${company.name} - ${fullName || "Prospect Deal"}`,
        pipeline_type: "deal",
        stage: "demo_done",
        company_id: company.id,
        assigned_to_id: contact.assigned_to_id || undefined,
        geography: company.region
          ? (company.region.toLowerCase().includes("united states") || company.region.toLowerCase() === "us"
              ? "US"
              : company.region.toLowerCase().includes("america")
                ? "Americas"
                : company.region.toLowerCase().includes("india")
                  ? "India"
                  : company.region.toLowerCase().includes("apac") || company.region.toLowerCase().includes("asia")
                    ? "APAC"
                : "Rest of World")
          : undefined,
        tags: ["converted_from_prospect"],
        next_step: "Review meeting notes and define the next demo follow-up",
      } as Partial<Deal>);
      await dealsApi.addContact(deal.id, contact.id, "champion");
      navigate(`/pipeline?deal=${deal.id}`);
    } finally {
      setConvertingDeal(false);
    }
  };

  const handleEnrichCompany = async () => {
    if (!contact || (!company && !suggestedCompanyName)) return;
    setCompanyEnriching(true);
    try {
      if (company) {
        await accountSourcingApi.reEnrichCompany(company.id);
        window.alert(`${company.name} was queued for enrichment.`);
      } else {
        const batch = await accountSourcingApi.createManualCompany({
          name: suggestedCompanyName!,
          domain: suggestedCompanyDomain,
        });
        const createdCompanyId = typeof batch.meta?.company_id === "string" ? batch.meta.company_id : undefined;
        if (createdCompanyId) {
          await contactsApi.update(contact.id, { company_id: createdCompanyId });
        }
        window.alert(`${suggestedCompanyName} was added to Account Sourcing and queued for enrichment.`);
      }
      await load();
    } finally {
      setCompanyEnriching(false);
    }
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

  const handleSaveEmail = async () => {
    if (!contact) return;
    const trimmed = emailInput.trim();
    setEmailSaving(true);
    try {
      const updated = await contactsApi.update(contact.id, { email: trimmed || undefined });
      setContact(updated);
      setEditingEmail(false);
    } finally {
      setEmailSaving(false);
    }
  };

  const handleSavePhone = async () => {
    if (!contact) return;
    const trimmed = phoneInput.trim();
    setPhoneSaving(true);
    try {
      const updated = await contactsApi.update(contact.id, { phone: trimmed || undefined });
      setContact(updated);
      setEditingPhone(false);
    } finally {
      setPhoneSaving(false);
    }
  };

  return (
    <>
      <style>{`
        @media (max-width: 760px) {
          .prospect-detail-page {
            padding: 0 !important;
            background: #f4f8fc !important;
          }
          .prospect-detail-wrap {
            max-width: none !important;
            padding: 10px 10px 88px !important;
          }
          .prospect-detail-hero {
            border-radius: 18px !important;
            padding: 14px !important;
            box-shadow: 0 10px 26px rgba(16, 42, 67, 0.08) !important;
          }
          .prospect-detail-hero-row {
            display: grid !important;
            gap: 14px !important;
          }
          .prospect-detail-title-row {
            gap: 12px !important;
            flex-wrap: nowrap !important;
          }
          .prospect-detail-avatar {
            width: 56px !important;
            height: 56px !important;
            border-radius: 16px !important;
            font-size: 16px !important;
          }
          .prospect-detail-name {
            font-size: 25px !important;
            line-height: 1.1 !important;
            letter-spacing: 0 !important;
          }
          .prospect-detail-subtitle {
            margin-top: 6px !important;
            font-size: 13.5px !important;
            line-height: 1.45 !important;
          }
          .prospect-detail-provenance,
          .prospect-detail-chips,
          .prospect-detail-progress,
          .prospect-detail-enrichment,
          .prospect-detail-company-inline,
          .prospect-detail-actions-desktop {
            display: none !important;
          }
          .prospect-detail-primary-actions {
            display: grid !important;
            grid-template-columns: 1fr 1fr !important;
            gap: 8px !important;
            margin-top: 12px !important;
          }
          .prospect-detail-call-button {
            grid-column: 1 / -1 !important;
            width: 100% !important;
          }
          .prospect-detail-call-button > * {
            width: 100% !important;
            min-height: 50px !important;
            border-radius: 14px !important;
            background: linear-gradient(135deg, #0f5fb8, #174ea6) !important;
            color: #ffffff !important;
            border-color: #a9cdf8 !important;
            justify-content: center !important;
            font-size: 15px !important;
            font-weight: 850 !important;
            box-shadow: 0 10px 20px rgba(23, 80, 137, 0.18) !important;
          }
          .prospect-detail-secondary-action {
            width: 100% !important;
          }
          .prospect-detail-secondary-action,
          .prospect-detail-secondary-action > * {
            min-height: 42px !important;
            justify-content: center !important;
            border-radius: 13px !important;
            font-size: 12.5px !important;
            font-weight: 800 !important;
          }
          .prospect-detail-mobile-card {
            display: block !important;
            margin-top: 12px !important;
            border: 1px solid #dce8f4 !important;
            background: #ffffff !important;
            border-radius: 16px !important;
            padding: 12px !important;
          }
          .prospect-detail-mobile-grid {
            display: grid !important;
            grid-template-columns: 1fr 1fr !important;
            gap: 8px !important;
          }
          .prospect-detail-metric-grid {
            grid-template-columns: 1fr 1fr !important;
            gap: 10px !important;
          }
          .prospect-detail-main-grid {
            grid-template-columns: 1fr !important;
            gap: 12px !important;
          }
          .prospect-detail-main-grid > div {
            min-width: 0 !important;
          }
          .prospect-detail-section {
            border-radius: 16px !important;
            padding: 14px !important;
          }
          .prospect-detail-outreach-section {
            display: block !important;
          }
          .prospect-detail-notes-row {
            display: grid !important;
          }
          .prospect-detail-notes-row textarea {
            width: 100% !important;
          }
          .prospect-detail-notes-row button {
            width: 100% !important;
            margin-top: 8px !important;
          }
          .prospect-detail-raw-row {
            grid-template-columns: 1fr !important;
            gap: 5px !important;
          }
          .prospect-detail-playbook-grid,
          .prospect-detail-signal-grid {
            grid-template-columns: 1fr !important;
          }
          .prospect-detail-edit-inline {
            width: 100% !important;
            flex-wrap: wrap !important;
          }
          .prospect-detail-edit-inline input {
            min-width: 100% !important;
            flex: 1 1 100% !important;
            margin-bottom: 6px !important;
          }
        }
      `}</style>
      <div className="prospect-detail-page" style={pageStyle}>
        <div className="prospect-detail-wrap" style={wrapStyle}>
          <div className="prospect-detail-hero" style={heroCardStyle}>
            <div className="prospect-detail-hero-row" style={{ display: "flex", justifyContent: "space-between", gap: 18, flexWrap: "wrap", alignItems: "flex-start" }}>
              <div style={{ display: "grid", gap: 14, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <button onClick={() => navigate(-1)} style={{ background: "none", border: "none", padding: 0, color: colors.primary, display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, cursor: "pointer", fontSize: "inherit" }}>
                  <ArrowLeft size={15} /> Back
                </button>
                {company ? (
                  <Link to={`/account-sourcing/${company.id}`} style={{ color: colors.sub, textDecoration: "none", fontSize: 13, display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Building2 size={13} /> {company.name}
                  </Link>
                ) : null}
              </div>

              <div className="prospect-detail-title-row" style={{ display: "flex", gap: 18, alignItems: "flex-start", flexWrap: "wrap" }}>
                <div className={`prospect-detail-avatar flex h-20 w-20 shrink-0 items-center justify-center rounded-[22px] text-[22px] font-extrabold ${avatarColor(fullName || "Prospect")}`}>
                  {getInitials(fullName || "Prospect")}
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <h1 className="prospect-detail-name" style={{ margin: 0, color: colors.text, fontSize: 38, letterSpacing: 0.2, lineHeight: 1.05 }}>{fullName || "Unnamed prospect"}</h1>
                  <div className="prospect-detail-subtitle" style={{ marginTop: 10, color: colors.sub, fontSize: 17, lineHeight: 1.6, maxWidth: 840 }}>
                    {contact.title || "No title yet"} {company ? `at ${company.name}` : ""}. This view combines uploaded prospecting context, company research, and the saved prospect-level outreach sequence.
                  </div>
                  <div className="prospect-detail-provenance">
                    <ProvenanceBar
                      source={(contact.enrichment_data as Record<string, unknown> | null | undefined)?.source as string | null | undefined}
                      uploadedBy={(contact.enrichment_data as Record<string, unknown> | null | undefined)?.uploaded_by as string | null | undefined}
                      createdAt={contact.created_at}
                      updatedAt={contact.updated_at}
                    />
                  </div>
                  <div className="prospect-detail-chips" style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <Chip label={prettify(contact.outreach_lane || company?.recommended_outreach_lane)} tone={toneForLane(contact.outreach_lane || company?.recommended_outreach_lane)} />
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 8,
                        borderRadius: 999,
                        padding: "6px 10px",
                        background: trackingTone.background,
                        color: trackingTone.color,
                        border: `1px solid ${trackingTone.border}`,
                        fontSize: 12,
                        fontWeight: 800,
                      }}
                    >
                      {getProspectTrackingStage(contact)}
                    </span>
                    <Chip label={contact.email ? "Email ready" : "Missing email"} tone={contact.email ? "green" : "warm"} />
                    <Chip label={contact.warm_intro_strength ? `Warm path ${contact.warm_intro_strength}/5` : Object.keys(displayWarmPath).length > 0 ? "Account warm path" : "Direct motion"} tone={Object.keys(displayWarmPath).length > 0 ? "warm" : "neutral"} />
                  </div>
                  <div
                    className="prospect-detail-progress"
                    style={{
                      marginTop: 14,
                      maxWidth: 840,
                      padding: "14px 16px",
                      borderRadius: 16,
                      background: trackingTone.soft,
                      border: `1px solid ${trackingTone.border}`,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                      <span style={{ color: trackingTone.color, fontWeight: 800, fontSize: 13 }}>
                        Automated progress
                      </span>
                      <span style={{ color: trackingTone.color, fontWeight: 900, fontSize: 13 }}>
                        {getProspectTrackingScore(contact)}
                      </span>
                    </div>
                    <div style={{ marginTop: 6, color: colors.sub, fontSize: 13.5, lineHeight: 1.6 }}>
                      {getProspectTrackingSummary(contact)}
                    </div>
                  </div>
                  {(companyNeedsEnrichment || canCreateCompanyFromProspect) ? (
                    <div
                      className="prospect-detail-enrichment"
                      style={{
                        marginTop: 14,
                        maxWidth: 840,
                        padding: "14px 16px",
                        borderRadius: 16,
                        background: "#fff8e8",
                        border: "1px solid #f5ddaa",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: 12,
                        flexWrap: "wrap",
                      }}
                    >
                      <div style={{ minWidth: 0 }}>
                        <div style={{ color: "#8a5b00", fontWeight: 800, fontSize: 12, letterSpacing: 0.35 }}>
                          COMPANY ENRICHMENT
                        </div>
                        <div style={{ marginTop: 6, color: "#6c5a2f", fontSize: 13.5, lineHeight: 1.6 }}>
                          {company
                            ? `${company.name} is attached to this prospect, but its account research is still incomplete. Enrich it now to unlock full account context here.`
                            : `${suggestedCompanyName} is not mapped to an account yet. Add this company in Account Sourcing first, then map this prospect to that account so future research, deal creation, and outreach use the right company context.`}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => void handleEnrichCompany()}
                        disabled={companyEnriching}
                        style={{
                          border: "1px solid #e5c980",
                          background: "#fff",
                          color: "#8a5b00",
                          borderRadius: 12,
                          padding: "10px 14px",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 8,
                          fontWeight: 700,
                          cursor: companyEnriching ? "default" : "pointer",
                          opacity: companyEnriching ? 0.75 : 1,
                        }}
                      >
                        {companyEnriching ? <Loader2 size={14} className="animate-spin" /> : <Building2 size={14} />}
                        {company ? "Enrich company" : "Add & enrich company"}
                      </button>
                    </div>
                  ) : null}
                  <div className="prospect-detail-primary-actions" style={{ marginTop: 16, display: "flex", gap: 14, flexWrap: "wrap", color: colors.sub, fontSize: 13.5 }}>
                    {company ? <span className="prospect-detail-company-inline" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><Building2 size={14} />{company.name}</span> : null}
                    {editingEmail ? (
                      <span className="prospect-detail-secondary-action" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <input
                          autoFocus
                          value={emailInput}
                          onChange={(e) => setEmailInput(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") handleSaveEmail(); if (e.key === "Escape") { setEditingEmail(false); setEmailInput(contact.email || ""); } }}
                          placeholder="email@example.com"
                          style={{ height: 36, borderRadius: 10, border: `1px solid ${colors.primary}`, padding: "0 10px", fontSize: 13, color: colors.text, outline: "none", minWidth: 200 }}
                        />
                        <button type="button" disabled={emailSaving} onClick={() => handleSaveEmail()}
                          style={{ height: 36, padding: "0 12px", borderRadius: 10, border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}>
                          {emailSaving ? "Saving..." : "Save"}
                        </button>
                        <button type="button" onClick={() => { setEditingEmail(false); setEmailInput(contact.email || ""); }}
                          style={{ height: 36, padding: "0 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}>
                          Cancel
                        </button>
                      </span>
                    ) : contact.email ? (
                      <span className="prospect-detail-secondary-action" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <ContactActionButton icon={<Mail size={14} />} href={gmailComposeUrl(contact.email)} label="Email" tone="primary" />
                        <button type="button" onClick={() => { setEmailInput(contact.email || ""); setEditingEmail(true); }}
                          style={{ height: 36, width: 36, borderRadius: 10, border: `1px solid ${colors.border}`, background: "#f7f9fc", color: colors.sub, display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                          <PenLine size={14} />
                        </button>
                      </span>
                    ) : (
                      <span className="prospect-detail-secondary-action" style={{ display: "inline-flex" }}>
                        <button type="button" onClick={() => { setEmailInput(""); setEditingEmail(true); }}
                          style={{ border: `1px dashed #bccfe0`, background: "#fbfdff", color: colors.sub, borderRadius: 12, padding: "9px 14px", display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13, cursor: "pointer" }}>
                          <Plus size={14} /> Add email
                        </button>
                      </span>
                    )}
                    {editingPhone ? (
                      <span className="prospect-detail-secondary-action" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <input
                          autoFocus
                          value={phoneInput}
                          onChange={(e) => setPhoneInput(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") handleSavePhone(); if (e.key === "Escape") { setEditingPhone(false); setPhoneInput(contact.phone || ""); } }}
                          placeholder="+1 555-123-4567"
                          style={{ height: 36, borderRadius: 10, border: `1px solid ${colors.primary}`, padding: "0 10px", fontSize: 13, color: colors.text, outline: "none", minWidth: 200 }}
                        />
                        <button type="button" disabled={phoneSaving} onClick={() => handleSavePhone()}
                          style={{ height: 36, padding: "0 12px", borderRadius: 10, border: `1px solid ${colors.green}`, background: colors.green, color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}>
                          {phoneSaving ? "Saving..." : "Save"}
                        </button>
                        <button type="button" onClick={() => { setEditingPhone(false); setPhoneInput(contact.phone || ""); }}
                          style={{ height: 36, padding: "0 12px", borderRadius: 10, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}>
                          Cancel
                        </button>
                      </span>
                    ) : contact.phone ? (
                      <span className="prospect-detail-call-button" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <ContactActionButton
                          icon={<Phone size={14} />}
                          onClick={() => window.__aircallDial?.(contact.phone!, fullName || undefined)}
                          label={`Call ${contact.phone}`}
                          tone="green"
                        />
                        <button type="button" onClick={() => { setPhoneInput(contact.phone || ""); setEditingPhone(true); }}
                          style={{ height: 36, width: 36, borderRadius: 10, border: `1px solid ${colors.border}`, background: "#f7f9fc", color: colors.sub, display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                          <PenLine size={14} />
                        </button>
                      </span>
                    ) : (
                      <span className="prospect-detail-secondary-action" style={{ display: "inline-flex" }}>
                        <button type="button" onClick={() => { setPhoneInput(""); setEditingPhone(true); }}
                          style={{ border: `1px dashed #bccfe0`, background: "#fbfdff", color: colors.sub, borderRadius: 12, padding: "9px 14px", display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13, cursor: "pointer" }}>
                          <Plus size={14} /> Add phone
                        </button>
                      </span>
                    )}
                    {contact.linkedin_url ? <span className="prospect-detail-secondary-action" style={{ display: "inline-flex" }}><ContactActionButton icon={<Globe size={14} />} href={contact.linkedin_url} label="LinkedIn" tone="primary" /></span> : null}
                    <button
                      type="button"
                      className="prospect-detail-secondary-action"
                      onClick={() => setShowTasksModal(true)}
                      style={{ border: `1px solid #d5e5ff`, background: colors.primarySoft, color: colors.primary, borderRadius: 12, padding: "8px 12px", display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, cursor: "pointer" }}
                    >
                      <CheckCircle2 size={14} />
                      Tasks
                    </button>
                  </div>
                  <div className="prospect-detail-mobile-card" style={{ display: "none" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                      <div style={{ fontSize: 11, color: colors.faint, fontWeight: 850, textTransform: "uppercase", letterSpacing: 0.4 }}>Contact info</div>
                    </div>
                    <div style={{ display: "grid", gap: 8 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Mail size={14} color={colors.sub} />
                        {editingEmail ? (
                          <div style={{ display: "flex", alignItems: "center", gap: 4, flex: 1 }}>
                            <input autoFocus value={emailInput} onChange={(e) => setEmailInput(e.target.value)}
                              onKeyDown={(e) => { if (e.key === "Enter") handleSaveEmail(); if (e.key === "Escape") { setEditingEmail(false); setEmailInput(contact.email || ""); } }}
                              placeholder="email@example.com"
                              style={{ flex: 1, height: 32, borderRadius: 8, border: `1px solid ${colors.primary}`, padding: "0 8px", fontSize: 13, color: colors.text, outline: "none" }} />
                            <button type="button" disabled={emailSaving} onClick={() => handleSaveEmail()}
                              style={{ height: 32, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                              {emailSaving ? "..." : "Save"}
                            </button>
                            <button type="button" onClick={() => { setEditingEmail(false); setEmailInput(contact.email || ""); }}
                              style={{ height: 32, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                              Cancel
                            </button>
                          </div>
                        ) : contact.email ? (
                          <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
                            <span style={{ color: colors.text, fontSize: 14, fontWeight: 700, flex: 1 }}>{contact.email}</span>
                            <button type="button" onClick={(e) => { e.preventDefault(); setEmailInput(contact.email || ""); setEditingEmail(true); }}
                              style={{ height: 30, width: 30, borderRadius: 8, border: `1px solid ${colors.border}`, background: "#f7f9fc", color: colors.sub, display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                              <PenLine size={13} />
                            </button>
                          </div>
                        ) : (
                          <button type="button" onClick={() => { setEmailInput(""); setEditingEmail(true); }}
                            style={{ border: `1px dashed #bccfe0`, background: "#fbfdff", color: colors.sub, borderRadius: 8, padding: "6px 10px", display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 700, fontSize: 12, cursor: "pointer" }}>
                            <Plus size={12} /> Add email
                          </button>
                        )}
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Phone size={14} color={colors.sub} />
                        {editingPhone ? (
                          <div style={{ display: "flex", alignItems: "center", gap: 4, flex: 1 }}>
                            <input autoFocus value={phoneInput} onChange={(e) => setPhoneInput(e.target.value)}
                              onKeyDown={(e) => { if (e.key === "Enter") handleSavePhone(); if (e.key === "Escape") { setEditingPhone(false); setPhoneInput(contact.phone || ""); } }}
                              placeholder="+1 555-123-4567"
                              style={{ flex: 1, height: 32, borderRadius: 8, border: `1px solid ${colors.primary}`, padding: "0 8px", fontSize: 13, color: colors.text, outline: "none" }} />
                            <button type="button" disabled={phoneSaving} onClick={() => handleSavePhone()}
                              style={{ height: 32, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.green}`, background: colors.green, color: "#fff", fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                              {phoneSaving ? "..." : "Save"}
                            </button>
                            <button type="button" onClick={() => { setEditingPhone(false); setPhoneInput(contact.phone || ""); }}
                              style={{ height: 32, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                              Cancel
                            </button>
                          </div>
                        ) : contact.phone ? (
                          <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
                            <button type="button" onClick={() => window.__aircallDial?.(contact.phone!, fullName || undefined)}
                              style={{ color: colors.green, fontSize: 14, fontWeight: 800, flex: 1, textAlign: "left", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
                              {contact.phone}
                            </button>
                            <button type="button" onClick={(e) => { e.preventDefault(); setPhoneInput(contact.phone || ""); setEditingPhone(true); }}
                              style={{ height: 30, width: 30, borderRadius: 8, border: `1px solid ${colors.border}`, background: "#f7f9fc", color: colors.sub, display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                              <PenLine size={13} />
                            </button>
                          </div>
                        ) : (
                          <button type="button" onClick={() => { setPhoneInput(""); setEditingPhone(true); }}
                            style={{ border: `1px dashed #bccfe0`, background: "#fbfdff", color: colors.sub, borderRadius: 8, padding: "6px 10px", display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 700, fontSize: 12, cursor: "pointer" }}>
                            <Plus size={12} /> Add phone
                          </button>
                        )}
                      </div>
                    </div>
                    <div style={{ marginTop: 8, color: colors.sub, fontSize: 12.5, lineHeight: 1.45 }}>
                      {company?.name || contact.company_name || "Company not mapped"} · {contact.timezone || "No timezone"}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="prospect-detail-actions-desktop" style={{ display: "inline-flex", gap: 10, flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => setShowTasksModal(true)}
              style={{ border: `1px solid #d5e5ff`, background: colors.primarySoft, color: colors.primary, borderRadius: 12, padding: "10px 14px", display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, cursor: "pointer" }}
            >
              <CheckCircle2 size={14} />
              Tasks
            </button>
            {canConvertToDeal && company ? (
              <button
                onClick={handleConvertToDeal}
                disabled={convertingDeal}
                style={{ border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", borderRadius: 12, padding: "10px 14px", display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, cursor: convertingDeal ? "wait" : "pointer", opacity: convertingDeal ? 0.8 : 1 }}
              >
                {convertingDeal ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {convertingDeal ? "Converting..." : "Convert to Deal"}
              </button>
            ) : null}
            <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <button
                onClick={async () => {
                  setReEnriching(true);
                  setReEnrichStatus("idle");
                  try {
                    await accountSourcingApi.reEnrichContact(contact.id);
                    setReEnrichStatus("success");
                  } catch {
                    setReEnrichStatus("error");
                  } finally {
                    setReEnriching(false);
                    setTimeout(() => setReEnrichStatus("idle"), 4000);
                  }
                }}
                disabled={reEnriching}
                style={{ border: `1px solid ${colors.border}`, background: "#fff", color: colors.text, borderRadius: 12, padding: "10px 14px", display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, cursor: reEnriching ? "not-allowed" : "pointer", opacity: reEnriching ? 0.7 : 1 }}
              >
                {reEnriching ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {reEnriching ? "Queuing..." : "Re-enrich"}
              </button>
              {reEnrichStatus === "success" && (
                <span style={{ fontSize: 12, color: "#15803d", fontWeight: 500 }}>✓ Queued — enrichment running in background</span>
              )}
              {reEnrichStatus === "error" && (
                <span style={{ fontSize: 12, color: "#dc2626", fontWeight: 500 }}>Failed — try again</span>
              )}
            </div>
            <button
              type="button"
              onClick={() => setShowEngagementTimeline((v) => !v)}
              style={{ border: `1px solid ${showEngagementTimeline ? colors.primary : colors.border}`, background: showEngagementTimeline ? colors.primarySoft : "#fff", color: showEngagementTimeline ? colors.primary : colors.text, borderRadius: 12, padding: "10px 14px", display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 700, cursor: "pointer" }}
            >
              <MessageSquare size={14} />
              Engagement ({commsLog.length})
            </button>
          </div>
          </div>
        </div>

        <div className="prospect-detail-metric-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
          <MetricCard
            label="Auto Stage"
            value={getProspectTrackingStage(contact)}
            hint="Derived from Instantly, Aircall, activity history, and linked deals."
            tone={
              contact.tracking_label === "good"
                ? "green"
                : contact.tracking_label === "blocked"
                  ? "danger"
                  : contact.tracking_label === "watch"
                    ? "warm"
                    : "primary"
            }
          />
          <MetricCard
            label="Momentum"
            value={getProspectTrackingScore(contact)}
            hint={getProspectTrackingSummary(contact)}
            tone={
              contact.tracking_label === "good"
                ? "green"
                : contact.tracking_label === "blocked"
                  ? "danger"
                  : contact.tracking_label === "watch"
                    ? "warm"
                    : "primary"
            }
          />
          <MetricCard
            label="Outreach Lane"
            value={prettify(contact.outreach_lane || company?.recommended_outreach_lane)}
            hint="The lane drives the sequence family and how warm or direct the motion should be."
            tone={toneForLane(contact.outreach_lane || company?.recommended_outreach_lane)}
          />
          <MetricCard
            label="Warm Path"
            value={contact.warm_intro_strength ? `Strength ${contact.warm_intro_strength}` : Object.keys(displayWarmPath).length > 0 ? "Account path" : "Direct"}
            hint={asText(displayWarmPath.connection_path) || "No warm path saved, so this prospect is likely a direct lane."}
            tone="warm"
          />
          <MetricCard
            label="Account Priority"
            value={priority ? `${priority.priorityBand} / ${priority.priorityScore}` : "Unknown"}
            hint={priority ? `Interest ${priority.interestLevel}; committee ${priority.committeeScore}; leverage ${priority.outreachLeverage}.` : "Account priority becomes available once the parent company is loaded."}
            tone="green"
          />
        </div>

        <div className="prospect-detail-main-grid" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.55fr) minmax(320px, 1fr)", gap: 16, alignItems: "start" }}>
          <div style={{ display: "grid", gap: 14 }}>
            <div className="prospect-detail-outreach-section">
              <Section title="Prospect Outreach" icon={<Send size={15} color={colors.primary} />}>
              <div
                style={{
                  borderRadius: 14,
                  border: `1px solid ${colors.border}`,
                  background: "#fbfdff",
                  padding: "12px 14px",
                  color: colors.sub,
                  fontSize: 13,
                  lineHeight: 1.6,
                }}
              >
                Launch, edit, and configure prospect-specific outreach directly here. Advanced settings lets you override timing for this prospect only before the sequence starts.
              </div>
              <OutreachDrawer contact={contact} onClose={() => {}} mode="inline" />
              </Section>
            </div>

            <Section title="Automation Signals" icon={<UserRound size={15} color={colors.primary} />}>
              <div
                style={{
                  borderRadius: 14,
                  border: `1px solid ${colors.border}`,
                  background: "#fbfdff",
                  padding: "12px 14px",
                  color: colors.sub,
                  fontSize: 13,
                  lineHeight: 1.6,
                }}
              >
                Beacon keeps this prospect current automatically. Instantly updates email stages and replies, Aircall logs call outcomes, and Beacon turns those signals into the stage and momentum you see on this page.
              </div>
              <div className="prospect-detail-signal-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
                <div style={{ border: `1px solid ${colors.border}`, borderRadius: 12, background: "#ffffff", padding: "12px 14px" }}>
                  <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.35 }}>EMAIL SIGNALS</div>
                  <div style={{ marginTop: 6, color: colors.text, fontWeight: 700 }}>{prettify(contact.sequence_status || contact.instantly_status)}</div>
                  <div style={{ marginTop: 4, color: colors.sub, fontSize: 12.5, lineHeight: 1.55 }}>
                    Synced automatically from Instantly email sends, opens, replies, and meeting-booked events.
                  </div>
                </div>
                <div style={{ border: `1px solid ${colors.border}`, borderRadius: 12, background: "#ffffff", padding: "12px 14px" }}>
                  <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.35 }}>CALL SIGNALS</div>
                  <div style={{ marginTop: 6, color: colors.text, fontWeight: 700 }}>{contact.phone ? "Aircall ready" : "Phone missing"}</div>
                  <div style={{ marginTop: 4, color: colors.sub, fontSize: 12.5, lineHeight: 1.55 }}>
                    Answered, missed, voicemail, and recording events flow in automatically from Aircall.
                  </div>
                </div>
                <div style={{ border: `1px solid ${colors.border}`, borderRadius: 12, background: "#ffffff", padding: "12px 14px" }}>
                  <div style={{ color: colors.faint, fontSize: 11, fontWeight: 800, letterSpacing: 0.35 }}>LAST SIGNAL</div>
                  <div style={{ marginTop: 6, color: colors.text, fontWeight: 700 }}>{formatDate(contact.tracking_last_activity_at || contact.updated_at)}</div>
                  <div style={{ marginTop: 4, color: colors.sub, fontSize: 12.5, lineHeight: 1.55 }}>
                    Beacon recalculates stage and momentum whenever a new synced engagement signal arrives.
                  </div>
                </div>
              </div>
            </Section>

            {showEngagementTimeline && <Section title={`Engagement Timeline (${commsLog.length})`} icon={<MessageSquare size={15} color={colors.primary} />}>
              <div
                style={{
                  borderRadius: 14,
                  border: `1px solid ${colors.border}`,
                  background: "#fbfdff",
                  padding: "12px 14px",
                  color: colors.sub,
                  fontSize: 13,
                  lineHeight: 1.6,
                  marginBottom: 12,
                }}
              >
                This feed is automatic. Instantly contributes email activity, Aircall contributes call activity, and Beacon uses both to keep the prospect current without manual logging.
              </div>
              {commsLog.length === 0 ? (
                <div style={{ color: colors.faint, fontSize: 13 }}>No synced activity yet. Once outreach starts or a call happens, the latest events will appear here automatically.</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {commsLog.slice(0, 15).map((act) => {
                    const mediumLabel = act.medium ? act.medium.replace(/_/g, " ") : act.type;
                    const sourceTone =
                      act.source === "instantly"
                        ? { bg: colors.primarySoft, color: colors.primary, border: "#cfe0fb", label: "Instantly" }
                        : act.source === "aircall"
                          ? { bg: colors.greenSoft, color: colors.green, border: "#cdeedc", label: "Aircall" }
                          : { bg: "#f1f5f9", color: colors.sub, border: colors.border, label: act.source || "Beacon" };
                    return (
                      <div key={act.id} style={{
                        padding: "10px 14px", borderRadius: 10,
                        border: `1px solid ${colors.border}`, background: "#fbfdff",
                      }}>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <span style={{
                              fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 6,
                              background: sourceTone.bg, color: sourceTone.color, textTransform: "capitalize", border: `1px solid ${sourceTone.border}`,
                            }}>
                              {sourceTone.label}
                            </span>
                            <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 6, background: "#f1f5f9", color: colors.faint, textTransform: "capitalize" }}>
                              {mediumLabel}
                            </span>
                          </div>
                          <span style={{ fontSize: 11, color: colors.faint }}>{formatDate(act.created_at)}</span>
                        </div>
                        {act.email_subject && (
                          <div style={{ fontSize: 12, fontWeight: 600, color: colors.text, marginBottom: 2 }}>{act.email_subject}</div>
                        )}
                        {act.content && (
                          <div style={{ fontSize: 12, color: colors.sub, lineHeight: 1.5 }}>
                            {act.content.length > 200 ? act.content.slice(0, 200) + "..." : act.content}
                          </div>
                        )}
                        {act.ai_summary && (
                          <div style={{ fontSize: 11, color: colors.primary, marginTop: 4, fontStyle: "italic" }}>
                            {act.ai_summary}
                          </div>
                        )}
                        {act.call_outcome && (
                          <div style={{ fontSize: 11, color: colors.sub, marginTop: 4 }}>
                            Outcome: {act.call_outcome.replace(/_/g, " ")}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </Section>}

            <Section title="Notes" icon={<MessageSquare size={15} color={colors.primary} />}>
              <div className="prospect-detail-notes-row" style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <textarea
                  value={noteInput}
                  onChange={(e) => setNoteInput(e.target.value)}
                  placeholder="Add a note about this prospect..."
                  rows={2}
                  style={{
                    flex: 1, resize: "vertical", border: `1px solid ${colors.border}`,
                    borderRadius: 10, padding: "9px 12px", fontSize: 13, color: colors.text,
                    fontFamily: "inherit", outline: "none",
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault();
                      if (!noteInput.trim() || noteSaving || !contact) return;
                      setNoteSaving(true);
                      accountSourcingApi.addContactNote(contact.id, noteInput.trim()).then((res) => {
                        const updated = { ...(contact.enrichment_data || {}), notes_log: res.notes_log };
                        setContact((prev) => prev ? { ...prev, enrichment_data: updated } : prev);
                        setNoteInput("");
                      }).catch(() => {}).finally(() => setNoteSaving(false));
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={!noteInput.trim() || noteSaving}
                  onClick={async () => {
                    if (!noteInput.trim() || noteSaving || !contact) return;
                    setNoteSaving(true);
                    try {
                      const res = await accountSourcingApi.addContactNote(contact.id, noteInput.trim());
                      const updated = { ...(contact.enrichment_data || {}), notes_log: res.notes_log };
                      setContact((prev) => prev ? { ...prev, enrichment_data: updated } : prev);
                      setNoteInput("");
                    } catch { /* ignore */ } finally { setNoteSaving(false); }
                  }}
                  style={{
                    padding: "0 16px", borderRadius: 10, border: "none",
                    cursor: noteInput.trim() ? "pointer" : "not-allowed",
                    background: noteInput.trim() ? colors.primary : colors.border,
                    color: noteInput.trim() ? "#fff" : colors.faint,
                    fontWeight: 700, fontSize: 13, alignSelf: "flex-end", height: 38, flexShrink: 0,
                  }}
                >
                  {noteSaving ? "..." : "Save"}
                </button>
              </div>
              <div style={{ color: colors.faint, fontSize: 11, marginBottom: 10 }}>⌘↵ or Ctrl+↵ to save quickly</div>
              {(() => {
                const notesLog = Array.isArray((contact?.enrichment_data as Record<string, unknown> | null)?.notes_log)
                  ? ([...((contact?.enrichment_data as Record<string, unknown>)?.notes_log as unknown[])] as Record<string, unknown>[]).reverse()
                  : [];
                if (notesLog.length === 0) {
                  return <div style={{ color: colors.faint, fontSize: 13 }}>No notes yet. Add the first one above.</div>;
                }
                return (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {notesLog.map((entry, idx) => (
                      <div key={idx} style={{ border: `1px solid #d0e8ff`, background: "#f0f7ff", borderRadius: 12, padding: "10px 14px" }}>
                        <div style={{ color: colors.text, fontSize: 13.5, lineHeight: 1.55 }}>{String(entry.message || "")}</div>
                        <div style={{ color: colors.sub, fontSize: 12, marginTop: 4 }}>
                          {entry.actor_name ? `Note by ${String(entry.actor_name)}` : "By system"}
                          {entry.at ? ` · ${new Date(String(entry.at)).toLocaleString()}` : ""}
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </Section>

            <Section title="Sales Playbook" icon={<Send size={15} color={colors.primary} />}>
              <div className="prospect-detail-playbook-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 }}>
                <ListCard title="Hooks To Use" items={planHooks} empty="No personalization hooks saved yet." />
                <ListCard
                  title="Conversation Starter"
                  items={asText(contact.conversation_starter) ? [contact.conversation_starter as string] : asText(companyProfile.conversation_starter) ? [String(companyProfile.conversation_starter)] : []}
                  empty="No conversation starter available."
                />
                <ListCard
                  title="Talking Points"
                  items={Array.isArray(contact.talking_points) ? contact.talking_points.map((item) => String(item)) : asList(aiSummary.talking_points)}
                  empty="No talking points available."
                />
                <ListCard
                  title="Pain Points"
                  items={asList(aiSummary.pain_points)}
                  empty="No AI pain points available yet."
                />
                <ListCard
                  title="Prospecting Priorities"
                  items={Array.isArray(companyProfile.priorities) ? companyProfile.priorities.map((item) => String(item)) : []}
                  empty="No priorities available yet."
                />
              </div>
            </Section>

            <Section title="Uploaded Prospect Intelligence" icon={<Sparkles size={15} color={colors.primary} />}>
              {Object.keys(displayRawRow).length === 0 ? (
                <div style={{ color: colors.faint }}>No uploaded raw row was captured for this prospect.</div>
              ) : (
                <div style={{ display: "grid", gap: 8 }}>
                  {Object.entries(displayRawRow).map(([key, value]) => (
                    <div className="prospect-detail-raw-row" key={key} style={{ display: "grid", gridTemplateColumns: "220px minmax(0,1fr)", gap: 10, borderRadius: 14, border: `1px solid ${colors.border}`, background: "linear-gradient(180deg, #fbfdff 0%, #ffffff 100%)", padding: "11px 13px" }}>
                      <div style={{ color: colors.faint, fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.35 }}>{key}</div>
                      <div style={{ color: colors.sub, fontSize: 13.5, lineHeight: 1.55 }}>{String(value)}</div>
                    </div>
                  ))}
                </div>
              )}
            </Section>
          </div>

          <div style={{ display: "grid", gap: 14 }}>
            <Section title="Prospect Snapshot" icon={<UserRound size={15} color={colors.primary} />}>
              <KV label="Auto Stage" value={getProspectTrackingStage(contact)} />
              <KV label="Momentum" value={`${getProspectTrackingScore(contact)} · ${getProspectTrackingSummary(contact)}`} />
              <KV label="Name" value={fullName} />
              <KV label="Title" value={contact.title} />
              <KV
                label="Email"
                value={editingEmail ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input
                      autoFocus
                      value={emailInput}
                      onChange={(e) => setEmailInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") handleSaveEmail(); if (e.key === "Escape") { setEditingEmail(false); setEmailInput(contact.email || ""); } }}
                      placeholder="email@example.com"
                      style={{ height: 28, borderRadius: 8, border: `1px solid ${colors.primary}`, padding: "0 8px", fontSize: 13, color: colors.text, outline: "none", width: 180 }}
                    />
                    <button type="button" disabled={emailSaving} onClick={() => handleSaveEmail()}
                      style={{ height: 28, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                      {emailSaving ? "Saving..." : "Save"}
                    </button>
                    <button type="button" onClick={() => { setEditingEmail(false); setEmailInput(contact.email || ""); }}
                      style={{ height: 28, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                      Cancel
                    </button>
                  </div>
                ) : contact.email ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <ContactActionButton icon={<Mail size={14} />} href={gmailComposeUrl(contact.email)} label={contact.email} tone="primary" />
                    <button type="button" onClick={() => { setEmailInput(contact.email || ""); setEditingEmail(true); }}
                      style={{ padding: "2px 8px", borderRadius: 6, border: `1px solid ${colors.border}`, background: "#f7f9fc", color: colors.sub, fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                      <PenLine size={12} />
                    </button>
                  </span>
                ) : (
                  <button type="button" onClick={() => { setEmailInput(""); setEditingEmail(true); }}
                    style={{ border: `1px dashed ${colors.border}`, background: "#fbfdff", color: colors.faint, borderRadius: 8, padding: "4px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Plus size={12} /> Add email
                  </button>
                )}
              />
              <KV
                label="Phone"
                value={editingPhone ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input
                      autoFocus
                      value={phoneInput}
                      onChange={(e) => setPhoneInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") handleSavePhone(); if (e.key === "Escape") { setEditingPhone(false); setPhoneInput(contact.phone || ""); } }}
                      placeholder="+1 555-123-4567"
                      style={{ height: 28, borderRadius: 8, border: `1px solid ${colors.primary}`, padding: "0 8px", fontSize: 13, color: colors.text, outline: "none", width: 180 }}
                    />
                    <button type="button" disabled={phoneSaving} onClick={() => handleSavePhone()}
                      style={{ height: 28, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.green}`, background: colors.green, color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                      {phoneSaving ? "Saving..." : "Save"}
                    </button>
                    <button type="button" onClick={() => { setEditingPhone(false); setPhoneInput(contact.phone || ""); }}
                      style={{ height: 28, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                      Cancel
                    </button>
                  </div>
                ) : contact.phone ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <ContactActionButton
                      icon={<Phone size={14} />}
                      onClick={() => window.__aircallDial?.(contact.phone!, fullName || undefined)}
                      label={contact.phone}
                      tone="green"
                    />
                    <button type="button" onClick={() => { setPhoneInput(contact.phone || ""); setEditingPhone(true); }}
                      style={{ padding: "2px 8px", borderRadius: 6, border: `1px solid ${colors.border}`, background: "#f7f9fc", color: colors.sub, fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                      <PenLine size={12} />
                    </button>
                  </span>
                ) : (
                  <button type="button" onClick={() => { setPhoneInput(""); setEditingPhone(true); }}
                    style={{ border: `1px dashed ${colors.border}`, background: "#fbfdff", color: colors.faint, borderRadius: 8, padding: "4px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Plus size={12} /> Add phone
                  </button>
                )}
              />
              <KV label="LinkedIn" value={contact.linkedin_url ? <ContactActionButton icon={<Globe size={14} />} href={contact.linkedin_url} label="View profile" tone="primary" /> : undefined} />
              <KV
                label="Assigned AE"
                value={
                  <AssignDropdown
                    entityType="contact"
                    entityId={contact.id}
                    currentAssignedId={contact.assigned_to_id || undefined}
                    currentAssignedName={contact.assigned_to_name || contact.assigned_rep_email || company?.assigned_to_name || company?.assigned_rep_name || company?.assigned_rep_email}
                    onAssigned={() => load()}
                    role="ae"
                    label="Assign AE"
                  />
                }
              />
              <KV
                label="Assigned SDR"
                value={
                  <AssignDropdown
                    entityType="contact"
                    entityId={contact.id}
                    currentAssignedId={contact.sdr_id || undefined}
                    currentAssignedName={contact.sdr_name || company?.sdr_name || company?.sdr_email}
                    onAssigned={() => load()}
                    role="sdr"
                    label="Assign SDR"
                  />
                }
              />
              <KV label="Sequence Status" value={prettify(contact.sequence_status)} />
              <KV label="Instantly Status" value={prettify(contact.instantly_status)} />
              <KV label="Persona" value={contact.persona_type || contact.persona} />
              <KV label="Enriched" value={formatDate(contact.enriched_at)} />
              <KV label="Updated" value={formatDate(contact.updated_at)} />
            </Section>

            <Section title="Company Context" icon={<Building2 size={15} color={colors.primary} />}>
              {company ? (
                <>
                  <KV label="Company" value={<Link to={`/account-sourcing/${company.id}`} style={{ color: colors.primary, textDecoration: "none" }}>{company.name}</Link>} />
                  <KV label="Domain" value={
                    editingDomain ? (
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <input
                          autoFocus
                          value={domainInput}
                          onChange={(e) => setDomainInput(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") handleSaveDomain(); if (e.key === "Escape") setEditingDomain(false); }}
                          placeholder="e.g. acme.com"
                          style={{ height: 28, borderRadius: 8, border: `1px solid ${colors.primary}`, padding: "0 8px", fontSize: 13, color: colors.text, outline: "none", width: 180 }}
                        />
                        <button type="button" disabled={domainSaving} onClick={handleSaveDomain}
                          style={{ height: 28, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.primary}`, background: colors.primary, color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                          {domainSaving ? "Saving…" : "Save"}
                        </button>
                        <button type="button" onClick={() => setEditingDomain(false)}
                          style={{ height: 28, padding: "0 10px", borderRadius: 8, border: `1px solid ${colors.border}`, background: "#fff", color: colors.faint, fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                        <span style={{ color: isPlaceholderDomain(company.domain) ? colors.faint : undefined, fontStyle: isPlaceholderDomain(company.domain) ? "italic" : undefined }}>
                          {company.domain ? formatDomain(company.domain) : "No domain set"}
                        </span>
                        <button type="button" onClick={() => { setDomainInput(company.domain || ""); setEditingDomain(true); }}
                          style={{ padding: "2px 8px", borderRadius: 6, border: `1px solid ${colors.border}`, background: "#f7f9fc", color: colors.primary, fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                          Edit
                        </button>
                      </span>
                    )
                  } />
                  <KV label="Account Thesis" value={company.account_thesis} />
                  <KV label="Why Now" value={company.why_now} />
                  <KV label="Beacon Angle" value={company.beacon_angle} />
                  <KV label="Recommended Strategy" value={asText(companyProfile.recommended_outreach_strategy)} />
                  <KV label="Next Best Action" value={asText(companyPlan.next_best_action)} />
                  <KV label="Uploaded Fit Type" value={asText(analyst.fit_type)} />
                </>
              ) : (
                <div style={{ color: colors.faint }}>This prospect is not attached to a sourced company.</div>
              )}
            </Section>

          </div>
        </div>
      </div>

      <TaskCenterModal
        isOpen={showTasksModal}
        onClose={() => setShowTasksModal(false)}
        entityType="contact"
        entityId={contact.id}
        entityLabel={fullName || "this prospect"}
        onChanged={() => {
          void load();
        }}
      />
      </div>
    </>
  );
}

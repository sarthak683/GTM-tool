import { useEffect, useState, type CSSProperties } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  companiesApi,
  contactsApi,
  dealsApi,
  enrichmentApi,
  intelligenceApi,
  outreachApi,
  signalsApi,
} from "../lib/api";
import type { Company, Contact, Deal, Signal } from "../types";
import {
  ArrowLeft,
  BrainCircuit,
  Building2,
  ExternalLink,
  Plus,
  RefreshCw,
  Sparkles,
  Target,
  Trash2,
  TrendingUp,
  Users,
  X,
} from "lucide-react";
import {
  getProspectTrackingScore,
  getProspectTrackingStage,
  getProspectTrackingSummary,
  getProspectTrackingTone,
} from "../lib/prospectTracking";
import { useAuth } from "../lib/AuthContext";
import { formatCurrency, formatDate, avatarColor, getInitials } from "../lib/utils";
import OutreachDrawer from "../components/outreach/OutreachDrawer";

const TIER_STYLE: Record<string, CSSProperties> = {
  hot: { color: "#8f2f11", background: "#ffe4d9", border: "1px solid #ffc5b3" },
  warm: { color: "#86581a", background: "#fff3dd", border: "1px solid #f7dda4" },
  monitor: { color: "#265179", background: "#eaf4ff", border: "1px solid #c7def8" },
  cold: { color: "#4f6073", background: "#eef3f8", border: "1px solid #d5e0ea" },
};

const PERSONA_STYLE: Record<string, CSSProperties> = {
  economic_buyer: { color: "#7b3a1d", background: "#ffe8de", border: "1px solid #ffc8b4" },
  champion: { color: "#1b6f53", background: "#e4fbf3", border: "1px solid #b8efd8" },
  technical_evaluator: { color: "#24567e", background: "#eaf4ff", border: "1px solid #c9e0f8" },
  unknown: { color: "#546679", background: "#edf3f9", border: "1px solid #d7e1eb" },
};

const PERSONA_SHORT: Record<string, string> = {
  economic_buyer: "Buyer", champion: "Champion", technical_evaluator: "Tech Eval", unknown: "Unknown",
};

function canonicalPersona(persona?: string | null, personaType?: string | null): keyof typeof PERSONA_STYLE {
  const normalized = (persona || personaType || "").toLowerCase();
  if (normalized === "buyer" || normalized === "economic_buyer") return "economic_buyer";
  if (normalized === "champion") return "champion";
  if (normalized === "evaluator" || normalized === "technical_evaluator") return "technical_evaluator";
  return "unknown";
}

function SummaryStat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "neutral" | "warm" | "blue" | "green";
}) {
  const tones = {
    neutral: { bg: "#fbfdff", border: "#e3eaf3", accent: "#506579" },
    warm: { bg: "#fff6ef", border: "#ffd7c7", accent: "#b4532a" },
    blue: { bg: "#f2f8ff", border: "#d5e5ff", accent: "#24567e" },
    green: { bg: "#eefcf5", border: "#ccefdc", accent: "#1b6f53" },
  }[tone];

  return (
    <div className="crm-panel" style={{ padding: "16px 18px", background: tones.bg, borderColor: tones.border }}>
      <p className="text-[11px] uppercase tracking-[0.08em] text-[#7d8fa3] font-semibold">{label}</p>
      <p className="text-[28px] font-extrabold mt-2" style={{ color: tones.accent }}>{value}</p>
      <p className="text-[12px] text-[#6f8399] mt-1">{hint}</p>
    </div>
  );
}

function CompanySection({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="crm-panel" style={{ padding: 24 }}>
      <div className="flex items-center justify-between gap-3 mb-4">
        <p className="text-[15px] font-bold text-[#2b3f55]">{title}</p>
        {action}
      </div>
      {children}
    </section>
  );
}

export default function CompanyDetail() {
  const { isAdmin } = useAuth();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [company, setCompany] = useState<Company | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [companyDeals, setCompanyDeals] = useState<Deal[]>([]);
  const [loading, setLoading] = useState(true);
  const [enriching, setEnriching] = useState(false);
  const [enrichMsg, setEnrichMsg] = useState("");
  const [bulkGenerating, setBulkGenerating] = useState(false);
  const [bulkMsg, setBulkMsg] = useState("");
  const [signalsRefreshing, setSignalsRefreshing] = useState(false);
  const [signalsMsg, setSignalsMsg] = useState("");
  const [showDealModal, setShowDealModal] = useState(false);
  const [creatingDeal, setCreatingDeal] = useState(false);
  const [dealError, setDealError] = useState("");
  const [dealForm, setDealForm] = useState({
    name: "",
    value: "",
    stage: "discovery",
    close_date_est: "",
  });
  const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
  const [brief, setBrief] = useState<string | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [contactBriefs, setContactBriefs] = useState<Record<string, string>>({});
  const [contactBriefLoading, setContactBriefLoading] = useState<Record<string, boolean>>({});
  const [discoveringContacts, setDiscoveringContacts] = useState(false);
  const [discoverMsg, setDiscoverMsg] = useState("");

  const loadCompanyContext = async (companyId: string) => {
    const [c, cs, ds, sig] = await Promise.all([
      companiesApi.get(companyId),
      contactsApi.list(0, 100, companyId),
      companiesApi.getDeals(companyId),
      signalsApi.getCompanySignals(companyId),
    ]);
    setCompany(c);
    setContacts(cs);
    setCompanyDeals(ds);
    setSignals(sig.slice(0, 8));
  };

  useEffect(() => {
    if (!id) return;
    loadCompanyContext(id).finally(() => setLoading(false));
  }, [id]);

  const handleGetBrief = async () => {
    if (!company) return;
    setBriefLoading(true); setBrief(null);
    try { const r = await intelligenceApi.getAccountBrief(company.id); setBrief(r.brief ?? "No brief generated."); }
    catch { setBrief("Failed — check API is running."); }
    finally { setBriefLoading(false); }
  };

  const handleBulkOutreach = async () => {
    if (!company) return;
    setBulkGenerating(true); setBulkMsg("Generating with GPT-4o…");
    try {
      const r = await outreachApi.bulkGenerate(company.id);
      setBulkMsg(`${r.generated} generated · ${r.skipped_existing} existed`);
      setTimeout(() => setBulkMsg(""), 4000);
    } catch { setBulkMsg("Failed"); }
    finally { setBulkGenerating(false); }
  };

  const handleEnrich = async () => {
    if (!company) return;
    setEnriching(true); setEnrichMsg("Queuing…");
    try {
      const { task_id } = await enrichmentApi.triggerCompany(company.id);
      setEnrichMsg("Enriching…");
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const s = await enrichmentApi.taskStatus(task_id);
        if (s.status === "SUCCESS") break;
        if (s.status === "FAILURE") { setEnrichMsg("Failed"); return; }
      }
      await loadCompanyContext(company.id);
      setEnrichMsg("Done ✓");
      setTimeout(() => setEnrichMsg(""), 3000);
    } catch { setEnrichMsg("Error"); }
    finally { setEnriching(false); }
  };

  const handleRefreshSignals = async () => {
    if (!company) return;
    setSignalsRefreshing(true);
    setSignalsMsg("");
    try {
      const res = await signalsApi.refreshCompanySignals(company.id);
      const latest = await signalsApi.getCompanySignals(company.id);
      setSignals(latest.slice(0, 8));
      setSignalsMsg(`${res.signals_created} new signals`);
      setTimeout(() => setSignalsMsg(""), 4000);
    } catch {
      setSignalsMsg("Signal refresh failed");
    } finally {
      setSignalsRefreshing(false);
    }
  };

  const handleCreateDeal = async () => {
    if (!company) return;
    if (!dealForm.name.trim()) {
      setDealError("Deal name is required.");
      return;
    }

    setCreatingDeal(true);
    setDealError("");
    try {
      await dealsApi.create({
        company_id: company.id,
        name: dealForm.name.trim(),
        stage: dealForm.stage,
        value: dealForm.value ? Number(dealForm.value) : undefined,
        close_date_est: dealForm.close_date_est || undefined,
      });
      const refreshedDeals = await companiesApi.getDeals(company.id);
      setCompanyDeals(refreshedDeals);
      setShowDealModal(false);
      setDealForm({ name: "", value: "", stage: "discovery", close_date_est: "" });
    } catch (e) {
      setDealError(e instanceof Error ? e.message : "Failed to create deal");
    } finally {
      setCreatingDeal(false);
    }
  };

  const handleDiscoverContacts = async () => {
    if (!company) return;
    setDiscoveringContacts(true);
    setDiscoverMsg("Searching Hunter…");
    try {
      const found = await contactsApi.discover(company.id);
      if (found.length === 0) {
        setDiscoverMsg("No new contacts found");
      } else {
        setContacts((prev) => [...prev, ...found]);
        setDiscoverMsg(`${found.length} contact${found.length !== 1 ? "s" : ""} added`);
      }
      setTimeout(() => setDiscoverMsg(""), 4000);
    } catch {
      setDiscoverMsg("Discovery failed");
    } finally {
      setDiscoveringContacts(false);
    }
  };

  const handleContactBrief = async (contactId: string) => {
    setContactBriefLoading((prev) => ({ ...prev, [contactId]: true }));
    try {
      const res = await contactsApi.getBrief(contactId);
      setContactBriefs((prev) => ({ ...prev, [contactId]: res.brief ?? "No brief generated." }));
    } catch {
      setContactBriefs((prev) => ({ ...prev, [contactId]: "Failed to generate brief." }));
    } finally {
      setContactBriefLoading((prev) => ({ ...prev, [contactId]: false }));
    }
  };

  if (loading) return <div className="crm-panel p-14 text-center crm-muted">Loading company profile...</div>;
  if (!company) return <div className="crm-panel p-14 text-center crm-muted">Company not found.</div>;

  const techStack = company.tech_stack as Record<string, string> | null;
  const initials = getInitials(company.name);
  const avatarCls = avatarColor(company.name);
  const tier = company.icp_tier ?? "monitor";
  const stakeholderCount = contacts.length;
  const activeDeals = companyDeals.filter((deal) => !["closed_won", "closed_lost"].includes(deal.stage)).length;
  const signalCount = signals.length;
  const totalPipeline = companyDeals.reduce((sum, deal) => sum + (deal.value ?? 0), 0);
  const personaSummary = contacts.reduce(
    (acc, contact) => {
      const persona = canonicalPersona(contact.persona, contact.persona_type);
      acc[persona] += 1;
      return acc;
    },
    { economic_buyer: 0, champion: 0, technical_evaluator: 0, unknown: 0 } as Record<keyof typeof PERSONA_STYLE, number>,
  );

  return (
    <>
      <div className="crm-page company-detail-page" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <div className="flex flex-wrap items-center justify-between gap-3 company-detail-top-actions">
          <button onClick={() => navigate(-1)} className="crm-button soft">
            <ArrowLeft className="h-3.5 w-3.5" />
            Back
          </button>
          <div className="flex flex-wrap items-center gap-2">
            {enrichMsg && <span className="text-[12px] text-[#ff6b35] font-semibold">{enrichMsg}</span>}
            <button className="crm-button soft" onClick={handleEnrich} disabled={enriching}>
              <RefreshCw className={`h-3.5 w-3.5 ${enriching ? "animate-spin" : ""}`} />
              {enriching ? "Enriching..." : "Re-enrich"}
            </button>
            {isAdmin ? (
              <button
                className="crm-button soft text-[#c0392b] border-[#fcc] hover:bg-[#fff5f5]"
                onClick={async () => {
                  if (!company) return;
                  if (!window.confirm(`Delete "${company.name}"? This will remove all associated contacts and deals.`)) return;
                  await companiesApi.delete(company.id);
                  navigate("/companies");
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete
              </button>
            ) : null}
          </div>
        </div>

        <section
          className="crm-panel company-detail-hero"
          style={{
            padding: 32,
            background:
              "linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(246,250,255,0.98) 58%, rgba(255,244,238,0.98) 100%)",
          }}
        >
          <div className="flex flex-col gap-8">
            <div className="flex flex-wrap items-start justify-between gap-6">
              <div className="flex items-start gap-5 min-w-0">
                <div className={`flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-[24px] text-[18px] font-extrabold shadow-sm ${avatarCls}`}>
                  {initials}
                </div>
                <div className="min-w-0">
                  <p className="text-[11px] uppercase tracking-[0.12em] text-[#7c8ea1] font-semibold">Account Overview</p>
                  <h2 className="text-[34px] leading-tight font-extrabold tracking-tight text-[#1f2d3d] mt-2">{company.name}</h2>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mt-3 text-[14px] text-[#61788f]">
                    <a href={`https://${company.domain}`} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-[#ff6b35]">
                      {company.domain}
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                    <span>{company.industry ?? "Industry not set"}</span>
                    <span>{company.employee_count ? `${company.employee_count.toLocaleString()} employees` : "Employee count unknown"}</span>
                  </div>
                  <p className="max-w-3xl text-[15px] leading-7 text-[#52687f] mt-4">
                    {company.vertical
                      ? `${company.name} is being tracked in the ${company.vertical} motion. Use this page to understand fit, map the buying committee, and decide the next best action before outreach or meeting prep.`
                      : "Use this page to understand account fit, map stakeholders, and keep outreach grounded in recent account context."}
                  </p>
                </div>
              </div>

              <div className="flex flex-col items-start gap-3 min-w-[220px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center px-3 py-1 rounded-full text-[11px] font-bold capitalize" style={TIER_STYLE[tier] ?? TIER_STYLE.cold}>
                    {tier}
                  </span>
                  {company.icp_score != null && <span className="crm-chip tabular">ICP {company.icp_score}</span>}
                  <span className="crm-chip text-[10px]">{formatDate(company.enriched_at)}</span>
                </div>
                <button className="crm-button primary w-full" onClick={handleBulkOutreach} disabled={bulkGenerating}>
                  <Sparkles className="h-3.5 w-3.5" />
                  {bulkGenerating ? "Generating..." : "Generate Outreach For All"}
                </button>
                {bulkMsg && <p className="text-[12px] text-[#ff6b35]">{bulkMsg}</p>}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
              <SummaryStat
                label="ICP Score"
                value={company.icp_score != null ? `${company.icp_score}` : "-"}
                hint={company.icp_score != null ? `${100 - company.icp_score} points from max fit` : "Run enrichment to score fit"}
                tone="warm"
              />
              <SummaryStat
                label="Stakeholders"
                value={`${stakeholderCount}`}
                hint={stakeholderCount === 0 ? "No contacts mapped yet" : `${personaSummary.economic_buyer} buyers, ${personaSummary.champion} champions`}
                tone="blue"
              />
              <SummaryStat
                label="Active Deals"
                value={`${activeDeals}`}
                hint={activeDeals > 0 ? `${formatCurrency(totalPipeline)} open pipeline` : "No open pipeline yet"}
                tone="green"
              />
              <SummaryStat
                label="Signals"
                value={`${signalCount}`}
                hint={signalCount > 0 ? "Recent account activity captured" : "Refresh to pull recent activity"}
              />
            </div>

            {company.icp_score != null && (
              <div>
                <div className="flex items-center justify-between gap-3 mb-2">
                  <p className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[#8093a8]">Account fit progress</p>
                  <p className="text-[12px] font-semibold text-[#516779]">{company.icp_score}% match to ICP</p>
                </div>
                <div className="h-2.5 rounded-full bg-[#eaf0f6] overflow-hidden">
                  <div className="h-2.5 rounded-full bg-[#ff6b35]" style={{ width: `${company.icp_score}%` }} />
                </div>
              </div>
            )}
          </div>
        </section>

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,0.95fr)] gap-6 items-start">
          <div className="space-y-6">
            <CompanySection
              title="AI Account Brief"
              action={
                <button className="crm-button soft" onClick={handleGetBrief} disabled={briefLoading}>
                  {briefLoading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  {briefLoading ? "Researching..." : "Generate Brief"}
                </button>
              }
            >
              {brief ? (
                <div className="rounded-2xl border border-[#dce6f0] bg-[#f8fbff] p-5 space-y-2.5">
                  {brief
                    .split("\n")
                    .filter((line) => line.trim())
                    .map((line, i) => (
                      <p key={i} className="text-[14px] leading-7 text-[#2d4258]">{line}</p>
                    ))}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-[#d5e2ee] bg-[#fbfdff] p-5">
                  <p className="text-[14px] leading-7 text-[#6f8399]">
                    Generate a concise account brief before outreach or meeting prep. We’ll summarize fit, account context, and recent activity into a seller-friendly readout.
                  </p>
                </div>
              )}
            </CompanySection>

            <CompanySection
              title={`Stakeholders (${contacts.length})`}
              action={
                <div className="flex items-center gap-2">
                  {discoverMsg && <span className="text-[12px] text-[#ff6b35] font-semibold">{discoverMsg}</span>}
                  <button className="crm-button soft" onClick={handleDiscoverContacts} disabled={discoveringContacts}>
                    <Users className={`h-3.5 w-3.5 ${discoveringContacts ? "animate-pulse" : ""}`} />
                    {discoveringContacts ? "Searching..." : "Find Contacts"}
                  </button>
                </div>
              }
            >
              {contacts.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-[#d5e2ee] bg-[#fbfdff] p-5">
                  <p className="text-[14px] leading-7 text-[#6f8399]">No contacts are mapped to this account yet. Discover more stakeholders before running outreach or meeting prep.</p>
                </div>
              ) : (
                <div className="grid gap-4">
                  {contacts.map((c) => {
                    const persona = canonicalPersona(c.persona, c.persona_type);
                    const trackingTone = getProspectTrackingTone(c);
                    return (
                      <div key={c.id} className="rounded-2xl border border-[#e0e8f1] bg-white p-5">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div className="flex items-start gap-3 min-w-0">
                            <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[12px] font-extrabold ${avatarColor(c.first_name + c.last_name)}`}>
                              {getInitials(`${c.first_name} ${c.last_name}`)}
                            </div>
                            <div className="min-w-0">
                              <p className="text-[15px] font-bold text-[#26384e]">{c.first_name} {c.last_name}</p>
                              <p className="text-[13px] text-[#6f8399] mt-1">{c.title ?? "Title not available"}</p>
                              <div className="flex flex-wrap items-center gap-2 mt-3 text-[12px] text-[#688097]">
                                {c.email && <span className="rounded-full bg-[#f2f6fa] px-2.5 py-1">{c.email}</span>}
                                {c.linkedin_url && (
                                  <a href={c.linkedin_url} target="_blank" rel="noreferrer" className="rounded-full bg-[#eef5ff] px-2.5 py-1 text-[#335f93] hover:text-[#ff6b35]">
                                    LinkedIn
                                  </a>
                                )}
                              </div>
                            </div>
                          </div>

                          <div className="flex flex-wrap items-center justify-end gap-2">
                            <span
                              className="inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-bold"
                              style={{ background: trackingTone.background, color: trackingTone.color, border: `1px solid ${trackingTone.border}` }}
                            >
                              {getProspectTrackingStage(c)}
                            </span>
                            <span className="inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-bold" style={PERSONA_STYLE[persona]}>
                              {PERSONA_SHORT[persona]}
                            </span>
                            <button className="crm-button soft h-10 px-3 text-[12px]" onClick={() => handleContactBrief(c.id)} disabled={contactBriefLoading[c.id]}>
                              {contactBriefLoading[c.id] ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <BrainCircuit className="h-3.5 w-3.5" />}
                              Brief
                            </button>
                            <button className="crm-button soft h-10 px-3 text-[12px]" onClick={() => setSelectedContact(c)}>
                              <Sparkles className="h-3.5 w-3.5" />
                              Outreach
                            </button>
                            <button
                              onClick={async () => {
                                if (!window.confirm(`Delete "${c.first_name} ${c.last_name}"?`)) return;
                                await contactsApi.delete(c.id);
                                setContacts((prev) => prev.filter((x) => x.id !== c.id));
                              }}
                              className="flex items-center justify-center h-10 w-10 rounded-xl text-[#9eb0c3] hover:text-[#c0392b] hover:bg-[#fff0f0] transition-colors"
                              title="Delete contact"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>

                        <div
                          className="mt-4 rounded-xl px-4 py-3"
                          style={{ background: trackingTone.soft, border: `1px solid ${trackingTone.border}` }}
                        >
                          <div className="flex items-center justify-between gap-3 flex-wrap">
                            <span style={{ color: trackingTone.color }} className="text-[12px] font-bold">
                              Automated progress
                            </span>
                            <span style={{ color: trackingTone.color }} className="text-[12px] font-extrabold">
                              {getProspectTrackingScore(c)}
                            </span>
                          </div>
                          <p className="mt-1.5 text-[13px] leading-6 text-[#2d4258]">{getProspectTrackingSummary(c)}</p>
                        </div>

                        {contactBriefs[c.id] && (
                          <div className="rounded-xl border border-[#dce6f0] bg-[#f8fbff] px-4 py-3 mt-4 space-y-1.5">
                            {contactBriefs[c.id]
                              .split("\n")
                              .filter(Boolean)
                              .map((line, i) => (
                                <p key={i} className="text-[13px] leading-6 text-[#2d4258]">{line}</p>
                              ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </CompanySection>

            <CompanySection
              title="Signals"
              action={
                <div className="flex items-center gap-2">
                  {signalsMsg && <span className="text-[12px] text-[#ff6b35] font-semibold">{signalsMsg}</span>}
                  <button className="crm-button soft h-10 px-3" onClick={handleRefreshSignals} disabled={signalsRefreshing}>
                    <RefreshCw className={`h-3.5 w-3.5 ${signalsRefreshing ? "animate-spin" : ""}`} />
                    Refresh
                  </button>
                </div>
              }
            >
              {signals.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-[#d5e2ee] bg-[#fbfdff] p-5">
                  <p className="text-[14px] leading-7 text-[#6f8399]">No signals available yet. Pull recent activity to give reps better timing context before outreach.</p>
                </div>
              ) : (
                <div className="grid gap-3">
                  {signals.slice(0, 6).map((s) => (
                    <div key={s.id} className="rounded-2xl border border-[#e3eaf3] bg-[#fbfdff] p-4">
                      <div className="flex items-center gap-2 text-[12px] font-semibold text-[#6f8399] capitalize">
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{
                            background:
                              s.signal_type === "funding"
                                ? "#f59e0b"
                                : s.signal_type === "pr"
                                  ? "#3b82f6"
                                  : s.signal_type === "news"
                                    ? "#64748b"
                                    : "#94a3b8",
                          }}
                        />
                        {s.signal_type}
                      </div>
                      <p className="text-[14px] font-semibold text-[#2b3f55] mt-2">{s.title}</p>
                      {s.summary && <p className="text-[13px] leading-6 text-[#647a91] mt-2">{s.summary}</p>}
                    </div>
                  ))}
                </div>
              )}
            </CompanySection>
          </div>

          <div className="space-y-6">
            <CompanySection
              title="Account Snapshot"
              action={
                <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-[#7b8ea4]">
                  <Building2 className="h-3.5 w-3.5" />
                  Key firmographics
                </span>
              }
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {[
                  { label: "Industry", value: company.industry },
                  { label: "Employees", value: company.employee_count?.toLocaleString() },
                  { label: "ARR Estimate", value: formatCurrency(company.arr_estimate) },
                  { label: "Funding", value: company.funding_stage },
                  { label: "Vertical", value: company.vertical },
                  { label: "DAP", value: company.has_dap ? company.dap_tool ?? "Yes" : "No" },
                ].map((item) => (
                  <div key={item.label} className="rounded-xl border border-[#e3eaf3] bg-[#f9fbfe] px-4 py-4">
                    <p className="text-[11px] uppercase tracking-[0.08em] text-[#7d8fa3] font-semibold">{item.label}</p>
                    <p className="text-[14px] font-bold text-[#2b3f55] mt-1.5">{item.value ?? "-"}</p>
                  </div>
                ))}
              </div>
            </CompanySection>

            <CompanySection
              title={`Pipeline & Automation (${companyDeals.length})`}
              action={
                <button className="crm-button primary h-10 px-3" onClick={() => setShowDealModal(true)}>
                  <Plus className="h-3.5 w-3.5" />
                  Create Deal
                </button>
              }
            >
              <div className="rounded-2xl border border-[#e5edf5] bg-[#fbfdff] p-4 mb-4">
                <div className="flex items-start gap-3">
                  <div className="rounded-xl bg-[#fff1eb] p-2 text-[#ff6b35]">
                    <Target className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-[14px] font-semibold text-[#24364b]">Outreach automation</p>
                    <p className="text-[13px] leading-6 text-[#6f8399] mt-1">
                      Keep this account warm with refreshed firmographics and one-click outreach generation across mapped stakeholders.
                    </p>
                  </div>
                </div>
              </div>

              {companyDeals.length === 0 ? (
                <p className="text-[13px] text-[#6f8399]">No deals linked yet.</p>
              ) : (
                <div className="grid gap-3">
                  {companyDeals.map((d) => (
                    <div key={d.id} className="rounded-xl border border-[#e3eaf3] bg-[#fbfdff] px-4 py-3 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <Link to={`/pipeline?deal=${d.id}`} className="text-[14px] font-bold text-[#24364b] hover:text-[#ff6b35]">
                          {d.name}
                        </Link>
                        <p className="text-[12px] text-[#7a8ea4] mt-1 capitalize">{d.stage.replace(/_/g, " ")} · {formatDate(d.close_date_est)}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-[14px] font-bold tabular text-[#2d4056]">{formatCurrency(d.value)}</span>
                        <button
                          onClick={async () => {
                            if (!window.confirm(`Delete deal "${d.name}"?`)) return;
                            await dealsApi.delete(d.id);
                            setCompanyDeals((prev) => prev.filter((x) => x.id !== d.id));
                          }}
                          className="flex items-center justify-center h-7 w-7 rounded-lg text-[#9eb0c3] hover:text-[#c0392b] hover:bg-[#fff0f0] transition-colors"
                          title="Delete deal"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CompanySection>

            {techStack && Object.keys(techStack).length > 0 && (
              <CompanySection
                title="Tech Stack"
                action={
                  <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-[#7b8ea4]">
                    <TrendingUp className="h-3.5 w-3.5" />
                    Context for qualification
                  </span>
                }
              >
                <div className="flex flex-wrap gap-2.5">
                  {Object.entries(techStack).map(([name, tool]) => (
                    <span key={name} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-[#d8e3ee] bg-white text-[12px]">
                      <span className="text-[#7a8ea4] capitalize">{name}:</span>
                      <span className="font-semibold text-[#2e4358]">{tool}</span>
                    </span>
                  ))}
                </div>
              </CompanySection>
            )}

            <CompanySection
              title="Stakeholder Mix"
              action={
                <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-[#7b8ea4]">
                  <Users className="h-3.5 w-3.5" />
                  Buying coverage
                </span>
              }
            >
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-xl border border-[#e7eef6] bg-[#fff8f4] px-4 py-4">
                  <p className="text-[11px] uppercase tracking-[0.08em] text-[#a06f53] font-semibold">Economic Buyers</p>
                  <p className="text-[24px] font-extrabold text-[#7b3a1d] mt-2">{personaSummary.economic_buyer}</p>
                </div>
                <div className="rounded-xl border border-[#d9efe4] bg-[#f2fcf7] px-4 py-4">
                  <p className="text-[11px] uppercase tracking-[0.08em] text-[#48826b] font-semibold">Champions</p>
                  <p className="text-[24px] font-extrabold text-[#1b6f53] mt-2">{personaSummary.champion}</p>
                </div>
                <div className="rounded-xl border border-[#d8e6f5] bg-[#f4f9ff] px-4 py-4">
                  <p className="text-[11px] uppercase tracking-[0.08em] text-[#4c6f91] font-semibold">Tech Evaluators</p>
                  <p className="text-[24px] font-extrabold text-[#24567e] mt-2">{personaSummary.technical_evaluator}</p>
                </div>
                <div className="rounded-xl border border-[#e4ebf3] bg-[#f8fbfe] px-4 py-4">
                  <p className="text-[11px] uppercase tracking-[0.08em] text-[#6b7c8d] font-semibold">Unclassified</p>
                  <p className="text-[24px] font-extrabold text-[#4f6073] mt-2">{personaSummary.unknown}</p>
                </div>
              </div>
            </CompanySection>
          </div>
        </div>
      </div>

      {showDealModal && (
        <>
          <div className="fixed inset-0 bg-black/25 z-40" onClick={() => setShowDealModal(false)} />
          <div className="fixed inset-0 z-50 grid place-items-center p-4">
            <div className="crm-panel w-full max-w-lg p-6 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-[18px] font-bold">Create Deal</h3>
                <button className="text-[#7a8ea4] hover:text-[#31465f]" onClick={() => setShowDealModal(false)}>
                  <X size={18} />
                </button>
              </div>

              <div className="grid gap-3">
                <input
                  className="h-11 rounded-xl border border-[#d7e2ee] px-3 text-[14px]"
                  placeholder="Deal name"
                  value={dealForm.name}
                  onChange={(e) => setDealForm((f) => ({ ...f, name: e.target.value }))}
                />
                <div className="grid grid-cols-2 gap-3">
                  <input
                    type="number"
                    className="h-11 rounded-xl border border-[#d7e2ee] px-3 text-[14px]"
                    placeholder="Value"
                    value={dealForm.value}
                    onChange={(e) => setDealForm((f) => ({ ...f, value: e.target.value }))}
                  />
                  <select
                    className="h-11 rounded-xl border border-[#d7e2ee] px-3 text-[14px] bg-white"
                    value={dealForm.stage}
                    onChange={(e) => setDealForm((f) => ({ ...f, stage: e.target.value }))}
                  >
                    <option value="discovery">discovery</option>
                    <option value="demo">demo</option>
                    <option value="poc">poc</option>
                    <option value="proposal">proposal</option>
                    <option value="negotiation">negotiation</option>
                    <option value="closed_won">closed_won</option>
                    <option value="closed_lost">closed_lost</option>
                  </select>
                </div>
                <input
                  type="date"
                  className="h-11 rounded-xl border border-[#d7e2ee] px-3 text-[14px]"
                  value={dealForm.close_date_est}
                  onChange={(e) => setDealForm((f) => ({ ...f, close_date_est: e.target.value }))}
                />
              </div>

              {dealError && <p className="text-[12px] text-[#b94a24] font-semibold">{dealError}</p>}

              <div className="flex justify-end gap-2">
                <button className="crm-button soft" onClick={() => setShowDealModal(false)}>Cancel</button>
                <button className="crm-button primary" onClick={handleCreateDeal} disabled={creatingDeal}>
                  {creatingDeal ? "Creating..." : "Create Deal"}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      <OutreachDrawer contact={selectedContact} onClose={() => setSelectedContact(null)} />
    </>
  );
}

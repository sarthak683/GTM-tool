import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, RefreshCw, Sparkles, Linkedin, Mail, Phone, UserCircle2 } from "lucide-react";
import { companiesApi, contactsApi, outreachApi } from "../lib/api";
import {
  getProspectTrackingScore,
  getProspectTrackingStage,
  getProspectTrackingSummary,
  getProspectTrackingTone,
} from "../lib/prospectTracking";
import type { Company, Contact, OutreachSequence } from "../types";
import { avatarColor, getInitials } from "../lib/utils";
import OutreachDrawer from "../components/outreach/OutreachDrawer";
import AccountSourcingContactDetail from "./AccountSourcingContactDetail";
import LogLinkedInDialog from "../components/LogLinkedInDialog";
import UnifiedTimeline from "../components/UnifiedTimeline";

export default function ContactDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [contact, setContact] = useState<Contact | null>(null);
  const [company, setCompany] = useState<Company | null>(null);
  const [sequence, setSequence] = useState<OutreachSequence | null>(null);
  const [brief, setBrief] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [briefLoading, setBriefLoading] = useState(false);
  const [seqLoading, setSeqLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingTimezone, setEditingTimezone] = useState(false);
  const [timezoneDraft, setTimezoneDraft] = useState("");
  const [linkedInDialogOpen, setLinkedInDialogOpen] = useState(false);

  const loadContact = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const c = await contactsApi.get(id);
      setContact(c);
      setTimezoneDraft(c.timezone ?? "");

      const tasks: Promise<unknown>[] = [];
      if (c.company_id) {
        tasks.push(companiesApi.get(c.company_id).then((co) => setCompany(co)));
      } else {
        setCompany(null);
      }
      tasks.push(
        outreachApi
          .getSequenceOptional(id)
          .then((s) => setSequence(s))
          .catch(() => setSequence(null))
      );
      await Promise.all(tasks);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadContact();
  }, [id]);

  const handleGetBrief = async () => {
    if (!id) return;
    setBriefLoading(true);
    try {
      const result = await contactsApi.getBrief(id);
      setBrief(result.brief ?? "No brief generated.");
    } catch {
      setBrief("Failed to generate brief.");
    } finally {
      setBriefLoading(false);
    }
  };

  const handleGenerateOutreach = async () => {
    if (!id) return;
    setSeqLoading(true);
    try {
      const generated = await outreachApi.generate(id);
      setSequence(generated);
      setDrawerOpen(true);
    } finally {
      setSeqLoading(false);
    }
  };

  const handleSaveTimezone = async () => {
    if (!contact || !id) return;
    const updated = await contactsApi.update(id, { timezone: timezoneDraft.trim() || null } as Partial<Contact>);
    setContact(updated);
    setEditingTimezone(false);
  };

  if (loading) {
    return <div className="crm-panel p-14 text-center crm-muted">Loading contact profile...</div>;
  }

  if (!contact) {
    return <div className="crm-panel p-14 text-center crm-muted">Contact not found.</div>;
  }

  const trackingTone = getProspectTrackingTone(contact);

  const isSourcedContact = Boolean(
    contact.company_id
    || company?.sourcing_batch_id
    || (contact.enrichment_data && typeof contact.enrichment_data === "object" && (
      (contact.enrichment_data as Record<string, unknown>).raw_row
      || (contact.enrichment_data as Record<string, unknown>).sequence_plan
    ))
    || contact.outreach_lane
    || contact.sequence_status
    || contact.instantly_status
    || contact.warm_intro_path
  );

  if (isSourcedContact) {
    return <AccountSourcingContactDetail />;
  }

  return (
    <>
      <div className="contact-detail-page" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <div className="flex items-center justify-between gap-3">
          <button className="crm-button soft" onClick={() => navigate(-1)}>
            <ArrowLeft size={14} />
            Back
          </button>
        </div>

        <section className="crm-panel p-8" style={{ padding: 32 }}>
          <div className="flex items-start gap-5" style={{ gap: 20 }}>
            <div className={`flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl text-[16px] font-extrabold ${avatarColor(contact.first_name + contact.last_name)}`}>
              {getInitials(`${contact.first_name} ${contact.last_name}`)}
            </div>
            <div className="min-w-0">
              <h2 className="text-[30px] font-extrabold tracking-tight text-[#1f2d3d]">{contact.first_name} {contact.last_name}</h2>
              <p className="text-[14px] text-[#6f8399] mt-1">{contact.title ?? "-"}</p>
              <div
                className="mt-3"
                style={{
                  marginTop: 14,
                  padding: "12px 14px",
                  borderRadius: 14,
                  border: `1px solid ${trackingTone.border}`,
                  background: trackingTone.soft,
                  maxWidth: 760,
                }}
              >
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <span style={{ color: trackingTone.color, fontWeight: 800, fontSize: 13 }}>
                    {getProspectTrackingStage(contact)}
                  </span>
                  <span style={{ color: trackingTone.color, fontWeight: 900, fontSize: 13 }}>
                    {getProspectTrackingScore(contact)}
                  </span>
                </div>
                <div className="text-[13px] text-[#4d6178] mt-1.5" style={{ lineHeight: 1.55 }}>
                  {getProspectTrackingSummary(contact)}
                </div>
              </div>
              <div className="flex items-center gap-3 mt-3 text-[13px] text-[#4d6178] flex-wrap" style={{ marginTop: 14, rowGap: 10, columnGap: 12 }}>
                {company && (
                  <Link to={`/account-sourcing/${company.id}`} className="hover:text-[#9ace3d] font-semibold">
                    {company.name}
                  </Link>
                )}
                {contact.email && (
                  <span className="inline-flex items-center gap-1"><Mail size={13} />{contact.email}</span>
                )}
                {contact.phone && (
                  <button
                    onClick={() => window.__aircallDial?.(contact.phone!, `${contact.first_name} ${contact.last_name}`)}
                    className="inline-flex items-center gap-1 hover:text-[#16a34a] transition-colors cursor-pointer"
                    title={`Call ${contact.phone}`}
                    style={{ background: "none", border: "none", padding: 0, font: "inherit", color: "inherit" }}
                  >
                    <Phone size={13} />{contact.phone}
                  </button>
                )}
                {contact.linkedin_url && (
                  <a href={contact.linkedin_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[#2a5f8c] hover:text-[#9ace3d]">
                    <Linkedin size={13} />LinkedIn
                  </a>
                )}
                <button
                  onClick={() => setLinkedInDialogOpen(true)}
                  className="inline-flex items-center gap-1 hover:text-[#0a66c2] transition-colors cursor-pointer"
                  style={{ background: "none", border: "none", padding: 0, font: "inherit", color: "#4d6178" }}
                  title="Log a LinkedIn touch on this contact"
                >
                  <Linkedin size={13} />Log touch
                </button>
                <span className="inline-flex items-center gap-2">
                  <span className="font-semibold">Time zone:</span>
                  {editingTimezone ? (
                    <>
                      <input
                        value={timezoneDraft}
                        onChange={(e) => setTimezoneDraft(e.target.value)}
                        placeholder="e.g. America/Chicago"
                        style={{ height: 32, borderRadius: 8, border: "1px solid #d5e3ef", padding: "0 10px", fontSize: 12, color: "#24364b" }}
                      />
                      <button className="crm-button soft" onClick={handleSaveTimezone}>Save</button>
                      <button className="crm-button soft" onClick={() => { setTimezoneDraft(contact.timezone ?? ""); setEditingTimezone(false); }}>Cancel</button>
                    </>
                  ) : (
                    <>
                      <span>{contact.timezone || "—"}</span>
                      <button className="crm-button soft" onClick={() => setEditingTimezone(true)}>Edit</button>
                    </>
                  )}
                </span>
              </div>
            </div>
          </div>
        </section>

        <section className="crm-panel p-6" style={{ padding: 26 }}>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <UserCircle2 size={16} className="text-[#9ace3d]" />
              <h3 className="text-[16px] font-bold">AI Brief</h3>
            </div>
            <button className="crm-button soft" onClick={handleGetBrief} disabled={briefLoading}>
              {briefLoading ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
              {briefLoading ? "Generating..." : "Generate Brief"}
            </button>
          </div>
          {brief ? (
            <div className="rounded-xl border border-[#dce6f0] bg-[#f8fbff] p-4 space-y-2">
              {brief.split("\n").filter(Boolean).map((line, i) => (
                <p key={i} className="text-[14px] text-[#2d4258]">{line}</p>
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-[#6f8399]">Generate stakeholder brief from profile and context.</p>
          )}
        </section>

        <section className="crm-panel p-6" style={{ padding: 26 }}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[16px] font-bold">Outreach</h3>
            {sequence ? (
              <button className="crm-button soft" onClick={() => setDrawerOpen(true)}>
                Open Sequence
              </button>
            ) : (
              <button className="crm-button primary" onClick={handleGenerateOutreach} disabled={seqLoading}>
                {seqLoading ? "Generating..." : "Generate Sequence"}
              </button>
            )}
          </div>
          {sequence ? (
            <div className="rounded-xl border border-[#dce6f0] bg-[#f8fbff] p-4 text-[13px] text-[#2d4258] space-y-1">
              <p><span className="font-semibold">Status:</span> {sequence.status}</p>
              <p><span className="font-semibold">Persona:</span> {sequence.persona ?? "-"}</p>
              <p><span className="font-semibold">Subject:</span> {sequence.subject_1 ?? "-"}</p>
            </div>
          ) : (
            <p className="text-[13px] text-[#6f8399]">No outreach sequence generated yet.</p>
          )}
        </section>

        <section className="crm-panel p-6" style={{ padding: 26 }}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[16px] font-bold">Timeline</h3>
            <button
              className="crm-button soft"
              onClick={() => setLinkedInDialogOpen(true)}
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <Linkedin size={13} />Log LinkedIn touch
            </button>
          </div>
          {id && <UnifiedTimeline scope={{ type: "contact", id }} />}
        </section>
      </div>

      <OutreachDrawer contact={drawerOpen ? contact : null} onClose={() => setDrawerOpen(false)} />
      {id && (
        <LogLinkedInDialog
          contactId={id}
          open={linkedInDialogOpen}
          onClose={() => setLinkedInDialogOpen(false)}
          onLogged={() => void loadContact()}
        />
      )}
    </>
  );
}

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, BriefcaseBusiness, Loader2, X } from "lucide-react";
import { dealsApi } from "../../lib/api";
import { getCachedUsers } from "../../lib/cachedFetch";
import { formatCurrencyAmount } from "../../lib/currencies";
import { formatDateOnly } from "../../lib/utils";
import type { Company, Deal, DealStageSetting, User } from "../../types";
import "./account-opportunities.css";

const DealDetailDrawer = lazy(() => import("./DealDetailDrawer"));

interface Props {
  company: Company;
  stages: DealStageSetting[];
  onClose: () => void;
}

export default function AccountOpportunitiesModal({ company, stages, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const requestRef = useRef(0);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [selectedDeal, setSelectedDeal] = useState<Deal | null>(null);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    setLoading(true);
    setError("");
    try {
      // Read every page for this account, including closed opportunities.
      const [rows, team] = await Promise.all([dealsApi.listAll(company.id), getCachedUsers()]);
      if (requestRef.current === requestId) {
        setDeals(rows);
        setUsers(team);
      }
    } catch (err) {
      if (requestRef.current === requestId) {
        setError(err instanceof Error ? err.message : "Could not load opportunities. Please try again.");
      }
    } finally {
      if (requestRef.current === requestId) setLoading(false);
    }
  }, [company.id]);

  useEffect(() => {
    void load();
    return () => { requestRef.current++; };
  }, [load]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!selectedDeal) dialog?.showModal();
    return () => dialog?.close();
  }, [selectedDeal?.id]);

  const openDeal = async (dealId: string) => {
    const requestId = ++requestRef.current;
    setOpeningId(dealId);
    setError("");
    try {
      const [deal, team] = await Promise.all([dealsApi.get(dealId), getCachedUsers()]);
      if (requestRef.current !== requestId) return;
      setUsers(team);
      setSelectedDeal(deal);
    } catch (err) {
      if (requestRef.current === requestId) {
        setError(err instanceof Error ? err.message : "Could not open this opportunity. Please try again.");
      }
    } finally {
      if (requestRef.current === requestId) setOpeningId(null);
    }
  };

  // The paginated list includes assignment IDs but does not join user names.
  const assigneeName = (id?: string | null, name?: string | null) =>
    name || users.find((user) => user.id === id)?.name || (id ? "Assigned user unavailable" : "Unassigned");

  if (selectedDeal) {
    return (
      <div className="account-opportunities-detail">
        <Suspense fallback={<div className="account-opportunities-loading" role="status">Loading deal details…</div>}>
          <DealDetailDrawer
            key={selectedDeal.id}
            deal={selectedDeal}
            companies={[company]}
            users={users}
            stages={stages}
            onClose={() => setSelectedDeal(null)}
            onDealUpdated={(updated) => {
              setSelectedDeal(updated);
              setDeals((rows) => updated.company_id === company.id
                ? rows.map((row) => row.id === updated.id ? updated : row)
                : rows.filter((row) => row.id !== updated.id));
            }}
            onDealDeleted={(dealId) => {
              setDeals((rows) => rows.filter((row) => row.id !== dealId));
              setSelectedDeal(null);
            }}
          />
        </Suspense>
      </div>
    );
  }

  return (
    <dialog
      ref={dialogRef}
      className="account-opportunities-dialog"
      aria-labelledby="account-opportunities-title"
      onCancel={onClose}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div className="account-opportunities-header">
        <div>
          <h2 id="account-opportunities-title"><BriefcaseBusiness size={20} /> Opportunities{!loading && !error ? ` (${deals.length})` : ""}</h2>
          <p>{company.name}</p>
        </div>
        <button type="button" className="account-opportunities-close" aria-label="Close opportunities" onClick={onClose}><X size={20} /></button>
      </div>
      <div className="account-opportunities-body">
        {error && (
          <div role="alert" className="account-opportunities-error">
            <p>{error}</p>
            <button type="button" className="account-opportunity-open" disabled={loading || openingId !== null} onClick={() => { void load(); }}>Try again</button>
          </div>
        )}
        {loading ? (
          <div role="status" className="account-opportunities-empty"><Loader2 size={20} className="animate-spin" /> Loading opportunities…</div>
        ) : !error && deals.length === 0 ? (
          <div className="account-opportunities-empty"><BriefcaseBusiness size={28} /><strong>No opportunities yet</strong><span>Deals linked to this account will appear here. Use Add to Deal on the account page to create one.</span></div>
        ) : (
          <div className="account-opportunities-list">
            {deals.map((deal) => (
              <article key={deal.id} className="account-opportunity-card">
                <div className="account-opportunity-heading">
                  <h3>{deal.name}</h3>
                  <span className="account-opportunity-stage">{stages.find((stage) => stage.id === deal.stage)?.label ?? deal.stage.replace(/_/g, " ")}</span>
                </div>
                <dl className="account-opportunity-fields">
                  <div><dt>Value</dt><dd>{formatCurrencyAmount(deal.value, deal.currency_code)}</dd></div>
                  <div><dt>Owner</dt><dd>{assigneeName(deal.assigned_to_id, deal.assigned_rep_name)}</dd></div>
                  <div><dt>SDR</dt><dd>{assigneeName(deal.sdr_id, deal.sdr_name)}</dd></div>
                  <div><dt>Close date</dt><dd>{formatDateOnly(deal.close_date || deal.close_date_est)}</dd></div>
                </dl>
                {deal.next_step && <p className="account-opportunity-next-step"><strong>Next step:</strong> {deal.next_step}</p>}
                <button type="button" className="account-opportunity-open" disabled={openingId !== null} onClick={() => { void openDeal(deal.id); }}>
                  {openingId === deal.id ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
                  {openingId === deal.id ? "Opening…" : "View full deal"}
                  <span className="sr-only">: {deal.name}</span>
                </button>
              </article>
            ))}
          </div>
        )}
      </div>
    </dialog>
  );
}

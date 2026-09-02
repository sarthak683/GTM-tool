import type { Company, Deal } from "../../types";
import { formatCurrencyAmount } from "../../lib/currencies";
import { formatDate, formatDateOnly } from "../../lib/utils";

export default function PipelineTableView({
  records,
  stageLabels,
  companies,
  selectedIds,
  onToggleSelect,
  onOpen,
}: {
  records: Deal[];
  stageLabels: Map<string, string>;
  companies: Map<string, Company>;
  selectedIds: Set<string>;
  onToggleSelect: (dealId: string) => void;
  onOpen: (deal: Deal) => void;
}) {
  return (
    <div className="desktop-only crm-panel overflow-hidden" style={{ margin: 16, minHeight: 0, overflow: "auto" }}>
      <table className="crm-table" style={{ minWidth: 1120 }}>
        <thead>
          <tr>
            <th style={{ width: 44 }} aria-label="Selection" />
            <th>Deal</th>
            <th>Account</th>
            <th>Stage</th>
            <th>Value</th>
            <th>Owner</th>
            <th>Health</th>
            <th>Close date</th>
            <th>Next step</th>
            <th>Last activity</th>
          </tr>
        </thead>
        <tbody>
          {records.map((deal) => {
            const company = deal.company_id ? companies.get(deal.company_id) : undefined;
            const lastActivity = deal.last_activity_at || deal.seller_engagement_at || deal.client_engagement_at;
            return (
              <tr key={deal.id} className="cursor-pointer" onClick={() => onOpen(deal)}>
                <td onClick={(event) => event.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(deal.id)}
                    onChange={() => onToggleSelect(deal.id)}
                    aria-label={`Select ${deal.name}`}
                    style={{ width: 16, height: 16, accentColor: "#175089" }}
                  />
                </td>
                <td>
                  <div style={{ fontSize: 13, fontWeight: 800, color: "#152a40" }}>{deal.name}</div>
                  <div style={{ marginTop: 3, fontSize: 10.5, fontWeight: 750, color: "#7a8ea4" }}>{deal.priority_tag || "No priority"}</div>
                </td>
                <td>{deal.company_name || company?.name || "No account"}</td>
                <td><span style={{ padding: "4px 8px", borderRadius: 999, background: "#eef4ff", color: "#3555c4", fontSize: 10.5, fontWeight: 800 }}>{stageLabels.get(deal.stage) || deal.stage.replace(/_/g, " ")}</span></td>
                <td style={{ fontWeight: 800, color: "#24405d" }}>{formatCurrencyAmount(deal.value, deal.currency_code)}</td>
                <td>{deal.assigned_rep_name || "Unassigned"}</td>
                <td>{deal.health ? `${deal.health}${deal.health_score != null ? ` · ${deal.health_score}` : ""}` : "—"}</td>
                <td>{deal.close_date_est ? formatDateOnly(deal.close_date_est) : "—"}</td>
                <td style={{ maxWidth: 260 }}><span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{deal.next_step || "No next step"}</span></td>
                <td>{lastActivity ? formatDate(lastActivity) : "—"}</td>
              </tr>
            );
          })}
          {!records.length && (
            <tr><td colSpan={10} style={{ padding: 48, textAlign: "center", color: "#7a8ea4" }}>No deals match the current filters.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

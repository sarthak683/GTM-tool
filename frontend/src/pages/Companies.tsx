import { useEffect, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { companiesApi, prospectingApi, type ProspectingBatch } from "../lib/api";
import { useAuth } from "../lib/AuthContext";

async function confirmDelete(name: string, onDelete: () => Promise<void>, onRemove: () => void) {
  if (!window.confirm(`Delete "${name}"? This cannot be undone.`)) return;
  await onDelete();
  onRemove();
}
import type { Company } from "../types";
import { Search, SlidersHorizontal, Building2, ArrowUpRight, Upload, FileSpreadsheet, X, Trash2 } from "lucide-react";
import { avatarColor, getInitials } from "../lib/utils";

const TIER_STYLE: Record<string, CSSProperties> = {
  hot: { color: "#8f2f11", background: "#ffe4d9", border: "1px solid #ffc5b3" },
  warm: { color: "#86581a", background: "#fff3dd", border: "1px solid #f7dda4" },
  monitor: { color: "#265179", background: "#eaf4ff", border: "1px solid #c7def8" },
  cold: { color: "#4f6073", background: "#eef3f8", border: "1px solid #d5e0ea" },
};

export default function Companies() {
  const { isAdmin, user } = useAuth();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [search, setSearch] = useState("");
  const [sortByScore, setSortByScore] = useState(true);
  // "Mine" toggle: client-side filter to companies where the current user
  // is either the AE (assigned_to_id) or SDR (sdr_id). Off by default so
  // the page still serves as a workspace-wide directory.
  const [mineOnly, setMineOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showImporter, setShowImporter] = useState(false);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [previewHeaders, setPreviewHeaders] = useState<string[]>([]);
  const [previewRows, setPreviewRows] = useState<Record<string, string>[]>([]);
  const [previewTotalRows, setPreviewTotalRows] = useState(0);
  const [previewValidRows, setPreviewValidRows] = useState(0);
  const [hasValidColumn, setHasValidColumn] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [batch, setBatch] = useState<ProspectingBatch | null>(null);
  const [polling, setPolling] = useState(false);

  useEffect(() => {
    companiesApi.list().then((cs) => setCompanies(cs)).catch(() => setCompanies([])).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!polling || !batch?.batch_id) return;

    const timer = window.setInterval(async () => {
      try {
        const latest = await prospectingApi.status(batch.batch_id);
        setBatch(latest);
        if ((latest.completed_enrichments ?? 0) >= latest.created) {
          setPolling(false);
        }
      } catch {
        setPolling(false);
      }
    }, 3000);

    return () => window.clearInterval(timer);
  }, [polling, batch?.batch_id, batch?.created]);

  const parseCsvLine = (line: string): string[] => {
    const fields: string[] = [];
    let current = "";
    let inQuotes = false;

    for (let i = 0; i < line.length; i++) {
      const char = line[i];
      const next = line[i + 1];

      if (char === '"') {
        if (inQuotes && next === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
        continue;
      }

      if (char === "," && !inQuotes) {
        fields.push(current.trim());
        current = "";
        continue;
      }

      current += char;
    }

    fields.push(current.trim());
    return fields;
  };

  const buildPreview = async (file: File) => {
    const text = await file.text();
    const lines = text
      .replace(/\r\n/g, "\n")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

    if (lines.length === 0) {
      setPreviewHeaders([]);
      setPreviewRows([]);
      setPreviewTotalRows(0);
      setPreviewValidRows(0);
      setHasValidColumn(false);
      return;
    }

    const headers = parseCsvLine(lines[0]).map((h) => h.trim());
    const normalized = headers.map((h) => h.toLowerCase());

    // Mirror the backend _ALIASES logic — accept any recognised name or domain column
    const NAME_ALIASES = ["name", "company name", "company", "organization"];
    const DOMAIN_ALIASES = ["domain", "domain name", "website", "url", "web"];

    const nameHeader = headers.find((_, i) => NAME_ALIASES.includes(normalized[i]));
    const domainHeader = headers.find((_, i) => DOMAIN_ALIASES.includes(normalized[i]));
    const hasValid = !!(nameHeader || domainHeader);

    const dataLines = lines.slice(1);
    const rows = dataLines.map((line) => {
      const values = parseCsvLine(line);
      const row: Record<string, string> = {};
      headers.forEach((h, idx) => {
        row[h] = values[idx] ?? "";
      });
      return row;
    });

    // A row is valid if it has a non-empty name OR domain value
    const validRows = rows.filter((row) => {
      const hasName = nameHeader ? (row[nameHeader] ?? "").trim().length > 0 : false;
      const hasDomain = domainHeader ? (row[domainHeader] ?? "").trim().length > 0 : false;
      return hasName || hasDomain;
    }).length;

    setPreviewHeaders(headers);
    setPreviewRows(rows.slice(0, 8));
    setPreviewTotalRows(rows.length);
    setPreviewValidRows(validRows);
    setHasValidColumn(hasValid);
  };

  const handleFileChange = async (file: File | null) => {
    setImportError("");
    setBatch(null);
    setPolling(false);
    setCsvFile(file);

    if (!file) {
      setPreviewHeaders([]);
      setPreviewRows([]);
      setPreviewTotalRows(0);
      setPreviewValidRows(0);
      setHasValidColumn(false);
      return;
    }

    try {
      await buildPreview(file);
    } catch {
      setImportError("Failed to parse CSV preview. Please check file format.");
      setPreviewHeaders([]);
      setPreviewRows([]);
      setPreviewTotalRows(0);
      setPreviewValidRows(0);
      setHasValidColumn(false);
    }
  };

  const handleTriggerBulk = async () => {
    if (!csvFile) {
      setImportError("Select a CSV file first.");
      return;
    }
    if (!hasValidColumn) {
      setImportError("CSV must include at least a company name or domain column.");
      return;
    }

    setImportError("");
    setImporting(true);
    try {
      const result = await prospectingApi.bulkUpload(csvFile);
      setBatch(result);
      setPolling(result.created > 0);
      const refreshed = await companiesApi.list();
      setCompanies(refreshed);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "Bulk upload failed.");
    } finally {
      setImporting(false);
    }
  };


  const filtered = companies
    .filter((c) => c.name.toLowerCase().includes(search.toLowerCase()) || c.domain.toLowerCase().includes(search.toLowerCase()))
    .filter((c) => !mineOnly || (user?.id ? (c.assigned_to_id === user.id || c.sdr_id === user.id) : true))
    .sort((a, b) => sortByScore ? (b.icp_score ?? 0) - (a.icp_score ?? 0) : 0);

  return (
    <div className="crm-page companies-page space-y-6">
      <div className="crm-panel px-8 py-6 crm-toolbar companies-toolbar">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="crm-chip">
              <span className="font-bold tabular">{companies.length}</span>
              Accounts
            </span>
            <span className="crm-chip">Sorted by ICP fit</span>
          </div>
          <p className="text-[12px] text-[#7a96b0] mt-1">
            Each account is scored and ranked by ICP fit. Click an account to view enrichment, deals, and prospects. Use Bulk Import to add accounts from a CSV.
          </p>
        </div>
        <div className="crm-toolbar-actions">
          <button
            className="h-12 flex items-center gap-2 px-4 rounded-xl border border-[#d7e2ee] bg-white text-[13px] font-semibold text-[#4d6178] hover:bg-[#f7fafe]"
            onClick={() => setShowImporter((v) => !v)}
          >
            {showImporter ? <X className="h-3.5 w-3.5" /> : <Upload className="h-3.5 w-3.5" />}
            {showImporter ? "Close Import" : "Bulk Import CSV"}
          </button>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#8194a8] pointer-events-none" />
            <input
              className="h-12 w-72 max-w-[78vw] rounded-xl border border-[#d7e2ee] bg-white pl-10 pr-4 text-[14px] text-[#223145] placeholder-[#92a4b8] outline-none focus:border-[#c2d3e5]"
              placeholder="Search account or domain"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button
            onClick={() => setSortByScore((s) => !s)}
            className={`h-12 flex items-center gap-2 px-4 rounded-xl border text-[13px] font-semibold transition-all ${
              sortByScore
                ? "bg-[#fff1ec] border-[#ffcbb8] text-[#b94a24]"
                : "bg-white border-[#d7e2ee] text-[#4d6178] hover:bg-[#f7fafe]"
            }`}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            Rank by ICP
          </button>
          {user?.id && (
            <button
              onClick={() => setMineOnly((m) => !m)}
              title={mineOnly ? "Showing only your accounts — click to clear" : "Show only accounts you own (AE or SDR)"}
              className={`h-12 flex items-center gap-2 px-4 rounded-xl border text-[13px] font-semibold transition-all ${
                mineOnly
                  ? "bg-[#fff1ec] border-[#ffcbb8] text-[#b94a24]"
                  : "bg-white border-[#d7e2ee] text-[#4d6178] hover:bg-[#f7fafe]"
              }`}
            >
              {mineOnly ? "Mine ✓" : "Mine"}
            </button>
          )}
        </div>
      </div>

      {showImporter && (
        <div className="crm-panel companies-importer-panel">
          <div className="companies-importer-head">
            <div className="flex items-center gap-2">
              <FileSpreadsheet className="h-4 w-4 text-[#9ace3d]" />
              <h3 className="text-[15px] font-bold text-[#2b3f55]">Bulk Prospecting Import</h3>
            </div>
            <p className="text-[12px] text-[#6f8399] mt-1">Upload a CSV with a <strong>company name</strong> or <strong>domain</strong> column. Beacon will enrich each row with ICP score, industry, employee count, and funding stage — then kick off prospecting in the background.</p>
          </div>

          <div className="companies-importer-controls">
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
              className="block w-full text-[13px] text-[#4d6178] file:mr-4 file:rounded-lg file:border-0 file:bg-[#fff1ec] file:px-3 file:py-2 file:text-[12px] file:font-semibold file:text-[#b94a24] hover:file:bg-[#ffe8de]"
            />
            <button
              className="crm-button primary"
              onClick={handleTriggerBulk}
              disabled={importing || !csvFile || !hasValidColumn || previewValidRows === 0}
            >
              {importing ? "Triggering..." : "Trigger Bulk API"}
            </button>
          </div>

          {csvFile && (
            <div className="companies-importer-meta">
              <span className="crm-chip">{csvFile.name}</span>
              <span className="crm-chip">Rows: {previewTotalRows}</span>
              <span className="crm-chip">Valid rows: {previewValidRows}</span>
              {!hasValidColumn && <span className="text-[12px] font-semibold text-[#b94a24]">CSV needs a company name or domain column</span>}
            </div>
          )}

          {previewRows.length > 0 && (
            <div className="companies-importer-preview crm-panel overflow-hidden">
              <div className="overflow-x-auto">
                <table className="crm-table" style={{ minWidth: 860 }}>
                  <thead>
                    <tr>
                      {previewHeaders.map((h) => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {previewRows.map((row, idx) => (
                      <tr key={idx}>
                        {previewHeaders.map((h) => (
                          <td key={`${idx}-${h}`}>{row[h] || <span className="text-[#96a7ba]">-</span>}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {batch && (
            <div className="companies-importer-result">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="companies-importer-stat"><span>Total</span><strong>{batch.total}</strong></div>
                <div className="companies-importer-stat"><span>Created</span><strong>{batch.created}</strong></div>
                <div className="companies-importer-stat"><span>Skipped</span><strong>{batch.skipped}</strong></div>
                <div className="companies-importer-stat"><span>Failed</span><strong>{batch.failed}</strong></div>
              </div>
              <div className="mt-3 text-[12px] text-[#5e738b]">
                Batch: {batch.batch_id}
                {batch.created > 0 && (
                  <span className="ml-2 font-semibold text-[#2f455d]">
                    Enrichment: {batch.completed_enrichments ?? 0}/{batch.created}
                    {polling ? " (running)" : " (completed)"}
                  </span>
                )}
              </div>
              {(batch.skipped_names?.length ?? 0) > 0 && (
                <div className="mt-3 rounded-xl border border-[#f0d9a8] bg-[#fffbf0] px-4 py-3">
                  <p className="text-[12px] font-bold text-[#8a6a1a] mb-1">
                    Skipped {batch.skipped_names!.length} duplicate{batch.skipped_names!.length > 1 ? "s" : ""}
                  </p>
                  <ul className="space-y-0.5">
                    {batch.skipped_names!.map((name, i) => (
                      <li key={i} className="text-[12px] text-[#7a5e1a]">· {name}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {importError && <p className="text-[12px] font-semibold text-[#b94a24]">{importError}</p>}
        </div>
      )}

      {loading ? (
        <div className="crm-panel p-14 text-center crm-muted">Loading accounts...</div>
      ) : filtered.length === 0 ? (
        <div className="crm-panel p-14 text-center text-[#6f8297]">
          <Building2 className="h-12 w-12 mx-auto mb-4 opacity-30" />
          <div className="font-bold text-[#25384d] text-[14px] mb-2">No accounts found</div>
          <div className="text-[12px] text-[#7a96b0] max-w-sm mx-auto">
            {companies.length === 0
              ? "Add your first account using Bulk Import CSV above, or have Beacon enrich a company from the Pipeline or Account Sourcing pages."
              : "Try a different search term or clear your filters."}
          </div>
        </div>
      ) : (
        <div className="crm-panel overflow-hidden companies-table-panel">
          <div className="overflow-x-auto">
            <table className="crm-table" style={{ minWidth: 1020 }}>
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Industry</th>
                  <th>Employees</th>
                  <th>Funding</th>
                  <th>ICP Fit</th>
                  <th>Tier</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => (
                  <tr key={c.id}>
                    <td data-label="Company">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-[13px] font-extrabold ${avatarColor(c.name)}`}>
                          {getInitials(c.name)}
                        </div>
                        <div className="min-w-0">
                          <Link to={`/account-sourcing/${c.id}`} className="font-bold text-[#24364b] hover:text-[#9ace3d] transition-colors">
                            {c.name}
                          </Link>
                          <p className="text-[13px] text-[#7a8ea4] truncate mt-0.5">{c.domain}</p>
                        </div>
                      </div>
                    </td>
                    <td data-label="Industry">{c.industry ?? <span className="text-[#96a7ba]">-</span>}</td>
                    <td data-label="Employees" className="tabular">{c.employee_count?.toLocaleString() ?? <span className="text-[#96a7ba]">-</span>}</td>
                    <td data-label="Funding">{c.funding_stage ?? <span className="text-[#96a7ba]">-</span>}</td>
                    <td data-label="ICP Fit">
                      {c.icp_score != null ? (
                        <div className="flex items-center gap-2.5">
                          <div className="h-2 w-28 bg-[#edf3fa] rounded-full overflow-hidden">
                            <div className="h-2 bg-[#9ace3d] rounded-full" style={{ width: `${c.icp_score}%` }} />
                          </div>
                          <span className="font-bold tabular text-[12px] text-[#2d4056]">{c.icp_score}</span>
                        </div>
                      ) : (
                        <span className="text-[#96a7ba]">-</span>
                      )}
                    </td>
                    <td data-label="Tier">
                      {c.icp_tier ? (
                        <div className="flex items-center justify-between gap-2">
                          <span className="inline-flex items-center px-2 py-1 rounded-full text-[11px] font-bold capitalize" style={TIER_STYLE[c.icp_tier] ?? TIER_STYLE.cold}>
                            {c.icp_tier}
                          </span>
                          <ArrowUpRight className="h-3.5 w-3.5 text-[#90a4b8]" />
                        </div>
                      ) : (
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[#96a7ba]">-</span>
                          <ArrowUpRight className="h-3.5 w-3.5 text-[#90a4b8]" />
                        </div>
                      )}
                    </td>
                    <td data-label="">
                      {isAdmin ? (
                        <button
                          onClick={(e) => {
                            e.preventDefault();
                            confirmDelete(c.name, () => companiesApi.delete(c.id), () =>
                              setCompanies((prev) => prev.filter((x) => x.id !== c.id))
                            );
                          }}
                          className="flex items-center justify-center h-8 w-8 rounded-lg text-[#9eb0c3] hover:text-[#c0392b] hover:bg-[#fff0f0] transition-colors"
                          title="Delete company"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { AlertTriangle, FileSpreadsheet, Loader2, RefreshCw, Upload } from "lucide-react";
import { companiesApi, prospectingApi, type ProspectingBatch } from "../lib/api";

export default function ImportPage() {
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
  const [dupNames, setDupNames] = useState<Set<string>>(new Set());
  const [dupDomains, setDupDomains] = useState<Set<string>>(new Set());
  const [checkingDups, setCheckingDups] = useState(false);
  const [previewing, setPreviewing] = useState(false);

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

    // Check existing DB records for duplicates — single POST, two indexed queries
    if (hasValid) {
      setCheckingDups(true);
      setDupNames(new Set());
      setDupDomains(new Set());
      try {
        const names = nameHeader
          ? rows.map((r) => (r[nameHeader] ?? "").trim()).filter(Boolean)
          : [];
        const domains = domainHeader
          ? rows.map((r) => (r[domainHeader] ?? "").trim()).filter(Boolean)
          : [];
        if (names.length || domains.length) {
          const result = await companiesApi.checkDuplicates(names, domains);
          setDupNames(new Set(result.duplicate_names));
          setDupDomains(new Set(result.duplicate_domains));
        }
      } catch {
        // Non-fatal — preview still shows without dup markers
      } finally {
        setCheckingDups(false);
      }
    }
  };

  // Column header lookups (stored for use in table render)
  const NAME_ALIASES_CONST = ["name", "company name", "company", "organization"];
  const DOMAIN_ALIASES_CONST = ["domain", "domain name", "website", "url", "web"];
  const nameCol = previewHeaders.find((h) => NAME_ALIASES_CONST.includes(h.toLowerCase()));
  const domainCol = previewHeaders.find((h) => DOMAIN_ALIASES_CONST.includes(h.toLowerCase()));

  const handleFileChange = async (file: File | null) => {
    setImportError("");
    setBatch(null);
    setPolling(false);
    setCsvFile(file);
    setDupNames(new Set());
    setDupDomains(new Set());

    if (!file) {
      setPreviewHeaders([]);
      setPreviewRows([]);
      setPreviewTotalRows(0);
      setPreviewValidRows(0);
      setHasValidColumn(false);
      return;
    }

    setPreviewing(true);
    try {
      await buildPreview(file);
    } catch {
      setImportError("Failed to parse CSV preview. Please check file format.");
      setPreviewHeaders([]);
      setPreviewRows([]);
      setPreviewTotalRows(0);
      setPreviewValidRows(0);
      setHasValidColumn(false);
    } finally {
      setPreviewing(false);
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
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "Bulk upload failed.");
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="import-page" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <section className="crm-panel p-8 import-main-card" style={{ padding: 32 }}>
        <div className="flex items-start gap-4" style={{ gap: 18 }}>
          <div className="h-12 w-12 rounded-xl bg-[#fff1ec] border border-[#ffd6c8] grid place-items-center text-[#ff6b35]">
            <Upload size={18} />
          </div>
          <div className="min-w-0">
            <h2 className="text-[20px] font-bold text-[#213246]">Bulk Account Import</h2>
            <p className="text-[14px] text-[#647a91] mt-2">
              Upload CSV, validate rows, and run bulk prospecting + enrichment directly from this page.
            </p>
            <div className="mt-5 flex items-center gap-3 flex-wrap" style={{ marginTop: 20, rowGap: 10, columnGap: 12 }}>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
                className="block w-full max-w-130 text-[13px] text-[#4d6178] file:mr-4 file:rounded-lg file:border-0 file:bg-[#fff1ec] file:px-3 file:py-2 file:text-[12px] file:font-semibold file:text-[#b94a24] hover:file:bg-[#ffe8de]"
              />
              <button
                className="crm-button primary"
                onClick={handleTriggerBulk}
                disabled={importing || !csvFile || !hasValidColumn || previewValidRows === 0}
              >
                {importing ? (
                  <>
                    <RefreshCw size={14} className="animate-spin" />
                    Triggering...
                  </>
                ) : (
                  <>
                    <FileSpreadsheet size={14} />
                    Trigger Bulk API
                  </>
                )}
              </button>
              <span className="text-[12px] text-[#7a8ea4]">Required CSV column: name or domain</span>
            </div>

            {csvFile && (
              <div className="mt-4 flex items-center gap-2.5 flex-wrap">
                <span className="crm-chip">{csvFile.name}</span>
                <span className="crm-chip">Rows: {previewTotalRows}</span>
                <span className="crm-chip">Valid rows: {previewValidRows}</span>
                {!hasValidColumn && (
                  <span className="text-[12px] font-semibold text-[#b94a24]">CSV needs a company name or domain column</span>
                )}
              </div>
            )}
          </div>
        </div>
      </section>

      {previewing && (
        <section className="crm-panel" style={{ padding: 24, display: "flex", alignItems: "center", gap: 12, color: "#647a91" }}>
          <Loader2 size={18} className="animate-spin" />
          <span style={{ fontSize: 14, fontWeight: 600 }}>Uploading and previewing...</span>
        </section>
      )}

      {previewRows.length > 0 && (
        <section className="crm-panel overflow-hidden" style={{ padding: 0 }}>
          <div className="px-6 py-4 border-b border-[#e3eaf3] bg-[#f9fbfe] flex items-center justify-between gap-3">
            <h3 className="text-[14px] font-bold text-[#2b3f55]">CSV Preview (first 8 rows)</h3>
            <div className="flex items-center gap-2">
              {checkingDups && (
                <span className="text-[12px] text-[#7a8ea4] flex items-center gap-1">
                  <RefreshCw size={11} className="animate-spin" /> Checking duplicates…
                </span>
              )}
              {!checkingDups && (dupNames.size > 0 || dupDomains.size > 0) && (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-[#fffbf0] border border-[#f0d9a8] text-[#8a6a1a]">
                  <AlertTriangle size={11} />
                  {dupNames.size + dupDomains.size} duplicate{dupNames.size + dupDomains.size > 1 ? "s" : ""} detected
                </span>
              )}
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="crm-table" style={{ minWidth: 860 }}>
              <thead>
                <tr>
                  <th style={{ width: 28 }} />
                  {previewHeaders.map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {previewRows.map((row, idx) => {
                  const rowName = nameCol ? (row[nameCol] ?? "").trim().toLowerCase() : "";
                  const rowDomain = domainCol ? (row[domainCol] ?? "").trim().toLowerCase() : "";
                  const isDup = dupNames.has(rowName) || dupDomains.has(rowDomain);
                  return (
                    <tr key={idx} style={isDup ? { background: "#fffbf0" } : undefined}>
                      <td style={{ width: 28, paddingRight: 0 }} data-label="">
                        {isDup && <AlertTriangle size={13} style={{ color: "#c48a1a" }} />}
                      </td>
                      {previewHeaders.map((h) => (
                        <td key={`${idx}-${h}`} data-label={h} style={isDup ? { color: "#8a6a1a" } : undefined}>
                          {row[h] || <span className="text-[#96a7ba]">-</span>}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {batch && (
        <section className="crm-panel" style={{ padding: 24 }}>
          <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
            <h3 className="text-[15px] font-bold text-[#2b3f55]">Bulk Run Result</h3>
            <span className="text-[12px] text-[#5e738b]">Batch: {batch.batch_id}</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="companies-importer-stat"><span>Total</span><strong>{batch.total}</strong></div>
            <div className="companies-importer-stat"><span>Created</span><strong>{batch.created}</strong></div>
            <div className="companies-importer-stat"><span>Skipped</span><strong>{batch.skipped}</strong></div>
            <div className="companies-importer-stat"><span>Failed</span><strong>{batch.failed}</strong></div>
          </div>
          {batch.created > 0 && (
            <p className="mt-3 text-[12px] font-semibold text-[#2f455d]">
              Enrichment: {batch.completed_enrichments ?? 0}/{batch.created}
              {polling ? " (running)" : " (completed)"}
            </p>
          )}
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
          {(batch.failed_rows?.length ?? 0) > 0 && (
            <div className="mt-3 rounded-xl border border-[#fcc] bg-[#fff5f5] px-4 py-3">
              <p className="text-[12px] font-bold text-[#9a2a1a] mb-1">
                Failed {batch.failed_rows!.length} row{batch.failed_rows!.length > 1 ? "s" : ""}
              </p>
              <ul className="space-y-0.5">
                {batch.failed_rows!.map((row, i) => (
                  <li key={i} className="text-[12px] text-[#8a2a1a]">· {row.name ?? row.domain ?? "unknown"}: {row.error}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-3 text-[12px] text-[#6f8399]">
            Imported companies are saved and visible in the Companies page.
          </p>
        </section>
      )}

      {importError && (
        <p className="text-[12px] font-semibold text-[#b94a24]">{importError}</p>
      )}
    </div>
  );
}

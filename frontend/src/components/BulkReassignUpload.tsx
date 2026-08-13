import { useCallback, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Upload, X } from "lucide-react";

import { assignmentsApi } from "../lib/api/workspace";
import { cardStyle, colors } from "../pages/accountSourcingShared";
import type { AssignmentUploadResult, AssignmentUploadRow } from "../types";

/**
 * Bulk AE/SDR reassignment from a spreadsheet.
 *
 * Reassigning hundreds of accounts used to mean running a script against prod.
 * The flow here is deliberately two-step: the same file is uploaded once for a
 * preview and again to apply, so nothing is written until an admin has seen the
 * exact per-row outcome, and the applied plan is always re-derived server-side.
 */

const STATUS_META: Record<
  AssignmentUploadRow["status"],
  { label: string; bg: string; fg: string }
> = {
  ok: { label: "Will change", bg: "#eafaf1", fg: "#1c7a4f" },
  no_change: { label: "No change", bg: "#f1f4f9", fg: "#5b6b7f" },
  not_found: { label: "Not found", bg: "#fdf1e3", fg: "#b4690e" },
  ambiguous: { label: "Ambiguous", bg: "#fdf1e3", fg: "#b4690e" },
  unknown_rep: { label: "Unknown rep", bg: "#fdecec", fg: "#b42318" },
  no_identifier: { label: "No account cell", bg: "#fdecec", fg: "#b42318" },
};

const UNKNOWN_STATUS = { label: "Unknown", bg: "#f1f4f9", fg: "#5b6b7f" };

function StatusPill({ status }: { status: AssignmentUploadRow["status"] }) {
  // Fall back rather than throw: an unrecognised status must never blank the
  // whole page through the error boundary, which is exactly what happened when
  // a summary key was passed in here by mistake.
  const meta = STATUS_META[status] ?? UNKNOWN_STATUS;
  return (
    <span
      style={{
        background: meta.bg,
        color: meta.fg,
        borderRadius: 999,
        padding: "2px 9px",
        fontSize: 11,
        fontWeight: 800,
        whiteSpace: "nowrap",
      }}
    >
      {meta.label}
    </span>
  );
}

function Arrow({ from, to, changes }: { from: string | null; to: string | null; changes: boolean }) {
  if (!changes) return <span style={{ color: colors.sub }}>{from || "—"}</span>;
  return (
    <span style={{ color: colors.text }}>
      <span style={{ color: colors.sub, textDecoration: "line-through" }}>{from || "Unassigned"}</span>
      {" → "}
      <strong>{to || "Unassigned"}</strong>
    </span>
  );
}

export default function BulkReassignUpload({ onApplied }: { onApplied: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<AssignmentUploadResult | null>(null);
  const [applied, setApplied] = useState<AssignmentUploadResult | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    setFile(null);
    setPreview(null);
    setApplied(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  }, []);

  const runPreview = useCallback(async (selected: File) => {
    setBusy("preview");
    setError(null);
    setApplied(null);
    try {
      setPreview(await assignmentsApi.bulkAssignUpload(selected, true));
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : "Could not read that file");
    } finally {
      setBusy(null);
    }
  }, []);

  const runApply = useCallback(async () => {
    if (!file) return;
    setBusy("apply");
    setError(null);
    try {
      const result = await assignmentsApi.bulkAssignUpload(file, false);
      setApplied(result);
      setPreview(null);
      onApplied();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reassignment failed");
    } finally {
      setBusy(null);
    }
  }, [file, onApplied]);

  const summary = preview?.summary;
  const rows = preview?.rows ?? [];
  const problems = summary
    ? summary.not_found + summary.ambiguous + summary.unknown_rep + summary.no_identifier
    : 0;

  return (
    <div style={{ ...cardStyle, padding: "16px 18px", display: "grid", gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ color: colors.text, fontWeight: 800, fontSize: 15 }}>Bulk reassign accounts</div>
          <div style={{ color: colors.sub, fontSize: 12.5, maxWidth: 640 }}>
            Upload a CSV or Excel with an <strong>account</strong> or <strong>domain</strong> column plus{" "}
            <strong>ae</strong> and/or <strong>sdr</strong>. A blank cell leaves that owner unchanged — write{" "}
            <strong>unassign</strong> to clear one. Nothing is written until you review the preview.
          </div>
        </div>
        {(preview || applied) && (
          <button
            type="button"
            onClick={reset}
            style={{
              height: 30, border: `1px solid ${colors.border}`, background: colors.card,
              color: colors.sub, borderRadius: 9, padding: "0 10px", fontSize: 12,
              fontWeight: 700, cursor: "pointer", display: "flex", alignItems: "center", gap: 5,
            }}
          >
            <X size={13} /> Start over
          </button>
        )}
      </div>

      {!preview && !applied && (
        <label
          style={{
            border: `1px dashed ${colors.border}`, borderRadius: 12, padding: "18px 16px",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 9,
            cursor: busy ? "wait" : "pointer", color: colors.sub, fontSize: 13, fontWeight: 700,
          }}
        >
          {busy === "preview" ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
          {busy === "preview" ? "Reading file…" : "Choose a .csv or .xlsx"}
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.xlsx"
            style={{ display: "none" }}
            disabled={busy !== null}
            onChange={(e) => {
              const selected = e.target.files?.[0];
              if (!selected) return;
              setFile(selected);
              void runPreview(selected);
            }}
          />
        </label>
      )}

      {error && (
        <div style={{ background: "#fdecec", color: "#b42318", borderRadius: 10, padding: "10px 12px", fontSize: 12.5, fontWeight: 700 }}>
          {error}
        </div>
      )}

      {applied?.applied && (
        <div style={{ background: "#f0faf4", border: "1px solid #c8e8d8", borderRadius: 10, padding: "12px 14px", display: "flex", gap: 9, alignItems: "center" }}>
          <CheckCircle2 size={16} color={colors.green} />
          <span style={{ color: colors.text, fontSize: 13, fontWeight: 700 }}>
            Reassigned {applied.applied.ae_changed} AE and {applied.applied.sdr_changed} SDR slots
            {" "}across {applied.summary.will_change} accounts ({applied.applied.contacts_touched} prospects followed).
          </span>
        </div>
      )}

      {summary && (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {/* The summary counts "will_change"; the row status for those rows
                is "ok". Keep this mapping explicit — passing the summary key
                straight through is what crashed the page. */}
            {(
              [
                ["ok", summary.will_change],
                ["no_change", summary.no_change],
                ["not_found", summary.not_found],
                ["ambiguous", summary.ambiguous],
                ["unknown_rep", summary.unknown_rep],
                ["no_identifier", summary.no_identifier],
              ] as [AssignmentUploadRow["status"], number][]
            )
              .filter(([, count]) => count > 0)
              .map(([status, count]) => (
                <div key={status} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <StatusPill status={status} />
                  <span style={{ color: colors.text, fontWeight: 800, fontSize: 13 }}>{count}</span>
                </div>
              ))}
          </div>

          {problems > 0 && (
            <div style={{ background: "#fff8ef", border: "1px solid #ffd8a8", borderRadius: 10, padding: "10px 12px", display: "flex", gap: 9, fontSize: 12.5, color: colors.text }}>
              <AlertTriangle size={15} color={colors.amber} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>
                {problems} row{problems === 1 ? "" : "s"} will be skipped. Applying only changes the{" "}
                {summary.will_change} row{summary.will_change === 1 ? "" : "s"} marked “Will change”.
              </span>
            </div>
          )}

          <div style={{ maxHeight: 340, overflowY: "auto", overflowX: "auto", border: `1px solid ${colors.border}`, borderRadius: 10 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead>
                <tr style={{ background: "#f7f9fc", position: "sticky", top: 0 }}>
                  {["#", "Account", "AE", "SDR", "Status"].map((h) => (
                    <th key={h} style={{ textAlign: "left", padding: "8px 10px", color: colors.sub, fontWeight: 800, whiteSpace: "nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.row_number} style={{ borderTop: `1px solid ${colors.border}` }}>
                    <td style={{ padding: "8px 10px", color: colors.sub }}>{row.row_number}</td>
                    <td style={{ padding: "8px 10px", color: colors.text, fontWeight: 700 }}>
                      {row.company_name || row.identifier}
                      {row.message && (
                        <div style={{ color: colors.sub, fontWeight: 500, fontSize: 11.5 }}>{row.message}</div>
                      )}
                    </td>
                    <td style={{ padding: "8px 10px" }}>
                      <Arrow from={row.current_ae} to={row.new_ae} changes={row.ae_changes} />
                    </td>
                    <td style={{ padding: "8px 10px" }}>
                      <Arrow from={row.current_sdr} to={row.new_sdr} changes={row.sdr_changes} />
                    </td>
                    <td style={{ padding: "8px 10px" }}><StatusPill status={row.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <button
              type="button"
              disabled={busy !== null || summary.will_change === 0}
              onClick={() => void runApply()}
              className="crm-button primary"
              style={{
                height: 36, padding: "0 16px", fontSize: 13, fontWeight: 800,
                cursor: busy !== null || summary.will_change === 0 ? "not-allowed" : "pointer",
                opacity: summary.will_change === 0 ? 0.55 : 1,
                display: "flex", alignItems: "center", gap: 7,
              }}
            >
              {busy === "apply" ? <Loader2 size={14} className="animate-spin" /> : null}
              {busy === "apply"
                ? "Applying…"
                : `Apply ${summary.will_change} reassignment${summary.will_change === 1 ? "" : "s"}`}
            </button>
            <span style={{ color: colors.sub, fontSize: 12 }}>
              Reassigning an SDR resets that account's outreach counters, same as changing it on the account.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

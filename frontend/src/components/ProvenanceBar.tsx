import { Database, Upload, Clock, User as UserIcon, RefreshCw } from "lucide-react";

/**
 * Small horizontal strip that shows where a record came from, who uploaded
 * it, and when. Drop it at the top of any detail page so reps know the
 * source-of-truth for the data they're looking at.
 *
 * Designed to be tolerant of sparse inputs — any field that's missing is
 * simply omitted rather than rendered as "—", so a minimally-populated
 * record still looks clean.
 */

type ProvenanceProps = {
  source?: string | null;
  uploadedBy?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  enrichedAt?: string | null;
  sourceLabel?: string | null;
};

const SOURCE_LABELS: Record<string, string> = {
  manual_prospect: "Manually added",
  prospect_csv_upload: "CSV / Excel upload",
  upload: "CSV / Excel upload",
  clickup_import: "ClickUp migration",
  personal_email_sync: "Gmail sync",
  account_sourcing: "Account sourcing",
  apollo: "Apollo enrichment",
  hunter: "Hunter enrichment",
  manual: "Manual entry",
};

function friendlySource(raw?: string | null, fallback?: string | null): string | null {
  if (!raw && !fallback) return null;
  const key = (raw || fallback || "").toString().trim().toLowerCase();
  return SOURCE_LABELS[key] ?? (raw || fallback || null);
}

function formatTime(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function ProvenanceBar({
  source,
  uploadedBy,
  createdAt,
  updatedAt,
  enrichedAt,
  sourceLabel,
}: ProvenanceProps) {
  const label = friendlySource(source, sourceLabel);
  const uploadedTime = formatTime(createdAt);
  const lastEnriched = formatTime(enrichedAt);
  const lastUpdated = formatTime(updatedAt);

  // Render nothing at all if we have zero signal (keeps the detail page clean)
  if (!label && !uploadedBy && !uploadedTime && !lastEnriched && !lastUpdated) {
    return null;
  }

  const chipBase: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "5px 10px",
    borderRadius: 999,
    background: "#f4f7fb",
    color: "#4b6480",
    fontSize: 12,
    fontWeight: 600,
    border: "1px solid #e1e8f0",
    whiteSpace: "nowrap",
  };

  return (
    <div style={{
      display: "flex",
      flexWrap: "wrap",
      gap: 6,
      marginTop: 6,
      marginBottom: 4,
      alignItems: "center",
    }}>
      {label && (
        <span style={chipBase} title="Where this record came from">
          <Database size={12} />
          Source: {label}
        </span>
      )}
      {uploadedBy && (
        <span style={chipBase} title="Who added this record">
          <UserIcon size={12} />
          By {uploadedBy}
        </span>
      )}
      {uploadedTime && (
        <span style={chipBase} title="When this record was created">
          <Upload size={12} />
          Added {uploadedTime}
        </span>
      )}
      {lastEnriched && (
        <span style={chipBase} title="Last enriched by Beacon research">
          <RefreshCw size={12} />
          Enriched {lastEnriched}
        </span>
      )}
      {lastUpdated && lastUpdated !== uploadedTime && (
        <span style={chipBase} title="Last modified">
          <Clock size={12} />
          Updated {lastUpdated}
        </span>
      )}
    </div>
  );
}

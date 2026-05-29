import { useEffect, useState } from "react";
import { Sparkles, RefreshCw, AlertTriangle, CheckCircle2 } from "lucide-react";
import {
  knowledgeApi,
  type IndexStatus,
  type IndexReport,
} from "../../lib/api";

/**
 * Settings-embedded panel for managing Zippy's knowledge index.
 *
 * Lets the user (and admin) see which Drive files are indexed, trigger a
 * fresh sync, force a full re-embed, or wipe the scope entirely.
 */
export function ZippyIndexPanel({ isAdmin }: { isAdmin: boolean }) {
  const [userStatus, setUserStatus] = useState<IndexStatus | null>(null);
  const [adminStatus, setAdminStatus] = useState<IndexStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const [u, a] = await Promise.all([
        knowledgeApi.status("user"),
        isAdmin ? knowledgeApi.status("admin") : Promise.resolve(null),
      ]);
      setUserStatus(u);
      setAdminStatus(a);
    } catch (e) {
      setError(formatError(e));
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  async function runReindex(scope: "user" | "admin", force: boolean) {
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const res =
        scope === "admin"
          ? await knowledgeApi.reindexAdmin(force)
          : await knowledgeApi.reindex(force);
      const r = res.report as IndexReport;
      if (r && typeof r === "object" && "files_indexed" in r) {
        setMessage(
          `Indexed ${r.files_indexed} / ${r.files_scanned} files · ` +
            `${r.chunks_written} chunks written · ` +
            `${r.files_skipped_unchanged} unchanged · ` +
            `${r.files_failed} failed.`,
        );
      } else {
        setMessage("Reindex complete.");
      }
      await refresh();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }

  async function runReset(scope: "user" | "admin") {
    const confirmed = window.confirm(
      scope === "admin"
        ? "Reset the workspace-wide knowledge index? This removes every indexed file and embedding for the shared folder."
        : "Reset your personal knowledge index? This removes every indexed file and embedding for your Drive folder.",
    );
    if (!confirmed) return;
    setLoading(true);
    setError(null);
    try {
      if (scope === "admin") await knowledgeApi.resetAdmin();
      else await knowledgeApi.reset();
      setMessage("Index reset. Run a reindex when ready.");
      await refresh();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      className="crm-panel"
      style={{
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 18,
        marginTop: 18,
      }}
    >
      <div>
        <div
          className="crm-chip"
          style={{
            marginBottom: 10,
            background: "#f4f0ff",
            color: "#5a3aa8",
            borderColor: "#d9ccf5",
          }}
        >
          <Sparkles size={13} />
          Zippy Knowledge Index
        </div>
        <h3
          style={{
            fontSize: 18,
            fontWeight: 800,
            color: "#182042",
            marginBottom: 6,
          }}
        >
          What Zippy can read from your Drive
        </h3>
        <p
          className="crm-muted"
          style={{ maxWidth: 640, lineHeight: 1.7, fontSize: 14 }}
        >
          Zippy searches your selected Drive folder (plus the admin shared
          folder) to answer questions and ground every response. Trigger a
          reindex after you've added new files to the folder.
        </p>
      </div>

      {message && (
        <div
          style={{
            padding: "10px 14px",
            borderRadius: 10,
            background: "#f0fbf4",
            border: "1px solid #c8e8d4",
            color: "#217a49",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <CheckCircle2 size={14} /> {message}
        </div>
      )}
      {error && (
        <div
          style={{
            padding: "10px 14px",
            borderRadius: 10,
            background: "#fdecec",
            border: "1px solid #f1c5c5",
            color: "#b42318",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      <ScopeCard
        title="Your folder"
        description="Private to you. Indexed into Qdrant with a per-user scope so only your Zippy sessions can see it."
        status={userStatus}
        loading={loading}
        onReindex={() => runReindex("user", false)}
        onForce={() => runReindex("user", true)}
        onReset={() => runReset("user")}
      />

      {isAdmin && (
        <ScopeCard
          title="Workspace (admin) folder"
          description="Shared across every user. Good for company playbooks, case studies, templates."
          status={adminStatus}
          loading={loading}
          onReindex={() => runReindex("admin", false)}
          onForce={() => runReindex("admin", true)}
          onReset={() => runReset("admin")}
        />
      )}
    </section>
  );
}


function ScopeCard({
  title,
  description,
  status,
  loading,
  onReindex,
  onForce,
  onReset,
}: {
  title: string;
  description: string;
  status: IndexStatus | null;
  loading: boolean;
  onReindex: () => void;
  onForce: () => void;
  onReset: () => void;
}) {
  const noFolder = !status?.folder_id;
  return (
    <div
      style={{
        border: "1px solid #e7eaf5",
        borderRadius: 12,
        padding: 18,
        background: "#fafbff",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              fontSize: 12,
              color: "#7c86a6",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.07em",
              marginBottom: 6,
            }}
          >
            {title}
          </div>
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: "#182042",
              marginBottom: 4,
            }}
          >
            {status?.folder_name || "No folder selected"}
          </div>
          <p
            className="crm-muted"
            style={{ fontSize: 13, lineHeight: 1.6, margin: 0 }}
          >
            {description}
          </p>
          {status && (
            <div
              className="crm-muted"
              style={{
                marginTop: 10,
                fontSize: 12,
                display: "flex",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <span>Files: {status.total_files}</span>
              <span>Indexed: {status.successful}</span>
              <span>Failed: {status.failed}</span>
              <span>Chunks: {status.total_chunks}</span>
            </div>
          )}
        </div>
        <div
          style={{
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <button
            className="crm-button primary"
            onClick={onReindex}
            disabled={noFolder || loading}
            title="Only sync files that changed since the last index."
          >
            <RefreshCw size={14} /> Reindex
          </button>
          <button
            className="crm-button"
            onClick={onForce}
            disabled={noFolder || loading}
            title="Rebuild every file, ignoring the modified-time cache."
          >
            Force rebuild
          </button>
          <button
            className="crm-button"
            onClick={onReset}
            disabled={noFolder || loading || (status?.total_files ?? 0) === 0}
            title="Drop all indexed content in this scope."
          >
            Reset
          </button>
        </div>
      </div>

      {status && status.files.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "#7c86a6",
              textTransform: "uppercase",
              letterSpacing: "0.07em",
              marginBottom: 8,
            }}
          >
            Latest files
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              maxHeight: 200,
              overflowY: "auto",
            }}
          >
            {status.files.slice(0, 25).map((f) => (
              <div
                key={f.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 12,
                  padding: "8px 10px",
                  borderRadius: 8,
                  background: "#fff",
                  border: "1px solid #eef0f8",
                  fontSize: 12,
                }}
              >
                <a
                  href={f.web_view_link || "#"}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    color: "#3d4678",
                    fontWeight: 600,
                    textDecoration: "none",
                    flex: 1,
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {f.name}
                </a>
                <span
                  style={{
                    color: f.last_error ? "#b42318" : "#217a49",
                    fontSize: 11,
                  }}
                >
                  {f.last_error
                    ? `error: ${f.last_error.slice(0, 40)}…`
                    : `${f.qdrant_chunk_count} chunks`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


function formatError(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  try {
    return JSON.stringify(e);
  } catch {
    return "Unexpected error.";
  }
}

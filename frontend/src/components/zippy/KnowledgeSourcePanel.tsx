import { useEffect, useRef, useState } from "react";
import {
  Folder,
  RefreshCw,
  Trash2,
  AlertTriangle,
  CheckCircle2,
  Sparkles,
  FileText,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import {
  knowledgeApi,
  type IndexStatus,
  type IndexReport,
  type SelectedDriveFolder,
} from "../../lib/api";

/**
 * Single merged panel that replaces both the "Google Drive Folders" and
 * "Zippy Knowledge Index" sections.
 *
 * Per scope (admin workspace + personal), a user can:
 *   • Pick / change / clear the Drive folder
 *   • See live indexing stats (files, chunks, last synced)
 *   • Trigger a sync (Sync now / Force rebuild / Reset)
 *   • Auto-sync is fired whenever the folder id changes, so they usually
 *     never have to click anything.
 */

export interface KnowledgeSourcePanelProps {
  isAdmin: boolean;
  connected: boolean;
  driveLoading: boolean;
  userFolder: SelectedDriveFolder | null;
  adminFolder: SelectedDriveFolder | null;
  driveMessage: string | null;
  onOpenPicker: (scope: "user" | "admin") => void;
  onClearUser: () => void;
}

export function KnowledgeSourcePanel({
  isAdmin,
  connected,
  driveLoading,
  userFolder,
  adminFolder,
  driveMessage,
  onOpenPicker,
  onClearUser,
}: KnowledgeSourcePanelProps) {
  const [userStatus, setUserStatus] = useState<IndexStatus | null>(null);
  const [adminStatus, setAdminStatus] = useState<IndexStatus | null>(null);
  const [loadingScope, setLoadingScope] = useState<"user" | "admin" | null>(null);
  const [indexMessage, setIndexMessage] = useState<string | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);

  // Track which folder ids we've already auto-indexed so we don't re-fire
  // every time the component rerenders.
  const autoIndexedUserFolderRef = useRef<string | null>(null);
  const autoIndexedAdminFolderRef = useRef<string | null>(null);

  async function refresh() {
    try {
      const [u, a] = await Promise.all([
        knowledgeApi.status("user"),
        isAdmin ? knowledgeApi.status("admin") : Promise.resolve(null),
      ]);
      setUserStatus(u);
      setAdminStatus(a);
    } catch (e) {
      setIndexError(formatError(e));
    }
  }

  useEffect(() => {
    if (!connected) {
      setUserStatus(null);
      setAdminStatus(null);
      return;
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin, connected]);

  // Auto-reindex when the user picks/changes a folder the first time.
  useEffect(() => {
    if (!connected) return;
    const personalFolderId =
      userFolder && !userFolder.is_admin_folder ? userFolder.folder_id : null;
    if (
      personalFolderId &&
      autoIndexedUserFolderRef.current !== personalFolderId
    ) {
      autoIndexedUserFolderRef.current = personalFolderId;
      void runReindex("user", false, { silent: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userFolder?.folder_id, userFolder?.is_admin_folder, connected]);

  useEffect(() => {
    if (!connected || !isAdmin) return;
    const id = adminFolder?.folder_id ?? null;
    if (id && autoIndexedAdminFolderRef.current !== id) {
      autoIndexedAdminFolderRef.current = id;
      void runReindex("admin", false, { silent: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminFolder?.folder_id, connected, isAdmin]);

  async function runReindex(
    scope: "user" | "admin",
    force: boolean,
    opts: { silent?: boolean } = {},
  ) {
    setLoadingScope(scope);
    setIndexError(null);
    if (!opts.silent) setIndexMessage(null);
    try {
      const res =
        scope === "admin"
          ? await knowledgeApi.reindexAdmin(force)
          : await knowledgeApi.reindex(force);
      const r = res.report as IndexReport;
      if (r && typeof r === "object" && "files_indexed" in r) {
        setIndexMessage(
          `${scope === "admin" ? "Workspace" : "Your"} folder · ` +
            `indexed ${r.files_indexed} / ${r.files_scanned} files · ` +
            `${r.chunks_written} chunks · ${r.files_skipped_unchanged} unchanged · ` +
            `${r.files_failed} failed.`,
        );
      } else {
        setIndexMessage("Sync complete.");
      }
      await refresh();
    } catch (e) {
      setIndexError(formatError(e));
    } finally {
      setLoadingScope(null);
    }
  }

  async function runReset(scope: "user" | "admin") {
    const confirmed = window.confirm(
      scope === "admin"
        ? "Reset the workspace-wide knowledge index? This removes every indexed file and embedding for the shared folder."
        : "Reset your personal knowledge index? This removes every indexed file and embedding for your Drive folder.",
    );
    if (!confirmed) return;
    setLoadingScope(scope);
    setIndexError(null);
    try {
      if (scope === "admin") await knowledgeApi.resetAdmin();
      else await knowledgeApi.reset();
      setIndexMessage("Index reset. Pick a folder again or hit Sync now to rebuild.");
      await refresh();
    } catch (e) {
      setIndexError(formatError(e));
    } finally {
      setLoadingScope(null);
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
          Knowledge Source
        </div>
        <h3
          style={{
            fontSize: 18,
            fontWeight: 800,
            color: "#182042",
            marginBottom: 6,
          }}
        >
          What Beacon &amp; Zippy can read from your Drive
        </h3>
        <p
          className="crm-muted"
          style={{ maxWidth: 720, lineHeight: 1.7, fontSize: 14 }}
        >
          Pick a Drive folder — Beacon will automatically sync its contents into
          Zippy's knowledge base so every answer is grounded in your own docs.
          {isAdmin
            ? " As an admin you can set a workspace-wide folder that every user sees, plus a personal one just for yourself."
            : " This is your private folder — only you can see what's indexed."}
        </p>
      </div>

      {!connected && (
        <div
          style={{
            padding: "12px 14px",
            borderRadius: 10,
            background: "#fff4e6",
            border: "1px solid #f0d4ac",
            color: "#a46206",
            fontSize: 13,
          }}
        >
          Connect your personal Gmail above first — Drive access is granted as
          part of that consent.
        </div>
      )}

      {driveMessage && (
        <div
          style={{
            padding: "10px 14px",
            borderRadius: 10,
            background: "#f0fbf4",
            border: "1px solid #c8e8d4",
            color: "#217a49",
            fontSize: 13,
          }}
        >
          {driveMessage}
        </div>
      )}

      {indexMessage && (
        <div
          style={{
            padding: "10px 14px",
            borderRadius: 10,
            background: "#eef4ff",
            border: "1px solid #cdd8f5",
            color: "#3b4dc8",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <CheckCircle2 size={14} /> {indexMessage}
        </div>
      )}
      {indexError && (
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
          <AlertTriangle size={14} /> {indexError}
        </div>
      )}

      {isAdmin && (
        <FolderCard
          label="Workspace folder (admin)"
          description="Shared across every user. Good for company playbooks, case studies, templates."
          folder={adminFolder}
          status={adminStatus}
          connected={connected}
          driveLoading={driveLoading}
          loadingScope={loadingScope}
          scope="admin"
          onPick={() => onOpenPicker("admin")}
          onReindex={() => runReindex("admin", false)}
          onForceRebuild={() => runReindex("admin", true)}
          onReset={() => runReset("admin")}
        />
      )}

      <FolderCard
        label="My personal folder"
        description="Only visible to you. Great for your own notes, call recordings, or reference material."
        folder={
          userFolder && !userFolder.is_admin_folder ? userFolder : null
        }
        status={userStatus}
        connected={connected}
        driveLoading={driveLoading}
        loadingScope={loadingScope}
        scope="user"
        onPick={() => onOpenPicker("user")}
        onClear={onClearUser}
        onReindex={() => runReindex("user", false)}
        onForceRebuild={() => runReindex("user", true)}
        onReset={() => runReset("user")}
      />
    </section>
  );
}


// ── Folder card ───────────────────────────────────────────────────────────────


interface FolderCardProps {
  label: string;
  description: string;
  folder: SelectedDriveFolder | null;
  status: IndexStatus | null;
  connected: boolean;
  driveLoading: boolean;
  loadingScope: "user" | "admin" | null;
  scope: "user" | "admin";
  onPick: () => void;
  onClear?: () => void;
  onReindex: () => void;
  onForceRebuild: () => void;
  onReset: () => void;
}


function FolderCard({
  label,
  description,
  folder,
  status,
  connected,
  driveLoading,
  loadingScope,
  scope,
  onPick,
  onClear,
  onReindex,
  onForceRebuild,
  onReset,
}: FolderCardProps) {
  const [showFiles, setShowFiles] = useState(false);
  const isLoading = loadingScope === scope;
  const hasFolder = !!folder?.folder_id;
  const lastSynced = status?.files
    .map((f) => f.last_indexed_at)
    .filter(Boolean)
    .sort()
    .reverse()[0];

  return (
    <div
      style={{
        border: "1px solid #e7eaf5",
        borderRadius: 12,
        padding: 18,
        background: scope === "admin" ? "#f8faff" : "#fff",
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
            {label}
          </div>
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: "#182042",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <Folder size={16} style={{ color: "#4958d8" }} />
            {folder?.folder_name || "Not selected"}
          </div>
          {folder?.owner_email && (
            <div className="crm-muted" style={{ fontSize: 12, marginTop: 4 }}>
              Owned by {folder.owner_email}
            </div>
          )}
          <p
            className="crm-muted"
            style={{ fontSize: 12, marginTop: 6, lineHeight: 1.55, margin: 0 }}
          >
            {description}
          </p>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className={hasFolder ? "crm-button soft" : "crm-button primary"}
            onClick={onPick}
            disabled={!connected || driveLoading || isLoading}
          >
            {driveLoading ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <Folder size={14} />
            )}
            {hasFolder ? "Change folder" : "Choose folder"}
          </button>
          {hasFolder && onClear && (
            <button
              className="crm-button soft"
              onClick={onClear}
              disabled={driveLoading || isLoading}
              style={{ color: "#c53030" }}
            >
              <Trash2 size={14} />
              Clear
            </button>
          )}
        </div>
      </div>

      {hasFolder && (
        <div
          style={{
            marginTop: 14,
            paddingTop: 14,
            borderTop: "1px dashed #e7eaf5",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 16,
            flexWrap: "wrap",
          }}
        >
          <div
            className="crm-muted"
            style={{
              fontSize: 12,
              display: "flex",
              gap: 14,
              flexWrap: "wrap",
              alignItems: "center",
            }}
          >
            <StatPill
              label="Files"
              value={status?.total_files ?? 0}
              tone="neutral"
            />
            <StatPill
              label="Chunks"
              value={status?.total_chunks ?? 0}
              tone="neutral"
            />
            {status && (status.skipped ?? 0) > 0 && (
              <StatPill
                label="Skipped"
                value={status.skipped ?? 0}
                tone="muted"
              />
            )}
            {status && status.failed > 0 && (
              <StatPill label="Failed" value={status.failed} tone="error" />
            )}
            <span>
              Last synced:{" "}
              <strong style={{ color: "#182042" }}>
                {lastSynced ? formatRelative(lastSynced) : "never"}
              </strong>
            </span>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className="crm-button primary"
              onClick={onReindex}
              disabled={isLoading}
              title="Only sync files that changed since the last index."
            >
              {isLoading ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              {isLoading ? "Syncing…" : "Sync now"}
            </button>
            <button
              className="crm-button soft"
              onClick={onForceRebuild}
              disabled={isLoading}
              title="Rebuild every file, ignoring the modified-time cache."
            >
              Force rebuild
            </button>
            <button
              className="crm-button soft"
              onClick={onReset}
              disabled={isLoading || (status?.total_files ?? 0) === 0}
              title="Drop all indexed content in this scope."
              style={{ color: "#c53030" }}
            >
              Reset
            </button>
          </div>
        </div>
      )}

      {hasFolder && status && status.files.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <button
            className="crm-button soft"
            onClick={() => setShowFiles((v) => !v)}
            style={{ fontSize: 12 }}
          >
            {showFiles ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            {showFiles ? "Hide" : "Show"} indexed files ({status.files.length})
          </button>
          {showFiles && (
            <div
              style={{
                marginTop: 10,
                display: "flex",
                flexDirection: "column",
                gap: 6,
                maxHeight: 260,
                overflowY: "auto",
              }}
            >
              {status.files.slice(0, 50).map((f) => (
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
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    <FileText size={12} style={{ color: "#7c86a6" }} />
                    {f.name}
                  </a>
                  <span
                    style={{
                      color: !f.last_error
                        ? "#217a49"
                        : isSkipError(f.last_error)
                        ? "#8a8fa8"
                        : "#b42318",
                      fontSize: 11,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {!f.last_error
                      ? `${f.qdrant_chunk_count} chunks`
                      : isSkipError(f.last_error)
                      ? `skipped: ${f.last_error.toLowerCase()}`
                      : `error: ${f.last_error.slice(0, 40)}…`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function StatPill({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "neutral" | "error" | "muted";
}) {
  const bg =
    tone === "error" ? "#fdecec" : tone === "muted" ? "#f2f2f5" : "#eef2fb";
  const color =
    tone === "error" ? "#b42318" : tone === "muted" ? "#6b7094" : "#3d4678";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "2px 8px",
        borderRadius: 999,
        background: bg,
        color,
        fontWeight: 600,
      }}
    >
      {label}: {value}
    </span>
  );
}


// Errors emitted by the indexer for files we intentionally don't embed
// (videos, images, macOS metadata). These shouldn't be styled as failures.
const SKIP_ERRORS = new Set([
  "Unsupported file type",
  "Drive returned no content",
  "No extractable text",
]);

function isSkipError(err: string | null | undefined): boolean {
  return !!err && SKIP_ERRORS.has(err);
}


function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Date.now() - then;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
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

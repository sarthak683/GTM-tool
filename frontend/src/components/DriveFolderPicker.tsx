/**
 * DriveFolderPicker
 * -----------------
 * Modal that lets a user browse their Google Drive folder tree and pick one.
 *
 * - Drills in/out using GET /drive/folders?parent_id=...
 * - Supports name search via GET /drive/folders/search?q=...
 * - Calls back with the chosen folder; the parent component decides what to do
 *   (select as personal folder, select as admin folder, etc.).
 *
 * Requires the user to have already connected Gmail (since the Drive scope is
 * granted as part of the Gmail OAuth consent).
 */
import { useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  Folder,
  FolderOpen,
  Home,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { driveApi, type DriveFolder } from "../lib/api";

interface Crumb {
  id: string | null; // null === root
  name: string;
}

interface DriveFolderPickerProps {
  open: boolean;
  onClose: () => void;
  onPick: (folder: DriveFolder) => void | Promise<void>;
  title?: string;
  description?: string;
  confirmLabel?: string;
}

export function DriveFolderPicker({
  open,
  onClose,
  onPick,
  title = "Select a Google Drive folder",
  description = "Pick the folder Beacon should sync from.",
  confirmLabel = "Use this folder",
}: DriveFolderPickerProps) {
  const [folders, setFolders] = useState<DriveFolder[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ id: null, name: "My Drive" }]);
  const [selected, setSelected] = useState<DriveFolder | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearchMode, setIsSearchMode] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const currentParentId = useMemo(() => crumbs[crumbs.length - 1]?.id ?? null, [crumbs]);

  useEffect(() => {
    if (!open) return;
    // Reset state on open
    setCrumbs([{ id: null, name: "My Drive" }]);
    setSelected(null);
    setSearchQuery("");
    setIsSearchMode(false);
    void loadFolders(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function loadFolders(parentId: string | null) {
    setLoading(true);
    setError(null);
    try {
      const data = await driveApi.listFolders(parentId ?? undefined);
      setFolders(data.folders || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load folders");
      setFolders([]);
    } finally {
      setLoading(false);
    }
  }

  async function runSearch(q: string) {
    if (!q.trim()) {
      setIsSearchMode(false);
      void loadFolders(currentParentId);
      return;
    }
    setLoading(true);
    setError(null);
    setIsSearchMode(true);
    try {
      const data = await driveApi.searchFolders(q.trim());
      setFolders(data.folders || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
      setFolders([]);
    } finally {
      setLoading(false);
    }
  }

  function openFolder(folder: DriveFolder) {
    setIsSearchMode(false);
    setSearchQuery("");
    setSelected(folder);
    setCrumbs((prev) => [...prev, { id: folder.id, name: folder.name }]);
    void loadFolders(folder.id);
  }

  function navigateToCrumb(index: number) {
    const crumb = crumbs[index];
    setIsSearchMode(false);
    setSearchQuery("");
    setSelected(null);
    setCrumbs(crumbs.slice(0, index + 1));
    void loadFolders(crumb.id);
  }

  async function handleConfirm() {
    if (!selected) return;
    setConfirming(true);
    try {
      await onPick(selected);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save folder");
    } finally {
      setConfirming(false);
    }
  }

  if (!open) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(16, 22, 55, 0.55)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#fff",
          borderRadius: 16,
          width: "100%",
          maxWidth: 620,
          maxHeight: "85vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 20px 40px rgba(16, 22, 55, 0.25)",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div style={{ padding: "18px 22px", borderBottom: "1px solid #eceffa", display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <h3 style={{ fontSize: 18, fontWeight: 800, color: "#182042", marginBottom: 4 }}>{title}</h3>
            <p style={{ fontSize: 13, color: "#7c86a6", lineHeight: 1.5 }}>{description}</p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              color: "#7c86a6",
              padding: 4,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Search */}
        <div style={{ padding: "14px 22px", borderBottom: "1px solid #eceffa" }}>
          <div style={{ position: "relative" }}>
            <Search
              size={15}
              style={{ position: "absolute", top: "50%", left: 12, transform: "translateY(-50%)", color: "#97a0c0" }}
            />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void runSearch(searchQuery);
              }}
              placeholder="Search folders by name…"
              style={{
                width: "100%",
                padding: "10px 12px 10px 36px",
                fontSize: 14,
                border: "1px solid #e1e5f2",
                borderRadius: 10,
                outline: "none",
                background: "#f8faff",
              }}
            />
          </div>
        </div>

        {/* Breadcrumbs (hidden during search) */}
        {!isSearchMode && (
          <div style={{ padding: "10px 22px", borderBottom: "1px solid #eceffa", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 13 }}>
            {crumbs.map((crumb, idx) => {
              const isLast = idx === crumbs.length - 1;
              return (
                <span key={`${crumb.id ?? "root"}-${idx}`} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <button
                    onClick={() => !isLast && navigateToCrumb(idx)}
                    disabled={isLast}
                    style={{
                      background: "none",
                      border: "none",
                      color: isLast ? "#182042" : "#4958d8",
                      fontWeight: isLast ? 700 : 500,
                      cursor: isLast ? "default" : "pointer",
                      padding: 0,
                      fontSize: 13,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    {idx === 0 ? <Home size={13} /> : null}
                    {crumb.name}
                  </button>
                  {!isLast && <ChevronRight size={13} style={{ color: "#b4bcd6" }} />}
                </span>
              );
            })}
          </div>
        )}

        {/* Folder list */}
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px" }}>
          {loading && (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "40px 0", color: "#7c86a6", gap: 8, fontSize: 13 }}>
              <RefreshCw size={14} className="animate-spin" />
              Loading folders…
            </div>
          )}
          {!loading && error && (
            <div style={{ padding: "12px 14px", background: "#fff4e6", border: "1px solid #f0d4ac", color: "#a46206", borderRadius: 10, margin: 10, fontSize: 13 }}>
              {error}
            </div>
          )}
          {!loading && !error && folders.length === 0 && (
            <div style={{ textAlign: "center", padding: "40px 20px", color: "#7c86a6", fontSize: 13, lineHeight: 1.5 }}>
              {isSearchMode
                ? "No folders matched that search."
                : currentParentId
                  ? "No sub-folders here. Any files inside this folder will be synced — click \u201cUse this folder\u201d to select it."
                  : "This folder is empty."}
            </div>
          )}
          {!loading && !error && folders.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 2, paddingBottom: 8 }}>
              {folders.map((folder) => {
                const isSelected = selected?.id === folder.id;
                return (
                  <div
                    key={folder.id}
                    onClick={() => setSelected(folder)}
                    onDoubleClick={() => openFolder(folder)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "10px 12px",
                      borderRadius: 8,
                      cursor: "pointer",
                      background: isSelected ? "#eef2ff" : "transparent",
                      border: isSelected ? "1px solid #c8daf8" : "1px solid transparent",
                    }}
                  >
                    {isSelected ? (
                      <FolderOpen size={18} style={{ color: "#4958d8", flexShrink: 0 }} />
                    ) : (
                      <Folder size={18} style={{ color: "#7c86a6", flexShrink: 0 }} />
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: "#182042", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {folder.name}
                      </div>
                      <div style={{ fontSize: 11, color: "#7c86a6", marginTop: 2 }}>
                        {folder.shared ? "Shared" : folder.owned_by_me ? "Owned by you" : "Drive"}
                      </div>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        openFolder(folder);
                      }}
                      style={{
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        color: "#7c86a6",
                        padding: 4,
                        display: "flex",
                        alignItems: "center",
                      }}
                      aria-label={`Open ${folder.name}`}
                    >
                      <ChevronRight size={16} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "14px 22px", borderTop: "1px solid #eceffa", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ fontSize: 13, color: "#7c86a6", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {selected ? (
              <>
                Selected: <strong style={{ color: "#182042" }}>{selected.name}</strong>
              </>
            ) : (
              "Click a folder to select it. Double-click to open."
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={onClose}
              className="crm-button soft"
              style={{ minWidth: 80 }}
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={!selected || confirming}
              className="crm-button primary"
              style={{ minWidth: 140, opacity: !selected ? 0.5 : 1 }}
            >
              {confirming ? <RefreshCw size={14} className="animate-spin" /> : null}
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

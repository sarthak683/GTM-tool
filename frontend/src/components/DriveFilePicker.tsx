/**
 * DriveFilePicker
 * ---------------
 * Modal that lets a user browse their Google Drive folder tree and pick a
 * FILE (not a folder) — used by the deal drawer's Documents tab "Add from
 * Drive" flow. Adapted from DriveFolderPicker, which only ever lists
 * folders; this lists both at each level (folders to drill into, files to
 * select) via the same GET /drive/folders and the newer
 * GET /drive/folders/{id}/files.
 *
 * Requires the user to have already connected Gmail with Drive scope (same
 * requirement as DriveFolderPicker) — an inactive/missing connection surfaces
 * as an inline error rather than a crash.
 */
import { useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  File as FileIcon,
  Folder,
  Home,
  RefreshCw,
  X,
} from "lucide-react";
import { driveApi, type DriveFolder, type DriveFile } from "../lib/api";

interface Crumb {
  id: string | null; // null === root
  name: string;
}

interface DriveFilePickerProps {
  open: boolean;
  onClose: () => void;
  onPick: (file: DriveFile) => void | Promise<void>;
  title?: string;
  description?: string;
}

function formatFileSize(bytes?: number): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function DriveFilePicker({
  open,
  onClose,
  onPick,
  title = "Add a file from Google Drive",
  description = "Pick a file already in your Drive — it stays there, this just links it to the deal.",
}: DriveFilePickerProps) {
  const [folders, setFolders] = useState<DriveFolder[]>([]);
  const [files, setFiles] = useState<DriveFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ id: null, name: "My Drive" }]);
  const [selected, setSelected] = useState<DriveFile | null>(null);
  const [attaching, setAttaching] = useState(false);

  const currentParentId = useMemo(() => crumbs[crumbs.length - 1]?.id ?? null, [crumbs]);

  useEffect(() => {
    if (!open) return;
    setCrumbs([{ id: null, name: "My Drive" }]);
    setSelected(null);
    void loadLevel(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function loadLevel(parentId: string | null) {
    setLoading(true);
    setError(null);
    setSelected(null);
    try {
      const folderData = await driveApi.listFolders(parentId ?? undefined);
      setFolders(folderData.folders || []);
      // Files only exist inside a real folder — Drive's API doesn't support
      // listing loose files sitting at "My Drive" root through this query
      // shape without a parent id, so root shows folders only until the rep
      // drills in once.
      if (parentId) {
        const fileData = await driveApi.listFilesInFolder(parentId);
        setFiles(fileData.files || []);
      } else {
        setFiles([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Drive contents");
      setFolders([]);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }

  function openFolder(folder: DriveFolder) {
    setCrumbs((prev) => [...prev, { id: folder.id, name: folder.name }]);
    void loadLevel(folder.id);
  }

  function navigateToCrumb(index: number) {
    const crumb = crumbs[index];
    setCrumbs(crumbs.slice(0, index + 1));
    void loadLevel(crumb.id);
  }

  async function handleConfirm() {
    if (!selected) return;
    setAttaching(true);
    try {
      await onPick(selected);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to attach file");
    } finally {
      setAttaching(false);
    }
  }

  if (!open) return null;

  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(16, 22, 55, 0.55)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ background: "#fff", borderRadius: 16, width: "100%", maxWidth: 620, maxHeight: "85vh", display: "flex", flexDirection: "column", boxShadow: "0 20px 40px rgba(16, 22, 55, 0.25)", overflow: "hidden" }}
      >
        <div style={{ padding: "18px 22px", borderBottom: "1px solid #eceffa", display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <h3 style={{ fontSize: 18, fontWeight: 800, color: "#182042", marginBottom: 4 }}>{title}</h3>
            <p style={{ fontSize: 13, color: "#7c86a6", lineHeight: 1.5 }}>{description}</p>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "#7c86a6", padding: 4, display: "flex", alignItems: "center", justifyContent: "center" }} aria-label="Close">
            <X size={20} />
          </button>
        </div>

        <div style={{ padding: "10px 22px", borderBottom: "1px solid #eceffa", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 13 }}>
          {crumbs.map((crumb, idx) => {
            const isLast = idx === crumbs.length - 1;
            return (
              <span key={`${crumb.id ?? "root"}-${idx}`} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <button
                  onClick={() => !isLast && navigateToCrumb(idx)}
                  disabled={isLast}
                  style={{ background: "none", border: "none", color: isLast ? "#182042" : "#4958d8", fontWeight: isLast ? 700 : 500, cursor: isLast ? "default" : "pointer", padding: 0, fontSize: 13, display: "inline-flex", alignItems: "center", gap: 4 }}
                >
                  {idx === 0 ? <Home size={13} /> : null}
                  {crumb.name}
                </button>
                {!isLast && <ChevronRight size={13} style={{ color: "#b4bcd6" }} />}
              </span>
            );
          })}
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px" }}>
          {loading && (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "40px 0", color: "#7c86a6", gap: 8, fontSize: 13 }}>
              <RefreshCw size={14} className="animate-spin" />
              Loading…
            </div>
          )}
          {!loading && error && (
            <div style={{ padding: "12px 14px", background: "#fff4e6", border: "1px solid #f0d4ac", color: "#a46206", borderRadius: 10, margin: 10, fontSize: 13 }}>
              {error}
            </div>
          )}
          {!loading && !error && folders.length === 0 && files.length === 0 && (
            <div style={{ textAlign: "center", padding: "40px 20px", color: "#7c86a6", fontSize: 13, lineHeight: 1.5 }}>
              This folder is empty.
            </div>
          )}
          {!loading && !error && (folders.length > 0 || files.length > 0) && (
            <div style={{ display: "flex", flexDirection: "column", gap: 2, paddingBottom: 8 }}>
              {folders.map((folder) => (
                <div
                  key={folder.id}
                  onClick={() => openFolder(folder)}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 8, cursor: "pointer" }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "#f8faff")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <Folder size={18} style={{ color: "#7c86a6", flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 600, color: "#182042", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {folder.name}
                  </div>
                  <ChevronRight size={16} style={{ color: "#b4bcd6", flexShrink: 0 }} />
                </div>
              ))}
              {files.map((file) => {
                const isSelected = selected?.id === file.id;
                return (
                  <div
                    key={file.id}
                    onClick={() => setSelected(file)}
                    style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 8, cursor: "pointer", background: isSelected ? "#eef2ff" : "transparent", border: isSelected ? "1px solid #c8daf8" : "1px solid transparent" }}
                  >
                    <FileIcon size={18} style={{ color: isSelected ? "#4958d8" : "#7c86a6", flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: "#182042", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {file.name}
                      </div>
                      {file.size_bytes != null && (
                        <div style={{ fontSize: 11, color: "#7c86a6", marginTop: 2 }}>{formatFileSize(file.size_bytes)}</div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ padding: "14px 22px", borderTop: "1px solid #eceffa", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ fontSize: 13, color: "#7c86a6", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {selected ? (
              <>
                Selected: <strong style={{ color: "#182042" }}>{selected.name}</strong>
              </>
            ) : (
              "Click a folder to open it, or a file to select it."
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={onClose} className="crm-button soft" style={{ minWidth: 80 }}>
              Cancel
            </button>
            <button onClick={handleConfirm} disabled={!selected || attaching} className="crm-button primary" style={{ minWidth: 120, opacity: !selected ? 0.5 : 1 }}>
              {attaching ? <RefreshCw size={14} className="animate-spin" /> : null}
              Attach file
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

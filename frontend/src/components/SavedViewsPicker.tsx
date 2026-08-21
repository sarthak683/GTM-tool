import { useEffect, useRef, useState } from "react";
import { BookmarkPlus, Check, ChevronDown, Pencil, Star, Trash2 } from "lucide-react";
import { savedViewsApi, type SavedView } from "../lib/api/crm";
import { useToast } from "../lib/ToastContext";

/**
 * Picker for the current user's saved views. Renders as a button + dropdown
 * beside the page title.
 *
 * The "filters" prop is whatever URL search-params shape the host page
 * already uses — the picker just shuttles it in/out via onApply. That keeps
 * the picker free of page-specific knowledge (Pipeline, Contacts, …) and
 * means saving a view = "snapshot the URL", which is exactly what we want.
 */

export interface SavedViewsPickerProps {
  objectType: string;
  viewType: string;
  /** Current filter shape — passed back into onApply on switch. */
  filters: Record<string, unknown>;
  /** Apply a saved view's filters. The page should push them to the URL. */
  onApply: (filters: Record<string, unknown>) => void;
  /** A friendly name to suggest for "Save current as…". Optional. */
  defaultName?: string;
}

export function SavedViewsPicker({
  objectType,
  viewType,
  filters,
  onApply,
  defaultName,
}: SavedViewsPickerProps) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [views, setViews] = useState<SavedView[]>([]);
  const [loading, setLoading] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [creatingNew, setCreatingNew] = useState(false);
  const [newName, setNewName] = useState("");
  const popoverRef = useRef<HTMLDivElement | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const data = await savedViewsApi.list(objectType, viewType);
      setViews(data);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load views.", "Could not load saved views");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    refresh();
  }, [open, objectType, viewType]);

  // Click-outside dismiss.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
        setCreatingNew(false);
        setRenamingId(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const applyView = async (view: SavedView) => {
    try {
      onApply(view.filters ?? {});
      // Fire-and-forget; failure to bump last_used_at isn't user-visible.
      void savedViewsApi.markApplied(view.id).catch(() => undefined);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not apply view.", "Apply failed");
    }
    setOpen(false);
  };

  const createView = async () => {
    const name = newName.trim() || defaultName || "Untitled view";
    try {
      await savedViewsApi.create({
        object_type: objectType,
        name,
        view_type: viewType,
        filters,
      });
      setNewName("");
      setCreatingNew(false);
      await refresh();
      toast.success(`Saved view “${name}”.`, "View created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save view.", "Save failed");
    }
  };

  const renameView = async (id: string) => {
    const name = renameDraft.trim();
    if (!name) return;
    try {
      await savedViewsApi.update(id, { name });
      setRenamingId(null);
      setRenameDraft("");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not rename view.", "Rename failed");
    }
  };

  const deleteView = async (id: string) => {
    if (!window.confirm("Delete this saved view? This cannot be undone.")) return;
    try {
      await savedViewsApi.delete(id);
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not delete view.", "Delete failed");
    }
  };

  const setDefault = async (id: string) => {
    try {
      await savedViewsApi.setDefault(id);
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not set default.", "Update failed");
    }
  };

  const defaultView = views.find((v) => v.is_default);
  const buttonLabel = defaultView ? defaultView.name : "Saved views";

  return (
    <div ref={popoverRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          height: 32,
          padding: "0 12px",
          borderRadius: 8,
          border: "1px solid #dce8f4",
          background: defaultView ? "#f3fbe3" : "#fff",
          color: defaultView ? "#4d7c0f" : "#4a6580",
          fontSize: 12.5,
          fontWeight: 700,
          cursor: "pointer",
        }}
      >
        <Star size={12} style={{ color: defaultView ? "#4d7c0f" : "#94a3b8" }} />
        {buttonLabel}
        <ChevronDown size={12} style={{ opacity: 0.7 }} />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Saved views"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 50,
            minWidth: 280,
            background: "#fff",
            border: "1px solid #dce8f4",
            borderRadius: 12,
            boxShadow: "0 18px 36px rgba(15,23,42,0.14)",
            padding: 6,
          }}
        >
          {/* Save current filters */}
          <div style={{ padding: "6px 8px" }}>
            {creatingNew ? (
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void createView();
                    if (e.key === "Escape") setCreatingNew(false);
                  }}
                  placeholder={defaultName ?? "View name…"}
                  style={{
                    flex: 1, height: 30, padding: "0 8px",
                    border: "1px solid #dce8f4", borderRadius: 7,
                    fontSize: 12.5, outline: "none",
                  }}
                />
                <button
                  type="button"
                  onClick={() => void createView()}
                  style={{
                    height: 30, padding: "0 12px", borderRadius: 7,
                    border: "none", background: "#4d7c0f", color: "#fff",
                    fontSize: 12, fontWeight: 800, cursor: "pointer",
                  }}
                >
                  Save
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setCreatingNew(true)}
                style={{
                  width: "100%", display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "7px 10px", border: "1px dashed #cfe89a", borderRadius: 8,
                  background: "#fafff1", color: "#4d7c0f",
                  fontSize: 12, fontWeight: 700, cursor: "pointer", textAlign: "left",
                }}
              >
                <BookmarkPlus size={13} /> Save current filters as view…
              </button>
            )}
          </div>

          <div style={{ borderTop: "1px solid #eef2f7", margin: "4px 0" }} />

          {/* Existing views */}
          {loading && views.length === 0 ? (
            <div style={{ padding: "10px 12px", fontSize: 12, color: "#94a3b8" }}>Loading…</div>
          ) : views.length === 0 ? (
            <div style={{ padding: "10px 12px", fontSize: 12, color: "#94a3b8" }}>
              No saved views yet for this page.
            </div>
          ) : (
            <div style={{ maxHeight: 280, overflowY: "auto" }}>
              {views.map((view) => (
                <div
                  key={view.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "6px 8px",
                    borderRadius: 8,
                  }}
                >
                  {renamingId === view.id ? (
                    <input
                      autoFocus
                      value={renameDraft}
                      onChange={(e) => setRenameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void renameView(view.id);
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      onBlur={() => void renameView(view.id)}
                      style={{
                        flex: 1, height: 28, padding: "0 8px",
                        border: "1px solid #cfe89a", borderRadius: 6,
                        fontSize: 12.5, outline: "none",
                      }}
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => void applyView(view)}
                      title={`Apply “${view.name}”`}
                      style={{
                        flex: 1, display: "inline-flex", alignItems: "center", gap: 6,
                        padding: "4px 6px", border: "none", background: "transparent",
                        color: "#1c2d40", fontSize: 12.5, fontWeight: 600, cursor: "pointer",
                        textAlign: "left", borderRadius: 6,
                      }}
                    >
                      {view.is_default && (
                        <Star size={11} style={{ color: "#9ace3d", flexShrink: 0 }} fill="#9ace3d" />
                      )}
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {view.name}
                      </span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setRenamingId(view.id);
                      setRenameDraft(view.name);
                    }}
                    title="Rename"
                    style={{
                      width: 24, height: 24, display: "inline-flex", alignItems: "center", justifyContent: "center",
                      border: "none", background: "transparent", color: "#94a3b8", cursor: "pointer", borderRadius: 5,
                    }}
                  >
                    <Pencil size={12} />
                  </button>
                  <button
                    type="button"
                    onClick={() => void setDefault(view.id)}
                    title={view.is_default ? "Already default" : "Set as default"}
                    style={{
                      width: 24, height: 24, display: "inline-flex", alignItems: "center", justifyContent: "center",
                      border: "none", background: "transparent",
                      color: view.is_default ? "#9ace3d" : "#94a3b8",
                      cursor: "pointer", borderRadius: 5,
                    }}
                  >
                    {view.is_default ? <Check size={12} /> : <Star size={12} />}
                  </button>
                  <button
                    type="button"
                    onClick={() => void deleteView(view.id)}
                    title="Delete view"
                    style={{
                      width: 24, height: 24, display: "inline-flex", alignItems: "center", justifyContent: "center",
                      border: "none", background: "transparent", color: "#dc2626", cursor: "pointer", borderRadius: 5,
                    }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

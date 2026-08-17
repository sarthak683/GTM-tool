import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Database, ExternalLink, FileCode2, Plus } from "lucide-react";
import { dataRoomApi } from "../lib/api";
import type { DataRoomCategory, DataRoomItem } from "../types";
import AddItemForm from "./data-room/AddItemForm";
import ItemsGrid from "./data-room/ItemsGrid";
import PreviewModal from "./data-room/PreviewModal";

// The Sales Lifecycle SOP is a *static document* served by the frontend nginx,
// not a database row. For it to exist in production it must be committed to
// frontend/public/data-room/index.html, which Vite copies into dist/ and the
// Docker build bakes into the image. Editing it anywhere else — a local copy, a
// file hand-copied into a running pod — cannot survive a deploy and will never
// reach users.
const DATA_ROOM_PATH = "/data-room/index.html";

type LifecycleStatus = "checking" | "ok" | "missing";

const ACCENT = "#4d7c0f";
const BORDER = "#e3e9f2";
const TEXT = "#142335";
const MUTED = "#68788d";

type TabKey = "sales_lifecycle" | DataRoomCategory;

const TABS: { key: TabKey; label: string }[] = [
  { key: "sales_lifecycle", label: "Sales Lifecycle" },
  { key: "documentation", label: "Documentation" },
  { key: "decks", label: "Decks" },
  { key: "videos", label: "Videos" },
  { key: "demo_recordings", label: "Demo Recordings" },
];

const TAB_DESCRIPTIONS: Record<TabKey, string> = {
  sales_lifecycle: "Shared sales lifecycle SOP.",
  documentation: "SOPs, guides and reference docs.",
  decks: "Pitch and sales decks.",
  videos: "Product demo videos.",
  demo_recordings: "Client call recordings.",
};

const isCategory = (key: TabKey): key is DataRoomCategory => key !== "sales_lifecycle";

export default function DataRoomPage() {
  const hostedUrl = useMemo(() => DATA_ROOM_PATH, []);
  const [activeTab, setActiveTab] = useState<TabKey>("sales_lifecycle");
  const [lifecycleStatus, setLifecycleStatus] = useState<LifecycleStatus>("checking");

  // Probe the document before rendering the iframe. Two failure modes to catch:
  //   1. A real 404 (nginx now returns this when the document is not in the image).
  //   2. HTTP 200 carrying the CRM's own index.html — what the SPA fallback
  //      returns on any host still running the old nginx config, and what the
  //      Vite dev server returns locally. Rendering that would load the entire
  //      app inside the iframe, which reads as "the document is stale" rather
  //      than "the document was never deployed".
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(DATA_ROOM_PATH, { cache: "no-store" });
        if (cancelled) return;
        if (!res.ok) {
          setLifecycleStatus("missing");
          return;
        }
        const body = await res.text();
        if (cancelled) return;
        const isAppShell = body.includes('<div id="root">');
        setLifecycleStatus(isAppShell ? "missing" : "ok");
      } catch {
        if (!cancelled) setLifecycleStatus("missing");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const [itemsByCategory, setItemsByCategory] = useState<Record<DataRoomCategory, DataRoomItem[]>>({
    documentation: [],
    decks: [],
    videos: [],
    demo_recordings: [],
  });
  const [loadingCategory, setLoadingCategory] = useState<DataRoomCategory | null>(null);
  const [errorByCategory, setErrorByCategory] = useState<Record<DataRoomCategory, string | null>>({
    documentation: null,
    decks: null,
    videos: null,
    demo_recordings: null,
  });

  const [previewItem, setPreviewItem] = useState<DataRoomItem | null>(null);
  const [addingCategory, setAddingCategory] = useState<DataRoomCategory | null>(null);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadCategory = useCallback(async (category: DataRoomCategory) => {
    setLoadingCategory(category);
    setErrorByCategory((prev) => ({ ...prev, [category]: null }));
    try {
      const items = await dataRoomApi.list(category);
      setItemsByCategory((prev) => ({ ...prev, [category]: items }));
    } catch (e) {
      setErrorByCategory((prev) => ({
        ...prev,
        [category]: e instanceof Error ? e.message : "Failed to load files.",
      }));
    } finally {
      setLoadingCategory((prev) => (prev === category ? null : prev));
    }
  }, []);

  const handleTabClick = (key: TabKey) => {
    setActiveTab(key);
    setDeleteError(null);
    if (isCategory(key)) {
      void loadCategory(key);
    }
  };

  const activeCategory = isCategory(activeTab) ? activeTab : null;
  const activeItems = activeCategory ? itemsByCategory[activeCategory] : [];
  const activeError = activeCategory ? errorByCategory[activeCategory] : null;
  const activeLoading = activeCategory === loadingCategory;

  const handleSaved = useCallback(() => {
    if (activeCategory) void loadCategory(activeCategory);
  }, [activeCategory, loadCategory]);

  const handleDelete = useCallback(
    async (item: DataRoomItem) => {
      if (!activeCategory) return;
      setDeletingId(item.id);
      setDeleteError(null);
      try {
        await dataRoomApi.remove(item.id);
        setItemsByCategory((prev) => ({
          ...prev,
          [activeCategory]: prev[activeCategory].filter((i) => i.id !== item.id),
        }));
        setPreviewItem((p) => (p && p.id === item.id ? null : p));
      } catch (e) {
        setDeleteError(e instanceof Error ? e.message : "Failed to delete file.");
      } finally {
        setDeletingId(null);
      }
    },
    [activeCategory],
  );

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", width: "100%" }}>
      {/* Small eyebrow only — the shell topbar owns page-title rendering.
          (/data-room currently falls back to the generic topbar title, so this
          stays as the page identity until PAGE_META covers the route.) */}
      <div style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 11, fontWeight: 700, color: ACCENT, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 12 }}>
        <Database size={13} /> Data Room
      </div>

      <div className="crm-panel" style={{ overflow: "hidden" }}>
        {/* Tab bar */}
        <div style={{ display: "flex", gap: 2, padding: "0 8px", borderBottom: `1px solid ${BORDER}`, background: "#f8fbff", overflowX: "auto" }}>
          {TABS.map((tab) => {
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => handleTabClick(tab.key)}
                style={{
                  padding: "11px 14px",
                  fontSize: 13,
                  fontWeight: 700,
                  color: active ? ACCENT : MUTED,
                  background: "transparent",
                  border: "none",
                  borderBottom: active ? `2px solid ${ACCENT}` : "2px solid transparent",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        {activeTab === "sales_lifecycle" ? (
          <div>
            <div style={{ padding: "12px 16px", borderBottom: `1px solid ${BORDER}`, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <FileCode2 size={16} color="#175089" />
                <span style={{ fontSize: 12.5, color: MUTED }}>{TAB_DESCRIPTIONS.sales_lifecycle}</span>
              </div>
              {lifecycleStatus === "ok" ? (
                <a
                  href={hostedUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="crm-button soft"
                  style={{ display: "inline-flex", alignItems: "center", gap: 8, textDecoration: "none", padding: "0 12px" }}
                >
                  <ExternalLink size={14} /> Open in new tab
                </a>
              ) : null}
            </div>
            <div style={{ minHeight: "68vh", background: "#f8fbff" }}>
              {lifecycleStatus === "ok" ? (
                <iframe
                  title="Data Room"
                  src={hostedUrl}
                  style={{ width: "100%", height: "68vh", border: 0, display: "block", background: "#fff" }}
                />
              ) : lifecycleStatus === "checking" ? (
                <div style={{ padding: "48px 16px", textAlign: "center", fontSize: 13, color: MUTED }}>
                  Loading the sales lifecycle document…
                </div>
              ) : (
                <div style={{ padding: "48px 24px", maxWidth: 640, margin: "0 auto", textAlign: "center" }}>
                  <AlertTriangle size={26} color="#b45309" style={{ marginBottom: 12 }} />
                  <div style={{ fontSize: 15, fontWeight: 700, color: TEXT, marginBottom: 8 }}>
                    The sales lifecycle document is not deployed.
                  </div>
                  <div style={{ fontSize: 13, color: MUTED, lineHeight: 1.6 }}>
                    This tab renders <code>{DATA_ROOM_PATH}</code>, a static file that ships inside
                    the frontend image. No such file is present in the current build, so nothing can
                    be shown here — and any edit made to a copy elsewhere will not appear until the
                    document is committed to <code>frontend/public/data-room/index.html</code> and
                    the frontend is rebuilt and redeployed.
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : activeCategory ? (
          <div style={{ padding: 16 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
              <div style={{ fontSize: 12.5, color: MUTED, fontWeight: 600 }}>
                {TAB_DESCRIPTIONS[activeCategory]}{" "}
                <span style={{ color: TEXT }}>· {activeItems.length} file{activeItems.length === 1 ? "" : "s"}</span>
              </div>
              <button
                onClick={() => setAddingCategory(activeCategory)}
                className="crm-button primary"
                style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "0 14px" }}
              >
                <Plus size={15} /> Add File
              </button>
            </div>
            <ItemsGrid
              category={activeCategory}
              items={activeItems}
              loading={activeLoading}
              error={activeError}
              onOpen={setPreviewItem}
              onRetry={() => void loadCategory(activeCategory)}
              onDelete={(item) => void handleDelete(item)}
              deletingId={deletingId}
              deleteError={deleteError}
            />
          </div>
        ) : null}
      </div>

      {previewItem ? (
        <PreviewModal item={previewItem} onClose={() => setPreviewItem(null)} />
      ) : null}

      {addingCategory ? (
        <AddItemForm
          category={addingCategory}
          onClose={() => setAddingCategory(null)}
          onSaved={handleSaved}
        />
      ) : null}
    </div>
  );
}

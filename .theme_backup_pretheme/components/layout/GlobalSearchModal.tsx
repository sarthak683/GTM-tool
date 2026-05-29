import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BriefcaseBusiness,
  Building2,
  Calendar,
  CheckSquare,
  CornerDownLeft,
  FileText,
  Loader2,
  Radar,
  Search,
  Settings,
  TrendingUp,
  Users,
  X,
} from "lucide-react";
import { globalSearchApi } from "../../lib/api";
import type { GlobalSearchItem, GlobalSearchSection } from "../../types";

type QuickAction = {
  id: string;
  title: string;
  subtitle: string;
  meta: string;
  link: string;
  keywords: string;
  icon: "pipeline" | "accounts" | "prospecting" | "analytics" | "meetings" | "tasks" | "settings" | "knowledge";
};

const QUICK_ACTIONS: QuickAction[] = [
  {
    id: "pipeline",
    title: "Open Pipeline",
    subtitle: "Manage stages, forecast movement, and active opportunities.",
    meta: "Quick Actions",
    link: "/pipeline",
    keywords: "pipeline deals forecast revenue board",
    icon: "pipeline",
  },
  {
    id: "accounts",
    title: "Open Account Sourcing",
    subtitle: "Import, score, and prioritize target accounts.",
    meta: "Quick Actions",
    link: "/account-sourcing",
    keywords: "accounts sourcing companies target icp",
    icon: "accounts",
  },
  {
    id: "prospecting",
    title: "Open Prospecting",
    subtitle: "Search prospects, ownership, personas, and outreach readiness.",
    meta: "Quick Actions",
    link: "/prospecting",
    keywords: "prospects prospecting contacts outreach personas",
    icon: "prospecting",
  },
  {
    id: "analytics",
    title: "Open Sales Analytics",
    subtitle: "Review activity, forecast, and pipeline quality.",
    meta: "Quick Actions",
    link: "/sales-analytics",
    keywords: "analytics dashboard forecast activity reports",
    icon: "analytics",
  },
  {
    id: "meetings",
    title: "Open Meetings",
    subtitle: "See upcoming customer meetings and prep work.",
    meta: "Quick Actions",
    link: "/meetings",
    keywords: "meetings calendar pre meeting assistance",
    icon: "meetings",
  },
  {
    id: "tasks",
    title: "Open Tasks",
    subtitle: "Work Beacon recommendations and manual follow-ups.",
    meta: "Quick Actions",
    link: "/tasks",
    keywords: "tasks to do next actions queue",
    icon: "tasks",
  },
  {
    id: "settings",
    title: "Open Settings",
    subtitle: "Configure stages, syncs, and shared workspace rules.",
    meta: "Quick Actions",
    link: "/settings",
    keywords: "settings configuration integrations stages",
    icon: "settings",
  },
];

function getQuickActionIcon(icon: QuickAction["icon"]) {
  switch (icon) {
    case "pipeline":
      return <BriefcaseBusiness size={15} />;
    case "accounts":
      return <Building2 size={15} />;
    case "prospecting":
      return <Radar size={15} />;
    case "analytics":
      return <TrendingUp size={15} />;
    case "meetings":
      return <Calendar size={15} />;
    case "tasks":
      return <CheckSquare size={15} />;
    case "knowledge":
      return <FileText size={15} />;
    case "settings":
      return <Settings size={15} />;
  }
}

// Map a backend result `kind` to an icon + tint so each result reads at a
// glance (a prospect looks different from a deal or a knowledge doc).
function getResultVisual(kind: string): { icon: ReactNode; bg: string; fg: string } {
  const k = (kind || "").toLowerCase();
  if (k.includes("compan") || k.includes("account")) return { icon: <Building2 size={15} />, bg: "#eef4ff", fg: "#3555c4" };
  if (k.includes("contact") || k.includes("prospect") || k.includes("person")) return { icon: <Users size={15} />, bg: "#eef9f1", fg: "#1f8f55" };
  if (k.includes("deal") || k.includes("opportunit")) return { icon: <BriefcaseBusiness size={15} />, bg: "#fff1ea", fg: "#d2541f" };
  if (k.includes("meeting") || k.includes("event") || k.includes("calendar")) return { icon: <Calendar size={15} />, bg: "#f3efff", fg: "#6d28d9" };
  if (k.includes("task")) return { icon: <CheckSquare size={15} />, bg: "#fff7e6", fg: "#b45309" };
  if (k.includes("knowledge") || k.includes("doc") || k.includes("article")) return { icon: <FileText size={15} />, bg: "#eef3f8", fg: "#475569" };
  return { icon: <Search size={15} />, bg: "#eef3f8", fg: "#5d7086" };
}

type PaletteEntry =
  | { type: "quick"; item: QuickAction }
  | { type: "search"; item: GlobalSearchItem; section: string };

export default function GlobalSearchModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<GlobalSearchSection[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setDebouncedQuery("");
      setResults([]);
      setActiveIndex(0);
      return;
    }
    const handle = window.setTimeout(() => inputRef.current?.focus(), 40);
    return () => window.clearTimeout(handle);
  }, [open]);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(query.trim()), 120);
    return () => window.clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    if (!open || !debouncedQuery) {
      setLoading(false);
      setResults([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    globalSearchApi.search(debouncedQuery)
      .then((response) => {
        if (!cancelled) setResults(response.sections);
      })
      .catch(() => {
        if (!cancelled) setResults([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, debouncedQuery]);

  const filteredQuickActions = useMemo(() => {
    if (!debouncedQuery) return QUICK_ACTIONS;
    const needle = debouncedQuery.toLowerCase();
    return QUICK_ACTIONS.filter((item) =>
      `${item.title} ${item.subtitle} ${item.keywords}`.toLowerCase().includes(needle),
    );
  }, [debouncedQuery]);

  const flatEntries = useMemo<PaletteEntry[]>(() => {
    const items: PaletteEntry[] = filteredQuickActions.map((item) => ({ type: "quick", item }));
    for (const section of results) {
      for (const item of section.items) {
        items.push({ type: "search", item, section: section.label });
      }
    }
    return items;
  }, [filteredQuickActions, results]);

  useEffect(() => {
    setActiveIndex(0);
  }, [debouncedQuery, results]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (!flatEntries.length) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((current) => (current + 1) % flatEntries.length);
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((current) => (current - 1 + flatEntries.length) % flatEntries.length);
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const current = flatEntries[activeIndex];
        if (!current) return;
        if (current.type === "quick") {
          navigate(current.item.link);
        } else {
          navigate(current.item.link);
        }
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, flatEntries, activeIndex, navigate, onClose]);

  // Keep the keyboard-highlighted row visible as the rep arrows through a long
  // result list.
  useEffect(() => {
    if (!open) return;
    const el = document.querySelector<HTMLElement>(`[data-palette-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  if (!open) return null;

  let cursor = -1;
  const nextIsActive = () => {
    cursor += 1;
    return cursor === activeIndex;
  };

  // Shared row renderer. Calling nextIsActive() here (in render order) keeps the
  // cursor aligned with flatEntries, so keyboard nav + this UI stay in sync.
  const renderRow = (opts: {
    key: string;
    title: string;
    detail?: string;
    badge?: string;
    icon: ReactNode;
    iconBg: string;
    iconFg: string;
    accent: string;
    tint: string;
    onSelect: () => void;
  }) => {
    const isActive = nextIsActive();
    const idx = cursor;
    return (
      <button
        key={opts.key}
        type="button"
        data-palette-index={idx}
        onMouseEnter={() => setActiveIndex(idx)}
        onClick={() => {
          opts.onSelect();
          onClose();
        }}
        style={{
          position: "relative",
          width: "100%",
          border: "1px solid transparent",
          background: isActive ? opts.tint : "transparent",
          borderRadius: 12,
          padding: "9px 12px 9px 14px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          cursor: "pointer",
          textAlign: "left",
          transition: "background 120ms ease",
        }}
      >
        {/* accent bar on the active row */}
        <span style={{ position: "absolute", left: 4, top: "50%", transform: "translateY(-50%)", width: 3, height: isActive ? 22 : 0, borderRadius: 999, background: opts.accent, transition: "height 120ms ease" }} />
        <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
          <div style={{ width: 34, height: 34, borderRadius: 10, background: isActive ? opts.accent : opts.iconBg, color: isActive ? "#fff" : opts.iconFg, display: "grid", placeItems: "center", flexShrink: 0, transition: "background 120ms ease, color 120ms ease" }}>
            {opts.icon}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13.5, fontWeight: 700, color: "#1c2c3e", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{opts.title}</div>
            {opts.detail && (
              <div style={{ marginTop: 1, fontSize: 12, color: "#75879b", lineHeight: 1.45, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{opts.detail}</div>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {opts.badge && (
            <span style={{ fontSize: 10, fontWeight: 700, color: "#8a99ad", background: "#f1f4f9", border: "1px solid #e6ecf4", borderRadius: 999, padding: "2px 8px", textTransform: "uppercase", letterSpacing: "0.04em" }}>{opts.badge}</span>
          )}
          {isActive ? (
            <span style={{ fontSize: 11, fontWeight: 800, color: opts.accent, background: "#fff", border: `1px solid ${opts.accent}33`, borderRadius: 7, padding: "2px 7px", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <CornerDownLeft size={11} /> Enter
            </span>
          ) : (
            <ArrowRight size={15} color="#b4c1d1" />
          )}
        </div>
      </button>
    );
  };

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: "rgba(8, 15, 31, 0.5)", backdropFilter: "blur(10px)", zIndex: 80 }}
      />
      <div style={{ position: "fixed", inset: 0, zIndex: 81, display: "grid", placeItems: "start center", padding: "11vh 16px 16px" }}>
        <div
          style={{
            width: "min(720px, 100%)",
            maxHeight: "76vh",
            display: "flex",
            flexDirection: "column",
            borderRadius: 20,
            border: "1px solid #e6ecf4",
            background: "#ffffff",
            boxShadow: "0 28px 80px rgba(13,23,42,0.32)",
            overflow: "hidden",
          }}
        >
          {/* SEARCH HEADER */}
          <div style={{ padding: "14px 16px", borderBottom: "1px solid #eef2f7" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, height: 50, borderRadius: 14, background: "#f5f8fc", border: "1px solid #e7edf5", padding: "0 12px" }}>
              <Search size={18} style={{ color: "#7c8ca0", flexShrink: 0 }} />
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search prospects, accounts, deals, meetings, tasks…"
                style={{
                  flex: 1,
                  height: "100%",
                  border: "none",
                  background: "transparent",
                  fontSize: 15.5,
                  fontWeight: 600,
                  color: "#1c2c3e",
                  outline: "none",
                  minWidth: 0,
                }}
              />
              {query && (
                <button
                  type="button"
                  onClick={() => { setQuery(""); inputRef.current?.focus(); }}
                  aria-label="Clear search"
                  style={{ width: 26, height: 26, borderRadius: 8, border: "none", background: "#e7edf5", color: "#6c7f94", display: "grid", placeItems: "center", cursor: "pointer", flexShrink: 0 }}
                >
                  <X size={14} />
                </button>
              )}
              <button
                type="button"
                onClick={onClose}
                aria-label="Close quick search"
                style={{ fontSize: 11, fontWeight: 800, color: "#7c8ca0", background: "#fff", border: "1px solid #e0e7f0", borderRadius: 8, padding: "4px 9px", cursor: "pointer", flexShrink: 0 }}
              >
                ESC
              </button>
            </div>
          </div>

          {/* RESULTS */}
          <div style={{ flex: 1, overflowY: "auto", padding: "12px 12px 6px", display: "grid", gap: 14, gridAutoRows: "min-content" }}>
            {filteredQuickActions.length > 0 && (
              <div style={{ display: "grid", gap: 4 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "0 10px 2px" }}>
                  <span style={{ fontSize: 10.5, fontWeight: 800, color: "#8294a8", textTransform: "uppercase", letterSpacing: "0.12em" }}>
                    {debouncedQuery ? "Jump to" : "Quick actions"}
                  </span>
                </div>
                {filteredQuickActions.map((item) =>
                  renderRow({
                    key: item.id,
                    title: item.title,
                    detail: item.subtitle,
                    icon: getQuickActionIcon(item.icon),
                    iconBg: "#eef4ff",
                    iconFg: "#3555c4",
                    accent: "#ff6b35",
                    tint: "#fff4ed",
                    onSelect: () => navigate(item.link),
                  }),
                )}
              </div>
            )}

            {loading && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, color: "#6f8095", fontSize: 13, padding: "6px 12px" }}>
                <Loader2 size={16} className="animate-spin" />
                Searching Beacon…
              </div>
            )}

            {!loading && results.map((section) => (
              <div key={section.key} style={{ display: "grid", gap: 4 }}>
                <span style={{ fontSize: 10.5, fontWeight: 800, color: "#8294a8", textTransform: "uppercase", letterSpacing: "0.12em", padding: "0 10px 2px" }}>
                  {section.label}
                </span>
                {section.items.map((item) => {
                  const visual = getResultVisual(item.kind);
                  return renderRow({
                    key: `${section.key}-${item.id}`,
                    title: item.title,
                    detail: [item.subtitle, item.meta].filter(Boolean).join(" • ") || undefined,
                    icon: visual.icon,
                    iconBg: visual.bg,
                    iconFg: visual.fg,
                    accent: "#2354d8",
                    tint: "#eef4ff",
                    onSelect: () => navigate(item.link),
                  });
                })}
              </div>
            ))}

            {!loading && debouncedQuery && results.length === 0 && filteredQuickActions.length === 0 && (
              <div style={{ borderRadius: 16, border: "1px dashed #dbe4ef", background: "#fbfcff", padding: 28, display: "grid", gap: 8, justifyItems: "center", textAlign: "center" }}>
                <div style={{ width: 44, height: 44, borderRadius: 12, background: "#eef3f8", display: "grid", placeItems: "center" }}>
                  <Search size={20} color="#8da0b6" />
                </div>
                <div style={{ fontSize: 14.5, fontWeight: 800, color: "#223547" }}>No matches for “{debouncedQuery}”</div>
                <div style={{ fontSize: 12.5, color: "#708297", maxWidth: 420, lineHeight: 1.7 }}>
                  Try a company name, prospect name, email, deal name, task keyword, or knowledge-base topic.
                </div>
              </div>
            )}
          </div>

          {/* KEYBOARD LEGEND */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "9px 16px", borderTop: "1px solid #eef2f7", background: "#fbfcfe" }}>
            <div style={{ display: "inline-flex", alignItems: "center", gap: 14, fontSize: 11, color: "#8294a8", fontWeight: 600 }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <kbd style={legendKbd}><ArrowUp size={11} /></kbd>
                <kbd style={legendKbd}><ArrowDown size={11} /></kbd>
                navigate
              </span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <kbd style={legendKbd}><CornerDownLeft size={11} /></kbd>
                open
              </span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <kbd style={legendKbd}>esc</kbd>
                close
              </span>
            </div>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "#8294a8", fontWeight: 700 }}>
              <kbd style={legendKbd}>⌘</kbd><kbd style={legendKbd}>K</kbd>
            </span>
          </div>
        </div>
      </div>
    </>
  );
}

const legendKbd: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 18,
  height: 18,
  padding: "0 5px",
  borderRadius: 6,
  background: "#fff",
  border: "1px solid #e0e7f0",
  boxShadow: "0 1px 0 #e0e7f0",
  fontSize: 10,
  fontWeight: 700,
  color: "#6c7f94",
  fontFamily: "inherit",
};

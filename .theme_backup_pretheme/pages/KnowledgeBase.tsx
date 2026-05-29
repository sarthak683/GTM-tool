import { useEffect, useState, useRef, type CSSProperties } from "react";
import {
  BookOpen,
  Upload,
  FileText,
  Trash2,
  Search,
  X,
  ChevronDown,
  Eye,
  Filter,
  Plus,
} from "lucide-react";
import { resourcesApi } from "../lib/api";
import type { SalesResource, Paginated } from "../types";

// ── Constants ────────────────────────────────────────────────────────────────

const CATEGORY_LABELS: Record<string, string> = {
  roi_template: "ROI Template",
  case_study: "Case Study",
  competitive_intel: "Competitive Intel",
  product_info: "Product Info",
  pricing: "Pricing Guide",
  objection_handling: "Objection Handling",
  email_template: "Email Template",
  playbook: "Sales Playbook",
  other: "Other",
};

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  roi_template: { bg: "#eef7ee", text: "#2d7a2d", border: "#b8deb8" },
  case_study: { bg: "#eef2ff", text: "#3b5bdb", border: "#bac8ff" },
  competitive_intel: { bg: "#fff4e6", text: "#d9480f", border: "#ffd8a8" },
  product_info: { bg: "#f3f0ff", text: "#7048e8", border: "#d0bfff" },
  pricing: { bg: "#e6fcf5", text: "#087f5b", border: "#96f2d7" },
  objection_handling: { bg: "#fff5f5", text: "#c92a2a", border: "#ffc9c9" },
  email_template: { bg: "#e7f5ff", text: "#1971c2", border: "#a5d8ff" },
  playbook: { bg: "#fff9db", text: "#e67700", border: "#ffe066" },
  other: { bg: "#f1f3f5", text: "#495057", border: "#ced4da" },
};

const MODULE_LABELS: Record<string, string> = {
  pre_meeting: "Pre-Meeting Intel",
  outreach: "Outreach",
  demo_strategy: "Demo Strategy",
  account_sourcing: "Account Sourcing",
  custom_demo: "Custom Demo",
  prospecting: "Prospecting",
};

const ALL_CATEGORIES = Object.keys(CATEGORY_LABELS);
const ALL_MODULES = Object.keys(MODULE_LABELS);

// ── Styles ───────────────────────────────────────────────────────────────────

const s: Record<string, CSSProperties> = {
  page: { display: "flex", flexDirection: "column", gap: 20, padding: "8px 2px 18px" },
  panel: {
    background: "#ffffff",
    border: "1px solid #e2eaf3",
    borderRadius: 16,
    boxShadow: "0 8px 28px rgba(18, 44, 70, 0.06)",
  },
  header: { padding: 24, display: "flex", flexDirection: "column", gap: 10 },
  title: { margin: 0, fontSize: 28, fontWeight: 800, color: "#25384d", letterSpacing: "-0.02em" },
  subtitle: { margin: 0, color: "#607589", fontSize: 14, lineHeight: 1.6, maxWidth: 700 },
  toolbar: {
    padding: "12px 20px",
    display: "flex",
    alignItems: "center",
    gap: 10,
    flexWrap: "wrap",
    borderBottom: "1px solid #edf2f8",
  },
  searchBox: {
    flex: 1,
    minWidth: 200,
    display: "flex",
    alignItems: "center",
    gap: 8,
    background: "#f5f8fc",
    border: "1px solid #e2eaf3",
    borderRadius: 10,
    padding: "7px 12px",
  },
  searchInput: {
    flex: 1,
    border: "none",
    background: "transparent",
    outline: "none",
    fontSize: 13,
    color: "#2b3f55",
  },
  select: {
    padding: "7px 10px",
    borderRadius: 10,
    border: "1px solid #e2eaf3",
    background: "#f5f8fc",
    fontSize: 13,
    color: "#2b3f55",
    cursor: "pointer",
  },
  primaryBtn: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "8px 16px",
    borderRadius: 10,
    border: "none",
    background: "#ff6b35",
    color: "white",
    fontWeight: 700,
    fontSize: 13,
    cursor: "pointer",
  },
  ghostBtn: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "7px 12px",
    borderRadius: 10,
    border: "1px solid #e2eaf3",
    background: "white",
    color: "#2b3f55",
    fontWeight: 600,
    fontSize: 13,
    cursor: "pointer",
  },
  card: {
    padding: "16px 20px",
    borderBottom: "1px solid #edf2f8",
    display: "flex",
    gap: 14,
    alignItems: "flex-start",
  },
  cardBody: { flex: 1, minWidth: 0 },
  cardTitle: { margin: 0, fontSize: 15, fontWeight: 700, color: "#2b3f55" },
  cardMeta: { margin: "4px 0 0", fontSize: 12, color: "#6a7c8f", lineHeight: 1.55 },
  badge: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 999,
    fontSize: 10,
    fontWeight: 700,
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
    marginRight: 6,
  },
  modulePill: {
    display: "inline-block",
    padding: "2px 7px",
    borderRadius: 999,
    fontSize: 10,
    fontWeight: 600,
    background: "#f0f4f8",
    color: "#4a6178",
    border: "1px solid #e2eaf3",
    marginRight: 4,
    marginTop: 4,
  },
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.35)",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    zIndex: 999,
  },
  modal: {
    background: "white",
    borderRadius: 16,
    width: "100%",
    maxWidth: 580,
    maxHeight: "90vh",
    overflow: "auto",
    boxShadow: "0 20px 60px rgba(0,0,0,0.2)",
  },
  modalHeader: {
    padding: "18px 24px",
    borderBottom: "1px solid #edf2f8",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  modalBody: { padding: "20px 24px", display: "flex", flexDirection: "column", gap: 16 },
  fieldLabel: { display: "block", fontSize: 12, fontWeight: 700, color: "#4a6178", marginBottom: 5 },
  fieldInput: {
    width: "100%",
    padding: "8px 12px",
    borderRadius: 10,
    border: "1px solid #e2eaf3",
    fontSize: 13,
    color: "#2b3f55",
    boxSizing: "border-box" as const,
  },
  textarea: {
    width: "100%",
    padding: "8px 12px",
    borderRadius: 10,
    border: "1px solid #e2eaf3",
    fontSize: 13,
    color: "#2b3f55",
    boxSizing: "border-box" as const,
    minHeight: 120,
    resize: "vertical" as const,
    fontFamily: "inherit",
  },
  dropZone: {
    border: "2px dashed #d0d8e4",
    borderRadius: 12,
    padding: "24px 16px",
    textAlign: "center" as const,
    cursor: "pointer",
    transition: "border-color 200ms",
  },
  checkGroup: { display: "flex", flexWrap: "wrap" as const, gap: 8 },
  checkItem: {
    display: "flex",
    alignItems: "center",
    gap: 5,
    fontSize: 12,
    color: "#4a6178",
    cursor: "pointer",
  },
  previewPanel: {
    padding: "16px 20px",
    background: "#f9fbfe",
    borderRadius: 10,
    border: "1px solid #edf2f8",
    maxHeight: 300,
    overflow: "auto",
    whiteSpace: "pre-wrap" as const,
    fontSize: 12,
    color: "#3a4f64",
    lineHeight: 1.6,
  },
  empty: { padding: "40px 20px", textAlign: "center" as const },
};

// ── Component ────────────────────────────────────────────────────────────────

export default function KnowledgeBase() {
  const [resources, setResources] = useState<SalesResource[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [searchQ, setSearchQ] = useState("");
  const [filterCat, setFilterCat] = useState("");
  const [filterMod, setFilterMod] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Upload form state
  const [mode, setMode] = useState<"file" | "text">("file");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("product_info");
  const [description, setDescription] = useState("");
  const [pastedContent, setPastedContent] = useState("");
  const [selectedModules, setSelectedModules] = useState<string[]>(ALL_MODULES);
  const [tags, setTags] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => {
    setLoading(true);
    resourcesApi
      .list(0, 100, filterCat || undefined, filterMod || undefined, searchQ || undefined)
      .then((res) => {
        setResources(res.items);
        setTotal(res.total);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, [filterCat, filterMod]);

  // Debounced search
  useEffect(() => {
    const t = setTimeout(load, 400);
    return () => clearTimeout(t);
  }, [searchQ]);

  const resetForm = () => {
    setFile(null);
    setTitle("");
    setCategory("product_info");
    setDescription("");
    setPastedContent("");
    setSelectedModules(ALL_MODULES);
    setTags("");
    setMode("file");
    setError("");
  };

  const handleSubmit = async () => {
    if (!title.trim()) return setError("Title is required");
    const tagList = tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    setSaving(true);
    setError("");
    try {
      if (mode === "file") {
        if (!file) return setError("Please select a file");
        await resourcesApi.upload(file, {
          title: title.trim(),
          category,
          description: description.trim() || undefined,
          tags: tagList,
          modules: selectedModules,
        });
      } else {
        if (!pastedContent.trim()) return setError("Content is required");
        await resourcesApi.create({
          title: title.trim(),
          category,
          description: description.trim() || undefined,
          content: pastedContent,
          tags: tagList,
          modules: selectedModules,
        });
      }
      setShowModal(false);
      resetForm();
      load();
    } catch (e: any) {
      setError(e?.message || "Upload failed");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this resource permanently?")) return;
    await resourcesApi.delete(id);
    load();
  };

  const toggleModule = (m: string) =>
    setSelectedModules((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]
    );

  const previewResource = resources.find((r) => r.id === previewId);

  return (
    <div style={s.page}>
      {/* Header */}
      <div style={{ ...s.panel, ...s.header }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <BookOpen size={22} style={{ color: "#ff6b35" }} />
          <h2 style={s.title}>Sales Knowledge Base</h2>
        </div>
        <p style={s.subtitle}>
          Upload ROI templates, case studies, competitive intel, product docs, and playbooks.
          These resources automatically feed into AI modules — pre-meeting intel, outreach, demo strategy, and more.
        </p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
          <span style={{ ...s.modulePill, background: "#fff4e6", color: "#d9480f", border: "1px solid #ffd8a8" }}>
            {total} resource{total !== 1 ? "s" : ""}
          </span>
          <span style={s.modulePill}>
            Feeds into {Object.keys(MODULE_LABELS).length} AI modules
          </span>
        </div>
      </div>

      {/* Toolbar */}
      <div style={{ ...s.panel, overflow: "hidden" }}>
        <div style={s.toolbar}>
          <div style={s.searchBox}>
            <Search size={14} style={{ color: "#96a7ba", flexShrink: 0 }} />
            <input
              style={s.searchInput}
              placeholder="Search resources..."
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
            />
            {searchQ && (
              <X
                size={14}
                style={{ color: "#96a7ba", cursor: "pointer" }}
                onClick={() => setSearchQ("")}
              />
            )}
          </div>

          <select style={s.select} value={filterCat} onChange={(e) => setFilterCat(e.target.value)}>
            <option value="">All Categories</option>
            {ALL_CATEGORIES.map((c) => (
              <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>
            ))}
          </select>

          <select style={s.select} value={filterMod} onChange={(e) => setFilterMod(e.target.value)}>
            <option value="">All Modules</option>
            {ALL_MODULES.map((m) => (
              <option key={m} value={m}>{MODULE_LABELS[m]}</option>
            ))}
          </select>

          <button style={s.primaryBtn} onClick={() => { resetForm(); setShowModal(true); }}>
            <Plus size={14} /> Add Resource
          </button>
        </div>

        {/* Resource list */}
        {loading ? (
          <div style={{ padding: "36px 20px", textAlign: "center", color: "#96a7ba", fontSize: 14 }}>
            Loading resources...
          </div>
        ) : resources.length === 0 ? (
          <div style={s.empty}>
            <BookOpen size={32} style={{ color: "#d0d8e4", margin: "0 auto 12px" }} />
            <p style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#2b3f55" }}>No resources yet</p>
            <p style={{ margin: "6px 0 16px", fontSize: 13, color: "#6a7c8f" }}>
              Upload your first ROI template, case study, or competitive doc to power AI across all modules.
            </p>
            <button style={s.primaryBtn} onClick={() => { resetForm(); setShowModal(true); }}>
              <Upload size={14} /> Upload First Resource
            </button>
          </div>
        ) : (
          resources.map((r) => {
            const catStyle = CATEGORY_COLORS[r.category] || CATEGORY_COLORS.other;
            return (
              <div key={r.id} style={s.card}>
                <div
                  style={{
                    width: 38,
                    height: 38,
                    borderRadius: 10,
                    background: catStyle.bg,
                    border: `1px solid ${catStyle.border}`,
                    display: "grid",
                    placeItems: "center",
                    flexShrink: 0,
                  }}
                >
                  <FileText size={16} style={{ color: catStyle.text }} />
                </div>
                <div style={s.cardBody}>
                  <p style={s.cardTitle}>{r.title}</p>
                  {r.description && (
                    <p style={{ ...s.cardMeta, marginTop: 2 }}>{r.description}</p>
                  )}
                  <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4 }}>
                    <span style={{ ...s.badge, background: catStyle.bg, color: catStyle.text, border: `1px solid ${catStyle.border}` }}>
                      {CATEGORY_LABELS[r.category] || r.category}
                    </span>
                    {r.filename && (
                      <span style={{ ...s.badge, background: "#f1f3f5", color: "#868e96", border: "1px solid #dee2e6" }}>
                        {r.filename}
                      </span>
                    )}
                    {r.file_size && (
                      <span style={{ fontSize: 11, color: "#96a7ba" }}>
                        {(r.file_size / 1024).toFixed(0)} KB
                      </span>
                    )}
                  </div>
                  <div style={{ marginTop: 4 }}>
                    {r.modules.map((m) => (
                      <span key={m} style={s.modulePill}>{MODULE_LABELS[m] || m}</span>
                    ))}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button
                    style={{ ...s.ghostBtn, padding: "6px 8px" }}
                    title="Preview"
                    onClick={() => setPreviewId(previewId === r.id ? null : r.id)}
                  >
                    <Eye size={14} />
                  </button>
                  <button
                    style={{ ...s.ghostBtn, padding: "6px 8px", color: "#c92a2a", borderColor: "#ffc9c9" }}
                    title="Delete"
                    onClick={() => handleDelete(r.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            );
          })
        )}

        {/* Inline preview */}
        {previewResource && (
          <div style={{ padding: "0 20px 16px" }}>
            <div style={s.previewPanel}>
              <strong>Content preview — {previewResource.title}</strong>
              <br /><br />
              {previewResource.content.slice(0, 2000)}
              {previewResource.content.length > 2000 && "\n\n... (truncated)"}
            </div>
          </div>
        )}
      </div>

      {/* Upload / Create Modal */}
      {showModal && (
        <div style={s.overlay} onClick={() => setShowModal(false)}>
          <div style={s.modal} onClick={(e) => e.stopPropagation()}>
            <div style={s.modalHeader}>
              <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "#2b3f55" }}>Add Resource</h3>
              <button
                onClick={() => setShowModal(false)}
                style={{ background: "none", border: "none", cursor: "pointer", color: "#96a7ba" }}
              >
                <X size={18} />
              </button>
            </div>
            <div style={s.modalBody as CSSProperties}>
              {/* Mode toggle */}
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  style={mode === "file" ? s.primaryBtn : s.ghostBtn}
                  onClick={() => setMode("file")}
                >
                  <Upload size={13} /> Upload File
                </button>
                <button
                  style={mode === "text" ? s.primaryBtn : s.ghostBtn}
                  onClick={() => setMode("text")}
                >
                  <FileText size={13} /> Paste Text
                </button>
              </div>

              {/* Title */}
              <div>
                <label style={s.fieldLabel}>Title *</label>
                <input
                  style={s.fieldInput}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. Beacon ROI Calculator for Enterprise"
                />
              </div>

              {/* Category */}
              <div>
                <label style={s.fieldLabel}>Category *</label>
                <select style={{ ...s.fieldInput, cursor: "pointer" }} value={category} onChange={(e) => setCategory(e.target.value)}>
                  {ALL_CATEGORIES.map((c) => (
                    <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>
                  ))}
                </select>
              </div>

              {/* Description */}
              <div>
                <label style={s.fieldLabel}>Description (optional)</label>
                <input
                  style={s.fieldInput}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Brief description of this resource"
                />
              </div>

              {/* File upload or text paste */}
              {mode === "file" ? (
                <div>
                  <label style={s.fieldLabel}>File (PDF, DOCX, TXT, MD, CSV)</label>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".pdf,.docx,.doc,.txt,.md,.csv"
                    style={{ display: "none" }}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) {
                        setFile(f);
                        if (!title) setTitle(f.name.replace(/\.[^.]+$/, ""));
                      }
                    }}
                  />
                  <div
                    style={{
                      ...s.dropZone,
                      borderColor: file ? "#ff6b35" : "#d0d8e4",
                      background: file ? "#fff8f5" : "#fafbfd",
                    }}
                    onClick={() => fileRef.current?.click()}
                  >
                    {file ? (
                      <div style={{ fontSize: 13, color: "#2b3f55" }}>
                        <FileText size={20} style={{ color: "#ff6b35", margin: "0 auto 6px" }} />
                        <p style={{ margin: 0, fontWeight: 700 }}>{file.name}</p>
                        <p style={{ margin: "4px 0 0", fontSize: 12, color: "#6a7c8f" }}>
                          {(file.size / 1024).toFixed(0)} KB — click to change
                        </p>
                      </div>
                    ) : (
                      <div style={{ fontSize: 13, color: "#6a7c8f" }}>
                        <Upload size={20} style={{ color: "#96a7ba", margin: "0 auto 6px" }} />
                        <p style={{ margin: 0, fontWeight: 600 }}>Click to select a file</p>
                        <p style={{ margin: "4px 0 0", fontSize: 12 }}>PDF, DOCX, TXT, MD, CSV supported</p>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div>
                  <label style={s.fieldLabel}>Content *</label>
                  <textarea
                    style={s.textarea}
                    value={pastedContent}
                    onChange={(e) => setPastedContent(e.target.value)}
                    placeholder="Paste your resource content here (ROI framework, competitive talking points, case study text, etc.)"
                  />
                </div>
              )}

              {/* Target modules */}
              <div>
                <label style={s.fieldLabel}>AI Modules (where this resource feeds into)</label>
                <div style={s.checkGroup}>
                  {ALL_MODULES.map((m) => (
                    <label key={m} style={s.checkItem}>
                      <input
                        type="checkbox"
                        checked={selectedModules.includes(m)}
                        onChange={() => toggleModule(m)}
                      />
                      {MODULE_LABELS[m]}
                    </label>
                  ))}
                </div>
              </div>

              {/* Tags */}
              <div>
                <label style={s.fieldLabel}>Tags (comma-separated, optional)</label>
                <input
                  style={s.fieldInput}
                  value={tags}
                  onChange={(e) => setTags(e.target.value)}
                  placeholder="e.g. enterprise, hr-tech, roi"
                />
              </div>

              {/* Error */}
              {error && (
                <p style={{ margin: 0, fontSize: 13, color: "#c92a2a", fontWeight: 600 }}>{error}</p>
              )}

              {/* Submit */}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, paddingTop: 4 }}>
                <button style={s.ghostBtn} onClick={() => setShowModal(false)}>Cancel</button>
                <button
                  style={{ ...s.primaryBtn, opacity: saving ? 0.6 : 1 }}
                  onClick={handleSubmit}
                  disabled={saving}
                >
                  {saving ? "Saving..." : mode === "file" ? "Upload & Save" : "Save Resource"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

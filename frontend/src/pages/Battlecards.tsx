import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Plus, Search, Swords, RefreshCw, Pencil, Trash2, X } from "lucide-react";
import { battlecardsApi } from "../lib/api";
import type { Battlecard } from "../types";

const CATEGORIES = [
  { key: "all", label: "All" },
  { key: "objection", label: "Objections" },
  { key: "competitor", label: "Competitors" },
  { key: "tech_faq", label: "Tech FAQs" },
];

const CATEGORY_STYLE: Record<string, { bg: string; border: string; text: string }> = {
  objection: { bg: "#fff1ec", border: "#ffcbb8", text: "#b94a24" },
  competitor: { bg: "#eaf4ff", border: "#c7def8", text: "#2a5f8c" },
  tech_faq: { bg: "#e4fbf3", border: "#b8efd8", text: "#1b6f53" },
  pricing: { bg: "#fff3dd", border: "#f7dda4", text: "#86581a" },
  use_case: { bg: "#edf3f9", border: "#d7e1eb", text: "#546679" },
};

const styles: Record<string, CSSProperties> = {
  page: {
    display: "flex",
    flexDirection: "column",
    gap: 20,
    padding: "8px 2px 18px",
  },
  panel: {
    background: "#ffffff",
    border: "1px solid #e2eaf3",
    borderRadius: 16,
    boxShadow: "0 8px 28px rgba(18, 44, 70, 0.06)",
  },
  topPanel: {
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 14,
  },
  toolbar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    flexWrap: "wrap",
  },
  chip: {
    display: "inline-flex",
    alignItems: "center",
    padding: "7px 12px",
    borderRadius: 999,
    border: "1px solid #d8e4ef",
    background: "#f8fbff",
    color: "#38526b",
    fontSize: 13,
    fontWeight: 700,
  },
  buttonSoft: {
    border: "1px solid #d9e5f0",
    background: "#f5f9ff",
    color: "#45607a",
    borderRadius: 10,
    padding: "9px 14px",
    fontSize: 13,
    fontWeight: 700,
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    cursor: "pointer",
  },
  buttonPrimary: {
    border: "1px solid #ff6b35",
    background: "#ff6b35",
    color: "white",
    borderRadius: 10,
    padding: "9px 14px",
    fontSize: 13,
    fontWeight: 700,
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    cursor: "pointer",
  },
  inputWrap: {
    position: "relative",
    maxWidth: 560,
  },
  input: {
    height: 44,
    width: "100%",
    borderRadius: 10,
    border: "1px solid #d7e2ee",
    background: "white",
    padding: "0 12px 0 36px",
    fontSize: 14,
    color: "#25384d",
    boxSizing: "border-box",
  },
  tabs: {
    display: "flex",
    gap: 8,
    flexWrap: "wrap",
  },
  cardsGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))",
    gap: 16,
  },
  card: {
    background: "#ffffff",
    border: "1px solid #e2eaf3",
    borderRadius: 16,
    boxShadow: "0 8px 28px rgba(18, 44, 70, 0.06)",
    padding: 18,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  modalOverlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(16, 24, 32, 0.3)",
    zIndex: 40,
  },
  modalWrap: {
    position: "fixed",
    inset: 0,
    zIndex: 50,
    display: "grid",
    placeItems: "center",
    padding: 16,
  },
  modal: {
    width: "100%",
    maxWidth: 860,
    background: "#ffffff",
    border: "1px solid #e2eaf3",
    borderRadius: 16,
    boxShadow: "0 18px 54px rgba(20, 46, 72, 0.2)",
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  field: {
    height: 42,
    borderRadius: 10,
    border: "1px solid #d7e2ee",
    padding: "0 12px",
    fontSize: 14,
    color: "#25384d",
    width: "100%",
    boxSizing: "border-box",
  },
  textarea: {
    width: "100%",
    minHeight: 120,
    borderRadius: 10,
    border: "1px solid #d7e2ee",
    padding: 12,
    fontSize: 14,
    color: "#25384d",
    boxSizing: "border-box",
    resize: "vertical",
  },
};

export default function Battlecards() {
  const [cards, setCards] = useState<Battlecard[]>([]);
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Battlecard | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    category: "objection",
    title: "",
    trigger: "",
    response: "",
    competitor: "",
    tags: "",
  });

  const loadCards = async (activeCategory = category, query = search) => {
    setLoading(true);
    setError("");
    try {
      let data: Battlecard[];
      if (query.trim().length >= 2) {
        data = await battlecardsApi.search(query.trim());
      } else if (activeCategory === "all") {
        data = await battlecardsApi.list();
      } else {
        data = await battlecardsApi.list(activeCategory);
      }
      setCards(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load battlecards");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadCards(category, search);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [category, search]);

  useEffect(() => {
    loadCards("all", "");
  }, []);

  const openCreate = () => {
    setEditing(null);
    setForm({ category: "objection", title: "", trigger: "", response: "", competitor: "", tags: "" });
    setShowModal(true);
  };

  const openEdit = (card: Battlecard) => {
    setEditing(card);
    setForm({
      category: card.category,
      title: card.title,
      trigger: card.trigger,
      response: card.response,
      competitor: card.competitor ?? "",
      tags: card.tags ?? "",
    });
    setShowModal(true);
  };

  const handleSave = async () => {
    if (!form.title.trim() || !form.trigger.trim() || !form.response.trim()) {
      setError("Title, trigger, and response are required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload = {
        category: form.category,
        title: form.title.trim(),
        trigger: form.trigger.trim(),
        response: form.response.trim(),
        competitor: form.competitor.trim() || undefined,
        tags: form.tags.trim() || undefined,
      };
      if (editing) {
        await battlecardsApi.update(editing.id, payload);
      } else {
        await battlecardsApi.create(payload);
      }
      setShowModal(false);
      await loadCards();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save battlecard");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    setError("");
    try {
      await battlecardsApi.delete(id);
      await loadCards();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete battlecard");
    }
  };

  const handleSeed = async () => {
    setSeeding(true);
    setError("");
    try {
      await battlecardsApi.seed();
      await loadCards();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to seed battlecards");
    } finally {
      setSeeding(false);
    }
  };

  const resultsLabel = useMemo(() => {
    if (search.trim().length >= 2) return `Search results (${cards.length})`;
    return `${cards.length} cards`;
  }, [cards.length, search]);

  return (
    <div style={styles.page}>
      <div style={{ ...styles.panel, ...styles.topPanel }}>
        <div style={styles.toolbar}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={styles.chip}>{resultsLabel}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <button style={styles.buttonSoft} onClick={handleSeed} disabled={seeding}>
              {seeding ? <RefreshCw size={14} /> : <Swords size={14} />}
              {seeding ? "Seeding..." : "Seed Default Cards"}
            </button>
            <button style={styles.buttonPrimary} onClick={openCreate}>
              <Plus size={14} />
              Add Card
            </button>
          </div>
        </div>

        <div style={styles.inputWrap}>
          <Search
            size={14}
            style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "#8094a8" }}
          />
          <input
            style={styles.input}
            placeholder="Search trigger, title, tags"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div style={styles.tabs}>
          {CATEGORIES.map((tab) => {
            const active = category === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setCategory(tab.key)}
                style={{
                  height: 38,
                  padding: "0 14px",
                  borderRadius: 10,
                  border: active ? "1px solid #ffcbb8" : "1px solid #d7e2ee",
                  background: active ? "#fff1ec" : "white",
                  color: active ? "#b94a24" : "#4d6178",
                  fontSize: 13,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {loading ? (
        <div style={{ ...styles.panel, padding: "46px 20px", textAlign: "center", color: "#7a8ea4", fontSize: 14 }}>
          Loading battlecards...
        </div>
      ) : (
        <div style={styles.cardsGrid}>
          {cards.map((card) => {
            const tone = CATEGORY_STYLE[card.category] ?? CATEGORY_STYLE.use_case;
            return (
              <article key={card.id} style={styles.card}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      padding: "4px 8px",
                      borderRadius: 999,
                      border: `1px solid ${tone.border}`,
                      background: tone.bg,
                      color: tone.text,
                      fontSize: 11,
                      fontWeight: 700,
                      textTransform: "capitalize",
                    }}
                  >
                    {card.category.replace(/_/g, " ")}
                  </span>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <button
                      style={{
                        height: 30,
                        width: 30,
                        borderRadius: 8,
                        border: "1px solid #d8e3ee",
                        background: "white",
                        display: "grid",
                        placeItems: "center",
                        color: "#5f748b",
                        cursor: "pointer",
                      }}
                      onClick={() => openEdit(card)}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      style={{
                        height: 30,
                        width: 30,
                        borderRadius: 8,
                        border: "1px solid #ffd0c0",
                        background: "#fffaf8",
                        display: "grid",
                        placeItems: "center",
                        color: "#c2532d",
                        cursor: "pointer",
                      }}
                      onClick={() => handleDelete(card.id)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800, color: "#24364b" }}>{card.title}</h3>
                <p style={{ margin: 0, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#7a8ea4" }}>
                  Trigger
                </p>
                <p style={{ margin: 0, fontSize: 13, color: "#2f455d", lineHeight: 1.55 }}>{card.trigger}</p>
                <p style={{ margin: 0, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#7a8ea4" }}>
                  Response
                </p>
                <p style={{ margin: 0, fontSize: 13, color: "#2f455d", lineHeight: 1.6 }}>{card.response}</p>
              </article>
            );
          })}
          {cards.length === 0 && (
            <div style={{ ...styles.panel, padding: "42px 20px", textAlign: "center", color: "#6f8297" }}>
              No battlecards found.
            </div>
          )}
        </div>
      )}

      {error && <p style={{ margin: 0, fontSize: 12, color: "#b94a24", fontWeight: 700 }}>{error}</p>}

      {showModal && (
        <>
          <div style={styles.modalOverlay} onClick={() => setShowModal(false)} />
          <div style={styles.modalWrap}>
            <div style={styles.modal}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <h3 style={{ margin: 0, fontSize: 18, fontWeight: 800, color: "#25384d" }}>
                  {editing ? "Edit Battlecard" : "Add Battlecard"}
                </h3>
                <button
                  style={{ background: "transparent", border: "none", color: "#7a8ea4", cursor: "pointer" }}
                  onClick={() => setShowModal(false)}
                >
                  <X size={18} />
                </button>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
                <select
                  style={styles.field}
                  value={form.category}
                  onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
                >
                  <option value="objection">objection</option>
                  <option value="competitor">competitor</option>
                  <option value="tech_faq">tech_faq</option>
                  <option value="pricing">pricing</option>
                  <option value="use_case">use_case</option>
                </select>
                <input
                  style={styles.field}
                  placeholder="Title"
                  value={form.title}
                  onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                />
              </div>

              <input
                style={styles.field}
                placeholder="Trigger"
                value={form.trigger}
                onChange={(e) => setForm((f) => ({ ...f, trigger: e.target.value }))}
              />

              <textarea
                style={styles.textarea}
                placeholder="Response"
                value={form.response}
                onChange={(e) => setForm((f) => ({ ...f, response: e.target.value }))}
              />

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
                <input
                  style={styles.field}
                  placeholder="Competitor (optional)"
                  value={form.competitor}
                  onChange={(e) => setForm((f) => ({ ...f, competitor: e.target.value }))}
                />
                <input
                  style={styles.field}
                  placeholder="Tags comma-separated"
                  value={form.tags}
                  onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button style={styles.buttonSoft} onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button style={styles.buttonPrimary} onClick={handleSave} disabled={saving}>
                  {saving ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

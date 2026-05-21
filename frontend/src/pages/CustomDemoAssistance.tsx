import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Download,
  FileText,
  FileUp,
  Layers,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import {
  customDemoApi,
  type CustomDemo,
  type DemoBriefIn,
  type SceneIn,
} from "../lib/api";

type Tab = "upload" | "brief" | "editor";
type EditorScene = SceneIn & { id: number };

const EMPTY_SCENE = (): EditorScene => ({
  id: Date.now() + Math.floor(Math.random() * 1000),
  scene_title: "",
  beacon_steps: [""],
  client_screen: "",
  reveal_description: "",
});

const panelStyle: React.CSSProperties = {
  borderRadius: 18,
  border: "1px solid #d8e0f0",
  background: "#ffffff",
  boxShadow: "0 16px 34px rgba(23, 39, 67, 0.06)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  border: "1px solid #c9d5ea",
  borderRadius: 10,
  padding: "10px 12px",
  fontSize: 13,
  color: "#1f2f45",
  background: "#ffffff",
  outline: "none",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  letterSpacing: "0.07em",
  textTransform: "uppercase",
  color: "#5c6f8e",
  fontWeight: 700,
  marginBottom: 6,
};

export default function CustomDemoAssistance() {
  const [tab, setTab] = useState<Tab>("brief");
  const [demos, setDemos] = useState<CustomDemo[]>([]);
  const [demosLoading, setDemosLoading] = useState(true);
  const listPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const reloadDemos = useCallback(async () => {
    setDemosLoading(true);
    try {
      const list = await customDemoApi.list();
      setDemos(list);
    } finally {
      setDemosLoading(false);
    }
  }, []);

  useEffect(() => {
    reloadDemos();
  }, [reloadDemos]);

  const handleDemoCreated = (demo: CustomDemo) => {
    setDemos((prev) => [demo, ...prev]);
  };

  const handleDemoUpdated = (updated: CustomDemo) => {
    setDemos((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
  };

  const handleDemoDeleted = (id: string) => {
    setDemos((prev) => prev.filter((d) => d.id !== id));
  };

  const hasPendingDemos = demos.some((d) => d.status === "draft" || d.status === "generating");

  useEffect(() => {
    if (!hasPendingDemos) {
      if (listPollRef.current) {
        clearInterval(listPollRef.current);
        listPollRef.current = null;
      }
      return;
    }

    const refreshPending = async () => {
      if (document.hidden) return;
      try {
        const list = await customDemoApi.list();
        setDemos(list);
      } catch {
        // Ignore transient polling errors; manual refresh is still available.
      }
    };

    listPollRef.current = setInterval(refreshPending, 30000);

    return () => {
      if (listPollRef.current) {
        clearInterval(listPollRef.current);
        listPollRef.current = null;
      }
    };
  }, [hasPendingDemos]);

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <section
        style={{
          ...panelStyle,
          padding: 24,
          background:
            "linear-gradient(140deg, #f8fbff 0%, #ffffff 46%, #eef4ff 100%)",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: -60,
            right: -40,
            width: 260,
            height: 260,
            borderRadius: "50%",
            background: "radial-gradient(circle, rgba(255,132,78,0.2) 0%, rgba(255,132,78,0) 68%)",
            pointerEvents: "none",
          }}
        />
        <p style={{ fontSize: 11, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.12em", color: "#5c6f8e" }}>
          Custom Demo Assistance
        </p>
        <h2 style={{ marginTop: 10, fontSize: 30, lineHeight: 1.1, fontWeight: 900, color: "#1c2f49" }}>
          AI Demo Builder
        </h2>
        <p style={{ marginTop: 10, maxWidth: 860, fontSize: 14, color: "#4c6282", lineHeight: 1.6 }}>
          Build a custom interactive HTML demo for any client meeting. Upload a PDF guide, build a structured company brief,
          or define scenes manually. The module sends context to Claude and returns a ready-to-run interactive demo file.
        </p>

        <div
          style={{
            marginTop: 16,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
            gap: 10,
          }}
        >
          {[
            "1. Collect context (PDF or structured brief)",
            "2. Generate interactive HTML with Claude",
            "3. Preview, revise, and download",
          ].map((text) => (
            <div
              key={text}
              style={{
                border: "1px solid #d5deef",
                borderRadius: 12,
                background: "#ffffff",
                padding: "10px 12px",
                fontSize: 12,
                color: "#314a6f",
                fontWeight: 600,
              }}
            >
              {text}
            </div>
          ))}
        </div>
      </section>

      <section style={{ ...panelStyle, overflow: "hidden" }}>
        <div style={{ display: "flex", borderBottom: "1px solid #e2e8f5", overflowX: "auto" }}>
          {[
            { key: "brief", label: "Build Brief", icon: Wand2 },
            { key: "upload", label: "Upload Guide", icon: FileUp },
            { key: "editor", label: "Scene Editor", icon: Layers },
          ].map((tabItem) => {
            const active = tab === tabItem.key;
            const Icon = tabItem.icon;
            return (
              <button
                key={tabItem.key}
                onClick={() => setTab(tabItem.key as Tab)}
                style={{
                  border: "none",
                  background: active ? "#f8fbff" : "#ffffff",
                  color: active ? "#1f5ec8" : "#5c6f8e",
                  padding: "14px 18px",
                  fontSize: 13,
                  fontWeight: 700,
                  borderBottom: active ? "2px solid #1f5ec8" : "2px solid transparent",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                <Icon size={14} />
                {tabItem.label}
              </button>
            );
          })}
        </div>

        <div style={{ padding: 18 }}>
          {tab === "upload" && <UploadPath onCreated={handleDemoCreated} />}
          {tab === "brief" && <BriefPath onCreated={handleDemoCreated} />}
          {tab === "editor" && <EditorPath onCreated={handleDemoCreated} />}
        </div>
      </section>

      <section style={{ ...panelStyle, overflow: "hidden" }}>
        <div
          style={{
            padding: "14px 16px",
            borderBottom: "1px solid #e5eaf6",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            background: "#f9fbff",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Sparkles size={15} color="#1f5ec8" />
            <p style={{ fontSize: 14, color: "#203550", fontWeight: 800 }}>
              Generated Demos
              {!demosLoading && demos.length > 0 ? (
                <span
                  style={{
                    marginLeft: 8,
                    borderRadius: 999,
                    border: "1px solid #c6d2e7",
                    background: "#ffffff",
                    padding: "2px 8px",
                    fontSize: 11,
                    color: "#375070",
                    fontWeight: 700,
                  }}
                >
                  {demos.length}
                </span>
              ) : null}
            </p>
          </div>
          <button
            onClick={reloadDemos}
            style={{
              border: "1px solid #c5d1e6",
              background: "#ffffff",
              borderRadius: 8,
              color: "#415b7d",
              fontSize: 12,
              fontWeight: 700,
              padding: "7px 12px",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>

        {demosLoading ? (
          <div style={{ padding: 24, color: "#69809f", fontSize: 13 }}>Loading demos...</div>
        ) : demos.length === 0 ? (
          <div style={{ padding: 24, color: "#69809f", fontSize: 13 }}>
            No demos yet. Create your first interactive HTML demo above.
          </div>
        ) : (
          <div style={{ borderTop: "1px solid #eef2fa" }}>
            {demos.map((demo) => (
              <DemoRow
                key={demo.id}
                demo={demo}
                onUpdated={handleDemoUpdated}
                onDeleted={handleDemoDeleted}
                onCompleted={reloadDemos}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function UploadPath({ onCreated }: { onCreated: (d: CustomDemo) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [clientName, setClientName] = useState("");
  const [clientDomain, setClientDomain] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async () => {
    if (!file || !title.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      const demo = await customDemoApi.generateFromFile(file, title, clientName, clientDomain);
      onCreated(demo);
      setFile(null);
      setTitle("");
      setClientName("");
      setClientDomain("");
    } catch (e: any) {
      setError(e.message || "Upload failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: 14, maxWidth: 860 }}>
      <div
        onClick={() => fileRef.current?.click()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files[0];
          if (f) setFile(f);
        }}
        onDragOver={(e) => e.preventDefault()}
        style={{
          border: "2px dashed #c2d0e7",
          borderRadius: 14,
          background: file ? "#f2fff4" : "#f8fbff",
          padding: "30px 18px",
          textAlign: "center",
          cursor: "pointer",
        }}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx"
          style={{ display: "none" }}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <FileUp size={26} color="#46658d" style={{ marginBottom: 8 }} />
        <p style={{ fontSize: 14, fontWeight: 700, color: "#243d5f" }}>
          {file ? file.name : "Drop PDF or DOCX production guide"}
        </p>
        <p style={{ marginTop: 6, fontSize: 12, color: "#6e85a5" }}>
          Extracted content is sent to Claude to create the demo HTML.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        <Field label="Demo Title *">
          <input value={title} onChange={(e) => setTitle(e.target.value)} style={inputStyle} placeholder="Abrigo x Beacon Demo" />
        </Field>
        <Field label="Client Name">
          <input value={clientName} onChange={(e) => setClientName(e.target.value)} style={inputStyle} placeholder="Abrigo" />
        </Field>
        <Field label="Client Domain">
          <input value={clientDomain} onChange={(e) => setClientDomain(e.target.value)} style={inputStyle} placeholder="abrigo.com" />
        </Field>
      </div>

      {error ? <ErrorBox message={error} /> : null}

      <button
        onClick={handleSubmit}
        disabled={!file || !title.trim() || submitting}
        style={primaryButtonStyle(!file || !title.trim() || submitting)}
      >
        {submitting ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
        {submitting ? "Generating..." : "Generate Demo from PDF/DOCX"}
      </button>
    </div>
  );
}

function BriefPath({ onCreated }: { onCreated: (d: CustomDemo) => void }) {
  const [payload, setPayload] = useState<DemoBriefIn>({
    title: "",
    client_name: "",
    client_domain: "",
    industry: "",
    company_summary: "",
    audience: "",
    business_objectives: [""],
    demo_objectives: [""],
    workflow_overview: "",
    key_capabilities: [""],
    scenes_outline: [""],
    success_metrics: [""],
    constraints: [""],
    additional_context: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const setField = <K extends keyof DemoBriefIn>(key: K, value: DemoBriefIn[K]) => {
    setPayload((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = async () => {
    if (!payload.title?.trim() || !payload.company_summary?.trim() || !payload.workflow_overview?.trim()) return;
    setSubmitting(true);
    setError("");

    const clean = (items: string[] | undefined) => (items || []).map((x) => x.trim()).filter(Boolean);

    const body: DemoBriefIn = {
      ...payload,
      business_objectives: clean(payload.business_objectives),
      demo_objectives: clean(payload.demo_objectives),
      key_capabilities: clean(payload.key_capabilities),
      scenes_outline: clean(payload.scenes_outline),
      success_metrics: clean(payload.success_metrics),
      constraints: clean(payload.constraints),
    };

    try {
      const demo = await customDemoApi.generateFromBrief(body);
      onCreated(demo);
      setPayload({
        title: "",
        client_name: "",
        client_domain: "",
        industry: "",
        company_summary: "",
        audience: "",
        business_objectives: [""],
        demo_objectives: [""],
        workflow_overview: "",
        key_capabilities: [""],
        scenes_outline: [""],
        success_metrics: [""],
        constraints: [""],
        additional_context: "",
      });
    } catch (e: any) {
      setError(e.message || "Failed to generate demo from brief");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div
        style={{
          border: "1px solid #d5def0",
          borderRadius: 12,
          background: "#f8fbff",
          padding: "10px 12px",
          fontSize: 12,
          color: "#3e5a7f",
          lineHeight: 1.5,
        }}
      >
        Use this when you do not have a production PDF. Fill company details, workflow goals, and scene direction.
        The backend converts this into a guide and asks Claude to produce a complete interactive HTML demo.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        <Field label="Demo Title *">
          <input style={inputStyle} value={payload.title || ""} onChange={(e) => setField("title", e.target.value)} placeholder="Consumer Lending Automation" />
        </Field>
        <Field label="Client Name">
          <input style={inputStyle} value={payload.client_name || ""} onChange={(e) => setField("client_name", e.target.value)} placeholder="BlueStar Credit Union" />
        </Field>
        <Field label="Client Domain">
          <input style={inputStyle} value={payload.client_domain || ""} onChange={(e) => setField("client_domain", e.target.value)} placeholder="bluestarcu.com" />
        </Field>
        <Field label="Industry">
          <input style={inputStyle} value={payload.industry || ""} onChange={(e) => setField("industry", e.target.value)} placeholder="Financial Services" />
        </Field>
        <Field label="Audience">
          <input style={inputStyle} value={payload.audience || ""} onChange={(e) => setField("audience", e.target.value)} placeholder="CRO, Head of Lending, Ops Manager" />
        </Field>
      </div>

      <Field label="Company Summary *">
        <textarea style={inputStyle} rows={3} value={payload.company_summary} onChange={(e) => setField("company_summary", e.target.value)} placeholder="Who the company is, what they sell, who they serve, and context for this demo." />
      </Field>

      <Field label="Workflow Overview *">
        <textarea style={inputStyle} rows={3} value={payload.workflow_overview} onChange={(e) => setField("workflow_overview", e.target.value)} placeholder="End-to-end flow the demo should show from intake to final value realization." />
      </Field>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 }}>
        <TagListEditor label="Business Objectives" values={payload.business_objectives} onChange={(v) => setField("business_objectives", v)} />
        <TagListEditor label="Demo Objectives" values={payload.demo_objectives} onChange={(v) => setField("demo_objectives", v)} />
        <TagListEditor label="Key Capabilities" values={payload.key_capabilities} onChange={(v) => setField("key_capabilities", v)} />
        <TagListEditor label="Scenes Outline" values={payload.scenes_outline} onChange={(v) => setField("scenes_outline", v)} />
        <TagListEditor label="Success Metrics" values={payload.success_metrics} onChange={(v) => setField("success_metrics", v)} />
        <TagListEditor label="Constraints" values={payload.constraints} onChange={(v) => setField("constraints", v)} />
      </div>

      <Field label="Additional Context">
        <textarea
          style={inputStyle}
          rows={3}
          value={payload.additional_context || ""}
          onChange={(e) => setField("additional_context", e.target.value)}
          placeholder="Optional notes: must-have visuals, specific UI style direction, compliance notes, branding preferences."
        />
      </Field>

      {error ? <ErrorBox message={error} /> : null}

      <button
        onClick={handleSubmit}
        disabled={!payload.title?.trim() || !payload.company_summary?.trim() || !payload.workflow_overview?.trim() || submitting}
        style={primaryButtonStyle(!payload.title?.trim() || !payload.company_summary?.trim() || !payload.workflow_overview?.trim() || submitting)}
      >
        {submitting ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />}
        {submitting ? "Generating..." : "Generate Demo from Brief"}
      </button>
    </div>
  );
}

function EditorPath({ onCreated }: { onCreated: (d: CustomDemo) => void }) {
  const [title, setTitle] = useState("");
  const [clientName, setClientName] = useState("");
  const [clientDomain, setClientDomain] = useState("");
  const [scenes, setScenes] = useState<EditorScene[]>([EMPTY_SCENE()]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const addScene = () => setScenes((prev) => [...prev, EMPTY_SCENE()]);

  const updateScene = (id: number, patch: Partial<EditorScene>) => {
    setScenes((prev) => prev.map((scene) => (scene.id === id ? { ...scene, ...patch } : scene)));
  };

  const removeScene = (id: number) => {
    setScenes((prev) => prev.filter((scene) => scene.id !== id));
  };

  const handleSubmit = async () => {
    if (!title.trim() || scenes.some((s) => !s.scene_title.trim())) return;
    setSubmitting(true);
    setError("");
    try {
      const demo = await customDemoApi.generateFromEditor({
        title,
        client_name: clientName || undefined,
        client_domain: clientDomain || undefined,
        scenes: scenes.map((scene) => ({
          scene_title: scene.scene_title,
          beacon_steps: scene.beacon_steps.map((x) => x.trim()).filter(Boolean),
          client_screen: scene.client_screen,
          reveal_description: scene.reveal_description,
        })),
      });
      onCreated(demo);
      setTitle("");
      setClientName("");
      setClientDomain("");
      setScenes([EMPTY_SCENE()]);
    } catch (e: any) {
      setError(e.message || "Failed to generate demo from scene editor");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        <Field label="Demo Title *">
          <input style={inputStyle} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="AI Onboarding Showcase" />
        </Field>
        <Field label="Client Name">
          <input style={inputStyle} value={clientName} onChange={(e) => setClientName(e.target.value)} placeholder="Client name" />
        </Field>
        <Field label="Client Domain">
          <input style={inputStyle} value={clientDomain} onChange={(e) => setClientDomain(e.target.value)} placeholder="client.com" />
        </Field>
      </div>

      {scenes.map((scene, index) => (
        <SceneEditorCard
          key={scene.id}
          scene={scene}
          index={index}
          removable={scenes.length > 1}
          onRemove={() => removeScene(scene.id)}
          onUpdate={(patch) => updateScene(scene.id, patch)}
        />
      ))}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          onClick={addScene}
          style={{
            border: "1px solid #c3d0e5",
            background: "#ffffff",
            color: "#3f5a7b",
            borderRadius: 9,
            fontSize: 12,
            fontWeight: 700,
            padding: "8px 12px",
            display: "flex",
            alignItems: "center",
            gap: 6,
            cursor: "pointer",
          }}
        >
          <Plus size={13} />
          Add Scene
        </button>
      </div>

      {error ? <ErrorBox message={error} /> : null}

      <button
        onClick={handleSubmit}
        disabled={!title.trim() || scenes.some((s) => !s.scene_title.trim()) || submitting}
        style={primaryButtonStyle(!title.trim() || scenes.some((s) => !s.scene_title.trim()) || submitting)}
      >
        {submitting ? <Loader2 size={14} className="animate-spin" /> : <Layers size={14} />}
        {submitting ? "Generating..." : "Generate Demo from Scene Plan"}
      </button>
    </div>
  );
}

function SceneEditorCard({
  scene,
  index,
  onUpdate,
  onRemove,
  removable,
}: {
  scene: EditorScene;
  index: number;
  onUpdate: (patch: Partial<EditorScene>) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <section style={{ ...panelStyle, borderRadius: 12, borderColor: "#dce5f3", overflow: "hidden" }}>
      <div
        style={{
          background: "#f8fbff",
          borderBottom: "1px solid #e2e8f5",
          padding: "10px 12px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          cursor: "pointer",
        }}
        onClick={() => setCollapsed((c) => !c)}
      >
        <p style={{ fontSize: 13, fontWeight: 800, color: "#243d5f" }}>
          Scene {index + 1}
          {scene.scene_title ? ` - ${scene.scene_title}` : ""}
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {removable ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRemove();
              }}
              style={{ border: "none", background: "transparent", color: "#b63c3c", cursor: "pointer" }}
            >
              <Trash2 size={14} />
            </button>
          ) : null}
          {collapsed ? <ChevronDown size={15} color="#4d6485" /> : <ChevronUp size={15} color="#4d6485" />}
        </div>
      </div>

      {!collapsed ? (
        <div style={{ padding: 12, display: "grid", gap: 10 }}>
          <Field label="Scene Title *">
            <input
              style={inputStyle}
              value={scene.scene_title}
              onChange={(e) => onUpdate({ scene_title: e.target.value })}
              placeholder="Lead intake and qualification"
            />
          </Field>

          <Field label="Beacon Steps">
            <div style={{ display: "grid", gap: 6 }}>
              {scene.beacon_steps.map((step, i) => (
                <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    style={{ ...inputStyle, flex: 1 }}
                    value={step}
                    onChange={(e) => {
                      const next = [...scene.beacon_steps];
                      next[i] = e.target.value;
                      onUpdate({ beacon_steps: next });
                    }}
                    placeholder={`Step ${i + 1}`}
                  />
                  {scene.beacon_steps.length > 1 ? (
                    <button
                      onClick={() => {
                        onUpdate({ beacon_steps: scene.beacon_steps.filter((_, idx) => idx !== i) });
                      }}
                      style={{ border: "none", background: "transparent", color: "#b63c3c", cursor: "pointer" }}
                    >
                      <X size={13} />
                    </button>
                  ) : null}
                </div>
              ))}
              <button
                onClick={() => onUpdate({ beacon_steps: [...scene.beacon_steps, ""] })}
                style={{
                  alignSelf: "flex-start",
                  border: "none",
                  background: "transparent",
                  color: "#1f5ec8",
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                }}
              >
                <Plus size={12} />
                Add step
              </button>
            </div>
          </Field>

          <Field label="Client Screen Description">
            <textarea
              style={inputStyle}
              rows={2}
              value={scene.client_screen}
              onChange={(e) => onUpdate({ client_screen: e.target.value })}
              placeholder="What the client's system UI should show in this scene"
            />
          </Field>

          <Field label="Reveal Description">
            <textarea
              style={inputStyle}
              rows={2}
              value={scene.reveal_description}
              onChange={(e) => onUpdate({ reveal_description: e.target.value })}
              placeholder="What appears after Beacon completes the scene"
            />
          </Field>
        </div>
      ) : null}
    </section>
  );
}

function TagListEditor({ label, values, onChange }: { label: string; values: string[]; onChange: (next: string[]) => void }) {
  return (
    <div>
      <label style={labelStyle}>{label}</label>
      <div style={{ display: "grid", gap: 6 }}>
        {values.map((value, idx) => (
          <div key={`${label}-${idx}`} style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              style={{ ...inputStyle, flex: 1 }}
              value={value}
              onChange={(e) => {
                const next = [...values];
                next[idx] = e.target.value;
                onChange(next);
              }}
              placeholder={`${label} item ${idx + 1}`}
            />
            {values.length > 1 ? (
              <button
                onClick={() => onChange(values.filter((_, i) => i !== idx))}
                style={{ border: "none", background: "transparent", color: "#b63c3c", cursor: "pointer" }}
              >
                <X size={13} />
              </button>
            ) : null}
          </div>
        ))}
        <button
          onClick={() => onChange([...values, ""])}
          style={{
            border: "none",
            background: "transparent",
            color: "#1f5ec8",
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 4,
          }}
        >
          <Plus size={12} />
          Add item
        </button>
      </div>
    </div>
  );
}

function DemoRow({
  demo: initialDemo,
  onUpdated,
  onDeleted,
  onCompleted,
}: {
  demo: CustomDemo;
  onUpdated: (d: CustomDemo) => void;
  onDeleted: (id: string) => void;
  onCompleted: () => void;
}) {
  const [demo, setDemo] = useState(initialDemo);
  const [expanded, setExpanded] = useState(false);
  const [revising, setRevising] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [revisingNow, setRevisingNow] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setDemo(initialDemo);
  }, [initialDemo]);

  // ── Real per-demo status polling (4s) while generating ────────────
  useEffect(() => {
    if (demo.status !== "draft" && demo.status !== "generating") {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }

    const poll = async () => {
      if (document.hidden) return;
      try {
        const st = await customDemoApi.status(demo.id);
        setDemo((prev) => ({ ...prev, status: st.status, error_message: st.error_message }));
        if (st.status === "ready" || st.status === "error") {
          // Full reload so parent gets complete demo object (html_content etc.)
          onCompleted();
        }
      } catch {
        // Transient fetch error — ignore, will retry next tick
      }
    };

    pollRef.current = setInterval(poll, 4000);
    // Also fire immediately so we don't wait 4s for first update
    poll();

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [demo.id, demo.status]);

  // ── Elapsed timer ─────────────────────────────────────────────────
  useEffect(() => {
    if (demo.status === "draft" || demo.status === "generating") {
      setElapsedSec(0);
      elapsedRef.current = setInterval(() => {
        setElapsedSec((s) => s + 1);
      }, 1000);
    } else if (elapsedRef.current) {
      clearInterval(elapsedRef.current);
      elapsedRef.current = null;
    }

    return () => {
      if (elapsedRef.current) {
        clearInterval(elapsedRef.current);
        elapsedRef.current = null;
      }
    };
  }, [demo.id, demo.status]);

  // Parse real stage from backend error_message (format: "[stage:name] detail")
  const stageMatch = (demo.error_message || "").match(/\[stage:(\w+)\]\s*(.*)/);
  const stageName = stageMatch?.[1] || "init";
  const stageDetail = stageMatch?.[2] || "";

  const stageLabels: Record<string, string> = {
    init: "Starting generation pipeline",
    prepare_guide: "Preparing production guide",
    brand_scrape: "Fetching brand data",
    model_generation: "Generating HTML with Claude",
    validation: "Validating generated HTML",
  };

  const generationPhase = stageLabels[stageName] || stageDetail || "Processing";

  const durationHint =
    elapsedSec < 90
      ? "Typical completion is 30-120s depending on prompt size."
      : elapsedSec < 240
        ? "Still running — large guides with many scenes take longer."
        : "Taking longer than expected. If this persists, check server logs.";

  // Progress based on real stage, not just time
  const stageProgress: Record<string, number> = {
    init: 5, prepare_guide: 15, brand_scrape: 25, model_generation: 50, validation: 85,
  };
  const generationProgress = Math.min(
    92,
    Math.max(stageProgress[stageName] || 10, 10 + elapsedSec * 0.5),
  );

  const runRevision = async () => {
    if (!instruction.trim()) return;
    setRevisingNow(true);
    try {
      const updated = await customDemoApi.revise(demo.id, instruction);
      setDemo(updated);
      onUpdated(updated);
      setInstruction("");
      setRevising(false);
    } catch (e: any) {
      alert(e.message || "Failed to revise demo");
    } finally {
      setRevisingNow(false);
    }
  };

  const downloadHtml = async () => {
    try {
      const res = await fetch(customDemoApi.htmlUrl(demo.id));
      if (!res.ok) throw new Error("Download failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${demo.title}.html`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      alert(e.message || "Failed to download demo");
    }
  };

  const remove = async () => {
    if (!confirm(`Delete demo \"${demo.title}\"?`)) return;
    await customDemoApi.delete(demo.id);
    onDeleted(demo.id);
  };

  const statusConfig: Record<CustomDemo["status"], { fg: string; bg: string }> = {
    draft: { fg: "#486388", bg: "#eef3fb" },
    generating: { fg: "#9b5c1a", bg: "#fff4e8" },
    ready: { fg: "#1e7c44", bg: "#ebf9f0" },
    error: { fg: "#a53e3e", bg: "#fbeeee" },
  };

  return (
    <div style={{ borderBottom: "1px solid #edf2fa" }}>
      <button
        onClick={() => setExpanded((x) => !x)}
        style={{
          width: "100%",
          border: "none",
          background: "#ffffff",
          textAlign: "left",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "12px 16px",
          cursor: "pointer",
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontSize: 14, color: "#223b5c", fontWeight: 800, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{demo.title}</p>
          <p style={{ marginTop: 3, fontSize: 12, color: "#617898" }}>
            {demo.client_name ? `${demo.client_name} - ` : ""}
            {demo.creation_path.replace("_", " ")} - {new Date(demo.created_at).toLocaleDateString()}
            {(demo.status === "draft" || demo.status === "generating") ? ` - ${elapsedSec}s` : ""}
          </p>
        </div>

        <span
          style={{
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            fontWeight: 800,
            borderRadius: 999,
            padding: "5px 10px",
            color: statusConfig[demo.status].fg,
            background: statusConfig[demo.status].bg,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {demo.status === "generating" ? <Loader2 size={11} className="animate-spin" /> : null}
          {demo.status}
        </span>

        {demo.status === "ready" ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              downloadHtml();
            }}
            style={{
              border: "1px solid #c5d1e6",
              background: "#ffffff",
              borderRadius: 8,
              padding: "6px 8px",
              color: "#3d5678",
              display: "inline-flex",
              cursor: "pointer",
            }}
            title="Download HTML"
          >
            <Download size={13} />
          </button>
        ) : null}

        <button
          onClick={(e) => {
            e.stopPropagation();
            remove();
          }}
          style={{ border: "none", background: "transparent", color: "#b63c3c", cursor: "pointer", padding: 3 }}
          title="Delete"
        >
          <Trash2 size={14} />
        </button>

        {expanded ? <ChevronUp size={15} color="#4b6284" /> : <ChevronDown size={15} color="#4b6284" />}
      </button>

      {expanded ? (
        <div style={{ padding: "0 16px 14px 16px", display: "grid", gap: 10 }}>
          {demo.status === "ready" ? (
            <>
              <div style={{ border: "1px solid #d9e3f3", borderRadius: 10, overflow: "hidden", height: 460 }}>
                <iframe
                  src={customDemoApi.htmlUrl(demo.id)}
                  title={demo.title}
                  style={{ width: "100%", height: "100%", border: "none" }}
                  sandbox="allow-scripts allow-same-origin"
                />
              </div>

              {!revising ? (
                <button
                  onClick={() => setRevising(true)}
                  style={{
                    border: "1px solid #c5d1e6",
                    background: "#ffffff",
                    borderRadius: 8,
                    color: "#3f5b80",
                    fontSize: 12,
                    fontWeight: 700,
                    padding: "8px 10px",
                    width: "fit-content",
                    cursor: "pointer",
                  }}
                >
                  Revise with AI
                </button>
              ) : (
                <div style={{ display: "grid", gap: 8 }}>
                  <Field label="Revision Instruction">
                    <textarea
                      style={inputStyle}
                      rows={3}
                      value={instruction}
                      onChange={(e) => setInstruction(e.target.value)}
                      placeholder="Make this flow more concise for an executive audience and add a ROI summary scene."
                    />
                  </Field>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button onClick={runRevision} disabled={!instruction.trim() || revisingNow} style={primaryButtonStyle(!instruction.trim() || revisingNow)}>
                      {revisingNow ? <Loader2 size={14} className="animate-spin" /> : null}
                      {revisingNow ? "Applying revision..." : "Apply Revision"}
                    </button>
                    <button
                      onClick={() => {
                        setInstruction("");
                        setRevising(false);
                      }}
                      style={{
                        border: "1px solid #c5d1e6",
                        background: "#ffffff",
                        borderRadius: 8,
                        color: "#3f5b80",
                        fontSize: 12,
                        fontWeight: 700,
                        padding: "8px 10px",
                        cursor: "pointer",
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : null}

          {demo.status === "error" ? <ErrorBox message={demo.error_message || "Generation failed"} /> : null}

          {(demo.status === "draft" || demo.status === "generating") ? (
            <div style={{ display: "grid", gap: 8, border: "1px solid #dae4f4", background: "#f8fbff", borderRadius: 10, padding: "10px 12px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#607a9f" }}>
                <Loader2 size={14} className="animate-spin" />
                {generationPhase} ({elapsedSec}s)
              </div>
              <div style={{ height: 7, borderRadius: 999, background: "#dde7f6", overflow: "hidden" }}>
                <div
                  style={{
                    width: `${generationProgress}%`,
                    height: "100%",
                    borderRadius: 999,
                    background: "linear-gradient(90deg, #1f5ec8 0%, #5ea3ff 100%)",
                    transition: "width 0.8s ease",
                  }}
                />
              </div>
              <p style={{ margin: 0, fontSize: 11, color: "#6f86a7" }}>
                {stageDetail ? `Stage: ${stageDetail}` : durationHint}
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label style={labelStyle}>{label}</label>
      {children}
    </div>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div
      style={{
        border: "1px solid #f1c8c8",
        background: "#fff6f6",
        color: "#a94444",
        borderRadius: 10,
        padding: "9px 11px",
        fontSize: 12,
      }}
    >
      {message}
    </div>
  );
}

function primaryButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    border: "none",
    borderRadius: 9,
    padding: "9px 12px",
    fontSize: 12,
    fontWeight: 800,
    background: disabled ? "#d8e0ee" : "#1f5ec8",
    color: disabled ? "#6f85a6" : "#ffffff",
    width: "fit-content",
    cursor: disabled ? "not-allowed" : "pointer",
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
  };
}

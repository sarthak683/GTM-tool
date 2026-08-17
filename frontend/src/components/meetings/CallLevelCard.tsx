import { useState } from "react";
import { AlertTriangle, Check, RotateCcw, Users } from "lucide-react";
import { meetingsApi } from "../../lib/api/crm";
import type { CallLevel, Meeting } from "../../types";

/**
 * L1/L2/L3 call classification — Sales Lifecycle SOP stage 04.
 *
 * The SOP has the AE classify each upcoming client call at the prep call, from
 * the invite's attendee list, and that level sets how the call is actually run.
 * So this card does not just display a label: it shows the SOP's instructions
 * for the chosen level, which is the whole point of capturing it.
 *
 * The guidance text lives here rather than server-side because it is display
 * copy — the backend owns the classification RULE, the SOP document owns the
 * wording, and this renders it.
 */
const LEVEL_GUIDANCE: Record<CallLevel, { audience: string; run: string; next: string; tone: string }> = {
  L1: {
    audience: "Solo Director/VP of Implementation, Delivery or PS",
    run: "Deep discovery questions, high-level demo only, output glimpses only.",
    next: "Book a Demo Deep Dive with a larger audience.",
    tone: "#1d4ed8",
  },
  L2: {
    audience: "Larger group (2+ attendees)",
    run: "Moderate discovery, then a deep platform demo — output only.",
    next: "Book a technical deep dive with an exec looped in.",
    tone: "#c2410c",
  },
  L3: {
    audience: "SVP+ executives",
    run: "Light/embedded discovery, lead with brand vision, deep demo — output only.",
    next: "Book an L1-style discovery + demo, then loop in Operations.",
    tone: "#6d28d9",
  },
};

const LEVELS: CallLevel[] = ["L1", "L2", "L3"];

export default function CallLevelCard({
  meeting,
  onChange,
}: {
  meeting: Meeting;
  onChange: (updated: Meeting) => void;
}) {
  const [saving, setSaving] = useState<CallLevel | "reset" | null>(null);
  const [error, setError] = useState("");

  const suggestion = meeting.call_level_suggestion ?? null;
  const level = meeting.call_level ?? null;
  const isManual = meeting.call_level_source === "manual";

  // An internal meeting has no client audience to classify — say so plainly
  // rather than rendering an empty control the AE would wonder about.
  if (!level && suggestion && suggestion.level === null) {
    return (
      <div className="crm-panel" style={{ padding: 16, marginTop: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#16273d", marginBottom: 4 }}>Call level</div>
        <div style={{ fontSize: 12.5, color: "#7f8fa5" }}>{suggestion.rationale}</div>
      </div>
    );
  }

  const guidance = level ? LEVEL_GUIDANCE[level] : null;
  // Only warn while the AE hasn't decided — once they've classified it by hand,
  // the classifier's uncertainty is no longer the operative fact.
  const showConfidenceWarning =
    !isManual && suggestion?.confidence === "low" && !!suggestion?.level;
  // The classifier now reads the invite differently from the stored manual
  // value — worth surfacing, because it usually means someone joined or dropped.
  const disagrees =
    isManual && !!suggestion?.level && suggestion.level !== level;

  async function apply(next: CallLevel | null) {
    setSaving(next ?? "reset");
    setError("");
    try {
      onChange(await meetingsApi.setCallLevel(meeting.id, next));
    } catch (e) {
      // Surface it: a silent failure here leaves the AE believing the call is
      // classified when it isn't.
      setError(e instanceof Error ? e.message : "Could not save the call level");
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="crm-panel" style={{ padding: 16, marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 800, color: "#16273d" }}>Call level</span>
          {level ? (
            <span
              style={{
                fontSize: 12, fontWeight: 800, color: LEVEL_GUIDANCE[level].tone,
                background: "#f4f7fb", border: `1px solid ${LEVEL_GUIDANCE[level].tone}33`,
                borderRadius: 999, padding: "3px 10px",
              }}
            >{level}</span>
          ) : (
            <span style={{ fontSize: 12, color: "#7f8fa5" }}>Not classified</span>
          )}
          <span style={{ fontSize: 11, color: "#9aa7b8" }}>
            {isManual ? "set by an AE" : level ? "suggested from the invite" : ""}
          </span>
          {suggestion && suggestion.external_count > 0 && (
            <span style={{ fontSize: 11, color: "#9aa7b8", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <Users size={11} />
              {suggestion.external_count} external · {suggestion.titles_known} with a title on record
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {LEVELS.map((option) => (
            <button
              key={option}
              type="button"
              className="crm-button soft"
              disabled={saving !== null}
              onClick={() => apply(option)}
              title={`${LEVEL_GUIDANCE[option].audience} — ${LEVEL_GUIDANCE[option].run}`}
              style={
                option === level
                  ? { borderColor: LEVEL_GUIDANCE[option].tone, color: LEVEL_GUIDANCE[option].tone, fontWeight: 800 }
                  : undefined
              }
            >
              {option === level && <Check size={12} />} {option}
            </button>
          ))}
          {isManual && (
            <button
              type="button"
              className="crm-button soft"
              disabled={saving !== null}
              onClick={() => apply(null)}
              title="Clear the manual classification and go back to the automatic suggestion"
            >
              <RotateCcw size={12} /> Auto
            </button>
          )}
        </div>
      </div>

      {guidance && (
        <div style={{ marginTop: 12, display: "grid", gap: 4 }}>
          <div style={{ fontSize: 12.5, color: "#16273d" }}><b>Audience:</b> {guidance.audience}</div>
          <div style={{ fontSize: 12.5, color: "#16273d" }}><b>Run it as:</b> {guidance.run}</div>
          <div style={{ fontSize: 12.5, color: LEVEL_GUIDANCE[level!].tone, fontWeight: 600 }}>
            Mandatory next step: {guidance.next}
          </div>
        </div>
      )}

      {suggestion?.rationale && (
        <div style={{ marginTop: 10, fontSize: 12, color: "#7f8fa5" }}>{suggestion.rationale}</div>
      )}

      {showConfidenceWarning && (
        <div
          style={{
            marginTop: 10, display: "flex", gap: 8, alignItems: "flex-start",
            background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, padding: "8px 10px",
          }}
        >
          <AlertTriangle size={13} style={{ color: "#92400e", flexShrink: 0, marginTop: 2 }} />
          <span style={{ fontSize: 12, color: "#92400e" }}>
            Confirm this at the prep call — we don't have titles for everyone on the invite, so an
            SVP+ could be attending, which would make it L3.
          </span>
        </div>
      )}

      {disagrees && (
        <div style={{ marginTop: 10, fontSize: 12, color: "#92400e" }}>
          The invite now reads as <b>{suggestion?.level}</b>; your classification of <b>{level}</b> is
          being kept. Attendees may have changed since the prep call.
        </div>
      )}

      {error && <div style={{ marginTop: 10, fontSize: 12, color: "#b91c1c" }}>{error}</div>}
    </div>
  );
}

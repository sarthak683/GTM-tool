import { useState } from "react";
import { meetingsApi } from "../../lib/api";

type Props = {
  meetingId: string;
  externalSource?: string | null;
  hasRecording?: boolean;
  style?: React.CSSProperties;
  className?: string;
};

/**
 * Renders a link-styled button that fetches a fresh tldv download URL when
 * clicked, then opens it. tldv signs URLs with short-lived tokens, so we
 * deliberately never persist them — every click triggers a fresh fetch.
 *
 * Only shown for meetings that came from tldv.
 */
export default function TldvRecordingLink({
  meetingId,
  externalSource,
  hasRecording = true,
  style,
  className,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if ((externalSource || "").toLowerCase() !== "tldv" || !hasRecording) return null;

  async function handleClick(e: React.MouseEvent) {
    e.preventDefault();
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const { url } = await meetingsApi.getRecordingUrl(meetingId);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      else setError("No recording available");
    } catch (err: any) {
      setError(err?.message ?? "Could not fetch recording");
    } finally {
      setLoading(false);
    }
  }

  const label = loading ? "Opening..." : error ? `⚠ ${error}` : "tl;dv recording";
  return (
    <button
      type="button"
      onClick={handleClick}
      title={error ? "Click to retry" : "Fetch a fresh tl;dv download link"}
      disabled={loading}
      className={className}
      style={{
        background: "transparent",
        border: "none",
        padding: 0,
        fontSize: 11,
        color: error ? "#b94343" : "#7c3aed",
        fontWeight: 700,
        textDecoration: "none",
        cursor: loading ? "wait" : "pointer",
        textAlign: "left",
        ...style,
      }}
    >
      {label}
    </button>
  );
}

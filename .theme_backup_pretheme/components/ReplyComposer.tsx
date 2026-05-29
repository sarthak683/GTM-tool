import { useEffect, useState } from "react";
import { Loader2, Send, X } from "lucide-react";
import { personalEmailSyncApi } from "../lib/api";

export interface ReplyContext {
  // Threading context — when these are set the Gmail send keeps the new
  // message in the existing conversation.
  threadId?: string;
  inReplyTo?: string;     // RFC Message-ID
  references?: string;    // References header chain
  // Prefilled fields
  to: string;
  cc?: string;
  subject: string;        // typically "Re: ${original.subject}"
  quotedBody?: string;    // appended as a > quoted block under the rep's reply
  // Linkage
  dealId?: string;
  contactId?: string;
}

interface Props {
  open: boolean;
  ctx: ReplyContext | null;
  onClose: () => void;
  onSent?: () => void;
}

export default function ReplyComposer({ open, ctx, onClose, onSent }: Props) {
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && ctx) {
      setTo(ctx.to);
      setCc(ctx.cc ?? "");
      setSubject(ctx.subject);
      setBody(ctx.quotedBody ? `\n\n${ctx.quotedBody}` : "");
      setError(null);
    }
  }, [open, ctx]);

  if (!open || !ctx) return null;

  const handleSend = async () => {
    if (!to.trim() || !subject.trim() || !body.trim()) {
      setError("To, subject, and body are required.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      await personalEmailSyncApi.send({
        to: to.trim(),
        cc: cc.trim() || undefined,
        subject: subject.trim(),
        body,
        thread_id: ctx.threadId,
        in_reply_to: ctx.inReplyTo,
        references: ctx.references,
        deal_id: ctx.dealId,
        contact_id: ctx.contactId,
      });
      onSent?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed");
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      data-mobile-modal
      style={{
        position: "fixed", inset: 0, zIndex: 9500,
        background: "rgba(15, 39, 68, 0.4)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={() => !sending && onClose()}
    >
      <div
        data-mobile-modal-panel
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 620, maxWidth: "94vw", background: "#fff", borderRadius: 16,
          boxShadow: "0 24px 60px rgba(15, 39, 68, 0.25)",
        }}
      >
        <div data-mobile-modal-body style={{ padding: "20px 22px 0" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#0f2744" }}>
            {ctx.threadId ? "Reply" : "New email"}
          </h3>
          <button
            onClick={onClose}
            disabled={sending}
            style={{ border: "none", background: "transparent", cursor: "pointer", color: "#64748b" }}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <label style={{ fontSize: 12, fontWeight: 600, color: "#2c4a63", display: "block", marginBottom: 4 }}>To</label>
        <input
          value={to} onChange={(e) => setTo(e.target.value)}
          style={{ width: "100%", border: "1px solid #c8d9e8", borderRadius: 10, padding: "8px 12px", fontSize: 13, marginBottom: 10 }}
        />

        <label style={{ fontSize: 12, fontWeight: 600, color: "#2c4a63", display: "block", marginBottom: 4 }}>Cc</label>
        <input
          value={cc} onChange={(e) => setCc(e.target.value)}
          placeholder="comma-separated"
          style={{ width: "100%", border: "1px solid #c8d9e8", borderRadius: 10, padding: "8px 12px", fontSize: 13, marginBottom: 10 }}
        />

        <label style={{ fontSize: 12, fontWeight: 600, color: "#2c4a63", display: "block", marginBottom: 4 }}>Subject</label>
        <input
          value={subject} onChange={(e) => setSubject(e.target.value)}
          style={{ width: "100%", border: "1px solid #c8d9e8", borderRadius: 10, padding: "8px 12px", fontSize: 13, marginBottom: 10 }}
        />

        <label style={{ fontSize: 12, fontWeight: 600, color: "#2c4a63", display: "block", marginBottom: 4 }}>Message</label>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={12}
          style={{
            width: "100%", border: "1px solid #c8d9e8", borderRadius: 10,
            padding: "10px 12px", fontSize: 13, color: "#0f2744",
            fontFamily: "inherit", resize: "vertical", marginBottom: 12,
          }}
        />

        {error && (
          <div style={{ color: "#ef4444", fontSize: 12, marginBottom: 10 }}>{error}</div>
        )}
        </div>

        <div data-mobile-modal-footer style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, padding: "14px 22px", flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: "#7c86a6", flex: "1 1 200px", minWidth: 0 }}>
            Sends from your connected Gmail and lands in this contact's timeline immediately.
          </span>
          <div style={{ display: "flex", gap: 10 }}>
            <button
              onClick={onClose}
              disabled={sending}
              style={{
                padding: "11px 16px", minHeight: 44, borderRadius: 10, border: "1px solid #c8d9e8",
                background: "#fff", color: "#0f2744", fontSize: 13, fontWeight: 600,
                cursor: sending ? "not-allowed" : "pointer",
              }}
            >
              Cancel
            </button>
            <button
              onClick={() => void handleSend()}
              disabled={sending}
              style={{
                padding: "11px 18px", minHeight: 44, borderRadius: 10, border: "none",
                background: "linear-gradient(135deg, #0f2744, #175089)",
                color: "#fff", fontSize: 13, fontWeight: 700,
                display: "inline-flex", alignItems: "center", gap: 8,
                cursor: sending ? "not-allowed" : "pointer", opacity: sending ? 0.7 : 1,
              }}
            >
              {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

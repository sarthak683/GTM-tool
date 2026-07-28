import { DragEvent, KeyboardEvent, useRef, useState } from "react";


function levenshtein(a: string, b: string): number {
  const m = a.length, n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, (_, i) =>
    Array.from({ length: n + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0))
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
    }
  }
  return dp[m][n];
}

function correctCompanyNames(text: string, companies: string[]): string {
  if (!companies.length) return text;
  let result = text;
  const words = text.split(/\s+/);

  for (const company of companies) {
    const companyLower = company.toLowerCase();

    // Try matching 3, 2, and 1 word windows from the transcript
    for (let size = 3; size >= 1; size--) {
      for (let i = 0; i <= words.length - size; i++) {
        const chunk = words.slice(i, i + size).join(" ");
        const chunkLower = chunk.toLowerCase();

        // Skip if already correct
        if (chunkLower === companyLower) continue;

        const dist = levenshtein(chunkLower, companyLower);
        const maxLen = Math.max(chunkLower.length, companyLower.length);
        const similarity = 1 - dist / maxLen;

        // Match if similarity > 70% and chunk is at least 4 chars
        if (similarity > 0.7 && chunk.length >= 4) {
          result = result.replace(chunk, company);
          break;
        }
      }
    }
  }
  return result;
}

export interface ComposerImage {
  base64: string; // raw base64 (no data: prefix)
  mediaType: string; // e.g. "image/png"
  previewUrl: string; // object URL for thumbnail
  filename: string;
}

interface ZippyComposerProps {
  disabled?: boolean;
  onSubmit: (text: string, image?: ComposerImage | null) => void;
  companyNames?: string[];
}

// Auto-growing textarea + Enter-to-send (Shift+Enter for newline) — standard
// Copilot/ChatGPT feel. Resets height after send.
//
// Layout: a single rounded box with the textarea on top and the keyboard
// hint + send button in the action row below. Keeps the composer compact
// so the thread above gets most of the vertical space.
//
// Image attach: a paperclip button opens a hidden <input type=file>. Selected
// images are read as base64 and shown as a small removable thumbnail above
// the textarea. The image is only sent for the current turn — once dispatched
// to onSubmit the local state clears.
export function ZippyComposer({ disabled, onSubmit, companyNames }: ZippyComposerProps) {
  const [value, setValue] = useState("");
  const [attachedImage, setAttachedImage] = useState<ComposerImage | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const recognitionRef = useRef<any>(null);

  function handleChange(v: string) {
    setValue(v);
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      // Cap the textarea at ~8 lines (220px) so long pastes scroll internally
      // rather than pushing the composer off-screen. Floor at 64px so the box
      // always looks like a real input, not a one-line strip.
      el.style.height = `${Math.min(Math.max(el.scrollHeight, 80), 220)}px`;
    }
  }

  function submit() {
    const trimmed = value.trim();
    // Allow image-only sends (e.g. "here, read this profile") but require
    // either text or an image — pure empty submit is a no-op.
    if ((!trimmed && !attachedImage) || disabled) return;
    onSubmit(trimmed, attachedImage);
    setValue("");
    clearAttachedImage();
    const el = textareaRef.current;
    if (el) el.style.height = "auto";
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function handleFileSelected(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      // Silently ignore — the input already filters by accept="image/*"
      // but Safari sometimes lets other types slip through.
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // result looks like "data:image/png;base64,iVBORw0..." — strip prefix.
      const commaIdx = result.indexOf(",");
      const base64 = commaIdx >= 0 ? result.slice(commaIdx + 1) : result;
      const previewUrl = URL.createObjectURL(file);
      setAttachedImage({
        base64,
        mediaType: file.type,
        previewUrl,
        filename: file.name,
      });
    };
    reader.readAsDataURL(file);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.types.includes("Files")) setIsDragOver(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFileSelected(file);
  }

  function toggleMic() {
    const SpeechRecognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      alert(
        "Speech recognition is not supported in this browser. Please use Chrome or Edge.",
      );
      return;
    }

    if (isListening) {
      recognitionRef.current?.stop();
      setIsListening(false);
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: any) => {
      let transcript = "";
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      // Correct misheared company names, then route through handleChange
      // so the textarea auto-grows with the live transcript.
      const corrected = correctCompanyNames(transcript, companyNames ?? []);
      handleChange(corrected);
    };

    recognition.onerror = () => {
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognition;
    recognition.start();
    setIsListening(true);
  }

  function clearAttachedImage() {
    if (attachedImage) URL.revokeObjectURL(attachedImage.previewUrl);
    setAttachedImage(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div
      className="border-t border-stone-200 bg-white"
      style={{ padding: "14px 16px 18px" }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {attachedImage && (
        <div
          className="mb-2 flex items-center gap-2 rounded-md border border-stone-200 bg-stone-50"
          style={{ padding: "6px 8px" }}
        >
          <img
            src={attachedImage.previewUrl}
            alt={attachedImage.filename}
            className="rounded"
            style={{ width: 40, height: 40, objectFit: "cover" }}
          />
          <span
            className="flex-1 truncate text-stone-700"
            style={{ fontSize: 13 }}
          >
            {attachedImage.filename}
          </span>
          <button
            type="button"
            onClick={clearAttachedImage}
            className="rounded-full text-stone-500 hover:bg-stone-200 hover:text-stone-700"
            style={{ width: 22, height: 22, lineHeight: "20px", fontSize: 14 }}
            aria-label="Remove image"
          >
            ×
          </button>
        </div>
      )}

      {/* Single rounded box: textarea on top, icon toolbar below a divider. */}
      <div
        className="flex flex-col"
        style={{
          border: isDragOver ? "1.5px solid #7F77DD" : "1.5px solid #d1d5db",
          borderRadius: 16,
          overflow: "hidden",
          background: "white",
        }}
      >
        {/* Large centered mic button above the textarea. */}
        <div
          style={{
            padding: "20px",
            textAlign: "center",
            borderBottom: "0.5px solid var(--color-border-tertiary)",
          }}
        >
          <button
            type="button"
            onClick={toggleMic}
            disabled={disabled}
            aria-label={isListening ? "Stop recording" : "Start voice input"}
            style={{
              width: 72,
              height: 72,
              borderRadius: "50%",
              background: isListening ? "#ef4444" : "#7F77DD",
              border: "none",
              cursor: disabled ? "not-allowed" : "pointer",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              transition: "background 0.2s",
              opacity: disabled ? 0.5 : 1,
            }}
          >
            {isListening ? (
              <span
                style={{
                  width: 22,
                  height: 22,
                  background: "#fff",
                  borderRadius: 4,
                  display: "block",
                }}
              />
            ) : (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="#fff"
                strokeWidth={1.8}
                style={{ width: 28, height: 28 }}
              >
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" />
              </svg>
            )}
          </button>
        </div>

        <textarea
          ref={textareaRef}
          rows={2}
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask Zippy anything — grounded in your Drive + Beacon's shared knowledge base."
          className="w-full resize-none placeholder-gray-400"
          style={{
            fontSize: 14,
            lineHeight: 1.55,
            minHeight: 60,
            maxHeight: 220,
            padding: "14px 16px",
            border: "none",
            outline: "none",
            boxShadow: "none",
            background: "transparent",
            color: "var(--color-text-primary)",
            resize: "none",
          }}
          disabled={disabled}
        />
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={(e) => handleFileSelected(e.target.files?.[0])}
        />
        <div
          className="flex items-center justify-between"
          style={{
            borderTop: "1px solid #e5e7eb",
            padding: "8px 12px",
          }}
        >
          {/* Left side intentionally empty. */}
          <div />
          <div className="flex items-center" style={{ gap: 10 }}>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              className="inline-flex items-center justify-center text-gray-400 transition hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-40"
              style={{ width: 28, height: 28, background: "transparent", border: "none" }}
              aria-label="Attach image"
              title="Attach image (e.g. LinkedIn profile screenshot)"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ width: 20, height: 20 }}
              >
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={disabled || (!value.trim() && !attachedImage)}
              className="inline-flex items-center justify-center rounded-full text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              style={{ width: 32, height: 32, background: "#7F77DD", border: "none" }}
              aria-label="Send"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.2}
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ width: 16, height: 16 }}
              >
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

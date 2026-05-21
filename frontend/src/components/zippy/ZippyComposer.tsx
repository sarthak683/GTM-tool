import { DragEvent, KeyboardEvent, useRef, useState } from "react";

export interface ComposerImage {
  base64: string; // raw base64 (no data: prefix)
  mediaType: string; // e.g. "image/png"
  previewUrl: string; // object URL for thumbnail
  filename: string;
}

interface ZippyComposerProps {
  disabled?: boolean;
  onSubmit: (text: string, image?: ComposerImage | null) => void;
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
export function ZippyComposer({ disabled, onSubmit }: ZippyComposerProps) {
  const [value, setValue] = useState("");
  const [attachedImage, setAttachedImage] = useState<ComposerImage | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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

  function clearAttachedImage() {
    if (attachedImage) URL.revokeObjectURL(attachedImage.previewUrl);
    setAttachedImage(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div
      className="border-t border-stone-200 bg-stone-50/60"
      style={{ padding: "14px 16px 18px" }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div
        className={`flex flex-col rounded-xl border bg-white shadow-sm transition-colors ${
          isDragOver
            ? "border-violet-400 bg-violet-50/40"
            : "border-stone-200"
        }`}
        style={{ padding: "14px 16px", gap: 10 }}
      >
        {attachedImage && (
          <div
            className="flex items-center gap-2 rounded-md border border-stone-200 bg-stone-50"
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
        <textarea
          ref={textareaRef}
          rows={2}
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask Zippy anything — grounded in your Drive + Beacon's shared knowledge base."
          className="w-full resize-none bg-transparent text-stone-900 placeholder-stone-400 focus:outline-none"
          style={{
            fontSize: 15,
            lineHeight: 1.55,
            minHeight: 80,
            maxHeight: 220,
            padding: "8px 10px",
            boxShadow: "none",
            outline: "none",
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
        <div className="flex items-center justify-between">
          <div className="text-stone-500" style={{ fontSize: 12 }}>
            ⏎ send · Shift+⏎ newline · ⌘J toggle · Esc close
          </div>
          <div className="flex items-center" style={{ gap: 8 }}>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              className="inline-flex items-center justify-center rounded-lg border border-stone-200 bg-white text-stone-600 transition hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-40"
              style={{ width: 36, height: 36 }}
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
                style={{ width: 16, height: 16 }}
              >
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={disabled || (!value.trim() && !attachedImage)}
              className="inline-flex items-center justify-center rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-500 text-white shadow-sm transition hover:from-violet-700 hover:to-fuchsia-600 disabled:cursor-not-allowed disabled:opacity-40"
              style={{ width: 36, height: 36 }}
              aria-label="Send"
            >
              <svg viewBox="0 0 20 20" fill="currentColor" style={{ width: 16, height: 16 }}>
                <path d="M3.105 3.105a.5.5 0 01.55-.105l13 5.5a.5.5 0 010 .92l-13 5.5a.5.5 0 01-.682-.63l1.89-4.74L10 10 4.863 8.475 2.973 3.735a.5.5 0 01.132-.63z" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

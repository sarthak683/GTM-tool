import type { ReactNode } from "react";
import type { ZippyMessage } from "../../lib/api";

// Backend base URL — we concatenate it with the relative zippy_outputs URL
// the server returns so artifact links resolve against the API host, not the
// Vite dev server.
const API_BASE = import.meta.env.VITE_API_URL || "";

// Render a single chat turn. Matches the Beacon chatbot widget pattern:
//   - AI:   small avatar on the left + white card + timestamp at the bottom right
//   - User: dark bubble right-aligned + timestamp inside
// The markdown renderer is intentionally simple — we render bold / italic /
// code / links and split double-newlines into paragraphs.
export function ZippyMessageBubble({ message }: { message: ZippyMessage }) {
  const isUser = message.role === "user";
  const timestamp = formatClock(message.created_at);

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div
          className="max-w-[78%] rounded-2xl rounded-br-sm bg-stone-900 text-white shadow-sm"
          style={{ padding: "10px 14px", fontSize: 15, lineHeight: 1.55 }}
        >
          <AssistantContent content={message.content} />
          {timestamp && (
            <div
              className="text-right text-stone-300"
              style={{ marginTop: 4, fontSize: 12 }}
            >
              {timestamp}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start" style={{ gap: 10 }}>
      <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-600 to-fuchsia-500 text-white shadow-sm">
        <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5">
          <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />
        </svg>
      </div>
      <div
        className="min-w-0 max-w-[88%] flex-1 rounded-2xl rounded-tl-sm border border-stone-200 bg-white text-stone-800 shadow-sm"
        style={{ padding: "14px 18px", fontSize: 15, lineHeight: 1.65 }}
      >
        <AssistantContent content={message.content} />

        {message.artifacts && message.artifacts.length > 0 && (
          <div className="mt-3 flex w-full min-w-0 flex-col gap-2 border-t border-stone-100 pt-3">
            {message.artifacts.map((artifact) => (
              <a
                key={artifact.url}
                // Prefer the absolute Google Docs link when the doc has been
                // uploaded — that's the editable canonical artifact. Fall back
                // to the local /zippy_outputs/ path (which needs the API_BASE
                // prefix) only if upload failed. Without this guard, gluing
                // API_BASE in front of an absolute https:// URL produces a
                // mangled href that Chrome rejects as about:blank#blocked.
                href={artifact.drive_url || `${API_BASE}${artifact.url}`}
                target="_blank"
                rel="noreferrer"
                className="group rounded-lg border border-violet-200 bg-violet-50/60 transition hover:border-violet-400 hover:bg-violet-50"
                // Defensive inline layout — Tailwind classes alone weren't
                // holding the chip inside its parent on long filenames; the
                // file icon and "Open" link were spilling past the chip's
                // right edge. Inline `box-sizing: border-box` + explicit
                // `width: 100%` + `overflow: hidden` guarantee the chip
                // can never exceed its container regardless of how narrow
                // the assistant bubble gets.
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  width: "100%",
                  maxWidth: "100%",
                  minWidth: 0,
                  boxSizing: "border-box",
                  padding: "10px 14px",
                  overflow: "hidden",
                }}
              >
                <span
                  className="text-violet-600"
                  style={{
                    flex: "0 0 auto",
                    display: "inline-flex",
                    alignItems: "center",
                  }}
                >
                  <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
                    <path d="M4 4a2 2 0 012-2h5.586A2 2 0 0113 2.586L16.414 6A2 2 0 0117 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm7 0v3a1 1 0 001 1h3" />
                  </svg>
                </span>
                <div
                  style={{
                    flex: "1 1 auto",
                    minWidth: 0,
                    overflow: "hidden",
                  }}
                >
                  <div
                    className="font-medium text-violet-900"
                    style={{
                      fontSize: 14,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {artifact.filename}
                  </div>
                  <div
                    className="text-violet-700/80"
                    style={{
                      fontSize: 13,
                      marginTop: 2,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {artifact.summary}
                  </div>
                </div>
                <span
                  className="font-medium text-violet-600 group-hover:underline"
                  style={{
                    fontSize: 13,
                    flex: "0 0 auto",
                    whiteSpace: "nowrap",
                  }}
                >
                  Open
                </span>
              </a>
            ))}
          </div>
        )}

        {message.citations && message.citations.length > 0 && (
          <div className="mt-5 border-t border-stone-100 pt-3">
            <div
              className="font-semibold uppercase tracking-wide text-stone-500"
              style={{ fontSize: 12, marginBottom: 6, letterSpacing: 0.4 }}
            >
              Sources
            </div>
            <ul className="flex flex-col" style={{ gap: 4 }}>
              {message.citations.slice(0, 5).map((c) => (
                <li key={`${c.source_id}-${c.chunk_index}`} style={{ fontSize: 13 }}>
                  <a
                    href={c.drive_url || "#"}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-violet-700 hover:underline"
                    title={c.snippet}
                  >
                    <span>•</span>
                    <span className="truncate">{c.source_name}</span>
                    <span
                      className="rounded bg-violet-100 text-violet-700"
                      style={{ fontSize: 12, padding: "1px 6px" }}
                    >
                      {Math.round(c.score * 100)}%
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {timestamp && (
          <div
            className="text-right text-stone-400"
            style={{ marginTop: 6, fontSize: 12 }}
          >
            {timestamp}
          </div>
        )}
      </div>
    </div>
  );
}


function AssistantContent({ content }: { content: string }) {
  // Real markdown-ish rendering: paragraphs on blank lines, "- " collected
  // into proper <ul>, ### → bold heading, inline bold/italic/code/md-links.
  const blocks = parseBlocks(content);
  return (
    <div className="flex flex-col break-words" style={{ gap: 10 }}>
      {blocks.map((b, i) => {
        const isFirst = i === 0;
        if (b.type === "list") {
          return (
            <ul
              key={i}
              className="list-disc marker:text-stone-400"
              style={{ marginLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}
            >
              {b.items.map((line, j) => (
                <li key={j}>{renderInline(line)}</li>
              ))}
            </ul>
          );
        }
        if (b.type === "heading") {
          return (
            <p
              key={i}
              className="font-semibold text-stone-900"
              style={{ marginTop: isFirst ? 0 : 6, fontSize: 15.5 }}
            >
              {renderInline(b.text)}
            </p>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            {renderInline(b.text)}
          </p>
        );
      })}
    </div>
  );
}


type Block =
  | { type: "paragraph"; text: string }
  | { type: "list"; items: string[] }
  | { type: "heading"; text: string };


function parseBlocks(content: string): Block[] {
  // Split on blank lines, then within each block pull out bullet runs so a
  // lead-in sentence + bullet list (no blank line between them) still renders.
  const blocks: Block[] = [];
  const paragraphs = content.split(/\n{2,}/);
  for (const para of paragraphs) {
    const lines = para.split("\n");
    let buffer: string[] = [];
    let currentList: string[] = [];
    const flushBuffer = () => {
      if (buffer.length) {
        blocks.push({ type: "paragraph", text: buffer.join("\n") });
        buffer = [];
      }
    };
    const flushList = () => {
      if (currentList.length) {
        blocks.push({ type: "list", items: currentList });
        currentList = [];
      }
    };
    for (const line of lines) {
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      const heading = line.match(/^#{1,6}\s+(.*)$/);
      if (bullet) {
        flushBuffer();
        currentList.push(bullet[1]);
      } else if (heading) {
        flushBuffer();
        flushList();
        blocks.push({ type: "heading", text: heading[1] });
      } else {
        flushList();
        buffer.push(line);
      }
    }
    flushList();
    flushBuffer();
  }
  return blocks;
}


function renderInline(text: string): ReactNode[] {
  // Order matters: [text](url) before raw URLs so we don't double-match the
  // URL inside a markdown link.
  const tokens: ReactNode[] = [];
  const regex =
    /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|https?:\/\/[^\s)]+|\*[^*\n]+\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push(text.slice(lastIndex, match.index));
    }
    const raw = match[0];
    if (raw.startsWith("**")) {
      tokens.push(<strong key={`b-${key++}`}>{raw.slice(2, -2)}</strong>);
    } else if (raw.startsWith("`")) {
      tokens.push(
        <code
          key={`c-${key++}`}
          className="rounded bg-stone-100 text-stone-800"
          style={{ fontSize: 13.5, padding: "1px 5px" }}
        >
          {raw.slice(1, -1)}
        </code>,
      );
    } else if (raw.startsWith("[")) {
      // [label](url)
      const inner = raw.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (inner) {
        tokens.push(
          <a
            key={`ml-${key++}`}
            href={inner[2]}
            target="_blank"
            rel="noreferrer"
            className="text-violet-600 underline decoration-violet-300 underline-offset-2 hover:decoration-violet-500"
          >
            {inner[1]}
          </a>,
        );
      } else {
        tokens.push(raw);
      }
    } else if (raw.startsWith("http")) {
      // Strip trailing punctuation not meant to be part of the URL.
      const clean = raw.replace(/[),.;:]+$/, "");
      tokens.push(
        <a
          key={`a-${key++}`}
          href={clean}
          target="_blank"
          rel="noreferrer"
          className="break-all text-violet-600 underline-offset-2 hover:underline"
        >
          {clean}
        </a>,
      );
      if (clean.length < raw.length) {
        tokens.push(raw.slice(clean.length));
      }
    } else if (raw.startsWith("*")) {
      tokens.push(<em key={`i-${key++}`}>{raw.slice(1, -1)}</em>);
    }
    lastIndex = match.index + raw.length;
  }
  if (lastIndex < text.length) {
    tokens.push(text.slice(lastIndex));
  }
  return tokens;
}


// "03:17 PM" — local-time clock format, matches the Beacon widget.
function formatClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

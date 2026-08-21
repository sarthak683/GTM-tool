import { useEffect, useMemo, useState } from "react";
import { useEditor, EditorContent, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import DOMPurify from "dompurify";
import {
  Bold, Italic, List, ListOrdered, Link as LinkIcon,
  Code, Quote, Strikethrough, Heading2, Undo2, Redo2,
} from "lucide-react";

/**
 * Block-style rich-text editor for deal/contact/company notes.
 *
 * Stores HTML. Plain-text content from older notes is loaded as-is and the
 * browser renders it correctly inside the contenteditable. We sanitize the
 * HTML on both write (before sending to the API) and read (before injecting
 * via dangerouslySetInnerHTML) — defense in depth because a malicious editor
 * config could otherwise sneak in <script> tags.
 *
 * Why Tiptap and not BlockNote: Tiptap is ~50KB smaller, has no BlockNote-
 * specific schema, and gives us a simple, deterministic HTML output the
 * backend can store in the existing Text columns (no schema migration).
 */

interface RichTextEditorProps {
  /** Current HTML or plain text value. Plain text is rendered as-is. */
  value: string | null | undefined;
  /** Called with sanitized HTML whenever the editor blurs. */
  onChange: (html: string) => void;
  placeholder?: string;
  /** Min height in px. Default 120. */
  minHeight?: number;
  /** Disable editing. Renders as a read-only contenteditable. */
  readOnly?: boolean;
  /** Optional extra class for the contenteditable surface. */
  contentClassName?: string;
}

function ToolbarButton({
  active,
  disabled,
  onClick,
  title,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()} // keep editor focus
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      style={{
        width: 28,
        height: 28,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 6,
        border: "1px solid transparent",
        background: active ? "#eef2f7" : "transparent",
        color: active ? "#1f2d3d" : disabled ? "#cbd5e1" : "#5a6b80",
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      {children}
    </button>
  );
}

function Toolbar({ editor }: { editor: Editor | null }) {
  if (!editor) return null;
  const can = editor.can();
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 2,
        padding: "4px 6px",
        borderBottom: "1px solid #e3e9f2",
        background: "#fafbfd",
        borderTopLeftRadius: 12,
        borderTopRightRadius: 12,
      }}
    >
      <ToolbarButton
        title="Bold"
        active={editor.isActive("bold")}
        disabled={!can.chain().focus().toggleBold().run()}
        onClick={() => editor.chain().focus().toggleBold().run()}
      >
        <Bold size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Italic"
        active={editor.isActive("italic")}
        disabled={!can.chain().focus().toggleItalic().run()}
        onClick={() => editor.chain().focus().toggleItalic().run()}
      >
        <Italic size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Strikethrough"
        active={editor.isActive("strike")}
        disabled={!can.chain().focus().toggleStrike().run()}
        onClick={() => editor.chain().focus().toggleStrike().run()}
      >
        <Strikethrough size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Inline code"
        active={editor.isActive("code")}
        disabled={!can.chain().focus().toggleCode().run()}
        onClick={() => editor.chain().focus().toggleCode().run()}
      >
        <Code size={13} />
      </ToolbarButton>
      <span style={{ width: 1, alignSelf: "stretch", background: "#e3e9f2", margin: "4px 4px" }} />
      <ToolbarButton
        title="Heading"
        active={editor.isActive("heading", { level: 2 })}
        disabled={!can.chain().focus().toggleHeading({ level: 2 }).run()}
        onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
      >
        <Heading2 size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Bulleted list"
        active={editor.isActive("bulletList")}
        disabled={!can.chain().focus().toggleBulletList().run()}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
      >
        <List size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Numbered list"
        active={editor.isActive("orderedList")}
        disabled={!can.chain().focus().toggleOrderedList().run()}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
      >
        <ListOrdered size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Blockquote"
        active={editor.isActive("blockquote")}
        disabled={!can.chain().focus().toggleBlockquote().run()}
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
      >
        <Quote size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Add link"
        active={editor.isActive("link")}
        onClick={() => {
          const previous = editor.getAttributes("link").href ?? "";
          const url = window.prompt("URL", previous);
          if (url === null) return;
          if (url === "") {
            editor.chain().focus().unsetLink().run();
            return;
          }
          editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
        }}
      >
        <LinkIcon size={13} />
      </ToolbarButton>
      <span style={{ flex: 1 }} />
      <ToolbarButton
        title="Undo"
        disabled={!editor.can().undo()}
        onClick={() => editor.chain().focus().undo().run()}
      >
        <Undo2 size={13} />
      </ToolbarButton>
      <ToolbarButton
        title="Redo"
        disabled={!editor.can().redo()}
        onClick={() => editor.chain().focus().redo().run()}
      >
        <Redo2 size={13} />
      </ToolbarButton>
    </div>
  );
}

export function sanitizeHtml(input: string): string {
  if (!input) return "";
  return DOMPurify.sanitize(input, {
    USE_PROFILES: { html: true },
    ALLOWED_ATTR: ["href", "target", "rel", "class", "title"],
    FORBID_TAGS: ["script", "style", "iframe", "object", "embed"],
  });
}

export function RichTextEditor({
  value,
  onChange,
  placeholder,
  minHeight = 120,
  readOnly = false,
  contentClassName,
}: RichTextEditorProps) {
  // Normalize incoming plain-text into a tiny HTML paragraph so the editor
  // doesn't render a string literal as HTML (browsers would, but it's safer
  // to keep Tiptap's schema in charge).
  const initialHtml = useMemo(() => {
    // Empty value must be a valid doc, otherwise ProseMirror refuses to mount
    // and the editor silently shows nothing. Always seed with a paragraph.
    if (!value) return "<p></p>";
    if (/<[a-z][\s\S]*>/i.test(value)) return value;
    return `<p>${value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>")}</p>`;
  }, []); // initial only — we re-set below if value changes externally

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // Keep history tight; defaults are fine for short notes.
        heading: { levels: [2, 3] },
      }),
      Link.configure({
        openOnClick: false,
        autolink: true,
        HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" },
      }),
      // Native placeholder via ProseMirror's data-placeholder + a CSS pseudo
      // rule. The browser renders the placeholder as soon as the doc is empty,
      // so we don't need a fragile overlap div on top of the editor surface.
      Placeholder.configure({
        placeholder: placeholder ?? "Start typing…",
        showOnlyWhenEditable: true,
      }),
    ],
    content: initialHtml,
    editable: !readOnly,
    onBlur: ({ editor: ed }) => {
      const html = ed.getHTML();
      onChange(sanitizeHtml(html));
    },
  });

  // If the parent swaps the value while we're mounted (e.g. drawer reopened
  // for a different deal), reset the editor. Without this, stale HTML lingers.
  useEffect(() => {
    if (!editor) return;
    const next = value ?? "";
    const current = editor.getHTML();
    if (next !== current) {
      editor.commands.setContent(
        next && !/<[a-z][\s\S]*>/i.test(next)
          ? `<p>${next.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>")}</p>`
          : next,
        { emitUpdate: false },
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, editor]);

  return (
    <div
      style={{
        border: "1px solid #dbe6f2",
        borderRadius: 12,
        background: "#fff",
        overflow: "hidden",
      }}
    >
      {!readOnly && <Toolbar editor={editor} />}
      <EditorContent
        editor={editor}
        className={contentClassName}
        style={{
          minHeight,
          padding: "10px 12px",
          fontSize: 13,
          color: "#16273d",
          lineHeight: 1.5,
          outline: "none",
          cursor: readOnly ? "default" : "text",
        }}
      />
    </div>
  );
}

/** Read-only renderer for stored HTML — sanitizes then injects. */
export function RichTextDisplay({ html }: { html?: string | null }) {
  const clean = useMemo(() => sanitizeHtml(html ?? ""), [html]);
  const [hasContent, setHasContent] = useState(false);
  useEffect(() => {
    // Strip wrapper tags; if there's no actual content, render the dash.
    const stripped = clean
      .replace(/<p>\s*<\/p>/gi, "")
      .replace(/<br\s*\/?>/gi, "")
      .replace(/&nbsp;/g, "")
      .trim();
    setHasContent(stripped.length > 0);
  }, [clean]);
  if (!hasContent) {
    return <span style={{ color: "#94a3b8" }}>—</span>;
  }
  return (
    <div
      // Pre-sanitized; dangerouslySetInnerHTML is intentional.
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{ __html: clean }}
      className="rte-display"
      style={{ fontSize: 13, lineHeight: 1.55, color: "#1f2d3d", wordBreak: "break-word" }}
    />
  );
}

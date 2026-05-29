import { useEffect, useRef, useState, type ReactNode } from "react";
import { Sparkles, ChevronDown } from "lucide-react";
import { useZippy } from "./ZippyContext";

export interface ZippyDocDropdownItem {
  label: string;
  icon: ReactNode;
  message: string;
  // Optional — if true, renders a visual separator BEFORE this item.
  // Lets callers visually group items (e.g. "MOM is separate").
  separatorBefore?: boolean;
}

interface Props {
  items: ZippyDocDropdownItem[];
}

// Generic "Create with Zippy ▼" button. Knows nothing about deals or
// meetings — just takes a list of items, each with a message string that
// gets handed to Zippy when picked. If a conversation is already open,
// asks whether to fork or append.
export function ZippyDocDropdown({ items }: Props) {
  const { setOpen, activeConversationId, setActiveConversationId, setPendingMessage } = useZippy();
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmFor, setConfirmFor] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Close menu on outside-click.
  useEffect(() => {
    if (!menuOpen) return;
    function onDocClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [menuOpen]);

  function handlePick(message: string) {
    setMenuOpen(false);
    if (activeConversationId) {
      setConfirmFor(message);
    } else {
      setOpen(true);
      setPendingMessage(message);
    }
  }

  function sendToNew() {
    if (!confirmFor) return;
    const msg = confirmFor;
    setConfirmFor(null);
    setActiveConversationId(null);
    setOpen(true);
    setPendingMessage(msg);
  }

  function sendToCurrent() {
    if (!confirmFor) return;
    const msg = confirmFor;
    setConfirmFor(null);
    setOpen(true);
    setPendingMessage(msg);
  }

  return (
    <>
      <div ref={wrapperRef} style={{ position: "relative", display: "inline-block" }}>
        <button
          type="button"
          className="crm-button soft"
          onClick={() => setMenuOpen((v) => !v)}
        >
          <Sparkles size={14} />
          Create with Zippy
          <ChevronDown size={14} style={{ marginLeft: -2 }} />
        </button>

        {menuOpen && (
          <div
            role="menu"
            style={{
              position: "absolute",
              top: "100%",
              right: 0,
              marginTop: 6,
              minWidth: 220,
              zIndex: 50,
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 12,
              boxShadow: "0 12px 32px rgba(15, 23, 42, 0.14)",
              padding: 6,
            }}
          >
            {items.map((item, idx) => (
              <div key={`${item.label}-${idx}`}>
                {item.separatorBefore && (
                  <div
                    style={{
                      height: 1,
                      background: "#eef2f7",
                      margin: "6px 4px",
                    }}
                  />
                )}
                <button
                  type="button"
                  onClick={() => handlePick(item.message)}
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-[13px] text-stone-700 transition hover:bg-violet-50 hover:text-violet-800"
                  role="menuitem"
                >
                  <span className="flex h-5 w-5 items-center justify-center text-violet-600">
                    {item.icon}
                  </span>
                  <span className="font-medium">{item.label}</span>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Confirmation dialog — only when there's an active conversation. */}
      {confirmFor && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-stone-900/40 backdrop-blur-sm"
          onClick={() => setConfirmFor(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-[420px] rounded-2xl bg-white shadow-2xl"
            style={{ padding: "22px 24px" }}
          >
            <h3 className="text-[16px] font-semibold text-stone-900">
              Where should Zippy create this?
            </h3>
            <p className="mt-1 text-[13px] text-stone-500">
              You already have an active Zippy conversation. Start fresh or add to it?
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={sendToNew}
                className="rounded-lg border border-stone-200 bg-white px-3 py-1.5 text-[12.5px] font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-stone-50"
              >
                New conversation
              </button>
              <button
                type="button"
                onClick={sendToCurrent}
                className="rounded-lg bg-violet-600 px-3 py-1.5 text-[12.5px] font-semibold text-white shadow-sm transition hover:bg-violet-700"
              >
                Add to current
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

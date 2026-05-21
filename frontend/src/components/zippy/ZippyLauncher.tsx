import { useEffect } from "react";
import { ZippyPanel } from "./ZippyPanel";
import { useZippy } from "./ZippyContext";

// Floating launcher button in the bottom-right. Opens the Copilot-style side
// panel. Open/closed state now lives in ZippyContext so other pages can
// trigger Zippy with a pre-filled message ("Create with Zippy" dropdowns).
export function ZippyLauncher() {
  const { open, setOpen } = useZippy();

  // Keyboard shortcut: ⌘/Ctrl + J toggles Zippy, matching Copilot feel.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen(!open);
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Ask Zippy (⌘J)"
        aria-label="Open Zippy"
        className="group fixed z-40 flex cursor-pointer items-center justify-center rounded-full bg-gradient-to-br from-violet-600 via-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30 transition hover:shadow-violet-500/50 active:opacity-90"
        style={{
          bottom: 28,
          right: 28,
          width: 56,
          height: 56,
        }}
      >
        <span aria-hidden="true" className="relative flex" style={{ width: 22, height: 22 }}>
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white/30" />
          <svg
            viewBox="0 0 24 24"
            fill="none"
            className="relative"
            xmlns="http://www.w3.org/2000/svg"
            style={{ width: 22, height: 22 }}
          >
            <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" fill="currentColor" />
          </svg>
        </span>
      </button>

      <ZippyPanel open={open} onClose={() => setOpen(false)} />
    </>
  );
}

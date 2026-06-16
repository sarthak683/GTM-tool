import { useCallback, useEffect, useRef, useState } from "react";
import { ZippyPanel } from "./ZippyPanel";
import { useZippy } from "./ZippyContext";

// Floating launcher button. Opens the Copilot-style side panel. Open/closed
// state lives in ZippyContext so other pages can trigger Zippy with a pre-filled
// message ("Create with Zippy" dropdowns).
//
// The button is DRAGGABLE: pointer events move it anywhere on screen, the
// position persists in localStorage, and a small movement threshold separates a
// drag from a click so dragging never accidentally opens the panel.
const BTN = 56; // button size (px)
const MARGIN = 28; // default inset from the bottom-right corner
const EDGE = 8; // min gap kept from any viewport edge
const STORAGE_KEY = "zippy-launcher-pos";
const DRAG_THRESHOLD = 4; // px of movement before a press counts as a drag

type Pos = { x: number; y: number };

function clampPos(x: number, y: number): Pos {
  const maxX = window.innerWidth - BTN - EDGE;
  const maxY = window.innerHeight - BTN - EDGE;
  return {
    x: Math.min(Math.max(x, EDGE), Math.max(EDGE, maxX)),
    y: Math.min(Math.max(y, EDGE), Math.max(EDGE, maxY)),
  };
}

function defaultPos(): Pos {
  return { x: window.innerWidth - BTN - MARGIN, y: window.innerHeight - BTN - MARGIN };
}

export function ZippyLauncher() {
  const { open, setOpen } = useZippy();

  // Lazy init from storage (CSR app — window is available at module eval).
  const [pos, setPos] = useState<Pos>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const p = JSON.parse(saved);
        if (typeof p?.x === "number" && typeof p?.y === "number") return clampPos(p.x, p.y);
      }
    } catch {
      /* ignore malformed storage */
    }
    return defaultPos();
  });

  // Live drag state kept in a ref so pointermove doesn't churn through renders.
  const drag = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean; last: Pos } | null>(null);

  // Keyboard shortcut: ⌘/Ctrl + J toggles Zippy, Esc closes — matching Copilot.
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

  // Keep the button on-screen if the window is resized.
  useEffect(() => {
    function onResize() {
      setPos((p) => clampPos(p.x, p.y));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const onPointerMove = useCallback((e: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) d.moved = true;
    if (d.moved) {
      const next = clampPos(d.originX + dx, d.originY + dy);
      d.last = next;
      setPos(next);
    }
  }, []);

  const onPointerUp = useCallback(() => {
    const d = drag.current;
    drag.current = null;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    if (!d) return;
    if (d.moved) {
      // A drag — persist the new home, don't open the panel.
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(d.last));
      } catch {
        /* ignore */
      }
    } else {
      // A plain click — open Zippy.
      setOpen(true);
    }
  }, [onPointerMove, setOpen]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return; // primary button / touch only
      drag.current = { startX: e.clientX, startY: e.clientY, originX: pos.x, originY: pos.y, moved: false, last: pos };
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
    },
    [pos, onPointerMove, onPointerUp],
  );

  return (
    <>
      <button
        type="button"
        onPointerDown={onPointerDown}
        title="Ask Zippy (⌘J) · drag to move"
        aria-label="Open Zippy"
        className="group fixed z-40 flex cursor-grab touch-none select-none items-center justify-center rounded-full bg-gradient-to-br from-violet-600 via-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30 transition-shadow hover:shadow-violet-500/50 active:cursor-grabbing active:opacity-90"
        style={{ left: pos.x, top: pos.y, width: BTN, height: BTN }}
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

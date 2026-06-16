import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { ZippyPanel } from "./ZippyPanel";
import { useZippy } from "./ZippyContext";

// Floating launcher. Draggable, but it RESTS anchored to a screen corner via
// pure CSS (right/bottom/left/top), so the browser keeps it pinned to the real
// viewport edge regardless of window size, zoom, or layout shifts — no JS
// measurement, so it can never get stranded mid-screen. While actively dragging
// it follows the cursor with left/top; on release it snaps to the nearest
// corner. The resting corner persists in localStorage.
const BTN = 56; // button size (px)
const MARGIN = 28; // inset from the corner
const DRAG_THRESHOLD = 8; // px of movement before a press is a drag, not a click
const STORAGE_KEY = "zippy-launcher-corner";
const LEGACY_KEY = "zippy-launcher-pos"; // old absolute-pos format — cleaned up

type Corner = "br" | "bl" | "tr" | "tl";
type Pos = { x: number; y: number };

// CSS that anchors the button to a corner. Browser-computed against the live
// viewport — always correct, no measurement.
const CORNER_STYLE: Record<Corner, CSSProperties> = {
  br: { right: MARGIN, bottom: MARGIN },
  bl: { left: MARGIN, bottom: MARGIN },
  tr: { right: MARGIN, top: MARGIN },
  tl: { left: MARGIN, top: MARGIN },
};

function nearestCorner(centerX: number, centerY: number): Corner {
  const onLeft = centerX < window.innerWidth / 2;
  const onTop = centerY < window.innerHeight / 2;
  return onTop ? (onLeft ? "tl" : "tr") : onLeft ? "bl" : "br";
}

function loadCorner(): Corner {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s === "br" || s === "bl" || s === "tr" || s === "tl") return s;
  } catch {
    /* ignore */
  }
  return "br";
}

export function ZippyLauncher() {
  const { open, setOpen } = useZippy();
  const [corner, setCorner] = useState<Corner>(loadCorner);
  // Non-null only while actively dragging (pixel position following the cursor).
  const [dragPos, setDragPos] = useState<Pos | null>(null);
  const drag = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean; last: Pos } | null>(null);

  // One-time cleanup of the old absolute-position key so a previously-stranded
  // icon resets to the default corner.
  useEffect(() => {
    try {
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  // Keyboard shortcut: ⌘/Ctrl + J toggles Zippy, Esc closes.
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

  const onPointerMove = useCallback((e: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) d.moved = true;
    if (d.moved) {
      const maxX = window.innerWidth - BTN;
      const maxY = window.innerHeight - BTN;
      const next = {
        x: Math.min(Math.max(d.originX + dx, 0), maxX),
        y: Math.min(Math.max(d.originY + dy, 0), maxY),
      };
      d.last = next;
      setDragPos(next);
    }
  }, []);

  const onPointerUp = useCallback(() => {
    const d = drag.current;
    drag.current = null;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    if (!d) return;
    if (d.moved) {
      // Snap to the nearest corner: pick it from the drop point, then drop back
      // to CSS edge-anchoring (dragPos = null) so it's pinned to the real edge.
      const c = nearestCorner(d.last.x + BTN / 2, d.last.y + BTN / 2);
      setCorner(c);
      setDragPos(null);
      try {
        localStorage.setItem(STORAGE_KEY, c);
      } catch {
        /* ignore */
      }
    } else {
      setDragPos(null);
      setOpen(true); // a plain click opens Zippy
    }
  }, [onPointerMove, setOpen]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      if (e.button !== 0) return; // primary button / touch only
      // Read the button's current pixel position (works whether it's currently
      // edge-anchored or mid-drag) so the drag picks up exactly where it is.
      const rect = e.currentTarget.getBoundingClientRect();
      drag.current = {
        startX: e.clientX,
        startY: e.clientY,
        originX: rect.left,
        originY: rect.top,
        moved: false,
        last: { x: rect.left, y: rect.top },
      };
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
    },
    [onPointerMove, onPointerUp],
  );

  const style: CSSProperties = dragPos
    ? { left: dragPos.x, top: dragPos.y, width: BTN, height: BTN }
    : { ...CORNER_STYLE[corner], width: BTN, height: BTN };

  return (
    <>
      <button
        type="button"
        onPointerDown={onPointerDown}
        title="Ask Zippy (⌘J) · drag to a corner"
        aria-label="Open Zippy"
        className="group fixed z-40 flex cursor-grab touch-none select-none items-center justify-center rounded-full bg-gradient-to-br from-violet-600 via-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30 transition-shadow hover:shadow-violet-500/50 active:cursor-grabbing active:opacity-90"
        style={style}
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

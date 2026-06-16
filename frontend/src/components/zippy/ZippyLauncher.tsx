import { useCallback, useEffect, useRef, useState } from "react";
import { ZippyPanel } from "./ZippyPanel";
import { useZippy } from "./ZippyContext";

// Floating launcher. It is draggable, but SNAPS to the nearest screen corner on
// release — so it always rests in a corner (default bottom-right) and can never
// get stranded floating over content. The resting corner persists in
// localStorage. Open/closed state lives in ZippyContext so other pages can
// trigger Zippy with a pre-filled message ("Create with Zippy" dropdowns).
const BTN = 56; // button size (px)
const MARGIN = 28; // inset from the corner
const DRAG_THRESHOLD = 8; // px of movement before a press is a drag, not a click
const STORAGE_KEY = "zippy-launcher-corner";
const LEGACY_KEY = "zippy-launcher-pos"; // old free-position format — cleaned up

type Corner = "br" | "bl" | "tr" | "tl";
type Pos = { x: number; y: number };

function cornerToPos(corner: Corner): Pos {
  const maxX = window.innerWidth - BTN - MARGIN;
  const maxY = window.innerHeight - BTN - MARGIN;
  const onLeft = corner === "bl" || corner === "tl";
  const onTop = corner === "tl" || corner === "tr";
  return { x: onLeft ? MARGIN : maxX, y: onTop ? MARGIN : maxY };
}

function nearestCorner(p: Pos): Corner {
  const cx = p.x + BTN / 2;
  const cy = p.y + BTN / 2;
  const onLeft = cx < window.innerWidth / 2;
  const onTop = cy < window.innerHeight / 2;
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
  const [pos, setPos] = useState<Pos>(() => cornerToPos(loadCorner()));
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean; last: Pos } | null>(null);

  // One-time cleanup of the old free-position key so a previously-stranded icon
  // resets to the default corner.
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

  // Keep the button pinned to its corner when the window is resized.
  useEffect(() => {
    function onResize() {
      setPos(cornerToPos(corner));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [corner]);

  const onPointerMove = useCallback((e: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
      d.moved = true;
      setDragging(true); // disables the snap transition so it tracks the cursor 1:1
    }
    if (d.moved) {
      const maxX = window.innerWidth - BTN;
      const maxY = window.innerHeight - BTN;
      const next = {
        x: Math.min(Math.max(d.originX + dx, 0), maxX),
        y: Math.min(Math.max(d.originY + dy, 0), maxY),
      };
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
      // Snap to the nearest corner and persist it. Transition (re-enabled now
      // that dragging is false) animates the glide into the corner.
      setDragging(false);
      const c = nearestCorner(d.last);
      setCorner(c);
      setPos(cornerToPos(c));
      try {
        localStorage.setItem(STORAGE_KEY, c);
      } catch {
        /* ignore */
      }
    } else {
      setOpen(true); // a plain click opens Zippy
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
        title="Ask Zippy (⌘J) · drag to a corner"
        aria-label="Open Zippy"
        className={`group fixed z-40 flex cursor-grab touch-none select-none items-center justify-center rounded-full bg-gradient-to-br from-violet-600 via-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30 hover:shadow-violet-500/50 active:cursor-grabbing active:opacity-90 ${dragging ? "" : "transition-[left,top,box-shadow] duration-200 ease-out"}`}
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

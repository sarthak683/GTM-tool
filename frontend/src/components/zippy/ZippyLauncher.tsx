import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { ZippyPanel } from "./ZippyPanel";
import { useZippy } from "./ZippyContext";

// Floating launcher. Draggable with pointer capture + direct DOM updates (no
// React re-render per frame → tracks the cursor 1:1), animates a snap to the
// nearest corner on release, and rests anchored to that corner via CSS edges so
// it stays pinned to the real viewport edge. The resting corner persists.
//
// IMPORTANT: <html> has `zoom: 0.8` app-wide. With CSS zoom, getBoundingClientRect
// returns VISUAL (zoomed) px while pointer clientX/Y are LAYOUT (un-zoomed) px.
// All drag math is done in LAYOUT space; rect values are divided by the zoom to
// convert. (`style.left`/`top` and `clientX`/`innerWidth`/`MARGIN`/`BTN` are all
// layout space already.)
const BTN = 56; // button size (layout px)
const MARGIN = 28; // inset from the corner at rest
const EDGE = 12; // min gap from the edge while dragging
const DRAG_THRESHOLD = 6; // px before a press counts as a drag (vs a click)
const SNAP_MS = 200;
const STORAGE_KEY = "zippy-launcher-corner";
const LEGACY_KEY = "zippy-launcher-pos"; // old absolute-pos format — cleaned up

type Corner = "br" | "bl" | "tr" | "tl";

const isLeft = (c: Corner) => c === "bl" || c === "tl";
const isTop = (c: Corner) => c === "tl" || c === "tr";

function getZoom(): number {
  const z = parseFloat(getComputedStyle(document.documentElement).zoom || "1");
  return z && isFinite(z) && z > 0 ? z : 1;
}

// Layout-space top-left of the button, regardless of how it's currently styled.
function layoutLeftTop(btn: HTMLElement) {
  const z = getZoom();
  const r = btn.getBoundingClientRect();
  return { x: r.left / z, y: r.top / z };
}

// Pin to a corner via CSS edges — browser keeps it on the live viewport edge.
function applyCornerAnchor(btn: HTMLElement, c: Corner) {
  btn.style.transition = "";
  btn.style.left = isLeft(c) ? `${MARGIN}px` : "auto";
  btn.style.right = isLeft(c) ? "auto" : `${MARGIN}px`;
  btn.style.top = isTop(c) ? `${MARGIN}px` : "auto";
  btn.style.bottom = isTop(c) ? "auto" : `${MARGIN}px`;
}

// Layout-space viewport: under CSS `zoom`, the fixed-positioning containing
// block (and the clientX range) is innerWidth/zoom, NOT innerWidth. Using this
// makes the snap target match where the CSS edge-anchor actually lands (no jump).
function layoutViewport() {
  const z = getZoom();
  return { w: window.innerWidth / z, h: window.innerHeight / z };
}

function cornerTargetPx(c: Corner) {
  const { w, h } = layoutViewport();
  return {
    x: isLeft(c) ? MARGIN : w - BTN - MARGIN,
    y: isTop(c) ? MARGIN : h - BTN - MARGIN,
  };
}

function nearestCorner(cx: number, cy: number): Corner {
  const { w, h } = layoutViewport();
  const left = cx < w / 2;
  const top = cy < h / 2;
  return top ? (left ? "tl" : "tr") : left ? "bl" : "br";
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
  const btnRef = useRef<HTMLButtonElement>(null);
  const cornerRef = useRef<Corner>(loadCorner());
  const drag = useRef<{ id: number; startX: number; startY: number; grabX: number; grabY: number; moved: boolean } | null>(null);

  // Place at the saved corner before first paint. Positioning is fully
  // imperative, so the React style prop only carries size and re-renders never
  // fight the drag/snap styles.
  useLayoutEffect(() => {
    const btn = btnRef.current;
    if (btn) applyCornerAnchor(btn, cornerRef.current);
    try {
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    function onResize() {
      const btn = btnRef.current;
      if (btn && !drag.current) applyCornerAnchor(btn, cornerRef.current);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

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

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    const btn = btnRef.current;
    if (!btn) return;
    const lt = layoutLeftTop(btn); // layout-space current position
    drag.current = {
      id: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      grabX: e.clientX - lt.x, // grab offset, in layout space
      grabY: e.clientY - lt.y,
      moved: false,
    };
    try {
      btn.setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const d = drag.current;
    const btn = btnRef.current;
    if (!d || !btn || e.pointerId !== d.id) return;
    if (!d.moved) {
      if (Math.hypot(e.clientX - d.startX, e.clientY - d.startY) < DRAG_THRESHOLD) return;
      d.moved = true;
      btn.style.transition = "none";
      btn.style.cursor = "grabbing";
      btn.style.willChange = "left, top";
    }
    // layout-space top-left = cursor − grab offset; grab point stays under cursor.
    const { w, h } = layoutViewport();
    const x = Math.min(Math.max(e.clientX - d.grabX, EDGE), w - BTN - EDGE);
    const y = Math.min(Math.max(e.clientY - d.grabY, EDGE), h - BTN - EDGE);
    btn.style.left = `${x}px`;
    btn.style.top = `${y}px`;
    btn.style.right = "auto";
    btn.style.bottom = "auto";
  }, []);

  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      const d = drag.current;
      const btn = btnRef.current;
      if (!d || !btn || e.pointerId !== d.id) return;
      drag.current = null;
      try {
        btn.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      btn.style.cursor = "";
      btn.style.willChange = "";

      if (!d.moved) {
        setOpen(true); // plain click
        return;
      }

      const cur = layoutLeftTop(btn); // layout space
      const c = nearestCorner(cur.x + BTN / 2, cur.y + BTN / 2);
      cornerRef.current = c;
      try {
        localStorage.setItem(STORAGE_KEY, c);
      } catch {
        /* ignore */
      }
      const target = cornerTargetPx(c);
      if (Math.abs(cur.x - target.x) < 1 && Math.abs(cur.y - target.y) < 1) {
        applyCornerAnchor(btn, c);
        return;
      }
      // Glide (layout space) to the corner, then settle to CSS edge-anchoring.
      btn.style.left = `${cur.x}px`;
      btn.style.top = `${cur.y}px`;
      btn.style.right = "auto";
      btn.style.bottom = "auto";
      void btn.offsetWidth;
      btn.style.transition = `left ${SNAP_MS}ms cubic-bezier(0.22,1,0.36,1), top ${SNAP_MS}ms cubic-bezier(0.22,1,0.36,1)`;
      btn.style.left = `${target.x}px`;
      btn.style.top = `${target.y}px`;
      let settled = false;
      const settle = () => {
        if (settled) return;
        settled = true;
        btn.removeEventListener("transitionend", settle);
        applyCornerAnchor(btn, c);
      };
      btn.addEventListener("transitionend", settle);
      window.setTimeout(settle, SNAP_MS + 80);
    },
    [setOpen],
  );

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        title="Ask Zippy (⌘J) · drag to a corner"
        aria-label="Open Zippy"
        className="group fixed z-40 flex cursor-grab touch-none select-none items-center justify-center rounded-full bg-gradient-to-br from-violet-600 via-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30 hover:shadow-violet-500/50 active:opacity-90"
        style={{ width: BTN, height: BTN }}
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

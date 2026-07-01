import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { ZippyPanel } from "./ZippyPanel";
import { useZippy } from "./ZippyContext";

// Floating launcher. Draggable with pointer capture + direct DOM updates (no
// React re-render per frame → tracks the cursor 1:1) and it STAYS exactly where
// you drop it (free placement, persisted). Defaults to the bottom-right corner
// until moved. Open/closed lives in ZippyContext.
//
// NOTE: <html> has app-wide `zoom: 0.8`. With CSS zoom, getBoundingClientRect
// returns VISUAL (zoomed) px while pointer clientX/Y are LAYOUT (un-zoomed) px,
// and the fixed-positioning viewport in layout space is innerWidth/zoom. All
// drag math is done in LAYOUT space so the grab point stays under the cursor.
const BTN = 56; // button size (layout px)
const MARGIN = 28; // default inset from the bottom-right corner
const EDGE = 12; // min gap kept from any edge
const DRAG_THRESHOLD = 6; // px before a press counts as a drag (vs a click)
const STORAGE_KEY = "zippy-launcher-xy";
const LEGACY_KEYS = ["zippy-launcher-pos", "zippy-launcher-corner"]; // cleaned up

type Pos = { x: number; y: number };

function getZoom(): number {
  const z = parseFloat(getComputedStyle(document.documentElement).zoom || "1");
  return z && isFinite(z) && z > 0 ? z : 1;
}

// Layout-space viewport (innerWidth/zoom under CSS zoom).
function layoutViewport() {
  const z = getZoom();
  return { w: window.innerWidth / z, h: window.innerHeight / z };
}

// Layout-space top-left of the button regardless of how it's currently styled.
function layoutLeftTop(btn: HTMLElement): Pos {
  const z = getZoom();
  const r = btn.getBoundingClientRect();
  return { x: r.left / z, y: r.top / z };
}

function clampPos(p: Pos): Pos {
  const { w, h } = layoutViewport();
  return {
    x: Math.min(Math.max(p.x, EDGE), w - BTN - EDGE),
    y: Math.min(Math.max(p.y, EDGE), h - BTN - EDGE),
  };
}

// Free placement: explicit left/top.
function applyPos(btn: HTMLElement, p: Pos) {
  btn.style.transition = "";
  btn.style.left = `${p.x}px`;
  btn.style.top = `${p.y}px`;
  btn.style.right = "auto";
  btn.style.bottom = "auto";
}

// Default: bottom-right via CSS edges (browser keeps it pinned to the edge).
function applyDefaultCorner(btn: HTMLElement) {
  btn.style.transition = "";
  btn.style.left = "auto";
  btn.style.top = "auto";
  btn.style.right = `${MARGIN}px`;
  btn.style.bottom = `${MARGIN}px`;
}

function loadPos(): Pos | null {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s) {
      const p = JSON.parse(s);
      if (typeof p?.x === "number" && typeof p?.y === "number") return { x: p.x, y: p.y };
    }
  } catch {
    /* ignore */
  }
  return null;
}

export function ZippyLauncher() {
  const { open, setOpen } = useZippy();
  const btnRef = useRef<HTMLButtonElement>(null);
  const posRef = useRef<Pos | null>(loadPos());
  const drag = useRef<{ id: number; startX: number; startY: number; grabX: number; grabY: number; moved: boolean } | null>(null);

  // Initial placement before first paint; clean up legacy keys.
  useLayoutEffect(() => {
    const btn = btnRef.current;
    if (btn) {
      if (posRef.current) {
        posRef.current = clampPos(posRef.current);
        applyPos(btn, posRef.current);
      } else {
        applyDefaultCorner(btn);
      }
    }
    for (const k of LEGACY_KEYS) {
      try {
        localStorage.removeItem(k);
      } catch {
        /* ignore */
      }
    }
  }, []);

  // Keep it on-screen if the window is resized (re-clamp the saved spot).
  useEffect(() => {
    function onResize() {
      const btn = btnRef.current;
      if (!btn || drag.current) return;
      if (posRef.current) {
        posRef.current = clampPos(posRef.current);
        applyPos(btn, posRef.current);
      } else {
        applyDefaultCorner(btn);
      }
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ⌘/Ctrl+J toggles, Esc closes.
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
    const lt = layoutLeftTop(btn);
    drag.current = {
      id: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      grabX: e.clientX - lt.x, // grab offset, layout space
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
      // Stay exactly where it was dropped; persist it.
      const cur = clampPos(layoutLeftTop(btn));
      posRef.current = cur;
      applyPos(btn, cur);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(cur));
      } catch {
        /* ignore */
      }
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
        title="Ask Zippy (⌘J) · drag to move"
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

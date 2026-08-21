import { useEffect, useRef } from "react";

/**
 * Live board updates via Server-Sent Events.
 *
 * Opens an EventSource to /api/v1/deals/board/stream and calls `onChange`
 * whenever a deal is created, updated, moved, or deleted elsewhere. The
 * handler should refetch the board — the stream only signals, it never
 * carries the payload.
 *
 * Reconnects automatically: the backend sends a 25s heartbeat and EventSource
 * re-opens the connection on error, so a flaky network or a backend restart
 * self-heals without a page reload.
 */

type BoardChangeKind = "deal.created" | "deal.updated" | "deal.deleted" | "deal.stage_changed";

export interface BoardChangeEvent {
  kind: BoardChangeKind | string;
  deal_id?: string | null;
  stage?: string | null;
  ts?: number;
}

export function useBoardStream(onChange: (event: BoardChangeEvent) => void, enabled = true) {
  // Keep the latest handler in a ref so the EventSource callbacks don't close
  // over a stale closure after re-renders.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!enabled) return;

    let source: EventSource | null = null;
    let closed = false;
    let reconnectTimer: number | undefined;

    const connect = () => {
      if (closed) return;
      source = new EventSource("/api/v1/deals/board/stream", { withCredentials: true });

      // One listener per named event; anything else (including the heartbeat
      // comments) is ignored. The named-event shape lets us branch later
      // without a JSON parse of a generic `message` stream.
      const kinds: BoardChangeKind[] = ["deal.created", "deal.updated", "deal.deleted", "deal.stage_changed"];
      for (const kind of kinds) {
        source.addEventListener(kind, (raw) => {
          let parsed: BoardChangeEvent = { kind };
          try {
            parsed = JSON.parse((raw as MessageEvent).data) as BoardChangeEvent;
          } catch {
            // Non-JSON payload — still a change signal, call with just the kind.
          }
          onChangeRef.current(parsed);
        });
      }

      // EventSource fires `error` on any network drop; it auto-reconnects on
      // its own, but if the server closes with a fatal error we re-open
      // manually after a short backoff.
      source.onerror = () => {
        source?.close();
        source = null;
        if (!closed) {
          window.clearTimeout(reconnectTimer);
          reconnectTimer = window.setTimeout(connect, 3000);
        }
      };
    };

    connect();

    return () => {
      closed = true;
      window.clearTimeout(reconnectTimer);
      source?.close();
    };
  }, [enabled]);
}

import { useEffect, useRef } from "react";

import { BASE } from "../lib/api/core";

/**
 * Live board updates via Server-Sent Events.
 *
 * Calls `onChange` whenever a deal is created, updated, moved, or deleted
 * elsewhere. The handler should refetch the board — the stream only signals,
 * it never carries the payload.
 *
 * Why this uses `fetch` and not `EventSource`:
 *
 * `EventSource` cannot send custom headers. That is a hard browser limitation,
 * and this app authenticates with `Authorization: Bearer <token>` from
 * localStorage, so an EventSource request arrives with no credentials at all
 * and the endpoint answers 401 every single time. `withCredentials` does not
 * help — it forwards cookies, and there is no auth cookie to forward. The
 * previous implementation therefore retried on a flat 3s timer forever: in
 * production it produced ~1,200 failed requests an hour per open tab and grew
 * to 82% of all backend traffic, while live updates never worked once.
 *
 * `fetch` streams the same `text/event-stream` response and *can* carry the
 * header, so this reads the body itself and parses SSE frames. That keeps the
 * credential in a header (never a URL, which would land it in access logs) and
 * needs no change to the login flow.
 *
 * Reconnect policy: retry with exponential backoff and give up after
 * MAX_ATTEMPTS failures. A 401 or 403 is fatal immediately — no amount of
 * retrying fixes a rejected credential. Only a connection that survived past
 * a server heartbeat refills the budget, so genuine network blips self-heal
 * indefinitely while an endpoint that answers 200 and hangs up cannot spin
 * the same unbounded loop under a healthier-looking status code.
 */

type BoardChangeKind = "deal.created" | "deal.updated" | "deal.deleted" | "deal.stage_changed";

export interface BoardChangeEvent {
  kind: BoardChangeKind | string;
  deal_id?: string | null;
  stage?: string | null;
  ts?: number;
}

// Must go through BASE like every other API call. A bare relative path is
// served by whatever hosts the frontend: locally that is nginx, which has no
// /api proxy block, so the SPA fallback answers `index.html` with a 200 and
// the "stream" ends after a few milliseconds.
const STREAM_PATH = "/api/v1/deals/board/stream";
const BASE_DELAY_MS = 3_000;
const MAX_DELAY_MS = 60_000;
// Six attempts spans ~3 minutes of backoff — long enough to ride out a backend
// rollout, short enough that a permanently-failing stream stops quickly.
const MAX_ATTEMPTS = 6;

// A stream that opens and dies immediately must not refill the retry budget,
// or the client loops forever on a 200 that isn't really a stream — the same
// unbounded loop as before, just harder to spot. The server heartbeats every
// 25s, so a connection that outlives one heartbeat is genuinely working.
const HEALTHY_AFTER_MS = 30_000;

const KNOWN_KINDS = new Set<string>([
  "deal.created",
  "deal.updated",
  "deal.deleted",
  "deal.stage_changed",
]);

/**
 * Split an SSE buffer into complete frames.
 *
 * Frames are separated by a blank line. Returns the parsed frames plus
 * whatever trailing bytes are still incomplete, which the caller carries into
 * the next chunk. Comment frames (`: keepalive`) parse to null and are
 * dropped — they exist only to keep proxies from culling an idle connection.
 */
export function parseSseFrames(buffer: string): { events: BoardChangeEvent[]; rest: string } {
  // Normalise CRLF so a proxy that rewrites line endings cannot hide the
  // blank-line frame separator from us.
  const normalized = buffer.replace(/\r\n/g, "\n");
  const chunks = normalized.split("\n\n");
  // The final chunk has no terminating blank line yet — hold it back.
  const rest = chunks.pop() ?? "";

  const events: BoardChangeEvent[] = [];
  for (const chunk of chunks) {
    let kind = "";
    const dataLines: string[] = [];
    for (const line of chunk.split("\n")) {
      if (!line || line.startsWith(":")) continue; // comment / heartbeat
      if (line.startsWith("event:")) kind = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!kind && !dataLines.length) continue;

    let parsed: BoardChangeEvent = { kind };
    if (dataLines.length) {
      try {
        parsed = { ...(JSON.parse(dataLines.join("\n")) as BoardChangeEvent) };
        if (!parsed.kind) parsed.kind = kind;
      } catch {
        // Non-JSON payload — still a change signal, keep just the kind.
      }
    }
    if (parsed.kind) events.push(parsed);
  }
  return { events, rest };
}

export function useBoardStream(onChange: (event: BoardChangeEvent) => void, enabled = true) {
  // Keep the latest handler in a ref so the stream loop doesn't close over a
  // stale closure after re-renders.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!enabled) return;

    let closed = false;
    let failures = 0;
    let reconnectTimer: number | undefined;
    const controller = new AbortController();

    const scheduleRetry = () => {
      if (closed) return;
      failures += 1;
      if (failures >= MAX_ATTEMPTS) return; // budget spent — stay quiet
      const delay = Math.min(BASE_DELAY_MS * 2 ** (failures - 1), MAX_DELAY_MS);
      window.clearTimeout(reconnectTimer);
      reconnectTimer = window.setTimeout(() => void connect(), delay);
    };

    const connect = async (): Promise<void> => {
      if (closed) return;

      const token = localStorage.getItem("beacon_token");
      if (!token) return; // signed out — nothing to stream

      let res: Response;
      try {
        res = await fetch(`${BASE}${STREAM_PATH}`, {
          headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
          signal: controller.signal,
        });
      } catch {
        if (!closed) scheduleRetry(); // network-level failure
        return;
      }

      // A rejected credential is permanent: retrying cannot produce a
      // different answer, and it is exactly the loop this hook used to run.
      if (res.status === 401 || res.status === 403) return;
      if (!res.ok || !res.body) {
        scheduleRetry();
        return;
      }

      // Guard against a 200 that is not a stream at all. Without this the
      // SPA fallback's `index.html` reads as success, the read loop ends at
      // once, and the client reconnects forever on a "working" endpoint.
      const contentType = res.headers.get("content-type") ?? "";
      if (!contentType.includes("text/event-stream")) {
        scheduleRetry();
        return;
      }

      const openedAt = Date.now();
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const { events, rest } = parseSseFrames(buffer);
          buffer = rest;
          for (const event of events) {
            if (KNOWN_KINDS.has(event.kind)) onChangeRef.current(event);
          }
        }
      } catch {
        // Aborted on unmount, or the connection dropped mid-read.
      }

      // Server closed the stream (restart, rollout, proxy timeout) — reconnect.
      // Only a connection that lasted past a heartbeat proves the endpoint
      // really works and has earned a fresh retry budget.
      if (closed) return;
      if (Date.now() - openedAt >= HEALTHY_AFTER_MS) failures = 0;
      scheduleRetry();
    };

    void connect();

    return () => {
      closed = true;
      window.clearTimeout(reconnectTimer);
      controller.abort();
    };
  }, [enabled]);
}

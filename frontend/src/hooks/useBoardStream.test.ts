import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { parseSseFrames, useBoardStream } from "./useBoardStream";

/**
 * Regression cover for the production incident where this hook hammered a
 * permanently-rejected endpoint. EventSource cannot send an Authorization
 * header, so /deals/board/stream answered 401 every time, the hook retried on
 * a flat 3s timer forever, and the endpoint grew to 82% of all backend
 * requests while live updates never worked once.
 */

const TOKEN = "test-token";

/** Build a Response whose body streams the given chunks, then ends. */
function streamingResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

/** A body that stays open until `close()` is called, so the hook keeps reading. */
function openResponse() {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      ctrl = controller;
      controller.enqueue(encoder.encode(": connected\n\n"));
    },
  });
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (text: string) => ctrl.enqueue(encoder.encode(text)),
    close: () => ctrl.close(),
  };
}

beforeEach(() => {
  localStorage.setItem("beacon_token", TOKEN);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("parseSseFrames", () => {
  it("drops comment heartbeats and keeps real events", () => {
    const { events, rest } = parseSseFrames(
      ': connected\n\n: keepalive\n\nevent: deal.updated\ndata: {"kind":"deal.updated","deal_id":"d1"}\n\n',
    );
    expect(events).toEqual([{ kind: "deal.updated", deal_id: "d1" }]);
    expect(rest).toBe("");
  });

  it("holds back a frame that has not finished arriving", () => {
    const partial = 'event: deal.created\ndata: {"kind":"deal.crea';
    const { events, rest } = parseSseFrames(partial);
    expect(events).toEqual([]);
    expect(rest).toBe(partial);

    // The rest of the frame arrives in the next chunk.
    const { events: done } = parseSseFrames(rest + 'ted","deal_id":"d2"}\n\n');
    expect(done).toEqual([{ kind: "deal.created", deal_id: "d2" }]);
  });

  it("survives CRLF line endings from a rewriting proxy", () => {
    const { events } = parseSseFrames(
      'event: deal.deleted\r\ndata: {"kind":"deal.deleted","deal_id":"d3"}\r\n\r\n',
    );
    expect(events).toEqual([{ kind: "deal.deleted", deal_id: "d3" }]);
  });

  it("falls back to the event name when the payload is not JSON", () => {
    const { events } = parseSseFrames("event: deal.updated\ndata: not-json\n\n");
    expect(events).toEqual([{ kind: "deal.updated" }]);
  });
});

describe("useBoardStream", () => {
  it("sends the bearer token as a header, which EventSource could never do", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamingResponse([": connected\n\n"]));
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useBoardStream(() => {}));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/deals/board/stream");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    // The credential must never travel in the query string — access logs
    // record full URLs.
    expect(String(url)).not.toContain(TOKEN);
  });

  it("delivers each board event to the handler", async () => {
    const stream = openResponse();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream.response));
    const onChange = vi.fn();

    renderHook(() => useBoardStream(onChange));

    await waitFor(() => expect(onChange).not.toHaveBeenCalled());
    stream.push('event: deal.stage_changed\ndata: {"kind":"deal.stage_changed","deal_id":"d9","stage":"won"}\n\n');

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "deal.stage_changed", deal_id: "d9", stage: "won" }),
    );
    stream.close();
  });

  it("does not retry a 401 — the exact loop that flooded production", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useBoardStream(() => {}));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Old behaviour: a request every 3s, forever.
    await vi.advanceTimersByTimeAsync(600_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("retries a transient network failure on a widening delay, then stops", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useBoardStream(() => {}));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Budget is 6 attempts: the initial connect plus 5 retries at 3s, 6s, 12s,
    // 24s and 48s.
    for (const delay of [3_000, 6_000, 12_000, 24_000, 48_000]) {
      await vi.advanceTimersByTimeAsync(delay);
    }
    expect(fetchMock).toHaveBeenCalledTimes(6);

    await vi.advanceTimersByTimeAsync(600_000);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    vi.useRealTimers();
  });

  it("makes no request at all when disabled or signed out", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamingResponse([": connected\n\n"]));
    vi.stubGlobal("fetch", fetchMock);

    const disabled = renderHook(() => useBoardStream(() => {}, false));
    expect(fetchMock).not.toHaveBeenCalled();
    disabled.unmount();

    localStorage.removeItem("beacon_token");
    renderHook(() => useBoardStream(() => {}));
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });

  it("stops on a 200 that is not a stream — the SPA fallback trap", async () => {
    // nginx has no /api proxy block locally, so a relative stream URL is
    // answered by the SPA fallback: HTTP 200, `text/html`, body ends at once.
    // Without the content-type guard that reads as a healthy connection and
    // the client reconnects forever under a green status code.
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("<!doctype html><html></html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useBoardStream(() => {}));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    for (const delay of [3_000, 6_000, 12_000, 24_000, 48_000]) {
      await vi.advanceTimersByTimeAsync(delay);
    }
    expect(fetchMock).toHaveBeenCalledTimes(6);

    // Budget spent — it must not keep going.
    await vi.advanceTimersByTimeAsync(600_000);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    vi.useRealTimers();
  });

  it("does not refill the retry budget for a stream that dies immediately", async () => {
    // A real event-stream that hangs up after a few milliseconds must still
    // exhaust the budget, or a flapping endpoint loops forever.
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockImplementation(async () =>
      streamingResponse([": connected\n\n"]),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useBoardStream(() => {}));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    for (const delay of [3_000, 6_000, 12_000, 24_000, 48_000]) {
      await vi.advanceTimersByTimeAsync(delay);
    }
    await vi.advanceTimersByTimeAsync(600_000);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    vi.useRealTimers();
  });

  it("aborts the in-flight stream on unmount", async () => {
    const stream = openResponse();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal("fetch", fetchMock);
    const onChange = vi.fn();

    const { unmount } = renderHook(() => useBoardStream(onChange));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    expect(signal.aborted).toBe(false);
    unmount();
    expect(signal.aborted).toBe(true);
    stream.close();
  });
});

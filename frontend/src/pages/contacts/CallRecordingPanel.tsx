import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Square, Loader2, CheckCircle2, AlertTriangle, Sparkles, History, RefreshCw, Edit3, Save, XCircle, FileText, ChevronDown, ChevronRight } from "lucide-react";
import { callRecordingsApi } from "../../lib/api";
import type { CallRecording } from "../../types";

/**
 * In-browser call recording panel for the manual-call drawer.
 *
 * Flow:
 *   1. One-time consent gate (per browser, persisted in localStorage).
 *   2. Click "Record" → MediaRecorder captures the laptop mic at
 *      audio/webm;codecs=opus. Live timer + dB meter so the rep sees
 *      audio is being captured.
 *   3. Silence detection: rolling 30-second RMS window. If integrated
 *      energy stays below threshold at t=25s, a soft banner asks the
 *      rep if the prospect picked up.
 *   4. Click "Stop" → POST multipart blob → poll the recording row
 *      every 3s until status is `ready` (transcript + AI disposition
 *      populated) or `failed`.
 *   5. When ready, call `onSuggestion({disposition, summary, transcript,
 *      confidence})` so the parent drawer can pre-select the AI's
 *      suggested disposition in its existing dropdown.
 *
 * The panel never *forces* the AI suggestion onto the form — the rep
 * always confirms. If recording fails, mic permission is denied, or
 * the network drops, the rep just falls back to the manual disposition
 * dropdown that already exists below this panel.
 */

const POLL_INTERVAL_MS = 3000;
const SILENCE_WINDOW_MS = 30_000;
const SILENCE_PROMPT_AT_MS = 25_000;
// Approx dB level below which we consider the mic stream "silent". Tuned for
// laptop mics in a typical office — quiet enough that breathing/HVAC noise
// doesn't count, loud enough that a clearly-speaking voice trips it.
const SILENCE_RMS_THRESHOLD = 0.012;

type Phase = "idle" | "recording" | "uploading" | "processing" | "ready" | "failed";

export interface AISuggestion {
  disposition: string;
  confidence: number;
  summary: string;
  transcript: string;
}

export function CallRecordingPanel({
  contactId,
  onSuggestion,
  onRecordingChange,
}: {
  contactId: string;
  onSuggestion?: (s: AISuggestion) => void;
  // Fires whenever the active recording's id changes (created, cleared,
  // or replaced by a fresh recording). The parent uses this to attach
  // `recording_id` to the Activity row it writes on save, so the
  // lifecycle drawer can surface the transcript later. Null means "no
  // recording on this call."
  onRecordingChange?: (recordingId: string | null) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [dbLevel, setDbLevel] = useState(0); // 0..1 normalized for the meter
  const [silencePrompt, setSilencePrompt] = useState(false);
  const [recording, setRecording] = useState<CallRecording | null>(null);
  // Past recordings for this contact — shown as a compact strip above
  // the consent gate so the rep sees prior context (and can re-open the
  // lifecycle drawer for full transcripts).
  const [pastRecordings, setPastRecordings] = useState<CallRecording[]>([]);
  // Inline transcript editing on the ready state — Whisper sometimes
  // mishears names / product terms and the rep needs to correct it
  // before saving the disposition.
  const [editingTranscript, setEditingTranscript] = useState(false);
  const [transcriptDraft, setTranscriptDraft] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [retrying, setRetrying] = useState(false);
  // Set of past-recording IDs whose full transcript is currently
  // expanded in the past-recordings strip. Per-row toggle so the rep can
  // peek at one transcript without flooding the panel with several.
  const [expandedPastTranscripts, setExpandedPastTranscripts] = useState<Set<string>>(() => new Set());

  // MediaRecorder + Web Audio analyser live in refs so re-renders don't
  // tear them down. Cleanup happens in the stop handler and on unmount.
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const startedAtRef = useRef<number | null>(null);
  const tickIntervalRef = useRef<number | null>(null);
  const pollIntervalRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rmsHistoryRef = useRef<Array<{ t: number; rms: number }>>([]);

  // Clean shutdown on unmount — leaving a mic stream open is the worst
  // possible UX bug.
  useEffect(() => () => {
    teardown();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load prior recordings for this contact. Refetches when the current
  // recording reaches a terminal state so the new one shows up in the
  // strip without needing a manual reload.
  const refreshPast = useCallback(() => {
    if (!contactId) return;
    callRecordingsApi
      .listForContact(contactId, 10)
      .then((rows) => setPastRecordings(rows))
      .catch(() => setPastRecordings([]));
  }, [contactId]);

  useEffect(() => { refreshPast(); }, [refreshPast]);
  useEffect(() => {
    if (recording && (recording.status === "ready" || recording.status === "failed")) {
      refreshPast();
    }
  }, [recording, refreshPast]);

  const teardown = useCallback(() => {
    if (tickIntervalRef.current) { window.clearInterval(tickIntervalRef.current); tickIntervalRef.current = null; }
    if (pollIntervalRef.current) { window.clearInterval(pollIntervalRef.current); pollIntervalRef.current = null; }
    try { mediaRecorderRef.current?.stop(); } catch { /* may already be inactive */ }
    mediaRecorderRef.current = null;
    mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    mediaStreamRef.current = null;
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close().catch(() => {});
    }
    audioCtxRef.current = null;
    analyserRef.current = null;
    rmsHistoryRef.current = [];
  }, []);

  const start = async () => {
    setError(null);
    setSilencePrompt(false);
    setRecording(null);
    setElapsedSec(0);
    setDbLevel(0);
    chunksRef.current = [];
    rmsHistoryRef.current = [];
    // Clear any prior recording linkage — starting a new recording
    // means the in-progress call no longer points at the old one.
    onRecordingChange?.(null);

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setError(e instanceof Error && e.name === "NotAllowedError"
        ? "Microphone access was denied. You can still log this call manually below."
        : "Could not start the microphone. You can still log this call manually below.");
      return;
    }
    mediaStreamRef.current = stream;

    // Web Audio plumbing for the dB meter + silence detection.
    const AudioCtxCtor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioCtxCtor();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    audioCtxRef.current = ctx;
    analyserRef.current = analyser;

    // MediaRecorder. Opus in webm is small + universal — Whisper handles it natively.
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "");
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    recorder.ondataavailable = (ev) => { if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data); };
    recorder.onerror = (ev) => {
      const err = (ev as unknown as { error?: Error }).error;
      setError(`Recorder error: ${err?.message ?? "unknown"}`);
      teardown();
      setPhase("failed");
    };
    mediaRecorderRef.current = recorder;
    recorder.start();
    startedAtRef.current = Date.now();
    setPhase("recording");

    // 200ms tick for the timer, dB meter, and rolling RMS history.
    const buf = new Float32Array(analyser.fftSize);
    tickIntervalRef.current = window.setInterval(() => {
      const startedAt = startedAtRef.current ?? Date.now();
      const elapsed = Date.now() - startedAt;
      setElapsedSec(Math.floor(elapsed / 1000));

      analyser.getFloatTimeDomainData(buf);
      let sumSq = 0;
      for (let i = 0; i < buf.length; i++) sumSq += buf[i] * buf[i];
      const rms = Math.sqrt(sumSq / buf.length);
      // Normalize loosely to 0..1 for the visible meter (sqrt for perceptual feel).
      setDbLevel(Math.min(1, Math.sqrt(Math.max(0, rms * 8))));

      // Append to rolling history, drop anything older than the silence window.
      const now = Date.now();
      rmsHistoryRef.current.push({ t: now, rms });
      const cutoff = now - SILENCE_WINDOW_MS;
      while (rmsHistoryRef.current.length && rmsHistoryRef.current[0].t < cutoff) {
        rmsHistoryRef.current.shift();
      }

      // Soft "did the call connect?" prompt fires once if the first 25s
      // of recording have produced no audible speech.
      if (!silencePrompt && elapsed >= SILENCE_PROMPT_AT_MS) {
        const speechFrames = rmsHistoryRef.current.filter((p) => p.rms > SILENCE_RMS_THRESHOLD).length;
        if (speechFrames < 5) {
          setSilencePrompt(true);
        }
      }
    }, 200);
  };

  const stop = async ({ discard = false }: { discard?: boolean } = {}) => {
    const recorder = mediaRecorderRef.current;
    const startedAt = startedAtRef.current;
    if (!recorder || recorder.state === "inactive") {
      teardown();
      setPhase("idle");
      return;
    }
    // Wait for the final ondataavailable so chunksRef is complete.
    const stopped: Promise<void> = new Promise((resolve) => {
      recorder.onstop = () => resolve();
    });
    try { recorder.stop(); } catch { /* already stopped */ }
    await stopped;

    const durationSec = startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0;
    teardown();

    if (discard) {
      setPhase("idle");
      setElapsedSec(0);
      setSilencePrompt(false);
      onRecordingChange?.(null);
      return;
    }

    const audioType = recorder.mimeType || "audio/webm";
    const audioBlob = new Blob(chunksRef.current, { type: audioType });
    chunksRef.current = [];
    if (audioBlob.size === 0) {
      setError("Recording was empty — nothing to upload.");
      setPhase("failed");
      return;
    }

    setPhase("uploading");
    try {
      const created = await callRecordingsApi.upload({
        audio: audioBlob,
        contactId,
        consentAcknowledgedAt: new Date().toISOString(),
        durationSeconds: durationSec,
      });
      setRecording(created);
      setPhase("processing");
      onRecordingChange?.(created.id);
      pollFor(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
      setPhase("failed");
    }
  };

  const pollFor = useCallback((recordingId: string) => {
    if (pollIntervalRef.current) window.clearInterval(pollIntervalRef.current);
    pollIntervalRef.current = window.setInterval(async () => {
      try {
        const fresh = await callRecordingsApi.get(recordingId);
        setRecording(fresh);
        if (fresh.status === "ready" || fresh.status === "failed") {
          if (pollIntervalRef.current) { window.clearInterval(pollIntervalRef.current); pollIntervalRef.current = null; }
          setPhase(fresh.status === "ready" ? "ready" : "failed");
          if (fresh.status === "ready" && fresh.ai_disposition && onSuggestion) {
            onSuggestion({
              disposition: fresh.ai_disposition,
              confidence: fresh.ai_confidence ?? 0,
              summary: fresh.ai_summary ?? "",
              transcript: fresh.transcript ?? "",
            });
          }
          if (fresh.status === "failed") {
            setError(fresh.failure_reason || "Transcription failed.");
          }
        }
      } catch (e) {
        // Don't stop polling on a single network blip — the server might
        // still be working. We'll just try again next tick.
        // eslint-disable-next-line no-console
        console.warn("recording poll failed", e);
      }
    }, POLL_INTERVAL_MS);
  }, [onSuggestion]);

  const handleRetry = async () => {
    if (!recording) return;
    setRetrying(true);
    setError(null);
    try {
      const fresh = await callRecordingsApi.retry(recording.id);
      setRecording(fresh);
      setPhase("processing");
      pollFor(fresh.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retry failed.");
    } finally {
      setRetrying(false);
    }
  };

  const handleStartEdit = () => {
    if (!recording?.transcript) return;
    setTranscriptDraft(recording.transcript);
    setEditingTranscript(true);
  };

  const handleSaveEdit = async () => {
    if (!recording) return;
    setSavingEdit(true);
    setError(null);
    try {
      const updated = await callRecordingsApi.patch(recording.id, { transcript: transcriptDraft });
      setRecording(updated);
      setEditingTranscript(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save edit.");
    } finally {
      setSavingEdit(false);
    }
  };

  const handleCancelEdit = () => {
    setEditingTranscript(false);
    setTranscriptDraft("");
  };

  // ── Render ─────────────────────────────────────────────────────────

  const elapsedLabel = `${String(Math.floor(elapsedSec / 60)).padStart(2, "0")}:${String(elapsedSec % 60).padStart(2, "0")}`;

  return (
    <div style={{
      padding: "14px 22px",
      borderTop: "1px solid #eef2f7",
      borderBottom: "1px solid #eef2f7",
      background: "linear-gradient(180deg, #fafbff 0%, #ffffff 100%)",
    }}>
      <div style={{
        fontSize: 10.5, fontWeight: 800, color: "#5e7290",
        textTransform: "uppercase", letterSpacing: "0.08em",
        display: "flex", alignItems: "center", gap: 8, marginBottom: 10,
      }}>
        <Mic size={12} />
        Record this call (optional)
        <span style={{ flex: 1 }} />
        {phase === "ready" ? (
          <span style={{ color: "#15803d", display: "inline-flex", alignItems: "center", gap: 4 }}>
            <CheckCircle2 size={11} /> AI ready
          </span>
        ) : null}
      </div>

      {/* Past recordings strip — small chip per prior recording. Clicking
          expands to show the transcript and AI summary inline. */}
      {pastRecordings.filter((r) => r.id !== recording?.id).length > 0 ? (
        <details style={{ marginBottom: 10 }}>
          <summary style={{
            cursor: "pointer", fontSize: 11, fontWeight: 800, color: "#475569",
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "4px 8px", borderRadius: 8,
            background: "#f1f5f9", border: "1px solid #e2e8f0",
          }}>
            <History size={11} />
            {pastRecordings.filter((r) => r.id !== recording?.id).length} prior recording{pastRecordings.filter((r) => r.id !== recording?.id).length === 1 ? "" : "s"}
          </summary>
          <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
            {pastRecordings.filter((r) => r.id !== recording?.id).map((r) => {
              const when = new Date(r.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
              const tone = r.status === "ready" ? { bg: "#f0fdf4", border: "#bbf7d0", fg: "#15803d" }
                         : r.status === "failed" ? { bg: "#fef2f2", border: "#fecaca", fg: "#b91c1c" }
                         : { bg: "#eff6ff", border: "#bfdbfe", fg: "#1d4ed8" };
              return (
                <div key={r.id} style={{
                  padding: "8px 10px", borderRadius: 9,
                  background: tone.bg, border: `1px solid ${tone.border}`,
                  fontSize: 12,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                    <span style={{ fontWeight: 800, color: tone.fg, fontVariantNumeric: "tabular-nums" }}>{when}</span>
                    <span style={{ fontSize: 10.5, fontWeight: 700, color: tone.fg, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                      {r.status}
                    </span>
                    {r.ai_disposition ? (
                      <span style={{ fontSize: 11, color: "#475569", fontWeight: 700 }}>
                        · {r.ai_disposition.replace(/_/g, " ")}
                      </span>
                    ) : null}
                  </div>
                  {r.ai_summary ? (
                    <div style={{ color: "#1e293b", lineHeight: 1.5 }}>{r.ai_summary}</div>
                  ) : r.failure_reason ? (
                    <div style={{ color: "#b91c1c" }}>{r.failure_reason}</div>
                  ) : null}
                  {r.transcript ? (() => {
                    const isOpen = expandedPastTranscripts.has(r.id);
                    return (
                      <>
                        <button
                          type="button"
                          onClick={() => setExpandedPastTranscripts((prev) => {
                            const next = new Set(prev);
                            if (next.has(r.id)) next.delete(r.id); else next.add(r.id);
                            return next;
                          })}
                          style={{
                            display: "inline-flex", alignItems: "center", gap: 4,
                            marginTop: 6, padding: "3px 8px",
                            border: `1px solid ${tone.border}`, borderRadius: 7,
                            background: "#ffffff", color: tone.fg,
                            fontSize: 10.5, fontWeight: 700, cursor: "pointer",
                          }}
                          title={`${r.transcript.length.toLocaleString()} chars`}
                        >
                          {isOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                          <FileText size={10} />
                          {isOpen ? "Hide transcript" : "Show transcript"}
                          <span style={{ color: "#64748b", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                            · {r.transcript.length.toLocaleString()}
                          </span>
                        </button>
                        {isOpen ? (
                          <div style={{
                            marginTop: 6, padding: 10, borderRadius: 8,
                            background: "#ffffff", border: `1px solid ${tone.border}`,
                            whiteSpace: "pre-wrap", maxHeight: 260, overflowY: "auto",
                            color: "#1e293b", lineHeight: 1.55, fontSize: 12,
                          }}>
                            {r.transcript}
                          </div>
                        ) : null}
                      </>
                    );
                  })() : null}
                </div>
              );
            })}
          </div>
        </details>
      ) : null}

      {/* Main control row */}
      {phase === "idle" || phase === "failed" ? (
        <div style={{ display: "grid", gap: 8 }}>
          <button
            type="button"
            onClick={start}
            style={{
              width: "100%", padding: "12px 14px",
              border: "1px solid #b91c1c", borderRadius: 11,
              background: "#fef2f2",
              color: "#b91c1c",
              fontSize: 13.5, fontWeight: 800,
              cursor: "pointer",
              display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8,
            }}
          >
            <Mic size={14} /> {phase === "failed" ? "Start a new recording" : "Start recording"}
          </button>
          {/* Retry the existing audio (only valid for ~30 min after upload
              because the audio lives in Redis with a TTL). If the audio
              expired the backend returns 410 and we surface it in the
              error banner — the rep re-records. */}
          {phase === "failed" && recording ? (
            <button
              type="button"
              onClick={handleRetry}
              disabled={retrying}
              style={{
                width: "100%", padding: "10px 14px",
                border: "1px solid #d4d4d8", borderRadius: 11,
                background: "#fff", color: "#3f3f46",
                fontSize: 12.5, fontWeight: 700,
                cursor: retrying ? "wait" : "pointer",
                display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8,
              }}
            >
              {retrying ? <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} /> : <RefreshCw size={12} />}
              {retrying ? "Retrying…" : "Retry transcription (re-uses last audio if still buffered)"}
            </button>
          ) : null}
        </div>
      ) : phase === "recording" ? (
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 12px", borderRadius: 11,
            background: "#fef2f2", border: "1px solid #fecaca",
          }}>
            <span style={{
              width: 10, height: 10, borderRadius: 999, background: "#dc2626",
              boxShadow: "0 0 0 4px #fecaca",
              animation: "recpulse 1.2s ease-in-out infinite",
            }} />
            <span style={{ fontSize: 13, fontWeight: 800, color: "#b91c1c" }}>Recording</span>
            <span style={{ fontSize: 13, fontWeight: 800, color: "#7f1d1d", fontVariantNumeric: "tabular-nums" }}>
              {elapsedLabel}
            </span>
            <div style={{ flex: 1, height: 6, borderRadius: 999, background: "#fee2e2", overflow: "hidden" }}>
              <div style={{
                width: `${Math.round(dbLevel * 100)}%`,
                height: "100%",
                background: "linear-gradient(90deg, #fca5a5, #dc2626)",
                transition: "width 100ms linear",
              }} />
            </div>
            <button
              type="button"
              onClick={() => void stop()}
              style={{
                border: "none", background: "#b91c1c", color: "#fff",
                borderRadius: 9, padding: "6px 12px", fontSize: 12, fontWeight: 800,
                display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer",
              }}
            >
              <Square size={11} /> Stop & save
            </button>
          </div>

          {silencePrompt ? (
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "10px 12px", borderRadius: 10,
              background: "#fffbeb", border: "1px solid #fde68a",
              fontSize: 12, color: "#92400e",
            }}>
              <AlertTriangle size={13} />
              <span style={{ flex: 1 }}>
                No voice detected in the first 30 seconds — did the prospect pick up?
              </span>
              <button
                type="button"
                onClick={() => void stop({ discard: true })}
                style={{ border: "1px solid #fde68a", background: "#fff", color: "#92400e", borderRadius: 8, padding: "4px 10px", fontSize: 11.5, fontWeight: 700, cursor: "pointer" }}
              >
                End call (no answer)
              </button>
              <button
                type="button"
                onClick={() => setSilencePrompt(false)}
                style={{ border: "none", background: "transparent", color: "#92400e", fontSize: 11.5, fontWeight: 700, cursor: "pointer" }}
              >
                Keep going
              </button>
            </div>
          ) : null}
        </div>
      ) : phase === "uploading" || phase === "processing" ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 12px", borderRadius: 11,
          background: "#eff6ff", border: "1px solid #bfdbfe",
          fontSize: 13, color: "#1d4ed8", fontWeight: 700,
        }}>
          <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
          {phase === "uploading"
            ? "Uploading audio…"
            : recording?.status === "transcribing" ? "Transcribing with Whisper…"
            : recording?.status === "classifying" ? "Classifying disposition with Claude…"
            : "Processing…"}
        </div>
      ) : phase === "ready" ? (
        <div style={{ display: "grid", gap: 8 }}>
          {recording?.ai_disposition ? (
            <div style={{
              padding: "10px 12px", borderRadius: 11,
              background: "#f0fdf4", border: "1px solid #bbf7d0",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 800, color: "#15803d" }}>
                <Sparkles size={12} /> AI suggestion · {Math.round((recording.ai_confidence ?? 0) * 100)}% confidence
              </div>
              {recording.ai_summary ? (
                <div style={{ marginTop: 4, fontSize: 12.5, color: "#1e293b", lineHeight: 1.5 }}>
                  {recording.ai_summary}
                </div>
              ) : null}
              <div style={{ marginTop: 4, fontSize: 11, color: "#475569" }}>
                Pre-filled below — you can change it before saving.
              </div>
            </div>
          ) : (
            <div style={{
              padding: "10px 12px", borderRadius: 11,
              background: "#fffbeb", border: "1px solid #fde68a",
              fontSize: 12, color: "#92400e",
            }}>
              Transcription succeeded, but no high-confidence disposition was returned. Pick one below.
            </div>
          )}
          {recording?.transcript ? (
            <div style={{ fontSize: 12, color: "#475569" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span style={{ fontWeight: 700, color: "#334155" }}>
                  Transcript ({recording.transcript.length.toLocaleString()} chars)
                </span>
                <span style={{ flex: 1 }} />
                {editingTranscript ? (
                  <>
                    <button
                      type="button"
                      onClick={handleSaveEdit}
                      disabled={savingEdit || transcriptDraft === recording.transcript}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 5,
                        padding: "4px 10px", borderRadius: 8,
                        border: "1px solid #16a34a", background: "#ecfdf5",
                        color: "#15803d", fontSize: 11.5, fontWeight: 700,
                        cursor: savingEdit ? "wait" : "pointer",
                      }}
                    >
                      {savingEdit ? <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> : <Save size={11} />}
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelEdit}
                      disabled={savingEdit}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 5,
                        padding: "4px 10px", borderRadius: 8,
                        border: "1px solid #e2e8f0", background: "#fff",
                        color: "#475569", fontSize: 11.5, fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      <XCircle size={11} /> Cancel
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={handleStartEdit}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 5,
                      padding: "4px 10px", borderRadius: 8,
                      border: "1px solid #e2e8f0", background: "#fff",
                      color: "#475569", fontSize: 11.5, fontWeight: 700,
                      cursor: "pointer",
                    }}
                    title="Correct any Whisper mistakes"
                  >
                    <Edit3 size={11} /> Edit
                  </button>
                )}
              </div>
              {editingTranscript ? (
                <textarea
                  value={transcriptDraft}
                  onChange={(e) => setTranscriptDraft(e.target.value)}
                  rows={10}
                  style={{
                    width: "100%", padding: 10, borderRadius: 8,
                    border: "1px solid #cbd5e1", background: "#fff",
                    fontSize: 12.5, lineHeight: 1.55,
                    fontFamily: "inherit", color: "#1e293b", resize: "vertical",
                    outline: "none",
                  }}
                />
              ) : (
                <div style={{
                  padding: 10, borderRadius: 8,
                  background: "#f8fafc", border: "1px solid #e2e8f0",
                  whiteSpace: "pre-wrap", maxHeight: 240, overflowY: "auto",
                  fontVariantNumeric: "tabular-nums", lineHeight: 1.55,
                }}>
                  {recording.transcript}
                </div>
              )}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Error surface — shown across all phases, never blocking */}
      {error ? (
        <div style={{
          marginTop: 10, padding: "8px 10px", borderRadius: 10,
          background: "#fef2f2", border: "1px solid #fecaca",
          color: "#b91c1c", fontSize: 12, fontWeight: 600,
          display: "flex", alignItems: "flex-start", gap: 8,
        }}>
          <AlertTriangle size={12} style={{ marginTop: 2, flexShrink: 0 }} />
          <span>{error}</span>
        </div>
      ) : null}

      <style>{`
        @keyframes recpulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.4); opacity: 0.7; } }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

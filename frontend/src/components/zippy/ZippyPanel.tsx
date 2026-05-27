import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  knowledgeApi,
  personalEmailSyncApi,
  zippyApi,
  type IndexStatus,
  type ZippyConversationSummary,
  type ZippyMessage,
} from "../../lib/api";
import { ZippyMessageBubble } from "./ZippyMessageBubble";
import { ZippyComposer, type ComposerImage } from "./ZippyComposer";
import { useZippy } from "./ZippyContext";

interface ZippyPanelProps {
  open: boolean;
  onClose: () => void;
}

// Copilot-style side panel. Default view is just the active thread so
// messages are easy to read. The session history is hidden behind a clock
// icon in the header — click it to reveal a compact list, click again or
// anywhere outside to collapse. That mirrors the Beacon chatbot widget
// pattern (+ / history / minimise / close).
export function ZippyPanel({ open, onClose }: ZippyPanelProps) {
  const {
    activeConversationId: activeId,
    setActiveConversationId: setActiveId,
    pendingMessage,
    setPendingMessage,
  } = useZippy();
  const [conversations, setConversations] = useState<ZippyConversationSummary[]>([]);
  const [messages, setMessages] = useState<ZippyMessage[]>([]);
  const [activeTitle, setActiveTitle] = useState<string>("");
  const [sending, setSending] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sessions drawer is closed by default — opens on demand from the header.
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [historyPage, setHistoryPage] = useState(0);
  const HISTORY_PAGE_SIZE = 20;

  // Inline-rename state — applies to either the active title (in header) or
  // a row in the sessions drawer. `renamingId` tells us which row is being
  // edited; null means nothing is being renamed.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  // Confirm-before-delete: holds the id of the session pending deletion.
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Knowledge footer — "Grounded in N files · Last synced Xm ago"
  const [userStatus, setUserStatus] = useState<IndexStatus | null>(null);
  const [adminStatus, setAdminStatus] = useState<IndexStatus | null>(null);
  const [emailConnected, setEmailConnected] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Load the session list + footer stats + email status the first time the panel opens.
  useEffect(() => {
    if (!open) return;
    void refreshConversations();
    void loadKnowledgeStatus();
    void loadEmailStatus();
    return undefined;
  }, [open]);

  // Auto-send any pending message dropped into context (e.g. via the
  // "Create with Zippy" dropdowns on the deal drawer / meeting detail).
  // Clear it first so re-renders don't re-fire the send.
  useEffect(() => {
    if (!open || !pendingMessage) return;
    const text = pendingMessage;
    setPendingMessage(null);
    void sendMessage(text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pendingMessage]);

  async function refreshConversations() {
    setRefreshing(true);
    try {
      const data = await zippyApi.listConversations(30);
      setConversations(data);
    } catch (e) {
      setError(formatError(e));
    } finally {
      setRefreshing(false);
    }
  }

  async function loadKnowledgeStatus() {
    try {
      const [user, admin] = await Promise.all([
        knowledgeApi.status("user").catch(() => null),
        knowledgeApi.status("admin").catch(() => null),
      ]);
      setUserStatus(user);
      setAdminStatus(admin);
    } catch {
      // Footer chip silently hides when this fails.
    }
  }

  async function loadEmailStatus() {
    try {
      const status = await personalEmailSyncApi.getStatus();
      setEmailConnected(!!status.connected);
    } catch {
      setEmailConnected(false);
    }
  }

  // Load the selected thread's messages + title.
  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      setActiveTitle("");
      return;
    }
    let cancelled = false;
    setLoadingThread(true);
    zippyApi
      .getConversation(activeId)
      .then((data) => {
        if (!cancelled) {
          setMessages(data.messages);
          setActiveTitle(data.title || "");
        }
      })
      .catch((e) => {
        if (!cancelled) setError(formatError(e));
      })
      .finally(() => {
        if (!cancelled) setLoadingThread(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  // Auto-scroll to the newest message whenever the list grows.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages.length, loadingThread]);

  const footerStats = useMemo(() => {
    const userFiles = userStatus?.total_files ?? 0;
    const adminFiles = adminStatus?.total_files ?? 0;
    const totalFiles = userFiles + adminFiles;
    const lastSynced = computeLastSynced([
      ...(userStatus?.files ?? []),
      ...(adminStatus?.files ?? []),
    ]);
    return { totalFiles, lastSynced };
  }, [userStatus, adminStatus]);

  const suggestions = useMemo(
    () => {
      const hasFiles = footerStats.totalFiles > 0;
      if (!emailConnected) {
        return [
          "Connect your Gmail in Settings to unlock Drive search.",
          "What can Zippy help me with?",
          "How do I get started with Beacon CRM?",
        ];
      }
      if (!hasFiles) {
        return [
          "Pick a Drive folder in Settings so I can search your files.",
          "Summarise the last client call with Beacon.",
          "Draft a mutual NDA for Beacon and Acme Corp (India).",
        ];
      }
      return [
        "Summarise the last client call with Optera.",
        "Draft a mutual NDA for Beacon and Acme Corp (India).",
        "Generate a MOM from the notes below.",
        "What's in the ROI deck for e2open?",
      ];
    },
    [emailConnected, footerStats.totalFiles],
  );

  const filteredConversations = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return conversations;
    return conversations.filter((c) => c.title.toLowerCase().includes(term));
  }, [conversations, search]);

  const totalPages = Math.max(1, Math.ceil(filteredConversations.length / HISTORY_PAGE_SIZE));
  const currentPage = Math.min(historyPage, totalPages - 1);
  const pagedConversations = useMemo(
    () =>
      filteredConversations.slice(
        currentPage * HISTORY_PAGE_SIZE,
        currentPage * HISTORY_PAGE_SIZE + HISTORY_PAGE_SIZE,
      ),
    [filteredConversations, currentPage],
  );

  useEffect(() => {
    setHistoryPage(0);
  }, [search]);

  async function sendMessage(text: string, image?: ComposerImage | null) {
    // Allow image-only sends — the agent can still react to a pure
    // screenshot ("here's a LinkedIn profile, draft outreach"). If both
    // text and image are empty we no-op.
    if (!text.trim() && !image) return;
    if (sending) return;
    setError(null);
    setSending(true);

    // Optimistic user bubble. We keep the bubble text-only on purpose —
    // ZippyMessageBubble doesn't render images, and the image isn't
    // persisted server-side either. The screenshot only travels with
    // this single request.
    const optimistic: ZippyMessage = {
      id: `local-${Date.now()}`,
      conversation_id: activeId ?? "",
      role: "user",
      content: text || (image ? `(attached ${image.filename})` : ""),
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);

    try {
      const res = await zippyApi.send({
        message: text,
        conversation_id: activeId ?? undefined,
        image_base64: image?.base64,
        image_media_type: image?.mediaType,
      });
      setActiveId(res.conversation_id);
      setMessages((prev) => {
        const normalised = prev.map((m) =>
          m.id === optimistic.id ? { ...m, conversation_id: res.conversation_id } : m,
        );
        return [...normalised, res.message];
      });
      zippyApi
        .listConversations(30)
        .then(setConversations)
        .catch(() => {});
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSending(false);
    }
  }

  function startNewChat() {
    setActiveId(null);
    setMessages([]);
    setError(null);
    setSessionsOpen(false);
  }

  function pickConversation(id: string) {
    setActiveId(id);
    setSessionsOpen(false);
  }

  // Begin inline rename for a session (or the active title in the header).
  function beginRename(id: string, currentTitle: string) {
    setRenamingId(id);
    setRenameDraft(currentTitle);
  }

  async function commitRename() {
    const id = renamingId;
    if (!id) return;
    const next = renameDraft.trim();
    setRenamingId(null);
    if (!next) return;
    // Optimistic: update local list + active title immediately.
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title: next } : c)),
    );
    if (id === activeId) setActiveTitle(next);
    try {
      await zippyApi.update(id, { title: next });
    } catch (e) {
      setError(formatError(e));
      // Re-fetch to recover.
      void refreshConversations();
    }
  }

  function cancelRename() {
    setRenamingId(null);
    setRenameDraft("");
  }

  async function togglePin(id: string, current: boolean) {
    // Optimistic toggle.
    setConversations((prev) =>
      prev
        .map((c) => (c.id === id ? { ...c, is_pinned: !current } : c))
        .sort((a, b) => {
          const ap = a.is_pinned ? 1 : 0;
          const bp = b.is_pinned ? 1 : 0;
          if (ap !== bp) return bp - ap;
          return (b.updated_at || "").localeCompare(a.updated_at || "");
        }),
    );
    try {
      await zippyApi.update(id, { is_pinned: !current });
    } catch (e) {
      setError(formatError(e));
      void refreshConversations();
    }
  }

  async function commitDelete(id: string) {
    setConfirmDeleteId(null);
    // Optimistic removal.
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (id === activeId) {
      setActiveId(null);
      setMessages([]);
      setActiveTitle("");
    }
    try {
      await zippyApi.delete(id);
    } catch (e) {
      setError(formatError(e));
      void refreshConversations();
    }
  }

  return (
    <>
      {/* Scrim */}
      <div
        className={`fixed inset-0 z-40 bg-stone-900/30 backdrop-blur-sm transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Slide-over panel */}
      <aside
        className={`fixed right-0 top-0 z-50 flex h-full w-full max-w-[520px] transform flex-col bg-white shadow-2xl transition-transform duration-300 ease-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        aria-hidden={!open}
      >
        {/* Header */}
        <header
          className="flex items-center border-b border-stone-200"
          style={{ padding: "14px 18px", gap: 14 }}
        >
          <div
            className="flex items-center justify-center rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-500 text-white shadow-sm"
            style={{ width: 36, height: 36 }}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" style={{ width: 18, height: 18 }}>
              <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />
            </svg>
          </div>
          <div className="min-w-0 flex-1" style={{ paddingRight: 8 }}>
            <div
              className="font-semibold text-stone-900"
              style={{ fontSize: 16, lineHeight: 1.2, letterSpacing: -0.1 }}
            >
              Zippy
            </div>
            {activeId && activeTitle && (
              renamingId === activeId && !sessionsOpen ? (
                <input
                  autoFocus
                  value={renameDraft}
                  onChange={(e) => setRenameDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") cancelRename();
                  }}
                  className="mt-0.5 w-full rounded border border-violet-300 bg-white text-stone-700 outline-none focus:border-violet-500"
                  style={{ fontSize: 11.5, padding: "2px 5px", lineHeight: 1.3 }}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    if (!sessionsOpen) beginRename(activeId, activeTitle);
                  }}
                  title={sessionsOpen ? activeTitle : "Click to rename"}
                  className="mt-0.5 block max-w-full truncate rounded text-stone-500 hover:bg-stone-100 hover:text-stone-700"
                  style={{ fontSize: 11.5, padding: "1px 4px", margin: "2px -4px 0", lineHeight: 1.3, textAlign: "left" }}
                >
                  {activeTitle}
                </button>
              )
            )}
          </div>
          <div className="flex items-center gap-1">
            <HeaderIconButton label="New chat" onClick={startNewChat}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-[18px] w-[18px]">
                <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
                <path d="M12 8v6M9 11h6" />
              </svg>
            </HeaderIconButton>
            <HeaderIconButton
              label="Chat history"
              active={sessionsOpen}
              onClick={() => setSessionsOpen((v) => !v)}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-[18px] w-[18px]">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 6v6l4 2" />
              </svg>
            </HeaderIconButton>
            <HeaderIconButton label="Close" onClick={onClose}>
              <svg viewBox="0 0 20 20" fill="currentColor" className="h-[18px] w-[18px]">
                <path
                  fillRule="evenodd"
                  d="M4.28 4.28a.75.75 0 011.06 0L10 8.94l4.66-4.66a.75.75 0 111.06 1.06L11.06 10l4.66 4.66a.75.75 0 11-1.06 1.06L10 11.06l-4.66 4.66a.75.75 0 01-1.06-1.06L8.94 10 4.28 5.34a.75.75 0 010-1.06z"
                  clipRule="evenodd"
                />
              </svg>
            </HeaderIconButton>
          </div>
        </header>

        {/* Sessions drawer — full overlay so it replaces the thread view */}
        {sessionsOpen && (
          <div className="absolute inset-x-0 top-[65px] bottom-0 z-10 flex flex-col bg-white">
            <div
              className="flex items-center"
              style={{ padding: "10px 12px 8px", gap: 8 }}
            >
              <button
                type="button"
                onClick={() => setSessionsOpen(false)}
                className="inline-flex items-center rounded-md text-stone-500 transition hover:bg-stone-100 hover:text-stone-700"
                style={{ padding: "4px 8px 4px 6px", gap: 4, fontSize: 12 }}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} style={{ width: 14, height: 14 }}>
                  <path d="M15 18l-6-6 6-6" />
                </svg>
                Back to chat
              </button>
              <div
                className="text-stone-400"
                style={{ fontSize: 11, letterSpacing: 0.4, textTransform: "uppercase", marginLeft: 4 }}
              >
                History
              </div>
              <button
                type="button"
                onClick={() => void refreshConversations()}
                disabled={refreshing}
                title="Refresh"
                aria-label="Refresh"
                className="ml-auto flex items-center justify-center rounded-md text-stone-400 transition hover:bg-stone-100 hover:text-stone-600 disabled:opacity-40"
                style={{ width: 24, height: 24 }}
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  className={refreshing ? "animate-spin" : ""}
                  style={{ width: 13, height: 13 }}
                >
                  <path d="M23 4v6h-6M1 20v-6h6" />
                  <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
                </svg>
              </button>
            </div>
            <div style={{ padding: "4px 14px 10px" }}>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search conversations..."
                className="w-full rounded-lg border border-stone-200 bg-white text-stone-800 placeholder-stone-400 focus:border-violet-400 focus:outline-none"
                style={{ padding: "10px 12px", fontSize: 14, lineHeight: 1.4 }}
              />
            </div>
            <div
              className="min-h-0 flex-1 overflow-y-auto"
              style={{ padding: "0 8px" }}
            >
              {pagedConversations.length === 0 ? (
                <div className="py-6 text-center text-stone-400" style={{ fontSize: 13 }}>
                  {conversations.length === 0
                    ? "No conversations yet. Click the + icon to start a new chat."
                    : "No sessions match your search."}
                </div>
              ) : (
                groupConversationsByDate(pagedConversations).map((group) => (
                  <div key={group.label} style={{ marginBottom: 8 }}>
                    <div
                      className="text-stone-400"
                      style={{
                        fontSize: 10,
                        fontWeight: 800,
                        letterSpacing: 0.6,
                        textTransform: "uppercase",
                        padding: "8px 10px 4px",
                      }}
                    >
                      {group.label}
                    </div>
                    {group.items.map((c) => {
                      const isActive = activeId === c.id;
                      const isRenamingThis = renamingId === c.id;
                      const isDeleting = confirmDeleteId === c.id;
                      return (
                        <div
                          key={c.id}
                          className={`group relative flex w-full items-center rounded-md text-left transition ${
                            isActive ? "bg-violet-100" : "hover:bg-stone-50"
                          }`}
                          style={{ padding: "8px 8px 8px 10px", gap: 8 }}
                        >
                          {/* Pin / active indicator */}
                          {c.is_pinned ? (
                            <svg
                              viewBox="0 0 24 24"
                              fill="currentColor"
                              className="flex-shrink-0 text-amber-500"
                              style={{ width: 11, height: 11 }}
                              aria-label="Pinned"
                            >
                              <path d="M16 4l4 4-5 5v6l-3-3-5 5-1.4-1.4 5-5-3-3 6-5z" />
                            </svg>
                          ) : (
                            <span
                              className={`flex-shrink-0 rounded-full ${
                                isActive ? "bg-violet-600" : "bg-stone-300"
                              }`}
                              style={{ width: 6, height: 6, marginLeft: 2 }}
                            />
                          )}

                          {/* Title — either inline-rename input or button */}
                          {isRenamingThis ? (
                            <div className="flex min-w-0 flex-1 items-center gap-1">
                              <input
                                autoFocus
                                value={renameDraft}
                                onChange={(e) => setRenameDraft(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") commitRename();
                                  if (e.key === "Escape") cancelRename();
                                }}
                                onClick={(e) => e.stopPropagation()}
                                className="min-w-0 flex-1 rounded border border-violet-300 bg-white outline-none focus:border-violet-500"
                                style={{ fontSize: 13, padding: "4px 6px" }}
                              />
                              <button
                                type="button"
                                aria-label="Save title"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void commitRename();
                                }}
                                className="rounded bg-violet-600 text-white hover:bg-violet-700"
                                style={{ padding: "4px 7px", fontSize: 11, fontWeight: 700 }}
                              >
                                Save
                              </button>
                              <button
                                type="button"
                                aria-label="Cancel rename"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  cancelRename();
                                }}
                                className="rounded border border-stone-300 text-stone-600 hover:bg-stone-100"
                                style={{ padding: "4px 7px", fontSize: 11, fontWeight: 700 }}
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              onClick={() => pickConversation(c.id)}
                              className="min-w-0 flex-1 text-left"
                            >
                              <div
                                className={`truncate font-medium ${
                                  isActive ? "text-violet-900" : "text-stone-700"
                                }`}
                                style={{ fontSize: 13 }}
                              >
                                {c.title}
                              </div>
                              <div
                                className="truncate text-stone-400"
                                style={{ fontSize: 11, marginTop: 2 }}
                              >
                                {formatSessionMeta(c)}
                              </div>
                            </button>
                          )}

                          {/* Right side — relative time fades out on hover so
                              action buttons can appear in its place. */}
                          {!isRenamingThis && !isDeleting && (
                            <>
                              <span
                                className={`flex-shrink-0 text-stone-400 ${
                                  isActive ? "opacity-0" : "group-hover:opacity-0"
                                }`}
                                style={{ fontSize: 11, transition: "opacity 0.12s ease" }}
                              >
                                {formatRelative(c.updated_at)}
                              </span>
                              <div
                                className={`absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-0.5 transition ${
                                  isActive ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                                }`}
                              >
                                <SessionActionButton
                                  label={c.is_pinned ? "Unpin" : "Pin"}
                                  onClick={() => void togglePin(c.id, Boolean(c.is_pinned))}
                                >
                                  <svg viewBox="0 0 24 24" fill={c.is_pinned ? "#f59e0b" : "none"} stroke="currentColor" strokeWidth={2} style={{ width: 13, height: 13 }}>
                                    <path d="M16 4l4 4-5 5v6l-3-3-5 5-1.4-1.4 5-5-3-3 6-5z" />
                                  </svg>
                                </SessionActionButton>
                                <SessionActionButton
                                  label="Rename"
                                  onClick={() => beginRename(c.id, c.title)}
                                >
                                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} style={{ width: 13, height: 13 }}>
                                    <path d="M12 20h9" />
                                    <path d="M16.5 3.5a2.121 2.121 0 113 3L7 19l-4 1 1-4 12.5-12.5z" />
                                  </svg>
                                </SessionActionButton>
                                <SessionActionButton
                                  label="Delete"
                                  danger
                                  onClick={() => setConfirmDeleteId(c.id)}
                                >
                                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} style={{ width: 13, height: 13 }}>
                                    <path d="M3 6h18" />
                                    <path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2" />
                                    <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
                                  </svg>
                                </SessionActionButton>
                              </div>
                            </>
                          )}

                          {/* Inline delete confirm — replaces the row's right side */}
                          {isDeleting && (
                            <div className="flex items-center gap-2">
                              <span className="text-red-600" style={{ fontSize: 11, fontWeight: 600 }}>
                                Delete?
                              </span>
                              <button
                                type="button"
                                onClick={() => void commitDelete(c.id)}
                                className="rounded bg-red-600 text-white hover:bg-red-700"
                                style={{ padding: "3px 8px", fontSize: 11, fontWeight: 700 }}
                              >
                                Yes
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmDeleteId(null)}
                                className="rounded border border-stone-300 text-stone-600 hover:bg-stone-100"
                                style={{ padding: "3px 8px", fontSize: 11, fontWeight: 700 }}
                              >
                                No
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
            {totalPages > 1 && (
              <div
                className="flex items-center justify-between border-t border-stone-100"
                style={{ padding: "8px 14px" }}
              >
                <button
                  type="button"
                  onClick={() => setHistoryPage((p) => Math.max(0, p - 1))}
                  disabled={currentPage === 0}
                  className="rounded-md text-stone-500 transition hover:bg-stone-100 hover:text-stone-700 disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ padding: "4px 10px", fontSize: 12 }}
                >
                  ← Prev
                </button>
                <span className="text-stone-400" style={{ fontSize: 11 }}>
                  Page {currentPage + 1} of {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => setHistoryPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={currentPage >= totalPages - 1}
                  className="rounded-md text-stone-500 transition hover:bg-stone-100 hover:text-stone-700 disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ padding: "4px 10px", fontSize: 12 }}
                >
                  Next →
                </button>
              </div>
            )}
          </div>
        )}

        {/* Thread */}
        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto"
          style={{ padding: "20px 18px" }}
        >
          {messages.length === 0 && !loadingThread && (
            <ZippyWelcome
              suggestions={suggestions}
              emailConnected={emailConnected}
              hasKnowledge={footerStats.totalFiles > 0}
              onPick={(text) => void sendMessage(text)}
            />
          )}
          {loadingThread && (
            <div className="flex h-full items-center justify-center text-xs text-stone-400">
              Loading conversation…
            </div>
          )}
          <div className="flex flex-col" style={{ gap: 18 }}>
            {messages.map((m) => (
              <ZippyMessageBubble key={m.id} message={m} />
            ))}
            {sending && (
              <div className="flex items-center gap-2 px-1 text-xs text-stone-400">
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400 [animation-delay:-0.2s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400 [animation-delay:-0.1s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400" />
                </span>
                Zippy is thinking…
              </div>
            )}
          </div>
        </div>

        {error && (
          <div className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs text-red-700">
            {error}
          </div>
        )}

        <ZippyComposer disabled={sending} onSubmit={sendMessage} />

        {/* Status chip */}
        <div className="flex items-center gap-2 border-t border-stone-200 bg-white px-4 py-2 text-[11px] text-stone-500">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              footerStats.totalFiles > 0 ? "bg-emerald-500" : "bg-stone-300"
            }`}
          />
          {footerStats.totalFiles > 0 ? (
            <span>
              Grounded in {footerStats.totalFiles} file
              {footerStats.totalFiles === 1 ? "" : "s"}
              {footerStats.lastSynced
                ? ` · Last synced ${footerStats.lastSynced}`
                : ""}
            </span>
          ) : (
            <Link
              to="/settings"
              className="text-violet-600 hover:text-violet-800 hover:underline"
            >
              Connect your Gmail in Settings → Zippy to ground answers in your Drive.
            </Link>
          )}
        </div>
      </aside>
    </>
  );
}


function SessionActionButton({
  children,
  label,
  onClick,
  danger,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={label}
      aria-label={label}
      className={`flex items-center justify-center rounded-md transition ${
        danger
          ? "text-stone-400 hover:bg-red-50 hover:text-red-600"
          : "text-stone-400 hover:bg-stone-100 hover:text-violet-700"
      }`}
      style={{ width: 22, height: 22 }}
    >
      {children}
    </button>
  );
}


// Bucket conversations into date groups for the sidebar. Keeps the order
// stable (within each bucket, we trust the API's order). Pinned items
// always go to a "Pinned" group at the top.
function groupConversationsByDate(
  list: ZippyConversationSummary[],
): Array<{ label: string; items: ZippyConversationSummary[] }> {
  const now = Date.now();
  const oneDay = 86_400_000;
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startOfYesterday = startOfToday - oneDay;
  const weekAgo = startOfToday - 6 * oneDay;
  const monthAgo = startOfToday - 30 * oneDay;

  const pinned: ZippyConversationSummary[] = [];
  const todayItems: ZippyConversationSummary[] = [];
  const yesterdayItems: ZippyConversationSummary[] = [];
  const thisWeek: ZippyConversationSummary[] = [];
  const thisMonth: ZippyConversationSummary[] = [];
  const older: ZippyConversationSummary[] = [];

  for (const c of list) {
    if (c.is_pinned) {
      pinned.push(c);
      continue;
    }
    const ts = new Date(c.updated_at).getTime();
    if (Number.isNaN(ts) || ts > now + 60_000) {
      // Fallback: keep at top if timestamp is garbage.
      todayItems.push(c);
    } else if (ts >= startOfToday) {
      todayItems.push(c);
    } else if (ts >= startOfYesterday) {
      yesterdayItems.push(c);
    } else if (ts >= weekAgo) {
      thisWeek.push(c);
    } else if (ts >= monthAgo) {
      thisMonth.push(c);
    } else {
      older.push(c);
    }
  }

  return [
    { label: "Pinned", items: pinned },
    { label: "Today", items: todayItems },
    { label: "Yesterday", items: yesterdayItems },
    { label: "Earlier this week", items: thisWeek },
    { label: "This month", items: thisMonth },
    { label: "Older", items: older },
  ].filter((g) => g.items.length > 0);
}


function HeaderIconButton({
  children,
  label,
  onClick,
  active,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={`flex h-9 w-9 cursor-pointer items-center justify-center rounded-md transition ${
        active
          ? "bg-violet-100 text-violet-700"
          : "text-stone-500 hover:bg-stone-100 hover:text-stone-700"
      }`}
    >
      {children}
    </button>
  );
}


function ZippyWelcome({
  suggestions,
  emailConnected,
  hasKnowledge,
  onPick,
}: {
  suggestions: string[];
  emailConnected: boolean;
  hasKnowledge: boolean;
  onPick: (text: string) => void;
}) {
  const subtitle = !emailConnected
    ? "Connect your Gmail in Settings → Zippy to let me search your Drive, generate documents, and more."
    : !hasKnowledge
    ? "Pick a Drive folder in Settings so I can answer from your files. I can still generate documents without it."
    : "Ask about files in your Drive, generate a MOM from call notes, or draft an NDA for any jurisdiction.";

  return (
    <div
      className="flex flex-col items-center text-center"
      style={{ padding: "32px 8px 16px", gap: 0 }}
    >
      <div
        className="flex items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-200"
        style={{ width: 56, height: 56 }}
      >
        <svg viewBox="0 0 24 24" fill="currentColor" style={{ width: 26, height: 26 }}>
          <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />
        </svg>
      </div>
      <h2
        className="font-semibold text-stone-900"
        style={{ marginTop: 16, fontSize: 20, lineHeight: 1.3 }}
      >
        How can I help today?
      </h2>
      <p
        className="text-stone-500"
        style={{ marginTop: 8, maxWidth: 360, fontSize: 13.5, lineHeight: 1.55 }}
      >
        {subtitle}
      </p>
      <div
        className="grid w-full grid-cols-1"
        style={{ marginTop: 24, gap: 10 }}
      >
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-xl border border-stone-200 bg-white text-left text-stone-700 shadow-sm transition hover:border-violet-300 hover:bg-violet-50"
            style={{
              padding: "12px 14px",
              fontSize: 13.5,
              lineHeight: 1.5,
            }}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}


function formatError(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  try {
    return JSON.stringify(e);
  } catch {
    return "Something went wrong talking to Zippy.";
  }
}


// "2h", "3d", "now" — compact relative time for the session row.
function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 45) return "now";
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h`;
  if (diffSec < 30 * 86400) return `${Math.round(diffSec / 86400)}d`;
  return `${Math.round(diffSec / (30 * 86400))}mo`;
}


// "Completed in 4s · 2 messages" — or just "N messages" for longer threads
// where wall-clock duration isn't meaningful.
function formatSessionMeta(c: ZippyConversationSummary): string {
  const count = c.message_count;
  const plural = count === 1 ? "" : "s";
  const created = new Date(c.created_at).getTime();
  const updated = new Date(c.updated_at).getTime();
  if (
    !Number.isNaN(created) &&
    !Number.isNaN(updated) &&
    updated >= created
  ) {
    const diffSec = Math.round((updated - created) / 1000);
    if (diffSec > 0 && diffSec < 120) {
      return `Completed in ${diffSec}s · ${count} message${plural}`;
    }
  }
  return `${count} message${plural}`;
}


// Most recent `last_indexed_at` across every file, formatted as "Xm ago".
function computeLastSynced(
  files: Array<{ last_indexed_at: string | null }>,
): string | null {
  let latest = 0;
  for (const f of files) {
    if (!f.last_indexed_at) continue;
    const t = new Date(f.last_indexed_at).getTime();
    if (!Number.isNaN(t) && t > latest) latest = t;
  }
  if (latest === 0) return null;
  const diffSec = Math.max(0, Math.round((Date.now() - latest) / 1000));
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}

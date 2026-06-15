import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

// Shared state so any page (deal drawer, meeting detail, etc.) can prefill a
// message and pop open the Zippy panel without having to lift state per-page.
export interface ZippyContextValue {
  open: boolean;
  setOpen: (v: boolean) => void;
  activeConversationId: string | null;
  setActiveConversationId: (id: string | null) => void;
  pendingMessage: string | null;
  setPendingMessage: (msg: string | null) => void;
}

const noop = () => {};

export const ZippyContext = createContext<ZippyContextValue>({
  open: false,
  setOpen: noop,
  activeConversationId: null,
  setActiveConversationId: noop,
  pendingMessage: null,
  setPendingMessage: noop,
});

export function ZippyProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [pendingMessage, setPendingMessage] = useState<string | null>(null);

  // Memoize so consumers don't re-render on every provider render (this wraps
  // the whole app shell, so it re-renders on each navigation). useState setters
  // are referentially stable, so only the three values need to be deps.
  const value = useMemo<ZippyContextValue>(
    () => ({ open, setOpen, activeConversationId, setActiveConversationId, pendingMessage, setPendingMessage }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open, activeConversationId, pendingMessage],
  );

  return <ZippyContext.Provider value={value}>{children}</ZippyContext.Provider>;
}

export function useZippy() {
  return useContext(ZippyContext);
}

import { createContext, useContext, useState, type ReactNode } from "react";

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

  return (
    <ZippyContext.Provider
      value={{
        open,
        setOpen,
        activeConversationId,
        setActiveConversationId,
        pendingMessage,
        setPendingMessage,
      }}
    >
      {children}
    </ZippyContext.Provider>
  );
}

export function useZippy() {
  return useContext(ZippyContext);
}

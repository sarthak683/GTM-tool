import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";

type ToastTone = "success" | "error" | "info" | "warning";

type Toast = {
  id: number;
  tone: ToastTone;
  message: string;
  title?: string;
  durationMs: number;
};

type ToastInput = {
  tone?: ToastTone;
  message: string;
  title?: string;
  durationMs?: number;
};

type ToastContextValue = {
  show: (input: ToastInput) => number;
  success: (message: string, title?: string) => number;
  error: (message: string, title?: string) => number;
  info: (message: string, title?: string) => number;
  warning: (message: string, title?: string) => number;
  dismiss: (id: number) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const TOAST_STYLES: Record<ToastTone, { bg: string; border: string; title: string; body: string; Icon: typeof CheckCircle2 }> = {
  success: { bg: "#f0fbf4", border: "#b7e6c5", title: "#1f7a47", body: "#28533a", Icon: CheckCircle2 },
  error: { bg: "#fff4f2", border: "#f3c3ba", title: "#c53030", body: "#7d2d2d", Icon: XCircle },
  info: { bg: "#f0f6ff", border: "#c8daf8", title: "#1a4fa8", body: "#27446d", Icon: Info },
  warning: { bg: "#fff7eb", border: "#f0d4ac", title: "#a46206", body: "#7b560e", Icon: AlertTriangle },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextIdRef = useRef(1);

  const dismiss = (id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  };

  const show = ({ tone = "info", message, title, durationMs = 5000 }: ToastInput) => {
    const id = nextIdRef.current++;
    setToasts((current) => [...current, { id, tone, message, title, durationMs }]);
    return id;
  };

  useEffect(() => {
    if (!toasts.length) return;
    const timers = toasts.map((toast) =>
      window.setTimeout(() => dismiss(toast.id), toast.durationMs)
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [toasts]);

  useEffect(() => {
    const originalAlert = window.alert.bind(window);
    window.alert = (message?: string) => {
      show({
        tone: "info",
        title: "Notice",
        message: typeof message === "string" ? message : String(message ?? ""),
      });
    };
    return () => {
      window.alert = originalAlert;
    };
  }, []);

  return (
    <ToastContext.Provider
      value={{
        show,
        dismiss,
        success: (message, title) => show({ tone: "success", message, title }),
        error: (message, title) => show({ tone: "error", message, title, durationMs: 6500 }),
        info: (message, title) => show({ tone: "info", message, title }),
        warning: (message, title) => show({ tone: "warning", message, title, durationMs: 6500 }),
      }}
    >
      {children}
      <div
        style={{
          position: "fixed",
          top: 18,
          right: 18,
          display: "grid",
          gap: 10,
          zIndex: 2000,
          width: "min(360px, calc(100vw - 32px))",
          pointerEvents: "none",
        }}
      >
        {toasts.map((toast) => {
          const tone = TOAST_STYLES[toast.tone];
          const Icon = tone.Icon;
          return (
            <div
              key={toast.id}
              style={{
                pointerEvents: "auto",
                display: "grid",
                gridTemplateColumns: "auto 1fr auto",
                gap: 12,
                alignItems: "start",
                padding: "14px 14px 14px 12px",
                borderRadius: 16,
                border: `1px solid ${tone.border}`,
                background: tone.bg,
                boxShadow: "0 16px 36px rgba(15, 23, 42, 0.12)",
              }}
            >
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 12,
                  display: "grid",
                  placeItems: "center",
                  background: "#fff",
                  color: tone.title,
                }}
              >
                <Icon size={17} />
              </div>
              <div style={{ minWidth: 0 }}>
                {toast.title && (
                  <div style={{ fontSize: 13, fontWeight: 800, color: tone.title, marginBottom: 4 }}>
                    {toast.title}
                  </div>
                )}
                <div style={{ fontSize: 13, lineHeight: 1.5, color: tone.body, whiteSpace: "pre-wrap" }}>
                  {toast.message}
                </div>
              </div>
              <button
                type="button"
                onClick={() => dismiss(toast.id)}
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 10,
                  border: "none",
                  background: "transparent",
                  color: tone.title,
                  cursor: "pointer",
                }}
                aria-label="Dismiss notification"
              >
                <X size={15} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const value = useContext(ToastContext);
  if (!value) {
    throw new Error("useToast must be used inside ToastProvider");
  }
  return value;
}

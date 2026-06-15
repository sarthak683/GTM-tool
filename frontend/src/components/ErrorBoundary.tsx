import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Top-level error boundary. Catches render-time exceptions anywhere in the
 * tree so a single bad component cannot take down the whole app with a blank
 * white screen. Placed OUTSIDE <Suspense> so it also covers runtime render
 * errors, not just lazy-chunk loading failures.
 */
export default class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    // Render the fallback UI on the next render.
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Surface the failure in the console for local debugging / log capture.
    console.error("Uncaught render error:", error, errorInfo);
    // TODO: forward to Sentry (e.g. Sentry.captureException(error, { extra: errorInfo }))
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div
          style={{
            position: "fixed",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "24px",
            background:
              "radial-gradient(120% 70% at 50% -5%, rgba(154, 206, 61, 0.12), transparent 55%), linear-gradient(135deg, #0b0c0e 0%, #0a0b0d 60%, #08090a 100%)",
            overflow: "auto",
          }}
        >
          <div
            role="alert"
            style={{
              background: "rgba(15, 17, 19, 0.85)",
              border: "1px solid rgba(154, 206, 61, 0.18)",
              borderRadius: "16px",
              padding: "40px",
              maxWidth: "460px",
              width: "100%",
              textAlign: "center",
              backdropFilter: "blur(20px)",
              boxShadow: "0 30px 80px rgba(0, 0, 0, 0.45)",
            }}
          >
            <h1
              style={{
                color: "#e2e8f0",
                fontSize: "20px",
                fontWeight: 600,
                marginBottom: "10px",
              }}
            >
              Something went wrong
            </h1>
            <p
              style={{
                color: "#8f98bd",
                fontSize: "14px",
                lineHeight: 1.5,
                marginBottom: "28px",
              }}
            >
              An unexpected error stopped this page from loading. Your data is
              safe — try reloading to get back to work.
            </p>
            <button
              type="button"
              onClick={this.handleReload}
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                padding: "11px 28px",
                borderRadius: "10px",
                background: "#9ace3d",
                border: "none",
                color: "#0b0c0e",
                fontSize: "15px",
                fontWeight: 600,
                cursor: "pointer",
                transition: "filter 0.2s",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.filter = "brightness(1.08)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.filter = "none";
              }}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

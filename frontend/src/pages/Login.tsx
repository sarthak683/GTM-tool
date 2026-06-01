import { authApi } from "../lib/api";

export default function Login() {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        width: "100%",
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background:
          "radial-gradient(120% 70% at 50% -5%, rgba(154, 206, 61, 0.12), transparent 55%), linear-gradient(135deg, #0b0c0e 0%, #0a0b0d 60%, #08090a 100%)",
        padding: "24px",
        overflow: "auto",
      }}
    >
      <div
        className="beacon-pop"
        style={{
          background: "rgba(15, 17, 19, 0.85)",
          border: "1px solid rgba(154, 206, 61, 0.18)",
          borderRadius: "16px",
          padding: "48px",
          maxWidth: "420px",
          width: "100%",
          textAlign: "center",
          backdropFilter: "blur(20px)",
          boxShadow: "0 30px 80px rgba(0, 0, 0, 0.45)",
        }}
      >
        {/* Brand */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "12px", marginBottom: "8px" }}>
          <div
            style={{
              width: "44px",
              height: "44px",
              borderRadius: "13px",
              background: "#0d0e10",
              border: "1px solid rgba(154, 206, 61, 0.32)",
              boxShadow: "0 0 18px rgba(154, 206, 61, 0.22)",
              display: "grid",
              placeItems: "center",
              overflow: "hidden",
            }}
          >
            <img src="/beacon-logo.jpg" alt="Beacon" style={{ width: "76%", height: "76%", objectFit: "contain" }} />
          </div>
          <span style={{ fontSize: "24px", fontWeight: 700, color: "#e2e8f0" }}>
            beacon.li
          </span>
        </div>
        <p style={{ color: "#8f98bd", fontSize: "14px", marginBottom: "40px" }}>
          GTM Execution Workspace
        </p>

        {/* Sign in */}
        <h2 style={{ color: "#e2e8f0", fontSize: "20px", fontWeight: 600, marginBottom: "8px" }}>
          Sign in to your workspace
        </h2>
        <p style={{ color: "#64748b", fontSize: "13px", marginBottom: "32px" }}>
          Use your Google account to continue
        </p>

        <a
          href={authApi.googleLoginUrl()}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "12px",
            padding: "12px 24px",
            borderRadius: "10px",
            background: "rgba(255, 255, 255, 0.05)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            color: "#e2e8f0",
            fontSize: "15px",
            fontWeight: 500,
            textDecoration: "none",
            cursor: "pointer",
            transition: "all 0.2s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "rgba(255, 255, 255, 0.1)";
            e.currentTarget.style.borderColor = "rgba(154, 206, 61, 0.45)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "rgba(255, 255, 255, 0.05)";
            e.currentTarget.style.borderColor = "rgba(255, 255, 255, 0.1)";
          }}
        >
          <svg width="18" height="18" viewBox="0 0 48 48">
            <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
            <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
            <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
            <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
          </svg>
          Continue with Google
        </a>

        <p style={{ color: "#475569", fontSize: "11px", marginTop: "32px" }}>
          First sign-in automatically creates an admin account
        </p>
      </div>
    </div>
  );
}

export default function PreMeetingPlaceholder() {
  return (
    <div className="crm-page" style={{ display: "grid", gap: 18 }}>
      <section
        className="crm-panel"
        style={{
          padding: 28,
          minHeight: 280,
          display: "grid",
          alignItems: "start",
        }}
      >
        <div>
          <h2 style={{ fontSize: 28, fontWeight: 800, color: "#1d2b3c", marginBottom: 8 }}>
            Pre-Meeting Assistance
          </h2>
          <p className="crm-muted" style={{ maxWidth: 640 }}>
            Nothing is being shown here yet.
          </p>
        </div>
      </section>
    </div>
  );
}

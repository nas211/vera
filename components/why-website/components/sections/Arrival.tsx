export function Arrival() {
  return (
    <section style={{
      minHeight: "78vh",
      display: "flex",
      flexDirection: "column",
      justifyContent: "center",
      borderBottom: "1px solid var(--steel-soft)",
    }}>
      <div className="wrap">
        <div className="eyebrow reveal">you&apos;ve arrived</div>
        <h2 className="reveal" style={{
          fontFamily: "var(--font-display, 'Bricolage Grotesque'), sans-serif",
          fontWeight: 800,
          fontSize: "clamp(40px, 7vw, 82px)",
          letterSpacing: "-.03em",
          lineHeight: 0.96,
          marginTop: 14,
        }}>
          Inside the <span className="molten-text">forge.</span>
        </h2>
        <p className="reveal" style={{
          color: "var(--ash)",
          maxWidth: "48ch",
          marginTop: 18,
          fontSize: 17,
        }}>
          This is where it happens. Scroll, and watch a strategy live its whole
          life — born, tested, deployed, and killed the moment it stops printing.
        </p>
        <div className="reveal" style={{
          fontFamily: "var(--font-mono, 'JetBrains Mono'), monospace",
          fontSize: 11,
          letterSpacing: ".2em",
          textTransform: "uppercase",
          color: "var(--ash-dim)",
          marginTop: 40,
          animation: "hintpulse 2s infinite",
        }}>
          scroll to watch ↓
        </div>
      </div>
    </section>
  );
}

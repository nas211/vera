"use client";

const ITEMS = [
  {
    href: "/trades",
    label: "Performance",
    blurb: "The Paper book, shown trade by trade with clear simulated results.",
    tag: "live",
  },
  {
    href: "/profile",
    label: "Paper trading",
    blurb: "Create a free account and run production strategies with $25,000 simulated USD.",
    tag: "free",
  },
  {
    href: "/#pricing",
    label: "Live trading",
    blurb: "Connect a supported exchange on Core, Pro or Prime. Funds stay on your exchange.",
    tag: "free",
  },
] as const;

export function Products() {
  return (
    <section aria-label="What is running" style={{ padding: "0 0 12px" }}>
      <div className="wrap">
        <div
          className="reveal"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(215px, 1fr))",
            gap: 12,
          }}
        >
          {ITEMS.map(({ href, label, blurb, tag }) => (
            <a
              key={href}
              href={href}
              style={{
                display: "block",
                padding: "17px 18px",
                border: "1px solid var(--steel-soft)",
                borderRadius: 12,
                background: "rgba(255,255,255,.015)",
                transition: "border-color .2s, background .2s, transform .2s",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "var(--steel)";
                e.currentTarget.style.background = "rgba(255,255,255,.04)";
                e.currentTarget.style.transform = "translateY(-2px)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "var(--steel-soft)";
                e.currentTarget.style.background = "rgba(255,255,255,.015)";
                e.currentTarget.style.transform = "translateY(0)";
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 7 }}>
                <span
                  style={{
                    fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                    fontSize: 13.5,
                    fontWeight: 600,
                    color: "var(--platinum)",
                  }}
                >
                  {label}
                </span>
                {tag && (
                  <span
                    style={{
                      fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                      fontSize: 9.5,
                      letterSpacing: ".08em",
                      textTransform: "uppercase",
                      color: "var(--ash)",
                      border: "1px solid var(--steel-soft)",
                      borderRadius: 999,
                      padding: "2px 7px",
                    }}
                  >
                    {tag}
                  </span>
                )}
                <span style={{ marginLeft: "auto", fontSize: 13, color: "var(--steel)" }} aria-hidden>
                  →
                </span>
              </div>
              <p style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--ash)", margin: 0 }}>{blurb}</p>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
}

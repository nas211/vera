"use client";

export const PLANS = [
  {
    id: "free",
    name: "WHY Free",
    monthly: 0,
    annual: 0,
    tag: null,
    blurb: "Trade LIVE through one supported exchange or start in PAPER. No card required.",
    features: [
      "LIVE trading on 1 supported exchange",
      "$25,000 simulated Paper balance",
      "Core production strategies in Paper",
      "Standard risk controls",
      "LIVE & Paper trade history",
      "Basic Paper analytics",
      "Account & profile access",
    ],
    cta: "Start Free",
  },
  {
    id: "core",
    name: "WHY Core",
    monthly: 49,
    annual: 490,
    tag: null,
    blurb: "More exchange capacity, analytics and support.",
    features: [
      "Live trading + Paper trading",
      "$25,000 simulated Paper balance",
      "Up to 2 connected LIVE supported exchanges",
      "Core production strategies",
      "Full user risk controls",
      "Trade history & performance analytics",
      "Standard support",
    ],
    cta: "Coming soon",
  },
  {
    id: "pro",
    name: "WHY Pro",
    monthly: 129,
    annual: 1290,
    tag: "Coming soon",
    blurb: "Every production strategy, deeper risk control, more venues.",
    features: [
      "Live trading + Paper trading",
      "$25,000 simulated Paper balance",
      "Up to 3 connected LIVE supported exchanges",
      "All production strategies",
      "Full user risk controls",
      "Independent settings per exchange",
      "Advanced analytics",
      "Priority support",
      "Early access to new production features",
    ],
    cta: "Coming soon",
  },
  {
    id: "prime",
    name: "WHY Prime",
    monthly: 299,
    annual: 2990,
    tag: null,
    blurb: "Everything in Pro, plus what has not shipped yet.",
    features: [
      "Live trading + Paper trading",
      "$25,000 simulated Paper balance",
      "Unlimited supported LIVE exchanges",
      "Everything in Pro",
      "Early access to new strategies & features",
      "Advanced analytics",
      "Priority / direct support",
    ],
    cta: "Coming soon",
  },
] as const;

export type PlanId = (typeof PLANS)[number]["id"];

const mono = 'var(--font-mono, "JetBrains Mono"), monospace';
const display = 'var(--font-display, "Bricolage Grotesque"), sans-serif';

export function Pricing() {
  const annual = false;

  return (
    <section id="pricing" aria-label="Pricing" style={{ padding: "80px 0 72px", borderTop: "1px solid var(--steel-soft)" }}>
      <div className="wrap">
        <div className="reveal" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 24, flexWrap: "wrap" }}>
          <div style={{ maxWidth: "58ch" }}>
            <div className="eyebrow" style={{ color: "var(--gold)" }}>Pricing</div>
            <h2 style={{ fontFamily: display, fontWeight: 600, fontSize: "clamp(30px, 4.4vw, 50px)", letterSpacing: "-.025em", lineHeight: 1.02, marginTop: 14 }}>
              One engine.<br />
              <span className="molten-text">Pick your seat.</span>
            </h2>
            <p style={{ color: "var(--ash)", fontSize: 16, maxWidth: "46ch", lineHeight: 1.55, marginTop: 16 }}>
              LIVE trading through one supported exchange and PAPER with a $25,000 simulated account are free. Paid plans and payment options are Coming soon.
            </p>
          </div>

          {/* Monthly / Annual toggle */}
          <div role="group" aria-label="Billing period" style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: 4, padding: 4, border: "1px solid var(--steel)", borderRadius: 999, background: "var(--carbon-2)" }}>
            {(["monthly", "annual"] as const).map((k) => {
              const on = annual === (k === "annual");
              return (
                <button
                  key={k}
                  type="button"
                  disabled
                  aria-pressed={on}
                  style={{
                    fontFamily: mono, fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase",
                    padding: "8px 16px", borderRadius: 999, border: "none", cursor: "not-allowed",
                    background: on ? "var(--molten)" : "transparent",
                    color: on ? "#1a0d05" : "var(--ash)",
                    transition: "background .2s, color .2s",
                  }}
                >
                  {k === "monthly" ? "Monthly" : "Annual"} · Coming soon
                </button>
              );
            })}
            <span style={{ fontFamily: mono, fontSize: 10.5, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--green)", padding: "0 12px 0 8px", whiteSpace: "nowrap" }}>
              Payments · Coming soon
            </span>
          </div>
        </div>

        <div className="pricing-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginTop: 40, alignItems: "stretch" }}>
          {PLANS.map((p) => {
            const hot = p.id === "pro";
            const price = annual ? p.annual : p.monthly;
            return (
              <div
                key={p.id}
                className="reveal"
                style={{
                  position: "relative",
                  display: "flex", flexDirection: "column",
                  padding: hot ? 1 : 0,
                  borderRadius: 12,
                  background: hot ? "var(--molten)" : "transparent",
                }}
              >
                <div style={{
                  flex: 1, display: "flex", flexDirection: "column",
                  padding: "28px 26px 26px",
                  borderRadius: hot ? 11 : 12,
                  border: hot ? "none" : "1px solid var(--steel-soft)",
                  background: hot ? "linear-gradient(180deg, #16120f, var(--carbon-2))" : "rgba(255,255,255,.015)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                    <span style={{ fontFamily: mono, fontSize: 13.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--platinum)" }}>
                      {p.name}
                    </span>
                    {p.id !== "free" && (
                      <span style={{ fontFamily: mono, fontSize: 9.5, letterSpacing: ".12em", textTransform: "uppercase", color: "#1a0d05", background: "var(--molten)", borderRadius: 999, padding: "4px 9px", fontWeight: 700 }}>
                        Coming soon
                      </span>
                    )}
                  </div>

                  <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 18 }}>
                    <span className={hot ? "molten-text" : undefined} style={{ fontFamily: mono, fontSize: 40, fontWeight: 700, letterSpacing: "-.03em", lineHeight: 1, color: hot ? undefined : "var(--platinum)" }}>
                      ${price.toLocaleString("en-US")}
                    </span>
                    {p.id !== "free" && <span style={{ fontFamily: mono, fontSize: 12, color: "var(--ash)" }}>/{annual ? "year" : "month"}</span>}
                  </div>
                  <div style={{ fontFamily: mono, fontSize: 11, color: "var(--ash-dim)", marginTop: 6, minHeight: 16 }}>
                    {p.id === "free" ? "No payment required" : annual ? `$${(p.annual / 12).toFixed(2)} / month, billed yearly` : `or $${p.annual.toLocaleString("en-US")} / year`}
                  </div>

                  <p style={{ color: "var(--ash)", fontSize: 14, lineHeight: 1.55, margin: "16px 0 0" }}>{p.blurb}</p>

                  <ul style={{ listStyle: "none", padding: 0, margin: "20px 0 26px", display: "grid", gap: 9 }}>
                    {p.features.map((f) => (
                      <li key={f} style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13.5, lineHeight: 1.5, color: "var(--platinum)" }}>
                        <span aria-hidden style={{ color: hot ? "var(--gold)" : "var(--green)", fontFamily: mono, fontSize: 12, marginTop: 2 }}>✓</span>
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>

                  {p.id === "free" ? (
                    <a href="/profile?signup=1" className="btn btn-ghost"
                      style={{ marginTop: "auto", textAlign: "center", fontSize: 13, padding: "12px 18px" }}>
                      {p.cta} →
                    </a>
                  ) : (
                    <button type="button" disabled className={hot ? "btn" : "btn btn-ghost"}
                      style={{ marginTop: "auto", textAlign: "center", fontSize: 13, padding: "12px 18px", cursor: "not-allowed", opacity: 0.6 }}>
                      Coming soon
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <p className="reveal" style={{ fontFamily: mono, fontSize: 11.5, lineHeight: 1.7, color: "var(--ash-dim)", marginTop: 22, maxWidth: "88ch" }}>
          Free accounts require no payment and include PAPER plus LIVE trading through one supported exchange. Paid plans and all payment options are Coming soon. Prices shown are in USD, excluding VAT.
        </p>
      </div>

      <style>{`
        @media(max-width: 900px){ .pricing-grid{ grid-template-columns: 1fr !important; max-width: 520px; margin-left: auto !important; margin-right: auto !important; } }
      `}</style>
    </section>
  );
}

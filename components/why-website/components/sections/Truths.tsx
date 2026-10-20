"use client";

import { useEffect } from "react";

const CARDS = [
  {
    big: "24/7",
    h3: "Never misses a move",
    p: "Crypto doesn't close, and neither does the forge. Every candle, every pair, every funding flip — caught while you're offline.",
  },
  {
    big: "0",
    h3: "No paper hands",
    p: "No fear, no FOMO, no revenge trades at 3am. The engine follows the edge and dumps losers the second they decay.",
  },
  {
    big: "<40ms",
    h3: "Faster than your finger",
    p: "Signal to fill in under forty milliseconds — gone before you'd even reach for the buy button.",
  },
];

export function Truths() {
  useEffect(() => {
    if (!("IntersectionObserver" in window)) {
      document.querySelectorAll(".truth.reveal").forEach((el) => el.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }),
      { threshold: 0.18 }
    );
    document.querySelectorAll(".truth.reveal").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  return (
    <section id="about" style={{ padding: "80px 0" }}>
      <div className="wrap">
        <div style={{ maxWidth: "62ch" }}>
          <div className="eyebrow">Why a machine beats your hands</div>
          <h2 style={{
            fontFamily: "var(--font-display, 'Bricolage Grotesque'), sans-serif",
            fontWeight: 600,
            fontSize: "clamp(30px, 4.4vw, 50px)",
            letterSpacing: "-.025em",
            lineHeight: 1.02,
            marginTop: 14,
          }}>
            You sleep. It doesn&apos;t.
          </h2>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 1,
          background: "var(--steel-soft)",
          border: "1px solid var(--steel-soft)",
          borderRadius: 6,
          marginTop: 44,
          overflow: "hidden",
        }}>
          {CARDS.map((c) => (
            <div key={c.h3} className="truth reveal" style={{
              background: "var(--carbon)",
              padding: "32px 26px",
              position: "relative",
            }}>
              <div className="molten-text" style={{
                fontFamily: "var(--font-mono, 'JetBrains Mono'), monospace",
                fontSize: 32,
                fontWeight: 700,
                letterSpacing: "-.02em",
              }}>
                {c.big}
              </div>
              <h3 style={{
                fontFamily: "var(--font-display, 'Bricolage Grotesque'), sans-serif",
                fontWeight: 600,
                fontSize: 19,
                marginTop: 14,
                marginBottom: 8,
              }}>
                {c.h3}
              </h3>
              <p style={{ color: "var(--ash)", fontSize: 14.5 }}>{c.p}</p>
            </div>
          ))}
        </div>
      </div>

      <style>{`
        @media(max-width:780px){
          .truths-grid{grid-template-columns:1fr!important}
        }
      `}</style>
    </section>
  );
}

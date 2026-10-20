"use client";

import { useEffect, useRef } from "react";
import { MINT_ITEMS } from "@/data/agent";

// Scrolling live feed of WHY Engine trade actions
// Two columns scrolling in opposite directions

const FEED_A = [...MINT_ITEMS, ...MINT_ITEMS, ...MINT_ITEMS]; // tripled for seamless loop
const FEED_B = [...MINT_ITEMS].reverse().concat([...MINT_ITEMS].reverse(), [...MINT_ITEMS].reverse());

const ACTION_COLOR: Record<string, string> = {
  MINT: "var(--green)",
  CUT:  "var(--red)",
  ARBIT:"var(--gold)",
  SCALP:"var(--gold)",
};

function FeedItem({ sym, action, pct, pos }: typeof MINT_ITEMS[0]) {
  const col = ACTION_COLOR[action] ?? "var(--ash)";
  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "10px 16px",
      borderRadius: 6,
      background: "rgba(18,16,26,0.7)",
      border: "1px solid var(--steel-soft)",
      marginBottom: 8,
      flexShrink: 0,
    }}>
      <span style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 13, fontWeight: 700, color: "var(--platinum)", minWidth: 44 }}>{sym}</span>
      <span style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 9, letterSpacing: ".14em", color: col, background: `${col}18`, padding: "2px 6px", borderRadius: 3 }}>{action}</span>
      <div style={{ marginLeft: "auto", textAlign: "right" }}>
        <div style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 12, color: col, fontWeight: 600 }}>{pct}</div>
        <div style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 10, color: "var(--ash-dim)" }}>{pos}</div>
      </div>
    </div>
  );
}

function ScrollFeed({ items, dir }: { items: typeof FEED_A; dir: "up" | "down" }) {
  return (
    <div className="holdy-scroll-feed" style={{ height: 440, overflow: "hidden", position: "relative" }}>
      {/* Top/bottom fade masks */}
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 80, background: "linear-gradient(to bottom, var(--carbon), transparent)", zIndex: 2, pointerEvents: "none" }} />
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 80, background: "linear-gradient(to top, var(--carbon), transparent)", zIndex: 2, pointerEvents: "none" }} />
      <div style={{
        animation: `${dir === "up" ? "feedUp" : "feedDown"} ${items.length * 1.35}s linear infinite`,
        willChange: "transform",
      }}>
        {items.map((item, i) => <FeedItem key={i} {...item} />)}
      </div>
    </div>
  );
}

const STATS = [
  { n: "340",     l: "live strategies" },
  { n: "400+",    l: "pairs monitored" },
  { n: "<40ms",   l: "signal to fill" },
  { n: "99.98%",  l: "uptime" },
];

export function HoldY() {
  return (
    <section style={{ padding: "110px 0", background: "var(--carbon)", borderTop: "1px solid var(--steel-soft)" }}>
      <div className="wrap">

        {/* Heading */}
        <div className="holdy-head-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 50, alignItems: "flex-end", marginBottom: 60 }}>
          <div>
            <div className="eyebrow reveal">live output</div>
            <h2 className="reveal" style={{
              fontFamily: "var(--font-display,'Bricolage Grotesque'),sans-serif",
              fontWeight: 800,
              fontSize: "clamp(36px,5.2vw,62px)",
              letterSpacing: "-.03em",
              lineHeight: 1.0,
              marginTop: 14,
            }}>
              The machine<br />never sleeps.
            </h2>
          </div>
          <p className="reveal" style={{ color: "var(--ash)", fontSize: 16, maxWidth: "38ch", paddingBottom: 4, lineHeight: 1.65 }}>
            Every row is a real decision — a strategy minting profit, cutting a loser, or rotating capital to the next edge. This is the forge, live.
          </p>
        </div>

        {/* Feed columns + stats */}
        <div className="holdy-feed-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 280px", gap: 20, alignItems: "start" }}>

          <div className="reveal"><ScrollFeed items={FEED_A} dir="up" /></div>
          <div className="reveal holdy-col-b"><ScrollFeed items={FEED_B} dir="down" /></div>

          {/* Stats sidebar */}
          <div className="reveal" style={{ paddingTop: 40 }}>
            {STATS.map(s => (
              <div key={s.l} style={{
                padding: "20px 0",
                borderBottom: "1px solid var(--steel-soft)",
              }}>
                <div className="molten-text" style={{
                  fontFamily: "var(--font-mono,'JetBrains Mono'),monospace",
                  fontSize: 34,
                  fontWeight: 700,
                  letterSpacing: "-.02em",
                }}>
                  {s.n}
                </div>
                <div style={{ fontSize: 13, color: "var(--ash)", marginTop: 4, letterSpacing: ".01em" }}>{s.l}</div>
              </div>
            ))}

            <div style={{ marginTop: 28 }}>
              <div style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 10, letterSpacing: ".14em", color: "var(--ash-dim)", textTransform: "uppercase", marginBottom: 10 }}>
                profit factor (30d)
              </div>
              <div style={{ display: "flex", gap: 3 }}>
                {[1.4, 1.2, 1.6, 1.1, 1.8, 1.3, 1.5, 1.9, 1.2, 1.7].map((h, i) => (
                  <div key={i} style={{
                    flex: 1,
                    height: h * 24,
                    background: `rgba(243,199,106,${0.3 + h * 0.22})`,
                    borderRadius: 2,
                    alignSelf: "flex-end",
                  }} />
                ))}
              </div>
              <div style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 11, color: "var(--gold)", marginTop: 8 }}>avg 1.47 PF</div>
            </div>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes feedUp   { from{transform:translateY(0)} to{transform:translateY(-33.33%)} }
        @keyframes feedDown { from{transform:translateY(-33.33%)} to{transform:translateY(0)} }
      `}</style>
    </section>
  );
}

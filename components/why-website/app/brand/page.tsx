import type { Metadata } from "next";
import { LegalPage } from "@/components/legal/LegalPage";

export const metadata: Metadata = { title: "Brand Kit — WHY TRADE", robots: { index: true, follow: true } };

const LOGOS = [
  { file: "logo.svg",         label: "Primary",       bg: "#0A0B0F" },
  { file: "logo-gold.svg",    label: "Gold",          bg: "#0A0B0F" },
  { file: "logo-on-dark.svg", label: "On dark",       bg: "#05060A" },
  { file: "logo-white.svg",   label: "Mono white",    bg: "#262430" },
];
const COLORS = [
  { name: "Molten (gradient)", value: "#C2381A → #F0852A → #F3C76A → #FFE6AE", css: "linear-gradient(100deg,#C2381A,#F0852A 38%,#F3C76A 72%,#FFE6AE)" },
  { name: "Ember",    value: "#F0852A", css: "#F0852A" },
  { name: "Gold",     value: "#F3C76A", css: "#F3C76A" },
  { name: "Gold dim", value: "#8C6F39", css: "#8C6F39" },
  { name: "Platinum", value: "#F0EDF2", css: "#F0EDF2" },
  { name: "Ash",      value: "#8A8595", css: "#8A8595" },
  { name: "Steel",    value: "#262430", css: "#262430" },
  { name: "Ink",      value: "#0A0B0F", css: "#0A0B0F" },
  { name: "Green",    value: "#3DE3A0", css: "#3DE3A0" },
  { name: "Red",      value: "#F0776F", css: "#F0776F" },
];

export default function Brand() {
  return (
    <LegalPage eyebrow="Brand" title="Brand Kit" updated="18 August 2026">
      <p>Assets and rules for referring to WHY TRADE / WHY Protocol. Use them as-is: don’t recolour, skew, add effects or combine the mark with other logos. Don’t imply endorsement, partnership or performance claims WHY has not made.</p>

      <h2>Name</h2>
      <p>Write <strong>WHY TRADE</strong> (all caps, one space) for the product and <strong>WHY Protocol</strong> for the technology brand. The legal entity is Why Protocol LTD. Telegram: <a href="https://t.me/Whytradingbot" target="_blank" rel="noopener">@Whytradingbot</a>.</p>

      <h2>Logo</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(180px,1fr))", gap: 14, margin: "0 0 12px" }}>
        {LOGOS.map(l => (
          <a key={l.file} href={`/${l.file}`} download style={{ textDecoration: "none", border: "1px solid var(--steel)", borderRadius: 12, overflow: "hidden", background: l.bg, display: "block" }}>
            <div style={{ display: "grid", placeItems: "center", height: 130 }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/${l.file}`} alt={`WHY logo — ${l.label}`} width={72} height={72} />
            </div>
            <div style={{ padding: "10px 12px", borderTop: "1px solid var(--steel)", fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 12, color: "var(--ash)", display: "flex", justifyContent: "space-between" }}>
              <span>{l.label}</span><span style={{ color: "var(--gold)" }}>SVG ↓</span>
            </div>
          </a>
        ))}
      </div>
      <p>Keep clear space of at least half the mark’s width on every side. Minimum size 24 px. On busy imagery use the “On dark” or “Mono white” versions.</p>

      <h2>Colour</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))", gap: 12, margin: "0 0 12px" }}>
        {COLORS.map(c => (
          <div key={c.name} style={{ border: "1px solid var(--steel)", borderRadius: 12, overflow: "hidden" }}>
            <div style={{ height: 64, background: c.css }} />
            <div style={{ padding: "10px 12px", fontSize: 12 }}>
              <div style={{ color: "var(--platinum)", fontWeight: 600 }}>{c.name}</div>
              <div style={{ color: "var(--ash)", fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 11, marginTop: 2 }}>{c.value}</div>
            </div>
          </div>
        ))}
      </div>
      <p>Backgrounds are Ink; text is Platinum with Ash for secondary copy. The molten gradient is reserved for the brand mark, headline accents and primary buttons — never for body text.</p>

      <h2>Typography</h2>
      <ul>
        <li><strong>Display:</strong> Bricolage Grotesque, weights 600–800, tight letter-spacing (−0.03em on headlines).</li>
        <li><strong>Body:</strong> Inter, weights 400–600.</li>
        <li><strong>Mono / data:</strong> JetBrains Mono, used for numbers, tickers, eyebrows and buttons.</li>
      </ul>

      <h2>Voice</h2>
      <p>Direct, calm, precise. Talk about what the engine does and what the record shows; never promise returns. Every performance reference must carry the same disclosure the site uses: results are simulated / paper trading unless expressly stated otherwise, and no orders were placed.</p>

      <h2>Contact</h2>
      <p>Press, partnership and asset requests: <a href="https://t.me/Whytradingbot" target="_blank" rel="noopener">@Whytradingbot</a>.</p>
    </LegalPage>
  );
}

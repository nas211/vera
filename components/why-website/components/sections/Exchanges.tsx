"use client";

import { useEffect, useState } from "react";

const mono = 'var(--font-mono, "JetBrains Mono"), monospace';
const display = 'var(--font-display, "Bricolage Grotesque"), sans-serif';

function Venue({ name, mark, status, live }: { name: string; mark: string; status: string; live: boolean }) {
  return (
    <div style={{ border: "1px solid var(--steel-soft)", borderRadius: 12, background: "rgba(255,255,255,.015)", padding: "22px 24px", display: "flex", alignItems: "center", gap: 16 }}>
      <div aria-hidden style={{ width: 48, height: 48, display: "grid", placeItems: "center", borderRadius: 12, background: live ? "linear-gradient(135deg,#F0852A,#C2381A)" : "linear-gradient(135deg,#20242c,#353b47)", color: "white", fontFamily: mono, fontWeight: 800, fontSize: 13, letterSpacing: "-.04em" }}>{mark}</div>
      <div>
        <div style={{ fontFamily: mono, fontSize: 14, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" }}>{name}</div>
        <div style={{ fontFamily: mono, fontSize: 10.5, letterSpacing: ".12em", textTransform: "uppercase", color: live ? "var(--green)" : "var(--gold)", marginTop: 5 }}>{status}</div>
      </div>
    </div>
  );
}

export function Exchanges() {
  const [weexLive, setWeexLive] = useState(false);
  useEffect(() => {
    fetch("/yellow/api/public-config", { cache: "no-store" }).then((r) => r.ok ? r.json() : null)
      .then((v) => setWeexLive(v?.weex_status === "live")).catch(() => {});
  }, []);
  return (
    <section id="how-it-works" aria-label="Supported exchange integrations" style={{ padding: "72px 0", borderTop: "1px solid var(--steel-soft)" }}>
      <div className="wrap reveal" style={{ display: "grid", gridTemplateColumns: "minmax(0,1.15fr) minmax(320px,.85fr)", gap: 44, alignItems: "center" }}>
        <div>
          <div className="eyebrow" style={{ color: "var(--gold)" }}>Non-custodial execution</div>
          <h2 style={{ fontFamily: display, fontWeight: 700, fontSize: "clamp(30px,4.4vw,50px)", letterSpacing: "-.025em", lineHeight: 1.04, marginTop: 14 }}>Trade through<br /><span className="molten-text">your exchange.</span></h2>
          <p style={{ color: "var(--ash)", fontSize: 16, lineHeight: 1.65, maxWidth: "54ch", marginTop: 16 }}>Connect your supported exchange account and let WHY execute directly while your funds remain on the exchange. Use a dedicated key with read and trading permissions only—never withdrawals.</p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 24 }}>
            <a href="/profile?signup=1" className="btn">Start trading</a>
            <a href="#pricing" className="btn btn-ghost">View pricing</a>
          </div>
        </div>
        <div className="exchange-list" style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 12 }}>
          <Venue name="Yellow Pro" mark="Y" status="Live" live />
          <Venue name="WEEX" mark="WEEX" status={weexLive ? "Live" : "Integrating"} live={weexLive} />
        </div>
      </div>
      <style>{`
        @media(max-width:900px){#how-it-works .exchange-list{grid-template-columns:1fr!important}}
        @media(max-width:760px){#how-it-works>.wrap{grid-template-columns:1fr!important}#how-it-works .exchange-list{grid-template-columns:repeat(2,minmax(0,1fr))!important}}
        @media(max-width:460px){#how-it-works .exchange-list{grid-template-columns:1fr!important}}
      `}</style>
    </section>
  );
}

import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";

export function LegalPage({ eyebrow, title, updated, children }: { eyebrow: string; title: string; updated: string; children: React.ReactNode }) {
  return (
    <>
      <Nav />
      <main className="wrap" style={{ maxWidth: 820, padding: "72px 24px 96px" }}>
        <p className="eyebrow" style={{ color: "var(--gold)", letterSpacing: ".2em", fontSize: 12, marginBottom: 14 }}>{eyebrow}</p>
        <h1 style={{ fontFamily: "var(--font-display,'Bricolage Grotesque'),sans-serif", fontWeight: 800, fontSize: "clamp(34px,5vw,54px)", letterSpacing: "-.03em", lineHeight: 1.02, margin: "0 0 10px" }}>{title}</h1>
        <p style={{ color: "var(--ash-dim)", fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 12, margin: "0 0 40px" }}>Last updated {updated}</p>
        <div className="legal">{children}</div>
      </main>
      <Footer />
      <style>{`
        .legal h2{font-family:var(--font-display,'Bricolage Grotesque'),sans-serif;font-weight:700;font-size:20px;letter-spacing:-.01em;margin:36px 0 10px;color:var(--platinum)}
        .legal p,.legal li{color:var(--ash);font-size:15px;line-height:1.7;margin:0 0 12px}
        .legal ul{padding-left:20px;margin:0 0 12px}.legal li{margin-bottom:6px}
        .legal strong{color:var(--platinum);font-weight:600}
        .legal a{color:var(--gold);text-decoration:none}.legal a:hover{text-decoration:underline}
        .legal .box{border:1px solid var(--steel);border-radius:12px;padding:18px 20px;background:rgba(255,255,255,.02);margin:0 0 16px}
      `}</style>
    </>
  );
}

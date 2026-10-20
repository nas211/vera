"use client";

import { Logo } from "@/components/Logo";

export const LINKS = [
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#pricing", label: "Pricing" },
  { href: "/trades", label: "Performance" },
  { href: "/#about", label: "About" },
] as const;

export function Nav() {
  return (
    <nav data-site-chrome="" style={{
      position: "sticky", top: 0, zIndex: 50,
      backdropFilter: "blur(14px)",
      background: "rgba(10,11,15,.74)",
      borderBottom: "1px solid var(--steel-soft)",
    }}>
      <div className="wrap" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", height: 64 }}>
        <a href="/" className="nav-brand" style={{ display: "flex", alignItems: "center", gap: 11 }} aria-label="WHY TRADE home">
          <Logo size={26} />
          <span style={{
            fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
            fontWeight: 700, letterSpacing: ".05em", fontSize: 15,
          }}>
            WHY&nbsp;TRADE
          </span>
        </a>

        <div className="nav-links" style={{ display: "flex", gap: 26, alignItems: "center" }}>
          {LINKS.map(({ href, label }) => (
            <a key={href} href={href} style={{ fontSize: 13.5, color: "var(--ash)", transition: "color .2s", whiteSpace: "nowrap" }}
              onMouseEnter={e => (e.currentTarget.style.color = "var(--platinum)")}
              onMouseLeave={e => (e.currentTarget.style.color = "var(--ash)")}
            >{label}</a>
          ))}
          <a href="/profile" className="nav-account" style={{ fontSize: 13.5, color: "var(--ash)", transition: "color .2s", whiteSpace: "nowrap" }}
            onMouseEnter={e => (e.currentTarget.style.color = "var(--platinum)")}
            onMouseLeave={e => (e.currentTarget.style.color = "var(--ash)")}
          >Account</a>
          <a href="/profile?signup=1" className="btn" style={{ fontSize: 13, padding: "9px 16px", border: "none" }}>
            Start trading
          </a>
        </div>
      </div>
    </nav>
  );
}

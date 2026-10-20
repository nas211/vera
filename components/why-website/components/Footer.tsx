"use client";

import { Logo } from "@/components/Logo";

const NAV_RIGHT = [
  { label: "Help desk",      href: "/help" },
  { label: "Privacy Policy", href: "/privacy" },
  { label: "Terms",          href: "/terms" },
  { label: "Brand Kit",      href: "/brand" },
];

const SOCIALS = [
  {
    label: "X / Twitter",
    href: "",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
      </svg>
    ),
  },
  {
    label: "Telegram",
    href: "https://t.me/Whytradingbot",
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/>
      </svg>
    ),
  },
];

export function Footer() {
  return (
    <footer data-site-chrome="" style={{
      background: "#05060a",
      borderTop: "1px solid rgba(255,255,255,.07)",
    }}>
      {/* Top row */}
      <div className="wrap footer-top" style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "44px 0 36px",
        gap: 24,
        flexWrap: "wrap",
      }}>
        {/* Brand */}
        <a href="/" style={{ display: "flex", alignItems: "center", gap: 11, textDecoration: "none" }} aria-label="WHY TRADE home">
          <Logo size={28} />
          <span style={{
            fontFamily: 'var(--font-mono,"JetBrains Mono"),monospace',
            fontWeight: 700,
            letterSpacing: ".06em",
            fontSize: 15,
            color: "var(--platinum)",
          }}>
            WHY TRADE
          </span>
        </a>

        {/* Nav links */}
        <nav className="footer-nav" style={{ display: "flex", alignItems: "center", gap: 0 }}>
          {NAV_RIGHT.map(l => (
            <a
              key={l.label}
              href={l.href}
              style={{
                fontFamily: 'var(--font-mono,"JetBrains Mono"),monospace',
                fontSize: 13,
                color: "var(--ash)",
                padding: "4px 18px",
                transition: "color .18s",
                textDecoration: "none",
              }}
              onMouseEnter={e => (e.currentTarget.style.color = "var(--platinum)")}
              onMouseLeave={e => (e.currentTarget.style.color = "var(--ash)")}
            >
              {l.label}
            </a>
          ))}
        </nav>
      </div>

      {/* Divider */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,.06)" }} />

      {/* Bottom row */}
      <div className="wrap footer-bot" style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "22px 0 28px",
        gap: 16,
        flexWrap: "wrap",
      }}>
        <span style={{
          fontFamily: 'var(--font-mono,"JetBrains Mono"),monospace',
          fontSize: 12,
          color: "rgba(255,255,255,.28)",
        }}>
          © 2026 WHY TRADE. All rights reserved.
        </span>

        {/* Social icon buttons */}
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {SOCIALS.filter(s => s.href).map(s => (
            <a
              key={s.label}
              href={s.href}
              aria-label={s.label}
              target="_blank" rel="noopener"
              style={{
                width: 38,
                height: 38,
                borderRadius: "50%",
                background: "rgba(255,255,255,.06)",
                border: "1px solid rgba(255,255,255,.10)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "rgba(255,255,255,.45)",
                transition: "background .18s, border-color .18s, color .18s",
                textDecoration: "none",
              }}
              onMouseEnter={e => {
                const el = e.currentTarget as HTMLAnchorElement;
                el.style.background   = "rgba(240,133,42,.14)";
                el.style.borderColor  = "rgba(240,133,42,.45)";
                el.style.color        = "#F0852A";
              }}
              onMouseLeave={e => {
                const el = e.currentTarget as HTMLAnchorElement;
                el.style.background   = "rgba(255,255,255,.06)";
                el.style.borderColor  = "rgba(255,255,255,.10)";
                el.style.color        = "rgba(255,255,255,.45)";
              }}
            >
              {s.icon}
            </a>
          ))}
        </div>
      </div>
    </footer>
  );
}

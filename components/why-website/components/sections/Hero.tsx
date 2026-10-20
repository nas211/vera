"use client";

import { useEffect, useRef } from "react";
import { Logo } from "@/components/Logo";

const SYMS = ['BTC','ETH','SOL','AVAX','LINK','ARB','OP','TON','SUI','INJ','WIF','PEPE'];

function makeRow() {
  const s = SYMS[Math.floor(Math.random() * SYMS.length)];
  const mint = Math.random() > 0.32;
  const pct = ((Math.random() * 2.2) + 0.05).toFixed(2);
  const ms = Math.floor(Math.random() * 46) + 12;
  return { s, mint, pct, ms };
}

export function Hero() {
  const pnlRef = useRef<HTMLSpanElement>(null);
  const curveRef = useRef<HTMLCanvasElement>(null);
  const rollRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);
  const pnlIntervalRef = useRef<ReturnType<typeof setInterval>>(null!);
  const rollIntervalRef = useRef<ReturnType<typeof setInterval>>(null!);
  const runningRef = useRef(false);

  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── PNL counter ─────────────────────────────────────────────────────────────
    let pnl = 4200 + Math.random() * 800;
    function fmtPnl() {
      if (pnlRef.current) pnlRef.current.textContent = '+$' + Math.floor(pnl).toLocaleString('en-US');
    }
    fmtPnl();
    if (!reduce) pnlIntervalRef.current = setInterval(() => { pnl += Math.random() * 38; fmtPnl(); }, 700);

    // ── Canvas: curve + embers ───────────────────────────────────────────────────
    const cv = curveRef.current!;
    const ctx = cv.getContext('2d')!;
    const N = 110;
    let val = 40, t = 0;
    const pts: number[] = [];
    for (let i = 0; i < N; i++) { val += Math.sin(i * 0.2) * 1.0 + (Math.random() - 0.4) * 2.6; pts.push(val); }

    const embers: { x: number; y: number; vy: number; vx: number; r: number; life: number; max: number; c: string }[] = [];

    function size() {
      const r = cv.getBoundingClientRect();
      cv.width = r.width * Math.min(devicePixelRatio, 2);
      cv.height = 150 * Math.min(devicePixelRatio, 2);
      ctx.setTransform(Math.min(devicePixelRatio, 2), 0, 0, Math.min(devicePixelRatio, 2), 0, 0);
    }
    size();
    window.addEventListener('resize', size);

    function molten(g: CanvasGradient) {
      g.addColorStop(0, '#C2381A'); g.addColorStop(.4, '#F0852A');
      g.addColorStop(.74, '#F3C76A'); g.addColorStop(1, '#FFF4D6'); return g;
    }

    function W() { return cv.width / Math.min(devicePixelRatio, 2); }
    const H = 150;

    function draw() {
      const w = W(), h = H, pad = 12;
      ctx.clearRect(0, 0, w, h);
      const min = Math.min(...pts), max = Math.max(...pts), rng = (max - min) || 1;
      const X = (i: number) => pad + i * (w - pad * 2) / (N - 1);
      const Y = (v: number) => h - pad - ((v - min) / rng) * (h - pad * 2);

      // fill under curve
      const fg = ctx.createLinearGradient(0, 0, 0, h);
      fg.addColorStop(0, 'rgba(240,133,42,.22)'); fg.addColorStop(1, 'rgba(240,133,42,0)');
      ctx.beginPath(); ctx.moveTo(X(0), Y(pts[0]));
      for (let i = 1; i < N; i++) ctx.lineTo(X(i), Y(pts[i]));
      ctx.lineTo(X(N - 1), h); ctx.lineTo(X(0), h); ctx.closePath();
      ctx.fillStyle = fg; ctx.fill();

      // molten line
      ctx.beginPath(); ctx.moveTo(X(0), Y(pts[0]));
      for (let i = 1; i < N; i++) ctx.lineTo(X(i), Y(pts[i]));
      ctx.strokeStyle = molten(ctx.createLinearGradient(0, 0, w, 0));
      ctx.lineWidth = 2; ctx.lineJoin = 'round';
      ctx.shadowColor = 'rgba(240,133,42,.6)'; ctx.shadowBlur = 8;
      ctx.stroke(); ctx.shadowBlur = 0;

      // glowing head dot
      const lx = X(N - 1), ly = Y(pts[N - 1]);
      ctx.beginPath(); ctx.arc(lx, ly, 3, 0, 7);
      ctx.fillStyle = '#FFF4D6'; ctx.shadowColor = '#F0852A'; ctx.shadowBlur = 12;
      ctx.fill(); ctx.shadowBlur = 0;

      // embers
      for (let i = embers.length - 1; i >= 0; i--) {
        const e = embers[i]; e.y -= e.vy; e.life -= 1; e.x += e.vx;
        ctx.globalAlpha = Math.max(0, e.life / e.max) * 0.9;
        ctx.beginPath(); ctx.arc(e.x, e.y, e.r, 0, 7); ctx.fillStyle = e.c; ctx.fill();
        ctx.globalAlpha = 1;
        if (e.life <= 0) embers.splice(i, 1);
      }
    }

    function spawnEmber() {
      const w = W();
      embers.push({ x: w * 0.55 + Math.random() * w * 0.4, y: H - 10, vy: .3 + Math.random() * .7, vx: (Math.random() - .5) * .3, r: .6 + Math.random() * 1.4, life: 60 + Math.random() * 40, max: 100, c: Math.random() > .5 ? '#F0852A' : '#F3C76A' });
    }

    function step() {
      if (!runningRef.current) return;
      t++;
      if (t % 3 === 0) { val += Math.sin(t * 0.05) + (Math.random() - 0.4) * 3; pts.push(val); pts.shift(); }
      if (t % 6 === 0) spawnEmber();
      draw();
      rafRef.current = requestAnimationFrame(step);
    }
    draw();
    if (!reduce) {
      runningRef.current = true;
      rafRef.current = requestAnimationFrame(step);
    }

    // ── Mint/cut roll ────────────────────────────────────────────────────────────
    const rollEl = rollRef.current!;
    const rows = Array.from({ length: 14 }, makeRow);
    const doubled = [...rows, ...rows];

    // render rows
    rollEl.innerHTML = doubled.map(({ s, mint, pct, ms }) => `
      <div style="display:grid;grid-template-columns:auto 1fr auto auto;gap:12px;align-items:center;padding:5px 16px;font-family:var(--font-mono,'JetBrains Mono',monospace);font-size:12px;color:var(--ash)">
        <span style="color:var(--platinum);font-weight:500">${s}</span>
        <span style="font-size:10px;letter-spacing:.08em;color:${mint ? 'var(--green)' : 'var(--red)'}">${mint ? '▲ MINTED' : '▼ CUT'}</span>
        <span style="color:var(--gold)">${mint ? '+' : '−'}${pct}%</span>
        <span style="color:var(--ash-dim)">${ms}ms</span>
      </div>
    `).join('');

    if (!reduce) {
      let y = 0;
      rollIntervalRef.current = setInterval(() => {
        y -= 0.4;
        if (y <= -(rollEl.scrollHeight / 2)) y = 0;
        rollEl.style.transform = `translateY(${y}px)`;
      }, 30);
    }

    // ── Reveal on scroll ─────────────────────────────────────────────────────────
    if ('IntersectionObserver' in window && !reduce) {
      const io = new IntersectionObserver(
        (entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }),
        { threshold: 0.18 }
      );
      document.querySelectorAll('.hero-reveal').forEach((el, i) => {
        (el as HTMLElement).style.transitionDelay = `${(i % 4) * 60}ms`;
        io.observe(el);
      });
    } else {
      document.querySelectorAll('.hero-reveal').forEach((el) => el.classList.add('in'));
    }

    // ── Visibility gate ──────────────────────────────────────────────────────────
    const visObs = new IntersectionObserver(([entry]) => {
      runningRef.current = entry.isIntersecting;
      if (entry.isIntersecting) { rafRef.current = requestAnimationFrame(step); }
      else cancelAnimationFrame(rafRef.current);
    }, { threshold: 0.1 });
    if (cv.parentElement) visObs.observe(cv.parentElement.parentElement!);

    return () => {
      cancelAnimationFrame(rafRef.current);
      clearInterval(pnlIntervalRef.current);
      clearInterval(rollIntervalRef.current);
      visObs.disconnect();
      window.removeEventListener('resize', size);
    };
  }, []);

  return (
    <header style={{
      position: "relative",
      padding: "88px 0 64px",
      borderBottom: "1px solid var(--steel-soft)",
      overflow: "hidden",
    }}>
      {/* Ambient glow */}
      <div style={{
        position: "absolute", bottom: -260, left: "50%", transform: "translateX(-50%)",
        width: 900, height: 520,
        background: "radial-gradient(ellipse at center, rgba(240,133,42,.16), rgba(194,56,26,.06) 40%, transparent 70%)",
        pointerEvents: "none",
      }} />

      <div className="wrap" style={{ position: "relative", zIndex: 1 }}>
        <div style={{
          display: "grid",
          gridTemplateColumns: "1.02fr .98fr",
          gap: 50,
          alignItems: "center",
        }} className="hero-grid">

          {/* LEFT */}
          <div>
            <div className="eyebrow hero-reveal reveal">Autonomous alpha engine</div>
            <h1
              className="hero-reveal reveal"
              style={{
                fontFamily: 'var(--font-display, "Bricolage Grotesque"), sans-serif',
                fontSize: "clamp(52px, 8.5vw, 104px)",
                fontWeight: 800,
                lineHeight: 0.94,
                letterSpacing: "-.035em",
                marginTop: 16,
              }}
            >
              Alpha,<br />
              <span className="molten-text">manufactured.</span>
            </h1>

            <p className="hero-reveal reveal" style={{
              color: "var(--ash)",
              fontSize: 17,
              maxWidth: "46ch",
              margin: "22px 0 30px",
              lineHeight: 1.55,
            }}>
              <strong style={{ color: "var(--platinum)", fontWeight: 600 }}>AI-powered crypto trading — fully automated.</strong>
              {" "}Scans 2,000+ trading setups. Finds long and short opportunities. Filters every trade through strict risk rules.
              Executes approved trades through your own connected exchange. Your funds remain on the exchange; WHY never takes custody.
            </p>

            <div className="hero-reveal reveal" style={{ display: "flex", gap: 13, alignItems: "center", flexWrap: "wrap" }}>
              <a href="/profile?signup=1" className="btn">Start trading</a>
              <a href="#pricing" className="btn btn-ghost">View pricing</a>
              <a href="/partners/signup" style={{
                fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                fontSize: 12.5, color: "var(--ash)", textDecoration: "underline",
                textUnderlineOffset: 4, textDecorationColor: "var(--steel)",
              }}>Become a partner</a>
            </div>

            <div className="hero-reveal reveal" style={{
              marginTop: 28,
              fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
              fontSize: 12,
              color: "var(--ash-dim)",
              display: "flex",
              gap: 18,
              flexWrap: "wrap",
            }}>
              <span>printing <strong style={{ color: "var(--gold)", fontWeight: 500 }}>24/7</strong></span>
              <span>strategies live <strong style={{ color: "var(--gold)", fontWeight: 500 }}>340+</strong></span>
              <span>missed pumps <strong style={{ color: "var(--gold)", fontWeight: 500 }}>0</strong></span>
            </div>
          </div>

          {/* RIGHT — Forge panel */}
          <div className="hero-reveal reveal" aria-hidden="true" style={{
            background: "linear-gradient(180deg, var(--carbon), var(--carbon-2))",
            border: "1px solid var(--steel)",
            borderRadius: 6,
            overflow: "hidden",
            boxShadow: "0 40px 90px -45px rgba(194,56,26,.45), 0 30px 80px -50px #000",
          }}>
            {/* Header */}
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "13px 16px", borderBottom: "1px solid var(--steel-soft)",
            }}>
              <span style={{
                fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                fontSize: 11, letterSpacing: ".16em", textTransform: "uppercase", color: "var(--ash)",
              }}>
                live paper feed
              </span>
              <span style={{
                fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                fontSize: 11, color: "var(--ember2)",
                display: "flex", alignItems: "center", gap: 7,
              }}>
                <span style={{
                  width: 7, height: 7, borderRadius: "50%",
                  background: "var(--ember2)", boxShadow: "0 0 8px var(--ember2)",
                  display: "inline-block", animation: "beat 2s infinite",
                }} />
                minting
              </span>
            </div>

            {/* P&L */}
            <div style={{ padding: "22px 18px 8px" }}>
              <div style={{
                fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                fontSize: 11, letterSpacing: ".14em", textTransform: "uppercase",
                color: "var(--ash)", display: "flex", justifyContent: "space-between",
              }}>
                <span>net realized · today</span>
                <span>auto-compounding</span>
              </div>
              <div style={{
                fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                fontWeight: 700,
                fontSize: "clamp(34px, 6vw, 52px)",
                letterSpacing: "-.02em",
                lineHeight: 1.05,
                marginTop: 6,
              }}>
                <span className="molten-text" ref={pnlRef}>+$0</span>
              </div>
            </div>

            {/* Curve */}
            <canvas
              ref={curveRef}
              aria-hidden="true"
              style={{ display: "block", width: "100%", height: 150 }}
            />

            {/* Mint roll */}
            <div style={{
              borderTop: "1px solid var(--steel-soft)",
              height: 118, overflow: "hidden", position: "relative", padding: "6px 0",
            }}>
              <div ref={rollRef} style={{ position: "absolute", width: "100%" }} />
            </div>
          </div>
        </div>
      </div>

      <style>{`
        @media(max-width:920px){.hero-grid{grid-template-columns:1fr!important;gap:34px!important}}
        @media(max-width:680px){.nav-links a:not(.btn):not(.nav-account){display:none}}
      `}</style>
    </header>
  );
}

"use client";

import { useEffect, useRef } from "react";
import { Logo } from "@/components/Logo";

const TARGET  = 2_480_000;
const CHARGE  = 2400; // ms to reach TARGET

export function Finale() {
  const sectionRef  = useRef<HTMLElement>(null);
  const canvasRef   = useRef<HTMLCanvasElement>(null);
  const coreRef     = useRef<HTMLDivElement>(null);
  const numRef      = useRef<HTMLSpanElement>(null);
  const flashRef    = useRef<HTMLDivElement>(null);
  const doneRef     = useRef(false);

  useEffect(() => {
    const sec    = sectionRef.current!;
    const canvas = canvasRef.current!;
    const core   = coreRef.current!;
    const numEl  = numRef.current!;
    const flash  = flashRef.current!;
    if (!sec || !canvas || !core || !numEl || !flash) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const ctx    = canvas.getContext("2d") as CanvasRenderingContext2D;
    let W = 1, H = 1;
    const dpr = Math.min(devicePixelRatio, 2);
    let rafId = 0;

    function size() {
      const r = canvas.getBoundingClientRect();
      W = r.width; H = r.height;
      canvas.width  = W * dpr;
      canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    window.addEventListener("resize", size);
    size();

    // Glow sprite
    const spr = document.createElement("canvas");
    spr.width = spr.height = 64;
    const sc = spr.getContext("2d")!;
    const g  = sc.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0,   "rgba(255,232,178,1)");
    g.addColorStop(0.4, "rgba(248,170,80,.6)");
    g.addColorStop(1,   "rgba(248,170,80,0)");
    sc.fillStyle = g; sc.fillRect(0, 0, 64, 64);

    function glow(x: number, y: number, sz: number, a: number) {
      ctx.globalAlpha = a;
      ctx.drawImage(spr, x - sz / 2, y - sz / 2, sz, sz);
      ctx.globalAlpha = 1;
    }

    type InPart  = { mode: "in";  ang: number; rad: number; rad0: number; sp: number };
    type UpPart  = { mode: "up";  x: number; y: number; vy: number; sway: number; r: number; life: number; max: number };
    type Burst   = { x: number; y: number; vx: number; vy: number; life: number; r: number };

    const parts: (InPart | UpPart)[] = [];
    const burst: Burst[] = [];
    let heat = 0, t0 = 0, flashed = false, eAcc = 0, running = false;

    function coreCenter(): [number, number] {
      const r  = core.getBoundingClientRect();
      const cb = canvas.getBoundingClientRect();
      return [r.left + r.width / 2 - cb.left, r.top + r.height / 2 - cb.top];
    }

    function inbound(): InPart {
      const ang = Math.random() * 6.283;
      const rad = 170 + Math.random() * 180;
      return { mode: "in", ang, rad, rad0: rad, sp: 1.4 + Math.random() * 1.2 };
    }

    function rise(): UpPart {
      return { mode: "up", x: Math.random() * W, y: H * 0.7 + Math.random() * H * 0.3, vy: 0.4 + Math.random() * 0.9, sway: Math.random() * 6, r: 1 + Math.random() * 2, life: 90 + Math.random() * 70, max: 160 };
    }

    function doFlash(cx: number, cy: number) {
      flashed = true;
      for (let i = 0; i < 36; i++) {
        const a = Math.random() * 6.283, s = 4 + Math.random() * 10;
        burst.push({ x: cx, y: cy, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life: 1, r: 6 + Math.random() * 7 });
      }
      flash.style.transition = "none";
      flash.style.opacity    = "1";
      requestAnimationFrame(() => {
        flash.style.transition = "opacity .9s ease";
        flash.style.opacity    = "0";
      });
      core.style.transform = "scale(1.5)";
      sec.classList.add("finale-done");
    }

    function loop(ts: number) {
      if (!t0) t0 = ts;
      const el = ts - t0;
      ctx.clearRect(0, 0, W, H);

      const [cx, cy] = coreCenter();

      if (!flashed) {
        const p = Math.min(1, el / CHARGE);
        const e = 1 - Math.pow(1 - p, 3);
        heat = e;
        numEl.textContent = `+$${Math.floor(e * TARGET).toLocaleString("en-US")}`;
        core.style.transform = `scale(${1 + e * 0.30}) rotate(${el * 0.03}deg)`;
        core.style.filter    = `drop-shadow(0 0 ${26 + e * 64}px rgba(240,133,42,${0.5 + e * 0.4}))`;
        if (p >= 1) doFlash(cx, cy);
      }

      // Aura
      const auraR = 120 + heat * 250;
      const ag    = ctx.createRadialGradient(cx, cy, 4, cx, cy, auraR);
      ag.addColorStop(0,   `rgba(240,110,30,${0.10 + heat * 0.32})`);
      ag.addColorStop(0.5, `rgba(194,56,26,${0.05 + heat * 0.13})`);
      ag.addColorStop(1,   "rgba(194,56,26,0)");
      ctx.fillStyle = ag;
      ctx.beginPath(); ctx.arc(cx, cy, auraR, 0, 7); ctx.fill();

      // Particles
      eAcc += flashed ? 0.5 : (1.2 + heat * 2.6);
      while (eAcc > 1) {
        if (parts.length < 140) parts.push(flashed ? rise() : inbound());
        eAcc -= 1;
      }
      for (let i = parts.length - 1; i >= 0; i--) {
        const pt = parts[i];
        if (pt.mode === "in") {
          pt.rad -= pt.sp; pt.sp *= 1.035;
          const x = cx + Math.cos(pt.ang) * pt.rad;
          const y = cy + Math.sin(pt.ang) * pt.rad;
          glow(x, y, 7 + (1 - pt.rad / pt.rad0) * 10, (1 - pt.rad / pt.rad0) * 0.8);
          if (pt.rad < 14) parts.splice(i, 1);
        } else {
          pt.life--;
          pt.y -= pt.vy;
          pt.x += Math.sin(pt.y * 0.03 + pt.sway) * 0.3;
          glow(pt.x, pt.y, pt.r * 5, Math.max(0, pt.life / pt.max) * 0.5);
          if (pt.life <= 0 || pt.y < H * 0.04) parts.splice(i, 1);
        }
      }

      // Burst sparks
      for (let b = burst.length - 1; b >= 0; b--) {
        const s = burst[b];
        s.x += s.vx; s.y += s.vy; s.vx *= 0.95; s.vy *= 0.95; s.life *= 0.93;
        glow(s.x, s.y, s.r * 3.4, s.life);
        if (s.life < 0.04) burst.splice(b, 1);
      }

      rafId = requestAnimationFrame(loop);
    }

    function start() {
      if (running) return;
      running = true;
      if (reduce) { numEl.textContent = `+$${TARGET.toLocaleString("en-US")}`; return; }
      requestAnimationFrame(loop);
    }

    const io = new IntersectionObserver(es => {
      es.forEach(e => { if (e.isIntersecting) { start(); io.disconnect(); } });
    }, { threshold: 0.45 });
    io.observe(sec);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", size);
      io.disconnect();
    };
  }, []);

  return (
    <section
      ref={sectionRef}
      id="start"
      style={{
        position: "relative",
        minHeight: "100vh",
        overflow: "hidden",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        background: "radial-gradient(120% 95% at 50% 58%, #160c07, #06070a 70%)",
        borderTop: "1px solid var(--steel-soft)",
      }}
    >
      {/* Particle canvas */}
      <canvas ref={canvasRef} style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} />

      {/* Charge overlay — logo + counter */}
      <div style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        zIndex: 4,
        transition: "opacity .5s ease",
      }} className="fin-charge">
        {/* Spinning/scaling logo */}
        <div ref={coreRef} style={{ width: 118, height: 118, willChange: "transform, filter" }}>
          <Logo size={118} />
        </div>

        {/* P&L counter */}
        <div style={{
          fontFamily: "var(--font-mono,'JetBrains Mono'),monospace",
          fontWeight: 700,
          fontSize: "clamp(38px,7.5vw,92px)",
          letterSpacing: "-.02em",
          lineHeight: 1,
        }}>
          <span ref={numRef} className="molten-text">+$0</span>
        </div>

        <div style={{
          fontFamily: "var(--font-mono,'JetBrains Mono'),monospace",
          fontSize: 10.5,
          letterSpacing: ".26em",
          textTransform: "uppercase",
          color: "var(--ash-dim)",
        }}>
          the engine never stops · paper · illustrative
        </div>
      </div>

      {/* Flash overlay */}
      <div ref={flashRef} style={{
        position: "absolute",
        inset: 0,
        background: "radial-gradient(circle at 50% 50%, #FFF7E4, #FFE6AE 38%, rgba(255,230,174,0) 70%)",
        opacity: 0,
        pointerEvents: "none",
        zIndex: 6,
      }} />

      {/* CTA — hidden until flash, then fades in */}
      <div className="fin-cta" style={{ position: "relative", zIndex: 7 }}>
        <div className="wrap">
          <div className="eyebrow" style={{ display: "flex", justifyContent: "center", marginBottom: 20, letterSpacing: ".22em", color: "var(--ash-dim)" }}>
            live paper feed · every trade, as it happens
          </div>
          <h2 style={{
            fontFamily: "var(--font-display,'Bricolage Grotesque'),sans-serif",
            fontWeight: 800,
            fontSize: "clamp(38px,6.5vw,76px)",
            letterSpacing: "-.035em",
            lineHeight: 1.0,
            marginTop: 8,
            marginBottom: 14,
          }}>
            Plug in.<br />
            <span className="molten-text">Let it print.</span>
          </h2>
          <p style={{ color: "var(--ash)", maxWidth: "46ch", margin: "14px auto 26px", fontSize: 16 }}>
            Start free in Paper with a $25,000 simulated account, or connect a supported exchange and trade LIVE with no platform subscription. Your funds stay on the exchange.
          </p>
          <div style={{ display: "flex", gap: 13, justifyContent: "center", flexWrap: "wrap" }}>
            <a href="/profile?signup=1" className="btn" style={{ fontSize: 14, padding: "14px 32px", border: "none" }}>
              Start trading →
            </a>
            <a href="#pricing" className="btn btn-ghost" style={{ fontSize: 14, padding: "14px 28px" }}>
              View pricing
            </a>
          </div>
        </div>
      </div>

      <style>{`
        .fin-charge { opacity: 1; pointer-events: auto; }
        .finale-done .fin-charge { opacity: 0 !important; pointer-events: none !important; }
        .fin-cta { opacity: 0; transform: scale(.95); transition: opacity .8s ease, transform .8s ease; }
        .finale-done .fin-cta { opacity: 1 !important; transform: none !important; }
        @media(prefers-reduced-motion: reduce) {
          .fin-charge { opacity: 0; }
          .fin-cta { opacity: 1; transform: none; }
        }
      `}</style>
    </section>
  );
}

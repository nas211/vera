"use client";

import { useEffect, useRef } from "react";

const BEATS = [
  { from: 0.02, to: 0.27, eyebrow: "the premise", title: "Every edge ", molten: "decays.", sub: "markets change · no single strategy lasts" },
  { from: 0.25, to: 0.51, eyebrow: "the answer",  title: "So we built a ", molten: "factory.", sub: "not a strategy — a machine that builds them" },
  { from: 0.49, to: 0.75, eyebrow: "the method",  title: "The market is the ", molten: "validator.", sub: "discover → validate → deploy → cut" },
  { from: 0.73, to: 0.99, eyebrow: "the output",  title: "Alpha, ", molten: "manufactured.", sub: "continuously · while you sleep" },
];

export function Tunnel() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const canvasRef  = useRef<HTMLCanvasElement>(null);
  const flashRef   = useRef<HTMLDivElement>(null);
  const beatRefs   = useRef<HTMLDivElement[]>([]);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const section = sectionRef.current!;
    const canvas  = canvasRef.current!;
    const flashEl = flashRef.current!;

    let THREE: typeof import("three");
    let rafId: number;
    let cleanup: () => void;

    import("three").then((mod) => {
      THREE = mod;

      let renderer: InstanceType<typeof mod.WebGLRenderer>;
      try {
        renderer = new mod.WebGLRenderer({ canvas, antialias: true, alpha: false });
      } catch {
        section.style.height = "100vh";
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

      const LEN = 240, R = 9;
      const scene = new mod.Scene();
      scene.background = new mod.Color(0x0a0b0f);
      scene.fog = new mod.FogExp2(0x0a0b0f, 0.014); // lighter fog so entrance candles are visible
      const camera = new mod.PerspectiveCamera(74, 1, 0.1, 500);

      const stops: [number, number, number][] = [
        [0xc2, 0x38, 0x1a], [0xf0, 0x85, 0x2a], [0xf3, 0xc7, 0x6a], [0xff, 0xf4, 0xd6],
      ];
      function ember(t: number): [number, number, number] {
        t = Math.max(0, Math.min(1, t));
        const s = t * 3, i = Math.min(Math.floor(s), 2), f = s - i;
        const a = stops[i], b = stops[i + 1];
        return [(a[0] + (b[0] - a[0]) * f) / 255, (a[1] + (b[1] - a[1]) * f) / 255, (a[2] + (b[2] - a[2]) * f) / 255];
      }

      function glowTex() {
        const c = document.createElement("canvas"); c.width = c.height = 64;
        const g = c.getContext("2d")!;
        const rad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
        rad.addColorStop(0, "rgba(255,255,255,1)");
        rad.addColorStop(0.35, "rgba(255,238,205,.75)");
        rad.addColorStop(1, "rgba(255,238,205,0)");
        g.fillStyle = rad; g.fillRect(0, 0, 64, 64);
        return new mod.CanvasTexture(c);
      }
      const tex = glowTex();

      // win-only ramp: vivid green dominant → amber → gold → whitehot (no red)
      function mintColor(t: number): [number, number, number] {
        t = Math.max(0, Math.min(1, t));
        const cs: [number, number, number][] = [
          [0x26, 0xff, 0x80],  // vivid neon green
          [0x3d, 0xe3, 0xa0],  // mint green
          [0xf0, 0x85, 0x2a],  // amber
          [0xff, 0xf4, 0xd6],  // whitehot
        ];
        const bps = [0, 0.45, 0.72, 1.0];
        let i = 0;
        while (i < 2 && t >= bps[i + 1]) i++;
        const f = (t - bps[i]) / (bps[i + 1] - bps[i]);
        const a = cs[i], b = cs[i + 1];
        return [(a[0] + (b[0] - a[0]) * f) / 255, (a[1] + (b[1] - a[1]) * f) / 255, (a[2] + (b[2] - a[2]) * f) / 255];
      }

      // particles — flat distribution so entrance is populated with green
      const COUNT = window.innerWidth < 700 ? 2200 : 5000;
      const pos = new Float32Array(COUNT * 3), col = new Float32Array(COUNT * 3);
      for (let i = 0; i < COUNT; i++) {
        const ang = Math.random() * Math.PI * 2;
        const rad = R * (0.58 + Math.random() * 0.6);
        const zNorm = Math.random(); // flat — green at entrance, gold at core
        pos[i * 3] = Math.cos(ang) * rad;
        pos[i * 3 + 1] = Math.sin(ang) * rad;
        pos[i * 3 + 2] = -zNorm * LEN;
        const rgb = mintColor(zNorm);
        col[i * 3] = rgb[0]; col[i * 3 + 1] = rgb[1]; col[i * 3 + 2] = rgb[2];
      }
      const geo = new mod.BufferGeometry();
      geo.setAttribute("position", new mod.BufferAttribute(pos, 3));
      geo.setAttribute("color", new mod.BufferAttribute(col, 3));
      const pmat = new mod.PointsMaterial({
        size: 0.16, map: tex, vertexColors: true, transparent: true, opacity: 0.92,
        blending: mod.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
      });
      const points = new mod.Points(geo, pmat); scene.add(points);

      const rings = new mod.Group();
      for (let k = 0; k < 20; k++) {
        const rgb2 = ember(0.25 + 0.7 * (k / 20));
        const rm = new mod.MeshBasicMaterial({
          color: new mod.Color(rgb2[0], rgb2[1], rgb2[2]),
          transparent: true, opacity: 0.45, blending: mod.AdditiveBlending, depthWrite: false,
        });
        const ring = new mod.Mesh(new mod.TorusGeometry(R * 0.94, 0.05, 8, 70), rm);
        ring.position.z = -k * (LEN / 20);
        ring.rotation.z = Math.random() * Math.PI;
        rings.add(ring);
      }
      scene.add(rings);

      const coreMat = new mod.SpriteMaterial({
        map: tex, color: 0xfff4d6, transparent: true,
        blending: mod.AdditiveBlending, depthWrite: false, opacity: 0.4,
      });
      const core = new mod.Sprite(coreMat);
      core.position.z = -LEN + 6; core.scale.set(30, 30, 1); scene.add(core);

      // C: all winning candles — vivid green entrance → gold core
      const candles = new mod.Group();
      const CN = window.innerWidth < 700 ? 60 : 110;
      for (let ci = 0; ci < CN; ci++) {
        const cang = Math.random() * Math.PI * 2;
        const crad = R * (0.42 + Math.random() * 0.52);
        const czNorm = 0.03 + Math.random() * 0.97;
        const cz = -czNorm * LEN;
        const rgb = mintColor(czNorm);
        const color = new mod.Color(rgb[0], rgb[1], rgb[2]);
        const isGreen = czNorm < 0.45;
        const cm = new mod.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: isGreen ? 1.0 : 0.85,
          blending: mod.AdditiveBlending, depthWrite: false,
        });
        const bodyH = 0.5 + Math.random() * 2.0;
        const cx = Math.cos(cang) * crad, cy = Math.sin(cang) * crad;
        const body = new mod.Mesh(new mod.BoxGeometry(0.36, bodyH, 0.36), cm);
        body.position.set(cx, cy, cz);
        const wick = new mod.Mesh(new mod.BoxGeometry(0.07, bodyH + 1.2, 0.07), cm);
        wick.position.set(cx, cy, cz);
        candles.add(body); candles.add(wick);

        // glow halo on green candles for extra pop
        if (isGreen) {
          const glowMat = new mod.SpriteMaterial({
            map: tex, color, transparent: true, opacity: 0.28,
            blending: mod.AdditiveBlending, depthWrite: false,
          });
          const glow = new mod.Sprite(glowMat);
          glow.position.set(cx, cy, cz);
          glow.scale.set(3.5, 3.5, 1);
          candles.add(glow);
        }
      }
      scene.add(candles);

      const tracers: InstanceType<typeof mod.Mesh>[] = [];
      const TG = new mod.Group();
      const TN = window.innerWidth < 700 ? 18 : 34;
      for (let tj = 0; tj < TN; tj++) {
        const tm = new mod.MeshBasicMaterial({
          color: 0xf3c76a, transparent: true, opacity: 0.5,
          blending: mod.AdditiveBlending, depthWrite: false,
        });
        const tr = new mod.Mesh(new mod.BoxGeometry(0.04, 0.04, 3 + Math.random() * 4.5), tm);
        const ta = Math.random() * Math.PI * 2;
        const trad = R * (0.4 + Math.random() * 0.6);
        tr.position.set(Math.cos(ta) * trad, Math.sin(ta) * trad, -Math.random() * LEN);
        (tr as any).userData.spd = 0.5 + Math.random() * 0.9;
        tracers.push(tr); TG.add(tr);
      }
      scene.add(TG);

      // ── Audio: forge strike — fresh context at play-time (most reliable) ────────
      let soundFired = false;

      function forgeStrike() {
        try {
          const AC = window.AudioContext || (window as any).webkitAudioContext;
          if (!AC) return;
          const ctx = new AC();

          ctx.resume().then(() => {
            const t = ctx.currentTime;
            const master = ctx.createGain();
            master.gain.setValueAtTime(0.85, t);
            master.connect(ctx.destination);

            // 1. Sub-bass thud
            const sub = ctx.createOscillator();
            sub.type = "sine";
            sub.frequency.setValueAtTime(90, t);
            sub.frequency.exponentialRampToValueAtTime(28, t + 0.28);
            const subG = ctx.createGain();
            subG.gain.setValueAtTime(1.0, t);
            subG.gain.exponentialRampToValueAtTime(0.001, t + 0.4);
            sub.connect(subG); subG.connect(master);
            sub.start(t); sub.stop(t + 0.45);

            // 2. Metallic ring
            const ring = ctx.createOscillator();
            ring.type = "triangle";
            ring.frequency.setValueAtTime(540, t);
            ring.frequency.exponentialRampToValueAtTime(320, t + 0.12);
            const ringG = ctx.createGain();
            ringG.gain.setValueAtTime(0.65, t);
            ringG.gain.exponentialRampToValueAtTime(0.001, t + 0.75);
            ring.connect(ringG); ringG.connect(master);
            ring.start(t); ring.stop(t + 0.8);

            // 3. Gold shimmer
            const shimmer = ctx.createOscillator();
            shimmer.type = "sine";
            shimmer.frequency.setValueAtTime(1100, t);
            shimmer.frequency.exponentialRampToValueAtTime(1900, t + 0.06);
            const shimG = ctx.createGain();
            shimG.gain.setValueAtTime(0.32, t);
            shimG.gain.exponentialRampToValueAtTime(0.001, t + 1.0);
            shimmer.connect(shimG); shimG.connect(master);
            shimmer.start(t); shimmer.stop(t + 1.1);

            // 4. Noise crack
            const bufLen = Math.floor(ctx.sampleRate * 0.055);
            const buf = ctx.createBuffer(1, bufLen, ctx.sampleRate);
            const d = buf.getChannelData(0);
            for (let i = 0; i < bufLen; i++) d[i] = Math.random() * 2 - 1;
            const noise = ctx.createBufferSource();
            noise.buffer = buf;
            const noiseG = ctx.createGain();
            noiseG.gain.setValueAtTime(0.45, t);
            noiseG.gain.exponentialRampToValueAtTime(0.001, t + 0.055);
            noise.connect(noiseG); noiseG.connect(master);
            noise.start(t);

            setTimeout(() => ctx.close(), 2000);
          });
        } catch { /* silent fail */ }
      }

      let prog = 0, target = 0;
      function onScroll() {
        const rect = section.getBoundingClientRect();
        const total = section.offsetHeight - window.innerHeight;
        const passed = Math.min(Math.max(-rect.top, 0), total);
        target = total > 0 ? passed / total : 0;
      }
      function smooth(a: number, b: number, x: number) {
        x = (x - a) / (b - a); x = x < 0 ? 0 : x > 1 ? 1 : x;
        return x * x * (3 - 2 * x);
      }
      function overlay(p: number) {
        beatRefs.current.forEach((el, bi) => {
          if (!el) return;
          const { from, to } = BEATS[bi], mid = (from + to) / 2;
          const o = Math.min(smooth(from, mid, p), 1 - smooth(mid, to, p));
          el.style.opacity = String(o);
          el.style.transform = `translateY(calc(-50% + ${(1 - o) * 22}px))`;
        });
      }
      function resize() {
        const w = canvas.clientWidth || window.innerWidth;
        const h = canvas.clientHeight || window.innerHeight;
        renderer.setSize(w, h, false);
        camera.aspect = w / h; camera.updateProjectionMatrix();
      }
      function frameUpdate() {
        // E: quadratic speed multiplier — slow entry, explosive finale
        const speed = 1 + prog * prog * 5;

        camera.position.z = -prog * (LEN - 26);
        points.rotation.z  += 0.0006 * speed;
        rings.rotation.z   -= 0.0004 * speed;
        candles.rotation.z += 0.0003 * speed;

        // E: tracers rocket at the end
        for (const tr of tracers) {
          tr.position.z -= (tr as any).userData.spd * (1 + prog * 6);
          if (tr.position.z < -LEN) tr.position.z += LEN;
        }

        // E: particles grow brighter + larger as density escalates
        pmat.opacity = 0.72 + prog * 0.28;
        pmat.size    = 0.14 + prog * 0.18;

        // ring opacity pulses with speed
        rings.children.forEach((r, ri) => {
          (r as any).material.opacity = 0.35 + prog * 0.55 + Math.sin(Date.now() * 0.002 + ri) * 0.04;
        });

        coreMat.opacity = 0.28 + prog * 0.72;
        const s = 22 + prog * 88; core.scale.set(s, s, 1);

        // flash kicks in at 82% scroll, hits full by 100%
        if (flashEl) flashEl.style.opacity = String(smooth(0.82, 1.0, prog) * 0.94);

        // reset so sound replays on next scroll-through
        if (prog < 0.75 && soundFired) soundFired = false;
        // forge strike at 88% — synced with flash
        if (prog >= 0.88 && !soundFired) { soundFired = true; forgeStrike(); }

        overlay(prog);
        renderer.render(scene, camera);
      }

      window.addEventListener("resize", resize);
      window.addEventListener("load", resize);
      resize();

      if (reduce) {
        prog = target = 0;
        camera.position.z = 0;
        renderer.render(scene, camera);
        const last = beatRefs.current[beatRefs.current.length - 1];
        if (last) { last.style.opacity = "1"; last.style.transform = "translateY(-50%)"; }
      } else {
        window.addEventListener("scroll", onScroll, { passive: true });
        onScroll();
        const loop = () => {
          prog += (target - prog) * 0.08;
          frameUpdate();
          rafId = requestAnimationFrame(loop);
        };
        rafId = requestAnimationFrame(loop);
      }

      cleanup = () => {
        cancelAnimationFrame(rafId);
        window.removeEventListener("resize", resize);
        window.removeEventListener("load", resize);
        window.removeEventListener("scroll", onScroll);
        renderer.dispose();
        geo.dispose(); pmat.dispose(); tex.dispose();
        // audio contexts are self-closing via setTimeout in forgeStrike
      };
    });

    return () => { cleanup?.(); };
  }, []);

  return (
    <div
      ref={sectionRef}
      id="tunnel"
      style={{ position: "relative", height: "320vh", background: "var(--obsidian)" }}
    >
      <div style={{ position: "sticky", top: 0, height: "100vh", overflow: "hidden", background: "var(--obsidian)" }}>
        <canvas
          ref={canvasRef}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block" }}
        />

        {/* Flash overlay */}
        <div
          ref={flashRef}
          style={{
            position: "absolute", inset: 0,
            background: "radial-gradient(circle at 50% 50%, #FFF7E0, #F0852A 42%, transparent 74%)",
            opacity: 0, pointerEvents: "none", zIndex: 2, mixBlendMode: "screen",
          }}
        />

        {/* Beat overlays */}
        <div style={{
          position: "absolute", inset: 0,
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          textAlign: "center", pointerEvents: "none", zIndex: 3, padding: "0 24px",
        }}>
          {BEATS.map((b, i) => (
            <div
              key={i}
              ref={(el) => { if (el) beatRefs.current[i] = el; }}
              style={{
                position: "absolute", left: 0, right: 0,
                top: "50%", transform: "translateY(calc(-50% + 22px))",
                display: "flex", flexDirection: "column", alignItems: "center",
                textAlign: "center", padding: "0 24px", opacity: 0,
              }}
            >
              <div style={{
                fontFamily: "var(--font-mono, 'JetBrains Mono'), monospace",
                fontSize: 12, letterSpacing: ".32em", textTransform: "uppercase",
                color: "var(--ember2)",
              }}>
                {b.eyebrow}
              </div>
              <h2 style={{
                fontFamily: "var(--font-display, 'Bricolage Grotesque'), sans-serif",
                fontWeight: 800,
                fontSize: "clamp(44px, 8vw, 96px)",
                letterSpacing: "-.035em",
                lineHeight: 0.95,
                marginTop: 14,
              }}>
                {b.title}<span className="molten-text">{b.molten}</span>
              </h2>
              <p style={{
                fontFamily: "var(--font-mono, 'JetBrains Mono'), monospace",
                fontSize: 13, letterSpacing: ".2em", textTransform: "uppercase",
                color: "var(--ash)", marginTop: 16,
              }}>
                {b.sub}
              </p>
            </div>
          ))}
        </div>

        {/* Scroll hint */}
        <div style={{
          position: "absolute", bottom: 30, left: "50%", transform: "translateX(-50%)",
          fontFamily: "var(--font-mono, 'JetBrains Mono'), monospace",
          fontSize: 11, letterSpacing: ".22em", textTransform: "uppercase",
          color: "var(--ash-dim)", zIndex: 3, animation: "hintpulse 2s infinite",
        }}>
          scroll to descend ↓
        </div>
      </div>
    </div>
  );
}

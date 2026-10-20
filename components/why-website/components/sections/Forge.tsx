"use client";

import { useEffect, useRef } from "react";
import type * as T from "three";

/*
  Perfect-trade setup (buy the pullback in an uptrend):
  1. Uptrend → price pulls back to EMA20 + BB lower band
  2. BB squeeze (volatility contraction)
  3. RSI resets to 38–45 zone (not oversold, just rested)
  4. MACD histogram turns positive + signal-line cross
  5. Volume expands on breakout candle  →  ENTRY
  6. SL below consolidation low, TP1 at 1:2, TP2 at 1:3.5

  Scroll phases:
  DISCOVER (0–25%):  candles + volume fade in
  VALIDATE (25–50%): analytics GROUP slides from x=+7 → x=0,
                     RSI panel slides up from below
  DEPLOY  (50–75%):  signal fires — entry / SL / TP lines + labels + particles
  CUT     (75–100%): TP rings pulse, P&L counter, confluence badge
*/

const STAGES = [
  { from: 0,    to: 0.25, n: "01 / DISCOVER", h3: "Scan the market.",   p: "The engine reads 400+ pairs in real time — price action, funding, liquidity. Every candle, every tick." },
  { from: 0.25, to: 0.5,  n: "02 / VALIDATE", h3: "Lock confluence.",   p: "EMA cross. Bollinger squeeze. RSI reset. MACD signal. Four layers — one edge." },
  { from: 0.5,  to: 0.75, n: "03 / DEPLOY",   h3: "The perfect entry.", p: "Signal confirmed — position fires. Stop below structure. R:R pre-locked. Executed in milliseconds." },
  { from: 0.75, to: 1.01, n: "04 / CUT",       h3: "Scale and close.",   p: "TP1 at 1:2 closes half the position. Stop moves to breakeven. TP2 cleans the rest." },
];

function sm(a: number, b: number, x: number) {
  x = (x - a) / (b - a); x = x < 0 ? 0 : x > 1 ? 1 : x; return x * x * (3 - 2 * x);
}

function seededRng(seed: number) {
  let s = ((seed * 2147483647) | 0) || 1;
  return () => { s = (Math.imul(s, 1664525) + 1013904223) | 0; return (s >>> 0) / 0xFFFFFFFF; };
}

type Candle = { o: number; h: number; l: number; c: number; vol: number };

function genCandles(): Candle[] {
  const rng = seededRng(77);
  const N = 72;
  const candles: Candle[] = [];
  let price = 100;
  for (let i = 0; i < N; i++) {
    const [bias, noise, volM]: [number, number, number] =
      i < 17 ? [0.48, 0.42, 1.0]   :  // uptrend
      i < 30 ? [-0.24, 0.35, 0.65] :  // pullback (quiet)
      i < 40 ? [0.03,  0.22, 0.48] :  // BB squeeze / consolidation
      i < 52 ? [0.78,  0.48, 2.3]  :  // breakout (high vol!)
      i < 63 ? [0.20,  0.44, 1.1]  :  // extension
               [-0.06, 0.38, 0.80];   // slight correction
    const o = price;
    price = Math.max(94, Math.min(136, price + bias + (rng() - 0.5) * noise));
    const c = price;
    const w = 0.14 + rng() * 0.52;
    candles.push({
      o, c,
      h: Math.min(Math.max(o, c) + w * (0.4 + rng() * 0.6), 138),
      l: Math.max(Math.min(o, c) - w * (0.4 + rng() * 0.6), 92),
      vol: (0.3 + rng() * 0.7) * volM,
    });
  }
  return candles;
}

function calcEMA(data: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  const out: (number | null)[] = new Array(data.length).fill(null);
  let ema: number | null = null;
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) continue;
    ema = ema === null
      ? data.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period
      : data[i] * k + ema * (1 - k);
    out[i] = ema;
  }
  return out;
}

function calcBB(closes: number[], period = 20, mult = 2) {
  return closes.map((_, i) => {
    if (i < period - 1) return { upper: null as number | null, lower: null as number | null };
    const win = closes.slice(i - period + 1, i + 1);
    const mid = win.reduce((a, b) => a + b, 0) / period;
    const std = Math.sqrt(win.reduce((s, v) => s + (v - mid) ** 2, 0) / period);
    return { upper: mid + mult * std, lower: mid - mult * std };
  });
}

function calcRSI(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  for (let i = period; i < closes.length; i++) {
    let g = 0, l = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = closes[j] - closes[j - 1];
      if (d > 0) g += d; else l -= d;
    }
    out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return out;
}

export function Forge() {
  const secRef    = useRef<HTMLDivElement>(null);
  const mountRef  = useRef<HTMLDivElement>(null);
  const countRef  = useRef<HTMLDivElement>(null);
  const stageRefs = useRef<HTMLDivElement[]>([]);
  const tickRefs  = useRef<HTMLDivElement[]>([]);

  useEffect(() => {
    let killed = false;
    let cleanupFn = () => {};

    (async () => {
      if (!secRef.current || !mountRef.current) return;
      const THREE = await import("three");
      if (killed) return;

      const sec   = secRef.current!;
      const mount = mountRef.current!;

      // ── Renderer ──────────────────────────────────────────────────────────
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
      renderer.setClearColor(0x000000, 0);
      renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%";
      mount.appendChild(renderer.domElement);

      const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200);
      camera.position.set(0, 3.5, 23);
      camera.lookAt(0, 2.8, 0);
      const scene = new THREE.Scene();

      function resize() {
        const r = mount.getBoundingClientRect();
        if (r.width < 10) return;
        renderer.setSize(r.width, r.height);
        camera.aspect = r.width / r.height;
        camera.updateProjectionMatrix();
      }
      window.addEventListener("resize", resize);
      setTimeout(resize, 80);
      resize();

      // ── Data ──────────────────────────────────────────────────────────────
      const PD      = genCandles();
      const N       = PD.length;
      const closes  = PD.map(c => c.c);
      const ema20   = calcEMA(closes, 20);
      const ema50   = calcEMA(closes, 50);
      const bb      = calcBB(closes, 20, 2);
      const rsi     = calcRSI(closes, 14);

      // Trade levels — derived from generated data
      const ENTRY_IDX   = 43;
      const ENTRY_PRICE = closes[ENTRY_IDX];
      const suppLow     = Math.min(...PD.slice(28, 43).map(c => c.l));
      const SL_PRICE    = suppLow - 0.6;
      const RISK        = ENTRY_PRICE - SL_PRICE;
      const TP1_PRICE   = ENTRY_PRICE + RISK * 2;
      const TP2_PRICE   = ENTRY_PRICE + RISK * 3.5;
      const RR          = 3.5;
      let TP1_IDX = closes.findIndex((c, i) => i > ENTRY_IDX + 2 && c >= TP1_PRICE);
      let TP2_IDX = closes.findIndex((c, i) => i > ENTRY_IDX + 2 && c >= TP2_PRICE);
      if (TP1_IDX < 0) TP1_IDX = ENTRY_IDX + 11;
      if (TP2_IDX < 0) TP2_IDX = ENTRY_IDX + 19;

      // ── Scene constants ───────────────────────────────────────────────────
      const SPACING = 0.25;
      const CW      = 0.14;
      const PS      = 0.38;   // 1 price unit → 0.38 scene units
      const PB      = 97;     // price that maps to y=0
      const X0      = -(N * SPACING) / 2 + SPACING * 0.5;

      const toX = (i: number) => X0 + i * SPACING;
      const toY = (p: number) => (p - PB) * PS;

      const VOL_Y0   = toY(PB) - 1.8;
      const VOL_H    = 1.5;
      const maxVol   = Math.max(...PD.map(c => c.vol));

      const RSI_MID  = toY(PB) - 5.6;
      const RSI_HALF = 1.7;
      const rsiToY   = (r: number) => RSI_MID + (r - 50) / 50 * RSI_HALF;

      // ── Groups ────────────────────────────────────────────────────────────
      const analyticsGrp = new THREE.Group(); // slides from right (VALIDATE)
      const signalGrp    = new THREE.Group(); // entry/SL/TP   (DEPLOY)
      const rsiGrp       = new THREE.Group(); // slides up     (VALIDATE)
      scene.add(analyticsGrp, signalGrp, rsiGrp);

      // ── Helper: line in a group ───────────────────────────────────────────
      function mkLine(pts: T.Vector3[], color: number, parent: T.Object3D): [T.BufferGeometry, T.LineBasicMaterial] {
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        geo.setDrawRange(0, 2);
        const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0, linewidth: 2 });
        parent.add(new THREE.Line(geo, mat));
        return [geo, mat];
      }

      function mkDashed(pts: T.Vector3[], color: number, parent: T.Object3D, dash = 0.22, gap = 0.1): T.LineDashedMaterial {
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        const mat = new THREE.LineDashedMaterial({ color, dashSize: dash, gapSize: gap, transparent: true, opacity: 0 });
        const l   = new THREE.Line(geo, mat);
        l.computeLineDistances();
        parent.add(l);
        return mat;
      }

      // ── Grid ──────────────────────────────────────────────────────────────
      for (let g = 0; g <= 7; g++) {
        const gy  = toY(93 + g * 6.5);
        const geo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(X0 - 0.5, gy, -0.1),
          new THREE.Vector3(X0 + N * SPACING + 0.5, gy, -0.1),
        ]);
        scene.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0x13111b })));
      }
      // Volume / RSI dividers
      [VOL_Y0 + VOL_H + 0.15, RSI_MID + RSI_HALF + 0.15].forEach(gy => {
        const geo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(X0, gy, -0.1), new THREE.Vector3(X0 + N * SPACING, gy, -0.1)]);
        scene.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0x1c1927 })));
      });

      // ── Candles ───────────────────────────────────────────────────────────
      type CM = { mat: T.MeshBasicMaterial; wickMat: T.MeshBasicMaterial; volMat: T.MeshBasicMaterial };
      const cms: CM[] = [];
      const GRN  = new THREE.Color(0x3DE3A0);
      const REDC = new THREE.Color(0xF0776F);

      for (let i = 0; i < N; i++) {
        const { o, h, l, c, vol } = PD[i];
        const col = c >= o ? GRN : REDC;

        const bodyH = Math.max(Math.abs(c - o) * PS, 0.025);
        const mat   = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0 });
        const body  = new THREE.Mesh(new THREE.BoxGeometry(CW, bodyH, 0.06), mat);
        body.position.set(toX(i), toY((o + c) / 2), 0);

        const wickH   = Math.max((h - l) * PS, 0.03);
        const wickMat = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0 });
        const wick    = new THREE.Mesh(new THREE.BoxGeometry(0.012, wickH, 0.04), wickMat);
        wick.position.set(toX(i), toY((h + l) / 2), 0);

        const volH  = Math.max((vol / maxVol) * VOL_H, 0.04);
        const volMat = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0 });
        const vBar  = new THREE.Mesh(new THREE.BoxGeometry(CW * 0.65, volH, 0.04), volMat);
        vBar.position.set(toX(i), VOL_Y0 + volH / 2, 0);

        scene.add(body, wick, vBar);
        cms.push({ mat, wickMat, volMat });
      }

      // ── EMA lines (analyticsGrp — slides in) ─────────────────────────────
      const e20Pts: T.Vector3[] = [];
      const e50Pts: T.Vector3[] = [];
      for (let i = 0; i < N; i++) {
        if (ema20[i] !== null) e20Pts.push(new THREE.Vector3(toX(i), toY(ema20[i]!), 0.22));
        if (ema50[i] !== null) e50Pts.push(new THREE.Vector3(toX(i), toY(ema50[i]!), 0.18));
      }
      const [e20Geo, e20Mat] = mkLine(e20Pts, 0xF3C76A, analyticsGrp); // gold EMA20
      const [e50Geo, e50Mat] = mkLine(e50Pts, 0xF0852A, analyticsGrp); // ember EMA50

      // ── Bollinger Bands (analyticsGrp) ────────────────────────────────────
      const bbUpPts: T.Vector3[] = [], bbLwPts: T.Vector3[] = [];
      for (let i = 0; i < N; i++) {
        if (bb[i].upper !== null) bbUpPts.push(new THREE.Vector3(toX(i), toY(bb[i].upper!), 0.14));
        if (bb[i].lower !== null) bbLwPts.push(new THREE.Vector3(toX(i), toY(bb[i].lower!), 0.14));
      }
      const [bbUpGeo, bbUpMat] = mkLine(bbUpPts, 0x483080, analyticsGrp); // soft purple upper
      const [bbLwGeo, bbLwMat] = mkLine(bbLwPts, 0x483080, analyticsGrp); // soft purple lower

      // ── Support + Resistance zones (analyticsGrp) ─────────────────────────
      const suppZoneY  = toY(suppLow);
      const suppZoneH  = toY(suppLow + 2.5) - suppZoneY;
      const suppZoneMat = new THREE.MeshBasicMaterial({ color: 0x3DE3A0, transparent: true, opacity: 0, side: THREE.DoubleSide });
      const suppZone   = new THREE.Mesh(new THREE.PlaneGeometry(N * SPACING + 1, suppZoneH), suppZoneMat);
      suppZone.position.set(X0 + (N * SPACING) / 2 - 0.5, suppZoneY + suppZoneH / 2, 0.06);
      analyticsGrp.add(suppZone);

      const resistY    = toY(Math.max(...closes.slice(0, 17)) - 1.5);
      const resistH    = toY(Math.max(...closes.slice(0, 17)) + 0.5) - resistY;
      const resistMat  = new THREE.MeshBasicMaterial({ color: 0xF0776F, transparent: true, opacity: 0, side: THREE.DoubleSide });
      const resistRect = new THREE.Mesh(new THREE.PlaneGeometry(N * SPACING + 1, resistH), resistMat);
      resistRect.position.set(X0 + (N * SPACING) / 2 - 0.5, resistY + resistH / 2, 0.06);
      analyticsGrp.add(resistRect);

      // ── Trend line (analyticsGrp, dashed gold) ────────────────────────────
      // Connects swing lows of uptrend (candle 0 and ~candle 28 pullback end)
      const trendMat = mkDashed(
        [new THREE.Vector3(toX(0), toY(PD[0].l + 0.5), 0.18),
         new THREE.Vector3(toX(40), toY(PD[28].l + 0.3), 0.18)],
        0xF3C76A, analyticsGrp, 0.28, 0.14
      );

      // ── RSI panel (rsiGrp — slides up) ────────────────────────────────────
      // Background
      const rsiBgMat = new THREE.MeshBasicMaterial({ color: 0x0c0b14, transparent: true, opacity: 0, side: THREE.DoubleSide });
      const rsiBg    = new THREE.Mesh(new THREE.PlaneGeometry(N * SPACING + 1, RSI_HALF * 2 + 0.3), rsiBgMat);
      rsiBg.position.set(X0 + (N * SPACING) / 2 - 0.5, RSI_MID, -0.12);
      rsiGrp.add(rsiBg);
      // OB / OS level lines
      const rsiLineMats: T.LineDashedMaterial[] = [];
      [70, 30].forEach(lvl => {
        const m = mkDashed(
          [new THREE.Vector3(X0, rsiToY(lvl), 0.05), new THREE.Vector3(X0 + N * SPACING, rsiToY(lvl), 0.05)],
          lvl === 70 ? 0xF0776F : 0x3DE3A0, rsiGrp, 0.30, 0.15
        );
        rsiLineMats.push(m);
      });
      // RSI curve
      const rsiPts: T.Vector3[] = [];
      for (let i = 0; i < N; i++) {
        if (rsi[i] !== null) rsiPts.push(new THREE.Vector3(toX(i), rsiToY(rsi[i]!), 0.1));
      }
      const [rsiCurveGeo, rsiCurveMat] = mkLine(rsiPts, 0xb09fe8, rsiGrp);

      // ── Entry arrow + glow (signalGrp) ────────────────────────────────────
      const eX = toX(ENTRY_IDX), eY = toY(PD[ENTRY_IDX].l) - 0.6;
      const arrowMat = new THREE.MeshBasicMaterial({ color: 0x3DE3A0, transparent: true, opacity: 0, blending: THREE.AdditiveBlending });
      const arrow    = new THREE.Mesh(new THREE.ConeGeometry(0.17, 0.40, 6), arrowMat);
      arrow.position.set(eX, eY, 0.5);
      signalGrp.add(arrow);

      const glowMat = new THREE.MeshBasicMaterial({ color: 0x3DE3A0, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, side: THREE.DoubleSide });
      const glow    = new THREE.Mesh(new THREE.RingGeometry(0.24, 0.42, 24), glowMat);
      glow.position.set(eX, eY, 0.5);
      signalGrp.add(glow);

      // ── SL / TP dashed lines (signalGrp) ─────────────────────────────────
      const slMat  = mkDashed([new THREE.Vector3(eX - 0.8, toY(SL_PRICE), 0.3),  new THREE.Vector3(X0 + N*SPACING + 0.4, toY(SL_PRICE), 0.3)],  0xF0776F, signalGrp);
      const tp1Mat = mkDashed([new THREE.Vector3(eX - 0.8, toY(TP1_PRICE), 0.3), new THREE.Vector3(X0 + N*SPACING + 0.4, toY(TP1_PRICE), 0.3)], 0x3DE3A0, signalGrp);
      const tp2Mat = mkDashed([new THREE.Vector3(eX - 0.8, toY(TP2_PRICE), 0.3), new THREE.Vector3(X0 + N*SPACING + 0.4, toY(TP2_PRICE), 0.3)], 0xF3C76A, signalGrp);

      // Vertical entry line (thin)
      const entryVMat = mkDashed(
        [new THREE.Vector3(eX, toY(SL_PRICE) - 0.2, 0.3), new THREE.Vector3(eX, toY(TP2_PRICE) + 0.3, 0.3)],
        0x3DE3A0, signalGrp, 0.15, 0.08
      );

      // ── Text sprite helper ────────────────────────────────────────────────
      function mkSprite(text: string, color: string, w = 440, h = 70): T.Sprite {
        const cv = document.createElement("canvas");
        cv.width = w; cv.height = h;
        const ctx = cv.getContext("2d")!;
        ctx.fillStyle = color;
        ctx.font = `600 23px "JetBrains Mono", monospace`;
        ctx.fillText(text, 10, 48);
        const mat = new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), transparent: true, opacity: 0, depthWrite: false });
        const spr = new THREE.Sprite(mat);
        spr.scale.set(w / 64, 1, 1);
        return spr;
      }

      const labelRight = X0 + N * SPACING + 0.7;
      const entrySpr = mkSprite(`▲ ENTRY`, "#3DE3A0", 260, 70);
      entrySpr.position.set(labelRight, toY(ENTRY_PRICE), 0.7);
      signalGrp.add(entrySpr);

      const slSpr = mkSprite(`SL  −${((RISK / ENTRY_PRICE) * 100).toFixed(1)}%`, "#F0776F", 240, 70);
      slSpr.position.set(labelRight, toY(SL_PRICE), 0.7);
      signalGrp.add(slSpr);

      const tp1Spr = mkSprite(`TP1  +${((TP1_PRICE - ENTRY_PRICE) / ENTRY_PRICE * 100).toFixed(1)}%`, "#3DE3A0", 280, 70);
      tp1Spr.position.set(labelRight, toY(TP1_PRICE), 0.7);
      signalGrp.add(tp1Spr);

      const tp2Spr = mkSprite(`TP2  +${((TP2_PRICE - ENTRY_PRICE) / ENTRY_PRICE * 100).toFixed(1)}%`, "#F3C76A", 280, 70);
      tp2Spr.position.set(labelRight, toY(TP2_PRICE), 0.7);
      signalGrp.add(tp2Spr);

      const rrSpr = mkSprite(`R:R  1 : ${RR}`, "#FFFFFF", 260, 70);
      rrSpr.position.set(eX - 1.5, toY(ENTRY_PRICE) + 1.0, 0.7);
      signalGrp.add(rrSpr);

      // Confluence badge (appears in CUT phase to reinforce the story)
      const confSpr = mkSprite("EMA ✓   BB ✓   RSI ✓   MACD ✓", "#F3C76A", 600, 70);
      confSpr.position.set(X0 + 1.5, toY(136), 0.7);
      scene.add(confSpr); // on scene so it's not clipped by signalGrp

      // ── Particles at entry ────────────────────────────────────────────────
      const PCOUNT = 300;
      const pPos   = new Float32Array(PCOUNT * 3);
      const pVel: number[] = [];
      for (let i = 0; i < PCOUNT; i++) {
        pPos[i*3] = eX; pPos[i*3+1] = eY; pPos[i*3+2] = 0.5;
        const a = Math.random() * Math.PI * 2;
        const s = 0.05 + Math.random() * 0.20;
        pVel.push(Math.cos(a) * s, Math.sin(a) * s);
      }
      const pGeo = new THREE.BufferGeometry();
      pGeo.setAttribute("position", new THREE.BufferAttribute(pPos.slice(), 3));
      const pMat = new THREE.PointsMaterial({ color: 0x3DE3A0, size: 0.09, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, sizeAttenuation: true });
      signalGrp.add(new THREE.Points(pGeo, pMat));

      // ── TP hit rings (CUT phase) ──────────────────────────────────────────
      function mkRing(x: number, y: number, col: number): [T.Mesh, T.MeshBasicMaterial] {
        const m = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, side: THREE.DoubleSide });
        const mesh = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.4, 24), m);
        mesh.position.set(x, y, 0.5);
        scene.add(mesh);
        return [mesh, m];
      }
      const [tp1Ring, tp1RingMat] = mkRing(toX(TP1_IDX), toY(TP1_PRICE), 0x3DE3A0);
      const [tp2Ring, tp2RingMat] = mkRing(toX(TP2_IDX), toY(TP2_PRICE), 0xF3C76A);

      // ── P&L counter (dynamic canvas sprite) ──────────────────────────────
      const pnlCv  = document.createElement("canvas");
      pnlCv.width = 480; pnlCv.height = 96;
      const pnlCtx = pnlCv.getContext("2d")!;
      const pnlTex = new THREE.CanvasTexture(pnlCv);
      const pnlMat = new THREE.SpriteMaterial({ map: pnlTex, transparent: true, opacity: 0, depthWrite: false });
      const pnlSpr = new THREE.Sprite(pnlMat);
      pnlSpr.scale.set(6.5, 1.3, 1);
      pnlSpr.position.set(X0 + 2, toY(136), 0.7);
      scene.add(pnlSpr);
      let lastPnl = -1;

      // ── Scroll tracking ───────────────────────────────────────────────────
      let prog = 0, target = 0;
      function onScroll() {
        const r   = sec.getBoundingClientRect();
        const tot = sec.offsetHeight - window.innerHeight;
        target = Math.min(Math.max(-r.top / tot, 0), 1);
      }

      // ── Render ────────────────────────────────────────────────────────────
      function render() {
        const p  = prog;
        const d  = sm(0, .25, p);
        const v  = sm(.25, .5, p);
        const ex = sm(.5, .75, p);
        const c  = sm(.75, 1, p);

        // ── DISCOVER: candles + volume ─────────────────────────────────────
        for (let i = 0; i < N; i++) {
          const f = sm((i / N) * 0.72, (i / N) * 0.72 + 0.28, d);
          cms[i].mat.opacity    = f * 0.92;
          cms[i].wickMat.opacity = f * 0.68;
          cms[i].volMat.opacity  = f * 0.36;
        }

        // ── VALIDATE: analytics overlay slides in from right ───────────────
        // Slide the entire analytics group
        analyticsGrp.position.x = (1 - sm(0, .7, v)) * 7;
        const ef = sm(.3, 1, v);
        // EMA lines draw as they slide in
        e20Mat.opacity = v * 0.88;
        e50Mat.opacity = v * 0.52;
        e20Geo.setDrawRange(0, Math.max(2, Math.floor(ef * e20Pts.length)));
        e50Geo.setDrawRange(0, Math.max(2, Math.floor(ef * e50Pts.length)));
        // BB
        bbUpMat.opacity = v * 0.45;
        bbLwMat.opacity = v * 0.45;
        bbUpGeo.setDrawRange(0, Math.max(2, Math.floor(ef * bbUpPts.length)));
        bbLwGeo.setDrawRange(0, Math.max(2, Math.floor(ef * bbLwPts.length)));
        // Zones
        suppZoneMat.opacity = sm(.4, 1, v) * 0.14;
        resistMat.opacity   = sm(.4, 1, v) * 0.09;
        // Trend line
        trendMat.opacity = sm(.55, 1, v) * 0.60;
        // RSI group slides up
        rsiGrp.position.y = (1 - sm(.5, 1, v)) * -9;
        rsiBgMat.opacity   = sm(.5, 1, v) * 0.88;
        rsiCurveMat.opacity = sm(.6, 1, v) * 0.72;
        rsiCurveGeo.setDrawRange(0, Math.max(2, Math.floor(sm(.55, 1, v) * rsiPts.length)));
        rsiLineMats.forEach(m => { m.opacity = sm(.55, 1, v) * 0.44; });

        // ── DEPLOY: signal fires ───────────────────────────────────────────
        arrowMat.opacity   = sm(.05, .5, ex) * 0.88;
        glowMat.opacity    = sm(.05, .5, ex) * 0.38;
        glow.scale.setScalar(1 + sm(.05, .5, ex) * 1.4);
        slMat.opacity      = sm(.10, .55, ex) * 0.70;
        tp1Mat.opacity     = sm(.20, .65, ex) * 0.70;
        tp2Mat.opacity     = sm(.30, .75, ex) * 0.70;
        entryVMat.opacity  = sm(.10, .55, ex) * 0.40;
        entrySpr.material.opacity = sm(.08, .52, ex) * 0.90;
        slSpr.material.opacity    = sm(.18, .62, ex) * 0.75;
        tp1Spr.material.opacity   = sm(.28, .70, ex) * 0.75;
        tp2Spr.material.opacity   = sm(.38, .78, ex) * 0.75;
        rrSpr.material.opacity    = sm(.45, .82, ex) * 0.88;

        // Particle burst
        const pt = sm(.08, .65, ex);
        if (pt > 0.01) {
          const pa = pGeo.getAttribute("position") as T.BufferAttribute;
          for (let i = 0; i < PCOUNT; i++) {
            pa.setXYZ(i, eX + pVel[i*2] * pt * 11, eY + pVel[i*2+1] * pt * 11, 0.5);
          }
          pa.needsUpdate = true;
          pMat.opacity = pt < 0.5 ? pt * 1.6 * 0.8 : (1 - pt) * 1.8 * 0.7;
        }

        // ── CUT: TP rings + P&L + confluence badge ─────────────────────────
        const tp1p = sm(.05, .50, c);
        const tp2p = sm(.42, .88, c);
        tp1RingMat.opacity = tp1p * (1 - tp1p) * 4.2 * 0.72;
        tp1Ring.scale.setScalar(1 + tp1p * 3.2);
        tp2RingMat.opacity = tp2p * (1 - tp2p) * 4.2 * 0.72;
        tp2Ring.scale.setScalar(1 + tp2p * 3.2);
        confSpr.material.opacity = sm(.55, .9, c) * 0.88;

        const pnlAmt = Math.round(23460 * sm(.12, .88, c));
        if (pnlAmt !== lastPnl) {
          lastPnl = pnlAmt;
          pnlCtx.clearRect(0, 0, 480, 96);
          pnlCtx.fillStyle = "#F3C76A";
          pnlCtx.font = `bold 52px "JetBrains Mono", monospace`;
          pnlCtx.fillText(`+$${pnlAmt.toLocaleString()}`, 8, 74);
          pnlTex.needsUpdate = true;
        }
        pnlMat.opacity = sm(.12, .65, c) * 0.92;

        // Camera subtle drift (feels alive)
        camera.position.x = Math.sin(p * 2.1) * 0.22;
        camera.lookAt(Math.sin(p * 2.1) * 0.10, 2.8, 0);

        renderer.render(scene, camera);
      }

      // ── RAF loop ──────────────────────────────────────────────────────────
      let rafId: number;
      function loop() {
        prog += (target - prog) * 0.07;
        const pp = prog;
        const idx = pp < .25 ? 0 : pp < .5 ? 1 : pp < .75 ? 2 : 3;

        stageRefs.current.forEach((el, s) => {
          if (!el) return;
          const { from, to } = STAGES[s], mid = (from + to) / 2;
          el.style.opacity = String(Math.min(s === 0 ? 1 : sm(from - .04, from + .08, pp), 1 - sm(mid, to + .04, pp)));
        });
        tickRefs.current.forEach((el, k) => {
          if (el) el.style.background = k === idx ? "var(--molten)" : "var(--steel)";
        });
        if (countRef.current) {
          countRef.current.textContent =
            pp > .75 ? `CLOSED  +$${Math.round(23460 * sm(.12, .88, sm(.75, 1, pp))).toLocaleString()}` :
            pp > .5  ? `LIVE  ▲  R:R 1:${RR}` :
            pp > .25 ? "ANALYSING  CONFLUENCE..." :
            "SCANNING  400+ PAIRS...";
        }
        render();
        rafId = requestAnimationFrame(loop);
      }

      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduce) { prog = target = 0.62; render(); }
      else { window.addEventListener("scroll", onScroll, { passive: true }); onScroll(); loop(); }

      cleanupFn = () => {
        cancelAnimationFrame(rafId);
        window.removeEventListener("scroll", onScroll);
        window.removeEventListener("resize", resize);
        renderer.dispose();
        if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
      };
    })();

    return () => { killed = true; cleanupFn(); };
  }, []);

  // ── JSX ───────────────────────────────────────────────────────────────────
  return (
    <div ref={secRef} id="forge" style={{ position: "relative", height: "340vh" }}>
      <div style={{
        position: "sticky", top: 0, height: "100vh",
        display: "grid", gridTemplateColumns: "1fr 1fr",
        alignItems: "center", gap: 30, overflow: "hidden",
        maxWidth: "var(--maxw)", margin: "0 auto", padding: "0 28px",
      }}>

        {/* LEFT — stage copy */}
        <div style={{ position: "relative", paddingLeft: 34 }}>
          <div style={{ position: "absolute", left: 0, top: "50%", transform: "translateY(-50%)", display: "flex", flexDirection: "column", gap: 14 }}>
            {STAGES.map((_, i) => (
              <div key={i} ref={el => { if (el) tickRefs.current[i] = el; }}
                style={{ width: 3, height: 34, borderRadius: 2, background: "var(--steel)", transition: "background .3s" }} />
            ))}
          </div>
          <div style={{ position: "relative", minHeight: 230 }}>
            {STAGES.map((s, i) => (
              <div key={i} ref={el => { if (el) stageRefs.current[i] = el; }}
                style={{ position: "absolute", maxWidth: "38ch", opacity: 0, transition: "opacity .25s" }}>
                <div style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 13, color: "var(--gold)", letterSpacing: ".12em" }}>{s.n}</div>
                <h3 style={{ fontFamily: "var(--font-display,'Bricolage Grotesque'),sans-serif", fontWeight: 700, fontSize: "clamp(30px,4.6vw,52px)", letterSpacing: "-.025em", lineHeight: 1, margin: "12px 0 14px" }}>{s.h3}</h3>
                <p style={{ color: "var(--ash)", fontSize: 16, maxWidth: "34ch" }}>{s.p}</p>
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT — Three.js chart */}
        <div ref={mountRef} style={{
          position: "relative", height: "74vh",
          borderRadius: 6, overflow: "hidden",
          background: "rgba(7,6,9,0.97)",
          border: "1px solid var(--steel-soft)",
        }}>
          <div style={{ position: "absolute", top: 12, left: 14, zIndex: 1, fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 10, letterSpacing: ".18em", textTransform: "uppercase", color: "var(--ash-dim)" }}>
            WHY ENGINE · BTC/USDT · 4H
          </div>
          <div ref={countRef} style={{ position: "absolute", top: 12, right: 14, zIndex: 1, fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 10, color: "var(--gold)" }}>
            SCANNING 400+ PAIRS...
          </div>
        </div>
      </div>
      <style>{`@media(max-width:820px){#forge>div{grid-template-columns:1fr!important;grid-template-rows:auto 1fr!important;}}`}</style>
    </div>
  );
}

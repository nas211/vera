"use client";
// TradingView lightweight-charts on Yellow Pro public candles, with the bot's trades drawn on it:
// entry / TP / SL price lines for an open position and entry/exit arrows for its trades.
import { useEffect, useRef, useState } from "react";
import type { IChartApi, IPriceLine, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { markers, price, TF_SECONDS, widenRange, type ClosedTrade, type OpenTrade, type Timeframe } from "./model";
import { YELLOW_API, yellowFeed } from "./yellowFeed";
import s from "./app.module.css";

const C = { green: "#3de3a0", red: "#f0776f", gold: "#f3c76a", ash: "#8a8595", steel: "#262430", goldDim: "#8c6f39", bg: "#0c0b11" };
type Bar = { time: UTCTimestamp; open: number; high: number; low: number; close: number };
type Legend = { open: number; high: number; low: number; close: number } | null;
const volColor = (b: { open: number; close: number }) => b.close >= b.open ? "rgba(61,227,160,.35)" : "rgba(240,119,111,.35)";

export function YellowChart({ market, tf, precision, position, closed, open, onLast }: {
  market: string; tf: Timeframe; precision: number; position?: OpenTrade; closed: ClosedTrade[]; open: OpenTrade[];
  onLast?: (close: number) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const levels = useRef<(number | null)[]>([]);
  const lastBar = useRef<Legend>(null);
  const onLastRef = useRef(onLast); onLastRef.current = onLast;
  const [legend, setLegend] = useState<Legend>(null);
  const [loaded, setLoaded] = useState<{ key: string; first: number } | null>(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const hovering = useRef(false);

  // create once
  useEffect(() => {
    let disposed = false;
    void import("lightweight-charts").then(({ createChart, CrosshairMode }) => {
      if (disposed || !el.current) return;
      const c = createChart(el.current, {
        autoSize: true,
        layout: { background: { color: C.bg }, textColor: C.ash, fontFamily: "JetBrains Mono, ui-monospace, monospace", fontSize: 11 },
        grid: { vertLines: { color: "rgba(38,36,48,.45)" }, horzLines: { color: "rgba(38,36,48,.45)" } },
        rightPriceScale: { borderColor: C.steel, scaleMargins: { top: .12, bottom: .22 } },
        timeScale: { borderColor: C.steel, timeVisible: true, secondsVisible: false, rightOffset: 6 },
        crosshair: { mode: CrosshairMode.Normal, vertLine: { color: C.goldDim, labelBackgroundColor: C.steel }, horzLine: { color: C.goldDim, labelBackgroundColor: C.steel } },
        handleScale: { axisPressedMouseMove: true },
        handleScroll: { vertTouchDrag: false },
      });
      const series = c.addCandlestickSeries({
        upColor: C.green, downColor: C.red, borderVisible: false, wickUpColor: C.green, wickDownColor: C.red,
        autoscaleInfoProvider: (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
          const r = base(); if (!r) return r;
          return { ...r, priceRange: widenRange(r.priceRange, levels.current)! };
        },
      });
      const vol = c.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
      vol.priceScale().applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
      c.subscribeCrosshairMove(p => {
        const d = p.seriesData?.get(series) as Legend | undefined;
        hovering.current = !!(d && "open" in d);
        setLegend(hovering.current ? d! : lastBar.current);
      });
      chart.current = c; candles.current = series; volume.current = vol;
      setReady(true);
    });
    return () => { disposed = true; chart.current?.remove(); chart.current = null; candles.current = null; volume.current = null; lines.current = []; };
  }, []);

  // history + live candles for the selected market/timeframe
  useEffect(() => {
    if (!ready || !candles.current || !volume.current) return;
    let alive = true;
    const series = candles.current, vol = volume.current, key = `${market}:${tf}`;
    setError(""); setLoaded(null);
    series.applyOptions({ priceFormat: { type: "price", precision, minMove: 1 / 10 ** precision } });
    let off: (() => void) | undefined;
    let last = 0;
    (async () => {
      try {
        const r = await fetch(`${YELLOW_API}/klines?symbol=${market}&interval=${tf}&limit=500`, { cache: "no-store", credentials: "omit" });
        const k = await r.json();
        if (!alive) return;
        if (!Array.isArray(k) || !k.length) throw new Error("No candles");
        const bars: Bar[] = k.map((x: unknown[]) => ({ time: Number(x[0]) / 1000 as UTCTimestamp, open: +String(x[1]), high: +String(x[2]), low: +String(x[3]), close: +String(x[4]) }))
          .sort((a: Bar, b: Bar) => a.time - b.time);
        series.setData(bars);
        vol.setData(k.map((x: unknown[], i: number) => ({ time: Number(x[0]) / 1000 as UTCTimestamp, value: +String(x[5]), color: volColor(bars[i] ?? { open: 0, close: 0 }) }))
          .sort((a: { time: number }, b: { time: number }) => a.time - b.time));
        const b = bars.at(-1)!; last = b.time;
        lastBar.current = b; setLegend(b); onLastRef.current?.(b.close);
        chart.current?.timeScale().setVisibleLogicalRange({ from: bars.length - 90, to: bars.length + 4 });
        setLoaded({ key, first: bars[0].time });
        off = yellowFeed().subscribe(`public.kline.${market}.${tf}`, d => {
          const x = d.kline as unknown[] | undefined;
          if (!alive || !Array.isArray(x)) return;
          const time = Number(x[0]) / 1000 as UTCTimestamp;
          if (!Number.isFinite(time) || time < last) return; // never rewrite older candles from a late message
          const bar = { time, open: +String(x[1]), high: +String(x[2]), low: +String(x[3]), close: +String(x[4]) };
          if (![bar.open, bar.high, bar.low, bar.close].every(Number.isFinite)) return;
          last = time; series.update(bar); vol.update({ time, value: +String(x[5]), color: volColor(bar) });
          lastBar.current = bar; if (!hovering.current) setLegend(bar); onLastRef.current?.(bar.close);
        });
      } catch { if (alive) setError("Candles unavailable from Yellow Pro. Retrying when you change pair or timeframe."); }
    })();
    return () => { alive = false; off?.(); };
  }, [ready, market, tf, precision]);

  // entry / TP / SL lines
  const sl = position?.stopLoss ?? null, tp = position?.takeProfit ?? null, entry = position?.entry ?? null;
  const label = position ? `${position.side.toUpperCase()}${position.leverage ? ` ${position.leverage}x` : ""}` : "";
  useEffect(() => {
    const series = candles.current; if (!series) return;
    lines.current.forEach(l => series.removePriceLine(l)); lines.current = [];
    levels.current = [entry, tp, sl];
    const add = (p: number | null, color: string, title: string, style: number) => { if (p != null) lines.current.push(series.createPriceLine({ price: p, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title })); };
    add(entry, C.gold, label, 0); add(tp, C.green, "TP", 2); add(sl, C.red, "SL", 2);
    chart.current?.priceScale("right").applyOptions({ autoScale: true });
  }, [entry, tp, sl, label, loaded]);

  // entry / exit arrows
  useEffect(() => {
    const series = candles.current; if (!series || !loaded || loaded.key !== `${market}:${tf}`) return;
    series.setMarkers(markers(closed, open, market, tf, loaded.first, C) as Parameters<typeof series.setMarkers>[0]);
  }, [loaded, closed, open, market, tf]);

  const up = legend ? legend.close >= legend.open : true;
  return <div className={s.chartWrap} data-testid="yellow-chart" data-market={market} data-tf={tf} data-loaded={loaded ? "1" : "0"}>
    <div className={s.legend} aria-live="off">{legend && (["O", "H", "L", "C"] as const).map((k, i) => <span key={k}>{k} <b className={up ? s.up : s.down}>{price([legend.open, legend.high, legend.low, legend.close][i], precision)}</b></span>)}</div>
    <div ref={el} className={s.chartEl} />
    {error && <div className={s.chartError} role="status">{error}</div>}
  </div>;
}

export const TIMEFRAMES = Object.keys(TF_SECONDS) as Timeframe[];

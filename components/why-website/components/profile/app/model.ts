// Pure view model for the mobile dashboard. No React, no network: every number shown on the
// Trades / History screens is derived here, so the P&L rules (§8 / §8a of the brief) are tested
// in one place (tests/mobile-dashboard-model.mjs).

export type Side = "long" | "short";
export type Result = "target" | "stop" | "closed";

export type OpenTrade = {
  id: string; market: string; base: string; side: Side;
  qty: number | null; entry: number | null; mark: number | null;
  leverage: number | null; notional: number | null; snapshotPnl: number | null;
  stopLoss: number | null; takeProfit: number | null;
  openedAt: number | null; state: string; exchange: string;
};

export type ClosedTrade = {
  id: string; market: string; base: string; side: Side;
  qty: number | null; entry: number | null; exit: number | null;
  pnl: number | null; pct: number | null; result: Result;
  openedAt: number | null; closedAt: number | null; exchange: string;
};

type Rec = Record<string, unknown>;

export const num = (v: unknown): number | null =>
  v == null || v === "" || typeof v === "boolean" || !Number.isFinite(Number(v)) ? null : Number(v);

/** Seconds since epoch from a unix seconds/ms number or an ISO string. */
export function seconds(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number") return v > 1e11 ? v / 1000 : v;
  const n = Number(v);
  if (Number.isFinite(n)) return n > 1e11 ? n / 1000 : n;
  const t = Date.parse(String(v));
  return Number.isNaN(t) ? null : t / 1000;
}

export function sideOf(v: unknown): Side {
  const s = String(v ?? "").toLowerCase();
  return s === "short" || s === "sell" ? "short" : "long";
}

export function baseOf(market: string): string {
  return market.toUpperCase().replace(/-PERP$/, "").replace(/[\/_-]?USDT?$/, "") || market;
}

/** TARGET / STOP / CLOSED from the many reason spellings the ledgers and Yellow use. */
export function resultOf(reason: unknown): Result {
  const r = String(reason ?? "").toLowerCase();
  if (/target|take_?profit|\btp\b/.test(r)) return "target";
  if (/stop|\bsl\b|liquidat/.test(r)) return "stop";
  return "closed";
}

export function openTrade(p: Rec, exchange: string, defaultLeverage: number | null): OpenTrade {
  const market = String(p.market ?? p.symbol ?? "—");
  const qty = num(p.qty ?? p.size), entry = num(p.entry_price ?? p.entry);
  const q = qty == null ? null : Math.abs(qty);
  return {
    id: String(p.position_id ?? p.id ?? `${market}-${p.direction}-${p.opened_ts ?? p.opened_at}`),
    market, base: baseOf(market), side: sideOf(p.direction ?? p.side),
    qty: q, entry, mark: num(p.mark_price ?? p.mark),
    leverage: num(p.leverage) ?? defaultLeverage,
    notional: num(p.notional ?? p.notional_value) ?? (q != null && entry != null ? q * entry : null),
    snapshotPnl: num(p.unrealized_pnl),
    stopLoss: num(p.stop_loss ?? p.stop_price ?? p.sl),
    takeProfit: num(p.take_profit ?? p.tp_price ?? p.tp),
    openedAt: seconds(p.opened_ts ?? p.opened_at ?? p.opened),
    state: String(p.state ?? "OPEN"), exchange,
  };
}

export function closedTrade(p: Rec, exchange: string, defaultLeverage: number | null): ClosedTrade {
  const market = String(p.market ?? p.symbol ?? "—");
  const qty = num(p.qty ?? p.size), entry = num(p.entry_price ?? p.entry);
  const q = qty == null ? null : Math.abs(qty);
  const pnl = num(p.net_pnl ?? p.pnl ?? p.net);
  const lev = num(p.leverage) ?? defaultLeverage;
  const notional = num(p.notional) ?? (q != null && entry != null ? q * entry : null);
  const margin = notional != null && lev ? notional / lev : null;
  return {
    id: String(p.position_id ?? p.id ?? `${market}-${p.opened_ts ?? p.opened_at}-${p.closed_ts ?? p.closed_at}`),
    market, base: baseOf(market), side: sideOf(p.direction ?? p.side),
    qty: q, entry, exit: num(p.exit_price ?? p.exit), pnl,
    pct: pnl != null && margin ? pnl / margin * 100 : null,
    result: resultOf(p.reason ?? p.result ?? p.state),
    openedAt: seconds(p.opened_ts ?? p.opened_at ?? p.opened),
    closedAt: seconds(p.closed_ts ?? p.closed_at ?? p.closed ?? p.time),
    exchange,
  };
}

/** Live P&L of one open trade. Mark-price estimate before fees and funding; snapshot otherwise. */
export function livePnl(t: OpenTrade, mark: number | null | undefined): { pnl: number | null; pct: number | null; mark: number | null; estimated: boolean } {
  const m = mark ?? t.mark;
  const dir = t.side === "long" ? 1 : -1;
  const estimated = m != null && t.entry != null && t.qty != null;
  const pnl = estimated ? (m! - t.entry!) * t.qty! * dir : t.snapshotPnl;
  const margin = t.notional != null && t.leverage ? t.notional / t.leverage : null;
  return { pnl, pct: pnl != null && margin ? pnl / margin * 100 : null, mark: m ?? null, estimated };
}

/** Sum of open P&L; null when any open trade's P&L is unknown. Zero open trades is a real $0.00. */
export function openTotal(values: (number | null)[]): number | null {
  return values.some(v => v == null) ? null : values.reduce<number>((a, v) => a + v!, 0);
}

export function localDayStart(now: number): number {
  const d = new Date(now * 1000); d.setHours(0, 0, 0, 0); return d.getTime() / 1000;
}

export type Today = { start: number; trades: ClosedTrade[]; net: number; won: number; lost: number; pending: number; complete: boolean };

/** Today in the viewer's own timezone, from exactly the rows History shows. `complete` is false when
 *  the loaded window may have cut off earlier trades from today (full page, all of it from today). */
export function today(rows: ClosedTrade[], now: number, limit: number): Today {
  const start = localDayStart(now);
  const trades = rows.filter(r => r.closedAt != null && r.closedAt >= start && r.closedAt <= now + 1);
  const known = trades.filter(r => r.pnl != null);
  const oldest = rows.reduce((m, r) => r.closedAt != null && r.closedAt < m ? r.closedAt : m, Infinity);
  return {
    start, trades,
    net: known.reduce((a, r) => a + r.pnl!, 0),
    won: known.filter(r => r.pnl! > 0).length,
    lost: known.filter(r => r.pnl! < 0).length,
    pending: trades.length - known.length,
    complete: rows.length < limit || oldest < start,
  };
}

export function summary(rows: ClosedTrade[]): { realized: number | null; wins: number; losses: number; trades: number; pending: number } {
  const known = rows.filter(r => r.pnl != null);
  const pending = rows.length - known.length;
  return { realized: pending ? null : known.reduce((a, r) => a + r.pnl!, 0), wins: known.filter(r => r.pnl! > 0).length,
    losses: known.filter(r => r.pnl! < 0).length, trades: rows.length, pending };
}

export function winRate(wins: number, losses: number): number | null {
  return wins + losses ? wins / (wins + losses) * 100 : null;
}

// ---------------------------------------------------------------- formatting
const MINUS = "−";
export const isZero = (v: number | null | undefined) => v != null && Math.abs(v) < 0.005;

/** "up" | "down" | "zero": zero and unknown are never green or red. */
export function tone(v: number | null | undefined): "up" | "down" | "zero" {
  return v == null || isZero(v) ? "zero" : v > 0 ? "up" : "down";
}

export function usd(v: number | null | undefined, signed = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const z = isZero(v);
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (z ? "" : v < 0 ? MINUS : signed ? "+" : "") + "$" + s;
}

export function pct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (Math.abs(v) < 0.005) return "0.00%";
  return (v < 0 ? MINUS : "+") + Math.abs(v).toFixed(2) + "%";
}

/** Market tick precision when known, otherwise a magnitude-based fallback. */
export function price(v: number | null | undefined, precision?: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const p = precision ?? (v >= 1000 ? 1 : v >= 100 ? 2 : v >= 1 ? 3 : 5);
  return v.toLocaleString("en-US", { minimumFractionDigits: p, maximumFractionDigits: p });
}

export function splitUsd(v: number | null | undefined): { whole: string; cents: string } | null {
  if (v == null || !Number.isFinite(v)) return null;
  const [w, c] = Math.abs(v).toFixed(2).split(".");
  return { whole: (v < 0 ? MINUS : "") + "$" + Number(w).toLocaleString("en-US"), cents: "." + c };
}

/** "14:23", "Yday 15:06" or "Oct 15 09:41" in the viewer's timezone. */
export function when(ts: number | null, now: number): string {
  if (ts == null) return "—";
  const d = new Date(ts * 1000), start = localDayStart(now);
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (ts >= start) return hm;
  if (ts >= start - 86400) return "Yday " + hm;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " + hm;
}

// ---------------------------------------------------------------- chart markers
export const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 } as const;
export type Timeframe = keyof typeof TF_SECONDS;

export type Marker = { time: number; position: "belowBar" | "aboveBar"; shape: "arrowUp" | "arrowDown"; color: string; text: string };
export type MarkerColors = { green: string; red: string; gold: string; ash: string };

/** TradingView-style entry/exit arrows, snapped to the candle open of the timeframe, oldest first. */
export function markers(closed: ClosedTrade[], open: OpenTrade[], market: string, tf: Timeframe, firstCandle: number, c: MarkerColors): Marker[] {
  const step = TF_SECONDS[tf], snap = (t: number) => Math.floor(t / step) * step;
  const out: Marker[] = [];
  const entry = (t: number, side: Side, text: string) => out.push({ time: snap(t), position: side === "long" ? "belowBar" : "aboveBar",
    shape: side === "long" ? "arrowUp" : "arrowDown", color: side === "long" ? c.green : c.red, text });
  for (const t of closed) {
    if (t.market !== market) continue;
    if (t.openedAt != null) entry(t.openedAt, t.side, t.side === "long" ? "Long" : "Short");
    if (t.closedAt != null) {
      const win = (t.pnl ?? 0) > 0;
      const label = t.result === "target" ? "TP" : t.result === "stop" ? "SL" : "Closed";
      out.push({ time: snap(t.closedAt), position: t.side === "long" ? "aboveBar" : "belowBar", shape: t.side === "long" ? "arrowDown" : "arrowUp",
        color: win ? c.gold : c.ash, text: t.pct != null ? `${label} ${t.pct > 0 ? "+" : MINUS}${Math.abs(t.pct).toFixed(1)}%` : label });
    }
  }
  for (const t of open) {
    if (t.market === market && t.openedAt != null) entry(t.openedAt, t.side, `${t.side === "long" ? "Long" : "Short"}${t.leverage ? ` ${t.leverage}x` : ""} · open`);
  }
  return out.filter(m => m.time >= firstCandle).sort((a, b) => a.time - b.time);
}

/** Price range that keeps entry / TP / SL inside the visible chart. */
export function widenRange(range: { minValue: number; maxValue: number } | null, levels: (number | null)[]) {
  const lv = levels.filter((v): v is number => v != null && Number.isFinite(v));
  if (!range || !lv.length) return range;
  return { minValue: Math.min(range.minValue, ...lv), maxValue: Math.max(range.maxValue, ...lv) };
}

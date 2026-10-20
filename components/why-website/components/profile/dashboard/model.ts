import type { Me, PaperAccount, TradesView, WeexAccount } from "../client";
export type Row = { id: string; market: string; side: string; qty: number | null; notional: number | null; entry: number | null; exit: number | null; mark: number | null; pnl: number | null; time: number | string | null; exchange: string; result: string; kind: string };
export const number = (v: unknown): number | null => v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v);
const text = (v: unknown, fallback = "—") => typeof v === "string" && v ? v : fallback;
export function recordRow(p: Record<string, unknown>, exchange: string, closed = false): Row {
  const qty = number(p.qty ?? p.size), entry = number(p.entry_price);
  return { id: String(p.id ?? p.position_id ?? `${p.market}-${p.direction}-${p.opened_at ?? p.opened_ts}`), market: text(p.market ?? p.symbol), side: text(p.direction ?? p.side), qty, entry, mark: number(p.mark_price), exit: number(p.exit_price), notional: number(p.notional) ?? (qty != null && entry != null ? qty * entry : null), pnl: number(closed ? p.net_pnl ?? p.pnl : p.unrealized_pnl), time: (closed ? p.closed_at ?? p.closed_ts : p.opened_at ?? p.opened_ts) as number | string | null ?? null, exchange, result: text(p.reason ?? p.state, closed ? "Closed" : "Open"), kind: closed ? "Exit" : "Entry" };
}
export function rows(mode: "paper" | "live", paper: PaperAccount | null, me: Me | null, weex: WeexAccount | null, trades: TradesView | null): { open: Row[]; closed: Row[] } {
  if (mode === "paper") return { open: (paper?.paper_open_positions ?? []).map(p => recordRow(p, "WHY Paper · simulated")), closed: (paper?.paper_trade_history ?? []).map(p => recordRow(p, "WHY Paper · simulated", true)) };
  return { open: [...(me?.runtime.positions ?? []).map(p => recordRow(p, "Yellow Pro")), ...(weex?.runtime.positions ?? []).map(p => recordRow(p, "WEEX"))], closed: (trades?.trades ?? []).map(p => recordRow(p, "Yellow Pro", true)) };
}
export const money = (v: number | null | undefined): string => v == null || !Number.isFinite(v) ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: v !== 0 && Math.abs(v) < 0.01 ? 4 : 2 }).format(v);
export const quantity = (v: number | null): string => v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 8 });
export function time(value: number | string | null): string {
  if (value == null) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "—" : date.toISOString().slice(0, 19).replace("T", " ") + " UTC";
}
export function configuredMarket(me: Me | null, paper: PaperAccount | null, weex: WeexAccount | null): string | null {
  // Only explicit strategy configuration; a recent fill is not a configured market.
  for (const settings of [me?.config, paper?.settings, weex?.settings]) {
    const s = settings as unknown as Record<string, unknown> | undefined;
    const candidate = s?.strategy_market ?? s?.market ?? s?.symbol;
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return null;
}

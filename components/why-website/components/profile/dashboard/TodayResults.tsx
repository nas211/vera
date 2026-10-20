import type { DailyResults } from "../client";
import { money } from "./model";
import s from "./today-results.module.css";

export function TodayResults({ today }: { today?: DailyResults }) {
  const available = today?.available === true && today.pending_pnl === 0;
  const signed = (value: number | null | undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${money(value)}`;
  return <section className={s.card} aria-label="Today's live results" data-testid="today-results">
    <div className={s.heading}><h3>Today · Live results</h3><span>{today?.date ?? "Today"} · Berlin time</span></div>
    <div className={s.grid}>
      <div><span>Today&apos;s net P&amp;L</span><strong className={(today?.net_pnl ?? 0) < 0 ? s.negative : s.positive} data-testid="today-net">{available ? signed(today.net_pnl) : "—"}</strong><small>{available ? `${today.closed_trades} closed trade${today.closed_trades === 1 ? "" : "s"} today` : "Results unavailable"}</small></div>
      <div><span>Won today</span><strong className={s.positive} data-testid="today-won">{available ? signed(today.won_usd) : "—"}</strong><small>{available ? `${today.wins} winning trade${today.wins === 1 ? "" : "s"} today` : "—"}</small></div>
      <div><span>Lost today</span><strong className={s.negative} data-testid="today-lost">{available ? signed(today.lost_usd == null ? null : (-today.lost_usd || 0)) : "—"}</strong><small>{available ? `${today.losses} losing trade${today.losses === 1 ? "" : "s"} today` : "—"}</small></div>
    </div>
    {available && today.breakeven > 0 && <p>{today.breakeven} break-even trade{today.breakeven === 1 ? "" : "s"} today.</p>}
  </section>;
}

#!/usr/bin/env python3
"""Status report from state/ (no network). ACTUAL BYBIT LIVE first (what Loculus reads), Yellow-adjusted second."""
import json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__)); ST = os.path.join(BASE, "state")
def load(p, default):
    try: return json.load(open(os.path.join(ST, p)))
    except Exception: return default
def main():
    s = load("status.json", {})
    if "--json" in sys.argv: print(json.dumps(s, indent=1)); return
    print(f"mode {s.get('mode')} (armed {s.get('armed')}: {s.get('arm_reason')})  halted {s.get('halted')}  uid {s.get('uid')}  host {s.get('host')}  api writes {s.get('api_writes')}")
    def block(title, d, extra=""):
        o = d.get("overall", {})
        print(f"\n== {title} {extra}")
        print(f"realized P&L    ${o.get('net_pnl_usd')}   unrealized ${d.get('unrealized_pnl_usd')}   net ${d.get('net_pnl_usd', o.get('net_pnl_usd'))}")
        print(f"closed trades   {o.get('trades')}  wins {o.get('wins')}  losses {o.get('losses')}  win rate {o.get('win_rate')}%  PF {o.get('profit_factor')}  avg ${o.get('avg_pnl_usd')}  fees ${o.get('fees_usd')}")
        if d.get("max_drawdown_usd") is not None: print(f"max drawdown    ${d.get('max_drawdown_usd')}")
        for k, v in (d.get("by_sleeve") or {}).items(): print(f"  sleeve {k:9s} trades {v['trades']} wins {v['wins']} net ${v['net_pnl_usd']} PF {v['profit_factor']}")
        for k, v in (d.get("by_market") or {}).items(): print(f"  market {k:9s} trades {v['trades']} wins {v['wins']} net ${v['net_pnl_usd']}")
    a = s.get("actual_bybit_live") or {}
    block("ACTUAL BYBIT LIVE (PRIMARY - what Loculus reads)", a, f"[start ${a.get('starting_balance')} equity ${a.get('current_equity')} available ${a.get('available')} fees {a.get('fee_rates', {}).get('BTCUSDT')}]")
    y = s.get("yellow_adjusted") or {}; fm = y.get("fee_model") or {}
    block("YELLOW-ADJUSTED (internal comparison, $%s reference notional)" % y.get("reference_notional_usd"), y, f"[Yellow refs, fees taker {fm.get('taker_bps')} / maker {fm.get('maker_bps')} / spread {fm.get('spread_bps')} bps; final {y.get('final_trades')} pending {y.get('pending_trades')}]")
    sc = y.get("scaled_to_live") or {}
    if sc: print(f"  scaled to live lots: realized ${sc.get('realized_pnl_usd')}  trades {sc.get('overall', {}).get('trades')}")
    for p in y.get("open") or []: print(f"  OPEN {p['sleeve']} {p['side']} {p['symbol']} yellow entry {p['yellow_entry']} mark {p['yellow_mark']} unrealized ${p['yellow_unrealized_usd']} (live-scaled ${p.get('scaled_to_live')})")
    print(f"\nopen positions  {len(s.get('open_positions', []))}")
    for p in s.get("open_positions", []): print(f"  OPEN {p['sleeve']} {p['side']} {p['symbol']} qty {p['qty']} fill {(p.get('fill') or {}).get('avg_price')} tp {p.get('tp')} sl {p.get('sl')} live {p.get('live')}")
    print(f"signals seen    {s.get('signals_seen')}  decisions {s.get('signal_decisions')}")
    print(f"strategy sha256 {(s.get('strategy') or {}).get('sha256')}")
if __name__ == "__main__": main()

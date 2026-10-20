#!/usr/bin/env python3
"""Status report from state/ (no network). `python3 report.py` -> text, `--json` -> JSON."""
import json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__)); ST = os.path.join(BASE, "state")
def load(p, default):
    try: return json.load(open(os.path.join(ST, p)))
    except Exception: return default
def main():
    s = load("status.json", {}); a = load("account.json", {})
    if "--json" in sys.argv: print(json.dumps(s, indent=1)); return
    print(f"host            {s.get('host')}   hosts contacted {s.get('hosts_contacted')}")
    def block(title, d, extra=""):
        o = d.get("overall", {})
        print(f"\n== {title} {extra}")
        print(f"realized P&L    ${d.get('realized_pnl_usd')}\nunrealized P&L  ${d.get('unrealized_pnl_usd')}\nnet P&L         ${d.get('net_pnl_usd')}")
        print(f"closed trades   {o.get('trades')}  wins {o.get('wins')}  losses {o.get('losses')}  win rate {o.get('win_rate')}%  PF {o.get('profit_factor')}  avg ${o.get('avg_pnl_usd')}  fees ${o.get('fees_usd')}")
        print(f"max drawdown    ${d.get('max_drawdown_usd')}")
        for k, v in (d.get("by_sleeve") or {}).items(): print(f"  sleeve {k:9s} trades {v['trades']} wins {v['wins']} net ${v['net_pnl_usd']} PF {v['profit_factor']}")
        for k, v in (d.get("by_market") or {}).items(): print(f"  market {k:9s} trades {v['trades']} wins {v['wins']} net ${v['net_pnl_usd']}")
    y = s.get("yellow_adjusted") or {}; fm = y.get("fee_model") or {}
    block("YELLOW-ADJUSTED P&L (PRIMARY)", y, f"[Yellow entry/exit refs, fees taker {fm.get('taker_bps')} / maker {fm.get('maker_bps')} / spread {fm.get('spread_bps')} bps, ${y.get('notional_usd')} notional; final {y.get('final_trades')} pending {y.get('pending_trades')}]")
    for p in y.get("open") or []: print(f"  OPEN {p['sleeve']} {p['side']} {p['symbol']} yellow entry {p['yellow_entry']} yellow mark {p['yellow_mark']} unrealized ${p['yellow_unrealized_usd']}")
    r = s.get("raw_bybit_demo") or {}
    block("RAW BYBIT DEMO P&L (container)", r, f"[start ${r.get('starting_balance')} equity ${r.get('current_equity')}]")
    print(f"\nopen positions  {len(s.get('open_positions', []))}")
    for p in s.get("open_positions", []): print(f"  OPEN {p['sleeve']} {p['side']} {p['symbol']} qty {p['qty']} fill {(p.get('fill') or {}).get('avg_price')} tp {p.get('tp')} sl {p.get('sl')} live {p.get('live')}")
    print(f"signals seen    {s.get('signals_seen')}  decisions {s.get('signal_decisions')}")
    print(f"strategy sha256 {(s.get('strategy') or {}).get('sha256')}")
if __name__ == "__main__": main()

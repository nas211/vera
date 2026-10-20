#!/usr/bin/env python3
"""Minimum live bankroll calculator. Reads CURRENT Bybit LIVE instrument rules + marks (public GETs),
the account fee rate (private GET when a key is present), and the production strategy CFG
(markets, leverage, max_open, tp/sl per sleeve). Writes state/capital_<ts>.json and prints the tiers.
No orders. `--json` prints JSON only."""
import json, os, sys, time, math
os.environ.setdefault("BYBIT_LIVE_YELLOW", "1")
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import guard; guard.install(check_env=False)
import engine
from bybit_live import LiveClient, from_secrets

def compute(strat, instr, marks, fees, sizing, risk, mmr):
    cfg = strat["cfg"]; lev = float(sizing["leverage"]); worst_sl = max(cfg["rv_sl"], cfg["pb_sl"])
    rows = []
    for m in strat["markets"]:
        sym = engine.to_symbol(m); ins = instr[sym]; mark = marks[sym]; fee = fees.get(sym, dict(taker=0.00055, maker=0.0002))
        qty = engine.min_lot(ins, mark, sizing); notional = qty * mark
        bound = "minOrderQty" if abs(qty - ins["minQty"]) < 1e-12 else "minNotional"
        im = notional / lev; sl_loss = notional * worst_sl / 100; fee_rt = notional * fee["taker"] * 2
        overshoot = notional * risk.get("stop_overshoot_pct", 0.1) / 100
        liq_pct = (1 / lev - mmr.get(sym, 0.01)) * 100
        rows.append(dict(symbol=sym, mark=mark, qtyStep=ins["qtyStep"], minOrderQty=ins["minQty"], minNotional=ins.get("minNotional"), tickSize=ins["tickSize"], maxLeverage=ins.get("maxLeverage"),
                         min_qty=qty, min_notional_usd=round(notional, 4), bound_by=bound, initial_margin_usd=round(im, 4), worst_sl_pct=worst_sl, sl_loss_usd=round(sl_loss, 4),
                         fees_roundtrip_usd=round(fee_rt, 4), stop_overshoot_usd=round(overshoot, 4), taker_fee=fee["taker"], maker_fee=fee["maker"], mmr_tier1=mmr.get(sym), liquidation_distance_pct=round(liq_pct, 2),
                         worst_case_cost_usd=round(im + sl_loss + fee_rt + overshoot, 4)))
    rows.sort(key=lambda r: -r["min_notional_usd"])
    k = min(cfg["max_open"], len(rows)); top = rows[:k]
    S = lambda key, rs=top: sum(r[key] for r in rs)
    exposure = S("min_notional_usd"); im = S("initial_margin_usd"); sl = S("sl_loss_usd"); fees_ = S("fees_roundtrip_usd"); ov = S("stop_overshoot_usd")
    rounding = 0.5                                                      # cents of rounding / float / funding on tiny lots
    theoretical = im + sl + fees_ + ov + rounding                        # holds k min lots at lev AND survives one full-book stop round
    price_buffer = 1.25                                                  # min lots bound by minOrderQty grow with price
    practical_margin_only = round(theoretical * price_buffer + 5 * (sl + fees_ + ov), 2)   # + 5 full-book stop rounds of runway
    exposure_rule_min = round(exposure * risk["max_exposure_x_equity"] ** -1, 2) if risk["max_exposure_x_equity"] else None   # equity needed so all k lots fit under exposure<=equity*x
    practical = round(max(practical_margin_only, exposure_rule_min or 0), 2)
    recommended = round(math.ceil(max(exposure_rule_min or 0, theoretical) * price_buffer / 10) * 10 + 0, 2)
    recommended = max(recommended, round(math.ceil((practical + 10 * (sl + fees_ + ov)) / 10) * 10, 2))
    return dict(ts=int(time.time() * 1000), leverage=lev, max_open=cfg["max_open"], worst_sl_pct=worst_sl, sleeves=dict(REVERT=dict(tp=cfg["rv_tp"], sl=cfg["rv_sl"]), PULLBACK=dict(tp=cfg["pb_tp"], sl=cfg["pb_sl"])),
                markets=rows, worst_case_book=[r["symbol"] for r in top],
                worst_case=dict(positions=k, exposure_usd=round(exposure, 4), initial_margin_usd=round(im, 4), all_hit_sl_loss_usd=round(sl, 4), fees_usd=round(fees_, 4), stop_overshoot_usd=round(ov, 4),
                                total_loss_if_all_sl_usd=round(sl + fees_ + ov, 4), rounding_buffer_usd=rounding),
                tiers=dict(minimum_theoretical_usd=round(theoretical, 2), minimum_practical_usd=practical, recommended_tiny_live_test_usd=recommended,
                           notes=dict(theoretical=f"initial margin for the {k} largest minimum lots at {lev:.0f}x + one full-book stop round (all at {worst_sl}% SL) + round-trip taker fees + {risk.get('stop_overshoot_pct',0.1)}% stop overshoot + ${rounding} rounding. Exposure/equity rule NOT satisfiable at this size.",
                                      practical=f"max(theoretical x{price_buffer} + 5 full-book stop rounds = ${practical_margin_only}, exposure rule: sum of {k} min lots ${exposure:.2f} <= equity x {risk['max_exposure_x_equity']} -> ${exposure_rule_min})",
                                      recommended=f"exposure rule with {price_buffer}x price buffer, plus 10 full-book stop rounds of runway, rounded up to $10")))

def main():
    strat = engine.load_strategy_cfg(); sizing = engine.CFG["sizing"]; risk = engine.CFG["risk"]
    sec = os.path.join(BASE, engine.CFG["secrets_file"])
    c = from_secrets(sec) if os.path.exists(sec) else LiveClient("x", "x")
    instr = {}
    for i in c.instruments():
        lot = i.get("lotSizeFilter", {}); pf = i.get("priceFilter", {}); lf = i.get("leverageFilter", {})
        instr[i["symbol"]] = dict(qtyStep=float(lot.get("qtyStep", 0)), minQty=float(lot.get("minOrderQty", 0)), minNotional=float(lot.get("minNotionalValue") or 0), tickSize=float(pf.get("tickSize", 0)), maxLeverage=float(lf.get("maxLeverage") or 0), status=i.get("status"))
    marks, fees, mmr = {}, {}, {}
    for m in strat["markets"]:
        sym = engine.to_symbol(m); marks[sym] = float(c.tickers(sym)[0]["markPrice"])
        try: mmr[sym] = float(c.risk_limit(sym)[0].get("maintenanceMargin") or 0.01)
        except Exception: mmr[sym] = 0.01
        if os.path.exists(sec):
            try: fr = c.fee_rate(sym); fees[sym] = dict(taker=float(fr["takerFeeRate"]), maker=float(fr["makerFeeRate"]))
            except Exception: pass
    out = compute(strat, instr, marks, fees, sizing, risk, mmr); out["strategy_sha256"] = strat["sha256"]; out["markets_list"] = strat["markets"]; out["fee_source"] = "account fee-rate" if fees else "default VIP0"
    os.makedirs(engine.STATE, exist_ok=True); path = os.path.join(engine.STATE, f"capital_{out['ts']}.json"); json.dump(out, open(path, "w"), indent=1)
    if "--json" in sys.argv: print(json.dumps(out, indent=1)); return
    print(f"strategy markets {len(strat['markets'])}: {', '.join(engine.to_symbol(m) for m in strat['markets'])}")
    print(f"leverage {out['leverage']:.0f}x   max open {out['max_open']}   worst SL {out['worst_sl_pct']}% (PULLBACK; REVERT {out['sleeves']['REVERT']['sl']}%)   fees {out['fee_source']}")
    print(f"{'symbol':9s} {'mark':>10s} {'step':>7s} {'minQty':>7s} {'minNot':>6s} {'-> min qty':>10s} {'notional':>9s} {'bound':>11s} {'IM@lev':>7s} {'SLloss':>7s} {'feesRT':>7s} {'liqDist%':>8s}")
    for r in out["markets"]:
        print(f"{r['symbol']:9s} {r['mark']:>10.4f} {r['qtyStep']:>7g} {r['minOrderQty']:>7g} {r['minNotional']:>6g} {r['min_qty']:>10g} {r['min_notional_usd']:>9.2f} {r['bound_by']:>11s} {r['initial_margin_usd']:>7.2f} {r['sl_loss_usd']:>7.3f} {r['fees_roundtrip_usd']:>7.3f} {r['liquidation_distance_pct']:>8.2f}")
    w = out["worst_case"]
    print(f"\nworst-case book = {w['positions']} largest min lots: {', '.join(out['worst_case_book'])}")
    print(f"  exposure ${w['exposure_usd']:.2f}   initial margin ${w['initial_margin_usd']:.2f}   all-hit-SL loss ${w['all_hit_sl_loss_usd']:.2f}   fees ${w['fees_usd']:.2f}   overshoot ${w['stop_overshoot_usd']:.2f}   -> total loss if all SL ${w['total_loss_if_all_sl_usd']:.2f}")
    t = out["tiers"]
    print(f"\nMINIMUM THEORETICAL CAPITAL: ${t['minimum_theoretical_usd']:.2f}\n  {t['notes']['theoretical']}")
    print(f"MINIMUM PRACTICAL CAPITAL: ${t['minimum_practical_usd']:.2f}\n  {t['notes']['practical']}")
    print(f"RECOMMENDED TINY LIVE TEST CAPITAL: ${t['recommended_tiny_live_test_usd']:.2f}\n  {t['notes']['recommended']}")
    print(f"\nsaved {path}")
if __name__ == "__main__": main()

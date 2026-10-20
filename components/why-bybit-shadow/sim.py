#!/usr/bin/env python3
"""Pure trade simulator for the shadow books. No I/O, no network, no globals.

Mirrors the live engine's paper accounting (bybit_paper.py open_position /
on_mark / close_position) so a shadow trade is priced exactly as a live-engine
paper trade would be:

  entry   = reference mark +/- spread_bps slippage (against you)
  fee_in  = notional * taker_bps
  exits   = checked on every mark tick: STOP first, then TARGET (as live on_mark)
            TIMEOUT at bars_held >= timeout_bars, exit at the mark
  exit px = exit level -/+ spread_bps slippage (against you)
  fee_out = maker on TARGET, taker otherwise
  net     = gross - fees - funding

Trades are plain dicts so they persist as JSON.
"""


def new_trade(cand_id, book, market, side, strategy, ref_price, now_ms, cfg, notional=None):
    W = cfg["wy"]
    fees = cfg["fees"]
    tp_pct, sl_pct = ((W["rv_tp_pct"], W["rv_sl_pct"]) if strategy == "WY-REVERT"
                      else (W["pb_tp_pct"], W["pb_sl_pct"]))
    notional = float(cfg["sizing"]["clip_usd"] if notional is None else notional)
    slip = ref_price * fees["spread_bps"] / 1e4
    entry = ref_price + slip if side == "long" else ref_price - slip
    if side == "long":
        target, stop = entry * (1 + tp_pct / 100), entry * (1 - sl_pct / 100)
    else:
        target, stop = entry * (1 - tp_pct / 100), entry * (1 + sl_pct / 100)
    return dict(id=f"{cand_id}:{book}", cand_id=cand_id, book=book, market=market, side=side,
                strategy=strategy, ref_price=ref_price, entry=entry, opened_ms=int(now_ms),
                bars_held=0, tp_pct=tp_pct, sl_pct=sl_pct, target=target, stop=stop,
                notional=notional, fee_in=notional * fees["taker_bps"] / 1e4,
                mfe_pct=0.0, mae_pct=0.0)


def _fav_pct(t, price):
    d = (price - t["entry"]) / t["entry"] * 100
    return d if t["side"] == "long" else -d


def mark_step(t, price):
    """One mark tick. Updates MFE/MAE and returns 'STOP' / 'TARGET' / None."""
    f = _fav_pct(t, price)
    if f > t["mfe_pct"]:
        t["mfe_pct"] = f
    if f < t["mae_pct"]:
        t["mae_pct"] = f
    if t["side"] == "long":
        if price <= t["stop"]:
            return "STOP"
        if price >= t["target"]:
            return "TARGET"
    else:
        if price >= t["stop"]:
            return "STOP"
        if price <= t["target"]:
            return "TARGET"
    return None


def bar_step(t, bar):
    """Historical catch-up ONLY (restart replay): a closed bar [ms,o,h,l,c].

    Pessimistic when a bar spans both levels: STOP wins. Returns (reason, level)
    or (None, None). Also updates MFE/MAE from the bar's extremes.
    """
    hi, lo = bar[2], bar[3]
    if t["side"] == "long":
        t["mfe_pct"] = max(t["mfe_pct"], _fav_pct(t, hi))
        t["mae_pct"] = min(t["mae_pct"], _fav_pct(t, lo))
        if lo <= t["stop"]:
            return "STOP", t["stop"]
        if hi >= t["target"]:
            return "TARGET", t["target"]
    else:
        t["mfe_pct"] = max(t["mfe_pct"], _fav_pct(t, lo))
        t["mae_pct"] = min(t["mae_pct"], _fav_pct(t, hi))
        if hi >= t["stop"]:
            return "STOP", t["stop"]
        if lo <= t["target"]:
            return "TARGET", t["target"]
    return None, None


def close_record(t, exit_level, reason, now_ms, cfg, funding_usd=0.0, funding_periods=0):
    """Final accounting for a closed trade -> journal record (dict)."""
    fees = cfg["fees"]
    slip = exit_level * fees["spread_bps"] / 1e4
    px = exit_level - slip if t["side"] == "long" else exit_level + slip
    fee_out = t["notional"] * (fees["maker_bps"] if reason == "TARGET" else fees["taker_bps"]) / 1e4
    pnl_pct = ((px - t["entry"]) / t["entry"] * 100 if t["side"] == "long"
               else (t["entry"] - px) / t["entry"] * 100)
    gross = t["notional"] * pnl_pct / 100
    fee_total = t["fee_in"] + fee_out
    net = gross - fee_total - funding_usd
    return dict(event="CLOSE", cand_id=t["cand_id"], book=t["book"], market=t["market"],
                side=t["side"], strategy=t["strategy"], entry=t["entry"], exit=px,
                exit_level=exit_level, exit_type=reason, opened_ms=t["opened_ms"], ts=int(now_ms),
                hold_min=round((now_ms - t["opened_ms"]) / 60000, 1), bars_held=t["bars_held"],
                notional=t["notional"], tp_pct=t["tp_pct"], sl_pct=t["sl_pct"],
                gross_pct=round(pnl_pct, 5), gross_usd=round(gross, 5),
                fees_usd=round(fee_total, 5), funding_usd=round(funding_usd, 5),
                funding_periods=funding_periods, net_usd=round(net, 5),
                net_bps=round(net / t["notional"] * 1e4, 3),
                gross_bps=round(gross / t["notional"] * 1e4, 3),
                mfe_pct=round(t["mfe_pct"], 4), mae_pct=round(t["mae_pct"], 4))

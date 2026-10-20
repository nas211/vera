"""Read-only mirror of the strategy sleeves' TP/SL geometry and leverage.

The values are read from the SAME environment variable names with the SAME
defaults as yellow_paper.CFG (WY_RV_TP_PCT 0.30, WY_RV_SL_PCT 1.60, WY_PB_TP_PCT
0.80, WY_PB_SL_PCT 2.00, WY_LEVERAGE 10, WY_TIMEOUT_BARS 144). A test
(tests/execution/test_strategy_integrity.py) parses yellow_paper.py and fails if
these defaults ever diverge. Nothing here changes strategy economics."""
from __future__ import annotations
import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SleeveGeometry:
    strategy: str
    tp_pct: Decimal
    sl_pct: Decimal


def _env_dec(k: str, d: str) -> Decimal:
    return Decimal(str(os.environ.get(k, d)))


def sleeves() -> dict:
    return {
        "WY-REVERT": SleeveGeometry("WY-REVERT", _env_dec("WY_RV_TP_PCT", "0.30"), _env_dec("WY_RV_SL_PCT", "1.60")),
        "WY-PULLBACK": SleeveGeometry("WY-PULLBACK", _env_dec("WY_PB_TP_PCT", "0.80"), _env_dec("WY_PB_SL_PCT", "2.00")),
    }


def leverage() -> int:
    return int(float(os.environ.get("WY_LEVERAGE", "10")))


def timeout_bars() -> int:
    return int(os.environ.get("WY_TIMEOUT_BARS", "144"))


def geometry_for(strategy: str) -> SleeveGeometry:
    g = sleeves().get(strategy)
    if g is None:
        from .ai_policy import parse
        ai = parse(strategy)
        if ai:
            g = SleeveGeometry(strategy, ai['tp_bps']/Decimal(100), ai['sl_bps']/Decimal(100))
    if g is None:
        raise KeyError(f"unknown strategy sleeve {strategy!r}; known: {sorted(sleeves())}")
    return g


def protection_prices(strategy: str, direction: str, entry: Decimal):
    """(target_price, stop_price) exactly as yellow_paper.open_position computes them
    (percent of entry), before tick rounding."""
    g = geometry_for(strategy)
    tp, sl = g.tp_pct / Decimal(100), g.sl_pct / Decimal(100)
    if direction == "long":
        return entry * (1 + tp), entry * (1 - sl)
    if direction == "short":
        return entry * (1 - tp), entry * (1 + sl)
    raise ValueError(direction)

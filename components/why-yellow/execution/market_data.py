"""Market-data freshness and continuity gate for EXECUTION decisions. It does not
compute indicators and does not alter yellow_paper's strategy logic; it only
answers 'may a new entry be taken on this market right now?' and 'is this mark
fresh enough to evaluate a software stop?'."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class MarketFreshness:
    last_mark: Optional[float] = None          # price
    last_mark_ts: Optional[float] = None       # seconds
    last_bar_open_ms: Optional[int] = None     # completed bar open time
    last_bar_recv_ts: Optional[float] = None
    continuity_ok: bool = False                # False until a seed or a contiguous sequence establishes it
    bars_seen: int = 0
    gap_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    future_count: int = 0


BAR_ACCEPTED, BAR_DUPLICATE, BAR_OUT_OF_ORDER, BAR_GAP, BAR_FUTURE = "ACCEPTED", "DUPLICATE", "OUT_OF_ORDER", "GAP", "FUTURE"


class FreshnessMonitor:
    def __init__(self, bar_interval_ms: int = 300_000, max_market_data_age_s: float = 30.0,
                 max_bar_age_s: Optional[float] = None, max_future_skew_s: float = 5.0,
                 clock: Callable[[], float] = None):
        self.interval_ms = int(bar_interval_ms)
        self.max_age_s = float(max_market_data_age_s)
        # a completed 5m bar is 'current' until the next one should have closed (+ grace)
        self.max_bar_age_s = float(max_bar_age_s) if max_bar_age_s is not None else bar_interval_ms / 1000.0 * 2
        self.max_future_skew_s = float(max_future_skew_s)
        self.clock = clock or (lambda: __import__("time").time())
        self.markets: Dict[str, MarketFreshness] = {}

    def _m(self, market: str) -> MarketFreshness:
        return self.markets.setdefault(market, MarketFreshness())

    # ---- ingestion
    def on_mark(self, market: str, price: float, ts: Optional[float] = None) -> str:
        now = self.clock(); ts = now if ts is None else float(ts)
        m = self._m(market)
        if ts - now > self.max_future_skew_s:
            m.future_count += 1
            return BAR_FUTURE
        if m.last_mark_ts is not None and ts < m.last_mark_ts:
            m.out_of_order_count += 1
            return BAR_OUT_OF_ORDER
        m.last_mark, m.last_mark_ts = float(price), ts
        return BAR_ACCEPTED

    def seed(self, market: str, bar_open_ms_list: List[int]) -> bool:
        """Establish continuity from a contiguous history (e.g. venue REST klines)."""
        m = self._m(market)
        if not bar_open_ms_list:
            return False
        opens = sorted(set(int(x) for x in bar_open_ms_list))
        contiguous = all(b - a == self.interval_ms for a, b in zip(opens, opens[1:]))
        m.last_bar_open_ms = opens[-1]
        m.last_bar_recv_ts = self.clock()
        m.continuity_ok = contiguous
        m.bars_seen = len(opens)
        return contiguous

    def on_bar_close(self, market: str, bar_open_ms: int, recv_ts: Optional[float] = None) -> str:
        now = self.clock(); recv_ts = now if recv_ts is None else float(recv_ts)
        m = self._m(market)
        bar_open_ms = int(bar_open_ms)
        if bar_open_ms / 1000.0 - now > self.max_future_skew_s:
            m.future_count += 1
            return BAR_FUTURE
        if m.last_bar_open_ms is not None:
            if bar_open_ms == m.last_bar_open_ms:
                m.duplicate_count += 1
                return BAR_DUPLICATE
            if bar_open_ms < m.last_bar_open_ms:
                m.out_of_order_count += 1
                return BAR_OUT_OF_ORDER
            if bar_open_ms - m.last_bar_open_ms != self.interval_ms:
                m.gap_count += 1
                m.continuity_ok = False
                m.last_bar_open_ms, m.last_bar_recv_ts = bar_open_ms, recv_ts
                m.bars_seen += 1
                return BAR_GAP
        else:
            # first bar with no seed: continuity unknown -> stays False until seeded
            m.last_bar_open_ms, m.last_bar_recv_ts = bar_open_ms, recv_ts
            m.bars_seen += 1
            return BAR_ACCEPTED
        m.last_bar_open_ms, m.last_bar_recv_ts = bar_open_ms, recv_ts
        m.bars_seen += 1
        return BAR_ACCEPTED

    def on_disconnect(self, market: Optional[str] = None) -> None:
        for k, m in self.markets.items():
            if market is None or k == market:
                m.continuity_ok = False

    # ---- queries
    def mark_age(self, market: str, now: Optional[float] = None) -> Optional[float]:
        m = self._m(market); now = self.clock() if now is None else now
        return None if m.last_mark_ts is None else now - m.last_mark_ts

    def mark_fresh(self, market: str, now: Optional[float] = None) -> bool:
        age = self.mark_age(market, now)
        return age is not None and 0 <= age <= self.max_age_s

    def entry_allowed(self, market: str, now: Optional[float] = None) -> Tuple[bool, str]:
        now = self.clock() if now is None else now
        m = self._m(market)
        if m.last_mark_ts is None:
            return False, "no_mark"
        age = now - m.last_mark_ts
        if age < -self.max_future_skew_s:
            return False, "clock_discrepancy"
        if age > self.max_age_s:
            return False, f"mark_stale:{age:.1f}s"
        if m.last_bar_open_ms is None:
            return False, "no_completed_bar"
        bar_age = now - m.last_bar_recv_ts if m.last_bar_recv_ts is not None else None
        if bar_age is None or bar_age > self.max_bar_age_s:
            return False, f"bar_stale:{bar_age}"
        if not m.continuity_ok:
            return False, "continuity_broken"
        return True, "ok"

    def snapshot(self, market: str, now: Optional[float] = None) -> dict:
        now = self.clock() if now is None else now
        m = self._m(market)
        ok, reason = self.entry_allowed(market, now)
        return dict(market=market, now=now, data_ts=m.last_mark_ts, age=self.mark_age(market, now),
                    last_bar_open_ms=m.last_bar_open_ms, bar_completed=m.last_bar_open_ms is not None,
                    continuity_ok=m.continuity_ok, entry_allowed=ok, reason=reason,
                    gaps=m.gap_count, duplicates=m.duplicate_count, out_of_order=m.out_of_order_count, future=m.future_count)

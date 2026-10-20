"""Account-scoped live entry confirmation. Never submits orders or changes exits.

Pending setups are deliberately discarded on restart/disconnect. Completed bars
only; a missing, stale or discontinuous history blocks new entries.
"""
from dataclasses import replace
from decimal import Decimal
import math
import threading
import time

OWNER_ACCOUNT = "8f3efa87-f7c0-4c21-ba1e-46b207613f96"
INTERVALS = {"5m": 300_000, "1h": 3_600_000}


def ema(values, period):
    result = values[0]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result += alpha * (value - result)
    return result


class LiveEntryFilter:
    def __init__(self, account_id=OWNER_ACCOUNT, clock=time.time):
        self.account_id, self.clock = account_id, clock
        self.lock = threading.RLock()
        self.bars = {}
        self.pending = {}
        self.last_decision = {}
        self.decision_at = {}
        self.decision_counts = {}
        self.allow_revert = False

    def applies(self, ctx):
        return ctx.account_id == self.account_id and ctx.venue == "yellow"

    def _bar(self, row, interval):
        step = INTERVALS[interval]
        ts = int(row[0])
        values = tuple(float(v) for v in row[1:5])
        if len(values) != 4 or not all(math.isfinite(v) and v > 0 for v in values):
            raise ValueError("invalid candle prices")
        o, h, l, c = values
        if ts % step or h < max(o, c, l) or l > min(o, c, h):
            raise ValueError("invalid candle geometry")
        return (ts, o, h, l, c)

    def seed(self, market, interval, rows):
        with self.lock:
            key = (market, interval)
            previous = self.bars.get(key, [])
            self.bars[key] = []
            try:
                parsed = sorted({int(r[0]): self._bar(r, interval) for r in rows}.values())
                step = INTERVALS[interval]
                parsed = [r for r in parsed if r[0] + step <= self.clock() * 1000]
                suffix = []
                for row in parsed:
                    if suffix and row[0] - suffix[-1][0] != step:
                        suffix = []
                    suffix.append(row)
                self.bars[key] = suffix[-300:]
            except (ValueError, TypeError, IndexError, OverflowError):
                self.cancel(market, "invalid_candle_history")
                return False
            current = self._current(market, interval)
            # A REST backfill is another delivery path for completed candles.
            # Preserve setups only when the prior evidence is unchanged and the
            # replacement history is current, continuous and overlaps its tail.
            by_time = {r[0]: r for r in self.bars[key]}
            continuous = bool(previous and previous[-1][0] in by_time
                              and all(by_time[r[0]] == r for r in previous if r[0] in by_time))
            if not current or not continuous:
                self.cancel(market, "candle_history_replaced")
            return current

    def on_bar(self, market, interval, row):
        with self.lock:
            key = (market, interval)
            try:
                bar = self._bar(row, interval)
                step = INTERVALS[interval]
                if bar[0] + step > self.clock() * 1000:
                    return
                history = self.bars.setdefault(key, [])
                if history and bar[0] <= history[-1][0]:
                    return
                if history and bar[0] - history[-1][0] != step:
                    history.clear()
                    self.cancel(market)
                history.append(bar)
                del history[:-300]
            except (ValueError, TypeError, IndexError, OverflowError):
                self.bars[key] = []
                self.cancel(market)

    def _current(self, market, interval):
        rows = self.bars.get((market, interval), [])
        step = INTERVALS[interval]
        expected = int(self.clock() * 1000) // step * step - step
        return len(rows) >= 250 and rows[-1][0] == expected

    def history_ready(self, market, interval):
        with self.lock:
            return self._current(market, interval)

    def _aligned(self, sig):
        for interval in INTERVALS:
            if not self._current(sig.market, interval):
                return False
            closes = [r[4] for r in self.bars[(sig.market, interval)][-250:]]
            fast, slow = ema(closes, 20), ema(closes, 50)
            if sig.direction == "long" and fast <= slow:
                return False
            if sig.direction == "short" and fast >= slow:
                return False
        return sig.direction in ("long", "short")

    def offer(self, sig):
        with self.lock:
            if sig.strategy == "WY-REVERT" and not self.allow_revert:
                reason = "revert_entries_disabled"
            elif sig.strategy not in ("WY-PULLBACK", "WY-REVERT"):
                # This policy owns the two Swing sleeves only.
                return None
            elif not 0 <= self.clock() - sig.ts <= 120:
                reason = "stale_or_future_setup"
            elif not self._aligned(sig):
                reason = "trend_unconfirmed_or_data_unready"
            else:
                key=(sig.market+"|"+sig.strategy) if self.allow_revert else sig.market
                prior = self.pending.get(key)
                if prior is None or prior.signal_id != sig.signal_id:
                    self.pending[key] = sig
                reason = "waiting_for_recovery_close"
            self._decision(sig.market, reason)
            return reason

    def _decision(self, market, reason):
        self.last_decision[market] = reason
        self.decision_at[market] = self.clock()
        self.decision_counts[reason] = self.decision_counts.get(reason, 0) + 1

    def cancel(self, market=None, reason="setup_cancelled"):
        with self.lock:
            for key, sig in list(self.pending.items()):
                if market is None or sig.market == market:
                    self.pending.pop(key, None)
                    self._decision(sig.market, reason)

    def disconnect(self):
        with self.lock:
            self.cancel(reason="feed_disconnected")
            self.bars.clear()

    def risk_evidence(self, market, direction, now):
        from statistics import median
        bars = self.bars[(market, '1h')]
        previous = bars[-15:-1]
        current = bars[-14:]
        true_ranges = [max(b[2]-b[3], abs(b[2]-a[4]), abs(b[3]-a[4])) for a,b in zip(previous,current)]
        atr = sum(true_ranges)/len(true_ranges)/bars[-1][4]
        reaches = []
        for i in range(50, len(bars)-6):
            entry = bars[i][4]; window = bars[i+1:i+7]
            reach = max(b[2] for b in window)/entry-1 if direction=='long' else 1-min(b[3] for b in window)/entry
            reaches.append(max(0, reach))
        return dict(version=1,as_of=now,hourly_atr_fraction=atr,
                    median_6h_favorable_fraction=median(reaches),samples=len(reaches),
                    hourly_last_open_ms=bars[-1][0])

    def ready(self, freshness):
        """Consume confirmed setups once; the execution engine still gates orders."""
        out = []
        with self.lock:
            now = self.clock()
            for key, sig in list(self.pending.items()):
                market=sig.market
                if now - sig.ts > 900 or now < sig.ts:
                    self.pending.pop(key)
                    self._decision(market, "setup_expired")
                    continue
                if not self._aligned(sig):
                    # Missing a just-closed hour can be a feed ordering race.
                    # Wait for its candle, but never enter while it is missing.
                    continue
                previous, current = self.bars[(market, "5m")][-2:]
                closed_at = (current[0] + INTERVALS["5m"]) / 1000
                if closed_at <= sig.ts or not 0 <= now - closed_at <= 120:
                    continue
                recovery = ((current[4] > current[1] and current[4] > previous[2])
                            if sig.direction == "long" else
                            (current[4] < current[1] and current[4] < previous[3]))
                if not recovery or not freshness.entry_allowed(market, now)[0]:
                    continue
                mark = freshness.markets[market].last_mark
                if mark is None or not math.isfinite(mark) or mark <= 0:
                    continue
                # Do not chase a confirmation which has already reversed.
                if (sig.direction == "long" and mark <= previous[2]) or (sig.direction == "short" and mark >= previous[3]):
                    continue
                detail = dict(sig.detail)
                detail["live_entry_filter"] = dict(version=1, original_signal_ts=sig.ts,
                    recovery_closed_at=closed_at, hourly_bar_open_ms=self.bars[(market, "1h")][-1][0])
                detail['risk_evidence'] = self.risk_evidence(market, sig.direction, now)
                out.append(replace(sig, ref_price=Decimal(str(mark)), ts=closed_at, detail=detail))
                self.pending.pop(key)
                self._decision(market, "recovery_confirmed")
        return out

    def confirm_agent(self,sig,freshness):
        with self.lock:
            now=self.clock()
            if not self.allow_revert or not 0<=now-sig.ts<=30 or not self._aligned(sig):return None
            previous,current=self.bars[(sig.market,'5m')][-2:]
            closed=(current[0]+300000)/1000
            if not 0<=now-closed<=120 or not freshness.entry_allowed(sig.market,now)[0]:return None
            recovery=(current[4]>current[1] and current[4]>previous[2]) if sig.direction=='long' else (current[4]<current[1] and current[4]<previous[3])
            mark=freshness.markets[sig.market].last_mark
            if not recovery or mark is None or not math.isfinite(mark) or mark<=0:return None
            if (sig.direction=='long' and mark<=previous[2]) or (sig.direction=='short' and mark>=previous[3]):return None
            detail=dict(sig.detail,live_entry_filter=dict(version=1,recovery_closed_at=closed,source='discovery_agents'),
                risk_evidence=self.risk_evidence(sig.market,sig.direction,now))
            return replace(sig,ref_price=Decimal(str(mark)),detail=detail)

    def status(self):
        with self.lock:
            markets = sorted({market for market, _ in self.bars})
            return dict(enabled=True, account_id=self.account_id, revert_entries_enabled=self.allow_revert,
                        trend_timeframes=["5m", "1h"], pending_markets=sorted(self.pending),
                        ready_markets=[market for market in markets
                                       if all(self._current(market, interval) for interval in INTERVALS)],
                        candle_history={market:{interval:dict(
                            bars=len(self.bars.get((market,interval),[])),
                            last_open_ms=(self.bars[(market,interval)][-1][0]
                                          if self.bars.get((market,interval)) else None),
                            ready=self._current(market,interval)) for interval in INTERVALS}
                            for market in markets},
                        decisions=dict(self.last_decision), decision_at=dict(self.decision_at),
                        decision_counts=dict(self.decision_counts))


JP_ACCOUNT = "04d10bf8-6614-4c11-9ab9-240e26fce66c"


class AccountEntryFilters:
    """Independent pending setups per account, with the same market evidence."""
    def __init__(self, account_ids=(OWNER_ACCOUNT, JP_ACCOUNT), clock=time.time):
        self.filters = {aid: LiveEntryFilter(aid, clock) for aid in account_ids}

    def applies(self, ctx):
        return any(gate.applies(ctx) for gate in self.filters.values())

    def seed(self, market, interval, rows):
        results = [gate.seed(market, interval, rows) for gate in self.filters.values()]
        return all(results)

    def on_bar(self, market, interval, row):
        for gate in self.filters.values():
            gate.on_bar(market, interval, row)

    def history_ready(self, market, interval):
        return all(gate.history_ready(market, interval) for gate in self.filters.values())

    def cancel(self, market=None):
        for gate in self.filters.values():
            gate.cancel(market)

    def disconnect(self):
        for gate in self.filters.values():
            gate.disconnect()

    def status(self):
        return dict(enabled=True, account_ids=list(self.filters),
                    by_account={aid: gate.status() for aid, gate in self.filters.items()})

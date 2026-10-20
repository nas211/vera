"""Fan canonical signals/marks to isolated Paper, Yellow and WEEX execution paths."""


class ExecutionRouter:
    def __init__(self, yellow, paper=None, weex=None, scalp=None):
        self.yellow, self.paper, self.weex, self.scalp = yellow, paper, weex, scalp
        self.registries = [x for x in (yellow, weex) if x is not None]

    def refresh(self):
        return {"yellow": self.yellow.refresh(), **({"weex": self.weex.refresh()} if self.weex else {}), **({"scalp": self.scalp.refresh()} if self.scalp else {})}

    def manage_all(self):
        return {"yellow": self.yellow.manage_all(), **({"weex": self.weex.manage_all()} if self.weex else {})}

    def dispatch(self, signal):
        out = {"yellow": self.yellow.dispatch(signal)}
        if self.weex: out["weex"] = self.weex.dispatch(signal)
        if self.paper: out["paper"] = self.paper.dispatch(signal)
        return out

    def seed_candles(self, market, interval, rows):
        return self.yellow.seed_candles(market, interval, rows)

    def on_candle(self, market, interval, row):
        self.yellow.on_candle(market, interval, row)

    def candle_history_ready(self, market, interval):
        gate = getattr(self.yellow, "entry_filter", None)
        return gate.history_ready(market,interval) if gate else True

    def dispatch_confirmed(self):
        return self.yellow.dispatch_confirmed()

    def dispatch_close(self, signal):
        # A source exit invalidates a still-pending entry, even when the live
        # venue manages the actual exit independently.
        if getattr(self.yellow, "entry_filter", None):
            self.yellow.entry_filter.cancel(signal.market)
        # Venue engines own their TARGET/STOP protection lifecycle. Paper has no
        # exchange-side protection orders, so it consumes every canonical close.
        # TIMEOUT remains a canonical explicit close for every venue.
        venue_close = str(signal.reason).upper() == "TIMEOUT"
        out = {"yellow": self.yellow.dispatch_close(signal) if venue_close else "VENUE_MANAGED"}
        if self.weex: out["weex"] = self.weex.dispatch_close(signal) if venue_close else "VENUE_MANAGED"
        if self.paper: out["paper"] = self.paper.dispatch_close(signal)
        return out

    def dispatch_ai(self, signal, *, live_enabled=True, paper_enabled=False):
        # Only the audited Yellow execution adapter receives live AI signals.
        out = {}
        for name, target, enabled in [('yellow', self.yellow, live_enabled), ('paper', self.paper, paper_enabled)]:
            if target is not None and enabled:
                try:
                    out[name] = target.dispatch(signal)
                except Exception as exc:
                    out[name] = 'ERROR:' + type(exc).__name__
        return out

    def close_for_user(self, ident, venue, position_id):
        from .accounts import AccountError
        if venue == 'yellow' and str(position_id).startswith('scalp-') and self.scalp:
            return self.scalp.close_for_user(ident,position_id)
        registry = self.yellow if venue == "yellow" else self.weex if venue == "weex" else None
        if registry is None:
            raise AccountError("execution_unavailable", 503)
        return registry.close_for_user(ident, position_id)

    def on_mark(self, market, price, ts=None):
        self.yellow.on_mark(market, price, ts)
        if self.weex: self.weex.on_mark(market, price, ts)
        if self.paper: self.paper.on_mark(market, price, ts)

    def on_disconnect(self, reason="feed_disconnect"):
        self.yellow.on_disconnect(reason)
        if self.weex: self.weex.on_disconnect(reason)
        if self.paper: self.paper.on_disconnect(reason)

    def runtime_info(self, account_id):
        return (self.scalp.runtime_info(account_id) if self.scalp else None) or self.yellow.runtime_info(account_id)

    def status(self):
        return {**({"paper": self.paper.status()} if hasattr(self.paper, "status") else {}), "yellow": self.yellow.status(), **({"weex": self.weex.status()} if self.weex else {}), **({"scalp": self.scalp.status()} if self.scalp else {})}

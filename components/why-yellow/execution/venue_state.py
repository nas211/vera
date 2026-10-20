"""Venue connectivity/recovery state. READY (new risk allowed) is reachable only
through RECOVERING -> RECONCILING -> READY, i.e. after a successful reconciliation."""
from __future__ import annotations
from typing import Callable, List, Optional
from .state_machine import check_venue_transition
from .types import VenueState as V


class VenueMonitor:
    def __init__(self, degrade_after_errors: int = 1, clock: Callable[[], float] = None, on_change=None):
        self.state = V.DISCONNECTED
        self.errors = 0
        self.degrade_after = int(degrade_after_errors)
        self.clock = clock or (lambda: __import__("time").time())
        self.history: List[tuple] = []
        self.last_change = self.clock()
        self.last_error: Optional[str] = None
        self.on_change = on_change

    def _to(self, new: V, reason: str = "") -> bool:
        if check_venue_transition(self.state, new):
            self.history.append((self.clock(), self.state.value, new.value, reason))
            self.state = new
            self.last_change = self.clock()
            if self.on_change:
                self.on_change(new, reason)
            return True
        return False

    # ---- events
    def on_connected(self):
        if self.state == V.DISCONNECTED:
            self._to(V.CONNECTED, "connected")

    def on_disconnect(self, reason: str = "disconnect"):
        self.errors = 0
        self.last_error = reason
        if self.state != V.DISCONNECTED:
            self._to(V.DISCONNECTED, reason)

    def on_transport_error(self, reason: str):
        """429/5xx/timeout on any call. Enough of them degrade the venue."""
        self.errors += 1
        self.last_error = reason
        if self.errors >= self.degrade_after and self.state in (V.READY, V.CONNECTED, V.RECONCILING, V.RECOVERING):
            self._to(V.DEGRADED, reason)

    def on_ambiguous_submission(self, reason: str = "ambiguous_submission"):
        self.on_transport_error(reason)

    def on_reconnect(self):
        self.errors = 0
        if self.state in (V.DISCONNECTED, V.DEGRADED, V.CONNECTED):
            self._to(V.RECOVERING, "reconnect")

    def begin_reconcile(self) -> bool:
        if self.state in (V.DISCONNECTED, V.DEGRADED, V.CONNECTED):
            self._to(V.RECOVERING, "reconcile_requested")
        return self._to(V.RECONCILING, "reconcile")

    def reconcile_ok(self):
        self.errors = 0
        self._to(V.READY, "reconciled")

    def reconcile_failed(self, reason: str = "reconcile_failed"):
        self.last_error = reason
        self._to(V.DEGRADED, reason)

    # ---- queries
    @property
    def entries_allowed(self) -> bool:
        return self.state == V.READY

    @property
    def management_possible(self) -> bool:
        """Existing-position management may try the REST path in every state except
        DISCONNECTED; results decide, not the flag."""
        return self.state != V.DISCONNECTED

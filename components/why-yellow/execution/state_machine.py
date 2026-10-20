"""Legal transitions for orders, positions and venue state. Same-state transitions
are idempotent no-ops (return False); anything not listed raises IllegalTransition."""
from __future__ import annotations
from .types import OrderState as O, PositionState as P, VenueState as V


class IllegalTransition(RuntimeError):
    pass


ORDER_TRANSITIONS = {
    O.INTENT: {O.VALIDATED, O.REJECTED, O.FAILED},
    O.VALIDATED: {O.SUBMITTING, O.REJECTED, O.FAILED},
    O.SUBMITTING: {O.ACKNOWLEDGED, O.PARTIALLY_FILLED, O.FILLED, O.REJECTED, O.UNKNOWN, O.CANCELLED, O.FAILED},
    O.UNKNOWN: {O.RECONCILIATION_REQUIRED},
    O.RECONCILIATION_REQUIRED: {O.ACKNOWLEDGED, O.PARTIALLY_FILLED, O.FILLED, O.CANCELLED, O.REJECTED, O.FAILED},
    O.ACKNOWLEDGED: {O.PARTIALLY_FILLED, O.FILLED, O.CANCEL_PENDING, O.CANCELLED, O.RECONCILIATION_REQUIRED, O.FAILED},
    O.PARTIALLY_FILLED: {O.PARTIALLY_FILLED, O.FILLED, O.CANCEL_PENDING, O.CANCELLED, O.RECONCILIATION_REQUIRED},
    O.CANCEL_PENDING: {O.CANCELLED, O.FILLED, O.PARTIALLY_FILLED, O.ACKNOWLEDGED, O.RECONCILIATION_REQUIRED},
    O.FILLED: set(),
    O.CANCELLED: set(),
    O.REJECTED: set(),
    # FAILED -> VALIDATED is reachable only through Ledger.approve_resubmission, which
    # requires a reconciliation that proved the order is absent from the venue.
    O.FAILED: {O.VALIDATED},
}
ORDER_TERMINAL = {O.FILLED, O.CANCELLED, O.REJECTED}
ORDER_LIVE = {O.ACKNOWLEDGED, O.PARTIALLY_FILLED, O.CANCEL_PENDING}
ORDER_AMBIGUOUS = {O.SUBMITTING, O.UNKNOWN, O.RECONCILIATION_REQUIRED}

POSITION_TRANSITIONS = {
    P.PROTECTION_PENDING: {P.PROTECTED, P.CLOSE_PENDING, P.CLOSED, P.RECONCILIATION_REQUIRED},
    P.PROTECTED: {P.PROTECTION_PENDING, P.CLOSE_PENDING, P.CLOSED, P.RECONCILIATION_REQUIRED},
    P.CLOSE_PENDING: {P.CLOSED, P.PROTECTED, P.PROTECTION_PENDING, P.RECONCILIATION_REQUIRED},
    P.RECONCILIATION_REQUIRED: {P.PROTECTION_PENDING, P.PROTECTED, P.CLOSE_PENDING, P.CLOSED},
    P.CLOSED: set(),
}

VENUE_TRANSITIONS = {
    V.DISCONNECTED: {V.CONNECTED, V.RECOVERING},
    V.CONNECTED: {V.RECOVERING, V.DEGRADED, V.DISCONNECTED},
    V.DEGRADED: {V.RECOVERING, V.DISCONNECTED},
    V.RECOVERING: {V.RECONCILING, V.DEGRADED, V.DISCONNECTED},
    V.RECONCILING: {V.READY, V.DEGRADED, V.DISCONNECTED, V.RECOVERING},
    V.READY: {V.DEGRADED, V.DISCONNECTED, V.RECONCILING},
}


def _check(table, kind, cur, new) -> bool:
    if cur == new:
        # PARTIALLY_FILLED -> PARTIALLY_FILLED carries a new fill and is a real event;
        # every other same-state transition is an idempotent no-op.
        return kind == "order" and cur == O.PARTIALLY_FILLED
    allowed = table.get(cur, set())
    if new not in allowed:
        raise IllegalTransition(f"{kind}: {cur.value} -> {new.value} is not a legal transition")
    return True


def check_order_transition(cur: O, new: O) -> bool:
    return _check(ORDER_TRANSITIONS, "order", cur, new)


def check_position_transition(cur: P, new: P) -> bool:
    return _check(POSITION_TRANSITIONS, "position", cur, new)


def check_venue_transition(cur: V, new: V) -> bool:
    return _check(VENUE_TRANSITIONS, "venue", cur, new)

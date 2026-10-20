from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional


class OrderState(str, Enum):
    INTENT = "INTENT"
    VALIDATED = "VALIDATED"
    SUBMITTING = "SUBMITTING"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class PositionState(str, Enum):
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    CLOSE_PENDING = "CLOSE_PENDING"
    CLOSED = "CLOSED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class VenueState(str, Enum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    RECOVERING = "RECOVERING"
    RECONCILING = "RECONCILING"
    READY = "READY"


class OrderPurpose(str, Enum):
    ENTRY = "ENTRY"
    TARGET = "TARGET"
    STOP = "STOP"
    CLOSE = "CLOSE"


SIDES = ("buy", "sell")
DIRECTIONS = ("long", "short")
ORDER_TYPES = ("market", "limit", "post_only", "trigger_market", "trigger_limit")
TIFS = ("gtc", "ioc", "fok")

# Venue order state vocabulary used inside the execution core (venue-specific
# strings are normalised by the transport implementation).
VENUE_OPEN, VENUE_FILLED, VENUE_CANCELLED, VENUE_REJECTED = "open", "filled", "cancelled", "rejected"


@dataclass(frozen=True)
class OrderRequest:
    account_id: str
    market: str
    side: str
    direction: str
    order_type: str
    qty: Decimal
    leverage: int
    client_order_id: str
    price: Optional[Decimal] = None
    reduce_only: bool = False
    time_in_force: str = "gtc"
    trigger_price: Optional[Decimal] = None
    trigger_type: Optional[str] = None


@dataclass(frozen=True)
class OrderAck:
    venue_order_id: str


@dataclass(frozen=True)
class VenueOrder:
    venue_order_id: str
    client_order_id: Optional[str]
    market: str
    side: str
    direction: str
    order_type: str
    qty: Decimal
    filled_qty: Decimal
    price: Optional[Decimal]
    state: str            # open | filled | cancelled | rejected
    reduce_only: bool = False
    trigger_price: Optional[Decimal] = None
    trigger_type: Optional[str] = None


@dataclass(frozen=True)
class VenuePosition:
    market: str
    direction: str
    qty: Decimal
    entry_price: Decimal


@dataclass(frozen=True)
class VenueTrade:
    trade_id: str
    venue_order_id: str
    client_order_id: Optional[str]
    market: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    ts: float


@dataclass(frozen=True)
class VenueAccount:
    available_balance: Decimal
    equity: Decimal


@dataclass(frozen=True)
class Capabilities:
    venue_stop_orders: bool = False      # transport must support verified exchange trigger orders
    client_order_id_dedup: bool = False  # Yellow behaviour unconfirmed (audit Q-03)
    conditional_take_profit: bool = False  # avoid resting limits reserving the stop's closing quantity


@dataclass
class EntrySignal:
    signal_id: str
    account_id: str
    market: str
    direction: str
    strategy: str
    notional_usd: Decimal
    ref_price: Decimal
    ts: float
    detail: Dict[str, Any] = field(default_factory=dict)


def D(x) -> Decimal:
    """Decimal from str/int/Decimal/float without binary-float surprises. Non-finite
    inputs stay non-finite so validation can reject them explicitly."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x)) if x == x and x not in (float("inf"), float("-inf")) else Decimal(str(x))
    return Decimal(str(x))

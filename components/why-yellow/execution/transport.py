"""Transport interface between the execution core and a venue. Two implementations:
execution.mock_adapter.MockYellowAdapter (tests) and execution.yellow_transport
.YellowTransport (wraps yellow_exec.YellowClient; still gated by YELLOW_ARMED)."""
from __future__ import annotations
from typing import List, Optional, Protocol, Tuple
from .types import (Capabilities, OrderAck, OrderRequest, VenueAccount, VenueOrder,
                    VenuePosition, VenueTrade)


class TransportAmbiguous(RuntimeError):
    """The request may or may not have reached the venue (timeout, reset, edge 5xx).
    The caller MUST NOT resubmit; only reconciliation may decide."""


class VenueRejected(RuntimeError):
    """The venue definitively refused the request."""
    def __init__(self, code: str, message: str = "", status: int = 400):
        super().__init__(f"{status} {code}: {message}")
        self.code, self.message, self.status = code, message, status


class VenueUnavailable(RuntimeError):
    """Definitive 'not processed' (e.g. 429) or read-path outage."""
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


Page = Tuple[List, Optional[str]]


class Transport(Protocol):
    capabilities: Capabilities

    def place_order(self, req: OrderRequest) -> OrderAck: ...
    def cancel_order(self, account_id: str, market: str, venue_order_id: str) -> None: ...
    def get_open_orders(self, account_id: str, market: Optional[str] = None, cursor: Optional[str] = None) -> Page: ...
    def get_orders(self, account_id: str, market: Optional[str] = None, since: Optional[float] = None, cursor: Optional[str] = None) -> Page: ...
    def get_positions(self, account_id: str) -> List[VenuePosition]: ...
    def get_trades(self, account_id: str, market: Optional[str] = None, since: Optional[float] = None, cursor: Optional[str] = None) -> Page: ...
    def get_account(self, account_id: str) -> VenueAccount: ...


def iter_pages(fn, *args, max_pages: int = 200, **kwargs):
    """Yield page items lazily. A positive order match may stop early; absence
    is established only by consuming the complete iterator successfully."""
    cursor, pages = None, 0
    seen = set()
    while True:
        items, cursor = fn(*args, cursor=cursor, **kwargs)
        yield from items
        pages += 1
        if not cursor:
            return
        if cursor in seen:
            raise VenueUnavailable(0, "pagination repeated a cursor")
        seen.add(cursor)
        if pages >= max_pages:
            raise VenueUnavailable(0, f"pagination exceeded {max_pages} pages")


def all_pages(fn, *args, max_pages: int = 200, **kwargs) -> list:
    """Complete snapshot: never accepts a partial listing as proof of absence."""
    return list(iter_pages(fn, *args, max_pages=max_pages, **kwargs))

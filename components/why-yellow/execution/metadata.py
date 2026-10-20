"""Venue instrument metadata abstraction. Production values must come from the
venue (Yellow exchangeInfo); tests use StaticMetadata with explicit fixtures.
Nothing here hard-codes Yellow numbers."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Dict, Optional, Protocol


@dataclass(frozen=True)
class MarketFilters:
    symbol: str
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_leverage: Decimal
    status: str                      # "TRADING" is the only tradeable status
    max_qty: Optional[Decimal] = None
    contract_multiplier: Decimal = Decimal(1)   # UNVERIFIED for Yellow (audit Q-06); 1 until confirmed
    maker_fee: Decimal = Decimal(0)
    taker_fee: Decimal = Decimal(0)

    @property
    def tradeable(self) -> bool:
        return self.status == "TRADING"

    def round_qty_down(self, qty: Decimal) -> Decimal:
        return (qty / self.step).to_integral_value(ROUND_DOWN) * self.step

    def round_price(self, px: Decimal, up: bool) -> Decimal:
        return (px / self.tick).to_integral_value(ROUND_CEILING if up else ROUND_DOWN) * self.tick

    def on_step(self, qty: Decimal) -> bool:
        return qty.is_finite() and qty > 0 and (qty / self.step) == (qty / self.step).to_integral_value()

    def on_tick(self, px: Decimal) -> bool:
        return px.is_finite() and px > 0 and (px / self.tick) == (px / self.tick).to_integral_value()

    @classmethod
    def from_adapter_filters(cls, symbol: str, f: dict) -> "MarketFilters":
        """From yellow_exec.YellowClient.filters() output (keys tick/step/min_qty/
        min_notional/max_lev/status/maker/taker)."""
        return cls(symbol=symbol, tick=Decimal(f["tick"]), step=Decimal(f["step"]), min_qty=Decimal(f["min_qty"]),
                   min_notional=Decimal(f["min_notional"]), max_leverage=Decimal(f["max_lev"]), status=str(f["status"]),
                   maker_fee=Decimal(f.get("maker", 0)), taker_fee=Decimal(f.get("taker", 0)))


class MetadataSource(Protocol):
    def filters(self, market: str) -> MarketFilters: ...
    def known_markets(self) -> set: ...


class UnknownMarket(KeyError):
    pass


class StaticMetadata:
    def __init__(self, filters: Dict[str, MarketFilters]):
        self._f = dict(filters)

    def filters(self, market: str) -> MarketFilters:
        try:
            return self._f[market]
        except KeyError:
            raise UnknownMarket(market)

    def known_markets(self) -> set:
        return set(self._f)

    def set_status(self, market: str, status: str) -> None:
        f = self._f[market]
        self._f[market] = MarketFilters(**{**f.__dict__, "status": status})


class AdapterMetadata:
    """Live metadata through a YellowClient-like object exposing filters(market) and
    exchange_info(). Only used by the production wrapper; never by tests."""
    def __init__(self, client):
        self.c = client

    def filters(self, market: str) -> MarketFilters:
        try:
            return MarketFilters.from_adapter_filters(market, self.c.filters(market))
        except KeyError as e:
            raise UnknownMarket(market) from e

    def known_markets(self) -> set:
        return set(self.c.exchange_info())

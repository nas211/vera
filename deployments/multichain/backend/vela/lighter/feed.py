"""Official public REST + sequenced WebSocket feed. No authenticated endpoints."""
import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal as D, InvalidOperation

import httpx
from websockets.asyncio.client import connect

REST = "https://mainnet.zklighter.elliot.ai/api/v1"
WS = "wss://mainnet.zklighter.elliot.ai/stream"
SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")


def decimal(value, positive=False):
    try:
        if len(str(value)) > 80:
            raise ValueError("Numeric value too long")
        result = D(str(value))
    except (InvalidOperation,TypeError):
        raise ValueError("Invalid numeric value") from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Invalid market number")
    return result


@dataclass
class Market:
    symbol: str
    market_id: int
    tick: D
    step: D
    min_qty: D
    min_notional: D
    max_leverage: D
    maintenance: D
    active: bool = True
    mark: D = D(0)
    index: D = D(0)
    change: float = 0
    funding_rate: D | None = None  # fractional hourly estimate (WS fields are percent)
    last_funding_rate: D | None = None
    funding_timestamp: float = 0
    stats_at: float = 0
    book_at: float = 0
    nonce: int | None = None
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)
    candles: list = field(default_factory=list)

    def fresh(self, now=None):
        now = time.time() if now is None else now
        return (self.active and self.nonce is not None and self.mark > 0 and
                self.funding_rate is not None and -2 <= now-self.stats_at <= 15 and
                -2 <= now-self.book_at <= 5 and bool(self.bids) and bool(self.asks) and
                max(self.bids) < min(self.asks))

    def levels(self, side):
        values = self.asks if side == "buy" else self.bids
        return sorted(values.items(), reverse=side == "sell")

    def public(self):
        return dict(symbol=self.symbol, marketId=self.market_id, price=str(self.mark),
                    index=str(self.index), change=self.change, fresh=self.fresh(),
                    statsAt=self.stats_at, bookAt=self.book_at, tick=str(self.tick), step=str(self.step),
                    minQty=str(self.min_qty), minNotional=str(self.min_notional),
                    maxLeverage=str(min(D(3), self.max_leverage)), maintenance=str(self.maintenance),
                    fundingRate=str(self.funding_rate) if self.funding_rate is not None else None,
                    fundingTimestamp=self.funding_timestamp,
                    bids=[[str(p), str(q)] for p,q in self.levels("sell")[:12]],
                    asks=[[str(p), str(q)] for p,q in self.levels("buy")[:12]],
                    candles=self.candles[-120:])


class LighterFeed:
    def __init__(self):
        self.markets = {}
        self.connected = False
        self.reconnects = 0
        self.error = "Connecting to public market data"
        self.last_message = 0

    def metadata(self, data):
        found = set()
        for row in data["order_book_details"]:
            symbol = row["symbol"] + "/USD"
            if symbol not in SYMBOLS or row.get("market_type") != "perp":
                continue
            found.add(symbol)
            tick = D(10) ** -int(row["supported_price_decimals"])
            step = D(10) ** -int(row["supported_size_decimals"])
            initial = decimal(row["min_initial_margin_fraction"], True) / 10000
            maintenance = decimal(row["maintenance_margin_fraction"], True) / 10000
            if not 0 < maintenance < initial <= 1 or not 0 < tick <= 1 or not 0 < step <= 1:
                raise ValueError("Unsupported market specification")
            m = self.markets.get(symbol) or Market(symbol, int(row["market_id"]), tick, step,
                decimal(row["min_base_amount"], True), decimal(row["min_quote_amount"], True), 1/initial, maintenance)
            if m.market_id != int(row["market_id"]):
                raise ValueError("Market identifier changed; restart required")
            m.tick, m.step, m.maintenance, m.max_leverage = tick, step, maintenance, 1/initial
            m.min_qty, m.min_notional = decimal(row["min_base_amount"], True), decimal(row["min_quote_amount"], True)
            m.active = (row["status"] == "active" and not row.get("is_frozen", False)
                        and not row.get("market_config", {}).get("force_reduce_only", False))
            self.markets[symbol] = m
        if found != set(SYMBOLS):
            for m in self.markets.values():
                m.active = False
            raise ValueError("Required perpetual markets unavailable")

    def message(self, data, now=None):
        now = time.time() if now is None else now
        self.last_message = now
        kind = data.get("type", "")
        channel = data.get("channel", "").replace("/", ":")
        if not channel or ":" not in channel:
            return
        try:
            market_id = int(channel.split(":")[-1])
        except ValueError:
            return
        m = next((m for m in self.markets.values() if m.market_id == market_id), None)
        if m is None:
            return
        ts = float(data.get("timestamp", 0))/1000
        if not -2 <= now-ts <= 15:
            m.book_at = m.stats_at = 0
            raise ValueError("Stale server timestamp")
        if channel.startswith("order_book:"):
            book = data["order_book"]
            snapshot = kind == "subscribed/order_book"
            nonce = int(book["nonce"])
            if not snapshot and (m.nonce is None or int(book.get("begin_nonce", -1)) != m.nonce):
                m.nonce, m.book_at = None, 0
                raise ValueError("Orderbook sequence gap")
            if not snapshot and nonce < m.nonce:
                raise ValueError("Orderbook nonce moved backwards")
            bids, asks = ({}, {}) if snapshot else (m.bids.copy(), m.asks.copy())
            for name, levels in (("bids", bids), ("asks", asks)):
                for row in book[name]:
                    p, q = decimal(row["price"], True), decimal(row["size"])
                    if q < 0:
                        raise ValueError("Negative book size")
                    if q == 0:
                        levels.pop(p, None)
                    else:
                        levels[p] = q
            if not bids or not asks or max(bids) >= min(asks):
                m.nonce, m.book_at = None, 0
                raise ValueError("Invalid or crossed orderbook")
            m.bids, m.asks, m.nonce, m.book_at = bids, asks, nonce, ts
        elif channel.startswith("market_stats:"):
            row = data["market_stats"]
            # Single-market subscriptions can be either a stats object or keyed by id.
            row = row if "mark_price" in row else row[str(market_id)]
            mark, index = decimal(row["mark_price"], True), decimal(row["index_price"], True)
            rate = decimal(row["current_funding_rate"])/100
            last_rate = decimal(row["funding_rate"])/100
            m.mark, m.index, m.funding_rate, m.last_funding_rate = mark, index, rate, last_rate
            m.funding_timestamp = float(row.get("funding_timestamp", 0))/1000
            m.change, m.stats_at = float(decimal(row.get("daily_price_change", 0))), ts

    async def bootstrap(self, client):
        response = await client.get(REST + "/orderBookDetails", params={"filter":"perp"})
        response.raise_for_status()
        self.metadata(response.json())

    async def history(self, client):
        end = int(time.time())//300*300
        for m in self.markets.values():
            response = await client.get(REST + "/candles", params=dict(market_id=m.market_id,
                resolution="5m", start_timestamp=end-300*260, end_timestamp=end, count_back=260))
            response.raise_for_status()
            candles = []
            payload = response.json()
            for r in payload.get("c", payload.get("candles", [])):
                ts = int(r.get("t", r.get("timestamp", 0)))//1000
                values = [float(decimal(r[short] if short in r else r[long], True))
                          for short,long in (("o","open"),("h","high"),("l","low"),("c","close"))]
                if ts % 300 or ts+300 > time.time() or not values[2] <= min(values[0], values[3]) <= max(values[0], values[3]) <= values[1]:
                    continue
                candles.append(dict(ts=ts, o=values[0], h=values[1], l=values[2], c=values[3]))
            m.candles = sorted({r["ts"]:r for r in candles}.values(), key=lambda r:r["ts"])

    async def refresh(self):
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                try:
                    await self.bootstrap(client)
                except Exception:
                    for m in self.markets.values():
                        m.active = False
                    self.error = "Market specifications unavailable; trading paused"
                    await asyncio.sleep(30)
                    continue
                try:
                    await self.history(client)
                    if self.connected:
                        self.error = ""
                except Exception:
                    self.error = "Market history refresh unavailable; retrying"
                await asyncio.sleep(30)

    async def stream(self):
        attempt = 0
        while True:
            try:
                if len(self.markets) != 3:
                    await asyncio.sleep(1)
                    continue
                async with connect(WS, open_timeout=15, ping_interval=20, ping_timeout=20, max_size=4_000_000) as ws:
                    self.connected, self.error = True, ""
                    for m in self.markets.values():
                        m.nonce, m.book_at, m.stats_at = None, 0, 0
                        for channel in ("order_book", "market_stats"):
                            await ws.send(json.dumps(dict(type="subscribe", channel=f"{channel}/{m.market_id}")))
                    started = time.time()
                    while True:
                        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
                        if data.get("type") == "ping":
                            await ws.send(json.dumps({"type":"pong"}))
                        elif data.get("type") == "error":
                            raise ValueError("Subscription rejected")
                        else:
                            self.message(data)
                        if time.time()-started > 30:
                            attempt = 0
                        if time.time()-started > 20 and any(not m.fresh() for m in self.markets.values()):
                            raise ValueError("Stale market stream")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected, self.error = False, "Market stream interrupted; reconnecting"
                self.reconnects += 1
                for m in self.markets.values():
                    m.nonce, m.book_at, m.stats_at = None, 0, 0
                await asyncio.sleep(min(30, 2**min(attempt, 5)) + random.random())
                attempt += 1

"""Public market data only. No exchange account or order endpoints."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
import httpx
from .db import log, health, get, put

SYMBOLS = ("DOGE/USD", "BTC/USD", "ETH/USD", "SOL/USD", "XAU/USD")
BINANCE = {s: s.split("/")[0] + "USDT" for s in SYMBOLS if s != "XAU/USD"}
HERMES = "https://hermes.pyth.network"
BINANCE_URL = "https://api.binance.com"


def positive(value):
    v = D(str(value))
    if not v.is_finite() or v <= 0:
        raise ValueError("Invalid price")
    return v


def choose(primary, fallback, now, max_age=60):
    def usable(q):
        try:
            return q and -5 <= now - q["ts"] <= max_age and positive(q["value"]) > 0
        except (ValueError, ArithmeticError):
            return False
    a, b = primary if usable(primary) else None, fallback if usable(fallback) else None
    if a and b and abs(positive(a["value"]) - positive(b["value"])) / positive(a["value"]) > D(".01"):
        return dict(a, safe=False, reason="sources_diverge_over_1pct")
    if a or b:
        return dict(a or b, safe=True, reason="" if a else "primary_unavailable_using_fallback")
    return dict(value=None, ts=now, source="none", safe=False, reason="no_fresh_price")


class PriceFeed:
    def __init__(self, db, settings, client=None):
        self.db, self.settings = db, settings
        self.client = client or httpx.Client(timeout=8, follow_redirects=False)
        self.ids = get(db, "pyth_feed_ids", {})
        # Hermes requires authentication since the August 2026 Pyth upgrade.
        # Keep credentials scoped to Hermes; never attach them to Binance calls.
        key = os.getenv("VELA_PYTH_API_KEY")
        self.pyth_headers = {"Authorization": "Bearer " + key} if key else {}

    def pyth(self):
        if len(self.ids) != len(SYMBOLS):
            for symbol in SYMBOLS:
                if symbol in self.ids:
                    continue
                response = self.client.get(HERMES + "/v2/price_feeds",
                                           params={"query": symbol}, headers=self.pyth_headers)
                response.raise_for_status()
                prefix = "Metal." if symbol == "XAU/USD" else "Crypto."
                matches = [r for r in response.json() if r.get("attributes", {}).get("symbol") == prefix + symbol]
                if len(matches) != 1:
                    raise ValueError("Exact Pyth symbol not found: " + symbol)
                self.ids[symbol] = matches[0]["id"]
        response = self.client.get(HERMES + "/v2/updates/price/latest",
                                   params=[("ids[]", x) for x in self.ids.values()], headers=self.pyth_headers)
        response.raise_for_status()
        inverse = {v.removeprefix("0x"): k for k, v in self.ids.items()}
        result = {}
        for row in response.json()["parsed"]:
            symbol = inverse.get(row["id"].removeprefix("0x"))
            if not symbol:
                continue
            p = row["price"]
            value = positive(p["price"]) * D(10) ** int(p["expo"])
            if D(p["conf"]) / positive(p["price"]) > D(".01"):
                continue
            result[symbol] = dict(value=str(value), ts=float(p["publish_time"]), source="Pyth Hermes")
        return result

    def binance(self):
        response = self.client.get(BINANCE_URL + "/api/v3/ticker/bookTicker")
        response.raise_for_status()
        result = {}
        inverse = {v: k for k, v in BINANCE.items()}
        now = time.time()
        for row in response.json():
            symbol = inverse.get(row["symbol"])
            if symbol:
                bid, ask = positive(row["bidPrice"]), positive(row["askPrice"])
                if ask < bid or (ask - bid) / bid > D(".01"):
                    continue
                result[symbol] = dict(value=str((bid + ask) / 2), ts=now, source="Binance USDT mid (USD proxy)")
        return result

    def poll(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(self.pyth), pool.submit(self.binance)]
            values = []
            for task in tasks:
                try:
                    values.append(task.result())
                except Exception as exc:
                    values.append({})
                    log(self.db, "prices", "provider_unavailable", "warning", error=type(exc).__name__)
        now = time.time()
        if self.ids:
            put(self.db, "pyth_feed_ids", self.ids)
        for symbol in SYMBOLS:
            chosen = choose(values[0].get(symbol), values[1].get(symbol), now, self.settings.max_price_age)
            old = self.db.execute("SELECT safe,reason FROM prices WHERE symbol=?", (symbol,)).fetchone()
            if not old or (bool(old["safe"]), old["reason"]) != (chosen["safe"], chosen["reason"]):
                log(self.db, "prices", "price_state", symbol=symbol, safe=chosen["safe"], reason=chosen["reason"])
            self.db.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)",
                            (symbol, chosen["value"], chosen["ts"], chosen["source"], int(chosen["safe"]), chosen["reason"]))
            if chosen["safe"]:
                self.candle(symbol, float(chosen["value"]), int(now // 300) * 300)
        health(self.db, "prices", any(v for v in values), "Public quotes polled; per-market gates in /api/prices")

    def candle(self, symbol, value, ts):
        self.db.execute("""INSERT INTO candles VALUES (?,?,?,?,?,?)
            ON CONFLICT(symbol,ts) DO UPDATE SET h=max(h,excluded.h),l=min(l,excluded.l),c=excluded.c""",
                        (symbol, ts, value, value, value, value))

    def bootstrap_candles(self):
        """Only completed public candles; no fabricated warmup or gap filling."""
        for symbol, market in BINANCE.items():
            if self.db.execute("SELECT count(*) FROM candles WHERE symbol=?", (symbol,)).fetchone()[0] >= 250:
                continue
            try:
                r = self.client.get(BINANCE_URL + "/api/v3/klines",
                                    params={"symbol": market, "interval": "5m", "limit": 300})
                r.raise_for_status()
                for row in r.json():
                    ts = int(row[0]) // 1000
                    vals = [float(positive(x)) for x in row[1:5]]
                    if ts + 300 <= time.time():
                        self.db.execute("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?)", (symbol, ts, *vals))
            except Exception as exc:
                log(self.db, "prices", "warmup_unavailable", "warning", symbol=symbol, error=type(exc).__name__)


def quote(db, symbol, max_age=60):
    r = db.execute("SELECT * FROM prices WHERE symbol=?", (symbol,)).fetchone()
    if not r or not r["safe"] or not -5 <= time.time() - r["ts"] <= max_age:
        raise ValueError("Fresh trustworthy price unavailable: " + symbol)
    return positive(r["value"])


def entries_allowed(db, symbol, max_age=60):
    # A detected discrepancy anywhere pauses new entries; no guess about the bad source.
    if db.execute("SELECT 1 FROM prices WHERE reason='sources_diverge_over_1pct'").fetchone():
        return False
    try:
        quote(db, symbol, max_age)
        quote(db, "DOGE/USD", max_age)
        return True
    except ValueError:
        return False

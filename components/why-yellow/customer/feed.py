"""Yellow public feed for execution (marks, 5m freshness and 1h entry confirmation).

Same public websocket channels the paper book uses (public.mark_price.<m>, public.kline.<m>.5m),
re-implemented here so yellow_paper.py stays untouched. Feeds the shared FreshnessMonitor
(bar continuity seeded from public REST klines on every (re)connect) and every tenant engine's
software-stop evaluation. Completed 5m/1h candles also feed the account-scoped
live entry filter. Public data only: no credentials, no orders."""
from __future__ import annotations
import json
import math
import queue
import threading
import time
from typing import Callable, List, Optional

from .log import get_logger, safe_exc

log = get_logger("feed")
WS_URL = "wss://trade.api.yellow.pro/ws"


class YellowMarketFeed(threading.Thread):
    def __init__(self, markets: List[str], freshness, registry, client_factory: Optional[Callable] = None,
                 ws_url: str = WS_URL, clock: Callable[[], float] = time.time):
        super().__init__(name="yellow-feed", daemon=True)
        self.markets, self.fresh, self.registry, self.ws_url, self.clock = list(markets), freshness, registry, ws_url, clock
        self.client_factory = client_factory
        self.connected = False
        self.last_msg = 0.0
        self.curbar = {m: None for m in self.markets}
        self.candle_bars = {}
        self.candle_lock = threading.RLock()
        self.epoch = 0
        self.stop_event = threading.Event()
        self.seeded = {m: False for m in self.markets}
        self.disconnect_count = 0
        self.last_disconnect_at = None
        self.last_error = None
        self.last_processed_at = 0.0
        self.processing_lag_s = 0.0
        self.max_processing_lag_s = 0.0
        self.subscriptions = set()
        self._inbox = None
        self.heartbeat_timeout_s = 330.0

    def _seed(self) -> None:
        try:
            from yellow_exec import YellowClient
            c = self.client_factory() if self.client_factory else YellowClient()
            for m in self.markets:
                try:
                    k = c.klines(m, "5m", 300)
                    callback = getattr(self.registry, "seed_candles", None)
                    if callback:
                        callback(m, "5m", k)
                        self._prime_candle(m,"5m",k)
                        try:
                            hourly = c.klines(m, "1h", 300)
                            callback(m, "1h", hourly)
                            self._prime_candle(m,"1h",hourly)
                        except Exception as exc:
                            callback(m, "1h", [])
                            log.warning("hourly seed %s failed: %s", m, safe_exc(exc))
                    opens = sorted(set(int(r[0]) for r in k[:-1]))  # drop the forming bar
                    # Yellow may return more history than requested. An old gap
                    # must not invalidate a continuous run of recent bars.
                    recent = opens[-1:] if opens else []
                    for opened in reversed(opens[:-1]):
                        if recent[0] - opened != self.fresh.interval_ms:
                            break
                        recent.insert(0, opened)
                    age_s = self.clock() - recent[-1] / 1000 if recent else float("inf")
                    self.seeded[m] = (len(recent) >= 3 and 0 <= age_s <= self.fresh.max_bar_age_s
                                      and self.fresh.seed(m, recent))
                except Exception as e:
                    log.warning("seed %s failed: %s", m, safe_exc(e))
        except Exception as e:
            log.warning("seed failed: %s", safe_exc(e))

    def _prime_candle(self, market, interval, rows):
        """Keep the REST forming bar so its first websocket rollover isn't lost."""
        step = 300_000 if interval == "5m" else 3_600_000
        opened = int(self.clock()*1000)//step*step
        forming = next((list(r) for r in reversed(rows) if int(r[0]) == opened and len(r)>=5),None)
        if forming:
            with self.candle_lock:
                existing = self.candle_bars.get((market,interval))
                if existing is None or int(existing[0]) < opened:
                    self.candle_bars[(market,interval)] = forming
                    if interval == "5m":self.curbar[market] = opened

    def _refresh_once(self):
        """Backfill broken histories from public REST without blocking the WS reader."""
        needed = getattr(self.registry,"candle_history_ready",None)
        seed = getattr(self.registry,"seed_candles",None)
        if not self.connected or not needed or not seed:
            return
        from yellow_exec import YellowClient
        client = self.client_factory() if self.client_factory else YellowClient()
        epoch = self.epoch
        for market in self.markets:
            for interval in ("5m","1h"):
                if not self.connected or self.epoch != epoch or self.stop_event.is_set():return
                if needed(market,interval):continue
                try:
                    rows = client.klines(market,interval,300)
                    with self.candle_lock:
                        if not self.connected or self.epoch != epoch:return
                        # WS may have supplied the missing bar while REST was in flight.
                        if needed(market,interval):continue
                        valid = seed(market,interval,rows)
                        self._prime_candle(market,interval,rows)
                        if interval == "5m" and valid:
                            now_ms = self.clock()*1000
                            opens = sorted(set(int(r[0]) for r in rows if int(r[0])+300_000<=now_ms))[-250:]
                            self.seeded[market] = self.fresh.seed(market,opens)
                        log.info("candle history refresh %s %s ready=%s",market,interval,valid)
                except Exception as exc:
                    log.warning("candle refresh %s %s failed: %s",market,interval,safe_exc(exc))

    def _refresh_loop(self):
        while not self.stop_event.wait(30):
            try:self._refresh_once()
            except Exception as exc:log.warning("candle refresh failed: %s",safe_exc(exc))

    def _receive(self, ws, inbox, ended, errors, subscriptions):
        """Drain the socket and answer heartbeats even while an engine is busy.

        Preserve publication ordering. Never silently drop market observations:
        backlog overflow fails the connection closed and requires a fresh seed.
        """
        import websocket
        try:
            while not self.stop_event.is_set() and not ended.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    # Server ping interval is negotiated (300s on Yellow).
                    # A receive timeout is not a server heartbeat request.
                    if self.last_msg and self.clock() - self.last_msg > self.heartbeat_timeout_s:
                        raise RuntimeError("feed_heartbeat_timeout")
                    continue
                if ended.is_set():
                    break
                if not raw:
                    raise RuntimeError("feed_socket_closed")
                received_at = self.clock()
                self.last_msg = received_at
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg == {}:
                        ws.send("{}")
                        continue
                    if msg.get("error"):
                        raise RuntimeError("feed_subscription_or_protocol_error:" + str(msg['error'].get('code')))
                    if 'subscribe' in msg and msg.get('id') in subscriptions:
                        self.subscriptions.add(subscriptions[msg['id']])
                    if msg.get('push', {}).get('disconnect'):
                        raise RuntimeError("feed_server_disconnect")
                    if 'push' in msg:
                        inbox.put_nowait((msg, received_at))
        except Exception as exc:
            errors.append(exc)
        finally:
            ended.set()

    def run(self) -> None:
        import websocket
        threading.Thread(target=self._refresh_loop, name="yellow-candle-refresh", daemon=True).start()
        backoff = 1
        while not self.stop_event.is_set():
            ws = None
            reader = None
            ended = threading.Event()
            errors = []
            try:
                with self.candle_lock:
                    self.epoch += 1
                    self.curbar = {m: None for m in self.markets}
                    self.candle_bars.clear()
                self.subscriptions = set()
                # REST seeding must not occupy a connected socket's heartbeat loop.
                self._seed()
                ws = websocket.create_connection(self.ws_url, timeout=30)
                ws.send(json.dumps({"id": 1, "connect": {}}))
                hello = json.loads(ws.recv())
                if hello.get('error') or 'connect' not in hello:
                    raise RuntimeError("feed_connect_rejected")
                self.heartbeat_timeout_s = max(30.0, float(hello["connect"].get("ping", 300)) + 30.0)
                subscriptions = {}
                i = 2
                for m in self.markets:
                    for channel in (f"public.mark_price.{m}", f"public.kline.{m}.5m", f"public.kline.{m}.1h"):
                        subscriptions[i] = channel
                        ws.send(json.dumps({"id": i, "subscribe": {"channel": channel}}))
                        i += 1
                ws.settimeout(10)
                self.last_msg = self.clock()
                inbox = queue.Queue(maxsize=10000)
                self._inbox = inbox
                reader = threading.Thread(target=self._receive, args=(ws, inbox, ended, errors, subscriptions),
                                          name="yellow-socket-reader", daemon=True)
                self.connected = True
                subscribed_at = self.clock()
                reader.start()
                backoff = 1
                log.info("yellow feed connected (%d channels)", len(subscriptions))
                while not self.stop_event.is_set():
                    if len(self.subscriptions) < len(subscriptions) and self.clock() - subscribed_at > 30:
                        raise RuntimeError("feed_subscription_timeout")
                    if ended.is_set():
                        raise errors[0] if errors else RuntimeError("feed_reader_stopped")
                    try:
                        msg, received_at = inbox.get(timeout=.5)
                    except queue.Empty:
                        continue
                    lag = max(0.0, self.clock() - received_at)
                    self.processing_lag_s = lag
                    self.max_processing_lag_s = max(self.max_processing_lag_s, lag)
                    if lag > self.fresh.max_age_s:
                        raise RuntimeError("feed_processing_backlog")
                    self._handle(msg, received_at)
                    self.last_processed_at = self.clock()
            except Exception as exc:
                self.last_error = safe_exc(exc)
                self.last_disconnect_at = self.clock()
                self.disconnect_count += 1
                log.warning("feed error: %s", self.last_error)
            finally:
                self.connected = False
                ended.set()
                with self.candle_lock:
                    self.epoch += 1
                if ws is not None:
                    try: ws.close(timeout=1)
                    except Exception: pass
                if reader is not None:
                    reader.join(timeout=2)
                self._inbox = None
                try:
                    self.fresh.on_disconnect()
                    self.registry.on_disconnect("feed_disconnect")
                except Exception as exc:
                    log.warning("disconnect propagation failed: %s", safe_exc(exc))
            self.stop_event.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _handle(self, msg: dict, received_at=None) -> None:
        push = msg.get("push")
        if not push:
            return
        ch = push.get("channel", "")
        data = (push.get("pub") or {}).get("data") or {}
        now = self.clock() if received_at is None else received_at
        if ch.startswith("public.mark_price."):
            m = ch.rsplit(".", 1)[-1]
            try:
                p = float(data["mark_price"])
            except Exception:
                return
            if m in self.curbar and math.isfinite(p) and p > 0:
                self.fresh.on_mark(m, p, now)
                self.registry.on_mark(m, p, now)
        elif ch.startswith("public.kline."):
            parts = ch.split(".")
            m = parts[2] if len(parts) > 2 else ""
            k = data.get("kline")
            if not k or m not in self.curbar:
                return
            interval = parts[3] if len(parts) > 3 else ""
            if interval not in ("5m", "1h"):
                return
            open_ms = int(k[0])
            with self.candle_lock:
                key = (m, interval)
                previous = self.candle_bars.get(key)
                if previous is not None and open_ms > int(previous[0]):
                    callback = getattr(self.registry, "on_candle", None)
                    if callback:
                        callback(m, interval, previous)
                    if interval == "5m":
                        self.fresh.on_bar_close(m, int(previous[0]), now)
                if previous is None or open_ms >= int(previous[0]):
                    self.candle_bars[key] = list(k)
                    if interval == "5m":
                        self.curbar[m] = open_ms

    def status(self) -> dict:
        now = self.clock()
        age = round(now - self.last_msg, 1) if self.last_msg else None
        processed_age = round(now - self.last_processed_at, 1) if self.last_processed_at else None
        market_ready = all(self.fresh.entry_allowed(m, now)[0] for m in self.markets)
        ready = bool(self.connected and age is not None and 0 <= age <= 30
                     and processed_age is not None and 0 <= processed_age <= 30
                     and len(self.subscriptions) == len(self.markets) * 3 and market_ready)
        return dict(connected=self.connected, ready=ready, last_msg_age=age,
                    last_processed_age=processed_age, processing_lag_s=round(self.processing_lag_s, 3),
                    max_processing_lag_s=round(self.max_processing_lag_s, 3),
                    queued_messages=self._inbox.qsize() if self._inbox is not None else 0,
                    subscriptions=len(self.subscriptions), expected_subscriptions=len(self.markets)*3,
                    disconnect_count=self.disconnect_count, last_disconnect_at=self.last_disconnect_at,
                    last_error=self.last_error,
                    seeded=sum(1 for v in self.seeded.values() if v), markets=len(self.markets))

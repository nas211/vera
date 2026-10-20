"""yellow_exec.YellowClient against a fake requests.Session. Never armed via the
environment: armed() is monkeypatched on the instance; TRADE_BASE points at an
.invalid host and the harness audit hook denies sockets anyway."""
import json, os, sys, unittest
from decimal import Decimal
from .harness import ROOT, NETWORK_ATTEMPTS
sys.path.insert(0, ROOT)
import yellow_exec
from execution.transport import TransportAmbiguous


class TransportError(Exception):
    pass


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, ({} if body is None else body)
        self.content = b"{}"; self.text = json.dumps(self.body)
    def json(self): return self.body


class FakeSession:
    def __init__(self, outcomes=None):
        self.calls, self.outcomes = [], list(outcomes or [])
    def request(self, method, url, **kw):
        body = json.loads(kw.get("data", "{}")) if "data" in kw else kw.get("params", {})
        self.calls.append(dict(method=method, path=url.split(".invalid")[-1], body=body))
        out = self.outcomes.pop(0) if self.outcomes else Resp(body={"order_uuid": f"mock-{len(self.calls)}"})
        if isinstance(out, BaseException): raise out
        return out
    def get(self, url, **kw): return self.request("GET", url, **kw)
    def delete(self, url, **kw): return self.request("DELETE", url, **kw)


FILTERS = dict(tick=Decimal(".1"), step=Decimal(".001"), min_qty=Decimal(".001"), min_notional=Decimal("10"), max_lev=Decimal("20"),
               status="TRADING", maker=Decimal("0"), taker=Decimal(".00015"), px_max_ratio=Decimal("2"), px_min_ratio=Decimal(".5"))


def client(outcomes=None, entries=True, filters=None):
    yellow_exec.TRADE_BASE = "https://exchange.invalid"
    yellow_exec.requests.RequestException = TransportError
    c = yellow_exec.YellowClient("synthetic-key", "synthetic-secret", "acct-A")
    c.s = FakeSession(outcomes)
    c.filters = lambda _m: dict(filters or FILTERS)
    c.top_of_book = lambda _m: (76999.9, 77000.1)
    c.armed = lambda: True                      # instance-only; os.environ untouched
    c.entries_allowed = lambda: entries
    return c


class TestAdapter(unittest.TestCase):
    def test_environment_never_armed(self):
        self.assertNotIn("YELLOW_ARMED", os.environ)
        self.assertFalse(yellow_exec.YellowClient("k", "s", "a").armed())

    def test_order_post_never_retried_transport_error(self):
        for err in ["timeout", "reset", "dns", "tls"]:
            c = client([TransportError(err), Resp(body={"order_uuid": "second"})])
            with self.assertRaises(TransportAmbiguous):
                c.market_entry("BTCUSDT-PERP", "long", "0.01", 10, coid="why-test")
            self.assertEqual(len(c.s.calls), 1, err)

    def test_order_post_never_retried_5xx(self):
        for st in (500, 502, 503, 504):
            c = client([Resp(st, {"error": "x"})] * 3)
            with self.assertRaises(TransportAmbiguous):
                c.market_entry("BTCUSDT-PERP", "long", "0.01", 10, coid="why-test")
            self.assertEqual(len(c.s.calls), 1, st)
        c = client([Resp(429, {"error": "rate"})] * 3)
        with self.assertRaises(yellow_exec.YellowError):
            c.market_entry("BTCUSDT-PERP", "long", "0.01", 10, coid="why-test")
        self.assertEqual(len(c.s.calls), 1)

    def test_reads_still_retry(self):
        c = client([TransportError("t"), Resp(body={"orders": [], "has_more": False})])
        yellow_exec.time.sleep = lambda _: None
        self.assertEqual(c.open_orders("BTCUSDT-PERP"), [])
        self.assertEqual(len(c.s.calls), 2)

    def test_client_order_id_mandatory_and_sent(self):
        c = client()
        with self.assertRaises(ValueError):
            c.place("BTCUSDT-PERP", True, "long", "0.01", typ="market", tif="ioc", leverage=10)
        c.market_entry("BTCUSDT-PERP", "long", "0.01", 10, coid="why-abc")
        self.assertEqual(c.s.calls[0]["body"]["client_order_id"], "why-abc")

    def test_local_validation_blocks_transport(self):
        cases = [dict(qty="Infinity"), dict(qty="NaN"), dict(qty="-1"), dict(qty="0"), dict(qty="0.0015"), dict(qty="0.0001"), dict(lev=10000)]
        for case in cases:
            c = client()
            with self.assertRaises(Exception) as cm:
                c.market_entry("BTCUSDT-PERP", "long", case.get("qty", "0.01"), case.get("lev", 10), coid="why-x")
            self.assertEqual(len(c.s.calls), 0, case)
        c = client(filters=dict(FILTERS, status="HALTED"))
        with self.assertRaises(Exception):
            c.market_entry("BTCUSDT-PERP", "long", "0.01", 10, coid="why-x")
        self.assertEqual(len(c.s.calls), 0)
        c = client()
        with self.assertRaises(Exception):
            c.place("BTCUSDT-PERP", True, "sideways", "0.01", typ="market", tif="ioc", coid="why-x")
        self.assertEqual(len(c.s.calls), 0)

    def test_entries_switch_blocks_only_entries(self):
        c = client(entries=False)
        with self.assertRaises(yellow_exec.NotArmed):
            c.market_entry("BTCUSDT-PERP", "long", "0.01", 10, coid="why-x")
        self.assertEqual(len(c.s.calls), 0)
        c.close_market("BTCUSDT-PERP", "long", "0.01", 10, coid="why-close")
        self.assertEqual(c.s.calls[-1]["body"]["reduce_only"], True)
        c.target_exit("BTCUSDT-PERP", "long", "0.01", "77500", 10, coid="why-tp")
        self.assertEqual(c.s.calls[-1]["body"]["reduce_only"], True)
        self.assertEqual(len(c.s.calls), 2)

    def test_unarmed_blocks_everything(self):
        c = client(); c.armed = lambda: False
        for fn in (lambda: c.market_entry("BTCUSDT-PERP", "long", "0.01", 10, coid="a"), lambda: c.close_market("BTCUSDT-PERP", "long", "0.01", 10, coid="b"),
                   lambda: c.cancel_verified("BTCUSDT-PERP", "x"), lambda: c.flatten()):
            with self.assertRaises(yellow_exec.NotArmed):
                fn()
        self.assertEqual(len(c.s.calls), 0)

    def test_open_orders_walks_every_page(self):
        c = client([Resp(body={"orders": [{"order_id": "a"}], "has_more": True, "next_cursor": "c1"}),
                    Resp(body={"orders": [{"order_id": "b"}], "has_more": True, "next_cursor": "c2"}),
                    Resp(body={"orders": [{"order_id": "still-open"}], "has_more": False, "next_cursor": ""})])
        self.assertEqual([o["order_id"] for o in c.open_orders("BTCUSDT-PERP")], ["a", "b", "still-open"])
        self.assertEqual(c.s.calls[0]["body"]["use_cursor"], "true"); self.assertEqual(c.s.calls[1]["body"]["cursor"], "c1")

    def test_cancel_verified_results(self):
        yellow_exec.time.sleep = lambda _: None
        # still open on a later page -> not cancelled
        c = client([Resp(body={}), Resp(body={"orders": [], "has_more": True, "next_cursor": "n"}), Resp(body={"orders": [{"order_id": "o1"}], "has_more": False})] * 4)
        self.assertEqual(c.cancel_verified("BTCUSDT-PERP", "o1"), "STILL_OPEN")
        # gone from open orders, history says filled
        c = client([Resp(body={}), Resp(body={"orders": [], "has_more": False}), Resp(body={"orders": [{"order_uuid": "o1", "state": "filled"}], "has_more": False})])
        self.assertEqual(c.cancel_verified("BTCUSDT-PERP", "o1"), "FILLED")
        # gone, history says canceled
        c = client([Resp(body={}), Resp(body={"orders": [], "has_more": False}), Resp(body={"orders": [{"order_id": "o1", "state": "canceled"}], "has_more": False})])
        self.assertEqual(c.cancel_verified("BTCUSDT-PERP", "o1"), "CANCELLED")
        # gone from both lists: never assumed cancelled
        c = client([Resp(body={}), Resp(body={"orders": [], "has_more": False}), Resp(body={"orders": [], "has_more": False})])
        self.assertEqual(c.cancel_verified("BTCUSDT-PERP", "o1"), "UNKNOWN")
        # lookup fails -> UNKNOWN, not True
        c = client([Resp(body={}), Resp(503, {"error": "down"})] * 3)
        self.assertEqual(c.cancel_verified("BTCUSDT-PERP", "o1"), "UNKNOWN")

    def test_fills_deduplicated_and_paginated(self):
        rows = [{"id": "t1", "order_uuid": "o", "amount": "0.2", "price": "100", "fee": "0.01", "fee_currency": "USDT"},
                {"id": "t2", "order_uuid": "o", "amount": "0.3", "price": "110", "fee": "0.02", "fee_currency": "USDT"}]
        c = client([Resp(body={"trades": [rows[0], rows[0]], "has_more": True, "next_cursor": "x"}),
                    Resp(body={"trades": [rows[1], rows[0]], "has_more": False})])
        q, avg, fees = c.fills_for("o", "BTCUSDT-PERP")
        self.assertEqual(q, Decimal("0.5")); self.assertEqual(avg, Decimal("106")); self.assertEqual(fees, {"USDT": Decimal("0.03")})

    def test_flatten_single_attempt(self):
        c = client([TransportError("t"), Resp(body={})])
        with self.assertRaises(TransportAmbiguous):
            c.flatten()
        self.assertEqual(len(c.s.calls), 1)

    def test_transport_wrapper_maps_states(self):
        from execution.yellow_transport import normalise_order, normalise_trade
        o = normalise_order({"order_id": "x", "client_order_id": "why-1", "market": "BTCUSDT-PERP", "side": "buy", "direction": "long", "type": "market",
                             "amount": "0.01", "filled_amount": "0.01", "state": "filled", "reduce_only": False})
        self.assertEqual((o.state, o.filled_qty), ("filled", Decimal("0.01")))
        self.assertEqual(normalise_order({"order_id": "y", "state": "wait", "amount": "1"}).state, "open")
        self.assertEqual(normalise_order({"order_id": "z", "state": "canceled", "amount": "1"}).state, "cancelled")
        t = normalise_trade({"id": "t1", "order_uuid": "x", "market": "BTCUSDT-PERP", "amount": "0.01", "price": "77000", "fee": "0.1"}, "acct")
        self.assertEqual((t.trade_id, t.qty), ("t1", Decimal("0.01")))

import unittest
from decimal import Decimal
from .harness import Env, BTC, HALT, make_metadata, ACCT
from execution.metadata import MarketFilters
from execution.sizing import ExposureLimits, SizingError, size_order
from execution.types import OrderRequest, VenueAccount
from execution.validation import ValidationError, validate_order


def req(**kw):
    base = dict(account_id=ACCT, market="BTCUSDT-PERP", side="buy", direction="long", order_type="market", qty=Decimal("0.01"),
                leverage=10, client_order_id="why-x", price=None, reduce_only=False, time_in_force="ioc")
    base.update(kw)
    return OrderRequest(**base)


class TestValidation(unittest.TestCase):
    def check(self, code, **kw):
        filters = kw.pop("filters", BTC)
        with self.assertRaises(ValidationError) as cm:
            validate_order(req(**kw), filters, ref_price=Decimal("77000"))
        self.assertEqual(cm.exception.code, code)

    def test_8_nan_infinity_rejected(self):
        self.check("non_finite_qty", qty=Decimal("NaN"))
        self.check("non_finite_qty", qty=Decimal("Infinity"))
        self.check("non_finite_qty", qty=Decimal("-Infinity"))
        self.check("non_finite_qty", qty=float("inf"))
        self.check("non_finite_qty", qty=float("nan"))
        self.check("non_finite_qty", qty=float("1e999"))          # float overflow -> inf
    def test_zero_negative(self):
        self.check("non_positive_qty", qty=Decimal("0"))
        self.check("non_positive_qty", qty=Decimal("-1"))
    def test_9_off_step(self):
        self.check("qty_off_step", qty=Decimal("0.0015"))
        self.check("qty_off_step", qty=Decimal("0.00100001"))
    def test_10_halted_market(self):
        self.check("market_not_trading", market="HALTUSDT-PERP", filters=HALT)
    def test_min_qty_min_notional(self):
        self.check("below_min_qty", qty=Decimal("0.0005"), filters=MarketFilters("BTCUSDT-PERP", Decimal("0.1"), Decimal("0.0005"), Decimal("0.001"), Decimal("10"), Decimal("20"), "TRADING"))
        self.check("below_min_notional", qty=Decimal("0.001"), filters=MarketFilters("BTCUSDT-PERP", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("100"), Decimal("20"), "TRADING"))
    def test_price_tick_and_limit_rules(self):
        self.check("price_off_tick", order_type="limit", price=Decimal("77000.05"), time_in_force="gtc", side="sell", reduce_only=True)
        self.check("limit_price_required", order_type="limit", time_in_force="gtc", side="sell", reduce_only=True)
        self.check("price_not_allowed_for_market_order", price=Decimal("77000"))
    def test_leverage(self):
        self.check("leverage_out_of_range", leverage=10000)
        self.check("leverage_out_of_range", leverage=0)
        self.check("invalid_leverage", leverage="ten")
        with self.assertRaises(ValidationError) as cm:
            validate_order(req(leverage=20), BTC, ref_price=Decimal("77000"), max_leverage_policy=10)
        self.assertEqual(cm.exception.code, "leverage_above_policy")
    def test_side_direction_type_symbol(self):
        self.check("invalid_side", side="long")
        self.check("invalid_direction", direction="sideways")
        self.check("invalid_order_type", order_type="stop")
        self.check("symbol_mismatch", market="ETHUSDT-PERP")
        self.check("side_direction_inconsistent", side="sell")                  # long entry must buy
        self.check("side_direction_inconsistent", side="buy", reduce_only=True)  # long close must sell
        self.check("missing_client_order_id", client_order_id="")
        self.check("invalid_time_in_force", time_in_force="day")
    def test_valid_passes_and_unknown_market(self):
        validate_order(req(), BTC, ref_price=Decimal("77000"))
        from execution.validation import validate_market_known
        with self.assertRaises(ValidationError) as cm:
            validate_market_known(make_metadata(), "DOGEUSDT-PERP")
        self.assertEqual(cm.exception.code, "unknown_market")
    def test_engine_rejects_bad_signals_before_transport(self):
        e = Env(); e.ready()
        for sig, expect in [(e.signal("s1", market="HALTUSDT-PERP"), "validation:market_not_trading"),
                            (e.signal("s2", market="NOPE-PERP"), "unknown_market"),
                            (e.signal("s3", notional="NaN"), "sizing:invalid_notional"),
                            (e.signal("s4", notional="Infinity"), "sizing:invalid_notional"),
                            (e.signal("s5", notional="-5"), "sizing:invalid_notional"),
                            (e.signal("s6", direction="sideways"), "invalid_direction"),
                            (e.signal("s7", strategy="WY-MEME"), "unknown_strategy:unknown strategy sleeve 'WY-MEME'; known: ['WY-PULLBACK', 'WY-REVERT']"),
                            (e.signal("s8", notional="1"), "sizing:below_min_qty_after_rounding")]:
            r = e.eng.submit_entry(sig)
            self.assertEqual(r.status, "REJECTED", sig.signal_id)
            self.assertEqual(r.reason, expect, sig.signal_id)
        self.assertEqual(e.mock.place_calls, 0)
        self.assertEqual(e.ledger.counts()["orders"], 0)   # nothing even reached the ledger


class TestSizing(unittest.TestCase):
    L = ExposureLimits(Decimal("2000"), Decimal("16000"), 8)
    A = VenueAccount(Decimal("1000"), Decimal("1000"))

    def test_terms_distinguished(self):
        s = size_order(Decimal("2000"), Decimal("77000"), 10, BTC, self.A, self.L)
        self.assertEqual(s.qty, Decimal("0.025"))
        self.assertEqual(s.notional, Decimal("0.025") * Decimal("77000"))    # 1925 <= 2000 requested (rounded down)
        self.assertEqual(s.required_margin, (s.notional / 10).quantize(Decimal("0.00000001")))
        self.assertEqual(s.leverage, 10)
        self.assertLessEqual(s.notional, Decimal("2000"))

    def test_rounding_never_increases_exposure(self):
        for notional in ("20", "50", "100", "250", "999.99", "2000"):
            for px in ("77400.1234567", "0.0845", "2490.09"):
                f = MarketFilters("X", Decimal("0.0001"), Decimal("0.001"), Decimal("0.001"), Decimal("0.0001"), Decimal("20"), "TRADING")
                try:
                    s = size_order(Decimal(notional), Decimal(px), 10, f, VenueAccount(Decimal("1e9"), Decimal("1e9")), ExposureLimits(Decimal("1e9"), Decimal("1e9"), 100))
                except SizingError as e:
                    self.assertIn(e.code, ("below_min_qty_after_rounding",)); continue
                self.assertLessEqual(s.notional, Decimal(notional))

    def test_available_balance_and_caps(self):
        with self.assertRaises(SizingError) as cm:
            size_order(Decimal("2000"), Decimal("77000"), 10, BTC, VenueAccount(Decimal("100"), Decimal("100")), self.L)
        self.assertEqual(cm.exception.code, "insufficient_available_balance")
        with self.assertRaises(SizingError) as cm:
            size_order(Decimal("5000"), Decimal("77000"), 10, BTC, self.A, self.L)
        self.assertEqual(cm.exception.code, "above_max_notional_per_order")
        with self.assertRaises(SizingError) as cm:
            size_order(Decimal("2000"), Decimal("77000"), 10, BTC, self.A, self.L, open_notional=Decimal("15000"))
        self.assertEqual(cm.exception.code, "above_max_total_notional")
        with self.assertRaises(SizingError) as cm:
            size_order(Decimal("2000"), Decimal("77000"), 10, BTC, self.A, self.L, open_positions=8)
        self.assertEqual(cm.exception.code, "max_open_positions")
        with self.assertRaises(SizingError) as cm:
            size_order(Decimal("2000"), Decimal("77000"), 25, BTC, self.A, self.L)
        self.assertEqual(cm.exception.code, "leverage_above_market_max")
        for bad in (Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-1")):
            with self.assertRaises(SizingError):
                size_order(bad, Decimal("77000"), 10, BTC, self.A, self.L)
        with self.assertRaises(SizingError) as cm:
            size_order(Decimal("2000"), Decimal("77000"), 10, BTC, VenueAccount(Decimal("NaN"), Decimal("0")), self.L)
        self.assertEqual(cm.exception.code, "no_available_balance")

    def test_engine_uses_live_balance(self):
        e = Env(); e.ready()
        e.mock.set_account(ACCT, "50", "50")
        r = e.eng.submit_entry(e.signal("sig-1"))
        self.assertEqual(r.status, "REJECTED"); self.assertEqual(r.reason, "sizing:insufficient_available_balance")
        e.mock.set_account(ACCT, "500", "500")
        self.assertEqual(e.eng.submit_entry(e.signal("sig-1")).status, "ACKNOWLEDGED")
        o = e.order(e.eng.status()["open_positions"][0] if e.eng.status()["open_positions"] else e.ledger.orders_where([__import__('execution.types', fromlist=['OrderState']).OrderState.ACKNOWLEDGED])[0]["client_order_id"])
        self.assertEqual(o["leverage"], 10)

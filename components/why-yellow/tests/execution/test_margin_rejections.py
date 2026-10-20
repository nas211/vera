"""Replay exchange rejection evidence without any network or order submission."""
import unittest
from decimal import Decimal as D
from execution.sizing import margin_percentage_notional, size_order, ExposureLimits
from execution.metadata import MarketFilters
from execution.types import VenueAccount

class RecordedMarginRejectionTests(unittest.TestCase):
    def test_recorded_rejections_fit_budget_after_quantity_rounding(self):
        balance = D('55.005282')
        # market, signal price, submitted quantity, step, venue-required margin
        cases = [
            ('LINKUSDT-PERP','13.9397874','38.95','.01','57.051577'),
            ('HYPEUSDT-PERP','93.0856134','5.83','.01','57.05826'),
            ('TAOUSDT-PERP','304.899008','1.780','.001','57.097494'),
        ]
        for market,px,old_qty,step,required in cases:
            for pct in (99,100):
                with self.subTest(market=market,pct=pct):
                    budget=balance*D(pct)/100
                    # Old calculation demonstrates the production failure.
                    old_notional=budget/(D('.1')+D('.0003'))/D('1.01')
                    old_q=(old_notional/D(px)//D(step))*D(step)
                    self.assertGreater(D(required)*old_q/D(old_qty),balance)
                    n=margin_percentage_notional(balance,pct,10,D('.0004'),D(20),confirmed_taker_fee=D('.00015'))
                    f=MarketFilters(market,D('.001'),D(step),D(step),D(20),D(100),'TRADING')
                    sized=size_order(n,D(px),10,f,VenueAccount(balance,balance),ExposureLimits(D(1000),D(1000),2))
                    # Replay the observed per-unit requirement; allow another 1%
                    # price move and both VIP taker fees. Still within allocation.
                    observed=D(required)*sized.qty/D(old_qty)
                    self.assertLessEqual((observed+sized.notional*D('.0003'))*D('1.01'),budget)
                    self.assertLess(sized.qty,D(old_qty))
                    self.assertEqual(sized.leverage,10)

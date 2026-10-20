"""Regression: irrelevant Yellow archive pages must not break a positive lookup."""
import unittest
from decimal import Decimal
from .harness import Env
from customer.tenants import minimum_lot_notional
from execution.metadata import MarketFilters
from execution.geometry import protection_prices
from execution.transport import iter_pages, all_pages, VenueUnavailable
from execution.types import VenueOrder, VENUE_CANCELLED

class TestTargetedHistory(unittest.TestCase):
    def test_positive_lookup_stops_before_broken_archive(self):
        e=Env()
        try:
            calls=[]
            vo=VenueOrder('venue-1','why-known','BTCUSDT-PERP','sell','long','limit',Decimal('1'),Decimal('0'),Decimal('77000'),VENUE_CANCELLED,True)
            e.mock.get_open_orders=lambda *a,**kw:([],None)
            def history(*a,cursor=None,**kw):
                calls.append(cursor)
                if cursor:raise VenueUnavailable(401,'expired timestamp in archive')
                return [vo],'broken-archive'
            e.mock.get_orders=history
            self.assertEqual(e.eng._find_venue_order('acct-A','BTCUSDT-PERP','why-known','venue-1'),vo)
            self.assertEqual(calls,[None])
            with self.assertRaises(VenueUnavailable):e.eng._find_venue_order('acct-A','BTCUSDT-PERP','missing','missing')
        finally:e.ledger.close()
    def test_full_snapshot_still_rejects_incomplete_history(self):
        def pages(*a,cursor=None):
            if cursor:raise VenueUnavailable(401,'timestamp')
            return [1],'next'
        it=iter_pages(pages);self.assertEqual(next(it),1)
        with self.assertRaises(VenueUnavailable):all_pages(pages)
    def test_link_minimum_covers_stop_and_target(self):
        f=MarketFilters('LINKUSDT-PERP',Decimal('.001'),Decimal('.01'),Decimal('.01'),Decimal('20'),Decimal('20'),'TRADING')
        for strategy in ['WY-PULLBACK','WY-REVERT']:
            for direction in ['long','short']:
                price=Decimal('14.172')
                notional=minimum_lot_notional(f,price,strategy,direction)
                qty=notional/price;tp,sl=protection_prices(strategy,direction,price)
                self.assertGreaterEqual(qty*f.round_price(tp,up=direction=='long'),Decimal('20'))
                self.assertGreaterEqual(qty*f.round_price(sl,up=direction!='long'),Decimal('20'))
        self.assertEqual(minimum_lot_notional(f,Decimal('14.172'),'WY-PULLBACK','long')/Decimal('14.172'),Decimal('1.45'))

class TestRecoveryClose(unittest.TestCase):
    def test_close_identifier_stays_bounded_after_many_attempts(self):
        e=Env()
        try:
            e.ready();pid,_=e.open_and_fill()
            e.ledger.kv_set(f'closes:{pid}',1263)
            self.assertEqual(e.eng.close_position(pid,'USER_MARKET_CLOSE'),'CLOSE_SUBMITTED')
            closes=[o for o in e.ledger.orders_for_position(pid) if o['purpose']=='CLOSE']
            self.assertEqual(len(closes),1)
            self.assertEqual(len(closes[0]['client_order_id']),32)
            self.assertTrue(closes[0]['reduce_only'])
        finally:e.ledger.close()

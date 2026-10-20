import unittest
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import patch

from .harness import Env, ACCT
from execution.loss_stop import loss_limit, stop_price
from execution.metadata import MarketFilters
from execution.types import Capabilities

JP = 'yellow:04d10bf8-6614-4c11-9ab9-240e26fce66c'
HYPE = MarketFilters('HYPEUSDT-PERP', D('.001'), D('.01'), D('.01'), D(5), D(20), 'TRADING')


class DollarStopMath(unittest.TestCase):
    def price(self, **kw):
        data = dict(entry='81.349', qty='6.14', multiplier='1', direction='long',
                    paid_fees='.199793144', realized_gross='0', exit_fee='.0004',
                    strategy_stop='79.722', tick='.001')
        data.update(kw)
        return stop_price(**data)

    def test_exact_hype_incident_including_fees(self):
        px = self.price()
        net = (px-D('81.349'))*D('6.14')-D('.199793144')-px*D('6.14')*D('.0004')
        self.assertEqual(px, D('81.089'))
        self.assertGreaterEqual(net, D(-2))
        self.assertLess(net, D('-1.99'))

    def test_short_stop_rounds_inward(self):
        px = self.price(direction='short', strategy_stop='82.976')
        net = (D('81.349')-px)*D('6.14')-D('.199793144')-px*D('6.14')*D('.0004')
        self.assertGreaterEqual(net, D(-2))

    def test_cannot_raise_cap(self):
        for config in (None, '2', '10', '100000'):
            self.assertEqual(loss_limit(config), D(2))
        self.assertEqual(loss_limit('1'), D(1))

    def test_invalid_values_fail(self):
        for value in ('0', '-1', 'NaN', 'Infinity'):
            with self.assertRaises(ValueError): loss_limit(value)
        for key in ('entry', 'qty', 'multiplier', 'tick'):
            with self.assertRaises(ValueError): self.price(**{key:'0'})

    def test_preserves_tighter_stop(self):
        self.assertEqual(self.price(strategy_stop='81.2'), D('81.2'))
        self.assertEqual(self.price(previous_stop='81.3'), D('81.3'))
        self.assertEqual(self.price(direction='short',strategy_stop='83',previous_stop='81.4'),D('81.4'))

    def test_partial_exit_consumes_loss_budget(self):
        px=self.price(qty='3.14',paid_fees='.3',realized_gross='-1')
        net=D(-1)+(px-D('81.349'))*D('3.14')-D('.3')-px*D('3.14')*D('.0004')
        self.assertGreaterEqual(net,D(-2))

    def test_multiplier_and_higher_fees(self):
        px=self.price(qty='.614',multiplier='10',paid_fees='.5',exit_fee='.001')
        net=(px-D('81.349'))*D('6.14')-D('.5')-px*D('6.14')*D('.001')
        self.assertGreaterEqual(net,D(-2))


class AllAccountStops(unittest.TestCase):
    def make(self):
        e=Env(capabilities=Capabilities(venue_stop_orders=True, conditional_take_profit=True))
        self.addCleanup(e.ledger.close)
        e.meta._f[HYPE.symbol]=HYPE
        e.ready();e.feed(HYPE.symbol,'81.349')
        return e

    def opened(self,e,account=JP,side='long'):
        e.mock.set_account(account,'10000','10000')
        sig=replace(e.signal(account,market=HYPE.symbol,price='81.349',notional='500',
                             strategy='WY-PULLBACK',direction=side), account_id=account)
        r=e.eng.submit_entry(sig)
        self.assertEqual(r.status,'ACKNOWLEDGED',r)
        o=e.order(r.client_order_id)
        fee=D(o['qty'])*D('81.349')*D('.0004')
        e.eng.on_fill(e.mock.fill(r.client_order_id,o['qty'],'81.349',fee=str(fee)))
        return r.client_order_id

    def test_every_account_both_directions_with_fixed_size(self):
        for account in (JP,'yellow:future-account',ACCT):
            for side in ('long','short'):
                with self.subTest(account=account,side=side):
                    e=self.make();pid=self.opened(e,account,side)
                    p=e.ledger.get_protection(pid);o=e.order(pid)
                    self.assertEqual(D(o['qty']),D('6.14'))
                    px=D(p['stop_price']);qty=D(o['qty'])
                    net=(px-D('81.349'))*qty*(1 if side=='long' else -1)-D(o['fees'])-px*qty*D('.0004')
                    self.assertGreaterEqual(net,D(-2))
                    self.assertEqual(e.order(p['stop_coid'])['order_type'],'trigger_market')

    def test_existing_position_migrates_and_restart_is_idempotent(self):
        e=self.make()
        with patch.object(e.eng,'_dollar_stop',side_effect=lambda pos,f,sl:sl):
            pid=self.opened(e)
        old=e.ledger.get_protection(pid)
        self.assertLess(D(old['stop_price']),D(80))
        e.eng.check_protection()
        new=e.ledger.get_protection(pid)
        self.assertEqual(D(new['stop_price']),D('81.089'))
        self.assertNotEqual(old['stop_coid'],new['stop_coid'])
        self.assertEqual(e.order(old['stop_coid'])['state'],'CANCELLED')
        count=e.mock.place_calls
        e.eng.check_protection();e.restart()
        self.assertEqual(e.mock.place_calls,count)

    def test_profit_override_cannot_bypass_stop(self):
        e=self.make();e.config.take_profit_margin_pct=D(3)
        pid=self.opened(e)
        self.assertEqual(D(e.ledger.get_protection(pid)['stop_price']),D('81.089'))

    def test_stop_fill_with_fees_accounts_for_full_loss(self):
        e=self.make();pid=self.opened(e)
        p=e.ledger.get_protection(pid);o=e.order(pid);px=D(p['stop_price']);qty=D(o['qty'])
        e.eng.on_fill(e.mock.fill(p['stop_coid'],qty,px,fee=str(px*qty*D('.0004'))))
        self.assertEqual(e.pos(pid)['state'],'CLOSED')
        self.assertGreaterEqual((px-D('81.349'))*qty-D(o['fees'])-px*qty*D('.0004'),D(-2))

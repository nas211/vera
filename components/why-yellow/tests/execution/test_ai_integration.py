import json
import sqlite3
import tempfile
from pathlib import Path
from decimal import Decimal as D
import unittest
from .harness import Env, ACCT
from execution.types import Capabilities, PositionState as P
from execution.ai_policy import parse, live_performance
from execution import geometry
from customer.ai_gateway import AIGateway, digest

STRATEGY='AI-'+'a'*64+'-30-40-6'


class AIExecutionTests(unittest.TestCase):
    def setUp(self):
        self.e=Env(capabilities=Capabilities(venue_stop_orders=True));self.e.ready()
        self.e.mock.set_account(ACCT,'1000','1000')
    def tearDown(self):self.e.ledger.close()
    def signal(self,sid='ai1',**kwargs):
        sig=self.e.signal(sid,**kwargs)
        from dataclasses import replace
        return replace(sig,strategy=STRATEGY,notional_usd=D('80'))
    def test_strategy_geometry_survives_without_research_files(self):
        tp,sl=geometry.protection_prices(STRATEGY,'long',D(100))
        self.assertEqual((tp,sl),(D('100.3'),D('99.6')))
        self.assertIsNone(parse('AI-bad-30-40-6'))
    def test_ai_cap_is_notional_not_leveraged_margin(self):
        from dataclasses import replace
        r=self.e.eng.submit_entry(replace(self.signal(),notional_usd=D('101')))
        self.assertEqual(r.reason,'ai_ten_percent_equity_cap');self.assertEqual(self.e.mock.place_calls,0)
    def test_pending_entry_reserves_equity_capacity(self):
        r=self.e.eng.submit_entry(self.signal());self.assertEqual(r.status,'ACKNOWLEDGED')
        self.e.feed('ETHUSDT-PERP','2500')
        r=self.e.eng.submit_entry(self.signal('ai2',market='ETHUSDT-PERP',price='2500'))
        self.assertEqual(r.reason,'ai_ten_percent_equity_cap');self.assertEqual(self.e.mock.place_calls,1)
    def test_same_coin_opposite_side_is_not_a_second_position(self):
        self.e.eng.submit_entry(self.signal())
        r=self.e.eng.submit_entry(self.signal('ai2',direction='short'))
        self.assertEqual(r.reason,'market_already_owned')
    def test_core_pending_entry_reserves_coin(self):
        r=self.e.eng.submit_entry(self.e.signal('core'))
        self.assertEqual(r.status,'ACKNOWLEDGED')
        r=self.e.eng.submit_entry(self.signal(direction='short'))
        self.assertEqual(r.reason,'market_already_owned')
    def test_ai_fills_receive_actual_exchange_protection_and_restart(self):
        r=self.e.eng.submit_entry(self.signal());o=self.e.order(r.client_order_id)
        self.e.eng.on_fill(self.e.mock.fill(r.client_order_id,o['qty'],'77000'))
        self.assertEqual(self.e.pos(r.client_order_id)['state'],P.PROTECTED.value)
        self.assertTrue(self.e.restart().ok)
        self.assertEqual(self.e.pos(r.client_order_id)['state'],P.PROTECTED.value)
        self.e.feed()
        self.assertFalse(live_performance(self.e.eng,STRATEGY)['unknown'])
    def test_protection_failure_blocks_ai_as_it_blocks_core(self):
        self.e.mock.script_place('accept','reject')
        r=self.e.eng.submit_entry(self.signal());o=self.e.order(r.client_order_id)
        self.e.eng.on_fill(self.e.mock.fill(r.client_order_id,o['qty'],'77000'))
        self.e.feed('ETHUSDT-PERP','2500')
        r=self.e.eng.submit_entry(self.signal('ai2',market='ETHUSDT-PERP',price='2500'))
        self.assertEqual(r.reason,'position_protection_unverified')


class AIGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'state').mkdir()
        self.now=10000.
        self.spec=dict(family='momentum',lookback=6,threshold=.3,side='long',regime='any',hold_bars=6,tp_bps=30,sl_bps=40)
        self.sid=digest(self.spec);self.bar=9_600_000
        self.cfg=dict(live=dict(enabled=True),core_snapshot=str(self.root/'core.json'),core_priority_delay_seconds=20)
        self.write('config.json',self.cfg)
        self.write('core.json',dict(ts=self.now*1000,feed=dict(connected=True),open=[]))
        self.sig=dict(id=digest(dict(candidate=self.sid,market='BTCUSDT-PERP',bar=self.bar)),
                      candidate=self.sid,market='BTCUSDT-PERP',side='long',bar=self.bar,ts=9970.,expires=10060.,price=100.,spec=self.spec)
        self.write('state/signals.json',dict(ts=self.now,venue='yellow',signals=[self.sig]))
        self.db=sqlite3.connect(self.root/'state/platform.sqlite3')
        self.db.executescript('create table candidates(id text,spec text,stage text,paper_started real);create table trades(candidate text,pnl real,evidence_ok int,opened real);')
        self.db.execute('insert into candidates values(?,?,?,?)',(self.sid,json.dumps(self.spec),'LIVE_ELIGIBLE',9000.))
        self.db.executemany('insert into trades values(?,?,?,?)',[(self.sid,1.,1,9100.)]*10);self.db.commit()
        self.gateway=AIGateway(str(self.root),clock=lambda:self.now)
    def write(self,path,value):(self.root/path).write_text(json.dumps(value))
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_valid_live_candidate_emits_once(self):
        self.assertEqual(len(self.gateway.poll()),1);self.assertEqual(self.gateway.poll(),[])
    def test_core_same_coin_takes_precedence(self):
        self.assertEqual(self.gateway.poll({'BTCUSDT-PERP'}),[])
    def test_core_open_position_reserves_coin(self):
        self.write('core.json',dict(ts=self.now*1000,feed=dict(connected=True),open=[dict(market='BTCUSDT-PERP')]))
        self.assertEqual(self.gateway.poll(),[])
    def test_stale_source_cannot_submit(self):
        self.now+=20
        self.assertEqual(self.gateway.poll(),[])
    def test_historical_trades_cannot_satisfy_forward_count(self):
        self.db.execute('update trades set opened=100');self.db.commit()
        self.assertEqual(self.gateway.poll(),[])
    def test_modified_strategy_cannot_inherit_promotion(self):
        self.sig['spec']['tp_bps']=100
        self.write('state/signals.json',dict(ts=self.now,venue='yellow',signals=[self.sig]))
        self.assertEqual(self.gateway.poll(),[])
    def test_disabled_live_switch_blocks_all(self):
        self.cfg['live']['enabled']=False;self.write('config.json',self.cfg)
        self.assertEqual(self.gateway.poll(),[])
    def test_customer_paper_can_run_with_live_disabled(self):
        self.cfg['live']['enabled']=False
        self.cfg['customer_paper']={'enabled':True}
        self.write('config.json',self.cfg)
        self.assertEqual(len(self.gateway.poll()),1)
        self.assertTrue(self.gateway.paper_enabled)
        self.assertFalse(self.gateway.live_enabled)
    def test_paper_switch_does_not_bypass_promotion(self):
        self.cfg['customer_paper']={'enabled':True};self.write('config.json',self.cfg)
        self.db.execute('delete from trades');self.db.commit()
        self.assertEqual(self.gateway.poll(),[])


class TenantOwnershipTests(unittest.TestCase):
    def setUp(self):
        from .test_tenant_fanout import Env as TenantEnv, ctx
        self.e=TenantEnv();self.e.accounts.set(ctx('acctA',100));self.e.ready()
        self.t=self.e.reg.tenants['acctA']
        self.e.mocks['acctA'].capabilities=Capabilities(venue_stop_orders=True)
    def tearDown(self):self.e.cleanup()
    def test_core_can_cancel_pending_ai_and_take_coin(self):
        first=self.e.reg.dispatch(self.e.sig('ai',strategy=STRATEGY))['acctA']
        self.assertTrue(first.startswith('ACKNOWLEDGED'),first)
        second=self.e.reg.dispatch(self.e.sig('core',strategy='WY-REVERT'))['acctA']
        self.assertTrue(second.startswith('ACKNOWLEDGED'),second)
        orders=self.t.ledger.orders_where(list(__import__('execution.types',fromlist=['OrderState']).OrderState),purpose='ENTRY')
        ai=next(o for o in orders if o['strategy']==STRATEGY)
        self.assertEqual(ai['state'],'CANCELLED')
    def test_core_close_event_cannot_close_ai_position(self):
        self.e.reg.dispatch(self.e.sig('ai',strategy=STRATEGY));self.e.fill_entries()
        self.assertEqual(self.t.close('BTCUSDT-PERP','long','TIMEOUT'),'AI_POSITION_NOT_OWNED_BY_CORE_CLOSE')
        self.assertEqual(self.t.open_positions()[0]['state'],'PROTECTED')
    def test_filled_ai_owns_coin_until_its_exit(self):
        self.e.reg.dispatch(self.e.sig('ai',strategy=STRATEGY));self.e.fill_entries()
        result=self.e.reg.dispatch(self.e.sig('core',direction='short'))['acctA']
        self.assertEqual(result,'BLOCKED:ai_position_owns_market')
    def test_expired_ai_position_uses_its_own_timeout(self):
        self.e.reg.dispatch(self.e.sig('ai',strategy=STRATEGY));self.e.fill_entries()
        self.e.clock.advance(1801);self.e.feed()
        self.t.manage()
        self.assertEqual(self.t.open_positions()[0]['state'],'CLOSE_PENDING')


if __name__=='__main__':unittest.main()

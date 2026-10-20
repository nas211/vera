import copy
import dataclasses
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from tests.execution.harness import FakeClock, make_metadata
from execution.mock_adapter import MockYellowAdapter
from customer.context import CustomerExecutionContext
from customer.scalp.strategy import LiveStrategy, BASE, CONFIG, RULES, MODULES, STOP_MAKER_SECONDS
from customer.scalp.runtime import Runtime

M='BTCUSDT-PERP'
F={M:dict(tick=.1,step=.001,min_qty=.001,min_notional=10)}
def book(model,clock,bid=100.,ask=100.2):
 model.books[M]={'bids':{bid:100},'asks':{ask:100},'received':clock()}
 model.connected=True
 model.history[M].append((clock()-31,(bid+ask)/2))

def lot():return dict(market=M,buy=True,entry=100.,qty=1.,opened=0.,exit_id=None,entry_order='original')

class StrategyParity(unittest.TestCase):
 def setUp(self):self.clock=FakeClock()
 def test_exact_source_parameters_and_decision_methods(self):
  self.assertEqual(CONFIG['target_bps'],10);self.assertEqual(CONFIG['entry_margin_usd'],10)
  self.assertEqual(CONFIG['levels_per_side'],5);self.assertEqual(CONFIG['max_entry_slots'],100)
  self.assertEqual(CONFIG['maker_fee'],0);self.assertEqual(CONFIG['taker_fee'],.00015)
  self.assertEqual(RULES['max_hold_seconds'],120);self.assertEqual(RULES['adverse_exit_bps'],5)
  for name in ['entry_reason','exit_price','signal','profitable','available_levels','rounded']:
   self.assertEqual(getattr(LiveStrategy,name).__code__.co_code,getattr(BASE,name).__code__.co_code)
 def test_identical_maker_grid_before_real_fills(self):
  original=BASE(F,clock=self.clock,record=False);live=LiveStrategy(F,self.clock);live.set_equity(1000)
  for model in [original,live]:book(model,self.clock);model.maintain()
  self.assertEqual([{k:v for k,v in o.items() if k!='qty'} for o in original.orders.values()],[{k:v for k,v in o.items() if k!='qty'} for o in live.orders.values()])
  from customer.scalp.sizing import minimum_quantity
  self.assertTrue(all(abs(o['qty']-float(minimum_quantity(o['price'],F[M])))<1e-9 for o in live.orders.values()))
  self.assertEqual(len(live.intents),len(original.orders))
  self.assertGreater(len(live.orders),2)
 def test_public_trades_never_create_live_inventory(self):
  live=LiveStrategy(F,self.clock);book(live,self.clock);live.maintain()
  live.trade(M,dict(price='99',amount='100',direction='sell',executed_at='2026-10-11T00:00:00Z'))
  self.assertEqual(live.lots,{});self.assertEqual(live.stats['realized_pnl'],0)
 def test_profit_decision_matches_original_without_synthetic_fill(self):
  original=BASE(F,clock=self.clock,record=False);live=LiveStrategy(F,self.clock);live.set_equity(1000)
  for model in [original,live]:book(model,self.clock,100.2,100.4);model.lots['lot']=lot()
  original.execute_profit('lot');live.execute_profit('lot')
  self.assertNotIn('lot',original.lots)
  self.assertEqual(live.lots['lot']['qty'],1.)
  self.assertEqual(live.stats['realized_pnl'],0)
  self.assertEqual(live.intents[0]['kind'],'ioc')
  self.assertEqual(live.intents[0]['qty'],1.)
  self.assertEqual(live.intents[0]['price'],100.2)
  live.execute_profit('lot');self.assertEqual(len(live.intents),1)
 def stopped(self,bid=99.8,ask=99.9):
  live=LiveStrategy(F,self.clock);live.set_equity(1000);live.allow_entries=False
  book(live,self.clock,bid,ask);live.lots['lot']=dict(lot(),opened=self.clock())
  return live
 def iocs(self,live):return [i for i in live.intents if i['kind']=='ioc']
 def test_stop_rests_free_maker_close_at_touch_then_bounded_taker(self):
  live=self.stopped()
  live.orders['x']=dict(id='x',market=M,buy=False,price=100.1,qty=1.,lot='lot',requested=self.clock()-2,active=True,active_at=0,queue=0.,cancel_at=None)
  live.maintain()
  self.assertNotIn('x',live.orders)
  self.assertFalse(self.iocs(live))
  self.assertEqual([(i['kind'],i['price'],i['lot']) for i in live.intents],[('maker',99.9,'lot')])
  self.clock.advance(STOP_MAKER_SECONDS-.1);book(live,self.clock,99.8,99.9);live.maintain()
  self.assertFalse(self.iocs(live))
  self.clock.advance(.1);book(live,self.clock,99.8,99.9);live.maintain()
  self.assertEqual(len(self.iocs(live)),1);i=self.iocs(live)[0]
  self.assertEqual((i['buy'],i['qty'],i['reason']),(False,1.,'STOP_EXIT'))
  self.assertAlmostEqual(i['price'],99.5)  # 30 bps through the bid, rounded outward to a tick
  self.assertFalse([o for o in live.orders.values() if o['lot']=='lot'])
  live.maintain();self.assertEqual(len(self.iocs(live)),1)
 def test_maker_close_follows_the_touch(self):
  live=self.stopped(99.7,99.8);live.maintain()
  self.assertEqual([round(o['price'],6) for o in live.orders.values() if o['lot']=='lot'],[99.8])
  self.clock.advance(.5);book(live,self.clock,99.8,99.9);live.maintain()
  self.assertEqual([round(o['price'],6) for o in live.orders.values() if o['lot']=='lot'],[99.8])  # reprices at most once a second
  self.clock.advance(.6);book(live,self.clock,99.8,99.9);live.maintain()
  self.assertEqual([round(o['price'],6) for o in live.orders.values() if o['lot']=='lot'],[99.9])
  self.assertFalse(self.iocs(live))
 def test_further_adverse_move_turns_taker_before_grace_ends(self):
  live=self.stopped();live.maintain();self.assertFalse(self.iocs(live))
  self.clock.advance(1);book(live,self.clock,99.4,99.5);live.maintain()
  self.assertEqual([i['reason'] for i in self.iocs(live)],['STOP_EXIT'])
 def test_time_limit_closes_maker_first_then_taker_and_target_waits(self):
  live=self.stopped(100.,100.1)
  live.maintain()
  self.assertFalse(self.iocs(live));self.assertNotIn('stop_at',live.lots['lot'])
  self.clock.advance(RULES['max_hold_seconds']);book(live,self.clock,100.,100.1)
  live.intents=[];live.maintain()
  self.assertFalse(self.iocs(live));self.assertIn('stop_at',live.lots['lot'])
  self.clock.advance(STOP_MAKER_SECONDS);book(live,self.clock,100.,100.1);live.maintain()
  self.assertEqual([i['reason'] for i in self.iocs(live)],['STOP_EXIT'])

class LiveBoundary(unittest.TestCase):
 def setUp(self):
  self.clock=FakeClock();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.ctx=CustomerExecutionContext('test-user','scalp-test','fake-credential',D('100'),'ACTIVE',False,max_open_positions=100,trading_style='scalp')
  self.mock=MockYellowAdapter(clock=self.clock);self.mock.set_account(self.ctx.namespace,'1000','1000')
  self.r=Runtime(self.ctx,self.mock,make_metadata(),self.tmp.name,self.clock)
  self.addCleanup(lambda:self.r.store.db.close())
  self.r.model.filters=F;book(self.r.model,self.clock,100.,100.2)
  self.assertTrue(self.r.reconcile());self.r.model.connected=True
 def intent(self,number='1'):
  return dict(kind='maker',id=number,market=M,buy=True,price=100.,qty=1.,lot=None,requested=self.clock())
 def test_real_fill_only_and_idempotent_reconciliation(self):
  self.r.submit(self.intent());self.assertEqual(self.r.model.lots,{})
  row=self.r.store.orders()[0];self.assertEqual(row['state'],'open')
  req=next(iter(self.mock.orders.values()));self.assertEqual(req['order_type'],'post_only')
  self.mock.fill(row['venue_id'],'.4','100',trade_id='confirmed-fill')
  self.assertTrue(self.r.reconcile());self.assertEqual(len(self.r.model.lots),1)
  self.r.reconcile();self.assertEqual(len(self.r.model.lots),1)
  self.assertAlmostEqual(sum(l['qty'] for l in self.r.model.lots.values()),.4)
 def test_ambiguous_post_never_retried(self):
  self.mock.place_script.append('timeout_after_accept')
  try:self.r.submit(self.intent())
  except Exception:self.fail('Ambiguity must be contained')
  self.assertEqual(self.r.store.orders()[0]["state"],"unknown")
  count=self.mock.place_calls
  self.r.submit(self.intent());self.assertEqual(self.mock.place_calls,count)
 def test_pause_prevents_entry(self):
  self.r.ctx=dataclasses.replace(self.ctx,entries_paused=True)
  with self.assertRaises(ValueError):self.r.submit(self.intent())
  self.assertEqual(self.mock.place_calls,0)
 def test_scalp_uses_100_slots_instead_of_saved_swing_limit(self):
  self.r.ctx=dataclasses.replace(self.ctx,max_open_positions=1)
  for i in range(CONFIG['max_entry_slots']):
   self.r.submit(dict(self.intent(str(i)),qty=.1,buy=bool(i%2)))
  self.assertEqual(self.mock.place_calls,100)
  with self.assertRaisesRegex(ValueError,'max_open_positions'):
   self.r.submit(self.intent('overflow'))
  self.assertEqual(self.mock.place_calls,100)
  self.assertEqual(self.r.ctx.max_open_positions,1)
  self.assertEqual(dataclasses.replace(self.r.ctx,trading_style='swing').limits().max_open_positions,1)
 def test_insufficient_funds_never_resizes_strategy_order(self):
  self.mock.set_account(self.ctx.namespace,'1','1')
  with self.assertRaises(ValueError):self.r.submit(self.intent())
  self.assertEqual(self.mock.place_calls,0)
 def test_exchange_reserved_margin_is_not_subtracted_twice(self):
  # A $30 account can fund three unchanged $10-margin orders. Yellow reports
  # available balance after reserving each accepted order's margin.
  for i in range(3):
   self.mock.set_account(self.ctx.namespace,str(30-i*10),'1000')
   self.r.submit(self.intent('funded-'+str(i)))
  self.assertEqual(self.mock.place_calls,3)
  self.assertTrue(all(D(row['payload']['qty'])==1 for row in self.r.store.orders()))
  self.mock.set_account(self.ctx.namespace,'0','1000')
  with self.assertRaisesRegex(ValueError,'no_available_balance'):
   self.r.submit(self.intent('unfunded'))
  self.assertEqual(self.mock.place_calls,3)
 def test_restart_recovers_confirmed_inventory(self):
  self.r.submit(self.intent());row=self.r.store.orders()[0]
  self.mock.fill(row['venue_id'],'1','100',trade_id='restart-fill')
  self.r.reconcile()
  restarted=Runtime(self.ctx,self.mock,make_metadata(),self.tmp.name,self.clock)
  self.addCleanup(restarted.store.db.close)
  self.assertTrue(restarted.reconcile());self.assertEqual(len(restarted.model.lots),1)
  self.assertEqual(self.mock.place_calls,1)
 def test_unowned_position_blocks_new_orders(self):
  self.mock.positions[(self.ctx.namespace,M,'long')]={'qty':D(1),'entry':D(100)}
  self.assertFalse(self.r.reconcile())
  with self.assertRaises(ValueError):self.r.submit(self.intent())
 def test_cancellation_requires_private_confirmation(self):
  self.r.submit(self.intent());row=self.r.store.orders()[0]
  self.r.cancel(row);self.assertEqual(self.r.store.orders()[0]['state'],'cancelled')
 def test_cancel_uses_fresh_row_after_another_cancel_reconciles_fill(self):
  self.r.submit(self.intent('1'));self.r.submit(self.intent('2'))
  first,stale=self.r.store.orders()
  self.mock.fill(stale['venue_id'],'.4','100',trade_id='fill-during-cancel')
  self.r.cancel(first)
  self.assertEqual(D(self.r.store.orders()[1]['payload']['filled_qty']),D('.4'))
  self.r.cancel(stale)
  self.assertTrue(self.r.ready)
  self.assertEqual(D(self.r.store.orders()[1]['payload']['filled_qty']),D('.4'))
  self.assertEqual(len(self.r.model.lots),1)
 def test_restart_repairs_stale_fill_counter_without_replaying_inventory(self):
  self.r.submit(self.intent());stale=self.r.store.orders()[0]
  self.mock.fill(stale['venue_id'],'1','100',trade_id='durable-fill')
  self.r.reconcile()
  stale['payload']['cancel_requested']=True
  with self.r.store.db:self.r.store.put(stale['id'],'cancel_pending',stale['payload'],stale['venue_id'])
  restarted=Runtime(self.ctx,self.mock,make_metadata(),self.tmp.name,self.clock)
  self.addCleanup(restarted.store.db.close)
  self.assertTrue(restarted.reconcile())
  self.assertEqual(restarted.store.orders()[0]['state'],'filled')
  self.assertEqual(D(restarted.store.orders()[0]['payload']['filled_qty']),D(1))
  self.assertEqual(len(restarted.model.lots),1)
  self.assertEqual(restarted.model.stats['entry_fills'],1)
  self.assertEqual(self.mock.place_calls,1)
  self.assertTrue(restarted.reconcile())
  self.assertEqual(len(restarted.model.lots),1)
 def test_frame_cancels_batch_then_verifies_once_before_replacements(self):
  from unittest.mock import patch
  for number in ('1','2','3'):self.r.submit(self.intent(number))
  self.r.ctx=dataclasses.replace(self.ctx,entries_paused=True)
  with patch.object(self.r,'reconcile',wraps=self.r.reconcile) as sync:self.r.frame()
  self.assertEqual(sync.call_count,1)
  self.assertTrue(self.r.ready)
  self.assertTrue(all(row['state']=='cancelled' for row in self.r.store.orders()))
  self.assertEqual(self.mock.place_calls,3)
 def test_disconnect_cancels_batch_then_verifies_once(self):
  from unittest.mock import patch
  for number in ('1','2','3'):self.r.submit(self.intent(number))
  with patch.object(self.r,'reconcile',wraps=self.r.reconcile) as sync:self.r.disconnected()
  self.assertEqual(sync.call_count,1)
  self.assertFalse(self.r.ready)
  self.assertTrue(all(row['state']=='cancelled' for row in self.r.store.orders()))
 def test_active_orders_exclude_terminal_history(self):
  for number in ('1','2'):self.r.submit(self.intent(number))
  self.r.cancel(self.r.store.orders()[0])
  active=self.r.store.active_orders()
  self.assertEqual([r['id'] for r in active],[r['id'] for r in self.r.store.orders() if r['state'] not in ('filled','cancelled','rejected')])
  self.assertEqual(len(active),1)
  self.assertTrue(self.r.store.has_order(active[0]['id']));self.assertFalse(self.r.store.has_order('absent'))
 def test_missing_order_history_is_read_for_its_market_only(self):
  from unittest.mock import patch
  self.r.submit(self.intent())
  order=next(iter(self.mock.orders.values()));order['state']='cancelled'
  markets=[]
  original=self.mock.get_orders
  def spy(*args,**kwargs):markets.append(kwargs.get('market'));return original(*args,**kwargs)
  with patch.object(self.mock,'get_orders',side_effect=spy):self.assertTrue(self.r.reconcile())
  self.assertTrue(markets);self.assertEqual(set(markets),{M})
  self.assertEqual(self.r.store.orders()[0]['state'],'cancelled')
 def test_independent_private_reads_overlap_and_positions_follow(self):
  import threading
  from unittest.mock import patch
  self.r.submit(self.intent())
  # Sequential reads cannot pass a three-party barrier; concurrent reads can.
  barrier=threading.Barrier(3,timeout=5);order=[]
  def together(name,fn):
   def call(*args,**kwargs):
    barrier.wait();order.append(name);return fn(*args,**kwargs)
   return call
  def last(*args,**kwargs):
   order.append('positions');return original_positions(*args,**kwargs)
  m=self.mock;original_positions=m.get_positions
  with patch.object(m,'get_account',together('account',m.get_account)),patch.object(m,'get_open_orders',together('open',m.get_open_orders)),\
       patch.object(m,'get_trades',together('trades',m.get_trades)),patch.object(m,'get_positions',last):
   self.assertTrue(self.r.reconcile())
  self.assertEqual(sorted(order[:3]),['account','open','trades'])
  self.assertEqual(order[3:],['positions'])
 def test_queued_old_book_keeps_arrival_time_and_cannot_create_orders(self):
  self.r.ctx=dataclasses.replace(self.ctx,entries_paused=True)
  self.r.model.lots['old']=lot()
  self.r.reconcile=lambda:True
  received=self.clock()-30
  data=dict(_received_at=received,market=M,header={'type':'orderbook.snapshot'},sequence_num=1,bids=[['101','10']],asks=[['102','10']])
  self.r.frame([data])
  self.assertEqual(self.r.model.books[M]['received'],received)
  self.assertEqual(self.mock.place_calls,0)
  self.assertEqual(self.r.model.lots['old']['qty'],1.)
  self.assertIs(self.r.model.clock,self.clock)
 def test_restart_restores_resting_exit_for_strategy_management(self):
  self.r.submit(self.intent());row=self.r.store.orders()[0]
  self.mock.fill(row['venue_id'],'1','100',trade_id='maker-restore');self.r.reconcile()
  lid=next(iter(self.r.model.lots));self.r.submit(dict(self.intent('2'),lot=lid,buy=False,price=100.2))
  self.r.model.orders.clear()
  self.r.reconcile()
  self.assertIn('2',self.r.model.orders)
  self.assertEqual(self.r.model.lots[lid]['exit_id'],'2')
  self.assertNotIn(lid,self.r.model.pending_exits,'A resting maker exit must still allow profit capture requests')
 def test_confirmed_exit_updates_real_pnl_only_once(self):
  self.r.submit(self.intent());row=self.r.store.orders()[0]
  self.mock.fill(row['venue_id'],'1','100',trade_id='pnl-entry');self.r.reconcile()
  lid=next(iter(self.r.model.lots));self.r.submit(dict(self.intent('2'),lot=lid,buy=False,price=100.2,kind='ioc'))
  exitrow=self.r.store.orders()[-1]
  self.assertIn(lid,self.r.model.lots)
  self.mock.fill(exitrow['venue_id'],'1','100.2',trade_id='real-exit',fee='.01503')
  self.r.reconcile();self.r.reconcile()
  self.assertEqual(self.r.model.lots,{})
  self.assertAlmostEqual(self.r.model.stats['realized_pnl'],.18497)
  self.assertEqual(len(self.r.results()['trades']),1)
 def test_unconfirmed_cancellation_keeps_exit_reserved(self):
  self.r.submit(self.intent());row=self.r.store.orders()[0]
  self.mock.cancel_order=lambda *a:None
  self.r.cancel(row)
  self.assertEqual(self.r.store.orders()[0]['state'],'cancel_pending')
  self.assertIn('1',self.r.model.orders)
  self.assertIsNotNone(self.r.model.orders['1']['cancel_at'])
 def test_exit_waits_for_previous_exit_cancellation(self):
  self.r.submit(self.intent());row=self.r.store.orders()[0]
  self.mock.fill(row['venue_id'],'1','100',trade_id='entry-for-exit');self.r.reconcile()
  lid=next(iter(self.r.model.lots))
  p=dict(self.intent('2'),buy=False,lot=lid,price=100.2)
  self.r.submit(p)
  with self.assertRaises(ValueError):self.r.submit(dict(p,id='3',kind='ioc'))
  self.assertEqual(self.mock.place_calls,2)

if __name__=='__main__':unittest.main()

class RecoveryBoundary(LiveBoundary):
 def test_strategy_cancel_time_survives_private_refresh(self):
  self.r.submit(self.intent());self.r.reconcile()
  self.r.model.orders['1']['cancel_at']=self.clock()+.25
  self.r.reconcile()
  self.assertEqual(self.r.model.orders['1']['cancel_at'],self.clock()+.25)
 def test_unknown_post_recovers_fill_using_exchange_id(self):
  self.mock.place_script.append('timeout_after_accept');self.r.submit(self.intent())
  coid=self.r.store.orders()[0]['id']
  trade=self.mock.fill(coid,'1','100',trade_id='lost-response-fill')
  self.mock.trades[-1]=dataclasses.replace(trade,client_order_id=None)
  self.assertTrue(self.r.reconcile())
  self.assertEqual(len(self.r.model.lots),1)
  self.assertEqual(self.mock.place_calls,1)
 def test_approved_account_cannot_enter_without_its_emergency_guard(self):
  from execution.sizing import HARD_LOSS_LIMIT_ACCOUNT
  self.r.ctx=dataclasses.replace(self.ctx,account_id=HARD_LOSS_LIMIT_ACCOUNT.split(':',1)[1])
  with self.assertRaisesRegex(ValueError,'hard_loss_policy'):self.r.submit(self.intent())
  self.assertEqual(self.mock.place_calls,0)
 def test_actual_frame_maker_then_partial_profit_capture_while_paused(self):
  self.r.frame()
  entries=self.r.store.orders();self.assertGreater(len(entries),2)
  entry=next(r for r in entries if r['payload']['buy'])
  self.mock.fill(entry['venue_id'],'.04','100',trade_id='frame-entry')
  self.r.ctx=dataclasses.replace(self.ctx,entries_paused=True)
  self.r.reconcile();self.r.frame()
  maker=[r for r in self.r.store.orders() if r['payload'].get('lot')]
  self.assertEqual(len(maker),1)
  self.clock.advance(.5);book(self.r.model,self.clock,100.2,100.4)
  self.r.frame();self.clock.advance(.3);self.r.frame()
  iocs=[r for r in self.r.store.orders() if r['payload']['kind']=='ioc']
  self.assertEqual(len(iocs),1)
  self.assertEqual(self.r.store.orders()[len(entries)]['state'],'cancelled')
  self.mock.fill(iocs[0]['venue_id'],'.02','100.2',trade_id='partial-ioc',fee='.0003006')
  self.mock.manual_cancel(iocs[0]['id'])
  self.r.reconcile()
  self.assertAlmostEqual(sum(l['qty'] for l in self.r.model.lots.values()),.02)
  self.assertFalse(self.r.model.pending_exits)
 def test_actual_frame_stop_exit_cancels_maker_then_sends_taker_ioc(self):
  self.r.frame()
  entry=next(r for r in self.r.store.orders() if r['payload']['buy'])
  self.mock.fill(entry['venue_id'],'.04','100',trade_id='stop-entry')
  self.r.ctx=dataclasses.replace(self.ctx,entries_paused=True)
  self.r.reconcile();self.r.frame()
  maker=[r for r in self.r.store.orders() if r['payload'].get('lot')]
  self.assertEqual([r['payload']['kind'] for r in maker],['maker'])
  self.clock.advance(.5);book(self.r.model,self.clock,99.8,99.9)
  for _ in range(3):self.r.frame();self.clock.advance(.4)
  self.assertEqual(self.r.store.get_order(maker[0]['id'])['state'],'cancelled')
  makers=[r for r in self.r.store.orders() if r['payload'].get('lot') and r['payload']['kind']=='maker' and r['state']=='open']
  self.assertEqual([D(r['payload']['price']) for r in makers],[D('99.9')])  # free close at the touch first
  self.assertFalse([r for r in self.r.store.orders() if r['payload']['kind']=='ioc'])
  self.clock.advance(STOP_MAKER_SECONDS);book(self.r.model,self.clock,99.8,99.9)
  for _ in range(3):self.r.frame();self.clock.advance(.4)
  iocs=[r for r in self.r.store.orders() if r['payload']['kind']=='ioc']
  self.assertEqual(len(iocs),1)
  self.assertEqual(iocs[0]['payload']['reason'],'STOP_EXIT')
  sent=self.mock.orders[iocs[0]['venue_id']]
  self.assertEqual((sent['order_type'],sent['reduce_only'],sent['side']),('limit',True,'sell'))
  self.assertLess(sent['price'],D('99.8'))
  self.mock.fill(iocs[0]['venue_id'],'.04','99.8',trade_id='stop-fill',fee='.0006')
  self.r.reconcile()
  self.assertEqual(self.r.model.lots,{})
  self.assertEqual(self.r.results()['trades'][0]['reason'],'STOP_EXIT')

class YellowWireBoundary(unittest.TestCase):
 def test_post_only_price_and_exit_flags_are_sent(self):
  from tests.execution.test_adapter_yellow_exec import client
  c=client()
  c.place(M,True,'long','.01',typ='post_only',price='77000',tif='gtc',leverage=10,coid='scalp-maker',validate=False)
  body=c.s.calls[-1]['body']
  self.assertEqual((body['type'],body['price'],body['time_in_force'],body['reduce_only']),('post_only','77000','gtc',False))
  c.place(M,False,'long','.01',typ='limit',price='77100',tif='ioc',reduce_only=True,leverage=10,coid='scalp-profit',validate=False)
  body=c.s.calls[-1]['body']
  self.assertEqual((body['type'],body['price'],body['time_in_force'],body['reduce_only']),('limit','77100','ioc',True))
 def test_history_timestamp_and_lower_bound(self):
  from customer.scalp.transport import ScalpYellowTransport
  from types import SimpleNamespace
  calls=[]
  def page(m,c,s):
   calls.append((m,c,s))
   return {'trades':[{'id':'fill','order_uuid':'order','market':M,'amount':'.1','price':'100','fee':'0','created_at':'2026-10-11T00:00:00Z'}]}
  rows,cursor=ScalpYellowTransport(SimpleNamespace(trades_page=page)).get_trades('scalp',since=1)
  self.assertEqual(calls[0][2],'1970-01-01T00:00:01+00:00')
  self.assertGreater(rows[0].ts,1700000000)

class GlobalScalpBrake(unittest.TestCase):
 def test_global_brake_is_definitive_unsent_before_any_post(self):
  from customer.scalp.transport import ScalpYellowTransport
  from types import SimpleNamespace
  from execution.transport import VenueUnavailable
  transport=ScalpYellowTransport(SimpleNamespace(ensure=lambda:SimpleNamespace(entries_allowed=lambda:False)))
  with self.assertRaisesRegex(VenueUnavailable,'global_entries_disabled'):
   transport.place_order(SimpleNamespace(reduce_only=False))

class WorkerLifecycle(unittest.TestCase):
 def test_no_second_worker_until_previous_worker_has_stopped(self):
  from customer.scalp.registry import ScalpRegistry
  from types import SimpleNamespace
  contexts=[];old=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:True),close=lambda:None)
  registry=ScalpRegistry(SimpleNamespace(list_execution_contexts=lambda:contexts),lambda ctx:self.fail('Duplicate account worker'),None,None)
  registry.workers['account']=old;registry.refresh()
  self.assertIs(registry.retiring['account'],old)
  contexts.append(SimpleNamespace(account_id='account',trading_style='scalp'))
  registry.refresh();self.assertNotIn('account',registry.workers)

class RuntimeStatusReporting(unittest.TestCase):
 def test_busy_worker_preserves_actual_last_check_and_staleness(self):
  from customer.scalp.registry import ScalpRegistry
  from types import SimpleNamespace
  from unittest.mock import patch
  lock=SimpleNamespace(acquire=lambda **kw:False)
  cached=dict(loaded=True,venue='READY',last_error=None,last_sync=100,entries_allowed=True)
  worker=SimpleNamespace(runtime=SimpleNamespace(lock=lock),cached=cached)
  registry=ScalpRegistry(None,None,None,None);registry.workers['a']=worker
  with patch('customer.scalp.registry.time.time',return_value=110):
   result=registry.runtime_info('a')
  self.assertFalse(result['loaded']);self.assertTrue(result['syncing'])
  self.assertEqual(result['venue'],'READY');self.assertIsNone(result['last_error'])
  self.assertFalse(result['entries_allowed'])
  with patch('customer.scalp.registry.time.time',return_value=131):
   result=registry.runtime_info('a')
  self.assertEqual(result['venue'],'RECONCILING');self.assertEqual(result['last_error'],'scalp_private_data_stale')
  worker.cached=dict(cached,venue='RECONCILING',last_error='actual_failure')
  with patch('customer.scalp.registry.time.time',return_value=110):
   self.assertEqual(registry.runtime_info('a')['last_error'],'actual_failure')
  self.assertIsNone(cached['last_error'])

class ScalpHistoryPagination(unittest.TestCase):
 def transport(self,page):
  from types import SimpleNamespace
  from customer.scalp.transport import ScalpYellowTransport
  client=SimpleNamespace(_page=page,asid='fixture-session')
  return ScalpYellowTransport(SimpleNamespace(ensure=lambda:client))
 def test_old_active_order_is_reached_beyond_200_newer_orders(self):
  from execution.transport import iter_pages
  calls=[]
  def page(path,params,cursor,size):
   calls.append((path,params,cursor,size));offset=int(cursor or 0)
   self.assertEqual(size,100)
   rows=[dict(order_id=str(i),client_order_id=str(i),market=M,state='canceled',amount='1') for i in range(offset,min(offset+size,351))]
   return dict(orders=rows,has_more=offset+size<351,next_cursor=str(offset+size))
  history=iter_pages(self.transport(page).get_orders,'account')
  match=next(order for order in history if order.client_order_id=='350')
  self.assertEqual(match.state,'cancelled');self.assertEqual(len(calls),4)
  self.assertEqual(calls[0][1]['app_session_id'],'fixture-session')
 def test_incomplete_or_stale_history_still_blocks(self):
  from execution.transport import VenueUnavailable
  for data in (dict(orders=[],has_more=True),dict(orders=[],stale=True),dict(orders=[],degraded=True)):
   with self.subTest(data=data),self.assertRaises(VenueUnavailable):
    self.transport(lambda *args:data).get_orders('account')

class ScalpFeedReception(unittest.TestCase):
 def test_reader_preserves_increment_sequence_and_services_ping_during_rest(self):
  from customer.scalp.registry import FeedInbox
  from types import SimpleNamespace
  import json
  sent=[];inbox=None
  messages=iter([json.dumps({'push':{'pub':{'data':{'sequence_num':i}}}}) for i in range(1,301)]+['{}'])
  def recv():
   try:return next(messages)
   except StopIteration:inbox.stop.set();raise TimeoutError()
  inbox=FeedInbox(SimpleNamespace(recv=recv,send=sent.append),TimeoutError)
  inbox.thread.start();inbox.thread.join(2)
  self.assertFalse(inbox.thread.is_alive());self.assertIsNone(inbox.error)
  self.assertEqual(sent,['{}'])
  self.assertEqual([d['sequence_num'] for d in inbox.batch()],list(range(1,301)))
 def test_stale_or_incomplete_feed_is_not_used(self):
  from customer.scalp.registry import FeedInbox
  from types import SimpleNamespace
  inbox=FeedInbox(SimpleNamespace(),TimeoutError,clock=lambda:100)
  inbox.items.put((80,{'sequence_num':1}))
  self.assertEqual(inbox.batch(),[{'sequence_num':1}])
  inbox.error=RuntimeError('scalp_feed_closed')
  with self.assertRaisesRegex(RuntimeError,'closed'):inbox.batch()

import copy,json,tempfile,unittest
from pathlib import Path
from paper.engine import Engine
from paper.feed import Feed,FeedError
from paper.record import Writer,freeze
from paper.replay import records,replay
R=Path(__file__).resolve().parents[1]
C=json.loads((R/'config.json').read_text());M=C['coins'][0]
F={m:dict(tick=.001,step=.001,min_qty=.001,min_notional=.01) for m in C['coins']}

def engine(strategy='candidate',**changes):
 c=copy.deepcopy(C);c.update(changes);e=Engine(c,c['scenarios']['paper_250ms_reference'],F,strategy);e.now=100;e.start=100;e.connected=True
 return e

def book(e,bid=100.03,ask=100.04,bq=10,aq=1,now=100):
 e.now=now;seq=e.feed.seq.get((M,'book'),0)+1
 d=dict(market=M,header={'type':'orderbook.snapshot' if (M,'book') not in e.feed.seq else 'orderbook.update'},sequence_num=seq,bids=[[bid,bq]],asks=[[ask,aq]])
 # A full replacement fixture, with explicit deleted levels for increments.
 if (M,'book') in e.feed.seq:
  d['bids'] += [[p,0] for p in e.feed.books[M]['bids'] if p!=bid]
  d['asks'] += [[p,0] for p in e.feed.books[M]['asks'] if p!=ask]
 e.receive(d,now);e.history[M].appendleft((now-31,100.))

def filled(e,qty=1):
 book(e);oid=e.place(M,True,100.03,qty);assert oid
 e.step(100.25);o=e.orders[oid];o['queue']=0
 e.trade(M,dict(price=100.03,amount=qty,ts=100.25,direction='sell'))
 return 'lot-'+oid

class Restrictions(unittest.TestCase):
 def test_agreement_both_directions_and_neutral(self):
  e=engine();book(e);self.assertIsNone(e.entry_reason(M,True));self.assertEqual(e.entry_reason(M,False),'momentum')
  book(e,bq=1,aq=10);self.assertEqual(e.entry_reason(M,True),'pressure')
  book(e,bid=99.96,ask=99.97,bq=1,aq=10);self.assertIsNone(e.entry_reason(M,False))
  book(e,bid=99.995,ask=100.005,bq=5,aq=5);self.assertEqual(e.entry_reason(M,True),'momentum')
 def test_spread_stale_source_age_and_disconnect(self):
  e=engine();book(e,ask=101);self.assertEqual(e.entry_reason(M,True),'spread')
  book(e);e.now=102;self.assertEqual(e.entry_reason(M,True),'stale_or_disconnected')
  e.now=100;e.feed.books[M]['source_time']=90;self.assertFalse(e.fresh(M))
  e.disconnect();self.assertIsNone(e.place(M,True,100,1))
 def test_one_entry_per_coin_includes_pending_partial_and_cancelling(self):
  e=engine();book(e);oid=e.place(M,True,100.03,1);self.assertIsNone(e.place(M,True,100.03,1))
  e.step(100.25);e.orders[oid]['queue']=0;e.trade(M,dict(price=100.03,amount=.4,ts=100.25,direction='sell'))
  e.cancel(e.orders[oid]);self.assertIsNone(e.place(M,True,100.03,1));e.step(100.5)
  self.assertAlmostEqual(e.lots['lot-'+oid]['qty'],.4);self.assertIsNone(e.place(M,True,100.03,1))
 def test_exposure_reservation_and_session_halt_no_reset(self):
  e=engine(max_exposure=50);book(e);self.assertIsNone(e.place(M,True,100.03,1))
  e=engine();lid=filled(e);e.realized=-4;e.step(100.3);self.assertTrue(e.halted)
  e.realized=0;e.step(100.4);self.assertTrue(e.halted);self.assertIn(lid,e.lots)
 def test_loss_cooldown(self):
  e=engine();lid=filled(e);o=dict(id='x',market=M,buy=False,price=99,qty=1,lot=lid,reason='stop');e.orders['x']=o;e.fill(o,1,99,True)
  self.assertEqual(e.entry_reason(M,True),'loss_cooldown');self.assertAlmostEqual(e.closed[0]['net'],-1.03-.01485)

class Execution(unittest.TestCase):
 def test_queue_partial_cancel_race_and_no_duplicate_volume(self):
  e=engine('existing_paper');book(e,bq=1);oid=e.place(M,True,100.03,1);e.last_quote[M]=100;e.step(100.25)
  e.trade(M,dict(price=100.03,amount=1.4,ts=100.25,direction='sell'));self.assertAlmostEqual(e.lots['lot-'+oid]['qty'],.4)
  e.cancel(e.orders[oid]);e.now=100.3;e.trade(M,dict(price=100.03,amount=.1,ts=100.3,direction='sell'));self.assertAlmostEqual(e.lots['lot-'+oid]['qty'],.5)
  e.step(100.5);self.assertNotIn(oid,e.orders)
 def test_no_fill_before_ack_or_on_historical_print(self):
  e=engine();book(e);oid=e.place(M,True,100.03,1)
  e.trade(M,dict(price=99,amount=100,ts=100,direction='sell'));self.assertFalse(e.lots)
  e.step(100.25);e.trade(M,dict(price=99,amount=100,ts=100,direction='sell'));self.assertFalse(e.lots)
 def test_post_only_rejected_on_delayed_arrival(self):
  e=engine();book(e);oid=e.place(M,True,100.03,1);book(e,bid=99.98,ask=100,now=100.2);e.step(100.25)
  self.assertNotIn(oid,e.orders);self.assertFalse(e.lots);self.assertEqual(e.count['post_only_reject'],1)
 def test_taker_depth_partial_fees_and_depth_not_reused(self):
  e=engine(maker_fee=.0001);lid=filled(e);book(e,bid=99.9,ask=99.91,bq=.3,now=100.3)
  e.step(100.3);e.step(100.6)
  self.assertAlmostEqual(e.lots[lid]['qty'],.7)
  self.assertAlmostEqual(e.fees,100.03*.0001+99.9*.3*.00015)
  e.step(100.9);self.assertAlmostEqual(e.lots[lid]['qty'],.7)
  book(e,bid=99.9,ask=99.91,bq=1,now=101);e.step(101);e.step(101.3)
  self.assertNotIn(lid,e.lots);self.assertAlmostEqual(e.closed[0]['net'],e.realized)
 def test_stop_and_time_exit_delays_and_no_duplicate_exits(self):
  e=engine();lid=filled(e);book(e,bid=99.9,ask=99.91,now=100.3);e.step(100.3)
  self.assertEqual(len([o for o in e.orders.values() if o['lot']==lid]),1)
  for _ in range(4):e.step(100.4)
  self.assertEqual(len([o for o in e.orders.values() if o['lot']==lid]),1)
  e.step(100.6);self.assertNotIn(lid,e.lots);self.assertAlmostEqual(e.exit_delays[-1],.3)
  e=engine();lid=filled(e);e.now=225;e.disconnect();e.step(225);self.assertEqual(e.lots[lid]['exit_reason'],'time')
  e.connect();book(e,now=226);e.step(226);e.step(226.3);self.assertNotIn(lid,e.lots);self.assertGreater(e.exit_delays[-1],1)
 def test_disconnect_keeps_unknown_orders_and_inventory_then_cancels(self):
  e=engine();lid=filled(e);e.step(100.3);self.assertTrue(e.orders);e.disconnect();self.assertIn(lid,e.lots);self.assertTrue(e.orders);self.assertTrue(e.unknown_orders)
  e.now=103;e.connect();e.step(103.3);self.assertFalse(e.orders);self.assertIn(lid,e.lots)
  book(e,now=104);e.step(104);self.assertTrue(e.orders);self.assertFalse(e.report()['valid_for_inference'])
 def test_unresolved_open_inventory_is_reported_not_counted_as_win(self):
  e=engine();lid=filled(e);r=e.report();self.assertIn(lid,r['unresolved_inventory']);self.assertEqual(r['closed']['closed_parents'],0);self.assertIsNone(r['closed']['expectancy']);self.assertLess(r['marked_net_before_funding'],0)
 def test_sequence_duplicates_snapshots_and_gap(self):
  f=Feed([M]);d=dict(market=M,header={'type':'orderbook.snapshot'},sequence_num=1,bids=[[100,2]],asks=[[101,2]])
  self.assertTrue(f.receive(d,1));self.assertIsNone(f.receive(d,2));self.assertEqual(f.books[M]['received'],1)
  with self.assertRaises(FeedError):f.receive(dict(d,header={'type':'orderbook.update'},sequence_num=3),3)
  t=dict(market=M,header={'type':'trades.snapshot'},sequence_num=9,trades=[{'bad':'historical'}]);self.assertEqual(f.receive(t,4)[2],[])
 def test_slow_scenario_applies_to_order_and_cancel(self):
  e=engine();e.s=e.c['scenarios']['slow_7s_disconnect'];book(e);oid=e.place(M,True,100.03,1)
  self.assertEqual(e.orders[oid]['ready'],107);e.cancel(e.orders[oid]);self.assertEqual(e.orders[oid]['cancel_at'],107)
 def test_baseline_entry_and_exit_methods_are_original(self):
  from paper.legacy import BASE
  e=engine('existing_paper');book(e)
  for buy in (True,False):self.assertEqual(e.entry_reason(M,buy),e.legacy.entry_reason(M,buy))
  l=dict(market=M,buy=True,entry=100,qty=1,opened=99,trigger=None)
  p,reason=e.legacy.exit_price(l);self.assertEqual(e.exit_decision(l),('maker',p,reason))

class Recording(unittest.TestCase):
 def test_hash_chain_detects_tampering(self):
  with tempfile.TemporaryDirectory() as temp:
   d=Path(temp);w=Writer(d);w.write('start');w.write('end');w.close();self.assertEqual(len(list(records(d))),2)
   p=next(d.glob('*.jsonl'));p.write_text(p.read_text().replace('"start"','"altered"'))
   with self.assertRaisesRegex(ValueError,'hash mismatch'):list(records(d))
 def test_full_replay_identical_data_and_sizing(self):
  with tempfile.TemporaryDirectory() as temp:
   d=Path(temp)/'run';freeze(d,C);w=Writer(d);w.write('start');w.write('metadata',filters=F);w.write('connect');w.write('data',data=dict(market=M,header={'type':'orderbook.snapshot'},sequence_num=1,bids=[[100,1]],asks=[[100.01,1]]));w.write('end');w.close()
   a=replay(d,Path(temp)/'a');b=replay(d,Path(temp)/'b');self.assertEqual(a,b);self.assertEqual(len(a['results']),12)
if __name__=='__main__':unittest.main()

class MoreSafety(unittest.TestCase):
 def test_missing_offline_fill_confirmation_blocks_reentry(self):
  e=engine();book(e);e.place(M,True,100.03,1);e.disconnect();e.now=102;e.connect();book(e,now=102);e.step(102.3)
  self.assertEqual(e.entry_reason(M,True),'unresolved_offline_fills')
  self.assertGreater(e.report()['unresolved_possible_notional'],0)
 def test_source_timestamp_is_used(self):
  f=Feed([M]);d=dict(market=M,header={'type':'orderbook.snapshot','created_at':'1970-01-01T00:00:10.000000000Z'},sequence_num=1,bids=[[100,2]],asks=[[101,2]])
  f.receive(d,20);self.assertEqual(f.books[M]['source_time'],10)
 def test_partial_target_cancellation_does_not_overclose(self):
  e=engine();lid=filled(e);e.step(100.3);maker=next(o for o in e.orders.values() if o['lot']==lid);maker['queue']=0;e.step(100.6)
  maker['queue']=0;e.trade(M,dict(price=maker['price'],amount=.4,ts=100.6,direction='buy'))
  book(e,bid=99.9,ask=99.91,now=100.7);e.step(100.7);e.step(101);e.step(101.3)
  self.assertFalse(e.lots);self.assertAlmostEqual(sum(ev['qty'] for ev in e.events if ev['kind']=='exit_fill'),1)

import unittest
from unittest.mock import patch,MagicMock
from types import SimpleNamespace
from tests.execution.test_scalp_live import LiveBoundary,StrategyParity,M,lot,book
from customer.accounts import AccountService,AccountError
from customer.scalp.strategy import LiveStrategy
from customer.scalp.availability import SCALP_LIVE_AVAILABLE

class Unavailable(unittest.TestCase):
 setUp=LiveBoundary.setUp
 intent=LiveBoundary.intent
 def test_entry_block_at_transport_boundary(self):
  self.assertFalse(SCALP_LIVE_AVAILABLE)
  with self.assertRaisesRegex(ValueError,'scalp_coming_soon'):self.r.submit(self.intent())
  self.assertEqual(self.mock.place_calls,0)
 def test_model_cannot_generate_entry(self):
  self.r.model.allow_entries=True
  self.assertIsNone(self.r.model.place(M,True,100,1))
 def test_pending_entry_cancelled_without_switch(self):
  with patch('customer.scalp.availability.SCALP_LIVE_AVAILABLE',True):self.r.submit(self.intent())
  self.r.frame()
  self.assertTrue(all(o['state']=='cancelled' for o in self.r.store.orders()))
  self.assertEqual(self.r.ctx.trading_style,'scalp')
  self.assertFalse(self.r.model.allow_entries)
 def test_existing_fills_reconcile_and_exit_idempotently(self):
  with patch('customer.scalp.availability.SCALP_LIVE_AVAILABLE',True):self.r.submit(self.intent())
  order=self.r.store.orders()[0];self.mock.fill(order['venue_id'],'1','100',trade_id='existing')
  self.r.reconcile();self.r.reconcile();self.assertEqual(len(self.r.model.lots),1)
  lid=next(iter(self.r.model.lots))
  p=dict(kind='maker',id='existing-exit',market=M,buy=False,price=101,qty=1,lot=lid,requested=self.clock())
  self.r.submit(p);self.r.submit(p)
  self.assertEqual(self.mock.place_calls,2)
 def test_selection_denied_without_database_write(self):
  svc=object.__new__(AccountService)
  with self.assertRaisesRegex(AccountError,'scalp_coming_soon'):svc.set_trading_style(None,'scalp')
 def test_activation_denied_without_write_or_style_change(self):
  svc=object.__new__(AccountService);svc.db=MagicMock()
  c=svc.db.service.return_value.__enter__.return_value
  cfg={'trading_style':'scalp'};c.execute.return_value.fetchone.return_value=cfg
  svc._get_owned=lambda *a:{'id':'fixture','state':'PAUSED','verification_state':'VERIFIED'}
  with self.assertRaisesRegex(AccountError,'scalp_coming_soon'):svc.activate(SimpleNamespace(user_id='fixture'),'scalp')
  self.assertFalse(any('update ' in call.args[0].lower() for call in c.execute.call_args_list))
  self.assertEqual(cfg,{'trading_style':'scalp'})

# Existing exit behavior must continue unchanged while entry availability is false.
for name in ('test_stop_rests_free_maker_close_at_touch_then_bounded_taker','test_maker_close_follows_the_touch','test_further_adverse_move_turns_taker_before_grace_ends','test_time_limit_closes_maker_first_then_taker_and_target_waits'):
 setattr(Unavailable,name,getattr(StrategyParity,name))
Unavailable.stopped=StrategyParity.stopped
Unavailable.iocs=StrategyParity.iocs

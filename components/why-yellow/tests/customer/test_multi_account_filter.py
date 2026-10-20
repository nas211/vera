import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from customer.live_entry_filter import AccountEntryFilters, OWNER_ACCOUNT, JP_ACCOUNT, INTERVALS
from customer.tenants import TenantRegistry
from tests.customer.test_live_entry_filter import FilterTests, history

class MultiAccountTests(unittest.TestCase):
    def setUp(self):
        self.fixture=FilterTests();self.fixture.setUp()
        self.gates=AccountEntryFilters((OWNER_ACCOUNT,JP_ACCOUNT),self.fixture.clock)
        self.reg=TenantRegistry(None,None,None,self.fixture.fresh,'/unused',entry_filter=self.gates)
        self.tenants={}
        for aid in (OWNER_ACCOUNT,JP_ACCOUNT):
            t=SimpleNamespace(ctx=SimpleNamespace(account_id=aid,venue='yellow',entries_wanted=True),
                              submit=Mock(return_value=SimpleNamespace(status='ACKNOWLEDGED',reason='')),last_signal=None)
            self.tenants[aid]=t
        self.reg.tenants=self.tenants

    def test_long_and_short_confirm_independently_with_owner_paused(self):
        for direction in ('long','short'):
            with self.subTest(direction=direction):
                self.setUp()
                self.tenants[OWNER_ACCOUNT].ctx.entries_wanted=False
                for interval in INTERVALS:
                    self.gates.seed(self.fixture.market,interval,history(self.fixture.clock(),interval,direction))
                out=self.reg.dispatch(self.fixture.sig(direction))
                self.assertEqual(out[JP_ACCOUNT],'FILTERED:waiting_for_recovery_close')
                self.assertEqual(out[OWNER_ACCOUNT],'SKIPPED:entries_not_wanted')
                self.fixture.gate=self.gates.filters[JP_ACCOUNT]
                self.fixture.recover(direction)
                out=self.reg.dispatch_confirmed()
                self.assertEqual(out,{JP_ACCOUNT+':'+self.fixture.market:'ACKNOWLEDGED'})
                self.tenants[OWNER_ACCOUNT].submit.assert_not_called()
                self.tenants[JP_ACCOUNT].submit.assert_called_once()
                self.assertEqual(self.reg.dispatch_confirmed(),{})

    def test_pausing_one_account_does_not_cancel_other_pending(self):
        for gate in self.gates.filters.values():gate.pending[self.fixture.market]=self.fixture.sig()
        self.tenants[OWNER_ACCOUNT].ctx.entries_wanted=False
        self.reg.dispatch_confirmed()
        self.assertFalse(self.gates.filters[OWNER_ACCOUNT].pending)
        self.assertTrue(self.gates.filters[JP_ACCOUNT].pending)

    def test_both_accounts_receive_their_own_confirmed_signal_once(self):
        sig=self.fixture.sig()
        for gate in self.gates.filters.values():gate.ready=Mock(side_effect=[[sig],[]])
        self.assertEqual(len(self.reg.dispatch_confirmed()),2)
        self.assertEqual(self.reg.dispatch_confirmed(),{})
        for t in self.tenants.values():t.submit.assert_called_once_with(sig)

    def test_revert_blocked_for_both_and_no_unrelated_account_opt_in(self):
        out=self.reg.dispatch(self.fixture.sig(strategy='WY-REVERT'))
        self.assertEqual(set(out.values()),{'FILTERED:revert_entries_disabled'})
        for t in self.tenants.values():t.submit.assert_not_called()
        self.assertFalse(self.gates.applies(SimpleNamespace(account_id='future-account',venue='yellow')))
        self.assertFalse(self.gates.applies(SimpleNamespace(account_id=JP_ACCOUNT,venue='weex')))

    def test_cancel_and_disconnect_reach_every_account(self):
        for gate in self.gates.filters.values():gate.pending[self.fixture.market]=self.fixture.sig()
        self.gates.cancel(self.fixture.market)
        self.assertTrue(all(not g.pending for g in self.gates.filters.values()))
        for interval in INTERVALS:self.gates.seed(self.fixture.market,interval,history(self.fixture.clock(),interval))
        self.assertTrue(self.gates.history_ready(self.fixture.market,'1h'))
        self.gates.disconnect()
        self.assertTrue(all(not g.bars for g in self.gates.filters.values()))
        self.assertFalse(self.gates.history_ready(self.fixture.market,'1h'))

    def test_public_status_identifies_both_accounts(self):
        status=self.gates.status()
        self.assertEqual(set(status['account_ids']),{OWNER_ACCOUNT,JP_ACCOUNT})
        self.assertEqual(status['by_account'][JP_ACCOUNT]['account_id'],JP_ACCOUNT)

import unittest
from customer.paper_validation import evaluate


class PaperValidationTests(unittest.TestCase):
    def records(self, side='long', move=1):
        return [dict(id=str(i),phase='FORWARD_PAPER',valid=True,fee_model_version=2,
                     reason='TARGET',qty=1,entry=100,exit=100+move*(1 if side=='long' else -1),
                     fees=.08,funding=0,net=move-.08,opened=100000+i*1800,closed=100060+i*1800)
                for i in range(50)]

    def test_reconciled_forward_trades_pass_both_sides(self):
        for side in ('long','short'):
            r=evaluate(self.records(side),side)
            self.assertTrue(r['qualified'],r)
            self.assertLess(float(r['stress_net']),50*.92)

    def test_fee_and_slippage_remove_apparent_profit(self):
        self.assertFalse(evaluate(self.records(move=.1),'long')['qualified'])

    def test_missing_malformed_duplicate_or_fake_net_never_qualifies(self):
        for change in ({'net':999},{'fees':float('nan')},{'funding':None},{'id':'1'},
                       {'closed':-1},{'qty':-1}):
            rows=self.records();rows[0].update(change)
            self.assertFalse(evaluate(rows,'long')['qualified'])

    def test_screening_legacy_and_short_runs_do_not_qualify(self):
        for field,value in [('phase','PAPER_SCREEN'),('fee_model_version',1),('valid',False),('reason','OWNER_CLOSE')]:
            rows=self.records()
            for row in rows:row[field]=value
            self.assertFalse(evaluate(rows,'long')['qualified'])
        self.assertFalse(evaluate(self.records()[:49],'long')['qualified'])
        rows=self.records()
        for i,row in enumerate(rows):row.update(opened=100+i,closed=101+i)
        self.assertFalse(evaluate(rows,'long')['qualified'])

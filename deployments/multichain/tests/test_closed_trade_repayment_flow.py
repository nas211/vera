"""Actual paper fills/closures -> authorized sweep -> accrued interest -> principal.

Uses an isolated ledger and synthetic quotes. No live orders or wallet signatures.
"""
import json
from decimal import Decimal as D

import pytest

from autodefi.lending import down
from autodefi.repayment import Repayment
from conftest import funded, fill
from vela.db import get, transaction


@pytest.mark.parametrize('side', ['long', 'short'])
@pytest.mark.parametrize('case', ['profit_all', 'profit_half', 'interest_only', 'loss', 'no_consent'])
def test_closed_trade_pays_accrued_interest_before_principal(env, side, case):
    funded(env)
    db, owner, paper = env['db'], env['owner'], env['paper']
    repay = Repayment(db, paper)
    interest = D('2') if case == 'interest_only' else D('.25')
    loan = repay.lending.loan(owner)
    elapsed = interest * D(31536000) / (D(loan['principal']) * D(loan['apr']))
    db.execute('UPDATE loans SET last=? WHERE id=?', (loan['last']-float(elapsed), loan['id']))
    before_debt = repay.lending.debt(repay.lending.loan(owner))[0]
    assert before_debt == D(800) + interest
    percent = D(50) if case == 'profit_half' else D(100)
    if case != 'no_consent':
        with transaction(db):
            repay.configure(owner, dict(confirm=True, sweepPercent=str(percent), frequency='off', minimum='0'))
    position = fill(env, side)
    market = env['feed'].markets['BTC/USD']
    profitable = case != 'loss'
    move = D('1.01') if (side == 'long') == profitable else D('.99')
    market.mark *= move; market.index = market.mark
    market.bids = {market.mark-D('.01'):D(10000)}
    market.asks = {market.mark+D('.01'):D(10000)}
    repay.tick()
    assert repay.lending.loan(owner)['paid'] == '0'  # Open gains cannot repay debt.
    with transaction(db):
        paper.close(position['id'], 'requested-flow-audit')
    assert not paper.getPosition(owner)
    net = repay.realized_net(owner)
    assert (net > 0) is profitable
    before_cash = D(paper.snapshot(owner)['balance'])
    repay.tick()
    after = repay.lending.loan(owner)
    paid = D(after['paid'])
    expected = down(net * percent / 100) if profitable and case != 'no_consent' else D(0)
    assert paid == expected
    interest_paid = min(expected, interest)
    principal_paid = expected-interest_paid
    assert D(get(db, 'pool')['interestIncome']) == interest_paid
    assert D(after['principal']) == D(800)-principal_paid
    assert repay.lending.debt(after)[0] == before_debt-paid
    assert D(paper.snapshot(owner)['balance']) == before_cash-paid
    assert D(paper.snapshot(owner)['pnl']) == net
    if paid:
        assert D(after['interest']) == interest-interest_paid
        receipt = db.execute("SELECT detail FROM loan_events WHERE owner=? AND kind='SIMULATED_REPAYMENT_RECEIPT'", (owner,)).fetchone()
        assert receipt and json.loads(receipt[0])['method'] == 'profit_sweep'
    # Repeated worker ticks cannot debit the same close twice.
    repay.tick()
    assert D(repay.lending.loan(owner)['paid']) == paid
    assert D(paper.snapshot(owner)['balance']) == before_cash-paid
    print(json.dumps(dict(side=side,case=case,loanPrincipalBefore='800',accruedInterest=str(interest),
        realizedNetProfit=str(net),sweepPercent=str(percent) if case!='no_consent' else 'disabled',
        repayment=str(paid),interestPaid=str(interest_paid),principalPaid=str(principal_paid),
        remainingDebt=str(repay.lending.debt(after)[0]),environment='LOCAL_PAPER_DEMO')))

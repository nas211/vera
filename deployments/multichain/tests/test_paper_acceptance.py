"""Deterministic paper-only acceptance; all prices and wallets are fixtures."""
import time
from decimal import Decimal as D
import pytest
from vela.db import get,transaction
from vela.paper import money
from conftest import funded,fill,order


def move_market(env,price):
    market=env['feed'].markets['BTC/USD']
    market.mark=market.index=D(price)
    market.bids={D(price)-D('.01'):D(10000)}
    market.asks={D(price)+D('.01'):D(10000)}
    return market


@pytest.mark.parametrize('side',['long','short'])
def test_limit_waits_for_depth_then_fills_with_protected_position(env,side):
    funded(env);paper=env['paper'];db=env['db']
    limit=D(59900 if side=='long' else 60100)
    payload={**order(side),'type':'limit','limitPrice':str(limit)}
    with transaction(db):pending=paper.submit(env['owner'],payload,'fixture-limit')
    db.execute('UPDATE paper_orders SET due=0')
    paper.tick()
    assert not paper.getPosition(env['owner'])
    assert db.execute('SELECT status FROM paper_orders WHERE id=?',(pending['id'],)).fetchone()[0]=='pending'
    move_market(env,limit+(-100 if side=='long' else 100))
    paper.tick()
    position=paper.getPosition(env['owner'])[0]
    assert D(position['entry'])==limit
    sign=D(1 if side=='long' else -1)
    budget=(limit-D(position['stop']))*D(position['qty'])*sign+D(position['paid_fees'])+D(position['stop'])*D(position['qty'])*D('.0004')
    assert budget<=2


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('reason',['target','stop','liquidation'])
def test_protective_exit_fills_and_reconciles_portfolio(env,side,reason):
    funded(env);position=fill(env,side);paper=env['paper'];db=env['db']
    sign=D(1 if side=='long' else -1)
    price=(D(position[reason])+sign*(10 if reason=='target' else -10)
           if reason!='liquidation' else D(10 if side=='long' else 120000))
    move_market(env,price)
    paper.tick()
    assert paper.getPosition(env['owner'])  # Risk exit respects simulated latency.
    db.execute('UPDATE paper_orders SET due=0')
    paper.tick()
    assert not paper.getPosition(env['owner'])
    snap=paper.snapshot(env['owner']);trade=snap['trades'][0]
    assert trade['reason']==reason and trade['chain_status']=='offchain'
    assert D(trade['pnl'])==D(trade['gross'])-D(trade['fees'])
    assert D(snap['pnl'])==D(trade['pnl'])
    assert snap['today']['trades']==snap['totals']['trades']==1
    assert snap['today']['wins']==int(D(trade['pnl'])>0)
    assert snap['today']['losses']==int(D(trade['pnl'])<0)


@pytest.mark.parametrize('side',['long','short'])
def test_funding_partial_exits_and_unknown_intervals(env,side):
    funded(env);position=fill(env,side);paper=env['paper'];db=env['db']
    market=env['feed'].markets['BTC/USD'];market.funding_rate=D('.001')
    before=D(paper.snapshot(env['owner'])['balance']);now=time.time()
    expected=market.index*D(position['qty'])*market.funding_rate*D(2)/3600*(1 if side=='long' else -1)
    with transaction(db):paper.funding(now+2)
    assert D(paper.meta(position)['fundingTotal'])==expected
    assert D(paper.snapshot(env['owner'])['balance'])==before-expected
    with transaction(db):first=paper.close(position['id'],'fixture_partial',D('.5'))
    with transaction(db):last=paper.close(position['id'],'fixture_close')
    allocated=sum(D(get(db,'paper-trade:'+trade['id'])['funding']) for trade in (first,last))
    assert allocated==expected
    assert sum(D(trade['pnl']) for trade in (first,last))==sum(D(trade['gross'])-D(trade['fees']) for trade in (first,last))-expected
    assert D(paper.snapshot(env['owner'])['pnl'])==money(sum(D(trade['pnl']) for trade in (first,last)))
    another=fill(env,side);before=D(paper.snapshot(env['owner'])['balance'])
    with transaction(db):paper.funding(now+10)
    assert D(paper.snapshot(env['owner'])['balance'])==before
    assert paper.meta(another)['fundingGapSeconds']==10
    assert paper.snapshot(env['owner'])['fundingIncomplete'] is True

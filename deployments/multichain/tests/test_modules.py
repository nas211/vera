"""Deterministic local ledger/market fixtures, not wallet or testnet verification."""
import calendar
import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D
import pytest
from vela.db import transaction, get, put
from autodefi.store import once, account
from autodefi.repayment import Repayment, TradingCapital, next_due
from autodefi.orchestration import AgentOrchestrator
from conftest import funded, fill, save_vault, ALICE, BOB


def service(e): return Repayment(e['db'], e['paper'])
def pay(e, value, key='repayment-1'):
    return once(e['db'], e['owner'], key, {'amount':str(value)}, lambda:service(e).pay(e['owner'], str(value)))
def configure(e, **kw):
    body=dict(confirm=True, sweepPercent='50', frequency='off', source='trading', minimum='0', days=30, **kw)
    with transaction(e['db']): return service(e).configure(e['owner'],body)


def test_vault_only_has_no_debt_or_trading(env):
    env['v'].update(loanState=0, withdrawable=env['v']['collateral'])
    save_vault(env['db'],env['v'])
    assert env['lending'].loan(env['owner']) is None
    assert env['paper'].snapshot(env['owner'])['balance']=='0'
    assert env['lending'].health(env['owner'])['withdrawableDoge']=='10000'
    assert service(env).status(env['owner'])['maxRepayable']=='0'


def test_borrow_only_partial_full_repayment_and_health(env):
    funded(env)
    before=env['lending'].health(env['owner'])
    receipt=pay(env,200)
    assert D(receipt['loan']['principal'])==600
    assert D(receipt['health']['ltv'])<D(before['ltv'])
    assert D(receipt['health']['healthFactor'])>D(before['healthFactor'])
    assert receipt['loan']['state']=='ACTIVE' and receipt['simulated']
    receipt=pay(env,600,'repayment-full')
    assert receipt['loan']['state']=='AWAITING_CHAIN'
    assert D(receipt['health']['debt'])==0
    assert receipt['health']['withdrawableDoge']=='0' # Only chain unlocks collateral.
    assert env['paper'].snapshot(env['owner'])['pnl']=='0.000000'


def test_interest_first_and_duplicate_repayment(env):
    funded(env);env['db'].execute("UPDATE loans SET interest='8'")
    receipt=pay(env,108)
    assert receipt==pay(env,108)
    assert D(receipt['loan']['principal'])==700 and D(receipt['loan']['interest'])==0
    assert D(env['paper'].snapshot(env['owner'])['balance'])==692
    assert D(get(env['db'],'pool')['interestIncome'])==8
    with pytest.raises(ValueError,match='conflict'):pay(env,109)


def test_loss_does_not_block_partial_repayment_or_erase_debt(env):
    funded(env);env['db'].execute("UPDATE users SET balance='500'")
    receipt=pay(env,300)
    assert D(receipt['loan']['principal'])==500
    assert D(env['paper'].snapshot(env['owner'])['balance'])==200
    assert D(env['paper'].snapshot(env['owner'])['pnl'])==-300
    with pytest.raises(ValueError,match='Maximum repayable is 200'):pay(env,201,'too-large-payment')


@pytest.mark.parametrize('side',['long','short'])
def test_partial_repayment_reserves_active_margin_and_pending_entries(env,side):
    funded(env);position=fill(env,side)
    s=service(env);free,_=s.free(env['owner'])
    margin=D(position['entry'])*D(position['qty'])/D(position['leverage'])
    assert 0<free<D(800)-margin
    assert free<D(env['paper'].snapshot(env['owner'])['available'])
    pay(env,100)
    assert env['paper'].getPosition(env['owner'])[0]['qty']==position['qty']
    with pytest.raises(ValueError,match='Maximum repayable'):pay(env,700,'margin-protected')
    env['feed'].markets['BTC/USD'].book_at=0
    assert s.free(env['owner'])[0]==0


def test_unrealized_gains_never_finance_repayment(env):
    funded(env);fill(env)
    p=env['paper'].getPosition(env['owner'])[0]
    ceiling=D(800)-D(p['qty'])*D(p['entry'])/D(p['leverage'])-D(p['entry_fee_left'])
    env['feed'].markets['BTC/USD'].mark=D(70000)
    assert service(env).free(env['owner'])[0]<ceiling


def profit_close(e, price=60600):
    p=fill(e);market=e['feed'].markets['BTC/USD']
    market.mark=D(price);market.bids={D(price)-D('.01'):D(10000)};market.asks={D(price)+D('.01'):D(10000)}
    with transaction(e['db']):e['paper'].close(p['id'],'profit-fixture')


def test_profit_sweep_net_costs_watermark_and_no_duplicate(env):
    funded(env);configure(env)
    profit_close(env)
    s=service(env);net=s.realized_net(env['owner'])
    before=D(env['lending'].loan(env['owner'])['principal'])
    s.tick();after=D(env['lending'].loan(env['owner'])['principal'])
    assert before-after==(net/2).quantize(D('.000001'),rounding='ROUND_FLOOR')
    s.tick();assert D(env['lending'].loan(env['owner'])['principal'])==after
    # Revocation / reauthorization cannot sweep the same realized profit.
    with transaction(env['db']):s.disable(env['owner'],'owner')
    configure(env);s.tick()
    assert D(env['lending'].loan(env['owner'])['principal'])==after


def test_profit_sweep_excludes_unrealized_and_deducts_funding(env):
    funded(env);configure(env);fill(env)
    env['feed'].markets['BTC/USD'].mark=D(61000)
    service(env).tick();assert env['lending'].loan(env['owner'])['paid']=='0'
    with transaction(env['db']):
        row=dict(env['db'].execute('SELECT * FROM users WHERE address=?',(env['owner'],)).fetchone())
        env['paper'].costs(row,'funding-fixture',funding=D(20))
    assert service(env).realized_net(env['owner'])<0
    service(env).tick();assert env['lending'].loan(env['owner'])['paid']=='0'


def test_scheduled_repayment_once_skips_insufficient_and_keeps_minimum(env,monkeypatch):
    funded(env);s=service(env)
    with transaction(env['db']):policy=s.configure(env['owner'],dict(confirm=True,frequency='daily',maximum='100',minimum='650',sweepPercent='0'))
    monkeypatch.setattr(time,'time',lambda:policy['nextDue'])
    s.tick();paid=D(env['lending'].loan(env['owner'])['paid'])
    assert paid==100 and D(env['paper'].snapshot(env['owner'])['balance'])==700
    s.tick();assert D(env['lending'].loan(env['owner'])['paid'])==paid
    monkeypatch.setattr(time,'time',lambda:policy['nextDue']+86400)
    s.tick();assert D(env['lending'].loan(env['owner'])['paid'])==paid
    assert env['db'].execute("SELECT 1 FROM loan_events WHERE kind='SCHEDULED_REPAYMENT_SKIPPED'").fetchone()


def test_calendar_schedule_expiry_and_revocation(env,monkeypatch):
    jan=datetime(2028,1,31,12,tzinfo=timezone.utc).timestamp()
    feb=next_due(jan,'monthly',jan,31)
    assert datetime.fromtimestamp(feb,timezone.utc).day==29
    march=next_due(feb,'monthly',feb,31)
    assert datetime.fromtimestamp(march,timezone.utc).day==31
    assert next_due(jan,'weekly',jan)==jan+604800
    funded(env);p=configure(env)
    monkeypatch.setattr(time,'time',lambda:p['expiresAt'])
    service(env).tick()
    assert get(env['db'],'repayment-policy:'+env['owner'])['enabled'] is False


def test_direct_ai_trade_without_vault_borrow_or_shared_accounts(env):
    db=env['db'];owner=env['owner'];paper=env['paper'];capital=TradingCapital(db,paper)
    db.execute('DELETE FROM vaults');db.execute("DELETE FROM kv WHERE key LIKE 'quote:%'")
    with transaction(db):
        capital.allocate(owner,'1000',True)
        paper.configure(owner,dict(confirm=True,allocation='1000',positionSize='150',maxLoss='1',leverage='1'))
        put(db,'paper-prefs:'+owner,dict(autonomous=True,generation=1))
    assert paper.gate(owner)['budget']==1
    from conftest import order
    with transaction(db):paper.submit(owner,{**order(),'riskBudget':'1'},'direct-paper-order')
    db.execute('UPDATE paper_orders SET due=0');paper.tick()
    assert len(paper.getPosition(owner))==1 and env['lending'].loan(owner) is None
    with transaction(db):capital.withdraw(owner,'100',True)
    assert D(paper.snapshot(owner)['balance'])==900
    with transaction(db):paper.settle(owner,True)
    db.execute('UPDATE paper_orders SET due=0');paper.tick();paper.settle_tick()
    assert not paper.getPosition(owner)
    assert not get(db,'direct-settlement:'+owner)
    assert db.execute('SELECT count(*) FROM users').fetchone()[0]==1


def test_direct_allocation_cannot_rescue_debt_or_bypass_risk(env):
    funded(env);capital=TradingCapital(env['db'],env['paper'])
    with pytest.raises(ValueError,match='existing loan'):
        with transaction(env['db']):capital.allocate(env['owner'],'100',True)
    with pytest.raises(ValueError,match='Repay'):
        with transaction(env['db']):capital.withdraw(env['owner'],'100',True)
    with pytest.raises(ValueError,match='stop budget'):
        with transaction(env['db']):env['paper'].configure(env['owner'],dict(confirm=True,allocation='800',maxLoss='3'))
    env['v']['limits']['maxLoss']='500000';save_vault(env['db'],env['v'])
    with transaction(env['db']):env['paper'].configure(env['owner'],dict(confirm=True,allocation='800',maxLoss='2'))
    assert env['paper'].gate(env['owner'],require_running=False)['budget']==D('.5')


def test_agent_mode_uses_shared_ledger_and_authorized_settlement(env):
    funded(env);s=service(env);agent=AgentOrchestrator(env['db'],env['paper'],s)
    configure(env);assert agent.status(env['owner'])['loan']['id']==env['lending'].loan(env['owner'])['id']
    with pytest.raises(ValueError,match='Confirm'):
        with transaction(env['db']):agent.settle(env['owner'],False)
    assert get(env['db'],'repayment-policy:'+env['owner'])['enabled']
    pay(env,100)
    with transaction(env['db']):agent.settle(env['owner'],True)
    env['paper'].settle_tick()
    assert env['lending'].loan(env['owner'])['state']=='AWAITING_CHAIN'
    assert not get(env['db'],'repayment-policy:'+env['owner'])['enabled']


def test_atomic_rollback_and_account_isolation(env):
    funded(env);db=env['db'];owner=BOB.address.lower()
    with transaction(db):account(db,owner)
    before=D(env['paper'].snapshot(env['owner'])['balance'])
    with pytest.raises(RuntimeError):
        with transaction(db):
            service(env).pay(env['owner'],'100')
            raise RuntimeError('simulated crash before commit')
    assert D(env['paper'].snapshot(env['owner'])['balance'])==before
    assert env['lending'].loan(env['owner'])['paid']=='0'
    with pytest.raises(ValueError,match='No repayable loan'):
        with transaction(db):service(env).pay(owner,'1')
    assert env['paper'].snapshot(owner)['balance']=='0'


def test_sweep_stops_at_zero_debt_and_permissions_are_bounded(env):
    funded(env);s=service(env)
    with pytest.raises(ValueError,match='authorization'):
        with transaction(env['db']):s.configure(env['owner'],dict(sweepPercent='50'))
    with pytest.raises(ValueError,match='Only available'):
        with transaction(env['db']):s.configure(env['owner'],dict(confirm=True,source='wallet'))
    configure(env)
    pay(env,800)
    assert not get(env['db'],'repayment-policy:'+env['owner'])['enabled']
    s.tick();assert env['lending'].loan(env['owner'])['paid']=='800'


def test_pending_order_reserves_funds_before_fill(env):
    from conftest import order
    funded(env)
    with transaction(env['db']):env['paper'].submit(env['owner'],order(),'pending-repayment-margin')
    assert service(env).free(env['owner'])[0]<700
    with pytest.raises(ValueError,match='Maximum repayable'):pay(env,701)
    pay(env,500,'pay-with-pending')
    env['db'].execute('UPDATE paper_orders SET due=0');env['paper'].tick()
    assert env['paper'].getPosition(env['owner'])


@pytest.mark.parametrize('frequency',['daily','weekly','monthly'])
def test_all_schedule_frequencies_execute_once_after_restart(env,monkeypatch,frequency):
    funded(env);s=service(env)
    with transaction(env['db']):p=s.configure(env['owner'],dict(confirm=True,frequency=frequency,maximum='20',minimum='10',days=90))
    monkeypatch.setattr(time,'time',lambda:p['nextDue'])
    service(env).tick();service(env).tick()
    assert D(env['lending'].loan(env['owner'])['paid'])==20
    assert get(env['db'],'repayment-policy:'+env['owner'])['nextDue']>p['nextDue']


def test_full_repayment_with_open_positions_can_still_close_them(env):
    funded(env);fill(env)
    # Existing independently allocated cash may coexist in the single ledger.
    from autodefi.store import cashflow
    with transaction(env['db']):cashflow(env['db'],env['owner'],D(300))
    with transaction(env['db']):service(env).pay(env['owner'],'800.01',full=True)
    assert env['lending'].loan(env['owner'])['state']=='AWAITING_CHAIN'
    with transaction(env['db']):env['paper'].settle(env['owner'],True)
    env['db'].execute('UPDATE paper_orders SET due=0');env['paper'].tick();env['paper'].settle_tick()
    assert not env['paper'].getPosition(env['owner'])
    assert D(env['lending'].loan(env['owner'])['paid'])==800


def test_deferred_profit_cannot_sweep_capital_after_losses(env):
    funded(env)
    with transaction(env['db']):service(env).configure(env['owner'],dict(confirm=True,sweepPercent='100',minimum='900'))
    profit_close(env)
    service(env).tick()
    policy=get(env['db'],'repayment-policy:'+env['owner'])
    assert D(policy['pendingProfit'])>0 and env['lending'].loan(env['owner'])['paid']=='0'
    # A funding loss consumes the profit before free margin becomes available.
    row=dict(env['db'].execute('SELECT * FROM users WHERE address=?',(env['owner'],)).fetchone())
    with transaction(env['db']):
        env['paper'].costs(row,'funding-loss',funding=D(20))
        policy['minimum']='0';put(env['db'],'repayment-policy:'+env['owner'],policy)
    service(env).tick()
    assert env['lending'].loan(env['owner'])['paid']=='0'


def test_duplicate_repayments_concurrent_connections(env):
    import threading
    from autodefi.store import open_db
    from autodefi.trading import GovernedPaper
    funded(env);errors=[]
    def attempt():
        db=open_db(env['config'].db_path)
        try:
            paper=GovernedPaper(db,env['config'].paper(),env['feed'])
            once(db,env['owner'],'concurrent-repay',{'amount':'100'},lambda:Repayment(db,paper).pay(env['owner'],'100'))
        except Exception as exc:errors.append(exc)
        finally:db.close()
    workers=[threading.Thread(target=attempt) for _ in range(3)]
    for t in workers:t.start()
    for t in workers:t.join()
    assert not errors
    assert env['lending'].loan(env['owner'])['paid']=='100'


def test_financed_permission_displays_stricter_limits_and_expires(env):
    funded(env);owner=env['owner'];paper=env['paper']
    with transaction(env['db']):paper.configure(owner,dict(confirm=True,allocation='400',positionSize='50',maxLoss='.5',leverage='1'))
    assert D(paper.permission(owner)['allocation'])==400000000
    assert D(paper.permission(owner)['maxLoss'])==500000
    extra=get(env['db'],'trading-permission:'+owner);extra['expiresAt']='0'
    put(env['db'],'trading-permission:'+owner,extra)
    with pytest.raises(ValueError,match='permission'):paper.gate(owner,require_running=False)

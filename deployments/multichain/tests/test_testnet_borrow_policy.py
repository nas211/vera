"""User policy: 10 deposited test DOGE -> 7 demo USD; block entries above 80% LTV."""
from decimal import Decimal as D
import pytest
from autodefi.adapters import adapter
from autodefi.risk import RISK
from vela.db import get, put, transaction
from conftest import funded, save_vault, order


def ten_doge(env, amount='7'):
    env['v'].update(collateral=str(10*10**18), principalMicro=str(int(D(amount)*1000000)))
    save_vault(env['db'], env['v'])


def test_ten_deposited_doge_credits_seven_demo_usd_exactly_once(env):
    ten_doge(env)
    lending, owner = env['lending'], env['owner']
    before = lending.health(owner)
    assert D(before['price']) == D(adapter().price) == 1
    assert D(before['collateralValue']) == 10
    assert D(before['borrowCapacity']) == 7
    assert before['maxLtv'] == '0.70' and before['maxTradingLtv'] == '0.80'
    funded(env)
    with transaction(env['db']): lending.borrow(owner)
    assert D(env['paper'].snapshot(owner)['balance']) == 7
    assert D(get(env['db'], 'pool')['cash']) == 999993
    assert D(lending.health(owner)['borrowCapacity']) == 0
    assert D(lending.health(owner)['healthFactor']) > D(RISK['blockHealthBelow'])
    assert env['paper'].gate(owner)['budget'] <= 2


@pytest.mark.parametrize('collateral,amount', [('10', '7.000001'), ('0', '1')])
def test_borrow_limit_is_deposited_collateral_not_wallet_balance(env, collateral, amount):
    ten_doge(env, amount)
    env['v']['collateral'] = str(int(D(collateral)*10**18))
    env['v']['walletBalance'] = str(100*10**18)
    save_vault(env['db'], env['v'])
    pool = get(env['db'], 'pool')
    with pytest.raises(ValueError, match='LTV'), transaction(env['db']):
        env['lending'].borrow(env['owner'])
    assert env['lending'].loan(env['owner']) is None
    assert env['paper'].snapshot(env['owner'])['balance'] == '0'
    assert get(env['db'], 'pool') == pool


@pytest.mark.parametrize('cash,capacity', [('200003','3'), ('199999','0')])
def test_capacity_and_credit_honor_pool_reserve(env, cash, capacity):
    ten_doge(env)
    pool = get(env['db'], 'pool'); pool['cash'] = cash; put(env['db'], 'pool', pool)
    health = env['lending'].health(env['owner'])
    assert D(health['borrowCapacity']) == D(capacity)
    assert D(health['lendingLiquidity']['available']) == D(capacity)
    assert health['lendingLiquidity']['currency'] == 'demo USD'
    with pytest.raises(ValueError, match='reserve'), transaction(env['db']):
        env['lending'].borrow(env['owner'])
    assert env['lending'].loan(env['owner']) is None


def test_old_native_quote_revalues_without_refreshing_or_losing_stress(env):
    ten_doge(env)
    lending, owner = env['lending'], env['owner']
    old = dict(price='.18', drop='.10', at=0, source='old illustrative quote')
    put(env['db'], 'quote:'+owner, old)
    quote = lending.quote(owner)
    assert D(quote['price']) == D('.90') and quote['at'] == 0 and quote['drop'] == '.10'
    assert D(lending.health(owner)['borrowCapacity']) == D('6.30')
    with pytest.raises(ValueError, match='quote'): lending.borrow(owner)
    assert get(env['db'], 'quote:'+owner) == old
    lending.quote(owner, '0')
    assert D(lending.health(owner)['borrowCapacity']) == 7


def test_uncreated_account_has_no_quote(env):
    assert env['lending'].quote('0x'+'44'*20) is None


@pytest.mark.parametrize('interest,blocked', [('0.999999',False), ('1',False), ('1.000001',True)])
def test_total_debt_boundary_at_80_percent_and_repayment_stays_open(env, interest, blocked):
    ten_doge(env); funded(env)
    env['db'].execute('UPDATE loans SET interest=?', (interest,))
    paper, lending, owner = env['paper'], env['lending'], env['owner']
    if blocked:
        with pytest.raises(ValueError, match='health'): paper.gate(owner)
        assert D(lending.health(owner)['borrowCapacity']) == 0
        # Reducing debt is still possible while new exposure is blocked.
        with transaction(env['db']): lending.repay_amount(owner, D('1.1'))
        assert D(lending.health(owner)['ltv']) < D('.8')
    assert paper.gate(owner)['budget'] <= 2


@pytest.mark.parametrize('side', ['long','short'])
def test_queued_order_cannot_fill_after_ltv_crosses_80_percent(env, side):
    ten_doge(env); funded(env)
    with transaction(env['db']):
        request = env['paper'].submit(env['owner'], {**order(side), 'notional':'2'}, 'ltv-fill-'+side)
    env['db'].execute("UPDATE loans SET interest='1.000001'")
    env['db'].execute('UPDATE paper_orders SET due=0')
    env['paper'].tick()
    assert not env['paper'].getPosition(env['owner'])
    row = env['db'].execute('SELECT status,error FROM paper_orders WHERE id=?', (request['id'],)).fetchone()
    assert row['status'] == 'rejected' and 'health' in row['error']


def test_stress_crossing_ltv_stops_entries_without_forcing_settlement(env):
    ten_doge(env); funded(env)
    lending, owner = env['lending'], env['owner']
    lending.quote(owner, '.10')
    assert env['paper'].gate(owner)['budget'] <= 2
    lending.quote(owner, '.25')
    env['paper'].risk_governor()
    assert not get(env['db'], 'paper-prefs:'+owner)['autonomous']
    assert lending.loan(owner)['state'] == 'ACTIVE'


@pytest.mark.parametrize('side', ['long', 'short'])
def test_seven_dollar_loan_supports_market_valid_small_paper_trades(env, side):
    ten_doge(env); funded(env)
    # No exception to market precision, minimums or the fee-inclusive stop budget.
    with transaction(env['db']):
        env['paper'].submit(env['owner'], {**order(side), 'notional':'2'}, 'small-loan-'+side)
    env['db'].execute('UPDATE paper_orders SET due=0'); env['paper'].tick()
    positions = env['paper'].getPosition(env['owner'])
    assert len(positions) == 1
    p = positions[0]; sign = D(1) if side == 'long' else D(-1)
    budget = (D(p['entry'])-D(p['stop']))*D(p['qty'])*sign+D(p['paid_fees'])+D(p['stop'])*D(p['qty'])*D('.0004')
    assert budget <= 2
    # An exit still works after total debt crosses the trading block.
    env['db'].execute("UPDATE loans SET interest='1.000001'")
    with transaction(env['db']): env['paper'].close(p['id'], 'owner_exit')
    env['db'].execute('UPDATE paper_orders SET due=0'); env['paper'].tick()
    assert not env['paper'].getPosition(env['owner'])


def test_small_loan_cannot_bypass_actual_market_minimum(env):
    ten_doge(env); funded(env)
    env['feed'].markets['BTC/USD'].min_notional = D('5')
    with pytest.raises(ValueError, match='market minimum'):
        env['paper'].submit(env['owner'], {**order(), 'notional':'2'}, 'below-market')

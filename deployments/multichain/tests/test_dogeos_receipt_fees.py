"""Receipt accounting, including the observed reward-cycle balance mismatch."""
import pytest

from tools.dogeos_receipt_fees import receipt_fees
from tools.verify_reward_cycle_rpc import public_state


def test_reward_cycle_fees_reconcile_observed_native_balance_change():
    receipts = [
        ('0x24f17c', '0x1f3a2ec8a72832'),
        ('0x1d2e5', '0x1dcd3add69cfc0'),
        ('0x2feb9', '0x190ffbda9edcb8'),
        ('0x1380d', '0x1ef76fbe58058a'),
    ]
    fees = [receipt_fees(dict(gasUsed=gas, effectiveGasPrice='0x1de84d8', l1Fee=l1))
            for gas, l1 in receipts]
    assert sum(int(f['executionFeeWei']) for f in fees) == 88335675950312
    assert sum(int(f['dataFinalityFeeWei']) for f in fees) == 32948881323448884
    reward_paid = 3757610350076
    actual_wallet_delta = -33033459389049120
    assert reward_paid-sum(int(f['totalFeeWei']) for f in fees) == actual_wallet_delta


def test_decoded_integer_receipts_and_explicit_zero_data_fee():
    assert receipt_fees(dict(gasUsed=21000, effectiveGasPrice=2, l1Fee=0)) == dict(
        executionFeeWei='42000', dataFinalityFeeWei='0', totalFeeWei='42000')


@pytest.mark.parametrize('value', [None, -1, True, 0.5, 'unknown', '-1'])
def test_unknown_or_invalid_data_fee_cannot_be_reported_as_zero(value):
    with pytest.raises(ValueError):
        receipt_fees(dict(gasUsed=21000, effectiveGasPrice=2, l1Fee=value))


def test_missing_data_fee_is_unknown():
    with pytest.raises(ValueError, match='l1Fee'):
        receipt_fees(dict(gasUsed=21000, effectiveGasPrice=2))


def test_public_native_balances_survive_javascript_json_consumers():
    state=public_state(dict(reserve=999996242389649924, poolBalanceWei=999996242389649924,
                           claimed=3757610350076, collateral=0, poolPrincipal=0,
                           loanState=0, trading=False))
    assert state['reserve']==state['poolBalanceWei']=='999996242389649924'
    assert state['claimed']=='3757610350076' and state['collateral']=='0'
    assert state['loanState']==0 and state['trading'] is False
    assert public_state(dict(amount=10*10**18,remaining=0))==dict(amount='10000000000000000000',remaining='0')

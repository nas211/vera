import sys
import time
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.reconcile_closed_funding import apply, preview
from autodefi.repayment import Repayment
from autodefi.store import cashflow
from vela.db import get, put, transaction
from conftest import funded, fill


def closed_gap(env, monkeypatch, side='long', partial=False, received=False):
    with transaction(env['db']):
        cashflow(env['db'], env['owner'], D(10))
    funded(env)
    p = fill(env, side)
    paper, db, owner = env['paper'], env['db'], env['owner']
    now = time.time()
    m = env['feed'].markets['BTC/USD']
    m.funding_rate = D('.001') * (1 if side == 'long' else -1) * (-1 if received else 1)
    with transaction(db):
        paper.funding(now + 2)
        paper.funding(now + 12)
    monkeypatch.setattr(time, 'time', lambda: now + 12)
    m.stats_at = m.book_at = time.time()
    with transaction(db):
        if partial:
            paper.close(p['id'], 'partial', D('.5'))
        paper.close(p['id'], 'closed')
        paper.pause_entries(owner)
    return p


@pytest.mark.parametrize('side', ['long', 'short'])
def test_approved_estimate_is_atomic_audited_and_preserves_loan_and_risk(env, monkeypatch, side):
    p = closed_gap(env, monkeypatch, side)
    db, owner, paper = env['db'], env['owner'], env['paper']
    loan_before = env['lending'].loan(owner)
    initial = db.execute('SELECT initial FROM users WHERE address=?', (owner,)).fetchone()[0]
    assert Repayment(db, paper).free(owner)[0] == 0
    plan = preview(db, owner)
    result = apply(db, owner, plan['approvalHash'], plan['debit'])
    assert result['applied'] and result['estimated'] and not result['historicalRatesRecovered']
    assert env['lending'].loan(owner) == loan_before
    assert db.execute('SELECT initial FROM users WHERE address=?', (owner,)).fetchone()[0] == initial
    assert not get(db, 'paper-funding-incomplete:' + owner)
    assert not get(db, 'paper-prefs:' + owner)['autonomous']
    assert get(db, 'paper-position:' + p['id'])['riskBudget'] == '2'
    snap = paper.snapshot(owner)
    assert D(snap['balance']) == D(plan['cashAfter'])
    assert abs(D(snap['totals']['realized']) - (D(snap['balance']) - D(snap['initial']))) < D('1e-18')
    assert snap['trades'][0]['fundingGapSeconds'] == 10
    assert snap['trades'][0]['fundingGapResolution']['estimated']
    before = db.total_changes
    assert apply(db, owner, plan['approvalHash'], plan['debit'])['replayed']
    assert db.total_changes == before
    with transaction(db):
        partial = Repayment(db, paper).pay(owner, '.01')
    # The repair does not implement a separate repayment path; interest remains first.
    assert D(partial['loan']['principal']) > D(loan_before['principal']) - D('.01')
    with transaction(db):
        Repayment(db, paper).pay(owner, '801', full=True)
    assert env['lending'].loan(owner)['state'] == 'AWAITING_CHAIN'
    assert env['lending'].debt(env['lending'].loan(owner))[0] == 0


@pytest.mark.parametrize('mutation', ['cash', 'flag', 'pending', 'autonomous', 'loan'])
def test_changed_preview_cannot_clear_or_mutate_account(env, monkeypatch, mutation):
    closed_gap(env, monkeypatch)
    db, owner = env['db'], env['owner']
    plan = preview(db, owner)
    if mutation == 'cash':
        db.execute("UPDATE users SET balance='9999' WHERE address=?", (owner,))
    elif mutation == 'flag':
        put(db, 'paper-funding-incomplete:' + owner, False)
    elif mutation == 'pending':
        db.execute("UPDATE paper_orders SET status='pending' WHERE user=?", (owner,))
    elif mutation == 'autonomous':
        put(db, 'paper-prefs:' + owner, dict(autonomous=True))
    else:
        db.execute("UPDATE loans SET principal='799' WHERE owner=?", (owner,))
    before = db.total_changes
    with pytest.raises(ValueError):
        apply(db, owner, plan['approvalHash'], plan['debit'])
    assert db.total_changes == before


def test_wrong_amount_and_partial_exits_fail_closed(env, monkeypatch):
    closed_gap(env, monkeypatch)
    db, owner = env['db'], env['owner']
    plan = preview(db, owner)
    with pytest.raises(ValueError, match='exact estimated debit'):
        apply(db, owner, plan['approvalHash'], '0')
    assert get(db, 'paper-funding-incomplete:' + owner)
    db.execute("INSERT INTO trades SELECT 'second',user,session,strategy,position_id,symbol,side,qty,entry,exit,gross,fees,pnl,pnl_micro,opened,closed,reason,tx_hash,chain_status FROM trades LIMIT 1")
    with pytest.raises(ValueError, match='Partial exits'):
        preview(db, owner)


def test_actual_partial_close_cannot_be_prorated_as_constant_quantity(env, monkeypatch):
    closed_gap(env, monkeypatch, partial=True)
    with pytest.raises(ValueError, match='Partial exits'):
        preview(env['db'], env['owner'])


def test_cannot_create_an_estimated_credit(env, monkeypatch):
    closed_gap(env, monkeypatch, side='short', received=True)
    with pytest.raises(ValueError, match='positive observed costs'):
        preview(env['db'], env['owner'])


def test_mid_apply_failure_rolls_back_balances_costs_and_flag(env, monkeypatch):
    closed_gap(env, monkeypatch)
    db, owner = env['db'], env['owner']
    plan = preview(db, owner)
    db.execute("CREATE TRIGGER fail_resolution BEFORE INSERT ON paper_activity WHEN NEW.kind='funding_reconciled' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(Exception, match='fixture failure'):
        apply(db, owner, plan['approvalHash'], plan['debit'])
    assert preview(db, owner) == plan
    assert get(db, 'paper-funding-resolution:' + owner + ':' + plan['approvalHash']) is None


def test_new_unknown_funding_is_blocked_after_a_repair(env, monkeypatch):
    closed_gap(env, monkeypatch)
    db, owner, paper = env['db'], env['owner'], env['paper']
    plan = preview(db, owner)
    apply(db, owner, plan['approvalHash'], plan['debit'])
    put(db, 'paper-prefs:' + owner, dict(autonomous=True, generation=2))
    fill(env)
    with transaction(db):
        paper.funding(time.time() + 10)
    assert get(db, 'paper-funding-incomplete:' + owner)
    assert Repayment(db, paper).free(owner)[0] == 0
    with pytest.raises(ValueError, match='Open positions'):
        preview(db, owner)


@pytest.mark.parametrize('tampered', [False, True])
def test_new_gap_preserves_and_verifies_previously_approved_resolution(env, monkeypatch, tampered):
    old_position = closed_gap(env, monkeypatch)
    db, owner, paper = env['db'], env['owner'], env['paper']
    first = preview(db, owner)
    apply(db, owner, first['approvalHash'], first['debit'])
    old_meta = get(db, 'paper-position:' + old_position['id'])
    put(db, 'paper-prefs:' + owner, dict(autonomous=True, generation=2))
    position = fill(env)
    now = time.time()
    with transaction(db):
        paper.funding(now + 2)
        paper.funding(now + 12)
    monkeypatch.setattr(time, 'time', lambda: now + 12)
    m = env['feed'].markets['BTC/USD']; m.stats_at = m.book_at = time.time()
    with transaction(db):
        paper.close(position['id'], 'closed')
        paper.pause_entries(owner)
    if tampered:
        saved = get(db, 'paper-funding-resolution:' + owner + ':' + first['approvalHash'])
        saved['corrections'][0]['estimatedDebit'] = '1'
        put(db, 'paper-funding-resolution:' + owner + ':' + first['approvalHash'], saved)
        with pytest.raises(ValueError, match='Prior resolution'):
            preview(db, owner)
        assert get(db, 'paper-funding-incomplete:' + owner)
    else:
        second = preview(db, owner)
        assert [c['positionId'] for c in second['corrections']] == [position['id']]
        apply(db, owner, second['approvalHash'], second['debit'])
        assert get(db, 'paper-position:' + old_position['id']) == old_meta
        assert not get(db, 'paper-funding-incomplete:' + owner)

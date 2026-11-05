"""Preview an explicitly approved estimate for closed paper funding gaps.

This is an operator repair, not a historical market-data backfill. Never apply
without the owner's approval of the exact preview hash and estimated debit.
No sessions, keys, contracts, loan balances or risk limits are modified.
"""
import argparse
from collections import Counter
from decimal import Decimal as D, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from vela.db import get, put, transaction

OWNER = '0xf8b7120f466f39063398fdc62f5f8cbce37ad1db'
DATABASE = Path('/root/VellaMultichain/.runtime/lighter-showcase/multichain-candidate.sqlite3')
METHOD = 'owner-approved-observed-average-proration-v1'
MICRO = D('.000001')


def check(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    value = D(str(value))
    check(value.is_finite(), 'Non-finite accounting data')
    return value


def same(a, b):
    # Legacy long-running decimal accrual can differ below ledger precision.
    return abs(number(a) - number(b)) < D('1e-18')


def preview(db, owner):
    check(get(db, 'paper-funding-incomplete:' + owner) is True, 'No unresolved funding flag')
    check(not db.execute('SELECT 1 FROM positions WHERE user=?', (owner,)).fetchone(), 'Open positions require their own reconciliation')
    check(not db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'", (owner,)).fetchone(), 'Pending orders must finish first')
    prefs = get(db, 'paper-prefs:' + owner, {})
    check(prefs.get('autonomous') is False, 'Stop autonomous entries first')
    check(not get(db, 'repayment-policy:' + owner, {}).get('enabled'), 'Revoke automated repayments first')
    account = dict(db.execute('SELECT balance,initial,status FROM users WHERE address=?', (owner,)).fetchone())
    trades = [dict(r) for r in db.execute('SELECT id,position_id,symbol,side,qty,entry,gross,fees,pnl,pnl_micro,opened,closed FROM trades WHERE user=? ORDER BY id', (owner,))]
    counts = Counter(t['position_id'] for t in trades)
    check(trades and all(n == 1 for n in counts.values()), 'Partial exits need interval-specific funding evidence')
    entries = []
    for r in db.execute("SELECT payload,result FROM paper_orders WHERE user=? AND status='filled' ORDER BY id", (owner,)):
        if not json.loads(r['payload']).get('reduceOnly'):
            entries.append(json.loads(r['result']))
    check(Counter(p['id'] for p in entries) == counts, 'Closed fills do not cover every opened position')
    costs = [dict(r) for r in db.execute('SELECT id,position_id,ts,kind,amount FROM paper_costs WHERE user=? ORDER BY id', (owner,))]
    cost_totals = {}
    for c in costs:
        cost_totals[c['kind']] = cost_totals.get(c['kind'], D(0)) + number(c['amount'])
    cached = get(db, 'paper-cost-totals:' + owner)
    check(cached is not None and set(cached) == set(cost_totals) and all(same(cached[k], v) for k, v in cost_totals.items()), 'Cost totals disagree with recorded costs')
    check(same(number(account['balance']) - number(account['initial']), sum((number(t['pnl']) for t in trades), D(0))), 'Cash does not reconcile with closed trade P&L')
    metadata, corrections = {}, []
    for t in trades:
        pid = t['position_id']
        p = next(p for p in entries if p['id'] == pid)
        check(p['user'] == owner and p['side'] == t['side'] and p['symbol'] == t['symbol'] and
              number(p['qty']) == number(t['qty']) and number(p['entry']) == number(t['entry']) and
              p['opened'] == t['opened'], 'Full close differs from original entry')
        m, tm = get(db, 'paper-position:' + pid), get(db, 'paper-trade:' + t['id'])
        check(isinstance(m, dict) and isinstance(tm, dict), 'Missing original funding metadata')
        metadata[pid] = dict(position=m, trade=tm)
        gap = number(m['fundingGapSeconds'])
        funding = number(m['fundingTotal'])
        check(gap >= 0 and gap == number(tm['fundingGapSeconds']), 'Funding gaps disagree')
        check(same(funding, tm['funding']) and same(funding, m['fundingRemaining']) and
              same(funding, sum((number(c['amount']) for c in costs if c['position_id'] == pid and c['kind'] == 'funding'), D(0))), 'Position funding disagrees with its cost ledger')
        check(same(number(t['gross']) - number(t['fees']) - funding, t['pnl']), 'Trade P&L does not reconcile')
        if gap == 0:
            continue
        check(not m.get('fundingGapResolution') and not tm.get('fundingGapResolution'), 'Previously resolved gaps require separate review')
        observed = number(m['fundingAt']) - number(t['opened']) - gap
        check(observed > 0 and number(m['fundingAt']) <= number(t['closed']), 'Missing valid observed duration')
        check(funding > 0, 'Cannot infer a missing charge or credit without positive observed costs')
        debit = (funding * gap / observed).quantize(MICRO, rounding=ROUND_CEILING)
        corrections.append(dict(tradeId=t['id'], positionId=pid, symbol=t['symbol'], side=t['side'],
                                gapSeconds=str(gap), observedSeconds=str(observed), observedFunding=str(funding),
                                estimatedDebit=str(debit), originalPnl=t['pnl'], correctedPnl=str(number(t['pnl']) - debit),
                                attributedAt=t['closed']))
    check(corrections, 'Flag has no supported unresolved gaps; do not clear it')
    total = sum((number(c['estimatedDebit']) for c in corrections), D(0))
    check(number(account['balance']) >= total, 'Insufficient cash for the proposed adjustment')
    loan = db.execute('SELECT id,principal,interest,original,paid,last,apr,state FROM loans WHERE owner=? ORDER BY rowid DESC LIMIT 1', (owner,)).fetchone()
    snapshot = dict(owner=owner, account=account, trades=trades, entries=entries, costs=costs,
                    metadata=metadata, totals=cached, preferences=prefs, loan=dict(loan) if loan else None)
    plan = dict(owner=owner, method=METHOD, estimated=True, historicalRatesRecovered=False,
                debit=str(total), cashBefore=account['balance'], cashAfter=str(number(account['balance']) - total),
                corrections=corrections, initialCapitalUnchanged=True, loanUnchanged=True, riskLimitsUnchanged=True,
                tradingRemainsStopped=True, transactionsSent=False)
    plan['approvalHash'] = hashlib.sha256(json.dumps(dict(snapshot=snapshot, plan=plan), sort_keys=True).encode()).hexdigest()
    return plan


def apply(db, owner, approval_hash, approved_debit):
    """Atomic, stale-preview fenced and repeat-safe; caller supplies explicit consent."""
    with transaction(db):
        key = 'paper-funding-resolution:' + owner + ':' + approval_hash
        old = get(db, key)
        if old is not None:
            check(old['debit'] == approved_debit, 'Approved amount differs from saved resolution')
            return dict(old, replayed=True)
        plan = preview(db, owner)
        check(approval_hash == plan['approvalHash'], 'Account changed; review a new preview')
        check(approved_debit == plan['debit'], 'Approve the exact estimated debit')
        totals = get(db, 'paper-cost-totals:' + owner)
        for c in plan['corrections']:
            debit = number(c['estimatedDebit'])
            # Attribute the cost/P&L to the historical close; record approval now.
            db.execute('INSERT INTO paper_costs(user,session,position_id,ts,kind,amount) SELECT ?,session,?,?,?,? FROM trades WHERE id=? AND user=?',
                       (owner, c['positionId'], c['attributedAt'], 'funding', c['estimatedDebit'], c['tradeId'], owner))
            db.execute('UPDATE trades SET pnl=?,pnl_micro=? WHERE id=? AND user=?',
                       (c['correctedPnl'], int(number(c['correctedPnl']) * 1_000_000), c['tradeId'], owner))
            resolution = dict(id=approval_hash, method=METHOD, estimated=True, estimatedDebit=c['estimatedDebit'], observedFunding=c['observedFunding'])
            tm = get(db, 'paper-trade:' + c['tradeId'])
            tm.update(funding=str(number(tm['funding']) + debit), fundingGapResolution=resolution)
            put(db, 'paper-trade:' + c['tradeId'], tm)
            pm = get(db, 'paper-position:' + c['positionId'])
            pm.update(fundingTotal=str(number(pm['fundingTotal']) + debit),
                      fundingRemaining=str(number(pm['fundingRemaining']) + debit), fundingGapResolution=resolution)
            put(db, 'paper-position:' + c['positionId'], pm)
        totals['funding'] = str(number(totals['funding']) + number(plan['debit']))
        put(db, 'paper-cost-totals:' + owner, totals)
        db.execute('UPDATE users SET balance=? WHERE address=?', (plan['cashAfter'], owner))
        result = dict(plan, applied=True, appliedAt=time.time(), replayed=False)
        put(db, key, result)
        # The old gaps remain recorded with their approved estimate. New gaps
        # still set this existing fail-closed flag through the unchanged engine.
        put(db, 'paper-funding-incomplete:' + owner, False)
        db.execute('INSERT INTO paper_activity(user,ts,kind,detail) VALUES (?,?,?,?)',
                   (owner, result['appliedAt'], 'funding_reconciled',
                    'Owner-approved estimated funding debit $' + plan['debit'] + '; historical rates not recovered; audit ' + approval_hash))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approval-hash')
    parser.add_argument('--approved-debit')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.apply and not (args.approval_hash and args.approved_debit):
        parser.error('Apply requires the owner-approved preview hash and exact debit')
    db = sqlite3.connect(DATABASE.as_uri() + ('?mode=rw' if args.apply else '?mode=ro'), uri=True, isolation_level=None, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        if args.apply:
            report = apply(db, OWNER, args.approval_hash, args.approved_debit)
        else:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            report = dict(preview(db, OWNER), applied=False)
    finally:
        db.close()
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()

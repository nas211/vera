"""Bounded, read-only observation. Never starts trading or modifies the ledger."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sqlite3
import time

DATABASE = Path('/root/VellaMultichain/.runtime/lighter-showcase/multichain-candidate.sqlite3')
OWNER = '0xf8b7120f466f39063398fdc62f5f8cbce37ad1db'


def observe(started, previous):
    now = time.time()
    db = sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    def get(key, default=None):
        row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    try:
        positions = []
        for row in db.execute('SELECT id,user,symbol,side,qty,entry,stop,paid_fees,realized_gross FROM positions'):
            p = dict(row); owner = p.pop('user'); meta = get('paper-position:' + p['id'], {})
            limits = [D(2), D(meta.get('riskBudget', '2')), D(get('account-stop-budget:' + owner, '2000000')) / 1000000]
            permission = get('trading-permission:' + owner, {})
            if permission.get('maxLoss') is not None:
                limits.append(D(permission['maxLoss']) / 1000000)
            vault = db.execute('SELECT data FROM vaults WHERE owner=?', (owner,)).fetchone()
            if vault:
                native = json.loads(vault[0])
                if native.get('loanState') == 2 and native.get('limits', {}).get('maxLoss') is not None:
                    limits.append(D(str(native['limits']['maxLoss'])) / 1000000)
            cap = min(limits); qty, entry, stop = (D(p[k]) for k in ('qty', 'entry', 'stop'))
            sign = D(1) if p['side'] == 'long' else D(-1)
            trigger = (entry-stop)*qty*sign + D(p['paid_fees']) - D(p['realized_gross']) + stop*qty*max(D('.0004'), D(meta.get('exitFee', '0')))
            old = previous.get(p['id'])
            tick = D(meta.get('tick', '0'))
            positions.append(dict(p, account=hashlib.sha256(owner.encode()).hexdigest()[:12],
                budget=str(cap), feeInclusiveTriggerLoss=str(trigger), withinBudget=trigger <= cap,
                tickAligned=tick > 0 and stop % tick == 0,
                stopDidNotWiden=old is None or (stop-D(old))*sign >= 0,
                fundingGapSeconds=meta.get('fundingGapSeconds', 0)))
        prefs = get('paper-prefs:' + OWNER, {})
        incomplete = bool(get('paper-funding-incomplete:' + OWNER, False))
        policy = get('repayment-policy:' + OWNER) or {}
        loan = db.execute('SELECT id,state,principal,interest FROM loans WHERE owner=? ORDER BY rowid DESC LIMIT 1', (OWNER,)).fetchone()
        blockers = []
        if not prefs.get('autonomous'): blockers.append('Trading is stopped')
        if incomplete: blockers.append('Funding is incomplete')
        if not loan or loan['state'] not in ('ACTIVE','SHORTFALL') or D(loan['principal'])+D(loan['interest']) <= 0: blockers.append('No repayable loan')
        if not policy.get('enabled') or policy.get('expiresAt', 0) <= now or D(str(policy.get('sweepPercent', 0))) <= 0: blockers.append('No active profit-sweep permission')
        trades = []
        for row in db.execute('SELECT id,position_id,symbol,side,pnl,closed,reason FROM trades WHERE user=? AND closed>=? ORDER BY closed', (OWNER, started)):
            t = dict(row); m = get('paper-trade:' + t['id'], {}); pm = get('paper-position:' + t['position_id'], {})
            t.update(agent=pm.get('agent') is True, fundingComplete=not m.get('fundingGapSeconds') or bool(m.get('fundingGapResolution')))
            trades.append(t)
        repayments = []
        for row in db.execute("SELECT id,loan,at,amount,detail FROM loan_events WHERE owner=? AND at>=? AND kind='SIMULATED_REPAYMENT_RECEIPT' ORDER BY at", (OWNER, started)):
            r = dict(row); detail = json.loads(r.pop('detail'))
            if detail.get('method') == 'profit_sweep': repayments.append(r)
        stops = [t for t in trades if t['reason'] == 'stop']
        positives = [t for t in trades if t['agent'] and t['fundingComplete'] and D(t['pnl']) > 0]
        prices = [dict(r) for r in db.execute('SELECT symbol,ts,safe,reason FROM prices')]
        for p in prices: p['ageSeconds'] = now-p['ts']
        return dict(observedAt=now, observedUtc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
            scope='Since monitor start only', startedAt=started, readOnly=True,
            ownerTradingRunning=bool(prefs.get('autonomous')), ownerFundingIncomplete=incomplete,
            ownerEntryStop=get('paper-stop-reason:'+OWNER), ownerLoan=dict(loan) if loan else None,
            profitRepaymentBlockers=blockers, positions=positions, prices=prices,
            newClosedTrades=trades, positiveAiTrades=positives, automaticRepaymentReceipts=repayments,
            observedAutomaticStopExits=stops,
            profitableAiThenRepaymentObserved=any(r['at'] >= t['closed'] for r in repayments for t in positives),
            limitation='PAPER only. Stored stops are not proof of a triggered exit. Slippage and funding can exceed trigger budgets. No orders or wallet transactions are sent.')
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hours', type=float, default=12)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.time(); previous = {}
    while True:
        try:
            result = observe(started, previous)
            previous.update({p['id']:p['stop'] for p in result['positions']})
        except Exception as error:
            result = dict(observedAt=time.time(), readOnly=True, error=str(error), verified=False)
        temp = args.output/'latest.tmp'
        temp.write_text(json.dumps(result, indent=2)+'\n')
        temp.replace(args.output/'latest.json')
        with (args.output/'observations.jsonl').open('a') as log:
            log.write(json.dumps(result)+'\n')
        if time.time() >= started+args.hours*3600: break
        time.sleep(30)


if __name__ == '__main__':
    main()

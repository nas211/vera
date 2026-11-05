#!/usr/bin/env python3
"""Bounded public PAPER verification with fresh disposable identities only.

No owner credentials, chain writes, fixture prices, manual debt adjustments or
service changes. The financed case uses Base's explicitly simulated vault path.
Evidence contains no cookies, authentication signatures or private keys.
Create OUTPUT_DIR/close-now or send SIGINT/SIGTERM for a graceful market close.
Run on a persistent host: abrupt process/host loss cannot execute cleanup.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import secrets
import signal
from threading import Event
import time
import uuid

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct


def now():
    return datetime.now(timezone.utc).isoformat()


def pending_reductions(orders):
    result = set()
    for order in orders:
        payload = order['payload']
        if isinstance(payload, str):
            payload = json.loads(payload)
        if order['status'] == 'pending' and payload.get('reduceOnly'):
            result.add(payload.get('positionId'))
    return result


class Control:
    def __init__(self, directory, seconds):
        self.close_file = directory/'close-now'
        self.stop = Event()
        # A suspended sandbox may freeze monotonic time while positions remain
        # open on the server. Count real elapsed time toward the run limit.
        self.deadline = time.time()+seconds

    def close_requested(self):
        return self.stop.is_set() or self.close_file.exists()

    def require_running(self):
        if self.close_requested():
            raise RuntimeError('Diagnostic close requested; no new action started')
        if time.time() >= self.deadline:
            raise RuntimeError('Diagnostic wall-clock deadline reached')


class Probe:
    def __init__(self, base, output, control=None, **headers):
        self.output = output
        self.control = control
        self.report = dict(startedAt=now(), status='RUNNING', execution='PAPER',
                           realFunds=False, blockchainWrites=False, steps=[])
        self.client = httpx.Client(base_url=base, timeout=30, headers={
            'Origin': base, 'X-ADP-Client': 'web', **headers})
        self.save()

    def save(self):
        temporary = self.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.report, indent=2)+'\n')
        temporary.replace(self.output)

    def get(self, path):
        # Read-only observations may be retried after a dropped connection.
        # Writes below are deliberately never retried after an uncertain result.
        for attempt in range(4):
            try:
                result = self.client.get('/api/'+path)
                if result.status_code in (502, 503, 504) and attempt < 3:
                    time.sleep(1+attempt)
                    continue
                result.raise_for_status()
                return result.json()
            except httpx.TransportError:
                if attempt == 3:
                    raise
                time.sleep(1+attempt)

    def post(self, path, body=None):
        cleanup = path in ('trading/stop', 'settle', 'logout') or path.endswith(('/close', '/cancel'))
        if self.control and not cleanup:
            self.control.require_running()
        # Never replay a timed-out mutation. Cleanup first reads the account.
        result = self.client.post('/api/'+path, json=body or {}, headers={
            'Idempotency-Key': 'verify-'+uuid.uuid4().hex},
            timeout=90 if path == 'trading/start' else 30)
        step = dict(at=now(), path=path, status=result.status_code)
        if result.is_error:
            step['error'] = result.json()
        self.report['steps'].append(step)
        self.save()
        result.raise_for_status()
        return result.json()

    def preflight(self):
        deadline = time.time()+30
        while True:
            if self.control:
                self.control.require_running()
            health = self.get('health')
            self.report['health'] = health
            self.save()
            assert health['mode'] == 'PAPER' and health['lending'] == 'DEMO', 'PAPER/DEMO required'
            assert not health['liveEnabled'] and not health['realFunding'], 'Real execution must be disabled'
            if health['engineReady'] and health['marketData'] and not health['runtimeError']:
                break
            if time.time() >= deadline:
                raise RuntimeError('Engine, market data or runtime health not ready; no account created')
            time.sleep(2)

    def finish(self):
        self.report['finishedAt'] = now()
        self.save()
        self.client.close()


def stop_probe(base, output, seconds, control=None):
    control = control or Control(output.parent, seconds)
    p = Probe(base, output, control, **{'X-Vela-Paper': secrets.token_hex(32)})
    created = False
    try:
        p.preflight()
        initial = p.post('paper/session')
        created = True
        p.report.update(initialBalance=initial['balance'], cases=[])
        for side in ('long', 'short'):
            before = p.get('paper/account')
            old_ids = {t['id'] for t in before['trades']}
            order = dict(clientId='auto-stop-'+uuid.uuid4().hex, symbol='SOL/USD',
                         side=side, type='market', notional='20', leverage='1',
                         stopBps='1', targetBps='100', riskBudget='0.03')
            case = dict(side=side, submitted=order, positions=[])
            p.report['cases'].append(case)
            case['order'] = p.post('paper/orders', order)
            while time.time() < control.deadline:
                control.require_running()
                snapshot = p.get('paper/account')
                for pos in snapshot['positions']:
                    sign = D(1 if pos['side'] == 'long' else -1)
                    trigger = ((D(pos['entry'])-D(pos['stop']))*D(pos['qty'])*sign
                               + D(pos['paid_fees'])-D(pos['realized_gross'])
                               + D(pos['stop'])*D(pos['qty'])*D('.0004'))
                    assert D(pos['riskBudget']) <= D('.03') and trigger <= D('.03')
                    case['positions'].append(dict(pos, feeInclusiveTriggerLoss=str(trigger)))
                trades = [t for t in snapshot['trades'] if t['id'] not in old_ids]
                if trades:
                    case['trades'] = trades
                    assert all(t['reason'] == 'stop' and t['side'] == side for t in trades)
                    assert not snapshot['positions']
                    case['status'] = 'PASS'
                    break
                rejected = [o for o in snapshot['orders'] if o.get('client_id') == order['clientId'] and o['status'] == 'rejected']
                if rejected:
                    raise RuntimeError('Entry rejected: '+str(rejected))
                p.save()
                time.sleep(.5)
            else:
                raise RuntimeError('No automatic stop observed before deadline')
        final = p.get('paper/account')
        # Very tight stops may fill before the first polling snapshot. The
        # persisted entry-order result retains the exact initial stop and fees.
        for case in p.report['cases']:
            entry_order = next(o for o in final['orders'] if o['id'] == case['order']['id'])
            assert entry_order['status'] == 'filled'
            position = json.loads(entry_order['result'])
            sign = D(1 if position['side'] == 'long' else -1)
            trigger = ((D(position['entry'])-D(position['stop']))*D(position['qty'])*sign
                       + D(position['paid_fees'])-D(position['realized_gross'])
                       + D(position['stop'])*D(position['qty'])*D('.0004'))
            assert trigger <= D(entry_order['payload']['riskBudget']) <= D('.03')
            assert all(t['position_id'] == position['id'] for t in case['trades'])
            case['persistedEntry'] = position
            case['feeInclusiveTriggerLoss'] = str(trigger)
        p.report.update(status='PASS', final=final,
                        closedFills=len(final['trades']),
                        realizedNetPnl=str(sum(D(t['pnl']) for t in final['trades'])),
                        scope='This fresh diagnostic account only, including all partial fills',
                        limitation='Observed paper stops; slippage and funding may exceed the trigger budget. No live exchange execution.')
    except Exception as error:
        p.report.update(status='INCOMPLETE', error=str(error), errorType=type(error).__name__)
    finally:
        if created:
            try:
                snap = p.get('paper/account')
                for order in snap['orders']:
                    payload = order['payload']
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if order['status'] == 'pending' and not payload.get('reduceOnly'):
                        p.post('paper/orders/'+order['id']+'/cancel')
                snap = p.get('paper/account')
                pending = pending_reductions(snap['orders'])
                for pos in snap['positions']:
                    if pos['id'] not in pending:
                        p.post('paper/positions/'+pos['id']+'/close', dict(clientId='cleanup-'+uuid.uuid4().hex, fraction='1'))
                deadline = time.time()+40
                while True:
                    snap = p.get('paper/account')
                    if not snap['positions'] and not any(o['status'] == 'pending' for o in snap['orders']):
                        break
                    if time.time() >= deadline:
                        raise RuntimeError('Cleanup still has positions or orders')
                    time.sleep(1)
                assert not snap['preferences']['autonomous']
                p.report['cleanup'] = dict(positions=0, pendingOrders=0, autonomous=False)
            except Exception as error:
                p.report.update(status='INCOMPLETE', cleanupError=str(error))
        p.finish()
    return p.report


def manage_financed_close(p, snapshot, requested_close):
    """Pause entries and request full reductions, including losing positions."""
    paper = snapshot['paper']
    manual = p.control.close_requested()
    if paper['positions'] and 'aiEntry' not in p.report:
        p.report['aiEntry'] = snapshot
    if (paper['positions'] or manual) and not p.report.get('entriesStopped'):
        p.post('trading/stop')
        p.report['entriesStopped'] = True
    if manual and 'manualCloseRequestedAt' not in p.report:
        p.report['manualCloseRequestedAt'] = now()
        p.save()
    reserve = sum(D(pos['mark'])*D(pos['qty'])*D('.0007')
                  for pos in paper['positions'] if pos.get('mark'))+D('.01')
    profitable = paper['unrealized'] is not None and D(paper['unrealized']) > reserve
    pending = pending_reductions(paper['orders'])
    if manual or profitable:
        for pos in paper['positions']:
            if pos['id'] in requested_close or pos['id'] in pending:
                continue
            # Record before sending; never blindly retry an uncertain mutation.
            requested_close.add(pos['id'])
            attempt = dict(position=pos['id'], at=now(), reason='manual' if manual else 'observed_gain', status='REQUESTING')
            p.report.setdefault('closeRequests', []).append(attempt)
            p.save()
            try:
                p.post('trading/positions/'+pos['id']+'/close', dict(confirm=True, fraction='1'))
            except Exception:
                attempt['status'] = 'UNCONFIRMED'
                p.save()
                raise
            attempt['status'] = 'ACCEPTED'
            p.save()
    return manual and not paper['positions'] and not paper['trades']


def financed_probe(base, output, seconds, control=None):
    control = control or Control(output.parent, seconds)
    wallet = Account.create()
    p = Probe(base, output, control, **{'X-Vella-Network': 'base', 'X-Wallet-Address': wallet.address})
    p.report.update(wallet=wallet.address, network='base',
                    collateral='Explicitly simulated ETH; no Base transaction',
                    loan='Simulated USD loan; no direct trading allocation', samples=[])
    logged_in = False

    def context(snapshot):
        loan = snapshot['agent']['loan']
        return dict(vaultId=snapshot['vault']['address'] if snapshot['vault'] else None,
                    loanId=loan['id'] if loan else None, confirm=True)

    try:
        p.preflight()
        challenge = p.post('auth/nonce', dict(address=wallet.address, chainId=8453))
        signature = wallet.sign_message(encode_defunct(text=challenge['message'])).signature
        p.post('auth/verify', dict(address=wallet.address, nonce=challenge['nonce'], signature='0x'+bytes(signature).hex()))
        logged_in = True
        initial = p.get('account')
        assert D(initial['paper']['balance']) == 0 and initial['agent']['loan'] is None
        p.report['initial'] = initial
        p.post('simulated-vault/create', dict(confirm=True, vaultId=None, loanId=None, asset='ETH'))
        p.post('simulated-vault/deposit', dict(context(p.get('account')), amount='1'))
        p.post('quote', {'drop': '0'})
        p.post('simulated-vault/borrow', dict(context(p.get('account')), amount='200'))
        p.post('simulated-vault/authorize', dict(context(p.get('account')),
               allocation='200', positionSize='100', maxLoss='1', leverage='1', autoReduce=True))
        p.post('repayment/policy', dict(context(p.get('account')), enabled=True,
               sweepPercent='100', frequency='off', minimum='0', source='trading', days=1))
        funded = p.get('account')
        assert D(funded['paper']['balance']) == 200 and D(funded['directAllocation']) == 0
        p.report['funded'] = funded
        p.post('trading/start')
        last_quote = time.time()
        requested_close = set()
        while time.time() < control.deadline:
            snapshot = p.get('account')
            paper = snapshot['paper']
            p.report['samples'].append(dict(at=now(), balance=paper['balance'],
                positions=paper['positions'], autonomous=paper['preferences']['autonomous'],
                debt=snapshot['repayment']['debt'], totals=paper['totals']))
            assert D(snapshot['directAllocation']) == 0
            for pos in paper['positions']:
                assert D(pos['riskBudget']) <= D('1')
            if manage_financed_close(p, snapshot, requested_close):
                raise RuntimeError('Diagnostic cancelled before a closed AI trade was observed')
            receipts = [e for e in snapshot['events'] if e['kind'] == 'SIMULATED_REPAYMENT_RECEIPT'
                        and json.loads(e['detail']).get('method') == 'profit_sweep']
            if receipts:
                assert all(D(e['amount']) > 0 for e in receipts)
                assert paper['trades'] and not paper['positions']
                net = sum(D(t['pnl']) for t in paper['trades'])
                assert net > 0
                paid = sum(D(e['amount']) for e in receipts)
                assert paid <= net and D(snapshot['agent']['loan']['principal']) < 200
                p.report.update(status='PASS', profitSweepReceipts=receipts, verified=snapshot,
                                realizedNetPnl=str(net), closedFills=len(paper['trades']),
                                sweptProfit=str(paid), scope='This new diagnostic account only',
                                limitation='Deployed simulated Base vault and real AI/market observations; no DogeOS loan or real exchange execution.')
                break
            if p.report.get('entriesStopped') and not paper['positions'] and paper['trades']:
                # Give the repayment task time to run after the closing fill.
                if time.time()-max(t['closed'] for t in paper['trades']) > 15:
                    p.report['closedWithoutSweep'] = snapshot
                    raise RuntimeError('AI trade closed without an eligible realized profit sweep')
            if not control.close_requested() and time.time()-last_quote > 180:
                p.post('quote', {'drop':'0'})
                last_quote = time.time()
            p.save()
            time.sleep(2)
        else:
            raise RuntimeError('AI entry/profit/automatic repayment not all observed before deadline')
    except Exception as error:
        p.report.update(status='INCOMPLETE', error=str(error), errorType=type(error).__name__)
    finally:
        if logged_in:
            try:
                p.post('trading/stop')
                snapshot = p.get('account')
                if snapshot['agent']['loan']:
                    p.post('settle', context(snapshot))
                    deadline = time.time()+50
                    while True:
                        snapshot = p.get('account')
                        if not snapshot['paper']['positions'] and not any(o['status'] == 'pending' for o in snapshot['paper']['orders']):
                            break
                        if time.time() >= deadline:
                            raise RuntimeError('Settlement still has positions or orders')
                        time.sleep(1)
                    # Do not fabricate additional capital to repay a loss.
                    p.report['cleanup'] = snapshot
                else:
                    p.report['cleanup'] = snapshot
            except Exception as error:
                p.report.update(status='INCOMPLETE', cleanupError=str(error))
            finally:
                try:
                    p.post('logout')
                except Exception as error:
                    p.report['logoutError'] = type(error).__name__
        p.finish()
    return p.report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='https://defivela.xyz')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=600, choices=range(30, 1801), metavar='30..1800')
    parser.add_argument('--case', choices=('both', 'stops', 'financed'), default='both')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    control = Control(args.output_dir, args.seconds)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: control.stop.set())
    cases = [('stops', stop_probe), ('financed', financed_probe)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [(name, pool.submit(fn, args.base_url.rstrip('/'), args.output_dir/(name+'.json'), args.seconds, control))
                for name, fn in cases if args.case in ('both', name)]
        results = {name: job.result()['status'] for name, job in jobs}
    print(json.dumps(results))
    raise SystemExit(any(status != 'PASS' for status in results.values()))

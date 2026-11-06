#!/usr/bin/env python3
"""Check long/short paper fills and partial exits in a NEW disposable demo desk.

Uses actual API requests and observed market data. No wallet, blockchain writes,
autonomous AI, model charges or existing account changes. Leaves the desk flat.
"""
import argparse
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import httpx


def check(base_url):
    report = dict(at=datetime.now(timezone.utc).isoformat(), baseUrl=base_url,
                  execution='PAPER', walletActions=False, autonomousStarted=False,
                  source='Observed market data; actual API and paper execution', positions=[])
    with httpx.Client(base_url=base_url, timeout=30, headers={
        'Origin':base_url, 'X-ADP-Client':'web', 'X-Vela-Paper':secrets.token_hex(32)}) as client:
        def request(path, body=None):
            response = client.get('/api/paper/'+path) if body is None else client.post('/api/paper/'+path,json=body)
            if response.is_error:
                raise RuntimeError(f'{path}: HTTP {response.status_code}: {response.text}')
            return response.json()

        def wait_for(predicate, label):
            deadline = time.monotonic()+25
            while True:
                snapshot = request('account')
                if predicate(snapshot):
                    return snapshot
                if time.monotonic() >= deadline:
                    raise RuntimeError(label+': '+json.dumps([{'status':o['status'],'error':o.get('error')} for o in snapshot['orders'][:4]]))
                time.sleep(.5)

        def close(position, fraction):
            return request('positions/'+position['id']+'/close',
                           dict(clientId='review-'+uuid.uuid4().hex, fraction=fraction))

        session_created = False
        try:
            first = request('session', {})
            session_created = True
            report['initialBalance'] = first['balance']
            health = client.get('/api/health').json()
            assert health['mode']=='PAPER' and not health['liveEnabled']
            markets = request('markets')
            assert markets['connected'] and all(m['fresh'] for m in markets['markets']), 'Fresh observed markets required'
            for symbol, side in [('BTC/USD','long'),('ETH/USD','short')]:
                request('orders', dict(clientId='review-'+uuid.uuid4().hex, symbol=symbol,
                    side=side,type='market',notional='20',leverage='1',stopBps='50',targetBps='100',riskBudget='1'))
                snapshot = wait_for(lambda a: bool(a['positions']), 'Entry did not fill')
                p = snapshot['positions'][0]
                sign = D(1 if side=='long' else -1)
                stop_loss = (D(p['entry'])-D(p['stop']))*D(p['qty'])*sign+D(p['paid_fees'])+D(p['stop'])*D(p['qty'])*D('.0004')
                assert D(p['riskBudget'])<=1 and stop_loss<=1, 'Stop budget exceeded'
                row = {k:p[k] for k in ('id','symbol','side','qty','entry','stop','riskBudget')}
                row['feeInclusiveStopTriggerLoss'] = str(stop_loss)
                report['positions'].append(row)
                close(p,'.5')
                partial = wait_for(lambda a: not a['positions'] or D(a['positions'][0]['qty'])<D(p['qty']), 'Partial exit did not fill')
                if partial['positions']:
                    remaining=partial['positions'][0]
                    assert D(remaining['riskBudget'])<=1
                    close(remaining,'1')
                wait_for(lambda a: not a['positions'] and not any(o['status']=='pending' for o in a['orders']), 'Final exit did not fill')
            final = request('account')
            report['trades'] = [{k:t.get(k) for k in ('symbol','side','qty','entry','exit','gross','fees','funding','pnl','reason','chain_status')} for t in final['trades']]
            total = sum(D(t['pnl']) for t in final['trades'])
            # Funding and balances accumulate at Decimal's 28-digit precision;
            # summing closed P&L in a different order can differ below 1e-22 USD.
            delta = D(final['balance']) - (D(first['balance'])+total)
            report.update(finalBalance=final['balance'], decimalAccumulationDelta=str(delta))
            assert abs(delta) <= D('1e-22'), 'Paper balance does not reconcile to net P&L'
            assert not final['preferences']['autonomous']
            report.update(status='PASS',finalBalance=final['balance'],realizedNetPnl=str(total),
                          scope='This new diagnostic session only; counts are closed fills, including partial exits',
                          closedFills=len(final['trades']))
        except Exception as error:
            report.update(status='FAIL',error=str(error))
        finally:
            try:
                if not session_created:
                    report['cleanup'] = {'sessionEstablished': False}
                    return report
                remaining = request('account')
                for order in remaining['orders']:
                    if order['status']=='pending':request('orders/'+order['id']+'/cancel',{})
                for p in request('account')['positions']:close(p,'1')
                final = wait_for(lambda a: not a['positions'] and not any(o['status']=='pending' for o in a['orders']), 'Cleanup did not finish')
                report['cleanup'] = dict(positions=len(final['positions']),pendingOrders=0,autonomous=final['preferences']['autonomous'])
            except Exception as error:
                report.update(status='FAIL',cleanupError=str(error))
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8802')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=check(args.base_url.rstrip('/'))
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    raise SystemExit(result['status']!='PASS')

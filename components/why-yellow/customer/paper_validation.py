"""Deterministic forward-paper qualification; never authorizes live trading.

Keep the existing minimum of 50 forward trades across at least 24 hours, a
1.2 profit factor and a $10 drawdown ceiling. Recompute fees and add an adverse
10bp-per-fill stress. Passing is evidence for review, not a profit guarantee.
"""
from decimal import Decimal as D


def evaluate(trades, side):
    result = dict(qualified=False, reason='insufficient_forward_evidence', trades=0,
                  stress_net='0', max_drawdown='0', elapsed_seconds=0,
                  fee_floor_per_fill='.0004', slippage_stress_per_fill='.001')
    try:
        if side not in ('long', 'short'):
            raise ValueError('unknown direction')
        rows = sorted((t for t in trades if t.get('phase') == 'FORWARD_PAPER'
                       and t.get('valid') is True and t.get('reason') != 'OWNER_CLOSE'
                       and t.get('fee_model_version') == 2), key=lambda t: t['closed'])
        seen = set(); total = peak = drawdown = gain = loss = D(0)
        opened = []; closed = []
        for row in rows:
            if not row.get('id') or row['id'] in seen:
                raise ValueError('duplicate or missing trade identity')
            seen.add(row['id'])
            q, entry, exit_price, fees, funding, net, start, end = (
                D(str(row[k])) for k in ('qty','entry','exit','fees','funding','net','opened','closed'))
            if (not all(x.is_finite() for x in (q,entry,exit_price,fees,funding,net,start,end))
                    or min(q,entry,exit_price) <= 0 or fees < 0 or start < 0 or end <= start):
                raise ValueError('invalid economics')
            gross = (exit_price-entry)*q*(1 if side=='long' else -1)
            if abs(net-(gross-fees+funding)) > D('.00001'):
                raise ValueError('net does not reconcile')
            costed_fees = max(fees, q*(entry+exit_price)*D('.0004'))
            # Charge additional adverse execution even if the simulator included
            # spread/slippage, making this an explicit conservative stress test.
            stress = q*(entry+exit_price)*D('.001')*(1+D('.0004'))
            value = gross-costed_fees+funding-stress
            total += value; peak = max(peak,total); drawdown = max(drawdown,peak-total)
            gain += max(value,0); loss += max(-value,0)
            opened.append(start); closed.append(end)
        elapsed = max(closed)-min(opened) if rows else D(0)
        enough = len(rows) >= 50 and elapsed >= 86400
        good = total > 0 and drawdown <= 10 and (loss == 0 or gain/loss >= D('1.2'))
        result.update(qualified=enough and good, trades=len(rows), stress_net=str(total),
                      max_drawdown=str(drawdown), elapsed_seconds=float(elapsed),
                      reason='passes_for_review' if enough and good else
                             'cost_adjusted_performance_failed' if enough else 'insufficient_forward_evidence')
    except (KeyError, TypeError, ValueError, ArithmeticError):
        result.update(qualified=False, reason='paper_economics_unconfirmed')
    return result

"""Repayment services over the existing loan, cash and event ledgers.

All mutations run inside store.once / a caller's IMMEDIATE transaction. No
second balance, debt ledger or schema migration is introduced.
"""
import calendar
import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D

from vela.db import get, put
from .lending import Lending, number, down, up
from .store import cashflow, event, once


def amount(value):
    value = number(value)
    if value <= 0 or value != down(value):
        raise ValueError('Use a positive amount with at most six decimal places')
    return value


def next_due(due, frequency, now, day=None):
    if frequency in ('daily', 'weekly'):
        interval = 86400 if frequency == 'daily' else 604800
        return due + (int(max(0, now - due) // interval) + 1) * interval
    date = datetime.fromtimestamp(due, timezone.utc)
    day = day or date.day
    while date.timestamp() <= now:
        year, month = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
        date = date.replace(year=year, month=month, day=min(day, calendar.monthrange(year, month)[1]))
    return date.timestamp()


class Repayment:
    def __init__(self, db, paper, clock=None):
        self.db, self.paper, self.clock = db, paper, clock or (lambda: time.time())
        self.lending = Lending(db, self.clock)

    def free(self, owner):
        """Settled cash less margin, losses, entry/exit fees and pending entries.

        Unrealized gains never finance a repayment or withdrawal. Funding is
        already debited by the engine; incomplete observations fail closed.
        """
        row = self.db.execute('SELECT balance FROM users WHERE address=?', (owner,)).fetchone()
        free = number(row['balance'])
        if get(self.db, 'paper-funding-incomplete:' + owner, False):
            return D(0), 'Funding history is incomplete; reconcile before moving equity'
        try:
            for p in self.paper.getPosition(owner):
                market = self.paper.market(p['symbol'])
                qty, entry = number(p['qty']), number(p['entry'])
                sign = D(1) if p['side'] == 'long' else D(-1)
                unrealized = (market.mark - entry) * qty * sign
                exit_fee = max(self.paper.settings.taker_fee + self.paper.settings.platform_fee,
                               number(self.paper.meta(p).get('exitFee', '0')))
                free -= entry * qty / number(p['leverage']) + number(p['entry_fee_left'])
                free -= max(D(0), -unrealized) + up(max(entry, market.mark) * qty * exit_fee)
            for row in self.db.execute("SELECT payload FROM paper_orders WHERE user=? AND status='pending'", (owner,)):
                order = json.loads(row['payload'])
                if not order.get('reduceOnly'):
                    self.paper.market(order['symbol'])
                    notional = number(order['notional'])
                    free -= notional / number(order['leverage']) + up(notional * (self.paper.settings.taker_fee + self.paper.settings.platform_fee) * 2)
        except ValueError as exc:
            return D(0), str(exc)
        return max(D(0), down(free)), None

    def status(self, owner):
        free, reason = self.free(owner)
        loan = self.lending.loan(owner)
        debt = self.lending.debt(loan)[0]
        repayable = loan and loan['state'] in ('ACTIVE', 'SHORTFALL')
        return dict(freeEquity=str(free), maxRepayable=str(min(free, debt) if repayable else D(0)),
                    debt=str(debt), restriction=reason, policy=get(self.db, 'repayment-policy:' + owner),
                    simulated=True)

    def pay(self, owner, requested, method='manual', minimum='0', full=False):
        requested = amount(requested)
        loan = self.lending.loan(owner)
        if not loan or loan['state'] not in ('ACTIVE', 'SHORTFALL'):
            raise ValueError('No repayable loan; finish any pending settlement first')
        free, reason = self.free(owner)
        now=self.clock()
        debt = self.lending.debt(loan,now)[0]
        if full:
            if debt>requested:raise ValueError('Accrued debt exceeds the authorized maximum; review a fresh repayment quote')
            requested=debt
        maximum = min(debt, max(D(0), free - number(minimum)))
        if requested > maximum:
            raise ValueError(f'Maximum repayable is {maximum} simulated USDC. ' +
                             (reason or 'Keep position margin reserved or confirm Stop & Settle.'))
        result = self.lending.repay_amount(owner, requested, now)
        event(self.db, owner, loan['id'], 'SIMULATED_REPAYMENT_RECEIPT', requested,
              json.dumps(dict(method=method, principal=result['principal'], interest=result['interest'],
                              disclosure='Simulated repayment; no stablecoin transferred')))
        if result['state'] in ('AWAITING_CHAIN','REPAID'):
            self.paper.pause_entries(owner)
            self.disable(owner, 'Debt fully repaid; review settlement status')
        return dict(amount=str(requested), loan=result, health=self.lending.health(owner),
                    repayment=self.status(owner), simulated=True,
                    disclosure='Simulated repayment; no stablecoin transferred')

    def realized_net(self, owner):
        # Entry and exit fees (including still-open positions), and ALL observed
        # funding costs, are deducted. No unrealized gain or loan cashflow.
        gross = sum((number(r[0]) for r in self.db.execute('SELECT gross FROM trades WHERE user=?', (owner,))), D(0))
        costs = self.paper.cost_totals(owner)
        return gross - number(costs.get('tradingFee', '0')) - number(costs.get('funding', '0'))

    def configure(self, owner, body):
        if body.get('confirm') is not True:
            raise ValueError('Explicit automated repayment authorization required')
        if body.get('enabled') is False:
            return self.disable(owner, 'Revoked by wallet owner')
        loan = self.lending.loan(owner)
        if not loan or loan['state'] != 'ACTIVE':
            raise ValueError('Active loan required for automated repayment')
        percent = number(body.get('sweepPercent', '0'))
        if not 0 <= percent <= 100:
            raise ValueError('Profit Sweep percentage must be between 0 and 100')
        frequency = body.get('frequency', 'off')
        if frequency not in ('off', 'daily', 'weekly', 'monthly'):
            raise ValueError('Choose daily, weekly or monthly repayment')
        if body.get('source', 'trading') != 'trading':
            raise ValueError('Only available paper trading equity is supported')
        maximum = amount(body.get('maximum', '0')) if frequency != 'off' else D(0)
        minimum = number(body.get('minimum', '0'))
        if minimum < 0 or minimum != down(minimum):
            raise ValueError('Invalid minimum remaining balance')
        days = number(body.get('days', 90))
        if not 1 <= days <= 365 or days != int(days):
            raise ValueError('Permission must expire in 1–365 whole days')
        now = self.clock()
        due=next_due(now,frequency,now) if frequency!='off' else None
        if due is not None and now+int(days)*86400<=due:
            raise ValueError('Permission must extend past the first scheduled repayment')
        old = get(self.db, 'repayment-policy:' + owner, {})
        # Editing/re-enabling cannot claim historical profits a second time.
        watermark = max(D(0), number(old.get('watermark', self.realized_net(owner))), self.realized_net(owner))
        policy = dict(enabled=bool(percent or frequency != 'off'), loanId=loan['id'],
                      revision=old.get('revision', 0) + 1, sweepPercent=str(percent),
                      watermark=str(watermark), pendingProfit='0', frequency=frequency,
                      profitBaseline=str(watermark), sweptProfitBase='0',
                      maximum=str(maximum), minimum=str(minimum), source='trading',
                      authorizedAt=now, expiresAt=now + int(days) * 86400,
                      day=datetime.fromtimestamp(now, timezone.utc).day,
                      nextDue=due)
        put(self.db, 'repayment-policy:' + owner, policy)
        event(self.db, owner, loan['id'], 'REPAYMENT_AUTHORIZED', '0', json.dumps(policy))
        return policy

    def disable(self, owner, reason):
        policy = get(self.db, 'repayment-policy:' + owner, {})
        if policy.get('enabled'):
            policy.update(enabled=False, pendingProfit='0')
            put(self.db, 'repayment-policy:' + owner, policy)
            event(self.db, owner, policy.get('loanId'), 'REPAYMENT_DISABLED', '0', reason)
        return policy

    def _run(self, owner, policy):
        loan = self.lending.loan(owner)
        now = self.clock()
        if not loan or loan['id'] != policy['loanId'] or loan['state'] not in ('ACTIVE', 'SHORTFALL') or now >= policy['expiresAt']:
            self.disable(owner, 'Permission expired or matching active debt ended')
            return
        net = self.realized_net(owner)
        if not get(self.db, 'paper-funding-incomplete:' + owner, False):
            new_profit = max(D(0), net - number(policy['watermark']))
            policy['watermark'] = str(max(number(policy['watermark']), net))
            policy['pendingProfit'] = str(number(policy['pendingProfit']) + new_profit * number(policy['sweepPercent']) / 100)
        free, reason = self.free(owner)
        capacity = max(D(0), free - number(policy['minimum']))
        percent=number(policy['sweepPercent'])/100
        # Deferred sweep credit cannot consume principal after intervening losses.
        net_entitlement=max(D(0),net-number(policy.get('profitBaseline','0'))-number(policy.get('sweptProfitBase','0')))*percent
        sweep = down(min(number(policy['pendingProfit']),net_entitlement,capacity,self.lending.debt(loan)[0]))
        if sweep > 0:
            self.pay(owner, sweep, 'profit_sweep', policy['minimum'])
            policy['pendingProfit'] = str(number(policy['pendingProfit']) - sweep)
            policy['sweptProfitBase']=str(number(policy.get('sweptProfitBase','0'))+sweep/percent)
        loan = self.lending.loan(owner)
        if self.lending.debt(loan)[0] == 0:
            policy.update(enabled=False, pendingProfit='0')
        elif policy['nextDue'] is not None and now >= policy['nextDue']:
            free, reason = self.free(owner)
            requested = min(number(policy['maximum']), self.lending.debt(loan)[0])
            if max(D(0), free - number(policy['minimum'])) >= requested and requested > 0:
                self.pay(owner, requested, 'scheduled', policy['minimum'])
                if self.lending.debt(self.lending.loan(owner))[0] == 0:
                    policy.update(enabled=False, pendingProfit='0')
            else:
                event(self.db, owner, loan['id'], 'SCHEDULED_REPAYMENT_SKIPPED', '0',
                      reason or f'Insufficient free equity; {free} available before minimum balance')
            policy['lastDue'] = policy['nextDue']
            policy['nextDue'] = next_due(policy['nextDue'], policy['frequency'], now, policy['day'])
        put(self.db, 'repayment-policy:' + owner, policy)

    def tick(self):
        for row in self.db.execute("SELECT key FROM kv WHERE key LIKE 'repayment-policy:%'").fetchall():
            owner = row['key'].split(':', 1)[1]
            # Read policy under the same write lock as its payment and watermark.
            # A crash rolls back all three. Multiple callers cannot double pay.
            from vela.db import transaction
            with transaction(self.db):
                policy = get(self.db, row['key'], {})
                if policy.get('enabled'):
                    self._run(owner, policy)


class TradingCapital:
    """Explicit demo allocation and retirement, sharing the paper cash ledger."""
    def __init__(self, db, paper):
        self.db, self.paper = db, paper

    def allocate(self, owner, value, confirm):
        if confirm is not True:
            raise ValueError('Confirm non-transferable demo trading allocation')
        value = amount(value)
        allocated = number(get(self.db, 'demo-allocated:' + owner, '0'))
        if allocated + value > 10000:
            raise ValueError('Lifetime direct demo allocation is limited to 10,000 USDC per wallet')
        # An arbitrary faucet must never rescue a loan or finance repayments.
        loan = Lending(self.db).loan(owner)
        if loan and loan['state'] not in ('REPAID', 'CANCELLED'):
            raise ValueError('Settle the existing loan before allocating direct demo capital')
        cashflow(self.db, owner, value)
        put(self.db, 'demo-allocated:' + owner, str(allocated + value))
        event(self.db, owner, None, 'DEMO_TRADING_ALLOCATION', value, 'Paper funds only; not redeemable or transferable')
        return dict(amount=str(value), simulated=True, transferable=False)

    def withdraw(self, owner, value, confirm):
        if confirm is not True:
            raise ValueError('Confirm retirement of demo equity; no real withdrawal')
        value = amount(value)
        loan = Lending(self.db).loan(owner)
        if loan and loan['state'] not in ('REPAID', 'CANCELLED'):
            raise ValueError('Repay and confirm loan settlement before retiring equity')
        free, reason = Repayment(self.db, self.paper).free(owner)
        if value > free:
            raise ValueError(f'Maximum available demo withdrawal is {free}. ' + (reason or 'Position margin remains reserved.'))
        cashflow(self.db, owner, -value)
        put(self.db, 'demo-surplus:' + owner, str(number(get(self.db, 'demo-surplus:' + owner, '0')) + value))
        event(self.db, owner, None, 'DEMO_EQUITY_WITHDRAWAL', value, 'Removed from trading; not redeemable or transferred to a wallet')
        return dict(amount=str(value), simulated=True, transferable=False)

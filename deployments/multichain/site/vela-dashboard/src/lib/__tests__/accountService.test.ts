import { describe, expect, it } from 'vitest';
import { AccountService, planAction, validateAccount, type AccountSnapshot, type ApiRequest, type SessionContext, type ServiceAction } from '../vela/accountService';
const context: SessionContext = { address: `0x${'11'.repeat(20)}`, network: 'dogeos', generation: 1 };
const account = (): AccountSnapshot => ({
  address: context.address, network: 'dogeos', vault: { address: 'vault', collateral: '1000000000000000000', loanState: 2 },
  health: { debt: '400', healthFactor: '2', loan: { id: 'loan', state: 'ACTIVE', principal: '400', interest: '0' } },
  paper: { mode: 'PAPER', balance: '100.009999', equity: '25', unrealized: '-75.009999', positions: [{ id: 'p1', symbol: 'BTC/USD', side: 'long', qty: '0.001', entry: '60000', stop: '59900', riskBudget: '1', unrealized: '0', fresh: true }], orders: [{ id: 'o1', status: 'pending' }], today: { realized: '-2', trades: 1, wins: 0, losses: 1 }, totals: { realized: '4', trades: 3, wins: 2, losses: 1 }, preferences: { autonomous: false } },
  repayment: { simulated: true, freeEquity: '10.25', maxRepayable: '10.25', debt: '400', restriction: null, policy: null }, tradingPermission: { maxLoss: '1000000' }, events: [],
});
function setup() {
  let value = account();
  const calls: ApiRequest[] = [];
  const service = new AccountService(async request => { calls.push(request); return request.body ? { simulated: true } : structuredClone(value); });
  service.setSession(context);
  return { service, calls, set: (s: AccountSnapshot) => { value = s; } };
}

describe('authoritative service boundary', () => {
  it('requires an authenticated context and never substitutes fixture balances', async () => {
    const service = new AccountService(async () => account());
    await expect(service.refresh()).rejects.toThrow('context');
    expect(() => validateAccount({ ...account(), address: `0x${'22'.repeat(20)}` }, context)).toThrow('mismatch');
    expect(() => validateAccount({ ...account(), network: 'base' }, context)).toThrow('mismatch');
    expect(() => validateAccount({ ...account(), paper: { ...account().paper, balance: 100 } }, context)).toThrow('amount');
    expect(() => validateAccount({ ...account(), paper: { ...account().paper, today: undefined } }, context)).toThrow('scoped');
  });
  it('expands authoritative Decimal risk notation exactly without accepting malformed amounts', () => {
    const a = account(); a.health!.ltv = '0E+2'; a.health!.interest = '1.50e-7'; a.health!.borrowCapacity = '1.123456789012345678901234567E+8';
    a.health!.scenarios = [{ drop: '0.1', collateralValue: '1E-18', healthFactor: '2E+1' }];
    const h = validateAccount(a, context).health!;
    expect(h.ltv).toBe('000'); expect(h.interest).toBe('0.000000150');
    expect(h.borrowCapacity).toBe('112345678.9012345678901234567');
    expect(h.scenarios![0]).toEqual({ drop: '0.1', collateralValue: '0.000000000000000001', healthFactor: '20' });
    expect(a.health!.interest).toBe('1.50e-7');
    for (const bad of ['NaN', 'Infinity', '1e1000', '1e101', '--1', '1E-999', 1]) {
      a.health!.ltv = bad as string; expect(() => validateAccount(a, context)).toThrow('risk');
    }
  });
  it('keeps reported spendable cash and P&L scopes unchanged', () => {
    const a = validateAccount(account(), context);
    expect(a.repayment.freeEquity).toBe('10.25');
    expect(a.paper.today).toEqual({ realized: '-2', trades: 1, wins: 0, losses: 1 });
    expect(a.paper.totals).toEqual({ realized: '4', trades: 3, wins: 2, losses: 1 });
  });
  it('binds the reviewed amount, loan and vault to confirmation and a stable retry key', async () => {
    const { service, calls } = setup(); await service.refresh();
    const action: ServiceAction = { kind: 'repay', amount: '1.000001' };
    const r = service.review(action, 'stable-repayment-1'); action.amount = '100';
    await expect(service.execute(r, false)).rejects.toThrow('Confirm');
    expect(Object.isFrozen(r.body)).toBe(true);
    await service.execute(r, true); await service.execute(r, true);
    const posts = calls.filter(c => c.body);
    expect(posts).toHaveLength(2);
    expect(posts.map(c => c.key)).toEqual(['stable-repayment-1', 'stable-repayment-1']);
    expect(posts[0].body).toEqual({ amount: '1.000001', full: false, confirm: true, loanId: 'loan', vaultId: 'vault' });
    await expect(service.execute({ ...r }, true)).rejects.toThrow('Confirm');
  });
  it('coalesces concurrent confirmations and sends one request', async () => {
    const { service, calls } = setup(); await service.refresh();
    const r = service.review({ kind: 'withdraw', amount: '1' }, 'concurrent-once');
    await Promise.all([service.execute(r, true), service.execute(r, true)]);
    expect(calls.filter(c => c.body)).toHaveLength(1);
  });
  it('rejects actions after wallet, network or loan identity changes', async () => {
    const { service, calls, set } = setup(); await service.refresh();
    const r = service.review({ kind: 'repay', amount: '1' }, 'changed-context');
    const other = account(); other.health!.loan!.id = 'new-loan'; set(other);
    await expect(service.execute(r, true)).rejects.toThrow('Loan or vault');
    expect(calls.filter(c => c.body)).toHaveLength(0);
    service.setSession({ ...context, generation: 2 });
    await expect(service.execute(r, true)).rejects.toThrow('Confirm');
  });
  it('ignores out-of-order reads and a response from a previous wallet session', async () => {
    const waits: ((a: AccountSnapshot) => void)[] = [];
    const service = new AccountService(() => new Promise(resolve => waits.push(resolve)));
    service.setSession(context);
    const old = service.refresh(), oldRejected = expect(old).rejects.toThrow('superseded');
    const latest = service.refresh(); waits[1](account()); await latest; waits[0](account()); await oldRejected;
    const pending = service.refresh(), rejected = expect(pending).rejects.toThrow('context changed');
    service.setSession(null); waits[2](account()); await rejected;
  });
  it('all accounts preserve stricter risk budgets; larger requests cannot lift the $2 cap', () => {
    for (const existing of ['750000', '2000000', '9000000', undefined]) {
      const a = account(); a.tradingPermission = { maxLoss: existing };
      const plan = planAction(a, { kind: 'permission', allocation: '100', positionSize: '50', maxLoss: '999' });
      expect(plan.body.maxLoss).toBe(existing === '750000' ? '0.75' : '2');
    }
    const a = account(); a.tradingPermission.maxLoss = '0';
    expect(() => planAction(a, { kind: 'permission', allocation: '100', positionSize: '50', maxLoss: '2' })).toThrow('cannot be raised');
    a.authorizationMaxLossMicro='500000';
    expect(planAction(a, { kind: 'permission', allocation: '100', positionSize: '50', maxLoss: '2' }).body.maxLoss).toBe('0.5');
    a.authorizationMaxLossMicro='0';
    expect(() => planAction(a, { kind: 'permission', allocation: '100', positionSize: '50', maxLoss: '2' })).toThrow('cannot be raised');
  });
  it('keeps sweep consent explicit and prohibits scheduled principal repayments', () => {
    const plan = planAction(account(), { kind: 'sweep', percent: 25, days: 7, floor: '5.25' });
    expect(plan.body).toEqual({ confirm: true, enabled: true, loanId: 'loan', vaultId: 'vault', sweepPercent: '25', days: 7, minimum: '5.25', frequency: 'off', source: 'trading' });
    expect(() => planAction(account(), { kind: 'sweep', percent: 100, days: NaN, floor: '0' })).toThrow('consent');
    expect(() => planAction(account(), { kind: 'sweep', percent: 100, days: 7, floor: '-1' })).toThrow();
  });
  it('protective closes remain available while stopped and use only owned ids', () => {
    expect(planAction(account(), { kind: 'close', positionId: 'p1', fraction: '0.5' })).toEqual({ path: '/api/trading/positions/p1/close', body: { confirm: true, fraction: '0.5' } });
    expect(() => planAction(account(), { kind: 'close', positionId: 'foreign', fraction: '1' })).toThrow('owned');
    expect(() => planAction(account(), { kind: 'cancel', orderId: 'foreign' })).toThrow('owned');
    expect(() => planAction(account(), { kind: 'deposit' } as unknown as ServiceAction)).toThrow('Unsupported');
  });
});

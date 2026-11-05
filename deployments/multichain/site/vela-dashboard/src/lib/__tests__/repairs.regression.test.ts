/**
 * Focused regressions for: zero/negative repayment + allowance rejection, shared spendable cash
 * (open positions, pending orders, costs reserved), sweep grant/expiry/floor validation (incl. NaN),
 * new-entry gating on stale/missing prices and blocked risk (protective management continues),
 * and carried unpaid sweep entitlement (exact 1165400 µUSD / 1 µUSD reproduction).
 * [FIXTURE] engine tests exercise the deterministic local reducer only.
 */
import { describe, expect, it } from 'vitest';
import * as SL from '../sweepLedger';
import { cashBreakdown, spendableCashUsd6 } from '../cashReserve';
import { debitAtConfirmation, fullRepayAllowance } from '../repayment';
import { entryBlockReason, paperCashFor, initialFixture, reduce, stopFor, type Action, type FixtureState, type Position } from '../vela/fixtureEngine';
import { deriveView } from '../../state/selectors';

const DAY = 86_400;
const run = (s: FixtureState, ...as: Action[]) => as.reduce(reduce, s);
const debt = (s: FixtureState) => (s.loan ? s.loan.principalUsd6 + s.loan.interestUsd6 : 0n);
const sweepReceipts = (s: FixtureState) => s.receipts.filter((r) => r.kind === 'demo-loan-repayment' && r.trigger === 'profit-sweep');
/** order-independent list of swept micro-USD amounts */
const sweptUnits = (s: FixtureState) => sweepReceipts(s).map((r) => r.amount.units).sort();

/** A failed action must throw AND leave every balance/state field unchanged. */
function expectRejected(s: FixtureState, a: Action, msg: string | RegExp) {
  const before = structuredClone(s);
  expect(() => reduce(s, a)).toThrow(msg);
  expect(s).toEqual(before);
}

const withPaper = (s: FixtureState, paper: Partial<FixtureState['paper']>): FixtureState => ({ ...s, paper: { ...s.paper, ...paper } });

describe('repayment amounts and full-repay allowances (calculation boundary)', () => {
  it('rejects negative balances and zero/negative allowances', () => {
    expect(() => fullRepayAllowance(-1n, 0n)).toThrow('negative');
    expect(() => fullRepayAllowance(0n, -1n)).toThrow('negative');
    expect(() => debitAtConfirmation(10n, 0n)).toThrow('greater than zero');
    expect(() => debitAtConfirmation(10n, -10_000n)).toThrow('greater than zero');
    expect(() => debitAtConfirmation(-1n, 10_000n)).toThrow('negative');
    const loan = { principalUsd6: 1_000_000n, interestUsd6: 5_000n };
    expect(() => SL.applyRepayment(loan, 0n)).toThrow('greater than zero');
    expect(() => SL.applyRepayment(loan, -1n)).toThrow('greater than zero');
  });
});

describe('[FIXTURE] repayment amounts and allowances (engine boundary)', () => {
  it('rejects zero/negative partial repayments and allowances with state unchanged', () => {
    const s0 = initialFixture();
    expectRejected(s0, { type: 'REPAY_PARTIAL', usd6: 0n }, 'greater than zero');
    expectRejected(s0, { type: 'REPAY_PARTIAL', usd6: -1n }, 'greater than zero');
    expectRejected(s0, { type: 'REPAY_PARTIAL', usd6: '-5' as unknown as bigint }, 'greater than zero');
    expectRejected(s0, { type: 'REPAY_FULL', maxAllowanceUsd6: 0n }, 'allowance must be greater than zero');
    expectRejected(s0, { type: 'REPAY_FULL', maxAllowanceUsd6: -10_000n }, 'allowance must be greater than zero');
  });

  it('rejects a "full" allowance that no longer covers debt instead of making it partial', () => {
    const rich = withPaper(initialFixture(), { cashUsd6: 1_000_000_000n });
    expectRejected(rich, { type: 'REPAY_FULL', maxAllowanceUsd6: 1_000_000n }, 'does not cover');
  });
});

describe('shared exact-unit spendable cash', () => {
  it('reserves open-position margin, pending orders and costs exactly; reports shortfall', () => {
    const p = { cashUsd6: 100_009_999n, openMarginUsd6: 12_500_000n, pendingOrdersUsd6: 1_000_001n, estCostsUsd6: 250_000n };
    expect(cashBreakdown(p)).toEqual({ reservedUsd6: 13_750_001n, floorUsd6: 0n, spendableUsd6: 86_259_998n, shortfallUsd6: 0n });
    expect(spendableCashUsd6(p, 86_259_998n)).toBe(0n);
    expect(cashBreakdown({ ...p, cashUsd6: 13_750_000n }).shortfallUsd6).toBe(1n);
    expect(() => spendableCashUsd6({ ...p, pendingOrdersUsd6: -1n })).toThrow('negative');
    expect(() => spendableCashUsd6(p, -1n)).toThrow('negative');
  });

  it('[FIXTURE] UI limit and engine validation use the same value, to the micro-USD', () => {
    const s0 = initialFixture();
    const v = deriveView(s0);
    expect(v.spendableCashUsd6).toBe(spendableCashUsd6(paperCashFor(s0)));
    expect(v.spendableCashUsd6).toBe(86_447_774n); // also reserve 0.750000 open loss + 0.062225 unpaid/closing costs
    expectRejected(s0, { type: 'WITHDRAW_CASH', usd6: v.spendableCashUsd6 + 1n }, 'Insufficient');
    const after = reduce(s0, { type: 'WITHDRAW_CASH', usd6: v.spendableCashUsd6 });
    expect(after.paper.cashUsd6).toBe(13_562_225n); // exactly the reserved funds remain
    expect(deriveView(after).spendableCashUsd6).toBe(0n);
  });

  it('[FIXTURE] insufficient reserved cash blocks repayments and withdrawals; state unchanged', () => {
    // cash 20.00, reserves 12.50 + 5.00 + 2.00 → spendable 0.50
    const s = withPaper(initialFixture(), { cashUsd6: 20_812_225n, openMarginUsd6: 12_500_000n, pendingOrdersUsd6: 5_000_000n, estCostsUsd6: 2_000_000n });
    expectRejected(s, { type: 'REPAY_PARTIAL', usd6: 500_001n }, 'Insufficient');
    expectRejected(s, { type: 'WITHDRAW_CASH', usd6: 500_001n }, 'Insufficient');
    expectRejected(s, { type: 'WITHDRAW_CASH', usd6: 0n }, 'greater than zero');
    const paid = reduce(s, { type: 'REPAY_PARTIAL', usd6: 500_000n });
    expect(paid.paper.cashUsd6).toBe(20_312_225n);
    const r = paid.receipts.find((x) => !s.receipts.includes(x));
    expect(r?.kind === 'demo-loan-repayment' && r.toInterest.units).toBe('500000'); // interest first
    expect(paid.loan!.principalUsd6).toBe(s.loan!.principalUsd6);
  });

  it('[FIXTURE] full repayment cannot consume funds reserved for positions', () => {
    // cash covers debt, but cash − reserves (12.75) does not
    const s = withPaper(initialFixture(), { cashUsd6: 401_000_000n });
    expect(debt(s) < 401_000_000n).toBe(true);
    expectRejected(s, { type: 'REPAY_FULL', maxAllowanceUsd6: fullRepayAllowance(s.loan!.principalUsd6, s.loan!.interestUsd6).maxAllowanceUsd6 }, 'Insufficient');
  });
});

describe('sweep grant floors and expiry durations', () => {
  it('accepts only finite integer days 1–30 and non-negative floors', () => {
    const g = (days: number, floorUsd6: bigint = 0n) => SL.sweepGrantProblem({ percent: 50, days, floorUsd6 });
    for (const bad of [Number.NaN, Infinity, -Infinity, 0, 31, 1.5, -1]) expect(g(bad)).toContain('whole number of days');
    expect(g(1)).toBeNull();
    expect(g(30)).toBeNull();
    expect(g(7, -1n)).toContain('negative');
  });

  it('[FIXTURE] engine rejects NaN/out-of-range expiry and negative floors with state unchanged', () => {
    const s0 = initialFixture();
    expectRejected(s0, { type: 'GRANT_SWEEP', percent: 50, days: Number.NaN, floorUsd6: 0n }, 'whole number of days');
    expectRejected(s0, { type: 'GRANT_SWEEP', percent: 50, days: Infinity, floorUsd6: 0n }, 'whole number of days');
    expectRejected(s0, { type: 'GRANT_SWEEP', percent: 50, days: 31, floorUsd6: 0n }, 'whole number of days');
    expectRejected(s0, { type: 'GRANT_SWEEP', percent: 50, days: 2.5, floorUsd6: 0n }, 'whole number of days');
    expectRejected(s0, { type: 'GRANT_SWEEP', percent: 50, days: 7, floorUsd6: -1n }, 'negative');
  });

  it('a stored permission with NaN expiry, >30-day span or negative floor never sweeps', () => {
    const base: SL.SweepPermission = { id: 'p', loanId: 'L', percent: 100, expiresAt: 7 * DAY, floorUsd6: 0n, createdAt: 0, revokedAt: null };
    expect(SL.permissionProblem({ ...base, expiresAt: Number.NaN }, 'L', 10)).toBe('Invalid permission expiry');
    expect(SL.permissionProblem({ ...base, expiresAt: 31 * DAY }, 'L', 10)).toBe('Invalid permission expiry');
    expect(SL.permissionProblem({ ...base, floorUsd6: -1n }, 'L', 10)).toBe('Invalid floor');
    const l = SL.applyFill(SL.newLedger('L'), { fillId: 'f', closedAt: 1, grossPnlUsd6: 1_000_000n, entryFeeUsd6: 0n, exitFeeUsd6: 0n, fundingUsd6: 0n });
    const cash = { availableUsd6: 50_000_000n, openMarginUsd6: 0n, pendingOrdersUsd6: 0n, estCostsUsd6: 0n };
    expect(SL.quoteSweep(l, { ...base, expiresAt: Number.NaN }, { principalUsd6: 10_000_000n, interestUsd6: 0n }, cash, 10).amountUsd6).toBe(0n);
  });
});

describe('[FIXTURE] new entries blocked on stale/missing price or blocked risk; protection continues', () => {
  const eth: Position = {
    id: 'p-eth', symbol: 'ETH-PERP', side: 'long', entryPriceUsd6: 3_000_000_000n, markUsd6: 3_000_000_000n,
    qty: 100_000n, qtyScale: 100_000_000n, tickUsd6: 10_000n, entryFeeUsd6: 1_500n, fundingUsd6: 0n, realizedLossPartialsUsd6: 0n, openedAt: 0,
  };
  const open: Action = { type: 'OPEN_POSITION', position: eth, marginUsd6: 1_000_000n };

  it('allows an entry when price is fresh and risk permits (with a valid $2 stop)', () => {
    const s0 = initialFixture();
    expect(entryBlockReason(s0)).toBeNull();
    const s = reduce(s0, open);
    expect(s.positions).toHaveLength(3);
    expect(s.paper.openMarginUsd6).toBe(s0.paper.openMarginUsd6 + 1_000_000n);
    const st = stopFor(s.positions[2]);
    expect(st.ok && st.worstLossAtTriggerUsd6 <= 2_000_000n).toBe(true);
    expectRejected(s, open, 'already open'); // duplicate retry
  });

  it('stale price blocks new entries; existing protective management continues', () => {
    const s = reduce(initialFixture(), { type: 'TOGGLE_STALE' });
    expect(entryBlockReason(s)).toContain('Risk unavailable');
    expect(deriveView(s).entriesBlockedBy).toBe(entryBlockReason(s)); // UI uses the same gate
    expectRejected(s, open, 'New entry blocked');
    expect(stopFor(s.positions[0]).ok).toBe(true);
    const closed = reduce(s, { type: 'CLOSE', positionId: 'p-btc', fraction: 'half' });
    expect(closed.positions.find((p) => p.id === 'p-btc')!.qty).toBe(25_000n);
    expect(stopFor(closed.positions.find((p) => p.id === 'p-btc')!).ok).toBe(true);
  });

  it('missing price (zero quote) blocks new entries', () => {
    const s0 = initialFixture();
    const s = { ...s0, quote: { ...s0.quote, usd6PerDoge: 0n } };
    expect(entryBlockReason(s)).toContain('Risk unavailable');
    expectRejected(s, open, 'New entry blocked');
  });

  it('risk policy block (HF < 1.15) blocks entries; closing still works', () => {
    const s = reduce(initialFixture(), { type: 'SET_PRICE', usd6: 70_000n });
    expect(entryBlockReason(s)).toContain('HF below 1.15');
    expectRejected(s, open, 'HF below 1.15');
    expect(reduce(s, { type: 'CLOSE', positionId: 'p-doge', fraction: 'all' }).positions.some((p) => p.id === 'p-doge')).toBe(false);
  });

  it('entry margin cannot use reserved cash', () => {
    const s = withPaper(initialFixture(), { cashUsd6: 13_250_000n }); // spendable 0.50
    expectRejected(s, open, 'Insufficient');
  });
});

describe('carried unpaid sweep entitlement (exact reproduction: 1165400 µUSD profit, 1 µUSD cash)', () => {
  const perm = (over: Partial<SL.SweepPermission> = {}): SL.SweepPermission => ({ id: 'p', loanId: 'L', percent: 100, expiresAt: 7 * DAY, floorUsd6: 0n, createdAt: 0, revokedAt: null, ...over });
  const profit: SL.RealizedFill = { fillId: 'f-prof', closedAt: 5, grossPnlUsd6: 1_165_400n, entryFeeUsd6: 0n, exitFeeUsd6: 0n, fundingUsd6: 0n };
  const loan0 = { principalUsd6: 400_000_000n, interestUsd6: 500_000n };
  const tight = (avail: bigint): SL.CashState => ({ availableUsd6: avail, openMarginUsd6: 12_500_000n, pendingOrdersUsd6: 0n, estCostsUsd6: 250_000n });
  const released: SL.CashState = { availableUsd6: 12_750_000n, openMarginUsd6: 0n, pendingOrdersUsd6: 0n, estCostsUsd6: 0n };

  function partiallyPaid() {
    const l = SL.applyFill(SL.anchorAtAuthorization(SL.newLedger('L')), profit);
    const q1 = SL.quoteSweep(l, perm(), loan0, tight(12_750_001n), 10); // spendable = 1 µUSD
    expect(q1).toMatchObject({ amountUsd6: 1n, toInterestUsd6: 1n, entitlementAddedUsd6: 1_165_400n, owedAfterUsd6: 1_165_399n });
    const l1 = SL.commitSweep(l, 's1', q1);
    const loan1 = SL.applyRepayment(loan0, q1.amountUsd6);
    return { l, q1, l1, loan1 };
  }

  it('partial payment carries 1165399 µUSD; it is paid exactly once after reserved cash is released', () => {
    const { q1, l1, loan1 } = partiallyPaid();
    expect(l1.owedUsd6).toBe(1_165_399n);
    expect(l1.sweptTotalUsd6).toBe(1n);
    expect(SL.commitSweep(l1, 's1', q1)).toBe(l1); // duplicate retry
    expect(() => SL.commitSweep(l1, 's-replay', q1)).toThrow('Stale'); // old quote can't be re-applied
    // cash now exhausted (repayment used the 1 µUSD): nothing paid, entitlement preserved
    const qNone = SL.quoteSweep(l1, perm(), loan1, tight(12_750_000n), 11);
    expect(qNone.amountUsd6).toBe(0n);
    expect(SL.commitSweep(l1, 's-none', qNone)).toBe(l1);
    // reserved cash released
    const q2 = SL.quoteSweep(l1, perm(), loan1, released, 12);
    expect(q2).toMatchObject({ amountUsd6: 1_165_399n, entitlementAddedUsd6: 0n, owedAfterUsd6: 0n, toInterestUsd6: 499_999n, toPrincipalUsd6: 665_400n });
    const l2 = SL.commitSweep(l1, 's2', q2);
    expect(l2.sweptTotalUsd6).toBe(1_165_400n); // 1 + 1165399, exactly once
    expect(l2.owedUsd6).toBe(0n);
    expect(SL.commitSweep(l2, 's2', q2)).toBe(l2);
    expect(SL.quoteSweep(l2, perm(), SL.applyRepayment(loan1, q2.amountUsd6), released, 13).amountUsd6).toBe(0n);
  });

  it('survives restart (serialization) and is not duplicated or erased by reauthorization', () => {
    const { l1, loan1 } = partiallyPaid();
    const restarted: SL.SweepLedger = JSON.parse(JSON.stringify(l1, (_k, v) => (typeof v === 'bigint' ? `${v}n` : v)), (_k, v) => (typeof v === 'string' && /^-?\d+n$/.test(v) ? BigInt(v.slice(0, -1)) : v));
    expect(SL.quoteSweep(restarted, perm(), loan1, released, 12).amountUsd6).toBe(1_165_399n);
    const reauth = SL.anchorAtAuthorization(l1);
    expect(reauth.owedUsd6).toBe(1_165_399n);
    const q = SL.quoteSweep(reauth, perm({ id: 'p2' }), loan1, released, 12);
    expect(q.amountUsd6).toBe(1_165_399n);
    expect(q.entitlementAddedUsd6).toBe(0n); // no second entitlement from the same profit
  });

  it('revocation, expiry and wrong loan pause payment without forfeiting entitlement', () => {
    const { l1, loan1 } = partiallyPaid();
    for (const [p, now] of [[perm({ revokedAt: 11 }), 12], [perm(), 7 * DAY], [perm({ loanId: 'X' }), 12]] as const) {
      const q = SL.quoteSweep(l1, p, loan1, released, now);
      expect(q.amountUsd6).toBe(0n);
      expect(q.owedAfterUsd6).toBe(1_165_399n);
    }
  });

  it('later losses suspend carried payments until fully recovered', () => {
    const { l1, loan1 } = partiallyPaid();
    let l = SL.applyFill(l1, { ...profit, fillId: 'f-loss', grossPnlUsd6: -500_000n });
    l = SL.applyFill(l, { ...profit, fillId: 'f-back', grossPnlUsd6: 300_000n });
    const q = SL.quoteSweep(l, perm(), loan1, released, 12);
    expect(q.entitlementAddedUsd6).toBe(0n);
    expect(q.amountUsd6).toBe(0n);
    expect(q.reason).toContain('Recovering');
    expect(SL.commitSweep(l, 's2', q).highWaterUsd6).toBe(l1.highWaterUsd6); // mark not moved by losses
  });

  it('[FIXTURE] engine: 1 µUSD paid, 1165399 µUSD paid exactly once after release; retries add nothing', () => {
    let s = run(initialFixture(), { type: 'GRANT_SWEEP', percent: 100, days: 7, floorUsd6: 0n });
    s = { ...s, sweep: SL.applyFill(s.sweep, profit), paper: { ...s.paper, cashUsd6: 13_562_226n } }; // includes open losses/costs, spendable 1 µUSD
    const debt0 = debt(s);
    const s1 = run(s, { type: 'RUN_SWEEP' }, { type: 'RUN_SWEEP' }); // second run: no cash → nothing
    expect(sweptUnits(s1)).toEqual(['1']);
    expect(s1.sweep.owedUsd6).toBe(1_165_399n);
    const s2 = withPaper(s1, { openMarginUsd6: 0n, estCostsUsd6: 0n, cashUsd6: 12_750_000n }); // reserved cash released
    const s3 = reduce(s2, { type: 'RUN_SWEEP' });
    expect(reduce(s2, { type: 'RUN_SWEEP' })).toEqual(s3); // retry of the same evaluation is deterministic
    const s4 = run(s3, { type: 'RUN_SWEEP' }, { type: 'RUN_SWEEP' });
    expect(sweptUnits(s4)).toEqual(['1', '1165399']);
    expect(s4.sweep.sweptTotalUsd6).toBe(1_165_400n);
    expect(s4.sweep.owedUsd6).toBe(0n);
    expect(debt(s4)).toBe(debt0 - 1_165_400n);
  });

  it('[FIXTURE] engine: revoke → no payment; reauthorize → remaining paid once', () => {
    let s = run(initialFixture(), { type: 'GRANT_SWEEP', percent: 100, days: 7, floorUsd6: 0n });
    s = { ...s, sweep: SL.applyFill(s.sweep, profit), paper: { ...s.paper, cashUsd6: 13_562_226n } };
    s = run(s, { type: 'RUN_SWEEP' }, { type: 'REVOKE_SWEEP' });
    s = withPaper(s, { openMarginUsd6: 0n, estCostsUsd6: 0n, cashUsd6: 12_750_000n });
    s = reduce(s, { type: 'RUN_SWEEP' });
    expect(sweepReceipts(s)).toHaveLength(1);
    expect(s.sweep.owedUsd6).toBe(1_165_399n);
    s = run(s, { type: 'GRANT_SWEEP', percent: 100, days: 7, floorUsd6: 0n }, { type: 'RUN_SWEEP' }, { type: 'RUN_SWEEP' });
    expect(sweptUnits(s)).toEqual(['1', '1165399']);
    expect(s.sweep.sweptTotalUsd6).toBe(1_165_400n);
  });
});

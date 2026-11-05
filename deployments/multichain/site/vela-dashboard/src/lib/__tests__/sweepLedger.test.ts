import { describe, expect, it } from 'vitest';
import * as SL from '../sweepLedger';

const fill = (id: string, gross: bigint): SL.RealizedFill => ({ fillId: id, closedAt: 0, grossPnlUsd6: gross, entryFeeUsd6: 10_000n, exitFeeUsd6: 10_000n, fundingUsd6: 5_000n });
const perm = (over: Partial<SL.SweepPermission> = {}): SL.SweepPermission => ({ id: 'p', loanId: 'L', percent: 50, expiresAt: 1_000, floorUsd6: 0n, createdAt: 0, revokedAt: null, ...over });
const loan = { principalUsd6: 100_000_000n, interestUsd6: 300_000n };
const cash: SL.CashState = { availableUsd6: 50_000_000n, openMarginUsd6: 0n, pendingOrdersUsd6: 0n, estCostsUsd6: 0n };

describe('profit sweep ledger', () => {
  it('nets fees and funding; fills are idempotent', () => {
    let l = SL.applyFill(SL.newLedger('L'), fill('f1', 1_025_000n));
    l = SL.applyFill(l, fill('f1', 1_025_000n));
    expect(l.cumulativeNetUsd6).toBe(1_000_000n);
  });
  it('sweeps percent of new profit, interest first then principal', () => {
    const l = SL.applyFill(SL.newLedger('L'), fill('f1', 1_025_000n));
    const q = SL.quoteSweep(l, perm(), loan, cash, 10);
    expect(q.amountUsd6).toBe(500_000n);
    expect(q.toInterestUsd6).toBe(300_000n);
    expect(q.toPrincipalUsd6).toBe(200_000n);
  });
  it('never double sweeps after retry, restart or reauthorization', () => {
    let l = SL.applyFill(SL.newLedger('L'), fill('f1', 1_025_000n));
    const q = SL.quoteSweep(l, perm(), loan, cash, 10);
    l = SL.commitSweep(l, 's1', q);
    expect(SL.commitSweep(l, 's1', q)).toBe(l); // retry
    const restarted = JSON.parse(JSON.stringify(l, (_k, v) => (typeof v === 'bigint' ? `${v}n` : v)), (_k, v) => (typeof v === 'string' && /^-?\d+n$/.test(v) ? BigInt(v.slice(0, -1)) : v));
    expect(SL.quoteSweep(restarted, perm({ id: 'p2' }), loan, cash, 10).amountUsd6).toBe(0n);
    expect(SL.quoteSweep(SL.anchorAtAuthorization(l), perm({ id: 'p3' }), loan, cash, 10).amountUsd6).toBe(0n);
  });
  it('profit realized before authorization is not swept', () => {
    const l = SL.anchorAtAuthorization(SL.applyFill(SL.newLedger('L'), fill('f0', 5_025_000n)));
    expect(SL.quoteSweep(l, perm(), loan, cash, 10).amountUsd6).toBe(0n);
  });
  it('losses must be recovered before new profit is swept', () => {
    let l = SL.applyFill(SL.newLedger('L'), fill('f1', 1_025_000n));
    l = SL.commitSweep(l, 's1', SL.quoteSweep(l, perm(), loan, cash, 10));
    l = SL.applyFill(l, fill('f2', -975_000n)); // net -1.00
    l = SL.applyFill(l, fill('f3', 525_000n)); // net +0.50
    expect(SL.quoteSweep(l, perm(), loan, cash, 10).reason).toContain('Recovering');
  });
  it('reserves margin, pending orders, costs and floor', () => {
    const l = SL.applyFill(SL.newLedger('L'), fill('f1', 10_025_000n));
    const q = SL.quoteSweep(l, perm({ floorUsd6: 1_000_000n }), loan, { availableUsd6: 5_000_000n, openMarginUsd6: 2_000_000n, pendingOrdersUsd6: 1_000_000n, estCostsUsd6: 500_000n }, 10);
    expect(q.amountUsd6).toBe(500_000n);
  });
  it('rejects expired, revoked, wrong-loan, 0% permissions and stops at zero debt', () => {
    const l = SL.applyFill(SL.newLedger('L'), fill('f1', 1_025_000n));
    expect(SL.quoteSweep(l, perm(), loan, cash, 1_000).reason).toContain('expired');
    expect(SL.quoteSweep(l, perm({ revokedAt: 5 }), loan, cash, 10).reason).toContain('revoked');
    expect(SL.quoteSweep(l, perm({ loanId: 'X' }), loan, cash, 10).reason).toContain('different loan');
    expect(SL.quoteSweep(l, perm({ percent: 0 }), loan, cash, 10).amountUsd6).toBe(0n);
    expect(SL.quoteSweep(l, perm(), { principalUsd6: 0n, interestUsd6: 0n }, cash, 10).reason).toContain('zero');
  });
  it('caps a sweep at outstanding debt', () => {
    const l = SL.applyFill(SL.newLedger('L'), fill('f1', 10_025_000n));
    expect(SL.quoteSweep(l, perm({ percent: 100 }), { principalUsd6: 1_000_000n, interestUsd6: 0n }, cash, 10).amountUsd6).toBe(1_000_000n);
  });
});

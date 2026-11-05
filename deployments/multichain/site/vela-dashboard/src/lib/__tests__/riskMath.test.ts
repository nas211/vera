import { describe, expect, it } from 'vitest';
import { accruedInterest, assessRisk, borrowCapacity, entryPolicy, type RiskInput } from '../riskMath';

const DOGE = 10n ** 18n;
const base: RiskInput = {
  collateralWei: 10_000n * DOGE, committedToSaleWei: 0n, principalUsd6: 400_000_000n, accruedInterestUsd6: 0n,
  quote: { usd6PerDoge: 180_000n, observedAt: 1_000, source: 't' }, nowSec: 1_010,
};

describe('LTV / health factor', () => {
  it('computes conservative LTV (round up) and HF (round down)', () => {
    const r = assessRisk(base);
    expect(r.status).toBe('healthy');
    if (r.status === 'healthy') { expect(r.ltvBps).toBe(2_223n); expect(r.hfBps).toBe(29_250n); }
  });
  it('includes accrued interest in debt', () => {
    const r = assessRisk({ ...base, accruedInterestUsd6: 50_000_000n });
    if (r.status !== 'healthy') throw new Error(r.status);
    expect(r.debtUsd6).toBe(450_000_000n);
  });
  it('subtracts collateral committed to sale exactly once', () => {
    const r = assessRisk({ ...base, committedToSaleWei: 5_000n * DOGE });
    if (!('hfBps' in r)) throw new Error(r.status);
    expect(r.collateralValueUsd6).toBe(900_000_000n);
    const after = assessRisk({ ...base, collateralWei: 5_000n * DOGE, committedToSaleWei: 0n });
    if (!('hfBps' in after)) throw new Error(after.status);
    expect(after.collateralValueUsd6).toBe(900_000_000n);
  });
  it('no debt → HF not applicable (not zero)', () => {
    expect(assessRisk({ ...base, principalUsd6: 0n }).status).toBe('no-debt');
  });
  it('debt with no collateral → critical', () => {
    expect(assessRisk({ ...base, collateralWei: 0n }).status).toBe('critical-no-collateral');
  });
  it('stale or missing price → risk unavailable; borrowing and entries blocked', () => {
    const stale = { ...base, nowSec: 2_000 };
    expect(assessRisk(stale).status).toBe('unavailable');
    expect(borrowCapacity(stale)).toBeNull();
    expect(entryPolicy(assessRisk(stale)).decision).toBe('blocked');
    expect(assessRisk({ ...base, quote: null }).status).toBe('unavailable');
  });
  it('applies 1.30 reduce / 1.15 block / <1.00 breach thresholds', () => {
    expect(entryPolicy(assessRisk({ ...base, principalUsd6: 975_000_000n })).decision).toBe('reduced');
    expect(entryPolicy(assessRisk({ ...base, principalUsd6: 1_063_000_000n })).decision).toBe('blocked');
    expect(assessRisk({ ...base, principalUsd6: 1_200_000_000n }).status).toBe('threshold-breach');
  });
  it('40% origination capacity, stricter external limit preserved', () => {
    expect(borrowCapacity(base)).toBe(320_000_000n);
    expect(borrowCapacity({ ...base, externalMaxLtvBps: 3_000n })).toBe(140_000_000n);
    expect(borrowCapacity({ ...base, externalMaxLtvBps: 9_000n })).toBe(320_000_000n);
  });
  it('simple 5% interest', () => {
    expect(accruedInterest(400_000_000n, 500n, 0, 365 * 86_400)).toBe(20_000_000n);
  });
});

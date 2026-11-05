import { describe, expect, it } from 'vitest';
import { fmtUsd, formatFixed, formatUnits, parseDecimal, usdPrefill, validateAmount, USD_DECIMALS } from '../units';
import { debitAtConfirmation, fullRepayAllowance } from '../repayment';

const usdRules = { inputDecimals: 2, decimals: USD_DECIMALS, max: 50_000_000n, maxLabel: 'available' };

describe('amount validation (regression 4)', () => {
  it.each([
    ['-1', 'negative'], ['', 'Enter'], ['   ', 'Enter'], ['abc', 'digits'], ['1.2.3', 'digits'],
    ['1e5', 'digits'], ['1,000', 'digits'], ['+5', 'digits'], ['.', 'digits'],
  ])('rejects %j', (input, msg) => {
    const r = validateAmount(input, usdRules);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain(msg);
  });
  it('rejects zero, over-precision and over-limit', () => {
    expect(validateAmount('0', usdRules).ok).toBe(false);
    expect(validateAmount('0.00', usdRules).ok).toBe(false);
    expect(validateAmount('1.001', usdRules).ok).toBe(false);
    expect(validateAmount('50.01', usdRules).ok).toBe(false);
  });
  it('accepts exact limit and converts to base units', () => {
    const r = validateAmount('50', usdRules);
    expect(r).toEqual({ ok: true, value: 50_000_000n });
    expect(parseDecimal('0.000000000000000001', 18)).toEqual({ ok: true, value: 1n });
  });
});

describe('formatting', () => {
  it('formats exact and fixed values without floats', () => {
    expect(formatUnits(1_234_500_000_000_000_000n, 18)).toBe('1.2345');
    expect(formatFixed(100_009_999n, 6, 2, 'down')).toBe('100.00');
    expect(formatFixed(100_005_000n, 6, 2, 'half-up')).toBe('100.01');
    expect(formatFixed(-1_500_000n, 6, 2)).toBe('-1.50');
  });
});

describe('demo withdrawal prefill (regression 7)', () => {
  it('prefills 100.00 from 100.009999 and reports the fractional-cent remainder', () => {
    const p = usdPrefill(100_009_999n);
    expect(p.text).toBe('100.00');
    expect(p.value).toBe(100_000_000n);
    expect(p.remainder).toBe(9_999n);
  });
});

describe('full-repay allowance (regression 6)', () => {
  it('$10.00 displayed debt → $10.02 maximum authorization', () => {
    const b = fullRepayAllowance(10_000_000n, 4_321n);
    expect(fmtUsd(b.debtUsd6)).toBe('$10.00');
    expect(b.roundedUpUsd6).toBe(10_010_000n);
    expect(b.maxAllowanceUsd6).toBe(10_020_000n);
  });
  it('debits only actual debt at confirmation; buffer unused', () => {
    const d = debitAtConfirmation(10_004_500n, 10_020_000n);
    expect(d.debitedUsd6).toBe(10_004_500n);
    expect(d.unusedUsd6).toBe(15_500n);
    expect(d.remainingDebtUsd6).toBe(0n);
  });
  it('never debits above the authorized maximum', () => {
    const d = debitAtConfirmation(10_030_000n, 10_020_000n);
    expect(d.debitedUsd6).toBe(10_020_000n);
    expect(d.remainingDebtUsd6).toBe(10_000n);
  });
});

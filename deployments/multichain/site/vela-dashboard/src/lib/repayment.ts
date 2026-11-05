/**
 * Vela full-repay allowance: debt rounded UP to cents, plus a $0.01 buffer for interest that
 * accrues between review and confirmation. Only the ACTUAL debt at confirmation is debited,
 * up to this maximum; the unused buffer is never charged.
 *   e.g. debt 10.004321 → displayed $10.00 → ceil $10.01 → allowance $10.02
 *
 * Calculation boundary: negative balances, and zero/negative allowances, throw.
 * (Zero debt is allowed in fullRepayAllowance so the UI can render a "no loan" breakdown.)
 */
import { USD_CENT, ceilToCents } from './units';

export type RepayBreakdown = {
  principalUsd6: bigint;
  interestUsd6: bigint;
  debtUsd6: bigint;
  roundedUpUsd6: bigint;
  bufferUsd6: bigint;
  maxAllowanceUsd6: bigint;
};

const nonNegative = (v: bigint, label: string) => {
  if (typeof v !== 'bigint') throw new TypeError(`${label}: must be an exact bigint base-unit amount`);
  if (v < 0n) throw new RangeError(`${label} cannot be negative (${v})`);
};

export function fullRepayAllowance(principalUsd6: bigint, interestUsd6: bigint): RepayBreakdown {
  nonNegative(principalUsd6, 'principalUsd6');
  nonNegative(interestUsd6, 'interestUsd6');
  const debt = principalUsd6 + interestUsd6;
  const rounded = ceilToCents(debt);
  return {
    principalUsd6, interestUsd6, debtUsd6: debt,
    roundedUpUsd6: rounded, bufferUsd6: USD_CENT, maxAllowanceUsd6: rounded + USD_CENT,
  };
}

/** Amount actually debited at confirmation: never more than debt or the authorized maximum. */
export function debitAtConfirmation(debtAtConfirmUsd6: bigint, maxAllowanceUsd6: bigint) {
  nonNegative(debtAtConfirmUsd6, 'debtAtConfirmUsd6');
  if (typeof maxAllowanceUsd6 !== 'bigint') throw new TypeError('maxAllowanceUsd6: must be an exact bigint base-unit amount');
  if (maxAllowanceUsd6 <= 0n) throw new RangeError(`Full-repayment allowance must be greater than zero (${maxAllowanceUsd6})`);
  const debited = debtAtConfirmUsd6 < maxAllowanceUsd6 ? debtAtConfirmUsd6 : maxAllowanceUsd6;
  return { debitedUsd6: debited, unusedUsd6: maxAllowanceUsd6 - debited, remainingDebtUsd6: debtAtConfirmUsd6 - debited };
}

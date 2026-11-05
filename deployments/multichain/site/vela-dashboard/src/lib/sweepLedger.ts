/**
 * Profit-sweep ledger (DEMO USD). Sweeps only NEW realized net profit:
 *   net = gross realized − entry fee − exit fee − observed funding
 * Excludes unrealized gains, borrowed capital, deposits and DOGE rewards.
 * Losses lower the cumulative net; a high-water mark means later profit must first
 * recover losses. Fill IDs and sweep IDs are idempotent → no double sweep after retry,
 * restart or reauthorization (the ledger is per LOAN, not per permission).
 *
 * Unpaid entitlement: when an evaluation's entitlement (percent × new profit) cannot be paid in
 * full because spendable cash or debt limits the payment, the unpaid part is CARRIED in
 * `owedUsd6` (visible, never forfeited). The high-water mark only records profit that has been
 * EVALUATED into entitlement, so each unit of profit becomes entitlement exactly once and each
 * unit of entitlement is paid at most once. Carried entitlement is paid only while a valid,
 * unexpired, unrevoked permission for THIS loan exists, and stops when debt reaches zero.
 * All carried payments pause below the evaluated high-water mark until losses are recovered.
 */
import { spendableCashUsd6 } from './cashReserve';

export const SWEEP_MIN_DAYS = 1;
export const SWEEP_MAX_DAYS = 30;
const DAY = 86_400;

export type SweepPermission = {
  id: string;
  loanId: string;
  percent: number; // integer 0–100
  expiresAt: number;
  floorUsd6: bigint; // optional remaining-balance floor (0 = none, never negative)
  createdAt: number;
  revokedAt: number | null;
};

export type RealizedFill = {
  fillId: string;
  closedAt: number;
  grossPnlUsd6: bigint; // signed
  entryFeeUsd6: bigint;
  exitFeeUsd6: bigint;
  fundingUsd6: bigint; // positive = paid
};

export type SweepLedger = {
  loanId: string;
  processedFillIds: string[];
  committedSweepIds: string[];
  cumulativeNetUsd6: bigint;
  highWaterUsd6: bigint;
  sweptTotalUsd6: bigint;
  /** Evaluated-but-unpaid sweep entitlement carried to later sweeps (never forfeited). */
  owedUsd6: bigint;
  /** Carried money retains the percentage that created it; altered terms fail closed. */
  owedPercent: number | null;
  sweepAudit: { sweepId: string; permission: SweepPermission; amountUsd6: bigint }[];
};

export type CashState = {
  availableUsd6: bigint;
  openMarginUsd6: bigint;
  pendingOrdersUsd6: bigint;
  estCostsUsd6: bigint;
  unrealizedLossUsd6?: bigint;
};

export type LoanBalances = { principalUsd6: bigint; interestUsd6: bigint };

export type SweepQuote = {
  loanId: string;
  permissionId: string | null;
  permission: SweepPermission | null;
  cumulativeNetBeforeUsd6: bigint;
  highWaterBeforeUsd6: bigint;
  amountUsd6: bigint;
  toInterestUsd6: bigint;
  toPrincipalUsd6: bigint;
  /** New realized net profit above the high-water mark that this sweep evaluates (>= 0). */
  newProfitUsd6: bigint;
  /** Entitlement created from newProfitUsd6 by this evaluation. */
  entitlementAddedUsd6: bigint;
  owedBeforeUsd6: bigint;
  /** Entitlement still unpaid after this sweep — carried, not forfeited. */
  owedAfterUsd6: bigint;
  reason: string;
};

export const netOf = (f: RealizedFill) => f.grossPnlUsd6 - f.entryFeeUsd6 - f.exitFeeUsd6 - f.fundingUsd6;

export const newLedger = (loanId: string): SweepLedger => ({
  loanId, processedFillIds: [], committedSweepIds: [], cumulativeNetUsd6: 0n, highWaterUsd6: 0n, sweptTotalUsd6: 0n, owedUsd6: 0n, owedPercent: null, sweepAudit: [],
});

export function applyFill(l: SweepLedger, f: RealizedFill): SweepLedger {
  if (l.processedFillIds.includes(f.fillId)) return l; // idempotent
  return { ...l, processedFillIds: [...l.processedFillIds, f.fillId], cumulativeNetUsd6: l.cumulativeNetUsd6 + netOf(f) };
}

/**
 * On (re)authorization only profit realized AFTER consent is eligible. The mark only moves
 * forward, so reauthorizing can never re-expose already-swept profit, and unrecovered
 * losses (cumulative < high-water) still have to be earned back first. Carried, already
 * evaluated entitlement (owedUsd6) is left untouched — it is neither duplicated nor erased.
 */
export function anchorAtAuthorization(l: SweepLedger): SweepLedger {
  return l.cumulativeNetUsd6 > l.highWaterUsd6 ? { ...l, highWaterUsd6: l.cumulativeNetUsd6 } : l;
}

/**
 * Grant-time validation shared by the UI and the engine. `days` must be a FINITE INTEGER
 * 1–30 (NaN, Infinity, fractions and out-of-range values are rejected); the floor must be a
 * non-negative exact bigint.
 */
export function sweepGrantProblem(g: { percent: unknown; days: unknown; floorUsd6: unknown }): string | null {
  if (typeof g.percent !== 'number' || !Number.isInteger(g.percent) || g.percent < 0 || g.percent > 100) return 'Percent must be a whole number from 0 to 100';
  if (typeof g.days !== 'number' || !Number.isInteger(g.days) || g.days < SWEEP_MIN_DAYS || g.days > SWEEP_MAX_DAYS) {
    return `Expiry must be a whole number of days from ${SWEEP_MIN_DAYS} to ${SWEEP_MAX_DAYS}`;
  }
  if (typeof g.floorUsd6 !== 'bigint') return 'Floor must be an exact base-unit amount';
  if (g.floorUsd6 < 0n) return 'Floor cannot be negative';
  return null;
}

export function permissionProblem(p: SweepPermission | null, loanId: string, now: number): string | null {
  if (!p) return 'No sweep permission';
  if (p.revokedAt !== null) return 'Permission revoked';
  // A NaN/Infinity expiry would make `now >= expiresAt` always false (never expires) — reject it.
  if (!Number.isSafeInteger(p.createdAt) || !Number.isSafeInteger(p.expiresAt) || p.expiresAt <= p.createdAt || p.expiresAt - p.createdAt > SWEEP_MAX_DAYS * DAY) {
    return 'Invalid permission expiry';
  }
  if (!Number.isSafeInteger(now)) return 'Invalid evaluation time';
  if (now < p.createdAt) return 'Permission not yet active';
  if (now >= p.expiresAt) return 'Permission expired';
  if (p.loanId !== loanId) return 'Permission is for a different loan';
  if (!Number.isInteger(p.percent) || p.percent < 0 || p.percent > 100) return 'Invalid percentage';
  if (typeof p.floorUsd6 !== 'bigint' || p.floorUsd6 < 0n) return 'Invalid floor';
  if (p.percent === 0) return 'Percentage is 0%';
  return null;
}

export function quoteSweep(l: SweepLedger, p: SweepPermission | null, loan: LoanBalances, cash: CashState, now: number): SweepQuote {
  const owed = l.owedUsd6;
  const basis = { loanId: l.loanId, permissionId: p?.id ?? null, permission: p ? { ...p } : null, cumulativeNetBeforeUsd6: l.cumulativeNetUsd6, highWaterBeforeUsd6: l.highWaterUsd6 };
  const zero = (reason: string, newProfit = 0n): SweepQuote => ({
    ...basis, amountUsd6: 0n, toInterestUsd6: 0n, toPrincipalUsd6: 0n, newProfitUsd6: newProfit,
    entitlementAddedUsd6: 0n, owedBeforeUsd6: owed, owedAfterUsd6: owed, reason,
  });
  const debt = loan.principalUsd6 + loan.interestUsd6;
  if (debt === 0n) return zero('Debt is zero — sweeps stopped');
  const problem = permissionProblem(p, l.loanId, now);
  if (problem) return zero(problem);
  if (owed > 0n && l.owedPercent !== p!.percent) return zero('Carried entitlement requires its original percentage under fresh consent');
  const diff = l.cumulativeNetUsd6 - l.highWaterUsd6;
  if (diff < 0n) return zero('Recovering prior losses — carried entitlement suspended');
  const newProfit = diff > 0n ? diff : 0n;
  if (newProfit === 0n && owed === 0n) {
    return zero(l.cumulativeNetUsd6 < l.highWaterUsd6 ? 'Recovering prior losses — no new net profit' : 'No new realized net profit');
  }
  const added = (newProfit * BigInt(p!.percent)) / 100n;
  const due = owed + added;
  const spendable = spendableCashUsd6(
    { cashUsd6: cash.availableUsd6, openMarginUsd6: cash.openMarginUsd6, pendingOrdersUsd6: cash.pendingOrdersUsd6, estCostsUsd6: cash.estCostsUsd6, unrealizedLossUsd6: cash.unrealizedLossUsd6 },
    p!.floorUsd6,
  );
  if (spendable === 0n) return zero('Insufficient free cash after margin, pending orders, costs and floor', newProfit);
  if (due === 0n) return zero('Entitlement rounds to zero — profit carried to the next evaluation', newProfit);
  let amount = due;
  if (spendable < amount) amount = spendable;
  if (debt < amount) amount = debt;
  const toInterest = amount < loan.interestUsd6 ? amount : loan.interestUsd6;
  return {
    ...basis, amountUsd6: amount, toInterestUsd6: toInterest, toPrincipalUsd6: amount - toInterest,
    newProfitUsd6: newProfit, entitlementAddedUsd6: added, owedBeforeUsd6: owed, owedAfterUsd6: due - amount,
    reason: amount < due ? 'Ready — partial; unpaid entitlement carried' : 'Ready',
  };
}

/**
 * Commit exactly once per sweepId. Only a paying quote commits: the evaluated profit advances the
 * high-water mark and any unpaid part of the entitlement is carried in owedUsd6.
 * A quote computed against a different ledger state is rejected (never applied silently).
 */
export function commitSweep(l: SweepLedger, sweepId: string, q: SweepQuote): SweepLedger {
  if (l.committedSweepIds.includes(sweepId)) return l;
  if (q.amountUsd6 <= 0n) return l; // nothing paid → nothing evaluated; entitlement stays derivable later
  const diff = l.cumulativeNetUsd6 - l.highWaterUsd6;
  const newProfit = diff > 0n ? diff : 0n;
  if (diff < 0n || q.cumulativeNetBeforeUsd6 !== l.cumulativeNetUsd6 || q.highWaterBeforeUsd6 !== l.highWaterUsd6 || q.loanId !== l.loanId) throw new Error('Stale sweep quote — re-evaluate before committing');
  if (q.owedBeforeUsd6 !== l.owedUsd6 || q.newProfitUsd6 !== newProfit) throw new Error('Stale sweep quote — re-evaluate before committing');
  if (!q.permission || q.permission.id !== q.permissionId || q.permission.loanId !== l.loanId || (l.owedUsd6 > 0n && l.owedPercent !== q.permission.percent)) throw new Error('Sweep consent mismatch');
  if (q.entitlementAddedUsd6 !== q.newProfitUsd6 * BigInt(q.permission.percent) / 100n) throw new Error('Inconsistent sweep percentage');
  if (q.owedAfterUsd6 !== q.owedBeforeUsd6 + q.entitlementAddedUsd6 - q.amountUsd6 || q.owedAfterUsd6 < 0n) throw new Error('Inconsistent sweep quote');
  return {
    ...l,
    committedSweepIds: [...l.committedSweepIds, sweepId],
    highWaterUsd6: l.highWaterUsd6 + q.newProfitUsd6,
    owedUsd6: q.owedAfterUsd6,
    owedPercent: q.owedAfterUsd6 > 0n ? q.permission.percent : null,
    sweepAudit: [...l.sweepAudit, { sweepId, permission: { ...q.permission }, amountUsd6: q.amountUsd6 }],
    sweptTotalUsd6: l.sweptTotalUsd6 + q.amountUsd6,
  };
}

/** Apply a repayment: interest first, then principal. Zero/negative amounts are rejected. */
export function applyRepayment(loan: LoanBalances, amount: bigint): LoanBalances & { appliedUsd6: bigint } {
  if (typeof amount !== 'bigint') throw new TypeError('Repayment amount must be an exact bigint base-unit amount');
  if (amount <= 0n) throw new RangeError('Repayment amount must be greater than zero.');
  if (loan.principalUsd6 < 0n || loan.interestUsd6 < 0n) throw new RangeError('Loan balances cannot be negative');
  const debt = loan.principalUsd6 + loan.interestUsd6;
  const applied = amount < debt ? amount : debt;
  const toI = applied < loan.interestUsd6 ? applied : loan.interestUsd6;
  return { interestUsd6: loan.interestUsd6 - toI, principalUsd6: loan.principalUsd6 - (applied - toI), appliedUsd6: applied };
}

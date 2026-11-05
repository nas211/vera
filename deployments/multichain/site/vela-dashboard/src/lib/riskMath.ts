/**
 * Demo-lending risk math. Illustrative research parameters — NOT validated production settings.
 * LTV  = current debt / current collateral value
 * HF   = collateral value × liquidation threshold / current debt
 * Debt includes accrued interest. Collateral committed to an un-reconciled sale is subtracted once.
 */
import { ONE_DOGE } from './units';

export const RISK_PARAMS = {
  maxOriginationLtvBps: 4_000n, // 40%
  liquidationThresholdBps: 6_500n, // 65%
  loanAprBps: 500n, // 5% simple
  reduceEntryHfBps: 13_000n, // HF < 1.30 → reduce new entry size
  blockEntryHfBps: 11_500n, // HF < 1.15 → block new entries
  maxQuoteAgeSec: 60,
} as const;

const BPS = 10_000n;
const YEAR = 365n * 24n * 3600n;

export type PriceQuote = { usd6PerDoge: bigint; observedAt: number; source: string };

export type RiskInput = {
  collateralWei: bigint;
  /** collateral already committed to a pending (un-reconciled) sale */
  committedToSaleWei: bigint;
  principalUsd6: bigint;
  accruedInterestUsd6: bigint;
  quote: PriceQuote | null;
  nowSec: number;
  /** stricter external limit, if any (bps). The stricter of this and defaults wins. */
  externalMaxLtvBps?: bigint;
};

export type RiskResult =
  | { status: 'unavailable'; reason: string }
  | { status: 'no-debt'; collateralValueUsd6: bigint }
  | { status: 'critical-no-collateral'; debtUsd6: bigint }
  | {
      status: 'healthy' | 'reduce-entries' | 'block-entries' | 'threshold-breach';
      collateralValueUsd6: bigint; debtUsd6: bigint; ltvBps: bigint; hfBps: bigint;
    };

export const quoteAge = (q: PriceQuote | null, now: number) => (q ? now - q.observedAt : Infinity);
export const isQuoteFresh = (q: PriceQuote | null, now: number) =>
  !!q && q.usd6PerDoge > 0n && quoteAge(q, now) >= 0 && quoteAge(q, now) <= RISK_PARAMS.maxQuoteAgeSec;

export const valueUsd6 = (wei: bigint, q: PriceQuote) => (wei * q.usd6PerDoge) / ONE_DOGE;

export function assessRisk(i: RiskInput): RiskResult {
  const debt = i.principalUsd6 + i.accruedInterestUsd6;
  const effectiveWei = i.collateralWei - i.committedToSaleWei;
  if (effectiveWei <= 0n && debt > 0n) return { status: 'critical-no-collateral', debtUsd6: debt };
  if (!isQuoteFresh(i.quote, i.nowSec)) {
    return { status: 'unavailable', reason: i.quote ? `Price quote is stale (${quoteAge(i.quote, i.nowSec)}s old)` : 'No price quote' };
  }
  const value = valueUsd6(effectiveWei > 0n ? effectiveWei : 0n, i.quote!);
  if (debt === 0n) return { status: 'no-debt', collateralValueUsd6: value };
  if (value === 0n) return { status: 'critical-no-collateral', debtUsd6: debt };
  const ltvBps = (debt * BPS + value - 1n) / value; // round up (conservative)
  const hfBps = (value * RISK_PARAMS.liquidationThresholdBps) / debt; // round down (conservative)
  const status =
    hfBps < BPS ? 'threshold-breach'
      : hfBps < RISK_PARAMS.blockEntryHfBps ? 'block-entries'
        : hfBps < RISK_PARAMS.reduceEntryHfBps ? 'reduce-entries' : 'healthy';
  return { status, collateralValueUsd6: value, debtUsd6: debt, ltvBps, hfBps };
}

/** Remaining borrow capacity (micro-USD), rounded down. null = unavailable. */
export function borrowCapacity(i: RiskInput): bigint | null {
  if (!isQuoteFresh(i.quote, i.nowSec)) return null;
  const maxLtv = i.externalMaxLtvBps !== undefined && i.externalMaxLtvBps < RISK_PARAMS.maxOriginationLtvBps
    ? i.externalMaxLtvBps : RISK_PARAMS.maxOriginationLtvBps;
  const effectiveWei = i.collateralWei - i.committedToSaleWei;
  if (effectiveWei <= 0n) return 0n;
  const cap = (valueUsd6(effectiveWei, i.quote!) * maxLtv) / BPS - (i.principalUsd6 + i.accruedInterestUsd6);
  return cap > 0n ? cap : 0n;
}

export type EntryPolicy = { decision: 'allowed' | 'reduced' | 'blocked'; reason: string };

export function entryPolicy(r: RiskResult): EntryPolicy {
  switch (r.status) {
    case 'unavailable': return { decision: 'blocked', reason: `Risk unavailable: ${r.reason}` };
    case 'critical-no-collateral': return { decision: 'blocked', reason: 'Debt with no collateral (critical)' };
    case 'no-debt': return { decision: 'allowed', reason: 'No debt — health factor not applicable' };
    case 'threshold-breach': return { decision: 'blocked', reason: 'HF below 1.00 — threshold breach (not an executed liquidation)' };
    case 'block-entries': return { decision: 'blocked', reason: 'HF below 1.15 — new entries blocked' };
    case 'reduce-entries': return { decision: 'reduced', reason: 'HF below 1.30 — new entry size reduced' };
    default: return { decision: 'allowed', reason: 'HF at or above 1.30' };
  }
}

/** Simple (non-compounding) interest, rounded UP to the base unit. */
export function accruedInterest(principalUsd6: bigint, aprBps: bigint, fromSec: number, toSec: number): bigint {
  const dt = BigInt(Math.max(0, toSec - fromSec));
  const num = principalUsd6 * aprBps * dt;
  const den = BPS * YEAR;
  return (num + den - 1n) / den;
}

export const fmtBpsRatio = (bps: bigint) => `${bps / BPS}.${(bps % BPS).toString().padStart(4, '0').slice(0, 2)}`;
export const fmtBpsPct = (bps: bigint) => `${bps / 100n}.${(bps % 100n).toString().padStart(2, '0')}%`;

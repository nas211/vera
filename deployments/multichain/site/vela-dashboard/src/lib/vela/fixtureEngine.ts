/**
 * FIXTURE ENGINE — deterministic, local-only rehearsal state. Nothing here is live,
 * nothing is sent to a chain or to Vela. Every record carries source: 'fixture'.
 * Pure reducer so it can be exercised by unit/integration tests without a browser.
 */
import * as RM from '../rewardModel';
import { accruedInterest, assessRisk, borrowCapacity, entryPolicy, RISK_PARAMS, type PriceQuote, type RiskInput } from '../riskMath';
import { spendableCashUsd6 } from '../cashReserve';
import { reconcileStop, type StopInput, type StopResult } from '../stopBudget';
import * as SL from '../sweepLedger';
import { debitAtConfirmation } from '../repayment';
import { ONE_DOGE, ONE_USD, USD_CENT, fmtUsd, toBaseUnits, toExact, USD_DECIMALS, DOGE_DECIMALS } from '../units';
import type { Receipt } from './types';
import { INTEGRATION } from './integrationConfig';

export const FIXTURE_CHAIN_ID = 6281971;
export const FIXTURE_OWNER = 'fixture-reserve-operator';
export const FIXTURE_USER = 'fixture-user';
export const T0 = 1_790_000_000; // fixed fixture epoch (UTC)
const DAY = 86_400;
const EXIT_FEE_BPS = 5n;

export type Position = {
  id: string; symbol: string; side: 'long' | 'short';
  entryPriceUsd6: bigint; markUsd6: bigint; qty: bigint; qtyScale: bigint; tickUsd6: bigint;
  entryFeeUsd6: bigint; fundingUsd6: bigint; realizedLossPartialsUsd6: bigint; openedAt: number;
  /** Exact remaining margin; assigned by OPEN_POSITION, explicit for seeded positions. */
  marginUsd6?: bigint;
  existingLimitUsd6?: bigint;
  stopPriceUsd6?: bigint;
};

export type Session = {
  status: 'running' | 'stopped';
  entriesPaused: boolean;
  reason: string | null;
  recovery: string | null;
  seq: number;
  authorization: { scope: 'unfinanced' | 'financed'; loanId: string | null; grantedAt: number } | null;
  ignoredPolls: number;
};

export type Activity = { id: string; at: number; kind: string; text: string };
export type CollateralSale = { id: string; wei: bigint; proceedsUsd6: bigint; status: 'committed' | 'reconciled'; at: number };

export type FixtureState = {
  clock: number;
  nextId: number;
  vault: RM.RewardVaultState;
  fixtureWalletRewardsWei: bigint;
  gasEmpty: boolean;
  quote: PriceQuote;
  priceStale: boolean;
  demoCollateralWei: bigint;
  sales: CollateralSale[];
  loan: { loanId: string; principalUsd6: bigint; interestUsd6: bigint; lastAccrual: number; openedAt: number } | null;
  badDebtUsd6: bigint;
  paper: { cashUsd6: bigint; openMarginUsd6: bigint; pendingOrdersUsd6: bigint; estCostsUsd6: bigint; withdrawnUsd6: bigint };
  positions: Position[];
  fills: SL.RealizedFill[];
  sweep: SL.SweepLedger;
  permission: SL.SweepPermission | null;
  session: Session;
  /** Idempotency keys of borrow requests already applied — a repeat is a no-op. */
  processedBorrowRequests: string[];
  receipts: Receipt[];
  activity: Activity[];
};

const usd = (dollars: number) => BigInt(Math.round(dollars * 1e6));

export function initialFixture(): FixtureState {
  const start = T0 - 20 * DAY;
  let v = RM.createVault({ vaultId: 'fixture-vault', chainId: FIXTURE_CHAIN_ID, owner: FIXTURE_OWNER, aprBps: 500n, now: start });
  v = RM.topUpReserve(v, { caller: FIXTURE_OWNER, chainId: FIXTURE_CHAIN_ID, now: start }, 50n * ONE_DOGE);
  v = RM.deposit(v, { caller: FIXTURE_USER, chainId: FIXTURE_CHAIN_ID, now: start }, 10_000n * ONE_DOGE);
  v = RM.checkpoint(v, { caller: FIXTURE_USER, chainId: FIXTURE_CHAIN_ID, now: T0 });
  const loanOpened = T0 - 10 * DAY;
  return {
    clock: T0, nextId: 1, vault: v, fixtureWalletRewardsWei: 0n, gasEmpty: false,
    quote: { usd6PerDoge: 180_000n, observedAt: T0 - 12, source: 'fixture constant' },
    priceStale: false,
    demoCollateralWei: 10_000n * ONE_DOGE, sales: [],
    loan: { loanId: 'fixture-loan-1', principalUsd6: usd(400), interestUsd6: accruedInterest(usd(400), RISK_PARAMS.loanAprBps, loanOpened, T0), lastAccrual: T0, openedAt: loanOpened },
    badDebtUsd6: 0n,
    paper: { cashUsd6: 100_009_999n, openMarginUsd6: usd(12.5), pendingOrdersUsd6: 0n, estCostsUsd6: usd(0.25), withdrawnUsd6: 0n },
    // Explicit seed allocation of the original $12.50 in entry-notional proportion (30:26.25).
    positions: [
      { marginUsd6: 6_666_667n, id: 'p-btc', symbol: 'BTC-PERP', side: 'long', entryPriceUsd6: usd(60_000), markUsd6: usd(62_400), qty: 50_000n, qtyScale: 100_000_000n, tickUsd6: usd(0.5), entryFeeUsd6: 15_000n, fundingUsd6: 4_000n, realizedLossPartialsUsd6: 0n, openedAt: T0 - 3600 * 5 },
      { marginUsd6: 5_833_333n, id: 'p-doge', symbol: 'DOGE-PERP', side: 'short', entryPriceUsd6: 175_000n, markUsd6: 180_000n, qty: 150n * ONE_DOGE, qtyScale: ONE_DOGE, tickUsd6: 10n, entryFeeUsd6: 13_125n, fundingUsd6: 1_000n, realizedLossPartialsUsd6: 0n, openedAt: T0 - 3600 * 3 },
    ].map((p) => reconcilePosition(p as Position)),
    fills: [{ fillId: 'f-hist-1', closedAt: T0 - 2 * DAY, grossPnlUsd6: usd(3.1), entryFeeUsd6: 20_000n, exitFeeUsd6: 21_000n, fundingUsd6: 5_000n }],
    sweep: SL.applyFill(SL.newLedger('fixture-loan-1'), { fillId: 'f-hist-1', closedAt: T0 - 2 * DAY, grossPnlUsd6: usd(3.1), entryFeeUsd6: 20_000n, exitFeeUsd6: 21_000n, fundingUsd6: 5_000n }),
    permission: null,
    session: { status: 'running', entriesPaused: false, reason: null, recovery: null, seq: 1, authorization: { scope: 'financed', loanId: 'fixture-loan-1', grantedAt: loanOpened }, ignoredPolls: 0 },
    processedBorrowRequests: [],
    receipts: [],
    activity: [{ id: 'a0', at: T0, kind: 'fixture', text: 'Fixture scenario loaded (deterministic rehearsal data, not live)' }],
  };
}

export type Action =
  | { type: 'RESET' }
  | { type: 'ADVANCE'; seconds: number }
  | { type: 'TOGGLE_STALE' }
  | { type: 'TOGGLE_GAS' }
  | { type: 'SET_PRICE'; usd6: bigint }
  | { type: 'DEPOSIT'; wei: bigint }
  | { type: 'WITHDRAW'; wei: bigint }
  | { type: 'CLAIM' }
  | { type: 'TOPUP'; wei: bigint }
  | { type: 'BORROW'; usd6: bigint; requestId: string }
  | { type: 'REPAY_PARTIAL'; usd6: bigint }
  | { type: 'REPAY_FULL'; maxAllowanceUsd6: bigint }
  | { type: 'AUTHORIZE_TRADING'; scope: 'unfinanced' | 'financed' }
  | { type: 'START' }
  | { type: 'STOP'; reason: string; recovery: string }
  | { type: 'POLL_RESULT'; seq: number; status: 'running' | 'stopped' }
  | { type: 'CLOSE'; positionId: string; fraction: 'half' | 'all' }
  | { type: 'OPEN_POSITION'; position: Position; marginUsd6: bigint }
  | { type: 'GRANT_SWEEP'; percent: number; days: number; floorUsd6: bigint }
  | { type: 'REVOKE_SWEEP' }
  | { type: 'RUN_SWEEP' }
  | { type: 'WITHDRAW_CASH'; usd6: bigint }
  | { type: 'VOLUNTARY_SALE'; wei: bigint }
  | { type: 'RECONCILE_SALE'; id: string };

const ctx = (s: FixtureState, caller = FIXTURE_USER): RM.Ctx => ({ caller, chainId: FIXTURE_CHAIN_ID, now: s.clock });

/** Exact bigint normalization of every amount field on a position (throws, never rounds). */
export function normalizePosition(p: Position): Position {
  const b = (v: unknown, k: string) => toBaseUnits(v, `${p.id}.${k}`);
  return {
    ...p,
    entryPriceUsd6: b(p.entryPriceUsd6, 'entryPriceUsd6'), markUsd6: b(p.markUsd6, 'markUsd6'),
    qty: b(p.qty, 'qty'), qtyScale: b(p.qtyScale, 'qtyScale'), tickUsd6: b(p.tickUsd6, 'tickUsd6'),
    entryFeeUsd6: b(p.entryFeeUsd6, 'entryFeeUsd6'), fundingUsd6: b(p.fundingUsd6, 'fundingUsd6'),
    realizedLossPartialsUsd6: b(p.realizedLossPartialsUsd6, 'realizedLossPartialsUsd6'),
    marginUsd6: p.marginUsd6 === undefined ? undefined : b(p.marginUsd6, 'marginUsd6'),
    existingLimitUsd6: p.existingLimitUsd6 === undefined ? undefined : b(p.existingLimitUsd6, 'existingLimitUsd6'),
    stopPriceUsd6: p.stopPriceUsd6 === undefined ? undefined : b(p.stopPriceUsd6, 'stopPriceUsd6'),
  };
}

/** Normalize amount fields on incoming actions so UI/adapter callers can't inject numbers. */
function normalizeAction(a: Action): Action {
  switch (a.type) {
    case 'SET_PRICE': return { ...a, usd6: toBaseUnits(a.usd6, 'usd6') };
    case 'DEPOSIT': case 'WITHDRAW': case 'TOPUP': case 'VOLUNTARY_SALE': return { ...a, wei: toBaseUnits(a.wei, 'wei') };
    case 'BORROW': case 'REPAY_PARTIAL': case 'WITHDRAW_CASH': return { ...a, usd6: toBaseUnits(a.usd6, 'usd6') };
    case 'REPAY_FULL': return { ...a, maxAllowanceUsd6: toBaseUnits(a.maxAllowanceUsd6, 'maxAllowanceUsd6') };
    case 'GRANT_SWEEP': return { ...a, floorUsd6: toBaseUnits(a.floorUsd6, 'floorUsd6') };
    case 'OPEN_POSITION': return { ...a, marginUsd6: toBaseUnits(a.marginUsd6, 'marginUsd6') };
    default: return a;
  }
}

/**
 * Why new entries need a NEW demo authorization, or null if the current one is valid.
 * With a loan open, only a 'financed' grant bound to THAT loan counts — earlier
 * unfinanced consent never carries into financed trading.
 */
export function authorizationProblem(s: Pick<FixtureState, 'session' | 'loan'>): string | null {
  const auth = s.session.authorization;
  if (s.session.entriesPaused) return s.session.reason ?? 'New entries paused';
  if (!auth) return 'No demo trading authorization';
  if (s.loan && (auth.scope !== 'financed' || auth.loanId !== s.loan.loanId)) return 'Authorization does not cover the current demo loan';
  return null;
}

/** Risk input for the current fixture state — single source for the engine and the selectors. */
export function riskInputFor(s: FixtureState): RiskInput {
  const committedWei = s.sales.filter((x) => x.status === 'committed').reduce((t, x) => t + x.wei, 0n);
  return {
    collateralWei: s.demoCollateralWei, committedToSaleWei: committedWei,
    principalUsd6: s.loan?.principalUsd6 ?? 0n, accruedInterestUsd6: s.loan?.interestUsd6 ?? 0n,
    quote: s.quote, nowSec: s.clock,
  };
}

/**
 * Why a NEW trading entry is blocked right now, or null if allowed. Used by the engine's
 * OPEN_POSITION boundary AND the dashboard (selectors) — one gate, no divergent copies.
 * Missing/stale prices and risk-policy blocks stop new entries; protective management
 * (CLOSE, stops) is never gated by this.
 */
export function entryBlockReason(s: FixtureState): string | null {
  if (s.session.status === 'stopped') return 'session stopped';
  if (!s.session.authorization || s.session.entriesPaused) return 'awaiting new demo authorization';
  const scope = authorizationProblem(s);
  if (scope) return scope;
  const policy = entryPolicy(assessRisk(riskInputFor(s)));
  if (policy.decision === 'blocked') return policy.reason;
  if (policy.decision === 'reduced') return 'Reduced-risk sizing is not verified — new entries blocked';
  if (s.priceStale) return 'Risk unavailable: price quote flagged stale';
  return null;
}

function log(s: FixtureState, kind: string, text: string): FixtureState {
  return { ...s, nextId: s.nextId + 1, activity: [{ id: `a${s.nextId}`, at: s.clock, kind, text }, ...s.activity] };
}
const id = (s: FixtureState, p: string) => `${p}-${s.nextId}`;

function accrueLoan(s: FixtureState, to: number): FixtureState {
  if (!s.loan) return s;
  const add = accruedInterest(s.loan.principalUsd6, RISK_PARAMS.loanAprBps, s.loan.lastAccrual, to);
  return { ...s, loan: { ...s.loan, interestUsd6: s.loan.interestUsd6 + add, lastAccrual: to } };
}

function repay(s: FixtureState, amount: bigint, trigger: 'manual-partial' | 'manual-full' | 'profit-sweep', maxAllowance: bigint | null): FixtureState {
  if (!s.loan) throw new Error('No active demo loan');
  if (amount <= 0n) throw new Error('Repayment amount must be greater than zero.');
  const r = SL.applyRepayment(s.loan, amount);
  const spendable = spendableCashUsd6(paperCashFor(s));
  if (r.appliedUsd6 > spendable) {
    throw new Error(`Insufficient demo cash: repayment needs ${r.appliedUsd6} µUSD but only ${spendable} µUSD is spendable after open-position margin, pending orders, open losses and estimated costs.`);
  }
  const receipt: Receipt = {
    kind: 'demo-loan-repayment', id: id(s, 'rp'), source: 'fixture', loanId: s.loan.loanId, trigger,
    amount: toExact(r.appliedUsd6, USD_DECIMALS), toInterest: toExact(s.loan.interestUsd6 - r.interestUsd6, USD_DECIMALS),
    toPrincipal: toExact(s.loan.principalUsd6 - r.principalUsd6, USD_DECIMALS),
    maxAllowance: maxAllowance === null ? null : toExact(maxAllowance, USD_DECIMALS), ledgerRef: null, at: s.clock,
  };
  const settled = r.principalUsd6 === 0n && r.interestUsd6 === 0n;
  let n: FixtureState = {
    ...s, receipts: [receipt, ...s.receipts],
    paper: { ...s.paper, cashUsd6: s.paper.cashUsd6 - r.appliedUsd6 },
    loan: settled ? null : { ...s.loan, principalUsd6: r.principalUsd6, interestUsd6: r.interestUsd6 },
  };
  n = log(n, 'repayment', `Simulated ${trigger} repayment ${r.appliedUsd6} µUSD (interest first)`);
  if (settled) {
    n = { ...n, permission: n.permission ? { ...n.permission, revokedAt: s.clock } : null };
    n = log(n, 'settlement', 'Demo loan settled — collateral unlocked, sweeps stopped (debt is zero)');
  }
  return n;
}

export function reduce(s: FixtureState, raw: Action): FixtureState {
  const a = normalizeAction(raw);
  switch (a.type) {
    case 'RESET': return initialFixture();
    case 'ADVANCE': {
      const to = s.clock + a.seconds;
      let n = accrueLoan(s, to);
      n = { ...n, clock: to, quote: n.priceStale ? n.quote : { ...n.quote, observedAt: to - 12 } };
      return log(n, 'clock', `Fixture clock advanced ${Math.round(a.seconds / 3600)}h`);
    }
    case 'TOGGLE_STALE': {
      const stale = !s.priceStale;
      const n = { ...s, priceStale: stale, quote: { ...s.quote, observedAt: stale ? s.clock - 600 : s.clock - 12 } };
      return log(n, 'price', stale ? 'Price quote made stale (risk unavailable)' : 'Fresh price quote restored');
    }
    case 'TOGGLE_GAS': return { ...s, gasEmpty: !s.gasEmpty };
    case 'SET_PRICE': return log({ ...s, quote: { ...s.quote, usd6PerDoge: a.usd6, observedAt: s.clock - 12 }, priceStale: false }, 'price', `Fixture DOGE price set to ${a.usd6} µUSD`);
    case 'DEPOSIT': return log({ ...s, vault: RM.deposit(s.vault, ctx(s), a.wei) }, 'vault', 'Fixture deposit (checkpoint first)');
    case 'WITHDRAW': return log({ ...s, vault: RM.withdraw(s.vault, ctx(s), a.wei) }, 'vault', 'Fixture withdrawal (checkpoint first)');
    case 'TOPUP': return log({ ...s, vault: RM.topUpReserve(s.vault, ctx(s, FIXTURE_OWNER), a.wei) }, 'reserve', 'Fixture reserve top-up (checkpoint first; no backfill)');
    case 'CLAIM': {
      const { state, paid } = RM.claim(s.vault, ctx(s));
      const receipt: Receipt = {
        kind: 'testnet-reward-claim', id: id(s, 'rc'), source: 'fixture', amount: toExact(paid, DOGE_DECIMALS),
        txHash: null, confirmations: 0, requiredConfirmations: INTEGRATION.requiredConfirmations, status: 'fixture-rehearsal', at: s.clock,
      };
      return log({ ...s, vault: state, fixtureWalletRewardsWei: s.fixtureWalletRewardsWei + paid, receipts: [receipt, ...s.receipts] }, 'reward', 'Fixture reward claim rehearsal (no transaction sent)');
    }
    case 'BORROW': {
      // Idempotent: a repeated click / retry of the same request never creates a second borrow.
      if (!a.requestId) throw new Error('Borrow request id missing');
      if (s.processedBorrowRequests.includes(a.requestId)) return s;
      let n = accrueLoan(s, s.clock);
      // Independent validation — never trust the UI's checks.
      if (a.usd6 <= 0n) throw new Error('Borrow amount must be greater than zero.');
      if (a.usd6 % USD_CENT !== 0n) throw new Error('Borrow amount must be in whole cents.');
      const committedWei = n.sales.filter((x) => x.status === 'committed').reduce((t, x) => t + x.wei, 0n);
      const cap = borrowCapacity({
        collateralWei: n.demoCollateralWei, committedToSaleWei: committedWei,
        principalUsd6: n.loan?.principalUsd6 ?? 0n, accruedInterestUsd6: n.loan?.interestUsd6 ?? 0n,
        quote: n.quote, nowSec: n.clock,
      });
      if (cap === null) throw new Error('Price quote missing or stale — borrowing is blocked until risk is available.');
      if (a.usd6 > cap) throw new Error(`Amount exceeds current borrow capacity (${fmtUsd(cap, 'down')} at 40% max LTV).`);
      const loan = n.loan ?? { loanId: id(n, 'fixture-loan'), principalUsd6: 0n, interestUsd6: 0n, lastAccrual: s.clock, openedAt: s.clock };
      n = { ...n, loan: { ...loan, principalUsd6: loan.principalUsd6 + a.usd6 }, paper: { ...n.paper, cashUsd6: n.paper.cashUsd6 + a.usd6 } };
      if (n.sweep.loanId !== loan.loanId) n = { ...n, sweep: SL.newLedger(loan.loanId) };
      n = { ...n, processedBorrowRequests: [...n.processedBorrowRequests, a.requestId] };
      // Regression 1: borrowing pauses new entries and drops any prior consent.
      n = { ...n, session: { ...n.session, entriesPaused: true, authorization: null, seq: n.session.seq + 1, reason: 'New demo borrowing changed your financing', recovery: 'Grant NEW demo financed-trading authorization to resume entries' } };
      return log(n, 'borrow', `Simulated borrow ${a.usd6} µUSD — new entries paused pending NEW demo authorization`);
    }
    case 'REPAY_PARTIAL': return repay(accrueLoan(s, s.clock), a.usd6, 'manual-partial', null);
    case 'REPAY_FULL': {
      const n = accrueLoan(s, s.clock);
      if (!n.loan) throw new Error('No active demo loan');
      if (a.maxAllowanceUsd6 <= 0n) throw new Error('Full-repayment allowance must be greater than zero.');
      const debtNow = n.loan.principalUsd6 + n.loan.interestUsd6;
      const d = debitAtConfirmation(debtNow, a.maxAllowanceUsd6);
      // A "full" repayment that would leave debt behind is rejected, not silently made partial.
      if (d.remainingDebtUsd6 > 0n) {
        throw new Error(`Full-repayment allowance ${a.maxAllowanceUsd6} µUSD does not cover the debt at confirmation (${debtNow} µUSD) — review the repayment again.`);
      }
      return repay(n, d.debitedUsd6, 'manual-full', a.maxAllowanceUsd6);
    }
    case 'AUTHORIZE_TRADING': {
      if (s.loan && a.scope !== 'financed') throw new Error('A demo loan is open — only a NEW financed demo authorization can resume entries');
      const n = { ...s, session: { ...s.session, entriesPaused: false, reason: null, recovery: null, seq: s.session.seq + 1, authorization: { scope: a.scope, loanId: s.loan?.loanId ?? null, grantedAt: s.clock } } };
      return log(n, 'authorization', `DEMO ${a.scope} trading authorization granted (off-chain, demo only)`);
    }
    case 'START': {
      const problem = authorizationProblem(s);
      if (problem) throw new Error(`Demo trading authorization required: ${problem}`);
      return log({ ...s, session: { ...s.session, status: 'running', reason: null, recovery: null, seq: s.session.seq + 1 } }, 'session', 'Demo session resumed');
    }
    case 'STOP': return log({ ...s, session: { ...s.session, status: 'stopped', reason: a.reason, recovery: a.recovery, seq: s.session.seq + 1 } }, 'session', `Demo session stopped: ${a.reason}`);
    case 'POLL_RESULT': {
      // Regression 5: older responses can never restore "running".
      if (a.seq < s.session.seq) return { ...s, session: { ...s.session, ignoredPolls: s.session.ignoredPolls + 1 } };
      return a.status === s.session.status ? s : { ...s, session: { ...s.session, status: a.status } };
    }
    case 'CLOSE': return closePosition(s, a.positionId, a.fraction);
    case 'OPEN_POSITION': return openPosition(s, a.position, a.marginUsd6);
    case 'GRANT_SWEEP': {
      if (!s.loan) throw new Error('No loan to authorize sweeps for');
      const grantProblem = SL.sweepGrantProblem(a);
      if (grantProblem) throw new Error(grantProblem);
      const permission: SL.SweepPermission = { id: id(s, 'perm'), loanId: s.loan.loanId, percent: a.percent, expiresAt: s.clock + a.days * DAY, floorUsd6: a.floorUsd6, createdAt: s.clock, revokedAt: null };
      return log({ ...s, permission, sweep: SL.anchorAtAuthorization(s.sweep) }, 'permission', `Profit-sweep permission ${a.percent}% for ${a.days}d granted`);
    }
    case 'REVOKE_SWEEP': return s.permission ? log({ ...s, permission: { ...s.permission, revokedAt: s.clock } }, 'permission', 'Profit-sweep permission revoked') : s;
    case 'RUN_SWEEP': return runSweep(s);
    case 'WITHDRAW_CASH': {
      if (a.usd6 <= 0n) throw new Error('Withdrawal amount must be greater than zero.');
      const spendable = spendableCashUsd6(paperCashFor(s));
      if (a.usd6 > spendable) {
        throw new Error(`Insufficient demo cash: withdrawal of ${a.usd6} µUSD exceeds the ${spendable} µUSD spendable after open-position margin, pending orders, open losses and estimated costs.`);
      }
      return log({ ...s, paper: { ...s.paper, cashUsd6: s.paper.cashUsd6 - a.usd6, withdrawnUsd6: s.paper.withdrawnUsd6 + a.usd6 } }, 'cash', `Demo cash withdrawal ${a.usd6} µUSD`);
    }
    case 'VOLUNTARY_SALE': {
      const committed = s.sales.filter((x) => x.status === 'committed').reduce((t, x) => t + x.wei, 0n);
      if (a.wei <= 0n || a.wei > s.demoCollateralWei - committed) throw new Error('Invalid sale amount');
      const sale: CollateralSale = { id: id(s, 'sale'), wei: a.wei, proceedsUsd6: (a.wei * s.quote.usd6PerDoge) / ONE_DOGE, status: 'committed', at: s.clock };
      return log({ ...s, sales: [sale, ...s.sales] }, 'collateral', 'Owner-authorized voluntary demo collateral sale committed (not a liquidation)');
    }
    case 'RECONCILE_SALE': return reconcileSale(s, a.id);
  }
}

function reconcileSale(s: FixtureState, saleId: string): FixtureState {
  const sale = s.sales.find((x) => x.id === saleId && x.status === 'committed');
  if (!sale) return s;
  let n: FixtureState = { ...s, demoCollateralWei: s.demoCollateralWei - sale.wei, sales: s.sales.map((x) => (x.id === saleId ? { ...x, status: 'reconciled' as const } : x)), paper: { ...s.paper, cashUsd6: s.paper.cashUsd6 + sale.proceedsUsd6 } };
  n = log(n, 'collateral', 'Sale reconciled: collateral reduced once, proceeds credited to demo cash');
  if (n.loan && sale.proceedsUsd6 > 0n) {
    n = repay(accrueLoan(n, n.clock), sale.proceedsUsd6 < n.loan.principalUsd6 + n.loan.interestUsd6 ? sale.proceedsUsd6 : n.loan.principalUsd6 + n.loan.interestUsd6, 'manual-partial', null);
    if (n.loan && n.demoCollateralWei === 0n) {
      const shortfall = n.loan.principalUsd6 + n.loan.interestUsd6;
      n = log({ ...n, badDebtUsd6: n.badDebtUsd6 + shortfall }, 'bad-debt', `Residual shortfall recorded as bad debt: ${shortfall} µUSD (not erased)`);
    }
  }
  return n;
}

/**
 * Position → StopInput boundary. Field names differ between the two types
 * (Position.fundingUsd6 → observedFundingUsd6, Position.realizedLossPartialsUsd6 →
 * realizedLossFromPartialsUsd6); every field is mapped explicitly and `satisfies` makes a
 * misspelled or missing key a type error instead of a silent `undefined` at runtime.
 */
export function stopInputFor(raw: Position): StopInput {
  const p = normalizePosition(raw);
  return {
    side: p.side,
    entryPriceUsd6: p.entryPriceUsd6,
    qty: p.qty,
    qtyScale: p.qtyScale,
    tickUsd6: p.tickUsd6,
    entryFeeUsd6: p.entryFeeUsd6,
    exitFeeBps: EXIT_FEE_BPS,
    observedFundingUsd6: p.fundingUsd6,
    realizedLossFromPartialsUsd6: p.realizedLossPartialsUsd6,
    existingLimitUsd6: p.existingLimitUsd6,
  } satisfies StopInput;
}
export const stopFor = (p: Position) => reconcileStop(stopInputFor(p), p.stopPriceUsd6 ?? null);

/** Reconcile a local position without ever loosening its persisted stop or risk limit. */
export function reconcilePosition(p: Position): Position {
  const normalized = normalizePosition(p);
  const stop = stopFor(normalized);
  return stop.ok ? { ...normalized, stopPriceUsd6: stop.stopPriceUsd6 } : normalized;
}

/** Reserve gross open losses, unpaid position costs and exit fees. Gains never offset losses.
 * Fixture cash debits entry/funding fees at close, so reserve them until then. */
export function paperCashFor(s: FixtureState) {
  const ceil = (n: bigint, d: bigint) => (n + d - 1n) / d;
  const unrealizedLossUsd6 = s.positions.reduce((total, raw) => {
    const p = normalizePosition(raw);
    if (p.qtyScale <= 0n || p.qty < 0n || p.markUsd6 <= 0n || p.entryPriceUsd6 <= 0n || p.entryFeeUsd6 < 0n || !['long', 'short'].includes(p.side)) throw new Error('Invalid position equity inputs');
    const diff = p.side === 'long' ? p.entryPriceUsd6 - p.markUsd6 : p.markUsd6 - p.entryPriceUsd6;
    const loss = diff > 0n ? ceil(diff * p.qty, p.qtyScale) : 0n;
    const exitFee = ceil(p.markUsd6 * p.qty * EXIT_FEE_BPS, p.qtyScale * 10_000n);
    return total + loss + p.entryFeeUsd6 + (p.fundingUsd6 > 0n ? p.fundingUsd6 : 0n) + exitFee;
  }, 0n);
  return { ...s.paper, unrealizedLossUsd6 };
}

/** Invalid position data → explicit "stop unavailable" result with the real error; never a zero stop. */
export type StopView = StopResult | { ok: false; invalid: true; reason: string };
export function tryStopFor(p: Position): StopView {
  try {
    return stopFor(p);
  } catch (e) {
    return { ok: false, invalid: true, reason: `Stop unavailable — invalid position data: ${e instanceof Error ? e.message : String(e)}` };
  }
}

export function unrealizedUsd6(raw: Position): bigint {
  const p = normalizePosition(raw);
  const diff = p.side === 'long' ? p.markUsd6 - p.entryPriceUsd6 : p.entryPriceUsd6 - p.markUsd6;
  return (diff * p.qty) / p.qtyScale;
}

function closePosition(s: FixtureState, positionId: string, fraction: 'half' | 'all'): FixtureState {
  const found = s.positions.find((x) => x.id === positionId);
  if (!found) throw new Error('Position not found');
  const p = reconcilePosition(found);
  if (p.marginUsd6 === undefined || p.marginUsd6 < 0n || p.marginUsd6 > s.paper.openMarginUsd6) throw new Error('Position margin requires reconciliation');
  if (p.qty <= 0n) throw new Error('No open quantity');
  if (p.markUsd6 <= 0n) throw new Error('Invalid close mark');
  const closeQty = fraction === 'all' ? p.qty : p.qty / 2n;
  if (closeQty <= 0n) throw new Error('Close quantity must be greater than zero');
  const marginRelease = p.marginUsd6 * closeQty / p.qty;
  const ratio = (v: bigint) => (v * closeQty) / p.qty;
  const diff = p.side === 'long' ? p.markUsd6 - p.entryPriceUsd6 : p.entryPriceUsd6 - p.markUsd6;
  const grossUnits = diff * closeQty;
  // Never discard a fractional micro-dollar loss on a partial exit.
  const gross = grossUnits < 0n ? -((-grossUnits + p.qtyScale - 1n) / p.qtyScale) : grossUnits / p.qtyScale;
  const exitFee = (p.markUsd6 * closeQty * EXIT_FEE_BPS + p.qtyScale * 10_000n - 1n) / (p.qtyScale * 10_000n);
  const fill: SL.RealizedFill = { fillId: id(s, `fill-${p.id}`), closedAt: s.clock, grossPnlUsd6: gross, entryFeeUsd6: ratio(p.entryFeeUsd6), exitFeeUsd6: exitFee, fundingUsd6: ratio(p.fundingUsd6) };
  const net = SL.netOf(fill);
  const remaining = p.qty - closeQty;
  const positions = remaining === 0n ? s.positions.filter((x) => x.id !== p.id) : s.positions.map((x) => x.id === p.id ? reconcilePosition({
    ...p, qty: remaining, marginUsd6: p.marginUsd6! - marginRelease, entryFeeUsd6: p.entryFeeUsd6 - fill.entryFeeUsd6, fundingUsd6: p.fundingUsd6 - fill.fundingUsd6,
    realizedLossPartialsUsd6: p.realizedLossPartialsUsd6 + (net < 0n ? -net : 0n),
  }) : x);
  const n: FixtureState = {
    ...s, positions, fills: [fill, ...s.fills], sweep: SL.applyFill(s.sweep, fill),
    paper: { ...s.paper, cashUsd6: s.paper.cashUsd6 + net, openMarginUsd6: s.paper.openMarginUsd6 - marginRelease },
  };
  return log(n, 'fill', `Fixture paper fill: ${fraction} close ${p.symbol} ${p.side}, net ${net} µUSD after fees/funding`);
}

/**
 * NEW entry boundary (fixture paper trading). Rejects when entryBlockReason() blocks (stale or
 * missing price, risk policy, session/authorization), when the $2 fee-inclusive stop cannot be
 * computed, or when margin exceeds spendable cash. Failures throw; state is unchanged.
 */
function openPosition(s: FixtureState, raw: Position, marginUsd6: bigint): FixtureState {
  const blocked = entryBlockReason(s);
  if (blocked) throw new Error(`New entry blocked: ${blocked}`);
  const p = normalizePosition(raw);
  if (s.positions.some((x) => x.id === p.id)) throw new Error('A position with this id is already open');
  if (p.qty <= 0n) throw new Error('Entry quantity must be greater than zero.');
  const stop = stopFor(p);
  if (!stop.ok) throw new Error(`New entry blocked: ${stop.reason}`);
  if (marginUsd6 <= 0n) throw new Error('Entry margin must be greater than zero.');
  const spendable = spendableCashUsd6(paperCashFor({ ...s, positions: [...s.positions, p] }));
  if (marginUsd6 > spendable) throw new Error(`Insufficient demo cash: entry margin ${marginUsd6} µUSD exceeds ${spendable} µUSD spendable.`);
  const n: FixtureState = {
    ...s, positions: [...s.positions, { ...p, marginUsd6, stopPriceUsd6: stop.stopPriceUsd6, openedAt: s.clock }],
    paper: { ...s.paper, openMarginUsd6: s.paper.openMarginUsd6 + marginUsd6 },
  };
  return log(n, 'entry', `Fixture paper entry ${p.symbol} ${p.side} (stop ${stop.stopPriceUsd6} µUSD within $2 budget)`);
}

function runSweep(s: FixtureState): FixtureState {
  const n0 = accrueLoan(s, s.clock);
  if (!n0.loan) return log(n0, 'sweep', 'Sweep skipped: debt is zero');
  const q = SL.quoteSweep(n0.sweep, n0.permission, n0.loan, { ...paperCashFor(n0), availableUsd6: n0.paper.cashUsd6 }, n0.clock);
  if (q.amountUsd6 === 0n) return log(n0, 'sweep', `Sweep: nothing repaid — ${q.reason}`);
  // Deterministic from ledger state → a retry of the same evaluation maps to the same id, while
  // a later evaluation of carried entitlement (owed) gets a new id and pays only what is still owed.
  const l0 = n0.sweep;
  const sweepId = `sweep-${l0.committedSweepIds.length}-${l0.cumulativeNetUsd6}-${l0.highWaterUsd6}-${l0.owedUsd6}`;
  const n = { ...n0, sweep: SL.commitSweep(n0.sweep, sweepId, q) };
  if (n.sweep === n0.sweep) return log(n0, 'sweep', 'Sweep already committed — not repeated');
  return repay(n, q.amountUsd6, 'profit-sweep', null);
}

export const usd6 = (d: number) => usd(d);
export { ONE_USD };

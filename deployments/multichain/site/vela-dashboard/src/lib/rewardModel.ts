/**
 * Reward accounting reference for the NEW isolated reward-enabled vault candidate.
 * Executable TypeScript model — NOT deployable contract code. It mirrors
 * contracts/VelaRewardVault.sol and is the oracle for deterministic tests.
 *
 * Rules enforced:
 * - Rewards are paid ONLY from this vault's separately funded reserve, never principal.
 * - Simple APR on principal (no compounding). Rate in basis points.
 * - Checkpoint before every deposit, withdrawal, seizure, rate change and reserve top-up.
 * - Accrual is capped at funded capacity (reserve - liabilities). Time that passed while
 *   unfunded is never backfilled after a later top-up.
 * - Accrued rewards become reserved liabilities; claims reduce reserve and liabilities 1:1.
 */
export const YEAR_SECONDS = 365n * 24n * 3600n;
export const ACC_PRECISION = 10n ** 36n;
const BPS = 10_000n;

export type UserPosition = {
  principal: bigint;
  /** ceil(principal * acc / PREC) at last snapshot — ceil keeps sum(owed) <= liabilities */
  rewardDebt: bigint;
  owed: bigint;
  claimed: bigint;
};

export type RewardVaultState = {
  vaultId: string;
  chainId: number;
  owner: string;
  aprBps: bigint;
  totalPrincipal: bigint;
  /** native balance held as collateral principal (separate from reserve) */
  principalBalance: bigint;
  reserveBalance: bigint;
  /** accrued-but-unclaimed rewards, reserved out of reserveBalance */
  liabilities: bigint;
  accPerShare: bigint;
  lastCheckpoint: number;
  /** first second accrual was capped by funding (null while fully funded) */
  exhaustedSince: number | null;
  totalFunded: bigint;
  totalClaimed: bigint;
  users: Record<string, UserPosition>;
};

export type Ctx = { caller: string; chainId: number; now: number };

export class RewardModelError extends Error {}

const ceilDiv = (a: bigint, b: bigint) => (a + b - 1n) / b;
const clone = (s: RewardVaultState): RewardVaultState => ({
  ...s,
  users: Object.fromEntries(Object.entries(s.users).map(([k, v]) => [k, { ...v }])),
});

export function createVault(p: { vaultId: string; chainId: number; owner: string; aprBps: bigint; now: number }): RewardVaultState {
  if (p.aprBps < 0n || p.aprBps > BPS) throw new RewardModelError('APR out of range');
  return {
    vaultId: p.vaultId, chainId: p.chainId, owner: p.owner, aprBps: p.aprBps,
    totalPrincipal: 0n, principalBalance: 0n, reserveBalance: 0n, liabilities: 0n,
    accPerShare: 0n, lastCheckpoint: p.now, exhaustedSince: null,
    totalFunded: 0n, totalClaimed: 0n, users: {},
  };
}

function checkChain(s: RewardVaultState, ctx: Ctx) {
  if (ctx.chainId !== s.chainId) throw new RewardModelError(`Wrong chain ${ctx.chainId}; vault is on ${s.chainId}`);
  if (ctx.now < s.lastCheckpoint) throw new RewardModelError('Clock moved backwards');
}
function onlyOwner(s: RewardVaultState, ctx: Ctx) {
  if (ctx.caller !== s.owner) throw new RewardModelError('Only owner');
}

/** Rewards that would accrue over dt if fully funded. */
export const dueFor = (principal: bigint, aprBps: bigint, dt: bigint) => (principal * aprBps * dt) / (BPS * YEAR_SECONDS);

/** Advance global accumulator to `now`. Mutates the provided (already cloned) state. */
function checkpointMut(s: RewardVaultState, now: number) {
  const dt = BigInt(now - s.lastCheckpoint);
  if (dt <= 0n) return;
  if (s.totalPrincipal === 0n || s.aprBps === 0n) { s.lastCheckpoint = now; return; }
  const due = dueFor(s.totalPrincipal, s.aprBps, dt);
  const available = s.reserveBalance - s.liabilities;
  const pay = due < available ? due : available;
  if (pay < due && s.exhaustedSince === null) {
    // estimate the second funding ran out (informational only)
    const perSec = dueFor(s.totalPrincipal, s.aprBps, 1n) || 1n;
    s.exhaustedSince = s.lastCheckpoint + Number(available / perSec);
  }
  if (pay > 0n) {
    const accDelta = (pay * ACC_PRECISION) / s.totalPrincipal;
    s.accPerShare += accDelta;
    // Reserve the CEIL of the distributed amount. Users settle on floor(principal * totalAcc / PREC),
    // losing < 1 wei per settlement, while a floored per-checkpoint liability loses < 1 wei per
    // checkpoint — so with more checkpoints than settlements, sum(owed) could exceed liabilities.
    // Ceil is still <= pay (accDelta * TP <= pay * PREC and pay is an integer), so it never reserves
    // more than the funded `available`. Leftover sub-wei dust stays reserved (conservative), never hidden.
    s.liabilities += ceilDiv(accDelta * s.totalPrincipal, ACC_PRECISION);
  }
  s.lastCheckpoint = now; // unfunded time is dropped, never backfilled
}

function settleUser(s: RewardVaultState, user: string): UserPosition {
  const u = s.users[user] ?? { principal: 0n, rewardDebt: 0n, owed: 0n, claimed: 0n };
  const earned = (u.principal * s.accPerShare) / ACC_PRECISION - u.rewardDebt;
  if (earned > 0n) u.owed += earned;
  s.users[user] = u;
  return u;
}
const snapshot = (s: RewardVaultState, u: UserPosition) => {
  u.rewardDebt = ceilDiv(u.principal * s.accPerShare, ACC_PRECISION);
};

export function checkpoint(state: RewardVaultState, ctx: Ctx): RewardVaultState {
  checkChain(state, ctx);
  const s = clone(state);
  checkpointMut(s, ctx.now);
  return s;
}

export function deposit(state: RewardVaultState, ctx: Ctx, amount: bigint): RewardVaultState {
  checkChain(state, ctx);
  if (amount <= 0n) throw new RewardModelError('Deposit must be > 0');
  const s = clone(state);
  checkpointMut(s, ctx.now);
  const u = settleUser(s, ctx.caller);
  u.principal += amount; s.totalPrincipal += amount; s.principalBalance += amount;
  snapshot(s, u);
  return s;
}

export function withdraw(state: RewardVaultState, ctx: Ctx, amount: bigint): RewardVaultState {
  checkChain(state, ctx);
  const s = clone(state);
  checkpointMut(s, ctx.now);
  const u = settleUser(s, ctx.caller);
  if (amount <= 0n || amount > u.principal) throw new RewardModelError('Invalid withdrawal');
  u.principal -= amount; s.totalPrincipal -= amount; s.principalBalance -= amount;
  snapshot(s, u);
  return s;
}

/** Owner-authorized collateral seizure (simulated sale). Accrued rewards stay with the user. */
export function seize(state: RewardVaultState, ctx: Ctx, user: string, amount: bigint): RewardVaultState {
  checkChain(state, ctx); onlyOwner(state, ctx);
  const s = clone(state);
  checkpointMut(s, ctx.now);
  const u = settleUser(s, user);
  if (amount <= 0n || amount > u.principal) throw new RewardModelError('Invalid seizure');
  u.principal -= amount; s.totalPrincipal -= amount; s.principalBalance -= amount;
  snapshot(s, u);
  return s;
}

export function setRate(state: RewardVaultState, ctx: Ctx, aprBps: bigint): RewardVaultState {
  checkChain(state, ctx); onlyOwner(state, ctx);
  if (aprBps < 0n || aprBps > BPS) throw new RewardModelError('APR out of range');
  const s = clone(state);
  checkpointMut(s, ctx.now);
  s.aprBps = aprBps;
  return s;
}

/** Reserve top-up: checkpoint FIRST so the new funds never pay for past unfunded time. */
export function topUpReserve(state: RewardVaultState, ctx: Ctx, amount: bigint): RewardVaultState {
  checkChain(state, ctx); onlyOwner(state, ctx);
  if (amount <= 0n) throw new RewardModelError('Top-up must be > 0');
  const s = clone(state);
  checkpointMut(s, ctx.now);
  s.reserveBalance += amount; s.totalFunded += amount; s.exhaustedSince = null;
  return s;
}

export function claim(state: RewardVaultState, ctx: Ctx): { state: RewardVaultState; paid: bigint } {
  checkChain(state, ctx);
  const s = clone(state);
  checkpointMut(s, ctx.now);
  const u = settleUser(s, ctx.caller);
  snapshot(s, u);
  const paid = u.owed;
  if (paid === 0n) throw new RewardModelError('Nothing to claim');
  if (paid > s.liabilities || paid > s.reserveBalance) throw new RewardModelError('Invariant: reserve shortfall');
  u.owed = 0n; u.claimed += paid;
  s.liabilities -= paid; s.reserveBalance -= paid; s.totalClaimed += paid;
  return { state: s, paid };
}

/** Read-only view: claimable at `now` without mutating. */
export function pendingFor(state: RewardVaultState, user: string, now: number): bigint {
  const s = clone(state);
  if (now > s.lastCheckpoint) checkpointMut(s, now);
  const u = s.users[user];
  if (!u) return 0n;
  const earned = (u.principal * s.accPerShare) / ACC_PRECISION - u.rewardDebt;
  return u.owed + (earned > 0n ? earned : 0n);
}

/** Seconds of full-rate accrual the unreserved reserve still funds. null = infinite (no principal). */
export function runwaySeconds(state: RewardVaultState, now: number): bigint | null {
  const s = clone(state);
  if (now > s.lastCheckpoint) checkpointMut(s, now);
  const perYear = (s.totalPrincipal * s.aprBps) / BPS;
  if (perYear === 0n) return null;
  return ((s.reserveBalance - s.liabilities) * YEAR_SECONDS) / perYear;
}

/** Conservation invariant used by tests and the UI health strip. */
export function checkInvariants(s: RewardVaultState): string[] {
  const errs: string[] = [];
  if (s.liabilities > s.reserveBalance) errs.push('liabilities exceed reserve');
  const sumPrincipal = Object.values(s.users).reduce((a, u) => a + u.principal, 0n);
  if (sumPrincipal !== s.totalPrincipal) errs.push('principal mismatch');
  if (s.principalBalance !== s.totalPrincipal) errs.push('principal balance mismatch');
  if (s.totalFunded !== s.reserveBalance + s.totalClaimed) errs.push('reserve conservation broken');
  const sumOwed = Object.values(s.users).reduce((a, u) => a + u.owed, 0n);
  if (sumOwed > s.liabilities) errs.push('owed exceeds liabilities');
  return errs;
}

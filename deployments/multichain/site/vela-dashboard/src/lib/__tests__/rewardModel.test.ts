import { describe, expect, it } from 'vitest';
import * as RM from '../rewardModel';

const CHAIN = 6281971;
const OWNER = 'owner';
const DOGE = 10n ** 18n;
const DAY = 86_400;
const c = (caller: string, now: number, chainId = CHAIN): RM.Ctx => ({ caller, chainId, now });
const fresh = (now = 0) => RM.createVault({ vaultId: 'v', chainId: CHAIN, owner: OWNER, aprBps: 500n, now });
const ok = (s: RM.RewardVaultState) => expect(RM.checkInvariants(s)).toEqual([]);

describe('reward vault accounting model', () => {
  it('conserves the reserve and never touches principal on claim', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 100n * DOGE);
    s = RM.deposit(s, c('a', 0), 1_000n * DOGE);
    const { state, paid } = RM.claim(s, c('a', 30 * DAY));
    expect(paid).toBe(RM.dueFor(1_000n * DOGE, 500n, BigInt(30 * DAY)));
    expect(state.principalBalance).toBe(1_000n * DOGE);
    expect(state.reserveBalance).toBe(100n * DOGE - paid);
    expect(state.totalFunded).toBe(state.reserveBalance + state.totalClaimed);
    ok(state);
  });

  it('gives no retroactive rewards to late deposits', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 100n * DOGE);
    s = RM.deposit(s, c('a', 0), 1_000n * DOGE);
    s = RM.deposit(s, c('b', 30 * DAY), 1_000n * DOGE);
    expect(RM.pendingFor(s, 'b', 30 * DAY)).toBe(0n);
    const tenDays = RM.dueFor(1_000n * DOGE, 500n, BigInt(10 * DAY));
    const b = RM.pendingFor(s, 'b', 40 * DAY);
    expect(b <= tenDays && b >= tenDays - 2n).toBe(true);
    const a = RM.pendingFor(s, 'a', 40 * DAY);
    expect(a >= RM.dueFor(1_000n * DOGE, 500n, BigInt(40 * DAY)) - 2n).toBe(true);
    ok(s);
  });

  it('prevents double claims', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 10n * DOGE);
    s = RM.deposit(s, c('a', 0), 1_000n * DOGE);
    s = RM.claim(s, c('a', DAY)).state;
    expect(() => RM.claim(s, c('a', DAY))).toThrow('Nothing to claim');
  });

  it('caps accrual at funded capacity and never backfills after a top-up', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 1n * DOGE);
    s = RM.deposit(s, c('a', 0), 10_000n * DOGE);
    expect(RM.pendingFor(s, 'a', 10 * DAY)).toBe(1n * DOGE); // capped
    s = RM.topUpReserve(s, c(OWNER, 10 * DAY), 100n * DOGE);
    expect(RM.pendingFor(s, 'a', 10 * DAY)).toBe(1n * DOGE); // no backfill for days 0.73–10
    const oneDay = RM.dueFor(10_000n * DOGE, 500n, BigInt(DAY));
    expect(RM.pendingFor(s, 'a', 11 * DAY)).toBe(1n * DOGE + oneDay);
    expect(s.exhaustedSince).toBeNull();
    ok(s);
  });

  it('keeps sum(owed) <= liabilities under awkward integer rounding', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 3n * DOGE + 7n);
    const users: [string, bigint][] = [['a', DOGE + 1n], ['b', 2n * DOGE + 3n], ['c', 3n * DOGE + 7n]];
    let t = 0;
    for (const [u, amt] of users) { t += 12_345; s = RM.deposit(s, c(u, t), amt * 1_000n); ok(s); }
    t += 7_777_777; s = RM.withdraw(s, c('b', t), 999n); ok(s);
    t += 3_333; s = RM.setRate(s, c(OWNER, t), 777n); ok(s);
    let claimed = 0n;
    for (const [u] of users) { t += 1; const r = RM.claim(s, c(u, t)); s = r.state; claimed += r.paid; ok(s); }
    expect(claimed <= s.totalFunded).toBe(true);
  });

  it('enforces owner and chain checks', () => {
    const s = fresh();
    expect(() => RM.topUpReserve(s, c('mallory', 0), DOGE)).toThrow('Only owner');
    expect(() => RM.setRate(s, c('mallory', 0), 1_000n)).toThrow('Only owner');
    expect(() => RM.seize(s, c('mallory', 0), 'a', 1n)).toThrow('Only owner');
    expect(() => RM.deposit(s, c('a', 0, 1), DOGE)).toThrow('Wrong chain');
  });

  it('checkpoints before rate changes (old rate for old time)', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 100n * DOGE);
    s = RM.deposit(s, c('a', 0), 1_000n * DOGE);
    s = RM.setRate(s, c(OWNER, 10 * DAY), 1_000n);
    const expected = RM.dueFor(1_000n * DOGE, 500n, BigInt(10 * DAY)) + RM.dueFor(1_000n * DOGE, 1_000n, BigInt(10 * DAY));
    const got = RM.pendingFor(s, 'a', 20 * DAY);
    expect(got <= expected && got >= expected - 2n).toBe(true);
  });

  it('seizure keeps already-accrued rewards with the user', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 100n * DOGE);
    s = RM.deposit(s, c('a', 0), 1_000n * DOGE);
    s = RM.seize(s, c(OWNER, 10 * DAY), 'a', 1_000n * DOGE);
    expect(RM.pendingFor(s, 'a', 50 * DAY)).toBe(RM.dueFor(1_000n * DOGE, 500n, BigInt(10 * DAY)));
    ok(s);
  });

  // Regression: liabilities used floor() per checkpoint (losing < 1 wei each time) while a user
  // settles on floor(principal * totalAcc / PREC) (losing < 1 wei once), so after >= 2 checkpoints
  // owed exceeded liabilities and claim threw 'Invariant: reserve shortfall'.
  it('regression: per-checkpoint liability rounding never lets owed exceed liabilities', () => {
    const p = 3n * DOGE + 7n; // coprime to 10 -> every checkpoint leaves a sub-wei remainder
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 10n * DOGE);
    s = RM.deposit(s, c('a', 0), p);
    const due = RM.dueFor(p, 500n, BigInt(DAY));
    for (let d = 1; d <= 3; d++) {
      s = RM.checkpoint(s, c('anyone', d * DAY));
      ok(s);
      expect(RM.pendingFor(s, 'a', d * DAY) <= s.liabilities).toBe(true);
      expect(s.liabilities <= BigInt(d) * due).toBe(true); // never reserves beyond funded accrual
    }
    const pendingBefore = RM.pendingFor(s, 'a', 4 * DAY);
    const r = RM.claim(s, c('a', 4 * DAY));
    expect(r.paid).toBe(pendingBefore);
    expect(r.paid <= 4n * due && r.paid >= 4n * due - 4n).toBe(true);
    ok(r.state);
    expect(r.state.liabilities <= 4n).toBe(true); // only sub-wei dust stays reserved, visibly
    expect(r.state.reserveBalance).toBe(10n * DOGE - r.paid);
  });

  it('regression: many checkpoints across awkward multi-user balances stay fully reserved', () => {
    let s = RM.topUpReserve(fresh(), c(OWNER, 0), 5n * DOGE + 3n);
    const users: [string, bigint][] = [['a', DOGE + 1n], ['b', 2n * DOGE + 3n], ['c', 3n * DOGE + 7n]];
    let t = 0;
    for (const [u, amt] of users) { t += 777; s = RM.deposit(s, c(u, t), amt * 1_000n); ok(s); }
    for (let i = 0; i < 40; i++) {
      t += 1_009 + i * 37;
      s = RM.checkpoint(s, c('anyone', t));
      ok(s);
      const pending = users.reduce((acc, [u]) => acc + RM.pendingFor(s, u, t), 0n);
      expect(pending <= s.liabilities).toBe(true);
    }
    let claimed = 0n;
    for (const [u] of users) { t += 1; const r = RM.claim(s, c(u, t)); s = r.state; claimed += r.paid; ok(s); }
    expect(claimed <= s.totalFunded).toBe(true);
    expect(s.totalFunded).toBe(s.reserveBalance + s.totalClaimed);
  });
});

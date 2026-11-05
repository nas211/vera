/**
 * FIXTURE integration tests — exercise the deterministic local engine only.
 * Passing here does NOT prove the live browser / Vela / chain flows work.
 */
import { describe, expect, it } from 'vitest';
import { initialFixture, reduce, type Action, type FixtureState } from '../vela/fixtureEngine';

const run = (s: FixtureState, ...as: Action[]) => as.reduce(reduce, s);
const debt = (s: FixtureState) => (s.loan ? s.loan.principalUsd6 + s.loan.interestUsd6 : 0n);

describe('[FIXTURE] borrow → authorization → fills → sweep → settlement', () => {
  it('borrowing pauses entries and requires NEW demo authorization (regression 1)', () => {
    const s = run(initialFixture(), { type: 'BORROW', usd6: 100_000_000n, requestId: 'r1' });
    expect(s.session.entriesPaused).toBe(true);
    expect(s.session.authorization).toBeNull();
    expect(s.session.recovery).toContain('NEW demo');
    expect(() => reduce(s, { type: 'START' })).toThrow('authorization');
  });

  it('runs the full rehearsal path with exactly one sweep repayment', () => {
    let s = run(initialFixture(), { type: 'BORROW', usd6: 100_000_000n, requestId: 'r1' }, { type: 'AUTHORIZE_TRADING', scope: 'financed' });
    s = reduce(s, { type: 'RUN_SWEEP' });
    expect(s.receipts.filter((r) => r.kind === 'demo-loan-repayment')).toHaveLength(0); // no permission
    s = run(s, { type: 'GRANT_SWEEP', percent: 50, days: 7, floorUsd6: 0n });
    const before = debt(s);
    s = run(s, { type: 'CLOSE', positionId: 'p-btc', fraction: 'half' }, { type: 'CLOSE', positionId: 'p-btc', fraction: 'all' });
    expect(s.fills.filter((f) => f.fillId.includes('p-btc'))).toHaveLength(2);
    s = run(s, { type: 'RUN_SWEEP' }, { type: 'RUN_SWEEP' });
    const sweeps = s.receipts.filter((r) => r.kind === 'demo-loan-repayment' && r.trigger === 'profit-sweep');
    expect(sweeps).toHaveLength(1);
    expect(debt(s) < before).toBe(true);
    // losing trade → nothing new to sweep
    s = run(s, { type: 'CLOSE', positionId: 'p-doge', fraction: 'all' }, { type: 'RUN_SWEEP' });
    expect(s.receipts.filter((r) => r.kind === 'demo-loan-repayment' && r.trigger === 'profit-sweep')).toHaveLength(1);
  });

  it('insufficient demo cash blocks full repayment; funded full repay settles and stops sweeps', () => {
    const s0 = initialFixture();
    expect(() => reduce(s0, { type: 'REPAY_PARTIAL', usd6: 500_000_000n })).toThrow('Insufficient');
    let s: FixtureState = { ...s0, paper: { ...s0.paper, cashUsd6: 1_000_000_000n } };
    s = run(s, { type: 'GRANT_SWEEP', percent: 100, days: 7, floorUsd6: 0n }, { type: 'REPAY_FULL', maxAllowanceUsd6: 401_000_000n });
    expect(s.loan).toBeNull();
    expect(s.permission?.revokedAt).not.toBeNull();
    expect(s.activity.some((a) => a.kind === 'settlement')).toBe(true);
  });

  it('successful borrow updates the loan, keeps positions and stops, and invalidates consent', () => {
    const s0 = initialFixture();
    const s = reduce(s0, { type: 'BORROW', usd6: 50_000_000n, requestId: 'r1' });
    expect(s.loan!.loanId).toBe(s0.loan!.loanId); // added to the same loan, not a second one
    expect(s.loan!.principalUsd6).toBe(s0.loan!.principalUsd6 + 50_000_000n);
    expect(s.paper.cashUsd6).toBe(s0.paper.cashUsd6 + 50_000_000n);
    expect(s.positions).toEqual(s0.positions); // protective management untouched
    expect(s.session.authorization).toBeNull();
    expect(s.session.seq).toBeGreaterThan(s0.session.seq);
  });

  it('repeated borrow with the same request id applies exactly once', () => {
    const s0 = initialFixture();
    const once = reduce(s0, { type: 'BORROW', usd6: 50_000_000n, requestId: 'dup' });
    const twice = reduce(once, { type: 'BORROW', usd6: 50_000_000n, requestId: 'dup' });
    expect(twice).toBe(once);
    expect(twice.loan!.principalUsd6).toBe(s0.loan!.principalUsd6 + 50_000_000n);
  });

  it('failed borrows throw and leave state untouched', () => {
    const s0 = initialFixture();
    expect(() => reduce(s0, { type: 'BORROW', usd6: 0n, requestId: 'a' })).toThrow('greater than zero');
    expect(() => reduce(s0, { type: 'BORROW', usd6: 1_000_001n, requestId: 'b' })).toThrow('whole cents');
    expect(() => reduce(s0, { type: 'BORROW', usd6: 10_000_000_000n, requestId: 'c' })).toThrow('capacity');
    const stale = reduce(s0, { type: 'TOGGLE_STALE' });
    expect(() => reduce(stale, { type: 'BORROW', usd6: 1_000_000n, requestId: 'd' })).toThrow('stale');
    expect(() => reduce(s0, { type: 'BORROW', usd6: 1_000_000n, requestId: '' })).toThrow('id missing');
    expect(s0.processedBorrowRequests).toEqual([]);
    // a failed id is not consumed: it can succeed once the problem is fixed
    expect(reduce(s0, { type: 'BORROW', usd6: 1_000_000n, requestId: 'c' }).processedBorrowRequests).toEqual(['c']);
  });

  it('resuming after a borrow needs a NEW financed demo authorization', () => {
    let s = run(initialFixture(), { type: 'STOP', reason: 'x', recovery: 'y' }, { type: 'BORROW', usd6: 50_000_000n, requestId: 'r1' });
    expect(() => reduce(s, { type: 'START' })).toThrow('authorization');
    expect(() => reduce(s, { type: 'AUTHORIZE_TRADING', scope: 'unfinanced' })).toThrow('financed');
    s = run(s, { type: 'AUTHORIZE_TRADING', scope: 'financed' }, { type: 'START' });
    expect(s.session.status).toBe('running');
    expect(s.session.authorization).toMatchObject({ scope: 'financed', loanId: s.loan!.loanId });
  });

  it('stale poll responses cannot restore running (regression 5)', () => {
    let s = initialFixture();
    const staleSeq = s.session.seq;
    s = reduce(s, { type: 'STOP', reason: 'Stopped by user', recovery: 'Press Resume' });
    s = reduce(s, { type: 'POLL_RESULT', seq: staleSeq, status: 'running' });
    expect(s.session.status).toBe('stopped');
    expect(s.session.ignoredPolls).toBe(1);
  });

  it('reward claim is a separate testnet record and never reduces USD debt', () => {
    const s0 = initialFixture();
    const s = reduce(s0, { type: 'CLAIM' });
    const r = s.receipts[0];
    expect(r.kind).toBe('testnet-reward-claim');
    if (r.kind === 'testnet-reward-claim') { expect(r.txHash).toBeNull(); expect(r.status).toBe('fixture-rehearsal'); }
    expect(debt(s)).toBe(debt(s0));
    expect(s.vault.principalBalance).toBe(s0.vault.principalBalance);
  });

  it('exhausted reserve accrues nothing more until a top-up, with no backfill', () => {
    let s = run(initialFixture(), { type: 'ADVANCE', seconds: 60 * 86_400 }, { type: 'CLAIM' });
    expect(s.vault.reserveBalance - s.vault.liabilities < 10n ** 18n).toBe(true);
    s = run(s, { type: 'ADVANCE', seconds: 30 * 86_400 });
    expect(() => reduce(s, { type: 'CLAIM' })).toThrow();
  });
});

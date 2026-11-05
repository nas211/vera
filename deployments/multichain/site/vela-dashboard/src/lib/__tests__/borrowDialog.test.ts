/**
 * [FIXTURE] Borrow-dialog outcome tests. Exercises the pure controller BorrowPanel uses,
 * wired to the real fixture reducer through an `act` identical in behavior to
 * FixtureProvider.act (ref-based, synchronous, returns error string or null).
 * This is NOT a DOM render test — no jsdom / testing-library is installed.
 */
import { describe, expect, it } from 'vitest';
import { confirmBorrowDialog, type BorrowDialogState } from '../borrowDialog';
import { initialFixture, reduce, type Action, type FixtureState } from '../vela/fixtureEngine';
import { deriveView } from '../../state/selectors';

function makeStore(start: FixtureState = initialFixture()) {
  const ref = { current: start };
  const calls: Action[] = [];
  const act = (a: Action) => {
    calls.push(a);
    try { ref.current = reduce(ref.current, a); return null; } catch (e) { return e instanceof Error ? e.message : String(e); }
  };
  return { ref, calls, act };
}
const openDialog = (requestId = 'req-1'): BorrowDialogState => ({ open: true, error: null, requestId, completedId: null });

describe('[FIXTURE] borrow dialog outcomes', () => {
  it('success closes the dialog, updates the loan, pauses entries and invalidates consent', () => {
    const store = makeStore();
    const before = store.ref.current;
    expect(before.session.authorization).not.toBeNull();
    const r = confirmBorrowDialog(openDialog(), 50_000_000n, store.act);
    expect(r.recorded).toBe(true);
    expect(r.next).toEqual({ open: false, error: null, requestId: null, completedId: 'req-1' });
    const s = store.ref.current;
    expect(s.loan!.principalUsd6).toBe(before.loan!.principalUsd6 + 50_000_000n);
    expect(s.session.entriesPaused).toBe(true);
    expect(s.session.authorization).toBeNull();
    expect(deriveView(s).entriesBlockedBy).toBe('awaiting new demo authorization');
    expect(s.positions).toEqual(before.positions); // protective management untouched
  });

  it('failure keeps the dialog OPEN with the engine error and changes nothing', () => {
    const store = makeStore();
    const before = store.ref.current;
    const r = confirmBorrowDialog(openDialog(), 10_000_000_000n, store.act);
    expect(r.recorded).toBe(false);
    expect(r.next.open).toBe(true);
    expect(r.next.error).toContain('capacity');
    expect(r.next.requestId).toBe('req-1');
    expect(store.ref.current).toBe(before);
  });

  it('stale price failure keeps the dialog open', () => {
    const store = makeStore(reduce(initialFixture(), { type: 'TOGGLE_STALE' }));
    const r = confirmBorrowDialog(openDialog(), 1_000_000n, store.act);
    expect(r.next.open).toBe(true);
    expect(r.next.error).toContain('stale');
  });

  it('repeated Confirm clicks create exactly one borrow', () => {
    const store = makeStore();
    const before = store.ref.current;
    let d = openDialog();
    const first = confirmBorrowDialog(d, 50_000_000n, store.act);
    // second click arrives with the pre-render dialog state but the updated completed ref
    d = { ...d, completedId: first.next.completedId };
    const second = confirmBorrowDialog(d, 50_000_000n, store.act);
    expect(second.skipped).toBe('already-completed');
    expect(store.calls.filter((a) => a.type === 'BORROW')).toHaveLength(1);
    expect(store.ref.current.loan!.principalUsd6).toBe(before.loan!.principalUsd6 + 50_000_000n);
  });

  it('even if the UI guard were bypassed, the engine dedupes the same request id', () => {
    const store = makeStore();
    const before = store.ref.current;
    confirmBorrowDialog(openDialog('same'), 50_000_000n, store.act);
    confirmBorrowDialog(openDialog('same'), 50_000_000n, store.act); // completedId lost
    expect(store.calls.filter((a) => a.type === 'BORROW')).toHaveLength(2);
    expect(store.ref.current.loan!.principalUsd6).toBe(before.loan!.principalUsd6 + 50_000_000n);
  });

  it('a failed attempt can be retried with the same id after the cause is fixed', () => {
    const store = makeStore(reduce(initialFixture(), { type: 'TOGGLE_STALE' }));
    const fail = confirmBorrowDialog(openDialog(), 1_000_000n, store.act);
    expect(fail.next.open).toBe(true);
    store.act({ type: 'TOGGLE_STALE' });
    const ok = confirmBorrowDialog(fail.next, 1_000_000n, store.act);
    expect(ok.recorded).toBe(true);
    expect(ok.next.open).toBe(false);
  });

  it('invalid amount or missing request id never calls the engine', () => {
    const store = makeStore();
    expect(confirmBorrowDialog(openDialog(), null, store.act).skipped).toBe('invalid-amount');
    expect(confirmBorrowDialog({ ...openDialog(), requestId: null }, 1_000_000n, store.act).skipped).toBe('no-request');
    expect(store.calls).toHaveLength(0);
  });

  it('resume after borrow requires fresh FINANCED demo authorization; entries then unblock', () => {
    const store = makeStore();
    confirmBorrowDialog(openDialog(), 50_000_000n, store.act);
    expect(store.act({ type: 'AUTHORIZE_TRADING', scope: 'unfinanced' })).toContain('financed');
    expect(store.act({ type: 'AUTHORIZE_TRADING', scope: 'financed' })).toBeNull();
    const s = store.ref.current;
    expect(s.session.authorization).toMatchObject({ scope: 'financed', loanId: s.loan!.loanId });
    expect(deriveView(s).entriesBlockedBy).toBeNull();
  });
});

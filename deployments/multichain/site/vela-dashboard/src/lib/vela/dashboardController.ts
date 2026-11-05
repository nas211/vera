/** UI coordination only. AccountService and NativeVaultWorkflow own all financial actions. */
import { AccountService, type AccountSnapshot, type ReviewedAction, type ServiceAction, type SessionContext } from './accountService';
import { NativeVaultWorkflow, type NativeAction, type NativePending, type NativeReceipt, type NativeReview, type NativeState } from './nativeVault';

export type DashboardReview = { kind: 'ledger'; action: ServiceAction; request: ReviewedAction } | { kind: 'native'; request: NativeReview };
export type DashboardState = {
  account: AccountSnapshot | null; native: NativeState | null; pending: NativePending | null;
  receipts: NativeReceipt[]; review: DashboardReview | null; busy: boolean; refreshing: boolean; error: string; nativeError: string;
  notice: string; refreshedAt: number | null;
};
const blank = (): DashboardState => ({ account: null, native: null, pending: null, receipts: [], review: null, busy: false, refreshing: false, error: '', nativeError: '', notice: '', refreshedAt: null });
const message = (e: unknown) => e instanceof Error ? e.message : 'The account request failed. Refresh to retry.';

export class DashboardController {
  private state = blank();
  private listeners = new Set<() => void>();
  private epoch = 0;
  private active = true;
  private context: SessionContext | null;
  private waiter: AbortController | null = null;
  private observation = 0;
  private observingEpoch: number | null = null;
  private refreshing: { epoch: number; promise: Promise<void> } | null = null;
  constructor(readonly service: AccountService | null, readonly workflow: NativeVaultWorkflow | null, private readonly makeKey: () => string = () => crypto.randomUUID(), private readonly refreshConfig?: ()=>Promise<void>) {
    this.context = service?.sessionContext() ?? null;
  }
  snapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private set(patch: Partial<DashboardState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()); }
  private valid(epoch: number) {
    if (!this.active || epoch !== this.epoch) return false;
    try { const c = this.service?.sessionContext(); return !this.service || !!c && c.address === this.context?.address && c.network === this.context.network && c.generation === this.context.generation; }
    catch { return false; }
  }
  start() { this.active = true; this.epoch++; return this.refresh(); }
  stop() { this.active = false; this.epoch++; this.refreshing = null; this.waiter?.abort(); this.set(blank()); }
  cancelReview() { if (!this.state.busy) this.set({ review: null, error: '' }); }
  stopWaiting() { this.waiter?.abort(); }
  private capture() {
    if (!this.workflow) return;
    try { this.set({ pending: this.workflow.pending() }); }
    catch (e) { this.set({ native: null, nativeError: message(e) }); }
  }
  private async run(fn: (epoch: number) => Promise<void>) {
    if (!this.valid(this.epoch) || !this.service || this.state.busy) return;
    const epoch = this.epoch;
    this.set({ busy: true, error: '', notice: '' });
    try {
      // A click takes priority over subsequent polling. Finish the one in-flight
      // sync before reviewing, so it cannot invalidate an account read mid-action.
      if (this.refreshing?.epoch === epoch) await this.refreshing.promise;
      if (this.valid(epoch)) await fn(epoch);
    }
    catch (e) { if (this.valid(epoch)) this.set({ error: message(e) }); }
    finally { if (this.valid(epoch)) { this.capture(); this.set({ busy: false }); } }
  }
  private async readAccount(epoch: number) {
    try {
      const account = await this.service!.refresh();
      if (this.valid(epoch)) this.set({ account, refreshedAt: Date.now() });
      return account;
    } catch (e) {
      if (this.valid(epoch)) this.set({ account: null, refreshedAt: null });
      throw e;
    }
  }
  private async observeReadOnly(epoch: number) {
    // A chain observation can outlast the polling interval. Reuse it rather
    // than continually superseding it and never displaying a result.
    if (this.observingEpoch === epoch) return;
    this.observingEpoch = epoch;
    const observation = ++this.observation;
    try {
      const native = await this.workflow!.refresh();
      if (this.valid(epoch) && observation === this.observation) this.set({ native, nativeError: '' });
    } catch (e) {
      if (this.valid(epoch) && observation === this.observation) this.set({ native: null, nativeError: message(e) });
    } finally {
      if (this.observingEpoch === epoch) this.observingEpoch = null;
    }
  }
  private async load(epoch: number, reconcile = true) {
    // Keep the last displayed values during refresh; actions remain guarded by
    // busy state and a fresh service read. Failed reads still clear balances.
    this.set({ nativeError: '' });
    if (!this.workflow?.writesEnabled) {
      await this.readAccount(epoch);
      if (this.valid(epoch) && this.workflow) void this.observeReadOnly(epoch);
      return;
    }
    this.observation++;
    // Show the authenticated ledger immediately on first connection. Native
    // synchronization still precedes the final read used for action reviews.
    if (!this.state.account) {
      await this.readAccount(epoch);
      if (!this.valid(epoch)) return;
    }
    if (this.workflow) {
      try {
        let state: NativeState;
        if (reconcile && this.workflow.pending()?.hash) {
          const result = await this.workflow.reconcile(); state = result.state;
          if (this.valid(epoch)) {
            if (result.receipt) this.set({ receipts: [result.receipt, ...this.state.receipts.filter(r => r.hash !== result.receipt!.hash)] });
            this.set({ notice: result.pending ? 'Transaction pending. No payout confirmed.' : result.receipt?.status === 'REVERTED' ? 'Transaction reverted; no payout applied. Network fees may have been charged.' : 'Transaction confirmed and account synchronized.' });
          }
        } else state = await this.workflow.refresh();
        if (!this.valid(epoch)) return;
        this.set({ native: state });
      } catch (e) { if (this.valid(epoch)) this.set({ native: null, nativeError: message(e) }); }
    }
    if (!this.valid(epoch)) return;
    // Native sync invalidates account reads, so this must run after it, never in parallel.
    const account = await this.readAccount(epoch);
    if (!this.valid(epoch)) return;
    const native = this.state.native;
    if (native && (native.vault?.address.toLowerCase() ?? null) !== (account.vault?.address.toLowerCase() ?? null)) {
      this.set({ native: null, nativeError: 'Vault observations disagree. Refresh before a wallet action.' });
    }
    this.set({ account, refreshedAt: Date.now() });
  }
  refresh() {
    if (!this.valid(this.epoch) || !this.service || this.state.busy) return Promise.resolve();
    if (this.refreshing?.epoch === this.epoch) return this.refreshing.promise;
    const epoch = this.epoch;
    const task = { epoch, promise: Promise.resolve() };
    this.refreshing = task;
    this.set({ refreshing: true, review: null, error: '' });
    task.promise = (async () => {
      try {
        // Background reads do not own the transaction lock or hide last figures.
        await Promise.all([this.refreshConfig?.(), this.load(epoch)]);
      } catch (e) { if (this.valid(epoch)) this.set({ error: message(e) }); }
      finally {
        if (this.valid(epoch)) {
          this.refreshing = null;
          this.capture(); this.set({ refreshing: false });
        }
      }
    })();
    return task.promise;
  }
  poll() { return this.state.review || this.state.busy ? Promise.resolve() : this.refresh(); }
  private checkLedgerAction(action: ServiceAction) {
    // Corrupt or unresolved wallet storage must not disable protective exits.
    if (!['stop', 'close', 'cancel'].includes(action.kind) && this.workflow?.pending()) throw Error('Reconcile the wallet transaction before changing this account. Protective exits remain available.');
  }
  reviewLedger(action: ServiceAction) {
    return this.run(async epoch => {
      this.set({ review: null });
      if(action.kind==='refreshQuote')await this.readAccount(epoch);
      else await this.load(epoch);
      if (!this.valid(epoch)) return;
      this.checkLedgerAction(action);
      const request = this.service!.review(action, this.makeKey());
      this.set({ review: { kind: 'ledger', action: structuredClone(action), request } });
    });
  }
  reviewNative(action: NativeAction) {
    return this.run(async epoch => {
      this.set({ review: null });
      if (!this.workflow?.writesEnabled) throw Error('Blockchain writes are OFF in this build');
      if (this.state.nativeError || !this.state.native || !this.state.account) throw Error('Refresh confirmed account and vault state first');
      const request = await this.workflow.review(action);
      if (this.valid(epoch)) this.set({ review: { kind: 'native', request } });
    });
  }
  confirm() {
    const review = this.state.review;
    if (!review) return Promise.resolve();
    return this.run(async epoch => {
      if (review.kind === 'ledger') {
        this.checkLedgerAction(review.action);
        // Keep the exact reviewed request after a failure, including its idempotency key.
        this.set({ account: null, refreshedAt: null });
        await this.service!.execute(review.request, true);
        if (!this.valid(epoch)) return;
        this.set({ review: null, notice: 'Request accepted. Refreshing confirmed account data.' });
        if(review.action.kind==='refreshQuote')await this.readAccount(epoch);
        else await this.load(epoch);
      } else {
        this.waiter = new AbortController();
        const pending = await this.workflow!.submit(review.request, true, this.waiter.signal);
        if (!this.valid(epoch)) return;
        this.set({ review: null, pending, native: null, notice: 'Submitted. Check confirmation before proceeding; no payout is confirmed yet.' });
      }
    });
  }
  recoverHash(hash: string) {
    return this.run(async epoch => {
      if (!this.workflow) throw Error('No wallet workflow available');
      await this.workflow.attachHash(hash); this.set({ review: null }); await this.load(epoch);
    });
  }
}

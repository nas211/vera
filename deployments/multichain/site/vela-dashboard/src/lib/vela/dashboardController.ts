/** UI coordination only. AccountService and NativeVaultWorkflow own all financial actions. */
import { AccountService, isTradingControl, type AccountSnapshot, type ReviewedAction, type ServiceAction, type SessionContext } from './accountService';
import { NativeVaultWorkflow, type NativeAction, type NativePending, type NativeReceipt, type NativeReview, type NativeState } from './nativeVault';

export type DashboardReview = { kind: 'ledger'; action: ServiceAction; request: ReviewedAction } | { kind: 'native'; request: NativeReview } | {kind:'preparing'; action:ServiceAction} | {kind:'preparingNative'; action:NativeAction};
export type DashboardState = {
  account: AccountSnapshot | null; native: NativeState | null; pending: NativePending | null;
  receipts: NativeReceipt[]; review: DashboardReview | null; busy: boolean; refreshing: boolean; error: string; nativeError: string;
  notice: string; refreshedAt: number | null;
  closing: Record<string, {orderId:string; qty:string; fraction:string}>;
};
const blank = (): DashboardState => ({ account: null, native: null, pending: null, receipts: [], review: null, busy: false, refreshing: false, error: '', nativeError: '', notice: '', refreshedAt: null, closing:{} });
const message = (e: unknown) => {
  const value = e as { message?: unknown; code?: unknown } | null;
  if (Number(value?.code) === -32002) return 'A MetaMask request is already open. Approve or reject it in your wallet before retrying.';
  if (Number(value?.code) === 4001) return 'Wallet request declined. No transaction was submitted by this request.';
  return typeof value?.message === 'string' && value.message.trim() ? value.message : 'The account request failed. Refresh to retry.';
};

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
  private accountObservation = 0;
  private closeTimer: ReturnType<typeof setTimeout> | null = null;
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
  stop() { this.active = false; this.epoch++; this.refreshing = null; this.waiter?.abort(); if(this.closeTimer)clearTimeout(this.closeTimer); this.closeTimer=null; this.set(blank()); }
  cancelReview() { if (!this.state.busy) this.set({ review: null, error: '' }); }
  stopWaiting() { this.waiter?.abort(); }
  private capture() {
    if (!this.workflow) return;
    try { this.set({ pending: this.workflow.pending() }); }
    catch (e) { this.set({ native: null, nativeError: message(e) }); }
  }
  private async run(fn: (epoch: number) => Promise<void>,waitForRefresh=true, preparing?:DashboardReview) {
    if (!this.active || this.state.busy) return;
    if (!this.valid(this.epoch) || !this.service) {
      this.set({ review: null, account: null, native: null, error: 'Wallet connection changed. Reconnect and review the action again.' });
      return;
    }
    const epoch = this.epoch;
    this.set({ busy: true, error: '', notice: '', ...(preparing?{review:preparing}:{}) });
    try {
      // A click takes priority over subsequent polling. Finish the one in-flight
      // sync before reviewing, so it cannot invalidate an account read mid-action.
      if (waitForRefresh&&this.refreshing?.epoch === epoch) await this.refreshing.promise;
      if (this.valid(epoch)) await fn(epoch);
    }
    catch (e) { if (this.valid(epoch)) this.set({ error: message(e) }); }
    finally { if (this.valid(epoch)) { this.capture(); this.set({ busy: false }); } }
  }
  private acceptAccount(account:AccountSnapshot) {
    const closing={...this.state.closing};let notice=this.state.notice,error=this.state.error;
    for(const [id,request] of Object.entries(closing)) {
      const position=account.paper.positions.find(p=>p.id===id),order=account.paper.orders.find(o=>o.id===request.orderId);
      if(!position || (request.fraction==='0.5'&&position.qty!==request.qty)) {
        delete closing[id];notice=position?'Trade reduced.':'Trade closed.';
      } else if(order && ['rejected','cancelled','expired','failed'].includes(order.status)) {
        delete closing[id];notice='';error=order.error||'Close request did not fill. Review the trade and retry.';
      }
    }
    this.set({account,closing,notice,error,refreshedAt:Date.now()});
  }
  private async readAccount(epoch: number, independent=false) {
    const observation=++this.accountObservation;
    try {
      const account = await (independent?this.service!.inspect():this.service!.refresh());
      if (this.valid(epoch)&&observation===this.accountObservation) this.acceptAccount(account);
      return account;
    } catch (e) {
      if (this.valid(epoch)&&observation===this.accountObservation) this.set({ account: null, refreshedAt: null });
      throw e;
    }
  }
  private watchClose(epoch:number, attempt=0) {
    if(this.closeTimer)clearTimeout(this.closeTimer);
    if(!this.valid(epoch)||!Object.keys(this.state.closing).length||attempt>=30)return;
    this.closeTimer=setTimeout(async()=>{
      this.closeTimer=null;
      if(!this.valid(epoch))return;
      try { await this.readAccount(epoch,true); }
      catch(e) { if(this.valid(epoch))this.set({error:'Close status unavailable. '+message(e)}); }
      this.watchClose(epoch,attempt+1);
    },attempt<10?500:2000);
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
  private async load(epoch: number, reconcile = true, background=false) {
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
    if(background&&(this.state.busy||this.state.review))return;
    // Native sync invalidates account reads, so this must run after it, never in parallel.
    const account = await this.readAccount(epoch);
    if (!this.valid(epoch)) return;
    const native = this.state.native;
    if (native && (native.vault?.address.toLowerCase() ?? null) !== (account.vault?.address.toLowerCase() ?? null)) {
      this.set({ native: null, nativeError: 'Vault observations disagree. Refresh before a wallet action.' });
    }
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
        await Promise.all([this.refreshConfig?.(), this.load(epoch,true,true)]);
      } catch (e) { if (this.valid(epoch)&&!this.state.busy&&!this.state.review) this.set({ error: message(e) }); }
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
  private async refreshBorrowQuote(epoch:number) {
    const account=await this.readAccount(epoch),quote=account.health?.quote;
    const age=quote?Date.now()/1000-quote.at:Infinity;
    if(!quote||age< -1||age>300) {
      if(!this.valid(epoch))return;
      this.checkLedgerAction({kind:'refreshQuote'});
      const request=this.service!.review({kind:'refreshQuote'},this.makeKey());
      await this.service!.execute(request,true);
      if(this.valid(epoch))await this.readAccount(epoch);
    }
  }
  reviewLedger(action: ServiceAction) {
    if(isTradingControl(action))return this.run(async epoch=>{
      this.checkLedgerAction(action);
      if(action.kind==='close'&&(this.state.closing[action.positionId]||this.state.account?.paper.orders.some(o=>o.status==='pending'&&o.payload?.reduceOnly&&o.payload.positionId===action.positionId)))throw Error('This trade is already closing.');
      const observation=++this.accountObservation;
      const {snapshot,request}=await this.service!.prepareTrading(action,this.makeKey());
      if(!this.valid(epoch))return;
      if(observation===this.accountObservation)this.acceptAccount(snapshot);
      this.set({review:{kind:'ledger',action:structuredClone(action),request}});
    },false,{kind:'preparing',action:structuredClone(action)});
    return this.run(async epoch => {
      this.set({ review: null });
      if(action.kind==='refreshQuote')await this.readAccount(epoch);
      else await this.load(epoch);
      if (!this.valid(epoch)) return;
      this.checkLedgerAction(action);
      if(action.kind==='issueBorrow')await this.refreshBorrowQuote(epoch);
      if(!this.valid(epoch))return;
      const request = this.service!.review(action, this.makeKey());
      this.set({ review: { kind: 'ledger', action: structuredClone(action), request } });
    });
  }
  reviewNative(action: NativeAction) {
    return this.run(async epoch => {
      if(this.state.review?.kind!=='preparingNative')this.set({ review: null });
      if (!this.workflow?.writesEnabled) throw Error('Blockchain writes are OFF in this build');
      if (this.state.nativeError || !this.state.native || !this.state.account) throw Error('Refresh confirmed account and vault state first');
      if(action.kind==='requestBorrow')await this.refreshBorrowQuote(epoch);
      if(!this.valid(epoch))return;
      const request = await this.workflow.review(action);
      if (this.valid(epoch)) this.set({ review: { kind: 'native', request } });
    },action.kind!=='deposit',['authorize','startTrading'].includes(action.kind)?{kind:'preparingNative',action:structuredClone(action)}:undefined);
  }
  confirm() {
    const review = this.state.review;
    if (!review || review.kind==='preparing'||review.kind==='preparingNative') return Promise.resolve();
    return this.run(async epoch => {
      if (review.kind === 'ledger') {
        this.checkLedgerAction(review.action);
        // Keep the exact reviewed request after a failure, including its idempotency key.
        const fast=isTradingControl(review.action),position=review.action.kind==='close'?this.state.account?.paper.positions.find(p=>p.id===(review.action as {positionId:string}).positionId):null;
        if(!fast)this.set({ account: null, refreshedAt: null });
        if(review.action.kind==='close')this.set({notice:'Closing trade…'});
        const result=await this.service!.execute(review.request, true);
        if (!this.valid(epoch)) return;
        this.set({ review: null, notice: review.action.kind === 'allocate'
          ? 'Demo trading cash added. Your test DOGE wallet and vault collateral are unchanged.'
          : review.action.kind==='close'?'Closing trade…':'Request accepted. Refreshing confirmed account data.' });
        if(review.action.kind==='close' && position && typeof (result as {id?:unknown})?.id==='string') {
          this.set({closing:{...this.state.closing,[position.id]:{orderId:(result as {id:string}).id,qty:position.qty,fraction:review.action.fraction}}});
          this.watchClose(epoch);
        }
        if(fast)await this.readAccount(epoch,true);
        else if(review.action.kind==='refreshQuote')await this.readAccount(epoch);
        else await this.load(epoch);
      } else {
        this.waiter = new AbortController();
        this.set({ notice: review.request.action.kind==='deposit'?'Opening MetaMask…':'Checking wallet, vault and fees before opening MetaMask…' });
        const pending = await this.workflow!.submit(review.request, true, this.waiter.signal, () => {
          if (this.valid(epoch)) this.set({ notice: 'Request sent to MetaMask. Approve or reject it in your wallet.' });
        });
        if (!this.valid(epoch)) return;
        this.set({ review: null, pending, native: null, notice: 'Submitted. Check confirmation before proceeding; no payout is confirmed yet.' });
      }
    },review.kind==='ledger'?!isTradingControl(review.action):review.request.action.kind!=='deposit');
  }
  recoverHash(hash: string) {
    return this.run(async epoch => {
      if (!this.workflow) throw Error('No wallet workflow available');
      await this.workflow.attachHash(hash); this.set({ review: null }); await this.load(epoch);
    });
  }
}

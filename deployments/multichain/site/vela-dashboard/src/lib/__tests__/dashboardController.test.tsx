import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { AccountService, type AccountSnapshot, type ApiRequest, type SessionContext } from '../vela/accountService';
import { DashboardController } from '../vela/dashboardController';
import type { NativePending, NativeReceipt, NativeReview, NativeState, NativeVaultWorkflow } from '../vela/nativeVault';
import { NativeReviewDetails } from '../../service/approved/NativeReviewDetails';
import { AccountDashboard } from '../../service/approved/AccountDashboard';
import { AccountFills } from '../../service/approved/ActivityPanels';
import { amountOk, maxCash, units } from '../../service/approved/presentation';
import { AccountContext, useAccountDashboard } from '../../service/approved/context';
const context: SessionContext = { address: `0x${'11'.repeat(20)}`, network: 'dogeos', generation: 1 };
const vault = `0x${'22'.repeat(20)}` as const;
const txhash = `0x${'33'.repeat(32)}` as const;
const account = (): AccountSnapshot => ({
  address: context.address, network: 'dogeos', vault: { address: vault, collateral: '1000000000000000000', loanState: 2 },
  health: { debt: '10.004321', healthFactor: '2', ltv: '0.325', interest: '0.004321', principal: '10', borrowCapacity: '30', quote: { at: Date.now()/1000, source: 'ILLUSTRATIVE' }, loan: { id: 'loan1', state: 'ACTIVE', principal: '10', interest: '0.004321' } },
  paper: { mode: 'PAPER', balance: '15', equity: '15', unrealized: '0', positions: [{ id: 'long-1', symbol: 'BTC/USD', side: 'long', qty: '1', entry: '100', stop: '99', riskBudget: '0.75', unrealized: '0', fresh: true, mark: '100' }], orders: [], today: { realized: '-1', trades: 1, wins: 0, losses: 1 }, totals: { realized: '5', trades: 4, wins: 2, losses: 2 }, preferences: { autonomous: false }, entryStop: { reason: 'Owner stopped new entries', action: 'Review permission then resume' }, trades: [], costs: { tradingFee: '0.1' } },
  repayment: { simulated: true, freeEquity: '12.000009', maxRepayable: '10.004321', debt: '10.004321', restriction: null, policy: null }, tradingPermission: { maxLoss: '750000' }, events: [],
});
const nativeState = (): NativeState => ({
  source: 'chain', chainId: 6281971, owner: context.address, factory: vault, factoryKind: 'rewarded', bytecodeVerified: true,
  factoryCodeHash: txhash, vaultCodeHash: txhash, block: 10, blockHash: txhash, observedAt: Date.now()/1000, confirmations: 3, walletBalanceWei: '1000000000000000000',
  vault: { address: vault, owner: context.address, kind: 'rewarded', collateral: '1000000000000000000', withdrawable: '0', loanState: 2, loanId: txhash, loanNonce: 1, principalMicro: '10000000', paused: false, trading: false, settlementSigner: vault, stopBudgetMicro: '750000', limits: { maxLoss: '750000' }, rewards: { source: 'chain', pool: vault, aprBps: '500', reserveWei: '10000', claimableWei: '1000', availableReserveWei: '9000', claimedWei: '0', fundedWei: '10000', principalWei: '1000000000000000000', loanLocked: true } }, rewardPool: {source:'chain',address:vault,factory:vault,manager:context.address,codeHash:txhash,bytecodeVerified:true,aprBps:'500',reserveWei:'10000',availableReserveWei:'9000',reservedWei:'1000',fundedWei:'10000',claimedWei:'0',totalPrincipalWei:'1000000000000000000'}, transaction: null,
});
const pendingTx = (): NativePending => ({ version: 1, owner: context.address, chainId: 6281971, action: { kind: 'claim' }, tx: { from: context.address as `0x${string}`, to: vault, value: '0x0', data: '0x1234' }, hash: txhash, createdAt: 1, phase: 'SUBMITTED' });
function setup() {
  let value = account(), failRead = false, failPost = false;
  const calls: ApiRequest[] = [];
  const service = new AccountService(async request => {
    calls.push(request);
    if (request.body) { if (failPost) throw Error('Response lost'); return { accepted: true }; }
    if (failRead) throw Error('Account offline');
    return structuredClone(value);
  });
  service.setSession(context);
  let counter = 0;
  const controller = new DashboardController(service, null, () => `review-key-${++counter}`);
  return { service, calls, controller, set: (v: AccountSnapshot) => { value = v; }, failRead: (v: boolean) => { failRead = v; }, failPost: (v: boolean) => { failPost = v; } };
}
function nativeStub() {
  let pending: NativePending | null = null;
  const state = nativeState();
  const stub = {
    writesEnabled: false,
    refresh: vi.fn(async () => state), pending: () => pending,
    review: vi.fn(async () => ({ owner: context.address, generation: 1, factory: vault, vault, loanId: txhash, loanNonce: 1, action: { kind: 'claim' }, tx: pendingTx().tx, feeReserveWei: '100', createdAt: 1 } as NativeReview)),
    submit: vi.fn<(r: NativeReview, confirmed: boolean, signal?: AbortSignal) => Promise<NativePending>>().mockImplementation(async () => { pending = pendingTx(); return pending; }),
    reconcile: vi.fn(async (): Promise<{ state: NativeState; pending: boolean; receipt?: NativeReceipt }> => ({ state, pending: true })),
    attachHash: vi.fn((hash: string) => { pending = { ...pendingTx(), hash: hash as `0x${string}` }; }),
  };
  return { stub, workflow: stub as unknown as NativeVaultWorkflow, setPending: (p: NativePending | null) => { pending = p; } };
}
const render = (controller: DashboardController, route = '/') => renderToStaticMarkup(<MemoryRouter initialEntries={[route]}><AccountDashboard service={controller.service} controller={controller} /></MemoryRouter>);
const NativeGate = () => <span>{useAccountDashboard().nativeReason ?? 'Ready for review'}</span>;
const gate = (controller: DashboardController) => renderToStaticMarkup(<AccountContext.Provider value={controller}><NativeGate /></AccountContext.Provider>);

describe('approved account presentation and coordination', () => {
  it('keeps the main screens simple and moves preferences to settings',async()=>{
    const s=setup();await s.controller.refresh();
    const overview=render(s.controller,'/'),trading=render(s.controller,'/trading'),settings=render(s.controller,'/settings');
    expect(overview).toContain('Loan amount');expect(overview).toContain('Amount to repay');expect(overview).toContain('Open trades');
    expect(overview).not.toContain('Connections');
    expect(trading).toContain('Start Trading');expect(trading).toContain('/settings?section=trading');
    expect(trading).not.toContain('Paper fills');expect(trading).not.toContain('Position size');
    expect(settings).toContain('Change profile picture');expect(settings).toContain('Light');expect(settings).toContain('Dark');
    expect(settings).toContain('Trading settings');expect(settings).toContain('account limit $0.75');
  });
  it('refreshes an expired test valuation as part of reviewing a loan, without issuing credit or sending a wallet transaction',async()=>{
    const s=setup(),n=nativeStub();n.stub.writesEnabled=true;
    const a=account();a.health!.quote!.at=0;s.set(a);
    const c=new DashboardController(s.service,n.workflow);await c.refresh();
    await c.reviewNative({kind:'requestBorrow',amount:'1',loanId:txhash});
    expect(s.calls.filter(r=>r.body).map(r=>r.path)).toEqual(['/api/quote']);
    expect(n.stub.review).toHaveBeenCalledWith({kind:'requestBorrow',amount:'1',loanId:txhash});
    expect(n.stub.submit).not.toHaveBeenCalled();
  });
  it('refreshes the illustrative quote without starting a slow native observation', async () => {
    const s=setup(), n=nativeStub();n.stub.writesEnabled=true;
    n.stub.refresh.mockImplementation(()=>new Promise(()=>{}));
    const c=new DashboardController(s.service,n.workflow,()=> 'quote-refresh-key');
    await c.reviewLedger({kind:'refreshQuote'});await c.confirm();
    expect(s.calls.filter(r=>r.body).map(r=>r.path)).toEqual(['/api/quote']);
    expect(n.stub.refresh).not.toHaveBeenCalled();expect(c.snapshot().busy).toBe(false);
    expect(c.snapshot().account).not.toBeNull();
  });
  it('reviews and confirms a deposit while a background vault poll is still waiting',async()=>{
    const s=setup(),n=nativeStub();n.stub.writesEnabled=true;
    const c=new DashboardController(s.service,n.workflow);await c.refresh();
    const q=await n.stub.review();n.stub.review.mockClear();n.stub.review.mockResolvedValue({...q,action:{kind:'deposit',amount:'1'}});
    let finish!:(s:NativeState)=>void;n.stub.refresh.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const polling=c.poll();await c.reviewNative({kind:'deposit',amount:'1'});
    expect(c.snapshot().refreshing).toBe(true);expect(n.stub.review).toHaveBeenCalledOnce();
    await c.confirm();expect(n.stub.submit).toHaveBeenCalledOnce();
    finish(nativeState());await polling;
  });
  it('lets funding review queue once behind a slow background sync, without repeating polls', async () => {
    const s = setup(), n = nativeStub(); n.stub.writesEnabled = true;
    const c = new DashboardController(s.service, n.workflow); await c.refresh();
    let finish!: (value: NativeState) => void;
    n.stub.refresh.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const reading = c.poll();
    expect(c.snapshot().refreshing).toBe(true); expect(c.snapshot().busy).toBe(false);
    expect(gate(c)).toContain('Ready for review'); expect(c.snapshot().account).not.toBeNull();
    const review = c.reviewNative({ kind: 'fundReserve', amount: '1' });
    expect(c.snapshot().busy).toBe(true); expect(n.stub.review).not.toHaveBeenCalled();
    await c.poll(); await c.reviewNative({ kind: 'fundReserve', amount: '1' });
    expect(n.stub.refresh).toHaveBeenCalledTimes(2);
    finish(nativeState()); await reading; await review;
    expect(c.snapshot().refreshing).toBe(false); expect(c.snapshot().busy).toBe(false);
    expect(c.snapshot().review?.kind).toBe('native'); expect(n.stub.review).toHaveBeenCalledOnce();
    expect(n.stub.review).toHaveBeenCalledWith({ kind: 'fundReserve', amount: '1' });
    expect(n.stub.submit).not.toHaveBeenCalled();
    await c.poll(); expect(n.stub.refresh).toHaveBeenCalledTimes(2);
  });
  it('keeps first-load and failed-refresh data guarded before a queued review', async () => {
    const s = setup(), n = nativeStub(); n.stub.writesEnabled = true;
    const c = new DashboardController(s.service, n.workflow);
    expect(gate(c)).not.toContain('Ready for review'); await c.refresh();
    let fail!: (reason: Error) => void;
    n.stub.refresh.mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }));
    const reading = c.poll(), review = c.reviewNative({ kind: 'fundReserve', amount: '1' });
    fail(Error('RPC unavailable')); await reading; await review;
    expect(c.snapshot().native).toBeNull(); expect(c.snapshot().busy).toBe(false);
    expect(n.stub.review).not.toHaveBeenCalled(); expect(gate(c)).not.toContain('Ready for review');
  });
  it('discards a queued review when the wallet changes during background refresh', async () => {
    const s = setup(), n = nativeStub(); n.stub.writesEnabled = true;
    const c = new DashboardController(s.service, n.workflow); await c.refresh();
    let finish!: (value: NativeState) => void;
    n.stub.refresh.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const reading = c.poll(), review = c.reviewNative({ kind: 'fundReserve', amount: '1' });
    c.stop(); s.service.setSession({ ...context, generation: 2 }); finish(nativeState());
    await reading; await review;
    expect(c.snapshot().account).toBeNull(); expect(c.snapshot().review).toBeNull();
    expect(n.stub.review).not.toHaveBeenCalled(); expect(n.stub.submit).not.toHaveBeenCalled();
  });
  it('refreshes activation without holding up account figures or bypassing the native gate', async () => {
    const s = setup(), n = nativeStub(); let resolve!: () => void;
    const refreshConfig = vi.fn(() => new Promise<void>(r => { resolve = r; }));
    const c = new DashboardController(s.service, n.workflow, undefined, refreshConfig);
    const pending = c.refresh();
    await vi.waitFor(() => expect(c.snapshot().account?.paper.balance).toBe('15'));
    resolve(); await pending;
    expect(refreshConfig).toHaveBeenCalledTimes(1);
    await c.reviewNative({ kind: 'fundReserve', amount: '1' });
    expect(n.stub.review).not.toHaveBeenCalled(); expect(n.stub.submit).not.toHaveBeenCalled();
  });
  it.each(['/', '/vault', '/borrow', '/trading', '/risk'])('renders %s without invented balances or connected actions', route => {
    const html = render(new DashboardController(null, null), route);
    expect(html).toContain('Connect your wallet to load your account');
    expect(html).toContain('disabled=""');
    expect(html).not.toContain('FixtureProvider');
    expect(html).not.toContain('Healthy');
  });
  it('renders authoritative amounts, separate P&L scopes and stricter stop limits', async () => {
    const s = setup(); await s.controller.refresh();
    const html = renderToStaticMarkup(<AccountContext.Provider value={s.controller}><AccountFills /></AccountContext.Provider>)+render(s.controller,'/');
    expect(html).toContain('Today UTC'); expect(html).toContain('All-time');
    expect(html).toContain('1 fills · 0 wins / 1 losses'); expect(html).toContain('4 fills · 2 wins / 2 losses');
    expect(html).toContain('$0.75'); expect(render(s.controller,'/trading')).toContain('Owner stopped new entries');
    expect(html).toContain('Close half');
    expect(html).toContain('Slippage or funding can exceed this');
  });
  it('clears stale balances after failure and restores only a successful read', async () => {
    const s = setup(); await s.controller.refresh(); s.failRead(true); await s.controller.refresh();
    expect(s.controller.snapshot().account).toBeNull(); expect(render(s.controller)).not.toContain('$15.00');
    expect(s.controller.snapshot().error).toContain('offline');
    s.failRead(false); await s.controller.refresh(); expect(s.controller.snapshot().account?.paper.balance).toBe('15');
  });
  it('shows initial account data, then re-reads after native synchronization', async () => {
    const s = setup(), n = nativeStub();
    n.stub.writesEnabled = true;
    n.stub.refresh.mockImplementation(async () => { expect(s.calls).toHaveLength(1); return nativeState(); });
    const c = new DashboardController(s.service, n.workflow); await c.refresh();
    expect(c.snapshot().account).not.toBeNull(); expect(c.snapshot().native).not.toBeNull();
    expect(s.calls).toHaveLength(2);
    n.stub.refresh.mockRejectedValue(Error('RPC unavailable')); await c.refresh();
    expect(c.snapshot().native).toBeNull(); expect(c.snapshot().nativeError).toContain('RPC unavailable');
    expect(c.snapshot().account).not.toBeNull();
  });
  it('keeps account balances and ledger controls available while a read-only vault request stalls', async () => {
    const s = setup(), n = nativeStub();
    let resolveNative!: (value: NativeState) => void;
    n.stub.refresh.mockImplementation(() => new Promise(resolve => { resolveNative = resolve; }));
    const c = new DashboardController(s.service, n.workflow);
    await c.refresh();
    expect(c.snapshot().account?.paper.balance).toBe('15');
    expect(c.snapshot().busy).toBe(false);
    await c.reviewLedger({kind:'stop'});
    expect(c.snapshot().review?.kind).toBe('ledger');
    expect(c.snapshot().account?.paper.balance).toBe('15');
    expect(n.stub.refresh).toHaveBeenCalledTimes(1);
    resolveNative(nativeState());
    await Promise.resolve();
    expect(c.snapshot().native).not.toBeNull();
  });
  it('blocks native actions on disagreeing vault observations', async () => {
    const s = setup(), n = nativeStub(), different = nativeState(); different.vault!.address = `0x${'44'.repeat(20)}`;
    n.stub.refresh.mockResolvedValue(different); n.stub.writesEnabled = true;
    const c = new DashboardController(s.service, n.workflow); await c.refresh(); await c.reviewNative({ kind: 'claim' });
    expect(c.snapshot().native).toBeNull(); expect(c.snapshot().nativeError).toContain('disagree'); expect(n.stub.review).not.toHaveBeenCalled();
  });
  it('keeps the native write gate off before any wallet review or submission', async () => {
    const s = setup(), n = nativeStub(), c = new DashboardController(s.service, n.workflow);
    await c.refresh(); await c.reviewNative({ kind: 'claim' }); await c.confirm();
    expect(c.snapshot().error).toContain('OFF'); expect(n.stub.review).not.toHaveBeenCalled(); expect(n.stub.submit).not.toHaveBeenCalled();
  });
  it('retains the exact reviewed amount and retry key after a lost response', async () => {
    const s = setup(); const action = { kind: 'repay' as const, amount: '1.01' };
    await s.controller.reviewLedger(action); const review = s.controller.snapshot().review;
    action.amount = '10'; expect(s.calls.filter(x => x.body)).toHaveLength(0);
    s.failPost(true); await s.controller.confirm();
    expect(s.controller.snapshot().review).toBe(review); expect(s.controller.snapshot().account).toBeNull();
    s.failPost(false); await s.controller.confirm();
    const posts = s.calls.filter(x => x.body); expect(posts).toHaveLength(2);
    expect(posts.map(x => x.key)).toEqual(['review-key-1', 'review-key-1']);
    expect(posts.map(x => x.body?.amount)).toEqual(['1.01', '1.01']); expect(s.controller.snapshot().review).toBeNull();
  });
  it('shows plain-object mobile wallet errors without losing the reviewed deposit', async () => {
    const s=setup(),n=nativeStub();n.stub.writesEnabled=true;
    n.stub.submit.mockRejectedValue({code:-32002,message:'Request already pending'});
    const c=new DashboardController(s.service,n.workflow);
    await c.refresh();await c.reviewNative({kind:'deposit',amount:'1'});
    const review=c.snapshot().review;await c.confirm();
    expect(c.snapshot().review).toBe(review);
    expect(c.snapshot().busy).toBe(false);
    expect(c.snapshot().error).toContain('MetaMask request is already open');
    expect(render(c,'/vault')).toContain('MetaMask request is already open');
  });
  it('explains an invalidated connection instead of silently ignoring confirmation', async () => {
    const s=setup();await s.controller.reviewLedger({kind:'allocate',amount:'10'});
    s.service.setSession({...context,generation:2});await s.controller.confirm();
    expect(s.controller.snapshot().error).toContain('Reconnect');
    expect(s.controller.snapshot().review).toBeNull();
    expect(s.calls.filter(x=>x.body)).toHaveLength(0);
  });
  it('distinguishes a successful demo allocation from a vault deposit', async () => {
    const s=setup();await s.controller.reviewLedger({kind:'allocate',amount:'10'});await s.controller.confirm();
    expect(s.calls.filter(x=>x.body).map(x=>x.path)).toEqual(['/api/trading/allocate']);
    expect(s.controller.snapshot().notice).toContain('Demo trading cash added');
    expect(s.controller.snapshot().notice).toContain('vault collateral are unchanged');
  });
  it('coalesces double confirmation and respects cancellation and review-stable polling', async () => {
    const s = setup(); await s.controller.reviewLedger({ kind: 'withdraw', amount: '1' });
    const review = s.controller.snapshot().review, reads = s.calls.length; await s.controller.poll();
    expect(s.controller.snapshot().review).toBe(review); expect(s.calls).toHaveLength(reads);
    s.controller.cancelReview(); await s.controller.confirm(); expect(s.calls.filter(x => x.body)).toHaveLength(0);
    await s.controller.reviewLedger({ kind: 'withdraw', amount: '1' });
    await Promise.all([s.controller.confirm(), s.controller.confirm()]); expect(s.calls.filter(x => x.body)).toHaveLength(1);
  });
  it('preserves the stricter account limit and rejects a changed loan before submission', async () => {
    const s = setup(); await s.controller.reviewLedger({ kind: 'permission', allocation: '10', positionSize: '5', maxLoss: '2' });
    const review = s.controller.snapshot().review; expect(review?.kind === 'ledger' && review.request.body.maxLoss).toBe('0.75');
    const changed = account(); changed.health!.loan!.id = 'different-loan'; s.set(changed); await s.controller.confirm();
    expect(s.controller.snapshot().error).toContain('Loan or vault changed'); expect(s.calls.filter(x => x.body)).toHaveLength(0);
  });
  it('discards a late read after unmount and a wallet generation change', async () => {
    let resolve!: (x: unknown) => void;
    const service = new AccountService(() => new Promise(r => { resolve = r; })); service.setSession(context);
    const c = new DashboardController(service, null); const wait = c.refresh();
    c.stop(); service.setSession({ ...context, generation: 2 }); resolve(account()); await wait;
    expect(c.snapshot().account).toBeNull(); expect(c.snapshot().busy).toBe(false); expect(c.snapshot().error).toBe('');
  });
  it('survives a StrictMode start/stop/start replay without accepting the first read', async () => {
    const resolves: ((x: unknown) => void)[] = [];
    const service = new AccountService(() => new Promise(r => resolves.push(r))); service.setSession(context);
    const c = new DashboardController(service, null); c.start(); c.stop(); service.setSession(context); c.start();
    resolves[0](account()); await vi.waitFor(() => expect(resolves).toHaveLength(2));
    expect(c.snapshot().account).toBeNull(); resolves[1](account());
    await vi.waitFor(() => expect(c.snapshot().account?.paper.balance).toBe('15')); c.stop();
  });
  it('blocks spending while a native transaction is pending but allows protective closes', async () => {
    const s = setup(), n = nativeStub(); n.setPending(pendingTx());
    const c = new DashboardController(s.service, n.workflow, () => 'protective-close');
    await c.reviewLedger({ kind: 'withdraw', amount: '1' }); expect(c.snapshot().review).toBeNull(); expect(c.snapshot().error).toContain('Reconcile');
    await c.reviewLedger({ kind: 'close', positionId: 'long-1', fraction: '1' }); await c.confirm();
    expect(s.calls.filter(x => x.body).map(x => x.path)).toEqual(['/api/trading/positions/long-1/close']);
  });
  it('keeps protective closes available with corrupt pending-wallet storage', async () => {
    const s = setup(), n = nativeStub(); n.stub.pending = () => { throw Error('Invalid saved transaction'); };
    const c = new DashboardController(s.service, n.workflow, () => 'protective-corrupt');
    await c.reviewLedger({ kind: 'close', positionId: 'long-1', fraction: '1' }); await c.confirm();
    expect(c.snapshot().error).toBe(''); expect(c.snapshot().nativeError).toContain('Invalid saved');
    expect(s.calls.filter(x => x.body).map(x => x.path)).toEqual(['/api/trading/positions/long-1/close']);
  });
  it('rechecks pending wallet state at confirmation before changing demo cash', async () => {
    const s = setup(), n = nativeStub(), c = new DashboardController(s.service, n.workflow, () => 'pending-recheck');
    await c.reviewLedger({ kind: 'withdraw', amount: '1' }); n.setPending(pendingTx()); await c.confirm();
    expect(c.snapshot().error).toContain('Reconcile'); expect(s.calls.filter(x => x.body)).toHaveLength(0);
  });
  it.each(['CONFIRMED', 'REVERTED'] as const)('reports %s claim outcomes only after reconciliation', async status => {
    const s = setup(), n = nativeStub(); n.stub.writesEnabled = true;
    const c = new DashboardController(s.service, n.workflow); await c.refresh(); await c.reviewNative({ kind: 'claim' });
    expect(n.stub.submit).not.toHaveBeenCalled(); await c.confirm();
    expect(c.snapshot().receipts).toHaveLength(0); expect(c.snapshot().notice).toContain('no payout is confirmed');
    await c.refresh(); expect(c.snapshot().receipts).toHaveLength(0);
    n.stub.reconcile.mockImplementation(async () => { n.setPending(null); return { state: nativeState(), pending: false, receipt: { kind: 'native-reward-claim', action: 'claim', hash: txhash, block: 10, status, rewardPaidWei: status === 'CONFIRMED' ? '1000' : '0' } }; });
    await c.refresh(); expect(c.snapshot().pending).toBeNull(); expect(c.snapshot().receipts[0].status).toBe(status);
    expect(c.snapshot().notice).toContain(status === 'CONFIRMED' ? 'confirmed and account synchronized' : 'reverted; no payout');
    expect(render(c, '/vault')).toContain(status === 'CONFIRMED' ? '0.000000000000001 test DOGE paid' : 'No payout');
  });
  it('stopping a wallet wait preserves unknown durable state for hash recovery', async () => {
    const s = setup(), n = nativeStub(); n.stub.writesEnabled = true;
    n.stub.submit.mockImplementation(async (_r, _confirmed, signal) => { n.setPending({ ...pendingTx(), hash: null, phase: 'AWAITING_WALLET' }); return new Promise((_resolve, reject) => { signal!.addEventListener('abort', () => reject(Error('Wallet response unknown'))); }); });
    const c = new DashboardController(s.service, n.workflow); await c.refresh(); await c.reviewNative({ kind: 'claim' });
    const waiting = c.confirm(); c.stopWaiting(); await waiting;
    expect(c.snapshot().pending?.hash).toBeNull(); expect(render(c)).toContain('wallet response unknown');
    await c.recoverHash(txhash); expect(n.stub.attachHash).toHaveBeenCalledWith(txhash); expect(c.snapshot().pending?.hash).toBe(txhash);
  });
  it('shows exact sub-display-precision wallet fees and certified collateral in the review', async () => {
    const n = nativeStub(), review = await n.stub.review();
    const html = renderToStaticMarkup(<NativeReviewDetails review={{ ...review, feeReserveWei: '52700', tx: { ...review.tx, value: '0x1' }, certificate: { action: 2, a: '1000001', b: '1', c: '0', deadline: 1, signature: '0x' } }} />);
    expect(html).toContain('0.0000000000000527 test DOGE');
    expect(html.match(/0\.000000000000000001 test DOGE/g)).toHaveLength(2);
    expect(html).toContain('$1.000001 demo USD');
  });
  it('rounds availability down and liabilities up without accepting extra form precision', () => {
    expect(units('1.0000009')).toBe(1000000n); expect(units('1.0000009',6,true)).toBe(1000001n);
    expect(units('1e6')).toBeNull(); expect(units('')).toBeNull(); expect(maxCash('12.009999')).toBe('12.00');
    expect(amountOk('1.001',6,2_000_000n,2)).toBe(false); expect(amountOk('1.01',6,1_009_999n,2)).toBe(false);
    expect(amountOk('1.01',6,1_010_000n,2)).toBe(true);
  });
});


describe('immediate trading controls',()=>{
  it.each(['start','stop','close','strategy'] as const)('opens %s immediately during a stalled native refresh',async kind=>{
    const s=setup(),n=nativeStub();n.stub.writesEnabled=true;
    const c=new DashboardController(s.service,n.workflow,()=> 'instant-trading-review');await c.refresh();
    let finish!:(value:NativeState)=>void;
    n.stub.refresh.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const polling=c.poll();
    const action=kind==='close'?{kind,positionId:'long-1',fraction:'1' as const}:kind==='strategy'?{kind,strategyStyle:'scalp' as const}:{kind};
    const reviewing=c.reviewLedger(action);
    expect(c.snapshot().review?.kind).toBe('preparing');
    await reviewing;expect(c.snapshot().review?.kind).toBe('ledger');
    await c.confirm();expect(c.snapshot().busy).toBe(false);
    expect(s.calls.filter(r=>r.body)).toHaveLength(1);expect(n.stub.refresh).toHaveBeenCalledTimes(2);
    finish(nativeState());await polling;c.stop();
  });
  it.each(['long','short'] as const)('keeps a %s position visible until a delayed close fills and blocks duplicate closes',async side=>{
    vi.useFakeTimers();
    const value=account();value.paper.positions[0].side=side;
    const writes:ApiRequest[]=[];
    const service=new AccountService(async request=>{
      if(request.body){writes.push(request);value.paper.orders=[{id:'closing-order',status:'pending',payload:{positionId:'long-1',reduceOnly:true}}];return {id:'closing-order',status:'pending'};}
      return structuredClone(value);
    });service.setSession(context);
    const c=new DashboardController(service,null,()=> 'delayed-close-key');
    try{
      await c.reviewLedger({kind:'close',positionId:'long-1',fraction:'1'});
      await Promise.all([c.confirm(),c.confirm()]);
      expect(writes).toHaveLength(1);expect(c.snapshot().account?.paper.positions).toHaveLength(1);
      expect(c.snapshot().closing['long-1']).toBeTruthy();
      await c.reviewLedger({kind:'close',positionId:'long-1',fraction:'1'});await c.confirm();
      expect(writes).toHaveLength(1);c.cancelReview();
      value.paper.positions=[];value.paper.orders[0].status='filled';
      await vi.advanceTimersByTimeAsync(500);
      expect(c.snapshot().account?.paper.positions).toHaveLength(0);expect(c.snapshot().closing).toEqual({});
      expect(c.snapshot().notice).toBe('Trade closed.');
    }finally{c.stop();vi.useRealTimers();}
  });
  it('reports a rejected close without hiding the position or resending',async()=>{
    vi.useFakeTimers();const value=account();let sends=0;
    const service=new AccountService(async r=>{if(r.body){sends++;return {id:'failed-close'};}return structuredClone(value);});service.setSession(context);
    const c=new DashboardController(service,null,()=> 'rejected-close-key');
    try{
      await c.reviewLedger({kind:'close',positionId:'long-1',fraction:'1'});await c.confirm();
      value.paper.orders=[{id:'failed-close',status:'rejected',error:'Market unavailable'}];
      await vi.advanceTimersByTimeAsync(500);
      expect(c.snapshot().account?.paper.positions).toHaveLength(1);expect(c.snapshot().closing).toEqual({});
      expect(c.snapshot().error).toBe('Market unavailable');expect(sends).toBe(1);
    }finally{c.stop();vi.useRealTimers();}
  });
  it('keeps fast reviews bound to the wallet and loan identity',async()=>{
    const s=setup();await s.controller.reviewLedger({kind:'start'});
    const changed=account();changed.health!.loan!.id='changed';s.set(changed);await s.controller.confirm();
    expect(s.controller.snapshot().error).toContain('Loan or vault changed');
    expect(s.calls.filter(r=>r.body)).toHaveLength(0);
  });
});


it('keeps the vault-trading window open while its fresh review is pending',async()=>{
  const s=setup(),n=nativeStub();n.stub.writesEnabled=true;
  const c=new DashboardController(s.service,n.workflow);await c.refresh();
  let finish!:(value:NativeReview)=>void;const original=await n.stub.review();
  n.stub.review.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const waiting=c.reviewNative({kind:'startTrading'});
  expect(c.snapshot().review?.kind).toBe('preparingNative');
  finish(original);await waiting;expect(c.snapshot().review?.kind).toBe('native');c.stop();
});

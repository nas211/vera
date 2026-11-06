import {expect,it,vi} from 'vitest';
import {WalletBalanceStore} from '../vela/walletBalance';
import {NATIVE_CHAIN} from '../vela/nativeVault';
const owner='0x'+'11'.repeat(20);
function setup(){
  let balance:unknown='0x8ac7230489e80000',chain='0x'+NATIVE_CHAIN.toString(16),account=owner;
  const request=vi.fn(async({method}:{method:string})=>method==='eth_chainId'?chain:method==='eth_accounts'?[account]:balance);
  return {store:new WalletBalanceStore({request},owner),request,balance:(v:unknown)=>{balance=v;},chain:(v:string)=>{chain=v;},account:(v:string)=>{account=v;}};
}
it('reads exact current wallet DOGE independently of vault data and refreshes changes',async()=>{
  const e=setup();await e.store.refresh();expect(e.store.snapshot().balanceWei).toBe('10000000000000000000');
  expect(e.request).toHaveBeenCalledWith({method:'eth_getBalance',params:[owner,'latest']});
  e.balance('0x0');await e.store.refresh();expect(e.store.snapshot().balanceWei).toBe('0');
});
it.each(['chain','account','balance'] as const)('clears stale balance on invalid %s without showing fake zero',async(field)=>{
  const e=setup();await e.store.refresh();e[field]('0x1');if(field==='balance')e.balance('unknown');
  await e.store.refresh();expect(e.store.snapshot().balanceWei).toBeNull();expect(e.store.snapshot().error).toBeTruthy();
});
it('discards a late response after disconnect',async()=>{
  const e=setup();let finish!:(v:unknown)=>void;
  const normal=e.request.getMockImplementation()!;
  e.request.mockImplementation(r=>r.method==='eth_getBalance'?new Promise(resolve=>{finish=resolve;}):normal(r));
  const p=e.store.refresh();await Promise.resolve();e.store.stop();finish('0x1');await p;
  expect(e.store.snapshot().balanceWei).toBeNull();
});
it('times out and coalesces a stuck wallet request',async()=>{
  vi.useFakeTimers();try{
    const e=setup();e.request.mockImplementation(()=>new Promise(()=>{}));const p=e.store.refresh();expect(e.store.refresh()).toBe(p);
    await vi.advanceTimersByTimeAsync(8001);await p;expect(e.store.snapshot().error).toContain('timed out');expect(e.store.snapshot().balanceWei).toBeNull();
  }finally{vi.useRealTimers();}
});

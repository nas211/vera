import { describe, expect, it, vi } from 'vitest';
import { decodeFunctionData, encodeAbiParameters, keccak256, toHex, type Abi, type Hex } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { AccountService, type AccountSnapshot } from '../vela/accountService';
import { NativeVaultWorkflow, NATIVE_CHAIN, type NativeState } from '../vela/nativeVault';
import abi from '../vela/RewardedCollateralVault.abi.json';
import poolAbi from '../vela/NativeRewardPool.abi.json';
const owner=`0x${'11'.repeat(20)}` as Hex, target=`0x${'22'.repeat(20)}` as Hex, factory=`0x${'33'.repeat(20)}` as Hex;
const pool=`0x${'66'.repeat(20)}` as Hex;
const block=`0x${'44'.repeat(32)}` as Hex, txhash=`0x${'55'.repeat(32)}` as Hex;
const code='0x60006000' as Hex, codeHash=keccak256(code), now=1800000000;
const signer=privateKeyToAccount(`0x${'03'.repeat(32)}`);
const context={address:owner,network:'dogeos' as const,generation:1};
function fixture(): NativeState {
  return {source:'chain',chainId:NATIVE_CHAIN,owner,factory,factoryKind:'rewarded',bytecodeVerified:true,factoryCodeHash:codeHash,vaultCodeHash:codeHash,block:98,blockHash:block,observedAt:now,confirmations:3,walletBalanceWei:'100000000000000000000',transaction:null,
    rewardPool:{source:'chain',address:pool,factory,manager:owner,codeHash,bytecodeVerified:true,aprBps:'500',reserveWei:'1000000000000000000',availableReserveWei:'980000000000000000',reservedWei:'20000000000000000',fundedWei:'1000000000000000000',claimedWei:'0',totalPrincipalWei:'10000000000000000000'},
    vault:{address:target,owner,kind:'rewarded',collateral:'10000000000000000000',withdrawable:'10000000000000000000',loanState:0,loanId:toHex(0n,{size:32}),loanNonce:0,principalMicro:'0',paused:false,trading:false,settlementSigner:signer.address,stopBudgetMicro:'2000000',limits:{maxLoss:'2000000'},
      rewards:{source:'chain',pool,aprBps:'500',reserveWei:'1000000000000000000',claimableWei:'20000000000000000',availableReserveWei:'980000000000000000',claimedWei:'0',fundedWei:'1000000000000000000',principalWei:'10000000000000000000',loanLocked:false}}};
}
function setup(enabled=true) {
  const state=fixture();let capacity='1000',quoteAt=now,prior='2000000',fee='0x1',chain=toHex(NATIVE_CHAIN),wallet=owner,runtime=code,failSync=false;
  let authorizationMaxLossMicro:string|undefined;
  const storageMap=new Map<string,string>();
  const storage={getItem:(k:string)=>storageMap.get(k)??null,setItem:(k:string,v:string)=>{storageMap.set(k,v);},removeItem:(k:string)=>{storageMap.delete(k);}};
  let customSend:(()=>Promise<unknown>)|undefined;
  let certificate:unknown;
  const api=vi.fn(async (r:{path:string;body?:unknown})=>{
    if(r.path==='/api/account')return {address:owner,network:'dogeos',vault:state.vault,health:{quote:{at:quoteAt,source:'ILLUSTRATIVE test'},borrowCapacity:capacity,debt:'0',loan:null,healthFactor:null},paper:{mode:'PAPER',balance:'0',equity:'0',unrealized:'0',positions:[],orders:[],today:{realized:'0',trades:0,wins:0,losses:0},totals:{realized:'0',trades:0,wins:0,losses:0},preferences:{}},repayment:{simulated:true,freeEquity:'0',maxRepayable:'0',debt:'0',restriction:null,policy:null},tradingPermission:{maxLoss:prior},authorizationMaxLossMicro,events:[]} as AccountSnapshot;
    if(r.path.startsWith('/api/vault/state'))return structuredClone(state);
    if(r.path==='/api/certificate')return certificate;
    if(r.path==='/api/vault/migration/authorize')return {authorized:true};
    if(r.path==='/api/vault/sync'){if(failSync)throw Error('Ledger sync unavailable');return structuredClone((r.body as {includeState?:boolean})?.includeState?state:state.vault);}
    throw Error(r.path);
  });
  const service=new AccountService(api);service.setSession(context);
  const rpc=vi.fn(async ({method}:{method:string;params?:unknown[]})=>{
    switch(method){
      case 'eth_chainId':return chain;case 'eth_accounts':return [wallet];case 'eth_getBlockByNumber':return {hash:block};case 'eth_getCode':return runtime;
      case 'eth_estimateGas':return '0x5208';case 'eth_gasPrice':return fee;case 'eth_getTransactionCount':return '0x0';case 'eth_call':return '0x64';
      case 'eth_getBalance':return toHex(100n*10n**18n);case 'eth_sendTransaction':return customSend?customSend():txhash;default:throw Error(method);
    }
  });
  // Same lock name serializes confirmations as navigator.locks does in the browser.
  let queue:Promise<unknown>=Promise.resolve();
  const withLock=async <T,>(_:string,fn:()=>Promise<T>)=>{const run=queue.then(fn);queue=run.catch(()=>{});return run;};
  const make=()=>new NativeVaultWorkflow({service,provider:{request:rpc},storage,writesEnabled:enabled,withLock,now:()=>now});
  const workflow=make();
  const confirm=(status:'CONFIRMED'|'REVERTED'='CONFIRMED')=>{
    const p=workflow.pending()!;
    state.transaction={hash:txhash,status,sender:owner,to:p.tx.to,data:p.tx.data,valueWei:BigInt(p.tx.value).toString(),block:98,blockHash:block,rewardPaidWei:status==='CONFIRMED'&&p.action.kind==='claim'?'20000000000000000':'0'};
  };
  const cert=async(action:number,a='0',b='0',c='0',deadline=now+600)=>{
    const v=state.vault!;
    const digest=keccak256(encodeAbiParameters([{type:'bytes32'},{type:'uint256'},{type:'address'},{type:'address'},{type:'uint256'},{type:'uint8'},{type:'bytes32'},{type:'uint256'},{type:'uint256'},{type:'uint256'},{type:'uint64'}],[keccak256(toHex('ADP_DEMO_V1')),BigInt(NATIVE_CHAIN),v.address,owner,BigInt(v.loanNonce),action,v.loanId,BigInt(a),BigInt(b),BigInt(c),BigInt(deadline)]));
    certificate={action,a,b,c,deadline,signature:await signer.signMessage({message:{raw:digest}})};
  };
  return {state,workflow,service,rpc,api,storage,make,confirm,cert,capacity:(v:string)=>{capacity=v;},quoteAt:(v:number)=>{quoteAt=v;},send:(fn:()=>Promise<unknown>)=>{customSend=fn;},fee:(v:string)=>{fee=v;},prior:(v:string)=>{prior=v;},authorization:(v:string)=>{authorizationMaxLossMicro=v;},network:(v:Hex)=>{chain=v;},wallet:(v:Hex)=>{wallet=v;},code:(v:Hex)=>{runtime=v;},syncFail:()=>{failSync=true;},syncRecover:()=>{failSync=false;}};
}

describe('owner reviewed native workflow',()=>{
  it('uses one canonical server observation for the synchronized state',async()=>{
    const e=setup();const state=await e.workflow.refresh();
    expect(state.vault?.address).toBe(target);
    expect(e.api.mock.calls.map(([r])=>r)).toEqual([expect.objectContaining({path:'/api/vault/sync',body:{includeState:true}})]);
  });
  it('keeps deposit preflight independent of an unrelated loan-ledger sync',async()=>{
    const e=setup();e.syncFail();
    await e.workflow.review({kind:'deposit',amount:'1'});
    expect(e.api.mock.calls.map(([r])=>r.path)).toEqual(['/api/vault/state']);
    expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
  });
  it('reads a confirmed vault without calling the disabled ledger synchronization route',async()=>{
    const e=setup(false);
    const state=await e.workflow.refresh();
    expect(state.vault?.address).toBe(target);
    expect(e.api.mock.calls.map(([request])=>request.path)).toEqual(['/api/vault/state']);
    expect(e.rpc).not.toHaveBeenCalled();
  });
  it('disabled builds send no wallet or backend requests',async()=>{
    const e=setup(false);await expect(e.workflow.review({kind:'deposit',amount:'1'})).rejects.toThrow('OFF');
    expect(e.api).not.toHaveBeenCalled();expect(e.rpc).not.toHaveBeenCalled();
  });
  it('times out stalled wallet reads without creating a pending send',async()=>{
    vi.useFakeTimers();
    try {
      const e=setup();e.rpc.mockImplementationOnce(()=>new Promise(()=>{}));
      const run=e.workflow.review({kind:'deposit',amount:'1'}),failed=expect(run).rejects.toThrow('timed out');
      await vi.advanceTimersByTimeAsync(15_001);await failed;expect(e.workflow.pending()).toBeNull();
    } finally {vi.useRealTimers();}
  });
  it('encodes exact amounts and reserves execution plus data fees',async()=>{
    const e=setup();const q=await e.workflow.review({kind:'withdraw',amount:'0.000000000000000001'});
    expect(decodeFunctionData({abi:abi as Abi,data:q.tx.data}).args).toEqual([1n]);expect(q.feeReserveWei).toBe('52700');
    await expect(e.workflow.review({kind:'withdraw',amount:'10.000000000000000001'})).rejects.toThrow('locked');
    await expect(e.workflow.review({kind:'deposit',amount:'100'})).rejects.toThrow('fees');
  });
  it.each(['wallet','network','code','stale','backing','lock'])('rejects %s mismatch before signing',async kind=>{
    const e=setup();
    if(kind==='wallet')e.wallet(target);if(kind==='network')e.network('0x1');if(kind==='code')e.code('0x1234');
    if(kind==='stale')e.state.observedAt-=121;if(kind==='backing')e.state.vault!.rewards!.claimableWei='2000000000000000000';if(kind==='lock')e.state.vault!.loanState=1;
    await expect(e.workflow.review({kind:'deposit',amount:'1'})).rejects.toThrow();expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
  });
  it('preserves stricter chain and backend stop budgets for every owner',async()=>{
    const e=setup();e.state.vault!.loanState=2;e.state.vault!.withdrawable='0';e.state.vault!.stopBudgetMicro='750000';e.prior('500000');
    const q=await e.workflow.review({kind:'authorize',allocation:'100',positionSize:'50',maxLoss:'999',expiresAt:now+3600});
    const args=decodeFunctionData({abi:abi as Abi,data:q.tx.data}).args as [Hex,{maxLoss:number}];expect(args[1].maxLoss).toBe(500000);
    e.prior('0');await expect(e.workflow.review({kind:'authorize',allocation:'100',positionSize:'50',maxLoss:'2',expiresAt:now+3600})).rejects.toThrow();
  });
  it('can review an unused legacy permission while preserving the separate review ceiling',async()=>{
    const e=setup();e.state.factoryKind='legacy';e.state.vault!.kind='legacy';e.state.vault!.rewards=null;
    delete e.state.vault!.stopBudgetMicro;e.state.vault!.limits.maxLoss='0';
    e.state.vault!.loanState=2;e.state.vault!.withdrawable='0';e.prior('0');e.authorization('500000');
    const action={kind:'authorize' as const,allocation:'100',positionSize:'50',maxLoss:'2',expiresAt:now+3600};
    const q=await e.workflow.review(action);
    const args=decodeFunctionData({abi:abi as Abi,data:q.tx.data}).args as [Hex,{maxLoss:number}];
    expect(args[1].maxLoss).toBe(500000);
    expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
    e.authorization('0');await expect(e.workflow.review(action)).rejects.toThrow();
    e.authorization('2000001');await expect(e.workflow.review(action)).rejects.toThrow('authorization stop limit');
  });
  it('funds the shared project pool before a personal vault exists, without creating demo credit',async()=>{
    const e=setup();e.state.vault=null;
    const q=await e.workflow.review({kind:'fundReserve',amount:'1.000000000000000001'});
    expect(q.tx.to).toBe(pool);expect(q.tx.value).toBe(toHex(1000000000000000001n));
    expect(decodeFunctionData({abi:poolAbi as Abi,data:q.tx.data}).functionName).toBe('topUpReserve');
    await e.workflow.submit(q,true);e.confirm();const r=await e.workflow.reconcile();
    expect(r.pending).toBe(false);expect(r.receipt?.rewardPaidWei).toBe('0');
    expect(e.api.mock.calls.some(([r])=>r.path==='/api/vault/sync'||r.path==='/api/borrow')).toBe(false);
  });
  it('only the project manager can review a global rate change, including a zero-rate pause',async()=>{
    const e=setup();const q=await e.workflow.review({kind:'setRewardRate',aprBps:'0'});
    expect(q.tx.to).toBe(pool);expect(decodeFunctionData({abi:poolAbi as Abi,data:q.tx.data}).args).toEqual([0n]);
    await expect(e.workflow.review({kind:'setRewardRate',aprBps:'10001'})).rejects.toThrow('manager');
    e.state.rewardPool!.manager=target;
    await expect(e.workflow.review({kind:'setRewardRate',aprBps:'500'})).rejects.toThrow('manager');
    expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
  });
  it('reviews and confirms pool funding without an unrelated legacy-vault ledger sync',async()=>{
    const e=setup();
    e.state.factoryKind='legacy'; e.state.vault!.kind='legacy'; e.state.vault!.rewards=null;
    e.syncFail();
    const q=await e.workflow.review({kind:'fundReserve',amount:'1'});
    expect(q.tx.to).toBe(pool); expect(q.tx.value).toBe(toHex(10n**18n));
    expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
    await e.workflow.submit(q,true);
    expect(e.api.mock.calls.every(([r])=>r.path==='/api/vault/state')).toBe(true);
    expect(e.rpc.mock.calls.filter(([r])=>r.method==='eth_sendTransaction')).toHaveLength(1);
  });
  it.each(['missing','backing','vault_link','pool_code'])('rejects shared pool %s mismatches',async problem=>{
    const e=setup();
    if(problem==='missing')e.state.rewardPool=null;
    if(problem==='backing')e.state.rewardPool!.reservedWei='0';
    if(problem==='vault_link')e.state.vault!.rewards!.pool=target;
    if(problem==='pool_code')e.state.rewardPool!.codeHash=block;
    await expect(e.workflow.review({kind:'fundReserve',amount:'1'})).rejects.toThrow();
    expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
  });
  it('binds review and serializes simultaneous sends; repeated action is blocked',async()=>{
    const e=setup(),q=await e.workflow.review({kind:'deposit',amount:'1'});
    await expect(e.workflow.submit({...q},true)).rejects.toThrow('Confirm');await expect(e.workflow.submit(q,false)).rejects.toThrow('Confirm');
    const results=await Promise.allSettled([e.workflow.submit(q,true),e.workflow.submit(q,true)]);
    expect(results.filter(r=>r.status==='fulfilled')).toHaveLength(1);expect(e.rpc.mock.calls.filter(([r])=>r.method==='eth_sendTransaction')).toHaveLength(1);
    await expect(e.workflow.review({kind:'deposit',amount:'1'})).rejects.toThrow('pending');
  });
  it('requires review again when fees or collateral context change',async()=>{
    const e=setup(),q=await e.workflow.review({kind:'deposit',amount:'1'});e.fee('0x2');await expect(e.workflow.submit(q,true)).rejects.toThrow('changed');
    e.fee('0x1');e.state.vault!.loanNonce++;await expect(e.workflow.submit(q,true)).rejects.toThrow('changed');expect(e.workflow.pending()).toBeNull();
  });
  it('rejected wallet request clears pending but ambiguous send does not',async()=>{
    const e=setup(),q=await e.workflow.review({kind:'claim'});e.send(async()=>{throw Object.assign(Error('Rejected'),{code:4001});});
    await expect(e.workflow.submit(q,true)).rejects.toThrow('Rejected');expect(e.workflow.pending()).toBeNull();
    e.send(async()=>{throw Error('Disconnected after broadcast');});await expect(e.workflow.submit(q,true)).rejects.toThrow('Disconnected');
    expect(e.make().pending()!.hash).toBeNull();await e.workflow.attachHash(txhash);e.confirm();
    await e.workflow.reconcile();expect(e.workflow.receipts()[0].rewardPaidWei).toBe('20000000000000000');
  });
  it('stopping a wallet wait retains late hash and never permits a duplicate',async()=>{
    const e=setup();let resolve!:(v:unknown)=>void;e.send(()=>new Promise(r=>{resolve=r;}));
    const q=await e.workflow.review({kind:'deposit',amount:'1'}),abort=new AbortController();
    const run=e.workflow.submit(q,true,abort.signal),rejected=expect(run).rejects.toThrow('Stopped waiting');
    await vi.waitFor(()=>expect(resolve).toBeTypeOf('function'));abort.abort();await rejected;
    expect(e.make().pending()!.hash).toBeNull();resolve(txhash);await vi.waitFor(()=>expect(e.workflow.pending()!.hash).toBe(txhash));
    await expect(e.workflow.review({kind:'deposit',amount:'1'})).rejects.toThrow('pending');
  });
  it('starts the wallet timeout after slow confirmation checks, not before the prompt',async()=>{
    vi.useFakeTimers();
    try {
      const e=setup(),q=await e.workflow.review({kind:'deposit',amount:'1'});
      let finishRead!:(value:NativeState)=>void,finished=false;
      vi.spyOn(e.service,'nativeState').mockImplementationOnce(()=>new Promise(resolve=>{finishRead=resolve;}));
      const submitted=e.workflow.submit(q,true);
      void submitted.then(()=>{finished=true;},()=>{finished=true;});
      await vi.advanceTimersByTimeAsync(60_000);
      expect(finished).toBe(false);expect(e.workflow.pending()).toBeNull();
      expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
      finishRead(structuredClone(e.state));await vi.advanceTimersByTimeAsync(0);
      expect((await submitted).hash).toBe(txhash);
    } finally {vi.useRealTimers();}
  });
  it('cancels during preflight without opening a later wallet prompt',async()=>{
    const e=setup(),q=await e.workflow.review({kind:'deposit',amount:'1'}),abort=new AbortController();
    let finishRead!:(value:NativeState)=>void;
    vi.spyOn(e.service,'nativeState').mockImplementationOnce(()=>new Promise(resolve=>{finishRead=resolve;}));
    const submitted=e.workflow.submit(q,true,abort.signal),rejected=expect(submitted).rejects.toThrow('Stopped waiting');
    await vi.waitFor(()=>expect(finishRead).toBeTypeOf('function'));abort.abort();await rejected;
    finishRead(structuredClone(e.state));
    // Acquiring the same lock waits for the cancelled background preflight to finish.
    const next=await e.workflow.review({kind:'deposit',amount:'1'});await e.workflow.submit(next,true);
    expect(e.rpc.mock.calls.filter(([r])=>r.method==='eth_sendTransaction')).toHaveLength(1);
  });
  it('bounds the actual wallet prompt and preserves a late transaction hash',async()=>{
    vi.useFakeTimers();
    try {
      const e=setup(),q=await e.workflow.review({kind:'deposit',amount:'1'});
      let finishSend!:(value:unknown)=>void;e.send(()=>new Promise(resolve=>{finishSend=resolve;}));
      const submitted=e.workflow.submit(q,true),rejected=expect(submitted).rejects.toThrow('Stopped waiting');
      await vi.advanceTimersByTimeAsync(0);expect(e.workflow.pending()?.phase).toBe('AWAITING_WALLET');
      await vi.advanceTimersByTimeAsync(45_000);await rejected;
      expect(e.workflow.pending()?.hash).toBeNull();
      finishSend(txhash);await vi.advanceTimersByTimeAsync(0);expect(e.workflow.pending()?.hash).toBe(txhash);
    } finally {vi.useRealTimers();}
  });
  it('late wallet hash remains bound to the original owner after logout',async()=>{
    const e=setup();let resolve!:(v:unknown)=>void;e.send(()=>new Promise(r=>{resolve=r;}));const q=await e.workflow.review({kind:'claim'});
    const run=e.workflow.submit(q,true),rejected=expect(run).rejects.toThrow('context');await vi.waitFor(()=>expect(resolve).toBeTypeOf('function'));
    e.service.setSession(null);resolve(txhash);await rejected;expect(e.workflow.pending()!.owner).toBe(owner);expect(e.workflow.pending()!.hash).toBe(txhash);
  });
  it('pending confirmations and sync failures cannot credit or clear a claim',async()=>{
    const e=setup(),q=await e.workflow.review({kind:'claim'});await e.workflow.submit(q,true);
    e.state.transaction={hash:txhash,status:'PENDING'};expect((await e.workflow.reconcile()).pending).toBe(true);expect(e.workflow.receipts()).toEqual([]);
    e.confirm();e.syncFail();await expect(e.workflow.reconcile()).rejects.toThrow('sync');expect(e.make().pending()!.hash).toBe(txhash);
    e.syncRecover();await e.make().reconcile();expect(e.workflow.pending()).toBeNull();expect(e.workflow.receipts()[0].kind).toBe('native-reward-claim');
  });
  it('reverted transaction records zero paid and allows a new reviewed attempt',async()=>{
    const e=setup(),q=await e.workflow.review({kind:'claim'});await e.workflow.submit(q,true);e.confirm('REVERTED');await e.workflow.reconcile();
    expect(e.workflow.receipts()[0]).toMatchObject({status:'REVERTED',rewardPaidWei:'0'});expect(e.api.mock.calls.filter(([r])=>r.path==='/api/vault/sync' && (r.body as {tx?:string}).tx)).toHaveLength(0);
    await expect(e.workflow.review({kind:'claim'})).resolves.toBeDefined();
  });
  it.each(['sender','data','amount','event','future'])('rejects forged receipt %s',async kind=>{
    const e=setup();await e.workflow.submit(await e.workflow.review({kind:'claim'}),true);e.confirm();const t=e.state.transaction!;
    if(kind==='sender')t.sender=target;if(kind==='data')t.data='0x';if(kind==='amount')t.valueWei='1';if(kind==='event')t.rewardPaidWei='0';if(kind==='future')t.block=99;
    await expect(e.workflow.reconcile()).rejects.toThrow();expect(e.workflow.pending()).not.toBeNull();expect(e.workflow.receipts()).toEqual([]);
  });
  it('binds certificates to action, loan, nonce and exact settlement; renewed deadline remains usable',async()=>{
    const e=setup(),v=e.state.vault!;v.loanState=2;v.withdrawable='0';v.loanNonce=1;v.loanId=toHex(1n,{size:32});v.principalMicro='1000000';
    await e.cert(2,'1000000');const q=await e.workflow.review({kind:'settle'});expect(e.api.mock.calls.findIndex(([r])=>r.path==='/api/vault/sync')).toBeLessThan(e.api.mock.calls.findIndex(([r])=>r.path==='/api/certificate'));await e.cert(2,'1000000','0','0',now+601);
    await expect(e.workflow.submit(q,true)).resolves.toBeDefined();
    const f=setup();Object.assign(f.state.vault!,v);await f.cert(2,'1000000');f.state.vault!.loanNonce++;
    await expect(f.workflow.review({kind:'settle'})).rejects.toThrow('signer');
    await f.cert(1,'1000000');await expect(f.workflow.review({kind:'settle'})).rejects.toThrow('certificate');
    await f.cert(2,'1000000','0','0',now-1);await expect(f.workflow.review({kind:'settle'})).rejects.toThrow('expired');
  });
  it('changed settlement amounts require a fresh review',async()=>{
    const e=setup();e.state.vault!.loanState=2;e.state.vault!.withdrawable='0';await e.cert(2,'1000000');const q=await e.workflow.review({kind:'settle'});
    await e.cert(2,'1000001');await expect(e.workflow.submit(q,true)).rejects.toThrow('amounts changed');
  });
  it('rounds high-precision capacity down and rejects stale illustrative prices',async()=>{
    const e=setup();e.capacity('100.000001999999999999');
    await expect(e.workflow.review({kind:'requestBorrow',amount:'100.000001',loanId:toHex(1n,{size:32})})).resolves.toBeDefined();
    await expect(e.workflow.review({kind:'requestBorrow',amount:'100.000002',loanId:toHex(1n,{size:32})})).rejects.toThrow('capacity');
    e.quoteAt(now-301);await expect(e.workflow.review({kind:'requestBorrow',amount:'1',loanId:toHex(1n,{size:32})})).rejects.toThrow('quote');
  });
  it('requires available borrow capacity and blocks legacy reward claims',async()=>{
    const e=setup();await expect(e.workflow.review({kind:'requestBorrow',amount:'1000.000001',loanId:toHex(1n,{size:32})})).rejects.toThrow('capacity');
    e.state.vault!.kind='legacy';e.state.factoryKind='legacy';e.state.vault!.rewards=null;
    await expect(e.workflow.review({kind:'claim'})).rejects.toThrow('legacy');
  });
});


describe('same wallet reward vault migration',()=>{
  function legacy() {
    const e=setup(),rewarded=structuredClone(e.state);
    const oldFactory=`0x${'77'.repeat(20)}` as Hex,oldVault=`0x${'88'.repeat(20)}` as Hex;
    e.state.factory=oldFactory;e.state.factoryKind='legacy';
    Object.assign(e.state.vault!,{address:oldVault,kind:'legacy',collateral:'0',withdrawable:'0',loanState:3,rewards:null});
    e.state.upgrade={sourceVault:oldVault,targetFactory:factory,targetVault:target,factoryCodeHash:codeHash,eligible:true,reason:null};
    return {...e,rewarded};
  }
  it('reviews without consent writes, authorizes once before sending, and reconciles into rewards',async()=>{
    const e=legacy(),q=await e.workflow.review({kind:'migrate'});
    expect(q.tx.to).toBe(factory);expect(q.tx.value).toBe('0x0');
    expect(q.migration?.targetVault).toBe(target);
    expect(e.api.mock.calls.some(([r])=>r.path.includes('/migration/'))).toBe(false);
    await e.workflow.submit(q,true);
    const authorize=e.api.mock.calls.find(([r])=>r.path.includes('/migration/'))![0];
    expect(authorize.body).toEqual({confirm:true,sourceVault:q.vault,targetFactory:factory,targetVault:target});
    expect(e.rpc.mock.calls.filter(([r])=>r.method==='eth_sendTransaction')).toHaveLength(1);
    Object.assign(e.state,e.rewarded,{upgrade:null});e.confirm();
    const result=await e.make().reconcile();expect(result.pending).toBe(false);expect(result.state.vault?.rewards).not.toBeNull();
    expect(e.workflow.pending()).toBeNull();expect(e.workflow.receipts()[0].action).toBe('migrate');
    await expect(e.workflow.review({kind:'migrate'})).rejects.toThrow();
  });
  it('wallet rejection retains server intent but permits another fresh review',async()=>{
    const e=legacy(),q=await e.workflow.review({kind:'migrate'});
    e.send(async()=>{throw Object.assign(Error('rejected'),{code:4001});});
    await expect(e.workflow.submit(q,true)).rejects.toThrow('rejected');
    expect(e.workflow.pending()).toBeNull();await e.workflow.review({kind:'migrate'});
  });
  it('does not send if migration authorization fails',async()=>{
    const e=legacy(),q=await e.workflow.review({kind:'migrate'});
    vi.spyOn(e.service,'authorizeMigration').mockRejectedValue(Error('Settle every loan'));
    await expect(e.workflow.submit(q,true)).rejects.toThrow('Settle every loan');
    expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
  });
  it.each(['balance','loan','target','code','changed'])('rejects unsafe migration: %s',async problem=>{
    const e=legacy();
    if(problem==='balance')e.state.vault!.collateral='1';
    if(problem==='loan')e.state.vault!.loanState=2;
    if(problem==='target')e.state.upgrade!.targetFactory=target;
    if(problem==='code')e.state.upgrade!.factoryCodeHash=block;
    if(problem==='changed'){
      const q=await e.workflow.review({kind:'migrate'});e.state.upgrade!.targetVault=pool;
      await expect(e.workflow.submit(q,true)).rejects.toThrow('changed');
    } else await expect(e.workflow.review({kind:'migrate'})).rejects.toThrow();
    expect(e.rpc.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
  });
});

describe('cross-tab and late-wallet recovery',()=>{
  it.each(['resolve','reject'])('late wallet %s cannot resurrect or remove a newer pending action',async outcome=>{
    const e=setup();let resolve!:(hash:Hex)=>void,reject!:(error:unknown)=>void;
    e.send(()=>new Promise((ok,bad)=>{resolve=ok;reject=bad;}));
    const q=await e.workflow.review({kind:'deposit',amount:'1'});
    const sent=e.workflow.submit(q,true);const settled=sent.catch(()=>null);
    await vi.waitFor(()=>expect(e.workflow.pending()?.phase).toBe('AWAITING_WALLET'));
    await e.workflow.attachHash(txhash);e.confirm();
    await e.make().reconcile();expect(e.workflow.pending()).toBeNull();
    const hash2=`0x${'ab'.repeat(32)}` as Hex;e.send(async()=>hash2);
    const q2=await e.workflow.review({kind:'deposit',amount:'2'});await e.workflow.submit(q2,true);
    const second=e.workflow.pending();
    if(outcome==='resolve')resolve(txhash);else reject(Object.assign(Error('late rejection'),{code:4001}));
    await settled;expect(e.workflow.pending()).toEqual(second);expect(e.workflow.pending()?.hash).toBe(hash2);
  });
  it('a delayed second reconciliation cannot erase the next transaction',async()=>{
    const e=setup(),q=await e.workflow.review({kind:'deposit',amount:'1'});
    await e.workflow.submit(q,true);e.confirm();
    const original=e.service.syncNative.bind(e.service);let release!:()=>void,entered=false;
    vi.spyOn(e.service,'syncNative').mockImplementationOnce(async()=>{entered=true;await new Promise<void>(r=>{release=r;});return e.state.vault;});
    const slow=e.make().reconcile();await vi.waitFor(()=>expect(entered).toBe(true));
    await e.workflow.reconcile();expect(e.workflow.pending()).toBeNull();
    vi.mocked(e.service.syncNative).mockImplementation(original);
    const hash2=`0x${'cd'.repeat(32)}` as Hex;e.send(async()=>hash2);
    const q2=await e.workflow.review({kind:'deposit',amount:'2'});await e.workflow.submit(q2,true);
    release();const result=await slow;
    expect(result.pending).toBe(true);expect(e.workflow.pending()?.hash).toBe(hash2);
    expect(e.workflow.receipts()).toHaveLength(1);
  });
});

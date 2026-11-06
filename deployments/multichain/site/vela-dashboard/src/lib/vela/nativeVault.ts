/** Owner-reviewed native testnet workflow. No chain write is enabled by this module itself. */
import { encodeFunctionData, encodeAbiParameters, keccak256, recoverMessageAddress, serializeTransaction, toHex, type Abi, type Hex } from 'viem';
import vaultAbi from './RewardedCollateralVault.abi.json';
import factoryAbi from './RewardedVaultFactory.abi.json';
import poolAbi from './NativeRewardPool.abi.json';
import { parseDecimal } from '../units';
import { walletRead } from './walletRpc';
import type { AccountService, SessionContext } from './accountService';
export const NATIVE_CHAIN = 6281971;
export type NativeProvider = { request(request: { method: string; params?: unknown[] }): Promise<unknown> };
export type NativePool = { source: 'chain'; address: Hex; factory: Hex; manager: string; codeHash: Hex; bytecodeVerified: true;
  aprBps: string; reserveWei: string; availableReserveWei: string; reservedWei: string; fundedWei: string; claimedWei: string; totalPrincipalWei: string };
export type NativeState = {
  source: 'chain'; chainId: number; owner: string; factory: Hex; factoryKind: 'legacy' | 'rewarded'; bytecodeVerified: true;
  factoryCodeHash: Hex; vaultCodeHash: Hex; block: number; blockHash: Hex; observedAt: number; confirmations: number; walletBalanceWei: string;
  rewardPool: NativePool | null;
  upgrade?: { sourceVault: Hex; targetFactory: Hex; targetVault: Hex; factoryCodeHash: Hex; eligible: boolean; reason: string | null } | null;
  vault: { address: Hex; owner: string; kind: 'legacy' | 'rewarded'; collateral: string; withdrawable: string; loanState: number; loanId: Hex;
    loanNonce: number; principalMicro: string; paused: boolean; trading: boolean; settlementSigner: Hex;
    stopBudgetMicro?: string; limits: { maxLoss: string }; rewards: null | { source: 'chain'; pool: Hex; aprBps: string; reserveWei: string; claimableWei: string; availableReserveWei: string; claimedWei: string; fundedWei: string; principalWei: string; loanLocked: boolean } } | null;
  transaction: null | { hash: Hex; status: 'PENDING' | 'CONFIRMED' | 'REVERTED'; sender?: string; to?: string; data?: string; valueWei?: string; block?: number; blockHash?: string; rewardPaidWei?: string };
};
export type NativeAction =
  | { kind: 'create' | 'migrate' | 'claim' | 'acknowledge' | 'settle' | 'cancel' | 'startTrading' }
  | { kind: 'deposit' | 'withdraw'; amount: string }
  | { kind: 'fundReserve'; amount: string }
  | { kind: 'requestBorrow'; amount: string; loanId: Hex }
  | { kind: 'setRewardRate'; aprBps: string }
  | { kind: 'authorize'; allocation: string; positionSize: string; maxLoss: string; expiresAt: number };
type Certificate = { action: number; a: string; b: string; c: string; deadline: number; signature: Hex };
type Transaction = { from: Hex; to: Hex; data: Hex; value: Hex; gas?: Hex; gasPrice?: Hex };
export type NativeReview = Readonly<{ owner: string; generation: number; factory: string; vault: string | null; loanId: string | null; loanNonce: number | null; action: NativeAction; tx: Readonly<Transaction>; feeReserveWei: string; createdAt: number; certificate?: Certificate; migration?: NativeState['upgrade'] }>;
export type NativePending = { version: 1; attemptId?: string; owner: string; chainId: number; action: NativeAction; tx: Transaction; hash: Hex | null; createdAt: number; phase: 'AWAITING_WALLET' | 'SUBMITTED' };
export type NativeReceipt = { kind: 'native-reward-claim' | 'native-vault-action'; hash: Hex; status: 'CONFIRMED' | 'REVERTED'; action: NativeAction['kind']; rewardPaidWei: string; block: number };
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const exact = (s: string, decimals: number) => { const n = parseDecimal(s, decimals); if (!n.ok || n.value <= 0n) throw new Error('Enter a positive exact amount'); return n.value; };
const capacityMicro = (s: string | undefined) => {
  if (typeof s !== 'string' || s.length > 100 || !/^\d+(\.\d+)?$/.test(s)) throw new Error('Fresh borrowing capacity is unavailable');
  const [whole, fraction = ''] = s.split('.');
  // The ledger's collateral valuation can have more than six decimals. Capacity
  // rounds down; user-entered borrow amounts must still be exact micro-USD.
  return BigInt(whole) * 1_000_000n + BigInt(fraction.padEnd(6, '0').slice(0, 6));
};
const address = (s: unknown): s is Hex => typeof s === 'string' && /^0x[0-9a-f]{40}$/i.test(s);
const hash = (s: unknown): s is Hex => typeof s === 'string' && /^0x[0-9a-f]{64}$/i.test(s);
const uint = (s: unknown): s is string => typeof s === 'string' && /^\d+$/.test(s);
const key = (owner: string) => `vela:native-v2:${NATIVE_CHAIN}:${owner}`;
const receiptKey = (owner: string) => `${key(owner)}:receipts`;
const sameAttempt = (a:NativePending|null,b:NativePending) => !!a && (a.attemptId || b.attemptId
  ? !!a.attemptId && a.attemptId===b.attemptId
  : a.createdAt===b.createdAt && JSON.stringify(a.tx)===JSON.stringify(b.tx));

export function validateNativeState(raw: unknown, owner: string, now: number): NativeState {
  const s = raw as NativeState;
  if (!s || s.source !== 'chain' || s.chainId !== NATIVE_CHAIN || s.owner !== owner || !s.bytecodeVerified || !address(s.factory) || !hash(s.factoryCodeHash) || !hash(s.vaultCodeHash) || !hash(s.blockHash) || !Number.isSafeInteger(s.block) || s.block < 0 || !Number.isSafeInteger(s.confirmations) || s.confirmations < 3 || !Number.isSafeInteger(s.observedAt) || now - s.observedAt < -30 || now - s.observedAt > 120 || !uint(s.walletBalanceWei)) throw new Error('Confirmed native state unavailable');
  const v = s.vault;
  const p = s.rewardPool;
  if (p !== null && (!p || p.source !== 'chain' || !p.bytecodeVerified || !address(p.address) || !address(p.factory) || !address(p.manager) || !hash(p.codeHash)
      || ![p.aprBps,p.reserveWei,p.availableReserveWei,p.reservedWei,p.fundedWei,p.claimedWei,p.totalPrincipalWei].every(uint)
      || BigInt(p.aprBps)>10_000n || BigInt(p.availableReserveWei)+BigInt(p.reservedWei)!==BigInt(p.reserveWei)
      || BigInt(p.fundedWei)-BigInt(p.claimedWei)!==BigInt(p.reserveWei))) throw new Error('Invalid shared reward pool');
  if (s.factoryKind==='rewarded' && (!p || p.factory.toLowerCase()!==s.factory.toLowerCase())) throw new Error('Shared reward pool unavailable');
  if (v && (!address(v.address) || v.owner !== owner || v.kind !== s.factoryKind || !hash(v.loanId) || !address(v.settlementSigner) || !Number.isSafeInteger(v.loanNonce) || v.loanNonce < 0 || ![0,1,2,3,4].includes(v.loanState) || ![v.collateral,v.withdrawable,v.principalMicro,v.limits?.maxLoss].every(uint) || typeof v.paused !== 'boolean' || typeof v.trading !== 'boolean' || (v.stopBudgetMicro !== undefined && !uint(v.stopBudgetMicro)))) throw new Error('Invalid native vault');
  if (v && (BigInt(v.withdrawable) > BigInt(v.collateral) || ((v.paused || v.trading || ![0,3].includes(v.loanState)) && v.withdrawable !== '0'))) throw new Error('Invalid collateral lock');
  if (v?.kind === 'rewarded' && (!v.rewards || !uint(v.stopBudgetMicro) || BigInt(v.stopBudgetMicro) === 0n || BigInt(v.stopBudgetMicro) > 2_000_000n)) throw new Error('Missing reward vault protection');
  if (v?.rewards && (v.kind !== 'rewarded' || v.rewards.source !== 'chain' || ![v.rewards.aprBps,v.rewards.reserveWei,v.rewards.claimableWei,v.rewards.availableReserveWei,v.rewards.claimedWei,v.rewards.fundedWei].every(uint) || v.rewards.principalWei !== v.collateral || BigInt(v.rewards.claimableWei) > BigInt(v.rewards.reserveWei))) throw new Error('Invalid reward backing');
  if (v?.rewards && (!p || !address(v.rewards.pool) || v.rewards.pool.toLowerCase()!==p.address.toLowerCase() || v.rewards.reserveWei!==p.reserveWei || v.rewards.availableReserveWei!==p.availableReserveWei || v.rewards.aprBps!==p.aprBps || v.rewards.fundedWei!==p.fundedWei)) throw new Error('Vault and shared reward pool disagree');
  const u=s.upgrade;
  if(u && (!v || v.kind!=='legacy' || u.sourceVault!==v.address || !address(u.targetFactory) || !address(u.targetVault)
      || u.targetVault===v.address || !hash(u.factoryCodeHash) || u.targetFactory!==p?.factory || typeof u.eligible!=='boolean'
      || (u.eligible && (v.collateral!=='0' || v.withdrawable!=='0' || ![0,3].includes(v.loanState) || v.trading || v.paused)))) throw new Error('Invalid reward vault migration');
  return s;
}

async function verifyCertificate(s: NativeState, raw: unknown, expected: number, now: number): Promise<Certificate> {
  const v = s.vault!, c = raw as Certificate;
  if (!c || c.action !== expected || ![c.a,c.b,c.c].every(uint) || !Number.isSafeInteger(c.deadline) || c.deadline < now || c.deadline > now + 3600 || !/^0x[0-9a-f]{130}$/i.test(c.signature)) throw new Error('Invalid or expired ledger certificate');
  if (expected === 1 && (c.a !== v.principalMicro || c.b !== '0' || c.c !== '0')) throw new Error('Loan credit certificate mismatch');
  if (expected === 3 && [c.a,c.b,c.c].some(x => x !== '0')) throw new Error('Cancellation certificate mismatch');
  const digest = keccak256(encodeAbiParameters(
    [{type:'bytes32'},{type:'uint256'},{type:'address'},{type:'address'},{type:'uint256'},{type:'uint8'},{type:'bytes32'},{type:'uint256'},{type:'uint256'},{type:'uint256'},{type:'uint64'}],
    [keccak256(toHex('ADP_DEMO_V1')),BigInt(NATIVE_CHAIN),v.address,s.owner as Hex,BigInt(v.loanNonce),c.action,v.loanId,BigInt(c.a),BigInt(c.b),BigInt(c.c),BigInt(c.deadline)]));
  if ((await recoverMessageAddress({ message: { raw: digest }, signature: c.signature })).toLowerCase() !== v.settlementSigner.toLowerCase()) throw new Error('Ledger certificate signer mismatch');
  return c;
}

function transaction(s: NativeState, a: NativeAction, certificate?: Certificate): Transaction {
  const v = s.vault;
  if (a.kind==='fundReserve' || a.kind==='setRewardRate') {
    const p=s.rewardPool;if(!p)throw new Error('Verified project reward pool required');
    if(a.kind==='fundReserve')return {from:s.owner as Hex,to:p.address,data:encodeFunctionData({abi:poolAbi as Abi,functionName:'topUpReserve'}),value:toHex(exact(a.amount,18))};
    if(p.manager.toLowerCase()!==s.owner || !/^\d+$/.test(a.aprBps) || BigInt(a.aprBps)>10_000n)throw new Error('Only the reward manager can set a rate from 0 to 100%');
    return {from:s.owner as Hex,to:p.address,data:encodeFunctionData({abi:poolAbi as Abi,functionName:'setRewardRate',args:[BigInt(a.aprBps)]}),value:'0x0'};
  }
  if (a.kind === 'migrate') {
    const u=s.upgrade;
    if(!v || v.kind!=='legacy' || !u?.eligible || v.collateral!=='0' || ![0,3].includes(v.loanState)) throw new Error(u?.reason??'Withdraw all legacy collateral and confirm settlement first');
    return {from:s.owner as Hex,to:u.targetFactory,data:encodeFunctionData({abi:factoryAbi as Abi,functionName:'createVault'}),value:'0x0'};
  }
  if (a.kind === 'create') {
    if (v) throw new Error('A registered vault already exists');
    return { from: s.owner as Hex, to: s.factory, data: encodeFunctionData({abi: factoryAbi as Abi,functionName:'createVault'}), value:'0x0' };
  }
  if (!v) throw new Error('Create and confirm a vault first');
  let name: string, args: unknown[] = [], value = 0n;
  if (a.kind === 'deposit' || a.kind === 'withdraw') {
    if (v.paused) throw new Error('Vault is paused');
    const n = exact(a.amount,18); name=a.kind;
    if (a.kind === 'deposit') { if(v.loanState===4) throw new Error('Defaulted vault'); value=n; }
    else { if(n>BigInt(v.withdrawable)) throw new Error('Collateral remains locked or amount is too large'); args=[n]; }
  } else if (a.kind === 'claim') {
    if(v.kind!=='rewarded'||!v.rewards) throw new Error('This legacy vault has no rewards; no automatic migration');
    name='claimRewards';
    if(BigInt(v.rewards.claimableWei)===0n) throw new Error('No funded rewards to claim');
  } else if (a.kind === 'requestBorrow') {
    if(v.paused||v.trading||![0,3].includes(v.loanState)||BigInt(v.collateral)===0n||!hash(a.loanId)||BigInt(a.loanId)===0n) throw new Error('Vault cannot request a new loan');
    name='requestDemoBorrow'; args=[a.loanId,exact(a.amount,6)];
  } else if (a.kind === 'authorize') {
    if(v.paused||v.loanState!==2) throw new Error('Confirmed active loan required');
    const limit=[2_000_000n,exact(a.maxLoss,6),BigInt(v.stopBudgetMicro??v.limits.maxLoss)||2_000_000n].reduce((x,y)=>x<y?x:y);
    const allocation=exact(a.allocation,6), size=exact(a.positionSize,6);
    if(size>allocation||size>200_000_000n||limit<2n) throw new Error('Invalid allocation or stricter stop limit');
    name='authorizeAgent'; args=[v.settlementSigner,{allocation,positionSize:size,expiresAt:BigInt(a.expiresAt),maxLoss:limit,leverage:1,markets:7,autoReduce:false}];
  } else if (a.kind === 'startTrading') {
    if(v.paused||v.loanState!==2||v.trading) throw new Error('Review loan trading authorization first');
    name='startTrading';
  } else {
    if(!certificate) throw new Error('Verified ledger certificate required');
    if(a.kind==='acknowledge'&&v.loanState===1){name='acknowledgeDemoLoan';args=[BigInt(certificate.deadline),certificate.signature];}
    else if(a.kind==='cancel'&&v.loanState===1){name='cancelUnissuedLoan';args=[BigInt(certificate.deadline),certificate.signature];}
    else if(a.kind==='settle'&&v.loanState===2){name='settleDemoLoan';args=[BigInt(certificate.a),BigInt(certificate.b),BigInt(certificate.c),BigInt(certificate.deadline),certificate.signature];}
    else throw new Error('Loan state changed');
  }
  return {from:s.owner as Hex,to:v.address,data:encodeFunctionData({abi:vaultAbi as Abi,functionName:name,args}),value:toHex(value)};
}

export class NativeVaultWorkflow {
  private reviews = new WeakSet<NativeReview>();
  private observed:NativeState|null=null;
  private context: SessionContext;
  constructor(private readonly deps: { service: AccountService; provider: NativeProvider; storage: Store; writesEnabled: boolean; withLock: <T>(name:string,fn:()=>Promise<T>)=>Promise<T>; now?:()=>number }) { this.context=deps.service.sessionContext(); }
  get writesEnabled() { return this.deps.writesEnabled; }
  private now() { return this.deps.now?.() ?? Math.floor(Date.now()/1000); }
  private async rpc(request: Parameters<NativeProvider['request']>[0]) {
    let timer: ReturnType<typeof setTimeout>;
    try {
      return await Promise.race([
        walletRead(this.deps.provider,request),
        new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('Wallet RPC timed out; refresh to retry')),15_000);}),
      ]);
    } finally { clearTimeout(timer!); }
  }
  private current() {
    const c=this.deps.service.sessionContext();
    if(c.address!==this.context.address||c.generation!==this.context.generation||c.network!=='dogeos') throw new Error('Wallet context changed');
  }
  private async wallet() {
    this.current();
    const [chain,accounts]=await Promise.all([this.rpc({method:'eth_chainId'}),this.rpc({method:'eth_accounts'})]);
    this.current();
    if(BigInt(chain as string)!==BigInt(NATIVE_CHAIN)||!Array.isArray(accounts)||accounts[0]?.toLowerCase()!==this.context.address) throw new Error('Wallet or network changed');
  }
  async read(tx?:string) { this.current(); const s=validateNativeState(await this.deps.service.nativeState(tx),this.context.address,this.now());this.current();this.observed=s;return s; }
  async refresh() {
    if(!this.deps.writesEnabled)return this.read();
    this.current();
    // One canonical read supplies both the view and the ledger synchronization.
    const s=validateNativeState(await this.deps.service.syncNative(undefined,true),this.context.address,this.now());
    this.current();this.observed=s;return s;
  }
  pending(): NativePending | null {
    const raw=this.deps.storage.getItem(key(this.context.address));if(!raw)return null;
    const p=JSON.parse(raw) as NativePending;
    if(p.version!==1||p.owner!==this.context.address||p.chainId!==NATIVE_CHAIN||!address(p.tx?.to)||(p.hash!==null&&!hash(p.hash))) throw new Error('Invalid saved transaction; review wallet history');
    return p;
  }
  receipts(): NativeReceipt[] { return JSON.parse(this.deps.storage.getItem(receiptKey(this.context.address))??'[]'); }
  async review(action:NativeAction): Promise<NativeReview> { return this.prepare(action); }
  private async prepare(action:NativeAction, retained?: Certificate): Promise<NativeReview> {
    if(!this.deps.writesEnabled)throw new Error('Blockchain writes are OFF in this build');
    if(this.pending())throw new Error('Reconcile the pending wallet transaction first');
    await this.wallet();
    // Only loan/permission decisions need synchronized ledger state. Pure chain
    // actions still recheck confirmed state, runtime, locks, wallet and fees.
    const ledgerRequired=['requestBorrow','acknowledge','settle','cancel','authorize','migrate'].includes(action.kind);
    const cached=action.kind==='deposit'&&this.observed&&this.now()-this.observed.observedAt>=0&&this.now()-this.observed.observedAt<=30?this.observed:null;
    const s=cached??await (ledgerRequired ? this.refresh() : this.read());
    let certificate:Certificate|undefined;
    if(['acknowledge','settle','cancel'].includes(action.kind)) {
      if(!s.vault)throw new Error('Missing vault');
      const expected=action.kind==='acknowledge'?1:action.kind==='settle'?2:3;
      const latest=await verifyCertificate(s,await this.deps.service.nativeCertificate(s.vault.address,s.vault.loanId),expected,this.now());
      if(retained && ['action','a','b','c'].some(k => retained[k as keyof Certificate] !== latest[k as keyof Certificate])) throw new Error('Ledger settlement amounts changed');
      certificate=Object.freeze(await verifyCertificate(s,retained??latest,expected,this.now()));
    }
    if(action.kind==='authorize'&&(!Number.isSafeInteger(action.expiresAt)||action.expiresAt<=this.now()||action.expiresAt>this.now()+86400))throw new Error('Trading permission must expire within 24 hours');
    if(action.kind==='authorize') {
      const account=await this.deps.service.refresh();
      const prior=account.authorizationMaxLossMicro??account.tradingPermission.maxLoss;
      if(prior!==undefined) {
        if(!/^\d+(\.0+)?$/.test(prior)) throw new Error('Existing stop limit unavailable');
        const budget=[exact(action.maxLoss,6),BigInt(prior.split('.')[0])].reduce((a,b)=>a<b?a:b);
        action={...action,maxLoss:`${budget/1_000_000n}.${(budget%1_000_000n).toString().padStart(6,'0')}`};
      }
    }
    if(action.kind==='requestBorrow') {
      const account=await this.deps.service.refresh();
      const quote=account.health?.quote;
      if(!quote || !Number.isFinite(quote.at) || this.now()-quote.at>300 || this.now()-quote.at < -1 || !quote.source.startsWith('ILLUSTRATIVE')) throw new Error('Refresh the illustrative demo collateral quote on the Borrow screen');
      const cap=account.health?.borrowCapacity;
      if(exact(action.amount,6)>capacityMicro(cap))throw new Error('Fresh borrowing capacity is unavailable or exceeded');
    }
    const tx=transaction(s,action,certificate),provider={request:(r:Parameters<NativeProvider['request']>[0])=>this.rpc(r)};
    const [block,code]=await Promise.all([
      provider.request({method:'eth_getBlockByNumber',params:[toHex(s.block),false]}) as Promise<{hash?:string}>,
      provider.request({method:'eth_getCode',params:[tx.to,toHex(s.block)]}) as Promise<Hex>,
    ]);
    const expectedHash=['fundReserve','setRewardRate'].includes(action.kind)?s.rewardPool!.codeHash:action.kind==='create'?s.factoryCodeHash:action.kind==='migrate'?s.upgrade!.factoryCodeHash:s.vaultCodeHash;
    if(block?.hash?.toLowerCase()!==s.blockHash.toLowerCase()||!code||code==='0x'||keccak256(code).toLowerCase()!==expectedHash.toLowerCase())throw new Error('Wallet RPC contract or confirmed block mismatch');
    const [gasEstimate,priceEstimate,nonceEstimate,walletBalance]=await Promise.all([
      provider.request({method:'eth_estimateGas',params:[tx]}),
      provider.request({method:'eth_gasPrice'}),
      provider.request({method:'eth_getTransactionCount',params:[tx.from,'pending']}),
      provider.request({method:'eth_getBalance',params:[tx.from,'pending']}),
    ]);
    const gas=(BigInt(gasEstimate as string)*125n+99n)/100n;
    const gasPrice=BigInt(priceEstimate as string)*2n;
    const nonce=Number(BigInt(nonceEstimate as string));
    if(gas<=0n||gasPrice<=0n||!Number.isSafeInteger(nonce))throw new Error('Invalid fee estimate');
    const unsigned=serializeTransaction({type:'legacy',chainId:NATIVE_CHAIN,nonce,to:tx.to,data:tx.data,value:BigInt(tx.value),gas,gasPrice});
    const data=encodeFunctionData({abi:[{type:'function',name:'getL1Fee',stateMutability:'view',inputs:[{type:'bytes',name:'data'}],outputs:[{type:'uint256'}]}],functionName:'getL1Fee',args:[unsigned]});
    const l1=BigInt(await provider.request({method:'eth_call',params:[{to:'0x5300000000000000000000000000000000000002',data},'latest']}) as string);
    if(l1<0n)throw new Error('Invalid data fee estimate');
    const fee=gas*gasPrice+2n*l1,balance=BigInt(walletBalance as string);
    if(balance<BigInt(tx.value)+fee)throw new Error('Insufficient test DOGE for amount and execution/data fees');
    await this.wallet();
    const q=Object.freeze({owner:this.context.address,generation:this.context.generation,factory:s.factory,vault:s.vault?.address??null,loanId:s.vault?.loanId??null,loanNonce:s.vault?.loanNonce??null,action:Object.freeze({...action}),tx:Object.freeze({...tx,gas:toHex(gas),gasPrice:toHex(gasPrice)}),feeReserveWei:fee.toString(),createdAt:this.now(),certificate,...(action.kind==='migrate'?{migration:Object.freeze({...s.upgrade!})}:{})});
    this.reviews.add(q);return q;
  }
  async submit(q:NativeReview,confirmed:boolean,signal?:AbortSignal,onWalletRequest?:()=>void) {
    if(!this.deps.writesEnabled||!confirmed||!this.reviews.has(q))throw new Error('Confirm the reviewed native transaction');
    if(signal?.aborted)throw new Error('Wallet wait cancelled');
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lockTimer: ReturnType<typeof setTimeout> | undefined;
    let lockExpired=false;
    let cancel!: () => void;
    const stopped=new Promise<never>((_,reject)=>{
      cancel=()=>reject(new Error('Stopped waiting. Check wallet history; any sent transaction still needs reconciliation.'));
      signal?.addEventListener('abort',cancel,{once:true});
    });
    const lockWait=new Promise<never>((_,reject)=>{
      lockTimer=setTimeout(()=>{
        lockExpired=true;
        reject(new Error('Another Vela tab is using the wallet. Close that review and try again. No wallet request was sent by this attempt.'));
      },15_000);
    });
    const launch=this.deps.withLock(key(this.context.address),async()=>{
      clearTimeout(lockTimer);
      if(lockExpired||signal?.aborted)throw new Error('Wallet request cancelled before acquiring its lock');
      this.current();if(this.pending())throw new Error('A wallet transaction is already pending');
      const age=this.now()-q.createdAt;
      if(age<0||age>(q.action.kind==='deposit'?30:120))throw new Error('Review expired; refresh amounts and fees');
      // Deposit checks, code verification and bounded gas are completed before
      // the review opens. Confirmation uses that exact short-lived transaction.
      // Wallet identity and the durable cross-tab send guard still run here.
      // Loan, permission, withdrawal and settlement actions retain full rechecks.
      if(q.action.kind==='deposit'&&this.observed&&(this.observed.factory!==q.factory||this.observed.vault?.address!==q.vault||this.observed.vault?.loanNonce!==q.loanNonce||this.observed.vault.paused))throw new Error('Vault changed; review again');
      const fresh=q.action.kind==='deposit'?q:await this.prepare(q.action,q.certificate);
      if(fresh.factory!==q.factory||fresh.vault!==q.vault||fresh.loanId!==q.loanId||fresh.loanNonce!==q.loanNonce||fresh.tx.to!==q.tx.to||fresh.tx.data!==q.tx.data||fresh.tx.value!==q.tx.value||fresh.migration?.targetVault!==q.migration?.targetVault||BigInt(fresh.feeReserveWei)>BigInt(q.feeReserveWei))throw new Error('Vault, certificate or fees changed; review again');
      const pending:NativePending={version:1,attemptId:crypto.randomUUID(),owner:q.owner,chainId:NATIVE_CHAIN,action:q.action,tx:{...fresh.tx},hash:null,createdAt:this.now(),phase:'AWAITING_WALLET'};
      if(q.action.kind==='migrate') {
        if(!q.migration)throw new Error('Reviewed migration required');
        await this.deps.service.authorizeMigration(q.migration);
      }
      await this.wallet();
      if(signal?.aborted)throw new Error('Wallet wait cancelled before sending');
      this.deps.storage.setItem(key(q.owner),JSON.stringify(pending));
      // Persist under the cross-tab lock, then release it while MetaMask is
      // open so hash recovery can complete without waiting for the extension.
      timer=setTimeout(cancel,45_000);
      const response=Promise.resolve().then(()=>{
        const result=this.deps.provider.request({method:'eth_sendTransaction',params:[{...fresh.tx,chainId:toHex(NATIVE_CHAIN)}]});
        onWalletRequest?.();return result;
      });
      const walletResult=response.then(async sent=>{
        if(!hash(sent))throw new Error('No valid hash returned; check wallet history before retrying');
        return this.deps.withLock(key(q.owner),async()=>{
          const current=this.pending();
          if(sameAttempt(current,pending)) {
            if(current?.hash && current.hash.toLowerCase()!==sent.toLowerCase())throw new Error('Wallet returned a different hash; reconcile wallet history');
            pending.hash=sent;pending.phase='SUBMITTED';this.deps.storage.setItem(key(q.owner),JSON.stringify(pending));
          }
          // A recovered attempt must never be resurrected by a late reply.
          this.current();return {...pending,hash:sent,phase:'SUBMITTED' as const};
        });
      },async e=>{
        const error=e as {code?:number;data?:{originalError?:{code?:number}}};
        if(Number(error.code??error.data?.originalError?.code)===4001)await this.deps.withLock(key(q.owner),async()=>{
          const current=this.pending();
          if(sameAttempt(current,pending)&&!current?.hash)this.deps.storage.removeItem(key(q.owner));
        });
        throw e;
      });
      void walletResult.catch(()=>{});
      return {walletResult};
    });
    const run=launch.then(({walletResult})=>walletResult);
    // Stopping the UI wait cannot cancel a wallet broadcast. The durable record and
    // late hash survive; no new send is allowed until that action is reconciled.
    try{return await Promise.race([run,stopped,lockWait]);}
    finally{clearTimeout(timer);clearTimeout(lockTimer);signal?.removeEventListener('abort',cancel);}
  }
  async attachHash(value:string) {
    this.current();if(!hash(value))throw new Error('Enter a valid wallet transaction hash');
    return this.deps.withLock(key(this.context.address),async()=>{
      const p=this.pending();if(!p||p.hash)throw new Error('No transaction awaiting a hash');
      p.hash=value;p.phase='SUBMITTED';this.deps.storage.setItem(key(p.owner),JSON.stringify(p));
    });
  }
  async reconcile() {
    const p=this.pending();if(!p?.hash)throw new Error('Find the transaction hash in wallet history; do not send again');
    const s=await this.read(p.hash),t=s.transaction;
    if(!t||t.status==='PENDING')return {pending:true as const,state:s,receipt:undefined};
    if(t.hash.toLowerCase()!==p.hash.toLowerCase()||t.sender?.toLowerCase()!==p.owner||t.to?.toLowerCase()!==p.tx.to.toLowerCase()||t.data?.toLowerCase()!==p.tx.data.toLowerCase()||!uint(t.valueWei)||BigInt(t.valueWei)!==BigInt(p.tx.value)||!Number.isSafeInteger(t.block)||t.block! > s.block||!hash(t.blockHash)||!['CONFIRMED','REVERTED'].includes(t.status))throw new Error('Receipt does not match this reviewed wallet action');
    if(t.status==='CONFIRMED') {
      if(!s.vault&&!['fundReserve','setRewardRate'].includes(p.action.kind))throw new Error('Confirmed vault state unavailable');
      if(p.action.kind==='migrate'&&(s.vault?.kind!=='rewarded'||s.factory.toLowerCase()!==p.tx.to.toLowerCase()))throw new Error('Reward vault migration is not confirmed');
      if(p.action.kind==='claim'&&(!uint(t.rewardPaidWei)||BigInt(t.rewardPaidWei)===0n))throw new Error('Confirmed reward claim event required');
      if(s.vault)await this.deps.service.syncNative(p.hash);
    }
    const receipt:NativeReceipt={kind:p.action.kind==='claim'?'native-reward-claim':'native-vault-action',hash:p.hash,status:t.status,action:p.action.kind,rewardPaidWei:t.status==='CONFIRMED'?(t.rewardPaidWei??'0'):'0',block:t.block!};
    return this.deps.withLock(key(p.owner),async()=>{
      const receipts=this.receipts().filter(r=>r.hash!==p.hash);
      this.deps.storage.setItem(receiptKey(p.owner),JSON.stringify([receipt,...receipts].slice(0,30)));
      const current=this.pending();
      if(sameAttempt(current,p)&&current?.hash?.toLowerCase()===p.hash?.toLowerCase())this.deps.storage.removeItem(key(p.owner));
      return {pending:!!this.pending(),state:s,receipt};
    });
  }
}

import { encodeFunctionData, keccak256, serializeTransaction, toHex, type Abi, type Hex } from 'viem';
import abi from './TestDogeLending.abi.json';
import type { AccountService, SessionContext } from './accountService';
import { NATIVE_CHAIN, type NativeProvider } from './nativeVault';
import { walletRead } from './walletRpc';
import { parseDecimal, formatUnits } from '../units';

export type DogeConfig = {enabled:boolean;chainId:6281971;market:Hex|null;runtimeCodeHash:Hex|null;confirmations:number;aprBps:500;maxLtvBps:7000;conversion:{enabled:false;reason:string}};
export type DogeState = {source:'chain';chainId:6281971;owner:string;market:Hex;codeHash:Hex;bytecodeVerified:true;block:number;blockHash:Hex;observedAt:number;confirmations:number;position:{collateral:string;principal:string;interest:string;available:string;revision:string};debtWei:string;availableToBorrowWei:string;liquidityWei:string;walletBalanceWei:string;paused:boolean;transaction:null|{hash:Hex;status:'PENDING'|'CONFIRMED'|'REVERTED';sender?:string;to?:string;data?:string;valueWei?:string;block?:number;blockHash?:string}};
export type DogeAction = 'deposit'|'borrow'|'withdrawProceeds'|'withdrawCollateral'|'repay'|'repayAvailable'|'repayCollateral';
type Tx = {from:Hex;to:Hex;data:Hex;value:Hex;gas?:Hex;gasPrice?:Hex};
export type DogeReview = Readonly<{action:DogeAction;amountWei:string;tx:Readonly<Tx>;createdAt:number;feeWei:string;revision:string}>;
export type DogePending = {id:string;owner:string;market:string;action:DogeAction;tx:Tx;hash:Hex|null;at:number};
export type DogeReceipt = {hash:Hex;action:DogeAction;status:'CONFIRMED'|'REVERTED';block:number};
const addr=(v:unknown):v is Hex=>typeof v==='string'&&/^0x[0-9a-f]{40}$/i.test(v)&&BigInt(v)!==0n;
const hash=(v:unknown):v is Hex=>typeof v==='string'&&/^0x[0-9a-f]{64}$/i.test(v);
const uint=(v:unknown):v is string=>typeof v==='string'&&/^\d{1,78}$/.test(v);
const pendingKey=(owner:string)=>`vela:doge-lending:${NATIVE_CHAIN}:${owner}`;
export function validateDogeConfig(value:unknown):DogeConfig {
  const c=value as DogeConfig;
  if(!c||typeof c.enabled!=='boolean'||c.chainId!==NATIVE_CHAIN||c.aprBps!==500||c.maxLtvBps!==7000||c.conversion?.enabled!==false||!Number.isSafeInteger(c.confirmations)||c.confirmations<3||c.enabled&&(!addr(c.market)||!hash(c.runtimeCodeHash)))throw Error('Verified test DOGE market configuration required');
  return c;
}
export function validateDogeState(value:unknown,owner:string,config:DogeConfig,now:number):DogeState {
  const s=value as DogeState,p=s?.position;
  if(!config.enabled||!s||s.source!=='chain'||s.chainId!==NATIVE_CHAIN||s.owner!==owner||s.market.toLowerCase()!==config.market?.toLowerCase()||s.codeHash.toLowerCase()!==config.runtimeCodeHash?.toLowerCase()||!s.bytecodeVerified||!hash(s.blockHash)||!Number.isSafeInteger(s.block)||s.block<0||!Number.isSafeInteger(s.observedAt)||now-s.observedAt<0||now-s.observedAt>90||!Number.isSafeInteger(s.confirmations)||s.confirmations<config.confirmations||!p||![p.collateral,p.principal,p.interest,p.available,p.revision,s.debtWei,s.availableToBorrowWei,s.liquidityWei,s.walletBalanceWei].every(uint)||typeof s.paused!=='boolean')throw Error('Confirmed DOGE account unavailable');
  const debt=BigInt(s.debtWei),ceiling=BigInt(p.collateral)*7n/10n;
  if(debt!==BigInt(p.principal)+BigInt(p.interest)||BigInt(s.availableToBorrowWei)>BigInt(s.liquidityWei)||BigInt(s.availableToBorrowWei)>(ceiling>debt?ceiling-debt:0n)||s.paused&&s.availableToBorrowWei!=='0')throw Error('Invalid DOGE borrowing amounts');
  return s;
}
export function dogeTransaction(s:DogeState,action:DogeAction,amount:string,now:number):Tx {
  const parsed=parseDecimal(amount,18);if(!parsed.ok||parsed.value<=0n)throw Error(parsed.ok?'Enter an amount above zero.':parsed.error);
  const n=parsed.value,p=s.position;let limit:bigint;
  switch(action){
    case 'borrow':limit=BigInt(s.availableToBorrowWei);if(s.paused)throw Error('New borrowing is paused');break;
    case 'deposit':case 'repay':limit=BigInt(s.walletBalanceWei);break;
    case 'withdrawProceeds':case 'repayAvailable':limit=BigInt(p.available);break;
    case 'withdrawCollateral':if(BigInt(s.debtWei)>0n)throw Error('Repay your loan before withdrawing collateral');limit=BigInt(p.collateral);break;
    case 'repayCollateral':limit=BigInt(p.collateral);break;
  }
  if(n>limit){
    const available=formatUnits(limit,18);
    if(action==='borrow')throw Error(`You can borrow up to ${available} DOGE against your deposited collateral and the pool liquidity.`);
    if(action==='deposit'||action==='repay')throw Error(`Your connected wallet has ${available} test DOGE on DogeOS. Enter a smaller amount and leave DOGE for fees.`);
    if(action==='withdrawProceeds'||action==='repayAvailable')throw Error(`Available borrowed funds: ${available} DOGE. To repay using your wallet balance, select Use wallet DOGE.`);
    throw Error(`Available collateral: ${available} DOGE.`);
  }
  if(action.startsWith('repay')&&BigInt(s.debtWei)===0n)throw Error('No DOGE debt to repay');
  const payable=action==='deposit'||action==='repay';
  const args=payable?[BigInt(p.revision),BigInt(now+600)]:[n,BigInt(p.revision),BigInt(now+600)];
  return {from:s.owner as Hex,to:s.market,data:encodeFunctionData({abi:abi as Abi,functionName:action,args}),value:payable?toHex(n):'0x0'};
}

/** Native market writes never touch demo USD. Exact transaction, wallet identity,
 * chain receipt, cross-tab lock and a persistent pending send all remain bound. */
export class DogeLendingWorkflow {
  private context:SessionContext;
  private reviews=new WeakSet<DogeReview>();
  constructor(private deps:{service:AccountService;provider:NativeProvider;storage:Pick<Storage,'getItem'|'setItem'|'removeItem'>;withLock:<T>(key:string,fn:()=>Promise<T>)=>Promise<T>;now?:()=>number}){this.context=deps.service.sessionContext();}
  private now(){return this.deps.now?.()??Math.floor(Date.now()/1000);}
  private current(){const c=this.deps.service.sessionContext();if(c.address!==this.context.address||c.generation!==this.context.generation||c.network!=='dogeos')throw Error('Wallet changed; review again');}
  private async wallet(){this.current();let timer:ReturnType<typeof setTimeout>|undefined;try{const [chain,accounts]=await Promise.race([Promise.all([this.deps.provider.request({method:'eth_chainId'}),this.deps.provider.request({method:'eth_accounts'})]),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Wallet identity check timed out')),8000);})]);this.current();if(BigInt(chain as string)!==BigInt(NATIVE_CHAIN)||!Array.isArray(accounts)||accounts[0]?.toLowerCase()!==this.context.address)throw Error('Wallet or network changed');}finally{clearTimeout(timer);}}
  async config(){this.current();return validateDogeConfig(await this.deps.service.dogeLending('config'));}
  async read(tx?:string){const c=await this.config();return validateDogeState(await this.deps.service.dogeLending('account',tx),this.context.address,c,this.now());}
  pending():DogePending|null {const raw=this.deps.storage.getItem(pendingKey(this.context.address));if(!raw)return null;const p=JSON.parse(raw) as DogePending;if(!p||p.owner!==this.context.address||!addr(p.market)||!addr(p.tx?.to)||p.market!==p.tx.to||p.tx.from!==p.owner||p.hash!==null&&!hash(p.hash)||typeof p.id!=='string')throw Error('Saved wallet request needs recovery');return p;}
  receipts():DogeReceipt[]{return JSON.parse(this.deps.storage.getItem(pendingKey(this.context.address)+':receipts')??'[]');}
  async review(action:DogeAction,amount:string,fullRepayment=false):Promise<DogeReview>{
    this.current();if(this.pending())throw Error('Check your pending wallet transaction first');
    await this.wallet();const s=await this.read();
    if(fullRepayment){if(action!=='repay')throw Error('Full repayment requires wallet DOGE');const principal=BigInt(s.position.principal);const reserve=(principal*500n*900n+315360000000n-1n)/315360000000n+1n;amount=formatUnits(BigInt(s.debtWei)+reserve,18);}
    const rpc=(method:string,params?:unknown[])=>walletRead(this.deps.provider,{method,params});
    const [block,code,currentBalance]=await Promise.all([rpc('eth_getBlockByNumber',[toHex(s.block),false]),rpc('eth_getCode',[s.market,toHex(s.block)]),rpc('eth_getBalance',[this.context.address,'pending'])]);
    if((block as {hash?:string})?.hash?.toLowerCase()!==s.blockHash.toLowerCase()||!code||code==='0x'||keccak256(code as Hex).toLowerCase()!==s.codeHash.toLowerCase())throw Error('Wallet and server chain observations differ');
    // Collateral/debt stay at the verified confirmed block. Spendable wallet DOGE
    // must include new funding and pending spends, not that older block's balance.
    if(typeof currentBalance!=='string'||!/^0x[0-9a-f]+$/i.test(currentBalance))throw Error('Wallet DOGE balance unavailable; retry.');
    const tx=dogeTransaction({...s,walletBalanceWei:BigInt(currentBalance).toString()},action,amount,this.now());
    const [g,gp,nonce,balance]=await Promise.all([rpc('eth_estimateGas',[tx]),rpc('eth_gasPrice'),rpc('eth_getTransactionCount',[tx.from,'pending']),rpc('eth_getBalance',[tx.from,'pending'])]);
    const gas=(BigInt(g as string)*125n+99n)/100n,gasPrice=BigInt(gp as string)*2n,n=Number(BigInt(nonce as string));
    if(gas<=0n||gasPrice<=0n||!Number.isSafeInteger(n)||n<0)throw Error('Invalid execution fee estimate');
    const unsigned=serializeTransaction({type:'legacy',chainId:NATIVE_CHAIN,nonce:n,to:tx.to,data:tx.data,value:BigInt(tx.value),gas,gasPrice});
    const data=encodeFunctionData({abi:[{type:'function',name:'getL1Fee',stateMutability:'view',inputs:[{type:'bytes',name:'data'}],outputs:[{type:'uint256'}]}],functionName:'getL1Fee',args:[unsigned]});
    const l1=BigInt(await rpc('eth_call',[{to:'0x5300000000000000000000000000000000000002',data},'latest']) as string),fee=gas*gasPrice+2n*l1;
    if(BigInt(balance as string)<BigInt(tx.value)+fee)throw Error('Leave enough DOGE for transaction fees');
    await this.wallet();const parsed=parseDecimal(amount,18);if(!parsed.ok)throw Error(parsed.error);
    const q=Object.freeze({action,amountWei:parsed.value.toString(),tx:Object.freeze({...tx,gas:toHex(gas),gasPrice:toHex(gasPrice)}),createdAt:this.now(),feeWei:fee.toString(),revision:s.position.revision});
    this.reviews.add(q);return q;
  }
  async submit(q:DogeReview,confirmed:boolean):Promise<DogePending>{
    if(!confirmed||!this.reviews.has(q))throw Error('Confirm the reviewed transaction');
    const key=pendingKey(this.context.address);
    const {response}=await this.deps.withLock(key,async()=>{
      this.current();if(this.pending())throw Error('Wallet transaction already pending');
      if(this.now()-q.createdAt<0||this.now()-q.createdAt>30)throw Error('Review expired; refresh amounts and fees');
      await this.wallet();
      const p:DogePending={id:crypto.randomUUID(),owner:this.context.address,market:q.tx.to,action:q.action,tx:{...q.tx},hash:null,at:this.now()};
      this.deps.storage.setItem(key,JSON.stringify(p));
      // Write-ahead record precedes the one send. Do not retry an uncertain send.
      const response=Promise.resolve().then(()=>this.deps.provider.request({method:'eth_sendTransaction',params:[{...q.tx,chainId:toHex(NATIVE_CHAIN)}]})).then(async sent=>{
        if(!hash(sent))throw Error('Wallet did not return a hash. Check wallet history.');
        return this.deps.withLock(key,async()=>{const saved=this.pending();if(saved?.id===p.id){if(saved.hash&&saved.hash.toLowerCase()!==sent.toLowerCase())throw Error('Different wallet hash; check history');p.hash=sent;this.deps.storage.setItem(key,JSON.stringify(p));}this.current();return {...p,hash:sent};});
      },async error=>{if(Number(error?.code??error?.data?.originalError?.code)===4001)await this.deps.withLock(key,async()=>{const saved=this.pending();if(saved?.id===p.id&&!saved.hash)this.deps.storage.removeItem(key);});throw error;});
      void response.catch(()=>{});return {response};
    });
    let timer:ReturnType<typeof setTimeout>|undefined;
    try{return await Promise.race([response,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Wallet response is taking longer. Check wallet history before retrying.')),45000);})]);}finally{clearTimeout(timer);}
  }
  async attachHash(tx:string){if(!hash(tx))throw Error('Enter a valid transaction hash');return this.deps.withLock(pendingKey(this.context.address),async()=>{this.current();const p=this.pending();if(!p||p.hash)throw Error('No wallet request awaiting a hash');p.hash=tx;this.deps.storage.setItem(pendingKey(p.owner),JSON.stringify(p));});}
  async reconcile(){const p=this.pending();if(!p?.hash)throw Error('Check wallet history for the transaction hash');const s=await this.read(p.hash),t=s.transaction;
    if(!t||t.status==='PENDING')return s;
    if(t.hash.toLowerCase()!==p.hash.toLowerCase()||t.sender!==p.owner||t.to?.toLowerCase()!==p.tx.to.toLowerCase()||t.data?.toLowerCase()!==p.tx.data.toLowerCase()||!uint(t.valueWei)||BigInt(t.valueWei)!==BigInt(p.tx.value)||!Number.isSafeInteger(t.block)||t.block!>s.block||!hash(t.blockHash))throw Error('Receipt does not match the reviewed action');
    await this.deps.withLock(pendingKey(p.owner),async()=>{const rows=this.receipts().filter(r=>r.hash!==p.hash);this.deps.storage.setItem(pendingKey(p.owner)+':receipts',JSON.stringify([{hash:p.hash,action:p.action,status:t.status,block:t.block},...rows].slice(0,80)));if(this.pending()?.id===p.id)this.deps.storage.removeItem(pendingKey(p.owner));});return s;
  }
}

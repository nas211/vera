import { encodeAbiParameters, getContractAddress, keccak256, serializeTransaction, toHex, type Hex } from 'viem';
import inputs from './deployment.json';
import type { NativeProvider } from '@/lib/vela/nativeVault';

export const deployment = inputs;
const OWNER = inputs.owner.toLowerCase() as Hex;
const CHAIN = 6281971;
const DATA = inputs.data as Hex;
const KEY = `vela:reward-deployment:${CHAIN}:${OWNER}:${inputs.initHash}`;
const isHex = (value: unknown): value is Hex => typeof value === 'string' && /^0x[0-9a-f]+$/i.test(value);
const quantity = (value: unknown) => { if (!isHex(value)) throw Error('Invalid RPC quantity'); return BigInt(value); };
const same = (a: unknown, b: string) => typeof a === 'string' && a.toLowerCase() === b.toLowerCase();
const hash = (value: unknown): value is Hex => typeof value === 'string' && /^0x[0-9a-f]{64}$/i.test(value);
export type ReadRPC = (method: string, params: unknown[]) => Promise<unknown>;
export type DeploymentQuote = Readonly<{ nonce: Hex; gas: Hex; gasPrice: Hex; balanceWei: string; reserveWei: string; factory: Hex; pool: Hex; createdAt: number }>;
export type DeploymentPending = { quote: DeploymentQuote; initHash: string; phase: 'AWAITING_WALLET' | 'SUBMITTED' | 'CONFIRMED' | 'REVERTED'; hash: Hex | null };
export type DeploymentReceipt = { status: 'PENDING' | 'CONFIRMED' | 'REVERTED'; hash: Hex; factory?: Hex; pool?: Hex; block?: string; factoryCodeHash?: Hex; poolCodeHash?: Hex };

const READS = new Set(['eth_chainId','eth_getTransactionCount','eth_getBalance','eth_estimateGas','eth_gasPrice','eth_call','eth_getCode','eth_getTransactionByHash','eth_getTransactionReceipt','eth_blockNumber','eth_getBlockByNumber']);
export const readRPC: ReadRPC = async (method, params) => {
  if (!READS.has(method)) throw Error('Read-only RPC method required');
  const response = await fetch('https://rpc.testnet.dogeos.com/', {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({jsonrpc:'2.0',id:1,method,params}),signal:AbortSignal.timeout(25000)});
  if (!response.ok) throw Error('DogeOS request failed. Retry the review.');
  const result = await response.json();
  if (result.error) throw Error('DogeOS could not complete the request. Retry the review.');
  return result.result;
};

export class RewardDeployment {
  private issued = new WeakSet<DeploymentQuote>();
  constructor(private readonly provider: NativeProvider, private readonly rpc: ReadRPC,
    private readonly storage: Pick<Storage,'getItem'|'setItem'|'removeItem'>,
    private readonly lock: <T>(fn:()=>Promise<T>)=>Promise<T>, private readonly now=()=>Date.now()) {
    if (inputs.chainId !== CHAIN || inputs.aprBps !== 500 || keccak256(DATA) !== inputs.initHash) throw Error('Deployment inputs differ');
  }
  private async wallet() {
    const [chain,accounts,remote] = await Promise.all([this.provider.request({method:'eth_chainId'}),this.provider.request({method:'eth_accounts'}),this.rpc('eth_chainId',[])]);
    if (quantity(chain) !== BigInt(CHAIN) || quantity(remote) !== BigInt(CHAIN)) throw Error('Select DogeOS testnet in your wallet, then review again.');
    if (!Array.isArray(accounts) || !same(accounts[0], OWNER)) throw Error('Connect the owner wallet '+inputs.owner+'.');
  }
  pending(): DeploymentPending | null {
    const raw = this.storage.getItem(KEY); if (!raw) return null;
    const p = JSON.parse(raw) as DeploymentPending, q = p.quote;
    if (p.initHash !== inputs.initHash || !q || !isHex(q.nonce) || !isHex(q.gas) || !isHex(q.gasPrice)
        || quantity(q.nonce)!==BigInt(inputs.expectedNonce) || quantity(q.gas)<=0n || quantity(q.gasPrice)<=0n
        || !['AWAITING_WALLET','SUBMITTED','CONFIRMED','REVERTED'].includes(p.phase) || (p.hash !== null && !hash(p.hash))
        || !same(q.factory,getContractAddress({from:OWNER,nonce:quantity(q.nonce)}))
        || !same(q.pool,getContractAddress({from:q.factory,nonce:1n}))) throw Error('Saved deployment needs reconciliation. Keep your wallet transaction history.');
    return p;
  }
  private save(p: DeploymentPending) { this.storage.setItem(KEY,JSON.stringify(p)); }
  private transaction(q: DeploymentQuote) {
    return {from:OWNER,chainId:toHex(CHAIN),nonce:q.nonce,gas:q.gas,gasPrice:q.gasPrice,data:DATA,value:'0x0'};
  }
  private async currentFees(nonce: Hex, gas?: Hex, gasPrice?: Hex) {
    const base = {from:OWNER,data:DATA,value:'0x0'};
    const [estimateRaw,priceRaw,balanceRaw] = await Promise.all([this.rpc('eth_estimateGas',[base]),this.rpc('eth_gasPrice',[]),this.rpc('eth_getBalance',[OWNER,'pending'])]);
    const estimate = quantity(estimateRaw), price = quantity(priceRaw);
    if (estimate <= 0n || price <= 0n) throw Error('Invalid gas estimate');
    const g = gas ? quantity(gas) : (estimate*125n+99n)/100n, gp = gasPrice ? quantity(gasPrice) : price*2n;
    if (estimate > g || price > gp) throw Error('Gas requirements changed. Review the updated quote.');
    const nonceNumber = Number(quantity(nonce));
    if (!Number.isSafeInteger(nonceNumber)) throw Error('Invalid deployment nonce');
    const serialized = serializeTransaction({type:'legacy',chainId:CHAIN,nonce:nonceNumber,gas:g,gasPrice:gp,value:0n,data:DATA});
    const feeData = (keccak256(toHex('getL1Fee(bytes)')).slice(0,10)+encodeAbiParameters([{type:'bytes'}],[serialized]).slice(2)) as Hex;
    const dataFee = quantity(await this.rpc('eth_call',[{to:'0x5300000000000000000000000000000000000002',data:feeData},'latest']));
    const reserve = g*gp+dataFee*2n, balance = quantity(balanceRaw);
    if (balance < reserve) throw Error('Owner wallet needs more test DOGE for deployment fees.');
    return {gas:toHex(g),gasPrice:toHex(gp),balanceWei:balance.toString(),reserveWei:reserve.toString()};
  }
  private async nonce() {
    const [latest,pending] = await Promise.all(['latest','pending'].map(tag=>this.rpc('eth_getTransactionCount',[OWNER,tag])));
    if (quantity(latest)!==quantity(pending)) throw Error('Wait for the owner wallet’s pending transaction to finish.');
    return toHex(quantity(pending));
  }
  async quote(): Promise<DeploymentQuote> {
    if (this.pending()) throw Error('Check the saved deployment before preparing another.');
    await this.wallet();
    const nonce = await this.nonce(), factory = getContractAddress({from:OWNER,nonce:quantity(nonce)}), pool = getContractAddress({from:factory,nonce:1n});
    if (quantity(nonce)!==BigInt(inputs.expectedNonce)) throw Error('Owner wallet nonce changed. This deployment link needs fresh preparation.');
    const fees = await this.currentFees(nonce);
    const code = await this.rpc('eth_getCode',[factory,'latest']);
    if (code !== '0x' || await this.nonce() !== nonce) throw Error('Deployment address or nonce changed. Review again.');
    await this.wallet();
    const q = Object.freeze({nonce,factory,pool,...fees,createdAt:this.now()}); this.issued.add(q); return q;
  }
  async submit(q: DeploymentQuote): Promise<DeploymentPending> {
    return this.lock(async()=> {
      if (!this.issued.has(q) || this.now()-q.createdAt > 90000 || this.pending()) throw Error('Refresh the deployment review or check the saved transaction.');
      await this.wallet();
      if (await this.nonce() !== q.nonce) throw Error('Owner nonce changed. Review again.');
      const fresh = await this.currentFees(q.nonce,q.gas,q.gasPrice);
      if (BigInt(fresh.reserveWei) > BigInt(q.reserveWei)) throw Error('Fee reserve increased. Review again.');
      if (await this.rpc('eth_getCode',[q.factory,'latest']) !== '0x') throw Error('A contract already exists at the predicted address.');
      await this.wallet();
      if (await this.nonce() !== q.nonce) throw Error('Owner nonce changed. Review again.');
      const p: DeploymentPending = {quote:q,initHash:inputs.initHash,phase:'AWAITING_WALLET',hash:null};
      this.save(p); this.issued.delete(q);
      try {
        const txHash = await this.provider.request({method:'eth_sendTransaction',params:[this.transaction(q)]});
        if (!hash(txHash)) throw Error('Wallet did not return a transaction hash. Check wallet history.');
        p.hash=txHash; p.phase='SUBMITTED'; this.save(p); return p;
      } catch (e) {
        if ((e as {code?:number}).code===4001) this.storage.removeItem(KEY);
        throw e;
      }
    });
  }
  async recover(txHash: string) {
    return this.lock(async()=> {
      const p=this.pending(); if (!p || p.hash || !hash(txHash)) throw Error('A saved deployment and valid transaction hash are required.');
      await this.matchTransaction(p,txHash); p.hash=txHash;p.phase='SUBMITTED';this.save(p);
    });
  }
  private async matchTransaction(p: DeploymentPending, txHash: Hex) {
    if (quantity(await this.rpc('eth_chainId',[])) !== BigInt(CHAIN)) throw Error('Wrong RPC chain');
    const tx = await this.rpc('eth_getTransactionByHash',[txHash]) as Record<string,unknown> | null;
    if (!tx || !same(tx.from,OWNER) || tx.to !== null || !same(tx.input,DATA) || quantity(tx.value)!==0n
        || quantity(tx.nonce)!==quantity(p.quote.nonce) || quantity(tx.chainId)!==BigInt(CHAIN)) throw Error('Transaction does not match this reviewed deployment.');
  }
  async receipt(): Promise<DeploymentReceipt> {
    return this.lock(async()=> {
      const p=this.pending();if (!p?.hash) throw Error('Add the transaction hash from your wallet history.');
      await this.matchTransaction(p,p.hash);
      const r=await this.rpc('eth_getTransactionReceipt',[p.hash]) as Record<string,unknown> | null;
      if (!r) return {status:'PENDING',hash:p.hash};
      const head=quantity(await this.rpc('eth_blockNumber',[])), height=quantity(r.blockNumber);
      if (head < height+2n) return {status:'PENDING',hash:p.hash};
      const canonical=await this.rpc('eth_getBlockByNumber',[r.blockNumber,false]) as Record<string,unknown>;
      if (!same(canonical.hash,String(r.blockHash))) throw Error('Receipt changed on-chain. Check again.');
      if (quantity(r.status)===0n) {p.phase='REVERTED';this.save(p);return {status:'REVERTED',hash:p.hash};}
      if (quantity(r.status)!==1n || !same(r.contractAddress,p.quote.factory) || !same(r.transactionHash,p.hash)) throw Error('Unexpected deployment receipt');
      const getter=async(address:Hex,name:string)=>this.rpc('eth_call',[{to:address,data:keccak256(toHex(name+'()')).slice(0,10)},r.blockNumber]);
      const [factoryCode,poolCode,signer,treasury,manager,pool,poolFactory,poolManager,chain,rate] = await Promise.all([
        this.rpc('eth_getCode',[p.quote.factory,r.blockNumber]),this.rpc('eth_getCode',[p.quote.pool,r.blockNumber]),
        getter(p.quote.factory,'settlementSigner'),getter(p.quote.factory,'liquidationTreasury'),getter(p.quote.factory,'rewardManager'),getter(p.quote.factory,'rewardPool'),
        getter(p.quote.pool,'factory'),getter(p.quote.pool,'rewardManager'),getter(p.quote.pool,'expectedChainId'),getter(p.quote.pool,'aprBps')]);
      const addr=(x:unknown)=>isHex(x)&&x.length===66 ? '0x'+x.slice(-40) : '';
      if (!isHex(factoryCode)||!isHex(poolCode)||!same(addr(signer),inputs.attestor)||!same(addr(treasury),inputs.treasury)||!same(addr(manager),inputs.manager)
          ||!same(addr(pool),p.quote.pool)||!same(addr(poolFactory),p.quote.factory)||!same(addr(poolManager),inputs.manager)||quantity(chain)!==BigInt(CHAIN)||quantity(rate)!==500n) throw Error('Deployed roles or reward terms differ. Keep deployment inactive.');
      const finalBlock=await this.rpc('eth_getBlockByNumber',[r.blockNumber,false]) as Record<string,unknown>;
      if (!same(finalBlock.hash,String(r.blockHash))) throw Error('Deployment confirmation changed. Check again.');
      p.phase='CONFIRMED';this.save(p);
      return {status:'CONFIRMED',hash:p.hash,factory:p.quote.factory,pool:p.quote.pool,block:String(r.blockNumber),factoryCodeHash:keccak256(factoryCode),poolCodeHash:keccak256(poolCode)};
    });
  }
}

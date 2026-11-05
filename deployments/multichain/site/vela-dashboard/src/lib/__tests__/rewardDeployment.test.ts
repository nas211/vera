import { describe, it, expect, vi } from 'vitest';
import { encodeAbiParameters, getContractAddress, keccak256, toHex, type Hex } from 'viem';
import { deployment, RewardDeployment } from '../../owner/deployment';

const txHash = `0x${'a1'.repeat(32)}` as Hex, blockHash = `0x${'b2'.repeat(32)}` as Hex;
function setup() {
  const data=new Map<string,string>();
  const state={nonce:BigInt(deployment.expectedNonce),price:1n,chain:6281971n,owner:deployment.owner,code:false,reorg:false,wrongRole:false,now:1000};
  const storage={getItem:(key:string)=>data.get(key)??null,setItem:(key:string,value:string)=>{data.set(key,value);},removeItem:(key:string)=>{data.delete(key);}};
  const factory=getContractAddress({from:deployment.owner as Hex,nonce:state.nonce}),pool=getContractAddress({from:factory,nonce:1n});
  const provider={request:vi.fn(async({method}:{method:string}):Promise<unknown>=>{
    if(method==='eth_accounts')return[state.owner];if(method==='eth_chainId')return toHex(state.chain);
    if(method==='eth_sendTransaction')return txHash;throw Error('Unexpected wallet request');
  })};
  const rpc=vi.fn(async(method:string,params:unknown[]):Promise<unknown>=>{
    if(method==='eth_chainId')return toHex(state.chain);
    if(method==='eth_getTransactionCount')return toHex(state.nonce);
    if(method==='eth_getBalance')return toHex(10n**20n);
    if(method==='eth_estimateGas')return toHex(1000n);
    if(method==='eth_gasPrice')return toHex(state.price);
    if(method==='eth_getCode')return state.code?'0x1234':'0x';
    if(method==='eth_getTransactionByHash')return {from:deployment.owner,to:null,input:deployment.data,nonce:deployment.expectedNonce,value:'0x0',chainId:toHex(6281971)};
    if(method==='eth_getTransactionReceipt')return {status:'0x1',transactionHash:txHash,blockNumber:'0xa',blockHash,contractAddress:factory};
    if(method==='eth_blockNumber')return '0xc';
    if(method==='eth_getBlockByNumber')return {hash:state.reorg?txHash:blockHash};
    if(method==='eth_call') {
      const call=params[0] as {to:string,data:string};
      if(call.to==='0x5300000000000000000000000000000000000002')return toHex(10n);
      const getters:Record<string,string|bigint>={settlementSigner:state.wrongRole?deployment.owner:deployment.attestor,
        liquidationTreasury:deployment.treasury,rewardManager:deployment.manager,rewardPool:pool,factory,expectedChainId:6281971n,aprBps:500n};
      for(const [name,value] of Object.entries(getters))if(call.data===keccak256(toHex(name+'()')).slice(0,10))
        return typeof value==='bigint'?encodeAbiParameters([{type:'uint256'}],[value]):encodeAbiParameters([{type:'address'}],[value as Hex]);
    }
    throw Error('Unexpected read '+method);
  });
  const workflow=new RewardDeployment(provider,rpc,storage,async fn=>fn(),()=>state.now);
  return {workflow,state,data,provider,rpc};
}
describe('owner deployment handoff',()=>{
  it('quotes without wallet sends, then sends exactly the reviewed constructor and nonce',async()=>{
    const s=setup(),q=await s.workflow.quote();
    expect(q.reserveWei).toBe('2520');expect(s.provider.request.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
    const p=await s.workflow.submit(q);expect(p.hash).toBe(txHash);
    const sent=s.provider.request.mock.calls.find(([r])=>r.method==='eth_sendTransaction')![0] as unknown as {params:Record<string,unknown>[]};
    expect(sent.params[0]).toEqual({from:deployment.owner.toLowerCase(),chainId:toHex(6281971),nonce:deployment.expectedNonce,gas:'0x4e2',gasPrice:'0x2',data:deployment.data,value:'0x0'});
    await expect(s.workflow.submit(q)).rejects.toThrow('saved');
  });
  it.each(['owner','chain','nonce','code'] as const)('refuses a changed %s before quotation',async(field)=>{
    const s=setup();if(field==='owner')s.state.owner='0x'+'11'.repeat(20);if(field==='chain')s.state.chain=1n;if(field==='nonce')s.state.nonce++;if(field==='code')s.state.code=true;
    await expect(s.workflow.quote()).rejects.toThrow();expect(s.data.size).toBe(0);
  });
  it.each(['nonce','price','age'] as const)('rejects %s changes before any wallet send',async(field)=>{
    const s=setup(),q=await s.workflow.quote();if(field==='nonce')s.state.nonce++;if(field==='price')s.state.price=3n;if(field==='age')s.state.now+=90001;
    await expect(s.workflow.submit(q)).rejects.toThrow();expect(s.data.size).toBe(0);
    expect(s.provider.request.mock.calls.some(([r])=>r.method==='eth_sendTransaction')).toBe(false);
  });
  it('retains an uncertain submission and recovers only its matching transaction',async()=>{
    const s=setup(),q=await s.workflow.quote();const original=s.provider.request.getMockImplementation()!;
    s.provider.request.mockImplementation(async r=>{if(r.method==='eth_sendTransaction')throw Error('Wallet response lost');return original(r);});
    await expect(s.workflow.submit(q)).rejects.toThrow('lost');expect(s.workflow.pending()?.phase).toBe('AWAITING_WALLET');
    await expect(s.workflow.quote()).rejects.toThrow('saved');
    await s.workflow.recover(txHash);expect(s.workflow.pending()?.hash).toBe(txHash);
  });
  it('clears only a definite wallet rejection so a fresh review is possible',async()=>{
    const s=setup(),q=await s.workflow.quote();const original=s.provider.request.getMockImplementation()!;
    s.provider.request.mockImplementation(async r=>{if(r.method==='eth_sendTransaction')throw Object.assign(Error('Rejected'),{code:4001});return original(r);});
    await expect(s.workflow.submit(q)).rejects.toThrow('Rejected');expect(s.workflow.pending()).toBeNull();
    await expect(s.workflow.submit(q)).rejects.toThrow('review');
  });
  it('verifies canonical receipts and deployed roles before recording confirmation',async()=>{
    const s=setup(),q=await s.workflow.quote();await s.workflow.submit(q);s.state.code=true;
    const receipt=await s.workflow.receipt();expect(receipt.status).toBe('CONFIRMED');expect(s.workflow.pending()?.phase).toBe('CONFIRMED');
    expect(receipt.factoryCodeHash).toBe(keccak256('0x1234'));await expect(s.workflow.quote()).rejects.toThrow('saved');
  });
  it.each(['reorg','wrongRole'] as const)('refuses a %s receipt',async(field)=>{
    const s=setup(),q=await s.workflow.quote();await s.workflow.submit(q);s.state.code=true;s.state[field]=true;
    await expect(s.workflow.receipt()).rejects.toThrow();expect(s.workflow.pending()?.phase).toBe('SUBMITTED');
  });
});

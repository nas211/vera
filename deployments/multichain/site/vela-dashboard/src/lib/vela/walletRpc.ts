import type { NativeProvider } from './nativeVault';
type Request=Parameters<NativeProvider['request']>[0];
type Queue={tail:Promise<void>;pending:Map<string,Promise<unknown>>;cooldown:number};
const queues=new WeakMap<NativeProvider,Queue>();
const reads=new Set(['eth_getBalance','eth_getCode','eth_getBlockByNumber','eth_estimateGas','eth_gasPrice','eth_getTransactionCount','eth_call']);
export function rateLimited(error:unknown,depth=0):boolean {
  if(!error||typeof error!=='object'||depth>2)return false;
  const e=error as {code?:unknown;status?:unknown;message?:unknown;data?:{originalError?:unknown};error?:unknown};
  return Number(e.code)===429||Number(e.code)===-32005||Number(e.status)===429||typeof e.message==='string'&&/rate.?limit|too many requests/i.test(e.message)||rateLimited(e.data?.originalError,depth+1)||rateLimited(e.error,depth+1);
}
async function timedRead(provider:NativeProvider,request:Request) {
  let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([provider.request(request),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Wallet RPC timed out; refresh to retry')),8000);})]);}
  finally{clearTimeout(timer);}
}
const wait=(ms:number)=>ms>0?new Promise<void>(resolve=>setTimeout(resolve,ms)):Promise.resolve();
/** Serialize/coalesce chain reads across balance polling and transaction review.
 * Wallet prompts, signatures and sends never enter this retry queue. */
export function walletRead(provider:NativeProvider,request:Request):Promise<unknown> {
  if(!reads.has(request.method))return provider.request(request);
  let queue=queues.get(provider);
  if(!queue){queue={tail:Promise.resolve(),pending:new Map(),cooldown:0};queues.set(provider,queue);}
  const key=JSON.stringify(request),old=queue.pending.get(key);if(old)return old;
  const state=queue;
  const run=state.tail.then(async()=>{
    if(state.cooldown>Date.now())throw Error('DogeOS is limiting requests. Wallet reads are paused briefly; retry in 30 seconds.');
    for(let attempt=0;attempt<3;attempt++){
      try{return await timedRead(provider,request);}
      catch(error){
        if(!rateLimited(error))throw error;
        if(attempt===2){state.cooldown=Date.now()+30_000;throw Error('DogeOS is limiting requests. Wallet reads are paused briefly; retry in 30 seconds.');}
        await wait((attempt+1)*1000);
      }
    }
  }).finally(()=>state.pending.delete(key));
  state.pending.set(key,run);state.tail=run.then(()=>{},()=>{});return run;
}

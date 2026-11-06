import type { NativeProvider } from './nativeVault';
import { NATIVE_CHAIN } from './nativeVault';
import { walletRead } from './walletRpc';
export type WalletBalanceState={balanceWei:string|null;loading:boolean;error:string;at:number|null};
/** A wallet balance read must not wait for the vault/reward-pool observation. */
export class WalletBalanceStore {
  private value:WalletBalanceState={balanceWei:null,loading:false,error:'',at:null};
  private listeners=new Set<()=>void>();
  private generation=0;
  private pending:Promise<void>|null=null;
  constructor(private provider:NativeProvider,private owner:string) {}
  snapshot=()=>this.value;
  subscribe=(fn:()=>void)=>{this.listeners.add(fn);return()=>{this.listeners.delete(fn);};};
  private set(patch:Partial<WalletBalanceState>){this.value={...this.value,...patch};this.listeners.forEach(fn=>fn());}
  refresh=():Promise<void>=>{
    if(this.pending)return this.pending;
    const generation=this.generation;this.set({loading:true,error:''});
    let timer:ReturnType<typeof setTimeout>;
    const read=async()=>{
      const [chain,accounts,balance]=await Promise.all([
        this.provider.request({method:'eth_chainId'}),this.provider.request({method:'eth_accounts'}),
        walletRead(this.provider,{method:'eth_getBalance',params:[this.owner,'latest']}),
      ]);
      if(BigInt(chain as string)!==BigInt(NATIVE_CHAIN)||!Array.isArray(accounts)||accounts[0]?.toLowerCase()!==this.owner)throw Error('Reconnect the correct wallet on DogeOS testnet.');
      if(typeof balance!=='string'||!/^0x[0-9a-f]+$/i.test(balance))throw Error('Wallet returned an invalid balance.');
      return BigInt(balance).toString();
    };
    this.pending=(async()=>{
      try {
        const balanceWei=await Promise.race([read(),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Wallet balance timed out. Refresh to retry.')),8000);})]);
        if(generation===this.generation)this.set({balanceWei,at:Date.now(),error:''});
      }catch(e){if(generation===this.generation)this.set({balanceWei:null,at:null,error:e instanceof Error?e.message:'Could not read your wallet balance.'});}
      finally{clearTimeout(timer!);if(generation===this.generation){this.pending=null;this.set({loading:false});}}
    })();
    return this.pending;
  };
  stop(){this.generation++;this.pending=null;this.set({balanceWei:null,loading:false,error:'',at:null});}
}

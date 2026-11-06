import { createContext, useContext } from 'react';
import type { WalletBalanceState } from '@/lib/vela/walletBalance';
export const WalletBalanceContext=createContext<(WalletBalanceState&{refresh:()=>Promise<void>})|null>(null);
export function useWalletBalance(fallback?:string) {
  const state=useContext(WalletBalanceContext);
  return state??{balanceWei:fallback??null,loading:false,error:'',at:null,refresh:async()=>{}};
}

/** Real read-only wallet/network/gas state. No writes, no signatures. */
import { useAccount, useBalance } from 'wagmi';
import { TARGET_CHAIN } from '@/lib/config';

export type GasState = 'disconnected' | 'wrong-network' | 'loading' | 'empty' | 'ok' | 'error';

export function useWalletGas(fixtureGasEmpty = false) {
  const { address, isConnected, chainId } = useAccount();
  const bal = useBalance({ address, chainId: TARGET_CHAIN.id, query: { enabled: !!address } });
  let state: GasState = 'ok';
  if (!isConnected) state = 'disconnected';
  else if (chainId !== TARGET_CHAIN.id) state = 'wrong-network';
  else if (bal.isLoading) state = 'loading';
  else if (bal.isError) state = 'error';
  else if ((bal.data?.value ?? 0n) === 0n) state = 'empty';
  return { address, isConnected, chainId, balanceWei: bal.data?.value ?? null, state, fixtureGasEmpty };
}

export const GAS_TEXT: Record<GasState, string> = {
  disconnected: 'Connect a wallet to check network and gas.',
  'wrong-network': `Wrong network — switch to ${TARGET_CHAIN.name}.`,
  loading: 'Reading balance…',
  empty: 'Insufficient gas — fund this wallet from the DogeOS testnet faucet.',
  ok: 'Gas available for testnet transactions.',
  error: 'Could not read balance from the RPC.',
};

import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { Row } from '@/components/common/Metric';
import { SourceTag } from '@/components/common/SourceTag';
import { GAS_TEXT, useWalletGas } from '@/hooks/useWalletGas';
import { TARGET_CHAIN } from '@/lib/config';
import { fmtDoge } from '@/lib/units';
import { usePreviewMode } from '@/state/PreviewMode';

/** Real wallet + network reads (read-only). */
export function WalletCard() {
  const preview = usePreviewMode();
  if (preview) return <Panel eyebrow="Wallet & network" title="Preview only" aside={<SourceTag kind="off" label="NO CONNECTION" />}>
    <Row k="Wallet" v="Not connected" />
    <Details label="Network details">
      <Row k="Target network" v={`${TARGET_CHAIN.name} · ${TARGET_CHAIN.id}`} />
      <p className="mt-2 text-xs text-muted-foreground">This preview does not read your wallet, sign messages, or send transactions.</p>
    </Details>
  </Panel>;
  return <ConnectedWalletCard />;
}

function ConnectedWalletCard() {
  const w = useWalletGas();
  return (
    <Panel eyebrow="Wallet & network" title={w.isConnected ? 'Connected' : 'Not connected'} aside={<SourceTag kind={w.isConnected ? 'live' : 'off'} label={w.isConnected ? 'LIVE READ' : 'NO WALLET'} />}>
      <Row k="Target network" v={`${TARGET_CHAIN.name} · ${TARGET_CHAIN.id}`} />
      <Row k="Wallet network" v={w.chainId ?? '—'} tone={w.state === 'wrong-network' ? 'negative' : 'default'} />
      <Row k="Address" v={w.address ? `${w.address.slice(0, 6)}…${w.address.slice(-4)}` : '—'} />
      <Row k="Test DOGE balance" v={w.balanceWei !== null ? fmtDoge(w.balanceWei) : '—'} exact={w.balanceWei?.toString()} />
      <p className={`mt-2 text-xs ${w.state === 'ok' ? 'text-muted-foreground' : 'text-warning'}`}>{GAS_TEXT[w.state]}</p>
    </Panel>
  );
}

import { ExternalLink } from 'lucide-react';
import { SourceTag } from '@/components/common/SourceTag';
import { TARGET_CHAIN } from '@/lib/config';
import { DOGE_DECIMALS, formatUnits } from '@/lib/units';
import type { RewardClaimReceipt } from '@/lib/vela/types';
import { useFixture } from '@/state/FixtureProvider';

/** Testnet reward-claim records only. Explorer link appears only for a real hash. */
export function ClaimReceipts() {
  const { state } = useFixture();
  const rows = state.receipts.filter((r): r is RewardClaimReceipt => r.kind === 'testnet-reward-claim');
  if (!rows.length) return <p className="mt-4 text-xs text-muted-foreground">No reward claim records yet.</p>;
  return (
    <ul className="mt-4 space-y-2">
      {rows.map((r) => (
        <li key={r.id} className="rounded-md border border-border/70 p-2.5 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="num text-sm">{formatUnits(BigInt(r.amount.units), DOGE_DECIMALS)} DOGE</span>
            <SourceTag kind={r.source === 'fixture' ? 'fixture' : 'live'} label={r.status === 'fixture-rehearsal' ? 'FIXTURE REHEARSAL' : r.status.toUpperCase()} />
          </div>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">exact: {r.amount.units} wei · confirmations {r.confirmations}/{r.requiredConfirmations}</p>
          {r.txHash ? (
            <a className="mt-1 inline-flex items-center gap-1 text-primary hover:underline" href={`${TARGET_CHAIN.blockExplorers.default.url}/tx/${r.txHash}`} target="_blank" rel="noreferrer">
              {r.txHash.slice(0, 12)}… <ExternalLink className="h-3 w-3" />
            </a>
          ) : (
            <p className="mt-1 text-muted-foreground">No transaction sent — no hash or explorer receipt exists.</p>
          )}
        </li>
      ))}
    </ul>
  );
}

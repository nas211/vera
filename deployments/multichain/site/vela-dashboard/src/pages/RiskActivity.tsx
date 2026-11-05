import { Panel } from '@/components/common/Panel';
import { SourceTag } from '@/components/common/SourceTag';
import { RiskSummary } from '@/components/risk/RiskSummary';
import { StressPanel } from '@/components/risk/StressPanel';
import { RiskActionsPanel } from '@/components/risk/RiskActionsPanel';
import { RepaymentHistory } from '@/components/risk/RepaymentHistory';
import { ClaimReceipts } from '@/components/vault/ClaimReceipts';
import { useFixture } from '@/state/FixtureProvider';

const t = (s: number) => new Date(s * 1000).toISOString().slice(0, 19).replace('T', ' ');

export default function RiskActivity() {
  const { state, view } = useFixture();
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-7">
        <Panel eyebrow="Now" title="Current risk" aside={<SourceTag kind="fixture" />}><RiskSummary risk={view.risk} /></Panel>
        <StressPanel />
        <RiskActionsPanel />
      </div>
      <div className="space-y-4 lg:col-span-5">
        <Panel eyebrow="Testnet reward claim receipts" title="Reward claims" aside={<SourceTag kind="testnet" />}><ClaimReceipts /></Panel>
        <RepaymentHistory />
        <Panel eyebrow="Append-only" title="Activity log" aside={<SourceTag kind="fixture" />}>
          <ol className="max-h-96 space-y-1.5 overflow-y-auto pr-1 text-xs">
            {state.activity.map((a) => (
              <li key={a.id} className="grid grid-cols-[auto_1fr] gap-x-2 border-b border-border/50 pb-1.5">
                <span className="num text-muted-foreground">{t(a.at)}</span>
                <span><span className="mr-1.5 font-mono text-[10px] uppercase text-primary">{a.kind}</span>{a.text}</span>
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </div>
  );
}

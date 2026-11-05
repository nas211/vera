import { SearchX } from 'lucide-react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';

/** The real public-paper result, reported separately from fixture rehearsals. */
export function ActualResultPanel() {
  return (
    <Panel eyebrow="Actual public-paper result" title="No fills observed" aside={<SourceTag kind="unavailable" label="UNVERIFIED" />} className="border-destructive/20">
      <div className="flex gap-3 text-sm">
        <SearchX className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="space-y-2">
          <p>Live trading is not connected. Results shown here use sample data.</p>
          <Details label="Verification details">
          <p>The earlier public-paper check recorded <span className="num">0</span> observed fills.</p>
          <p className="text-muted-foreground">Browser opening, protective exits, closing, fees, P&amp;L and settlement remain unverified. The fixture fills on this page are a labeled deterministic rehearsal and are not evidence that those live flows work.</p>
          <p className="text-muted-foreground">Live Vela trading data requires the Stage 4 adapter (integration currently unavailable).</p>
          </Details>
        </div>
      </div>
    </Panel>
  );
}

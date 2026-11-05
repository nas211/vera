import { useState } from 'react';
import { Clock, RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/common/Panel';
import { SourceTag } from '@/components/common/SourceTag';
import { useFixture } from '@/state/FixtureProvider';

const fmtClock = (s: number) => new Date(s * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';

/** Rehearsal controls for the deterministic fixture clock and scenarios. */
export function FixtureControls() {
  const { state, act } = useFixture();
  const [err, setErr] = useState<string | null>(null);
  const run = (a: Parameters<typeof act>[0]) => setErr(act(a));
  return (
    <Panel eyebrow="Rehearsal" title="Fixture scenario controls" aside={<SourceTag kind="fixture" />} className="border-fixture/20">
      <p className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Clock className="h-3.5 w-3.5" /> Fixture clock <span className="num text-foreground">{fmtClock(state.clock)}</span>
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" onClick={() => run({ type: 'ADVANCE', seconds: 3600 })}>+1 hour</Button>
        <Button size="sm" variant="secondary" onClick={() => run({ type: 'ADVANCE', seconds: 86_400 })}>+1 day</Button>
        <Button size="sm" variant="secondary" onClick={() => run({ type: 'ADVANCE', seconds: 30 * 86_400 })}>+30 days (exhaust reserve)</Button>
        <Button size="sm" variant={state.priceStale ? 'destructive' : 'secondary'} onClick={() => run({ type: 'TOGGLE_STALE' })}>
          {state.priceStale ? 'Restore fresh price' : 'Make price stale'}
        </Button>
        <Button size="sm" variant={state.gasEmpty ? 'destructive' : 'secondary'} onClick={() => run({ type: 'TOGGLE_GAS' })}>
          {state.gasEmpty ? 'Restore fixture gas' : 'Simulate zero gas'}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => run({ type: 'RESET' })} className="gap-1"><RefreshCcw className="h-3.5 w-3.5" />Reset fixture</Button>
      </div>
      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
    </Panel>
  );
}

import { useState } from 'react';
import { CirclePause, CirclePlay, OctagonAlert, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { useFixture } from '@/state/FixtureProvider';
import { AuthorizeTradingDialog } from './AuthorizeTradingDialog';
import { cn } from '@/lib/utils';

/** Main status card (regressions 2 & 5): reason + recovery action always visible. */
export function TradingStatusCard({ compact = false }: { compact?: boolean }) {
  const { state, view, act } = useFixture();
  const [err, setErr] = useState<string | null>(null);
  const s = state.session;
  const running = s.status === 'running';
  const needsAuth = !s.authorization || s.entriesPaused;
  const headline = !running ? 'AI stopped' : needsAuth ? 'Running — new entries paused' : view.entriesBlockedBy ? 'Running — new entries blocked' : 'Running';
  const reason = !running ? s.reason : needsAuth ? s.reason ?? 'No demo trading authorization' : view.entriesBlockedBy;
  const recovery = !running ? s.recovery : needsAuth ? s.recovery ?? 'Grant demo trading authorization' : view.entriesBlockedBy ? 'Restore a fresh price / improve health factor' : null;

  return (
    <Panel eyebrow="Demo trading session" title={headline} aside={<SourceTag kind="fixture" />}
      className={cn(!running ? 'border-destructive/30' : needsAuth || view.entriesBlockedBy ? 'border-warning/30' : 'border-primary/25')}>
      {reason && (
        <div className="mb-3 flex gap-2 rounded-md bg-muted/60 p-3 text-sm">
          <OctagonAlert className={cn('mt-0.5 h-4 w-4 shrink-0', running ? 'text-warning' : 'text-destructive')} />
          <div><p><span className="text-muted-foreground">Reason: </span>{reason}</p>
            {recovery && <p className="mt-1"><span className="text-muted-foreground">To resume: </span>{recovery}</p>}</div>
        </div>
      )}
      <p className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground">
        <ShieldCheck className="h-3.5 w-3.5 text-primary" /> Sample stops remain active when entries stop.
      </p>
      <div className="flex flex-wrap gap-2">
        {running ? (
          <Button size="sm" variant="destructive" className="gap-1.5" onClick={() => setErr(act({ type: 'STOP', reason: 'Stopped by user', recovery: 'Press Resume (requires valid demo authorization)' }))}>
            <CirclePause className="h-4 w-4" />Stop new entries
          </Button>
        ) : (
          <Button size="sm" className="gap-1.5" disabled={!s.authorization} onClick={() => setErr(act({ type: 'START' }))}>
            <CirclePlay className="h-4 w-4" />Resume
          </Button>
        )}
        {needsAuth && <AuthorizeTradingDialog />}
      </div>
      {!running && !s.authorization && <p className="mt-1 text-[11px] text-muted-foreground">Resume is disabled until a new demo authorization is granted.</p>}
      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
      {!compact && (
        <Details label="Session details"><p className="text-[11px] text-muted-foreground">
          Status seq <span className="num">{s.seq}</span> · stale poll responses ignored: <span className="num">{s.ignoredPolls}</span>
        </p></Details>
      )}
    </Panel>
  );
}

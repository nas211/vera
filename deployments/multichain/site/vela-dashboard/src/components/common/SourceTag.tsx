import { cn } from '@/lib/utils';
import { createContext, useContext } from 'react';

export const ModeNoticeContext = createContext(false);

type Kind = 'fixture' | 'live' | 'unavailable' | 'demo' | 'testnet' | 'off';

const STYLES: Record<Kind, string> = {
  fixture: 'border-border/70 bg-muted/40 text-muted-foreground',
  live: 'border-primary/40 bg-primary/10 text-primary',
  unavailable: 'border-destructive/40 bg-destructive/10 text-destructive',
  demo: 'border-border/70 bg-muted/40 text-muted-foreground',
  testnet: 'border-primary/30 bg-accent text-accent-foreground',
  off: 'border-border bg-muted text-muted-foreground',
};
const LABEL: Record<Kind, string> = {
  fixture: 'FIXTURE', live: 'LIVE', unavailable: 'UNAVAILABLE', demo: 'DEMO USD', testnet: 'TESTNET DOGE', off: 'WRITES OFF',
};

/** A page-level mode notice replaces repeated mode badges in the connected app. */
export function SourceTag({ kind, label, className }: { kind: Kind; label?: string; className?: string }) {
  const modeExplained = useContext(ModeNoticeContext);
  if (modeExplained && ['fixture', 'demo', 'testnet'].includes(kind)) return null;
  return (
    <span className={cn('inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium tracking-wider', STYLES[kind], className)}>
      {label ?? LABEL[kind]}
    </span>
  );
}

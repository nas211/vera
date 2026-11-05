import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

type Tone = 'default' | 'positive' | 'negative' | 'warning' | 'muted';
const TONE: Record<Tone, string> = {
  default: 'text-foreground', positive: 'text-success', negative: 'text-destructive', warning: 'text-warning', muted: 'text-muted-foreground',
};

type Props = { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; exact?: string; className?: string };

/** Label + large number. `exact` exposes the full base-unit value on hover/focus. */
export function Metric({ label, value, sub, tone = 'default', exact, className }: Props) {
  return (
    <div className={cn('min-w-0', className)}>
      <p className="eyebrow mb-1.5">{label}</p>
      <p className={cn('num truncate text-lg font-medium sm:text-xl', TONE[tone])} title={exact}>{value}</p>
      {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

export function Row({ k, v, exact, tone = 'default' }: { k: ReactNode; v: ReactNode; exact?: string; tone?: Tone }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-2 text-sm last:border-0">
      <span className="min-w-0 flex-1 text-muted-foreground">{k}</span>
      <span className={cn('num min-w-0 flex-1 text-right [overflow-wrap:anywhere]', TONE[tone])} title={exact}>{v}</span>
    </div>
  );
}

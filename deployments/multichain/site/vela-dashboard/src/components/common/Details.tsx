import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Secondary information stays available without crowding the main action. */
export function Details({ children, label = 'Details', className }: { children: ReactNode; label?: string; className?: string }) {
  return <details className={cn('group/details mt-4 border-t border-border/60 pt-3', className)}>
    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-sm text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
      {label}<ChevronDown aria-hidden className="h-3.5 w-3.5 shrink-0 transition-transform group-open/details:rotate-180" />
    </summary>
    <div className="pt-3">{children}</div>
  </details>;
}

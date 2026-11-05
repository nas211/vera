import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

type Props = {
  title?: string;
  eyebrow?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  delay?: number;
};

/** Glass panel used across every screen. */
export function Panel({ title, eyebrow, aside, children, className, delay = 0 }: Props) {
  return (
    <section className={cn('glass relative rounded-lg p-5 sm:p-6 animate-rise', className)} style={{ animationDelay: `${delay}ms` }}>
      {(title || eyebrow || aside) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            {eyebrow && <p className={title ? 'sr-only' : 'eyebrow mb-1'}>{eyebrow}</p>}
            {title && <h2 className="font-display text-base font-semibold tracking-tight sm:text-lg">{title}</h2>}
          </div>
          {aside && <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

import type { ReactNode } from 'react';
import { Lock } from 'lucide-react';
import { Button, type ButtonProps } from '@/components/ui/button';
import { cn } from '@/lib/utils';

type Props = ButtonProps & { reason?: string | null; children: ReactNode };

/** Button that, when disabled, always states WHY directly beneath it. */
export function GatedButton({ reason, children, className, disabled, ...rest }: Props) {
  const blocked = !!reason || disabled;
  return (
    <div className="space-y-1">
      <Button {...rest} disabled={blocked} className={cn('interactive w-full', className)}>
        {reason && <Lock className="mr-1.5 h-3.5 w-3.5" aria-hidden />}
        {children}
      </Button>
      {reason && <p className="text-[11px] leading-snug text-muted-foreground">{reason}</p>}
    </div>
  );
}

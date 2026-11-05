import { useId } from 'react';
import { Input } from '@/components/ui/input';
import { validateAmount, type AmountRules } from '@/lib/units';
import { cn } from '@/lib/utils';

type Props = {
  label: string;
  unit: string;
  value: string;
  onChange: (v: string) => void;
  rules: AmountRules;
  onMax?: () => void;
  maxLabel?: string;
  hint?: string;
};

/** Validated amount input (regression 4). Parent should call validateAmount to gate Confirm. */
export function AmountField({ label, unit, value, onChange, rules, onMax, maxLabel = 'Max', hint }: Props) {
  const id = useId();
  const touched = value !== '';
  const result = validateAmount(value, rules);
  const error = touched && !result.ok ? result.error : null;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="eyebrow">{label}</label>
      <div className="relative">
        <Input
          id={id} inputMode="decimal" autoComplete="off" value={value} placeholder="0.00"
          onChange={(e) => onChange(e.target.value)} aria-invalid={!!error} aria-describedby={`${id}-msg`}
          className={cn('num h-11 bg-background/60 pr-28 text-base', error && 'border-destructive focus-visible:ring-destructive')}
        />
        <div className="absolute inset-y-0 right-2 flex items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">{unit}</span>
          {onMax && (
            <button type="button" onClick={onMax} className="interactive rounded border border-primary/30 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-primary hover:bg-primary/10">
              {maxLabel}
            </button>
          )}
        </div>
      </div>
      <p id={`${id}-msg`} className={cn('min-h-4 text-xs', error ? 'text-destructive' : 'text-muted-foreground')}>
        {error ?? hint ?? ''}
      </p>
    </div>
  );
}

export const isValidAmount = (v: string, rules: AmountRules) => validateAmount(v, rules).ok;

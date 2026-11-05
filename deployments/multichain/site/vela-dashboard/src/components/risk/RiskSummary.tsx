import { AlertTriangle } from 'lucide-react';
import { Metric } from '@/components/common/Metric';
import { fmtBpsPct, fmtBpsRatio, type RiskResult } from '@/lib/riskMath';
import { fmtUsd } from '@/lib/units';

const STATUS_TEXT: Record<RiskResult['status'], string> = {
  healthy: 'Healthy', 'reduce-entries': 'Entry size reduced (HF < 1.30)', 'block-entries': 'New entries blocked (HF < 1.15)',
  'threshold-breach': 'Liquidation threshold BREACHED — not an executed liquidation', 'no-debt': 'No debt',
  'critical-no-collateral': 'CRITICAL — debt with no collateral', unavailable: 'Risk unavailable',
};

/** LTV and HF readout that never shows "0" or "healthy" when it isn't. */
export function RiskSummary({ risk }: { risk: RiskResult }) {
  const ltv = 'ltvBps' in risk ? fmtBpsPct(risk.ltvBps) : risk.status === 'no-debt' ? '0.00%' : '—';
  const hf = 'hfBps' in risk ? fmtBpsRatio(risk.hfBps) : risk.status === 'no-debt' ? 'N/A' : '—';
  const tone = risk.status === 'healthy' || risk.status === 'no-debt' ? 'positive' : risk.status === 'reduce-entries' ? 'warning' : 'negative';
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-4">
        <Metric label="Current LTV" value={ltv} />
        <Metric label="Health factor" value={hf} tone={tone} />
      </div>
      <div className={`flex items-start gap-2 rounded-md p-2.5 text-xs ${tone === 'positive' ? 'bg-accent text-accent-foreground' : tone === 'warning' ? 'bg-warning/10 text-warning' : 'bg-destructive/10 text-destructive'}`}>
        {tone !== 'positive' && <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
        <span>{STATUS_TEXT[risk.status]}{risk.status === 'unavailable' ? ` — ${risk.reason}. Borrowing and new entries blocked.` : ''}
          {risk.status === 'critical-no-collateral' ? ` Debt ${fmtUsd(risk.debtUsd6)}.` : ''}</span>
      </div>
    </div>
  );
}

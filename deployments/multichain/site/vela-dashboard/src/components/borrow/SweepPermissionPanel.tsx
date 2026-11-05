import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { Row } from '@/components/common/Metric';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { permissionProblem, sweepGrantProblem } from '@/lib/sweepLedger';
import { USD_DECIMALS, fmtSignedUsd, fmtUsd, validateAmount } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

const intIn = (v: string, lo: number, hi: number) => /^\d+$/.test(v) && +v >= lo && +v <= hi;

export function SweepPermissionPanel() {
  const { state, act } = useFixture();
  const [pct, setPct] = useState('');
  const [days, setDays] = useState('7');
  const [floor, setFloor] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const floorRules = { inputDecimals: 2, decimals: USD_DECIMALS, max: 1_000_000_000n, maxLabel: 'limit' };
  const floorParsed = floor === '' ? { ok: true as const, value: 0n } : validateAmount(floor, floorRules);
  const toInt = (v: string) => (/^\d+$/.test(v) ? Number(v) : Number.NaN);
  // Same validator the engine enforces: finite integer days 1–30, non-negative floor.
  const valid = floorParsed.ok && sweepGrantProblem({ percent: toInt(pct), days: toInt(days), floorUsd6: floorParsed.value }) === null;
  const p = state.permission;
  const problem = state.loan ? permissionProblem(p, state.loan.loanId, state.clock) : 'No active loan';
  const l = state.sweep;

  const grant = () => { if (valid && floorParsed.ok) setMsg(act({ type: 'GRANT_SWEEP', percent: toInt(pct), days: toInt(days), floorUsd6: floorParsed.value }) ?? 'Permission granted. Only profit realized after now is eligible.'); };

  return (
    <Panel eyebrow="Explicit & revocable" title="Profit-sweep permission">
      <p className="mb-3 text-sm text-muted-foreground">Use future realized net profit to repay your demo loan.</p>
      <Row k="Status" v={problem ?? `Active · ${p!.percent}% · floor ${fmtUsd(p!.floorUsd6)}`} tone={problem ? 'muted' : 'positive'} />
      <Row k="Total swept" v={fmtUsd(l.sweptTotalUsd6)} />
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" onClick={() => setMsg(act({ type: 'RUN_SWEEP' }) ?? 'Sweep evaluated — see activity log.')}>Preview repayment</Button>
        <Button size="sm" variant="outline" disabled={!p || p.revokedAt !== null} onClick={() => setMsg(act({ type: 'REVOKE_SWEEP' }) ?? 'Permission revoked.')}>Revoke</Button>
      </div>
      {msg && <p className="mt-2 text-xs text-fixture">{msg}</p>}
      <Details label="Permission settings & breakdown">
      <p className="mb-3 text-xs text-muted-foreground">Sweeps only NEW realized net profit (after entry/exit fees and funding). Never unrealized gains, borrowed capital, deposits or DOGE rewards. Interest first, then principal. Carried payments pause until trading losses are recovered and require their original percentage under valid consent. Stops at zero debt.</p>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5"><label className="eyebrow" htmlFor="sw-pct">Percent (0–100)</label>
          <Input id="sw-pct" inputMode="numeric" value={pct} onChange={(e) => setPct(e.target.value)} className="num" aria-invalid={pct !== '' && !intIn(pct, 0, 100)} />
          {pct !== '' && !intIn(pct, 0, 100) && <p className="text-xs text-destructive">Whole number 0–100.</p>}</div>
        <div className="space-y-1.5"><label className="eyebrow" htmlFor="sw-days">Expires in days (1–30)</label>
          <Input id="sw-days" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} className="num" aria-invalid={!intIn(days, 1, 30)} />
          {!intIn(days, 1, 30) && <p className="text-xs text-destructive">Whole number 1–30.</p>}</div>
      </div>
      <div className="mt-3"><AmountField label="Keep at least (optional floor)" unit="USD" value={floor} onChange={setFloor} rules={floorRules} hint="Leave blank for no floor." /></div>
      <GatedButton onClick={grant} reason={!state.loan ? 'No active demo loan to authorize sweeps for.' : valid ? null : 'Complete the fields above.'}>Grant sweep permission for {state.loan?.loanId ?? '—'}</GatedButton>
      <div className="mt-4">
        <Row k="Cumulative realized net (this loan)" v={fmtSignedUsd(l.cumulativeNetUsd6)} />
        <Row k="Already evaluated (high-water)" v={fmtUsd(l.highWaterUsd6)} />
        <Row k="Unpaid entitlement (carried)" v={fmtUsd(l.owedUsd6, 'up')} exact={`${l.owedUsd6} µUSD`} tone={l.owedUsd6 > 0n ? 'warning' : 'muted'} />
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">Scheduled repayments that could use principal are not enabled; they would require a separate disclosure and authorization.</p>
      </Details>
    </Panel>
  );
}

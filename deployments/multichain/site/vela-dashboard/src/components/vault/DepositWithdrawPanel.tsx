import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DOGE_DECIMALS, formatUnits, validateAmount } from '@/lib/units';
import { onChainBlockReason } from '@/lib/vela/writeGate';
import { useFixture } from '@/state/FixtureProvider';

export function DepositWithdrawPanel() {
  const { view, act } = useFixture();
  const [mode, setMode] = useState<'deposit' | 'withdraw'>('deposit');
  const [amt, setAmt] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const max = mode === 'withdraw' ? view.user.principal : 1_000_000n * 10n ** 18n;
  const rules = { inputDecimals: DOGE_DECIMALS, decimals: DOGE_DECIMALS, max, maxLabel: mode === 'withdraw' ? 'your principal' : 'fixture deposit cap' };
  const parsed = validateAmount(amt, rules);

  const rehearse = () => {
    if (!parsed.ok) return;
    const err = act(mode === 'deposit' ? { type: 'DEPOSIT', wei: parsed.value } : { type: 'WITHDRAW', wei: parsed.value });
    setMsg(err ? { ok: false, text: err } : { ok: true, text: `Fixture ${mode} recorded (checkpointed first). No transaction was sent.` });
    if (!err) setAmt('');
  };

  return (
    <Panel eyebrow="Principal" title="Deposit / withdraw" aside={<SourceTag kind="testnet" />}>
      <Tabs value={mode} onValueChange={(v) => { setMode(v as typeof mode); setAmt(''); setMsg(null); }} className="mb-4">
        <TabsList className="grid w-full grid-cols-2"><TabsTrigger value="deposit">Deposit</TabsTrigger><TabsTrigger value="withdraw">Withdraw</TabsTrigger></TabsList>
      </Tabs>
      <AmountField label={`${mode} amount`} unit="DOGE" value={amt} onChange={setAmt} rules={rules}
        onMax={mode === 'withdraw' ? () => setAmt(formatUnits(view.user.principal, DOGE_DECIMALS)) : undefined}
        hint={mode === 'withdraw' ? 'Withdrawal never touches the reward reserve.' : 'Deposits never fund rewards.'} />
      <div className="mt-2">
        <GatedButton onClick={rehearse} reason={parsed.ok ? null : 'Enter an amount.'}>Preview {mode}</GatedButton>
      </div>
      {msg && <p className={`mt-2 text-xs ${msg.ok ? 'text-fixture' : 'text-destructive'}`}>{msg.text}</p>}
      <Details label="On-chain availability"><GatedButton reason={onChainBlockReason()}>{mode === 'deposit' ? 'Deposit on-chain' : 'Withdraw on-chain'}</GatedButton></Details>
    </Panel>
  );
}

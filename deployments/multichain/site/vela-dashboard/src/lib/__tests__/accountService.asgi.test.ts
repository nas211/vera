/** Real adapter → real ASGI routes → isolated existing Vela ledger. No browser or network. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { parseDecimal } from '../units';
const units = (s: string) => { const r = parseDecimal(s, 18); if (!r.ok) throw Error(r.error); return r.value; };
import { AccountService, type ServiceAction } from '../vela/accountService';
import { DashboardController } from '../vela/dashboardController';

it.each(['long', 'short'])('existing ledger flow with a %s position and repeat-safe requests', async side => {
  const root = fileURLToPath(new URL('../../../../../', import.meta.url));
  const child = spawn(resolve(root, '.venv/bin/python'), [resolve(root, 'verification/noah/vela-asgi-driver.py'), side], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
  const timeout = setTimeout(() => child.kill(), 25_000);
  const lines = createInterface({ input: child.stdout });
  const iterator = lines[Symbol.asyncIterator]();
  let stderr = ''; child.stderr.on('data', c => { stderr += String(c); });
  const next = async () => {
    const line = await iterator.next();
    if (line.done) throw new Error(`ASGI fixture stopped: ${stderr}`);
    return JSON.parse(line.value);
  };
  const send = async (payload: unknown) => { child.stdin.write(JSON.stringify(payload) + '\n'); return next(); };
  try {
    const ready = await next(); expect(ready.ready).toBe(true);
    const context = { address: ready.address as string, network: 'dogeos' as const, generation: 1 };
    let response: unknown;
    const service = new AccountService(async request => {
      const { signal, ...payload } = request;
      if (signal.aborted) throw new Error('Aborted');
      const result = await send(payload);
      if (result.status !== 200) throw new Error(`${result.status}: ${JSON.stringify(result.body)}`);
      if (request.body) response = result.body;
      return result.body;
    });
    service.setSession(context);
    const initial = await service.refresh();
    expect(initial.paper.positions).toHaveLength(1);
    expect(initial.paper.positions[0].side).toBe(side);
    expect(initial.paper.mode).toBe('PAPER');
    let currentKey = '';
    const dashboard = new DashboardController(service, null, () => currentKey);
    const apply = async (action: ServiceAction, key: string) => {
      currentKey = key; await dashboard.reviewLedger(action);
      const review = dashboard.snapshot().review;
      expect(review?.kind).toBe('ledger');
      if (review?.kind !== 'ledger') throw Error(dashboard.snapshot().error);
      await dashboard.confirm(); expect(dashboard.snapshot().error).toBe('');
      expect(dashboard.snapshot().account).not.toBeNull();
      return { response, reviewed: review.request };
    };
    const payment = await apply({ kind: 'repay', amount: '1' }, `asgi-repay-${side}`);
    await service.execute(payment.reviewed, true); // repeat exact key against store.once
    const after = await service.refresh();
    expect(units(initial.repayment.debt) - units(after.repayment.debt)).toBe(10n ** 18n);
    expect(after.events.filter(e => e.kind === 'SIMULATED_REPAYMENT_RECEIPT')).toHaveLength(1);
    await apply({ kind: 'sweep', percent: 25, days: 7, floor: '5' }, `asgi-sweep-${side}`);
    const withConsent = await service.refresh();
    expect(withConsent.repayment.policy).toMatchObject({ sweepPercent: '25', minimum: '5', frequency: 'off', loanId: initial.health!.loan!.id });
    await apply({ kind: 'revokeSweep' }, `asgi-revoke-${side}`);
    await apply({ kind: 'permission', allocation: '100', positionSize: '50', maxLoss: '0.5' }, `asgi-permission-${side}`);
    await apply({ kind: 'permission', allocation: '100', positionSize: '50', maxLoss: '10' }, `asgi-retain-limit-${side}`);
    expect(units((await service.refresh()).tradingPermission.maxLoss!)).toBe(500000n * 10n ** 18n);
    await apply({ kind: 'stop' }, `asgi-stop-${side}`);
    const close = await apply({ kind: 'close', positionId: initial.paper.positions[0].id, fraction: '0.5' }, `asgi-close-${side}`);
    await service.execute(close.reviewed, true);
    await send({ control: 'tick' });
    const closed = await service.refresh();
    expect(closed.paper.preferences.autonomous).toBe(false);
    expect(units(closed.paper.positions[0].qty) < units(initial.paper.positions[0].qty)).toBe(true);
    expect(closed.paper.today.trades).toBe(1);
    expect(closed.paper.totals.trades).toBe(1);
    expect(closed.paper.orders.filter(o => o.id === (close.response as { id: string }).id)).toHaveLength(1);
    const foreign = await send({ path: '/api/account', context: { ...context, address: `0x${'22'.repeat(20)}` } });
    expect(foreign.status).toBe(403);
  } finally {
    child.stdin.end();
    await new Promise<void>(resolve => { if (child.exitCode !== null) resolve(); else child.once('exit', () => resolve()); });
    clearTimeout(timeout); lines.close();
  }
}, 30_000);

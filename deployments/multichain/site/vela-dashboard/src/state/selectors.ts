import * as RM from '@/lib/rewardModel';
import { assessRisk, borrowCapacity, entryPolicy, quoteAge } from '@/lib/riskMath';
import { cashBreakdown } from '@/lib/cashReserve';
import { netOf } from '@/lib/sweepLedger';
import { FIXTURE_CHAIN_ID, FIXTURE_USER, entryBlockReason, paperCashFor, riskInputFor, unrealizedUsd6, type FixtureState } from '@/lib/vela/fixtureEngine';

export type View = ReturnType<typeof deriveView>;

export function deriveView(s: FixtureState) {
  const vaultNow = RM.checkpoint(s.vault, { caller: FIXTURE_USER, chainId: FIXTURE_CHAIN_ID, now: s.clock });
  const user = vaultNow.users[FIXTURE_USER] ?? { principal: 0n, owed: 0n, claimed: 0n, rewardDebt: 0n };
  const claimable = RM.pendingFor(s.vault, FIXTURE_USER, s.clock);
  const reserveAvailable = vaultNow.reserveBalance - vaultNow.liabilities;
  const runway = RM.runwaySeconds(s.vault, s.clock);

  const riskInput = riskInputFor(s);
  const committedWei = riskInput.committedToSaleWei;
  const principalUsd6 = s.loan?.principalUsd6 ?? 0n;
  const interestUsd6 = s.loan?.interestUsd6 ?? 0n;
  const risk = assessRisk(riskInput);
  const capacity = borrowCapacity(riskInput);
  const policy = entryPolicy(risk);

  const dayStart = s.clock - (s.clock % 86_400);
  const todayRealized = s.fills.filter((f) => f.closedAt >= dayStart).reduce((t, f) => t + netOf(f), 0n);
  const allTimeRealized = s.fills.reduce((t, f) => t + netOf(f), 0n);
  const unrealized = s.positions.reduce((t, p) => t + unrealizedUsd6(p), 0n);
  const feesPaid = s.fills.reduce((t, f) => t + f.entryFeeUsd6 + f.exitFeeUsd6, 0n) + s.positions.reduce((t, p) => t + p.entryFeeUsd6, 0n);
  const fundingPaid = s.fills.reduce((t, f) => t + f.fundingUsd6, 0n) + s.positions.reduce((t, p) => t + p.fundingUsd6, 0n);

  const collateralLocked = !!s.loan || committedWei > 0n || s.badDebtUsd6 > 0n;
  // Same gate the engine enforces on OPEN_POSITION — never a separate UI-only formula.
  const entriesBlockedBy = entryBlockReason(s);
  // Same spendable definition the engine enforces on repayments and withdrawals.
  const cash = cashBreakdown(paperCashFor(s));

  return {
    vaultNow, user, claimable, reserveAvailable, runway, committedWei,
    principalUsd6, interestUsd6, debtUsd6: principalUsd6 + interestUsd6,
    risk, capacity, policy, quoteAgeSec: quoteAge(s.quote, s.clock),
    todayRealized, allTimeRealized, unrealized, feesPaid, fundingPaid,
    collateralLocked, entriesBlockedBy,
    spendableCashUsd6: cash.spendableUsd6, reservedCashUsd6: cash.reservedUsd6, cashShortfallUsd6: cash.shortfallUsd6,
  };
}

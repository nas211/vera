import { RewardTermsPanel } from '@/components/vault/RewardTermsPanel';
import { DepositWithdrawPanel } from '@/components/vault/DepositWithdrawPanel';
import { ClaimPanel } from '@/components/vault/ClaimPanel';
import { ReserveTopUpPanel } from '@/components/vault/ReserveTopUpPanel';

export default function VaultRewards() {
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-7">
        <RewardTermsPanel />
        <DepositWithdrawPanel />
      </div>
      <div className="space-y-4 lg:col-span-5">
        <ClaimPanel />
        <ReserveTopUpPanel />
      </div>
    </div>
  );
}

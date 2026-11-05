import { useMemo } from 'react';
import { Routes, Route } from 'react-router-dom';
import { Panel } from '@/components/common/Panel';
import { SourceTag } from '@/components/common/SourceTag';
import { Button } from '@/components/ui/button';
import NotFound from '@/pages/NotFound';
import type { AccountService } from '@/lib/vela/accountService';
import type { NativeVaultWorkflow } from '@/lib/vela/nativeVault';
import { DashboardController } from '@/lib/vela/dashboardController';
import { AccountProvider } from './AccountProvider';
import { useAccountDashboard } from './context';
import { AccountRisk, CollateralSummary, DebtSummary, ProfitSummary, AccountWallet, AccountConnections, AccountTradingStatus, AccountResult } from './AccountPanels';
import { AccountRewardTerms, AccountDepositWithdraw, AccountClaim, AccountReserve, AccountClaimReceipts } from './VaultPanels';
import { AccountLoanStatus, AccountDemoCash, AccountBorrow, AccountRepay, AccountSweep } from './LendingPanels';
import { AccountPositions, AccountFills, AccountStress, AccountRiskActions, AccountRepaymentHistory, AccountActivity } from './ActivityPanels';
import { AccountReview, PendingTransaction } from './ReviewDialog';

/** The approved five-screen layout, using only authoritative account and chain state. */
export function AccountDashboard({ service, native = null, controller: provided, refreshConfig }: { service: AccountService | null; native?: NativeVaultWorkflow | null; controller?: DashboardController; refreshConfig?: ()=>Promise<void> }) {
  const controller=useMemo(()=>provided??new DashboardController(service,native,undefined,refreshConfig),[provided,service,native,refreshConfig]);
  return <AccountProvider controller={controller}><AccountScreens /></AccountProvider>;
}
export function AccountScreens() {
  const {account,busy,refreshing,error,nativeError,notice,controller}=useAccountDashboard();
  return <>
    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><p>{!controller.service?'Connect your wallet to load your account':busy?'Processing action…':refreshing?'Updating account…':account?'Account updated':'Account unavailable'}</p><Button size="sm" variant="ghost" disabled={!controller.service||busy||refreshing} onClick={()=>void controller.refresh()}>Refresh</Button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}{nativeError && <p role="alert" className="text-sm text-warning">Native state unavailable: {nativeError}</p>}{notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    <PendingTransaction />
    <Routes>
      <Route path="/" element={<div className="grid gap-4 lg:grid-cols-12"><div className="space-y-4 lg:col-span-8"><CollateralSummary /><DebtSummary /><ProfitSummary /></div><div className="space-y-4 lg:col-span-4"><AccountTradingStatus compact /><AccountWallet /><AccountConnections /></div></div>} />
      <Route path="/vault" element={<div className="grid gap-4 lg:grid-cols-12"><div className="space-y-4 lg:col-span-7"><AccountRewardTerms /><AccountDepositWithdraw /></div><div className="space-y-4 lg:col-span-5"><AccountClaim /><AccountReserve /></div></div>} />
      <Route path="/borrow" element={<div className="grid gap-4 lg:grid-cols-12"><div className="space-y-4 lg:col-span-5"><AccountLoanStatus /><AccountDemoCash /></div><div className="space-y-4 lg:col-span-7"><div className="grid gap-4 md:grid-cols-2"><AccountBorrow /><AccountRepay /></div><AccountSweep /><AccountRepaymentHistory /></div></div>} />
      <Route path="/trading" element={<div className="grid gap-4 lg:grid-cols-12"><div className="space-y-4 lg:col-span-5"><AccountTradingStatus /><AccountResult /></div><div className="space-y-4 lg:col-span-7"><AccountPositions /><AccountFills /></div></div>} />
      <Route path="/risk" element={<div className="grid gap-4 lg:grid-cols-12"><div className="space-y-4 lg:col-span-7"><Panel title="Current risk" aside={<SourceTag kind="demo" />}><AccountRisk /></Panel><AccountStress /><AccountRiskActions /></div><div className="space-y-4 lg:col-span-5"><Panel title="Reward claims" aside={<SourceTag kind="testnet" />}><AccountClaimReceipts /></Panel><AccountRepaymentHistory /><AccountActivity /></div></div>} />
      <Route path="*" element={<NotFound />} />
    </Routes>
    <AccountReview />
  </>;
}

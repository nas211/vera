import { useMemo } from 'react';
import { Routes, Route } from 'react-router-dom';
import { Panel } from '@/components/common/Panel';
import { Button } from '@/components/ui/button';
import NotFound from '@/pages/NotFound';
import type { AccountService } from '@/lib/vela/accountService';
import type { NativeVaultWorkflow } from '@/lib/vela/nativeVault';
import { DashboardController } from '@/lib/vela/dashboardController';
import { AccountProvider } from './AccountProvider';
import { useAccountDashboard } from './context';
import { AccountTradingStatus } from './AccountPanels';
import { Metric } from '@/components/common/Metric';
import { usd } from './presentation';
import { AccountSettings } from './ProfilePreferences';
import { BorrowPolicyContext } from './BorrowSetup';
import type { BorrowPolicy } from '@/lib/vela/borrowSetup';
import { AccountRewardTerms, AccountDepositWithdraw, AccountClaim } from './VaultPanels';
import { AccountLoanStatus, AccountBorrow, AccountRepay } from './LendingPanels';
import { AccountPositions, AccountOpenTrades, AccountRepaymentHistory, AccountActivity } from './ActivityPanels';
import { AccountReview, PendingTransaction } from './ReviewDialog';

/** The approved five-screen layout, using only authoritative account and chain state. */
export function AccountDashboard({ service, native = null, controller: provided, refreshConfig, basePath = '', borrowPolicy=null }: { borrowPolicy?: BorrowPolicy|null; basePath?: string; service: AccountService | null; native?: NativeVaultWorkflow | null; controller?: DashboardController; refreshConfig?: ()=>Promise<void> }) {
  const controller=useMemo(()=>provided??new DashboardController(service,native,undefined,refreshConfig),[provided,service,native,refreshConfig]);
  return <BorrowPolicyContext.Provider value={borrowPolicy}><AccountProvider controller={controller}><AccountScreens basePath={basePath} /></AccountProvider></BorrowPolicyContext.Provider>;
}
export function AccountScreens({basePath=''}:{basePath?:string}) {
  const route=(path:string)=>basePath+(path==='/'?'/':path);
  const {account,busy,refreshing,error,nativeError,notice,controller}=useAccountDashboard();
  return <>
    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><p>{!controller.service?'Connect your wallet to load your account':busy?'Processing action…':refreshing?'Updating account…':account?'Account updated':'Account unavailable'}</p><Button size="sm" variant="ghost" disabled={!controller.service||busy||refreshing} onClick={()=>void controller.refresh()}>Refresh</Button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}{nativeError && <p role="alert" className="text-sm text-warning">Native state unavailable: {nativeError}</p>}{notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    <PendingTransaction />
    <Routes>
      <Route path={route('/')} element={<div className="space-y-4"><div className="grid gap-4 sm:grid-cols-3"><Panel title="Loan"><Metric label="Loan amount" value={usd(account?.health?.principal??account?.health?.loan?.principal??(account?'0':undefined))} /></Panel><Panel title="Repay"><Metric label="Amount to repay" value={usd(account?.repayment.debt)} /></Panel><Panel title="Trades"><Metric label="Open trades" value={account?String(account.paper.positions.length):'Unavailable'} /></Panel></div>{!!account?.paper.positions.length && <AccountPositions />}</div>} />
      <Route path={route('/vault')} element={<div className="grid gap-4 lg:grid-cols-2"><div className="space-y-4"><AccountRewardTerms /><AccountClaim /></div><AccountDepositWithdraw /></div>} />
      <Route path={route('/borrow')} element={<div className="grid gap-4 lg:grid-cols-3"><AccountLoanStatus /><AccountBorrow /><AccountRepay /></div>} />
      <Route path={route('/trading')} element={<div className="grid max-w-5xl items-start gap-4 md:grid-cols-2"><div className="space-y-4"><AccountTradingStatus /></div><AccountOpenTrades /></div>} />
      <Route path="/settings" element={<AccountSettings />} />
      <Route path={route('/risk')} element={<div className="grid gap-4 lg:grid-cols-2"><AccountRepaymentHistory /><AccountActivity /></div>} />
      <Route path="*" element={<NotFound />} />
    </Routes>
    <AccountReview />
  </>;
}

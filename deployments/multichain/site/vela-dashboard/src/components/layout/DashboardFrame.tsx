import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { Activity, Gauge, Landmark, LineChart, Vault } from 'lucide-react';
import { cn } from '@/lib/utils';

const NAV = [
  { to: '/', label: 'Overview', icon: Gauge },
  { to: '/vault', label: 'Vault & Rewards', icon: Vault },
  { to: '/borrow', label: 'Borrow & Repay', icon: Landmark },
  { to: '/trading', label: 'Trading', icon: LineChart },
  { to: '/risk', label: 'Risk & Activity', icon: Activity },
];

export function DashboardFrame({ banner, wallet, children, footer }: { banner: ReactNode; wallet: ReactNode; children: ReactNode; footer: ReactNode }) {
  return (
    <div className="bg-atmosphere grain min-h-screen">
      {banner}
      <header className="sticky top-0 z-40 border-b border-border/70 bg-background/80 backdrop-blur-md">
        <div className="container flex h-14 items-center justify-between gap-3">
          <div className="flex items-baseline gap-2">
            <span className="font-display text-lg font-extrabold tracking-[0.18em]">VELA</span>
            <span className="hidden text-xs text-muted-foreground sm:inline">Collateral Rewards</span>
          </div>
          {wallet}
        </div>
        <nav className="container -mb-px flex gap-1 overflow-x-auto" aria-label="Sections">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => cn(
              'interactive flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-xs font-medium sm:text-sm',
              isActive ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
            )}>
              <Icon className="h-3.5 w-3.5" aria-hidden />{label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="container relative z-10 space-y-4 py-5 sm:py-7">
        {children}
      </main>
      <footer className="container relative z-10 pb-8 text-[11px] leading-relaxed text-muted-foreground">
        {footer}
      </footer>
    </div>
  );
}

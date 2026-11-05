import { ConnectButton } from '@rainbow-me/rainbowkit';
import { Loader2, RotateCcw, Wallet, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useGuardedConnect } from '@/hooks/useGuardedConnect';

/** Themed wallet button with 45 s timeout, Cancel/Retry and late-approval guard. */
export function WalletControl() {
  const g = useGuardedConnect();
  return (
    <div className="relative">
      <ConnectButton.Custom>
        {({ account, chain, openAccountModal, openChainModal, mounted }) => {
          if (!mounted) return <div className="h-9 w-32" aria-hidden />;
          if (!account || !chain) {
            const busy = g.phase === 'connecting';
            return (
              <Button size="sm" onClick={g.start} disabled={busy || !g.available} className="interactive gap-2">
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wallet className="h-4 w-4" />}
                {busy ? `Connecting… ${g.secondsLeft}s` : 'Connect wallet'}
              </Button>
            );
          }
          return (
            <div className="flex items-center gap-1.5">
              <Button size="sm" variant={chain.unsupported ? 'destructive' : 'secondary'} onClick={openChainModal} className="interactive h-9 px-2.5 text-xs">
                {chain.unsupported ? 'Wrong network' : chain.name}
              </Button>
              <Button size="sm" variant="secondary" onClick={openAccountModal} className="interactive num h-9 px-2.5 text-xs">
                {account.displayName}
              </Button>
            </div>
          );
        }}
      </ConnectButton.Custom>
      {(g.phase === 'connecting' || g.phase === 'timed-out' || g.phase === 'cancelled' || g.lateIgnored) && (
        <ConnectNotice g={g} />
      )}
    </div>
  );
}

function ConnectNotice({ g }: { g: ReturnType<typeof useGuardedConnect> }) {
  const msg = g.lateIgnored
    ? 'A wallet approval arrived after this attempt was cancelled. It was ignored and disconnected.'
    : g.phase === 'connecting'
      ? 'Waiting for your wallet. Times out after 45 seconds.'
      : g.phase === 'timed-out'
        ? 'Connection timed out after 45 seconds. The network selector is released.'
        : 'Connection attempt cancelled.';
  return (
    <div role="status" className="glass absolute right-0 top-11 z-50 w-72 rounded-md p-3 text-xs">
      <p className="text-foreground">{msg}</p>
      <p className="mt-1 text-muted-foreground">Your wallet extension may still show its own prompt — dismiss it there if needed.</p>
      <div className="mt-2.5 flex gap-2">
        {g.phase === 'connecting' ? (
          <Button size="sm" variant="outline" onClick={g.cancel} className="h-7 gap-1 text-xs"><X className="h-3 w-3" />Cancel</Button>
        ) : (
          <>
            <Button size="sm" onClick={g.retry} className="h-7 gap-1 text-xs"><RotateCcw className="h-3 w-3" />Retry</Button>
            <Button size="sm" variant="ghost" onClick={g.dismiss} className="h-7 text-xs">Close</Button>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Wallet connection with a 45 s timeout (regression 3).
 * - Cancel / Retry after timeout; the network selector is never locked once an attempt ends.
 * - Approvals that arrive after an attempt was cancelled or timed out are ignored
 *   (the late session is disconnected) — the wallet's own prompt may still need dismissing.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useConnectModal } from '@rainbow-me/rainbowkit';
import { useAccount, useDisconnect } from 'wagmi';
import { INTEGRATION } from '@/lib/vela/integrationConfig';

export type ConnectPhase = 'idle' | 'connecting' | 'timed-out' | 'cancelled';

export function useGuardedConnect() {
  const { openConnectModal } = useConnectModal();
  const { isConnected } = useAccount();
  const { disconnect } = useDisconnect();
  const [phase, setPhase] = useState<ConnectPhase>('idle');
  const [lateIgnored, setLateIgnored] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const wasConnected = useRef(isConnected);

  const clear = () => { if (timer.current) clearInterval(timer.current); timer.current = null; };

  const start = useCallback(() => {
    clear();
    setLateIgnored(false);
    setPhase('connecting');
    const deadline = Date.now() + INTEGRATION.connectTimeoutMs;
    setSecondsLeft(Math.ceil(INTEGRATION.connectTimeoutMs / 1000));
    timer.current = setInterval(() => {
      const left = Math.ceil((deadline - Date.now()) / 1000);
      setSecondsLeft(Math.max(0, left));
      if (left <= 0) { clear(); setPhase((p) => (p === 'connecting' ? 'timed-out' : p)); }
    }, 250);
    openConnectModal?.();
  }, [openConnectModal]);

  const cancel = useCallback(() => { clear(); setPhase('cancelled'); }, []);
  const dismiss = useCallback(() => { setPhase('idle'); setLateIgnored(false); }, []);

  useEffect(() => {
    const became = isConnected && !wasConnected.current;
    wasConnected.current = isConnected;
    if (!became) return;
    if (phase === 'connecting') { clear(); setPhase('idle'); return; }
    if (phase === 'timed-out' || phase === 'cancelled') {
      disconnect(); // late approval from an abandoned attempt
      setLateIgnored(true);
    }
  }, [isConnected, phase, disconnect]);

  useEffect(() => clear, []);

  return { phase, secondsLeft, lateIgnored, start, cancel, retry: start, dismiss, available: !!openConnectModal };
}

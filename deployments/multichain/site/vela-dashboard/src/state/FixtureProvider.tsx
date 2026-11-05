/**
 * FIXTURE store. Local deterministic rehearsal only — never displayed as live execution.
 * `act` runs the pure reducer synchronously and returns an error string (or null), so
 * buttons can show real failure reasons instead of fake success.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { initialFixture, reduce, type Action, type FixtureState } from '@/lib/vela/fixtureEngine';
import { deriveView, type View } from './selectors';

type Ctx = { state: FixtureState; view: View; act: (a: Action) => string | null };
const FixtureContext = createContext<Ctx | null>(null);

const POLL_EVERY_MS = 4_000;
const POLL_LATENCY_MS = 2_500;

export function FixtureProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<FixtureState>(initialFixture);
  const ref = useRef(state);

  const act = useCallback((a: Action) => {
    try {
      const next = reduce(ref.current, a);
      ref.current = next;
      setState(next);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  }, []);

  // Simulated status poller with latency: a response captures the seq at request time.
  // If Stop happens while it is in flight, the reducer discards it (regression 5).
  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    const iv = setInterval(() => {
      const { seq, status } = ref.current.session;
      timers.push(setTimeout(() => act({ type: 'POLL_RESULT', seq, status }), POLL_LATENCY_MS));
    }, POLL_EVERY_MS);
    return () => { clearInterval(iv); timers.forEach(clearTimeout); };
  }, [act]);

  const view = useMemo(() => deriveView(state), [state]);
  return <FixtureContext.Provider value={{ state, view, act }}>{children}</FixtureContext.Provider>;
}

export function useFixture() {
  const c = useContext(FixtureContext);
  if (!c) throw new Error('useFixture must be used inside FixtureProvider');
  return c;
}

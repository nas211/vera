import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import App from '../../App';
import { WalletCard } from '../../components/overview/WalletCard';
import { PreviewMode } from '../../state/PreviewMode';
import { BUILD_ID } from '../buildInfo';

// Any real wallet hook reached by a preview is a failure, even during rendering.
vi.mock('wagmi', () => ({
  useAccount: () => { throw Error('Preview attempted a wallet read'); },
  useBalance: () => { throw Error('Preview attempted an RPC balance read'); },
  useDisconnect: () => { throw Error('Preview mounted wallet controls'); },
}));
vi.mock('@rainbow-me/rainbowkit', () => ({
  ConnectButton: { Custom: () => { throw Error('Preview mounted a wallet connector'); } },
  useConnectModal: () => { throw Error('Preview opened a wallet modal'); },
}));

describe('Noah preview boundary', () => {
  it.each(['/', '/vault', '/borrow', '/trading', '/risk'])('renders %s without any wallet provider or account service', route => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={[route]}><App preview /></MemoryRouter>);
    expect(html).toContain('Noah UI preview');
    expect(html).toContain('sample balances and actions');
    expect(html).toContain('Preview · no wallet');
    expect(html).toContain('Fixture scenario controls');
    expect(html).toContain(BUILD_ID.slice(0,12));
    expect(html).not.toContain('Connect wallet');
    expect(html).not.toContain('LIVE READ');
  });
  it('does not report sample balances as a real wallet balance', () => {
    const html = renderToStaticMarkup(<PreviewMode.Provider value><WalletCard /></PreviewMode.Provider>);
    expect(html).toContain('NO CONNECTION');
    expect(html).toContain('does not read your wallet');
    expect(html).not.toContain('Test DOGE balance');
  });
  it('an unknown route uses router navigation back to this app', () => {
    const html = renderToStaticMarkup(<MemoryRouter basename="/preview" initialEntries={['/preview/unknown']}><App preview /></MemoryRouter>);
    expect(html).toContain('Return to Home');
    expect(html).toContain('href="/preview"');
  });
});

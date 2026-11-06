import { describe, expect, it } from 'vitest';
import { isMobileBrowser, metamaskDappLink } from '../vela/mobileWallet';

describe('mobile wallet handoff', () => {
  it('recognizes phone browsers and iPad desktop mode without redirecting desktops', () => {
    for (const userAgent of ['Mozilla/5.0 (Linux; Android 14) Chrome/130', 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)']) expect(isMobileBrowser({userAgent,maxTouchPoints:1})).toBe(true);
    expect(isMobileBrowser({userAgent:'Mozilla/5.0 (Macintosh; Intel Mac OS X)',maxTouchPoints:5})).toBe(true);
    expect(isMobileBrowser({userAgent:'Mozilla/5.0 (Macintosh; Intel Mac OS X)',maxTouchPoints:0})).toBe(false);
    expect(isMobileBrowser({userAgent:'Mozilla/5.0 (Windows NT 10.0)',maxTouchPoints:5})).toBe(false);
  });
  it('opens only public Vela pages and does not forward arbitrary hosts or tokens', () => {
    expect(metamaskDappLink('/app/trading')).toBe('https://link.metamask.io/dapp/defivela.xyz/app/trading');
    for(const path of ['https://evil.example', '//evil.example', '/app/?token=private', '/unknown']) expect(metamaskDappLink(path)).toBe('https://link.metamask.io/dapp/defivela.xyz/app/');
  });
});

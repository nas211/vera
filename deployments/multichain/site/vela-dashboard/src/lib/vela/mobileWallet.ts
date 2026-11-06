/** MetaMask Mobile exposes its provider in its own browser, not mobile Chrome. */
export function isMobileBrowser(nav: Pick<Navigator, 'userAgent' | 'maxTouchPoints'> = navigator): boolean {
  return /Android|iPhone|iPad|iPod/i.test(nav.userAgent) || /Macintosh/i.test(nav.userAgent) && nav.maxTouchPoints > 1;
}

/** Only public app routes cross the app boundary; never forward session/query data. */
export function metamaskDappLink(pathname: string): string {
  const path = ['/app/', '/app/trading', '/app/borrow', '/app/vault', '/app/risk', '/settings'].includes(pathname) ? pathname : '/app/';
  return 'https://link.metamask.io/dapp/defivela.xyz' + path;
}

# Vela for DogeOS

Independent Vela website for `defivela.xyz`, included in the Vera project.
HTML, CSS and browser JavaScript, with a React wallet screen built using the
official DogeOS SDK. The website has no server or trading credentials and makes
no requests to the original WHY system.

## Implemented

- Responsive graphite and gold design, animated DOGE artwork and accessible
  mobile navigation, tabs, dialogs and reduced-motion behavior.
- MetaMask discovery with EIP-6963 and an injected-provider fallback.
- MyDoge Wallet connection through `@dogeos/dogeos-sdk` 4.0.4, using Vela's
  registered public Sandbox client ID. The chooser opens `/mydoge.html` as a
  top-level page, which is required for MyDoge's native wallet bridge.
- User-approved connection, account and network changes, explicit DogeOS
  Chikyū Testnet addition/switching, native testnet DOGE balance, explorer link,
  address copying and disconnect.
- Mobile MetaMask deep link and official extension installation link.
- Interactive trade previews, hypothetical yield scenarios, preview missions,
  and an illustrative six-role agent workflow.

The wallet connection reads public account information. Vela does not authenticate
a backend trading session or request token approvals, transfers or trading
signatures. DogeOS manages its own wallet login and consent flow.
Trading and rewards are simulated. There are no deployed trading contracts,
deposits, withdrawals, live model agents, payable bonuses or real APY product
attached to this frontend. The copied execution and agent code lives in the
parent Vera repository.

## Network

Only the documented DogeOS Chikyū Testnet is configured:

| Setting | Value |
| --- | --- |
| Chain ID | 6281971 (`0x5fdaf3`) |
| RPC | `https://rpc.testnet.dogeos.com/` |
| Native currency | DOGE, 18 decimals |
| Explorer | `https://dogeos-testnet.l2scan.co` |

Settings verified on 2026-10-20 against the official
[DogeOS quickstart](https://docs.dogeos.com/en/developers/developer-quickstart) and
[native DOGE documentation](https://docs.dogeos.com/en/developers/native-doge-erc20).
Wallet behavior follows the official
[MetaMask provider API](https://docs.metamask.io/metamask-connect/evm/reference/provider-api/)
and [mobile deep links](https://docs.metamask.io/metamask-connect/evm/guides/metamask-exclusive/use-deeplinks/).

On the main page, MetaMask makes no account requests before the user chooses to
connect. The MyDoge SDK loads only on its dedicated wallet screen, after selection
in the chooser. It handles native wallet discovery and its own connection state.
A connected account's balance is shown as DOGE only on the configured testnet.
Stale balance reads are discarded after account or network changes. MetaMask
disconnect clears local state and attempts to revoke account permissions; MyDoge
disconnect uses the SDK and hides the local connection even if that call fails.

## MyDoge registration

`dist/wallet-config.js` contains Vela's public DogeOS Sandbox client ID, supplied
by the owner. It is not a private credential. The owner confirmed these origins
were registered in the Sandbox environment:

- `https://defivela.uuuiiaaaa.chatgpt.site`
- `https://defivela.xyz`

The custom domain still requires DNS activation. A Sandbox registration and a
testnet blockchain are distinct settings: the SDK uses DogeOS's production login
services even for testnet apps. This integration configures only Chikyū's EVM
network. The official SDK chooses the native wallet inside MyDoge; we do not
invent a `window.mydoge` provider or guess a deep link. In ordinary browsers the
SDK provides its supported login/connection flow.

No WalletConnect project ID is configured, so QR-based WalletConnect is not
enabled. The official SDK requires a separate app-owned project ID for that.
MetaMask's existing extension and mobile-browser flow is independent.

References: [SDK quickstart](https://docs.dogeos.com/en/sdk),
[native MyDoge connection](https://docs.dogeos.com/en/sdk/embedded-wallets#native-first-connection-in-mydoge),
[provider workflows](https://docs.dogeos.com/en/sdk/guides).

## Local preview and verification

```sh
npm ci
npm run build
python3 -m http.server 8769 --bind 127.0.0.1 --directory dist
```

Open `http://127.0.0.1:8769`. Build output is tracked for static hosting.
Browser checks require Node, Playwright and Chrome. Set `PLAYWRIGHT_MODULE_PATH`
and `CHROME_PATH` if using existing installations, then run:

```sh
node --check dist/app.js
node --check dist/wallet.js
node checks/browser.cjs
node checks/wallet.cjs
node checks/mydoge.cjs
```

`PREVIEW_URL` can override the local server URL. `CHECK_ARTIFACTS_DIR` can override
the test screenshot directory (defaults to `/tmp/vela-checks`).

Checks passed on 2026-10-20: six responsive widths, all feature tabs, chart
controls, long/short fee-inclusive $2 stop previews, yield math, agent animation,
missions, dialogs, FAQ, mobile navigation and reduced motion. Wallet tests passed
with mocked providers, covering absence, connection rejection/retry, EIP-6963,
legacy injection, chain addition/switching, wrong-chain behavior, balances,
account changes, stale responses, disconnect and four dialog widths.

The automated connection tests use mock providers and SDK hooks, never an
exchange or funded wallet. The MyDoge tests cover the registered-config shape,
native connection with embedded readiness disabled, rejection/retry, account
changes, chain switching, stale responses and disconnect. The real SDK login
modal opened without JavaScript errors using local source served in the browser
at the registered site origin, with real MyDoge services responding. No user
signed in during this check; the unauthenticated session endpoint returned 401.
A connection from a real MyDoge mobile app or MetaMask extension still needs
owner verification. Run `node checks/sdk-smoke.cjs` to repeat this separate
service-dependent check.

The dependency install reported 52 advisories (9 low, 21 moderate, 22 high)
on 2026-10-20. A `protobufjs` override pins 7.6.6 and removes the reported
critical advisory. Remaining SDK/transitive advisories require review before
enabling funded execution; not every installed optional adapter is used by this
EVM screen. No claim is made that the dependency tree or SDK is security-audited.
The SDK remains pinned to 4.0.4; automatic remediation that downgrades it was
not applied. The `wagmi/connectors` build alias re-exports the same official
`injected` connector from `@wagmi/core`, avoiding unused optional connector
imports. Required browser peers are explicitly pinned in `package.json`.

## Hosting

`.openai/hosting.json` retains this site's hosting identity and declares `dist`
as the deployment output. Publish only this static directory. The initial
audience is owner-private. Domain activation requires the DNS records returned
by the hosting provider. Neither site publication nor a wallet connection
deploys the copied trading services or changes any live stop protection.

The $2 fee-inclusive stop calculation in the trade ticket is an illustrative
preview. Real DogeOS execution needs an independently configured and tested
adapter. Stop triggers do not guarantee realized losses against slippage,
market gaps or funding charges.

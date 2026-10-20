# Vela deployment status

Updated on 2026-10-20. Prepared source, tests, website publication, and live
trading protection are separate verification scopes.

## Website

The independent Vela static website was successfully published with owner-private
access at:

https://defivela.uuuiiaaaa.chatgpt.site

The source is in `site/`. The deployed Sites source commit is
`5fd4011d130424075542b606750cf6eb2935de56`.
Its tracked source and static build output are copied into this repository
without nested Git metadata or installed dependencies. The hosting project is
recorded in `site/.openai/hosting.json`. Wallet deployment succeeded with ID
`appgdep_6ad770ce7c9c81919ed0c795fbafe8aa`.
The resumed check confirmed that deployment is `succeeded` and site access
contains only the owner, with no groups or external visitors. No new website
deployment was made during repository finalization.

MetaMask and MyDoge Wallet connections support DogeOS Chikyū Testnet, including
account/network changes, testnet balance, network switching and disconnect.
MyDoge uses official SDK 4.0.4 and the owner's public Sandbox client ID.
The chooser opens MyDoge at top-level `/mydoge.html` for its native mobile bridge.

Automated browser suites passed with mock wallet providers and SDK hooks.
The real MyDoge SDK opened its connection modal without JavaScript errors using
local assets at the registered site origin and real MyDoge services. No real
wallet sign-in/approval was performed. The owner must still verify the native
MyDoge mobile and MetaMask extension flows. Dependency advisories and build
instructions are documented in `site/README.md`.

All trading, yield, bonus and agent displays are previews. No execution protocol
is connected; selecting one remains a separate decision.

## Custom domain: pending DNS

`defivela.xyz` has been registered with the site host. Refreshing verification
at 2026-10-20 14:30:55 UTC returned `pending`, provider status `pending`, and SSL
`pending_validation`, with no provider error. The domain is not yet verified or
confirmed to route to the site. DNS records were not changed.

Configure these records at the authoritative DNS provider:

| Type | Name | Value |
| --- | --- | --- |
| A | `@` | `162.159.143.30` |
| A | `@` | `172.66.3.26` |
| TXT | `_openai-site-verification` | `openai-site-verification=IBp1NHk68Ftv7ScrIaTikFTC93FeLcle-JUj-LxmIsU` |
| TXT | `_cf-custom-hostname` | `ac59adc5-830b-4e17-8216-8d71271e411f` |

For providers requiring absolute record names, append `.defivela.xyz` to each
TXT name. These are public DNS validation values, not credentials. Preserve
unrelated DNS records. The two A records are for the apex domain; the returned
subdomain CNAME target is `custom-domains.chatgpt.site.`.

After DNS is configured, refresh domain verification for project
`appgprj_6ad757d1f93c8191bc736d87ada530be` and custom domain
`appgdom_6ad75f3f36a081918c9897f8f8da62af`. Domain attachment does not change the
site's private audience.

## GitHub

Private repository: https://github.com/nas211/vera

The GitHub connection now has push access. This repository contains the isolated
trading/agent source snapshot and the full Vela website source, dependency lock,
static build, wallet tests and hosting manifest. Original runtime secrets,
account state and installed dependencies are excluded.

The resumed check found only the bootstrap README on `main` at
`ca3d7cbcf80cc76ed67c221cc28bf5d95206df0f`; previously uploaded component
objects were available but were not yet referenced by the complete branch.

The upload is assembled through GitHub's Git Data API. Its final tree must
match the local committed tree before `main` is updated, and the branch must
then be read back and checked recursively for identical paths, modes, and blob
hashes. The GitHub commit message records the local source commit and tree.
Git Data API commit metadata can differ from local metadata; matching tree
hashes, rather than matching commit IDs, establish identical source.
A fresh clone can build the wallet with `cd site && npm ci && npm run build`.
GitHub stores the code; hosting runs the website separately. The copied trading
services have not been started or deployed.

## Trading system and live protection

The copied trading/agent system remains undeployed. No WHY services, source,
accounts, orders, domains, environment files or live stops were modified.

The original read-only comparison covered 571 source files and 35 service units,
31 active. A later check at 13:46:38 UTC still found no production source hash
changes, but observed `why-discovery-bulk-shadow.service` change from `inactive`
to `failed`. Both observations had MainPID 0 and NRestarts 0. The only local
snapshot-manifest mismatch was `AGENTS.md`, which had been extended with Vera's
copy and wallet boundaries; the manifest describes the earlier capture.
The earlier "no service process changes" result must not be treated as current.

During this resume, `systemctl` could not connect to the system bus because of
the sandbox. Read-only journal access did work. At 16:25:43 Europe/Berlin
(14:25:43 UTC), the job reported `ValueError:candle_schema_or_history` for
`AAVEUSDT-PERP`, `ARBUSDT-PERP`, `GOOGLUSDT-PERP`, `UNIUSDT-PERP`, and
`XAUUSDT-PERP`, then raised `RuntimeError:shadow_market_errors` and exited 1.
The copied source validates at least 250 completed candles, six numeric columns,
and finite values; the journal does not distinguish which of these conditions
failed. It reported `live_enabled: false` for this simulation job. No market
endpoints were queried, no services were changed, and no repair was deployed.
This diagnoses the immediate failure path, not the upstream data cause.

The copied execution core passed all 41 offline regression tests again during
this resume, with zero failures, errors, or skips. Network calls and subprocesses
were blocked, and writes were confined to a temporary directory. Website and
wallet browser results above are the prior checks; they were not rerun for this
documentation and upload work. Real mobile wallet approval remains untested.

Vera trading execution remains disabled and undeployed pending the protocol
decision. The maximum $2 fee-inclusive per-position stop trigger budget applies
to every account, including JP and future accounts, while preserving stricter
limits. It does not guarantee realized losses against slippage or funding.
No live venue was queried, so current exchange stop protection was neither
changed nor independently reverified.

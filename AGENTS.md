# WHY Trading Bot risk requirement

The owner requires a maximum $2 per-position stop-loss budget for EVERY account,
including JP and future accounts. This requirement is not account-specific.
Keep stricter existing limits. Account size, leverage, strategy, profit targets,
and account overrides must never raise or bypass the $2 budget.

Include trading fees in stop calculations, round stops inward, account for partial
exits, and test both long and short positions and existing-position reconciliation.
Do not describe a stop trigger budget as a guarantee against slippage or funding.

Distinguish prepared code, tests, deployment, and verified live protection in every
status report. Never claim live stops changed solely because code was edited.

For P&L, distinguish today's results from cumulative results. Use the same time
scope for trade counts and win/loss figures, or label differing scopes explicitly.

# Vera copy boundary

This is an independent development copy of WHY for future Doge OS integration.
Work only inside this repository. Do not edit or deploy to the original WHY
repositories, /opt/why-* installations, /etc service configuration, live state,
databases, credentials or exchange accounts.

Inherited WHY deployment files and shell scripts have .reference suffixes and
are documentation, not Vera deployment instructions. Preserve that boundary.
Use isolated mock tests; do not start trading, contact live account endpoints or
reuse production account/model credentials while validating this copy.

The owner authorized Vela's dedicated DogeOS Chikyū TESTNET + PAPER ONLY vault,
backend, frontend, and new Vela service units. This authorization does not extend
to the inherited WHY deployments. Reuse only pure signal, model and risk modules;
never reuse WHY credentials or live account endpoints. The only permitted
non-loopback chain is 6281971. TUSDC is test accounting, not real USDC or a DEX
swap. Distinguish mocked browser checks, direct testnet signing and actual
MyDoge mobile approval. Bonuses, yield and real trading remain unimplemented.

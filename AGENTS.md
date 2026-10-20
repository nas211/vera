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

The separate Vela frontend includes MetaMask and official MyDoge SDK connections
for DogeOS Chikyū Testnet. Backend wallet authentication, trading execution,
DOGE reward settlement, bonuses and an APY product are future work. Do not
represent a wallet connection or simulated UI as a deployed financial product.

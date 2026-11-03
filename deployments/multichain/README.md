# Vela multichain backend

**Current status:** the backend, `/paper` desk and `/earn` demo staking are deployed on the existing VPS. DogeOS is featured in the portal; the homepage is unchanged. See the [latest acceptance record](docs/PAPER-EARN-20261103.md). Real trading, funded staking and fund transfers remain disabled.

This directory publishes the newer VPS backend and all prepared backend changes from source commit `77ad84168da9e95101696313f4ba86ca650db929`. It coexists with the legacy Vela application at the repository root. It does not replace that application, its database, or any UI files.

The existing runtime directory is `/root/VellaMultichain`; Nginx serves its separately preserved UI and routes API traffic through the existing gateway. Do not start a second engine against that runtime's ledger. This source publication does not deploy or restart any service.

Included: FastAPI backend, reused pure risk/agent/feed modules, contract source and tests, configuration schemas, backend tests, operational tools, pending service configuration and validation records. All prepared backend changed/new files are included verbatim. The original checkout's README is retained as [source history](docs/SOURCE-README.md); historical deployment statements there are not current acceptance evidence.

Current frontend source and the newly deployed product/portal bundles are now included, together with the exact prior portal assets needed to reproduce the three text replacements. Historical generated frontend builds, customer databases, environment files, private recovery copies and installed dependencies are excluded. Backend tests that inspect preserved frontend bundles require those original build artifacts; this publication intentionally does not generate or replace them. The full suite was executed against the preserved artifacts in the isolated VPS worktree, as recorded in [validation](snapshot/backend-validation-20261103.json).

Start with the [backend implementation and deployment report](docs/BACKEND-COMPLETION-20261103.md), [implementation inventory](snapshot/completion-inventory-20261103.json), and [source publication manifest](snapshot/publication-20261103.json). The manifest records the exact original source hashes. Its `README.md` hash refers to `docs/SOURCE-README.md`; this entrypoint explains the additive publication layout.

## Run backend checks

Use an isolated dependency environment with `requirements.txt`, never a WHY environment. With this directory as the working directory:

```bash
PYTHONPATH=backend python -m pytest tests/test_completion_safety.py \
  tests/test_agent_sessions.py tests/test_model_usage.py tests/test_staking.py \
  tests/test_trading_details.py tests/test_backend_acceptance.py \
  tests/test_backend_release.py tests/test_source_preservation.py \
  tests/test_backend_deployment.py -q
```

The tests use synthetic wallet keys, market data, model responses and temporary ledgers. They do not prove genuine wallet approvals, live market/provider connectivity or exchange-side protection. `tools/restricted_pytest.py` provides a selector heartbeat for restricted test executors; it does not bypass test assertions.

## Deploy the existing backend

Use the verified backend overlay and matching code rollback from `/root/vela-completion-20261103/release-v2`, following the report's preflight, fresh backup, ownership, graceful restart and acceptance sequence. This GitHub copy provides the source of every overlay file and its unchanged backend dependencies. The release builder's base reference belongs to the original multichain repository history, not this additive import commit; do not attempt to resolve that base in the legacy repository history.

Publication does not authorize enabling real trading, real lending, funded staking, chain writes or a new contract deployment. The fee-inclusive per-position stop-trigger budget remains $2 or stricter; it is not a guarantee against slippage, gaps or funding. Live protection remains unverified. Demo staking is enabled explicitly in the public application factory. The library default remains disabled. Existing page layouts and the homepage remain unchanged; independent paper/earn products and three DogeOS text updates are documented in the latest acceptance record.

The owner excludes ZK/privacy products from this project. Backend deployment automation, monitoring/backups and CI are documented in the updated backend report. No host deployment is claimed from publishing this source.

## Build and check the new products

From `site`, run `npm ci --prefix products-toolchain --ignore-scripts` and `npm run build:products`. The build checks its bundled dependency allowlist and excludes the disabled DogeOS SDK. Run `python3 scripts/prepare-dogeos-portal.py --source deployed-simple-tabs` to reproduce the copy-only portal bundle.

For local browser journeys, start `uvicorn tools.products_browser_fixture:app --host 127.0.0.1 --port 18917` from this backend directory in an isolated environment, then run `npm run test:products` from `site`. The harness uses a new temporary ledger and synthetic market/model/wallet fixtures. `npm run test:public-products` checks public page rendering without real funds or wallet approvals.

The full prepared frontend dependency tree still has SDK-related advisories (15 high, 27 moderate, 62 low at the latest audit). Do not interpret the narrow new-product bundle allowlist or passing backend CI as a complete security audit.

The scoped product toolchain now has its own exact lockfile; a clean install reproduces all nine deployed product assets byte-for-byte and its current npm audit reports zero findings. The larger disabled SDK tree remains separately disclosed above.

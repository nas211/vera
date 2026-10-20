"""WHY affiliate / partner CRM — team dashboard (affiliate.whytradingbot.com).

Read-only over the customer platform's data (auth.users + public.yellow_* via the server's
Postgres connection) plus its own three tables (partners, team users, audit). Standard library
HTTP server on loopback behind nginx; never touches Yellow, Vault or the execution runner."""

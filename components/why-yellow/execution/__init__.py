"""WHY x Yellow execution core (2026-09-22 execution-safety fix).

Pure-stdlib package: deterministic order identity, persisted order/position state
machines, exchange-authoritative reconciliation, protection lifecycle, split
entry/management switches, strict validation, market-data freshness and venue
state. Strategy logic lives in yellow_paper.py and is NOT touched here; sleeve
geometry is mirrored read-only in execution.geometry.

Nothing in this package opens a network connection. The only venue implementations
are MockYellowAdapter (tests) and YellowTransport (thin wrapper over
yellow_exec.YellowClient, which remains gated by YELLOW_ARMED=1 + key file).
"""
__all__ = ["types", "state_machine", "ledger", "metadata", "validation", "sizing",
           "geometry", "market_data", "venue_state", "switches", "transport",
           "mock_adapter", "engine"]

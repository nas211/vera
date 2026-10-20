"""Fresh overnight PAPER-only profile; no private API or live order transport."""
import runner
import v2

MARKETS=['LINKUSDT-PERP','HYPEUSDT-PERP','SOLUSDT-PERP','XRPUSDT-PERP','DOGEUSDT-PERP',
         'BTCUSDT-PERP','ETHUSDT-PERP','BNBUSDT-PERP','SUIUSDT-PERP','TAOUSDT-PERP']

def configure():
    runner.MARKETS=list(MARKETS)
    v2.EXCLUDED_ENTRIES.clear()
    v2.ACTIVE_MARKETS[:]=MARKETS
    runner.CONFIG.update(strategy_version=3,run_mode='overnight',stop_after_completed_trades=None,
        starting_equity=1000,leverage=10,entry_margin_usd=10,max_entry_slots=100,
        sizing='fixed_margin',levels_per_side=5,target_bps=10,
        gross_notional_cap=10000,per_market_cap=1000,
        refresh_seconds=35,maker_fee=0,taker_fee=.00015,taker_orders=False)
    # Preserve risk/time exits; keep the requested 0.10% target until an exit
    # rule fires instead of quietly reducing it to break-even after 30 seconds.
    v2.RULES.update(side_notional_limit=500,entry_refresh_seconds=35,
                    decay_target_after_seconds=None)

if __name__=='__main__':
    configure()
    runner.main(v2.PaperV2)

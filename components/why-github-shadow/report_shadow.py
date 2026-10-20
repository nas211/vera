"""Read-only summaries of isolated Freqtrade SQLite ledgers for WHY's existing lab."""
import json
import os
from pathlib import Path
import sqlite3
import time

BASE = Path('/opt/why-github-shadow')
DEST = Path('/opt/why-shadow-lab/state/github_shadow.json')
NAMES = ('FAdxSmaStrategy','BbandRsi','Strategy001','FSupertrendStrategy',
         'VolatilitySystem','NostalgiaForInfinityX8')

def summary(name, now):
    folder = BASE/'state'/name
    result = dict(name=name, state='starting', closed_trades=0, open_trades=0,
                  realized_pnl=0, simulated=True)
    try:
        runtime = json.loads((folder/'runtime.json').read_text())
        result.update(state=runtime['state'], heartbeat_age_s=round(now-runtime['ts'],1),
                      timeframe=runtime.get('timeframe'), pairs=runtime.get('pairs'),
                      analyzed_pairs=runtime.get('analyzed_pairs'),
                      oldest_analyzed_candle_age_s=runtime.get('oldest_analyzed_candle_age_s'))
        if not 0 <= now-runtime['ts'] <= 180: result['state'] = 'stale'
        db = sqlite3.connect(f'file:{folder / "trades.sqlite"}?mode=ro', uri=True, timeout=3)
        try:
            db.execute('pragma query_only=ON')
            rows = db.execute('select close_profit_abs from trades where is_open=0 order by close_date,id').fetchall()
            pnls = [float(r[0] or 0) for r in rows]
            wins = sum(x for x in pnls if x > 0); losses = -sum(x for x in pnls if x < 0)
            total=peak=drawdown=0
            for pnl in pnls:
                total += pnl; peak=max(peak,total); drawdown=max(drawdown,peak-total)
            result.update(closed_trades=len(rows), realized_pnl=round(total,6),
                          profit_factor=round(wins/losses,4) if losses else None,
                          realized_max_drawdown=round(drawdown,6),
                          open_trades=db.execute('select count(*) from trades where is_open=1').fetchone()[0])
        finally: db.close()
    except (OSError, ValueError, sqlite3.Error, KeyError) as error:
        result['read_status'] = type(error).__name__
    return result

def snapshot():
    now=time.time()
    return dict(ts=int(now*1000), mode='EXTERNAL_STRATEGY_DRY_RUN', live_promotion_allowed=False,
                venue='Bybit USDT perpetual public data', price_source='exchange OHLCV and orderbook',
                wallet_per_strategy=25000, requested_initial_stake=200, leverage=1,
                note='Separate forward benchmarks. Not AI-approved or profitability-certified. '
                     'Native Freqtrade simulated fills; not directly comparable to the mark-candle lab. '
                     'Realized P&L excludes unrealized positions; do not interpret it as total equity.',
                strategies=[summary(name,now) for name in NAMES])

if __name__ == '__main__':
    while True:
        payload=snapshot()
        tmp=DEST.with_suffix('.tmp');tmp.write_text(json.dumps(payload,allow_nan=False))
        os.replace(tmp,DEST)
        time.sleep(15)

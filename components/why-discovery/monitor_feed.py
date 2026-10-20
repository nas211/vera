"""Read-only feed-health journal. No account data or trading controls."""
import argparse
import datetime
import json
import time
import urllib.request

def sample():
    with urllib.request.urlopen('http://127.0.0.1:8114/health',timeout=3) as response:
        feed=json.load(response)['feed']
    return dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        fresh=feed['fresh_markets'],markets=feed['markets'],
        stale={m:b['source_age'] for m,b in feed['books'].items() if b.get('issue')=='STALE_MARKET_DATA'},
        empty=[m for m,b in feed['books'].items() if b.get('issue')=='EMPTY_ORDER_BOOK'],
        last_event_age=feed['last_event_age'],last_gap=feed['last_gap'],
        market_recoveries=feed.get('market_recoveries'),
        confirmations=feed.get('confirmed_snapshots'),transport=feed.get('transport'),
        candle_ages={m:v['last_closed_age'] for m,v in feed.get('candles',{}).items()})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    previous=None;last_print=0.
    while True:
        try:
            row=sample()
            key=(tuple(sorted(row['stale'])),tuple(sorted(row['empty'])),str(row['last_gap']),row['fresh']==0)
        except Exception as error:
            row=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),error=type(error).__name__)
            key=('unavailable',)
        if args.once or key!=previous or time.monotonic()-last_print>=60:
            print(json.dumps(row,separators=(',',':')),flush=True);last_print=time.monotonic()
        previous=key
        if args.once:return
        time.sleep(1)

if __name__=='__main__':main()

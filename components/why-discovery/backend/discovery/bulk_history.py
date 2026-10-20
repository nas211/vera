"""Public-only research history. Binance is an explicitly labelled venue proxy."""
import gzip
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from .model import NoRedirect
from .bulk_screen import BAR_MS,validate_candles

SOURCE='Binance USDT perpetual 5m OHLCV proxy; not Yellow execution history'
DOCS='https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/market-data/rest-api/Kline-Candlestick-Data'

def get_json(path,params):
    if path!='/fapi/v1/klines':raise ValueError('public_research_route_only')
    url='https://fapi.binance.com'+path+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'User-Agent':'WHY-Public-Research/1.0','Accept':'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as response:raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise ValueError('public_history_response_too_large')
            return json.loads(raw)
        except (OSError,ValueError):
            if attempt==2:raise
            time.sleep(1+attempt)

def history(market,as_of,days=30,fetch=get_json):
    if not market.endswith('USDT-PERP') or not market[:-5].isalnum():raise ValueError('unsupported_proxy_symbol')
    end=int(as_of*1000)//BAR_MS*BAR_MS;start=end-days*86400000;cursor=end-1;rows={}
    for _ in range((days*288+999)//1000+2):
        page=fetch('/fapi/v1/klines',dict(symbol=market[:-5],interval='5m',limit=1000,endTime=cursor))
        if not isinstance(page,list) or not page:raise ValueError('history_empty')
        batch=[]
        for r in page:
            if not isinstance(r,list) or len(r)<6:raise ValueError('history_schema')
            t=int(r[0]);bar=[t]+[float(v) for v in r[1:6]]
            if t>cursor:raise ValueError('history_cursor_ignored')
            if t in rows and rows[t]!=bar:raise ValueError('conflicting_candle')
            rows[t]=bar;batch.append(t)
        earliest=min(batch)
        if earliest<=start:break
        if earliest-1>=cursor:raise ValueError('history_cursor_stalled')
        cursor=earliest-1;time.sleep(.1)
    result=[rows[t] for t in sorted(rows) if start<=t<end]
    if len(result)!=days*288:raise ValueError('history_incomplete_window')
    validate_candles(result,as_of,minimum=days*288)
    return result

def save_snapshot(path,rows,market,as_of):
    raw=json.dumps(rows,separators=(',',':'),allow_nan=False).encode()
    with gzip.open(path,'wb') as f:f.write(raw)
    return dict(market=market,source=SOURCE,documentation=DOCS,as_of=as_of,first_open_ms=rows[0][0],last_open_ms=rows[-1][0],bars=len(rows),sha256=hashlib.sha256(raw).hexdigest(),file=Path(path).name)

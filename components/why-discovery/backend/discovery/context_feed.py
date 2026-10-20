"""Public-only evidence refresh. No exchange credentials, trading or model calls."""
import concurrent.futures
import datetime as dt
import email.utils
import html
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from .model import NoRedirect
from . import macro

NEWS={'CoinDesk':'https://www.coindesk.com/arc/outboundfeeds/rss', 'Cointelegraph':'https://cointelegraph.com/rss'}
CALENDAR='https://nfs.faireconomy.media/ff_calendar_thisweek.json'
FUNDING='https://fapi.binance.com/fapi/v1/premiumIndex'
SYMBOLS=('BTC','ETH','SOL','BNB','XRP','DOGE','SUI','HYPE','LINK','TAO')
ALIASES={'BTC':('bitcoin','btc'),'ETH':('ethereum','ether','eth'),'SOL':('solana','sol'),'BNB':('binance','bnb'),'XRP':('ripple','xrp'),'DOGE':('dogecoin','doge'),'SUI':('sui',),'HYPE':('hyperliquid','hype'),'LINK':('chainlink','link'),'TAO':('bittensor','tao')}
POSITIVE={'rally','rallies','gains','surge','surges','approval','approved','recovery','inflows','record'}
NEGATIVE={'hack','hacked','exploit','collapse','crash','plunge','outflows','ban','lawsuit','liquidations','fraud'}

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'WHY-Paper-Research/1.0','Accept':'application/json, application/rss+xml, text/xml, text/html'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=10) as r:data=r.read(2_000_001)
    if len(data)>2_000_000:raise ValueError('RESPONSE_TOO_LARGE')
    return data

def sentiment(title):
    words=set(re.findall(r'[a-z]+',title.lower()))
    pos=len(words&POSITIVE);neg=len(words&NEGATIVE)
    return (pos-neg)/(pos+neg) if pos+neg else 0.

def parse_news(raw,source,now):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('XML_DECLARATION_REFUSED')
    root=ET.fromstring(raw);rows=[]
    for item in root.findall('.//item')[:100]:
        title=html.unescape(re.sub('<[^>]+>','',item.findtext('title',''))).strip()[:500]
        try:published=email.utils.parsedate_to_datetime(item.findtext('pubDate','')).timestamp()
        except (ValueError,TypeError):continue
        if title and 0<=now-published<=86400:
            link=item.findtext('link','')
            rows.append(dict(title=title,source=link if link.startswith('https://') else source,published_at=published,sentiment=sentiment(title)))
    if not rows:raise ValueError('NO_FRESH_NEWS')
    return rows

def parse_calendar(raw,now):
    data=json.loads(raw)
    if not isinstance(data,list) or not data:raise ValueError('CALENDAR_EMPTY')
    rows=[];dates=[]
    for event in data:
        stamp=dt.datetime.fromisoformat(event['date'])
        if stamp.tzinfo is None:raise ValueError('CALENDAR_TIMEZONE_MISSING')
        ts=stamp.timestamp();dates.append(stamp.date())
        impact=event['impact'].lower()
        if impact not in ('high','medium','low'):continue
        if now-86400<=ts<=now+8*86400:
            rows.append(dict(title=(event['country']+' — '+event['title'])[:500],time=ts,impact=impact))
    # Validate the provider's weekly coverage, including weekends; an old cached
    # calendar is never relabeled as a fresh week just because download worked.
    current=dt.datetime.fromtimestamp(now,ZoneInfo('America/New_York')).date()
    week=lambda d:(d-dt.timedelta(days=(d.weekday()+1)%7))
    if week(current) not in {week(d) for d in dates}:raise ValueError('CALENDAR_WRONG_WEEK')
    return rows

def market_context(symbol,mark,oi,news,now):
    if mark['symbol']!=symbol+'USDT':raise ValueError('MARKET_MISMATCH')
    at=float(mark['time'])/1000;rate=float(mark['lastFundingRate'])
    if not math.isfinite(rate) or not 0<=now-at<=300:raise ValueError('FUNDING_STALE')
    if not isinstance(oi,list) or len(oi)!=2:raise ValueError('OI_BASELINE_MISSING')
    a,b=sorted(oi,key=lambda r:r['timestamp']);old=float(a['sumOpenInterest']);new=float(b['sumOpenInterest']);ot=b['timestamp']/1000
    if a['symbol']!=symbol+'USDT' or b['symbol']!=symbol+'USDT' or not all(math.isfinite(v) and v>0 for v in (old,new)):raise ValueError('OI_INVALID')
    if not 0<=now-ot<=900 or b['timestamp']-a['timestamp']!=300000:raise ValueError('OI_STALE_OR_GAP')
    change=new/old-1
    matched=[n for n in news if any(re.search(r'\b'+re.escape(alias)+r'\b',n['title'].lower()) for alias in ALIASES[symbol])]
    selected=matched or news
    return dict(funding_shock=abs(rate)>=.0005,oi_shock=abs(change)>=.05,
        sentiment=sum(n['sentiment'] for n in selected)/len(selected),
        evidence=dict(source='Binance public USD-M perpetual proxy; not Yellow execution data',
            funding_at=at,funding_rate=rate,oi_at=ot,oi_change_5m=change,
            sentiment_method='deterministic headline lexicon; not a model forecast',
            sentiment_scope='asset headlines' if matched else 'global crypto headlines',headline_count=len(selected),
            funding_shock_threshold=.0005,oi_shock_threshold=.05))

def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,allow_nan=False));os.chmod(tmp,0o600);os.replace(tmp,path)

class Feed:
    def __init__(self,root,fetcher=fetch,clock=time.time):
        self.root=Path(root);self.fetch=fetcher;self.clock=clock;self.cache={};self.retry_after={}
    def cached(self,key,url,ttl,parser):
        now=self.clock();old=self.cache.get(key)
        if old and now-old['at']<ttl:
            return dict(old,data=parser(old['raw']))
        if now<self.retry_after.get(key,0):raise ValueError('SOURCE_RETRY_BACKOFF')
        try:raw=self.fetch(url);data=parser(raw)
        except Exception:
            if key.startswith(('geo_','oil_','spot_')):self.retry_after[key]=now+ttl
            raise
        value=dict(at=self.clock(),url=url,data=data,raw=raw);self.cache[key]=value;return value
    def once(self):
        now=self.clock();errors={};sources={};headlines=[]
        tasks={name:(url,180,lambda b,n=name:parse_news(b,n,now)) for name,url in NEWS.items()}
        tasks['calendar']=(CALENDAR,900,lambda b:parse_calendar(b,now))
        tasks['funding']=(FUNDING,30,json.loads)
        for sym in SYMBOLS:
            tasks['oi_'+sym]=(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}USDT&period=5m&limit=2',60,json.loads)
        tasks.update(macro.tasks(now,parse_news))
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures={pool.submit(self.cached,k,*v):k for k,v in tasks.items()}
            for f in concurrent.futures.as_completed(futures):
                k=futures[f]
                try:sources[k]=f.result()
                except Exception as ex:errors[k]=type(ex).__name__+':'+(str(ex) if isinstance(ex,ValueError) else 'FETCH_FAILED')
        now=self.clock()
        for name in NEWS:
            if name in sources:headlines.extend(sources[name]['data'])
        headlines=list({n['title'].lower():n for n in sorted(headlines,key=lambda x:x['published_at'],reverse=True)}.values())[:60]
        headlines=[n for n in headlines if 0<=now-n['published_at']<=86400]
        status=dict(at=self.clock(),sources={k:dict(url=v['url'],fetched_at=v['at']) for k,v in sources.items()},errors=errors,markets_ready=0,ready=False)
        macro_data=macro.research_view(macro.build(sources,errors,now),now)
        status['macro']=dict(status=macro_data['status'],geopolitical_reports=len(macro_data.get('geopolitics',{}).get('reports',[])),oil_futures=len(macro_data.get('oil',{}).get('futures',[])),oil_spot=len(macro_data.get('oil',{}).get('spot',[])))
        if headlines and 'calendar' in sources and 'funding' in sources:
            marks={m['symbol']:m for m in sources['funding']['data']};markets={}
            for sym in SYMBOLS:
                try:markets[sym+'USDT-PERP']=market_context(sym,marks[sym+'USDT'],sources['oi_'+sym]['data'],headlines,now)
                except (KeyError,ValueError,TypeError) as ex:errors['market_'+sym]=str(ex) if isinstance(ex,ValueError) else 'MISSING_MARKET_EVIDENCE'
            payload=dict(as_of=now,news=headlines,calendar=sources['calendar']['data'],markets=markets,
                sources=status['sources'],macro=macro_data,coverage='Forex Factory weekly economic calendar; crypto RSS headlines; Binance funding and 5-minute OI proxy; geopolitical reports and dated oil observations. Not exhaustive event coverage.')
            if markets:
                atomic(self.root/'context.json',payload);status.update(ready=True,markets_ready=len(markets),context_as_of=now,news_count=len(headlines),calendar_count=len(payload['calendar']))
        if not status['ready']:status['reason']='REQUIRED_EVIDENCE_UNAVAILABLE'
        atomic(self.root/'context-status.json',status)
        return status

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');p.add_argument('--state',default=os.environ.get('WHY_DISCOVERY_STATE','/var/lib/why-discovery'));args=p.parse_args()
    feed=Feed(args.state)
    while True:
        try:
            status=feed.once();print(json.dumps(dict(ready=status['ready'],markets_ready=status['markets_ready'],errors=status['errors'])),flush=True)
        except Exception as ex:print(json.dumps(dict(ready=False,error=type(ex).__name__)),flush=True)
        if args.once:return
        time.sleep(60)
if __name__=='__main__':main()

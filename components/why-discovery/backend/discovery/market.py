"""Read-only public Yellow data. No account endpoints or exchange order transport."""
import collections
import datetime
import json
import math
import os
import socket
import ssl
import queue
import threading
import time
import urllib.request
import urllib.parse
from .model import NoRedirect

def public_socket(timeout=5,family=None):
    """Keep the authenticated hostname/TLS while choosing independent IP routes."""
    import websocket
    url='wss://trade.api.yellow.pro/ws'
    # Explicit proxy configuration always wins; never bypass a configured proxy.
    if family is None or any(os.environ.get(k) for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')):
        return websocket.create_connection(url,timeout=timeout)
    hostname='trade.api.yellow.pro';deadline=time.monotonic()+timeout;last=None
    addresses=socket.getaddrinfo(hostname,443,family,socket.SOCK_STREAM)
    for af,kind,proto,canonical,address in addresses:
        transport=None
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('PUBLIC_CONNECT_TIMEOUT')
            transport=socket.socket(af,kind,proto);transport.settimeout(remaining)
            transport.setsockopt(socket.SOL_SOCKET,socket.SO_KEEPALIVE,1)
            transport.connect(address)
            transport=ssl.create_default_context().wrap_socket(transport,server_hostname=hostname)
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('PUBLIC_CONNECT_TIMEOUT')
            ws=websocket.create_connection(url,timeout=remaining,socket=transport)
            ws.route_family='IPv4' if af==socket.AF_INET else 'IPv6'
            return ws
        except Exception as ex:
            last=ex
            if transport:transport.close()
    raise last or OSError('PUBLIC_ROUTE_UNAVAILABLE')

def timestamp(value):
    if isinstance(value,(int,float)):return value/1000 if value>1e12 else float(value)
    return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

def get(path,params=None):
    url='https://trade.api.yellow.pro'+path
    if params:url+='?'+urllib.parse.urlencode(params)
    request=urllib.request.Request(url,headers={'User-Agent':'WHY-Discovery/1.0','Accept':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as r:
        data=r.read(8_000_001)
    if len(data)>8_000_000:raise ValueError('PUBLIC_RESPONSE_TOO_LARGE')
    return json.loads(data)

def universe(data):
    result={}
    for row in data['symbols']:
        if str(row.get('status','')).upper() not in ('TRADING','ACTIVE'):continue
        filters={f['filter_type']:f['config'] for f in row.get('filters',[])}
        try:
            limits=dict(tick=float(filters['PRICE_FILTER']['tick_size']),step=float(filters['LOT_SIZE']['step_size']),
                min_qty=float(filters['LOT_SIZE']['min_qty']),min_notional=float(filters['MIN_NOTIONAL']['min_notional']))
            if not all(math.isfinite(v) and v>0 for v in limits.values()):continue
            result[row['symbol']]=limits
        except (KeyError,TypeError,ValueError):continue
    if not result:raise ValueError('NO_VALID_MARKETS')
    return result

class Books:
    def __init__(self):
        self.books={};self.seq={};self.bars={};self.tape={};self.marks={};self.seen=collections.OrderedDict()
    def reset(self):self.books.clear();self.seq.clear();self.tape.clear();self.marks.clear()
    def forget(self,m):
        self.books.pop(m,None);self.tape.pop(m,None);self.marks.pop(m,None)
        self.seq.pop((m,'book'),None);self.seq.pop((m,'trades'),None)
    def confirm(self,d,now):
        # A second connection may confirm an unchanged primary book, but cannot
        # skip primary sequence numbers, change depth, or create maker fills.
        m=d.get('market');previous=self.books.get(m)
        if not previous or d.get('header',{}).get('type')!='orderbook.snapshot':return False
        if d.get('sequence_num')!=previous['seq']:return False
        parsed=Books();parsed.receive(d,now);b=parsed.books[m]
        if not -.5<=now-b['source']<=5 or b['source']<=previous['source']:return False
        if any(b[side]!=previous[side] for side in ('bids','asks')):return False
        self.books[m]=b
        return True
    def seed(self,m,rows,now):
        bars={}
        for r in rows:
            if not isinstance(r,list) or len(r)<6:continue
            t=timestamp(float(r[0]));v=[float(x) for x in r[1:6]]
            if t+300>now or not all(math.isfinite(x) for x in v) or min(v[:4])<=0 or v[4]<0:continue
            bars[t]=v
        self.bars[m]=sorted(bars.items())[-80:]
    def receive(self,d,now):
        m=d.get('market');typ=d.get('header',{}).get('type','')
        if typ=='mark_price':
            rate=float(d['funding_rate']);next_time=timestamp(d['next_funding_time']);source=timestamp(d['header']['created_at'])
            if not all(math.isfinite(x) for x in (rate,next_time,source)) or abs(rate)>1:raise ValueError('INVALID_FUNDING')
            self.marks[m]=dict(rate=rate,next=next_time,source=source,received=now)
            return []
        if not typ.startswith(('orderbook.','trades.')):return []
        kind='book' if typ.startswith('orderbook.') else 'trades';key=(m,kind)
        snap=typ.endswith('snapshot');seq=d.get('sequence_num')
        if type(seq)!=int:raise ValueError('MISSING_SEQUENCE')
        if key in self.seq and (seq<self.seq[key] or (seq==self.seq[key] and not (snap and kind=='book'))):return []
        if snap and key in self.seq and seq>self.seq[key]+1:raise ValueError('SEQUENCE_GAP')
        if not snap and (key not in self.seq or seq!=self.seq[key]+1):raise ValueError('SEQUENCE_GAP')
        if kind=='book':
            b={'bids':{},'asks':{}} if snap else {s:dict(self.books[m][s]) for s in ('bids','asks')}
            for side in ('bids','asks'):
                for p,q in d.get(side,[]):
                    p,q=float(p),float(q)
                    if not math.isfinite(p+q) or p<=0 or q<0:raise ValueError('INVALID_DEPTH')
                    if q:b[side][p]=q
                    else:b[side].pop(p,None)
            # A liquidity provider can withdraw an entire side before replenishing
            # it in later contiguous updates. Keep the sequence, but do not trade
            # until both sides return. This is not a transport/schema gap.
            if b['bids'] and b['asks'] and max(b['bids'])>=min(b['asks']):raise ValueError('INVALID_BOOK')
            source=d.get('timestamp') or d.get('updated_at') or d.get('header',{}).get('created_at')
            ts=timestamp(source) if source else 0
            if not math.isfinite(ts):raise ValueError('INVALID_TIMESTAMP')
            if snap and key in self.seq and seq==self.seq[key]:
                previous=self.books[m]
                if any(b[side]!=previous[side] for side in ('bids','asks')):raise ValueError('SNAPSHOT_SEQUENCE_MISMATCH')
                if ts<=previous['source']:return []
            b.update(received=now,source=ts,seq=seq)
            self.books[m]=b;self.seq[key]=seq
            return []
        trades=[]
        if not snap:
            for t in d.get('trades',[]):
                p,q=float(t['price']),float(t['amount']);ts=timestamp(t['executed_at'])
                if not math.isfinite(p+q+ts) or min(p,q)<=0 or t['direction'] not in ('buy','sell'):raise ValueError('INVALID_TRADE')
                tid=(m,str(t['id']))
                if tid in self.seen:continue
                self.seen[tid]=True
                if len(self.seen)>100000:self.seen.popitem(last=False)
                trade=dict(price=p,qty=q,ts=ts,side=t['direction'],id=str(t['id']))
                trades.append(trade)
                tape=self.tape.setdefault(m,collections.deque(maxlen=500));tape.append(trade)
        self.seq[key]=seq
        return sorted(trades,key=lambda t:t['ts'])
    def issue(self,m,now):
        b=self.books.get(m)
        if not b or not (0<=now-b['received']<=5 and -.5<=now-b['source']<=5):return 'STALE_MARKET_DATA'
        if not b['bids'] or not b['asks']:return 'EMPTY_ORDER_BOOK'
        return ''
    def fresh(self,m,now):return not self.issue(m,now)
    def features(self,m,now):
        if not self.fresh(m,now):return None
        b=self.books[m];bid=max(b['bids']);ask=min(b['asks']);mid=(bid+ask)/2
        bids=sum(q for _,q in sorted(b['bids'].items(),reverse=True)[:5]);asks=sum(q for _,q in sorted(b['asks'].items())[:5])
        f=dict(spread_bps=(ask-bid)/mid*10000,book_imbalance=(bids-asks)/(bids+asks))
        funding=self.marks.get(m)
        if funding and 0<=now-funding['source']<=30:f['funding_bps']=funding['rate']*10000
        tape=[t for t in self.tape.get(m,[]) if 0<=now-t['ts']<=60]
        volume=sum(t['qty'] for t in tape)
        if volume:f['trade_imbalance']=sum(t['qty']*(1 if t['side']=='buy' else -1) for t in tape)/volume
        bars=self.bars.get(m,[])
        if len(bars)>=21 and bars[-1][0]+300<=now<=bars[-1][0]+660 and all(bars[j][0]-bars[j-1][0]==300 for j in range(len(bars)-20,len(bars))):
            close=bars[-1][1][3]
            for n in (1,5,20):f['return_'+str(n)]=(close/bars[-1-n][1][3]-1)*10000
            returns=[(bars[j][1][3]/bars[j-1][1][3]-1)*10000 for j in range(len(bars)-20,len(bars))]
            mean=sum(returns)/len(returns);f['volatility']=(sum((x-mean)**2 for x in returns)/len(returns))**.5
            hi=max(v[1] for _,v in bars[-20:]);lo=min(v[2] for _,v in bars[-20:])
            if hi>lo:f['range_position']=(close-lo)/(hi-lo)
            avg=sum(v[4] for _,v in bars[-21:-1])/20
            if avg:f['volume_ratio']=bars[-1][1][4]/avg
        return f

class BookRefresh:
    """Refresh accepted book state; duplicate traffic is not proof of freshness."""
    def __init__(self):
        self.books=Books();self.started={};self.requested={}
    def select(self,markets,now):
        for m in set(self.started)-set(markets):
            self.started.pop(m,None);self.requested.pop(m,None)
            self.books.books.pop(m,None);self.books.seq.pop((m,'book'),None)
        for m in markets:self.started.setdefault(m,now)
    def observe(self,data,now):
        if data.get('header',{}).get('type','').startswith('orderbook.'):
            self.books.receive(data,now)
    def due(self,now):
        result=[]
        for m,started in sorted(self.started.items()):
            b=self.books.books.get(m)
            accepted=min(b['source'],b['received']) if b else started
            # Leave at least three seconds for the response before the safety
            # gate expires. Missing or rejected snapshots remain due for retry.
            empty=b is not None and (not b['bids'] or not b['asks'])
            if (not empty and now-accepted<2) or now-self.requested.get(m,float('-inf'))<1:continue
            result.append(m);self.requested[m]=now
        return result
    def stalled(self,now):
        # Give snapshot requests time to recover before replacing a connection
        # that is still open but no longer delivers accepted, current books.
        for m,started in self.started.items():
            b=self.books.books.get(m)
            accepted=min(b['source'],b['received']) if b else started
            if now-max(started,accepted)>5 and m in self.requested:return True
        return False
    def expired(self,now):
        result=[]
        for m,started in self.started.items():
            b=self.books.books.get(m)
            accepted=min(b['source'],b['received']) if b else started
            if now-max(started,accepted)>5 and m in self.requested:result.append(m)
        return result

class SnapshotFeed:
    """Obtain confirmations on a separate socket without interrupting increments."""
    def __init__(self,stop,family=None):
        self.family=family
        self.stop=stop;self.requests=queue.Queue(maxsize=64);self.results=queue.Queue(maxsize=256)
        self.pending=set();self.lock=threading.Lock()
    def start(self):threading.Thread(target=self.run,daemon=True).start()
    def request(self,m):
        with self.lock:
            if m in self.pending:return
            try:self.requests.put_nowait(m)
            except queue.Full:return
            self.pending.add(m)
    def release(self,m):
        with self.lock:self.pending.discard(m)
    def run(self):
        import websocket
        while not self.stop.is_set():
            ws=None;inflight={}
            try:
                ws=public_socket(timeout=5,family=self.family)
                ws.send(json.dumps(dict(id=1,connect={})))
                reply=json.loads(ws.recv())
                if reply.get('id')!=1 or 'connect' not in reply or 'error' in reply:raise ValueError('HANDSHAKE_FAILED')
                ws.settimeout(.1);i=2
                while not self.stop.is_set():
                    while len(inflight)<32:
                        try:m=self.requests.get_nowait()
                        except queue.Empty:break
                        inflight[i]=(m,time.time())
                        ws.send(json.dumps(dict(id=i,subscribe=dict(channel='public.orderbook.increment.'+m))));i+=1
                        self.stop.wait(.015)
                    if any(time.time()-at>2 for m,at in inflight.values()):raise ValueError('SNAPSHOT_TIMEOUT')
                    try:raw=ws.recv()
                    except websocket.WebSocketTimeoutException:continue
                    if not raw:raise ValueError('FEED_CLOSED')
                    for line in raw.splitlines():
                        if not line.strip():continue
                        msg=json.loads(line)
                        if msg=={}:ws.send('{}');continue
                        if 'error' in msg:raise ValueError('SNAPSHOT_REJECTED')
                        request=inflight.get(msg.get('id'))
                        if not request:continue # Never forward incremental publications.
                        m,at=request;d=msg.get('subscribe',{}).get('data')
                        if not d or d.get('market')!=m or d.get('header',{}).get('type')!='orderbook.snapshot':raise ValueError('INVALID_SNAPSHOT')
                        self.results.put((d,time.time()),timeout=.1)
                        ws.send(json.dumps(dict(id=i,unsubscribe=dict(channel='public.orderbook.increment.'+m))));i+=1
                        inflight.pop(msg['id']);self.release(m)
            except Exception:
                # Confirmation failures never disconnect the primary stream or
                # extend freshness. The normal safety gate remains in force.
                pass
            finally:
                for m,at in inflight.values():self.release(m)
                if ws:
                    try:ws.close()
                    except Exception:pass
            self.stop.wait(.5)

class CandleFeed:
    """Refresh closed candles independently of slow model calls and reviews."""
    def __init__(self,out,selected,stop,clock=time.time):
        self.out=out;self.selected=selected;self.stop=stop;self.clock=clock
        self.attempted={};self.completed={}
    def once(self):
        now=self.clock();markets=set(self.selected());expected=math.floor(now/300)*300-300
        for m in set(self.attempted)-markets:
            self.attempted.pop(m,None);self.completed.pop(m,None)
        due=[m for m in markets if self.completed.get(m,float('-inf'))<expected
             and now-self.attempted.get(m,float('-inf'))>=15]
        if not due:return
        m=min(due,key=lambda m:(self.attempted.get(m,float('-inf')),m))
        self.attempted[m]=now
        rows=get('/klines',dict(symbol=m,interval='5m',limit=80))
        received=self.clock();parsed=Books();parsed.seed(m,rows,received)
        self.out.put(('bars',(m,rows),received),timeout=2)
        if parsed.bars[m]:self.completed[m]=parsed.bars[m][-1][0]
    def run(self):
        while not self.stop.is_set():
            try:self.once()
            except Exception:pass # Stale/missing features stay unavailable; retry fairly.
            self.stop.wait(1)

class StreamFeed:
    def __init__(self,out,pinned=lambda:set(),refresh_enabled=True,family=None):
        self.out=out;self.stopping=threading.Event();self.reconnect=threading.Event();self.pinned=pinned;self.cursor=0;self.recoveries=queue.SimpleQueue();self.refresh_enabled=refresh_enabled;self.family=family
    def recover(self,m):self.recoveries.put(m)
    def emit(self,item):self.out.put(item,timeout=2)
    def run(self):
        import websocket
        snapshots=SnapshotFeed(self.stopping)
        if self.refresh_enabled:snapshots.start()
        while not self.stopping.is_set():
            ws=None
            try:
                markets=universe(get('/perpetual/exchangeInfo'))
                self.emit(('universe',markets,time.time()))
                ws=public_socket(timeout=5,family=self.family)
                self.reconnect.clear()
                ws.send(json.dumps(dict(id=1,connect={})))
                handshake=json.loads(ws.recv())
                if handshake.get('id')!=1 or 'connect' not in handshake or 'error' in handshake:raise ValueError('HANDSHAKE_FAILED')
                self.emit(('route',dict(family=getattr(ws,'route_family','system')),time.time()))
                i=2;subscribed=set();changed=0;refresh=BookRefresh();waiting=set();last_data=time.time()
                def recover_market(m,reason):
                    nonlocal i
                    now=time.time();refresh.books.forget(m);refresh.started[m]=now;refresh.requested.pop(m,None)
                    waiting.update(((m,'book'),(m,'trades')))
                    self.emit(('market_gap',dict(market=m,reason=reason),now))
                    for action in ('unsubscribe','subscribe'):
                        for channel in ('public.orderbook.increment.','public.trades.increment.','public.mark_price.'):
                            ws.send(json.dumps(dict(id=i,**{action:dict(channel=channel+m)})));i+=1
                            self.stopping.wait(.015)
                ws.settimeout(.5)
                while not self.stopping.is_set():
                    if self.reconnect.is_set():raise ValueError('RESUBSCRIBE_REQUIRED')
                    # The merger owns quiet-book confirmations, but each transport
                    # must still detect silence without depending on refresh.due().
                    if not self.refresh_enabled and time.time()-last_data>5:raise ValueError('PUBLIC_FEED_STALLED')
                    if time.time()-changed>300:
                        pinned=self.pinned() & set(markets)
                        # 3 public channels per market, below the documented 100-subscription limit.
                        # Open experiments stay subscribed; idle slots scan the full universe in rotation.
                        if len(pinned)>32:raise ValueError('FEED_CAPACITY_EXCEEDED')
                        idle=sorted(set(markets)-pinned);slots=32-len(pinned)
                        selected=set((idle+idle)[self.cursor%max(1,len(idle)):self.cursor%max(1,len(idle))+slots])
                        wanted=pinned|selected;self.cursor+=max(1,slots)
                        for action,items in [('unsubscribe',subscribed-wanted),('subscribe',wanted-subscribed)]:
                            for m in sorted(items):
                                for channel in ('public.orderbook.increment.','public.trades.increment.','public.mark_price.'):
                                    ws.send(json.dumps(dict(id=i,**{action:dict(channel=channel+m)})));i+=1
                                    self.stopping.wait(.015)
                        if subscribed-wanted:self.emit(('drop',list(subscribed-wanted),time.time()))
                        subscribed=wanted;changed=time.time()
                        self.emit(('selected',sorted(subscribed),changed))
                        refresh.select(subscribed,changed)
                    # Refresh on an independent connection: unsubscribing the
                    # primary stream to take a snapshot loses real increments.
                    while True:
                        try:d,received=snapshots.results.get_nowait()
                        except queue.Empty:break
                        if d.get('market') not in subscribed:continue
                        try:
                            refresh.books.confirm(d,received)
                            self.emit(('confirmation',d,received))
                        except (ValueError,KeyError,TypeError,OverflowError):pass
                    while True:
                        try:m=self.recoveries.get_nowait()
                        except queue.Empty:break
                        if m in subscribed and (m,'book') not in waiting:recover_market(m,'PUBLIC_MARKET_SEQUENCE_OR_SCHEMA_GAP')
                    now=time.time();expired=refresh.expired(now)
                    if expired and set(expired)==subscribed:raise ValueError('PUBLIC_FEED_STALLED')
                    for m in expired:recover_market(m,'PUBLIC_MARKET_STALLED')
                    if self.refresh_enabled:
                        for m in refresh.due(now):snapshots.request(m)
                    try:raw=ws.recv()
                    except websocket.WebSocketTimeoutException:continue
                    if not raw:raise ValueError('FEED_CLOSED')
                    for line in raw.splitlines():
                        if not line.strip():continue
                        msg=json.loads(line)
                        if msg=={}:ws.send('{}');continue
                        if 'error' in msg or any(msg.get(k,{}).get('error') for k in ('subscribe','unsubscribe')):raise ValueError('SUBSCRIPTION_REJECTED')
                        d=msg.get('subscribe',{}).get('data') or msg.get('push',{}).get('pub',{}).get('data')
                        if d:
                            m=d.get('market');typ=d.get('header',{}).get('type','')
                            if m not in subscribed:continue
                            kind='book' if typ.startswith('orderbook.') else 'trades' if typ.startswith('trades.') else None
                            key=(m,kind)
                            if key in waiting and not typ.endswith('snapshot'):continue
                            received=time.time();last_data=received
                            try:refresh.observe(d,received)
                            except (ValueError,KeyError,TypeError,OverflowError):
                                recover_market(m,'PUBLIC_MARKET_SEQUENCE_OR_SCHEMA_GAP');continue
                            waiting.discard(key)
                            self.emit(('data',d,received))
            except Exception as ex:
                # No raw exception text: transport errors can contain connection details.
                reason='PUBLIC_FEED_STALLED' if isinstance(ex,ValueError) and ex.args==('PUBLIC_FEED_STALLED',) else 'PUBLIC_FEED_UNAVAILABLE'
                try:
                    self.emit(('transport_error',dict(error=type(ex).__name__,reason=reason),time.time()))
                    self.emit(('gap',reason,time.time()))
                except queue.Full:pass
                if ws:
                    try:ws.close()
                    except Exception:pass
                    ws=None
                self.stopping.wait(5)
            finally:
                if ws:ws.close()

class FeedMerge:
    """Deduplicate two continuous streams without relaxing sequence continuity."""
    def __init__(self,emit,recover,clock=time.time,recover_source=None):
        self.emit=emit;self.recover=recover;self.clock=clock;self.books=Books()
        self.pending={};self.live=set();self.last_seen={};self.started=False;self.last_status=0
        self.trade_books={0:Books(),1:Books()};self.trade_serial={};self.trade_seen=collections.OrderedDict()
        self.trade_good=set();self.recover_source=recover_source or (lambda source,m:recover(m))
        self.subscriptions={0:set(),1:set()};self.errors={};self.routes={};self.error_types={}
    def gap_market(self,m,reason):
        self.books.forget(m)
        self.trade_serial.pop(m,None)
        for source in (0,1):self.trade_books[source].forget(m);self.trade_good.discard((source,m))
        for key in list(self.pending):
            if key[0]==m:self.pending.pop(key)
        self.emit(('market_gap',dict(market=m,reason=reason),self.clock()))
        self.recover(m)
    def trades(self,source,d,at):
        # Exchange trade-channel sequence counters can differ across connections.
        # Validate each source independently, then deduplicate actual trade IDs.
        m=d['market'];b=self.trade_books[source];snap=d['header']['type'].endswith('snapshot')
        if snap:b.forget(m)
        try:accepted=b.receive(d,at)
        except (ValueError,KeyError,TypeError,OverflowError):
            b.forget(m);self.trade_good.discard((source,m));self.recover_source(source,m)
            if not any((other,m) in self.trade_good for other in (0,1)):
                self.gap_market(m,'PUBLIC_TRADE_SEQUENCE_GAP')
            return
        if m not in self.trade_serial:
            self.trade_serial[m]=0
            self.emit(('data',dict(market=m,header=dict(type='trades.snapshot'),sequence_num=0,trades=[]),at))
        if snap:return
        self.trade_good.add((source,m));ids={t['id'] for t in accepted};trades=[]
        for t in d.get('trades',[]):
            tid=(m,str(t['id']))
            if str(t['id']) not in ids or tid in self.trade_seen:continue
            self.trade_seen[tid]=True;trades.append(t)
            if len(self.trade_seen)>100000:self.trade_seen.popitem(last=False)
        if not trades:return
        self.trade_serial[m]+=1
        normalized=dict(d,sequence_num=self.trade_serial[m],trades=trades,source_sequence_num=d['sequence_num'],source_connection=source)
        self.emit(('data',normalized,at))
    def data(self,d,at):
        m=d.get('market');typ=d.get('header',{}).get('type','')
        if typ=='mark_price':
            previous=self.books.marks.get(m)
            if previous and timestamp(d['header']['created_at'])<=previous['source']:return
            self.books.receive(d,at);self.emit(('data',d,at));return
        kind='book' if typ.startswith('orderbook.') else 'trades' if typ.startswith('trades.') else None
        if not kind:return
        key=(m,kind);seq=d.get('sequence_num');previous=self.books.seq.get(key)
        if type(seq)!=int:raise ValueError('MISSING_SEQUENCE')
        if typ.endswith('snapshot'):
            if previous is not None:
                if kind=='book' and self.books.confirm(d,at):self.emit(('confirmation',d,at))
                return # A late-joining stream cannot jump over missed increments.
        else:
            if previous is None or seq>previous+1:
                waiting=self.pending.setdefault(key,{})
                waiting.setdefault(seq,(d,at,self.clock()))
                if len(waiting)>256:self.gap_market(m,'PUBLIC_MERGE_BUFFER_OVERFLOW')
                return
            if seq<=previous:return
        self.books.receive(d,at);self.emit(('data',d,at))
        waiting=self.pending.get(key,{})
        while self.books.seq.get(key,0)+1 in waiting:
            next_seq=self.books.seq[key]+1;next_data,received,queued=waiting.pop(next_seq)
            self.books.receive(next_data,received);self.emit(('data',next_data,received))
        for old in list(waiting):
            if old<=self.books.seq.get(key,0):waiting.pop(old)
        if not waiting:self.pending.pop(key,None)
    def receive(self,source,item):
        kind,d,at=item
        if kind in ('data','confirmation'):
            if source in (0,1):self.live.add(source);self.last_seen[source]=at;self.started=True
            try:
                if kind=='confirmation':
                    if self.books.confirm(d,at):self.emit(item)
                elif d.get('header',{}).get('type','').startswith('trades.'):self.trades(source,d,at)
                else:self.data(d,at)
            except (ValueError,KeyError,TypeError,OverflowError):
                self.gap_market(d.get('market'),'PUBLIC_MERGED_SEQUENCE_OR_SCHEMA_GAP')
        elif kind=='route':self.routes[source]=d
        elif kind=='transport_error':self.error_types[source]=d['error']
        elif kind=='gap':
            self.live.discard(source);self.errors[source]=dict(at=at,reason=d,error=self.error_types.get(source))
            self.trade_books[source].reset();self.trade_good={key for key in self.trade_good if key[0]!=source}
            if self.started and not self.live:
                self.books.reset();self.pending.clear();self.trade_serial.clear();self.emit(item)
        elif kind=='market_gap':
            m=d['market'];self.trade_books[source].forget(m);self.trade_good.discard((source,m))
        elif kind=='universe':self.emit(item)
        elif kind=='selected':
            self.subscriptions[source]=set(d)
            selected=self.subscriptions[0]|self.subscriptions[1]
            dropped=set(self.books.books)-selected
            for m in dropped:self.books.forget(m)
            if dropped:self.emit(('drop',sorted(dropped),at))
    def tick(self):
        now=self.clock()
        expired={m for (m,kind),rows in self.pending.items() if any(now-t>1 for d,at,t in rows.values())}
        for m in expired:self.gap_market(m,'PUBLIC_MERGED_SEQUENCE_GAP')
        if now-self.last_status>=1:
            self.last_status=now
            self.emit(('transport_status',dict(streams={str(i):dict(connected=i in self.live and now-self.last_seen.get(i,0)<=5,last_data_age=round(now-self.last_seen[i],3) if i in self.last_seen else None,last_error=self.errors.get(i),route=self.routes.get(i)) for i in (0,1)}),now))

class PublicFeed:
    """Two continuous streams; one confirmation socket; no venue order access."""
    def __init__(self,out,pinned=lambda:set()):
        self.out=out;self.pinned=pinned;self.stopping=threading.Event();self.reconnect=threading.Event()
        self.recoveries=queue.SimpleQueue();self.streams=[]
    def recover(self,m):self.recoveries.put(m)
    def emit(self,item):self.out.put(item,timeout=2)
    def run(self):
        incoming=queue.Queue(maxsize=40000)
        class Sink:
            def __init__(self,source):self.source=source
            def put(self,item,timeout):incoming.put((self.source,item),timeout=timeout)
        for source in (0,1):
            stream=StreamFeed(Sink(source),self.pinned,refresh_enabled=False,family=socket.AF_INET if source==0 else socket.AF_INET6)
            stream.stopping=self.stopping;self.streams.append(stream)
            threading.Thread(target=stream.run,daemon=True).start()
        def recover(m):
            for stream in self.streams:stream.recover(m)
        merge=FeedMerge(self.emit,recover,recover_source=lambda source,m:self.streams[source].recover(m))
        snapshots=SnapshotFeed(self.stopping,family=socket.AF_INET);snapshots.start()
        refresh=BookRefresh();refresh.books=merge.books
        while not self.stopping.is_set():
            if self.reconnect.is_set():
                self.reconnect.clear()
                for stream in self.streams:stream.reconnect.set()
            while True:
                try:m=self.recoveries.get_nowait()
                except queue.Empty:break
                merge.books.forget(m)
                merge.trade_serial.pop(m,None)
                for source in (0,1):merge.trade_books[source].forget(m);merge.trade_good.discard((source,m))
                for key in list(merge.pending):
                    if key[0]==m:merge.pending.pop(key)
                recover(m)
            try:
                source,item=incoming.get(timeout=.1);merge.receive(source,item)
            except queue.Empty:pass
            # Confirmations belong to the merger, never to either connection's
            # reconnect/backoff loop. Quiet books keep refreshing during failover.
            while True:
                try:d,at=snapshots.results.get_nowait()
                except queue.Empty:break
                if d.get('market') in merge.books.books:merge.receive(2,('confirmation',d,at))
            now=time.time();refresh.select(set(merge.books.books),now)
            for m in refresh.due(now):snapshots.request(m)
            # A missing confirmation or stuck market channel needs a new seed.
            # Invalidate only that market before requesting either stream again.
            for m in refresh.expired(now):merge.gap_market(m,'PUBLIC_MARKET_STALLED')
            merge.tick()
